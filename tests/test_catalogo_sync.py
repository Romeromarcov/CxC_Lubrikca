"""Espejo de Catálogo (Fase 0 del plan de consolidación de fuentes, agosto

2026) -- referencia de product.product (volumen/peso/marca), usada hoy
para litros y otras columnas que varias páginas piden en vivo por separado
(Ventas, Reporte Diario). Última pieza de la Fase 0: junto a Facturas y
Entregas cierra el espejo de contenido inmutable/baja-frecuencia que
alimentará las fases siguientes (Ventas/Cobranza consolidadas).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from cxc.db.postgres_repository import _catalogo_to_row, _row_to_catalogo
from cxc.odoo.client import OdooReader, OdooXmlRpcReader, map_producto_espejo
from cxc.repositories import InMemoryRepository
from cxc.sync.incremental import IncrementalSync

from . import builders as b

# --- map_producto_espejo: mapeo Odoo -> dataclass ---------------------------


def test_map_producto_normal_con_marca():
    rec = {
        "id": 801,
        "default_code": "SKU-800",
        "name": "Aceite Sinoco 20W50 (1x6)",
        "product_volume": 6.0,
        "weight": 5.4,
        "brand_id": [3, "Sinoco"],
    }
    p = map_producto_espejo(rec)
    assert p.producto_id == "801"
    assert p.codigo == "SKU-800"
    assert p.nombre == "Aceite Sinoco 20W50 (1x6)"
    assert p.marca == "Sinoco"
    assert p.volumen == Decimal("6.0")
    assert p.peso == Decimal("5.4")


def test_map_producto_sin_marca_ni_volumen_no_rompe():
    rec = {
        "id": 802,
        "default_code": False,
        "name": "Producto genérico",
        "product_volume": False,
        "weight": False,
        "brand_id": False,
    }
    p = map_producto_espejo(rec)
    assert p.marca == ""
    assert p.codigo == ""
    assert p.volumen == Decimal("0")
    assert p.peso == Decimal("0")


def test_map_producto_con_unidades_por_paleta():
    rec = {
        "id": 804,
        "default_code": "SKU-804",
        "name": "Producto con paleta",
        "product_volume": 1.0,
        "weight": 1.0,
        "brand_id": False,
    }
    p = map_producto_espejo(rec, Decimal("150"))
    assert p.unidades_por_paleta == Decimal("150")


def test_map_producto_sin_unidades_por_paleta_default_cero():
    rec = {
        "id": 805,
        "default_code": "SKU-805",
        "name": "Sin paleta",
        "product_volume": 1.0,
        "weight": 1.0,
        "brand_id": False,
    }
    p = map_producto_espejo(rec)
    assert p.unidades_por_paleta == Decimal("0")


def test_map_producto_usa_product_volume_no_volume_generico():
    """Verificado en vivo (agosto 2026, producto real 1034): volume=6.0

    vs product_volume=5.67 -- campos genuinamente distintos en Odoo
    (volume es logística/empaque, product_volume es el litraje real que
    usa _get_ventas_sync/el motor de descuentos). Un rec con ambos
    presentes debe leer product_volume, ignorando volume."""
    rec = {
        "id": 803,
        "default_code": "SKU-803",
        "name": "Producto con ambos campos",
        "volume": 6.0,
        "product_volume": 5.67,
        "weight": 1.0,
        "brand_id": False,
    }
    p = map_producto_espejo(rec)
    assert p.volumen == Decimal("5.67")


# --- OdooXmlRpcReader.changed_catalogo --------------------------------------


def test_changed_catalogo_lee_campos_esperados():
    def fake_execute(model, method, args, kwargs=None):
        if model == "product.product":
            return [
                {
                    "id": 1,
                    "default_code": "SKU-1",
                    "name": "P1",
                    "product_volume": 1.0,
                    "weight": 1.0,
                    "brand_id": [1, "Global Oil"],
                }
            ]
        return []

    reader = OdooXmlRpcReader.__new__(OdooXmlRpcReader)
    reader._execute = fake_execute
    result = reader.changed_catalogo(since=None)
    assert len(result) == 1
    assert result[0].producto_id == "1"
    assert result[0].marca == "Global Oil"
    assert result[0].volumen == Decimal("1.0")


def test_changed_catalogo_incluye_productos_archivados():
    """Verificado en vivo (agosto 2026): search_read excluye productos

    archivados (active=False) por defecto, a diferencia del `read` por id
    explícito que usa la consulta en vivo de litros -- sin el filtro
    ["active", "in", [True, False]] en el dominio, un producto
    descontinuado referenciado en una orden histórica desaparece
    silenciosamente del espejo."""
    captured_domain = {}

    def fake_execute(model, method, args, kwargs=None):
        if model == "product.product":
            captured_domain["domain"] = args[0]
            return []
        return []

    reader = OdooXmlRpcReader.__new__(OdooXmlRpcReader)
    reader._execute = fake_execute
    reader.changed_catalogo(since=None)
    active_clauses = [c for c in captured_domain["domain"] if c[0] == "active"]
    assert active_clauses == [["active", "in", [True, False]]]


def test_changed_catalogo_filtra_sale_ok():
    """Odoo distingue productos de VENTA de los de compra/gasto con

    sale_ok (pedido explícito del usuario, agosto 2026) -- sin este
    filtro, el catálogo mezclaba productos que Lubrikca nunca vende."""
    captured_domain = {}

    def fake_execute(model, method, args, kwargs=None):
        if model == "product.product":
            captured_domain["domain"] = args[0]
            return []
        return []

    reader = OdooXmlRpcReader.__new__(OdooXmlRpcReader)
    reader._execute = fake_execute
    reader.changed_catalogo(since=None)
    sale_ok_clauses = [c for c in captured_domain["domain"] if c[0] == "sale_ok"]
    assert sale_ok_clauses == [["sale_ok", "=", True]]


def test_changed_catalogo_incluye_unidades_por_paleta_desde_packaging():
    """"Und. x Paleta" no vive en product.product -- es product.packaging

    (name="Paleta"), confirmado en vivo contra Odoo real (producto 1085,
    qty=150.0, coincide exacto con la ficha de referencia del usuario)."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "product.product":
            return [
                {
                    "id": 1,
                    "default_code": "SKU-1",
                    "name": "P1",
                    "product_volume": 1.0,
                    "weight": 1.0,
                    "brand_id": False,
                }
            ]
        if model == "product.packaging":
            domain = args[0]
            product_ids = next(c[2] for c in domain if c[0] == "product_id")
            assert 1 in product_ids
            return [{"product_id": [1, "P1"], "qty": 150.0}]
        return []

    reader = OdooXmlRpcReader.__new__(OdooXmlRpcReader)
    reader._execute = fake_execute
    result = reader.changed_catalogo(since=None)
    assert len(result) == 1
    assert result[0].unidades_por_paleta == Decimal("150.0")


def test_changed_catalogo_sin_regla_de_packaging_deja_cero():
    def fake_execute(model, method, args, kwargs=None):
        if model == "product.product":
            return [
                {
                    "id": 2,
                    "default_code": "SKU-2",
                    "name": "P2",
                    "product_volume": 1.0,
                    "weight": 1.0,
                    "brand_id": False,
                }
            ]
        return []

    reader = OdooXmlRpcReader.__new__(OdooXmlRpcReader)
    reader._execute = fake_execute
    result = reader.changed_catalogo(since=None)
    assert result[0].unidades_por_paleta == Decimal("0")


def test_changed_catalogo_falla_de_packaging_no_rompe_el_catalogo():
    """Si la consulta de product.packaging falla (permisos, modelo

    inexistente en alguna instalación de Odoo), el catálogo sigue
    sincronizando -- solo se pierde el dato de unidades por paleta."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "product.product":
            return [
                {
                    "id": 3,
                    "default_code": "SKU-3",
                    "name": "P3",
                    "product_volume": 1.0,
                    "weight": 1.0,
                    "brand_id": False,
                }
            ]
        raise ConnectionError("modelo no disponible")

    reader = OdooXmlRpcReader.__new__(OdooXmlRpcReader)
    reader._execute = fake_execute
    result = reader.changed_catalogo(since=None)
    assert len(result) == 1
    assert result[0].unidades_por_paleta == Decimal("0")


# --- Repository: InMemoryRepository round-trip ------------------------------


def test_inmemory_repository_upsert_all_catalogo_roundtrip():
    repo = InMemoryRepository()
    p1 = b.producto("P1", codigo="SKU-1")
    p2 = b.producto("P2", codigo="SKU-2")
    repo.upsert_catalogo([p1, p2])
    assert {p.producto_id for p in repo.all_catalogo()} == {"P1", "P2"}

    p1_actualizado = b.producto("P1", codigo="SKU-1", volumen="2.5")
    repo.upsert_catalogo([p1_actualizado])
    assert len(repo.all_catalogo()) == 2
    actualizado = next(p for p in repo.all_catalogo() if p.producto_id == "P1")
    assert actualizado.volumen == Decimal("2.5")


# --- PostgresRepository: mapeo fila <-> dataclass (sin DB real) -------------


class _FakeRow:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_postgres_row_mapping_roundtrip():
    p = b.producto("P9", codigo="SKU-9", nombre="Nueve", marca="Sinoco", volumen="3.3", peso="4.4")
    row_dict = _catalogo_to_row(p)
    assert row_dict["producto_id"] == "P9"
    assert row_dict["volumen"] == Decimal("3.3")

    row = _FakeRow(**row_dict)
    p_reconstruido = _row_to_catalogo(row)
    assert p_reconstruido == p


# --- IncrementalSync: wiring end-to-end -------------------------------------


class _FakeOdooReaderConCatalogo(OdooReader):
    def __init__(self, catalogo_full=None, catalogo_delta=None):
        self._catalogo_full = catalogo_full or []
        self._catalogo_delta = catalogo_delta if catalogo_delta is not None else []

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

    def changed_catalogo(self, since):
        return self._catalogo_full if since is None else self._catalogo_delta


def test_incremental_sync_sincroniza_catalogo_en_backend_que_lo_soporta():
    repo = InMemoryRepository()
    reader = _FakeOdooReaderConCatalogo(catalogo_full=[b.producto("P1")])
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 27, 10, 0))
    assert result.catalogo == 1
    assert repo.all_catalogo()[0].producto_id == "P1"


def test_incremental_sync_no_rompe_si_backend_no_soporta_catalogo():
    class RepoSinCatalogo(InMemoryRepository):
        def upsert_catalogo(self, filas):
            raise NotImplementedError("backend sin soporte")

    repo = RepoSinCatalogo()
    reader = _FakeOdooReaderConCatalogo(catalogo_full=[b.producto("P1")])
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 27, 10, 0))
    assert result.catalogo == 0
    assert repo.all_catalogo() == []


def test_incremental_sync_sync_catalogo_false_no_consulta_ni_sincroniza():
    """Fase B (agosto 2026): el catálogo se saca del ciclo de 5 min del

    daemon -- sync_catalogo=False debe evitar la consulta por completo
    (no solo descartar el resultado), para no gastar una llamada a Odoo
    en cada corrida."""

    class _ReaderQueRevientaSiPidenCatalogo(_FakeOdooReaderConCatalogo):
        def changed_catalogo(self, since):
            raise AssertionError("no debía llamarse con sync_catalogo=False")

    repo = InMemoryRepository()
    reader = _ReaderQueRevientaSiPidenCatalogo(catalogo_full=[b.producto("P1")])
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 27, 10, 0), sync_catalogo=False)
    assert result.catalogo == 0
    assert repo.all_catalogo() == []


def test_incremental_sync_sync_catalogo_true_por_defecto():
    repo = InMemoryRepository()
    reader = _FakeOdooReaderConCatalogo(catalogo_full=[b.producto("P1")])
    result = IncrementalSync(repo, reader).run(datetime(2026, 6, 27, 10, 0))
    assert result.catalogo == 1


def test_incremental_sync_total_incluye_las_3_tablas_nuevas_de_fase_0():
    """Fase 0 completa: Facturas + Entregas + Catálogo, las 3 cuentan hacia

    SyncResult.total sin pisarse entre sí."""
    from . import builders as b2

    class _FakeReaderTodo(OdooReader):
        def changed_clientes(self, since):
            return []

        def changed_ordenes(self, since):
            return []

        def changed_lineas(self, since):
            return []

        def changed_pagos(self, since):
            return []

        def lineas_vigentes_por_orden(self, so_ids):
            return {}

        def changed_facturas(self, since):
            return [b2.factura("F1")]

        def changed_entregas(self, since):
            return [b2.entrega("E1")]

        def changed_catalogo(self, since):
            return [b2.producto("P1")]

    repo = InMemoryRepository()
    result = IncrementalSync(repo, _FakeReaderTodo()).run(datetime(2026, 6, 27, 10, 0))
    assert (result.facturas, result.entregas, result.catalogo) == (1, 1, 1)
    assert result.total == 3
