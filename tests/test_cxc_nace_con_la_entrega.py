"""La cuenta por cobrar nace con la entrega.

Criterio del usuario (septiembre 2026), textual: "la cuenta por cobrar
nace con la entrega, la orden, si no ha sido entregada no es susceptible
de cobro. Puede pasar el caso de que se registra primero el pago y luego
la entrega o la misma orden, pero son muy contadas excepciones".

Salió de preguntar si las órdenes sin entregar afectaban los saldos.
Medido contra producción: de 941 órdenes, 161 no tenían nada entregado y
solo 6 llegaban a la tabla de Ventas. De esas, 4 sumaban $6.542,45 a la
cartera sin estar facturadas ni tener un solo pago -- tres eran del día
anterior y una de cuatro días antes, esperando despacho.

Peor: una de ellas (S00952, $3.763,32) tenía saldo en el reporte que
consume el reparto FIFO, así que un pago de ese cliente podía asignarse
automáticamente a mercancía que nunca salió del depósito.

La excepción se respeta con el dinero aplicado: si la orden ya tiene un
abono, sigue contando aunque no se haya despachado. Es lo que mantiene
visible a S00372 -- entregado cero por una DEVOLUCIÓN, no por falta de
despacho, y con $393,85 encima.

Detalle del dato que costó encontrar: en el reporte de saldos,
``cantidad_entregada`` vacía cae a ``cantidad`` (lo pedido). Ese respaldo
es deliberado para calcular el MONTO, pero no sirve para responder "¿se
entregó algo?" -- por eso la guarda mira el campo crudo y trata un vacío
como cero.
"""

from __future__ import annotations

_EPS = 0.005


def _cobrable(entregado: float, pagado: float) -> bool:
    """La regla, aislada tal como quedó en get_ventas."""
    return (entregado > _EPS) or (pagado > _EPS)


def _saldo_para_fifo(saldo: float, entregado_crudo: float, abono: float) -> float:
    """La guarda del reporte de saldos, que es lo que consume el FIFO."""
    if entregado_crudo <= _EPS and abono <= _EPS:
        return 0.0
    return saldo


def test_una_orden_tomada_y_no_despachada_no_es_cobrable() -> None:
    """El caso de S00952: $4.444,01, sin facturar y sin un solo pago."""
    assert _cobrable(entregado=0.0, pagado=0.0) is False


def test_entregada_es_cobrable() -> None:
    assert _cobrable(entregado=10.0, pagado=0.0) is True


def test_una_entrega_parcial_ya_la_hace_cobrable() -> None:
    """La cuenta nace con la entrega, no con la entrega completa."""
    assert _cobrable(entregado=1.0, pagado=0.0) is True


def test_el_pago_anticipado_es_la_excepcion() -> None:
    """"Puede pasar el caso de que se registra primero el pago y luego la
    entrega o la misma orden". Si ya entró dinero, la orden cuenta."""
    assert _cobrable(entregado=0.0, pagado=393.85) is True


def test_el_fifo_no_puede_aplicar_pagos_a_lo_no_despachado() -> None:
    """Sin esta guarda, S00952 aparecía con $3.763,32 en el reporte que
    consume el reparto, así que un pago del cliente podía asignarse solo a
    mercancía que nunca salió."""
    assert _saldo_para_fifo(3763.32, entregado_crudo=0.0, abono=0.0) == 0.0


def test_con_abono_el_fifo_sigue_viendo_la_orden() -> None:
    assert _saldo_para_fifo(555.28, entregado_crudo=0.0, abono=393.85) == 555.28


def test_con_entrega_el_saldo_queda_intacto() -> None:
    assert _saldo_para_fifo(1000.0, entregado_crudo=5.0, abono=0.0) == 1000.0


def test_unas_milesimas_no_cuentan_como_entrega() -> None:
    assert _cobrable(entregado=0.004, pagado=0.0) is False
    assert _cobrable(entregado=0.006, pagado=0.0) is True
