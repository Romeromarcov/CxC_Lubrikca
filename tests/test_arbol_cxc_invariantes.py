"""Invariantes del árbol que decide si una orden sale de cuentas por cobrar.

Frente D del plan de auditoría (septiembre 2026). ``clasificar_estado_cxc``
tiene 14 condiciones, de las cuales 4 se alcanzan por un pago que Odoo
todavía no confirmó (las ``*_incl_pendiente``).

Esas ramas se escribieron cuando una Vinculación PENDIENTE servía solo
para visibilidad y no destrababa ningún descuento. Ese supuesto cambió: hoy
PENDIENTE sí activa Contado y Diferencial. El árbol sigue siendo coherente
porque nunca declara confirmado un pago que no lo está -- y eso es
justamente lo que este test amarra, recorriendo todas las combinaciones en
vez de confiar en la lectura.
"""

from __future__ import annotations

import itertools

from cxc.engine.cxc_routing import BandejaDestino, clasificar_estado_cxc

_CONFIRMADAS = (
    "teorico_bs_pagado",
    "teorico_usd_pagado",
    "factura_real_pagada",
    "venta_real_pagada",
    "factura_pagada_confirmada_odoo",
)
_PENDIENTES = (
    "teorico_bs_pagado_incl_pendiente",
    "teorico_usd_pagado_incl_pendiente",
    "venta_real_pagada_incl_pendiente",
    "factura_real_pagada_incl_pendiente",
)


def _clasificar(**flags):
    base = dict.fromkeys(_CONFIRMADAS + _PENDIENTES, False)
    base.update(flags)
    return clasificar_estado_cxc(so_id="SO1", **base)


def test_un_pago_sin_confirmar_nunca_saca_la_orden_como_confirmada() -> None:
    """El invariante central: si lo único que hay es un pago PENDIENTE y eso

    alcanza para sacar la orden de cuentas por cobrar, la clasificación no
    puede afirmar que el pago está confirmado.

    Se mira solo el caso en que ``sale_de_cxc`` es verdadero: cuando la
    orden se queda en cobranza, ``confirmado`` conserva su valor por
    defecto y no afirma nada -- pasa, por ejemplo, con una orden nacida en
    lista USD evaluada contra el teórico en bolívares, que es una
    combinación que el árbol descarta a propósito.
    """
    for facturada, nacio_usd in itertools.product((True, False), repeat=2):
        for señal in _PENDIENTES:
            r = _clasificar(facturada=facturada, nacio_en_lista_usd=nacio_usd, **{señal: True})
            if r.sale_de_cxc:
                assert r.confirmado is False, (
                    f"{señal} facturada={facturada} nacio_usd={nacio_usd} "
                    "sacó la orden de CxC declarándola confirmada"
                )


def test_sin_ninguna_señal_no_sale_de_cuentas_por_cobrar() -> None:
    for facturada in (True, False):
        r = _clasificar(facturada=facturada)
        assert r.sale_de_cxc is False
        assert r.bandeja_destino is None


def test_una_orden_no_facturada_nunca_va_a_la_bandeja_de_facturadas() -> None:
    """Bandeja 2 es para órdenes ya facturadas; Bandeja 1 para las que
    faltan facturar. Ninguna combinación puede cruzarlas."""
    todas = _CONFIRMADAS + _PENDIENTES
    for señal in todas:
        r = _clasificar(facturada=False, **{señal: True})
        assert r.bandeja_destino is not BandejaDestino.FACTURACION_2, señal
        r2 = _clasificar(facturada=True, **{señal: True})
        assert r2.bandeja_destino is not BandejaDestino.FACTURACION_1, señal


def test_un_pago_confirmado_gana_sobre_uno_pendiente() -> None:
    """Si hay señal confirmada y pendiente a la vez, manda la confirmada."""
    r = _clasificar(
        facturada=True,
        teorico_usd_pagado=True,
        factura_real_pagada_incl_pendiente=True,
    )
    assert r.confirmado is True
    assert r.bandeja_destino is BandejaDestino.FACTURACION_2


def test_toda_clasificacion_que_sale_de_cxc_dice_por_que() -> None:
    """Nunca se saca una orden de cobranza sin dejar el motivo escrito."""
    for señal in _CONFIRMADAS + _PENDIENTES:
        for facturada in (True, False):
            r = _clasificar(facturada=facturada, **{señal: True})
            if r.sale_de_cxc:
                assert r.motivo.strip(), f"{señal} salió de CxC sin motivo"
