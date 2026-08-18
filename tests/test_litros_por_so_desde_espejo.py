"""_litros_por_so_desde_espejo (Fase 2 del plan de consolidación de

fuentes, agosto 2026) -- réplica, leyendo del espejo Producto, del
cálculo de litros por SO que _get_ventas_sync arma hoy con una consulta
en vivo a product.template.product_volume. NO está conectada a ningún
endpoint todavía (ver docstring de la función).
"""

from __future__ import annotations

from cxc.repositories import InMemoryRepository
from cxc.web.app import _litros_por_so_desde_espejo

from . import builders as b


def test_suma_litros_de_una_linea():
    repo = InMemoryRepository()
    repo.upsert_catalogo([b.producto("P1", volumen="4.0")])
    lineas_por_so = {"SO1": [b.linea("L1", so_id="SO1", producto="P1", cantidad="3")]}
    result = _litros_por_so_desde_espejo(repo, lineas_por_so)
    assert result == {"SO1": 12.0}


def test_suma_litros_de_varias_lineas_de_la_misma_orden():
    repo = InMemoryRepository()
    repo.upsert_catalogo([b.producto("P1", volumen="4.0"), b.producto("P2", volumen="1.0")])
    lineas_por_so = {
        "SO1": [
            b.linea("L1", so_id="SO1", producto="P1", cantidad="2"),
            b.linea("L2", so_id="SO1", producto="P2", cantidad="5"),
        ]
    }
    result = _litros_por_so_desde_espejo(repo, lineas_por_so)
    assert result == {"SO1": 13.0}


def test_producto_sin_entrada_en_catalogo_cuenta_como_cero():
    repo = InMemoryRepository()
    repo.upsert_catalogo([])
    lineas_por_so = {"SO1": [b.linea("L1", so_id="SO1", producto="P_DESCONOCIDO", cantidad="3")]}
    result = _litros_por_so_desde_espejo(repo, lineas_por_so)
    assert result == {"SO1": 0.0}


def test_orden_sin_lineas_da_cero():
    repo = InMemoryRepository()
    repo.upsert_catalogo([b.producto("P1", volumen="4.0")])
    result = _litros_por_so_desde_espejo(repo, {"SO1": []})
    assert result == {"SO1": 0.0}


def test_multiples_ordenes_independientes():
    repo = InMemoryRepository()
    repo.upsert_catalogo([b.producto("P1", volumen="4.0")])
    lineas_por_so = {
        "SO1": [b.linea("L1", so_id="SO1", producto="P1", cantidad="2")],
        "SO2": [b.linea("L2", so_id="SO2", producto="P1", cantidad="10")],
    }
    result = _litros_por_so_desde_espejo(repo, lineas_por_so)
    assert result == {"SO1": 8.0, "SO2": 40.0}
