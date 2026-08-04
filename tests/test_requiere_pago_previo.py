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
from cxc.engine.discounts import EngineInputs, _filtrar_por_pago_previo, calcular_factura
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
    bcv_diario=(),
    resolver,
    fecha_calculo=date(2026, 6, 8),
) -> EngineInputs:
    return EngineInputs(
        orden=orden,
        lineas=list(lineas),
        abonos=list(abonos),
        descuentos=list(descuentos),
        descuentos_volumen=list(descuentos_volumen),
        reglas_recurrencia=[],
        descuento_bcv_diario=list(bcv_diario),
        promociones_primera_compra=[],
        feriados_tabla=[],
        price_resolver=resolver,
        engine_config=CFG,
        fecha_calculo=fecha_calculo,
        all_ordenes=[],
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
