"""Cambiar la variante BCV de una vinculación: la vuelta a USD, que nadie probaba.

`post_cambiar_tipo_tasa_bcv` recongela el equivalente de una vinculación con otra tasa
—USD o EUR—, y es de las pocas rutas que reescriben un equivalente **congelado**. La ida
a EUR tenía su test e2e. La vuelta a USD, que resuelve la tasa del día con
`get_rate_for_datetime`, no tenía ninguno: ni el caso feliz ni el `TasaNoDisponible`
que desde la Fase 2.1 es un error duro en vez de 36,5/38,0.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import cxc.web.app as app
from cxc.models import Moneda, SerieTasa, Vinculacion
from cxc.rates import TasaNoDisponible


def _vinc(variante="EUR", tasa="40.0"):
    return Vinculacion(
        vinc_id="V1",
        pago_id="P1",
        so_id="S00010",
        monto_aplicado=Decimal("82774.00"),
        hora_pago_confirmada=datetime(2026, 9, 11, 10, 0),
        tasa_bcv_aplicada=Decimal(tasa),
        tasa_binance_aplicada=Decimal("961.67"),
        es_tasa_heredada=False,
        moneda_abono=Moneda.VES,
        bcv_variante=variante,
    )


def _pedir(cuerpo, *, serie=None, parches=()):
    async def _nada():
        return None

    repo = MagicMock()
    repo.all_vinculaciones.return_value = [_vinc()]
    repo.all_serie_tasas.return_value = serie or []
    repo.all_tasas_historicas_auditoria.return_value = []
    with ExitStack() as pila:
        for parche in (
            patch("cxc.web.app.get_repo", return_value=repo),
            patch("cxc.web.app.hay_sesion_valida", return_value=True),
            patch("cxc.web.app.recalculate_all"),
            patch("cxc.web.app.run_scraper_in_background", _nada),
            patch("cxc.web.app.run_sync_in_background", _nada),
            patch("cxc.web.app._aplicar_migraciones_pendientes"),
            *parches,
        ):
            pila.enter_context(parche)
        app.invalidar_tasas()
        c = pila.enter_context(TestClient(app.app, raise_server_exceptions=False))
        r = c.post("/api/vinculacion/V1/tasa-bcv-tipo", json=cuerpo)
    return r, repo


def test_volver_a_USD_recongela_con_la_tasa_del_dia() -> None:
    """82.774 Bs a 827,74 = 100 USD. El equivalente congelado se reescribe con esa
    tasa y con `q6`, igual que las otras rutas."""
    r, repo = _pedir(
        {"variante": "usd"},
        parches=(
            patch(
                "cxc.web.app.get_rate_for_datetime",
                return_value=(Decimal("827.74"), Decimal("961.67")),
            ),
        ),
    )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["bcv_variante"] == "USD", "se normaliza a mayúsculas"
    assert cuerpo["tasa_bcv_aplicada"] == 827.74
    assert cuerpo["equiv_usd_bcv"] == 100.0
    assert cuerpo["equiv_ves_bcv"] == 82774.0
    guardada = repo.update_vinculacion.call_args[0][0]
    assert guardada.bcv_variante == "USD"
    assert guardada.equiv_usd_bcv == Decimal("100.000000"), "seis decimales, como las demás rutas"


def test_volver_a_USD_sin_tasa_para_ese_dia_es_400_y_no_recongela() -> None:
    """Desde la Fase 2.1 `get_rate_for_datetime` lanza en vez de devolver 36,5/38,0.
    Acá eso tiene que ser un 400 que nombre la fecha, y la vinculación no se toca."""
    r, repo = _pedir(
        {"variante": "USD"},
        parches=(
            patch("cxc.web.app.get_rate_for_datetime", side_effect=TasaNoDisponible("sin tasa")),
        ),
    )
    assert r.status_code == 400, r.text
    assert "2026-09-11" in r.json()["detail"]
    repo.update_vinculacion.assert_not_called()


@pytest.mark.parametrize("variante", ["BTC", "", "euro"])
def test_una_variante_que_no_es_USD_ni_EUR_es_400(variante) -> None:
    r, repo = _pedir({"variante": variante})
    assert r.status_code == 400
    assert "'USD' o 'EUR'" in r.json()["detail"]
    repo.update_vinculacion.assert_not_called()


def test_una_tasa_del_dia_en_cero_no_se_congela() -> None:
    """La serie puede traer un cero (captura fallida). Recongelar con cero dejaría el
    equivalente sin sentido para siempre."""
    r, repo = _pedir(
        {"variante": "USD"},
        parches=(
            patch(
                "cxc.web.app.get_rate_for_datetime",
                return_value=(Decimal("0"), Decimal("961.67")),
            ),
        ),
    )
    assert r.status_code == 400
    assert "<= 0" in r.json()["detail"]
    repo.update_vinculacion.assert_not_called()


def test_ir_a_EUR_con_tasa_euro_en_la_serie_recongela_con_ella() -> None:
    """La ida, para tener las dos direcciones en el mismo archivo."""
    serie = [
        SerieTasa(
            timestamp=datetime(2026, 9, 11, 10, 0, 0),
            tasa_bcv=Decimal("827.74"),
            tasa_binance=Decimal("961.67"),
            fuente="test",
            tasa_bcv_euro=Decimal("963.21"),
        )
    ]
    r, repo = _pedir({"variante": "EUR"}, serie=serie)
    assert r.status_code == 200, r.text
    assert r.json()["tasa_bcv_aplicada"] == 963.21
    guardada = repo.update_vinculacion.call_args[0][0]
    assert guardada.bcv_variante == "EUR"
    assert abs(float(guardada.equiv_usd_bcv) - 82774 / 963.21) < 1e-5


def test_una_vinculacion_que_no_existe_es_404() -> None:
    async def _nada():
        return None

    repo = MagicMock()
    repo.all_vinculaciones.return_value = []
    with (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app.hay_sesion_valida", return_value=True),
        patch("cxc.web.app.run_scraper_in_background", _nada),
        patch("cxc.web.app.run_sync_in_background", _nada),
        patch("cxc.web.app._aplicar_migraciones_pendientes"),
        TestClient(app.app, raise_server_exceptions=False) as c,
    ):
        r = c.post("/api/vinculacion/NO/tasa-bcv-tipo", json={"variante": "USD"})
    assert r.status_code == 404
