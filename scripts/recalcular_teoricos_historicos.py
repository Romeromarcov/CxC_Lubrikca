"""Recalcula ventas_teoricos para órdenes de la Lista Histórica de Auditoría.

``EngineRunner.run_teoricos_pendientes`` (usado por
``/api/backfill/ventas-teoricos``) SALTA cualquier orden que ya tenga fila
en ``ventas_teoricos`` sin ``usa_fallback_ves``/``_usd`` -- el teórico se
diseñó como un punto de comparación FIJO, no se recalcula por defecto.
Pero las filas de órdenes históricas calculadas ANTES de resolver el
crosswalk código->product_id (``scripts/cruzar_codigos_lista_historica.py``)
quedaron con el override histórico sin aplicar (silenciosamente, sin
fallback) -- fijas para siempre con el precio de lista normal en vez del
histórico. Este script fuerza el recálculo SOLO para esas órdenes.

Uso:
    railway run --service CxC_Lubrikca --environment production \\
        python scripts/recalcular_teoricos_historicos.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "src")

from cxc.config import AppConfig  # noqa: E402
from cxc.db.postgres_repository import PostgresRepository  # noqa: E402
from cxc.engine.discounts import calcular_teorico_orden_con_fallback  # noqa: E402
from cxc.engine.historical_pricing import es_orden_historica  # noqa: E402
from cxc.engine.runner import EngineRunner  # noqa: E402
from cxc.models import VentasTeorico  # noqa: E402
from cxc.odoo.client import _connect  # noqa: E402
from cxc.odoo.price import OdooPriceResolver  # noqa: E402
from cxc.web.app import (  # noqa: E402
    get_valid_pricelists_usd_and_ves,
    is_historical_pricelist_enabled,
)


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    config = AppConfig.from_env()
    execute = _connect(config.odoo)
    if not execute:
        print("ERROR: sin conexión a Odoo.")
        sys.exit(1)
    repo = PostgresRepository.from_url(config.database.url)

    historical_enabled = is_historical_pricelist_enabled(repo)
    ordenes = repo.all_ordenes()
    historicas = [
        o for o in ordenes if es_orden_historica(o.fecha, o.lista_precios, historical_enabled)
    ]
    print(f"Órdenes de la ventana histórica: {len(historicas)} / {len(ordenes)} totales")

    usd_lists, ves_lists = get_valid_pricelists_usd_and_ves(repo)
    primary_usd_id = int(usd_lists[0]) if usd_lists and usd_lists[0].isdigit() else 4
    primary_ves_id = int(ves_lists[0]) if ves_lists and ves_lists[0].isdigit() else 5
    pricelist_ids = {"USD": primary_usd_id, "BCV": primary_ves_id}
    fallback_pricelist_ids = [int(x) for x in (*usd_lists, *ves_lists) if str(x).isdigit()]
    resolver = OdooPriceResolver(execute, pricelist_ids, fallback_pricelist_ids)
    runner = EngineRunner(repo, resolver, config.engine)

    actualizadas = 0
    for o in historicas:
        st = str(getattr(o, "estado_orden", "sale") or "").strip().lower()
        if st in ("cancel", "cancelled", "draft"):
            continue
        inputs = runner.build_inputs(o.so_id, o.fecha)
        if inputs is None:
            print(f"  {o.so_id}: sin inputs (líneas vacías?) -- saltada")
            continue
        resultado = calcular_teorico_orden_con_fallback(inputs)
        print(
            f"  {o.so_id}: teorico_ves={resultado['teorico_ves']} "
            f"teorico_usd={resultado['teorico_usd']} "
            f"fallback_ves={resultado['usa_fallback_ves']} "
            f"fallback_usd={resultado['usa_fallback_usd']}"
        )
        if not dry_run:
            repo.upsert_ventas_teorico(
                VentasTeorico(
                    so_id=o.so_id,
                    teorico_ves=resultado["teorico_ves"],
                    teorico_usd=resultado["teorico_usd"],
                    descuentos_teorico_ves=resultado["descuentos_teorico_ves"],
                    descuentos_teorico_usd=resultado["descuentos_teorico_usd"],
                    lista_ves_id=resultado["lista_ves_id"],
                    lista_usd_id=resultado["lista_usd_id"],
                    usa_fallback_ves=resultado["usa_fallback_ves"],
                    usa_fallback_usd=resultado["usa_fallback_usd"],
                )
            )
        actualizadas += 1

    if dry_run:
        print(f"\n--dry-run: {actualizadas} órdenes se habrían recalculado (nada escrito).")
    else:
        print(f"\nOK: {actualizadas} órdenes recalculadas en ventas_teoricos.")


if __name__ == "__main__":
    main()
