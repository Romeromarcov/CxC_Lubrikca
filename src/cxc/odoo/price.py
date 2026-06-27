"""Resolución de precio por pricelist contra Odoo (sección 4.2).

El motor lee el precio REAL del producto en la pricelist que aplica (no
multiplica por un factor). En producción esto consulta Odoo; el mapeo de nombre
lógico de lista (USD/BCV) → id de pricelist en Odoo es parametrizable y se
documenta en SETUP.md (es específico del entorno, como las credenciales).
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from ..decimal_utils import to_decimal
from ..engine.price_resolver import PriceResolver

ExecuteFn = Callable[[str, str, list[Any], dict[str, Any]], Any]


class OdooPriceResolver(PriceResolver):  # pragma: no cover - red externa (Odoo)
    """Lee el precio de un producto en una pricelist vía XML-RPC, con caché.

    ``pricelist_ids`` mapea el nombre lógico de lista del motor (p. ej. "USD",
    "BCV") al id de la ``product.pricelist`` correspondiente en Odoo.
    """

    def __init__(
        self,
        execute: ExecuteFn,
        pricelist_ids: dict[str, int],
    ) -> None:
        self._execute = execute
        self._pricelist_ids = pricelist_ids
        self._cache: dict[tuple[str, str], Decimal] = {}

    def precio(self, producto: str, lista: str) -> Decimal:
        clave = (producto, lista)
        if clave in self._cache:
            return self._cache[clave]
        pricelist_id = self._pricelist_ids[lista]
        # product.pricelist.price_get devuelve {pricelist_id: precio}.
        resultado = self._execute(
            "product.pricelist",
            "price_get",
            [int(pricelist_id), 1.0, [int(producto)]],
            {},
        )
        precio = to_decimal(str(resultado[str(pricelist_id)]))
        self._cache[clave] = precio
        return precio
