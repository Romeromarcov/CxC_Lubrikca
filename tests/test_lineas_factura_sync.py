"""Espejo de LineasFactura (Fase 4/5 del plan de consolidación de fuentes,

agosto 2026) -- mitad "factura" del espejo de descuentos de línea
(account.move.line), sincronizada por el mismo sync incremental delta que
ya mirrorea Clientes/Órdenes/Líneas/Pagos/Facturas/Entregas/Catálogo.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from cxc.db.postgres_repository import _linea_factura_to_row, _row_to_linea_factura
from cxc.odoo.client import OdooReader, OdooXmlRpcReader, map_linea_factura
from cxc.repositories import InMemoryRepository
from cxc.sync.incremental import IncrementalSync

from . import builders as b

# --- map_linea_factura: mapeo Odoo -> dataclass ------------------------------


def test_map_linea_factura_normal():
    rec = {
        "id": 5001,
        "move_id": [900, "FAC/900"],
        "name": "Aceite Sinoco 20W50 (1x6)",
        "quantity": 2.0,
        "price_unit": 100.0,
        "discount": 5.0,
        "price_subtotal": 190.0,
    }
    ln = map_linea_factura(rec)
    assert ln.linea_id == "5001"
    assert ln.factura_id == "900"
    assert ln.nombre == "Aceite Sinoco 20W50 (1x6)"
    assert ln.cantidad == Decimal("2.0")
    assert ln.precio_unitario == Decimal("100.0")
    assert ln.descuento == Decimal("5.0")
    assert ln.subtotal == Decimal("190.0")


def test_map_linea_factura_linea_descuento_price_subtotal_negativo():
    rec = {
        "id": 5002,
        "move_id": [900, "FAC/900"],
        "name": "Descuento",
        "quantity": 1.0,
        "price_unit": -20.0,
        "discount": 0.0,
        "price_subtotal": -20.0,
    }
    ln = map_linea_factura(rec)
    assert ln.subtotal == Decimal("-20.0")
    assert ln.descuento == Decimal("0")


def test_map_linea_factura_move_id_sin_lista_no_rompe():
    rec = {"id": 5003, "move_id": 900, "name": "X", "quantity": 1.0, "price_unit": 1.0}
    ln = map_linea_factura(rec)
    assert ln.factura_id == "900"


# --- OdooXmlRpcReader.changed_lineas_factura ---------------------------------


def test_changed_lineas_factura_filtra_display_type():
    captured_domain = {}

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.move.line":
            captured_domain["domain"] = args[0]
            return [
                {
                    "id": 1,
                    "move_id": [900, "FAC/900"],
                    "name": "P1",
                    "quantity": 1.0,
                    "price_unit": 100.0,
                    "discount": 0.0,
                    "price_subtotal": 100.0,
                }
            ]
        return []

    reader = OdooXmlRpcReader.__new__(OdooXmlRpcReader)
    reader._execute = fake_execute
    result = reader.changed_lineas_factura(since=None)
    assert len(result) == 1
    assert result[0].linea_id == "1"
    display_type_clauses = [c for c in captured_domain["domain"] if c[0] == "display_type"]
    assert display_type_clauses == [["display_type", "in", ["product", False]]]


# --- Repository: InMemoryRepository round-trip ------------------------------


def test_inmemory_repository_upsert_all_lineas_factura_roundtrip():
    repo = InMemoryRepository()
    ln1 = b.linea_factura("LF1", nombre="P1")
    ln2 = b.linea_factura("LF2", nombre="P2")
    repo.upsert_lineas_factura([ln1, ln2])
    assert {ln.linea_id for ln in repo.all_lineas_factura()} == {"LF1", "LF2"}

    ln1_actualizada = b.linea_factura("LF1", nombre="P1-CORREGIDO")
    repo.upsert_lineas_factura([ln1_actualizada])
    assert len(repo.all_lineas_factura()) == 2
    actualizada = next(ln for ln in repo.all_lineas_factura() if ln.linea_id == "LF1")
    assert actualizada.nombre == "P1-CORREGIDO"


def test_base_repository_sin_backend_avisa_no_implementado():
    import pytest

    from cxc.repositories import Repository

    with pytest.raises(NotImplementedError):
        Repository.upsert_lineas_factura(None, [b.linea_factura()])
    with pytest.raises(NotImplementedError):
        Repository.all_lineas_factura(None)


# --- PostgresRepository: mapeo fila <-> dataclass (sin DB real) -------------


class _FakeRow:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_postgres_row_mapping_roundtrip():
    ln = b.linea_factura(
        "LF9",
        factura_id="900",
        nombre="Nueve",
        cantidad="3",
        precio_unitario="50",
        descuento="10",
        subtotal="135",
    )
    row_dict = _linea_factura_to_row(ln)
    assert row_dict["linea_id"] == "LF9"
    assert row_dict["subtotal"] == Decimal("135")

    row = _FakeRow(**row_dict)
    ln_reconstruida = _row_to_linea_factura(row)
    assert ln_reconstruida == ln


# --- IncrementalSync: wiring end-to-end -------------------------------------


class _FakeOdooReaderConLineasFactura(OdooReader):
    def __init__(self, lineas_full=None, lineas_delta=None):
        self._lineas_full = lineas_full or []
        self._lineas_delta = lineas_delta if lineas_delta is not None else []

    def changed_clientes(self, since):
        return []

    def changed_ordenes(self, since):
        return []

    def changed_lineas(self, since):
        return []

    def changed_pagos(self, since):
        return []

    def lineas_vigentes_por_orden(self, so_ids):
        return {so_id: set() for so_id in so_ids}

    def changed_lineas_factura(self, since):
        return self._lineas_full if since is None else self._lineas_delta


def test_incremental_sync_sincroniza_lineas_factura_en_backend_que_las_soporta():
    repo = InMemoryRepository()
    reader = _FakeOdooReaderConLineasFactura(lineas_full=[b.linea_factura("LF1")])
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 27, 10, 0))
    assert result.lineas_factura == 1
    assert repo.all_lineas_factura()[0].linea_id == "LF1"


def test_incremental_sync_no_rompe_si_backend_no_soporta_lineas_factura():
    class RepoSinLineasFactura(InMemoryRepository):
        def upsert_lineas_factura(self, filas):
            raise NotImplementedError("backend sin soporte")

    repo = RepoSinLineasFactura()
    reader = _FakeOdooReaderConLineasFactura(lineas_full=[b.linea_factura("LF1")])
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 27, 10, 0))
    assert result.lineas_factura == 0
    assert repo.all_lineas_factura() == []


def test_incremental_sync_sin_lineas_nuevas_no_llama_upsert():
    repo = InMemoryRepository()
    reader = _FakeOdooReaderConLineasFactura(lineas_full=[])
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 27, 10, 0))
    assert result.lineas_factura == 0
    assert repo.all_lineas_factura() == []
