"""Espejo de Entregas (Fase 0 del plan de consolidación de fuentes, agosto

2026) -- contenido INMUTABLE de stock.picking (despachos y devoluciones),
sincronizado por su propio write_date (no depende de que la SO padre se
vuelva a tocar -- ver docstring de cxc.models.Entrega).
"""

from __future__ import annotations

from datetime import date, datetime

from cxc.db.postgres_repository import _entrega_to_row, _row_to_entrega
from cxc.odoo.client import OdooReader, OdooXmlRpcReader, map_entrega_espejo
from cxc.repositories import InMemoryRepository
from cxc.sync.incremental import IncrementalSync

from . import builders as b

# --- map_entrega_espejo: mapeo Odoo -> dataclass ----------------------------


def test_map_entrega_despacho_saliente_normal():
    rec = {
        "id": 701,
        "sale_id": [42, "SO0042"],
        "picking_type_code": "outgoing",
        "date_done": "2026-06-10",
        "state": "done",
        "return_id": False,
    }
    e = map_entrega_espejo(rec)
    assert e.entrega_id == "701"
    assert e.so_id == "SO0042"
    assert e.tipo == "outgoing"
    assert e.fecha == date(2026, 6, 10)
    assert e.estado == "done"
    assert e.es_devolucion is False
    assert e.entrega_origen_id is None


def test_map_entrega_devolucion_por_return_id():
    rec = {
        "id": 702,
        "sale_id": [42, "SO0042"],
        "picking_type_code": "incoming",
        "date_done": "2026-06-20",
        "state": "done",
        "return_id": [701, "Devolución de WH/OUT/00701"],
    }
    e = map_entrega_espejo(rec)
    assert e.es_devolucion is True
    assert e.entrega_origen_id == "701"
    assert e.tipo == "incoming"


def test_map_entrega_sin_fecha_ni_orden_no_rompe():
    rec = {
        "id": 703,
        "sale_id": False,
        "picking_type_code": "outgoing",
        "date_done": False,
        "state": "assigned",
        "return_id": False,
    }
    e = map_entrega_espejo(rec)
    assert e.fecha is None  # a diferencia de Factura.fecha, sí puede ser None
    assert e.so_id is None
    assert e.es_devolucion is False


# --- OdooXmlRpcReader.changed_entregas --------------------------------------


def test_changed_entregas_filtra_tipos_de_almacen_de_venta():
    captured_domain = {}

    def fake_execute(model, method, args, kwargs=None):
        if model == "stock.picking":
            captured_domain["domain"] = args[0]
            return [
                {
                    "id": 1,
                    "sale_id": [1, "SO1"],
                    "picking_type_code": "outgoing",
                    "date_done": "2026-06-01",
                    "state": "done",
                    "return_id": False,
                }
            ]
        return []

    reader = OdooXmlRpcReader.__new__(OdooXmlRpcReader)
    reader._execute = fake_execute
    result = reader.changed_entregas(since=None)
    assert len(result) == 1
    assert result[0].entrega_id == "1"
    # Nunca debe traer transferencias internas (internal) ni de manufactura.
    tipo_clauses = [c for c in captured_domain["domain"] if c[0] == "picking_type_code"]
    assert tipo_clauses == [["picking_type_code", "in", ["outgoing", "incoming"]]]


# --- Repository: InMemoryRepository round-trip ------------------------------


def test_inmemory_repository_upsert_all_entregas_roundtrip():
    repo = InMemoryRepository()
    e1 = b.entrega("E1", so_id="SO1")
    e2 = b.entrega("E2", so_id="SO2")
    repo.upsert_entregas([e1, e2])
    assert {e.entrega_id for e in repo.all_entregas()} == {"E1", "E2"}

    e1_actualizada = b.entrega("E1", so_id="SO1", estado="cancel")
    repo.upsert_entregas([e1_actualizada])
    assert len(repo.all_entregas()) == 2
    actualizada = next(e for e in repo.all_entregas() if e.entrega_id == "E1")
    assert actualizada.estado == "cancel"


# --- PostgresRepository: mapeo fila <-> dataclass (sin DB real) -------------


class _FakeRow:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_postgres_row_mapping_roundtrip():
    e = b.entrega(
        "E9",
        so_id="SO9",
        tipo="incoming",
        fecha=date(2026, 7, 1),
        estado="done",
        es_devolucion=True,
        entrega_origen_id="E1",
    )
    row_dict = _entrega_to_row(e)
    assert row_dict["entrega_id"] == "E9"
    assert row_dict["es_devolucion"] is True

    row = _FakeRow(**row_dict)
    e_reconstruida = _row_to_entrega(row)
    assert e_reconstruida == e


# --- IncrementalSync: wiring end-to-end -------------------------------------


class _FakeOdooReaderConEntregas(OdooReader):
    def __init__(self, entregas_full=None, entregas_delta=None):
        self._entregas_full = entregas_full or []
        self._entregas_delta = entregas_delta if entregas_delta is not None else []

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

    def changed_entregas(self, since):
        return self._entregas_full if since is None else self._entregas_delta


def test_incremental_sync_sincroniza_entregas_en_backend_que_las_soporta():
    repo = InMemoryRepository()
    reader = _FakeOdooReaderConEntregas(entregas_full=[b.entrega("E1")])
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 27, 10, 0))
    assert result.entregas == 1
    assert repo.all_entregas()[0].entrega_id == "E1"


def test_incremental_sync_no_rompe_si_backend_no_soporta_entregas():
    class RepoSinEntregas(InMemoryRepository):
        def upsert_entregas(self, filas):
            raise NotImplementedError("backend sin soporte")

    repo = RepoSinEntregas()
    reader = _FakeOdooReaderConEntregas(entregas_full=[b.entrega("E1")])
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 27, 10, 0))
    assert result.entregas == 0
    assert repo.all_entregas() == []


def test_incremental_sync_captura_devolucion_tardia_via_su_propio_write_date():
    """Caso central del diseño: una devolución procesada DÍAS después de la

    entrega original, sin que la SO padre vuelva a tocarse -- el picking de
    devolución sí aparece en el delta (por su propio write_date), aunque
    changed_ordenes no reporte ningún cambio en esa corrida."""
    repo = InMemoryRepository()
    repo.upsert_entregas([b.entrega("E1", so_id="SO1", estado="done")])
    repo.set_last_sync(datetime(2026, 6, 27, 10, 0))  # simula que ya hubo una corrida previa
    reader = _FakeOdooReaderConEntregas(
        entregas_delta=[
            b.entrega(
                "E2",
                so_id="SO1",
                tipo="incoming",
                estado="done",
                es_devolucion=True,
                entrega_origen_id="E1",
            )
        ]
    )
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 30, 10, 0))
    assert result.entregas == 1
    ids = {e.entrega_id for e in repo.all_entregas()}
    assert ids == {"E1", "E2"}  # la original persiste, la devolución se agrega
