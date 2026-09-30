"""Odoo manda sobre las vinculaciones PENDIENTE (``engine/pendientes_contra_odoo``)."""

from __future__ import annotations

from cxc.engine.pendientes_contra_odoo import clasificar_pendientes
from cxc.models import EstadoVinculacion

from . import builders as b

PEND = EstadoVinculacion.PENDIENTE
CONC = EstadoVinculacion.CONCILIADO


def _v(vinc_id, so, monto, estado, pago="P1"):
    return b.vinculacion(vinc_id, pago_id=pago, so_id=so, monto_aplicado=monto, estado=estado)


def _ids(lista):
    return sorted(v.vinc_id for v in lista)


def test_si_odoo_cubrio_todo_el_pago_las_pendientes_sobran():
    """Pago 2018: conciliado en S2 por el monto completo, pendiente en S1 por lo mismo."""
    r = clasificar_pendientes(
        [_v("a", "S1", "100", PEND), _v("b", "S2", "100", CONC)], [b.pago("P1", monto="100")]
    )
    assert _ids(r.a_retirar) == ["a"]
    assert r.ambiguas == []


def test_la_tolerancia_de_centavos_cuenta_como_cubierto():
    r = clasificar_pendientes(
        [_v("a", "S1", "50", PEND), _v("b", "S2", "99.995", CONC)], [b.pago("P1", monto="100")]
    )
    assert _ids(r.a_retirar) == ["a"]


def test_parcial_conciliado_con_pendiente_que_cabe_en_lo_que_falta_se_queda():
    r = clasificar_pendientes(
        [_v("a", "S1", "40", PEND), _v("b", "S2", "60", CONC)], [b.pago("P1", monto="100")]
    )
    assert r.a_retirar == []
    assert r.ambiguas == []


def test_parcial_conciliado_con_pendiente_que_excede_es_ambigua_y_no_se_borra():
    r = clasificar_pendientes(
        [_v("a", "S1", "70", PEND), _v("b", "S2", "60", CONC)], [b.pago("P1", monto="100")]
    )
    assert r.a_retirar == []
    assert _ids(r.ambiguas) == ["a"]


def test_dos_sugerencias_sin_conciliar_que_reclaman_mas_que_el_pago_son_ambiguas():
    r = clasificar_pendientes(
        [_v("a", "S1", "100", PEND), _v("b", "S2", "100", PEND)], [b.pago("P1", monto="100")]
    )
    assert r.a_retirar == []
    assert _ids(r.ambiguas) == ["a", "b"]


def test_pendiente_normal_sin_conciliar_no_se_toca():
    r = clasificar_pendientes([_v("a", "S1", "100", PEND)], [b.pago("P1", monto="100")])
    assert r.a_retirar == []
    assert r.ambiguas == []


def test_solo_se_miran_las_pendientes_del_pago_que_odoo_cubrio():
    r = clasificar_pendientes(
        [
            _v("a", "S1", "100", PEND, pago="P1"),
            _v("b", "S2", "100", CONC, pago="P1"),
            _v("c", "S3", "100", PEND, pago="P2"),  # otro pago, sin conciliar
        ],
        [b.pago("P1", monto="100"), b.pago("P2", monto="100")],
    )
    assert _ids(r.a_retirar) == ["a"]


def test_un_pago_que_no_esta_en_el_espejo_no_se_evalua_aqui():
    """Lo resuelve el barrido de borrados, no esta funcion."""
    r = clasificar_pendientes([_v("a", "S1", "100", PEND)], [])
    assert r.a_retirar == []
    assert r.ambiguas == []
