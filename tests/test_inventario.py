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


def test_inventario_comparativo_usa_lista_vigente_y_suma_iva():
    repo = MagicMock()
    repo.all_catalogo.return_value = [_producto(producto_id="100", nombre="Producto Comparado")]

    def fake_execute(model, method, args, kwargs=None):
        assert model == "product.pricelist.item"
        return [
            {"pricelist_id": [8, "Pago USD"], "product_tmpl_id": [100, "P"], "fixed_price": 100.0},
            {"pricelist_id": [5, "USD"], "product_tmpl_id": [100, "P"], "fixed_price": 90.0},
        ]

    with (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch.object(
            app_module,
            "get_pricelist_vigente_por_grupo",
            return_value={
                "comercial_usd": "8",
                "comercial_ves": "5",
                "industrial_usd": None,
                "industrial_ves": None,
            },
        ),
    ):
        res = client.get("/api/inventario/comparativo?categoria=comercial")

    assert res.status_code == 200
    data = res.json()
    assert data["usd_lista_id"] == "8"
    assert data["ves_lista_id"] == "5"
    assert len(data["items"]) == 1
    item = data["items"][0]
    # IVA 16% (default de config en tests) sobre el precio fijo de la regla.
    assert item["precio_usd_con_iva"] == 116.0
    assert item["precio_ves_con_iva"] == 104.4


def test_inventario_comparativo_sin_lista_vigente_devuelve_items_vacios():
    repo = MagicMock()
    repo.all_catalogo.return_value = [_producto()]
    with (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app._connect", return_value=None),
        patch.object(
            app_module,
            "get_pricelist_vigente_por_grupo",
            return_value={k: None for k in app_module._CLASIFICACION_KEYS},
        ),
    ):
        res = client.get("/api/inventario/comparativo?categoria=industrial")
    assert res.status_code == 200
    data = res.json()
    assert data["usd_lista_id"] is None
    assert data["items"] == []


def test_inventario_comparativo_categoria_invalida_400():
    res = client.get("/api/inventario/comparativo?categoria=rara")
    assert res.status_code == 400


def test_pagina_inventario_incluida_en_permisos_de_todos_los_roles():
    from cxc.auth import ALL_PAGES, ROLES_PERMISOS

    assert "inventario" in ALL_PAGES
    for rol, permisos in ROLES_PERMISOS.items():
        assert "inventario" in permisos, f"rol {rol} sin acceso a inventario"
