from datetime import date, datetime
from decimal import Decimal

from cxc.models import (
    DescuentoDiferencialCambiario,
    DescuentoProducto,
    DescuentoProntoPago,
    DescuentoRecompra,
    SerieTasa,
)
from cxc.sheets import serde


def test_descuentos_serde_roundtrip():
    pp = DescuentoProntoPago(
        regla_id="PP1",
        marca="Sinoco",
        categoria="Comercial",
        ventana_pago_tipo="vencimiento",
        ventana_pago_dias=5,
        porcentaje=Decimal("0.05"),
        monedas_aplicables="USD",
        listas_aplicables="4",
        vigencia_desde=date(2026, 1, 1),
        activo=True,
    )
    pp_row = serde.pronto_pago_to_row(pp)
    pp_back = serde.pronto_pago_from_row(pp_row)
    assert pp_back.regla_id == "PP1"
    assert pp_back.ventana_pago_dias == 5

    rec = DescuentoRecompra(
        regla_id="REC1",
        porcentaje=Decimal("0.05"),
        ventana_pago_tipo="vencimiento",
        ventana_pago_dias=30,
        vigencia_desde=date(2026, 1, 1),
        activo=True,
    )
    rec_row = serde.recompra_to_row(rec)
    rec_back = serde.recompra_from_row(rec_row)
    assert rec_back.regla_id == "REC1"
    assert rec_back.ventana_pago_dias == 30

    prod = DescuentoProducto(
        regla_id="PROD1",
        productos="SKU123",
        marca="Sinoco",
        categoria="*",
        porcentaje=Decimal("0.10"),
        vigencia_desde=date(2026, 1, 1),
        activo=True,
    )
    prod_row = serde.producto_to_row(prod)
    prod_back = serde.producto_from_row(prod_row)
    assert prod_back.productos == "SKU123"

    dif = DescuentoDiferencialCambiario(
        regla_id="DIF1",
        nombre="35% Fijo",
        tipo_diferencial="fijo_35_ves_usd",
        tipo_calculo="fijo",
        porcentaje_fijo=Decimal("0.35"),
        vigencia_desde=date(2026, 1, 1),
        activo=True,
    )
    dif_row = serde.diferencial_to_row(dif)
    dif_back = serde.diferencial_from_row(dif_row)
    assert dif_back.regla_id == "DIF1"


def test_serie_tasa_averages_serde():
    st = SerieTasa(
        timestamp=datetime(2026, 1, 1, 10, 0, 0),
        tasa_bcv=Decimal("36.5"),
        tasa_binance=Decimal("38.0"),
        fuente="binance+bcv",
        tasa_binance_manana=Decimal("37.8"),
        tasa_binance_tarde=Decimal("38.2"),
        tasa_binance_diario=Decimal("38.0"),
        diferencial_bcv_binance_pct=Decimal("3.94"),
    )
    row = serde.serie_to_row(st)
    st_back = serde.serie_from_row(row)
    assert st_back.tasa_binance_manana == Decimal("37.8")
    assert st_back.diferencial_bcv_binance_pct == Decimal("3.94")


def test_repository_new_discounts():
    class DummyGateway:
        def read_rows(self, table):
            return []

    from cxc.sheets.repository import SheetsRepository

    repo = SheetsRepository(DummyGateway())
    assert len(repo.descuentos_pronto_pago()) == 0
    assert len(repo.descuentos_recompra()) == 0
    assert len(repo.descuentos_producto()) == 0
    assert len(repo.descuentos_diferencial_cambiario()) == 3
    assert len(repo.descuentos_marca_categoria()) == 0
    assert len(repo.descuentos_volumen()) == 0
    assert len(repo.reglas_recurrencia()) == 0
    assert len(repo.promociones_primera_compra()) == 0
    assert len(repo.exclusiones()) == 0
    assert len(repo.feriados()) == 0


def test_effective_dating_helpers():
    from cxc.engine.effective_dating import _vigente

    assert not _vigente(date(2026, 1, 1), date(2026, 12, 31), False, date(2026, 6, 1))
    assert not _vigente(date(2026, 1, 1), date(2026, 12, 31), True, date(2025, 12, 31))
    assert not _vigente(date(2026, 1, 1), date(2026, 12, 31), True, date(2027, 1, 1))
    assert _vigente(date(2026, 1, 1), date(2026, 12, 31), True, date(2026, 6, 1))


def test_rates_scraper_run():
    class DummyBinance:
        def fetch_rate(self):
            return Decimal("38.5")

    class DummyBCV:
        def fetch_rate(self):
            return Decimal("36.5")

    class DummyRepo:
        def __init__(self):
            self.saved = []

        def append_serie_tasa(self, r):
            self.saved.append(r)

        def all_serie_tasas(self):
            return list(self.saved)

    from cxc.alerts import LoggingAlerter
    from cxc.config import ScraperPolicy
    from cxc.scraper.rates_scraper import RatesScraper

    repo = DummyRepo()
    scraper = RatesScraper(
        repo, DummyBinance(), DummyBCV(), ScraperPolicy(fail_alert_threshold=3), LoggingAlerter()
    )
    now = datetime(2026, 1, 1, 9, 0, 0)
    fila = scraper.run(now)
    assert fila.tasa_binance == Decimal("38.5")
    assert fila.tasa_binance_manana == Decimal("38.5")
    assert fila.diferencial_bcv_binance_pct is not None

    now_tarde = datetime(2026, 1, 1, 11, 0, 0)
    fila_tarde = scraper.run(now_tarde)
    assert fila_tarde.tasa_binance_tarde == Decimal("38.5")


def test_linea_orden_presentation_classification():
    """``producto`` es el ID de Odoo (no el nombre) -- la presentación real

    viene de ``presentacion_odoo``, parseada del nombre del producto en
    OdooClient._productos (contenido entre paréntesis al final, ej.
    "... (1x6)" -> "1X6"), no de sniffing sobre el ID."""
    from cxc.models import LineaOrden

    l1 = LineaOrden(
        "L1", "SO1", "101", "GLOBAL OIL", "Comercial", Decimal("2"), Decimal("10"),
        presentacion_odoo="1X6",
    )
    assert l1.presentacion == "1X6"
    assert l1.categoria_madre == "Comercial"

    l2 = LineaOrden(
        "L2", "SO1", "102", "GLOBAL OIL", "Industrial", Decimal("1"), Decimal("50"),
        presentacion_odoo="PAILA",
    )
    assert l2.presentacion == "PAILA"
    assert l2.categoria_madre == "Industrial"

    l3 = LineaOrden(
        "L3", "SO1", "103", "GLOBAL OIL", "Industrial", Decimal("1"), Decimal("200"),
        presentacion_odoo="TAMBOR",
    )
    assert l3.presentacion == "TAMBOR"
    assert l3.categoria_madre == "Industrial"

    # Sin presentacion_odoo (ej. backend Sheets legado) -> guess binario
    # anterior por categoria, como fallback de compatibilidad.
    l4 = LineaOrden(
        "L4", "SO1", "104", "GLOBAL OIL", "Comercial", Decimal("1"), Decimal("10"),
    )
    assert l4.presentacion == "CAJA"
    l5 = LineaOrden(
        "L5", "SO1", "105", "GLOBAL OIL", "Industrial", Decimal("1"), Decimal("10"),
    )
    assert l5.presentacion == "PAILA"


def test_effective_dating_match_categoria():
    from cxc.engine.effective_dating import _match_categoria

    assert _match_categoria("CAJA", "CAJA")
    assert _match_categoria("CAJA", "Comercial")
    assert _match_categoria("PAILA", "PAILA")
    assert _match_categoria("PAILA", "Industrial")
    assert _match_categoria("TAMBOR", "Industrial")
    assert _match_categoria("*", "Comercial")
