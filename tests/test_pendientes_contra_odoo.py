"""Odoo manda sobre las vinculaciones PENDIENTE (``engine/pendientes_contra_odoo``)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cxc.engine.pendientes_contra_odoo import clasificar_pendientes
from cxc.models import EstadoVinculacion, Moneda

from . import builders as b

PEND = EstadoVinculacion.PENDIENTE
CONC = EstadoVinculacion.CONCILIADO


def _v(vinc_id, so, monto, estado, pago="P1", hora=datetime(2026, 9, 3, 10, 0), **kw):
    v = b.vinculacion(vinc_id, pago_id=pago, so_id=so, monto_aplicado=monto, estado=estado, **kw)
    v.timestamp_registro = hora
    return v


def _ids(lista):
    return sorted(v.vinc_id for v in lista)


def test_si_odoo_cubrio_todo_el_pago_las_pendientes_sobran():
    """Pago 2018: conciliado en S2 por el monto completo, pendiente en S1 por lo mismo."""
    r = clasificar_pendientes(
        [_v("a", "S1", "100", PEND), _v("b", "S2", "100", CONC)], [b.pago("P1", monto="100")]
    )
    assert _ids(r.a_retirar) == ["a"]
    assert r.a_ajustar == []


def test_la_tolerancia_relativa_cuenta_como_cubierto():
    """Pago en VES: Odoo concilio 173.226,58 de 173.232,01 (0,003%) -> cubierto."""
    r = clasificar_pendientes(
        [_v("a", "S1", "173235.15", PEND), _v("b", "S2", "173226.58", CONC)],
        [b.pago("P1", monto="173232.01", moneda=Moneda.VES)],
    )
    assert _ids(r.a_retirar) == ["a"]


def test_redondeo_de_la_conversion_no_es_un_exceso():
    """Dos pendientes en VES que suman 0,57 Bs mas que el pago (pago 1090): se quedan."""
    r = clasificar_pendientes(
        [_v("a", "S1", "235.6931", PEND), _v("b", "S2", "173964.8798", PEND)],
        [b.pago("P1", monto="174200", moneda=Moneda.VES)],
    )
    assert r.a_retirar == []
    assert r.a_ajustar == []


def test_parcial_conciliado_con_pendiente_que_cabe_en_lo_que_falta_se_queda():
    r = clasificar_pendientes(
        [_v("a", "S1", "40", PEND), _v("b", "S2", "60", CONC)], [b.pago("P1", monto="100")]
    )
    assert r.a_retirar == []
    assert r.a_ajustar == []


def test_la_pendiente_que_excede_lo_que_dejo_odoo_se_achica():
    """FIFO asigno el pago completo (677) a S1 y Odoo ya habia conciliado 252,91."""
    r = clasificar_pendientes(
        [_v("a", "S1", "677", PEND), _v("b", "S2", "252.91", CONC)], [b.pago("P1", monto="677")]
    )
    assert r.a_retirar == []
    (ajustada,) = r.a_ajustar
    assert ajustada.vinc_id == "a"
    assert ajustada.monto_aplicado == Decimal("424.09")


def test_al_achicar_los_equivalentes_escalan_en_proporcion():
    v = _v("a", "S1", "200", PEND)
    v.equiv_usd_bcv = Decimal("200")
    v.equiv_ves_bcv = Decimal("7200")
    r = clasificar_pendientes(
        [v, _v("b", "S2", "100", CONC)], [b.pago("P1", monto="200")]
    )
    (ajustada,) = r.a_ajustar
    assert ajustada.monto_aplicado == Decimal("100")
    assert ajustada.equiv_usd_bcv == Decimal("100")
    assert ajustada.equiv_ves_bcv == Decimal("3600")


def test_el_reparto_sigue_el_orden_fifo_de_las_ordenes():
    """Dos sugerencias sin conciliar que reclaman mas que el pago: se queda entera la de
    la orden MAS ANTIGUA y la otra se achica a lo que sobra."""
    r = clasificar_pendientes(
        [_v("nueva", "S2", "60", PEND), _v("vieja", "S1", "60", PEND)],
        [b.pago("P1", monto="100")],
        fechas_orden={"S1": date(2026, 3, 1), "S2": date(2026, 6, 1)},
    )
    assert r.a_retirar == []
    (ajustada,) = r.a_ajustar
    assert ajustada.vinc_id == "nueva"
    assert ajustada.monto_aplicado == Decimal("40")


def test_las_que_ya_no_tienen_de_donde_se_retiran():
    r = clasificar_pendientes(
        [_v(x, s, "100", PEND) for x, s in (("a", "S1"), ("b", "S2"), ("c", "S3"))],
        [b.pago("P1", monto="100")],
        fechas_orden={"S1": date(2026, 1, 1), "S2": date(2026, 2, 1), "S3": date(2026, 3, 1)},
    )
    assert _ids(r.a_retirar) == ["b", "c"]
    assert r.a_ajustar == []


def test_pendiente_normal_sin_conciliar_no_se_toca():
    r = clasificar_pendientes([_v("a", "S1", "100", PEND)], [b.pago("P1", monto="100")])
    assert r.a_retirar == []
    assert r.a_ajustar == []


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
    assert r.a_ajustar == []
