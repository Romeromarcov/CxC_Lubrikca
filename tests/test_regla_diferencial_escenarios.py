"""Matriz de escenarios del Diferencial Cambiario

(``descuentos_diferencial_cambiario``, Panel 6).

Producción tiene las 3 filas del diseño: ``fijo_35_ves_usd`` (Regla 1),
``equiparar_binance`` (Regla 2) y ``candidato_cierre_factura`` (Regla 3, que
NO es un descuento del motor sino el interruptor de un reporte aparte, por
eso aquí solo se verifica que su presencia no altere el cálculo).

A diferencia del resto, este componente NO es proyectable: se calcula a
partir de la moneda y la tasa de los abonos reales, así que sin abonos su
aporte es siempre 0 -- también en el teórico.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.engine.discounts import calcular_factura
from cxc.models import DescuentoDiferencialCambiario, Moneda, TipoTasa

from . import builders as b
from .reglas_helpers import LISTA_VES, inputs, precios_ambas_listas, resolver

PROD = "1033"


def _regla(tipo="fijo_35_ves_usd", *, regla_id=None, pct="0.35", activo=True,
           desde=date(2026, 1, 1), hasta=None, listas="LISTAS_VES"):
    return DescuentoDiferencialCambiario(
        regla_id=regla_id or f"DIF_{tipo}",
        nombre=tipo,
        tipo_diferencial=tipo,
        porcentaje_fijo=Decimal(pct),
        listas_aplicables=listas,
        vigencia_desde=desde,
        vigencia_hasta=hasta,
        activo=activo,
    )


def _abono_usd(monto="800"):
    """Pago 100% USD por ruta BCV -- dispara la Regla 1 (fijo)."""
    vinc = b.vinculacion(
        monto_aplicado=monto, moneda_abono=Moneda.USD, tipo_tasa_abono=TipoTasa.BCV
    )
    return [(vinc, b.metodo(moneda=Moneda.USD))]


def _abono_ves(monto="32000"):
    """Pago en VES por ruta BCV -- candidato a la Regla 2 (equiparar).

    32000 VES a tasa Binance 40 = 800 USD (cubre el teórico USD), y a tasa
    BCV 36 = 888.89 USD (por debajo de los 1000 de la lista VES): esa brecha
    es exactamente lo que la equiparación cubre.
    """
    vinc = b.vinculacion(
        monto_aplicado=monto, moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV
    )
    return [(vinc, b.metodo(moneda=Moneda.VES))]


def _inp(reglas=(), *, abonos=(), lista=LISTA_VES, huerfanos=False):
    return inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista=lista, monto_total="1000"),
        lineas=[b.linea("L1", producto=PROD, marca="Sinoco", categoria="Comercial",
                        cantidad="10", precio="100", presentacion_odoo="CAJA")],
        abonos=list(abonos),
        descuentos_diferencial=list(reglas),
        cliente_tiene_pagos_huerfanos=huerfanos,
        price_resolver=resolver(precios_ambas_listas(PROD)),
    )


def test_regla_fijo_aplica_con_pago_100_pct_usd():
    bandeja = calcular_factura(_inp([_regla("fijo_35_ves_usd")], abonos=_abono_usd()))

    # 35% sobre los 1000 de la lista VES.
    assert bandeja.total_descuentos == Decimal("350.00")


def test_regla_fijo_no_aplica_si_el_pago_no_cubre_el_teorico_usd():
    """Se exige la orden pagada 100% según el teórico USD (800)."""
    bandeja = calcular_factura(_inp([_regla("fijo_35_ves_usd")], abonos=_abono_usd("500")))

    assert bandeja.total_descuentos == Decimal("0.00")


def test_regla_equiparar_aplica_con_pago_ves_sin_huerfanos():
    reglas = [_regla("fijo_35_ves_usd"), _regla("equiparar_binance")]
    bandeja = calcular_factura(_inp(reglas, abonos=_abono_ves()))

    # 1000 (lista VES) - 888.89 (lo pagado valorado a BCV), topado al 35%.
    assert bandeja.total_descuentos == Decimal("111.11")


def test_regla_equiparar_no_aplica_si_el_cliente_tiene_pagos_huerfanos():
    reglas = [_regla("fijo_35_ves_usd"), _regla("equiparar_binance")]
    bandeja = calcular_factura(_inp(reglas, abonos=_abono_ves(), huerfanos=True))

    assert bandeja.total_descuentos == Decimal("0.00")


def test_regla_equiparar_sin_la_regla_fijo_no_tiene_tope_y_no_aplica():
    """El % de la Regla 1 es también el TOPE de la Regla 2: sin esa fila

    vigente no hay diferencial de ningún tipo.
    """
    bandeja = calcular_factura(_inp([_regla("equiparar_binance")], abonos=_abono_ves()))

    assert bandeja.total_descuentos == Decimal("0.00")


def test_regla_equiparar_ausente_no_aplica_con_pago_ves():
    """Solo la Regla 1 configurada y un pago mixto/VES: nada que otorgar."""
    bandeja = calcular_factura(_inp([_regla("fijo_35_ves_usd")], abonos=_abono_ves()))

    assert bandeja.total_descuentos == Decimal("0.00")


def test_sin_abonos_no_hay_diferencial_ni_en_el_teorico():
    """No es proyectable: depende de cómo pague el cliente."""
    reglas = [_regla("fijo_35_ves_usd"), _regla("equiparar_binance")]
    bandeja = calcular_factura(_inp(reglas, abonos=[]))

    assert bandeja.total_descuentos == Decimal("0.00")
    assert bandeja.descuentos_teorico_ves == Decimal("0.00")


def test_sin_reglas_de_diferencial_no_crashea_ni_descuenta():
    bandeja = calcular_factura(_inp([], abonos=_abono_usd()))

    assert bandeja.total_descuentos == Decimal("0.00")
    assert bandeja.total_motor == Decimal("1000.00")


def test_regla_inactiva_no_aplica():
    bandeja = calcular_factura(
        _inp([_regla("fijo_35_ves_usd", activo=False)], abonos=_abono_usd())
    )
    assert bandeja.total_descuentos == Decimal("0.00")


def test_vigencia_arranca_hoy_si_aplica():
    regla = _regla("fijo_35_ves_usd", desde=date(2026, 6, 1))
    assert calcular_factura(_inp([regla], abonos=_abono_usd())).total_descuentos == Decimal(
        "350.00"
    )


def test_vigencia_futura_no_aplica():
    regla = _regla("fijo_35_ves_usd", desde=date(2026, 6, 2))
    assert calcular_factura(_inp([regla], abonos=_abono_usd())).total_descuentos == Decimal("0.00")


def test_vigencia_vencida_ayer_no_aplica():
    regla = _regla("fijo_35_ves_usd", hasta=date(2026, 5, 31))
    assert calcular_factura(_inp([regla], abonos=_abono_usd())).total_descuentos == Decimal("0.00")


def test_vigencia_termina_hoy_si_aplica():
    regla = _regla("fijo_35_ves_usd", hasta=date(2026, 6, 1))
    assert calcular_factura(_inp([regla], abonos=_abono_usd())).total_descuentos == Decimal(
        "350.00"
    )


def test_regla_candidato_cierre_no_es_un_descuento_del_motor():
    """Regla 3 es el interruptor de un reporte aprobado a mano; su presencia

    no debe otorgar nada por sí sola.
    """
    bandeja = calcular_factura(
        _inp([_regla("candidato_cierre_factura")], abonos=_abono_usd())
    )
    assert bandeja.total_descuentos == Decimal("0.00")


def test_porcentaje_fijo_configurado_es_el_que_se_usa():
    """El % NO está hardcodeado: sale de la fila de Configuración."""
    bandeja = calcular_factura(
        _inp([_regla("fijo_35_ves_usd", pct="0.10")], abonos=_abono_usd())
    )
    assert bandeja.total_descuentos == Decimal("100.00")
