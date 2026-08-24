"""``_es_descuento_manual_patron_obsequio_conocido`` -- Check 2 de

``get_auditoria`` ("Descuento Manual No Explicado") no debe flagear una
línea cuyo descuento manual matchea el patrón conocido de "obsequio" que
el negocio ya usa en producción.

Bug real (reportado por el usuario, agosto 2026, orden S00679/factura
5407, producto "[0761] LIGA PARA FRENOS DOT3"): Check 2 marcaba como
"Descuento Manual No Explicado" un producto obsequio con
``discount=99.99%`` en Odoo -- falso positivo, porque
``descuentos_producto`` (DescuentoProducto) está vacía en producción y no
existe ningún mecanismo de regla configurada para "obsequio"; el negocio
depende 100% de este override manual. Confirmado con un barrido en vivo
de Odoo: el patrón "99.99%" aparece 3 veces (S00671/S00674/S00679),
siempre sobre el mismo producto y con el mismo valor exacto -- evidencia
de un mecanismo deliberado. Un 100.0% exacto (visto una sola vez, en
S00336, sobre un producto distinto) NO matchea este patrón a propósito --
una sola ocurrencia con un producto distinto no basta como evidencia de
un mecanismo sistémico, y sigue flageado como antes.
"""

from __future__ import annotations

from decimal import Decimal

from cxc.web.app import _es_descuento_manual_patron_obsequio_conocido


def test_descuento_99_99_por_ciento_matchea_patron_obsequio() -> None:
    """Caso real S00679/S00671/S00674 (LIGA PARA FRENOS DOT3, 99.99%)."""
    assert _es_descuento_manual_patron_obsequio_conocido(Decimal("99.99")) is True


def test_descuento_100_0_por_ciento_no_matchea_patron_obsequio() -> None:
    """Caso real S00336 (producto distinto, 100.0% exacto, una sola

    ocurrencia): sigue tratándose como descuento manual sin explicar."""
    assert _es_descuento_manual_patron_obsequio_conocido(Decimal("100.0")) is False


def test_descuento_parcial_no_matchea_patron_obsequio() -> None:
    """Un descuento manual parcial (p.ej. 15%) es un caso genuinamente

    distinto al patrón de obsequio y debe seguir flageado por Check 2."""
    assert _es_descuento_manual_patron_obsequio_conocido(Decimal("15")) is False


def test_descuento_cero_no_matchea_patron_obsequio() -> None:
    assert _es_descuento_manual_patron_obsequio_conocido(Decimal("0")) is False


def test_limite_inferior_99_9_matchea() -> None:
    assert _es_descuento_manual_patron_obsequio_conocido(Decimal("99.9")) is True


def test_limite_superior_100_exacto_no_matchea() -> None:
    assert _es_descuento_manual_patron_obsequio_conocido(Decimal("100")) is False
