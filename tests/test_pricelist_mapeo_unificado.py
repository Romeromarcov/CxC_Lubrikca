"""Mapeo UNIFICADO de listas de precio (agosto 2026, pedido explicito del

usuario): reemplaza los DOS sistemas de mapeo que existian por separado
(valid_pricelists_usd/_ves para el motor, y la clasificacion Industrial/
Comercial de la Fase C, solo consulta) -- una unica fuente de verdad por
lista de precios, con un campo nuevo "vigente" para elegir cual lista
mostrar en Inventario cuando haya mas de una candidata por grupo.
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
    get_pricelist_mapeo,
    get_pricelist_vigente_por_grupo,
    get_valid_pricelists_usd_and_ves,
    set_pricelist_mapeo,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_mapeo_cache():
    app_module._PRICELIST_MAPEO_CACHE_UNIFICADO.clear()
    with (
        patch.object(app_module, "_load_pricelist_mapeo_from_json", return_value=None),
        patch.object(app_module, "_save_pricelist_mapeo_to_json"),
    ):
        yield
    app_module._PRICELIST_MAPEO_CACHE_UNIFICADO.clear()


def _mock_repo_con_config(config: dict[str, str]) -> MagicMock:
    repo = MagicMock()
    repo.get_config.side_effect = lambda k: config.get(k)
    return repo


# --- get_pricelist_mapeo: lectura pura + migración legada -------------------


def test_get_pricelist_mapeo_vacio_sin_config():
    repo = _mock_repo_con_config({})
    assert get_pricelist_mapeo(repo) == {}


def test_get_pricelist_mapeo_lee_json_unificado_ya_guardado():
    guardado = '{"8": {"moneda": "usd", "categoria": "comercial", "vigente": true}}'
    repo = _mock_repo_con_config({"pricelist_mapeo_unificado": guardado})
    result = get_pricelist_mapeo(repo)
    assert result == {"8": {"moneda": "usd", "categoria": "comercial", "vigente": True}}


def test_get_pricelist_mapeo_migra_claves_legadas_si_no_hay_unificado():
    """Sin mapeo unificado guardado, pero CON las 6 claves legadas

    (bug real que motivó la unificación: dos sistemas separados que podían
    desincronizarse) -- se reconstruye automáticamente, sin inventar
    "vigente" (campo nuevo sin equivalente legado)."""
    repo = _mock_repo_con_config(
        {
            "valid_pricelists_usd": "7,8",
            "valid_pricelists_ves": "3,4,5,9",
            "valid_pricelists_comercial_usd": "8",
            "valid_pricelists_comercial_ves": "5",
            "valid_pricelists_industrial_ves": "9",
        }
    )
    result = get_pricelist_mapeo(repo)
    assert result["7"] == {"moneda": "usd", "categoria": "", "vigente": False}
    assert result["8"] == {"moneda": "usd", "categoria": "comercial", "vigente": False}
    assert result["5"] == {"moneda": "ves", "categoria": "comercial", "vigente": False}
    assert result["9"] == {"moneda": "ves", "categoria": "industrial", "vigente": False}
    assert result["3"] == {"moneda": "ves", "categoria": "", "vigente": False}


def test_get_pricelist_mapeo_migracion_persiste_para_no_repetirse():
    repo = _mock_repo_con_config({"valid_pricelists_usd": "8"})
    get_pricelist_mapeo(repo)
    repo.set_config.assert_called_once()
    assert repo.set_config.call_args[0][0] == "pricelist_mapeo_unificado"


# --- get_valid_pricelists_usd_and_ves: sigue alimentando al motor -----------


def test_get_valid_pricelists_usd_and_ves_deriva_de_moneda():
    repo = MagicMock()
    with patch.object(
        app_module,
        "get_pricelist_mapeo",
        return_value={
            "7": {"moneda": "usd", "categoria": "", "vigente": False},
            "8": {"moneda": "usd", "categoria": "comercial", "vigente": True},
            "5": {"moneda": "ves", "categoria": "comercial", "vigente": True},
        },
    ):
        usd, ves = get_valid_pricelists_usd_and_ves(repo)
    assert sorted(usd) == ["7", "8"]
    assert ves == ["5"]


def test_get_valid_pricelists_usd_and_ves_cae_a_env_si_mapeo_vacio():
    repo = MagicMock()
    with patch.object(app_module, "get_pricelist_mapeo", return_value={}):
        usd, ves = get_valid_pricelists_usd_and_ves(repo)
    assert usd == ["4"]
    assert ves == ["5"]


# --- get_pricelist_clasificacion: Inventario, solo consulta -----------------


def test_get_pricelist_clasificacion_agrupa_por_categoria_y_moneda():
    with patch.object(
        app_module,
        "get_pricelist_mapeo",
        return_value={
            "8": {"moneda": "usd", "categoria": "comercial", "vigente": True},
            "5": {"moneda": "ves", "categoria": "comercial", "vigente": True},
            "9": {"moneda": "ves", "categoria": "industrial", "vigente": True},
            "7": {"moneda": "usd", "categoria": "", "vigente": False},
        },
    ):
        result = get_pricelist_clasificacion(None)
    assert result["comercial_usd"] == ["8"]
    assert result["comercial_ves"] == ["5"]
    assert result["industrial_ves"] == ["9"]
    assert result["industrial_usd"] == []
    assert set(result.keys()) == set(_CLASIFICACION_KEYS)


# --- get_pricelist_vigente_por_grupo: UN id por grupo (Inventario) ---------


def test_get_pricelist_vigente_por_grupo_solo_las_marcadas_vigente():
    with patch.object(
        app_module,
        "get_pricelist_mapeo",
        return_value={
            "8": {"moneda": "usd", "categoria": "comercial", "vigente": True},
            "7": {"moneda": "usd", "categoria": "comercial", "vigente": False},
            "5": {"moneda": "ves", "categoria": "comercial", "vigente": True},
            "9": {"moneda": "ves", "categoria": "industrial", "vigente": True},
        },
    ):
        result = get_pricelist_vigente_por_grupo(None)
    assert result["comercial_usd"] == "8"
    assert result["comercial_ves"] == "5"
    assert result["industrial_ves"] == "9"
    assert result["industrial_usd"] is None


def test_get_pricelist_vigente_por_grupo_vacio_si_nada_marcado():
    with patch.object(app_module, "get_pricelist_mapeo", return_value={}):
        result = get_pricelist_vigente_por_grupo(None)
    assert all(v is None for v in result.values())


# --- Endpoints GET/POST unificados ------------------------------------------


def test_get_endpoint_devuelve_mapeo_y_flag_historico():
    guardado = '{"8": {"moneda": "usd", "categoria": "comercial", "vigente": true}}'
    repo = _mock_repo_con_config(
        {
            "pricelist_mapeo_unificado": guardado,
            "historical_pricelist_enabled": "false",
        }
    )
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.get("/api/config/pricelist-mapeo")
    assert res.status_code == 200
    data = res.json()
    assert data["mapeo"]["8"]["vigente"] is True
    assert data["historical_pricelist_enabled"] is False


def test_post_endpoint_guarda_mapeo_completo():
    repo = _mock_repo_con_config({})
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.post(
            "/api/config/pricelist-mapeo",
            json={
                "mapeo": {
                    "8": {"moneda": "usd", "categoria": "comercial", "vigente": True},
                    "5": {"moneda": "ves", "categoria": "comercial", "vigente": True},
                    "9": {"moneda": "ves", "categoria": "industrial", "vigente": True},
                },
                "historical_pricelist_enabled": True,
            },
        )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["mapeo"]["9"]["categoria"] == "industrial"
    saved_key, saved_json = repo.set_config.call_args_list[0].args
    assert saved_key == "pricelist_mapeo_unificado"
    assert '"9"' in saved_json


def test_post_endpoint_ignora_moneda_o_categoria_invalidas():
    repo = _mock_repo_con_config({})
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.post(
            "/api/config/pricelist-mapeo",
            json={"mapeo": {"1": {"moneda": "eur", "categoria": "rara", "vigente": True}}},
        )
    assert res.status_code == 200
    assert res.json()["mapeo"]["1"] == {"moneda": "", "categoria": "", "vigente": True}


def test_set_pricelist_mapeo_actualiza_cache_y_config():
    repo = MagicMock()
    mapeo = {"8": {"moneda": "usd", "categoria": "comercial", "vigente": True}}
    set_pricelist_mapeo(mapeo, repo)
    assert mapeo == app_module._PRICELIST_MAPEO_CACHE_UNIFICADO
    esperado = '{"8": {"moneda": "usd", "categoria": "comercial", "vigente": true}}'
    repo.set_config.assert_called_once_with("pricelist_mapeo_unificado", esperado)
