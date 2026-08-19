"""Fase C (plan de Inventario/Catalogo, agosto 2026, pedido explicito del

usuario): clasificacion Industrial/Comercial de listas de precio, un eje
ortogonal al USD/VES existente. Solo de consulta -- no alimenta el motor
de descuentos.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from cxc.web import app as app_module
from cxc.web.app import (
    _CLASIFICACION_KEYS,
    app,
    get_pricelist_clasificacion,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_clasificacion_cache():
    """Evita fugas de estado global entre tests -- mismo patron que

    _PRICELIST_MAPEO_CACHE, cache de proceso a nivel de modulo."""
    app_module._PRICELIST_CLASIFICACION_CACHE.clear()
    with patch.object(app_module, "_load_clasificacion_from_json", return_value=None), \
         patch.object(app_module, "_save_clasificacion_to_json"):
        yield
    app_module._PRICELIST_CLASIFICACION_CACHE.clear()


def _mock_repo_con_config(config: dict[str, str]) -> MagicMock:
    repo = MagicMock()
    repo.get_config.side_effect = lambda k: config.get(k)
    return repo


def test_get_pricelist_clasificacion_vacio_sin_config():
    repo = _mock_repo_con_config({})
    result = get_pricelist_clasificacion(repo)
    assert result == {k: [] for k in _CLASIFICACION_KEYS}


def test_get_pricelist_clasificacion_lee_config_persistida():
    repo = _mock_repo_con_config(
        {
            "valid_pricelists_industrial_usd": "8",
            "valid_pricelists_industrial_ves": "9",
            "valid_pricelists_comercial_usd": "7,8",
            "valid_pricelists_comercial_ves": "3,5",
        }
    )
    result = get_pricelist_clasificacion(repo)
    assert result["industrial_usd"] == ["8"]
    assert result["industrial_ves"] == ["9"]
    assert result["comercial_usd"] == ["7", "8"]
    assert result["comercial_ves"] == ["3", "5"]


def test_get_endpoint_devuelve_clasificacion():
    repo = _mock_repo_con_config({"valid_pricelists_industrial_ves": "9"})
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.get("/api/config/listas-precio-clasificacion")
    assert res.status_code == 200
    data = res.json()
    assert data["industrial_ves"] == ["9"]
    assert data["comercial_usd"] == []


def test_post_endpoint_guarda_clasificacion_via_set_config():
    repo = _mock_repo_con_config({})
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.post(
            "/api/config/listas-precio-clasificacion",
            json={
                "industrial_usd": ["8"],
                "industrial_ves": ["9"],
                "comercial_usd": ["7", "8"],
                "comercial_ves": ["3", "5"],
            },
        )
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    calls = {c.args[0]: c.args[1] for c in repo.set_config.call_args_list}
    assert calls["valid_pricelists_industrial_usd"] == "8"
    assert calls["valid_pricelists_industrial_ves"] == "9"
    assert calls["valid_pricelists_comercial_usd"] == "7,8"
    assert calls["valid_pricelists_comercial_ves"] == "3,5"


def test_post_endpoint_acepta_listas_vacias():
    repo = _mock_repo_con_config({})
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.post("/api/config/listas-precio-clasificacion", json={})
    assert res.status_code == 200
