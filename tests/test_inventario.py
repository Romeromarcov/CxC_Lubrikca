"""Fase D (plan de Inventario/Catalogo, agosto 2026, pedido explicito del

usuario): pagina nueva "Inventario" -- listas de precio agrupadas
Industrial/Comercial x USD/VES, y ficha de catalogo (codigo, presentacion,
litros, peso, unidades por paleta), sin datos hardcodeados.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cxc.models import Producto
from cxc.web import app as app_module
from cxc.web.app import app

client = TestClient(app)


def _producto(**kwargs) -> Producto:
    defaults = {
        "producto_id": "1",
        "codigo": "SKU-1",
        "nombre": "ELITE API SP SAE 0W-20 (1x6)",
        "marca": "Global Oil",
        "volumen": Decimal("5.67"),
        "peso": Decimal("5.3"),
        "unidades_por_paleta": Decimal("150"),
    }
    defaults.update(kwargs)
    return Producto(**defaults)


def test_inventario_catalogo_deriva_presentacion_del_nombre():
    repo = MagicMock()
    repo.all_catalogo.return_value = [_producto()]
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.get("/api/inventario/catalogo")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["presentacion"] == "1X6"
    assert data[0]["litros"] == 5.67
    assert data[0]["unidades_por_paleta"] == 150.0


def test_inventario_catalogo_sin_parentesis_presentacion_vacia():
    repo = MagicMock()
    repo.all_catalogo.return_value = [_producto(nombre="Agua Desmineralizada")]
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.get("/api/inventario/catalogo")
    assert res.json()[0]["presentacion"] == ""


def test_inventario_catalogo_ordena_por_nombre():
    repo = MagicMock()
    repo.all_catalogo.return_value = [
        _producto(producto_id="1", nombre="Z Producto"),
        _producto(producto_id="2", nombre="A Producto"),
    ]
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.get("/api/inventario/catalogo")
    nombres = [r["nombre"] for r in res.json()]
    assert nombres == ["A Producto", "Z Producto"]


def test_inventario_listas_agrupa_por_clasificacion():
    repo = MagicMock()
    repo.get_config.side_effect = lambda k: {
        "valid_pricelists_industrial_usd": "8",
        "valid_pricelists_comercial_ves": "3,9",
    }.get(k)

    def fake_execute(model, method, args, kwargs=None):
        assert model == "product.pricelist"
        return [
            {"id": 8, "name": "Pago USD", "currency_id": [1, "USD"], "active": True},
            {"id": 3, "name": "USD", "currency_id": [1, "USD"], "active": True},
            {"id": 9, "name": "Lista Industrial 3%", "currency_id": [1, "USD"], "active": False},
        ]

    with (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch.object(app_module, "_load_clasificacion_from_json", return_value=None),
        patch.object(app_module, "_save_clasificacion_to_json"),
    ):
        app_module._PRICELIST_CLASIFICACION_CACHE.clear()
        res = client.get("/api/inventario/listas")
        app_module._PRICELIST_CLASIFICACION_CACHE.clear()

    assert res.status_code == 200
    data = res.json()
    assert [pl["id"] for pl in data["industrial_usd"]] == [8]
    assert data["industrial_ves"] == []
    assert data["comercial_usd"] == []
    ves_ids = sorted(pl["id"] for pl in data["comercial_ves"])
    assert ves_ids == [3, 9]


def test_inventario_listas_sin_conexion_odoo_devuelve_vacio():
    repo = MagicMock()
    repo.get_config.return_value = None
    with (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app._connect", return_value=None),
        patch.object(app_module, "_load_clasificacion_from_json", return_value=None),
        patch.object(app_module, "_save_clasificacion_to_json"),
    ):
        app_module._PRICELIST_CLASIFICACION_CACHE.clear()
        res = client.get("/api/inventario/listas")
        app_module._PRICELIST_CLASIFICACION_CACHE.clear()
    assert res.status_code == 200
    data = res.json()
    assert all(v == [] for v in data.values())


def test_pagina_inventario_incluida_en_permisos_de_todos_los_roles():
    from cxc.auth import ALL_PAGES, ROLES_PERMISOS

    assert "inventario" in ALL_PAGES
    for rol, permisos in ROLES_PERMISOS.items():
        assert "inventario" in permisos, f"rol {rol} sin acceso a inventario"
