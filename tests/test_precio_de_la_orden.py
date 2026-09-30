"""Precio de la orden como respaldo (decisión del usuario, 30-sep-2026).

Cuando un producto no tiene precio en una lista VIGENTE para el período de la orden
(sin regla, o con la regla vencida), el precio que quedó en la propia orden se toma
como cierto. Si se pide la otra moneda se convierte con el 35% de diferencial
(VES -> USD: x 0.65; USD -> VES: / 0.65).
"""

from __future__ import annotations

from decimal import Decimal

from cxc.engine.discounts import _precio_unitario_linea
from cxc.engine.price_resolver import DictPriceResolver

from . import builders as b
from .reglas_helpers import LISTA_USD, LISTA_VES, inputs


class _ResolverConFallback(DictPriceResolver):
    """Resolver de prueba que declara como «fallback» los pares indicados."""

    def __init__(self, precios, fallbacks):
        super().__init__(precios)
        self._fallbacks = set(fallbacks)

    def fue_fallback(self, producto, lista):
        return (producto, lista) in self._fallbacks


def _precio(lista_orden, lista_pedida, *, precio_linea="100", precio_lista="999", fallback=True):
    resolver = _ResolverConFallback(
        {("P1", LISTA_VES): Decimal(precio_lista), ("P1", LISTA_USD): Decimal(precio_lista)},
        [("P1", LISTA_VES), ("P1", LISTA_USD)] if fallback else [],
    )
    ln = b.linea(producto="P1", precio=precio_linea)
    inp = inputs(
        orden=b.orden(lista=lista_orden),
        lineas=[ln],
        price_resolver=resolver,
    )
    return _precio_unitario_linea(inp, ln, lista_pedida)


def test_orden_ves_pide_lista_ves_usa_el_precio_de_la_orden():
    assert _precio(LISTA_VES, LISTA_VES) == Decimal("100")


def test_orden_usd_pide_lista_usd_usa_el_precio_de_la_orden():
    assert _precio(LISTA_USD, LISTA_USD) == Decimal("100")


def test_orden_ves_pide_usd_multiplica_por_065():
    assert _precio(LISTA_VES, LISTA_USD) == Decimal("65.00")


def test_orden_usd_pide_ves_divide_entre_065():
    assert _precio(LISTA_USD, LISTA_VES) == Decimal("100") / Decimal("0.65")


def test_con_precio_en_lista_vigente_no_se_toca_nada():
    assert _precio(LISTA_VES, LISTA_VES, fallback=False) == Decimal("999")


def test_linea_sin_precio_conserva_lo_que_resolvio_el_resolvedor():
    assert _precio(LISTA_VES, LISTA_VES, precio_linea="0") == Decimal("999")
