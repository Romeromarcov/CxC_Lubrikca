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
    cfg = AlertConfig(telegram_bot_token=None, telegram_chat_id=None, telegram_api_url="http://t")
    assert isinstance(build_alerter(cfg), LoggingAlerter)


def test_telegram_alerter_requiere_credenciales() -> None:
    cfg = AlertConfig(telegram_bot_token=None, telegram_chat_id=None, telegram_api_url="http://t")
    with pytest.raises(ValueError):
        TelegramAlerter(cfg)


# --- effective_dating --------------------------------------------------------
def _d(regla_id: str, marca: str, categoria: str, pct: str) -> DescuentoMarcaCategoria:
    return DescuentoMarcaCategoria(
        regla_id=regla_id,
        marca=marca,
        categoria=categoria,
        tipo_descuento=TipoDescuento.CONTADO,
        porcentaje=Decimal(pct),
        vigencia_desde=date(2026, 1, 1),
    )


def test_descuento_categoria_exacta_gana_sobre_comodin() -> None:
    reglas = [
        _d("A", "Global Oil", "*", "0.06"),
        _d("B", "Global Oil", "Comercial sintéticos", "0.08"),
    ]
    elegido = descuento_vigente(
        reglas,
        marca="Global Oil",
        categoria="Comercial sintéticos",
        tipo=TipoDescuento.CONTADO,
        fecha=date(2026, 6, 1),
    )
    assert elegido is not None and elegido.regla_id == "B"


def test_descuento_fuera_de_vigencia_no_aplica() -> None:
    regla = _d("A", "Sinoco", "*", "0.03")
    regla.vigencia_hasta = date(2026, 3, 1)
    elegido = descuento_vigente(
        [regla],
        marca="Sinoco",
        categoria="*",
        tipo=TipoDescuento.CONTADO,
        fecha=date(2026, 6, 1),
    )
    assert elegido is None


def test_descuento_sin_match_devuelve_none() -> None:
    assert (
        descuento_vigente(
            [],
            marca="X",
            categoria="Y",
            tipo=TipoDescuento.CONTADO,
            fecha=date(2026, 6, 1),
        )
        is None
    )


def test_effective_dating_match_marca_y_lista() -> None:
    from cxc.engine.effective_dating import _match_lista, _match_marca, _match_producto_especial

    assert _match_marca("*", "GLOBAL OIL") is True
    assert _match_marca("GLOBAL OIL, SINOCO", "GLOBAL OIL") is True
    assert _match_marca("GLOBAL OIL, SINOCO", "Mobil") is False
    assert _match_lista("*", "4") is True
    assert _match_lista("4,5,8", "5") is True
    assert _match_lista("4,5", "8") is False

    regla_elite = _d("PP_ELITE", "GLOBAL OIL", "ELITE", "0.10")
    assert _match_producto_especial(regla_elite, "ACEITE LUBRIKCA ELITE 20W50", "CAJA") is True
    assert _match_producto_especial(regla_elite, "ACEITE BASICO MULTIGRADO", "CAJA") is False


def test_recurrencia_vigente_selecciona_mas_reciente() -> None:
    vieja = b.regla_recompra("0.03", desde=date(2026, 1, 1))
    nueva = b.regla_recompra("0.04", desde=date(2026, 5, 1))
    elegido = regla_recurrencia_vigente(
        [vieja, nueva],
        condicion=Condicion.RECOMPRA,
        fecha=date(2026, 6, 1),
    )
    assert elegido is not None and elegido.valor == Decimal("0.04")


def test_recurrencia_sin_match_none() -> None:
    assert (
        regla_recurrencia_vigente([], condicion=Condicion.PRIMERA_COMPRA, fecha=date(2026, 6, 1))
        is None
    )


# --- equivalents -------------------------------------------------------------
def test_equivalentes_ves() -> None:
    eq = calcular_equivalentes(Decimal("3600"), Moneda.VES, Decimal("36"), Decimal("40"))
    assert eq.equiv_usd_bcv == Decimal("100.000000")
    assert eq.equiv_usd_binance == Decimal("90.000000")
    assert eq.equiv_ves_bcv == Decimal("3600.000000")


def test_equivalentes_usd() -> None:
    eq = calcular_equivalentes(Decimal("100"), Moneda.USD, Decimal("36"), Decimal("40"))
    assert eq.equiv_usd_bcv == Decimal("100.000000")
    assert eq.equiv_ves_bcv == Decimal("3600.000000")
    assert eq.equiv_ves_binance == Decimal("4000.000000")


def test_equivalentes_tasa_invalida() -> None:
    with pytest.raises(ValueError):
        calcular_equivalentes(Decimal("100"), Moneda.VES, Decimal("0"), Decimal("40"))


def test_congelar_es_idempotente() -> None:
    v = b.vinculacion(
        moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BCV,
        monto_aplicado="3600",
        tasa_bcv="36",
        tasa_binance="40",
    )
    congelar_en_vinculacion(v)
    primero = v.equiv_usd_bcv
    v.tasa_bcv_aplicada = Decimal("99")  # cambiar tasa no debe recalcular
    congelar_en_vinculacion(v)
    assert v.equiv_usd_bcv == primero


def test_ruta_bcv_pura_y_valor_pagado() -> None:
    v1 = b.vinculacion(
        "V1",
        moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BCV,
        monto_aplicado="3600",
        tasa_bcv="36",
        tasa_binance="40",
    )
    congelar_en_vinculacion(v1)
    assert es_ruta_bcv_pura([v1]) is True
    assert es_ruta_bcv_pura([]) is False
    assert valor_pagado_usd([v1]) == Decimal("100.000000")


def test_valor_pagado_binance_route() -> None:
    v = b.vinculacion(
        moneda_abono=Moneda.VES,
        tipo_tasa_abono=TipoTasa.BINANCE,
        monto_aplicado="4000",
        tasa_bcv="36",
        tasa_binance="40",
    )
    congelar_en_vinculacion(v)
    assert valor_pagado_usd([v]) == Decimal("100.000000")


def test_valor_pagado_sin_congelar_falla() -> None:
    v = b.vinculacion(moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV)
    with pytest.raises(ValueError, match="congelar"):
        valor_pagado_usd([v])


# --- el par BCV de los equivalentes (Fase 2.4, pieza 17) --------------------


def test_equivalentes_bcv_en_bolivares_divide_y_redondea_a_seis() -> None:
    """El redondeo es el punto de la pieza.

    ``post_cambiar_tipo_tasa_bcv`` hacía esta misma cuenta **sin** ``q6``, así que
    editar la variante USD/EUR de una vinculación reescribía un equivalente
    **congelado** con otra precisión que la que tenía al crearse.
    """
    from decimal import Decimal

    from cxc.engine.equivalents import equivalentes_bcv
    from cxc.models import Moneda

    usd, ves = equivalentes_bcv(Decimal("1000"), Moneda.VES, Decimal("3"))
    assert usd == Decimal("333.333333"), "seis decimales, no la división cruda"
    assert ves == Decimal("1000.000000")


def test_equivalentes_bcv_en_dolares_multiplica() -> None:
    from decimal import Decimal

    from cxc.engine.equivalents import equivalentes_bcv
    from cxc.models import Moneda

    usd, ves = equivalentes_bcv(Decimal("100"), Moneda.USD, Decimal("732.5"))
    assert usd == Decimal("100.000000")
    assert ves == Decimal("73250.000000")


def test_equivalentes_bcv_exige_una_tasa_positiva() -> None:
    """Un equivalente calculado con tasa cero no significa nada, y queda congelado.

    Es la misma razón por la que ``calcular_equivalentes`` ya lo exigía: acá se
    está fijando un número que por diseño no se vuelve a revisar.
    """
    from decimal import Decimal

    import pytest

    from cxc.engine.equivalents import equivalentes_bcv
    from cxc.models import Moneda

    for mala in (Decimal("0"), Decimal("-3")):
        with pytest.raises(ValueError, match="positiva"):
            equivalentes_bcv(Decimal("1000"), Moneda.VES, mala)


def test_calcular_equivalentes_usa_la_misma_pieza_para_el_lado_BCV() -> None:
    """Las dos rutas comparten la función, así que no pueden volver a divergir."""
    from decimal import Decimal

    from cxc.engine.equivalents import calcular_equivalentes, equivalentes_bcv
    from cxc.models import Moneda

    todos = calcular_equivalentes(
        Decimal("1000"), Moneda.VES, Decimal("3"), Decimal("4")
    )
    usd, ves = equivalentes_bcv(Decimal("1000"), Moneda.VES, Decimal("3"))
    assert (todos.equiv_usd_bcv, todos.equiv_ves_bcv) == (usd, ves)
    # Y el lado Binance sigue siendo el suyo, no una copia del BCV.
    assert todos.equiv_usd_binance == Decimal("250.000000")


def test_equivalentes_binance_es_el_gemelo_del_lado_bcv() -> None:
    """Misma divergencia, otro endpoint: ``post_editar_tasa_binance`` también
    reimplementaba la cuenta inline sin ``q6``."""
    from decimal import Decimal

    from cxc.engine.equivalents import equivalentes_binance
    from cxc.models import Moneda

    usd, ves = equivalentes_binance(Decimal("1000"), Moneda.VES, Decimal("3"))
    assert usd == Decimal("333.333333")
    assert ves == Decimal("1000.000000")

    usd, ves = equivalentes_binance(Decimal("100"), Moneda.USD, Decimal("800"))
    assert usd == Decimal("100.000000")
    assert ves == Decimal("80000.000000")


def test_equivalentes_binance_exige_tasa_positiva() -> None:
    from decimal import Decimal

    import pytest

    from cxc.engine.equivalents import equivalentes_binance
    from cxc.models import Moneda

    with pytest.raises(ValueError, match="positiva"):
        equivalentes_binance(Decimal("1000"), Moneda.VES, Decimal("0"))


def test_los_dos_lados_son_independientes() -> None:
    """Cada endpoint edita UN lado, y por eso son dos funciones y no una.

    Si fueran una sola con los cuatro valores, cada endpoint tendría que descartar
    dos — y descartar invita a pisar el lado que no venía a tocar.
    """
    from decimal import Decimal

    from cxc.engine.equivalents import calcular_equivalentes, equivalentes_bcv, equivalentes_binance
    from cxc.models import Moneda

    todos = calcular_equivalentes(Decimal("1000"), Moneda.VES, Decimal("3"), Decimal("4"))
    assert (todos.equiv_usd_bcv, todos.equiv_ves_bcv) == equivalentes_bcv(
        Decimal("1000"), Moneda.VES, Decimal("3")
    )
    assert (todos.equiv_usd_binance, todos.equiv_ves_binance) == equivalentes_binance(
        Decimal("1000"), Moneda.VES, Decimal("4")
    )
    assert todos.equiv_usd_bcv != todos.equiv_usd_binance, "tasas distintas, valores distintos"


# --- el equivalente USD de un pago a una tasa dada (Fase 2.4, pieza 30) -------


class TestEquivalenteUsdATasa:
    """En `get_cobranza_pagos_unificado` este cuerpo estaba escrito DOS veces.

    `monto_eur` y `monto_bcv_real`, idénticos salvo por cuál tasa divide, y las dos con
    sus caminos de error sin cubrir.

    Las dos existen por un bug real: agosto 2026, pago 1279 del cliente SJMG 2012 C.A.,
    la tarjeta mostraba `tasa_bcv_real` (752,09, correcta) junto a un equivalente
    calculado con la tasa BCV-EUR (865,17, la base de conversión interna para clientes
    con órdenes históricas). El número y su tasa no se correspondían.
    """

    def test_un_pago_en_bolivares_se_divide_por_la_tasa(self) -> None:
        from decimal import Decimal

        from cxc.engine.equivalents import equivalente_usd_a_tasa

        assert equivalente_usd_a_tasa(Decimal("82774"), "VES", 827.74) == 100.0

    def test_un_pago_YA_en_dolares_no_se_toca(self) -> None:
        """Dividirlo sería convertir dos veces, y la tasa ni se mira."""
        from decimal import Decimal

        from cxc.engine.equivalents import equivalente_usd_a_tasa

        assert equivalente_usd_a_tasa(Decimal("100"), "USD", 827.74) == 100.0
        assert equivalente_usd_a_tasa(Decimal("100"), "usd", None) == 100.0

    def test_sin_tasa_devuelve_None_y_NO_cero(self) -> None:
        """Un equivalente que no se pudo calcular no es cero dólares.

        La pantalla tiene que mostrar un guion, no un monto. Es la misma distinción que
        `RangoDelDia.verificado` y que `diferencial_verificable`: la ausencia de una
        medición no es una medición de cero.
        """
        from decimal import Decimal

        from cxc.engine.equivalents import equivalente_usd_a_tasa

        assert equivalente_usd_a_tasa(Decimal("82774"), "VES", None) is None

    @pytest.mark.parametrize("tasa", [0, 0.0, -1, -827.74])
    def test_una_tasa_cero_o_negativa_tampoco_convierte(self, tasa) -> None:
        """Cero dividiría por cero; negativa daría un equivalente negativo."""
        from decimal import Decimal

        from cxc.engine.equivalents import equivalente_usd_a_tasa

        assert equivalente_usd_a_tasa(Decimal("82774"), "VES", tasa) is None

    def test_una_tasa_ilegible_devuelve_None_en_vez_de_reventar(self) -> None:
        from decimal import Decimal

        from cxc.engine.equivalents import equivalente_usd_a_tasa

        assert equivalente_usd_a_tasa(Decimal("82774"), "VES", "no es un numero") is None  # type: ignore[arg-type]

    def test_la_moneda_se_normaliza_en_mayusculas_y_sin_espacios(self) -> None:
        """El espejo guarda la moneda como texto libre."""
        from decimal import Decimal

        from cxc.engine.equivalents import equivalente_usd_a_tasa

        for moneda in ("USD", "usd", " Usd ", "uSd"):
            assert equivalente_usd_a_tasa(Decimal("100"), moneda, None) == 100.0

    def test_una_moneda_vacia_NO_se_toma_como_dolares(self) -> None:
        """Suponer USD ante la duda mostraría el nominal en bolívares como dólares.

        Es el mismo error que este plan corrigió en `pago_monto_usd`, que devolvía el
        nominal VES cuando no había tasa.
        """
        from decimal import Decimal

        from cxc.engine.equivalents import equivalente_usd_a_tasa

        assert equivalente_usd_a_tasa(Decimal("82774"), "", 827.74) == 100.0
        assert equivalente_usd_a_tasa(Decimal("82774"), "", None) is None
