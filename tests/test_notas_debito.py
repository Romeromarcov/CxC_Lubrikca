"""Notas de débito: se leen por ``out_debit`` y AUMENTAN lo que se debe.

Frente 3 del plan de auditoría (septiembre 2026). Caso real:
Agropecuaria Leche y Miel tiene tres notas de débito sobre sus facturas
(643.999,20 · 192.029,45 · 377.753,16 en VES).

Dos cosas que este test fija, porque el código ya se equivocó en ambas:

1. El ``move_type``. Un docstring afirmaba que Odoo no les da tipo propio
   y que vienen como ``out_invoice``. Es falso en esta instancia: usan
   ``out_debit``, con journal dedicado. Con el filtro equivocado NINGUNA
   nota de débito se veía (bug real de la orden S00357, de este mismo
   cliente).

2. La dirección. Una N/D no es una N/C con otro nombre: aumenta la deuda.
   Confundirlas invierte el signo del saldo.
"""

from __future__ import annotations

from cxc.web.app import _leer_notas_debito_odoo

_INV_A_SO = {2455: "S00328", 2456: "S00357", 4151: "S00555"}


def _execute(moves, *, esperado_move_type="out_debit"):
    def execute(model, method, args, kwargs=None):
        dominio = args[0] if args else []
        # Solo devuelve filas si preguntan por el move_type correcto.
        if ["move_type", "=", esperado_move_type] in dominio:
            return moves
        return []

    return execute


def test_lee_las_notas_de_debito_reales() -> None:
    ex = _execute(
        [
            {"debit_origin_id": [4151, "00000192"], "amount_total_signed_usd": 1060.23},
            {"debit_origin_id": [2456, "00000052"], "amount_total_signed_usd": 375.94},
            {"debit_origin_id": [2455, "00000053"], "amount_total_signed_usd": 739.53},
        ]
    )
    assert _leer_notas_debito_odoo(ex, list(_INV_A_SO), _INV_A_SO) == {
        "S00555": 1060.23,
        "S00357": 375.94,
        "S00328": 739.53,
    }


def test_si_buscara_out_invoice_no_encontraria_ninguna() -> None:
    """Reproduce el bug de S00357: con el filtro equivocado el resultado es
    vacío y las notas de débito quedan invisibles."""
    ex = _execute(
        [{"debit_origin_id": [2456, "00000052"], "amount_total_signed_usd": 375.94}],
        esperado_move_type="out_invoice",
    )
    assert _leer_notas_debito_odoo(ex, list(_INV_A_SO), _INV_A_SO) == {}


def test_varias_notas_sobre_la_misma_factura_se_suman() -> None:
    ex = _execute(
        [
            {"debit_origin_id": [2455, "00000053"], "amount_total_signed_usd": 100.0},
            {"debit_origin_id": [2455, "00000053"], "amount_total_signed_usd": 50.0},
        ]
    )
    assert _leer_notas_debito_odoo(ex, list(_INV_A_SO), _INV_A_SO) == {"S00328": 150.0}


def test_se_toma_la_magnitud_aunque_odoo_devuelva_signo() -> None:
    """La N/D suma a la deuda; el signo con que venga no debe invertirla."""
    ex = _execute(
        [{"debit_origin_id": [2455, "00000053"], "amount_total_signed_usd": -739.53}]
    )
    assert _leer_notas_debito_odoo(ex, list(_INV_A_SO), _INV_A_SO) == {"S00328": 739.53}


def test_nota_sobre_una_factura_ajena_se_ignora() -> None:
    ex = _execute(
        [{"debit_origin_id": [9999, "otra"], "amount_total_signed_usd": 500.0}]
    )
    assert _leer_notas_debito_odoo(ex, list(_INV_A_SO), _INV_A_SO) == {}


def test_sin_odoo_no_revienta() -> None:
    assert _leer_notas_debito_odoo(None, list(_INV_A_SO), _INV_A_SO) == {}
    assert _leer_notas_debito_odoo(_execute([]), [], {}) == {}
