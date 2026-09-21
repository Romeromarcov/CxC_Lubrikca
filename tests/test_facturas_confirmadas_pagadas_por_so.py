"""``_facturas_confirmadas_pagadas_por_so`` -- bug real (reportado por el

usuario, agosto 2026, auditoría de saldos de CxC): 107 órdenes reales
confirmadas en vivo donde Odoo ya consideraba la factura saldada
(``payment_state`` paid/in_payment/reversed) pero nuestra propia
reconstrucción bottom-up (Vinculaciones, teóricos, saldo de factura
neto) nunca llegó a esa conclusión -- por un hueco de sync, una tasa
mal congelada, o un pago que nunca se vinculó localmente.

**Desde el 21-sep-2026 usa ``pagada_unificada`` en vez de su propia
lectura de estado exacto** -- ver el docstring de la función. Dos
consecuencias de ese cambio, ambas fijadas acá con test:

- una factura ``not_paid``/``partial`` con residual real (no cero) sigue
  contando como impaga -- la tolerancia de centavos de ``pagada_unificada``
  no perdona una deuda de verdad, solo centavos;
- una orden cuya ÚNICA factura está ``reversed`` deja de contar como
  "saldada" acá: antes SÍ contaba (bug real -- una factura reversada por
  una nota de crédito no significa que haya entrado un cobro, y el propio
  docstring de ``pagada_en_odoo.py`` lo dice explícitamente).
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


def test_una_factura_sin_pagar_con_deuda_real_marca_false() -> None:
    """Si la orden tiene DOS facturas y solo una está saldada, no alcanza --

    debe seguir en CxC activa. Con un residual real (50 USD, no centavos),
    la tolerancia de ``pagada_unificada`` tampoco la perdona."""

    def fake_execute(model, method, args, kwargs=None):
        return [
            {"id": 1, "move_type": "out_invoice", "payment_state": "paid"},
            {
                "id": 2,
                "move_type": "out_invoice",
                "payment_state": "not_paid",
                "amount_residual_usd": 50.0,
            },
        ]

    resultado = _facturas_confirmadas_pagadas_por_so(
        fake_execute, [1, 2], {1: "S00002", 2: "S00002"}
    )
    assert resultado == {"S00002": False}


def test_una_factura_con_residual_de_centavos_ahora_SI_cuenta_como_saldada() -> None:
    """Antes de unificar, un residual de 0,01 USD dejaba la orden en CxC

    activa para siempre por tres centavos -- lo mismo que ``pagada_unificada``
    ya corrige en Reporte de Saldos desde la decisión 10 del quiz. Caso real
    de producción: S00237, con un residual de 0,01 USD."""

    def fake_execute(model, method, args, kwargs=None):
        return [
            {
                "id": 1,
                "move_type": "out_invoice",
                "payment_state": "partial",
                "amount_residual_usd": 0.01,
            }
        ]

    resultado = _facturas_confirmadas_pagadas_por_so(fake_execute, [1], {1: "S00237"})
    assert resultado == {"S00237": True}


def test_una_factura_sobreaplicada_tambien_cuenta_como_saldada() -> None:
    """Residual NEGATIVO (Odoo aplicó más de lo facturado): no es deuda, es un

    sobrepago -- ``pagada_unificada`` la da por saldada (con la señal
    ``sobreaplicada`` aparte, que esta función no expone porque
    ``clasificar_estado_cxc`` solo necesita el booleano). Caso real de
    producción: S00061, residual de -38,98 USD."""

    def fake_execute(model, method, args, kwargs=None):
        return [
            {
                "id": 1,
                "move_type": "out_invoice",
                "payment_state": "partial",
                "amount_residual_usd": -38.98,
            }
        ]

    resultado = _facturas_confirmadas_pagadas_por_so(fake_execute, [1], {1: "S00061"})
    assert resultado == {"S00061": True}


def test_una_factura_reversada_sola_ya_NO_cuenta_como_saldada() -> None:
    """Corrección de comportamiento (21-sep-2026): antes una factura

    ``reversed`` sola marcaba la orden como saldada -- pero una factura
    reversada por una nota de crédito no significa que haya entrado un
    cobro. ``pagada_unificada`` la excluye de "vivas" y, sin ninguna otra
    factura, no está pagada: está anulada sin reemplazo."""

    def fake_execute(model, method, args, kwargs=None):
        return [{"id": 1, "move_type": "out_invoice", "payment_state": "reversed"}]

    resultado = _facturas_confirmadas_pagadas_por_so(fake_execute, [1], {1: "S00003"})
    assert resultado == {"S00003": False}


def test_ignora_notas_de_credito() -> None:
    """out_refund (NC) nunca debe contarse -- solo out_invoice."""

    def fake_execute(model, method, args, kwargs=None):
        return [
            {
                "id": 1,
                "move_type": "out_invoice",
                "payment_state": "not_paid",
                "amount_residual_usd": 100.0,
            },
            {"id": 2, "move_type": "out_refund", "payment_state": "paid"},
        ]

    resultado = _facturas_confirmadas_pagadas_por_so(
        fake_execute, [1, 2], {1: "S00004", 2: "S00004"}
    )
    assert resultado == {"S00004": False}


def test_sin_execute_o_sin_ids_no_falla() -> None:
    assert _facturas_confirmadas_pagadas_por_so(None, [1], {1: "S00005"}) == {}
    assert _facturas_confirmadas_pagadas_por_so(lambda *a, **k: [], [], {}) == {}
