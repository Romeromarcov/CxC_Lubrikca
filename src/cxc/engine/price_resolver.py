"""Resolución de precio por lista (sección 4.2).

El "35%" NUNCA entra al cálculo. El motor lee el precio REAL del producto en la
pricelist que corresponde (USD o BCV). En producción esto consulta Odoo vía
XML-RPC; en tests se usa ``DictPriceResolver``. Así se elimina la ambigüedad
aritmética (×0.65 ≠ ÷1.35).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal


class PriceResolver(ABC):
    @abstractmethod
    def precio(self, producto: str, lista: str, fecha: date | None = None) -> Decimal:
        """Precio unitario del producto en la lista indicada."""

    @abstractmethod
    def volumen(self, producto: str) -> Decimal:
        """Volumen en litros del producto template."""

    def fue_fallback(self, producto: str, lista: str) -> bool:
        """True si el último ``precio()`` resuelto para (producto, lista)

        NO vino de una regla fija en ``lista`` -- vino de otra pricelist
        configurada como respaldo, o del precio de venta $ de la ficha del
        producto (Fase 10: señal para saber cuándo un teórico guardado en
        ``ventas_teoricos`` necesita re-verificarse -- si esa lista se
        completa más tarde con el precio faltante). Default False: los
        resolvers en memoria (``DictPriceResolver``, usado en tests) no
        tienen esa ambigüedad -- cada (producto, lista) mapea a un único
        precio explícito.
        """
        return False


class DictPriceResolver(PriceResolver):
    """Resolver en memoria — ``{(producto, lista): precio}``."""

    def __init__(
        self, precios: dict[tuple[str, str], Decimal], volumenes: dict[str, Decimal] | None = None
    ) -> None:
        self._precios = dict(precios)
        self._volumenes = dict(volumenes or {})

    def precio(self, producto: str, lista: str, fecha: date | None = None) -> Decimal:
        try:
            return self._precios[(producto, lista)]
        except KeyError as exc:
            raise KeyError(f"Sin precio para producto={producto!r} en lista={lista!r}") from exc

    def volumen(self, producto: str) -> Decimal:
        return self._volumenes.get(producto, Decimal("0"))

    def set_precio(self, producto: str, lista: str, precio: Decimal) -> None:
        self._precios[(producto, lista)] = precio

    def set_volumen(self, producto: str, vol: Decimal) -> None:
        self._volumenes[producto] = vol
