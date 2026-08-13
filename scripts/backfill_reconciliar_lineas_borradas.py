"""Backfill (one-shot): purga líneas fantasma de ``lineas_orden`` (agosto 2026).

``changed_lineas`` (delta por ``write_date``) nunca puede detectar una línea
BORRADA en Odoo -- un registro que ya no existe no deja rastro de
``write_date``, así que el espejo local la conservaba para siempre (hallazgo
real orden S00792: un producto sacado de la orden en Odoo seguía apareciendo
en el teórico). El sync incremental (``sync/incremental.py``) ya reconcilia
esto hacia adelante para cada orden que Odoo vuelva a tocar; este script hace
la limpieza única de lo que ya quedó fantasma en el pasado, consultando a
Odoo el set VIGENTE de líneas para TODAS las órdenes locales de una sola vez.

Uso:
    railway run --service CxC_Lubrikca --environment staging/production \
        python scripts/backfill_reconciliar_lineas_borradas.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "src")

from sqlalchemy import create_engine  # noqa: E402

from cxc.config import AppConfig  # noqa: E402
from cxc.db.postgres_repository import PostgresRepository  # noqa: E402
from cxc.odoo.client import OdooXmlRpcReader  # noqa: E402


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    config = AppConfig.from_env()
    engine = create_engine(config.database.url)
    repo = PostgresRepository(engine)
    reader = OdooXmlRpcReader(config.odoo)

    ordenes = repo.all_ordenes()
    so_ids = [o.so_id for o in ordenes]
    print(f"Órdenes locales a verificar: {len(so_ids)}")

    vigentes_por_orden = reader.lineas_vigentes_por_orden(so_ids)

    a_borrar: list[str] = []
    ordenes_afectadas: set[str] = set()
    for so_id in so_ids:
        vigentes = vigentes_por_orden.get(so_id, set())
        for ln in repo.lineas_de_orden(so_id):
            if ln.linea_id not in vigentes:
                a_borrar.append(ln.linea_id)
                ordenes_afectadas.add(so_id)
                print(
                    f"  Fantasma: SO {so_id} línea {ln.linea_id} "
                    f"(producto {ln.producto}, cantidad {ln.cantidad})"
                )

    print(
        f"\n{len(a_borrar)} línea(s) fantasma en {len(ordenes_afectadas)} orden(es): "
        f"{sorted(ordenes_afectadas)}"
    )

    if dry_run:
        print("--dry-run: nada escrito.")
        return
    if a_borrar:
        repo.delete_lineas(a_borrar)
        print("OK: líneas fantasma borradas.")
    else:
        print("OK: nada que borrar.")


if __name__ == "__main__":
    main()
