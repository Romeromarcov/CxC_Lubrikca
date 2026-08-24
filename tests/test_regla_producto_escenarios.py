"""Matriz de escenarios de Descuento por Producto (``descuentos_producto``,

Panel 5 "Descuento por Producto/Marca/Categoría").

La tabla está VACÍA en producción, así que no hay una orden real contra la
cual contrastar: estos tests fijan el contrato estructural (qué matchea, qué
no, y que el camino "sin reglas" no rompe ni inventa nada) y cubren el
hueco que tenía este componente en los teóricos de Ventas -- ``comp.producto``
no se sumaba a ``descuentos_teorico_ves``/``_usd`` aunque sí se cobraba en el
neto real.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.engine.discounts import calcular_factura

from . import builders as b
from .reglas_helpers import LISTA_VES, inputs, precios_ambas_listas, resolver

PROD = "1033"
PROD_OTRO = "1022"


def _inp(reglas=(), *, producto=PROD, marca="Sinoco", categoria="CAJA"):
    return inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista=LISTA_VES),
        lineas=[b.linea("L1", producto=producto, marca=marca, categoria=categoria,
                        cantidad="10", precio="100")],
        descuentos_producto=list(reglas),
        price_resolver=resolver(precios_ambas_listas(PROD, PROD_OTRO)),
    )


def test_producto_matchea_y_se_cobra_en_el_neto():
    regla = b.descuento_producto(productos=PROD, porcentaje="0.05")
    bandeja = calcular_factura(_inp([regla]))

    assert bandeja.total_descuentos == Decimal("50.00")
    assert bandeja.total_motor == Decimal("950.00")


def test_producto_llega_a_los_teoricos_de_ventas():
    """Regresión: ``comp.producto`` faltaba en ``_teoricos_por_lista``."""
    regla = b.descuento_producto(productos=PROD, porcentaje="0.05")
    bandeja = calcular_factura(_inp([regla]))

    assert bandeja.descuentos_teorico_ves == Decimal("50.00")
    assert bandeja.descuentos_teorico_usd == Decimal("40.00")


def test_producto_no_matchea_otro_producto():
    regla = b.descuento_producto(productos=PROD_OTRO, porcentaje="0.05")
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("0.00")


def test_producto_comodin_matchea_cualquier_producto():
    regla = b.descuento_producto(productos="*", porcentaje="0.05")
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("50.00")


def test_producto_csv_matchea_cualquiera_de_la_lista():
    regla = b.descuento_producto(productos=f"{PROD_OTRO},{PROD}", porcentaje="0.05")
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("50.00")


def test_producto_no_matchea_por_marca_distinta():
    regla = b.descuento_producto(productos=PROD, marca="GLOBAL OIL", porcentaje="0.05")
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("0.00")


def test_producto_no_matchea_por_categoria_distinta():
    regla = b.descuento_producto(productos=PROD, categoria="PAILA", porcentaje="0.05")
    assert calcular_factura(_inp([regla], categoria="CAJA")).total_descuentos == Decimal("0.00")


def test_producto_regla_inactiva_no_aplica():
    regla = b.descuento_producto(productos=PROD, porcentaje="0.05")
    regla.activo = False
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("0.00")


def test_producto_vigencia_arranca_hoy_si_aplica():
    regla = b.descuento_producto(productos=PROD, porcentaje="0.05", desde=date(2026, 6, 1))
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("50.00")


def test_producto_vigencia_futura_no_aplica():
    regla = b.descuento_producto(productos=PROD, porcentaje="0.05", desde=date(2026, 6, 2))
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("0.00")


def test_producto_vigencia_vencida_ayer_no_aplica():
    regla = b.descuento_producto(productos=PROD, porcentaje="0.05", hasta=date(2026, 5, 31))
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("0.00")


def test_producto_vigencia_termina_hoy_si_aplica():
    regla = b.descuento_producto(productos=PROD, porcentaje="0.05", hasta=date(2026, 6, 1))
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("50.00")


def test_sin_reglas_de_producto_no_crashea_ni_descuenta():
    """El estado real de producción: tabla vacía."""
    bandeja = calcular_factura(_inp([]))

    assert bandeja.total_descuentos == Decimal("0.00")
    assert bandeja.total_motor == Decimal("1000.00")
    assert bandeja.descuentos_teorico_ves == Decimal("0.00")


def test_producto_scoped_a_lista_ves_no_afecta_el_teorico_usd():
    regla = b.descuento_producto(productos=PROD, porcentaje="0.05")
    regla.listas_aplicables = LISTA_VES
    bandeja = calcular_factura(_inp([regla]))

    assert bandeja.descuentos_teorico_ves == Decimal("50.00")
    assert bandeja.descuentos_teorico_usd == Decimal("0.00")


def test_producto_scoped_a_moneda_ves_no_aplica_sin_abonos_en_ves():
    regla = b.descuento_producto(productos=PROD, porcentaje="0.05")
    regla.monedas_aplicables = "VES"
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("0.00")


def test_varias_reglas_gana_la_de_codigo_de_producto_sobre_la_comodin():
    """La especificidad pesa: código exacto (4) > marca (2) > categoría (1)."""
    comodin = b.descuento_producto("PROD_ANY", productos="*", porcentaje="0.02")
    exacta = b.descuento_producto("PROD_EXACT", productos=PROD, porcentaje="0.05")
    assert calcular_factura(_inp([comodin, exacta])).total_descuentos == Decimal("50.00")


def test_producto_aplica_a_subtotal_pesa_sobre_la_base_completa():
    regla = b.descuento_producto(productos="*", porcentaje="0.05", aplica_a="subtotal")
    inp = inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista=LISTA_VES),
        lineas=[
            b.linea("L1", producto=PROD, categoria="CAJA", cantidad="5", precio="100"),
            b.linea("L2", producto=PROD, categoria="CAJA", cantidad="5", precio="100"),
        ],
        descuentos_producto=[regla],
        price_resolver=resolver(precios_ambas_listas(PROD)),
    )
    assert calcular_factura(inp).total_descuentos == Decimal("50.00")


def test_matching_de_producto_es_por_substring_hallazgo_documentado():
    """HALLAZGO (no corregido -- tabla vacía, sin evidencia de daño real):

    ``_match_producto_codigo`` acepta un match por SUBSTRING
    (``any(c in prod_u for c in codigos)``), así que una regla apuntando a
    "103" alcanza también al producto "1033". Sobre ids numéricos de
    ``product.product`` eso es un match accidental del mismo tipo que el bug
    del obsequio; sobre SKUs alfanuméricos podría ser intencional (prefijo
    de familia). Este test FIJA el comportamiento actual para que un cambio
    futuro sea deliberado y no silencioso -- ver reporte de auditoría.
    """
    regla = b.descuento_producto(productos="103", porcentaje="0.05")
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("50.00")
