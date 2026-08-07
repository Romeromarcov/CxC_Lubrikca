"""Backfill de subcategoria/presentacion_odoo en lineas_orden.

Las columnas se agregaron en la migracion e440941fae83 (agosto 2026),
despues de que la mayoria de las lineas ya estaban sincronizadas -- este
script rellena las lineas existentes re-consultando Odoo por producto
(una sola consulta bulk, no por linea) y actualizando en lote por
product_id. El proximo sync normal (OdooClient.changed_lineas) ya
poblara estas columnas para lineas nuevas/modificadas sin este script.

Uso:
    railway run --service CxC_Lubrikca --environment staging/production \
        python scripts/backfill_subcategoria_presentacion.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "src")

from sqlalchemy import create_engine, text  # noqa: E402

from cxc.config import AppConfig  # noqa: E402
from cxc.odoo.client import OdooXmlRpcReader, _connect  # noqa: E402


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    config = AppConfig.from_env()
    engine = create_engine(config.database.url)
    execute = _connect(config.odoo)
    if not execute:
        print("ERROR: sin conexión a Odoo.")
        sys.exit(1)

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT producto FROM lineas_orden WHERE producto != ''")
        ).fetchall()
    prod_ids = {int(r[0]) for r in rows if str(r[0]).isdigit()}
    print(f"Productos distintos referenciados en lineas_orden: {len(prod_ids)}")

    reader = OdooXmlRpcReader(config.odoo, execute)
    mapa = reader._productos(prod_ids)
    print(f"Productos resueltos desde Odoo: {len(mapa)}")

    actualizadas = 0
    with engine.connect() as conn:
        trans = conn.begin()
        for pid, (_marca, _categoria, subcategoria, presentacion) in mapa.items():
            if not subcategoria and not presentacion:
                continue
            result = conn.execute(
                text(
                    "UPDATE lineas_orden SET subcategoria = :sub, "
                    "presentacion_odoo = :pres WHERE producto = :pid "
                    "AND (subcategoria = '' OR presentacion_odoo = '')"
                ),
                {"sub": subcategoria, "pres": presentacion, "pid": str(pid)},
            )
            actualizadas += result.rowcount
        if dry_run:
            trans.rollback()
            print(f"\n--dry-run: {actualizadas} líneas se habrían actualizado (nada escrito).")
        else:
            trans.commit()
            print(f"\nOK: {actualizadas} líneas actualizadas en lineas_orden.")


if __name__ == "__main__":
    main()
