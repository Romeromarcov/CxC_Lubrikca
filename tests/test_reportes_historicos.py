"""Tests e2e (mocked) para los 3 nuevos endpoints de reportes históricos:

- GET /api/reportes/cxc-vencida-junio
- GET /api/reportes/cobranza-julio
- GET /api/reportes/recuperacion-julio

Mismo patrón que ``tests/test_e2e_production_readiness.py``: se mockea
``get_repo``/``AppConfig.from_env``/``_connect``/``get_current_user_from_cookie``,
sin tocar Odoo/Postgres reales. ``_connect`` se mockea para devolver un
``execute`` falso que responde según el modelo/método pedido, simulando la
red de conciliación real de Odoo (facturas, líneas por cobrar,
account.partial.reconcile, pagos).
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cxc.models import Cliente, OrdenVenta
from cxc.web.app import SECRET_KEY, app, crear_session_token

client = TestClient(app)


def _mock_repo():
    repo = MagicMock()
    repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="S00143",
            cliente_id="442",
            fecha=None,
            fecha_entrega=None,
            monto_total=0,
            lista_precios="4",
            vendedor_email="TORO ARDILES DAVID JOSE",
            es_primera_compra=False,
        )
    ]
    repo.all_clientes.return_value = [
        Cliente(cliente_id="442", nombre="MOTOREPUESTOS", vendedor_email="TORO ARDILES DAVID JOSE"),
        Cliente(cliente_id="999", nombre="OTRO CLIENTE", vendedor_email="OTRO VENDEDOR"),
    ]
    return repo


def _fake_execute(model: str, method: str, args, kwargs=None):
    kwargs = kwargs or {}
    if model == "account.move" and method == "search_read":
        # Una sola factura vencida: venció 2026-03-31, se reconcilió
        # completa el 2026-07-02 (después de ambos cortes) -- debe salir
        # como vencida-y-no-pagada al 30-jun, y recuperada en julio.
        return [
            {
                "id": 1355,
                "name": "00000218",
                "invoice_origin": "S00143",
                "partner_id": [442, "MOTOREPUESTOS"],
                "invoice_date": "2026-03-01",
                "invoice_date_due": "2026-03-31",
                "amount_total_signed_usd": 200.0,
                "amount_residual": 0.0,
                "amount_residual_usd": 0.0,
                "amount_total": 100000.0,
                "currency_id": [1, "VES"],
                "payment_state": "in_payment",
                "state": "posted",
                "move_type": "out_invoice",
            }
        ]
    if model == "account.move" and method == "read":
        # Usado por cobranza_por_vendedor para resolver invoice_origin de
        # facturas conciliadas por un pago.
        return [{"id": 1355, "invoice_origin": "S00143"}]
    if model == "account.move.line" and method == "search_read":
        return [
            {
                "id": 3007,
                "move_id": [1355, "00000218"],
                "debit": 100000.0,
                "credit": 0.0,
                "balance": 100000.0,
                "matched_credit_ids": [660],
                "reconciled": True,
            }
        ]
    if model == "account.partial.reconcile" and method == "read":
        return [
            {
                "id": 660,
                "debit_move_id": [3007, "00000218"],
                "credit_move_id": [4412, "pago"],
                "amount": 100000.0,
                "max_date": "2026-07-02",
            }
        ]
    if model == "account.payment" and method == "search_read":
        return [
            {
                "id": 9001,
                "partner_id": [442, "MOTOREPUESTOS"],
                "amount": 200.0,
                "amount_ref": 200.0,
                "date": "2026-07-02",
                "currency_id": [1, "VES"],
                "reconciled_invoice_ids": [1355],
            }
        ]
    raise AssertionError(f"llamada Odoo inesperada en el test: {model}.{method}")


@contextlib.contextmanager
def _auth_and_mocks():
    repo = _mock_repo()
    token = crear_session_token("gerencia@lubrikca.com", SECRET_KEY)
    with (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=_fake_execute),
        patch(
            "cxc.web.app.get_current_user_from_cookie",
            return_value={"email": "gerencia@lubrikca.com", "rol": "admin", "nombre": "Gerencia"},
        ),
    ):
        client.cookies.set("cxc_session", token)
        try:
            yield
        finally:
            client.cookies.delete("cxc_session")


def test_cxc_vencida_junio_marca_factura_no_pagada_al_corte():
    with _auth_and_mocks():
        res = client.get("/api/reportes/cxc-vencida-junio")
        assert res.status_code == 200
        data = res.json()
        assert data["corte"] == "2026-06-30"
        assert data["total_facturas"] == 1
        assert data["total_monto_vencido_usd"] == 200.0
        assert data["resumen_por_vendedor"] == [
            {"vendedor": "TORO ARDILES DAVID JOSE", "n_facturas": 1, "monto_vencido_usd": 200.0}
        ]
        detalle = data["detalle"][0]
        assert detalle["factura_id"] == 1355
        assert detalle["pagado_al_corte_usd"] == 0.0
        assert detalle["residual_al_corte_usd"] == 200.0


def test_cobranza_julio_agrupa_por_vendedor_via_orden():
    with _auth_and_mocks():
        res = client.get("/api/reportes/cobranza-julio")
        assert res.status_code == 200
        data = res.json()
        assert data["total_pagos"] == 1
        assert data["total_monto_usd"] == 200.0
        assert data["resumen_por_vendedor"] == [
            {"vendedor": "TORO ARDILES DAVID JOSE", "monto_cobrado_usd": 200.0}
        ]


def test_recuperacion_julio_cruza_vencida_con_cobranza_de_julio():
    with _auth_and_mocks():
        res = client.get("/api/reportes/recuperacion-julio")
        assert res.status_code == 200
        data = res.json()
        assert len(data["detalle"]) == 1
        fila = data["detalle"][0]
        assert fila["residual_al_corte_usd"] == 200.0
        assert fila["recuperado_en_ventana_usd"] == 200.0
        assert fila["residual_al_fin_ventana_usd"] == 0.0

        totales = data["totales_por_vendedor"]
        assert len(totales) == 1
        t = totales[0]
        assert t["vendedor"] == "TORO ARDILES DAVID JOSE"
        assert t["monto_vencido_30jun_usd"] == 200.0
        assert t["cobrado_julio_especifico_usd"] == 200.0
        assert t["pct_recuperacion_especifica"] == 100.0
        assert t["cobrado_julio_general_usd"] == 200.0
        assert t["pct_recuperacion_general"] == 100.0


def test_endpoints_requieren_autenticacion():
    with contextlib.suppress(Exception):
        client.cookies.delete("cxc_session")
    for path in (
        "/api/reportes/cxc-vencida-junio",
        "/api/reportes/cobranza-julio",
        "/api/reportes/recuperacion-julio",
    ):
        res = client.get(path)
        assert res.status_code == 401
