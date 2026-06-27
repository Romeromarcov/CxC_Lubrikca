"""Tests del scraper de tasas (secciones 5.x) — sin red, con fixtures."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from cxc.alerts import CollectingAlerter
from cxc.config import BcvConfig, BinanceConfig, ScraperPolicy
from cxc.repositories import InMemoryRepository
from cxc.scraper.bcv import BcvClient, parse_bcv_html
from cxc.scraper.binance import (
    BinanceClient,
    compute_binance_rate,
    parse_binance_prices,
)
from cxc.scraper.rates_scraper import RatesScraper, ScraperError

FIXTURES = Path(__file__).parent / "fixtures"


def _load(nombre: str) -> dict[str, Any]:
    return json.loads((FIXTURES / nombre).read_text(encoding="utf-8"))


def _binance_config() -> BinanceConfig:
    return BinanceConfig(
        url="https://example.test/p2p",
        asset="USDT",
        fiat="VES",
        trade_type_buy="BUY",
        trade_type_sell="SELL",
        pay_types=[],
        rows=5,
        adv_list_path="data",
        price_path="adv.price",
        timeout_seconds=10,
    )


def _bcv_config() -> BcvConfig:
    return BcvConfig(
        url="https://example.test/bcv",
        usd_regex=r'id="dolar".*?<strong>\s*([\d.,]+)\s*</strong>',
        timeout_seconds=10,
    )


# --- Binance: parsing parametrizado -----------------------------------------
def test_parse_binance_toma_solo_las_primeras_n() -> None:
    payload = _load("binance_buy.json")
    precios = parse_binance_prices(
        payload, adv_list_path="data", price_path="adv.price", rows=5
    )
    assert precios == [Decimal(p) for p in ("40.10", "40.20", "40.30", "40.40", "40.50")]


def test_compute_binance_rate_promedio_5_compra_5_venta() -> None:
    buy = _load("binance_buy.json")
    sell = _load("binance_sell.json")
    # (201.50 + 198.50) / 10 = 40.00
    assert compute_binance_rate(buy, sell, _binance_config()) == Decimal("40.000000")


def test_parse_binance_falla_si_faltan_anuncios() -> None:
    payload = {"data": [{"adv": {"price": "1"}}]}
    with pytest.raises(ValueError, match="se necesitan 5"):
        parse_binance_prices(
            payload, adv_list_path="data", price_path="adv.price", rows=5
        )


def test_dot_path_invalido_falla() -> None:
    with pytest.raises(KeyError):
        parse_binance_prices(
            {"otra": []}, adv_list_path="data", price_path="adv.price", rows=1
        )


def test_binance_client_usa_post_inyectado() -> None:
    buy = _load("binance_buy.json")
    sell = _load("binance_sell.json")
    llamadas: list[dict[str, Any]] = []

    def fake_post(url: str, body: dict[str, Any], timeout: int) -> dict[str, Any]:
        llamadas.append(body)
        return buy if body["tradeType"] == "BUY" else sell

    client = BinanceClient(_binance_config(), post=fake_post)
    assert client.fetch_rate() == Decimal("40.000000")
    assert [c["tradeType"] for c in llamadas] == ["BUY", "SELL"]


# --- BCV: parsing del HTML ---------------------------------------------------
def test_parse_bcv_html() -> None:
    html = (FIXTURES / "bcv_page.html").read_text(encoding="utf-8")
    assert parse_bcv_html(html, _bcv_config().usd_regex) == Decimal("36.500000")


def test_parse_bcv_html_sin_match_falla() -> None:
    with pytest.raises(ValueError, match="No se encontró"):
        parse_bcv_html("<html></html>", _bcv_config().usd_regex)


def test_bcv_client_usa_get_inyectado() -> None:
    html = (FIXTURES / "bcv_page.html").read_text(encoding="utf-8")
    client = BcvClient(_bcv_config(), get=lambda url, timeout: html)
    assert client.fetch_rate() == Decimal("36.500000")


# --- Orquestador: append, fallback y alerta ---------------------------------
def _scraper(repo: InMemoryRepository, alerter: CollectingAlerter,
             *, binance_post, bcv_get) -> RatesScraper:
    return RatesScraper(
        repo,
        BinanceClient(_binance_config(), post=binance_post),
        BcvClient(_bcv_config(), get=bcv_get),
        alerter,
        ScraperPolicy(fail_alert_threshold=3),
    )


def test_captura_exitosa_hace_append() -> None:
    repo = InMemoryRepository()
    alerter = CollectingAlerter()
    buy, sell = _load("binance_buy.json"), _load("binance_sell.json")
    html = (FIXTURES / "bcv_page.html").read_text(encoding="utf-8")
    scraper = _scraper(
        repo, alerter,
        binance_post=lambda u, b, t: buy if b["tradeType"] == "BUY" else sell,
        bcv_get=lambda u, t: html,
    )
    fila = scraper.run(datetime(2026, 6, 27, 10, 0))
    assert fila.capturada_ok is True
    assert fila.es_heredada is False
    assert fila.tasa_binance == Decimal("40.000000")
    assert fila.tasa_bcv == Decimal("36.500000")
    assert repo.last_serie_tasa() == fila


def test_fallback_hereda_ultima_tasa() -> None:
    repo = InMemoryRepository()
    alerter = CollectingAlerter()
    buy, sell = _load("binance_buy.json"), _load("binance_sell.json")
    html = (FIXTURES / "bcv_page.html").read_text(encoding="utf-8")

    def falla(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("Binance caído")

    # Primera corrida OK.
    ok = _scraper(
        repo, alerter,
        binance_post=lambda u, b, t: buy if b["tradeType"] == "BUY" else sell,
        bcv_get=lambda u, t: html,
    )
    ok.run(datetime(2026, 6, 27, 10, 0))

    # Segunda corrida: Binance falla → hereda.
    caido = _scraper(repo, alerter, binance_post=falla, bcv_get=lambda u, t: html)
    fila = caido.run(datetime(2026, 6, 27, 11, 0))
    assert fila.es_heredada is True
    assert fila.capturada_ok is False
    assert fila.tasa_binance == Decimal("40.000000")  # heredada
    assert "heredada de" in fila.fuente


def test_tres_fallos_consecutivos_alertan() -> None:
    repo = InMemoryRepository()
    alerter = CollectingAlerter()
    buy, sell = _load("binance_buy.json"), _load("binance_sell.json")
    html = (FIXTURES / "bcv_page.html").read_text(encoding="utf-8")

    def falla(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("caído")

    _scraper(
        repo, alerter,
        binance_post=lambda u, b, t: buy if b["tradeType"] == "BUY" else sell,
        bcv_get=lambda u, t: html,
    ).run(datetime(2026, 6, 27, 10, 0))

    caido = _scraper(repo, alerter, binance_post=falla, bcv_get=lambda u, t: html)
    caido.run(datetime(2026, 6, 27, 11, 0))
    assert alerter.mensajes == []  # 1 fallo
    caido.run(datetime(2026, 6, 27, 12, 0))
    assert alerter.mensajes == []  # 2 fallos
    caido.run(datetime(2026, 6, 27, 13, 0))
    assert len(alerter.mensajes) == 1  # 3 fallos -> alerta
    assert "3 capturas fallidas" in alerter.mensajes[0]


def test_primera_captura_falla_sin_previa_alerta_y_lanza() -> None:
    repo = InMemoryRepository()
    alerter = CollectingAlerter()

    def falla(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("caído")

    scraper = _scraper(repo, alerter, binance_post=falla, bcv_get=falla)
    with pytest.raises(ScraperError):
        scraper.run(datetime(2026, 6, 27, 10, 0))
    assert len(alerter.mensajes) == 1
