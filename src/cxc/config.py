"""Configuración del sistema por variables de entorno.

Regla dura: no hay secretos en el repo. Todo entra por env vars (o un `.env`
local cargado con python-dotenv). La estructura/URL del scraper Binance está
parametrizada AQUÍ — es el único punto de ajuste cuando Binance cambia formato.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal


def _get(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise KeyError(f"Falta la variable de entorno requerida: {name}")
    return value


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _get_decimal(name: str, default: str) -> Decimal:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return Decimal(default)
    return Decimal(raw)


def _get_optional(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else None


@dataclass(frozen=True)
class OdooConfig:
    url: str
    db: str
    username: str
    password: str

    @classmethod
    def from_env(cls) -> OdooConfig:
        return cls(
            url=_get("ODOO_URL"),
            db=_get("ODOO_DB"),
            username=_get("ODOO_USERNAME"),
            password=_get("ODOO_PASSWORD"),
        )


@dataclass(frozen=True)
class SheetsConfig:
    spreadsheet_id: str
    service_account_file: str

    @classmethod
    def from_env(cls) -> SheetsConfig:
        return cls(
            spreadsheet_id=_get("GOOGLE_SHEETS_SPREADSHEET_ID"),
            service_account_file=_get("GOOGLE_SERVICE_ACCOUNT_FILE"),
        )


@dataclass(frozen=True)
class BinanceConfig:
    """Parametrización completa del scraper Binance P2P.

    Ningún literal de Binance vive en la lógica: URL, asset, fiat, los dos
    trade types, cuántas filas promediar y los dot-paths del JSON viven aquí.
    """

    url: str
    asset: str
    fiat: str
    trade_type_buy: str
    trade_type_sell: str
    pay_types: list[str]
    rows: int
    adv_list_path: str
    price_path: str
    timeout_seconds: int

    @classmethod
    def from_env(cls) -> BinanceConfig:
        pay_raw = os.environ.get("BINANCE_P2P_PAY_TYPES", "")
        pay_types = [p.strip() for p in pay_raw.split(",") if p.strip()]
        return cls(
            url=_get("BINANCE_P2P_URL"),
            asset=_get("BINANCE_P2P_ASSET", "USDT"),
            fiat=_get("BINANCE_P2P_FIAT", "VES"),
            trade_type_buy=_get("BINANCE_P2P_TRADE_TYPE_BUY", "BUY"),
            trade_type_sell=_get("BINANCE_P2P_TRADE_TYPE_SELL", "SELL"),
            pay_types=pay_types,
            rows=_get_int("BINANCE_P2P_ROWS", 5),
            adv_list_path=_get("BINANCE_P2P_ADV_LIST_PATH", "data"),
            price_path=_get("BINANCE_P2P_PRICE_PATH", "adv.price"),
            timeout_seconds=_get_int("BINANCE_REQUEST_TIMEOUT_SECONDS", 20),
        )


@dataclass(frozen=True)
class BcvConfig:
    url: str
    usd_regex: str
    timeout_seconds: int

    @classmethod
    def from_env(cls) -> BcvConfig:
        return cls(
            url=_get("BCV_URL", "https://www.bcv.org.ve/"),
            usd_regex=_get(
                "BCV_USD_REGEX",
                r'id="dolar".*?<strong>\s*([\d.,]+)\s*</strong>',
            ),
            timeout_seconds=_get_int("BCV_REQUEST_TIMEOUT_SECONDS", 20),
        )


@dataclass(frozen=True)
class AlertConfig:
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    telegram_api_url: str

    @classmethod
    def from_env(cls) -> AlertConfig:
        return cls(
            telegram_bot_token=_get_optional("ALERT_TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_get_optional("ALERT_TELEGRAM_CHAT_ID"),
            telegram_api_url=_get("ALERT_TELEGRAM_API_URL", "https://api.telegram.org"),
        )


@dataclass(frozen=True)
class EngineConfig:
    cash_window_business_days: int
    bcv_complete_formula: str
    lista_usd: str
    lista_bcv: str

    @classmethod
    def from_env(cls) -> EngineConfig:
        return cls(
            cash_window_business_days=_get_int("CASH_WINDOW_BUSINESS_DAYS", 3),
            bcv_complete_formula=_get(
                "BCV_COMPLETE_FORMULA", "differential_over_binance"
            ),
            lista_usd=_get("ENGINE_LISTA_USD", "USD"),
            lista_bcv=_get("ENGINE_LISTA_BCV", "BCV"),
        )


@dataclass(frozen=True)
class ReconciliationConfig:
    tolerance_rounding: Decimal
    tolerance_red: Decimal

    @classmethod
    def from_env(cls) -> ReconciliationConfig:
        return cls(
            tolerance_rounding=_get_decimal("RECON_TOLERANCE_ROUNDING", "0.01"),
            tolerance_red=_get_decimal("RECON_TOLERANCE_RED", "1.00"),
        )


@dataclass(frozen=True)
class HourAuditConfig:
    threshold_minutes: int
    rate_swing_pct: Decimal

    @classmethod
    def from_env(cls) -> HourAuditConfig:
        return cls(
            threshold_minutes=_get_int("HOUR_AUDIT_THRESHOLD_MINUTES", 60),
            rate_swing_pct=_get_decimal("HOUR_AUDIT_RATE_SWING_PCT", "0.03"),
        )


@dataclass(frozen=True)
class ScraperPolicy:
    fail_alert_threshold: int

    @classmethod
    def from_env(cls) -> ScraperPolicy:
        return cls(fail_alert_threshold=_get_int("SCRAPER_FAIL_ALERT_THRESHOLD", 3))


@dataclass(frozen=True)
class AppConfig:
    """Configuración raíz. Cada pieza toma solo lo que necesita."""

    odoo: OdooConfig
    sheets: SheetsConfig
    binance: BinanceConfig
    bcv: BcvConfig
    alert: AlertConfig
    engine: EngineConfig
    reconciliation: ReconciliationConfig
    hour_audit: HourAuditConfig
    scraper_policy: ScraperPolicy = field(default_factory=ScraperPolicy.from_env)

    @classmethod
    def from_env(cls, load_dotenv: bool = True) -> AppConfig:
        if load_dotenv:
            _maybe_load_dotenv()
        return cls(
            odoo=OdooConfig.from_env(),
            sheets=SheetsConfig.from_env(),
            binance=BinanceConfig.from_env(),
            bcv=BcvConfig.from_env(),
            alert=AlertConfig.from_env(),
            engine=EngineConfig.from_env(),
            reconciliation=ReconciliationConfig.from_env(),
            hour_audit=HourAuditConfig.from_env(),
            scraper_policy=ScraperPolicy.from_env(),
        )


def _maybe_load_dotenv() -> None:
    """Carga un `.env` local si python-dotenv está disponible. Best-effort."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv siempre está en requirements
        return
    load_dotenv()
