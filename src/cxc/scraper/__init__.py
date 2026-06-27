"""Pieza 1 — Scraper de tasas horario → SerieTasas (secciones 5.x)."""

from .bcv import BcvClient, parse_bcv_html
from .binance import BinanceClient, compute_binance_rate, parse_binance_prices
from .rates_scraper import RatesScraper, ScraperError

__all__ = [
    "BcvClient",
    "parse_bcv_html",
    "BinanceClient",
    "compute_binance_rate",
    "parse_binance_prices",
    "RatesScraper",
    "ScraperError",
]
