"""Cuando Odoo aplica un pago a una orden distinta a la que asignó el FIFO.

Bug real (auditoría de agosto 2026, pedido explícito del usuario): el
reparto FIFO asigna un pago a la orden A; después, en Odoo, ese pago se
reconcilia contra la orden B. ``_resincronizar_vinculaciones_con_odoo``
re-apunta la Vinculación -- pero la orden A quedaba fuera del filtro de
``run_all`` (facturada y ya sin abono), así que su fila de bandeja
sobrevivía intacta con el descuento que ya no le correspondía. El
descuento terminaba contado DOS veces: en la orden vieja y en la nueva.

Importa especialmente desde que una Vinculación PENDIENTE destraba
descuentos: el FIFO adivina, y cuando Odoo lo corrige la adivinanza tiene
que desaparecer de verdad, no solo mudarse.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from decimal import Decimal

from cxc.config import EngineConfig
from cxc.engine.price_resolver import DictPriceResolver
from cxc.engine.runner import EngineRunner
from cxc.models import Moneda, TipoTasa
from cxc.repositories import InMemoryRepository

from . import builders as b

CFG = EngineConfig(cash_window_business_days=3, bcv_complete_formula="differential_over_binance")
PRECIOS = {("P1", "USD"): Decimal("100"), ("P1", "BCV"): Decimal("100")}


def _repo_con_dos_ordenes_facturadas() -> InMemoryRepository:
    repo = InMemoryRepository()
    repo.upsert_clientes([b.cliente("C1")])
    for so in ("SO_A", "SO_B"):
        repo.upsert_ordenes([b.orden(so, cliente_id="C1", primera=False, facturada=True)])
        repo.upsert_lineas(
            [b.linea(f"L_{so}", so_id=so, marca="Sinoco", categoria="*", precio="100")]
        )
    repo.add_metodo_pago(b.metodo("M1", moneda=Moneda.USD, es_contado=True))
    repo.upsert_pagos([b.pago("PG1", cliente_id="C1", monto="94", metodo_id="M1")])
    repo.add_vinculacion(
        b.vinculacion(
            "V1",
            pago_id="PG1",
            so_id="SO_A",
            monto_aplicado="94",
            moneda_abono=Moneda.USD,
            tipo_tasa_abono=TipoTasa.N_A,
            hora=datetime(2026, 6, 5, 10, 0),
        )
    )
    repo.add_descuento(b.descuento("D1", marca="Sinoco", categoria="*", porcentaje="0.03"))
    return repo


def test_la_orden_que_pierde_el_pago_pierde_el_descuento() -> None:
    repo = _repo_con_dos_ordenes_facturadas()
    runner = EngineRunner(repo, DictPriceResolver(PRECIOS), CFG)

    runner.run_all(date(2026, 6, 8))
    bandeja_a = repo.get_bandeja("SO_A")
    assert bandeja_a is not None
    assert bandeja_a.total_descuentos == Decimal("3.00")

    # Odoo reconcilió el pago contra SO_B: el resync re-apunta la
    # Vinculación (mismo efecto que _resincronizar_vinculaciones_con_odoo).
    vinc = repo.vinculaciones_de_orden("SO_A")[0]
    repo.add_vinculacion(dataclasses.replace(vinc, so_id="SO_B"))
    runner.run_all(date(2026, 6, 8))

    bandeja_a = repo.get_bandeja("SO_A")
    bandeja_b = repo.get_bandeja("SO_B")
    # SO_A ya no tiene abono -> su descuento tiene que desaparecer.
    assert bandeja_a is not None
    assert bandeja_a.total_descuentos == Decimal("0")
    assert not [d for d in bandeja_a.descuentos_detalle if d.origen == "contado"]
    # Y SO_B, que ahora sí lo tiene, lo gana.
    assert bandeja_b is not None
    assert bandeja_b.total_descuentos == Decimal("3.00")


def test_no_se_cuenta_el_mismo_descuento_en_dos_ordenes() -> None:
    """La forma en que el bug se manifestaba en plata: el total del sistema
    incluía el descuento dos veces."""
    repo = _repo_con_dos_ordenes_facturadas()
    runner = EngineRunner(repo, DictPriceResolver(PRECIOS), CFG)
    runner.run_all(date(2026, 6, 8))

    vinc = repo.vinculaciones_de_orden("SO_A")[0]
    repo.add_vinculacion(dataclasses.replace(vinc, so_id="SO_B"))
    runner.run_all(date(2026, 6, 8))

    total = sum(bd.total_descuentos for bd in repo.all_bandeja())
    assert total == Decimal("3.00"), f"el descuento se contó más de una vez: {total}"
