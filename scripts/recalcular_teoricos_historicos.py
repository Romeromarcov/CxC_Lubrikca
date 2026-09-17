"""Recalcula ``ventas_teoricos`` para las órdenes de la ventana histórica.

``EngineRunner.run_teoricos_pendientes`` (usado por
``/api/backfill/ventas-teoricos``) SALTA cualquier orden que ya tenga fila en
``ventas_teoricos`` sin ``usa_fallback_ves``/``_usd`` -- el teórico se diseñó
como un punto de comparación FIJO, no se recalcula por defecto. Este script
fuerza el recálculo para las órdenes de la ventana histórica (20-feb al
12-mar-2026) o sin lista de precios propia, sin esa guarda.

**Historia del script.** Nació (agosto 2026) para un bug distinto: las filas
calculadas ANTES de resolver el crosswalk código->product_id
(``scripts/cruzar_codigos_lista_historica.py``) habían quedado con el
override histórico sin aplicar. Con eso ya cerrado, el 12-sep-2026 la
decisión 5 del quiz apagó la Lista Histórica de Auditoría para todo camino
de monto real -- y el 17-sep-2026 se encontró que ``EngineRunner.
build_inputs`` (que este mismo script usa) seguía leyendo el selector de
Configuración en vivo, no la función ya fijada de ``web/app.py`` (ver el
commit que corrigió ``runner.py``). Con esa segunda corrección aplicada,
este script ya no "aplica el override histórico que faltaba" -- **congela**
el teórico de cada orden de la ventana con el precio de lista normal
vigente, que es lo que la decisión 5 pidió. La selección de candidatos sigue
usando el criterio de ventana histórica (``es_orden_historica`` con
``enabled=True``) porque es la única forma de encontrar las filas que
quedaron congeladas con el precio Euro ANTES de la decisión -- el criterio
de selección no cambia con la decisión, el valor que se guarda sí.

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
from cxc.web.app import get_valid_pricelists_usd_and_ves  # noqa: E402


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    config = AppConfig.from_env()
    execute = _connect(config.odoo)
    if not execute:
        print("ERROR: sin conexión a Odoo.")
        sys.exit(1)
    repo = PostgresRepository.from_url(config.database.url)

    usd_lists, ves_lists = get_valid_pricelists_usd_and_ves(repo)
    usd_ids_str = {str(x) for x in usd_lists}

    ordenes = repo.all_ordenes()
    # ``enabled=True``: el criterio de "cae en la ventana histórica" no
    # depende de la decisión 5 -- selecciona las mismas órdenes de siempre.
    # Lo que cambió es el precio que ``calcular_teorico_orden_con_fallback``
    # les asigna (ver docstring del módulo).
    historicas = [
        o
        for o in ordenes
        if es_orden_historica(
            o.fecha,
            o.lista_precios,
            enabled=True,
            lista_es_usd_valida=str(o.lista_precios or "").strip() in usd_ids_str,
        )
    ]
    print(f"Órdenes de la ventana histórica: {len(historicas)} / {len(ordenes)} totales")

    primary_usd_id = int(usd_lists[0]) if usd_lists and usd_lists[0].isdigit() else 4
    primary_ves_id = int(ves_lists[0]) if ves_lists and ves_lists[0].isdigit() else 5
    pricelist_ids = {"USD": primary_usd_id, "BCV": primary_ves_id}
    fallback_pricelist_ids = [int(x) for x in (*usd_lists, *ves_lists) if str(x).isdigit()]
    resolver = OdooPriceResolver(execute, pricelist_ids, fallback_pricelist_ids)
    runner = EngineRunner(repo, resolver, config.engine)

    existentes = {v.so_id: v for v in repo.all_ventas_teoricos()}

    actualizadas = 0
    for o in historicas:
        st = str(getattr(o, "estado_orden", "sale") or "").strip().lower()
        if st in ("cancel", "cancelled", "draft"):
            continue
        previo = existentes.get(o.so_id)
        inputs = runner.build_inputs(o.so_id, o.fecha)
        if inputs is None:
            print(f"  {o.so_id}: sin inputs (líneas vacías?) -- saltada")
            continue
        assert inputs.orden_es_historica is False, (
            f"{o.so_id}: build_inputs todavía resuelve histórica -- "
            "la decisión 5 dejó de aplicarse, revisar runner.py"
        )
        resultado = calcular_teorico_orden_con_fallback(inputs)
        cambio = ""
        if previo is not None and previo.teorico_ves != resultado["teorico_ves"]:
            cambio = f" (antes {previo.teorico_ves})"
        print(
            f"  {o.so_id}: teorico_ves={resultado['teorico_ves']}{cambio} "
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
