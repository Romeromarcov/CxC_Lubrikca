"""Helpers compartidos por los tests de escenarios de reglas (auditoría de

reglas del motor, agosto 2026).

``tests/test_engine.py`` tiene su propio ``_inputs`` local, pero no expone
``exclusiones`` ni permite variar todos los ejes que necesita la matriz de
escenarios por tipo de regla (matchea / no matchea / vigencia / inactiva /
sin reglas / varias candidatas). Este módulo centraliza esa construcción
para no repetirla en cada archivo ``test_regla_*_escenarios.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.config import EngineConfig
from cxc.engine.discounts import EngineInputs
from cxc.engine.price_resolver import DictPriceResolver

CFG = EngineConfig(
    cash_window_business_days=3,
    bcv_complete_formula="differential_over_binance",
)

# Ids de pricelist usados por los escenarios -- deliberadamente numéricos,
# como los reales de Odoo, para que el matching de ``listas_aplicables``
# ("LISTAS_VES"/"LISTAS_USD") se ejercite igual que en producción.
LISTA_VES = "5"
LISTA_USD = "8"


def resolver(
    precios: dict[tuple[str, str], str] | None = None,
    volumenes: dict[str, str] | None = None,
) -> DictPriceResolver:
    return DictPriceResolver(
        {k: Decimal(v) for k, v in (precios or {}).items()},
        {k: Decimal(v) for k, v in (volumenes or {}).items()},
    )


def precios_ambas_listas(*productos: str, ves: str = "100", usd: str = "80") -> dict:
    """Precio para cada producto en AMBAS listas vigentes.

    Los teóricos se calculan siempre contra las dos listas; si a alguna le
    falta un precio ``_teoricos_por_lista`` traga el ``KeyError`` y devuelve
    ceros, lo que enmascararía el escenario bajo prueba.
    """
    mapa: dict[tuple[str, str], str] = {}
    for p in productos:
        mapa[(p, LISTA_VES)] = ves
        mapa[(p, LISTA_USD)] = usd
    return mapa


def inputs(
    *,
    orden,
    lineas,
    abonos=(),
    descuentos=(),
    descuentos_volumen=(),
    reglas=(),
    promociones=(),
    feriados=(),
    price_resolver=None,
    fecha_calculo=date(2026, 6, 8),
    engine_config=None,
    exclusiones=(),
    descuentos_recompra=(),
    descuentos_diferencial=(),
    descuentos_producto=(),
    historial_cliente_lineas=(),
    orden_anterior=None,
    orden_anterior_vincs=(),
    valid_ves=(LISTA_VES,),
    valid_usd=(LISTA_USD,),
    cliente_tiene_pagos_huerfanos=False,
) -> EngineInputs:
    return EngineInputs(
        orden=orden,
        lineas=list(lineas),
        abonos=list(abonos),
        descuentos=list(descuentos),
        descuentos_volumen=list(descuentos_volumen),
        reglas_recurrencia=list(reglas),
        promociones_primera_compra=list(promociones),
        feriados_tabla=list(feriados),
        price_resolver=price_resolver or resolver(),
        engine_config=engine_config or CFG,
        fecha_calculo=fecha_calculo,
        exclusiones=list(exclusiones),
        descuentos_recompra=list(descuentos_recompra),
        descuentos_diferencial=list(descuentos_diferencial),
        descuentos_producto=list(descuentos_producto),
        historial_cliente_lineas=list(historial_cliente_lineas),
        orden_anterior_cliente=orden_anterior,
        orden_anterior_cliente_vincs=list(orden_anterior_vincs),
        valid_ves=list(valid_ves),
        valid_usd=list(valid_usd),
        cliente_tiene_pagos_huerfanos=cliente_tiene_pagos_huerfanos,
    )
