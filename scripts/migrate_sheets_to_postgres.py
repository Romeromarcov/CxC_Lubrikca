"""Backfill: copia todos los datos de Google Sheets a PostgreSQL.

Uso:
  # Simulación -- lee de Sheets y muestra conteos, no escribe en Postgres.
  python scripts/migrate_sheets_to_postgres.py --dry-run

  # Ejecución real (upsert en Postgres). Requiere DATABASE_URL en el entorno
  # (o --database-url).
  python scripts/migrate_sheets_to_postgres.py --apply

  # Limitar a un subconjunto de tablas (para reintentar una sola tras un
  # error, o probar rápido durante desarrollo):
  python scripts/migrate_sheets_to_postgres.py --apply --tables clientes,pagos

Lee vía ``SheetGateway`` (mismo binding que usa la app en producción) y
parsea cada fila con las mismas reglas que ``cxc.sheets.serde`` usa hoy
(``Decimal``/bool/fecha, valores por defecto ante campos vacíos o
inválidos). Las tablas-espejo y de trabajo humano que ya tienen métodos de
escritura en ``PostgresRepository`` (clientes, órdenes, líneas, pagos,
vinculaciones, bandeja, conciliación) se escriben a través de esos métodos
-- misma lógica de upsert que usa producción. Las tablas de configuración
que ``Repository`` todavía no expone para escritura (reglas de descuento,
métodos de pago, feriados, etc. -- se amplía en un PR futuro) se escriben
con upsert directo contra el esquema (``cxc.db.postgres_repository._upsert``).

Idempotente: se puede reejecutar cuantas veces haga falta durante la
ventana de validación en paralelo sin duplicar filas. Una fila individual
inválida (celda corrupta, campo requerido vacío) se omite con un warning en
vez de abortar toda la tabla -- mismo espíritu tolerante que
``cxc.sheets.serde.p_dec``.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import sys
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Table, delete, insert, select

from cxc.config import AppConfig
from cxc.db import schema as t
from cxc.db.engine import make_engine
from cxc.db.postgres_repository import PostgresRepository, _upsert
from cxc.sheets import gateway as g
from cxc.sheets import serde
from cxc.sheets.gateway import GspreadGateway, SheetGateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cxc.migrate_sheets_to_postgres")

# Segundos entre lecturas consecutivas a la API de Sheets. Este backfill lee
# ~26 pestañas completas en una sola corrida -- sin espaciarlas se puede
# disparar la cuota "Read requests per minute" (el mismo problema del
# HOTFIX de logging de 429 en producción), y GspreadGateway.read_rows()
# atrapa esa excepción y devuelve [] en silencio: sin este pacing, una
# tabla con datos reales puede reportarse como vacía sin ningún error.
_SHEETS_READ_DELAY_SECONDS = 1.5


class _PacedGateway(SheetGateway):
    """Envoltorio de solo lectura que espacía las llamadas a ``read_rows``.

    Ver ``_SHEETS_READ_DELAY_SECONDS``. Los demás métodos delegan sin
    modificar -- este backfill nunca escribe en Sheets.
    """

    def __init__(
        self, inner: SheetGateway, delay_seconds: float = _SHEETS_READ_DELAY_SECONDS
    ) -> None:
        self._inner = inner
        self._delay = delay_seconds

    def read_rows(self, table: str) -> list[dict[str, str]]:
        time.sleep(self._delay)
        return self._inner.read_rows(table)

    def append_row(self, table: str, row: Mapping[str, str]) -> None:
        self._inner.append_row(table, row)

    def upsert_row(self, table: str, pk_field: str, row: Mapping[str, str]) -> None:
        self._inner.upsert_row(table, pk_field, row)

    def delete_row(self, table: str, pk_field: str, pk_value: str) -> bool:
        return self._inner.delete_row(table, pk_field, pk_value)

    def get_meta(self, key: str) -> str | None:
        return self._inner.get_meta(key)

    def set_meta(self, key: str, value: str) -> None:
        self._inner.set_meta(key, value)


def _make_gateway(config: AppConfig) -> SheetGateway:
    if (
        os.environ.get("GOOGLE_TOKEN_JSON")
        or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        or os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    ):
        inner = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
    else:
        inner = GspreadGateway(config.sheets.spreadsheet_id, config.sheets.service_account_file)
    return _PacedGateway(inner)


def _dc_row(dc: Any, enum_fields: tuple[str, ...] = ()) -> dict[str, Any]:
    """``dataclass`` -> dict listo para insertar en Postgres.

    Los campos en ``enum_fields`` (``StrEnum``) se convierten con ``.value``;
    el resto viaja tal cual -- para las tablas de configuración, los campos
    del dataclass coinciden 1:1 con las columnas del esquema (ver
    ``cxc.db.schema``).
    """
    row = dataclasses.asdict(dc)
    for field in enum_fields:
        row[field] = row[field].value if hasattr(row[field], "value") else row[field]
    return row


def _parse_all(
    rows: list[Mapping[str, str]],
    parse_fn: Callable[[Mapping[str, str]], Any],
    label: str,
) -> list[Any]:
    out = []
    for r in rows:
        try:
            out.append(parse_fn(r))
        except Exception:
            logger.warning("Fila inválida en %s, se omite: %r", label, dict(r))
    return out


# --- Estrategia genérica: upsert directo contra una tabla del esquema -------
def _backfill_upsert(
    gw: SheetGateway,
    pg: PostgresRepository | None,
    *,
    dry_run: bool,
    label: str,
    read_rows: Callable[[SheetGateway], list[Mapping[str, str]]],
    parse_fn: Callable[[Mapping[str, str]], dict[str, Any]],
    table: Table,
    pk_cols: list[str],
) -> int:
    rows = _parse_all(read_rows(gw), parse_fn, label)
    if not dry_run and pg is not None and rows:
        with pg._engine.begin() as conn:  # noqa: SLF001 -- script interno, mismo paquete
            _upsert(conn, table, rows, pk_cols)
    return len(rows)


# --- Estrategia genérica: reemplazo total (sin clave natural en Sheets) -----
def _backfill_replace_all(
    gw: SheetGateway,
    pg: PostgresRepository | None,
    *,
    dry_run: bool,
    label: str,
    sheet_tab: str,
    parse_fn: Callable[[Mapping[str, str]], dict[str, Any]],
    table: Table,
) -> int:
    rows = _parse_all(gw.read_rows(sheet_tab), parse_fn, label)
    if not dry_run and pg is not None:
        with pg._engine.begin() as conn:  # noqa: SLF001
            conn.execute(delete(table))
            if rows:
                conn.execute(insert(table), rows)
    return len(rows)


# --- Tablas-espejo y de trabajo -- vía PostgresRepository (misma lógica que
# usa producción para upsert/append) -----------------------------------------
def _backfill_clientes(gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool) -> int:
    parsed = _parse_all(gw.read_rows(g.T_CLIENTES), serde.cliente_from_row, "Clientes")
    if not dry_run and pg is not None:
        pg.upsert_clientes(parsed)
    return len(parsed)


def _backfill_ordenes(gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool) -> int:
    parsed = _parse_all(gw.read_rows(g.T_ORDENES), serde.orden_from_row, "OrdenesVenta")
    if not dry_run and pg is not None:
        pg.upsert_ordenes(parsed)
    return len(parsed)


def _backfill_lineas(gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool) -> int:
    parsed = _parse_all(gw.read_rows(g.T_LINEAS), serde.linea_from_row, "LineasOrden")
    if not dry_run and pg is not None:
        pg.upsert_lineas(parsed)
    return len(parsed)


def _backfill_pagos(gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool) -> int:
    parsed = _parse_all(gw.read_rows(g.T_PAGOS), serde.pago_from_row, "Pagos")
    if not dry_run and pg is not None:
        pg.upsert_pagos(parsed)
    return len(parsed)


def _backfill_vinculaciones(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    parsed = _parse_all(
        gw.read_rows(g.T_VINCULACIONES), serde.vinculacion_from_row, "Vinculaciones"
    )
    if not dry_run and pg is not None:
        pg.update_vinculaciones(parsed)
    return len(parsed)


def _backfill_bandeja(gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool) -> int:
    parsed = _parse_all(gw.read_rows(g.T_BANDEJA), serde.bandeja_from_row, "BandejaFacturacion")
    if not dry_run and pg is not None:
        pg.upsert_bandejas(parsed)
    return len(parsed)


def _backfill_conciliacion(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    parsed = _parse_all(gw.read_rows(g.T_CONCILIACION), serde.conciliacion_from_row, "Conciliacion")
    if not dry_run and pg is not None:
        pg.upsert_conciliaciones(parsed)
    return len(parsed)


def _backfill_exclusiones(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    parsed = _parse_all(gw.read_rows("Exclusiones"), serde.exclusion_from_row, "Exclusiones")
    if not dry_run and pg is not None:
        for rule in parsed:
            pg.save_exclusion(rule)
    return len(parsed)


def _backfill_serie_tasas(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    # Append-only: una fila por captura, sin PK natural en Sheets. Para que
    # reejecutar el backfill no duplique capturas ya migradas, se compara
    # contra los timestamps ya presentes en Postgres y solo se insertan los
    # nuevos (nunca se actualiza ni se borra una captura existente).
    parsed = _parse_all(gw.read_rows(g.T_SERIE), serde.serie_from_row, "SerieTasas")
    if dry_run or pg is None:
        return len(parsed)
    with pg._engine.connect() as conn:  # noqa: SLF001
        existentes = {row.timestamp for row in conn.execute(select(t.serie_tasas.c.timestamp))}
    nuevas = [s for s in parsed if s.timestamp not in existentes]
    if nuevas:
        with pg._engine.begin() as conn:  # noqa: SLF001
            conn.execute(insert(t.serie_tasas), [dataclasses.asdict(s) for s in nuevas])
    return len(nuevas)


# --- Tablas de configuración -- Repository aún no expone escritura (se
# amplía en un PR futuro), así que se escriben directo contra el esquema ----
def _backfill_metodos_pago(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    return _backfill_upsert(
        gw,
        pg,
        dry_run=dry_run,
        label="MetodosPago",
        read_rows=lambda gw: gw.read_rows(g.T_METODOS),
        parse_fn=lambda r: _dc_row(serde.metodo_from_row(r), ("moneda", "tipo_tasa")),
        table=t.metodos_pago,
        pk_cols=["metodo_id"],
    )


def _backfill_descuentos_pronto_pago(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    def _read(gw: SheetGateway) -> list[Mapping[str, str]]:
        rows = gw.read_rows("DescuentosProntoPago")
        return rows if rows else gw.read_rows(g.T_DESCUENTOS)

    return _backfill_upsert(
        gw,
        pg,
        dry_run=dry_run,
        label="DescuentosProntoPago",
        read_rows=_read,
        parse_fn=lambda r: _dc_row(serde.pronto_pago_from_row(r), ("tipo_descuento",)),
        table=t.descuentos_pronto_pago,
        pk_cols=["regla_id"],
    )


def _backfill_descuentos_volumen(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    return _backfill_upsert(
        gw,
        pg,
        dry_run=dry_run,
        label="DescuentosVolumen",
        read_rows=lambda gw: gw.read_rows("DescuentosVolumen"),
        parse_fn=lambda r: _dc_row(serde.desc_volumen_from_row(r)),
        table=t.descuentos_volumen,
        pk_cols=["regla_id"],
    )


def _backfill_descuentos_recompra(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    return _backfill_upsert(
        gw,
        pg,
        dry_run=dry_run,
        label="DescuentosRecompra",
        read_rows=lambda gw: gw.read_rows("DescuentosRecompra"),
        parse_fn=lambda r: _dc_row(serde.recompra_from_row(r)),
        table=t.descuentos_recompra,
        pk_cols=["regla_id"],
    )


def _backfill_descuentos_producto(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    return _backfill_upsert(
        gw,
        pg,
        dry_run=dry_run,
        label="DescuentosProducto",
        read_rows=lambda gw: gw.read_rows("DescuentosProducto"),
        parse_fn=lambda r: _dc_row(serde.producto_from_row(r)),
        table=t.descuentos_producto,
        pk_cols=["regla_id"],
    )


def _backfill_descuentos_diferencial(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    return _backfill_upsert(
        gw,
        pg,
        dry_run=dry_run,
        label="DescuentosDiferencialCambiario",
        read_rows=lambda gw: gw.read_rows("DescuentosDiferencialCambiario"),
        parse_fn=lambda r: _dc_row(serde.diferencial_from_row(r)),
        table=t.descuentos_diferencial_cambiario,
        pk_cols=["regla_id"],
    )


def _backfill_bcv_completo(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    return _backfill_upsert(
        gw,
        pg,
        dry_run=dry_run,
        label="DescuentoBCVCompleto",
        read_rows=lambda gw: gw.read_rows(g.T_BCV_COMPLETO),
        parse_fn=lambda r: _dc_row(serde.bcv_completo_from_row(r)),
        table=t.descuento_bcv_completo,
        pk_cols=["vigencia_desde"],
    )


def _backfill_promocion_primera_compra(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    return _backfill_upsert(
        gw,
        pg,
        dry_run=dry_run,
        label="PromocionPrimeraCompra",
        read_rows=lambda gw: gw.read_rows(g.T_PROMO_PRIMERA),
        parse_fn=lambda r: _dc_row(serde.promocion_from_row(r)),
        table=t.promocion_primera_compra,
        pk_cols=["regla_id"],
    )


def _backfill_feriados(gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool) -> int:
    return _backfill_upsert(
        gw,
        pg,
        dry_run=dry_run,
        label="Feriados",
        read_rows=lambda gw: gw.read_rows(g.T_FERIADOS),
        parse_fn=lambda r: _dc_row(serde.feriado_from_row(r), ("tipo",)),
        table=t.feriados,
        pk_cols=["fecha"],
    )


def _backfill_reglas_recurrencia(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    return _backfill_replace_all(
        gw,
        pg,
        dry_run=dry_run,
        label="ReglasRecurrencia",
        sheet_tab=g.T_REGLAS,
        parse_fn=lambda r: _dc_row(serde.regla_from_row(r), ("condicion", "tipo_beneficio")),
        table=t.reglas_recurrencia,
    )


# --- Tablas fuera de cxc.models -- se leen/escriben como dict crudo --------
def _parse_usuario(r: Mapping[str, str]) -> dict[str, Any]:
    fecha_raw = r.get("fecha_registro", "").strip()
    return {
        "email": r["email"].strip().lower(),
        "nombre_odoo": r.get("nombre_odoo", ""),
        "password_hash": r.get("password_hash") or None,
        "salt": r.get("salt") or None,
        "rol": r.get("rol", ""),
        "activo": serde.p_bool(r.get("activo", "TRUE")),
        "fecha_registro": datetime.fromisoformat(fecha_raw) if fecha_raw else None,
    }


def _backfill_usuarios_plataforma(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    return _backfill_upsert(
        gw,
        pg,
        dry_run=dry_run,
        label="UsuariosPlataforma",
        read_rows=lambda gw: gw.read_rows("UsuariosPlataforma"),
        parse_fn=_parse_usuario,
        table=t.usuarios_plataforma,
        pk_cols=["email"],
    )


def _parse_anomalia(r: Mapping[str, str]) -> dict[str, Any]:
    ts_raw = r.get("timestamp_aprobacion", "").strip()
    return {
        "anomalia_id": r["anomalia_id"],
        "so_id": r.get("so_id", ""),
        "factura_id": r.get("factura_id", ""),
        "tipo_anomalia": r.get("tipo_anomalia", ""),
        "motivo_aceptacion": r.get("motivo_aceptacion", ""),
        "aprobado_por": r.get("aprobado_por", ""),
        "timestamp_aprobacion": datetime.fromisoformat(ts_raw) if ts_raw else None,
    }


def _backfill_anomalias_aceptadas(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    return _backfill_upsert(
        gw,
        pg,
        dry_run=dry_run,
        label="AnomaliasAceptadas",
        read_rows=lambda gw: gw.read_rows("AnomaliasAceptadas"),
        parse_fn=_parse_anomalia,
        table=t.anomalias_aceptadas,
        pk_cols=["anomalia_id"],
    )


def _parse_bandeja_auditoria(r: Mapping[str, str]) -> dict[str, Any]:
    return {
        "audit_id": r["audit_id"],
        "so_id": r.get("so_id", ""),
        "tipo_auditoria": r.get("tipo_auditoria", ""),
        "motor_calcula_usd": serde.p_optdec(r.get("motor_calcula_usd", "")),
        "odoo_registrado_usd": serde.p_optdec(r.get("odoo_registrado_usd", "")),
        "diferencia_usd": serde.p_optdec(r.get("diferencia_usd", "")),
        "detalle_odoo": r.get("detalle_odoo", ""),
        "detalle_motor": r.get("detalle_motor", ""),
        "estado": r.get("estado", ""),
        "revisado_por": r.get("revisado_por") or None,
        "timestamp_audit": serde.p_dt(r["timestamp_audit"]),
    }


def _backfill_bandeja_auditoria(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    return _backfill_upsert(
        gw,
        pg,
        dry_run=dry_run,
        label="BandejaAuditoria",
        read_rows=lambda gw: gw.read_rows(g.T_BANDEJA_AUDITORIA),
        parse_fn=_parse_bandeja_auditoria,
        table=t.bandeja_auditoria,
        pk_cols=["audit_id"],
    )


def _parse_tasa_historica(r: Mapping[str, str]) -> dict[str, Any]:
    return {
        "fecha": serde.p_date(r["fecha"]),
        "tasa_bcv_usd": serde.p_optdec(r.get("tasa_bcv_usd", "")),
        "tasa_bcv_euro": serde.p_optdec(r.get("tasa_bcv_euro", "")),
        "tasa_binance_promedio_diario": serde.p_optdec(r.get("tasa_binance_promedio_diario", "")),
        "diferencial_bcv_binance_pct": r.get("diferencial_bcv_binance_pct") or None,
        "fuente": r.get("fuente", ""),
        "notas": r.get("notas", ""),
    }


def _backfill_tasas_historicas(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    return _backfill_upsert(
        gw,
        pg,
        dry_run=dry_run,
        label="TasasHistoricasAuditoria",
        read_rows=lambda gw: gw.read_rows("TasasHistoricasAuditoria"),
        parse_fn=_parse_tasa_historica,
        table=t.tasas_historicas_auditoria,
        pk_cols=["fecha"],
    )


def _parse_lista_historica(r: Mapping[str, str]) -> dict[str, Any]:
    return {
        "codigo": r["codigo"],
        "producto_nombre": r.get("producto_nombre", ""),
        "precio_usd": serde.p_optdec(r.get("precio_usd", "")),
        "precio_bcv_euro": serde.p_optdec(r.get("precio_bcv_euro", "")),
        "fecha_corte": serde.p_optdate(r.get("fecha_corte", "")),
        "notas": r.get("notas", ""),
    }


def _backfill_listas_precios_historicas(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    # Sin clave natural en Sheets (ni codigo es único: puede repetirse en
    # distintos cortes) -- se reemplaza la tabla completa en cada corrida.
    return _backfill_replace_all(
        gw,
        pg,
        dry_run=dry_run,
        label="ListasPreciosHistoricas",
        sheet_tab="ListasPreciosHistoricas",
        parse_fn=_parse_lista_historica,
        table=t.listas_precios_historicas,
    )


def _parse_fecha_historica(r: Mapping[str, str]) -> dict[str, Any]:
    return {"so_id": r["so_id"], "fecha_historica": serde.p_date(r["fecha_historica"])}


def _backfill_fechas_historicas(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    return _backfill_upsert(
        gw,
        pg,
        dry_run=dry_run,
        label="FechasHistoricas",
        read_rows=lambda gw: gw.read_rows(g.T_FECHAS_HISTORICAS),
        parse_fn=_parse_fecha_historica,
        table=t.fechas_historicas,
        pk_cols=["so_id"],
    )


def _backfill_app_settings(
    gw: SheetGateway, pg: PostgresRepository | None, *, dry_run: bool
) -> int:
    return _backfill_upsert(
        gw,
        pg,
        dry_run=dry_run,
        label="_Meta",
        read_rows=lambda gw: gw.read_rows(g.T_META),
        parse_fn=lambda r: {"key": r["key"], "value": r.get("value", "")},
        table=t.app_settings,
        pk_cols=["key"],
    )


# Orden de migración: primero lo que las FKs de las demás tablas referencian
# (clientes antes que órdenes/pagos, órdenes antes que líneas/vinculaciones/
# bandeja/conciliación).
STEPS: list[tuple[str, Callable[[SheetGateway, PostgresRepository | None, bool], int]]] = [
    ("clientes", lambda gw, pg, dry: _backfill_clientes(gw, pg, dry_run=dry)),
    ("ordenes_venta", lambda gw, pg, dry: _backfill_ordenes(gw, pg, dry_run=dry)),
    ("lineas_orden", lambda gw, pg, dry: _backfill_lineas(gw, pg, dry_run=dry)),
    ("pagos", lambda gw, pg, dry: _backfill_pagos(gw, pg, dry_run=dry)),
    ("metodos_pago", lambda gw, pg, dry: _backfill_metodos_pago(gw, pg, dry_run=dry)),
    ("serie_tasas", lambda gw, pg, dry: _backfill_serie_tasas(gw, pg, dry_run=dry)),
    ("vinculaciones", lambda gw, pg, dry: _backfill_vinculaciones(gw, pg, dry_run=dry)),
    ("bandeja_facturacion", lambda gw, pg, dry: _backfill_bandeja(gw, pg, dry_run=dry)),
    ("conciliacion", lambda gw, pg, dry: _backfill_conciliacion(gw, pg, dry_run=dry)),
    ("exclusiones", lambda gw, pg, dry: _backfill_exclusiones(gw, pg, dry_run=dry)),
    (
        "descuentos_pronto_pago",
        lambda gw, pg, dry: _backfill_descuentos_pronto_pago(gw, pg, dry_run=dry),
    ),
    (
        "descuentos_volumen",
        lambda gw, pg, dry: _backfill_descuentos_volumen(gw, pg, dry_run=dry),
    ),
    (
        "descuentos_recompra",
        lambda gw, pg, dry: _backfill_descuentos_recompra(gw, pg, dry_run=dry),
    ),
    (
        "descuentos_producto",
        lambda gw, pg, dry: _backfill_descuentos_producto(gw, pg, dry_run=dry),
    ),
    (
        "descuentos_diferencial_cambiario",
        lambda gw, pg, dry: _backfill_descuentos_diferencial(gw, pg, dry_run=dry),
    ),
    ("descuento_bcv_completo", lambda gw, pg, dry: _backfill_bcv_completo(gw, pg, dry_run=dry)),
    (
        "promocion_primera_compra",
        lambda gw, pg, dry: _backfill_promocion_primera_compra(gw, pg, dry_run=dry),
    ),
    ("feriados", lambda gw, pg, dry: _backfill_feriados(gw, pg, dry_run=dry)),
    (
        "reglas_recurrencia",
        lambda gw, pg, dry: _backfill_reglas_recurrencia(gw, pg, dry_run=dry),
    ),
    (
        "usuarios_plataforma",
        lambda gw, pg, dry: _backfill_usuarios_plataforma(gw, pg, dry_run=dry),
    ),
    (
        "anomalias_aceptadas",
        lambda gw, pg, dry: _backfill_anomalias_aceptadas(gw, pg, dry_run=dry),
    ),
    (
        "bandeja_auditoria",
        lambda gw, pg, dry: _backfill_bandeja_auditoria(gw, pg, dry_run=dry),
    ),
    (
        "tasas_historicas_auditoria",
        lambda gw, pg, dry: _backfill_tasas_historicas(gw, pg, dry_run=dry),
    ),
    (
        "listas_precios_historicas",
        lambda gw, pg, dry: _backfill_listas_precios_historicas(gw, pg, dry_run=dry),
    ),
    (
        "fechas_historicas",
        lambda gw, pg, dry: _backfill_fechas_historicas(gw, pg, dry_run=dry),
    ),
    ("app_settings", lambda gw, pg, dry: _backfill_app_settings(gw, pg, dry_run=dry)),
]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(
        description="Copia todos los datos de Google Sheets a PostgreSQL (idempotente)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lee de Sheets y muestra conteos, sin escribir en Postgres.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Ejecuta el backfill real (upsert en Postgres).",
    )
    parser.add_argument(
        "--tables",
        type=str,
        default="",
        help="Lista separada por comas de tablas a migrar (default: todas).",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=None,
        help="Override de DATABASE_URL (default: variable de entorno / .env).",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        logger.error("Debe especificar --dry-run o --apply para ejecutar el script.")
        sys.exit(1)

    config = AppConfig.from_env()
    logger.info("Conectando a Google Sheets...")
    gw = _make_gateway(config)

    pg: PostgresRepository | None = None
    if not args.dry_run:
        database_url = args.database_url or config.database.url
        if not database_url:
            logger.error("Falta DATABASE_URL (variable de entorno o --database-url).")
            sys.exit(1)
        engine = make_engine(database_url)
        t.metadata.create_all(engine)  # idempotente -- no pisa tablas existentes
        pg = PostgresRepository(engine)
        logger.info("Conectado a Postgres.")

    selected = {x.strip() for x in args.tables.split(",") if x.strip()} or None

    resultados: dict[str, int] = {}
    for name, fn in STEPS:
        if selected and name not in selected:
            continue
        try:
            resultados[name] = fn(gw, pg, args.dry_run)
            logger.info("%s: %d filas", name, resultados[name])
        except Exception:
            logger.exception("Error migrando '%s' -- se continúa con las demás tablas", name)
            resultados[name] = -1

    print("\n" + "=" * 60)
    print("RESUMEN" + (" (DRY-RUN, nada escrito en Postgres)" if args.dry_run else ""))
    print("=" * 60)
    for name, count in resultados.items():
        estado = "ERROR" if count < 0 else f"{count} filas"
        print(f"  - {name}: {estado}")
    print("=" * 60)

    if any(c < 0 for c in resultados.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
