"""Facturas de cliente SIN orden de venta como deuda en los reportes de CxC.

Caso real (noviembre 2026, TERA): fletes facturados directo en Odoo, sin
``invoice_origin``. Son deuda igual que la factura de una orden, pero como
Ventas y el árbol de CxC salen de las órdenes, no aparecían en ningún reporte.
Pedido del usuario: cualquier factura sin orden (no solo fletes) y con el saldo
en USD que da Odoo (``amount_residual_usd``), no en VES.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from cxc.web.app import _facturas_sin_orden_con_saldo, app
from cxc.web.reporte_cxc_vendedor import agrupar_por_vendedor

_FLETE = {
    "id": 21404,
    "name": "00000989",
    "partner_id": [382, "CONSTRUCTORA GRANO AGREGADO, C.A"],
    "invoice_date": "2026-10-27",
    "invoice_date_due": "2026-10-27",
    "invoice_user_id": [7, "Marco Romero"],
    "amount_total_signed_usd": 450.0,
    "amount_residual_usd": 316.06,
    "amount_residual": 284341.15,  # VES: NO es el que se usa
    "invoice_origin": False,
    "move_type": "out_invoice",
}


def _leer_con(recs):
    with (
        patch("cxc.web.app.AppConfig.from_env", return_value=SimpleNamespace(odoo=None)),
        patch("cxc.web.app._connect", return_value=lambda *a, **k: recs),
    ):
        return _facturas_sin_orden_con_saldo()


def test_el_saldo_es_el_de_odoo_en_usd_no_en_ves():
    (f,) = _leer_con([_FLETE])
    assert f["saldo_usd"] == 316.06
    assert f["total_usd"] == 450.0
    assert f["cliente_id"] == "382"
    assert f["vendedor"] == "Marco Romero"
    assert f["numero"] == "00000989"


def test_cualquier_factura_sin_orden_no_solo_fletes():
    servicio = {**_FLETE, "id": 1, "name": "00000999", "amount_residual_usd": 80.0}
    assert {f["numero"] for f in _leer_con([_FLETE, servicio])} == {"00000989", "00000999"}


def test_una_factura_de_orden_nunca_se_cuela_aunque_odoo_la_devuelva():
    """Si el dominio no filtrara (o un execute falso lo ignora), la factura de una orden
    se contaría dos veces: en su orden y aquí."""
    de_orden = {**_FLETE, "id": 2, "invoice_origin": "S01025"}
    nota = {**_FLETE, "id": 3, "move_type": "out_refund"}
    assert [f["factura_id"] for f in _leer_con([_FLETE, de_orden, nota])] == ["21404"]


def test_sin_saldo_o_sin_cliente_no_aparece():
    pagada = {**_FLETE, "id": 4, "amount_residual_usd": 0.0}
    sin_partner = {**_FLETE, "id": 5, "partner_id": False}
    assert _leer_con([pagada, sin_partner]) == []


def test_si_odoo_falla_devuelve_vacio_sin_romper():
    with patch("cxc.web.app.AppConfig.from_env", side_effect=RuntimeError("sin odoo")):
        assert _facturas_sin_orden_con_saldo() == []


@pytest.fixture
def cliente_http():
    return TestClient(app)


def test_el_reporte_por_cliente_la_muestra_como_deuda(cliente_http):
    repo = MagicMock()
    repo.all_ordenes.return_value = []
    repo.all_facturas.return_value = []
    fso = _leer_con([_FLETE])
    with (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app.get_ventas", new=AsyncMock(return_value={"items": []})),
        patch("cxc.web.app.get_conciliaciones_sugerencias", new=AsyncMock(return_value=[])),
        patch("cxc.web.app._facturas_sin_orden_con_saldo", return_value=fso),
    ):
        res = cliente_http.get("/api/reporte-cxc-cliente")
    assert res.status_code == 200
    (c,) = res.json()["clientes"]
    assert c["cliente_nombre"] == "CONSTRUCTORA GRANO AGREGADO, C.A"
    assert c["vendedor"] == "Marco Romero"
    assert c["saldos"] == {
        "teorico_bs": 316.06,
        "teorico_usd": 316.06,
        "venta_real": 316.06,
        "factura_real": 316.06,
    }
    assert c["saldo_cobrable"] == 316.06
    (d,) = c["documentos"]
    assert d["tipo"] == "factura_sin_orden"
    assert d["factura_numero"] == "00000989"
    assert d["montos_originales"]["venta_real"] == 450.0


def test_en_la_vista_por_vendedor_cuenta_en_los_tramos_no_como_pago():
    doc = {
        "tipo": "factura_sin_orden",
        "factura_numero": "00000989",
        "dias_vencido": 14,
        "saldos": dict.fromkeys(
            ("teorico_bs", "teorico_usd", "venta_real", "factura_real"), 316.06
        ),
        "vendedor": "Marco Romero",
    }
    (v,) = agrupar_por_vendedor(
        [
            {
                "cliente_id": "382",
                "cliente_nombre": "X",
                "vendedor": "Marco Romero",
                "documentos": [doc],
            }
        ]
    )
    assert v["antiguedad"]["1_30"] == 316.06
    assert v["pagos_sin_aplicar"] == 0.0
    assert v["n_ordenes"] == 1
