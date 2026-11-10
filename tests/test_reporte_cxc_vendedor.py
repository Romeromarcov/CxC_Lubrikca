import io
from unittest.mock import AsyncMock, patch

import openpyxl
from fastapi.testclient import TestClient

from cxc.web.app import app
from cxc.web.reporte_cxc_vendedor import agrupar_por_vendedor, filtrar_y_ordenar, totales

client = TestClient(app)


def _orden(so_id, vendedor, dias, venta_real, teorico_usd=None, factura_real=None):
    teorico_usd = venta_real if teorico_usd is None else teorico_usd
    saldos = {
        "teorico_bs": venta_real * 1.5,
        "teorico_usd": teorico_usd,
        "venta_real": venta_real,
        "factura_real": factura_real,
    }
    return {
        "tipo": "orden",
        "so_id": so_id,
        "factura_numero": None,
        "fecha": "2026-10-01",
        "dias_vencido": dias,
        "descripcion": f"Orden {so_id}",
        "montos_originales": dict(saldos),
        "saldos": saldos,
        "vendedor": vendedor,
    }


def _pago(pago_id, monto, vendedor=None):
    s = dict.fromkeys(("teorico_bs", "teorico_usd", "venta_real", "factura_real"), -monto)
    return {
        "tipo": "pago_huerfano",
        "pago_id": pago_id,
        "numero_pago_odoo": f"PBNK/{pago_id}",
        "fecha": "2026-11-01",
        "dias_vencido": 0,
        "descripcion": "Pago sin aplicar (USD)",
        "saldos": s,
        "vendedor": vendedor,
    }


# Cliente Mixto compra con Ana y con Beto: cada uno debe ver solo lo suyo.
_CLIENTES = [
    {
        "cliente_id": "1",
        "cliente_nombre": "Cliente Mixto",
        "vendedor": "Ana, Beto",
        "documentos": [
            _orden("SO1", "Ana", 0, 100.0, factura_real=90.0),
            _orden("SO2", "Beto", 45, 200.0),
            _pago("P1", 30.0),  # sin orden sugerida y cliente con 2 vendedores
        ],
    },
    {
        "cliente_id": "2",
        "cliente_nombre": "Cliente Ana",
        "vendedor": "Ana",
        "documentos": [
            _orden("SO3", "Ana", 120, 50.0),
            _pago("P2", 10.0),  # sin orden sugerida, cliente con un solo vendedor
        ],
    },
]


def _por_nombre(vendedores):
    return {v["vendedor"]: v for v in vendedores}


def test_reparte_documento_por_documento():
    v = _por_nombre(agrupar_por_vendedor(_CLIENTES))
    assert set(v) == {"Ana", "Beto", "Sin Vendedor"}

    ana = v["Ana"]
    assert ana["n_clientes"] == 2
    assert ana["n_ordenes"] == 2
    assert ana["saldos"]["venta_real"] == 140.0  # 100 + 50 - 10
    assert ana["antiguedad"] == {
        "vigente": 100.0,
        "1_30": 0.0,
        "31_60": 0.0,
        "61_90": 0.0,
        "mas_90": 50.0,
    }
    assert ana["pagos_sin_aplicar"] == -10.0
    assert ana["dias_vencido_max"] == 120
    # La factura_real None de SO3 no suma ni rompe.
    assert ana["saldos"]["factura_real"] == 80.0

    beto = v["Beto"]
    assert [c["cliente_nombre"] for c in beto["clientes"]] == ["Cliente Mixto"]
    assert beto["antiguedad"]["31_60"] == 200.0

    assert v["Sin Vendedor"]["pagos_sin_aplicar"] == -30.0


def test_tramos_mas_pagos_igual_venta_real_y_total_cuadra_con_clientes():
    vendedores = agrupar_por_vendedor(_CLIENTES)
    for v in vendedores:
        suma = sum(v["antiguedad"].values()) + v["pagos_sin_aplicar"]
        assert round(suma, 2) == v["saldos"]["venta_real"]
    t = totales(vendedores)
    total_docs = sum(d["saldos"]["venta_real"] for c in _CLIENTES for d in c["documentos"])
    assert t["saldos"]["venta_real"] == round(total_docs, 2)
    assert t["n_vendedores"] == 3


def test_pago_con_orden_sugerida_va_a_ese_vendedor():
    clientes = [
        {
            "cliente_id": "9",
            "cliente_nombre": "X",
            "vendedor": "Ana, Beto",
            "documentos": [_orden("SO9", "Ana", 0, 10.0), _pago("P9", 5.0, vendedor="Beto")],
        }
    ]
    v = _por_nombre(agrupar_por_vendedor(clientes))
    assert v["Beto"]["pagos_sin_aplicar"] == -5.0


def test_filtrar_y_ordenar():
    vendedores = agrupar_por_vendedor(_CLIENTES)
    assert [v["vendedor"] for v in filtrar_y_ordenar(vendedores)][0] == "Beto"
    assert [v["vendedor"] for v in filtrar_y_ordenar(vendedores, sort="vendedor_asc")] == [
        "Ana",
        "Beto",
        "Sin Vendedor",
    ]
    assert [v["vendedor"] for v in filtrar_y_ordenar(vendedores, search="ana")] == ["Ana"]


@patch("cxc.web.app.get_reporte_cxc_cliente", new_callable=AsyncMock)
def test_endpoint_json(mock_get):
    mock_get.return_value = {"clientes": _CLIENTES, "totales": {}}
    res = client.get("/api/reporte-cxc-vendedor")
    assert res.status_code == 200
    data = res.json()
    assert {v["vendedor"] for v in data["vendedores"]} == {"Ana", "Beto", "Sin Vendedor"}
    assert data["totales"]["n_vendedores"] == 3


@patch("cxc.web.app.get_reporte_cxc_cliente", new_callable=AsyncMock)
def test_endpoint_excel(mock_get):
    mock_get.return_value = {"clientes": _CLIENTES, "totales": {}}
    res = client.get("/api/reporte-cxc-vendedor/excel", params={"sort": "vendedor_asc"})
    assert res.status_code == 200
    assert "cuentas_por_cobrar_por_vendedor_" in res.headers["content-disposition"]

    wb = openpyxl.load_workbook(io.BytesIO(res.content))
    assert wb.sheetnames == ["Resumen por Vendedor", "Vendedor - Cliente", "Detalle por Documento"]

    filas = list(wb["Resumen por Vendedor"].iter_rows(values_only=True))
    header, datos = filas[3], filas[4:]
    assert [r[0] for r in datos[:-1]] == ["Ana", "Beto", "Sin Vendedor"]
    assert datos[-1][0] == "TOTAL (3 vendedores)"
    assert datos[0][header.index("+90 días ($)")] == 50.0
    assert datos[0][header.index("Pagos sin Aplicar ($)")] == -10.0

    vc = list(wb["Vendedor - Cliente"].iter_rows(values_only=True))[4:-1]
    assert len(vc) == 4  # Ana x2 clientes, Beto x1, Sin Vendedor x1

    detalle = list(wb["Detalle por Documento"].iter_rows(values_only=True))[4:]
    assert len(detalle) == 5
