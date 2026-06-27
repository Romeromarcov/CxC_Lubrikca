"""Resolución de precio por lista (sección 4.2).

El "35%" NUNCA entra al cálculo. El motor lee el precio REAL del producto en la
pricelist que corresponde (USD o BCV). En producción esto consulta Odoo vía
XML-RPC; en tests se usa ``DictPriceResolver``. Así se elimina la ambigüedad
aritmética (×0.65 ≠ ÷1.35).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal


class PriceResolver(ABC):
    @abstractmethod
    def precio(self, producto: str, lista: str) -> Decimal:
        """Precio unitario del producto en la lista indicada."""


class DictPriceResolver(PriceResolver):
    """Resolver en memoria — ``{(producto, lista): precio}``."""

    def __init__(self, precios: dict[tuple[str, str], Decimal]) -> None:
        self._precios = dict(precios)

    def precio(self, producto: str, lista: str) -> Decimal:
        try:
            return self._precios[(producto, lista)]
        except KeyError as exc:
            raise KeyError(
                f"Sin precio para producto={producto!r} en lista={lista!r}"
            ) from exc

    def set_precio(self, producto: str, lista: str, precio: Decimal) -> None:
        self._precios[(producto, lista)] = precio
