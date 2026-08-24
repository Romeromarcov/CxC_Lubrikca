"""Matriz de escenarios de Pronto Pago / Contado (``descuentos_pronto_pago``,

Panel 2).

Las reglas reales de producción tienen ``requiere_pago_previo=True``, así
que en el neto real solo entran con un abono vinculado. El teórico las
proyecta igual (``ignorar_pago_previo=True``) mientras la ventana de pago
siga vigente -- por eso la mayoría de estos escenarios se verifican contra
``descuentos_teorico_ves``, que es lo que el usuario ve en Ventas.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.engine.discounts import calcular_factura

from . import builders as b
from .reglas_helpers import LISTA_USD, LISTA_VES, inputs, precios_ambas_listas, resolver

PROD = "1033"


def _inp(descuentos=(), *, marca="Sinoco", categoria="CAJA", fecha_calculo=date(2026, 6, 8)):
    return inputs(
        orden=b.orden(fecha=date(2026, 6, 1), fecha_entrega=date(2026, 6, 5), lista=LISTA_VES),
        lineas=[b.linea("L1", producto=PROD, marca=marca, categoria=categoria, cantidad="10",
                        precio="100")],
        descuentos=list(descuentos),
        fecha_calculo=fecha_calculo,
        price_resolver=resolver(precios_ambas_listas(PROD)),
    )


def test_contado_matchea_y_se_proyecta_en_el_teorico():
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    bandeja = calcular_factura(_inp([regla]))

    # 1000 de base en lista VES, 3%.
    assert bandeja.descuentos_teorico_ves == Decimal("30.00")
    # 800 en lista USD, mismo 3%.
    assert bandeja.descuentos_teorico_usd == Decimal("24.00")


def test_contado_no_matchea_por_marca_distinta():
    regla = b.descuento(marca="GLOBAL OIL", categoria="CAJA", porcentaje="0.03")
    assert calcular_factura(_inp([regla])).descuentos_teorico_ves == Decimal("0.00")


def test_contado_no_matchea_por_categoria_distinta():
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    inp = _inp([regla], categoria="PAILA")
    assert calcular_factura(inp).descuentos_teorico_ves == Decimal("0.00")


def test_contado_con_comodines_matchea_cualquier_linea():
    regla = b.descuento(marca="*", categoria="*", porcentaje="0.03")
    inp = _inp([regla], marca="Marca Rara", categoria="Categoria Rara")
    assert calcular_factura(inp).descuentos_teorico_ves == Decimal("30.00")


def test_contado_regla_inactiva_no_aplica():
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    regla.activo = False
    assert calcular_factura(_inp([regla])).descuentos_teorico_ves == Decimal("0.00")


def test_contado_vigencia_arranca_hoy_si_aplica():
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03",
                        desde=date(2026, 6, 1))
    assert calcular_factura(_inp([regla])).descuentos_teorico_ves == Decimal("30.00")


def test_contado_vigencia_futura_no_aplica():
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03",
                        desde=date(2026, 6, 2))
    assert calcular_factura(_inp([regla])).descuentos_teorico_ves == Decimal("0.00")


def test_contado_vigencia_vencida_ayer_no_aplica():
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03",
                        hasta=date(2026, 5, 31))
    assert calcular_factura(_inp([regla])).descuentos_teorico_ves == Decimal("0.00")


def test_contado_vigencia_termina_hoy_si_aplica():
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03",
                        hasta=date(2026, 6, 1))
    assert calcular_factura(_inp([regla])).descuentos_teorico_ves == Decimal("30.00")


def test_sin_reglas_de_contado_no_crashea_ni_descuenta():
    bandeja = calcular_factura(_inp([]))

    assert bandeja.descuentos_teorico_ves == Decimal("0.00")
    assert bandeja.total_motor == Decimal("1000.00")


def test_contado_scoped_a_lista_ves_no_afecta_el_teorico_usd():
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    regla.listas_aplicables = LISTA_VES
    bandeja = calcular_factura(_inp([regla]))

    assert bandeja.descuentos_teorico_ves == Decimal("30.00")
    assert bandeja.descuentos_teorico_usd == Decimal("0.00")


def test_contado_scoped_por_nombre_logico_de_listas_ves():
    """``listas_aplicables='LISTAS_VES'`` se resuelve contra ``valid_ves``."""
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    regla.listas_aplicables = "LISTAS_VES"
    bandeja = calcular_factura(_inp([regla]))

    assert bandeja.descuentos_teorico_ves == Decimal("30.00")
    assert bandeja.descuentos_teorico_usd == Decimal("0.00")


def test_contado_scoped_a_moneda_ves_no_aplica_sin_abonos_en_ves():
    """Sin abonos, el motor asume moneda de pago USD."""
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    regla.monedas_aplicables = "VES"
    assert calcular_factura(_inp([regla])).descuentos_teorico_ves == Decimal("0.00")


def test_varias_reglas_gana_la_mas_especifica_no_la_de_mayor_porcentaje():
    """(marca exacta, categoría exacta) pesa más que ('*', '*'), aunque la

    comodín ofrezca un porcentaje mayor.
    """
    especifica = b.descuento("D_ESP", marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    generosa = b.descuento("D_GEN", marca="*", categoria="*", porcentaje="0.10")
    bandeja = calcular_factura(_inp([especifica, generosa]))

    assert bandeja.descuentos_teorico_ves == Decimal("30.00")


def test_contado_no_entra_al_neto_real_sin_abono():
    """``requiere_pago_previo=True``: se proyecta en el teórico pero no se

    cobra hasta que haya un abono vinculado.
    """
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    bandeja = calcular_factura(_inp([regla]))

    assert bandeja.descuentos_teorico_ves == Decimal("30.00")
    assert bandeja.total_descuentos == Decimal("0.00")
    assert bandeja.total_motor == Decimal("1000.00")


def test_contado_sin_fecha_de_entrega_no_es_evaluable():
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03")
    inp = inputs(
        orden=b.orden(fecha=date(2026, 6, 1), fecha_entrega=None, lista=LISTA_VES),
        lineas=[b.linea("L1", producto=PROD, marca="Sinoco", categoria="CAJA", cantidad="10",
                        precio="100")],
        descuentos=[regla],
        price_resolver=resolver(precios_ambas_listas(PROD)),
    )
    assert calcular_factura(inp).descuentos_teorico_ves == Decimal("0.00")


def test_contado_deja_de_proyectarse_cuando_vence_la_ventana_de_pago():
    """Ventana "entrega" + 3 días sobre una entrega del 5 de junio vence el

    8; al 9 el contado ya no debe mostrarse como pendiente.
    """
    regla = b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.03",
                        ventana_pago_tipo="entrega", ventana_pago_dias=3)
    vigente = _inp([regla], fecha_calculo=date(2026, 6, 8))
    vencida = _inp([regla], fecha_calculo=date(2026, 6, 9))

    assert calcular_factura(vigente).descuentos_teorico_ves == Decimal("30.00")
    assert calcular_factura(vencida).descuentos_teorico_ves == Decimal("0.00")


def test_contado_aplica_a_subtotal_se_cuenta_una_sola_vez():
    """Una regla en modo "subtotal" pesa sobre el precio base completo, sin

    importar cuántas líneas le hagan match.
    """
    regla = b.descuento(marca="*", categoria="*", porcentaje="0.03")
    regla.aplica_a = "subtotal"
    inp = inputs(
        orden=b.orden(fecha=date(2026, 6, 1), fecha_entrega=date(2026, 6, 5), lista=LISTA_VES),
        lineas=[
            b.linea("L1", producto=PROD, categoria="CAJA", cantidad="5", precio="100"),
            b.linea("L2", producto=PROD, categoria="CAJA", cantidad="5", precio="100"),
        ],
        descuentos=[regla],
        price_resolver=resolver(precios_ambas_listas(PROD)),
    )
    assert calcular_factura(inp).descuentos_teorico_ves == Decimal("30.00")


def test_teorico_usd_usa_la_lista_usd():
    """Control de que el escenario ejercita ambas listas de verdad."""
    bandeja = calcular_factura(_inp([]))

    assert bandeja.teorico_lista_ves == Decimal("1000.00")
    assert bandeja.teorico_lista_usd == Decimal("800.00")
    assert bandeja.lista_aplicada == LISTA_VES
    assert LISTA_USD == "8"
