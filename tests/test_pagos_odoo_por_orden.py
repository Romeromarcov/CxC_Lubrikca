"""Test de caracterización de ``_pagos_odoo_por_orden`` (agosto 2026).

Esta función alimenta ``/api/reporte-saldos`` y es intencionalmente
distinta de ``_pagos_por_so_desde_cobranza`` (usada por ``/api/ventas`` --
ver docstring de la función). El propio código documenta que esta
divergencia es una limitación heredada que NO se corrige aquí a propósito
porque el reporte de saldos se va a sustituir en una fase posterior del
plan de migración a Ventas+Cobranza como fuente única.

Estos tests NO validan que el comportamiento sea "correcto" -- solo lo
fijan (characterization tests) para que una futura migración real pueda
compararse contra un baseline conocido en vez de adivinar si cambió algo.
"""

from __future__ import annotations

from decimal import Decimal

from cxc.web.app import _pagos_odoo_por_orden
from tests.builders import orden


def _factura(
    *,
    amount_total: str,
    amount_residual: str,
    currency: str = "USD",
    invoice_date: str = "2026-06-01",
) -> dict:
    return {
        "amount_total": amount_total,
        "amount_residual": amount_residual,
        "currency_id": [1, currency] if currency != "USD" else False,
        "invoice_date": invoice_date,
    }


def test_sin_execute_devuelve_vacio():
    assert _pagos_odoo_por_orden(None, [], {}, {}, {}, {}, 0.0) == {}


def test_ruta_principal_account_payment_usd():
    def fake_execute(model, method, args, kwargs=None):
        assert model == "account.payment"
        return [
            {
                "id": 1,
                "amount": 500,
                "currency_id": False,
                "date": "2026-06-10",
                "reconciled_invoice_ids": [101],
            }
        ]

    result = _pagos_odoo_por_orden(
        fake_execute,
        [101],
        {101: "SO1"},
        {"SO1": [_factura(amount_total="500", amount_residual="0")]},
        {"SO1": orden("SO1", monto_total="500")},
        {},
        0.0,
    )
    assert result == {
        "SO1": {
            "abono_bcv": Decimal("500"),
            "abono_binance": Decimal("500"),
            "ultimo_abono": "2026-06-10",
        }
    }


def test_ruta_principal_account_payment_ves_usa_tasa_bcv():
    def fake_execute(model, method, args, kwargs=None):
        return [
            {
                "id": 1,
                "amount": 3600,
                "currency_id": [2, "VES"],
                "date": "2026-06-10",
                "reconciled_invoice_ids": [101],
            }
        ]

    result = _pagos_odoo_por_orden(
        fake_execute,
        [101],
        {101: "SO1"},
        {"SO1": [_factura(amount_total="500", amount_residual="0", currency="VES")]},
        {"SO1": orden("SO1", monto_total="500")},
        {"2026-06-10": 36.0},
        30.0,
    )
    # abono_bcv y abono_binance salen IGUALES para VES -- ver docstring
    # de la función (Odoo no distingue la ruta de pago a nivel de
    # account.payment, solo la moneda).
    assert result["SO1"]["abono_bcv"] == Decimal("100")
    assert result["SO1"]["abono_binance"] == Decimal("100")


def test_fallback_sin_pago_reconciliado_usa_amount_total_menos_residual_usd():
    def fake_execute(model, method, args, kwargs=None):
        return []  # sin account.payment reconciliado -> cae al fallback

    result = _pagos_odoo_por_orden(
        fake_execute,
        [101],
        {101: "SO1"},
        {"SO1": [_factura(amount_total="800", amount_residual="300")]},
        {"SO1": orden("SO1", monto_total="800")},
        {},
        0.0,
    )
    assert result["SO1"]["abono_bcv"] == Decimal("500")
    assert result["SO1"]["abono_binance"] == Decimal("500")


def test_fallback_ves_usa_ratio_amount_total_vs_monto_orden_usd():
    """Cuando la factura VES trae amount_total > 0 y la orden tiene

    monto_total en USD > 0, el fallback infiere la tasa efectiva como
    amount_total_ves / monto_total_usd de la orden, en vez de usar
    rates_map -- ver rama ``if order_usd_total > 0 and tot > 0``.
    """

    def fake_execute(model, method, args, kwargs=None):
        return []

    result = _pagos_odoo_por_orden(
        fake_execute,
        [101],
        {101: "SO1"},
        {"SO1": [_factura(amount_total="18000", amount_residual="9000", currency="VES")]},
        {"SO1": orden("SO1", monto_total="500")},
        {"2026-06-01": 99.0},  # no debería usarse -- hay ratio directo
        30.0,
    )
    # effective_rate = 18000 / 500 = 36; paid_inv = 18000-9000 = 9000 VES
    # -> 9000/36 = 250 USD
    assert result["SO1"]["abono_bcv"] == Decimal("250")
    assert result["SO1"]["abono_binance"] == Decimal("250")


def test_fallback_ves_sin_ratio_usa_rates_map_por_fecha_de_factura():
    """Sin monto_total en la orden (o factura en 0), cae a rates_map por

    fecha de la factura, y a ``last_bcv_val`` si esa fecha no está en
    rates_map."""

    def fake_execute(model, method, args, kwargs=None):
        return []

    result = _pagos_odoo_por_orden(
        fake_execute,
        [101],
        {101: "SO1"},
        {
            "SO1": [
                _factura(
                    amount_total="3600",
                    amount_residual="0",
                    currency="VES",
                    invoice_date="2026-06-01",
                )
            ]
        },
        {"SO1": orden("SO1", monto_total="0")},
        {"2026-06-01": 36.0},
        30.0,
    )
    assert result["SO1"]["abono_bcv"] == Decimal("100")


def test_error_al_consultar_account_payment_no_propaga_y_sigue_al_fallback():
    def fake_execute(model, method, args, kwargs=None):
        raise RuntimeError("Odoo caído")

    result = _pagos_odoo_por_orden(
        fake_execute,
        [101],
        {101: "SO1"},
        {"SO1": [_factura(amount_total="800", amount_residual="300")]},
        {"SO1": orden("SO1", monto_total="800")},
        {},
        0.0,
    )
    assert result["SO1"]["abono_bcv"] == Decimal("500")


def test_ordenes_sin_pago_no_aparecen_en_el_resultado():
    def fake_execute(model, method, args, kwargs=None):
        return []

    result = _pagos_odoo_por_orden(
        fake_execute,
        [101],
        {101: "SO1"},
        {"SO1": [_factura(amount_total="500", amount_residual="500")]},
        {"SO1": orden("SO1", monto_total="500")},
        {},
        0.0,
    )
    assert result == {}
