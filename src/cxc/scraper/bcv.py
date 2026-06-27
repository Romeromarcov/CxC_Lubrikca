"""Scraper de la tasa BCV (sección 5).

El BCV publica el dólar en HTML con formato local (``36,50``). El patrón de
extracción es parametrizable (``BcvConfig.usd_regex``) para sobrevivir cambios
de maquetado sin tocar código.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from ..config import BcvConfig
from ..decimal_utils import q6


def _parse_decimal_local(texto: str) -> Decimal:
    """Convierte un número en formato local venezolano a Decimal.

    ``1.234,56`` -> ``1234.56`` ; ``36,50`` -> ``36.50`` ; ``40.00`` -> ``40.00``.
    """
    t = texto.strip()
    if "," in t:
        # Coma decimal: el punto es separador de miles.
        t = t.replace(".", "").replace(",", ".")
    try:
        return Decimal(t)
    except InvalidOperation as exc:
        raise ValueError(f"Tasa BCV no parseable: {texto!r}") from exc


def parse_bcv_html(html: str, regex: str) -> Decimal:
    """Extrae la tasa USD del HTML del BCV con el patrón configurado."""
    match = re.search(regex, html, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        raise ValueError("No se encontró la tasa USD en el HTML del BCV")
    valor = q6(_parse_decimal_local(match.group(1)))
    if valor <= 0:
        raise ValueError(f"Tasa BCV inválida: {valor}")
    return valor


GetFn = Callable[[str, int], str]


class BcvClient:
    """Cliente del BCV. La función de red es inyectable para tests."""

    def __init__(self, config: BcvConfig, get: GetFn | None = None) -> None:
        self._config = config
        self._get = get or _default_get

    def fetch_rate(self) -> Decimal:
        html = self._get(self._config.url, self._config.timeout_seconds)
        return parse_bcv_html(html, self._config.usd_regex)


def _default_get(url: str, timeout: int) -> str:  # pragma: no cover - red externa
    import requests

    resp = requests.get(url, timeout=timeout, verify=False)  # noqa: S501 - BCV usa cert propio
    resp.raise_for_status()
    return resp.text
