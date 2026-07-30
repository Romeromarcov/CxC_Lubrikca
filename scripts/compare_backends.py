"""Fase 2 de la migración: compara Sheets vs Postgres, tabla por tabla.

Lee ambos backends con el mismo ``Repository`` (misma interfaz, dos
implementaciones) y compara conteos + una huella de contenido (suma de un
campo numérico representativo, o el set de claves primarias) para detectar
filas que falten, sobren, o difieran.

Uso:
    python scripts/compare_backends.py --database-url "postgresql://..."

    # Solo algunas tablas
    python scripts/compare_backends.py --database-url "..." --tables clientes,pagos

Salida: "SIN DISCREPANCIAS" y exit code 0 si todo coincide; si no, imprime el
detalle de cada tabla con diferencias y termina con exit code 1.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cxc.config import AppConfig  # noqa: E402
from cxc.db.engine import make_engine  # noqa: E402
from cxc.db.postgres_repository import PostgresRepository  # noqa: E402
from cxc.sheets import gateway as g  # noqa: E402
from cxc.sheets.repository import SheetsRepository  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("compare_backends")


def _build_sheets_repo() -> SheetsRepository:
    config = AppConfig.from_env()
    if (
        os.environ.get("GOOGLE_TOKEN_JSON")
        or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        or os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    ):
        gateway = g.GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
    else:
        gateway = g.GspreadGateway(config.sheets.spreadsheet_id, config.sheets.service_account_file)
    return SheetsRepository(gateway)


@dataclass
class CompareResult:
    name: str
    sheets_count: int
    postgres_count: int
    missing_in_postgres: list[str]
    extra_in_postgres: list[str]
    mismatched: list[str]

    @property
    def ok(self) -> bool:
        return not (self.missing_in_postgres or self.extra_in_postgres or self.mismatched)


def _compare_by_key(
    name: str,
    sheets_rows: dict[str, object],
    postgres_rows: dict[str, object],
    money_field: str | None,
    tolerance: Decimal = Decimal("0.01"),
) -> CompareResult:
    sheets_keys = set(sheets_rows)
    pg_keys = set(postgres_rows)
    missing = sorted(sheets_keys - pg_keys)
    extra = sorted(pg_keys - sheets_keys)

    mismatched: list[str] = []
    if money_field:
        for key in sorted(sheets_keys & pg_keys):
            v_sheets = getattr(sheets_rows[key], money_field, None)
            v_pg = getattr(postgres_rows[key], money_field, None)
            if v_sheets is None or v_pg is None:
                continue
            if abs(Decimal(str(v_sheets)) - Decimal(str(v_pg))) > tolerance:
                mismatched.append(f"{key} ({money_field}: sheets={v_sheets} postgres={v_pg})")

    return CompareResult(
        name,
        len(sheets_rows),
        len(postgres_rows),
        missing[:20],
        extra[:20],
        mismatched[:20],
    )


TableComparator = Callable[[SheetsRepository, PostgresRepository], CompareResult]


def _cmp_clientes(sr: SheetsRepository, pr: PostgresRepository) -> CompareResult:
    s = {c.cliente_id: c for c in sr.all_clientes()}
    p = {c.cliente_id: c for c in pr.all_clientes()}
    return _compare_by_key("clientes", s, p, money_field=None)


def _cmp_ordenes(sr: SheetsRepository, pr: PostgresRepository) -> CompareResult:
    s = {o.so_id: o for o in sr.all_ordenes()}
    p = {o.so_id: o for o in pr.all_ordenes()}
    return _compare_by_key("ordenes_venta", s, p, money_field="monto_total")


def _cmp_lineas(sr: SheetsRepository, pr: PostgresRepository) -> CompareResult:
    s = {ln.linea_id: ln for ln in sr.all_lineas()}
    p = {ln.linea_id: ln for ln in pr.all_lineas()}
    return _compare_by_key("lineas_orden", s, p, money_field="cantidad")


def _cmp_pagos(sr: SheetsRepository, pr: PostgresRepository) -> CompareResult:
    s = {p.pago_id: p for p in sr.all_pagos()}
    p = {p.pago_id: p for p in pr.all_pagos()}
    return _compare_by_key("pagos", s, p, money_field="monto")


def _cmp_serie_tasas(sr: SheetsRepository, pr: PostgresRepository) -> CompareResult:
    s = {f.timestamp.isoformat(): f for f in sr.all_serie_tasas()}
    p = {f.timestamp.isoformat(): f for f in pr.all_serie_tasas()}
    return _compare_by_key("serie_tasas", s, p, money_field="tasa_bcv")


def _cmp_vinculaciones(sr: SheetsRepository, pr: PostgresRepository) -> CompareResult:
    s = {v.vinc_id: v for v in sr.all_vinculaciones()}
    p = {v.vinc_id: v for v in pr.all_vinculaciones()}
    return _compare_by_key("vinculaciones", s, p, money_field="monto_aplicado")


def _cmp_bandeja(sr: SheetsRepository, pr: PostgresRepository) -> CompareResult:
    s = {b.so_id: b for b in sr.all_bandeja()}
    p = {b.so_id: b for b in pr.all_bandeja()}
    return _compare_by_key("bandeja_facturacion", s, p, money_field="total_motor")


def _cmp_conciliacion(sr: SheetsRepository, pr: PostgresRepository) -> CompareResult:
    s = {c.so_id: c for c in sr.all_conciliaciones()}
    p = {c.so_id: c for c in pr.all_conciliaciones()}
    return _compare_by_key("conciliacion", s, p, money_field="diferencia")


def _cmp_reglas_recurrencia(sr: SheetsRepository, pr: PostgresRepository) -> CompareResult:
    # Sin clave natural -- solo compara conteo (ver nota en migrate_sheets_to_postgres.py).
    s_count = len(sr.reglas_recurrencia())
    p_count = len(pr.reglas_recurrencia())
    return CompareResult("reglas_recurrencia", s_count, p_count, [], [], [])


def _cmp_feriados(sr: SheetsRepository, pr: PostgresRepository) -> CompareResult:
    s = {f.fecha.isoformat(): f for f in sr.feriados()}
    p = {f.fecha.isoformat(): f for f in pr.feriados()}
    return _compare_by_key("feriados", s, p, money_field=None)


def _cmp_usuarios(sr: SheetsRepository, pr: PostgresRepository) -> CompareResult:
    s = {u["email"]: u for u in sr.all_usuarios_plataforma() if u.get("email")}
    p = {u["email"]: u for u in pr.all_usuarios_plataforma() if u.get("email")}
    return _compare_by_key("usuarios_plataforma", s, p, money_field=None)


TABLE_COMPARATORS: dict[str, TableComparator] = {
    "clientes": _cmp_clientes,
    "ordenes_venta": _cmp_ordenes,
    "lineas_orden": _cmp_lineas,
    "pagos": _cmp_pagos,
    "serie_tasas": _cmp_serie_tasas,
    "vinculaciones": _cmp_vinculaciones,
    "bandeja_facturacion": _cmp_bandeja,
    "conciliacion": _cmp_conciliacion,
    "reglas_recurrencia": _cmp_reglas_recurrencia,
    "feriados": _cmp_feriados,
    "usuarios_plataforma": _cmp_usuarios,
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Connection string de Postgres. Si se omite, usa DATABASE_URL del entorno.",
    )
    parser.add_argument(
        "--tables",
        default=None,
        help=f"Lista separada por comas. Disponibles: {', '.join(TABLE_COMPARATORS)}",
    )
    args = parser.parse_args()

    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        parser.error("Falta --database-url o la variable de entorno DATABASE_URL")

    tables_to_run = list(TABLE_COMPARATORS)
    if args.tables:
        requested = [x.strip() for x in args.tables.split(",") if x.strip()]
        unknown = [x for x in requested if x not in TABLE_COMPARATORS]
        if unknown:
            parser.error(
                f"Tablas desconocidas: {unknown}. Disponibles: {', '.join(TABLE_COMPARATORS)}"
            )
        tables_to_run = requested

    logger.info("Conectando a Google Sheets...")
    sheets_repo = _build_sheets_repo()
    logger.info("Conectando a Postgres...")
    pg_repo = PostgresRepository(make_engine(database_url))

    results: list[CompareResult] = []
    for name in tables_to_run:
        logger.info("Comparando %s...", name)
        results.append(TABLE_COMPARATORS[name](sheets_repo, pg_repo))

    print("\n" + "=" * 78)
    print(
        f"{'tabla':<24}{'sheets':>10}{'postgres':>10}{'faltan':>10}{'sobran':>10}{'difieren':>12}"
    )
    for r in results:
        print(
            f"{r.name:<24}{r.sheets_count:>10}{r.postgres_count:>10}"
            f"{len(r.missing_in_postgres):>10}{len(r.extra_in_postgres):>10}"
            f"{len(r.mismatched):>12}"
        )

    discrepancias = [r for r in results if not r.ok]
    if discrepancias:
        print("\n" + "=" * 78)
        print("DETALLE DE DISCREPANCIAS (máx. 20 por tabla)")
        for r in discrepancias:
            print(f"\n[{r.name}]")
            if r.missing_in_postgres:
                print(f"  faltan en Postgres: {r.missing_in_postgres}")
            if r.extra_in_postgres:
                print(f"  sobran en Postgres: {r.extra_in_postgres}")
            if r.mismatched:
                print(f"  valores distintos: {r.mismatched}")
        print(f"\n{len(discrepancias)} tabla(s) con discrepancias.")
        return 1

    print("\nSIN DISCREPANCIAS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
