"""La pista (VES/USD) de la orden la decide el precio cobrado, no la etiqueta.

Caso real S00913 (1-oct-2026): lista «Pago USD» con la linea al precio de la lista VES.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from cxc.engine.discounts import _con_pista_por_precio, calcular_factura

from . import builders as b
from .reglas_helpers import LISTA_USD, LISTA_VES, inputs, precios_ambas_listas, resolver
from .test_regla_diferencial_escenarios import _abono_usd as _abono_usd_ref
from .test_regla_diferencial_escenarios import _regla

PROD = "P1"


def _inp(*, etiqueta, precio_linea, abonos=(), historica=False, ves="100", usd="65"):
    inp = inputs(
        orden=b.orden(fecha=date(2026, 8, 28), lista=etiqueta),
        lineas=[b.linea("L1", producto=PROD, cantidad="1", precio=precio_linea)],
        abonos=abonos,
        price_resolver=resolver(precios_ambas_listas(PROD, ves=ves, usd=usd)),
    )
    return replace(inp, orden_es_historica=historica)


def test_etiqueta_usd_con_precio_de_la_lista_ves_pasa_a_ves():
    inp = _con_pista_por_precio(_inp(etiqueta=LISTA_USD, precio_linea="100"))
    assert inp.orden.lista_precios == LISTA_VES


def test_etiqueta_usd_con_precio_usd_no_cambia():
    inp = _con_pista_por_precio(_inp(etiqueta=LISTA_USD, precio_linea="65"))
    assert inp.orden.lista_precios == LISTA_USD


def test_etiqueta_ves_con_precio_de_la_lista_usd_pasa_a_usd():
    inp = _con_pista_por_precio(_inp(etiqueta=LISTA_VES, precio_linea="65"))
    assert inp.orden.lista_precios == LISTA_USD


def test_etiqueta_ves_con_precio_ves_no_cambia():
    inp = _con_pista_por_precio(_inp(etiqueta=LISTA_VES, precio_linea="100"))
    assert inp.orden.lista_precios == LISTA_VES


def test_si_las_dos_listas_dan_lo_mismo_no_se_toca():
    inp = _con_pista_por_precio(_inp(etiqueta=LISTA_USD, precio_linea="100", ves="100", usd="100"))
    assert inp.orden.lista_precios == LISTA_USD


def test_precio_que_no_es_de_ninguna_lista_no_cambia():
    inp = _con_pista_por_precio(_inp(etiqueta=LISTA_USD, precio_linea="80"))
    assert inp.orden.lista_precios == LISTA_USD


def test_orden_historica_no_se_toca():
    inp = _con_pista_por_precio(_inp(etiqueta=LISTA_USD, precio_linea="100", historica=True))
    assert inp.orden.lista_precios == LISTA_USD


def test_tolerancia_de_redondeo():
    inp = _con_pista_por_precio(_inp(etiqueta=LISTA_USD, precio_linea="100.80"))  # +0,8%
    assert inp.orden.lista_precios == LISTA_VES


def _abono_usd(monto):
    return _abono_usd_ref(monto)


def test_s00913_el_diferencial_ya_se_calcula_aunque_la_etiqueta_diga_usd():
    """Pago de 62 sobre una orden a precio VES 100 (sin IVA en el escenario): el 35%
    cambiario aplica porque el precio cobrado fue el de la lista VES."""
    inp = replace(
        _inp(etiqueta=LISTA_USD, precio_linea="100", abonos=_abono_usd("62")),
        descuentos_diferencial=[_regla("fijo_35_ves_usd")],
    )
    bandeja = calcular_factura(inp)
    assert bandeja.lista_aplicada == LISTA_VES
    assert any(d.origen == "bcv_completo" for d in bandeja.descuentos_detalle)


def test_orden_usd_con_precio_usd_no_recibe_diferencial():
    inp = replace(
        _inp(etiqueta=LISTA_USD, precio_linea="65", abonos=_abono_usd("65")),
        descuentos_diferencial=[_regla("fijo_35_ves_usd")],
    )
    bandeja = calcular_factura(inp)
    assert not any(d.origen == "bcv_completo" for d in bandeja.descuentos_detalle)
    assert isinstance(bandeja.total_descuentos, Decimal)
