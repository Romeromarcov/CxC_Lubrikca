"""Resolución de precio por pricelist contra Odoo (sección 4.2).

El motor lee el precio REAL del producto en la pricelist que aplica (no
multiplica por un factor). En producción esto consulta Odoo; el mapeo de nombre
lógico de lista (USD/BCV) → id de pricelist en Odoo es parametrizable y se
documenta en SETUP.md (es específico del entorno, como las credenciales).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

from ..decimal_utils import to_decimal
from ..engine.price_resolver import PriceResolver

ExecuteFn = Callable[[str, str, list[Any], dict[str, Any]], Any]


class OdooPriceResolver(PriceResolver):  # pragma: no cover - red externa (Odoo)
    """Lee el precio de un producto en una pricelist vía XML-RPC, con caché.

    ``pricelist_ids`` mapea el nombre lógico de lista del motor (p. ej. "USD",
    "BCV") al id de la ``product.pricelist`` correspondiente en Odoo.

    ⚠️ CALIBRAR PARA ODOO 18 (ver TODO.md): ``price_get`` fue removido en Odoo 18
    y los métodos privados no son invocables por XML-RPC. Para la ruta BCV/VES
    (lista de nacimiento) usar ``DictPriceResolver`` con el ``precio_unitario`` de
    las líneas ya sincronizadas; para la lista USD definir el método real con
    Odoo. Este resolver queda como esqueleto.
    """

    def __init__(
        self,
        execute: ExecuteFn,
        pricelist_ids: dict[str, int],
    ) -> None:
        self._execute = execute
        self._pricelist_ids = pricelist_ids
        # Claves de forma variable: (producto, lista, fecha) en precio(),
        # (producto, "volumen") en volumen().
        self._cache: dict[tuple[str, ...], Decimal] = {}

    def precio(self, producto: str, lista: str, fecha: date | None = None) -> Decimal:
        clave = (producto, lista, fecha.isoformat() if fecha else "sin_fecha")
        if clave in self._cache:
            return self._cache[clave]

        pricelist_id: int | None
        if lista.isdigit():
            pricelist_id = int(lista)
        else:
            pricelist_id = self._pricelist_ids.get(lista)
            if not pricelist_id:
                try:
                    pricelist_id = int(lista)
                except Exception:
                    pricelist_id = self._pricelist_ids.get("USD", 4)

        try:
            prod_id = int(producto)
        except (ValueError, TypeError):
            prod_id = None

        rules = []
        if prod_id:
            try:
                rules = self._execute(
                    "product.pricelist.item",
                    "search_read",
                    [
                        [
                            ["pricelist_id", "=", pricelist_id],
                            ["product_tmpl_id", "=", prod_id],
                            ["compute_price", "=", "fixed"],
                        ]
                    ],
                    {"fields": ["fixed_price", "date_start", "date_end"]},
                )
            except Exception:
                rules = []

        if rules:
            matched = []
            from datetime import datetime

            for r in rules:
                d_start_str = r.get("date_start")
                d_end_str = r.get("date_end")
                d_start = (
                    datetime.strptime(d_start_str[:10], "%Y-%m-%d").date() if d_start_str else None
                )
                d_end = datetime.strptime(d_end_str[:10], "%Y-%m-%d").date() if d_end_str else None

                if fecha:
                    if d_start and fecha < d_start:
                        continue
                    if d_end and fecha > d_end:
                        continue
                p_val = to_decimal(str(r.get("fixed_price") or "0"))
                matched.append((d_start or date.min, p_val))

            if matched:
                matched.sort(key=lambda x: x[0], reverse=True)
                precio = matched[0][1]
            else:
                precio = to_decimal(str(rules[0]["fixed_price"]))
        else:
            # Sin regla de precio fijo para esta pricelist -- usar el campo
            # "Precio de venta $" (list_price_usd) de la ficha del producto,
            # NUNCA "list_price" (esa está en VES, la moneda de la compañía
            # en Odoo -- tratarla como USD infla el precio ~800x). Verificado
            # en vivo: S00700/S00718, producto "GLOBAL MOTORGAS W SAE 40
            # (Tambor)" sin regla en la lista de la orden -- list_price
            # devuelve 1,457,052.51 (VES) vs list_price_usd 1,961.54 (USD).
            precio = Decimal("0.0")
            if prod_id:
                try:
                    prod = self._execute(
                        "product.product", "read", [[prod_id]], {"fields": ["list_price_usd"]}
                    )
                    if prod and isinstance(prod, list) and len(prod) > 0:
                        precio = to_decimal(str(prod[0].get("list_price_usd") or "0.0"))
                except Exception:
                    precio = Decimal("0.0")

        self._cache[clave] = precio
        return precio

    def volumen(self, producto: str) -> Decimal:
        clave = (producto, "volumen")
        if clave in self._cache:
            return self._cache[clave]

        vol = Decimal("0.0")
        try:
            prod_id = int(producto)
            prod = self._execute(
                "product.template", "read", [[prod_id]], {"fields": ["product_volume"]}
            )
            if prod and isinstance(prod, list) and len(prod) > 0:
                vol = to_decimal(str(prod[0].get("product_volume") or "0.0"))
        except Exception:
            vol = Decimal("0.0")

        self._cache[clave] = vol
        return vol
