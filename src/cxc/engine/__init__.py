"""Pieza 4 — Motor de descuentos (secciones 4.x de la especificación)."""

from .discounts import EngineInputs, calcular_factura
from .price_resolver import DictPriceResolver, PriceResolver

__all__ = [
    "EngineInputs",
    "calcular_factura",
    "PriceResolver",
    "DictPriceResolver",
]
