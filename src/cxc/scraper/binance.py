"""Scraper Binance P2P (sección 5.1).

    tasa_binance = (Σ 5 primeras COMPRA + Σ 5 primeras VENTA) / 10

REGLA DURA: ni la URL ni la estructura del JSON viven aquí hardcodeadas. Todo
sale de ``BinanceConfig`` (dot-paths ``adv_list_path`` y ``price_path``, nº de
filas, asset/fiat/trade types). Cuando Binance cambie de formato, se ajusta la
config — no este módulo.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from ..config import BinanceConfig
from ..decimal_utils import q6, to_decimal


def _dig(obj: Any, dotpath: str) -> Any:
    """Navega un objeto anidado por un dot-path (``a.b.c``)."""
    actual = obj
    for parte in dotpath.split("."):
        if not isinstance(actual, dict):
            raise KeyError(f"Dot-path {dotpath!r} no resuelve: {parte!r} no es objeto")
        if parte not in actual:
            raise KeyError(f"Dot-path {dotpath!r} no resuelve: falta {parte!r}")
        actual = actual[parte]
    return actual


def parse_binance_prices(
    payload: dict[str, Any],
    *,
    adv_list_path: str,
    price_path: str,
    rows: int,
) -> list[Decimal]:
    """Extrae los precios de los primeros ``rows`` anuncios del payload."""
    lista = _dig(payload, adv_list_path)
    if not isinstance(lista, list):
        raise ValueError(f"{adv_list_path!r} no apunta a una lista de anuncios")
    if len(lista) < rows:
        raise ValueError(
            f"Binance devolvió {len(lista)} anuncios; se necesitan {rows}"
        )
    precios: list[Decimal] = []
    for anuncio in lista[:rows]:
        raw = _dig(anuncio, price_path)
        try:
            precios.append(to_decimal(str(raw)))
        except (InvalidOperation, TypeError) as exc:
            raise ValueError(f"Precio Binance no parseable: {raw!r}") from exc
    return precios


def compute_binance_rate(
    buy_payload: dict[str, Any],
    sell_payload: dict[str, Any],
    config: BinanceConfig,
) -> Decimal:
    """(Σ 5 compra + Σ 5 venta) / 10, según ``config.rows``."""
    compras = parse_binance_prices(
        buy_payload,
        adv_list_path=config.adv_list_path,
        price_path=config.price_path,
        rows=config.rows,
    )
    ventas = parse_binance_prices(
        sell_payload,
        adv_list_path=config.adv_list_path,
        price_path=config.price_path,
        rows=config.rows,
    )
    total = sum(compras, Decimal("0")) + sum(ventas, Decimal("0"))
    divisor = Decimal(config.rows * 2)
    return q6(total / divisor)


PostFn = Callable[[str, dict[str, Any], int], dict[str, Any]]


class BinanceClient:
    """Cliente Binance P2P. La función de red es inyectable para tests.

    En producción pega directo al endpoint público (sin credenciales).
    """

    def __init__(self, config: BinanceConfig, post: PostFn | None = None) -> None:
        self._config = config
        self._post = post or _default_post

    def _request_body(self, trade_type: str) -> dict[str, Any]:
        return {
            "asset": self._config.asset,
            "fiat": self._config.fiat,
            "tradeType": trade_type,
            "page": 1,
            "rows": self._config.rows,
            "payTypes": self._config.pay_types,
        }

    def fetch_rate(self) -> Decimal:
        buy = self._post(
            self._config.url,
            self._request_body(self._config.trade_type_buy),
            self._config.timeout_seconds,
        )
        sell = self._post(
            self._config.url,
            self._request_body(self._config.trade_type_sell),
            self._config.timeout_seconds,
        )
        return compute_binance_rate(buy, sell, self._config)


def _default_post(  # pragma: no cover - red externa
    url: str, body: dict[str, Any], timeout: int
) -> dict[str, Any]:
    import requests

    resp = requests.post(url, json=body, timeout=timeout)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return data
