"""Tests de la capa de conciliación (sección 7)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from cxc.config import ReconciliationConfig
from cxc.models import BandejaFacturacion, ResultadoConciliacion
from cxc.reconciliation.reconcile import (
    NetoFacturado,
    OdooFacturasReader,
    Reconciler,
    clasificar_diferencia,
)
from cxc.repositories import InMemoryRepository

CFG = ReconciliationConfig(
    tolerance_rounding=Decimal("0.01"), tolerance_red=Decimal("1.00")
)


def test_verde_cuando_cuadra() -> None:
    c = clasificar_diferencia(Decimal("94.00"), Decimal("94.00"), Decimal("0"), CFG)
    assert c.resultado == ResultadoConciliacion.VERDE
    assert c.diferencia == Decimal("0.00")


def test_amarillo_por_redondeo() -> None:
    c = clasificar_diferencia(Decimal("94.00"), Decimal("94.50"), Decimal("0"), CFG)
    assert c.resultado == ResultadoConciliacion.AMARILLO


def test_rojo_por_desviacion() -> None:
    # El motor calculó 94 pero Odoo facturó 120 → desviación de 26 > tolerancia.
    c = clasificar_diferencia(Decimal("94.00"), Decimal("120.00"), Decimal("0"), CFG)
    assert c.resultado == ResultadoConciliacion.ROJO
    assert c.diferencia == Decimal("-26.00")


def test_considera_notas_credito_de_odoo() -> None:
    # Odoo facturó 100 pero con NC de 6 → neto 94 = motor → verde.
    c = clasificar_diferencia(Decimal("94.00"), Decimal("100.00"), Decimal("6.00"), CFG)
    assert c.resultado == ResultadoConciliacion.VERDE


class _FakeFacturas:
    def __init__(self, datos: dict[str, NetoFacturado]) -> None:
        self._datos = datos

    def neto_facturado(self, so_id: str) -> NetoFacturado:
        return self._datos[so_id]


def test_reconciler_recorre_bandeja_y_persiste() -> None:
    repo = InMemoryRepository()
    repo.upsert_bandeja(
        BandejaFacturacion(
            so_id="SO1", lista_aplicada="USD",
            precio_base_calculado=Decimal("100"), total_motor=Decimal("94.00"),
        )
    )
    repo.upsert_bandeja(
        BandejaFacturacion(
            so_id="SO2", lista_aplicada="USD",
            precio_base_calculado=Decimal("200"), total_motor=Decimal("180.00"),
        )
    )
    facturas = _FakeFacturas(
        {
            "SO1": NetoFacturado(Decimal("94.00"), Decimal("0"), True),
            "SO2": NetoFacturado(Decimal("200.00"), Decimal("0"), True),  # rojo
        }
    )
    resultados = Reconciler(repo, facturas, CFG).run()
    por_so = {c.so_id: c.resultado for c in resultados}
    assert por_so["SO1"] == ResultadoConciliacion.VERDE
    assert por_so["SO2"] == ResultadoConciliacion.ROJO
    assert len(repo.all_conciliaciones()) == 2


def test_odoo_facturas_reader_suma_facturas_y_ncs_en_usd() -> None:
    # La compañía factura en VES; se usa el equivalente USD de la factura.
    def fake_execute(model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        return [
            {"id": 1, "invoice_origin": "S00553",
             "amount_total_signed_usd": "1650.42", "move_type": "out_invoice"},
            {"id": 2, "invoice_origin": "S00553",
             "amount_total_signed_usd": "-50.00", "move_type": "out_refund"},
        ]

    reader = OdooFacturasReader(fake_execute)
    neto = reader.neto_facturado("S00553")
    assert neto.monto_facturado == Decimal("1650.42")
    assert neto.ncs == Decimal("50.00")
    assert neto.facturada is True


def test_odoo_facturas_reader_sin_factura() -> None:
    reader = OdooFacturasReader(lambda *a, **k: [])
    neto = reader.neto_facturado("SOX")
    assert neto.facturada is False
    assert neto.monto_facturado == Decimal("0")
