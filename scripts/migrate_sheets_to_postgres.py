"""Backfill: copia todas las tablas de Google Sheets a PostgreSQL.

Fase 1 de la migración a Postgres. Lee el Google Sheet real (mismas
credenciales que usa la app hoy) y escribe en la base Postgres indicada,
tabla por tabla, respetando el orden de dependencias (FK).

Uso:
    # Solo cuenta filas en Sheets, no toca Postgres.
    python scripts/migrate_sheets_to_postgres.py --dry-run

    # Backfill real.
    python scripts/migrate_sheets_to_postgres.py --apply \\
        --database-url "postgresql://user:pass@host:puerto/db"

    # Repetir solo una tabla (por si una falló).
    python scripts/migrate_sheets_to_postgres.py --apply --tables clientes,pagos \\
        --database-url "postgresql://..."

Todas las escrituras son upsert (ON CONFLICT DO UPDATE) por clave primaria,
salvo donde se indica lo contrario -- correr el script más de una vez sobre
la misma base es seguro (idempotente), no duplica filas.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import Engine, delete, insert, select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

from cxc.config import AppConfig  # noqa: E402
from cxc.db import schema as t  # noqa: E402
from cxc.db.engine import make_engine  # noqa: E402
from cxc.db.postgres_repository import PostgresRepository  # noqa: E402
from cxc.sheets import gateway as g  # noqa: E402
from cxc.sheets import serde  # noqa: E402
from cxc.sheets.repository import SheetsRepository  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_sheets_to_postgres")


def _upsert(conn, table, rows: list[dict], pk_cols: list[str]) -> None:
    """INSERT ... ON CONFLICT DO UPDATE genérico (mismo patrón que
    ``cxc.db.postgres_repository._upsert``, reimplementado acá para no
    depender de un símbolo privado de otro módulo)."""
    if not rows:
        return
    stmt = pg_insert(table).values(rows)
    update_cols = {col: stmt.excluded[col] for col in rows[0] if col not in pk_cols}
    if update_cols:
        stmt = stmt.on_conflict_do_update(index_elements=pk_cols, set_=update_cols)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
    conn.execute(stmt)


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
class TableResult:
    name: str
    sheets_rows: int
    written: int
    error: str | None = None


TableMigrator = Callable[[SheetsRepository, Engine, bool], TableResult]


def _migrate_via_repo_method(
    table_name: str,
    read_fn: Callable[[SheetsRepository], list],
    write_fn: Callable[[PostgresRepository, list], None],
) -> TableMigrator:
    """Fábrica para tablas que ya tienen upsert_* en el Repository
    abstracto -- reusa el mismo código que usa la app en producción."""

    def _run(sheets_repo: SheetsRepository, engine: Engine, apply: bool) -> TableResult:
        rows = read_fn(sheets_repo)
        if apply:
            write_fn(PostgresRepository(engine), rows)
        return TableResult(table_name, len(rows), len(rows) if apply else 0)

    return _run


def _migrate_config_table(
    table_name: str,
    pg_table,
    pk_cols: list[str],
    tab_name: str,
    from_row_fn: Callable,
) -> TableMigrator:
    """Tablas de configuración con clave primaria natural (regla_id, fecha,
    metodo_id, ...) -- upsert directo, sin pasar por el Repository (que no
    expone escritura para estas, solo lectura -- las edita el admin panel
    fila a fila en Sheets)."""

    def _run(sheets_repo: SheetsRepository, engine: Engine, apply: bool) -> TableResult:
        raw_rows = sheets_repo._g.read_rows(tab_name)
        parsed = [from_row_fn(r) for r in raw_rows]
        pg_rows = [_dataclass_to_pg_row(p) for p in parsed]
        if apply and pg_rows:
            with engine.begin() as conn:
                _upsert(conn, pg_table, pg_rows, pk_cols)
        return TableResult(table_name, len(raw_rows), len(pg_rows) if apply else 0)

    return _run


def _dataclass_to_pg_row(dataclass_obj) -> dict:
    """Convierte un dataclass de cxc.models a dict listo para SQLAlchemy,
    dejando Decimal/date/bool tal cual (SQLAlchemy+psycopg los adapta
    directamente) y los Enum como su .value (mismo valor que usan las
    columnas ENUM nativas de cxc.db.schema)."""
    from dataclasses import fields
    from enum import Enum

    out = {}
    for f in fields(dataclass_obj):
        val = getattr(dataclass_obj, f.name)
        if isinstance(val, Enum):
            val = val.value
        out[f.name] = val
    return out


def _migrate_reglas_recurrencia(
    sheets_repo: SheetsRepository, engine: Engine, apply: bool
) -> TableResult:
    rows = sheets_repo.reglas_recurrencia()
    if apply:
        pg_rows = [
            {
                "condicion": r.condicion.value,
                "tipo_beneficio": r.tipo_beneficio.value,
                "valor": r.valor,
                "vigencia_desde": r.vigencia_desde,
                "vigencia_hasta": r.vigencia_hasta,
                "activo": r.activo,
            }
            for r in rows
        ]
        with engine.begin() as conn:
            # Sin clave natural en Sheets (ni en Postgres, salvo el id
            # autoincrement) -- replace completo en cada corrida, tabla de
            # configuración chica (nunca la toca el sync).
            conn.execute(delete(t.reglas_recurrencia))
            if pg_rows:
                conn.execute(insert(t.reglas_recurrencia), pg_rows)
    return TableResult("reglas_recurrencia", len(rows), len(rows) if apply else 0)


def _migrate_exclusiones(sheets_repo: SheetsRepository, engine: Engine, apply: bool) -> TableResult:
    rows = sheets_repo.exclusiones()
    if apply:
        pg_rows = [
            {"regla_tipo_a": r.regla_tipo_a, "regla_tipo_b": r.regla_tipo_b, "activo": r.activo}
            for r in rows
        ]
        with engine.begin() as conn:
            conn.execute(delete(t.exclusiones))
            if pg_rows:
                conn.execute(insert(t.exclusiones), pg_rows)
    return TableResult("exclusiones", len(rows), len(rows) if apply else 0)


def _migrate_metodos_pago(
    sheets_repo: SheetsRepository, engine: Engine, apply: bool
) -> TableResult:
    raw_rows = sheets_repo._g.read_rows(g.T_METODOS)
    metodos = [serde.metodo_from_row(r) for r in raw_rows]
    if apply:
        pg_rows = [
            {
                "metodo_id": m.metodo_id,
                "nombre": m.nombre,
                "moneda": m.moneda.value,
                "tipo_tasa": m.tipo_tasa.value,
                "es_contado": m.es_contado,
            }
            for m in metodos
        ]
        with engine.begin() as conn:
            _upsert(conn, t.metodos_pago, pg_rows, ["metodo_id"])
    return TableResult("metodos_pago", len(raw_rows), len(metodos) if apply else 0)


def _migrate_pagos(sheets_repo: SheetsRepository, engine: Engine, apply: bool) -> TableResult:
    """Pagos necesita trato especial: además de las columnas espejo
    (upsert_pagos, que el sync también escribe) hay que preservar las
    columnas humanas de cobranza (recibido/numero_recibido/fecha_recibido/
    recibido_por) que solo existen en la hoja cruda, no en el dataclass
    ``Pago``."""
    raw_rows = sheets_repo._g.read_rows(g.T_PAGOS)
    pagos = [serde.pago_from_row(r) for r in raw_rows]
    if apply:
        pg_repo = PostgresRepository(engine)
        pg_repo.upsert_pagos(pagos)
        human_rows = []
        for r in raw_rows:
            pid = r.get("pago_id")
            if not pid:
                continue
            row: dict = {"pago_id": pid}
            if str(r.get("recibido", "")).strip().upper() == "TRUE":
                row["recibido"] = True
            if r.get("numero_recibido"):
                row["numero_recibido"] = r["numero_recibido"]
            if r.get("fecha_recibido"):
                row["fecha_recibido"] = serde.p_dt(r["fecha_recibido"])
            if r.get("recibido_por"):
                row["recibido_por"] = r["recibido_por"]
            if len(row) > 1:
                human_rows.append(row)
        if human_rows:
            with engine.begin() as conn:
                _upsert(conn, t.pagos, human_rows, ["pago_id"])
    return TableResult("pagos", len(raw_rows), len(pagos) if apply else 0)


def _migrate_serie_tasas(sheets_repo: SheetsRepository, engine: Engine, apply: bool) -> TableResult:
    """Append-only: idempotente por timestamp -- si ya existe una fila con
    ese timestamp en Postgres, no se reinserta (evita duplicar en corridas
    repetidas del backfill)."""
    filas = sheets_repo._serie_rows()
    written = 0
    if apply and filas:
        with engine.begin() as conn:
            existentes = {row.timestamp for row in conn.execute(select(t.serie_tasas.c.timestamp))}
            pg_rows = []
            for f in filas:
                if f.timestamp in existentes:
                    continue
                pg_rows.append(
                    {
                        "timestamp": f.timestamp,
                        "tasa_bcv": f.tasa_bcv,
                        "tasa_binance": f.tasa_binance,
                        "fuente": f.fuente,
                        "es_heredada": f.es_heredada,
                        "capturada_ok": f.capturada_ok,
                        "tasa_binance_manana": f.tasa_binance_manana,
                        "tasa_binance_tarde": f.tasa_binance_tarde,
                        "tasa_binance_diario": f.tasa_binance_diario,
                        "diferencial_bcv_binance_pct": f.diferencial_bcv_binance_pct,
                        "tasa_bcv_euro": f.tasa_bcv_euro,
                    }
                )
            if pg_rows:
                conn.execute(insert(t.serie_tasas), pg_rows)
            written = len(pg_rows)
    return TableResult("serie_tasas", len(filas), written)


def _migrate_usuarios_plataforma(
    sheets_repo: SheetsRepository, engine: Engine, apply: bool
) -> TableResult:
    raw_rows = sheets_repo._g.read_rows("UsuariosPlataforma")
    if apply:
        pg_repo = PostgresRepository(engine)
        for r in raw_rows:
            if r.get("email"):
                pg_repo.upsert_usuario_plataforma(r)
    return TableResult("usuarios_plataforma", len(raw_rows), len(raw_rows) if apply else 0)


def _migrate_config_meta(sheets_repo: SheetsRepository, engine: Engine, apply: bool) -> TableResult:
    meta = sheets_repo.all_config()
    if apply:
        pg_repo = PostgresRepository(engine)
        for k, v in meta.items():
            pg_repo.set_config(k, v)
    return TableResult("app_settings(_Meta)", len(meta), len(meta) if apply else 0)


# --- Orden de ejecución: primero las tablas sin FK entrantes, luego las que
# dependen de ellas (clientes -> ordenes/pagos -> lineas/vinculaciones ->
# bandeja/conciliacion). --------------------------------------------------
TABLE_SPECS: dict[str, TableMigrator] = {
    "clientes": _migrate_via_repo_method(
        "clientes", lambda r: r.all_clientes(), lambda pg, rows: pg.upsert_clientes(rows)
    ),
    "metodos_pago": _migrate_metodos_pago,
    "ordenes_venta": _migrate_via_repo_method(
        "ordenes_venta", lambda r: r.all_ordenes(), lambda pg, rows: pg.upsert_ordenes(rows)
    ),
    "lineas_orden": _migrate_via_repo_method(
        "lineas_orden", lambda r: r.all_lineas(), lambda pg, rows: pg.upsert_lineas(rows)
    ),
    "pagos": _migrate_pagos,
    "serie_tasas": _migrate_serie_tasas,
    "vinculaciones": _migrate_via_repo_method(
        "vinculaciones",
        lambda r: r.all_vinculaciones(),
        lambda pg, rows: pg.update_vinculaciones(rows),
    ),
    "bandeja_facturacion": _migrate_via_repo_method(
        "bandeja_facturacion", lambda r: r.all_bandeja(), lambda pg, rows: pg.upsert_bandejas(rows)
    ),
    "conciliacion": _migrate_via_repo_method(
        "conciliacion",
        lambda r: r.all_conciliaciones(),
        lambda pg, rows: pg.upsert_conciliaciones(rows),
    ),
    "descuentos_pronto_pago": _migrate_config_table(
        "descuentos_pronto_pago",
        t.descuentos_pronto_pago,
        ["regla_id"],
        "DescuentosProntoPago",
        serde.pronto_pago_from_row,
    ),
    "descuentos_volumen": _migrate_config_table(
        "descuentos_volumen",
        t.descuentos_volumen,
        ["regla_id"],
        "DescuentosVolumen",
        serde.desc_volumen_from_row,
    ),
    "descuentos_recompra": _migrate_config_table(
        "descuentos_recompra",
        t.descuentos_recompra,
        ["regla_id"],
        "DescuentosRecompra",
        serde.recompra_from_row,
    ),
    "descuentos_producto": _migrate_config_table(
        "descuentos_producto",
        t.descuentos_producto,
        ["regla_id"],
        "DescuentosProducto",
        serde.producto_from_row,
    ),
    "descuentos_diferencial_cambiario": _migrate_config_table(
        "descuentos_diferencial_cambiario",
        t.descuentos_diferencial_cambiario,
        ["regla_id"],
        "DescuentosDiferencialCambiario",
        serde.diferencial_from_row,
    ),
    "descuento_bcv_completo": _migrate_config_table(
        "descuento_bcv_completo",
        t.descuento_bcv_completo,
        ["vigencia_desde"],
        g.T_BCV_COMPLETO,
        serde.bcv_completo_from_row,
    ),
    "promocion_primera_compra": _migrate_config_table(
        "promocion_primera_compra",
        t.promocion_primera_compra,
        ["regla_id"],
        g.T_PROMO_PRIMERA,
        serde.promocion_from_row,
    ),
    "feriados": _migrate_config_table(
        "feriados", t.feriados, ["fecha"], g.T_FERIADOS, serde.feriado_from_row
    ),
    "reglas_recurrencia": _migrate_reglas_recurrencia,
    "exclusiones": _migrate_exclusiones,
    "usuarios_plataforma": _migrate_usuarios_plataforma,
    "app_settings": _migrate_config_meta,
}

TABLE_ORDER = list(TABLE_SPECS.keys())


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run", action="store_true", help="Solo cuenta filas en Sheets, no escribe nada."
    )
    mode.add_argument("--apply", action="store_true", help="Backfill real hacia Postgres.")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Connection string de Postgres. Si se omite, usa DATABASE_URL del entorno "
        "(cxc.config.DatabaseConfig). Requerido con --apply.",
    )
    parser.add_argument(
        "--tables",
        default=None,
        help=f"Lista separada por comas para migrar solo esas tablas. "
        f"Disponibles: {', '.join(TABLE_ORDER)}",
    )
    args = parser.parse_args()

    if args.apply:
        database_url = args.database_url or os.environ.get("DATABASE_URL")
        if not database_url:
            parser.error("--apply requiere --database-url o la variable de entorno DATABASE_URL")
    else:
        database_url = args.database_url or os.environ.get("DATABASE_URL") or "postgresql://dry-run"

    tables_to_run = TABLE_ORDER
    if args.tables:
        requested = [x.strip() for x in args.tables.split(",") if x.strip()]
        unknown = [x for x in requested if x not in TABLE_SPECS]
        if unknown:
            parser.error(f"Tablas desconocidas: {unknown}. Disponibles: {', '.join(TABLE_ORDER)}")
        tables_to_run = requested

    logger.info("Conectando a Google Sheets...")
    sheets_repo = _build_sheets_repo()

    engine = None
    if args.apply:
        logger.info("Conectando a Postgres...")
        engine = make_engine(database_url)
        with engine.connect():
            pass  # falla rápido si la URL/credenciales están mal, antes de tocar nada

    results: list[TableResult] = []
    for name in tables_to_run:
        migrator = TABLE_SPECS[name]
        logger.info("Procesando %s...", name)
        try:
            result = migrator(sheets_repo, engine, args.apply)
            results.append(result)
            logger.info(
                "  %s: %d filas en Sheets, %d escritas en Postgres",
                result.name,
                result.sheets_rows,
                result.written,
            )
        except Exception as e:  # noqa: BLE001 - queremos seguir con las demás tablas
            logger.exception("  ERROR en %s: %s", name, e)
            results.append(TableResult(name, -1, 0, error=str(e)))

    print("\n" + "=" * 70)
    print(f"RESUMEN ({'APPLY' if args.apply else 'DRY-RUN'})")
    print("=" * 70)
    print(f"{'tabla':<36}{'sheets':>10}{'postgres':>12}")
    for r in results:
        estado = f"  ERROR: {r.error}" if r.error else ""
        print(f"{r.name:<36}{r.sheets_rows:>10}{r.written:>12}{estado}")

    failed = [r for r in results if r.error]
    if failed:
        print(f"\n{len(failed)} tabla(s) con error. Revisar log arriba.")
        return 1
    print("\nOK -- sin errores.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
