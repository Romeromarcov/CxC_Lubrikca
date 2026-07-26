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
        self._cache: dict[tuple[str, str], Decimal] = {}

    def precio(self, producto: str, lista: str, fecha: date | None = None) -> Decimal:
        clave = (producto, lista, fecha.isoformat() if fecha else "sin_fecha")
        if clave in self._cache:
            return self._cache[clave]
            
        if lista.isdigit():
            pricelist_id = int(lista)
        else:
            pricelist_id = self._pricelist_ids.get(lista)
            if not pricelist_id:
                try:
                    pricelist_id = int(lista)
                except:
                    pricelist_id = self._pricelist_ids.get("USD", 4)
            
        # Search rules for this product template in Odoo including vigencia dates
        rules = self._execute(
            "product.pricelist.item",
            "search_read",
            [[["pricelist_id", "=", pricelist_id], ["product_tmpl_id", "=", int(producto)], ["compute_price", "=", "fixed"]]],
            {"fields": ["fixed_price", "date_start", "date_end"]}
        )
        
        if rules:
            matched = []
            from datetime import datetime
            for r in rules:
                d_start_str = r.get("date_start")
                d_end_str = r.get("date_end")
                d_start = datetime.strptime(d_start_str[:10], "%Y-%m-%d").date() if d_start_str else None
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
            # Fallback to product.template list_price
            prod = self._execute(
                "product.template",
                "read",
                [int(producto)],
                ["list_price"]
            )
            if prod:
                precio = to_decimal(str(prod[0]["list_price"]))
            else:
                precio = Decimal("0.0")
                
        self._cache[clave] = precio
        return precio

    def volumen(self, producto: str) -> Decimal:
        clave = (producto, "volumen")
        if clave in self._cache:
            return self._cache[clave]
            
        prod = self._execute(
            "product.template",
            "read",
            [int(producto)],
            ["product_volume"]
        )
        if prod:
            vol = to_decimal(str(prod[0].get("product_volume") or "0.0"))
        else:
            vol = Decimal("0.0")
            
        self._cache[clave] = vol
        return vol
