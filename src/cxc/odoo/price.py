"""Resolución de precio por pricelist contra Odoo (sección 4.2).

El motor lee el precio REAL del producto en la pricelist que aplica (no
multiplica por un factor). En producción esto consulta Odoo; el mapeo de nombre
lógico de lista (USD/BCV) → id de pricelist en Odoo es parametrizable y se
documenta en SETUP.md (es específico del entorno, como las credenciales).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

from ..decimal_utils import to_decimal
from ..engine.price_resolver import PriceResolver

ExecuteFn = Callable[[str, str, list[Any], dict[str, Any]], Any]

# Caché COMPARTIDA a nivel de módulo (agosto 2026, Fase 2 del plan de
# rendimiento del usuario -- hallazgo real: el caché anterior era un dict
# por INSTANCIA, y ``recalculate_all_orders`` crea una instancia nueva de
# ``OdooPriceResolver`` en CADA ciclo (~cada 5 min) -- el caché moría con
# la instancia, así que cada ciclo repetía la consulta de precio a Odoo
# para CADA producto x lista, aunque las reglas de pricelist casi nunca
# cambian. TTL largo (horas, no minutos) porque no hay ninguna razón de
# negocio para que un precio fijo de pricelist cambie más seguido que eso.
_SHARED_PRICE_CACHE: dict[tuple[str, ...], tuple[Decimal, float]] = {}
_SHARED_VOLUMEN_CACHE: dict[str, tuple[Decimal, float]] = {}
_SHARED_CACHE_TTL_SECONDS = 6 * 3600.0


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
        fallback_pricelist_ids: list[int] | None = None,
    ) -> None:
        self._execute = execute
        self._pricelist_ids = pricelist_ids
        # Tarea 4 (auditoria saldos teoricos): ambas listas (USD y VES/BCV)
        # están fijadas en USD por definición de negocio -- si la lista
        # asignada a la orden no tiene una regla de precio fijo para el
        # producto (item nunca creado en esa pricelist puntual), se prueba
        # con las demás pricelists configuradas en Configuración antes de
        # rendirse a `list_price_usd` (que en la práctica suele estar en 0
        # para productos que Odoo nunca vendió por esa lista -- ver caso real
        # S00754/producto 1655: sin regla en pricelist 5, con regla en 8,
        # list_price_usd=0.0). Root cause del bug de "teórico = 0".
        self._fallback_pricelist_ids = list(fallback_pricelist_ids or [])
        # Claves de forma variable: (producto, lista, fecha) en precio(),
        # (producto, "volumen") en volumen().
        self._cache: dict[tuple[str, ...], Decimal] = {}
        # (producto, lista) -> True si el último precio() resuelto para ese
        # par NO vino de una regla fija en `lista` -- ver fue_fallback().
        self._fallback_flags: dict[tuple[str, str], bool] = {}

    def _precio_fijo_en_lista(
        self, pricelist_id: int, prod_id: int, fecha: date | None
    ) -> Decimal | None:
        """Precio de la regla ``compute_price=fixed`` vigente a ``fecha`` en
        ``pricelist_id`` para ``prod_id``, o ``None`` si no hay ninguna."""
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

        if not rules:
            return None

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
            return matched[0][1]
        return to_decimal(str(rules[0]["fixed_price"]))

    def precio(self, producto: str, lista: str, fecha: date | None = None) -> Decimal:
        clave = (producto, lista, fecha.isoformat() if fecha else "sin_fecha")
        if clave in self._cache:
            return self._cache[clave]
        cached_compartido = _SHARED_PRICE_CACHE.get(clave)
        if cached_compartido is not None:
            precio_cached, ts = cached_compartido
            if time.time() - ts < _SHARED_CACHE_TTL_SECONDS:
                self._cache[clave] = precio_cached
                return precio_cached

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

        precio: Decimal | None = None
        es_fallback = False
        if prod_id:
            precio = self._precio_fijo_en_lista(pricelist_id, prod_id, fecha)
            if precio is None:
                es_fallback = True
                for fallback_id in self._fallback_pricelist_ids:
                    if fallback_id == pricelist_id:
                        continue
                    precio = self._precio_fijo_en_lista(fallback_id, prod_id, fecha)
                    if precio is not None:
                        break

        if precio is None:
            # Sin regla de precio fijo en ninguna pricelist candidata -- usar
            # el campo "Precio de venta $" (list_price_usd) de la ficha del
            # producto, NUNCA "list_price" (esa está en VES, la moneda de la
            # compañía en Odoo -- tratarla como USD infla el precio ~800x).
            # Verificado en vivo: S00700/S00718, producto "GLOBAL MOTORGAS W
            # SAE 40 (Tambor)" sin regla en la lista de la orden -- list_price
            # devuelve 1,457,052.51 (VES) vs list_price_usd 1,961.54 (USD).
            es_fallback = True
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
        # No cachear compartido un resultado de fallback ($0 por producto sin
        # regla de precio en ninguna lista candidata): ese "no hay regla" es
        # justo la señal que se re-verifica cuando alguien completa la
        # pricelist en Odoo (ver ``usa_fallback_ves``/``_usd`` en Ventas) --
        # cachearlo por horas ocultaría esa corrección hasta que expire.
        if not es_fallback:
            _SHARED_PRICE_CACHE[clave] = (precio, time.time())
        self._fallback_flags[(producto, lista)] = es_fallback
        return precio

    def fue_fallback(self, producto: str, lista: str) -> bool:
        return self._fallback_flags.get((producto, lista), False)

    def volumen(self, producto: str) -> Decimal:
        clave = (producto, "volumen")
        if clave in self._cache:
            return self._cache[clave]
        cached_compartido = _SHARED_VOLUMEN_CACHE.get(producto)
        if cached_compartido is not None:
            vol_cached, ts = cached_compartido
            if time.time() - ts < _SHARED_CACHE_TTL_SECONDS:
                self._cache[clave] = vol_cached
                return vol_cached

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
        _SHARED_VOLUMEN_CACHE[producto] = (vol, time.time())
        return vol
