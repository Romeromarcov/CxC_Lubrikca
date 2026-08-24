"""Matriz de escenarios de la regla de Obsequio (``promocion_primera_compra``,

Panel 4 de Configuración).

El fix anterior (agosto 2026) corrigió el espacio de identificadores del
campo ``productos`` -- guardaba el ``default_code`` de Odoo ("0761") cuando
el motor compara contra ``LineaOrden.producto``, que es el id de
``product.product`` ("1033") -- pero solo cubrió con tests la función de
auditoría, no el motor. Aquí se cubre el recorrido completo: que la regla
llegue al neto real (``calcular_factura``) Y a los teóricos de Ventas
(``descuentos_teorico_ves``/``_usd``), más los casos en que NO debe
disparar.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.engine.discounts import calcular_factura, conceptos_descuento_teorico

from . import builders as b
from .reglas_helpers import LISTA_VES, inputs, precios_ambas_listas, resolver

# Ids de product.product, el espacio de identificadores que compara el motor
# (``ln.producto in lista_prod``) -- NO el default_code, que fue el bug.
PROD_REGALO = "1033"
PROD_OTRO = "1022"


def _orden_con_obsequio(
    *,
    productos: str = PROD_REGALO,
    compra_minima: str = "3",
    cantidad: str = "5",
    activo: bool = True,
    desde: date = date(2026, 1, 1),
    hasta: date | None = None,
    solo_primera_compra: bool = False,
    primera: bool = False,
    categorias_aplica: str = "*",
    promos=None,
):
    """Orden de 5 cajas del producto regalable, con la promo indicada."""
    orden = b.orden(fecha=date(2026, 6, 1), lista=LISTA_VES, primera=primera)
    lineas = [
        b.linea(
            "L1",
            producto=PROD_REGALO,
            marca="GLOBAL OIL",
            categoria="CAJA",
            cantidad=cantidad,
            precio="100",
        )
    ]
    if promos is None:
        promo = b.promo_primera(
            producto=productos,
            desde=desde,
            hasta=hasta,
            compra_minima=compra_minima,
            regalo_tipo="solo_uno",
            categorias_aplica=categorias_aplica,
            solo_primera_compra=solo_primera_compra,
        )
        promo.activo = activo
        promos = [promo]
    return inputs(
        orden=orden,
        lineas=lineas,
        promociones=promos,
        price_resolver=resolver(precios_ambas_listas(PROD_REGALO, PROD_OTRO)),
    )


def test_obsequio_matchea_descuenta_del_neto_real():
    """Con la regla apuntando al producto_id correcto, el obsequio se cobra."""
    inp = _orden_con_obsequio()
    bandeja = calcular_factura(inp)

    # 1 unidad regalada al precio real de la línea (100).
    assert bandeja.ncs_calculadas == Decimal("100.00")
    # 5 x 100 (lista VES) - 100 de obsequio.
    assert bandeja.precio_base_calculado == Decimal("500.00")
    assert bandeja.total_motor == Decimal("400.00")


def test_obsequio_llega_a_los_teoricos_de_ventas():
    """Regresión del bug corregido: el obsequio debe aparecer en los

    descuentos teóricos de AMBAS listas, no solo en el neto real. Antes
    ``_teoricos_por_lista`` sumaba solo recompra + contado + volumen y
    estos dos campos salían en 0 con la regla aplicando.
    """
    bandeja = calcular_factura(_orden_con_obsequio())

    assert bandeja.descuentos_teorico_ves == Decimal("100.00")
    assert bandeja.descuentos_teorico_usd == Decimal("100.00")


def test_obsequio_aparece_en_el_desglose_de_conceptos():
    inp = _orden_con_obsequio()
    conceptos = conceptos_descuento_teorico(inp, LISTA_VES, pura_bcv=True)

    assert [c["concepto"] for c in conceptos] == [f"NC obsequio ({PROD_REGALO})"]
    assert conceptos[0]["monto"] == Decimal("100.00")


def test_obsequio_no_aplica_si_el_producto_no_esta_en_la_orden():
    """La regla apunta a otro producto -- cae al camino porcentual, que sin

    un ``valor``/``descuento_fallback`` configurado no descuenta nada.
    """
    inp = _orden_con_obsequio(productos=PROD_OTRO)
    bandeja = calcular_factura(inp)

    assert bandeja.ncs_calculadas == Decimal("0.00")
    assert bandeja.descuentos_teorico_ves == Decimal("0.00")


def test_obsequio_no_aplica_bajo_la_compra_minima():
    inp = _orden_con_obsequio(compra_minima="12", cantidad="5")
    assert calcular_factura(inp).ncs_calculadas == Decimal("0.00")


def test_obsequio_aplica_justo_en_la_compra_minima():
    inp = _orden_con_obsequio(compra_minima="5", cantidad="5")
    assert calcular_factura(inp).ncs_calculadas == Decimal("100.00")


def test_obsequio_regla_inactiva_no_aplica():
    inp = _orden_con_obsequio(activo=False)
    bandeja = calcular_factura(inp)

    assert bandeja.ncs_calculadas == Decimal("0.00")
    assert bandeja.descuentos_teorico_ves == Decimal("0.00")


def test_obsequio_vigencia_arranca_hoy_si_aplica():
    """Borde inferior: ``vigencia_desde`` == fecha de la orden -> vigente."""
    inp = _orden_con_obsequio(desde=date(2026, 6, 1))
    assert calcular_factura(inp).ncs_calculadas == Decimal("100.00")


def test_obsequio_vigencia_futura_no_aplica():
    inp = _orden_con_obsequio(desde=date(2026, 6, 2))
    assert calcular_factura(inp).ncs_calculadas == Decimal("0.00")


def test_obsequio_vigencia_vencida_ayer_no_aplica():
    inp = _orden_con_obsequio(desde=date(2026, 1, 1), hasta=date(2026, 5, 31))
    assert calcular_factura(inp).ncs_calculadas == Decimal("0.00")


def test_obsequio_vigencia_termina_hoy_si_aplica():
    """Borde superior: ``vigencia_hasta`` == fecha de la orden -> vigente."""
    inp = _orden_con_obsequio(hasta=date(2026, 6, 1))
    assert calcular_factura(inp).ncs_calculadas == Decimal("100.00")


def test_obsequio_sin_vigencia_hasta_sigue_vigente():
    inp = _orden_con_obsequio(hasta=None)
    assert calcular_factura(inp).ncs_calculadas == Decimal("100.00")


def test_sin_promociones_configuradas_no_crashea_ni_descuenta():
    """Tabla vacía: el motor debe caer limpio en "sin obsequio", no romper."""
    inp = inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista=LISTA_VES),
        lineas=[b.linea("L1", producto=PROD_REGALO, categoria="CAJA", cantidad="5", precio="100")],
        promociones=[],
        price_resolver=resolver(precios_ambas_listas(PROD_REGALO)),
    )
    bandeja = calcular_factura(inp)

    assert bandeja.ncs_calculadas == Decimal("0.00")
    assert bandeja.total_motor == Decimal("500.00")
    assert conceptos_descuento_teorico(inp, LISTA_VES, pura_bcv=True) == []


def test_sin_promociones_primera_compra_cae_al_2pct_industrial():
    """Fallback histórico: primera compra sin ninguna promo configurada

    descuenta 2% pero SOLO sobre líneas Industrial.
    """
    inp = inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista=LISTA_VES, primera=True),
        lineas=[
            b.linea("L1", producto=PROD_REGALO, categoria="INDUSTRIAL", cantidad="5", precio="100")
        ],
        promociones=[],
        price_resolver=resolver(precios_ambas_listas(PROD_REGALO)),
    )
    # 5 x 100 (lista VES) x 2%.
    assert calcular_factura(inp).ncs_calculadas == Decimal("10.00")


def test_promo_solo_primera_compra_no_dispara_en_orden_recurrente():
    inp = _orden_con_obsequio(solo_primera_compra=True, primera=False)
    assert calcular_factura(inp).ncs_calculadas == Decimal("0.00")


def test_promo_solo_primera_compra_dispara_en_la_primera():
    inp = _orden_con_obsequio(solo_primera_compra=True, primera=True)
    assert calcular_factura(inp).ncs_calculadas == Decimal("100.00")


def test_varias_promos_candidatas_gana_la_de_mayor_compra_minima():
    """``_evaluar_promociones_producto`` elige por ``compra_minima`` -- la

    regla más exigente que la orden igual satisface es la que manda.
    """
    floja = b.promo_primera(
        producto=PROD_OTRO, compra_minima="3", regalo_tipo="solo_uno", categorias_aplica="*"
    )
    floja.regla_id = "PROMO_FLOJA"
    exigente = b.promo_primera(
        producto=PROD_REGALO, compra_minima="5", regalo_tipo="solo_uno", categorias_aplica="*"
    )
    exigente.regla_id = "PROMO_EXIGENTE"

    inp = _orden_con_obsequio(promos=[floja, exigente])
    bandeja = calcular_factura(inp)

    # Gana la exigente (compra_minima 5) -> regala PROD_REGALO, presente en
    # la orden. Si ganara la floja el obsequio sería 0 (PROD_OTRO no está).
    assert bandeja.ncs_calculadas == Decimal("100.00")


def test_promo_fuera_de_la_lista_aplicable_no_afecta_ese_teorico():
    """``listas_aplicables`` scoped a VES: el teórico USD no debe cobrarla."""
    promo = b.promo_primera(
        producto=PROD_REGALO, compra_minima="3", regalo_tipo="solo_uno", categorias_aplica="*"
    )
    promo.listas_aplicables = LISTA_VES

    inp = _orden_con_obsequio(promos=[promo])
    bandeja = calcular_factura(inp)

    assert bandeja.descuentos_teorico_ves == Decimal("100.00")
    assert bandeja.descuentos_teorico_usd == Decimal("0.00")


def test_obsequio_conjunto_regala_cada_producto_de_la_lista():
    promo = b.promo_primera(
        producto=f"{PROD_REGALO},{PROD_OTRO}",
        compra_minima="3",
        regalo_tipo="conjunto",
        categorias_aplica="*",
    )
    inp = inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista=LISTA_VES),
        lineas=[
            b.linea("L1", producto=PROD_REGALO, categoria="CAJA", cantidad="5", precio="100"),
            b.linea("L2", producto=PROD_OTRO, categoria="CAJA", cantidad="4", precio="50"),
        ],
        promociones=[promo],
        price_resolver=resolver(precios_ambas_listas(PROD_REGALO, PROD_OTRO)),
    )
    # 1 unidad de cada uno, al precio real de su línea: 100 + 50.
    assert calcular_factura(inp).ncs_calculadas == Decimal("150.00")


def test_obsequio_no_se_duplica_si_la_linea_ya_viene_regalada_en_odoo():
    """Línea con 100% de descuento = el obsequio ya se dio en la SO; el

    motor no debe volver a acreditarlo.
    """
    promo = b.promo_primera(
        producto=PROD_REGALO, compra_minima="3", regalo_tipo="solo_uno", categorias_aplica="*"
    )
    inp = inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista=LISTA_VES),
        lineas=[
            b.linea("L1", producto=PROD_REGALO, categoria="CAJA", cantidad="5", precio="100"),
            b.linea(
                "L2",
                producto=PROD_REGALO,
                categoria="CAJA",
                cantidad="1",
                precio="100",
                descuento="100",
            ),
        ],
        promociones=[promo],
        price_resolver=resolver(precios_ambas_listas(PROD_REGALO)),
    )
    assert calcular_factura(inp).ncs_calculadas == Decimal("0.00")


def test_teorico_usd_usa_la_lista_usd_no_la_de_nacimiento():
    """Control de que el escenario realmente ejercita las dos listas."""
    bandeja = calcular_factura(_orden_con_obsequio())

    assert bandeja.teorico_lista_ves == Decimal("500.00")
    assert bandeja.teorico_lista_usd == Decimal("400.00")
    assert bandeja.lista_aplicada == LISTA_VES
