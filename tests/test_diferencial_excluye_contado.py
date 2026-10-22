"""Si aplica el diferencial cambiario, no aplica el contado/pronto pago (caso S01226).

La orden nace en lista VES; el vendedor le puso al renglon un descuento a mano (31,45 %) para
llevar el monto en VES a lo que valdria a tasa Binance, y el cliente pago exactamente ese monto.
Ese descuento ES el diferencial. El motor le sumaba ademas el 20 % de contado: decision del
usuario (22-oct-2026) -- uno excluye al otro, y la exclusion es configurable (tabla
``exclusiones``, par ``contado`` <-> ``bcv_completo``).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.engine.discounts import calcular_factura
from cxc.models import DescuentoDiferencialCambiario, ExclusionRegla, Moneda, TipoTasa

from . import builders as b
from .reglas_helpers import LISTA_VES, inputs, precios_ambas_listas, resolver

PROD = "1033"
EXCLUSION = ExclusionRegla(regla_tipo_a="contado", regla_tipo_b="bcv_completo", activo=True)


def _dif():
    return DescuentoDiferencialCambiario(
        regla_id="DIF_FIJO",
        tipo_diferencial="fijo_35_ves_usd",
        porcentaje_fijo=Decimal("0.35"),
        listas_aplicables="LISTAS_VES",
        vigencia_desde=date(2026, 1, 1),
        vigencia_hasta=None,
        activo=True,
    )


def _inp(*, exclusiones=(), descuento_linea="31.45", pagado_ves="24678"):
    vinc = b.vinculacion(
        monto_aplicado=pagado_ves, moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV
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
        descuentos=[b.descuento("PP20", marca="*", categoria="*", porcentaje="0.20")],
        descuentos_diferencial=[_dif()],
        exclusiones=list(exclusiones),
        price_resolver=resolver(precios_ambas_listas(PROD, ves="100", usd="65")),
    )


def test_sin_la_exclusion_el_contado_se_apila_sobre_un_precio_que_ya_trae_el_diferencial():
    bandeja = calcular_factura(_inp())

    assert bandeja.total_descuentos > 0  # el contado


def test_con_la_exclusion_el_diferencial_ya_en_el_precio_anula_el_contado():
    bandeja = calcular_factura(_inp(exclusiones=[EXCLUSION]))

    assert bandeja.total_descuentos == Decimal("0.00")
    # Lo que el precio ya trae: la brecha contra la lista (1000 - 685,50), por debajo del tope
    # de 35 % (350). Es el mismo 31,45 % que el vendedor puso en la linea.
    assert bandeja.diferencial_en_precio == Decimal("314.50")
    # La base ya es el monto real de la orden: no se le resta de nuevo.
    assert bandeja.total_motor == bandeja.precio_base_calculado


def test_la_exclusion_cubre_tambien_la_regla_de_pronto_pago_con_otra_ventana():
    """El 15 % «hasta el vencimiento» es un pronto pago, pero vive en la misma tabla y entra al
    mismo componente que el contado: lo cubre la misma exclusion."""
    inp = _inp(exclusiones=[EXCLUSION])
    inp.descuentos.append(
        b.descuento(
            "PP15", marca="*", categoria="*", porcentaje="0.15", ventana_pago_tipo="vencimiento"
        )
    )
    assert calcular_factura(inp).total_descuentos == Decimal("0.00")


def test_si_no_hay_pago_que_justifique_el_diferencial_el_contado_sigue_aplicando():
    """Sin pagos no hay diferencial: la exclusion no tiene a quien darle prioridad."""
    inp = _inp(exclusiones=[EXCLUSION])
    inp.abonos = []
    assert calcular_factura(inp).diferencial_en_precio == Decimal("0")


def test_orden_sin_descuento_en_la_linea_cobra_el_diferencial_y_anula_el_contado():
    """Caso normal (sin descuento puesto a mano): el diferencial se calcula y el contado cede."""
    bandeja = calcular_factura(
        _inp(exclusiones=[EXCLUSION], descuento_linea="0", pagado_ves="24678")
    )
    assert bandeja.diferencial_en_precio == Decimal("0")
    detalle = [d.origen for d in bandeja.descuentos_detalle]
    assert "contado" not in detalle
    assert "bcv_completo" in detalle
