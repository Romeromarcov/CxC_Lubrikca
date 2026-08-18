"""Espejo de LineasEntrega (Fase 5 del plan de consolidación de fuentes,

agosto 2026) -- líneas de producto de despachos (stock.move.line), usadas
por Auditoría (Check 5) para detectar si se despachó un producto distinto
o adicional al pedido. Sincronizada por el mismo sync incremental delta
que ya mirrorea Clientes/Órdenes/Líneas/Pagos/Facturas/Entregas/Catálogo/
LineasFactura.
"""

from __future__ import annotations

from datetime import datetime

from cxc.db.postgres_repository import _entrega_linea_to_row, _row_to_entrega_linea
from cxc.odoo.client import OdooReader, OdooXmlRpcReader, map_entrega_linea
from cxc.repositories import InMemoryRepository
from cxc.sync.incremental import IncrementalSync

from . import builders as b

# --- map_entrega_linea: mapeo Odoo -> dataclass ------------------------------


def test_map_entrega_linea_normal():
    rec = {
        "id": 7001,
        "picking_id": [501, "ALM/OUT/00123"],
        "product_id": [1082, "Aceite Sinoco 20W50 (1x6)"],
    }
    ln = map_entrega_linea(rec)
    assert ln.linea_id == "7001"
    assert ln.entrega_id == "501"
    assert ln.producto_id == "1082"


def test_map_entrega_linea_picking_id_sin_lista_no_rompe():
    rec = {"id": 7002, "picking_id": 501, "product_id": [1082, "X"]}
    ln = map_entrega_linea(rec)
    assert ln.entrega_id == "501"


def test_map_entrega_linea_sin_product_id():
    rec = {"id": 7003, "picking_id": [501, "ALM/OUT/00123"], "product_id": False}
    ln = map_entrega_linea(rec)
    assert ln.producto_id == ""


# --- OdooXmlRpcReader.changed_entregas_lineas --------------------------------


def test_changed_entregas_lineas_filtra_picking_type_outgoing():
    captured_domain = {}

    def fake_execute(model, method, args, kwargs=None):
        if model == "stock.move.line":
            captured_domain["domain"] = args[0]
            return [
                {
                    "id": 1,
                    "picking_id": [501, "ALM/OUT/00123"],
                    "product_id": [1082, "Aceite"],
                }
            ]
        return []

    reader = OdooXmlRpcReader.__new__(OdooXmlRpcReader)
    reader._execute = fake_execute
    result = reader.changed_entregas_lineas(since=None)
    assert len(result) == 1
    assert result[0].linea_id == "1"
    outgoing_clauses = [
        c for c in captured_domain["domain"] if c[0] == "picking_id.picking_type_id.code"
    ]
    assert outgoing_clauses == [["picking_id.picking_type_id.code", "=", "outgoing"]]


# --- Repository: InMemoryRepository round-trip ------------------------------


def test_inmemory_repository_upsert_all_entregas_lineas_roundtrip():
    repo = InMemoryRepository()
    ln1 = b.entrega_linea("EL1", entrega_id="501", producto_id="P1")
    ln2 = b.entrega_linea("EL2", entrega_id="501", producto_id="P2")
    repo.upsert_entregas_lineas([ln1, ln2])
    assert {ln.linea_id for ln in repo.all_entregas_lineas()} == {"EL1", "EL2"}

    ln1_actualizada = b.entrega_linea("EL1", entrega_id="501", producto_id="P9")
    repo.upsert_entregas_lineas([ln1_actualizada])
    assert len(repo.all_entregas_lineas()) == 2
    actualizada = next(ln for ln in repo.all_entregas_lineas() if ln.linea_id == "EL1")
    assert actualizada.producto_id == "P9"


def test_base_repository_sin_backend_avisa_no_implementado():
    import pytest

    from cxc.repositories import Repository

    with pytest.raises(NotImplementedError):
        Repository.upsert_entregas_lineas(None, [b.entrega_linea()])
    with pytest.raises(NotImplementedError):
        Repository.all_entregas_lineas(None)


# --- PostgresRepository: mapeo fila <-> dataclass (sin DB real) -------------


class _FakeRow:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_postgres_row_mapping_roundtrip():
    ln = b.entrega_linea("EL9", entrega_id="501", producto_id="P9")
    row_dict = _entrega_linea_to_row(ln)
    assert row_dict["linea_id"] == "EL9"
    assert row_dict["producto_id"] == "P9"

    row = _FakeRow(**row_dict)
    ln_reconstruida = _row_to_entrega_linea(row)
    assert ln_reconstruida == ln


# --- IncrementalSync: wiring end-to-end -------------------------------------


class _FakeOdooReaderConEntregasLineas(OdooReader):
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

    def changed_entregas_lineas(self, since):
        return self._lineas_full if since is None else self._lineas_delta


def test_incremental_sync_sincroniza_entregas_lineas_en_backend_que_las_soporta():
    repo = InMemoryRepository()
    reader = _FakeOdooReaderConEntregasLineas(lineas_full=[b.entrega_linea("EL1")])
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 27, 10, 0))
    assert result.entregas_lineas == 1
    assert repo.all_entregas_lineas()[0].linea_id == "EL1"


def test_incremental_sync_no_rompe_si_backend_no_soporta_entregas_lineas():
    class RepoSinEntregasLineas(InMemoryRepository):
        def upsert_entregas_lineas(self, filas):
            raise NotImplementedError("backend sin soporte")

    repo = RepoSinEntregasLineas()
    reader = _FakeOdooReaderConEntregasLineas(lineas_full=[b.entrega_linea("EL1")])
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 27, 10, 0))
    assert result.entregas_lineas == 0
    assert repo.all_entregas_lineas() == []


def test_incremental_sync_sin_lineas_nuevas_no_llama_upsert():
    repo = InMemoryRepository()
    reader = _FakeOdooReaderConEntregasLineas(lineas_full=[])
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 27, 10, 0))
    assert result.entregas_lineas == 0
    assert repo.all_entregas_lineas() == []
