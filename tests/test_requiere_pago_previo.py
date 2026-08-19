"""Tarea 1: reglas de descuento con ``requiere_pago_previo``.

El motor debe excluir una regla cuando la orden/factura no tiene ningún
abono (``EngineInputs.abonos``) vinculado todavía y la regla tiene
``requiere_pago_previo=True``. Reglas con el flag en ``False`` deben seguir
evaluándose sin importar si hay abonos o no.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cxc.config import EngineConfig
from cxc.engine.discounts import (
    EngineInputs,
    _filtrar_por_pago_previo,
    calcular_factura,
    calcular_teorico_orden_con_fallback,
)
from cxc.engine.price_resolver import DictPriceResolver
from cxc.models import Moneda, TipoTasa

from . import builders as b

CFG = EngineConfig(
    cash_window_business_days=3,
    bcv_complete_formula="differential_over_binance",
)


def _resolver(**precios: str) -> DictPriceResolver:
    mapa: dict[tuple[str, str], Decimal] = {}
    for clave, val in precios.items():
        producto, lista = clave.split("@")
        mapa[(producto, lista)] = Decimal(val)
    return DictPriceResolver(mapa)


def _inputs(
    *,
    orden,
    lineas,
    abonos,
    descuentos=(),
    descuentos_volumen=(),
    resolver,
    fecha_calculo=date(2026, 6, 8),
    valid_usd=(),
    valid_ves=(),
) -> EngineInputs:
    return EngineInputs(
        orden=orden,
        lineas=list(lineas),
        abonos=list(abonos),
        descuentos=list(descuentos),
        descuentos_volumen=list(descuentos_volumen),
        reglas_recurrencia=[],
        promociones_primera_compra=[],
        feriados_tabla=[],
        price_resolver=resolver,
        engine_config=CFG,
        fecha_calculo=fecha_calculo,
        all_ordenes=[],
        valid_usd=list(valid_usd),
        valid_ves=list(valid_ves),
    )


# --- Unidad: el filtro puro -------------------------------------------------


def test_filtro_excluye_reglas_que_requieren_pago_sin_abonos() -> None:
    regla_true = b.descuento(requiere_pago_previo=True)
    regla_false = b.descuento(regla_id="D2", requiere_pago_previo=False)
    assert _filtrar_por_pago_previo([regla_true, regla_false], tiene_pago=False) == [regla_false]


def test_filtro_no_excluye_nada_si_hay_pago() -> None:
    regla_true = b.descuento(requiere_pago_previo=True)
    regla_false = b.descuento(regla_id="D2", requiere_pago_previo=False)
    reglas = [regla_true, regla_false]
    assert _filtrar_por_pago_previo(reglas, tiene_pago=True) == reglas


# --- Integración: motor completo (contado = requiere_pago_previo=True) -----


def test_contado_no_aplica_sin_abonos_aunque_regla_este_vigente() -> None:
    """Sin abonos, `contado_evaluable` ya es False -- y el filtro también
    excluiría la regla porque `requiere_pago_previo=True` por defecto en
    DescuentoProntoPago."""
    orden = b.orden(primera=False)
    linea = b.linea(marca="Sinoco", categoria="*", precio="100", cantidad="1")
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[],  # sin pagos vinculados todavía
        descuentos=[b.descuento(marca="Sinoco", categoria="*", porcentaje="0.03")],
        resolver=_resolver(**{"P1@BCV": "100"}),
    )
    res = calcular_factura(inp)
    assert res.total_descuentos == Decimal("0")
    assert res.total_motor == Decimal("100.00")


def test_contado_aplica_con_abonos_y_requiere_pago_previo_true() -> None:
    orden = b.orden(primera=False)
    linea = b.linea(marca="Sinoco", categoria="*", precio="100", cantidad="1")
    metodo = b.metodo(moneda=Moneda.USD, es_contado=True)
    vinc = b.vinculacion(
        monto_aplicado="97",
        moneda_abono=Moneda.USD,
        tipo_tasa_abono=TipoTasa.N_A,
        hora=datetime(2026, 6, 5, 10, 0),
    )
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[(vinc, metodo)],
        descuentos=[
            b.descuento(
                marca="Sinoco", categoria="*", porcentaje="0.03", requiere_pago_previo=True
            )
        ],
        resolver=_resolver(**{"P1@USD": "100"}),
    )
    res = calcular_factura(inp)
    assert res.total_descuentos == Decimal("3.00")
    assert res.total_motor == Decimal("97.00")


# --- Integración: regla con requiere_pago_previo=False no depende de abonos


def test_volumen_con_flag_false_aplica_incluso_sin_abonos() -> None:
    orden = b.orden(primera=False)
    linea = b.linea(marca="Sinoco", categoria="*", precio="100", cantidad="200")
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[],  # sin pagos vinculados todavía
        descuentos_volumen=[
            b.descuento_volumen(
                marca="Sinoco",
                categoria="*",
                litros_minimo="0",
                porcentaje="0.05",
                requiere_pago_previo=False,
            )
        ],
        resolver=_resolver(**{"P1@BCV": "100"}),
    )
    res = calcular_factura(inp)
    assert res.total_descuentos == Decimal("1000.00")
    assert res.total_motor == Decimal("19000.00")


def test_volumen_con_flag_true_no_aplica_sin_abonos() -> None:
    orden = b.orden(primera=False)
    linea = b.linea(marca="Sinoco", categoria="*", precio="100", cantidad="200")
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[],  # sin pagos vinculados todavía
        descuentos_volumen=[
            b.descuento_volumen(
                marca="Sinoco",
                categoria="*",
                litros_minimo="0",
                porcentaje="0.05",
                requiere_pago_previo=True,
            )
        ],
        resolver=_resolver(**{"P1@BCV": "100"}),
    )
    res = calcular_factura(inp)
    assert res.total_descuentos == Decimal("0")
    assert res.total_motor == Decimal("20000.00")


# --- Puntos 5-6 (agosto 2026): el TEÓRICO debe mostrar descuentos con
# requiere_pago_previo=True aunque la orden no tenga abonos todavía -- solo
# lo "aplicable ahora" (factura real, arriba) exige el pago. -------------


def test_teorico_muestra_contado_con_pago_previo_aunque_no_haya_abonos() -> None:
    """A diferencia de `calcular_factura` (arriba, $0 sin abonos), el

    teórico debe mostrar los $3 de Contado -- es lo que "se mantiene en
    memoria temporal" esperando la ventana de pago, y debe verse en las
    columnas teóricas/pendiente incluso antes de que exista un abono."""
    orden = b.orden(primera=False)
    linea = b.linea(marca="Sinoco", categoria="*", precio="100", cantidad="1")
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[],  # sin pagos vinculados todavía
        descuentos=[
            b.descuento(
                marca="Sinoco", categoria="*", porcentaje="0.03", requiere_pago_previo=True
            )
        ],
        resolver=_resolver(**{"P1@USD": "100", "P1@BCV": "100"}),
        valid_usd=["USD"],
        valid_ves=["BCV"],
    )
    factura = calcular_factura(inp)
    assert factura.total_descuentos == Decimal("0")  # no materializado sin pago

    teorico = calcular_teorico_orden_con_fallback(inp)
    assert teorico["descuentos_teorico_usd"] == Decimal("3.00")
    assert teorico["descuentos_teorico_ves"] == Decimal("3.00")


def test_teorico_no_muestra_contado_si_la_ventana_ya_vencio_sin_abonos() -> None:
    """Igual que Recompra: una vez vencida la ventana de pago (3 días

    hábiles desde la entrega en `CFG`), Contado deja de proyectarse en el
    teórico -- ya no podría confirmarse aunque llegara un abono ahora."""
    orden = b.orden(primera=False, fecha_entrega=date(2026, 6, 5))
    linea = b.linea(marca="Sinoco", categoria="*", precio="100", cantidad="1")
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[],
        descuentos=[
            b.descuento(
                marca="Sinoco", categoria="*", porcentaje="0.03", requiere_pago_previo=True
            )
        ],
        resolver=_resolver(**{"P1@USD": "100", "P1@BCV": "100"}),
        valid_usd=["USD"],
        valid_ves=["BCV"],
        fecha_calculo=date(2026, 6, 20),  # muy fuera de la ventana de 3 días hábiles
    )
    teorico = calcular_teorico_orden_con_fallback(inp)
    assert teorico["descuentos_teorico_usd"] == Decimal("0.00")
    assert teorico["descuentos_teorico_ves"] == Decimal("0.00")
