"""Tests de config, utilidades Decimal, alertas y helpers del motor."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from cxc.alerts import (
    AlertConfig,
    CollectingAlerter,
    LoggingAlerter,
    TelegramAlerter,
    build_alerter,
)
from cxc.config import AppConfig, BinanceConfig
from cxc.decimal_utils import q2, q6, to_decimal
from cxc.engine.effective_dating import (
    descuento_vigente,
    regla_recurrencia_vigente,
)
from cxc.engine.equivalents import (
    calcular_equivalentes,
    congelar_en_vinculacion,
    es_ruta_bcv_pura,
    valor_pagado_usd,
)
from cxc.models import (
    Condicion,
    DescuentoMarcaCategoria,
    Moneda,
    TipoDescuento,
    TipoTasa,
)

from . import builders as b

_REQUIRED = {
    "ODOO_URL": "http://odoo",
    "ODOO_DB": "db",
    "ODOO_USERNAME": "u",
    "ODOO_PASSWORD": "p",
    "GOOGLE_SHEETS_SPREADSHEET_ID": "sheet1",
    "GOOGLE_SERVICE_ACCOUNT_FILE": "sa.json",
    "BINANCE_P2P_URL": "http://binance",
}


def _set_env(monkeypatch: pytest.MonkeyPatch, extra: dict[str, str] | None = None) -> None:
    for k, v in {**_REQUIRED, **(extra or {})}.items():
        monkeypatch.setenv(k, v)


# --- config ------------------------------------------------------------------
def test_appconfig_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, {"BINANCE_P2P_ROWS": "7", "CASH_WINDOW_BUSINESS_DAYS": "5"})
    cfg = AppConfig.from_env(load_dotenv=False)
    assert cfg.odoo.db == "db"
    assert cfg.binance.rows == 7
    assert cfg.engine.cash_window_business_days == 5
    assert cfg.reconciliation.tolerance_rounding == Decimal("0.01")


def test_appconfig_falta_var_requerida(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in _REQUIRED:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(KeyError, match="ODOO_URL"):
        AppConfig.from_env(load_dotenv=False)


def test_binance_paytypes_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, {"BINANCE_P2P_PAY_TYPES": "Banesco, Mercantil ,"})
    cfg = BinanceConfig.from_env()
    assert cfg.pay_types == ["Banesco", "Mercantil"]


# --- decimal_utils -----------------------------------------------------------
def test_to_decimal_acepta_tipos_seguros() -> None:
    assert to_decimal(5) == Decimal("5")
    assert to_decimal("5.5") == Decimal("5.5")
    assert to_decimal(Decimal("2")) == Decimal("2")


def test_to_decimal_rechaza_bool_y_float() -> None:
    with pytest.raises(TypeError):
        to_decimal(True)
    with pytest.raises(TypeError):
        to_decimal(1.5)


def test_quantize() -> None:
    assert q2(Decimal("1.005")) == Decimal("1.01")
    assert q6(Decimal("1.0000005")) == Decimal("1.000001")


# --- alerts ------------------------------------------------------------------
def test_collecting_alerter() -> None:
    a = CollectingAlerter()
    a.send("hola")
    assert a.mensajes == ["hola"]


def test_logging_alerter_no_explota() -> None:
    LoggingAlerter().send("x")  # no debe lanzar


def test_build_alerter_sin_credenciales_es_logging() -> None:
    cfg = AlertConfig(telegram_bot_token=None, telegram_chat_id=None,
                      telegram_api_url="http://t")
    assert isinstance(build_alerter(cfg), LoggingAlerter)


def test_telegram_alerter_requiere_credenciales() -> None:
    cfg = AlertConfig(telegram_bot_token=None, telegram_chat_id=None,
                      telegram_api_url="http://t")
    with pytest.raises(ValueError):
        TelegramAlerter(cfg)


# --- effective_dating --------------------------------------------------------
def _d(regla_id: str, marca: str, categoria: str, pct: str) -> DescuentoMarcaCategoria:
    return DescuentoMarcaCategoria(
        regla_id=regla_id, marca=marca, categoria=categoria,
        tipo_descuento=TipoDescuento.CONTADO, porcentaje=Decimal(pct),
        vigencia_desde=date(2026, 1, 1),
    )


def test_descuento_categoria_exacta_gana_sobre_comodin() -> None:
    reglas = [
        _d("A", "Global Oil", "*", "0.06"),
        _d("B", "Global Oil", "Comercial sintéticos", "0.08"),
    ]
    elegido = descuento_vigente(
        reglas, marca="Global Oil", categoria="Comercial sintéticos",
        tipo=TipoDescuento.CONTADO, fecha=date(2026, 6, 1),
    )
    assert elegido is not None and elegido.regla_id == "B"


def test_descuento_fuera_de_vigencia_no_aplica() -> None:
    regla = _d("A", "Sinoco", "*", "0.03")
    regla.vigencia_hasta = date(2026, 3, 1)
    elegido = descuento_vigente(
        [regla], marca="Sinoco", categoria="*", tipo=TipoDescuento.CONTADO,
        fecha=date(2026, 6, 1),
    )
    assert elegido is None


def test_descuento_sin_match_devuelve_none() -> None:
    assert descuento_vigente(
        [], marca="X", categoria="Y", tipo=TipoDescuento.CONTADO,
        fecha=date(2026, 6, 1),
    ) is None


def test_recurrencia_vigente_selecciona_mas_reciente() -> None:
    vieja = b.regla_recompra("0.03", desde=date(2026, 1, 1))
    nueva = b.regla_recompra("0.04", desde=date(2026, 5, 1))
    elegido = regla_recurrencia_vigente(
        [vieja, nueva], condicion=Condicion.RECOMPRA, fecha=date(2026, 6, 1),
    )
    assert elegido is not None and elegido.valor == Decimal("0.04")


def test_recurrencia_sin_match_none() -> None:
    assert regla_recurrencia_vigente(
        [], condicion=Condicion.PRIMERA_COMPRA, fecha=date(2026, 6, 1)
    ) is None


# --- equivalents -------------------------------------------------------------
def test_equivalentes_ves() -> None:
    eq = calcular_equivalentes(Decimal("3600"), Moneda.VES, Decimal("36"),
                               Decimal("40"))
    assert eq.equiv_usd_bcv == Decimal("100.000000")
    assert eq.equiv_usd_binance == Decimal("90.000000")
    assert eq.equiv_ves_bcv == Decimal("3600.000000")


def test_equivalentes_usd() -> None:
    eq = calcular_equivalentes(Decimal("100"), Moneda.USD, Decimal("36"),
                               Decimal("40"))
    assert eq.equiv_usd_bcv == Decimal("100.000000")
    assert eq.equiv_ves_bcv == Decimal("3600.000000")
    assert eq.equiv_ves_binance == Decimal("4000.000000")


def test_equivalentes_tasa_invalida() -> None:
    with pytest.raises(ValueError):
        calcular_equivalentes(Decimal("100"), Moneda.VES, Decimal("0"), Decimal("40"))


def test_congelar_es_idempotente() -> None:
    v = b.vinculacion(moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV,
                      monto_aplicado="3600", tasa_bcv="36", tasa_binance="40")
    congelar_en_vinculacion(v)
    primero = v.equiv_usd_bcv
    v.tasa_bcv_aplicada = Decimal("99")  # cambiar tasa no debe recalcular
    congelar_en_vinculacion(v)
    assert v.equiv_usd_bcv == primero


def test_ruta_bcv_pura_y_valor_pagado() -> None:
    v1 = b.vinculacion("V1", moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV,
                       monto_aplicado="3600", tasa_bcv="36", tasa_binance="40")
    congelar_en_vinculacion(v1)
    assert es_ruta_bcv_pura([v1]) is True
    assert es_ruta_bcv_pura([]) is False
    assert valor_pagado_usd([v1]) == Decimal("100.000000")


def test_valor_pagado_binance_route() -> None:
    v = b.vinculacion(moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BINANCE,
                      monto_aplicado="4000", tasa_bcv="36", tasa_binance="40")
    congelar_en_vinculacion(v)
    assert valor_pagado_usd([v]) == Decimal("100.000000")


def test_valor_pagado_sin_congelar_falla() -> None:
    v = b.vinculacion(moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV)
    with pytest.raises(ValueError, match="congelar"):
        valor_pagado_usd([v])
