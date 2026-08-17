"""Espejo de Facturas (Fase 0 del plan de consolidación de fuentes, agosto

2026) -- contenido INMUTABLE de account.move (facturas/NC/ND), sincronizado
por el mismo sync incremental delta que ya mirrorea Clientes/Órdenes/
Líneas/Pagos. amount_residual NUNCA se lee aquí a propósito -- eso sigue
siendo dominio de Cobranza, consultado en vivo (ver docstring de
cxc.models.Factura).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cxc.db.postgres_repository import _factura_to_row, _row_to_factura
from cxc.odoo.client import OdooReader, OdooXmlRpcReader, map_factura_espejo
from cxc.repositories import InMemoryRepository
from cxc.sync.incremental import IncrementalSync

from . import builders as b

# --- map_factura_espejo: mapeo Odoo -> dataclass -----------------------------------


def test_map_factura_espejo_normal():
    rec = {
        "id": 501,
        "name": "INV/2026/0042",
        "invoice_origin": "SO0042",
        "move_type": "out_invoice",
        "invoice_date": "2026-06-10",
        "date": "2026-06-10",
        "currency_id": [1, "USD"],
        "amount_total": 1160.0,
        "amount_untaxed": 1000.0,
        "state": "posted",
        "reversed_entry_id": False,
        "debit_origin_id": False,
        "amount_total_signed_usd": 1160.0,
        "amount_untaxed_signed_usd": 1000.0,
    }
    f = map_factura_espejo(rec)
    assert f.factura_id == "501"
    assert f.numero == "INV/2026/0042"
    assert f.so_id == "SO0042"
    assert f.move_type == "out_invoice"
    assert f.es_nota_debito is False
    assert f.fecha == date(2026, 6, 10)
    assert f.moneda == "USD"
    assert f.monto_total == Decimal("1160.0")
    assert f.monto_sin_impuestos == Decimal("1000.0")
    assert f.estado == "posted"
    assert f.factura_origen_id is None
    assert f.monto_total_signed_usd == Decimal("1160.0")
    assert f.monto_sin_impuestos_signed_usd == Decimal("1000.0")


def test_map_factura_espejo_nota_credito_referencia_factura_origen():
    rec = {
        "id": 502,
        "name": "RINV/2026/0007",
        "invoice_origin": "SO0042",
        "move_type": "out_refund",
        "invoice_date": "2026-06-15",
        "date": "2026-06-15",
        "currency_id": [1, "USD"],
        "amount_total": 100.0,
        "amount_untaxed": 86.21,
        "state": "posted",
        "reversed_entry_id": [501, "INV/2026/0042"],
        "debit_origin_id": False,
    }
    f = map_factura_espejo(rec)
    assert f.move_type == "out_refund"
    assert f.es_nota_debito is False
    assert f.factura_origen_id == "501"


def test_map_factura_espejo_nota_debito_es_move_type_out_debito():
    """Verificado en vivo (agosto 2026, orden S00357 y otras del mismo

    cliente): las notas de débito reales en Odoo 18 traen
    move_type == "out_debit" (journal dedicado), NUNCA "out_invoice" --
    debit_origin_id sigue apuntando a la factura original (ver
    _leer_notas_debito_odoo en cxc.web.app para el bug real que motivó
    esta corrección)."""
    rec = {
        "id": 503,
        "name": "INV/2026/0055",
        "invoice_origin": "SO0042",
        "move_type": "out_debit",
        "invoice_date": "2026-06-20",
        "date": "2026-06-20",
        "currency_id": [1, "USD"],
        "amount_total": 50.0,
        "amount_untaxed": 43.1,
        "state": "posted",
        "reversed_entry_id": False,
        "debit_origin_id": [501, "INV/2026/0042"],
    }
    f = map_factura_espejo(rec)
    assert f.move_type == "out_debit"
    assert f.es_nota_debito is True
    assert f.factura_origen_id == "501"


def test_map_factura_espejo_out_invoice_con_debit_origin_no_es_nota_debito():
    """Guardia de regresión: antes de la corrección, este caso (out_invoice

    con debit_origin_id) se clasificaba erróneamente como nota de débito.
    Verificado en vivo que las ND reales usan move_type="out_debit", así
    que un out_invoice normal nunca debe marcarse como ND aunque traiga
    debit_origin_id poblado por alguna otra razón."""
    rec = {
        "id": 505,
        "name": "INV/2026/0060",
        "invoice_origin": "SO0042",
        "move_type": "out_invoice",
        "invoice_date": "2026-06-20",
        "date": "2026-06-20",
        "currency_id": [1, "USD"],
        "amount_total": 50.0,
        "amount_untaxed": 43.1,
        "state": "posted",
        "reversed_entry_id": False,
        "debit_origin_id": [501, "INV/2026/0042"],
    }
    f = map_factura_espejo(rec)
    assert f.es_nota_debito is False


def test_map_factura_espejo_sin_fecha_cae_a_hoy_no_rompe():
    rec = {
        "id": 504,
        "name": "INV/2026/DRAFT",
        "invoice_origin": False,
        "move_type": "out_invoice",
        "invoice_date": False,
        "date": False,
        "currency_id": False,
        "amount_total": 0.0,
        "amount_untaxed": 0.0,
        "state": "draft",
        "reversed_entry_id": False,
        "debit_origin_id": False,
    }
    f = map_factura_espejo(rec)
    assert f.fecha == date.today()
    assert f.so_id is None
    assert f.moneda == "USD"  # default conservador sin currency_id


# --- OdooXmlRpcReader.changed_facturas --------------------------------------


def test_changed_facturas_filtra_por_move_type_salida():
    captured_domain = {}

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.move":
            captured_domain["domain"] = args[0]
            return [
                {
                    "id": 1,
                    "name": "INV/1",
                    "invoice_origin": "SO1",
                    "move_type": "out_invoice",
                    "invoice_date": "2026-06-01",
                    "date": "2026-06-01",
                    "currency_id": [1, "USD"],
                    "amount_total": 100.0,
                    "amount_untaxed": 86.21,
                    "state": "posted",
                    "reversed_entry_id": False,
                    "debit_origin_id": False,
                    "amount_total_signed_usd": 100.0,
                    "amount_untaxed_signed_usd": 86.21,
                }
            ]
        return []

    reader = OdooXmlRpcReader.__new__(OdooXmlRpcReader)
    reader._execute = fake_execute
    result = reader.changed_facturas(since=None)
    assert len(result) == 1
    assert result[0].factura_id == "1"
    # El dominio debe filtrar move_type in [out_invoice, out_refund,
    # out_debit] -- nunca facturas de proveedor (in_invoice/in_refund).
    # out_debit incluido porque las ND reales de Odoo 18 usan ese
    # move_type, no "out_invoice" (ver map_factura_espejo).
    move_type_clauses = [c for c in captured_domain["domain"] if c[0] == "move_type"]
    assert move_type_clauses == [
        ["move_type", "in", ["out_invoice", "out_refund", "out_debit"]]
    ]


# --- Repository: InMemoryRepository round-trip ------------------------------


def test_inmemory_repository_upsert_all_facturas_roundtrip():
    repo = InMemoryRepository()
    f1 = b.factura("F1", numero="INV/1")
    f2 = b.factura("F2", numero="INV/2")
    repo.upsert_facturas([f1, f2])
    assert {f.factura_id for f in repo.all_facturas()} == {"F1", "F2"}

    # Upsert de nuevo con el mismo id actualiza, no duplica.
    f1_actualizada = b.factura("F1", numero="INV/1-CORREGIDA")
    repo.upsert_facturas([f1_actualizada])
    assert len(repo.all_facturas()) == 2
    actualizada = next(f for f in repo.all_facturas() if f.factura_id == "F1")
    assert actualizada.numero == "INV/1-CORREGIDA"


def test_base_repository_sin_backend_avisa_no_implementado():
    """Backends que no son PostgresRepository (ej. Sheets, en retiro) deben

    fallar explícito en vez de fingir que guardaron algo -- ver docstring
    en Repository.upsert_facturas. Repository es ABC (no instanciable
    directo); se llama el método "unbound" del default concreto de la
    clase base directamente, pasando cualquier self."""
    import pytest

    from cxc.repositories import Repository

    with pytest.raises(NotImplementedError):
        Repository.upsert_facturas(None, [b.factura()])
    with pytest.raises(NotImplementedError):
        Repository.all_facturas(None)


# --- PostgresRepository: mapeo fila <-> dataclass (sin DB real) -------------


class _FakeRow:
    """Simula una Row de SQLAlchemy (acceso por atributo)."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_postgres_row_mapping_roundtrip():
    f = b.factura(
        "F9",
        numero="INV/9",
        so_id="SO9",
        move_type="out_refund",
        fecha=date(2026, 7, 1),
        moneda="VES",
        monto_total="500.5",
        monto_sin_impuestos="431.5",
        estado="posted",
        factura_origen_id="F1",
    )
    row_dict = _factura_to_row(f)
    assert row_dict["factura_id"] == "F9"
    assert row_dict["monto_total"] == Decimal("500.5")

    row = _FakeRow(**row_dict)
    f_reconstruida = _row_to_factura(row)
    assert f_reconstruida == f


# --- IncrementalSync: wiring end-to-end -------------------------------------


class _FakeOdooReaderConFacturas(OdooReader):
    def __init__(self, facturas_full=None, facturas_delta=None):
        self._facturas_full = facturas_full or []
        self._facturas_delta = facturas_delta if facturas_delta is not None else []

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

    def changed_facturas(self, since):
        return self._facturas_full if since is None else self._facturas_delta


def test_incremental_sync_sincroniza_facturas_en_backend_que_las_soporta():
    repo = InMemoryRepository()
    reader = _FakeOdooReaderConFacturas(facturas_full=[b.factura("F1")])
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 27, 10, 0))
    assert result.facturas == 1
    assert repo.all_facturas()[0].factura_id == "F1"


def test_incremental_sync_no_rompe_si_backend_no_soporta_facturas():
    """Un backend que no implementa upsert_facturas (ej. Sheets) no debe

    tumbar el resto del sync -- solo se omite el espejo de facturas para
    esa corrida (ver try/except NotImplementedError en IncrementalSync.run).
    """

    class RepoSinFacturas(InMemoryRepository):
        def upsert_facturas(self, filas):
            raise NotImplementedError("backend sin soporte")

    repo = RepoSinFacturas()
    reader = _FakeOdooReaderConFacturas(facturas_full=[b.factura("F1")])
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 27, 10, 0))
    assert result.facturas == 0  # se omitió, sin excepción
    assert repo.all_facturas() == []  # nunca se guardó


def test_incremental_sync_sin_facturas_nuevas_no_llama_upsert():
    repo = InMemoryRepository()
    reader = _FakeOdooReaderConFacturas(facturas_full=[])
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 27, 10, 0))
    assert result.facturas == 0
    assert repo.all_facturas() == []
