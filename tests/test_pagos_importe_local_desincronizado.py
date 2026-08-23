"""``_detectar_pagos_con_importe_local_desincronizado`` -- bug real de Odoo

(descrito por el usuario, agosto 2026, con el método de detección exacto
que propuso): editar la fecha o la tasa de un pago YA reconciliado hace
que Odoo recalcule "Importe local" (equivalente en Bs) con la tasa del
DÍA DE LA EDICIÓN, pero el asiento contable ya posteado se queda con el
monto VES viejo. Verificado en vivo (pago 1208, PBNB/2026/00024):
amount_local=22.369,11 Bs vs línea contable debit=23.005,81 Bs.
"""

from __future__ import annotations

from decimal import Decimal

from cxc.web.app import _detectar_pagos_con_importe_local_desincronizado


def test_detecta_importe_local_desincronizado() -> None:
    """Caso real: pago 1208, amount_local != monto de su propia línea

    contable -- diferencia de 636.70 Bs."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "read":
            return [
                {
                    "id": 1208,
                    "name": "PBNB/2026/00024",
                    "amount_local": 22369.11,
                    "move_id": [6292, "x"],
                    "state": "in_process",
                }
            ]
        if model == "account.move.line" and method == "search_read":
            return [{"id": 16638, "move_id": [6292, "x"], "debit": 23005.81}]
        return []

    resultado = _detectar_pagos_con_importe_local_desincronizado(fake_execute, [1208])
    assert len(resultado) == 1
    assert resultado[0]["pago_id"] == "1208"
    assert resultado[0]["importe_local_ves"] == 22369.11
    assert resultado[0]["monto_asiento_ves"] == 23005.81
    assert round(resultado[0]["diferencia_ves"], 2) == -636.70


def test_ignora_pago_ves_con_importe_local_cero() -> None:
    """Verificado en vivo: ~90 pagos en VES (moneda de la compañía) tienen

    amount_local=0 SIEMPRE -- comportamiento normal de Odoo para esa
    moneda, no el bug. No deben generar ruido."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "read":
            return [
                {
                    "id": 1,
                    "name": "PBAMI/2026/00467",
                    "amount_local": 0.0,
                    "move_id": [10, "x"],
                    "state": "in_process",
                }
            ]
        if model == "account.move.line" and method == "search_read":
            # No debería ni llamarse -- el pago se filtra antes por
            # amount_local vacío.
            return [{"id": 100, "move_id": [10, "x"], "debit": 61850.21}]
        return []

    assert _detectar_pagos_con_importe_local_desincronizado(fake_execute, [1]) == []


def test_no_marca_pago_sincronizado() -> None:
    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "read":
            return [
                {
                    "id": 650,
                    "name": "PBNB/2026/00005",
                    "amount_local": 15648782.33,
                    "move_id": [3541, "x"],
                    "state": "in_process",
                }
            ]
        if model == "account.move.line" and method == "search_read":
            return [{"id": 9021, "move_id": [3541, "x"], "debit": 15648782.33}]
        return []

    assert _detectar_pagos_con_importe_local_desincronizado(fake_execute, [650]) == []


def test_ignora_pago_cancelado() -> None:
    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "read":
            return [
                {
                    "id": 2,
                    "name": "P2",
                    "amount_local": 1000.0,
                    "move_id": [11, "x"],
                    "state": "cancel",
                }
            ]
        if model == "account.move.line" and method == "search_read":
            return [{"id": 101, "move_id": [11, "x"], "debit": 500.0}]
        return []

    assert _detectar_pagos_con_importe_local_desincronizado(fake_execute, [2]) == []


def test_solo_toma_la_primera_linea_con_debito_por_asiento() -> None:
    """Un pago con IGTF u otra línea adicional puede traer más de una

    línea con debit > 0 -- se toma la primera (la principal) y se
    ignoran las demás del mismo asiento, sin duplicar el resultado."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "read":
            return [
                {
                    "id": 3,
                    "name": "P3",
                    "amount_local": 100.0,
                    "move_id": [12, "x"],
                    "state": "in_process",
                }
            ]
        if model == "account.move.line" and method == "search_read":
            return [
                {"id": 200, "move_id": [12, "x"], "debit": 110.0},
                {"id": 201, "move_id": [12, "x"], "debit": 3.0},
            ]
        return []

    resultado = _detectar_pagos_con_importe_local_desincronizado(fake_execute, [3])
    assert len(resultado) == 1
    assert resultado[0]["monto_asiento_ves"] == 110.0


def test_respeta_tolerancia_custom() -> None:
    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "read":
            return [
                {
                    "id": 4,
                    "name": "P4",
                    "amount_local": 100.0,
                    "move_id": [13, "x"],
                    "state": "in_process",
                }
            ]
        if model == "account.move.line" and method == "search_read":
            return [{"id": 300, "move_id": [13, "x"], "debit": 105.0}]
        return []

    assert (
        _detectar_pagos_con_importe_local_desincronizado(fake_execute, [4], Decimal("10.00"))
        == []
    )
    resultado = _detectar_pagos_con_importe_local_desincronizado(
        fake_execute, [4], Decimal("1.00")
    )
    assert len(resultado) == 1


def test_sin_execute_o_sin_pago_ids_no_falla() -> None:
    assert _detectar_pagos_con_importe_local_desincronizado(None, [1]) == []
    assert _detectar_pagos_con_importe_local_desincronizado(lambda *a, **k: [], []) == []
