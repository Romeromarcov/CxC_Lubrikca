"""Compara Google Sheets vs PostgreSQL entidad por entidad.

Herramienta de solo lectura para la ventana de validación en paralelo (ver
plan de migración): corre el sync real contra ambos backends y usa este
script periódicamente para confirmar que no hay discrepancias antes de
cortar producción a Postgres.

Uso:
  python scripts/compare_backends.py
  python scripts/compare_backends.py --sample-size 100
  python scripts/compare_backends.py --database-url postgresql://...

No escribe en ningún lado. Compara:
  - Conteos de filas por tabla (clientes, órdenes, líneas, pagos, reglas de
    descuento, feriados, exclusiones, tablas de auditoría, etc.)
  - Para una muestra de órdenes: los campos que calcula cada backend en
    BandejaFacturacion (total_motor, estado, requiere_revision,
    candidata_a_cierre) y Conciliacion (resultado, diferencia,
    total_motor, monto_odoo) -- estos son el resultado del motor de
    descuentos y del reconciliador, así que una discrepancia acá señala un
    problema real de datos, no solo de conteo.

Exit code 1 si se encontró alguna discrepancia (para correrlo en un
cron/CI de monitoreo durante la validación).
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import Table, func, select

from cxc.config import AppConfig
from cxc.db import schema as t
from cxc.db.engine import make_engine
from cxc.db.postgres_repository import PostgresRepository
from cxc.repositories import Repository
from cxc.sheets import gateway as g
from cxc.sheets.gateway import GspreadGateway, SheetGateway
from cxc.sheets.repository import SheetsRepository

_TOLERANCE = Decimal("0.01")

# Ver el mismo comentario en scripts/migrate_sheets_to_postgres.py: sin
# espaciar las lecturas, este comparador puede disparar la cuota de la API
# de Sheets y GspreadGateway.read_rows() atrapa esa excepción devolviendo
# [] en silencio -- eso se vería como una "discrepancia" (Postgres con más
# filas que Sheets) que en realidad es un falso positivo por cuota, no un
# problema real de datos.
_SHEETS_READ_DELAY_SECONDS = 1.5


class _PacedGateway(SheetGateway):
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


@dataclass
class Discrepancy:
    entidad: str
    detalle: str


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


def _pg_count(engine: Any, table: Table) -> int:
    with engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


def _report_count(
    label: str, sheets_count: int, pg_count: int, discrepancies: list[Discrepancy]
) -> None:
    status = "OK" if sheets_count == pg_count else "DIFF"
    print(f"  [{status}] {label}: sheets={sheets_count} postgres={pg_count}")
    if sheets_count != pg_count:
        discrepancies.append(
            Discrepancy(label, f"conteo distinto: sheets={sheets_count} postgres={pg_count}")
        )


def _compare_raw_counts(
    gw: SheetGateway, engine: Any, discrepancies: list[Discrepancy]
) -> None:
    print("\n--- Conteos (lectura cruda de tabla) ---")
    pares: list[tuple[str, str, Table]] = [
        ("clientes", g.T_CLIENTES, t.clientes),
        ("lineas_orden", g.T_LINEAS, t.lineas_orden),
        ("pagos", g.T_PAGOS, t.pagos),
        ("metodos_pago", g.T_METODOS, t.metodos_pago),
        ("serie_tasas", g.T_SERIE, t.serie_tasas),
        ("usuarios_plataforma", "UsuariosPlataforma", t.usuarios_plataforma),
        ("anomalias_aceptadas", "AnomaliasAceptadas", t.anomalias_aceptadas),
        ("bandeja_auditoria", g.T_BANDEJA_AUDITORIA, t.bandeja_auditoria),
        ("tasas_historicas_auditoria", "TasasHistoricasAuditoria", t.tasas_historicas_auditoria),
        ("listas_precios_historicas", "ListasPreciosHistoricas", t.listas_precios_historicas),
        ("fechas_historicas", g.T_FECHAS_HISTORICAS, t.fechas_historicas),
    ]
    for label, sheet_tab, table in pares:
        _report_count(label, len(gw.read_rows(sheet_tab)), _pg_count(engine, table), discrepancies)


def _compare_repository_counts(
    sheets_repo: Repository, pg_repo: Repository, discrepancies: list[Discrepancy]
) -> None:
    print("\n--- Conteos (vía Repository -- misma semántica que usa la app) ---")
    pares: list[tuple[str, Any]] = [
        ("ordenes_venta", lambda r: r.all_ordenes()),
        ("vinculaciones", lambda r: r.all_vinculaciones()),
        ("bandeja_facturacion", lambda r: r.all_bandeja()),
        ("conciliacion", lambda r: r.all_conciliaciones()),
        ("descuentos_pronto_pago", lambda r: r.descuentos_marca_categoria()),
        ("descuentos_volumen", lambda r: r.descuentos_volumen()),
        ("descuentos_recompra", lambda r: r.descuentos_recompra()),
        ("descuentos_diferencial_cambiario", lambda r: r.descuentos_diferencial_cambiario()),
        ("reglas_recurrencia", lambda r: r.reglas_recurrencia()),
        ("descuento_bcv_completo", lambda r: r.descuento_bcv_completo()),
        ("promociones_primera_compra", lambda r: r.promociones_primera_compra()),
        ("feriados", lambda r: r.feriados()),
        ("exclusiones", lambda r: r.exclusiones()),
    ]
    for label, fn in pares:
        _report_count(label, len(fn(sheets_repo)), len(fn(pg_repo)), discrepancies)


def _decimals_close(a: Decimal | None, b: Decimal | None) -> bool:
    if a is None or b is None:
        return a == b
    return abs(a - b) <= _TOLERANCE


def _compare_bandeja_sample(
    so_ids: list[str],
    bandeja_sheets: dict[str, Any],
    bandeja_pg: dict[str, Any],
    discrepancies: list[Discrepancy],
) -> None:
    for so_id in so_ids:
        bs = bandeja_sheets.get(so_id)
        bp = bandeja_pg.get(so_id)
        if bs is None or bp is None:
            if bs is not bp:
                discrepancies.append(
                    Discrepancy(
                        f"bandeja_facturacion[{so_id}]",
                        f"presente en sheets={bs is not None} "
                        f"presente en postgres={bp is not None}",
                    )
                )
            continue
        if not _decimals_close(bs.total_motor, bp.total_motor):
            discrepancies.append(
                Discrepancy(
                    f"bandeja_facturacion[{so_id}].total_motor",
                    f"sheets={bs.total_motor} postgres={bp.total_motor}",
                )
            )
        if bs.estado != bp.estado:
            discrepancies.append(
                Discrepancy(
                    f"bandeja_facturacion[{so_id}].estado",
                    f"sheets={bs.estado} postgres={bp.estado}",
                )
            )
        if bs.requiere_revision != bp.requiere_revision:
            discrepancies.append(
                Discrepancy(
                    f"bandeja_facturacion[{so_id}].requiere_revision",
                    f"sheets={bs.requiere_revision} postgres={bp.requiere_revision}",
                )
            )
        if bs.candidata_a_cierre != bp.candidata_a_cierre:
            discrepancies.append(
                Discrepancy(
                    f"bandeja_facturacion[{so_id}].candidata_a_cierre",
                    f"sheets={bs.candidata_a_cierre} postgres={bp.candidata_a_cierre}",
                )
            )


def _compare_conciliacion_sample(
    so_ids: list[str],
    conc_sheets: dict[str, Any],
    conc_pg: dict[str, Any],
    discrepancies: list[Discrepancy],
) -> None:
    for so_id in so_ids:
        cs = conc_sheets.get(so_id)
        cp = conc_pg.get(so_id)
        if cs is None or cp is None:
            if cs is not cp:
                discrepancies.append(
                    Discrepancy(
                        f"conciliacion[{so_id}]",
                        f"presente en sheets={cs is not None} "
                        f"presente en postgres={cp is not None}",
                    )
                )
            continue
        if cs.resultado != cp.resultado:
            discrepancies.append(
                Discrepancy(
                    f"conciliacion[{so_id}].resultado",
                    f"sheets={cs.resultado} postgres={cp.resultado}",
                )
            )
        if not _decimals_close(cs.diferencia, cp.diferencia):
            discrepancies.append(
                Discrepancy(
                    f"conciliacion[{so_id}].diferencia",
                    f"sheets={cs.diferencia} postgres={cp.diferencia}",
                )
            )
        if not _decimals_close(cs.total_motor, cp.total_motor):
            discrepancies.append(
                Discrepancy(
                    f"conciliacion[{so_id}].total_motor",
                    f"sheets={cs.total_motor} postgres={cp.total_motor}",
                )
            )
        if not _decimals_close(cs.monto_odoo, cp.monto_odoo):
            discrepancies.append(
                Discrepancy(
                    f"conciliacion[{so_id}].monto_odoo",
                    f"sheets={cs.monto_odoo} postgres={cp.monto_odoo}",
                )
            )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(
        description="Compara Google Sheets vs PostgreSQL entidad por entidad (solo lectura)."
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=50,
        help="Cantidad de órdenes a comparar en detalle (Bandeja/Conciliacion). Default: 50.",
    )
    parser.add_argument("--seed", type=int, default=None, help="Semilla para la muestra aleatoria.")
    parser.add_argument(
        "--database-url",
        type=str,
        default=None,
        help="Override de DATABASE_URL (default: variable de entorno / .env).",
    )
    args = parser.parse_args()

    config = AppConfig.from_env()
    database_url = args.database_url or config.database.url
    if not database_url:
        print("ERROR: falta DATABASE_URL (variable de entorno o --database-url).", file=sys.stderr)
        sys.exit(1)

    print("Conectando a Google Sheets...")
    gw = _make_gateway(config)
    print("Conectando a Postgres...")
    engine = make_engine(database_url)

    sheets_repo = SheetsRepository(gw)
    pg_repo = PostgresRepository(engine)

    discrepancies: list[Discrepancy] = []
    _compare_raw_counts(gw, engine, discrepancies)
    _compare_repository_counts(sheets_repo, pg_repo, discrepancies)

    print("\n--- Muestra de órdenes (BandejaFacturacion / Conciliacion) ---")
    so_ids_sheets = {o.so_id for o in sheets_repo.all_ordenes()}
    so_ids_pg = {o.so_id for o in pg_repo.all_ordenes()}
    so_ids_comunes = sorted(so_ids_sheets & so_ids_pg)
    solo_sheets = so_ids_sheets - so_ids_pg
    solo_pg = so_ids_pg - so_ids_sheets
    if solo_sheets:
        discrepancies.append(
            Discrepancy("ordenes_venta", f"{len(solo_sheets)} so_id solo en sheets: "
            f"{sorted(solo_sheets)[:10]}{'...' if len(solo_sheets) > 10 else ''}")
        )
    if solo_pg:
        discrepancies.append(
            Discrepancy("ordenes_venta", f"{len(solo_pg)} so_id solo en postgres: "
            f"{sorted(solo_pg)[:10]}{'...' if len(solo_pg) > 10 else ''}")
        )

    rng = random.Random(args.seed)
    muestra = (
        rng.sample(so_ids_comunes, args.sample_size)
        if len(so_ids_comunes) > args.sample_size
        else so_ids_comunes
    )
    print(f"  Comparando {len(muestra)} de {len(so_ids_comunes)} órdenes en común...")

    bandeja_sheets = {b.so_id: b for b in sheets_repo.all_bandeja()}
    bandeja_pg = {b.so_id: b for b in pg_repo.all_bandeja()}
    _compare_bandeja_sample(muestra, bandeja_sheets, bandeja_pg, discrepancies)

    conc_sheets = {c.so_id: c for c in sheets_repo.all_conciliaciones()}
    conc_pg = {c.so_id: c for c in pg_repo.all_conciliaciones()}
    _compare_conciliacion_sample(muestra, conc_sheets, conc_pg, discrepancies)

    print("\n" + "=" * 60)
    if not discrepancies:
        print("SIN DISCREPANCIAS -- Sheets y Postgres coinciden en todo lo comparado.")
        print("=" * 60)
        return

    print(f"{len(discrepancies)} DISCREPANCIA(S) ENCONTRADA(S):")
    print("=" * 60)
    for d in discrepancies:
        print(f"  - [{d.entidad}] {d.detalle}")
    sys.exit(1)


if __name__ == "__main__":
    main()
