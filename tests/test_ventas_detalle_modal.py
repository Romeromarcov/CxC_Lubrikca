"""Fase 5: modal de detalle de orden -- ``GET /api/ventas/{so_id}/detalle``.

Cubre los 4 modos (Real Orden, Real Factura, Teórico VES, Teórico USD) con
un mock de Odoo mínimo: una orden con 1 línea, una factura posted con 1
línea, y reglas de precio fijo por pricelist para el cálculo teórico.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cxc.config import EngineConfig
from cxc.models import LineaOrden, OrdenVenta
from cxc.web.app import app

client = TestClient(app)


def _orden() -> OrdenVenta:
    return OrdenVenta(
        so_id="SO_DETALLE",
        cliente_id="CLI_1",
        vendedor_email="ana@lubrikca.com",
        fecha=date(2026, 7, 1),
        fecha_entrega=None,
        monto_total=Decimal("100.00"),
        lista_precios="4",
        es_primera_compra=False,
        estado_orden="sale",
        facturada=True,
    )


def _linea() -> LineaOrden:
    return LineaOrden(
        linea_id="L1",
        so_id="SO_DETALLE",
        producto="101",
        marca="Sinoco",
        categoria="Comercial",
        cantidad=Decimal("2"),
        precio_unitario=Decimal("50"),
    )


def _fake_execute(model, method, args, kwargs=None):
    domain = args[0] if args else []

    if model == "product.pricelist":
        return [{"id": 4, "name": "USD"}, {"id": 5, "name": "BCV"}]

    if model == "product.pricelist.item":
        pl_id = next((c[2] for c in domain if c[0] == "pricelist_id"), None)
        if pl_id == 4:
            return [{"fixed_price": 55.0, "date_start": False, "date_end": False}]
        if pl_id == 5:
            return [{"fixed_price": 45.0, "date_start": False, "date_end": False}]
        return []

    if model == "sale.order.line":
        return [
            {
                "product_id": [101, "Producto Sinoco"],
                "product_uom_qty": 2.0,
                "price_unit": 50.0,
                "discount": 10.0,
                "price_subtotal": 90.0,
            }
        ]

    if model == "account.move":
        return [{"id": 900}]

    if model == "account.move.line":
        return [
            {
                "product_id": [101, "Producto Sinoco"],
                "quantity": 2.0,
                "price_unit": 50.0,
                "discount": 5.0,
                "price_subtotal": 95.0,
            }
        ]

    return []


def _run_get_detalle():
    mock_repo = MagicMock()
    mock_repo.get_orden.return_value = _orden()
    mock_repo.all_clientes.return_value = []
    mock_repo.lineas_de_orden.return_value = [_linea()]
    mock_repo.vinculaciones_de_orden.return_value = []

    fake_config = MagicMock()
    fake_config.engine = EngineConfig(
        cash_window_business_days=3,
        bcv_complete_formula="differential_over_binance",
    )
    fake_config.odoo = MagicMock()

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=_fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/ventas/SO_DETALLE/detalle")
        assert res.status_code == 200
        return res.json()


def test_detalle_real_orden_trae_todas_las_lineas_con_pct_y_monto() -> None:
    data = _run_get_detalle()
    lineas = data["real_orden"]["lineas"]
    assert len(lineas) == 1
    linea = lineas[0]
    assert linea["cantidad"] == 2.0
    assert linea["precio_unitario"] == 50.0
    assert linea["descuento_pct"] == 10.0
    assert linea["descuento_monto"] == 10.0  # 2*50*0.10
    assert linea["subtotal"] == 90.0
    assert data["real_orden"]["descuento_total"] == 10.0


def test_detalle_real_factura_lee_de_account_move_line() -> None:
    data = _run_get_detalle()
    lineas = data["real_factura"]["lineas"]
    assert len(lineas) == 1
    assert lineas[0]["descuento_pct"] == 5.0
    assert lineas[0]["subtotal"] == 95.0


def test_detalle_teorico_ves_y_usd_resuelven_precio_por_pricelist() -> None:
    data = _run_get_detalle()
    ves = data["teorico_ves"]
    usd = data["teorico_usd"]
    assert len(ves["lineas"]) == 1
    assert len(usd["lineas"]) == 1
    # pricelist 5 (BCV) = 45.0/unidad; pricelist 4 (USD) = 55.0/unidad.
    assert ves["lineas"][0]["precio_unitario"] == 45.0
    assert usd["lineas"][0]["precio_unitario"] == 55.0
    assert ves["lineas"][0]["subtotal"] == 90.0  # 2 * 45
    assert usd["lineas"][0]["subtotal"] == 110.0  # 2 * 55


def test_detalle_orden_inexistente_da_404() -> None:
    mock_repo = MagicMock()
    mock_repo.get_orden.return_value = None
    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        res = client.get("/api/ventas/SO_NO_EXISTE/detalle")
    assert res.status_code == 404
