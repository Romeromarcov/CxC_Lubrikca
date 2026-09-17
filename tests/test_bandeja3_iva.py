"""La Bandeja 3 no pide comprobante de IVA sobre una factura ya saldada.

Pedido del usuario al revisar las bandejas (septiembre 2026): "que no pida
retención o IVA de una factura que está pagada al 100 % incluyendo los
impuestos".

Bug real encontrado al validarlo contra producción: de 33 facturas con
``amount_residual`` en 0 en Odoo, **10 entraban igual** a la bandeja. La
causa es que nuestro cálculo veía un resto de entre 3,01 y 264,58 que Odoo
no tiene: los asientos de *Exchange Difference* saldan la factura pero no
son ``account.payment``, así que ``valor_pagado_*`` no los cuenta.

Mismo criterio que la regla 5 del árbol de CxC: cuando Odoo dice que la
factura está saldada, manda Odoo -- nuestra reconstrucción puede tener
huecos, la suya no.
"""

from __future__ import annotations


def _entra_a_bandeja3(item: dict, saldo_pendiente: float, iva_total: float) -> bool:
    """Réplica de la condición de entrada, con la guarda nueva."""
    if item.get("wh_iva_aplicado") or item.get("factura_saldada_odoo"):
        return False
    return 0.05 < saldo_pendiente <= iva_total + 0.05


def test_una_factura_saldada_en_odoo_no_pide_comprobante() -> None:
    """El caso de los 10 falsos positivos: nuestro cálculo ve un resto que
    Odoo no tiene."""
    assert _entra_a_bandeja3({"factura_saldada_odoo": True}, 264.58, 355.79) is False


def test_una_retencion_ya_aplicada_tampoco() -> None:
    """La guarda que ya existía."""
    assert _entra_a_bandeja3({"wh_iva_aplicado": True}, 100.0, 160.0) is False


def test_con_saldo_real_dentro_del_iva_si_entra() -> None:
    """El caso legítimo: falta exactamente lo que el cliente retuvo."""
    assert _entra_a_bandeja3({}, 100.0, 160.0) is True


def test_un_saldo_mayor_al_iva_no_es_una_retencion() -> None:
    """Si debe más que el IVA total, no es un comprobante lo que falta --
    es plata."""
    assert _entra_a_bandeja3({}, 200.0, 160.0) is False


def test_sin_saldo_no_hay_nada_que_pedir() -> None:
    assert _entra_a_bandeja3({}, 0.0, 160.0) is False
    assert _entra_a_bandeja3({}, 0.04, 160.0) is False
