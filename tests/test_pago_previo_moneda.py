"""``pago_previo_moneda`` (item 1, septiembre 2026, pedido del usuario):

no basta con "¿hay al menos un abono?" -- una regla puede exigir que lo
pagado CUBRA (>=) el teórico de una moneda específica ("ves"/"usd") antes
de aplicar en el neto real. El teórico (``ignorar_pago_previo=True``)
sigue ignorando esto por completo, igual que ya ignoraba ``tiene_pago``.

Mismo patrón de medición que ya usa Diferencial Cambiario para "¿el pago
cubre el teórico USD?": abonos valorados a la tasa que menos favorece al
cliente (Binance para USD, BCV para VES) contra el precio de la lista de
esa moneda -- ver ``valor_pagado_ves_bcv``/``valor_pagado_binance_usd`` en
``engine/equivalents.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.engine.discounts import calcular_factura
from cxc.models import Moneda, TipoTasa

from . import builders as b
from .reglas_helpers import LISTA_VES, inputs, precios_ambas_listas, resolver

PROD = "1033"


def _inp(regla, abonos=()):
    orden = b.orden(fecha=date(2026, 6, 1), fecha_entrega=date(2026, 6, 5), lista=LISTA_VES)
    return inputs(
        orden=orden,
        lineas=[b.linea("L1", producto=PROD, marca="Sinoco", categoria="CAJA", cantidad="10",
                        precio="100")],
        descuentos=[regla],
        abonos=abonos,
        price_resolver=resolver(precios_ambas_listas(PROD)),
        fecha_calculo=date(2026, 6, 8),
    )


def _abono_usd(monto: str):
    vinc = b.vinculacion(monto_aplicado=monto, moneda_abono=Moneda.USD,
                          tipo_tasa_abono=TipoTasa.BINANCE)
    return (vinc, b.metodo())


def _abono_ves(monto: str):
    vinc = b.vinculacion(monto_aplicado=monto, moneda_abono=Moneda.VES,
                          tipo_tasa_abono=TipoTasa.BCV)
    return (vinc, b.metodo())


def test_pago_previo_moneda_usd_no_aplica_si_solo_pago_en_ves_sin_cubrir_usd():
    """Teórico USD de la orden es 800 (10 u. x 80). Un abono en VES vale,

    a tasa Binance (40), solo 25 USD -- no cubre. La regla, scopeada a
    USD, no debe entrar al neto real.
    """
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    regla.pago_previo_moneda = "usd"
    bandeja = calcular_factura(_inp(regla, [_abono_ves("1000")]))
    assert bandeja.total_descuentos == Decimal("0.00")


def test_pago_previo_moneda_usd_SI_aplica_cuando_el_pago_cubre_el_teorico_usd():
    """Un abono de 800 USD directos cubre exactamente el teórico USD (800)."""
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    regla.pago_previo_moneda = "usd"
    bandeja = calcular_factura(_inp(regla, [_abono_usd("800")]))
    # Pagar en USD puro mueve el camino evaluado a la lista USD (800 de
    # base): 3% de 800 = 24.
    assert bandeja.total_descuentos == Decimal("24.00")


def test_pago_previo_moneda_ves_no_aplica_si_el_pago_no_cubre_el_teorico_ves():
    """Teórico VES es 1000. Un abono de 500 VES no lo cubre."""
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    regla.pago_previo_moneda = "ves"
    bandeja = calcular_factura(_inp(regla, [_abono_ves("500")]))
    assert bandeja.total_descuentos == Decimal("0.00")


def test_pago_previo_moneda_ves_SI_aplica_cuando_el_pago_cubre_el_teorico_ves():
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    regla.pago_previo_moneda = "ves"
    bandeja = calcular_factura(_inp(regla, [_abono_ves("1000")]))
    assert bandeja.total_descuentos == Decimal("30.00")


def test_pago_previo_moneda_cualquiera_por_defecto_no_exige_cobertura():
    """Default de compatibilidad: como antes de este campo, basta con que

    haya CUALQUIER abono -- no importa si cubre o no el teórico.
    """
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    assert regla.pago_previo_moneda == "cualquiera"
    bandeja = calcular_factura(_inp(regla, [_abono_ves("1")]))
    assert bandeja.total_descuentos == Decimal("30.00")


def test_pago_previo_moneda_sin_abonos_sigue_excluyendo_sin_importar_moneda():
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    regla.pago_previo_moneda = "usd"
    bandeja = calcular_factura(_inp(regla, []))
    assert bandeja.total_descuentos == Decimal("0.00")


def test_pago_previo_moneda_no_afecta_el_teorico_que_sigue_ignorando_pago_previo():
    """El camino teórico (``ignorar_pago_previo=True``) sigue proyectando

    el descuento aunque no haya ningún abono todavía -- ``pago_previo_
    moneda`` solo gatea el neto REAL.
    """
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    regla.pago_previo_moneda = "usd"
    bandeja = calcular_factura(_inp(regla, []))
    assert bandeja.descuentos_teorico_ves == Decimal("30.00")
    assert bandeja.descuentos_teorico_usd == Decimal("24.00")
