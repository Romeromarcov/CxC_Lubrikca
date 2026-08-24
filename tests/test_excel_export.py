import io
from unittest.mock import AsyncMock, patch

import openpyxl
from fastapi.testclient import TestClient

from cxc.web.app import app

client = TestClient(app)

_COBRANZA_ITEMS = [
    {
        "pago_id": "P1",
        "pago_fecha": "2026-08-01",
        "cliente_nombre": "Cliente Uno",
        "vendedor": "Ana Vendedora",
        "monto_pago_original": 100.5,
        "moneda_pago": "USD",
        "so_id": "SO001",
        "estado": "pendiente",
        "posible_duplicado": False,
        "reasignado_por_odoo": False,
        "duplicado_de": [],
        "facturas": [],
    },
    {
        "pago_id": "P2",
        "pago_fecha": "2026-08-02",
        "cliente_nombre": "Cliente Dos",
        "vendedor": "Beto Vendedor",
        "monto_pago_original": 250.0,
        "moneda_pago": "VES",
        "so_id": "SO002",
        "estado": "vinculado_local",
        "posible_duplicado": True,
        "reasignado_por_odoo": False,
        "duplicado_de": ["P1"],
        "facturas": [{"factura_id": "F1", "monto": 100.0}],
    },
    {
        "pago_id": "P3",
        "pago_fecha": "2026-08-03",
        "cliente_nombre": "Cliente Tres",
        "vendedor": "Ana Vendedora",
        "monto_pago_original": 50.0,
        "moneda_pago": "USD",
        "so_id": None,
        "estado": "cerrado_empresa",
        "posible_duplicado": False,
        "reasignado_por_odoo": False,
        "duplicado_de": [],
        "facturas": [],
    },
]

_VENTAS_ITEMS = [
    {
        "so_id": "SO001",
        "cliente_nombre": "Cliente Uno",
        "vendedor": "Ana Vendedora",
        "venta_neta_real": 1000.0,
        "alerta": True,
        "revisar_motivo": "Diferencia detectada",
    },
    {
        "so_id": "SO002",
        "cliente_nombre": "Cliente Dos",
        "vendedor": "Beto Vendedor",
        "venta_neta_real": 500.0,
        "alerta": False,
        "revisar_motivo": None,
    },
]


def _load_xlsx(content: bytes):
    wb = openpyxl.load_workbook(io.BytesIO(content))
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    return rows[0], rows[1:]


@patch("cxc.web.app.get_cobranza_pagos_unificado", new_callable=AsyncMock)
def test_cobranza_excel_returns_all_fields_and_excludes_cerrado_empresa(mock_get):
    mock_get.return_value = _COBRANZA_ITEMS
    res = client.get("/api/cobranza/pagos/excel")
    assert res.status_code == 200
    assert res.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "attachment; filename=" in res.headers["content-disposition"]
    assert ".xlsx" in res.headers["content-disposition"]

    header, rows = _load_xlsx(res.content)
    assert "Cliente" in header
    assert "ID Pago" in header
    # cerrado_empresa nunca aparece en la tabla principal de Cobranza.
    assert len(rows) == 2
    cliente_idx = header.index("Cliente")
    clientes = {r[cliente_idx] for r in rows}
    assert clientes == {"Cliente Uno", "Cliente Dos"}


@patch("cxc.web.app.get_cobranza_pagos_unificado", new_callable=AsyncMock)
def test_cobranza_excel_respeta_filtro_vendedor(mock_get):
    mock_get.return_value = _COBRANZA_ITEMS
    res = client.get("/api/cobranza/pagos/excel", params={"vendedor": "Ana Vendedora"})
    assert res.status_code == 200
    header, rows = _load_xlsx(res.content)
    assert len(rows) == 1
    vendedor_idx = header.index("Vendedor")
    assert rows[0][vendedor_idx] == "Ana Vendedora"


@patch("cxc.web.app.get_cobranza_pagos_unificado", new_callable=AsyncMock)
def test_cobranza_excel_respeta_solo_duplicados_y_search(mock_get):
    mock_get.return_value = _COBRANZA_ITEMS
    res = client.get("/api/cobranza/pagos/excel", params={"solo_duplicados": "true"})
    assert res.status_code == 200
    _, rows = _load_xlsx(res.content)
    assert len(rows) == 1

    res2 = client.get("/api/cobranza/pagos/excel", params={"search": "Cliente Uno"})
    assert res2.status_code == 200
    header2, rows2 = _load_xlsx(res2.content)
    assert len(rows2) == 1
    assert rows2[0][header2.index("Cliente")] == "Cliente Uno"


@patch("cxc.web.app.get_ventas", new_callable=AsyncMock)
def test_ventas_excel_returns_all_fields(mock_get):
    mock_get.return_value = {"items": _VENTAS_ITEMS, "kpis": {}}
    res = client.get("/api/ventas/excel")
    assert res.status_code == 200
    assert res.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    header, rows = _load_xlsx(res.content)
    assert "Orden (SO)" in header
    assert "Motivo Revisión" in header
    assert len(rows) == 2


@patch("cxc.web.app.get_ventas", new_callable=AsyncMock)
def test_ventas_excel_respeta_solo_alertas_y_vendedor_param(mock_get):
    mock_get.return_value = {"items": _VENTAS_ITEMS, "kpis": {}}
    res = client.get("/api/ventas/excel", params={"solo_alertas": "true"})
    assert res.status_code == 200
    header, rows = _load_xlsx(res.content)
    assert len(rows) == 1
    assert rows[0][header.index("Orden (SO)")] == "SO001"

    res2 = client.get("/api/ventas/excel", params={"vendedor": "Beto Vendedor"})
    assert res2.status_code == 200
    mock_get.assert_any_call(vendedor="Beto Vendedor", cxc_session=None)


@patch("cxc.web.app.get_ventas", new_callable=AsyncMock)
def test_ventas_excel_respeta_search(mock_get):
    mock_get.return_value = {"items": _VENTAS_ITEMS, "kpis": {}}
    res = client.get("/api/ventas/excel", params={"search": "SO002"})
    assert res.status_code == 200
    header, rows = _load_xlsx(res.content)
    assert len(rows) == 1
    assert rows[0][header.index("Orden (SO)")] == "SO002"
