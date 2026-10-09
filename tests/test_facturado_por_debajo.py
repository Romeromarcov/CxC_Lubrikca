"""Alerta de auditoria: se vende o se factura por debajo de lo debido."""

from __future__ import annotations

from decimal import Decimal

from cxc.engine.facturado_por_debajo import (
    FACTURA_MENOR_QUE_ORDEN,
    ORDEN_MENOR_QUE_LISTA,
    evaluar_facturado_por_debajo,
)
from cxc.models import VentasTeorico

from . import builders as b

USD = {"8"}


def _teorico(so="S1", ves="1000", usd="650", fb=False):
    return VentasTeorico(
        so_id=so,
        teorico_ves=Decimal(ves),
        teorico_usd=Decimal(usd),
        descuentos_teorico_ves=Decimal("0"),
        descuentos_teorico_usd=Decimal("0"),
        usa_fallback_ves=fb,
        usa_fallback_usd=fb,
    )


def _evaluar(*, descuento="0", precio="100", factura_usd=None, lista="5", teorico=None, orden=None):
    orden = orden or b.orden("S1", lista=lista)
    lineas = [b.linea("L1", so_id="S1", cantidad="10", precio=precio, descuento=descuento)]
    facturas = []
    if factura_usd is not None:
        facturas = [b.factura("F1", so_id="S1", monto_sin_impuestos_signed_usd=str(factura_usd))]
    return evaluar_facturado_por_debajo([orden], lineas, [teorico or _teorico()], facturas, USD)


def test_una_orden_a_precio_de_lista_sin_factura_no_alerta():
    assert _evaluar() == []


def test_un_descuento_de_linea_corriente_de_3_pct_no_alerta():
    assert _evaluar(descuento="3") == []


def test_un_descuento_de_linea_alto_alerta_con_su_causa():
    (a,) = _evaluar(descuento="20")
    assert a.tipo == ORDEN_MENOR_QUE_LISTA
    assert a.diferencia == Decimal("200.00")
    assert "20.0%" in a.detalle


def test_un_precio_de_linea_menor_que_la_lista_alerta():
    (a,) = _evaluar(precio="80")
    assert a.tipo == ORDEN_MENOR_QUE_LISTA
    assert "precio de linea" in a.detalle


def test_una_factura_menor_que_la_orden_alerta():
    alertas = _evaluar(factura_usd=650)
    assert [a.tipo for a in alertas] == [FACTURA_MENOR_QUE_ORDEN]
    assert alertas[0].diferencia == Decimal("350")


def test_factura_igual_a_la_orden_no_alerta():
    assert _evaluar(factura_usd=1000) == []


def test_factura_sin_equivalente_usd_es_un_dato_que_falta_no_una_alerta():
    assert _evaluar(factura_usd=0) == []


def test_si_el_teorico_uso_formula_de_respaldo_no_se_alerta_contra_la_lista():
    assert _evaluar(precio="80", teorico=_teorico(fb=True)) == []


def test_ordenes_canceladas_o_con_devolucion_no_alertan():
    assert _evaluar(precio="80", orden=b.orden("S1", estado_orden="cancel")) == []


def test_orden_en_lista_usd_se_compara_con_el_teorico_usd():
    assert _evaluar(precio="65", lista="8", teorico=_teorico(usd="650")) == []
    (a,) = _evaluar(precio="40", lista="8", teorico=_teorico(usd="650"))
    assert a.esperado == Decimal("650")
