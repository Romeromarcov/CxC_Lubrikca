"""``get_rate_for_datetime`` -- auditoría de tasas históricas (agosto 2026).

``SerieTasas`` (scraper horario) solo cubre desde que el cron corre en
producción (2026-07-25). Para fechas anteriores, la función debe caer a
``TasasHistoricasAuditoria`` (poblada desde el CSV de Sheets, tasa BCV real
de Odoo día a día + Binance real/estimado) ANTES de usar los defaults
hardcodeados 36.5/38.0 -- esos solo deben salir si NINGUNA fuente tiene
dato para esa fecha.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from cxc.rates import TasaNoDisponible
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


def test_sin_ninguna_fuente_no_se_inventa_una_tasa() -> None:
    """Era la especificación del default de 2019, y ahora es la de su ausencia.

    Este test asertaba ``36,5 / 38,0``. Se reescribió el 11-sep-2026 por decisión
    del usuario: un equivalente calculado con esa tasa se **congela** en la
    vinculación y por diseño no se vuelve a mirar. En el espejo de prueba hay
    1.463 con ese valor escrito, acreditando 2.260.174,60 USD donde correspondían
    unos 99.664 — 22,7 veces.

    Devolver un número inventado no es degradarse con elegancia: es escribir una
    cifra mala donde nadie la va a corregir.
    """
    mock_repo = MagicMock()
    mock_repo.all_tasas_historicas_auditoria.return_value = []
    with patch("cxc.web.app.get_repo", return_value=mock_repo), pytest.raises(TasaNoDisponible):
        get_rate_for_datetime(datetime(2025, 1, 1, 12, 0), [])


def test_el_error_dice_la_fecha_que_falta() -> None:
    """Sin la fecha, el error manda a revisar toda la serie en vez de un día."""
    mock_repo = MagicMock()
    mock_repo.all_tasas_historicas_auditoria.return_value = []
    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        pytest.raises(TasaNoDisponible, match="2025-01-01"),
    ):
        get_rate_for_datetime(datetime(2025, 1, 1, 12, 0), [])


# Las guardas del euro se migraron a ``cxc.rates.Tasas`` (septiembre 2026),
# donde viven junto al resto de la política de tasas. Los casos que
# cubrían estos tests siguen fijados en ``tests/test_rates.py``; acá queda
# el que le faltaba: una fila sin ``tasa_bcv_usd`` propio no tiene contra
# qué medir el ratio, y descartarla por eso sería perder un dato bueno.


def test_un_euro_sin_usd_en_la_misma_fila_no_se_descarta() -> None:
    from cxc.rates import Tasas

    t = Tasas(historicas=[{"fecha": "2026-03-18", "tasa_bcv_euro": "520.642"}])
    assert t.bcv_eur(date(2026, 3, 18)) == Decimal("520.642")
