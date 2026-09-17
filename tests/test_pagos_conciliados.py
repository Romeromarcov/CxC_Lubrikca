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


# --- factura consolidada: invoice_origin nombra dos órdenes (17-sep-2026) -------


def _fake_execute_factura_consolidada(model, method, args, kwargs=None):
    if model == "account.payment":
        return [
            {
                "id": 1866,
                "partner_id": [300, "Cliente Consolidado"],
                "amount": 36616286.07,
                "amount_ref": 43476.60,
                "amount_available_for_refund": 0.0,
                "currency_id": [166, "VES"],
                "journal_id": [5, "Banco"],
                "date": "2026-09-15",
                "reconciled_invoice_ids": [900],
                "is_reconciled": True,
                "state": "in_process",
            }
        ]
    if model == "account.move":
        return [
            {
                "id": 900,
                "name": "00000900",
                # Factura armada consolidando dos órdenes: el caso real que
                # tumbaba el sync cada cinco minutos.
                "invoice_origin": "S00718, S00700",
                "move_type": "out_invoice",
                "state": "posted",
                "amount_total_signed_usd": 43476.60,
                "amount_residual_usd": 0.0,
            }
        ]
    if model == "res.partner":
        return [{"id": 300, "user_id": False}]
    return []


def test_una_factura_consolidada_expande_so_ids_en_vez_de_un_string_con_coma() -> None:
    """Sin esto, ``so_ids`` era ``["S00718, S00700"]`` -- UN elemento, así que

    ``_resincronizar_vinculaciones_con_odoo`` lo trataba como el caso "una sola
    orden, sin ambigüedad" y escribía ese string en ``Vinculacion.so_id``, que
    tiene clave foránea contra ``ordenes_venta``. Con las dos órdenes separadas,
    el conjunto tiene 2 elementos y esa función cae en "discrepancia_multi_orden"
    (audita, no escribe).
    """
    (pago,) = get_live_pagos_conciliados(_fake_execute_factura_consolidada)
    assert pago["so_ids"] == ["S00700", "S00718"]
    assert not any("," in so for so in pago["so_ids"])
