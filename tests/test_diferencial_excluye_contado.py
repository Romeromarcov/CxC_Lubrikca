"""Con el diferencial cambiario, el pago en VES se evalua como USD (caso S01226).

La orden nace en lista VES; el vendedor le puso a la linea un descuento a mano (31,45 %) que es
el diferencial (llevar el monto en VES a lo que valdria a tasa Binance) mas el contado del 8 %. El
motor, en cambio, le calculaba el 20 % de contado de las reglas del camino BCV en bolivares.

Decision del usuario (22-oct-2026): si aplica el diferencial, el pago es «VES a tasa Binance», o
sea USD: aplican las reglas de contado en dolares (8, 6, 3 %) y no las del camino BCV (20 y 15 %).
Es configurable: el par ``contado_ves`` <-> ``bcv_completo`` en la tabla ``exclusiones``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.engine.discounts import calcular_factura
from cxc.models import (
    DescuentoDiferencialCambiario,
    DescuentoProntoPago,
    ExclusionRegla,
    Moneda,
    TipoTasa,
)

from . import builders as b
from .reglas_helpers import LISTA_VES, inputs, precios_ambas_listas, resolver

PROD = "1033"
PAR = ExclusionRegla(regla_tipo_a="contado_ves", regla_tipo_b="bcv_completo", activo=True)


def _pronto(regla_id, pct, *, moneda, aplica_a="linea", ventana="entrega"):
    return DescuentoProntoPago(
        regla_id=regla_id,
        marca="*",
        categoria="*",
        porcentaje=Decimal(pct),
        monedas_aplicables=moneda,
        aplica_a=aplica_a,
        ventana_pago_tipo=ventana,
        ventana_pago_dias=3,
    )


def _inp(*, exclusiones=(), descuento_linea="31.45", pagado_ves="24678", con_dif=True):
    vinc = b.vinculacion(
        monto_aplicado=pagado_ves, moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV
    )
    dif = DescuentoDiferencialCambiario(
        regla_id="DIF_FIJO",
        tipo_diferencial="fijo_35_ves_usd",
        porcentaje_fijo=Decimal("0.35"),
        listas_aplicables="LISTAS_VES",
        vigencia_desde=date(2026, 1, 1),
        vigencia_hasta=None,
        activo=True,
    )
    return inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista=LISTA_VES, monto_total="1000"),
        lineas=[
            b.linea(
                "L1", producto=PROD, marca="Sinoco", categoria="Comercial", cantidad="10",
                precio="100", descuento=descuento_linea, presentacion_odoo="CAJA",
            )
        ],
        abonos=[(vinc, b.metodo(moneda=Moneda.VES))],
        descuentos=[
            _pronto("PP20", "0.20", moneda="VES", aplica_a="subtotal"),
            _pronto("PP15", "0.15", moneda="VES", ventana="vencimiento"),
            _pronto("PP08", "0.08", moneda="USD"),
        ],
        descuentos_diferencial=[dif] if con_dif else [],
        exclusiones=list(exclusiones),
        price_resolver=resolver(precios_ambas_listas(PROD, ves="100", usd="65")),
    )


def _origenes(bandeja):
    return {d.origen for d in bandeja.descuentos_detalle}


def test_sin_el_par_el_contado_en_ves_del_20_pct_sigue_aplicando_como_antes():
    bandeja = calcular_factura(_inp())

    # 20 % de contado sobre el subtotal real (685,50): 137,10.
    assert bandeja.total_descuentos == Decimal("137.10")


def test_con_el_par_y_el_diferencial_en_el_precio_aplica_el_8_pct_y_no_el_20():
    """La linea trae 31,45 % = diferencial + contado 8 %: el motor reconoce las dos cosas."""
    bandeja = calcular_factura(_inp(exclusiones=[PAR]))

    contado_8 = Decimal("54.84")  # 8 % del monto real de la linea (685,50)
    assert bandeja.total_descuentos == contado_8
    # Lo que el precio ya trae de diferencial + el contado = el descuento que puso el vendedor.
    assert bandeja.diferencial_en_precio + contado_8 == Decimal("314.50")


def test_las_reglas_del_20_y_del_15_no_aplican_cuando_hay_diferencial():
    """Ni el 20 % de subtotal ni el 15 % de pronto pago (otra ventana): ambos son del camino
    BCV en bolivares y viven en el mismo componente que el contado."""
    bandeja = calcular_factura(_inp(exclusiones=[PAR]))

    assert Decimal("137.10") not in {d.monto for d in bandeja.descuentos_detalle}
    assert bandeja.total_descuentos < Decimal("100")


def test_si_no_aplica_el_diferencial_el_par_no_cambia_nada():
    sin_dif = calcular_factura(_inp(exclusiones=[PAR], con_dif=False))
    base = calcular_factura(_inp(con_dif=False))

    assert sin_dif.total_descuentos == base.total_descuentos
    assert sin_dif.diferencial_en_precio == Decimal("0")


def test_orden_sin_descuento_en_la_linea_cobra_el_diferencial_y_el_contado_en_usd():
    """Caso normal, sin descuento puesto a mano: el diferencial se calcula y se cobra por NC, y
    el contado que se le suma es el de dolares, no el de bolivares."""
    bandeja = calcular_factura(
        _inp(exclusiones=[PAR], descuento_linea="0", pagado_ves="26000")
    )

    assert bandeja.diferencial_en_precio == Decimal("0")
    assert {"bcv_completo", "contado"} <= _origenes(bandeja)
    montos = {d.origen: d.monto for d in bandeja.descuentos_detalle}
    assert montos["contado"] == Decimal("80.00")  # 8 % de 1.000, no el 20 % (200)
    assert montos["bcv_completo"] == Decimal("197.78")
