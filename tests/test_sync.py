"""Tests del sync incremental delta (sección 2 + regla de oro 1.2)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from cxc.models import Moneda, SerieTasa, TipoTasa
from cxc.odoo.client import OdooReader
from cxc.repositories import InMemoryRepository
from cxc.sync.incremental import IncrementalSync

from . import builders as b


class FakeOdooReader(OdooReader):
    """Reader controlable: devuelve distintas filas según el cursor."""

    def __init__(self) -> None:
        self.full = {
            "clientes": [b.cliente("C1")],
            "ordenes": [b.orden("SO1")],
            "lineas": [b.linea("L1")],
            "pagos": [b.pago("PG1")],
        }
        self.delta = {
            "clientes": [],
            "ordenes": [b.orden("SO1", monto_total="2000")],  # cambió
            "lineas": [],
            "pagos": [],
        }

    def _pick(self, key: str, since: datetime | None) -> list:
        return self.full[key] if since is None else self.delta[key]

    def changed_clientes(self, since: datetime | None) -> list:
        return self._pick("clientes", since)

    def changed_ordenes(self, since: datetime | None) -> list:
        return self._pick("ordenes", since)

    def changed_lineas(self, since: datetime | None) -> list:
        return self._pick("lineas", since)

    def changed_pagos(self, since: datetime | None) -> list:
        return self._pick("pagos", since)


def test_full_load_luego_delta() -> None:
    repo = InMemoryRepository()
    reader = FakeOdooReader()
    sync = IncrementalSync(repo, reader)

    r1 = sync.run(datetime(2026, 6, 27, 10, 0))
    assert (r1.clientes, r1.ordenes, r1.lineas, r1.pagos) == (1, 1, 1, 1)
    assert r1.desde is None
    assert repo.get_last_sync() == datetime(2026, 6, 27, 10, 0)
    assert repo.get_orden("SO1").monto_total == Decimal("1000")

    r2 = sync.run(datetime(2026, 6, 27, 11, 0))
    assert r2.desde == datetime(2026, 6, 27, 10, 0)
    assert r2.total == 1  # solo la orden cambiada
    # El upsert refrescó la orden con el nuevo monto.
    assert repo.get_orden("SO1").monto_total == Decimal("2000")


def test_sync_nunca_toca_vinculaciones_ni_serietasas() -> None:
    repo = InMemoryRepository()
    # Sembrar trabajo humano + auditoría que el sync NO debe tocar.
    vinc = b.vinculacion("V1", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV)
    repo.add_vinculacion(vinc)
    serie = SerieTasa(
        timestamp=datetime(2026, 6, 27, 9, 0),
        tasa_bcv=Decimal("36"),
        tasa_binance=Decimal("40"),
        fuente="seed",
    )
    repo.append_serie_tasa(serie)

    IncrementalSync(repo, FakeOdooReader()).run(datetime(2026, 6, 27, 10, 0))

    # Intactas.
    assert repo.all_vinculaciones() == [vinc]
    assert repo.all_serie_tasas() == [serie]
