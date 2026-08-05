"""``get_live_pagos_conciliados`` -- bug real (orden S00010, ~12% de los

pagos en producción): Odoo distingue "conciliado con extracto bancario"
(``is_reconciled``) de "aplicado a una factura vía Registrar Pago"
(``reconciled_invoice_ids`` poblado, factura con ``amount_residual=0``).
El filtro anterior exigía ``is_reconciled=True`` en el dominio y excluía
pagos genuinamente aplicados cuyo ``is_reconciled`` calculado da False.
"""

from __future__ import annotations

from cxc.web.app import get_live_pagos_conciliados


def _fake_execute_pago_no_bancario(model, method, args, kwargs=None):
    if model == "account.payment":
        return [
            {
                "id": 650,
                "partner_id": [202, "TERA INGENIERIA"],
                "amount": 27781.08,
                "amount_ref": 27781.08,
                "amount_available_for_refund": 0.0,
                "currency_id": [1, "USD"],
                "journal_id": [5, "Transferencia"],
                "date": "2026-06-08",
                # Caso real S00010: is_reconciled=False pero SÍ está
                # vinculado a la factura (reconciled_invoice_ids poblado).
                "reconciled_invoice_ids": [1359],
                "is_reconciled": False,
                "state": "in_process",
            }
        ]
    if model == "account.move":
        return [
            {
                "id": 1359,
                "name": "00000167",
                "invoice_origin": "S00010",
                "move_type": "out_invoice",
                "state": "posted",
                "amount_total_signed_usd": 27780.56,
                "amount_residual_usd": 0.0,
            }
        ]
    if model == "res.partner":
        return [{"id": 202, "user_id": False}]
    return []


def test_get_live_pagos_conciliados_incluye_pago_sin_is_reconciled() -> None:
    pagos = get_live_pagos_conciliados(_fake_execute_pago_no_bancario)
    assert len(pagos) == 1
    pago = pagos[0]
    assert pago["pago_id"] == "650"
    assert pago["so_ids"] == ["S00010"]
    assert pago["monto_conciliado_usd"] == 27781.08


def _fake_execute_pago_no_vinculado(model, method, args, kwargs=None):
    if model == "account.payment":
        return [
            {
                "id": 999,
                "partner_id": [202, "X"],
                "amount": 100.0,
                "amount_ref": 100.0,
                "amount_available_for_refund": 100.0,
                "currency_id": [1, "USD"],
                "journal_id": [5, "Transferencia"],
                "date": "2026-06-08",
                # Sin factura vinculada -- debe seguir excluido.
                "reconciled_invoice_ids": [],
                "is_reconciled": False,
                "state": "in_process",
            }
        ]
    return []


def test_get_live_pagos_conciliados_excluye_pago_sin_factura_vinculada() -> None:
    pagos = get_live_pagos_conciliados(_fake_execute_pago_no_vinculado)
    assert pagos == []
