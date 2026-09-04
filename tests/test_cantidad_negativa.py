"""Una línea devuelta aporta cero al teórico, nunca un monto en contra.

Bug real reportado por el usuario (septiembre 2026, orden S00952
"PROYECTOS Y DESARROLLO TOMTOM"): la bandeja mostraba un teórico de 206,88
para una orden de 4 tambores de TERMOGLOB, y con él un descuento a aplicar
de 16,55 (0,4 %).

La causa: la orden tenía una segunda línea (HIDRAGLOB) devuelta entera y
además editada a cantidad 0 en Odoo, que quedó con ``cantidad_entregada =
-4``. Como la orden está entregada completa y tiene devolución,
``_cantidad_efectiva`` devolvía esa cantidad tal cual, así que la línea
entraba al teórico con -4 unidades a 1.421,75 -- restando 5.687,00:

    5.893,88  (4 x 1.473,47, la línea real)
   -5.687,00  (-4 x 1.421,75, la línea devuelta)
   ---------
      206,88

La devolución ya se refleja en que esa mercancía no se factura. Convertirla
además en un crédito contra las otras líneas la cuenta dos veces y hunde el
teórico, que es la referencia con la que se calculan los descuentos.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from cxc.engine.discounts import _cantidad_efectiva
from cxc.models import LineaOrden


def _linea(cantidad, entregada):
    return LineaOrden(
        linea_id="L1",
        so_id="S00952",
        producto="945",
        marca="Sinoco",
        categoria="TAMBOR",
        cantidad=Decimal(cantidad),
        precio_unitario=Decimal("1421.75"),
        cantidad_entregada=Decimal(entregada),
    )


def _inp(entregada_completa, tiene_devolucion):
    return SimpleNamespace(
        orden=SimpleNamespace(
            entregada_completa=entregada_completa, tiene_devolucion=tiene_devolucion
        )
    )


def test_una_entrega_negativa_no_resta_del_teorico() -> None:
    """El caso exacto de S00952."""
    assert _cantidad_efectiva(_inp(True, True), _linea("0", "-4")) == Decimal("0")


def test_una_cantidad_pedida_negativa_tampoco() -> None:
    assert _cantidad_efectiva(_inp(False, False), _linea("-4", "0")) == Decimal("0")


def test_una_devolucion_parcial_conserva_lo_que_si_quedo() -> None:
    """Se pidieron 10 y quedaron 6 tras la devolución: se cobran 6."""
    assert _cantidad_efectiva(_inp(True, True), _linea("10", "6")) == Decimal("6")


def test_sin_devolucion_manda_la_cantidad_pedida() -> None:
    """Lubrikca factura antes de despachar, así que ``qty_delivered`` puede
    ser 0 mientras la orden es perfectamente cobrable."""
    assert _cantidad_efectiva(_inp(False, False), _linea("4", "0")) == Decimal("4")
    assert _cantidad_efectiva(_inp(True, False), _linea("4", "0")) == Decimal("4")


def test_entregada_completa_sin_devolucion_no_mira_la_entrega() -> None:
    assert _cantidad_efectiva(_inp(True, False), _linea("4", "-4")) == Decimal("4")
