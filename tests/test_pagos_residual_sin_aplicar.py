"""``_detectar_pagos_con_residual_sin_aplicar`` -- bug real (reportado por

el usuario, agosto 2026, cliente TERA INGENIERIA): un pago de $27.781,08
contra una factura de $27.780,60 dejó $0,48 sin ningún destino visible en
Ventas/Cobranza/Reporte de Saldos -- la FACTURA mostraba residual $0
(Odoo cerró la reconciliación desde ese lado), y solo la línea contable
del propio PAGO conservaba el rastro real
(``account.move.line.amount_residual_currency``/``reconciled``).
"""

from __future__ import annotations

from decimal import Decimal

from cxc.web.app import _detectar_pagos_con_residual_sin_aplicar


def test_detecta_residual_sin_aplicar_en_linea_del_pago() -> None:
    """Caso real: pago 650 (PBNB/2026/00005), move 3541, línea AR 9022 con

    amount_residual_currency=-0.48, reconciled=False -- debe salir
    marcado con el monto absoluto ($0.48), sin necesidad de tocar la
    factura para nada."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "read":
            return [
                {
                    "id": 650,
                    "name": "PBNB/2026/00005",
                    "move_id": [3541, "x"],
                    "state": "in_process",
                }
            ]
        if model == "account.move.line" and method == "search_read":
            return [
                {
                    "id": 9022,
                    "move_id": [3541, "x"],
                    "amount_residual_currency": -0.48,
                    "reconciled": False,
                }
            ]
        return []

    resultado = _detectar_pagos_con_residual_sin_aplicar(fake_execute, [650])
    assert len(resultado) == 1
    assert resultado[0]["pago_id"] == "650"
    assert resultado[0]["numero_pago_odoo"] == "PBNB/2026/00005"
    assert resultado[0]["residual_sin_aplicar_usd"] == -0.48


def test_no_marca_pago_ya_reconciliado_del_todo() -> None:
    """``reconciled=True`` -- el pago está genuinamente cerrado, sin importar

    qué diga amount_residual_currency (Odoo lo deja en 0 en ese caso de
    todas formas, pero el chequeo no debe depender solo del monto)."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "read":
            return [{"id": 1, "name": "P1", "move_id": [10, "x"], "state": "in_process"}]
        if model == "account.move.line" and method == "search_read":
            return [
                {
                    "id": 20,
                    "move_id": [10, "x"],
                    "amount_residual_currency": 0.0,
                    "reconciled": True,
                }
            ]
        return []

    assert _detectar_pagos_con_residual_sin_aplicar(fake_execute, [1]) == []


def test_ignora_pago_cancelado() -> None:
    """Pedido explícito del usuario (encontrado auditando el caso real: 100

    asientos de "Ajuste Cambio" cancelados en la muestra) -- un pago
    ``state == "cancel"`` no debe generar alerta aunque su línea contable
    todavía muestre un residual grande (ya no representa dinero real)."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "read":
            return [{"id": 2, "name": "P2", "move_id": [11, "x"], "state": "cancel"}]
        if model == "account.move.line" and method == "search_read":
            # No debería ni llamarse -- el pago cancelado se filtra antes.
            return [
                {
                    "id": 21,
                    "move_id": [11, "x"],
                    "amount_residual_currency": 500.0,
                    "reconciled": False,
                }
            ]
        return []

    assert _detectar_pagos_con_residual_sin_aplicar(fake_execute, [2]) == []


def test_ignora_residual_menor_a_la_tolerancia() -> None:
    """Un centavo de redondeo (bien por debajo de la tolerancia por

    defecto, $0.05) no debe generar ruido en Auditoría."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "read":
            return [{"id": 3, "name": "P3", "move_id": [12, "x"], "state": "in_process"}]
        if model == "account.move.line" and method == "search_read":
            return [
                {
                    "id": 22,
                    "move_id": [12, "x"],
                    "amount_residual_currency": 0.01,
                    "reconciled": False,
                }
            ]
        return []

    assert _detectar_pagos_con_residual_sin_aplicar(fake_execute, [3]) == []


def test_respeta_tolerancia_custom() -> None:
    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "read":
            return [{"id": 4, "name": "P4", "move_id": [13, "x"], "state": "in_process"}]
        if model == "account.move.line" and method == "search_read":
            return [
                {
                    "id": 23,
                    "move_id": [13, "x"],
                    "amount_residual_currency": 2.0,
                    "reconciled": False,
                }
            ]
        return []

    assert _detectar_pagos_con_residual_sin_aplicar(fake_execute, [4], Decimal("5.00")) == []
    resultado = _detectar_pagos_con_residual_sin_aplicar(fake_execute, [4], Decimal("1.00"))
    assert len(resultado) == 1


def test_sin_execute_o_sin_pago_ids_no_falla() -> None:
    assert _detectar_pagos_con_residual_sin_aplicar(None, [1]) == []
    assert _detectar_pagos_con_residual_sin_aplicar(lambda *a, **k: [], []) == []
