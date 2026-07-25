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


def parse_bcv_rates(html: str) -> tuple[Decimal, Decimal]:
    """Extrae las tasas USD y EUR del HTML del BCV."""
    m_usd = re.search(r'id=["\']dolar["\'].*?<strong[^>]*>\s*([\d\.,]+)\s*</strong>', html, flags=re.DOTALL | re.IGNORECASE)
    m_eur = re.search(r'id=["\']euro["\'].*?<strong[^>]*>\s*([\d\.,]+)\s*</strong>', html, flags=re.DOTALL | re.IGNORECASE)

    usd_val = q6(_parse_decimal_local(m_usd.group(1))) if m_usd else Decimal("0")
    eur_val = q6(_parse_decimal_local(m_eur.group(1))) if m_eur else Decimal("0")
    return usd_val, eur_val


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

    def fetch_rates(self) -> tuple[Decimal, Decimal]:
        html = self._get(self._config.url, self._config.timeout_seconds)
        return parse_bcv_rates(html)


def _default_get(url: str, timeout: int) -> str:  # pragma: no cover - red externa
    import requests

    resp = requests.get(url, timeout=timeout, verify=False)  # noqa: S501 - BCV usa cert propio
    resp.raise_for_status()
    return resp.text


class OdooBcvClient:
    """Consigue la tasa del BCV desde Odoo en lugar de hacer scraping del sitio web."""

    def __init__(self, odoo_config: object) -> None:
        self._config = odoo_config

    def fetch_rate(self) -> Decimal:
        rates_usd, _ = self.fetch_rates()
        if rates_usd > Decimal("0"):
            return rates_usd
        raise ValueError("No se pudo obtener la tasa BCV desde Odoo")

    def fetch_rates(self) -> tuple[Decimal, Decimal]:
        from ..odoo.client import _connect
        execute = _connect(self._config)
        
        rates = execute(
            "res.currency.rate",
            "search_read",
            [[["name", ">=", "2026-01-01"], ["currency_id", "in", [1, 125]]]],
            {"fields": ["name", "currency_id", "inverse_company_rate"], "order": "name desc"}
        )
        rate_usd = Decimal("0")
        rate_eur = Decimal("0")
        for r in rates:
            curr = r.get("currency_id")
            c_id = curr[0] if isinstance(curr, (list, tuple)) else curr
            val = r.get("inverse_company_rate")
            if val:
                if c_id == 1 and rate_usd == Decimal("0"):
                    rate_usd = Decimal(str(val))
                elif c_id == 125 and rate_eur == Decimal("0"):
                    rate_eur = Decimal(str(val))
        return rate_usd, rate_eur
