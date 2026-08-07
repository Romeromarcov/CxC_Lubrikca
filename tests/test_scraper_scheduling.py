"""Agenda del scraper de tasas (app.py) -- pedido del usuario (agosto 2026):

capturar cada hora EN PUNTO entre las 6:00 y las 22:00 (Caracas), primera
captura exactamente a las 6:00.
"""

from __future__ import annotations

from datetime import datetime

from cxc.web.app import (
    _SCRAPER_HORA_FIN,
    _SCRAPER_HORA_INICIO,
    _segundos_hasta_proxima_hora_en_punto,
)


def test_ventana_de_captura_es_6_a_22() -> None:
    assert _SCRAPER_HORA_INICIO == 6
    assert _SCRAPER_HORA_FIN == 22


def test_segundos_hasta_proxima_hora_en_punto_desde_mitad_de_hora() -> None:
    ahora = datetime(2026, 8, 6, 14, 7, 30)
    segundos = _segundos_hasta_proxima_hora_en_punto(ahora)
    # De 14:07:30 a 15:00:00 -> 52 min 30 s = 3150 s.
    assert segundos == 3150.0


def test_segundos_hasta_proxima_hora_en_punto_ya_en_el_filo() -> None:
    ahora = datetime(2026, 8, 6, 14, 0, 0)
    segundos = _segundos_hasta_proxima_hora_en_punto(ahora)
    assert segundos == 3600.0
