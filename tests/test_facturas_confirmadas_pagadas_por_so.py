"""``_facturas_confirmadas_pagadas_por_so`` -- bug real (reportado por el

usuario, agosto 2026, auditoría de saldos de CxC): 107 órdenes reales
confirmadas en vivo donde Odoo ya consideraba la factura saldada
(``payment_state`` paid/in_payment/reversed) pero nuestra propia
reconstrucción bottom-up (Vinculaciones, teóricos, saldo de factura
neto) nunca llegó a esa conclusión -- por un hueco de sync, una tasa
mal congelada, o un pago que nunca se vinculó localmente.
"""

from __future__ import annotations

from cxc.web.app import _facturas_confirmadas_pagadas_por_so


def test_todas_las_facturas_pagadas_marca_true() -> None:
    def fake_execute(model, method, args, kwargs=None):
        return [
            {"id": 1, "move_type": "out_invoice", "payment_state": "paid"},
            {"id": 2, "move_type": "out_invoice", "payment_state": "in_payment"},
        ]

    resultado = _facturas_confirmadas_pagadas_por_so(
        fake_execute, [1, 2], {1: "S00001", 2: "S00001"}
    )
    assert resultado == {"S00001": True}


def test_una_factura_sin_pagar_marca_false() -> None:
    """Si la orden tiene DOS facturas y solo una está saldada, no alcanza --

    debe seguir en CxC activa."""

    def fake_execute(model, method, args, kwargs=None):
        return [
            {"id": 1, "move_type": "out_invoice", "payment_state": "paid"},
            {"id": 2, "move_type": "out_invoice", "payment_state": "not_paid"},
        ]

    resultado = _facturas_confirmadas_pagadas_por_so(
        fake_execute, [1, 2], {1: "S00002", 2: "S00002"}
    )
    assert resultado == {"S00002": False}


def test_reversed_cuenta_como_saldada() -> None:
    def fake_execute(model, method, args, kwargs=None):
        return [{"id": 1, "move_type": "out_invoice", "payment_state": "reversed"}]

    resultado = _facturas_confirmadas_pagadas_por_so(fake_execute, [1], {1: "S00003"})
    assert resultado == {"S00003": True}


def test_ignora_notas_de_credito() -> None:
    """out_refund (NC) nunca debe contarse -- solo out_invoice."""

    def fake_execute(model, method, args, kwargs=None):
        return [
            {"id": 1, "move_type": "out_invoice", "payment_state": "not_paid"},
            {"id": 2, "move_type": "out_refund", "payment_state": "paid"},
        ]

    resultado = _facturas_confirmadas_pagadas_por_so(
        fake_execute, [1, 2], {1: "S00004", 2: "S00004"}
    )
    assert resultado == {"S00004": False}


def test_sin_execute_o_sin_ids_no_falla() -> None:
    assert _facturas_confirmadas_pagadas_por_so(None, [1], {1: "S00005"}) == {}
    assert _facturas_confirmadas_pagadas_por_so(lambda *a, **k: [], [], {}) == {}
