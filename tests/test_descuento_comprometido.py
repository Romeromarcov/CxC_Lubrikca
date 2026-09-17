"""El descuento ya prometido no debe inflar la cuenta por cobrar.

Planteo del usuario (septiembre 2026), textual: "casi siempre los
vendedores dan el descuento al cliente, lo que pasa es que por seguridad
yo no permito que modifiquen en Odoo los precios ni apliquen descuentos
directamente, porque como puedes ver hacia el pasado hay muchos
desajustes y errores intencionales o no. Entonces aunque no esté la nota
de crédito creada aún, de qué manera podemos reflejar ese saldo a favor
[...] para no inflar la cuenta por cobrar por un trámite administrativo
que debe hacer el dpto de administración".

Medido contra producción: $14.482,08 en 154 órdenes que siguen en
cobranza, el 5,8% de los $251.380,75 que se perseguían.

El descuento se asume COMPROMETIDO por defecto y las excepciones se marcan
a mano (``descuentos_no_otorgados``). La excepción que lo motivó es TERA:
el motor le calcula $3.949,79 entre S00010 y S00584, pero "a ellos no se
les dio ese descuento, pagaron completo y ya".

Los libros no se tocan: en Odoo la factura sigue en bruto hasta que
administración emita la NC. Lo que cambia es a cuánto sale el cobrador.
"""

from __future__ import annotations


def _comprometido(descuento_pendiente: float, marcada_como_no_otorgada: bool) -> float:
    return 0.0 if marcada_como_no_otorgada else descuento_pendiente


def _cobrable(saldo_facturado: float, comprometido: float) -> float:
    return round(max(0.0, saldo_facturado - comprometido), 2)


def test_el_descuento_se_asume_comprometido() -> None:
    """El caso normal: el vendedor ya se lo prometió al cliente."""
    assert _comprometido(1926.63, marcada_como_no_otorgada=False) == 1926.63


def test_la_excepcion_marcada_no_baja_la_cobranza() -> None:
    """El caso TERA: pagaron completo, no se les dio descuento."""
    assert _comprometido(3949.79, marcada_como_no_otorgada=True) == 0.0


def test_el_cobrador_persigue_lo_neto() -> None:
    """Factura $21.577, descuento prometido $2.589 -> se cobra $18.988."""
    assert _cobrable(21577.0, _comprometido(2589.0, False)) == 18988.0


def test_una_orden_marcada_se_persigue_completa() -> None:
    assert _cobrable(21577.0, _comprometido(2589.0, True)) == 21577.0


def test_el_cobrable_nunca_es_negativo() -> None:
    """Un descuento mal calculado no puede volver la deuda un crédito."""
    assert _cobrable(100.0, 500.0) == 0.0


def test_sin_descuento_el_cobrable_es_el_facturado() -> None:
    assert _cobrable(1000.0, 0.0) == 1000.0


def test_revertir_la_marca_devuelve_el_descuento() -> None:
    """no_otorgado=False vuelve a descontarlo de la cobranza."""
    assert _comprometido(500.0, marcada_como_no_otorgada=False) == 500.0
