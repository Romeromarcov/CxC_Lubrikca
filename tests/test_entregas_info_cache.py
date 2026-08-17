"""Caché por-orden de get_live_entregas_info (agosto 2026, mismo patrón que

get_live_so_states -- ver tests/test_so_states_cache.py). Cachea
(es_entrega_valida, fecha_entrega) por SO individual; solo las órdenes sin
caché fresca llegan a Odoo en cada llamada.
"""

from __future__ import annotations

from cxc.web import app as app_module
from cxc.web.app import get_live_entregas_info


def _reset_cache():
    app_module._ENTREGA_CACHE.clear()


def _fake_execute_con_pickings(pickings_por_so_id, picking_id_to_so):
    """Simula sale.order (picking_ids) + stock.picking (estado/tipo/fecha)."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            so_names = args[0][0][2]
            return [
                {"name": so, "picking_ids": [pid for pid, s in picking_id_to_so.items() if s == so]}
                for so in so_names
                if so in picking_id_to_so.values()
            ]
        if model == "stock.picking":
            ids_pedidos = args[0][0][2]
            return [p for p in pickings_por_so_id if p["id"] in ids_pedidos]
        return []

    return fake_execute


def test_entrega_normal_queda_en_delivered_y_con_fecha():
    _reset_cache()
    picking_id_to_so = {101: "SO1"}
    pickings = [
        {
            "id": 101,
            "picking_type_code": "outgoing",
            "return_id": False,
            "date_done": "2026-06-10 10:00:00",
        }
    ]
    execute = _fake_execute_con_pickings(pickings, picking_id_to_so)

    delivered, fechas = get_live_entregas_info(["SO1"], execute=execute)
    assert delivered == {"SO1"}
    assert fechas == {"SO1": "2026-06-10"}


def test_entrega_con_devolucion_no_queda_en_delivered_pero_si_tiene_fecha():
    """Replica el comportamiento original: fecha_entrega_map se llena para

    CUALQUIER orden con despacho saliente, incluso si terminó devuelta --
    solo el set `delivered` (entrega válida) la excluye."""
    _reset_cache()
    picking_id_to_so = {101: "SO1", 102: "SO1"}
    pickings = [
        {
            "id": 101,
            "picking_type_code": "outgoing",
            "return_id": False,
            "date_done": "2026-06-10 10:00:00",
        },
        {
            "id": 102,
            "picking_type_code": "incoming",
            "return_id": [101, "dev"],
            "date_done": "2026-06-12",
        },
    ]
    execute = _fake_execute_con_pickings(pickings, picking_id_to_so)

    delivered, fechas = get_live_entregas_info(["SO1"], execute=execute)
    assert delivered == set()  # devuelta -- no cuenta como entrega válida
    assert fechas == {"SO1": "2026-06-10"}  # pero la fecha de despacho persiste


def test_orden_sin_ningun_picking_se_cachea_como_sin_entrega():
    _reset_cache()
    execute = _fake_execute_con_pickings([], {})

    delivered, fechas = get_live_entregas_info(["SO_NUEVA"], execute=execute)
    assert delivered == set()
    assert fechas == {}
    # Se cacheó explícitamente -- no queda "faltante" en la próxima llamada.
    assert app_module._ENTREGA_CACHE["SO_NUEVA"][:2] == (False, None)


def test_solapamiento_solo_pide_las_faltantes():
    _reset_cache()
    call_log = []
    picking_id_to_so = {101: "SO1", 102: "SO2"}
    pickings = [
        {"id": 101, "picking_type_code": "outgoing", "return_id": False, "date_done": "2026-06-01"},
        {"id": 102, "picking_type_code": "outgoing", "return_id": False, "date_done": "2026-06-02"},
    ]
    base_execute = _fake_execute_con_pickings(pickings, picking_id_to_so)

    def tracking_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            call_log.append(sorted(args[0][0][2]))
        return base_execute(model, method, args, kwargs)

    r1 = get_live_entregas_info(["SO1", "SO2"], execute=tracking_execute)
    assert r1[0] == {"SO1", "SO2"}
    assert call_log == [["SO1", "SO2"]]

    # SO1 se solapa (cacheada), SO3 es nueva.
    picking_id_to_so["103"] = "SO3"  # no se usa por base_execute con este id nuevo, ok
    r2 = get_live_entregas_info(["SO1", "SO3"], execute=tracking_execute)
    assert "SO1" in r2[0]
    assert call_log == [["SO1", "SO2"], ["SO3"]]


def test_lista_vacia_no_llama_a_odoo():
    _reset_cache()

    def _debe_no_llamarse(*a, **k):
        raise AssertionError("no debería llamar a Odoo con so_names vacío")

    assert get_live_entregas_info([], execute=_debe_no_llamarse) == (set(), {})
