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


_CXC_CLIENTES = [
    {
        "cliente_id": "10",
        "cliente_nombre": "Cliente Uno",
        "vendedor": "Ana Vendedora",
        "dias_vencido_max": 45,
        "saldos": {
            "teorico_bs": 150.0,
            "teorico_usd": 100.0,
            "venta_real": 120.0,
            "factura_real": 110.0,
        },
        "saldo_priorizacion": 120.0,
        "descuento_comprometido": 20.0,
        "saldo_cobrable": 100.0,
        "saldo_a_favor": 0.0,
        "saldo_pendiente_por_aplicar": 5.0,
        "documentos": [
            {
                "tipo": "orden",
                "so_id": "SO001",
                "factura_numero": "00000586",
                "fecha": "2026-09-01",
                "dias_vencido": 45,
                "descripcion": "Orden SO001 / Factura 00000586 (facturada)",
                "montos_originales": {
                    "teorico_bs": 150.0,
                    "teorico_usd": 100.0,
                    "venta_real": 120.0,
                    "factura_real": 110.0,
                },
                "saldos": {
                    "teorico_bs": 150.0,
                    "teorico_usd": 100.0,
                    "venta_real": 120.0,
                    "factura_real": 110.0,
                },
                "bandeja_destino": None,
            },
        ],
    },
    {
        "cliente_id": "20",
        "cliente_nombre": "Cliente Dos",
        "vendedor": "Beto Vendedor",
        "dias_vencido_max": 0,
        "saldos": {
            "teorico_bs": 300.0,
            "teorico_usd": 200.0,
            "venta_real": 250.0,
            "factura_real": None,
        },
        "saldo_priorizacion": 250.0,
        "descuento_comprometido": 0.0,
        "saldo_cobrable": 250.0,
        "saldo_a_favor": 0.0,
        "saldo_pendiente_por_aplicar": 0.0,
        "documentos": [
            {
                "tipo": "orden",
                "so_id": "SO002",
                "factura_numero": None,
                "fecha": "2026-11-01",
                "dias_vencido": 0,
                "descripcion": "Orden SO002 (sin facturar)",
                "montos_originales": {
                    "teorico_bs": 350.0,
                    "teorico_usd": 250.0,
                    "venta_real": 300.0,
                    "factura_real": None,
                },
                "saldos": {
                    "teorico_bs": 350.0,
                    "teorico_usd": 250.0,
                    "venta_real": 300.0,
                    "factura_real": None,
                },
            },
            {
                "tipo": "pago_huerfano",
                "pago_id": "PX",
                "numero_pago_odoo": "PBNK/0001",
                "fecha": "2026-11-02",
                "dias_vencido": 0,
                "descripcion": "Pago sin aplicar (USD)",
                "saldos": {
                    "teorico_bs": -50.0,
                    "teorico_usd": -50.0,
                    "venta_real": -50.0,
                    "factura_real": -50.0,
                },
            },
        ],
    },
]


def _load_sheets(content: bytes):
    wb = openpyxl.load_workbook(io.BytesIO(content))
    out = {}
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        out[ws.title] = (rows[3], rows[4:])  # fila 4 = encabezado
    return out


@patch("cxc.web.app.get_reporte_cxc_cliente", new_callable=AsyncMock)
def test_reporte_cxc_excel_resumen_y_detalle(mock_get):
    mock_get.return_value = {"clientes": _CXC_CLIENTES, "totales": {}}
    res = client.get("/api/reporte-cxc-cliente/excel")
    assert res.status_code == 200
    assert "cuentas_por_cobrar_" in res.headers["content-disposition"]

    hojas = _load_sheets(res.content)
    assert set(hojas) == {"Resumen por Cliente", "Detalle por Documento"}

    header, rows = hojas["Resumen por Cliente"]
    datos, total = rows[:-1], rows[-1]
    # Orden por defecto: mayor saldo de priorización primero.
    assert [r[0] for r in datos] == ["Cliente Dos", "Cliente Uno"]
    assert datos[1][header.index("Tramo Antigüedad")] == "31-60 días"
    assert datos[1][header.index("Saldo Cobrable ($)")] == 100.0
    assert total[0] == "TOTAL (2 clientes)"
    assert str(total[header.index("Venta Real ($)")]).startswith("=SUBTOTAL(9,")

    header_d, rows_d = hojas["Detalle por Documento"]
    assert len(rows_d) == 3
    pago = next(r for r in rows_d if r[header_d.index("Tipo")] == "Pago sin aplicar")
    assert pago[header_d.index("Pago")] == "PBNK/0001"
    assert pago[header_d.index("Saldo Venta Real ($)")] == -50.0
    orden = next(r for r in rows_d if r[header_d.index("Orden (SO)")] == "SO001")
    assert orden[header_d.index("Factura")] == "00000586"


@patch("cxc.web.app.get_reporte_cxc_cliente", new_callable=AsyncMock)
def test_reporte_cxc_excel_respeta_filtros(mock_get):
    mock_get.return_value = {"clientes": _CXC_CLIENTES, "totales": {}}

    res = client.get("/api/reporte-cxc-cliente/excel", params={"antiguedad": "31_60"})
    header, rows = _load_sheets(res.content)["Resumen por Cliente"]
    assert [r[0] for r in rows[:-1]] == ["Cliente Uno"]

    res = client.get("/api/reporte-cxc-cliente/excel", params={"vendedor": "Beto Vendedor"})
    _, rows_d = _load_sheets(res.content)["Detalle por Documento"]
    assert len(rows_d) == 2

    res = client.get("/api/reporte-cxc-cliente/excel", params={"search": "ana"})
    _, rows = _load_sheets(res.content)["Resumen por Cliente"]
    assert [r[0] for r in rows[:-1]] == ["Cliente Uno"]

    res = client.get("/api/reporte-cxc-cliente/excel", params={"sort": "cliente_asc"})
    _, rows = _load_sheets(res.content)["Resumen por Cliente"]
    assert [r[0] for r in rows[:-1]] == ["Cliente Dos", "Cliente Uno"]


@patch("cxc.web.app.get_reporte_cxc_cliente", new_callable=AsyncMock)
def test_reporte_cxc_excel_sin_clientes(mock_get):
    mock_get.return_value = {"clientes": [], "totales": {}}
    res = client.get("/api/reporte-cxc-cliente/excel", params={"vendedor": "Nadie"})
    assert res.status_code == 200
    header, rows = _load_sheets(res.content)["Resumen por Cliente"]
    assert header[0] == "Cliente"
    assert rows[-1][0] == "TOTAL (0 clientes)"
