"""Una orden devuelta entera y sin pagar no es venta ni deuda.

Reportado por el usuario (septiembre 2026) al ver tres órdenes de "Mini
Market Las Mercedes" en la Bandeja de Facturación: "no están pagadas, y
además eso fue una devolución, el cliente devolvió la mercancía".

La mercancía volvió al almacén y no hay nada que cobrar ni que facturar.
En Odoo se ve como un ``incoming`` en "done" encima del ``outgoing``
original, y todas las líneas quedan con cantidad entregada en cero.

Medido en producción antes del cambio: 4 órdenes por 7.829,04 figurando
como cuentas por cobrar sin que nadie debiera nada -- S00620, S00098 y
S00368 de Mini Market, más S00708 de Carlos Ruiz.

La salvaguarda importa tanto como la regla: si el cliente SÍ pagó y
después devolvió, la orden NO se esconde. Ahí el saldo va al revés y es la
empresa la que debe un reembolso -- taparlo sería peor que el problema
original.
"""

from __future__ import annotations

from types import SimpleNamespace

from cxc.web.app import orden_devuelta_por_completo, venta_real_neta_de_devolucion


def _orden(entregada=True, devuelta=True):
    return SimpleNamespace(entregada_completa=entregada, tiene_devolucion=devuelta)


def _lineas(*entregadas):
    return [SimpleNamespace(cantidad_entregada=q) for q in entregadas]


def test_devuelta_entera_y_sin_pagar_se_excluye() -> None:
    """El caso de Mini Market: 15 líneas, todas en cero."""
    assert orden_devuelta_por_completo(_orden(), _lineas(*([0.0] * 15))) is True


def test_una_devolucion_que_deja_saldo_negativo_tambien_cuenta() -> None:
    """Odoo puede netear la devolución dejando la cantidad en negativo."""
    assert orden_devuelta_por_completo(_orden(), _lineas(-4.0, 0.0)) is True


def test_si_el_cliente_pago_la_orden_sigue_visible() -> None:
    """Ahí el saldo va al revés: la empresa le debe un reembolso."""
    assert (
        orden_devuelta_por_completo(_orden(), _lineas(0.0, 0.0), tiene_abonos=True) is False
    )


def test_una_devolucion_parcial_no_se_excluye() -> None:
    """Quedó mercancía en manos del cliente: eso sí se cobra."""
    assert orden_devuelta_por_completo(_orden(), _lineas(0.0, 3.0)) is False


def test_sin_devolucion_registrada_no_aplica() -> None:
    """Cantidad entregada en cero sin devolución es una orden que todavía
    no se despachó -- Lubrikca factura antes de despachar."""
    assert orden_devuelta_por_completo(_orden(devuelta=False), _lineas(0.0, 0.0)) is False


def test_sin_entrega_completa_no_aplica() -> None:
    assert orden_devuelta_por_completo(_orden(entregada=False), _lineas(0.0)) is False


def test_una_orden_sin_lineas_no_se_excluye_por_esta_via() -> None:
    """No hay evidencia de devolución total: se deja que la vean."""
    assert orden_devuelta_por_completo(_orden(), []) is False


# --- Devoluciones parciales -------------------------------------------------
#
# "Al implementar la regla resta las devoluciones parciales también, no solo
# si el cliente devuelve toda la orden" (usuario, septiembre 2026).
#
# ``amount_untaxed`` de Odoo NO neta la devolución: sigue contando la
# mercancía que volvió al almacén. Verificado en S00628 -- el tambor SINOCO
# (815,11) figura en el total aunque su ``qty_delivered`` volvió a 0 y existe
# el picking de retorno ``ALM/STOR/00132``, con la misma fecha en que se
# modificó la orden. El teórico sí la restaba desde siempre, así que las dos
# referencias del árbol de CxC medían cosas distintas.


def _linea(cantidad, entregada, precio):
    return SimpleNamespace(
        cantidad=cantidad, cantidad_entregada=entregada, precio_unitario=precio
    )


def test_una_devolucion_parcial_se_resta_del_monto_real() -> None:
    """Caso S00628: se devolvió el tambor de 815,11 y quedó el resto."""
    lineas = [_linea(1, 0, 815.11), _linea(1, 1, 50.21), _linea(1, 1, 57.22)]
    assert venta_real_neta_de_devolucion(_orden(), lineas, 922.54) == 107.43


def test_una_devolucion_total_deja_el_monto_en_cero() -> None:
    lineas = [_linea(2, 0, 100.0), _linea(1, 0, 50.0)]
    assert venta_real_neta_de_devolucion(_orden(), lineas, 250.0) == 0.0


def test_una_orden_ya_modificada_da_el_mismo_monto_que_antes() -> None:
    """10 de las 23 órdenes con devolución están así: Odoo ya ajustó las
    líneas, entregado y pedido coinciden."""
    lineas = [_linea(4, 4, 957.76)]
    assert venta_real_neta_de_devolucion(_orden(), lineas, 3831.04) == 3831.04


def test_sin_devolucion_se_respeta_el_monto_de_odoo() -> None:
    """Lubrikca factura antes de despachar: una orden sin entregar todavía
    es perfectamente cobrable y su cantidad entregada en 0 no significa
    nada."""
    lineas = [_linea(4, 0, 957.76)]
    assert venta_real_neta_de_devolucion(_orden(devuelta=False), lineas, 3831.04) == 3831.04


def test_una_cantidad_entregada_negativa_no_resta_de_las_demas() -> None:
    """Mismo criterio que ``_cantidad_efectiva``: una línea devuelta aporta
    cero, nunca un monto en contra."""
    lineas = [_linea(0, -4, 1421.75), _linea(4, 4, 1473.47)]
    assert venta_real_neta_de_devolucion(_orden(), lineas, 5893.88) == 5893.88


def test_sin_lineas_se_respeta_el_monto_de_odoo() -> None:
    assert venta_real_neta_de_devolucion(_orden(), [], 500.0) == 500.0


def test_una_linea_que_no_pidio_nada_no_es_un_faltante_de_despacho() -> None:
    """Falso positivo real del detector de devoluciones no reflejadas.

    En producción hay dos líneas así -- S00952 y S00925 -- con
    ``cantidad = 0`` y ``cantidad_entregada`` NEGATIVA: se crearon solo
    para registrar la devolución. El detector las leía como "faltan 4" y
    "faltan 10" unidades que nunca se pidieron, y las sumaba a los 112
    hallazgos de la bandeja.
    """
    from decimal import Decimal
    from types import SimpleNamespace

    from cxc.web.app import _detectar_devolucion_no_reflejada_en_cantidad

    orden = SimpleNamespace(so_id="S00952", tiene_devolucion=True)
    linea_solo_devolucion = SimpleNamespace(
        so_id="S00952",
        linea_id="2505",
        producto="0141",
        nombre="HIDRAGLOB AW/ISO 46",
        cantidad=Decimal("0"),
        cantidad_entregada=Decimal("-4"),
        precio_unitario=Decimal("100"),
    )
    assert _detectar_devolucion_no_reflejada_en_cantidad(
        [orden], [linea_solo_devolucion], []
    ) == []


def test_un_faltante_real_se_sigue_reportando() -> None:
    """La contracara: pidió 10, se entregaron 6, faltan 4."""
    from decimal import Decimal
    from types import SimpleNamespace

    from cxc.web.app import _detectar_devolucion_no_reflejada_en_cantidad

    orden = SimpleNamespace(so_id="S00133", tiene_devolucion=True)
    linea = SimpleNamespace(
        so_id="S00133",
        linea_id="1",
        producto="0877",
        nombre="SS EXTRA PROTECCION",
        cantidad=Decimal("10"),
        cantidad_entregada=Decimal("6"),
        precio_unitario=Decimal("100"),
    )
    r = _detectar_devolucion_no_reflejada_en_cantidad([orden], [linea], [])
    assert len(r) == 1
    assert float(r[0]["faltante"]) == 4.0
