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

from cxc.web.app import get_rate_for_datetime


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
