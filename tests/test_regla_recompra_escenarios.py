"""Matriz de escenarios de Recompra / Recurrencia.

Hay DOS tablas en juego y conviene no confundirlas:

- ``descuentos_recompra`` (``DescuentoRecompra``) es la que edita el panel
  "Descuento por Recompra/Recurrencia" y la que el motor prefiere.
- ``reglas_recurrencia`` (``ReglaRecurrencia``) es legado: solo se usa como
  respaldo si NO hay ninguna fila vigente en ``descuentos_recompra``, y su
  único editor es el campo "Descuento Recompra Legado (%)" de Configuración.
  Como producción sí tiene filas en ``descuentos_recompra``, hoy la tabla
  legado no interviene -- el último test de este archivo fija esa
  precedencia para que el respaldo no se active por accidente.

La condición de recompra NO es "primera orden del mes": exige que la orden
inmediatamente anterior del cliente esté totalmente pagada y que esta orden
llegue dentro de la ventana (días de crédito reales de esa orden + margen).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cxc.engine.discounts import calcular_factura
from cxc.models import Moneda, TipoTasa

from . import builders as b
from .reglas_helpers import LISTA_VES, inputs, precios_ambas_listas, resolver

PROD = "1033"


def _anterior(*, fecha=date(2026, 5, 1), dias_credito=30, pagada=True):
    """Orden anterior del cliente y sus vinculaciones."""
    orden = b.orden("SO0", fecha=fecha, monto_total="100", dias_credito=dias_credito)
    if not pagada:
        return orden, []
    vinc = b.vinculacion(
        monto_aplicado="100",
        hora=datetime(2026, 5, 2, 10, 0),
        moneda_abono=Moneda.USD,
        tipo_tasa_abono=TipoTasa.N_A,
    )
    return orden, [vinc]


def _inp(reglas=(), *, anterior=None, cantidad="10", marca="Sinoco", categoria="Comercial",
         presentacion="CAJA", reglas_legacy=()):
    """Línea con la forma REAL de Odoo: ``categoria`` es la categoría madre

    (Comercial/Industrial) y la presentación viaja en ``presentacion_odoo``.
    Sin ``presentacion_odoo``, ``LineaOrden.presentacion`` adivina "PAILA"
    para todo lo que no sea Comercial, y una regla scoped a PAILA matchearía
    líneas que no le tocan.
    """
    orden_ant, vincs_ant = anterior if anterior is not None else _anterior()
    return inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista=LISTA_VES),
        lineas=[b.linea("L1", producto=PROD, marca=marca, categoria=categoria,
                        cantidad=cantidad, precio="100", presentacion_odoo=presentacion)],
        descuentos_recompra=list(reglas),
        reglas=list(reglas_legacy),
        orden_anterior=orden_ant,
        orden_anterior_vincs=vincs_ant,
        price_resolver=resolver(precios_ambas_listas(PROD)),
    )


def _regla(regla_id="REC1", **kw):
    kw.setdefault("marca", "*")
    kw.setdefault("categoria", "*")
    kw.setdefault("porcentaje", "0.05")
    return b.descuento_recompra(regla_id, **kw)


def test_recompra_matchea_y_se_cobra():
    bandeja = calcular_factura(_inp([_regla()]))

    assert bandeja.total_descuentos == Decimal("50.00")
    assert bandeja.descuentos_teorico_ves == Decimal("50.00")


def test_recompra_no_aplica_si_la_orden_anterior_no_esta_pagada():
    anterior = _anterior(pagada=False)
    assert calcular_factura(_inp([_regla()], anterior=anterior)).total_descuentos == Decimal("0.00")


def test_recompra_no_aplica_sin_orden_anterior():
    """Primer pedido del cliente: no hay recompra posible."""
    inp = inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista=LISTA_VES),
        lineas=[b.linea("L1", producto=PROD, marca="Sinoco", categoria="CAJA", cantidad="10",
                        precio="100")],
        descuentos_recompra=[_regla()],
        orden_anterior=None,
        price_resolver=resolver(precios_ambas_listas(PROD)),
    )
    assert calcular_factura(inp).total_descuentos == Decimal("0.00")


def test_recompra_dentro_de_la_ventana_de_pago_aplica():
    """Anterior del 1/5 con 30 días de crédito + 3 de margen -> vence el 3/6;

    esta orden es del 1/6.
    """
    anterior = _anterior(fecha=date(2026, 5, 1), dias_credito=30)
    regla = _regla(ventana_pago_tipo="vencimiento", dias_gracia=3)
    assert calcular_factura(
        _inp([regla], anterior=anterior)
    ).total_descuentos == Decimal("50.00")


def test_recompra_fuera_de_la_ventana_de_pago_no_aplica():
    """Misma regla, pero la orden anterior es tan vieja que la ventana ya

    venció antes del 1/6.
    """
    anterior = _anterior(fecha=date(2026, 1, 1), dias_credito=30)
    regla = _regla(ventana_pago_tipo="vencimiento", dias_gracia=3)
    assert calcular_factura(_inp([regla], anterior=anterior)).total_descuentos == Decimal("0.00")


def test_recompra_fuera_del_tramo_de_cajas_no_aplica():
    regla = _regla(min_cajas=20, max_cajas=50)
    assert calcular_factura(_inp([regla], cantidad="10")).total_descuentos == Decimal("0.00")


def test_recompra_en_el_borde_del_tramo_aplica():
    regla = _regla(min_cajas=10, max_cajas=10)
    assert calcular_factura(_inp([regla], cantidad="10")).total_descuentos == Decimal("50.00")


def test_recompra_no_matchea_por_marca_distinta():
    regla = _regla(marca="GLOBAL OIL")
    assert calcular_factura(_inp([regla], marca="Sinoco")).total_descuentos == Decimal("0.00")


def test_recompra_no_matchea_por_categoria_distinta():
    """Regla scoped a TAMBOR contra una línea Comercial/CAJA."""
    regla = _regla(categoria="TAMBOR")
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("0.00")


def test_recompra_matchea_por_presentacion_ademas_de_categoria():
    """Las reglas reales de producción vienen scoped por presentación

    ("CAJA"), no por la categoría madre -- el matching debe alcanzarlas.
    """
    regla = _regla(categoria="CAJA")
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("50.00")


def test_recompra_regla_inactiva_no_aplica():
    assert calcular_factura(_inp([_regla(activo=False)])).total_descuentos == Decimal("0.00")


def test_recompra_vigencia_arranca_hoy_si_aplica():
    regla = _regla(vigencia_desde=date(2026, 6, 1))
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("50.00")


def test_recompra_vigencia_futura_no_aplica():
    regla = _regla(vigencia_desde=date(2026, 6, 2))
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("0.00")


def test_recompra_vigencia_vencida_ayer_no_aplica():
    regla = _regla(vigencia_hasta=date(2026, 5, 31))
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("0.00")


def test_recompra_vigencia_termina_hoy_si_aplica():
    regla = _regla(vigencia_hasta=date(2026, 6, 1))
    assert calcular_factura(_inp([regla])).total_descuentos == Decimal("50.00")


def test_sin_reglas_de_recompra_no_crashea_ni_descuenta():
    bandeja = calcular_factura(_inp([]))

    assert bandeja.total_descuentos == Decimal("0.00")
    assert bandeja.total_motor == Decimal("1000.00")


def test_varios_tramos_gana_el_de_mayor_porcentaje_aplicable():
    """Los tramos reales de producción no se solapan, pero si dos reglas

    alcanzan la misma línea gana la más generosa.
    """
    tramo1 = _regla("REC_TRAMO1", min_cajas=2, max_cajas=20, porcentaje="0.03")
    tramo2 = _regla("REC_TRAMO2", min_cajas=5, max_cajas=999999, porcentaje="0.05")
    assert calcular_factura(_inp([tramo1, tramo2])).total_descuentos == Decimal("50.00")


def test_tramo_correcto_segun_la_cantidad():
    """Con 3 cajas solo aplica el tramo 2-4, no el de 5 o más."""
    tramo1 = _regla("REC_TRAMO1", min_cajas=2, max_cajas=4, porcentaje="0.03")
    tramo2 = _regla("REC_TRAMO2", min_cajas=5, max_cajas=999999, porcentaje="0.05")
    # 3 x 100 = 300 de base, 3%.
    assert calcular_factura(
        _inp([tramo1, tramo2], cantidad="3")
    ).total_descuentos == Decimal("9.00")


def test_recompra_scoped_a_lista_ves_no_afecta_el_teorico_usd():
    regla = _regla()
    regla.listas_aplicables = LISTA_VES
    bandeja = calcular_factura(_inp([regla]))

    assert bandeja.descuentos_teorico_ves == Decimal("50.00")
    assert bandeja.descuentos_teorico_usd == Decimal("0.00")


def test_regla_legacy_solo_actua_si_no_hay_descuentos_recompra():
    """Precedencia entre las dos tablas: ``descuentos_recompra`` manda.

    Con una fila vigente ahí, el % legado de ``reglas_recurrencia`` no debe
    intervenir; sin ninguna, el legado sí sirve de respaldo.
    """
    legacy = b.regla_recompra(valor="0.10")

    con_ambas = calcular_factura(_inp([_regla(porcentaje="0.05")], reglas_legacy=[legacy]))
    solo_legacy = calcular_factura(_inp([], reglas_legacy=[legacy]))

    assert con_ambas.total_descuentos == Decimal("50.00")
    assert solo_legacy.total_descuentos == Decimal("100.00")
