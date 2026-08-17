"""Caché por-orden de get_live_so_states (agosto 2026, plan de reducción de

llamadas a Odoo). A diferencia de _PRICELIST_ITEMS_CACHE (keyed por el
conjunto completo de ids), esta cachea CADA nombre de SO por separado --
Dashboard/Auditoría/Reporte Diario piden listas de órdenes que se SOLAPAN
pero rara vez coinciden como conjunto completo, así que cachear por orden
individual es lo que realmente aprovecha ese solapamiento.
"""

from __future__ import annotations

from unittest.mock import patch

from cxc.web import app as app_module
from cxc.web.app import get_live_so_states


def _reset_cache():
    app_module._SO_STATE_CACHE.clear()


def test_pide_solo_las_faltantes_cuando_hay_solapamiento():
    _reset_cache()
    calls = []

    def fake_execute(model, method, args, kwargs=None):
        so_names_pedidos = args[0][0][2]
        calls.append(sorted(so_names_pedidos))
        return [{"name": n, "state": "sale"} for n in so_names_pedidos]

    with patch("cxc.web.app._connect", return_value=fake_execute):
        r1 = get_live_so_states(["SO1", "SO2"])
        assert r1 == {"SO1": "sale", "SO2": "sale"}
        assert calls == [["SO1", "SO2"]]

        # Segunda llamada: SO1 se solapa (ya cacheada), SO3 es nueva --
        # solo SO3 debe llegar a Odoo.
        r2 = get_live_so_states(["SO1", "SO3"])
        assert r2 == {"SO1": "sale", "SO3": "sale"}
        assert calls == [["SO1", "SO2"], ["SO3"]]


def test_ttl_vencido_vuelve_a_pedir_la_orden():
    _reset_cache()
    calls = []

    def fake_execute(model, method, args, kwargs=None):
        so_names_pedidos = args[0][0][2]
        calls.append(sorted(so_names_pedidos))
        return [{"name": n, "state": "cancel"} for n in so_names_pedidos]

    with patch("cxc.web.app._connect", return_value=fake_execute):
        get_live_so_states(["SO1"])
        assert len(calls) == 1

        app_module._SO_STATE_CACHE["SO1"] = (
            app_module._SO_STATE_CACHE["SO1"][0],
            app_module._SO_STATE_CACHE["SO1"][1] - (app_module._SO_STATE_CACHE_TTL + 1),
        )
        get_live_so_states(["SO1"])
        assert len(calls) == 2


def test_falla_odoo_solo_afecta_las_faltantes_no_lo_ya_cacheado():
    """Antes: un fallo de Odoo devolvía {} completo (el llamador caía al

    estado local para TODAS las órdenes). Ahora: si algunas ya estaban en
    caché fresca, esas se devuelven igual -- solo las faltantes quedan sin
    dato (y el llamador cae a local SOLO para esas)."""
    _reset_cache()

    def fake_execute_ok(model, method, args, kwargs=None):
        return [{"name": "SO1", "state": "sale"}]

    with patch("cxc.web.app._connect", return_value=fake_execute_ok):
        get_live_so_states(["SO1"])  # cachea SO1

    def fake_execute_falla(model, method, args, kwargs=None):
        raise RuntimeError("Odoo no responde")

    with patch("cxc.web.app._connect", return_value=fake_execute_falla):
        result = get_live_so_states(["SO1", "SO2"])

    assert result == {"SO1": "sale"}  # SO1 sigue disponible, SO2 no


def test_lista_vacia_no_llama_a_odoo():
    _reset_cache()
    assert get_live_so_states([]) == {}
