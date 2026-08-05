"""``GET /api/pagos-historial`` -- bug real (agosto 2026): para pagos YA

reconciliados en Odoo (la mayoría, item "Odoo (automático vía factura)"),
``tasa_binance`` quedaba hardcodeada en ``None`` -- Binance no es un campo
de Odoo, así que a diferencia de ``tasa_bcv`` (que sí tenía un fallback
separado) esta tasa nunca se calculaba, dejando el equivalente Binance en
blanco para todo pago en VES ya conciliado.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cxc.web.app import app

client = TestClient(app)


def _fake_execute_pago_ves_conciliado(model, method, args, kwargs=None):
    if model == "account.payment":
        return [
            {
                "id": 17,
                "partner_id": [202, "TERA INGENIERIA"],
                "amount": 16606.59,
                "amount_ref": 32.53,
                "amount_available_for_refund": 0.0,
                "currency_id": [166, "VES"],
                "journal_id": [5, "Banco Bancamiga"],
                "date": "2026-03-12",
                "reconciled_invoice_ids": [4824],
            }
        ]
    if model == "account.move":
        return [
            {
                "id": 4824,
                "name": "00000270",
                "invoice_origin": "S00020",
                "move_type": "out_invoice",
                "state": "posted",
                "amount_total_signed_usd": 32.53,
                "amount_residual_usd": 0.0,
            }
        ]
    if model == "res.partner":
        return [{"id": 202, "user_id": False}]
    return []


def test_pagos_historial_calcula_tasa_binance_para_pagos_ya_conciliados() -> None:
    mock_repo = MagicMock()
    mock_repo.all_vinculaciones.return_value = []
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_pagos.return_value = []
    mock_repo.all_clientes.return_value = []
    mock_repo.all_ordenes.return_value = []
    mock_repo.all_serie_tasas.return_value = []
    mock_repo.all_tasas_historicas_auditoria.return_value = [
        {
            "fecha": "2026-03-12",
            "tasa_bcv_usd": "440.9657",
            "tasa_bcv_euro": "510.4884",
            "tasa_binance_promedio_diario": "516.8118",
        }
    ]

    fake_config = MagicMock()
    fake_config.odoo = MagicMock()

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=_fake_execute_pago_ves_conciliado),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/pagos-historial")
        assert res.status_code == 200
        data = res.json()

    pago = next(p for p in data if p["pago_id"] == "17")
    assert pago["origen"] == "Odoo (automático vía factura)"
    assert pago["tasa_binance"] == 516.8118
