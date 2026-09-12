"""Las 24 lecturas directas de `SerieTasas`, y el caché que nadie invalidaba.

Ítem de la Fase 6 del plan, asignado a 2.4, citado textual: *«Quedan 24 lecturas
directas a la base repartidas por `app.py`. Las que pasan las filas por parámetro
están bien; las otras deberían ir por `tasas_vigentes()`»*. Medido el 11-sep-2026:
seguían siendo exactamente 24.

En vez de reescribir 24 sitios, `_all_serie_tasas_rows` pasa a servir del mismo caché
que `tasas_vigentes()`: mismos datos, una consulta cada cinco minutos en vez de una
por request.

**Lo que hizo falta antes, y era un hallazgo propio.** `invalidar_tasas()` no tenía
**ningún** llamador. Su docstring decía «para después de tocar la tabla a mano — si no,
la corrección no se ve hasta que vence el TTL», y nadie la llamaba: ni el scraper, ni
la carga manual por la web, ni el import desde Odoo. Así que una tasa recién cargada la
veían al instante los 24 lectores sin caché y tardaba hasta cinco minutos en verla
`tasas_vigentes()`: dos lectores en desacuerdo durante esa ventana. Cachear las 24
lecturas sin arreglar eso habría roto la carga manual.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from cxc.models import SerieTasa
from cxc.web import app as app_module


def _fila(bcv: str) -> SerieTasa:
    return SerieTasa(
        timestamp=datetime(2026, 9, 11, 10, 0, 0),
        tasa_bcv=Decimal(bcv),
        tasa_binance=Decimal("40.0"),
        fuente="test",
    )


def test_la_segunda_lectura_no_vuelve_a_la_base() -> None:
    """Es el punto: 24 sitios leían la base en cada request."""
    repo = MagicMock()
    repo.all_serie_tasas.return_value = [_fila("36.0")]
    app_module.invalidar_tasas()

    primera = app_module._all_serie_tasas_rows(repo)
    segunda = app_module._all_serie_tasas_rows(repo)
    assert primera == segunda
    assert repo.all_serie_tasas.call_count == 1, "la segunda lectura salió del caché"


def test_invalidar_tasas_olvida_tambien_las_filas() -> None:
    """Si el caché nuevo no entrara en `invalidar_tasas`, una corrección a mano se
    vería en `tasas_vigentes()` y no en los 24 lectores -- el desacuerdo, al revés."""
    repo = MagicMock()
    repo.all_serie_tasas.return_value = [_fila("36.0")]
    app_module.invalidar_tasas()
    app_module._all_serie_tasas_rows(repo)

    repo.all_serie_tasas.return_value = [_fila("37.0")]
    app_module.invalidar_tasas()
    filas = app_module._all_serie_tasas_rows(repo)
    assert filas[0]["tasa_bcv"].startswith("37"), "después de invalidar se relee"
    assert repo.all_serie_tasas.call_count == 2


def test_se_devuelve_una_copia_para_que_mutarla_no_toque_el_cache() -> None:
    """Hay un llamador que hace `[-15:]` y otros que ordenan la lista."""
    repo = MagicMock()
    repo.all_serie_tasas.return_value = [_fila("36.0"), _fila("36.5")]
    app_module.invalidar_tasas()

    filas = app_module._all_serie_tasas_rows(repo)
    filas.clear()
    assert len(app_module._all_serie_tasas_rows(repo)) == 2, "el caché no se vació"


def test_los_tres_escritores_invalidan_el_cache() -> None:
    """La guarda del hallazgo: `invalidar_tasas()` no tenía llamadores.

    Los tres sitios que escriben la serie -- el scraper del demonio, la carga manual
    por la web y el import desde Odoo -- tienen que invalidar al escribir. Si alguien
    agrega un cuarto escritor sin invalidar, este test no lo ve; si alguien quita uno
    de estos tres, sí.
    """
    fuente = Path("src/cxc/web/app.py").read_text(encoding="utf-8")
    llamadas = [
        n
        for n, ln in enumerate(fuente.split("\n"), 1)
        if "invalidar_tasas()" in ln
        and "def invalidar_tasas" not in ln
        and not ln.strip().startswith("#")
    ]
    assert len(llamadas) >= 3, f"invalidar_tasas() se llama en {len(llamadas)} sitio(s); eran cero"

    # Y cada escritura de la serie tiene una invalidación cerca, después.
    lineas = fuente.split("\n")
    escrituras = [n for n, ln in enumerate(lineas, 1) if "append_serie_tasa(" in ln]
    assert (
        len(escrituras) == 2
    ), "carga manual + import desde Odoo (el scraper escribe desde su módulo)"
    for n in escrituras:
        ventana = "\n".join(lineas[n : n + 8])
        assert "invalidar_tasas()" in ventana, f"la escritura de la línea {n} no invalida"
    # El scraper escribe desde `rates_scraper.py`; el demonio invalida al volver.
    i = fuente.index("fila = await asyncio.to_thread(scraper.run, now_caracas)")
    assert "invalidar_tasas()" in fuente[i : i + 600]
