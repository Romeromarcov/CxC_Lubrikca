"""``get_rate_for_datetime`` -- auditoría de tasas históricas (agosto 2026).

``SerieTasas`` (scraper horario) solo cubre desde que el cron corre en
producción (2026-07-25). Para fechas anteriores, la función debe caer a
``TasasHistoricasAuditoria`` (poblada desde el CSV de Sheets, tasa BCV real
de Odoo día a día + Binance real/estimado) ANTES de usar los defaults
hardcodeados 36.5/38.0 -- esos solo deben salir si NINGUNA fuente tiene
dato para esa fecha.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from cxc.web.app import get_bcv_euro_rate_for_datetime, get_rate_for_datetime


def test_usa_serie_tasas_si_hay_captura_del_mismo_dia() -> None:
    mock_repo = MagicMock()
    mock_repo.all_tasas_historicas_auditoria.return_value = []
    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        rows = [
            {
                "timestamp": "2026-07-25 10:00:00",
                "tasa_bcv": "742.2292",
                "tasa_binance": "871.8974",
            }
        ]
        bcv, binance = get_rate_for_datetime(datetime(2026, 7, 25, 15, 0), rows)
    assert bcv == Decimal("742.2292")
    assert binance == Decimal("871.8974")


def test_cae_a_tasas_historicas_auditoria_si_serie_tasas_no_tiene_ese_dia() -> None:
    """Fecha 2026-05-15, muy anterior a que el scraper de SerieTasas

    existiera (2026-07-25) -- debe usar TasasHistoricasAuditoria, NO el
    default hardcodeado 36.5/38.0."""
    mock_repo = MagicMock()
    mock_repo.all_tasas_historicas_auditoria.return_value = [
        {
            "fecha": "2026-05-15",
            "tasa_bcv_usd": "515.18",
            "tasa_bcv_euro": "601.452",
            "tasa_binance_promedio_diario": "603.791",
        }
    ]
    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        # SerieTasas solo tiene una fila de julio -- muy lejos de mayo.
        rows = [{"timestamp": "2026-07-25 10:00:00", "tasa_bcv": "742.0", "tasa_binance": "870.0"}]
        bcv, binance = get_rate_for_datetime(datetime(2026, 5, 15, 12, 0), rows)
    assert bcv == Decimal("515.18")
    assert binance == Decimal("603.791")


def test_usa_default_hardcodeado_solo_si_ninguna_fuente_tiene_dato() -> None:
    mock_repo = MagicMock()
    mock_repo.all_tasas_historicas_auditoria.return_value = []
    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        bcv, binance = get_rate_for_datetime(datetime(2025, 1, 1, 12, 0), [])
    assert bcv == Decimal("36.5")
    assert binance == Decimal("38.0")


def test_bcv_euro_rechaza_fila_implausible_ratio_bajo() -> None:
    """Bug real (agosto 2026): OdooBcvClient leia la tasa EUR de Odoo, que

    quedo congelada un mes mientras BCV-USD si se actualizaba a diario --
    SerieTasas repetia fielmente ese valor viejo. Una fila donde EUR/BCV
    cae por debajo de ~1.05 (nunca ocurre en datos reales -- EUR siempre
    supera a BCV-USD por un margen mayor) se rechaza como implausible."""
    rows = [
        {
            "timestamp": "2026-08-06 10:00:00",
            "tasa_bcv": "755.9001",
            "tasa_bcv_euro": "770.682890",  # ratio ~1.0195 -- congelada
        }
    ]
    resultado = get_bcv_euro_rate_for_datetime(datetime(2026, 8, 6, 12, 0), rows)
    assert resultado is None


def test_bcv_euro_acepta_fila_con_ratio_plausible() -> None:
    rows = [
        {
            "timestamp": "2026-08-06 10:00:00",
            "tasa_bcv": "755.9001",
            "tasa_bcv_euro": "872.83",  # ratio ~1.155 -- plausible
        }
    ]
    resultado = get_bcv_euro_rate_for_datetime(datetime(2026, 8, 6, 12, 0), rows)
    assert resultado == Decimal("872.83")


def test_bcv_euro_rechaza_fila_de_otro_dia_aunque_ratio_sea_plausible() -> None:
    """Bug real (agosto 2026, auditoria de N/C de marzo): sin guardia de

    fecha, para un ``dt`` de marzo (antes de que el scraper empezara a
    capturar EUR el 2026-07-31) "la fila mas cercana" terminaba siendo la
    primera fila de SerieTasas disponible -- semanas/meses despues -- y
    esa fila ganaba silenciosamente sobre el fallback correcto de
    TasasHistoricasAuditoria porque pasaba el filtro de ratio (1.14,
    plausible en si misma, solo que de la fecha equivocada). Debe devolver
    None para que el llamador caiga a get_eur_rate_for_date."""
    rows = [
        {
            "timestamp": "2026-07-31 10:52:05",
            "tasa_bcv": "674.9305",
            "tasa_bcv_euro": "770.682890",  # ratio ~1.1419 -- plausible en si misma
        }
    ]
    resultado = get_bcv_euro_rate_for_datetime(datetime(2026, 3, 3, 12, 0), rows)
    assert resultado is None
