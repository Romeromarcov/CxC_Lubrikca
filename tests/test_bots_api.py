"""API del panel de bots: permisos, validaciones, contrato del latido y ausencia de secretos."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cxc.bots.servicio import ServicioBots
from cxc.permisos import efectivos
from cxc.web.app import app
from cxc.web.bots_api import get_servicio_bots
from tests.bots_dobles import RepoBotsMemoria

client = TestClient(app)
KEY = "clave-de-prueba-del-bot"
BOT = {"X-Bot-Key": KEY}
TOKEN = "1234567890:AAFakeFakeFakeFakeFakeFakeFakeFake000"  # forma de un token (no es real)
GOOGLE = "AIzaSyFakeFakeFakeFakeFakeFakeFakeFake000"  # forma de una API key (no es real)

ADMIN = {"email": "admin@x.test", "rol": "admin", "permisos": []}
TESORERIA = {"email": "t@x.test", "rol": "tesoreria", "permisos": efectivos("tesoreria")}
AUDITOR = {"email": "a@x.test", "rol": "auditor", "permisos": efectivos("auditor")}
VENTAS = {"email": "v@x.test", "rol": "ventas", "permisos": efectivos("ventas")}


def _como(usuario: dict[str, Any] | None):
    return patch("cxc.web.app.get_current_user_from_cookie", return_value=usuario)


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> Iterator[RepoBotsMemoria]:
    monkeypatch.setenv("BOT_API_KEY", KEY)
    r = RepoBotsMemoria()
    svc = ServicioBots(r)
    app.dependency_overrides[get_servicio_bots] = lambda: svc
    yield r
    app.dependency_overrides.pop(get_servicio_bots, None)


def _latido(**kw: Any) -> dict[str, Any]:
    d = {
        "bot": "pedidos",
        "nombre": "Ventas",
        "version": "1.0",
        "estado": {"omni": "ok", "cxc": "ok", "gemini": "ok"},
    }
    d.update(kw)
    return d


# ── latido ───────────────────────────────────────────────────────────────────


def test_latido_crea_y_devuelve_la_config(repo: RepoBotsMemoria) -> None:
    r = client.post("/api/bots/heartbeat", json=_latido(), headers=BOT)
    assert r.status_code == 200
    assert r.json() == {
        "habilitado": True,
        "parametros": {"auto_envio_min": 10, "gemini_model": "gemini-2.5-flash"},
        "chats": {"respaldo": None, "grupo": None},
        "mensaje": None,
        "usuarios": [],
    }
    assert repo.bots["pedidos"].version == "1.0"


def test_latido_devuelve_lo_configurado_en_el_panel(repo: RepoBotsMemoria) -> None:
    with _como(ADMIN):
        assert (
            client.post(
                "/api/bots/usuarios",
                json={"telegram_id": 7, "email": "ana@x.com", "nombre": "Ana", "bots": ["pedidos"]},
            ).status_code
            == 201
        )
        assert (
            client.patch(
                "/api/bots/pedidos",
                json={
                    "habilitado": False,
                    "chats": {"respaldo": -1003931474743},
                    "mensaje": "Pausa",
                },
            ).status_code
            == 200
        )
    r = client.post("/api/bots/heartbeat", json=_latido(), headers=BOT).json()
    assert (
        r["habilitado"] is False
        and r["chats"]["respaldo"] == -1003931474743
        and r["mensaje"] == "Pausa"
    )
    assert r["usuarios"] == [{"telegram_id": 7, "email": "ana@x.com", "nombre": "Ana"}]


@pytest.mark.parametrize("bot", ["otro", "", "PEDIDOS", "capturas"])
def test_latido_de_bot_fuera_de_la_familia_da_422(repo: RepoBotsMemoria, bot: str) -> None:
    assert client.post("/api/bots/heartbeat", json=_latido(bot=bot), headers=BOT).status_code == 422
    assert repo.bots == {}


@pytest.mark.parametrize("bot", ["pedidos", "cobranza", "compras", "eventos", "asana"])
def test_los_cinco_bots_pueden_latir(repo: RepoBotsMemoria, bot: str) -> None:
    assert client.post("/api/bots/heartbeat", json=_latido(bot=bot), headers=BOT).status_code == 200


def test_latido_sin_body_valido_da_422(repo: RepoBotsMemoria) -> None:
    assert client.post("/api/bots/heartbeat", json={}, headers=BOT).status_code == 422


def test_el_estado_con_secretos_se_guarda_redactado_y_no_se_devuelve(repo: RepoBotsMemoria) -> None:
    malo = _latido(
        estado={"ultimo_error": f"fallo en https://api.telegram.org/bot{TOKEN}/getMe con {GOOGLE}"}
    )
    client.post("/api/bots/heartbeat", json=malo, headers=BOT)
    with _como(ADMIN):
        texto = client.get("/api/bots/pedidos").text + client.get("/api/bots").text
    assert TOKEN not in texto and GOOGLE not in texto and "[oculto]" in texto


# ── permisos de la interfaz ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ruta", ["/api/bots", "/api/bots/pedidos", "/api/bots/usuarios", "/api/bots/destinos"]
)
def test_ver_exige_sesion_y_permiso(repo: RepoBotsMemoria, ruta: str) -> None:
    with _como(None):
        assert client.get(ruta).status_code == 401
    with _como(VENTAS):
        assert client.get(ruta).status_code == 403
    for quien in (ADMIN, TESORERIA, AUDITOR):
        with _como(quien):
            assert client.get(ruta).status_code == 200


def test_gestionar_solo_para_quien_tiene_el_permiso(repo: RepoBotsMemoria) -> None:
    cuerpo_usuario = {"telegram_id": 7, "bots": ["pedidos"]}
    for quien, ok in ((ADMIN, True), (TESORERIA, True), (AUDITOR, False), (VENTAS, False)):
        with _como(quien):
            codigos = {
                client.patch("/api/bots/pedidos", json={"habilitado": False}).status_code,
                client.post("/api/bots/usuarios", json=cuerpo_usuario).status_code,
            }
            codigo_baja = client.delete("/api/bots/usuarios/7").status_code
        if ok:
            assert codigos <= {200, 201} and codigo_baja in (200, 404)
        else:
            assert codigos == {403} and codigo_baja == 403
    with _como(None):
        assert client.patch("/api/bots/pedidos", json={"habilitado": False}).status_code == 401


def test_el_reparto_de_permisos_por_rol() -> None:
    from cxc.permisos import tiene

    assert tiene(TESORERIA, "bots.ver") and tiene(TESORERIA, "bots.gestionar")
    assert tiene(AUDITOR, "bots.ver") and not tiene(AUDITOR, "bots.gestionar")
    assert not tiene(VENTAS, "bots.ver") and "bots" not in VENTAS["permisos"]
    assert "bots" in TESORERIA["permisos"] and "bots" in AUDITOR["permisos"]


# ── listado, detalle, edición ────────────────────────────────────────────────


def test_listado_trae_los_cinco_bots_con_estado(repo: RepoBotsMemoria) -> None:
    client.post("/api/bots/heartbeat", json=_latido(), headers=BOT)
    with _como(ADMIN):
        items = client.get("/api/bots").json()["items"]
    assert [b["bot"] for b in items] == ["pedidos", "cobranza", "compras", "eventos", "asana"]
    assert (
        items[0]["en_linea"] is True
        and items[4]["en_linea"] is False
        and items[4]["visto_alguna_vez"] is False
    )


def test_detalle_de_bot_inexistente_da_404(repo: RepoBotsMemoria) -> None:
    with _como(ADMIN):
        assert client.get("/api/bots/inexistente").status_code == 404
        assert client.patch("/api/bots/inexistente", json={"habilitado": False}).status_code == 404


def test_patch_valida_y_devuelve_el_detalle(repo: RepoBotsMemoria) -> None:
    with _como(TESORERIA):
        r = client.patch(
            "/api/bots/asana",
            json={"url_panel": "https://asana.example.com", "parametros": {"auto_envio_min": 15}},
        )
        assert r.status_code == 200 and r.json()["url_panel"] == "https://asana.example.com"
        assert r.json()["parametros"]["auto_envio_min"] == 15
        for mal in (
            {"parametros": {"auto_envio_min": 999}},
            {"habilitado": "quizá"},
            {"url_panel": "javascript:1"},
            {"chats": {"grupo": 0}},
            {"parametros": {"api_key": "x"}},
            {},
        ):
            r = client.patch("/api/bots/asana", json=mal)
            assert r.status_code == 422, mal
    assert len(repo.log) == 1  # lo rechazado no deja rastro


def test_patch_deja_bitacora_con_el_actor(repo: RepoBotsMemoria) -> None:
    with _como(TESORERIA):
        client.patch("/api/bots/compras", json={"habilitado": False})
    (c,) = repo.log
    assert (
        c["usuario"] == "t@x.test"
        and c["antes"] == {"habilitado": True}
        and c["despues"] == {"habilitado": False}
    )


def test_el_detalle_incluye_capturas_y_modelos(repo: RepoBotsMemoria) -> None:
    repo.capturas = [{"captura_id": 9, "kind": "pago", "estado": "ERROR"}]
    with _como(AUDITOR):
        d = client.get("/api/bots/cobranza").json()
    assert d["capturas"][0]["captura_id"] == 9 and d["modelos_sugeridos"]


# ── usuarios ─────────────────────────────────────────────────────────────────


def test_crud_de_usuarios(repo: RepoBotsMemoria) -> None:
    with _como(ADMIN):
        alta = client.post(
            "/api/bots/usuarios",
            json={
                "telegram_id": "5269337388",
                "email": "Marco@x.com",
                "nombre": "Marco",
                "bots": ["pedidos", "compras"],
            },
        )
        assert alta.status_code == 201 and alta.json()["email"] == "marco@x.com"
        again = client.post(
            "/api/bots/usuarios",
            json={"telegram_id": 5269337388, "nombre": "Marco R.", "bots": ["pedidos"]},
        )
        assert again.status_code == 200 and again.json()["nombre"] == "Marco R."
        assert [u["telegram_id"] for u in client.get("/api/bots/usuarios").json()["items"]] == [
            5269337388
        ]
        assert client.delete("/api/bots/usuarios/5269337388").status_code == 200
        assert client.delete("/api/bots/usuarios/5269337388").status_code == 404
        assert client.get("/api/bots/usuarios").json()["items"] == []
    assert [c["accion"] for c in repo.log] == ["alta", "actualizar", "baja"]


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"telegram_id": 0, "bots": ["pedidos"]},
        {"telegram_id": -4, "bots": ["pedidos"]},
        {"telegram_id": "abc", "bots": ["pedidos"]},
        {"telegram_id": 7, "bots": []},
        {"telegram_id": 7, "bots": ["otro"]},
        {"telegram_id": 7, "email": "no-es-correo", "bots": ["pedidos"]},
    ],
)
def test_usuario_invalido_da_422(repo: RepoBotsMemoria, cuerpo: dict[str, Any]) -> None:
    with _como(ADMIN):
        assert client.post("/api/bots/usuarios", json=cuerpo).status_code == 422
    assert repo.users == {}


def test_borrar_con_id_invalido(repo: RepoBotsMemoria) -> None:
    with _como(ADMIN):
        assert client.delete("/api/bots/usuarios/abc").status_code == 422


# ── destinos ─────────────────────────────────────────────────────────────────


def test_destinos(repo: RepoBotsMemoria) -> None:
    repo.conteos = {"ERROR": 2}
    client.post(
        "/api/bots/heartbeat",
        json=_latido(estado={"omni": "error", "cxc": "ok", "gemini": "ok", "outbox_pendientes": 4}),
        headers=BOT,
    )
    with _como(AUDITOR):
        d = client.get("/api/bots/destinos").json()
    assert (
        d["omni"]["estado"] == "error"
        and d["outbox_pendientes"] == 4
        and d["capturas"]["ERROR"] == 2
    )


# ── las rutas de capturas siguen ganando a /api/bots/{bot} ───────────────────


def test_api_bots_capturas_no_la_pisa_el_panel(repo: RepoBotsMemoria) -> None:
    from cxc.capturas.servicio import ServicioCapturas
    from cxc.odoo.escritura import EscritorOdoo
    from cxc.web.capturas_api import get_servicio as get_capturas
    from tests.capturas_dobles import EspejoFalso, OdooFalso, RepoMemoria

    svc = ServicioCapturas(
        RepoMemoria(), EspejoFalso(), lambda: EscritorOdoo(OdooFalso()), diarios={}
    )
    app.dependency_overrides[get_capturas] = lambda: svc
    try:
        with _como(ADMIN):
            r = client.get("/api/bots/capturas")
        assert r.status_code == 200 and set(r.json()) == {
            "items",
            "total",
        }  # la lista de capturas, no un bot
    finally:
        app.dependency_overrides.pop(get_capturas, None)


def test_ninguna_respuesta_menciona_variables_secretas(repo: RepoBotsMemoria) -> None:
    client.post("/api/bots/heartbeat", json=_latido(), headers=BOT)
    with _como(ADMIN):
        todo = json.dumps(
            [
                client.get(r).json()
                for r in (
                    "/api/bots",
                    "/api/bots/pedidos",
                    "/api/bots/usuarios",
                    "/api/bots/destinos",
                )
            ]
        )
    for prohibido in (KEY, "BOT_API_KEY", "token", "api_key", "password"):
        assert prohibido.lower() not in todo.lower().replace("gemini_model", "")


# ── contrato exacto con el cliente del bot (lubrikca-bots/core/control.py) ───


def test_contrato_del_latido_con_el_payload_exacto_del_bot(repo: RepoBotsMemoria) -> None:
    """El bot envía ``{bot, nombre, version, estado:{…}}`` y espera ``{habilitado, parametros,
    usuarios, chats, mensaje}``: ni una clave más ni una menos (si cambia, el bot no aplica)."""
    payload = {
        "bot": "compras",
        "nombre": "Compras",
        "version": "1.0",
        "estado": {
            "modo": "polling",
            "gemini": "ok",
            "omni": "ok",
            "cxc": "desactivado",
            "outbox_pendientes": 0,
            "mensajes_hoy": 3,
            "errores_hoy": 0,
            "ultimo_error": None,
        },
    }
    r = client.post("/api/bots/heartbeat", json=payload, headers=BOT)
    assert r.status_code == 200
    cuerpo = r.json()
    assert set(cuerpo) == {"habilitado", "parametros", "usuarios", "chats", "mensaje"}
    assert set(cuerpo["parametros"]) == {"auto_envio_min", "gemini_model"}
    assert set(cuerpo["chats"]) == {"respaldo", "grupo"}
    assert isinstance(cuerpo["habilitado"], bool) and isinstance(cuerpo["usuarios"], list)
    # Lo que el bot reportó es lo que el panel muestra.
    assert repo.bots["compras"].estado["mensajes_hoy"] == 3
    assert repo.bots["compras"].estado["modo"] == "polling"


def test_el_latido_de_asana_con_contadores_en_cero_es_valido(repo: RepoBotsMemoria) -> None:
    estado = {
        "modo": "polling",
        "gemini": "ok",
        "omni": "desactivado",
        "cxc": "desactivado",
        "outbox_pendientes": 0,
        "mensajes_hoy": 0,
        "errores_hoy": 0,
        "ultimo_error": None,
    }
    r = client.post(
        "/api/bots/heartbeat", json=_latido(bot="asana", nombre="Asana", estado=estado), headers=BOT
    )
    assert r.status_code == 200
    with _como(ADMIN):
        d = client.get("/api/bots/asana").json()
    assert d["en_linea"] and d["estado"]["omni"] == "desactivado"
    assert d["estado"]["mensajes_hoy"] == 0
