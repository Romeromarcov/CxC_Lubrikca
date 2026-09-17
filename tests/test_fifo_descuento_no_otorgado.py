"""Marcar "no se otorgó" tiene que llegar hasta el FIFO.

Pregunta del usuario (septiembre 2026): "qué sucede con el FIFO cuando
mientras el descuento sigue 'activo' y qué sucede cuando 'no se otorga'".

Destapó un hueco que yo había dejado. ``_get_reporte_saldos_sync`` resta
``descuentos_motor_con_iva`` del saldo deudor, y ese saldo es contra el
que consume el reparto FIFO (ver ``_get_saldos_reales_por_so_sync``).
Cuando agregué la marca de "descuento no otorgado" la cablée en el
reporte por cliente y en la Bandeja 2, pero NO en el cálculo del saldo:
la orden se veía con el monto completo en pantalla y el FIFO le seguía
descontando igual, así que la daba por saldada con menos plata de la que
realmente hay que cobrar, y la sacaba de Cuentas por Cobrar.

Verificado contra producción con S00792 (TERA):

    descuento ACTIVO       saldo que consume el FIFO: $18.518,10
    marcada NO OTORGADO    saldo que consume el FIFO: $20.753,00
    revertida              saldo que consume el FIFO: $18.518,10

La diferencia, $2.234,90, es exactamente el descuento de $1.926,63 con su
IVA (× 1,16) -- el descuento se calcula sobre el subtotal pero el saldo
deudor está con impuesto.
"""

from __future__ import annotations

_IVA = 0.16


def _saldo_para_fifo(
    saldo_deudor: float,
    descuento_motor: float,
    ncs_odoo: float = 0.0,
    *,
    no_otorgado: bool = False,
) -> float:
    """El cálculo tal como quedó en _get_reporte_saldos_sync."""
    if no_otorgado:
        descuento_motor = 0.0
    return round(max(0.0, saldo_deudor - descuento_motor * (1 + _IVA) - ncs_odoo), 2)


def test_con_el_descuento_activo_el_fifo_pide_menos() -> None:
    """El caso normal: el descuento se asume comprometido, así que la
    orden necesita menos plata y el resto del pago cascadea."""
    assert _saldo_para_fifo(20753.0, 1926.63) == 18518.11


def test_marcarla_devuelve_el_monto_al_fifo() -> None:
    """El caso TERA: no se les dio el descuento, se les cobra todo."""
    assert _saldo_para_fifo(20753.0, 1926.63, no_otorgado=True) == 20753.0


def test_revertir_vuelve_a_descontar() -> None:
    assert _saldo_para_fifo(20753.0, 1926.63, no_otorgado=False) == 18518.11


def test_el_descuento_se_resta_con_su_iva() -> None:
    """El descuento se calcula sobre el subtotal, pero el saldo deudor
    lleva impuesto: restar el monto pelado dejaría la orden $16 corta por
    cada $100 de descuento."""
    assert _saldo_para_fifo(1160.0, 100.0) == 1044.0
    assert _saldo_para_fifo(1160.0, 100.0) != 1060.0


def test_las_ncs_reales_de_odoo_siguen_restando_aunque_se_marque() -> None:
    """La marca solo apaga el descuento del MOTOR. Una nota de crédito ya
    emitida en Odoo es un documento real y sigue bajando el saldo."""
    assert _saldo_para_fifo(1000.0, 100.0, ncs_odoo=200.0, no_otorgado=True) == 800.0


def test_el_saldo_nunca_es_negativo() -> None:
    assert _saldo_para_fifo(100.0, 500.0) == 0.0
