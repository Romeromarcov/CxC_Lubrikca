"""Matriz de escenarios de Descuento por Volumen (``descuentos_volumen``,

Panel 3).

Producción tiene reglas de los dos sabores: por CAJAS/unidades con tramos
(``VOL_SINOCO_PAILA_1/2/3``) y por LITROS acumulados en una ventana de días
(``FID_GLOBAL_2500L``, ``FID_SINOCO_5000L``), así que ambos caminos de
``_calcular_componentes`` se cubren aquí.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.engine.discounts import calcular_factura

from . import builders as b
from .reglas_helpers import LISTA_VES, inputs, precios_ambas_listas, resolver

PROD = "1033"


def _regla_litros(regla_id="VOL_L", *, marca="*", categoria="*", litros="100", pct="0.05",
                  desde=date(2026, 1, 1), hasta=None):
    r = b.descuento_volumen(regla_id, marca=marca, categoria=categoria, litros_minimo=litros,
                            porcentaje=pct, desde=desde, hasta=hasta)
    r.unidad_medida = "LITROS"
    r.min_cantidad = Decimal("0")
    return r


def _regla_cajas(regla_id="VOL_C", *, marca="*", categoria="*", minimo="10", maximo="19",
                 pct="0.05"):
    r = b.descuento_volumen(regla_id, marca=marca, categoria=categoria, litros_minimo="0",
                            porcentaje=pct)
    r.unidad_medida = "CAJAS"
    r.min_cantidad = Decimal(minimo)
    r.max_cantidad = Decimal(maximo)
    return r


def _inp(reglas=(), *, cantidad="10", marca="Sinoco", categoria="PAILA", volumen="20",
         historial=()):
    return inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista=LISTA_VES),
        lineas=[b.linea("L1", producto=PROD, marca=marca, categoria=categoria,
                        cantidad=cantidad, precio="100")],
        descuentos_volumen=list(reglas),
        historial_cliente_lineas=list(historial),
        price_resolver=resolver(precios_ambas_listas(PROD), {PROD: volumen}),
    )


def test_volumen_por_litros_matchea_sobre_el_umbral():
    # 10 unidades x 20 L = 200 L >= 100 L.
    bandeja = calcular_factura(_inp([_regla_litros(litros="100")]))

    assert bandeja.total_descuentos == Decimal("50.00")
    assert bandeja.descuentos_teorico_ves == Decimal("50.00")


def test_volumen_por_litros_justo_en_el_umbral_aplica():
    # 10 x 20 = 200 L exactos.
    assert calcular_factura(_inp([_regla_litros(litros="200")])).total_descuentos == Decimal(
        "50.00"
    )


def test_volumen_por_litros_bajo_el_umbral_no_aplica():
    assert calcular_factura(_inp([_regla_litros(litros="201")])).total_descuentos == Decimal("0.00")


def test_volumen_por_cajas_dentro_del_tramo_aplica():
    assert calcular_factura(
        _inp([_regla_cajas(minimo="10", maximo="19")], cantidad="10")
    ).total_descuentos == Decimal("50.00")


def test_volumen_por_cajas_sobre_el_tramo_no_aplica():
    """``max_cantidad`` acota el tramo: 20 cajas ya no es el tramo 10-19."""
    assert calcular_factura(
        _inp([_regla_cajas(minimo="10", maximo="19")], cantidad="20")
    ).total_descuentos == Decimal("0.00")


def test_volumen_por_cajas_bajo_el_tramo_no_aplica():
    assert calcular_factura(
        _inp([_regla_cajas(minimo="10", maximo="19")], cantidad="9")
    ).total_descuentos == Decimal("0.00")


def test_volumen_no_matchea_por_marca_distinta():
    regla = _regla_litros(marca="GLOBAL OIL")
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("0.00")


def test_volumen_no_matchea_por_categoria_distinta():
    regla = _regla_litros(categoria="CAJA")
    assert calcular_factura(_inp([regla], categoria="PAILA")).total_descuentos == Decimal("0.00")


def test_volumen_regla_inactiva_no_aplica():
    regla = _regla_litros()
    regla.activo = False
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("0.00")


def test_volumen_vigencia_arranca_hoy_si_aplica():
    regla = _regla_litros(desde=date(2026, 6, 1))
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("50.00")


def test_volumen_vigencia_futura_no_aplica():
    regla = _regla_litros(desde=date(2026, 6, 2))
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("0.00")


def test_volumen_vigencia_vencida_ayer_no_aplica():
    regla = _regla_litros(hasta=date(2026, 5, 31))
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("0.00")


def test_volumen_vigencia_termina_hoy_si_aplica():
    regla = _regla_litros(hasta=date(2026, 6, 1))
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("50.00")


def test_sin_reglas_de_volumen_no_crashea_ni_descuenta():
    bandeja = calcular_factura(_inp([]))

    assert bandeja.total_descuentos == Decimal("0.00")
    assert bandeja.total_motor == Decimal("1000.00")


def test_volumen_porcentaje_cero_no_genera_descuento():
    assert calcular_factura(_inp([_regla_litros(pct="0")])).total_descuentos == Decimal("0.00")


def test_volumen_producto_sin_litros_conocidos_no_alcanza_umbral_de_litros():
    """Sin volumen en el catálogo, litros_eval = 0 -- la regla por litros no

    debe disparar por accidente.
    """
    assert calcular_factura(
        _inp([_regla_litros(litros="100")], volumen="0")
    ).total_descuentos == Decimal("0.00")


def test_volumen_acumulado_suma_el_historial_del_cliente():
    """``tipo_evaluacion='acumulado'``: 100 L de esta orden + 150 L de otra

    orden del cliente dentro de la ventana superan el umbral de 200 L.
    """
    regla = _regla_litros(litros="200")
    regla.tipo_evaluacion = "acumulado"
    regla.dias_evaluacion = 30

    orden_previa = b.orden("SO0", fecha=date(2026, 5, 20), lista=LISTA_VES)
    linea_previa = b.linea("L0", so_id="SO0", producto=PROD, marca="Sinoco", categoria="PAILA",
                           cantidad="15", precio="100")

    sin_historial = _inp([regla], cantidad="5")
    con_historial = _inp([regla], cantidad="5", historial=[(orden_previa, [linea_previa])])

    # 5 x 20 = 100 L solos: no alcanza.
    assert calcular_factura(sin_historial).total_descuentos == Decimal("0.00")
    # 100 L + 300 L del historial: alcanza, 5% sobre las 5 unidades de ESTA orden.
    assert calcular_factura(con_historial).total_descuentos == Decimal("25.00")


def test_volumen_acumulado_ignora_historial_fuera_de_la_ventana():
    regla = _regla_litros(litros="200")
    regla.tipo_evaluacion = "acumulado"
    regla.dias_evaluacion = 5

    orden_vieja = b.orden("SO0", fecha=date(2026, 1, 1), lista=LISTA_VES)
    linea_vieja = b.linea("L0", so_id="SO0", producto=PROD, marca="Sinoco", categoria="PAILA",
                          cantidad="15", precio="100")

    inp = _inp([regla], cantidad="5", historial=[(orden_vieja, [linea_vieja])])
    assert calcular_factura(inp).total_descuentos == Decimal("0.00")


def test_varias_reglas_gana_la_mas_especifica_sin_doble_conteo():
    """Una regla scoped a la categoría real gana las líneas frente a una

    genérica; la genérica no vuelve a cobrar sobre las mismas unidades.
    """
    generica = _regla_litros("VOL_GEN", marca="*", categoria="*", pct="0.05")
    especifica = _regla_litros("VOL_ESP", marca="Sinoco", categoria="PAILA", pct="0.10")
    bandeja = calcular_factura(_inp([generica, especifica]))

    assert bandeja.total_descuentos == Decimal("100.00")


def test_volumen_scoped_a_lista_ves_no_afecta_el_teorico_usd():
    regla = _regla_litros()
    regla.listas_aplicables = LISTA_VES
    bandeja = calcular_factura(_inp([regla]))

    assert bandeja.descuentos_teorico_ves == Decimal("50.00")
    assert bandeja.descuentos_teorico_usd == Decimal("0.00")


def test_volumen_aplica_a_subtotal_pesa_sobre_la_base_completa():
    regla = _regla_litros(marca="*", categoria="*")
    regla.aplica_a = "subtotal"
    inp = inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista=LISTA_VES),
        lineas=[
            b.linea("L1", producto=PROD, marca="Sinoco", categoria="PAILA", cantidad="5",
                    precio="100"),
            b.linea("L2", producto=PROD, marca="Sinoco", categoria="PAILA", cantidad="5",
                    precio="100"),
        ],
        descuentos_volumen=[regla],
        price_resolver=resolver(precios_ambas_listas(PROD), {PROD: "20"}),
    )
    assert calcular_factura(inp).total_descuentos == Decimal("50.00")
