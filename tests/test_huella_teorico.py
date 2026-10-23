"""La lista de precios entra en la huella que dispara el recálculo.

``fingerprint_lineas`` decide si un teórico ya calculado sigue vigente.
Hasta agosto de 2026 cubría solo las líneas, y eso bastaba: el teórico
usaba una lista global e ignoraba por completo la lista de la orden, así
que un cambio de lista no podía alterarlo. Se verificó contra producción y
se dejó documentado justamente porque invitaba a un arreglo equivocado.

Desde septiembre de 2026 el teórico se calcula contra la lista de
NACIMIENTO (ver ``test_teorico_usa_listas_configuradas.py``), y esa
premisa se cayó: una orden que cambia de lista en Odoo sin tocar ninguna
línea tiene un teórico distinto y nada lo dispararía. Por eso la lista
entra ahora en la huella.
"""

from __future__ import annotations

from decimal import Decimal

from cxc.engine.runner import fingerprint_lineas
from cxc.models import LineaOrden


def _linea(linea_id="L1", producto="P1", cantidad="2", precio="10"):
    return LineaOrden(
        linea_id=linea_id,
        so_id="S00001",
        producto=producto,
        marca="Sinoco",
        categoria="CAJA",
        cantidad=Decimal(cantidad),
        precio_unitario=Decimal(precio),
    )


_LINEAS = [_linea("L1"), _linea("L2", producto="P2")]


def test_cambiar_de_lista_mueve_la_huella() -> None:
    """El caso que motivó el cambio: mismas líneas, otra lista."""
    assert fingerprint_lineas(_LINEAS, "15") != fingerprint_lineas(_LINEAS, "10")


def test_perder_la_lista_mueve_la_huella() -> None:
    """Le pasó de verdad a 54 órdenes: se borró la lista en Odoo."""
    assert fingerprint_lineas(_LINEAS, "15") != fingerprint_lineas(_LINEAS, "")


def test_la_misma_orden_da_siempre_la_misma_huella() -> None:
    assert fingerprint_lineas(_LINEAS, "15") == fingerprint_lineas(_LINEAS, "15")
    assert fingerprint_lineas(list(reversed(_LINEAS)), "15") == fingerprint_lineas(_LINEAS, "15")


def test_los_espacios_alrededor_de_la_lista_no_cuentan() -> None:
    assert fingerprint_lineas(_LINEAS, " 15 ") == fingerprint_lineas(_LINEAS, "15")


def test_editar_una_linea_sigue_moviendo_la_huella() -> None:
    """Lo que la huella ya cubría -- hallazgo original, orden S00792."""
    otras = [_linea("L1", cantidad="3"), _linea("L2", producto="P2")]
    assert fingerprint_lineas(otras, "15") != fingerprint_lineas(_LINEAS, "15")
    menos = [_linea("L1")]
    assert fingerprint_lineas(menos, "15") != fingerprint_lineas(_LINEAS, "15")


def test_la_lista_no_se_confunde_con_una_linea() -> None:
    """La marca ``__lista__`` va aparte: un producto llamado igual que un
    id de lista no puede colisionar con el campo de la lista."""
    con_lista = fingerprint_lineas([_linea("L1", producto="15")], "")
    sin_lista = fingerprint_lineas([_linea("L1", producto="")], "15")
    assert con_lista != sin_lista


def test_la_version_del_desglose_invalida_lo_ya_calculado() -> None:
    """Al agregar ``regla_id`` al detalle, las 387 filas de descuento en
    producción quedaron con la columna vacía: la huella solo miraba líneas
    y lista, así que nada invalidaba lo ya guardado y el campo nuevo no se
    llenaba nunca. ``VERSION_DESGLOSE_MOTOR`` entra en la huella para que
    un cambio en la FORMA del resultado dispare el recálculo."""
    from cxc.engine import runner

    original = runner.VERSION_DESGLOSE_MOTOR
    try:
        huella_antes = fingerprint_lineas(_LINEAS, "15")
        runner.VERSION_DESGLOSE_MOTOR = "99"
        assert fingerprint_lineas(_LINEAS, "15") != huella_antes
    finally:
        runner.VERSION_DESGLOSE_MOTOR = original
    assert fingerprint_lineas(_LINEAS, "15") == huella_antes


# --- los pagos entran en la huella (S01226) ----------------------------------------------------


def _vinc(vinc_id="V1", monto="100", estado=None, dia=5):
    from datetime import datetime

    from cxc.models import EstadoVinculacion
    from tests import builders as b

    return b.vinculacion(
        vinc_id,
        monto_aplicado=monto,
        hora=datetime(2026, 10, dia, 10, 0),
        estado=estado or EstadoVinculacion.CONCILIADO,
    )


def _orden(**kw):
    from tests import builders as b

    return b.orden(**kw)


def test_sin_pagos_la_huella_es_la_de_siempre():
    """Las ordenes sin pagos conservan la huella que ya tenian: no se recalculan de golpe."""
    from cxc.engine.runner import _huella_de_abonos

    assert _huella_de_abonos([], _orden()) == ""
    assert fingerprint_lineas(_LINEAS, "15", "") == fingerprint_lineas(_LINEAS, "15")


def test_vincular_un_pago_mueve_la_huella():
    """El teorico calculado antes del pago tiene que recalcularse cuando el pago llega."""
    from cxc.engine.runner import _huella_de_abonos

    orden = _orden()
    sin_pago = fingerprint_lineas(_LINEAS, "15", _huella_de_abonos([], orden))
    con_pago = fingerprint_lineas(_LINEAS, "15", _huella_de_abonos([_vinc()], orden))
    assert con_pago != sin_pago


def test_cambia_con_el_monto_el_estado_o_el_dia_del_pago():
    from cxc.engine.runner import _huella_de_abonos
    from cxc.models import EstadoVinculacion

    orden = _orden()
    base = _huella_de_abonos([_vinc()], orden)
    assert _huella_de_abonos([_vinc(monto="150")], orden) != base
    assert _huella_de_abonos([_vinc(estado=EstadoVinculacion.PENDIENTE)], orden) != base
    assert _huella_de_abonos([_vinc(dia=9)], orden) != base


def test_cambia_cuando_la_orden_se_factura_porque_cambia_que_pagos_cuentan():
    from cxc.engine.runner import _huella_de_abonos

    vincs = [_vinc()]
    assert _huella_de_abonos(vincs, _orden(facturada=False)) != _huella_de_abonos(
        vincs, _orden(facturada=True)
    )


def test_es_estable_aunque_los_pagos_lleguen_en_otro_orden():
    from cxc.engine.runner import _huella_de_abonos

    a, c = _vinc("V1"), _vinc("V2", monto="50")
    assert _huella_de_abonos([a, c], _orden()) == _huella_de_abonos([c, a], _orden())
