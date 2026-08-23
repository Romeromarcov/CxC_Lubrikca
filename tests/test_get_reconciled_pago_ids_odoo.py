"""``get_reconciled_pago_ids_odoo`` -- bug real (reportado por el usuario,

agosto 2026, cliente CONSTRUCTORA GRANO AGREGADO/orden S00608): un pago
que ya aplicó una fracción pequeña de su monto a OTRA factura quedaba
marcado como "reconciliado" (``reconciled_invoices_count > 0``) y
desaparecía del todo del universo de sugerencias FIFO, aunque le
sobrara un residual grande y genuinamente disponible (Odoo lo sigue
mostrando en "Outstanding credits" al ver otra factura del mismo
cliente). Ahora se consulta la línea contable propia del pago
(``account.move.line.reconciled`` en la cuenta de Cuentas por Cobrar) --
la misma fuente que ``_detectar_pagos_con_residual_sin_aplicar``.
"""

from __future__ import annotations

from cxc.web.app import get_reconciled_pago_ids_odoo


def test_pago_parcialmente_reconciliado_con_residual_sigue_disponible() -> None:
    """Caso real: pago 1029 (PBAMI/2026/00274) ya aplicó una fracción a

    otra factura, pero su línea AR sigue con reconciled=False -- NO debe
    excluirse."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "search_read":
            return [{"id": 1029, "is_reconciled": False, "state": "paid", "move_id": [5242, "x"]}]
        if model == "account.move.line" and method == "search_read":
            return [{"id": 14110, "move_id": [5242, "x"], "reconciled": False}]
        return []

    assert get_reconciled_pago_ids_odoo(fake_execute, ["1029"]) == set()


def test_pago_totalmente_reconciliado_se_excluye() -> None:
    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "search_read":
            return [{"id": 650, "is_reconciled": True, "state": "paid", "move_id": [3541, "x"]}]
        if model == "account.move.line" and method == "search_read":
            return [{"id": 9022, "move_id": [3541, "x"], "reconciled": True}]
        return []

    assert get_reconciled_pago_ids_odoo(fake_execute, ["650"]) == {"650"}


def test_pago_en_estado_no_confirmado_se_excluye_sin_mirar_linea() -> None:
    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "search_read":
            return [{"id": 5, "is_reconciled": False, "state": "cancel", "move_id": [1, "x"]}]
        # No debería ni llamarse -- se excluye por estado antes de mirar la línea.
        if model == "account.move.line" and method == "search_read":
            return [{"id": 1, "move_id": [1, "x"], "reconciled": False}]
        return []

    assert get_reconciled_pago_ids_odoo(fake_execute, ["5"]) == {"5"}


def test_pago_sin_move_id_cae_a_is_reconciled() -> None:
    """Sin move_id (caso raro) -- no hay línea contable que consultar, se

    usa el campo agregado de Odoo como red de seguridad."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "search_read":
            return [
                {"id": 111, "is_reconciled": True, "state": "paid", "move_id": False},
                {"id": 222, "is_reconciled": False, "state": "in_process", "move_id": False},
            ]
        return []

    resultado = get_reconciled_pago_ids_odoo(fake_execute, ["111", "222"])
    assert resultado == {"111"}


def test_sin_linea_ar_encontrada_para_el_move_no_excluye() -> None:
    """Si no se encuentra la línea AR del move (caso raro) -- mejor

    ofrecer el pago de más que ocultarlo de más."""

    def fake_execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "search_read":
            return [{"id": 7, "is_reconciled": True, "state": "paid", "move_id": [99, "x"]}]
        if model == "account.move.line" and method == "search_read":
            return []
        return []

    assert get_reconciled_pago_ids_odoo(fake_execute, ["7"]) == set()


def test_sin_ids_no_falla() -> None:
    assert get_reconciled_pago_ids_odoo(lambda *a, **k: [], []) == set()
