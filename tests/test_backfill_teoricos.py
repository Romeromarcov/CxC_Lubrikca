"""El backfill manual de `ventas_teoricos`, que **escribe** cifras de dinero.

Salió del barrido de cobertura sin **ninguna** línea cubierta, y es un endpoint que
recalcula y guarda el teórico de cientos de órdenes. Lo que se fija acá:

1. que un Odoo caído dé **503 y no 500 ni "ok"**: sin Odoo no hay precios, y un backfill
   que corre sin precios llenaría la tabla de ceros;
2. que el límite viaje al motor, porque es lo que permite ir en tandas en vez de tardar
   varios minutos en un solo request;
3. de qué fuente de configuración salen las listas, que es el hallazgo de la pieza 26:
   este endpoint lee el **mapeo unificado** y no las claves `valid_pricelists_*`, y las
   dos difieren. Fijarlo hace visible cuál usa el que escribe.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import cxc.web.app as app


def _pedir(ruta, *, execute=None, procesadas=7, usd=("11",), ves=("10",), runner_explota=False):
    async def _nada():
        return None

    repo = MagicMock()
    runner = MagicMock()
    if runner_explota:
        runner.run_teoricos_pendientes.side_effect = RuntimeError("el motor se cayó")
    else:
        runner.run_teoricos_pendientes.return_value = procesadas

    with ExitStack() as pila:
        for parche in (
            patch("cxc.web.app.get_repo", return_value=repo),
            patch(
                "cxc.web.app._connect",
                return_value=(execute if execute is not None else (lambda *a, **k: [])),
            ),
            patch(
                "cxc.web.app.get_valid_pricelists_usd_and_ves",
                return_value=(list(usd), list(ves)),
            ),
            patch("cxc.web.app._resolvedor_de_precios", return_value=MagicMock()),
            patch("cxc.web.app.EngineRunner", return_value=runner),
            patch("cxc.web.app.hay_sesion_valida", return_value=True),
            patch("cxc.web.app.run_scraper_in_background", _nada),
            patch("cxc.web.app.run_sync_in_background", _nada),
            patch("cxc.web.app._aplicar_migraciones_pendientes"),
        ):
            pila.enter_context(parche)
        cliente = pila.enter_context(TestClient(app.app, raise_server_exceptions=False))
        r = cliente.get(ruta)
    return r, runner, repo


@pytest.mark.parametrize("metodo", ["get", "post"])
def test_responde_por_GET_y_por_POST(metodo) -> None:
    """Está declarado con `api_route(methods=["GET", "POST"])`: se corre a mano desde
    el navegador, así que el GET tiene que servir."""
    async def _nada():
        return None

    runner = MagicMock()
    runner.run_teoricos_pendientes.return_value = 3
    with ExitStack() as pila:
        for parche in (
            patch("cxc.web.app.get_repo", return_value=MagicMock()),
            patch("cxc.web.app._connect", return_value=lambda *a, **k: []),
            patch("cxc.web.app.get_valid_pricelists_usd_and_ves", return_value=(["11"], ["10"])),
            patch("cxc.web.app._resolvedor_de_precios", return_value=MagicMock()),
            patch("cxc.web.app.EngineRunner", return_value=runner),
            patch("cxc.web.app.hay_sesion_valida", return_value=True),
            patch("cxc.web.app.run_scraper_in_background", _nada),
            patch("cxc.web.app.run_sync_in_background", _nada),
            patch("cxc.web.app._aplicar_migraciones_pendientes"),
        ):
            pila.enter_context(parche)
        cliente = pila.enter_context(TestClient(app.app))
        r = getattr(cliente, metodo)("/api/backfill/ventas-teoricos")
    assert r.status_code == 200


def test_devuelve_cuantas_ordenes_proceso() -> None:
    r, _runner, _repo = _pedir("/api/backfill/ventas-teoricos", procesadas=42)
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["status"] == "ok"
    assert cuerpo["ordenes_procesadas"] == 42
    assert "42 orden(es)" in cuerpo["mensaje"]


def test_sin_Odoo_da_503_y_NO_corre_el_motor() -> None:
    """Es la razón por la que este test existe.

    Sin Odoo no hay precios, y un backfill que corriera igual escribiría teóricos en
    cero sobre cientos de órdenes. El 503 dice «vuelve a intentar», que es lo correcto;
    un 500 sugeriría un error del servidor y un 200 sería una mentira.
    """
    r, runner, _repo = _pedir("/api/backfill/ventas-teoricos", execute=False)
    assert r.status_code == 503
    assert "Odoo" in r.json()["detail"]
    runner.run_teoricos_pendientes.assert_not_called()


def test_el_limite_viaja_al_motor_para_poder_ir_en_tandas() -> None:
    """Sin el límite el request tarda varios minutos; con él se corre por partes."""
    _r, runner, _repo = _pedir("/api/backfill/ventas-teoricos?limite=50")
    assert runner.run_teoricos_pendientes.call_args[0][1] == 50


def test_sin_limite_se_pasa_None_y_el_motor_decide() -> None:
    """`None` no es cero: cero no procesaría nada."""
    _r, runner, _repo = _pedir("/api/backfill/ventas-teoricos")
    assert runner.run_teoricos_pendientes.call_args[0][1] is None


def test_las_listas_salen_del_MAPEO_UNIFICADO_y_no_de_las_claves() -> None:
    """El hallazgo de la pieza 26, fijado del lado del que escribe.

    Cinco sitios deciden con qué lista se valora un teórico leyendo de dos fuentes que
    nada sincroniza —medido: USD=11/BCV=10 contra USD=4/BCV=5, estas últimas
    archivadas—. Este endpoint **escribe** `ventas_teoricos`, así que cuál fuente usa no
    es un detalle. Queda fijado para que un cambio de fuente sea deliberado y no un
    descuido.
    """
    with ExitStack() as pila:
        lector = pila.enter_context(
            patch(
                "cxc.web.app.get_valid_pricelists_usd_and_ves",
                return_value=(["11"], ["10"]),
            )
        )
        otro = pila.enter_context(patch("cxc.web.app.get_ui_pricelist_ids"))
        resolvedor = pila.enter_context(
            patch("cxc.web.app._resolvedor_de_precios", return_value=MagicMock())
        )
        runner = MagicMock()
        runner.run_teoricos_pendientes.return_value = 1

        async def _nada():
            return None

        for parche in (
            patch("cxc.web.app.get_repo", return_value=MagicMock()),
            patch("cxc.web.app._connect", return_value=lambda *a, **k: []),
            patch("cxc.web.app.EngineRunner", return_value=runner),
            patch("cxc.web.app.hay_sesion_valida", return_value=True),
            patch("cxc.web.app.run_scraper_in_background", _nada),
            patch("cxc.web.app.run_sync_in_background", _nada),
            patch("cxc.web.app._aplicar_migraciones_pendientes"),
        ):
            pila.enter_context(parche)
        cliente = pila.enter_context(TestClient(app.app))
        r = cliente.get("/api/backfill/ventas-teoricos")

    assert r.status_code == 200
    lector.assert_called_once()
    otro.assert_not_called(), "no lee las claves valid_pricelists_*"
    assert resolvedor.call_args[0][2] == ["11"]
    assert resolvedor.call_args[0][3] == ["10"]


def test_si_el_motor_revienta_el_backfill_NO_dice_ok() -> None:
    """500, no un "ok" con cero órdenes: nadie debe creer que el backfill corrió."""
    r, _runner, _repo = _pedir("/api/backfill/ventas-teoricos", runner_explota=True)
    assert r.status_code == 500
