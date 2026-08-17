"""Caché por-partner de resolve_vendedores_por_partner (agosto 2026, mismo

patrón que _SO_STATE_CACHE/_ENTREGA_CACHE). get_live_pagos_confirmados y
get_live_pagos_conciliados (ambas llamadas típicamente en la misma carga
de Cobranza) piden vendedores para conjuntos de partner_ids que suelen
solaparse -- se cachea por partner individual, TTL más largo (5 min) por
ser configuración administrativa, no un estado de negocio en vivo.
"""

from __future__ import annotations

from cxc.web import app as app_module
from cxc.web.app import resolve_vendedores_por_partner


def _reset_cache():
    app_module._VENDEDOR_POR_PARTNER_CACHE.clear()


def test_resuelve_login_del_vendedor_asignado():
    _reset_cache()

    def fake_execute(model, method, args, kwargs=None):
        if model == "res.partner":
            return [{"id": pid, "user_id": [900 + pid, "Vendedor"]} for pid in args[0]]
        if model == "res.users":
            return [{"id": uid, "login": f"vendedor{uid}@lubrikca.com"} for uid in args[0]]
        return []

    result = resolve_vendedores_por_partner(fake_execute, {1, 2})
    assert result == {1: "vendedor901@lubrikca.com", 2: "vendedor902@lubrikca.com"}


def test_solapamiento_solo_pide_los_partners_faltantes():
    _reset_cache()
    calls = []

    def fake_execute(model, method, args, kwargs=None):
        if model == "res.partner":
            calls.append(sorted(args[0]))
            return [{"id": pid, "user_id": [900 + pid, "Vendedor"]} for pid in args[0]]
        if model == "res.users":
            return [{"id": uid, "login": f"v{uid}@x.com"} for uid in args[0]]
        return []

    r1 = resolve_vendedores_por_partner(fake_execute, {1, 2})
    assert r1 == {1: "v901@x.com", 2: "v902@x.com"}
    assert calls == [[1, 2]]

    # Partner 1 se solapa (cacheado), 3 es nuevo -- solo 3 debe pedirse.
    r2 = resolve_vendedores_por_partner(fake_execute, {1, 3})
    assert r2 == {1: "v901@x.com", 3: "v903@x.com"}
    assert calls == [[1, 2], [3]]


def test_partner_sin_vendedor_asignado_se_cachea_como_vacio():
    _reset_cache()
    calls = []

    def fake_execute(model, method, args, kwargs=None):
        if model == "res.partner":
            calls.append(list(args[0]))
            return [{"id": 1, "user_id": False}]  # sin vendedor asignado
        return []

    r1 = resolve_vendedores_por_partner(fake_execute, {1})
    assert r1 == {1: ""}

    # Segunda llamada -- no debe volver a pedirse a Odoo (ya cacheado).
    r2 = resolve_vendedores_por_partner(fake_execute, {1})
    assert r2 == {1: ""}
    assert len(calls) == 1


def test_partner_no_devuelto_por_odoo_se_cachea_como_vacio():
    """Id inexistente/archivado sin acceso: Odoo simplemente no lo incluye

    en la respuesta -- debe cachearse igual para no reintentarlo siempre."""
    _reset_cache()

    def fake_execute(model, method, args, kwargs=None):
        if model == "res.partner":
            return []  # Odoo no devolvió nada para el id pedido
        return []

    result = resolve_vendedores_por_partner(fake_execute, {999})
    assert result == {999: ""}
    assert app_module._VENDEDOR_POR_PARTNER_CACHE[999][0] == ""


def test_conjunto_vacio_no_llama_a_odoo():
    _reset_cache()

    def _debe_no_llamarse(*a, **k):
        raise AssertionError("no debería llamar a Odoo con partner_ids vacío")

    assert resolve_vendedores_por_partner(_debe_no_llamarse, set()) == {}
