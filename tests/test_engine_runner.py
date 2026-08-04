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


def test_run_teoricos_pendientes_calcula_ordenes_facturadas() -> None:
    """A diferencia de run_all, run_teoricos_pendientes SÍ procesa órdenes

    ya facturadas -- el teórico es precisamente el punto de comparación
    que más se necesita ahí (Fase 10)."""
    repo = _seed()
    orden = repo.get_orden("SO1")
    assert orden is not None
    orden.facturada = True
    repo.upsert_ordenes([orden])

    resolver = DictPriceResolver({("P1", "USD"): Decimal("100"), ("P1", "BCV"): Decimal("90")})
    runner = EngineRunner(repo, resolver, CFG)

    assert runner.run_all(date(2026, 6, 8)) == []  # sigue saltando facturadas
    procesadas = runner.run_teoricos_pendientes(date(2026, 6, 8))
    assert procesadas == 1

    teorico = repo.get_ventas_teorico("SO1")
    assert teorico is not None
    assert teorico.teorico_usd == Decimal("100.00")
    assert teorico.teorico_ves == Decimal("90.00")


def test_run_teoricos_pendientes_no_recalcula_si_ya_existe_sin_fallback() -> None:
    """Un teórico ya calculado SIN fallback es fijo -- no se recalcula en

    corridas posteriores aunque el resolver cambiaría el resultado."""
    repo = _seed()
    resolver = DictPriceResolver({("P1", "USD"): Decimal("100"), ("P1", "BCV"): Decimal("90")})
    runner = EngineRunner(repo, resolver, CFG)

    assert runner.run_teoricos_pendientes(date(2026, 6, 8)) == 1
    primero = repo.get_ventas_teorico("SO1")
    assert primero is not None
    assert primero.teorico_usd == Decimal("100.00")

    # El resolver "cambia de opinión" -- si se recalculara, daría 999.
    resolver.set_precio("P1", "USD", Decimal("999"))
    assert runner.run_teoricos_pendientes(date(2026, 6, 8)) == 0
    segundo = repo.get_ventas_teorico("SO1")
    assert segundo is not None
    assert segundo.teorico_usd == Decimal("100.00")  # sin cambios


def test_run_teoricos_pendientes_recalcula_si_estaba_marcado_fallback() -> None:
    """Un teórico marcado usa_fallback SÍ se re-verifica en la siguiente

    corrida -- si la lista ya se completó, el nuevo valor se guarda."""
    from cxc.engine.price_resolver import PriceResolver

    class _ResolverConFallback(PriceResolver):
        def __init__(self) -> None:
            self.resuelto_sin_fallback = False

        def precio(self, producto, lista, fecha=None):
            if lista == "BCV" and not self.resuelto_sin_fallback:
                return Decimal("100")  # fallback: usa el mismo que USD
            return Decimal("100") if lista == "USD" else Decimal("90")

        def volumen(self, producto):
            return Decimal("0")

        def fue_fallback(self, producto, lista):
            return lista == "BCV" and not self.resuelto_sin_fallback

    repo = _seed()
    resolver = _ResolverConFallback()
    runner = EngineRunner(repo, resolver, CFG)

    assert runner.run_teoricos_pendientes(date(2026, 6, 8)) == 1
    primero = repo.get_ventas_teorico("SO1")
    assert primero is not None
    assert primero.usa_fallback_ves is True
    assert primero.teorico_ves == Decimal("100.00")  # via fallback

    # La lista BCV "se completa" -- el resolver ya no necesita fallback.
    resolver.resuelto_sin_fallback = True
    assert runner.run_teoricos_pendientes(date(2026, 6, 8)) == 1  # se re-verificó
    segundo = repo.get_ventas_teorico("SO1")
    assert segundo is not None
    assert segundo.usa_fallback_ves is False
    assert segundo.teorico_ves == Decimal("90.00")  # valor real, ya no fallback

    # Ahora que quedó sin fallback, una tercera corrida NO lo vuelve a tocar.
    assert runner.run_teoricos_pendientes(date(2026, 6, 8)) == 0
