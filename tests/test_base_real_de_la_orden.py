"""La base de los descuentos es el MONTO REAL de la orden (8-oct-2026).

Casos reales: S00719 (descuento de linea 3%, la orden vale 1.878,31 y no 1.936,40; la NC correcta
fue el 35% de 1.878,31) y S00542 (descuento de linea 4,59%; con 779 USD de pendientes del FIFO
contados como pago el diferencial daba cero).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from cxc.engine.discounts import calcular_factura, calcular_teorico_orden_con_fallback
from cxc.models import EstadoVinculacion, Moneda, TipoTasa

from . import builders as b
from .reglas_helpers import LISTA_VES, inputs, precios_ambas_listas, resolver
from .test_regla_diferencial_escenarios import _regla

PROD = "1033"


def _abono(monto, estado=EstadoVinculacion.CONCILIADO, vinc_id="V1"):
    v = b.vinculacion(
        vinc_id,
        monto_aplicado=monto,
        moneda_abono=Moneda.USD,
        tipo_tasa_abono=TipoTasa.BCV,
        estado=estado,
    )
    return (v, b.metodo(moneda=Moneda.USD))


def _inp(*, descuento_linea="0", abonos=(), facturada=False, cantidad="10"):
    return inputs(
        orden=b.orden(
            fecha=date(2026, 6, 1), lista=LISTA_VES, monto_total="1000", facturada=facturada
        ),
        lineas=[
            b.linea(
                "L1",
                producto=PROD,
                marca="Sinoco",
                categoria="Comercial",
                cantidad=cantidad,
                precio="100",
                descuento=descuento_linea,
                presentacion_odoo="CAJA",
            )
        ],
        abonos=list(abonos),
        descuentos_diferencial=[_regla("fijo_35_ves_usd")],
        price_resolver=resolver(precios_ambas_listas(PROD)),
    )


def test_el_descuento_de_linea_baja_la_base_de_la_orden():
    sin = calcular_factura(_inp())
    con = calcular_factura(_inp(descuento_linea="3"))
    assert sin.precio_base_calculado == Decimal("1000.00")
    assert con.precio_base_calculado == Decimal("970.00")


def test_el_diferencial_se_mide_contra_el_monto_real_de_la_orden():
    """En este escenario la lista USD vale el 80% de la VES. Con 3% de descuento de linea la
    orden vale 970 en VES y el teorico USD real es 776: pagando 776 el hueco a cerrar es 194
    (970 - 776), no 200 (1000 - 800)."""
    bandeja = calcular_factura(_inp(descuento_linea="3", abonos=[_abono("776")]))
    dif = next(d for d in bandeja.descuentos_detalle if d.origen == "bcv_completo")
    assert dif.monto == Decimal("194.00")


def test_sin_descuento_de_linea_el_resultado_es_el_de_siempre():
    bandeja = calcular_factura(_inp(abonos=[_abono("800")]))
    dif = next(d for d in bandeja.descuentos_detalle if d.origen == "bcv_completo")
    assert dif.monto == Decimal("200.00")


def test_un_obsequio_no_se_reduce_por_su_descuento_de_linea():
    """Una linea al 100% de descuento es un regalo: se valora a precio completo."""
    inp = replace(
        _inp(),
        lineas=[
            b.linea("L1", producto=PROD, cantidad="10", precio="100", presentacion_odoo="CAJA"),
            b.linea("L2", producto=PROD, cantidad="1", precio="100", descuento="100"),
        ],
    )
    assert calcular_factura(inp).precio_base_calculado == Decimal("1100.00")


def test_el_teorico_de_ventas_sigue_a_precio_de_lista_bruto():
    """Es el punto de comparacion de «lo que debio facturarse»: sin descuento de linea."""
    teorico = calcular_teorico_orden_con_fallback(_inp(descuento_linea="3"))
    assert teorico["teorico_ves"] == Decimal("1000.00")


# --- Odoo manda: con la orden facturada, las PENDIENTES no cuentan como pagado ----------


def test_orden_facturada_ignora_las_pendientes_en_el_diferencial():
    conciliado = _abono("800", vinc_id="V1")
    pendiente = _abono("400", estado=EstadoVinculacion.PENDIENTE, vinc_id="V2")
    solo = calcular_factura(_inp(abonos=[conciliado], facturada=True))
    con_pend = calcular_factura(_inp(abonos=[conciliado, pendiente], facturada=True))

    def dif(bandeja):
        return next(d for d in bandeja.descuentos_detalle if d.origen == "bcv_completo").monto

    # Contando las pendientes lo «pagado» seria 1.200, pasaria la orden y el diferencial seria 0.
    assert dif(con_pend) == dif(solo) == Decimal("200.00")


def test_orden_sin_factura_conserva_las_pendientes_como_senal_de_pago():
    """Sin factura Odoo no puede conciliar nada: la propuesta es la unica senal de pago."""
    pendiente = _abono("800", estado=EstadoVinculacion.PENDIENTE)
    bandeja = calcular_factura(_inp(abonos=[pendiente], facturada=False))
    assert any(d.origen == "bcv_completo" for d in bandeja.descuentos_detalle)
