"""``_detectar_ajustes_cambio_huerfanos`` -- bug real de Odoo (reportado por

el usuario, agosto 2026): al desvincular un pago de una factura (romper la
conciliación), el asiento de "Ajuste Cambio" (journal Exchange Difference)
que Odoo generó para esa reconciliación NO se cancela automáticamente --
queda "posted", restando un residual que ya no corresponde a ninguna
reconciliación real. Verificado en vivo (factura 00000522/S00682): la
factura sigue genuinamente pendiente (payment_state="partial", $59,99
por cobrar) y el pago que el ajuste dice haber cubierto ya ni existe --
solo la línea contable del propio Ajuste Cambio (reconciled=False)
conserva el rastro.
"""

from __future__ import annotations

from cxc.web.app import _detectar_ajustes_cambio_huerfanos


def test_detecta_ajuste_cambio_huerfano_por_linea_sin_reconciliar() -> None:
    """Caso real: move 7168 ("Ajuste Cambio 00000522 | ..."), línea AR sin

    reconciliar -- debe salir marcado con so_id resuelto vía el mapa
    factura_numero -> so_id."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.move" and method == "search_read":
            return [
                {
                    "id": 7168,
                    "name": "00000633",
                    "ref": (
                        "Ajuste Cambio 00000522 | PUSD1/2026/00601 "
                        "Pago: 227.00$ Monto: 227.00$ Cambi: 7.03$ TOTAL: 234.03$"
                    ),
                    "date": "2026-06-01",
                }
            ]
        if model == "account.move.line" and method == "search_read":
            return [
                {
                    "id": 900,
                    "move_id": [7168, "x"],
                    "amount_residual_currency": 5316.66,
                    "reconciled": False,
                }
            ]
        return []

    resultado = _detectar_ajustes_cambio_huerfanos(
        fake_execute, {"00000522": "S00682"}
    )
    assert len(resultado) == 1
    assert resultado[0]["move_id"] == 7168
    assert resultado[0]["factura_numero"] == "00000522"
    assert resultado[0]["so_id"] == "S00682"
    assert resultado[0]["residual_ves"] == 5316.66


def test_no_marca_ajuste_ya_reconciliado() -> None:
    """``reconciled=True`` -- el ajuste sigue amarrado a una reconciliación

    real, sin importar el monto del residual."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.move" and method == "search_read":
            return [
                {
                    "id": 1,
                    "name": "00000001",
                    "ref": "Ajuste Cambio 00000100 | P1 Pago: 100.00$ Monto: 100.00$",
                    "date": "2026-06-01",
                }
            ]
        if model == "account.move.line" and method == "search_read":
            return [
                {
                    "id": 10,
                    "move_id": [1, "x"],
                    "amount_residual_currency": 0.0,
                    "reconciled": True,
                }
            ]
        return []

    assert _detectar_ajustes_cambio_huerfanos(fake_execute, {"00000100": "S1"}) == []


def test_ignora_facturas_no_rastreadas() -> None:
    """Si la factura del ref no está en nuestro mapa local (nunca

    sincronizada), el ajuste igual se reporta -- con so_id=None -- en vez
    de descartarse silenciosamente (es justo el tipo de hallazgo contable
    que vale la pena mostrar aunque no podamos ligarlo a una orden)."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.move" and method == "search_read":
            return [
                {
                    "id": 2,
                    "name": "00000002",
                    "ref": "Ajuste Cambio 99999999 | P2 Pago: 50.00$ Monto: 50.00$",
                    "date": "2026-06-01",
                }
            ]
        if model == "account.move.line" and method == "search_read":
            return [
                {
                    "id": 11,
                    "move_id": [2, "x"],
                    "amount_residual_currency": 300.0,
                    "reconciled": False,
                }
            ]
        return []

    resultado = _detectar_ajustes_cambio_huerfanos(fake_execute, {})
    assert len(resultado) == 1
    assert resultado[0]["factura_numero"] == "99999999"
    assert resultado[0]["so_id"] is None


def test_sin_ajustes_no_llama_a_move_line() -> None:
    calls = []

    def fake_execute(model, method, args, kwargs=None):
        calls.append(model)
        if model == "account.move" and method == "search_read":
            return []
        return []

    assert _detectar_ajustes_cambio_huerfanos(fake_execute, {}) == []
    assert "account.move.line" not in calls


def test_sin_execute_no_falla() -> None:
    assert _detectar_ajustes_cambio_huerfanos(None, {}) == []
