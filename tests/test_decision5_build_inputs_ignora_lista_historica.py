"""``EngineRunner.build_inputs`` (17-sep-2026): un segundo camino de monto real

que la decisión 5 no había tocado.

`web/app.py` aplicó la decisión 5 del quiz (12-sep-2026: «la Lista Histórica
de Auditoría y la vía euro no deben modificar los montos reales») fijando
``is_historical_pricelist_enabled`` en ``False`` siempre. Pero ese cableo
vive en `web/app.py`; `EngineRunner.build_inputs` -- que arma los
``EngineInputs`` que ``calcular_factura`` (el camino de monto real) y
``calcular_teorico_orden_con_fallback`` (el teórico) consumen -- seguía
leyendo el selector de Configuración en vivo, con default "activo" si nadie
lo había puesto en "false" nunca. En la copia de QA (17-sep-2026) el
selector nunca se había tocado (``get_config`` devuelve ``None``), así que
94 órdenes de la ventana histórica o sin lista propia seguían resolviendo
``orden_es_historica=True`` -- y con eso el precio Euro de la Lista
Histórica -- vía este camino, exactamente lo que la decisión 5 dijo que no
debía pasar.

Estos tests fijan que ``build_inputs`` ya no consulta el selector: una orden
sin lista de precios (el caso incondicional de ``es_orden_historica``) o
dentro de la ventana 20-feb/12-mar-2026 nunca resuelve histórica, sin
importar qué diga (o no diga) la Configuración.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cxc.config import EngineConfig
from cxc.engine.discounts import calcular_factura
from cxc.engine.price_resolver import DictPriceResolver
from cxc.engine.runner import EngineRunner
from cxc.models import Moneda, TipoTasa
from cxc.repositories import InMemoryRepository

from . import builders as b

CFG = EngineConfig(cash_window_business_days=3, bcv_complete_formula="differential_over_binance")


def _repo_con_orden(lista: str, fecha: date) -> InMemoryRepository:
    repo = InMemoryRepository()
    repo.upsert_clientes([b.cliente("C1")])
    repo.upsert_ordenes([b.orden("SO1", cliente_id="C1", fecha=fecha, lista=lista)])
    repo.upsert_lineas([b.linea("L1", so_id="SO1", precio="100")])
    return repo


def test_orden_sin_lista_no_es_historica_aunque_el_selector_nunca_se_haya_tocado() -> None:
    repo = _repo_con_orden(lista="", fecha=date(2026, 6, 1))
    assert repo.get_config("historical_pricelist_enabled") is None
    runner = EngineRunner(repo, DictPriceResolver({}), CFG)
    inputs = runner.build_inputs("SO1", date(2026, 6, 8))
    assert inputs is not None
    assert inputs.orden_es_historica is False
    assert inputs.historical_price_map == {}


def test_orden_en_la_ventana_historica_no_es_historica_aunque_el_selector_diga_activo() -> None:
    repo = _repo_con_orden(lista="BCV", fecha=date(2026, 3, 1))
    repo.set_config("historical_pricelist_enabled", "true")
    runner = EngineRunner(repo, DictPriceResolver({}), CFG)
    inputs = runner.build_inputs("SO1", date(2026, 3, 8))
    assert inputs is not None
    assert inputs.orden_es_historica is False
    assert inputs.historical_price_map == {}


def test_la_factura_real_de_una_orden_historica_usa_el_precio_de_lista_normal() -> None:
    """El camino de monto real (``calcular_factura``), no solo el teórico.

    Hallazgo real (17-sep-2026, medido contra la copia de QA): 58 órdenes de
    la ventana histórica con abono ya registrado tenían su
    ``bandeja_facturacion.total_motor`` -- lo que el cliente debe -- calculado
    con el precio Euro de la Lista Histórica, no con el de la lista de Odoo.
    Este test fija que, con abono y selector "activo", la factura real usa
    el precio que devuelve el ``PriceResolver`` (lista de Odoo), no ningún
    precio histórico -- sin este fix habría usado la lista '7' fija de
    ``_LISTA_USD_HISTORICA`` sin siquiera consultar el resolver para P1.
    """
    repo = InMemoryRepository()
    repo.upsert_clientes([b.cliente("C1")])
    repo.upsert_ordenes(
        [b.orden("SO1", cliente_id="C1", fecha=date(2026, 3, 1), lista="")]
    )
    repo.upsert_lineas([b.linea("L1", so_id="SO1", producto="P1", precio="100")])
    repo.add_metodo_pago(b.metodo("M1", moneda=Moneda.USD, es_contado=False))
    repo.upsert_pagos([b.pago("PG1", cliente_id="C1", monto="100", metodo_id="M1")])
    repo.add_vinculacion(
        b.vinculacion(
            "V1",
            pago_id="PG1",
            so_id="SO1",
            monto_aplicado="100",
            moneda_abono=Moneda.USD,
            tipo_tasa_abono=TipoTasa.N_A,
            hora=datetime(2026, 3, 2, 10, 0),
        )
    )
    repo.set_config("historical_pricelist_enabled", "true")

    resolver = DictPriceResolver({("P1", "USD"): Decimal("100")})
    runner = EngineRunner(repo, resolver, CFG)
    inputs = runner.build_inputs("SO1", date(2026, 3, 8))
    assert inputs is not None
    bandeja = calcular_factura(inputs)
    assert bandeja.precio_base_calculado == Decimal("100.00")
