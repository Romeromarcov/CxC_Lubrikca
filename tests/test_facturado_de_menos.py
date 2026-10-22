"""La alerta «Facturado de menos»: misma base y lo vendido manda (caso S01226)."""

from __future__ import annotations

from cxc.engine.facturado import facturado_de_menos


def _alerta(**kw) -> bool:
    base = {
        "tiene_factura": True,
        "facturado_neto": 474.58,
        "iva_retenido": 0.0,
        "teorico_neto_con_impuestos": 692.31,
        "venta_neta_real": 474.58,
    }
    return facturado_de_menos(**{**base, **kw})


def test_facturar_lo_que_se_vendio_no_es_facturar_de_menos_aunque_el_teorico_sea_mayor():
    """S01226: 31,4 % de descuento en la linea; la factura calza con la orden."""
    assert _alerta() is False


def test_el_iva_retenido_no_cuenta_como_facturado_de_menos():
    """Agente de retencion: el neto ya no incluye el IVA que retiene (65,46)."""
    assert _alerta(facturado_neto=409.12, iva_retenido=65.46) is False


def test_sin_devolver_el_iva_retenido_se_veria_como_faltante():
    assert _alerta(facturado_neto=409.12, iva_retenido=0.0) is True


def test_factura_menor_que_la_orden_si_alerta():
    assert _alerta(facturado_neto=300.0) is True


def test_factura_menor_que_el_teorico_cuando_la_orden_vale_mas_si_alerta():
    assert _alerta(facturado_neto=150.0, teorico_neto_con_impuestos=208.8, venta_neta_real=232.0)


def test_orden_por_encima_del_teorico_manda_el_teorico():
    assert _alerta(
        facturado_neto=208.8, teorico_neto_con_impuestos=208.8, venta_neta_real=232.0
    ) is False


def test_sin_factura_no_hay_alerta():
    assert _alerta(tiene_factura=False, facturado_neto=0.0) is False


def test_sin_venta_real_conocida_se_compara_contra_el_teorico():
    assert _alerta(venta_neta_real=0.0, facturado_neto=100.0) is True
