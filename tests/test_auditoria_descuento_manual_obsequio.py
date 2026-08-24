"""``_es_descuento_manual_patron_obsequio_conocido`` -- Check 2 de

``get_auditoria`` ("Descuento Manual No Explicado") no debe flagear una
línea cuyo descuento manual corresponde a una regla de obsequio real y
configurada.

Corrección (agosto 2026, orden S00679/factura 5407, producto "[0761] LIGA
PARA FRENOS DOT3"): una primera versión de este chequeo aceptaba
CUALQUIER descuento en 99.9%-100% como "obsequio", asumiendo que
``descuentos_producto`` (vacía en producción) era el único mecanismo
posible. El usuario señaló que SÍ existe una regla configurada en
Configuración ("Reglas de Obsequio y Promociones") -- la tabla real es
``promocion_primera_compra``. Al investigar se encontró la causa raíz
verdadera: sus 2 reglas activas tenían el campo ``productos`` guardado
como código de catálogo o id de ``product.template`` en vez del
``producto_id`` real (``product.product``) que el motor de descuentos
compara -- la regla nunca tuvo efecto desde que se creó. Se corrigieron
los datos en producción y el selector del formulario. Este chequeo ahora
verifica la regla real (producto_id en una promo activa y vigente para la
fecha de la orden) en vez de confiar ciegamente en el %.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.models import PromocionPrimeraCompra
from cxc.web.app import _es_descuento_manual_patron_obsequio_conocido


def _promo(
    productos: str, vigencia_desde=date(2026, 1, 1), vigencia_hasta=None
) -> PromocionPrimeraCompra:
    return PromocionPrimeraCompra(
        regla_id="PROMO_TEST",
        tipo_beneficio="producto",
        productos=productos,
        vigencia_desde=vigencia_desde,
        vigencia_hasta=vigencia_hasta,
        activo=True,
    )


def test_descuento_99_99_con_producto_en_regla_activa_matchea() -> None:
    """Caso real S00679/S00671/S00674 (LIGA PARA FRENOS DOT3, producto_id

    1033, cubierto por PROMO_NUEVO_GLOBAL una vez corregida)."""
    promos = [_promo("1033,1022")]
    assert (
        _es_descuento_manual_patron_obsequio_conocido(
            Decimal("99.99"), "1033", date(2026, 8, 1), promos
        )
        is True
    )


def test_descuento_99_99_sin_regla_para_ese_producto_no_matchea() -> None:
    """El % por sí solo ya no basta -- el producto tiene que estar

    listado en alguna regla activa. Sin eso, sigue siendo un descuento
    manual sin explicar (el bug que este chequeo debía prevenir)."""
    promos = [_promo("1033,1022")]
    assert (
        _es_descuento_manual_patron_obsequio_conocido(
            Decimal("99.99"), "9999", date(2026, 8, 1), promos
        )
        is False
    )


def test_descuento_100_0_por_ciento_no_matchea_aunque_el_producto_este_en_regla() -> None:
    """Caso real S00336: 100.0% exacto (no 99.99%) no matchea el patrón

    -- ni siquiera si, hipotéticamente, el producto estuviera en una regla."""
    promos = [_promo("1033")]
    assert (
        _es_descuento_manual_patron_obsequio_conocido(
            Decimal("100.0"), "1033", date(2026, 8, 1), promos
        )
        is False
    )


def test_descuento_parcial_no_matchea() -> None:
    promos = [_promo("1033")]
    assert (
        _es_descuento_manual_patron_obsequio_conocido(
            Decimal("15"), "1033", date(2026, 8, 1), promos
        )
        is False
    )


def test_sin_promos_activas_no_matchea() -> None:
    resultado = _es_descuento_manual_patron_obsequio_conocido(
        Decimal("99.99"), "1033", date(2026, 8, 1), []
    )
    assert resultado is False


def test_regla_fuera_de_vigencia_no_matchea() -> None:
    promos = [_promo("1033", vigencia_desde=date(2026, 9, 1))]  # empieza después de la orden
    assert (
        _es_descuento_manual_patron_obsequio_conocido(
            Decimal("99.99"), "1033", date(2026, 8, 1), promos
        )
        is False
    )


def test_regla_vencida_no_matchea() -> None:
    promos = [_promo("1033", vigencia_hasta=date(2026, 6, 30))]
    assert (
        _es_descuento_manual_patron_obsequio_conocido(
            Decimal("99.99"), "1033", date(2026, 8, 1), promos
        )
        is False
    )
