"""Test de integración del EngineRunner: repositorio → motor → bandeja."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cxc.config import EngineConfig
from cxc.engine.price_resolver import DictPriceResolver
from cxc.engine.runner import EngineRunner
from cxc.models import Moneda, TipoTasa
from cxc.repositories import InMemoryRepository

from . import builders as b

CFG = EngineConfig(
    cash_window_business_days=3,
    bcv_complete_formula="differential_over_binance",
)


def _seed() -> InMemoryRepository:
    repo = InMemoryRepository()
    repo.upsert_clientes([b.cliente("C1")])
    repo.upsert_ordenes([b.orden("SO1", cliente_id="C1", primera=False)])
    repo.upsert_lineas([b.linea("L1", so_id="SO1", marca="Sinoco", categoria="*", precio="100")])
    repo.add_metodo_pago(b.metodo("M1", moneda=Moneda.USD, es_contado=True))
    repo.upsert_pagos([b.pago("PG1", cliente_id="C1", monto="94", metodo_id="M1")])
    repo.add_vinculacion(
        b.vinculacion(
            "V1",
            pago_id="PG1",
            so_id="SO1",
            monto_aplicado="94",
            moneda_abono=Moneda.USD,
            tipo_tasa_abono=TipoTasa.N_A,
            hora=datetime(2026, 6, 5, 10, 0),
        )
    )
    repo.add_descuento(b.descuento("D1", marca="Sinoco", categoria="*", porcentaje="0.03"))
    repo.add_regla_recurrencia(b.regla_recompra("0.03"))
    return repo


def test_runner_calcula_y_persiste_bandeja() -> None:
    repo = _seed()
    resolver = DictPriceResolver({("P1", "USD"): Decimal("100")})
    runner = EngineRunner(repo, resolver, CFG)

    resultados = runner.run_all(date(2026, 6, 8))
    assert len(resultados) == 1
    bandeja = repo.get_bandeja("SO1")
    assert bandeja is not None
    assert bandeja.total_descuentos == Decimal("6.00")  # 3% + 3%
    assert bandeja.total_motor == Decimal("94.00")
    assert bandeja.candidata_a_cierre is True

    # Los equivalentes quedaron congelados en la vinculación.
    vinc = repo.vinculaciones_de_orden("SO1")[0]
    assert vinc.equiv_usd_binance is not None


def test_runner_omite_ordenes_facturadas() -> None:
    repo = _seed()
    orden = repo.get_orden("SO1")
    assert orden is not None
    orden.facturada = True
    repo.upsert_ordenes([orden])
    runner = EngineRunner(repo, DictPriceResolver({("P1", "USD"): Decimal("100")}), CFG)
    assert runner.run_all(date(2026, 6, 8)) == []


def test_run_orden_inexistente_devuelve_none() -> None:
    repo = _seed()
    runner = EngineRunner(repo, DictPriceResolver({("P1", "USD"): Decimal("100")}), CFG)
    assert runner.run_orden("NOPE", date(2026, 6, 8)) is None
