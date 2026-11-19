"""Cierre de la API frente al panel de bots: la clave del bot solo abre el latido (POST).

Va aparte (y excluido del parche de sesión de ``conftest``) para ejercer el middleware de verdad,
igual que ``test_capturas_api_auth.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from cxc.auth import generar_api_key, hash_api_key
from cxc.bots.servicio import ServicioBots
from cxc.permisos import efectivos
from cxc.web.app import app
from cxc.web.bots_api import get_servicio_bots
from cxc.web.capturas_api import es_ruta_bot
from tests.bots_dobles import RepoBotsMemoria

client = TestClient(app)
KEY = "clave-de-prueba-del-bot"
CUERPO = {"bot": "pedidos", "nombre": "Ventas", "version": "1", "estado": {"omni": "ok"}}


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> Iterator[RepoBotsMemoria]:
    monkeypatch.setenv("BOT_API_KEY", KEY)
    r = RepoBotsMemoria()
    svc = ServicioBots(r)
    app.dependency_overrides[get_servicio_bots] = lambda: svc
    yield r
    app.dependency_overrides.pop(get_servicio_bots, None)


@pytest.mark.parametrize(
    "metodo,ruta,esperado",
    [
        ("POST", "/api/bots/heartbeat", True),
        ("GET", "/api/bots/heartbeat", False),
        ("POST", "/api/bots/heartbeat/", False),
        ("POST", "/api/bots/heartbeat/x", False),
        ("POST", "/api/bots/usuarios", False),
        ("POST", "/api/bots/pedidos", False),
        ("PATCH", "/api/bots/pedidos", False),
        ("DELETE", "/api/bots/usuarios/7", False),
        ("GET", "/api/bots", False),
    ],
)
def test_del_panel_solo_el_latido_acepta_la_clave_del_bot(
    metodo: str, ruta: str, esperado: bool
) -> None:
    assert es_ruta_bot(metodo, ruta) is esperado


def test_el_bot_late_sin_sesion_con_su_clave(repo: RepoBotsMemoria) -> None:
    r = client.post("/api/bots/heartbeat", json=CUERPO, headers={"X-Bot-Key": KEY})
    assert r.status_code == 200 and "pedidos" in repo.bots


def test_sin_clave_ni_sesion_da_401_del_middleware(repo: RepoBotsMemoria) -> None:
    assert client.post("/api/bots/heartbeat", json=CUERPO).status_code == 401
    assert repo.bots == {}


def test_clave_mala_la_rechaza_el_endpoint(repo: RepoBotsMemoria) -> None:
    assert (
        client.post("/api/bots/heartbeat", json=CUERPO, headers={"X-Bot-Key": "mala"}).status_code
        == 401
    )
    assert repo.bots == {}


def test_sin_bot_api_key_configurada_da_503(
    repo: RepoBotsMemoria, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BOT_API_KEY")
    assert (
        client.post("/api/bots/heartbeat", json=CUERPO, headers={"X-Bot-Key": KEY}).status_code
        == 503
    )
    assert repo.bots == {}


def test_con_la_clave_del_bot_no_se_abre_nada_mas_del_panel(repo: RepoBotsMemoria) -> None:
    h = {"X-Bot-Key": KEY}
    assert client.get("/api/bots", headers=h).status_code == 401
    assert client.get("/api/bots/pedidos", headers=h).status_code == 401
    assert client.get("/api/bots/heartbeat", headers=h).status_code == 401
    assert client.get("/api/bots/usuarios", headers=h).status_code == 401
    assert client.get("/api/bots/destinos", headers=h).status_code == 401
    assert (
        client.patch("/api/bots/pedidos", json={"habilitado": False}, headers=h).status_code == 401
    )
    assert (
        client.post(
            "/api/bots/usuarios", json={"telegram_id": 7, "bots": ["pedidos"]}, headers=h
        ).status_code
        == 401
    )
    assert client.delete("/api/bots/usuarios/7", headers=h).status_code == 401
    assert repo.users == {} and repo.log == []


def test_una_sesion_sola_no_basta_para_latir_como_bot(repo: RepoBotsMemoria) -> None:
    """El latido exige SIEMPRE X-Bot-Key: nadie con sesión puede hacerse pasar por un bot."""
    with patch("cxc.web.app.hay_sesion_valida", return_value=True):
        assert client.post("/api/bots/heartbeat", json=CUERPO).status_code == 401
    assert repo.bots == {}


def test_una_llave_de_api_normal_no_puede_latir_ni_administrar(repo: RepoBotsMemoria) -> None:
    api_key = generar_api_key()
    base = MagicMock()
    base.all_api_keys.return_value = [
        {"key_id": "K1", "key_hash": hash_api_key(api_key), "activo": "true"}
    ]
    base.feriados.return_value = []
    h = {"Authorization": f"Bearer {api_key}"}
    with patch("cxc.web.app.get_repo", return_value=base):
        assert client.post("/api/bots/heartbeat", json=CUERPO, headers=h).status_code == 401
        assert (
            client.patch("/api/bots/pedidos", json={"habilitado": False}, headers=h).status_code
            == 401
        )
        # los GET con llave de API pasan el middleware pero el panel exige sesión con permiso
        assert client.get("/api/bots", headers=h).status_code in (401, 403)
    assert repo.bots == {} and repo.log == []


def test_la_clave_del_bot_no_sirve_como_llave_de_api(repo: RepoBotsMemoria) -> None:
    base = MagicMock()
    base.all_api_keys.return_value = []
    with patch("cxc.web.app.get_repo", return_value=base):
        assert (
            client.get("/api/bots", headers={"Authorization": f"Bearer {KEY}"}).status_code == 401
        )


def test_con_sesion_y_permiso_el_panel_responde(repo: RepoBotsMemoria) -> None:
    usuario = {"email": "t@x.test", "rol": "tesoreria", "permisos": efectivos("tesoreria")}
    with (
        patch("cxc.web.app.hay_sesion_valida", return_value=True),
        patch("cxc.web.app.get_current_user_from_cookie", return_value=usuario),
    ):
        assert client.get("/api/bots").status_code == 200
        assert client.patch("/api/bots/pedidos", json={"habilitado": False}).status_code == 200
    assert client.get("/api/bots").status_code == 401  # sin sesión, el middleware la cierra


def test_pagina_bots_pide_permiso(repo: RepoBotsMemoria) -> None:
    r_legado = client.get("/bots", follow_redirects=False)
    assert r_legado.status_code == 302 and r_legado.headers["location"] == "/configuracion?tab=bots"

    tesoreria = {"email": "t@x.test", "rol": "tesoreria", "permisos": efectivos("tesoreria")}
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=tesoreria):
        r = client.get("/configuracion?tab=bots", follow_redirects=False)
        assert r.status_code == 200 and 'id="bots-usuarios-body"' in r.text
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=None):
        assert (
            client.get("/configuracion?tab=bots", follow_redirects=False).headers["location"]
            == "/login"
        )
