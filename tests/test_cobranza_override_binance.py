"""La corrección manual de la tasa Binance de un pago pendiente se ve en cobranza.

``POST /api/pago/{pago_id}/tasa-binance`` guarda la corrección; estas seis líneas de
``get_cobranza_pagos_unificado`` son las que la aplican al mostrar el pago -- y ninguna
prueba las ejecutaba. Sin ellas, el usuario corrige la tasa y la pantalla sigue
mostrando la del día.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from cxc.web.app import app
from tests.test_e2e_production_readiness import _mock_repo_with_gateway_bridge

client = TestClient(app)


def test_la_correccion_manual_de_binance_manda_sobre_la_del_dia_en_un_pago_pendiente():
    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "777",
                "cliente_id": "C1",
                "monto": "50000.00",
                "moneda": "VES",
                "fecha_pago": "2026-03-18",
                "vendedor_email": "actual@lubrikca.com",
                "metodo_pago": "5",
            }
        ]
        if sheet == "Pagos"
        else (
            [{"cliente_id": "C1", "nombre": "Cliente Uno", "vendedor_email": "actual@lubrikca.com"}]
            if sheet == "Clientes"
            else (
                [
                    {
                        "fecha": "2026-03-18",
                        # La BCV del día se agregó el 11-sep-2026: este test
                        # ejercita las tres tasas por la vía de auditoría, y
                        # antes la BCV le llegaba del default de 2019 sin que
                        # el test lo dijera. Ahora las tres vienen de donde el
                        # test dice que vienen.
                        "tasa_bcv_usd": "451.5072",
                        "tasa_binance_promedio_diario": "550.0",
                        "tasa_bcv_euro": "600.0",
                    }
                ]
                if sheet == "TasasHistoricasAuditoria"
                else []
            )
        )
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = []
    mock_repo.all_auditoria.return_value = []

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment":
            domain = args[0] if args else []
            if ["is_reconciled", "=", True] in domain:
                return []  # get_live_pagos_conciliados -- este pago no está reconciliado
            return [
                {
                    "id": 777,
                    "name": "PBAMI/2026/00099",
                    "tax_today": 500.0,
                    "amount_ref": 100.0,
                    "is_reconciled": False,
                    "state": "in_process",
                    "reconciled_invoices_count": 0,
                }
            ]
        if model == "account.journal":
            return [{"id": 5, "name": "Banco Mercantil VES"}]
        return []

    # La corrección manual: 1.000 en vez de los 550 del día.
    mock_repo.all_pagos_tasa_binance_override.return_value = [
        {"pago_id": "777", "tasa_binance": "1000.0"}
    ]

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=fake_execute),
    ):
        res = client.get("/api/cobranza/pagos")
        assert res.status_code == 200
        (item,) = res.json()

    assert item["pago_id"] == "777" and item["estado"] == "pendiente"
    assert abs(item["tasa_binance"] - 1000.0) < 0.01, "la corregida, no los 550 del día"
    # 50.000 Bs / 1.000 = 50 USD por la ruta Binance; la BCV no se toca (amount_ref).
    assert abs(item["monto_pago_binance_usd"] - 50.0) < 0.01
    assert abs(item["monto_pago_bcv_usd"] - 100.0) < 0.01


def test_una_correccion_en_cero_o_negativa_no_se_aplica():
    mock_repo = _mock_repo_with_gateway_bridge()
    mock_repo._g.read_rows.side_effect = lambda sheet: (
        [
            {
                "pago_id": "777",
                "cliente_id": "C1",
                "monto": "50000.00",
                "moneda": "VES",
                "fecha_pago": "2026-03-18",
                "vendedor_email": "actual@lubrikca.com",
                "metodo_pago": "5",
            }
        ]
        if sheet == "Pagos"
        else (
            [{"cliente_id": "C1", "nombre": "Cliente Uno", "vendedor_email": "actual@lubrikca.com"}]
            if sheet == "Clientes"
            else (
                [
                    {
                        "fecha": "2026-03-18",
                        # La BCV del día se agregó el 11-sep-2026: este test
                        # ejercita las tres tasas por la vía de auditoría, y
                        # antes la BCV le llegaba del default de 2019 sin que
                        # el test lo dijera. Ahora las tres vienen de donde el
                        # test dice que vienen.
                        "tasa_bcv_usd": "451.5072",
                        "tasa_binance_promedio_diario": "550.0",
                        "tasa_bcv_euro": "600.0",
                    }
                ]
                if sheet == "TasasHistoricasAuditoria"
                else []
            )
        )
    )
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_ordenes.return_value = []
    mock_repo.all_auditoria.return_value = []

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment":
            domain = args[0] if args else []
            if ["is_reconciled", "=", True] in domain:
                return []  # get_live_pagos_conciliados -- este pago no está reconciliado
            return [
                {
                    "id": 777,
                    "name": "PBAMI/2026/00099",
                    "tax_today": 500.0,
                    "amount_ref": 100.0,
                    "is_reconciled": False,
                    "state": "in_process",
                    "reconciled_invoices_count": 0,
                }
            ]
        if model == "account.journal":
            return [{"id": 5, "name": "Banco Mercantil VES"}]
        return []

    mock_repo.all_pagos_tasa_binance_override.return_value = [
        {"pago_id": "777", "tasa_binance": "0"}
    ]

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app._connect", return_value=fake_execute),
    ):
        (item,) = client.get("/api/cobranza/pagos").json()
    assert abs(item["tasa_binance"] - 550.0) < 0.01, "un cero no es una corrección"
