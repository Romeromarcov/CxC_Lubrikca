"""``PostgresRepository`` — implementación de ``Repository`` sobre Postgres.

Traduce cada método del repositorio a SQL vía SQLAlchemy Core, igual que
``SheetsRepository`` (``cxc.sheets.repository``) lo hace hoy para Sheets --
mismo contrato, mismo comportamiento observable (el motor de descuentos, el
sync incremental y el reconciliador no distinguen cuál de las dos
implementaciones están usando).

Diferencia clave de plomería respecto a Sheets: donde ``GspreadGateway``
necesita un merge parcial de columnas para no pisar campos editados a mano
(ver ``gateway.py``), acá un ``UPDATE``/``INSERT ... ON CONFLICT`` que solo
nombra las columnas que cada escritor posee ya tiene esa propiedad de forma
nativa -- no hace falta ningún truco adicional.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, and_, delete, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..models import (
    BandejaFacturacion,
    Cliente,
    Conciliacion,
    Condicion,
    DescuentoAplicado,
    DescuentoBCVCompleto,
    DescuentoDiferencialCambiario,
    DescuentoMarcaCategoria,
    DescuentoProducto,
    DescuentoProntoPago,
    DescuentoRecompra,
    DescuentoVolumen,
    EstadoBandeja,
    EstadoVinculacion,
    ExclusionRegla,
    Feriado,
    LineaOrden,
    MetodoPago,
    Moneda,
    OrdenVenta,
    Pago,
    PromocionPrimeraCompra,
    ReglaRecurrencia,
    ResultadoConciliacion,
    SerieTasa,
    TipoBeneficio,
    TipoDescuento,
    TipoFeriado,
    TipoTasa,
    VentasTeorico,
    Vinculacion,
)
from ..repositories import Repository
from . import schema as t
from .engine import make_engine

_META_LAST_SYNC = "last_sync"

# --- Defaults de diferencial cambiario -- mismos 3 que SheetsRepository
# usa cuando la tabla está vacía (ver sheets/repository.py). Se replican acá
# para que el motor de descuentos tenga las mismas reglas por defecto sin
# importar qué backend esté activo.
_DIFERENCIAL_DEFAULTS = [
    DescuentoDiferencialCambiario(
        regla_id="DIF_35_VES",
        nombre="35% Fijo VES a USD",
        tipo_diferencial="fijo_35_ves_usd",
        tipo_calculo="fijo",
        porcentaje_fijo=Decimal("0.35"),
        unidad_medida="USD",
        monedas_aplicables="USD",
        listas_aplicables="LISTAS_VES",
    ),
    DescuentoDiferencialCambiario(
        regla_id="DIF_EQUIPARAR",
        nombre="Equiparar Binance N/C",
        tipo_diferencial="equiparar_binance",
        tipo_calculo="variable",
        porcentaje_fijo=Decimal("0"),
        unidad_medida="USD",
        monedas_aplicables="*",
        listas_aplicables="LISTAS_VES",
    ),
    DescuentoDiferencialCambiario(
        regla_id="DIF_CANDIDATOS_CIERRE",
        nombre="Candidatos a Cierre de Factura (reporte)",
        tipo_diferencial="candidato_cierre_factura",
        tipo_calculo="variable",
        porcentaje_fijo=Decimal("0"),
        unidad_medida="USD",
        monedas_aplicables="*",
        listas_aplicables="LISTAS_VES",
        activo=False,
    ),
]


def _dataclass_row(obj: Any) -> dict[str, Any]:
    """dataclass de cxc.models -> dict listo para SQLAlchemy: Decimal/date/
    bool tal cual, Enum como su .value -- válido porque en cada tabla de
    config el nombre de cada campo del dataclass coincide 1:1 con el nombre
    de columna (ver cxc.db.schema)."""
    from dataclasses import fields
    from enum import Enum

    out: dict[str, Any] = {}
    for f in fields(obj):
        val = getattr(obj, f.name)
        out[f.name] = val.value if isinstance(val, Enum) else val
    return out


def _upsert(conn: Any, table: Any, rows: list[dict[str, Any]], pk_cols: list[str]) -> None:
    """INSERT ... ON CONFLICT DO UPDATE, solo con las columnas presentes en

    ``rows`` -- un escritor que no conoce una columna (p.ej. el sync de
    Pagos no conoce ``recibido``) simplemente nunca la toca.
    """
    if not rows:
        return
    stmt = pg_insert(table).values(rows)
    update_cols = {
        col: stmt.excluded[col] for col in rows[0] if col not in pk_cols
    }
    if update_cols:
        stmt = stmt.on_conflict_do_update(index_elements=pk_cols, set_=update_cols)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
    conn.execute(stmt)


class PostgresRepository(Repository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @classmethod
    def from_url(cls, database_url: str) -> PostgresRepository:
        return cls(make_engine(database_url))

    # --- SerieTasas (append-only) --------------------------------------------
    def last_serie_tasa(self) -> SerieTasa | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(t.serie_tasas).order_by(t.serie_tasas.c.timestamp.desc()).limit(1)
            ).first()
        return _row_to_serie(row) if row else None

    def append_serie_tasa(self, fila: SerieTasa) -> None:
        with self._engine.begin() as conn:
            conn.execute(insert(t.serie_tasas).values(**_serie_to_row(fila)))

    def trailing_failed_captures(self) -> int:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(t.serie_tasas).order_by(t.serie_tasas.c.timestamp.desc())
            ).all()
        count = 0
        for row in rows:
            if row.capturada_ok:
                break
            count += 1
        return count

    def serie_tasas_del_dia(self, fecha: date) -> list[SerieTasa]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(t.serie_tasas).where(
                    t.serie_tasas.c.timestamp >= datetime.combine(fecha, datetime.min.time()),
                    t.serie_tasas.c.timestamp < datetime.combine(fecha, datetime.max.time()),
                )
            ).all()
        return [_row_to_serie(r) for r in rows]

    # --- Cursor de sync (app_settings) ---------------------------------------
    def get_last_sync(self) -> datetime | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(t.app_settings.c.value).where(t.app_settings.c.key == _META_LAST_SYNC)
            ).first()
        return datetime.fromisoformat(row.value) if row and row.value else None

    def set_last_sync(self, cursor: datetime) -> None:
        with self._engine.begin() as conn:
            _upsert(
                conn,
                t.app_settings,
                [{"key": _META_LAST_SYNC, "value": cursor.isoformat()}],
                ["key"],
            )

    # --- Configuración genérica (app_settings) --------------------------------
    def get_config(self, key: str) -> str | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(t.app_settings.c.value).where(t.app_settings.c.key == key)
            ).first()
        return row.value if row else None

    def set_config(self, key: str, value: str) -> None:
        with self._engine.begin() as conn:
            _upsert(conn, t.app_settings, [{"key": key, "value": value}], ["key"])

    def all_config(self) -> dict[str, str]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.app_settings)).all()
        return {r.key: r.value for r in rows}

    # --- Usuarios de la plataforma ---------------------------------------------
    def get_usuario_plataforma(self, email: str) -> dict[str, str] | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(t.usuarios_plataforma).where(
                    t.usuarios_plataforma.c.email == email.strip().lower()
                )
            ).first()
        return _row_to_usuario(row) if row else None

    def all_usuarios_plataforma(self) -> list[dict[str, str]]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.usuarios_plataforma)).all()
        return [_row_to_usuario(r) for r in rows]

    def upsert_usuario_plataforma(self, row: dict[str, str]) -> None:
        pg_row = {
            "email": row["email"].strip().lower(),
            "nombre": row.get("nombre_odoo") or row.get("nombre") or "",
            "rol": row.get("rol") or "",
            "password_hash": row.get("password_hash") or None,
            "salt": row.get("salt") or None,
            "activo": str(row.get("activo", "TRUE")).strip().upper() != "FALSE",
            "fecha_registro": row.get("fecha_registro") or "",
        }
        with self._engine.begin() as conn:
            _upsert(conn, t.usuarios_plataforma, [pg_row], ["email"])

    # --- Espejo (upsert por PK, el sync es el único escritor) ---------------
    def upsert_clientes(self, filas: list[Cliente]) -> None:
        with self._engine.begin() as conn:
            _upsert(conn, t.clientes, [_cliente_to_row(c) for c in filas], ["cliente_id"])

    def upsert_ordenes(self, filas: list[OrdenVenta]) -> None:
        with self._engine.begin() as conn:
            _upsert(conn, t.ordenes_venta, [_orden_to_row(o) for o in filas], ["so_id"])

    def upsert_lineas(self, filas: list[LineaOrden]) -> None:
        with self._engine.begin() as conn:
            _upsert(conn, t.lineas_orden, [_linea_to_row(ln) for ln in filas], ["linea_id"])

    def upsert_pagos(self, filas: list[Pago]) -> None:
        with self._engine.begin() as conn:
            _upsert(conn, t.pagos, [_pago_to_row(p) for p in filas], ["pago_id"])

    # --- Lecturas para el motor -----------------------------------------------
    def get_cliente(self, cliente_id: str) -> Cliente | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(t.clientes).where(t.clientes.c.cliente_id == cliente_id)
            ).first()
        return _row_to_cliente(row) if row else None

    def all_clientes(self) -> list[Cliente]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.clientes)).all()
        return [_row_to_cliente(r) for r in rows]

    def get_orden(self, so_id: str) -> OrdenVenta | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(t.ordenes_venta).where(t.ordenes_venta.c.so_id == so_id)
            ).first()
        return _row_to_orden(row) if row else None

    def all_ordenes(self) -> list[OrdenVenta]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.ordenes_venta)).all()
        return [_row_to_orden(r) for r in rows]

    def lineas_de_orden(self, so_id: str) -> list[LineaOrden]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(t.lineas_orden).where(t.lineas_orden.c.so_id == so_id)
            ).all()
        return [_row_to_linea(r) for r in rows]

    def all_lineas(self) -> list[LineaOrden]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.lineas_orden)).all()
        return [_row_to_linea(r) for r in rows]

    def get_pago(self, pago_id: str) -> Pago | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(t.pagos).where(t.pagos.c.pago_id == pago_id)).first()
        return _row_to_pago(row) if row else None

    def all_pagos(self) -> list[Pago]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.pagos)).all()
        return [_row_to_pago(r) for r in rows]

    def all_pagos_full(self) -> list[dict[str, str]]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.pagos)).all()
        return [_row_to_pago_full(r) for r in rows]

    def marcar_pagos_recibido(
        self, pago_ids: list[str], numero_recibido: str, fecha_recibido: datetime, recibido_por: str
    ) -> list[dict[str, str]]:
        if not pago_ids:
            return []
        with self._engine.begin() as conn:
            conn.execute(
                update(t.pagos)
                .where(t.pagos.c.pago_id.in_(pago_ids))
                .values(
                    recibido=True,
                    numero_recibido=numero_recibido,
                    fecha_recibido=fecha_recibido,
                    recibido_por=recibido_por,
                )
            )
            rows = conn.execute(
                select(t.pagos).where(t.pagos.c.pago_id.in_(pago_ids))
            ).all()
        return [_row_to_pago_full(r) for r in rows]

    def get_metodo_pago(self, metodo_id: str) -> MetodoPago | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(t.metodos_pago).where(t.metodos_pago.c.metodo_id == metodo_id)
            ).first()
        return _row_to_metodo(row) if row else None

    def all_serie_tasas(self) -> list[SerieTasa]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(t.serie_tasas).order_by(t.serie_tasas.c.timestamp)
            ).all()
        return [_row_to_serie(r) for r in rows]

    def vinculaciones_de_orden(self, so_id: str) -> list[Vinculacion]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(t.vinculaciones).where(t.vinculaciones.c.so_id == so_id)
            ).all()
        return [_row_to_vinc(r) for r in rows]

    def all_vinculaciones(self) -> list[Vinculacion]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.vinculaciones)).all()
        return [_row_to_vinc(r) for r in rows]

    def update_vinculacion(self, vinc: Vinculacion) -> None:
        with self._engine.begin() as conn:
            _upsert(conn, t.vinculaciones, [_vinc_to_row(vinc)], ["vinc_id"])

    def update_vinculaciones(self, vincs: list[Vinculacion]) -> None:
        if not vincs:
            return
        with self._engine.begin() as conn:
            _upsert(conn, t.vinculaciones, [_vinc_to_row(v) for v in vincs], ["vinc_id"])

    # --- Reglas de descuento (config, solo lectura en esta fase) -------------
    def descuentos_marca_categoria(self) -> list[DescuentoMarcaCategoria]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.descuentos_pronto_pago)).all()
        return [_row_to_pronto_pago(r) for r in rows]

    def append_descuento_pronto_pago(self, regla: DescuentoMarcaCategoria) -> None:
        with self._engine.begin() as conn:
            _upsert(conn, t.descuentos_pronto_pago, [_dataclass_row(regla)], ["regla_id"])

    def descuentos_volumen(self) -> list[DescuentoVolumen]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.descuentos_volumen)).all()
        return [_row_to_volumen(r) for r in rows]

    def append_descuento_volumen(self, regla: DescuentoVolumen) -> None:
        with self._engine.begin() as conn:
            _upsert(conn, t.descuentos_volumen, [_dataclass_row(regla)], ["regla_id"])

    def descuentos_recompra(self) -> list[DescuentoRecompra]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.descuentos_recompra)).all()
        return [_row_to_recompra(r) for r in rows]

    def append_descuento_recompra(self, regla: DescuentoRecompra) -> None:
        with self._engine.begin() as conn:
            _upsert(conn, t.descuentos_recompra, [_dataclass_row(regla)], ["regla_id"])

    def descuentos_diferencial_cambiario(self) -> list[DescuentoDiferencialCambiario]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.descuentos_diferencial_cambiario)).all()
        if not rows:
            return list(_DIFERENCIAL_DEFAULTS)
        return [_row_to_diferencial(r) for r in rows]

    def append_descuento_diferencial_cambiario(
        self, regla: DescuentoDiferencialCambiario
    ) -> None:
        with self._engine.begin() as conn:
            _upsert(conn, t.descuentos_diferencial_cambiario, [_dataclass_row(regla)], ["regla_id"])

    def descuentos_producto(self) -> list[DescuentoProducto]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.descuentos_producto)).all()
        return [_row_to_producto(r) for r in rows]

    def append_descuento_producto(self, regla: DescuentoProducto) -> None:
        with self._engine.begin() as conn:
            _upsert(conn, t.descuentos_producto, [_dataclass_row(regla)], ["regla_id"])

    def reglas_recurrencia(self) -> list[ReglaRecurrencia]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.reglas_recurrencia)).all()
        return [_row_to_regla_recurrencia(r) for r in rows]

    def set_regla_recurrencia_porcentaje(self, condicion: str, valor: Decimal) -> None:
        with self._engine.begin() as conn:
            result = conn.execute(
                update(t.reglas_recurrencia)
                .where(t.reglas_recurrencia.c.condicion == condicion)
                .values(valor=valor)
            )
            if result.rowcount == 0:
                conn.execute(
                    insert(t.reglas_recurrencia).values(
                        condicion=condicion,
                        tipo_beneficio=TipoBeneficio.PORCENTAJE.value,
                        valor=valor,
                        vigencia_desde=date.today(),
                        activo=True,
                    )
                )

    def descuento_bcv_completo(self) -> list[DescuentoBCVCompleto]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.descuento_bcv_completo)).all()
        return [_row_to_bcv_completo(r) for r in rows]

    def promociones_primera_compra(self) -> list[PromocionPrimeraCompra]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.promocion_primera_compra)).all()
        return [_row_to_promocion(r) for r in rows]

    def append_promocion_primera_compra(self, regla: PromocionPrimeraCompra) -> None:
        with self._engine.begin() as conn:
            _upsert(conn, t.promocion_primera_compra, [_dataclass_row(regla)], ["regla_id"])

    _REGLA_TABLAS = {
        "DescuentosRecompra": "descuentos_recompra",
        "DescuentosProntoPago": "descuentos_pronto_pago",
        "DescuentosMarcaCategoria": "descuentos_pronto_pago",
        "DescuentosVolumen": "descuentos_volumen",
        "PromocionPrimeraCompra": "promocion_primera_compra",
        "DescuentosProducto": "descuentos_producto",
        "DescuentosDiferencialCambiario": "descuentos_diferencial_cambiario",
    }

    def _regla_table(self, tabla: str) -> Any:
        table_name = self._REGLA_TABLAS.get(tabla)
        return getattr(t, table_name) if table_name else None

    def delete_regla(self, tabla: str, regla_id: str) -> bool:
        table = self._regla_table(tabla)
        if table is None:
            return False
        with self._engine.begin() as conn:
            result = conn.execute(delete(table).where(table.c.regla_id == regla_id))
        return bool(result.rowcount)

    def set_regla_activo(self, tabla: str, regla_id: str, activo: bool) -> bool:
        table = self._regla_table(tabla)
        if table is None:
            return False
        with self._engine.begin() as conn:
            result = conn.execute(
                update(table).where(table.c.regla_id == regla_id).values(activo=activo)
            )
        return bool(result.rowcount)

    def all_anomalias_aceptadas(self) -> list[dict[str, str]]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.anomalias_aceptadas)).all()
        return [
            {
                "anomalia_id": r.anomalia_id,
                "so_id": r.so_id,
                "factura_id": r.factura_id,
                "tipo_anomalia": r.tipo_anomalia,
                "motivo_aceptacion": r.motivo_aceptacion,
                "aprobado_por": r.aprobado_por,
                "timestamp_aprobacion": r.timestamp_aprobacion.isoformat(),
            }
            for r in rows
        ]

    def append_anomalia_aceptada(self, row: dict[str, str]) -> None:
        with self._engine.begin() as conn:
            _upsert(
                conn,
                t.anomalias_aceptadas,
                [
                    {
                        "anomalia_id": row["anomalia_id"],
                        "so_id": row.get("so_id", ""),
                        "factura_id": row.get("factura_id", ""),
                        "tipo_anomalia": row.get("tipo_anomalia", ""),
                        "motivo_aceptacion": row.get("motivo_aceptacion", ""),
                        "aprobado_por": row.get("aprobado_por", ""),
                        "timestamp_aprobacion": datetime.fromisoformat(
                            row["timestamp_aprobacion"]
                        ),
                    }
                ],
                ["anomalia_id"],
            )

    def all_listas_precios_historicas(self) -> list[dict[str, str]]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.listas_precios_historicas)).all()
        return [
            {
                "codigo": r.codigo,
                "producto_nombre": r.producto_nombre,
                "precio_usd": str(r.precio_usd),
                "precio_bcv_euro": str(r.precio_bcv_euro),
                "producto_id_odoo": str(r.producto_id_odoo) if r.producto_id_odoo else "",
            }
            for r in rows
        ]

    def replace_listas_precios_historicas(self, rows: list[dict[str, str]]) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(t.listas_precios_historicas))
            if rows:
                conn.execute(
                    insert(t.listas_precios_historicas),
                    [
                        {
                            "codigo": r.get("codigo") or "",
                            "producto_nombre": r.get("producto_nombre") or "",
                            "precio_usd": r.get("precio_usd") or "0",
                            "precio_bcv_euro": r.get("precio_bcv_euro") or "0",
                            "producto_id_odoo": int(r["producto_id_odoo"])
                            if r.get("producto_id_odoo")
                            else None,
                        }
                        for r in rows
                    ],
                )

    def all_tasas_historicas_auditoria(self) -> list[dict[str, str]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(t.tasas_historicas_auditoria).order_by(t.tasas_historicas_auditoria.c.fecha)
            ).all()
        return [_row_to_tasa_historica(r) for r in rows]

    def replace_tasas_historicas_auditoria(self, rows: list[dict[str, str]]) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(t.tasas_historicas_auditoria))
            if rows:
                conn.execute(
                    insert(t.tasas_historicas_auditoria),
                    [
                        {
                            "fecha": date.fromisoformat(str(r["fecha"])[:10]),
                            "tasa_bcv_usd": r.get("tasa_bcv_usd") or None,
                            "tasa_bcv_euro": r.get("tasa_bcv_euro") or None,
                            "tasa_binance_promedio_diario": r.get("tasa_binance_promedio_diario")
                            or None,
                            "diferencial_bcv_binance_pct": r.get("diferencial_bcv_binance_pct")
                            or "",
                            "fuente": r.get("fuente") or "",
                            "notas": r.get("notas") or "",
                        }
                        for r in rows
                    ],
                )

    def upsert_tasa_historica_auditoria(self, row: dict[str, str]) -> None:
        with self._engine.begin() as conn:
            _upsert(
                conn,
                t.tasas_historicas_auditoria,
                [
                    {
                        "fecha": date.fromisoformat(str(row["fecha"])[:10]),
                        "tasa_bcv_usd": row.get("tasa_bcv_usd") or None,
                        "tasa_bcv_euro": row.get("tasa_bcv_euro") or None,
                        "tasa_binance_promedio_diario": row.get("tasa_binance_promedio_diario")
                        or None,
                        "diferencial_bcv_binance_pct": row.get("diferencial_bcv_binance_pct")
                        or "",
                        "fuente": row.get("fuente") or "",
                        "notas": row.get("notas") or "",
                    }
                ],
                ["fecha"],
            )

    def all_pagos_huerfanos_cerrados(self) -> list[dict[str, str]]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.pagos_huerfanos_cerrados)).all()
        return [
            {
                "pago_id": r.pago_id,
                "motivo": r.motivo or "",
                "cerrado_por": r.cerrado_por or "",
                "timestamp_cierre": r.timestamp_cierre.isoformat(),
            }
            for r in rows
        ]

    def upsert_pago_huerfano_cerrado(self, row: dict[str, str]) -> None:
        with self._engine.begin() as conn:
            _upsert(
                conn,
                t.pagos_huerfanos_cerrados,
                [
                    {
                        "pago_id": row["pago_id"],
                        "motivo": row.get("motivo") or "",
                        "cerrado_por": row.get("cerrado_por") or "",
                        "timestamp_cierre": datetime.fromisoformat(row["timestamp_cierre"]),
                    }
                ],
                ["pago_id"],
            )

    def all_pagos_tasa_binance_override(self) -> list[dict[str, str]]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.pagos_tasa_binance_override)).all()
        return [
            {
                "pago_id": r.pago_id,
                "tasa_binance": str(r.tasa_binance),
                "editado_por": r.editado_por or "",
                "timestamp_edicion": r.timestamp_edicion.isoformat(),
            }
            for r in rows
        ]

    def upsert_pago_tasa_binance_override(self, row: dict[str, str]) -> None:
        with self._engine.begin() as conn:
            _upsert(
                conn,
                t.pagos_tasa_binance_override,
                [
                    {
                        "pago_id": row["pago_id"],
                        "tasa_binance": row["tasa_binance"],
                        "editado_por": row.get("editado_por") or "",
                        "timestamp_edicion": datetime.fromisoformat(row["timestamp_edicion"]),
                    }
                ],
                ["pago_id"],
            )

    def all_descuentos_sistema_aprobados(self) -> list[dict[str, str]]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.descuentos_sistema_aprobados)).all()
        return [
            {
                "so_id": r.so_id,
                "monto": str(r.monto),
                "motivo": r.motivo or "",
                "aprobado_por": r.aprobado_por or "",
                "timestamp_aprobacion": r.timestamp_aprobacion.isoformat(),
                "activo": "true" if r.activo else "false",
            }
            for r in rows
        ]

    def upsert_descuento_sistema_aprobado(self, row: dict[str, str]) -> None:
        with self._engine.begin() as conn:
            _upsert(
                conn,
                t.descuentos_sistema_aprobados,
                [
                    {
                        "so_id": row["so_id"],
                        "monto": Decimal(str(row.get("monto") or "0")),
                        "motivo": row.get("motivo") or "",
                        "aprobado_por": row.get("aprobado_por") or "",
                        "timestamp_aprobacion": datetime.fromisoformat(
                            row["timestamp_aprobacion"]
                        ),
                        "activo": str(row.get("activo", "true")).strip().lower()
                        not in ("false", "0", "no"),
                    }
                ],
                ["so_id"],
            )

    def all_reglas_dias_credito_volumen(self) -> list[dict[str, str]]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.reglas_dias_credito_volumen)).all()
        return [
            {
                "regla_id": r.regla_id,
                "litros_minimo": str(r.litros_minimo),
                "litros_maximo": str(r.litros_maximo) if r.litros_maximo is not None else "",
                "dias_credito_max": str(r.dias_credito_max),
                "descripcion": r.descripcion or "",
                "activo": "true" if r.activo else "false",
            }
            for r in rows
        ]

    def upsert_regla_dias_credito_volumen(self, row: dict[str, str]) -> None:
        with self._engine.begin() as conn:
            _upsert(
                conn,
                t.reglas_dias_credito_volumen,
                [
                    {
                        "regla_id": row["regla_id"],
                        "litros_minimo": Decimal(str(row.get("litros_minimo") or "0")),
                        "litros_maximo": Decimal(str(row["litros_maximo"]))
                        if row.get("litros_maximo")
                        else None,
                        "dias_credito_max": int(row["dias_credito_max"]),
                        "descripcion": row.get("descripcion") or "",
                        "activo": str(row.get("activo", "true")).strip().lower()
                        not in ("false", "0", "no"),
                    }
                ],
                ["regla_id"],
            )

    def feriados(self) -> list[Feriado]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.feriados)).all()
        return [_row_to_feriado(r) for r in rows]

    def append_feriado(self, feriado: Feriado) -> None:
        with self._engine.begin() as conn:
            _upsert(
                conn,
                t.feriados,
                [
                    {
                        "fecha": feriado.fecha,
                        "descripcion": feriado.descripcion,
                        "tipo": feriado.tipo.value,
                    }
                ],
                ["fecha"],
            )

    def exclusiones(self) -> list[ExclusionRegla]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.exclusiones)).all()
        return [_row_to_exclusion(r) for r in rows]

    def save_exclusion(self, rule: ExclusionRegla) -> None:
        with self._engine.begin() as conn:
            existing = conn.execute(
                select(t.exclusiones.c.id).where(
                    or_(
                        and_(
                            t.exclusiones.c.regla_tipo_a == rule.regla_tipo_a,
                            t.exclusiones.c.regla_tipo_b == rule.regla_tipo_b,
                        ),
                        and_(
                            t.exclusiones.c.regla_tipo_a == rule.regla_tipo_b,
                            t.exclusiones.c.regla_tipo_b == rule.regla_tipo_a,
                        ),
                    )
                )
            ).first()
            if existing:
                conn.execute(
                    update(t.exclusiones)
                    .where(t.exclusiones.c.id == existing.id)
                    .values(activo=rule.activo)
                )
            else:
                conn.execute(
                    insert(t.exclusiones).values(
                        regla_tipo_a=rule.regla_tipo_a,
                        regla_tipo_b=rule.regla_tipo_b,
                        activo=rule.activo,
                    )
                )

    # --- Bandeja de facturación (salida del motor) ---------------------------
    def upsert_bandeja(self, fila: BandejaFacturacion) -> None:
        self.upsert_bandejas([fila])

    def upsert_bandejas(self, filas: list[BandejaFacturacion]) -> None:
        if not filas:
            return
        with self._engine.begin() as conn:
            _upsert(
                conn, t.bandeja_facturacion, [_bandeja_to_row(b) for b in filas], ["so_id"]
            )
            for b in filas:
                conn.execute(
                    delete(t.descuento_aplicado).where(t.descuento_aplicado.c.so_id == b.so_id)
                )
                if b.descuentos_detalle:
                    conn.execute(
                        insert(t.descuento_aplicado),
                        [
                            {
                                "so_id": b.so_id,
                                "origen": d.origen,
                                "descripcion": d.descripcion,
                                "monto": d.monto,
                            }
                            for d in b.descuentos_detalle
                        ],
                    )

    def get_bandeja(self, so_id: str) -> BandejaFacturacion | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(t.bandeja_facturacion).where(t.bandeja_facturacion.c.so_id == so_id)
            ).first()
            if not row:
                return None
            descuentos = conn.execute(
                select(t.descuento_aplicado).where(t.descuento_aplicado.c.so_id == so_id)
            ).all()
        return _row_to_bandeja(row, [_row_to_descuento_aplicado(d) for d in descuentos])

    def all_bandeja(self) -> list[BandejaFacturacion]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.bandeja_facturacion)).all()
            all_descuentos = conn.execute(select(t.descuento_aplicado)).all()
        descuentos_por_so: dict[str, list[DescuentoAplicado]] = {}
        for d in all_descuentos:
            descuentos_por_so.setdefault(d.so_id, []).append(_row_to_descuento_aplicado(d))
        return [_row_to_bandeja(r, descuentos_por_so.get(r.so_id, [])) for r in rows]

    # --- Teóricos de Ventas (Fase 10) -----------------------------------------
    def upsert_ventas_teorico(self, fila: VentasTeorico) -> None:
        with self._engine.begin() as conn:
            _upsert(conn, t.ventas_teoricos, [_dataclass_row(fila)], ["so_id"])

    def get_ventas_teorico(self, so_id: str) -> VentasTeorico | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(t.ventas_teoricos).where(t.ventas_teoricos.c.so_id == so_id)
            ).first()
        return _row_to_ventas_teorico(row) if row else None

    def all_ventas_teoricos(self) -> list[VentasTeorico]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.ventas_teoricos)).all()
        return [_row_to_ventas_teorico(r) for r in rows]

    # --- Conciliación ----------------------------------------------------------
    def upsert_conciliacion(self, fila: Conciliacion) -> None:
        self.upsert_conciliaciones([fila])

    def upsert_conciliaciones(self, filas: list[Conciliacion]) -> None:
        if not filas:
            return
        with self._engine.begin() as conn:
            _upsert(conn, t.conciliacion, [_conciliacion_to_row(c) for c in filas], ["so_id"])

    def all_conciliaciones(self) -> list[Conciliacion]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(t.conciliacion)).all()
        return [_row_to_conciliacion(r) for r in rows]


# --- Mapeo fila <-> dataclass (un par por tabla, espejo de cxc.sheets.serde) --


def _cliente_to_row(c: Cliente) -> dict[str, Any]:
    return {
        "cliente_id": c.cliente_id,
        "nombre": c.nombre,
        "vendedor_email": c.vendedor_email,
        "wh_iva_agent": c.wh_iva_agent,
        "wh_iva_rate": Decimal(str(c.wh_iva_rate)),
    }


def _row_to_cliente(r: Any) -> Cliente:
    return Cliente(
        cliente_id=r.cliente_id,
        nombre=r.nombre,
        vendedor_email=r.vendedor_email,
        wh_iva_agent=r.wh_iva_agent,
        wh_iva_rate=float(r.wh_iva_rate),
    )


def _orden_to_row(o: OrdenVenta) -> dict[str, Any]:
    return {
        "so_id": o.so_id,
        "cliente_id": o.cliente_id,
        "fecha": o.fecha,
        "fecha_entrega": o.fecha_entrega,
        "monto_total": o.monto_total,
        "lista_precios": o.lista_precios,
        "vendedor_email": o.vendedor_email,
        "es_primera_compra": o.es_primera_compra,
        "facturada": o.facturada,
        "factura_id": o.factura_id,
        "monto_facturado": o.monto_facturado,
        "estado_orden": o.estado_orden,
        "estado_entrega": o.estado_entrega,
        "entregada_completa": o.entregada_completa,
        "tiene_devolucion": o.tiene_devolucion,
        "dias_credito": o.dias_credito,
    }


def _row_to_orden(r: Any) -> OrdenVenta:
    return OrdenVenta(
        so_id=r.so_id,
        cliente_id=r.cliente_id,
        fecha=r.fecha,
        fecha_entrega=r.fecha_entrega,
        monto_total=r.monto_total,
        lista_precios=r.lista_precios,
        vendedor_email=r.vendedor_email,
        es_primera_compra=r.es_primera_compra,
        facturada=r.facturada,
        factura_id=r.factura_id,
        monto_facturado=r.monto_facturado,
        estado_orden=r.estado_orden,
        estado_entrega=r.estado_entrega,
        entregada_completa=r.entregada_completa,
        tiene_devolucion=r.tiene_devolucion,
        dias_credito=getattr(r, "dias_credito", 0) or 0,
    )


def _linea_to_row(ln: LineaOrden) -> dict[str, Any]:
    return {
        "linea_id": ln.linea_id,
        "so_id": ln.so_id,
        "producto": ln.producto,
        "marca": ln.marca,
        "categoria": ln.categoria,
        "cantidad": ln.cantidad,
        "precio_unitario": ln.precio_unitario,
        "cantidad_entregada": ln.cantidad_entregada,
        "descuento": ln.descuento,
        "subcategoria": ln.subcategoria,
        "presentacion_odoo": ln.presentacion_odoo,
    }


def _row_to_linea(r: Any) -> LineaOrden:
    return LineaOrden(
        linea_id=r.linea_id,
        so_id=r.so_id,
        producto=r.producto,
        marca=r.marca,
        categoria=r.categoria,
        cantidad=r.cantidad,
        precio_unitario=r.precio_unitario,
        cantidad_entregada=r.cantidad_entregada,
        descuento=r.descuento,
        subcategoria=getattr(r, "subcategoria", "") or "",
        presentacion_odoo=getattr(r, "presentacion_odoo", "") or "",
    )


def _pago_to_row(p: Pago) -> dict[str, Any]:
    # Solo columnas de espejo -- recibido/numero_recibido/fecha_recibido/
    # recibido_por (humanas, /api/cobranza/marcar-recibido) no forman parte
    # del dataclass Pago y por eso nunca entran en este dict ni se tocan.
    return {
        "pago_id": p.pago_id,
        "cliente_id": p.cliente_id,
        "monto": p.monto,
        "moneda": p.moneda.value,
        "metodo_pago": p.metodo_pago,
        "fecha_pago": p.fecha_pago,
        "vendedor_email": p.vendedor_email,
    }


def _row_to_pago(r: Any) -> Pago:
    return Pago(
        pago_id=r.pago_id,
        cliente_id=r.cliente_id,
        monto=r.monto,
        moneda=Moneda(r.moneda),
        metodo_pago=r.metodo_pago,
        fecha_pago=r.fecha_pago,
        vendedor_email=r.vendedor_email,
    )


def _row_to_pago_full(r: Any) -> dict[str, str]:
    return {
        "pago_id": r.pago_id,
        "cliente_id": r.cliente_id,
        "monto": str(r.monto),
        "moneda": r.moneda,
        "metodo_pago": r.metodo_pago,
        "fecha_pago": r.fecha_pago.isoformat(),
        "vendedor_email": r.vendedor_email,
        "recibido": "TRUE" if r.recibido else "FALSE",
        "numero_recibido": r.numero_recibido or "",
        "fecha_recibido": r.fecha_recibido.isoformat()[:19] if r.fecha_recibido else "",
        "recibido_por": r.recibido_por or "",
    }


def _row_to_metodo(r: Any) -> MetodoPago:
    return MetodoPago(
        metodo_id=r.metodo_id,
        nombre=r.nombre,
        moneda=Moneda(r.moneda),
        tipo_tasa=TipoTasa(r.tipo_tasa),
        es_contado=r.es_contado,
    )


def _serie_to_row(s: SerieTasa) -> dict[str, Any]:
    return {
        "timestamp": s.timestamp,
        "tasa_bcv": s.tasa_bcv,
        "tasa_binance": s.tasa_binance,
        "fuente": s.fuente,
        "es_heredada": s.es_heredada,
        "capturada_ok": s.capturada_ok,
        "tasa_binance_manana": s.tasa_binance_manana,
        "tasa_binance_tarde": s.tasa_binance_tarde,
        "tasa_binance_diario": s.tasa_binance_diario,
        "diferencial_bcv_binance_pct": s.diferencial_bcv_binance_pct,
        "tasa_bcv_euro": s.tasa_bcv_euro,
    }


def _row_to_serie(r: Any) -> SerieTasa:
    return SerieTasa(
        timestamp=r.timestamp,
        tasa_bcv=r.tasa_bcv,
        tasa_binance=r.tasa_binance,
        fuente=r.fuente,
        es_heredada=r.es_heredada,
        capturada_ok=r.capturada_ok,
        tasa_binance_manana=r.tasa_binance_manana,
        tasa_binance_tarde=r.tasa_binance_tarde,
        tasa_binance_diario=r.tasa_binance_diario,
        diferencial_bcv_binance_pct=r.diferencial_bcv_binance_pct,
        tasa_bcv_euro=r.tasa_bcv_euro,
    )


def _row_to_pronto_pago(r: Any) -> DescuentoProntoPago:
    return DescuentoProntoPago(
        regla_id=r.regla_id,
        marca=r.marca,
        categoria=r.categoria,
        min_cantidad=r.min_cantidad,
        max_cantidad=r.max_cantidad,
        unidad_medida=r.unidad_medida,
        tipo_beneficio=r.tipo_beneficio,
        ventana_pago_tipo=r.ventana_pago_tipo,
        ventana_pago_dias=r.ventana_pago_dias,
        porcentaje=r.porcentaje,
        monedas_aplicables=r.monedas_aplicables,
        listas_aplicables=r.listas_aplicables,
        vigencia_desde=r.vigencia_desde,
        vigencia_hasta=r.vigencia_hasta,
        activo=r.activo,
        tipo_descuento=TipoDescuento(r.tipo_descuento),
        requiere_pago_previo=r.requiere_pago_previo,
        aplica_a=r.aplica_a,
        descripcion=r.descripcion,
    )


def _row_to_volumen(r: Any) -> DescuentoVolumen:
    return DescuentoVolumen(
        regla_id=r.regla_id,
        marca=r.marca,
        categoria=r.categoria,
        litros_minimo=r.litros_minimo,
        porcentaje=r.porcentaje,
        min_cantidad=r.min_cantidad,
        max_cantidad=r.max_cantidad,
        unidad_medida=r.unidad_medida,
        tipo_evaluacion=r.tipo_evaluacion,
        dias_evaluacion=r.dias_evaluacion,
        vigencia_desde=r.vigencia_desde,
        vigencia_hasta=r.vigencia_hasta,
        listas_aplicables=r.listas_aplicables,
        activo=r.activo,
        requiere_pago_previo=r.requiere_pago_previo,
        aplica_a=r.aplica_a,
        descripcion=r.descripcion,
    )


def _row_to_recompra(r: Any) -> DescuentoRecompra:
    return DescuentoRecompra(
        regla_id=r.regla_id,
        marca=r.marca,
        categoria=r.categoria,
        min_cajas=r.min_cajas,
        max_cajas=r.max_cajas,
        min_cantidad=r.min_cantidad,
        max_cantidad=r.max_cantidad,
        unidad_medida=r.unidad_medida,
        tipo_beneficio=r.tipo_beneficio,
        porcentaje=r.porcentaje,
        listas_aplicables=r.listas_aplicables,
        vigencia_desde=r.vigencia_desde,
        vigencia_hasta=r.vigencia_hasta,
        activo=r.activo,
        requiere_pago_previo=r.requiere_pago_previo,
        aplica_a=r.aplica_a,
        descripcion=r.descripcion,
        ventana_pago_tipo=r.ventana_pago_tipo,
        ventana_pago_dias=r.ventana_pago_dias,
    )


def _row_to_diferencial(r: Any) -> DescuentoDiferencialCambiario:
    return DescuentoDiferencialCambiario(
        regla_id=r.regla_id,
        nombre=r.nombre,
        tipo_diferencial=r.tipo_diferencial,
        tipo_calculo=r.tipo_calculo,
        porcentaje_fijo=r.porcentaje_fijo,
        marca=r.marca,
        categoria=r.categoria,
        min_cantidad=r.min_cantidad,
        max_cantidad=r.max_cantidad,
        unidad_medida=r.unidad_medida,
        tipo_beneficio=r.tipo_beneficio,
        monedas_aplicables=r.monedas_aplicables,
        listas_aplicables=r.listas_aplicables,
        vigencia_desde=r.vigencia_desde,
        vigencia_hasta=r.vigencia_hasta,
        activo=r.activo,
        requiere_pago_previo=r.requiere_pago_previo,
        aplica_a=r.aplica_a,
        descripcion=r.descripcion,
    )


def _row_to_tasa_historica(r: Any) -> dict[str, str]:
    return {
        "fecha": r.fecha.isoformat(),
        "tasa_bcv_usd": str(r.tasa_bcv_usd) if r.tasa_bcv_usd is not None else "",
        "tasa_bcv_euro": str(r.tasa_bcv_euro) if r.tasa_bcv_euro is not None else "",
        "tasa_binance_promedio_diario": str(r.tasa_binance_promedio_diario)
        if r.tasa_binance_promedio_diario is not None
        else "",
        "diferencial_bcv_binance_pct": r.diferencial_bcv_binance_pct or "",
        "fuente": r.fuente or "",
        "notas": r.notas or "",
    }


def _row_to_usuario(r: Any) -> dict[str, str]:
    return {
        "email": r.email,
        "nombre_odoo": r.nombre or "",
        "password_hash": r.password_hash or "",
        "salt": r.salt or "",
        "rol": r.rol or "",
        "activo": "TRUE" if r.activo else "FALSE",
        "fecha_registro": r.fecha_registro or "",
    }


def _row_to_producto(r: Any) -> DescuentoProducto:
    return DescuentoProducto(
        regla_id=r.regla_id,
        productos=r.productos,
        marca=r.marca,
        categoria=r.categoria,
        min_cantidad=r.min_cantidad,
        max_cantidad=r.max_cantidad,
        unidad_medida=r.unidad_medida,
        tipo_beneficio=r.tipo_beneficio,
        porcentaje=r.porcentaje,
        monedas_aplicables=r.monedas_aplicables,
        listas_aplicables=r.listas_aplicables,
        vigencia_desde=r.vigencia_desde,
        vigencia_hasta=r.vigencia_hasta,
        activo=r.activo,
        requiere_pago_previo=r.requiere_pago_previo,
        aplica_a=r.aplica_a,
        descripcion=r.descripcion,
    )


def _row_to_regla_recurrencia(r: Any) -> ReglaRecurrencia:
    return ReglaRecurrencia(
        condicion=Condicion(r.condicion),
        tipo_beneficio=TipoBeneficio(r.tipo_beneficio),
        valor=r.valor,
        vigencia_desde=r.vigencia_desde,
        vigencia_hasta=r.vigencia_hasta,
        activo=r.activo,
        requiere_pago_previo=r.requiere_pago_previo,
        aplica_a=r.aplica_a,
        descripcion=r.descripcion,
    )


def _row_to_bcv_completo(r: Any) -> DescuentoBCVCompleto:
    return DescuentoBCVCompleto(
        vigencia_desde=r.vigencia_desde,
        porcentaje=r.porcentaje,
        vigencia_hasta=r.vigencia_hasta,
        activo=r.activo,
        requiere_pago_previo=r.requiere_pago_previo,
        aplica_a=r.aplica_a,
        descripcion=r.descripcion,
    )


def _row_to_promocion(r: Any) -> PromocionPrimeraCompra:
    return PromocionPrimeraCompra(
        regla_id=r.regla_id,
        tipo_beneficio=r.tipo_beneficio,
        productos=r.productos,
        valor=r.valor,
        compra_minima=r.compra_minima,
        regalo_tipo=r.regalo_tipo,
        vigencia_desde=r.vigencia_desde,
        vigencia_hasta=r.vigencia_hasta,
        descuento_fallback=r.descuento_fallback,
        categorias_aplica=r.categorias_aplica,
        marca=r.marca,
        categoria=r.categoria,
        min_cantidad=r.min_cantidad,
        max_cantidad=r.max_cantidad,
        unidad_medida=r.unidad_medida,
        listas_aplicables=r.listas_aplicables,
        solo_primera_compra=r.solo_primera_compra,
        activo=r.activo,
        requiere_pago_previo=r.requiere_pago_previo,
        aplica_a=r.aplica_a,
        descripcion=r.descripcion,
    )


def _row_to_feriado(r: Any) -> Feriado:
    return Feriado(fecha=r.fecha, descripcion=r.descripcion, tipo=TipoFeriado(r.tipo))


def _row_to_exclusion(r: Any) -> ExclusionRegla:
    return ExclusionRegla(
        regla_tipo_a=r.regla_tipo_a, regla_tipo_b=r.regla_tipo_b, activo=r.activo
    )


def _vinc_to_row(v: Vinculacion) -> dict[str, Any]:
    return {
        "vinc_id": v.vinc_id,
        "pago_id": v.pago_id,
        "so_id": v.so_id,
        "monto_aplicado": v.monto_aplicado,
        "hora_pago_confirmada": v.hora_pago_confirmada,
        "tasa_bcv_aplicada": v.tasa_bcv_aplicada,
        "tasa_binance_aplicada": v.tasa_binance_aplicada,
        "es_tasa_heredada": v.es_tasa_heredada,
        "equiv_usd_bcv": v.equiv_usd_bcv,
        "equiv_usd_binance": v.equiv_usd_binance,
        "equiv_ves_bcv": v.equiv_ves_bcv,
        "equiv_ves_binance": v.equiv_ves_binance,
        "confirmado_por": v.confirmado_por,
        "timestamp_registro": v.timestamp_registro,
        "estado": v.estado.value,
        "moneda_abono": v.moneda_abono.value,
        "tipo_tasa_abono": v.tipo_tasa_abono.value,
        "bcv_variante": v.bcv_variante,
    }


def _row_to_vinc(r: Any) -> Vinculacion:
    return Vinculacion(
        vinc_id=r.vinc_id,
        pago_id=r.pago_id,
        so_id=r.so_id,
        monto_aplicado=r.monto_aplicado,
        hora_pago_confirmada=r.hora_pago_confirmada,
        tasa_bcv_aplicada=r.tasa_bcv_aplicada,
        tasa_binance_aplicada=r.tasa_binance_aplicada,
        es_tasa_heredada=r.es_tasa_heredada,
        equiv_usd_bcv=r.equiv_usd_bcv,
        equiv_usd_binance=r.equiv_usd_binance,
        equiv_ves_bcv=r.equiv_ves_bcv,
        equiv_ves_binance=r.equiv_ves_binance,
        confirmado_por=r.confirmado_por,
        timestamp_registro=r.timestamp_registro,
        estado=EstadoVinculacion(r.estado),
        moneda_abono=Moneda(r.moneda_abono),
        tipo_tasa_abono=TipoTasa(r.tipo_tasa_abono),
        bcv_variante=r.bcv_variante,
    )


def _bandeja_to_row(b: BandejaFacturacion) -> dict[str, Any]:
    return {
        "so_id": b.so_id,
        "lista_aplicada": b.lista_aplicada,
        "precio_base_calculado": b.precio_base_calculado,
        "total_descuentos": b.total_descuentos,
        "ncs_calculadas": b.ncs_calculadas,
        "total_motor": b.total_motor,
        "requiere_revision": b.requiere_revision,
        "candidata_a_cierre": b.candidata_a_cierre,
        "aprobado_por": b.aprobado_por,
        "estado": b.estado.value,
        "equivalente_lista_usd": b.equivalente_lista_usd,
        "teorico_lista_ves": b.teorico_lista_ves,
        "teorico_lista_usd": b.teorico_lista_usd,
        "descuentos_teorico_ves": b.descuentos_teorico_ves,
        "descuentos_teorico_usd": b.descuentos_teorico_usd,
    }


def _row_to_bandeja(r: Any, descuentos: Sequence[DescuentoAplicado]) -> BandejaFacturacion:
    return BandejaFacturacion(
        so_id=r.so_id,
        lista_aplicada=r.lista_aplicada,
        precio_base_calculado=r.precio_base_calculado,
        descuentos_detalle=list(descuentos),
        total_descuentos=r.total_descuentos,
        ncs_calculadas=r.ncs_calculadas,
        total_motor=r.total_motor,
        requiere_revision=r.requiere_revision,
        candidata_a_cierre=r.candidata_a_cierre,
        aprobado_por=r.aprobado_por,
        estado=EstadoBandeja(r.estado),
        equivalente_lista_usd=r.equivalente_lista_usd,
        teorico_lista_ves=r.teorico_lista_ves,
        teorico_lista_usd=r.teorico_lista_usd,
        descuentos_teorico_ves=r.descuentos_teorico_ves,
        descuentos_teorico_usd=r.descuentos_teorico_usd,
    )


def _row_to_descuento_aplicado(r: Any) -> DescuentoAplicado:
    return DescuentoAplicado(origen=r.origen, descripcion=r.descripcion, monto=r.monto)


def _row_to_ventas_teorico(r: Any) -> VentasTeorico:
    return VentasTeorico(
        so_id=r.so_id,
        teorico_ves=r.teorico_ves,
        teorico_usd=r.teorico_usd,
        descuentos_teorico_ves=r.descuentos_teorico_ves,
        descuentos_teorico_usd=r.descuentos_teorico_usd,
        lista_ves_id=r.lista_ves_id,
        lista_usd_id=r.lista_usd_id,
        usa_fallback_ves=r.usa_fallback_ves,
        usa_fallback_usd=r.usa_fallback_usd,
        calculado_en=r.calculado_en,
        lineas_fingerprint=getattr(r, "lineas_fingerprint", "") or "",
    )


def _conciliacion_to_row(c: Conciliacion) -> dict[str, Any]:
    return {
        "so_id": c.so_id,
        "total_motor": c.total_motor,
        "monto_odoo": c.monto_odoo,
        "ncs_odoo": c.ncs_odoo,
        "diferencia": c.diferencia,
        "resultado": c.resultado.value,
        "revisado_por": c.revisado_por,
    }


def _row_to_conciliacion(r: Any) -> Conciliacion:
    return Conciliacion(
        so_id=r.so_id,
        total_motor=r.total_motor,
        monto_odoo=r.monto_odoo,
        ncs_odoo=r.ncs_odoo,
        diferencia=r.diferencia,
        resultado=ResultadoConciliacion(r.resultado),
        revisado_por=r.revisado_por,
    )
