"""Tarea 3a/3b/4a/4b: equivalente/teóricos por lista en BandejaFacturacion.

El motor expone, junto al cálculo real, los teóricos en lista VES y USD
vigentes (y sus descuentos correspondientes) reutilizando
``_calcular_componentes`` -- no se duplica la lógica de descuentos, solo se
corre con una lista distinta a la aplicada. Ver
docs/REDISENO_DESCUENTOS_UNIFICADOS.md, Tarea 3/4.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.config import EngineConfig
from cxc.engine.discounts import EngineInputs, calcular_factura
from cxc.engine.price_resolver import DictPriceResolver

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


def _inputs(*, orden, lineas, abonos, resolver) -> EngineInputs:
    return EngineInputs(
        orden=orden,
        lineas=list(lineas),
        abonos=list(abonos),
        descuentos=[],
        descuentos_volumen=[],
        reglas_recurrencia=[],
        descuento_bcv_diario=[],
        promociones_primera_compra=[],
        feriados_tabla=[],
        price_resolver=resolver,
        engine_config=CFG,
        fecha_calculo=date(2026, 6, 8),
        all_ordenes=[],
    )


def test_orden_nacida_en_usd_equivalente_igual_al_teorico_usd() -> None:
    orden = b.orden(lista="USD", primera=False)
    linea = b.linea(marca="Sinoco", categoria="*", precio="100", cantidad="1")
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[],
        resolver=_resolver(**{"P1@USD": "100", "P1@BCV": "3600"}),
    )
    res = calcular_factura(inp)
    assert res.teorico_lista_usd == Decimal("100.00")
    assert res.teorico_lista_ves == Decimal("3600.00")
    assert res.equivalente_lista_usd == Decimal("100.00")


def test_orden_nacida_en_ves_equivalente_es_teorico_en_usd() -> None:
    orden = b.orden(lista="BCV", primera=False)
    linea = b.linea(marca="Sinoco", categoria="*", precio="3600", cantidad="1")
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[],
        resolver=_resolver(**{"P1@USD": "100", "P1@BCV": "3600"}),
    )
    res = calcular_factura(inp)
    assert res.teorico_lista_usd == Decimal("100.00")
    assert res.teorico_lista_ves == Decimal("3600.00")
    # 3a: nació en VES -> el equivalente USD es el teórico, no el real (VES).
    assert res.equivalente_lista_usd == Decimal("100.00")


def test_sin_precio_en_alguna_lista_no_rompe_el_calculo() -> None:
    """Catálogo parcial (solo tiene precio en la lista de nacimiento) --

    el teórico de la lista sin dato cae a 0 en vez de propagar KeyError."""
    orden = b.orden(lista="USD", primera=False)
    linea = b.linea(marca="Sinoco", categoria="*", precio="100", cantidad="1")
    inp = _inputs(
        orden=orden,
        lineas=[linea],
        abonos=[],
        resolver=_resolver(**{"P1@USD": "100"}),
    )
    res = calcular_factura(inp)
    assert res.teorico_lista_usd == Decimal("100.00")
    assert res.teorico_lista_ves == Decimal("0.00")
