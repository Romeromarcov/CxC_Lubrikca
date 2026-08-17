"""Caché de product.pricelist.item (agosto 2026, plan de reducción de

llamadas a Odoo) -- Reporte de Saldos y Auditoría consultaban la MISMA
tabla con el mismo filtro (compute_price=fixed), cada uno con su propio
conjunto de pricelist_ids. La clave de caché es el conjunto EXACTO de ids
pedido, para que nunca se mezcle el alcance de un llamador con el de otro.
"""

from __future__ import annotations

from cxc.web import app as app_module
from cxc.web.app import _get_pricelist_items_fixed


def _reset_cache():
    app_module._PRICELIST_ITEMS_CACHE.clear()


def test_reutiliza_cache_para_el_mismo_conjunto_de_ids():
    _reset_cache()
    call_count = {"n": 0}

    def fake_execute(model, method, args, kwargs=None):
        call_count["n"] += 1
        return [{"pricelist_id": 5, "product_tmpl_id": 1, "fixed_price": 10.0}]

    r1 = _get_pricelist_items_fixed(fake_execute, [5, 8])
    r2 = _get_pricelist_items_fixed(fake_execute, [8, 5])  # mismo set, otro orden
    assert r1 == r2
    assert call_count["n"] == 1  # la segunda llamada usó el caché


def test_conjuntos_de_ids_distintos_no_comparten_cache():
    """Caso central: Reporte de Saldos pide solo usd_ids, Auditoría pide

    usd_ids+ves_ids combinados -- deben ser entradas de caché separadas,
    nunca la data de un alcance filtrando al otro."""
    _reset_cache()
    calls = []

    def fake_execute(model, method, args, kwargs=None):
        domain = args[0]
        ids_pedidos = domain[0][2]
        calls.append(sorted(ids_pedidos))
        return [
            {"pricelist_id": pid, "product_tmpl_id": 1, "fixed_price": 10.0}
            for pid in ids_pedidos
        ]

    reporte_saldos_result = _get_pricelist_items_fixed(fake_execute, [5])  # solo USD
    auditoria_result = _get_pricelist_items_fixed(fake_execute, [4, 5])  # USD+VES

    assert len(calls) == 2  # dos queries reales, un alcance distinto cada una
    assert {r["pricelist_id"] for r in reporte_saldos_result} == {5}
    assert {r["pricelist_id"] for r in auditoria_result} == {4, 5}


def test_ttl_vencido_dispara_nueva_consulta():
    _reset_cache()
    call_count = {"n": 0}

    def fake_execute(model, method, args, kwargs=None):
        call_count["n"] += 1
        return []

    _get_pricelist_items_fixed(fake_execute, [5])
    assert call_count["n"] == 1

    # Forzar expiración manipulando el timestamp guardado.
    key = (5,)
    app_module._PRICELIST_ITEMS_CACHE[key]["timestamp"] -= (
        app_module._PRICELIST_ITEMS_CACHE_TTL + 1
    )
    _get_pricelist_items_fixed(fake_execute, [5])
    assert call_count["n"] == 2


def test_sin_execute_o_sin_ids_no_falla():
    _reset_cache()
    assert _get_pricelist_items_fixed(None, [5]) == []
    assert _get_pricelist_items_fixed(lambda *a, **k: [{"x": 1}], []) == []
