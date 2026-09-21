"""``montos_reembolsados_en_pagos`` (cxc/odoo/client.py), función de módulo
extraída del método de instancia homónimo el 19-sep-2026 para que
``engine/balance.py`` la pudiera reusar sin depender de la clase entera --
ver ``tests/test_balance_externas.py`` para el caso real que motivó la
extracción (pago de producción id 1084, reembolso embebido de 2.372.712,49
VES en el mismo asiento).
"""

from __future__ import annotations

from decimal import Decimal

from cxc.odoo.client import montos_reembolsados_en_pagos


class _Odoo:
    def __init__(self, respuestas: dict[str, list[dict]]) -> None:
        self.respuestas = respuestas
        self.consultas: list[str] = []

    def __call__(self, modelo, metodo, args, kwargs=None):
        self.consultas.append(modelo)
        return self.respuestas.get(modelo, [])


def test_sin_move_ids_no_consulta_nada() -> None:
    odoo = _Odoo({})
    assert montos_reembolsados_en_pagos(odoo, []) == {}
    assert odoo.consultas == []


def test_sin_lineas_de_credito_no_hay_reembolsos() -> None:
    odoo = _Odoo({"account.move.line": []})
    assert montos_reembolsados_en_pagos(odoo, [555]) == {}


def test_una_linea_en_cuenta_asset_cash_es_un_reembolso() -> None:
    odoo = _Odoo(
        {
            "account.move.line": [
                {
                    "move_id": [555, "PBAMI/2026/00001"],
                    "account_id": [99, "Banco"],
                    "amount_currency": -500.0,
                }
            ],
            "account.account": [{"id": 99, "account_type": "asset_cash"}],
        }
    )
    assert montos_reembolsados_en_pagos(odoo, [555]) == {555: Decimal("500.0")}


def test_una_linea_de_credito_en_la_cuenta_por_cobrar_no_es_un_reembolso() -> None:
    """La aplicación normal del pago también acredita en algún lado -- pero
    NUNCA en ``asset_cash``. Distinguir por tipo de cuenta es justo lo que
    evita confundir la aplicación real con la devolución."""
    odoo = _Odoo(
        {
            "account.move.line": [
                {
                    "move_id": [555, "PBAMI/2026/00001"],
                    "account_id": [11, "Cuentas por Cobrar"],
                    "amount_currency": -1000.0,
                }
            ],
            "account.account": [{"id": 11, "account_type": "asset_receivable"}],
        }
    )
    assert montos_reembolsados_en_pagos(odoo, [555]) == {}


def test_dos_lineas_de_reembolso_en_el_mismo_asiento_se_suman() -> None:
    odoo = _Odoo(
        {
            "account.move.line": [
                {
                    "move_id": [555, "x"],
                    "account_id": [99, "Banco"],
                    "amount_currency": -300.0,
                },
                {
                    "move_id": [555, "x"],
                    "account_id": [99, "Banco"],
                    "amount_currency": -200.0,
                },
            ],
            "account.account": [{"id": 99, "account_type": "asset_cash"}],
        }
    )
    assert montos_reembolsados_en_pagos(odoo, [555]) == {555: Decimal("500.0")}


def test_reembolsos_de_distintos_asientos_no_se_mezclan() -> None:
    odoo = _Odoo(
        {
            "account.move.line": [
                {"move_id": [1, "a"], "account_id": [99, "Banco"], "amount_currency": -100.0},
                {"move_id": [2, "b"], "account_id": [99, "Banco"], "amount_currency": -200.0},
            ],
            "account.account": [{"id": 99, "account_type": "asset_cash"}],
        }
    )
    assert montos_reembolsados_en_pagos(odoo, [1, 2]) == {1: Decimal("100.0"), 2: Decimal("200.0")}
