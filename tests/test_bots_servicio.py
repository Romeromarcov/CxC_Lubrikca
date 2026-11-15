"""Servicio del panel de bots con el repositorio en memoria."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from cxc.bots.modelos import BOTS, DatoInvalido
from cxc.bots.servicio import BotDesconocido, ServicioBots, UsuarioNoEncontrado
from tests.bots_dobles import RepoBotsMemoria

T0 = datetime(2026, 11, 14, 12, 0, 0)


@pytest.fixture
def repo() -> RepoBotsMemoria:
    return RepoBotsMemoria()


@pytest.fixture
def reloj() -> dict[str, datetime]:
    return {"ahora": T0}


@pytest.fixture
def svc(repo: RepoBotsMemoria, reloj: dict[str, datetime]) -> ServicioBots:
    return ServicioBots(repo, lambda: reloj["ahora"])


# ── latido ───────────────────────────────────────────────────────────────────


def test_primer_latido_registra_el_bot_con_valores_seguros(
    svc: ServicioBots, repo: RepoBotsMemoria
):
    cfg = svc.latido("pedidos", "Ventas", "1.0", {"omni": "ok"})
    assert cfg == {
        "habilitado": True,
        "parametros": {"auto_envio_min": 10, "gemini_model": "gemini-2.5-flash"},
        "chats": {"respaldo": None, "grupo": None},
        "mensaje": None,
        "usuarios": [],
    }
    b = repo.bots["pedidos"]
    assert b.ultimo_latido == T0 and b.version == "1.0" and b.estado["omni"] == "ok"


def test_el_latido_devuelve_solo_los_usuarios_de_ese_bot(svc: ServicioBots):
    svc.guardar_usuario(7, "ana@x.com", "Ana", ["pedidos", "cobranza"], "admin")
    svc.guardar_usuario(8, "luis@x.com", "Luis", ["compras"], "admin")
    assert [u["telegram_id"] for u in svc.latido("pedidos", "", "", {})["usuarios"]] == [7]
    assert [u["telegram_id"] for u in svc.latido("compras", "", "", {})["usuarios"]] == [8]
    assert svc.latido("eventos", "", "", {})["usuarios"] == []
    assert set(svc.latido("pedidos", "", "", {})["usuarios"][0]) == {
        "telegram_id",
        "email",
        "nombre",
    }


def test_el_latido_refleja_los_cambios_del_panel(svc: ServicioBots):
    svc.latido("cobranza", "Cobranzas", "1", {})
    svc.actualizar(
        "cobranza",
        {"habilitado": False, "parametros": {"auto_envio_min": 30}, "mensaje": "Pausa"},
        "admin",
    )
    cfg = svc.latido("cobranza", "Cobranzas", "1", {})
    assert (
        cfg["habilitado"] is False
        and cfg["parametros"]["auto_envio_min"] == 30
        and cfg["mensaje"] == "Pausa"
    )


def test_latido_de_bot_desconocido(svc: ServicioBots):
    with pytest.raises(BotDesconocido):
        svc.latido("hacker", "", "", {})


def test_el_latido_sanea_lo_que_reporta_el_bot(svc: ServicioBots, repo: RepoBotsMemoria):
    svc.latido(
        "pedidos",
        "x" * 500,
        "v" * 99,
        {"ultimo_error": "token 1234567890:AAFakeFakeFakeFakeFakeFakeFakeFake000", "gemini": "?"},
    )
    b = repo.bots["pedidos"]
    assert len(b.nombre) <= 60 and len(b.version) <= 30
    assert "AAGPIf" not in b.estado["ultimo_error"] and b.estado["gemini"] == "sin_clave"


# ── listado y detalle ────────────────────────────────────────────────────────


def test_el_listado_incluye_los_cinco_bots_aunque_nunca_hayan_latido(svc: ServicioBots, reloj):
    svc.latido("pedidos", "Ventas", "1", {})
    reloj["ahora"] = T0 + timedelta(seconds=60)
    items = {b["bot"]: b for b in svc.listar()}
    assert list(items) == list(BOTS)
    assert items["pedidos"]["en_linea"] and items["pedidos"]["visto_alguna_vez"]
    assert items["pedidos"]["hace_segundos"] == 60 and items["asana"]["hace_segundos"] is None
    assert not items["asana"]["en_linea"] and not items["asana"]["visto_alguna_vez"]
    reloj["ahora"] = T0 + timedelta(minutes=10)
    assert not {b["bot"]: b for b in svc.listar()}["pedidos"]["en_linea"]


def test_el_listado_cuenta_usuarios_por_bot(svc: ServicioBots):
    svc.guardar_usuario(7, "", "Ana", ["pedidos", "compras"], "admin")
    items = {b["bot"]: b["n_usuarios"] for b in svc.listar()}
    assert items["pedidos"] == 1 and items["compras"] == 1 and items["cobranza"] == 0


def test_detalle_trae_capturas_del_bot_historial_y_bitacora(
    svc: ServicioBots, repo: RepoBotsMemoria
):
    repo.capturas = [
        {"captura_id": 1, "kind": "pago", "estado": "ENVIADO"},
        {"captura_id": 2, "kind": "pedido", "estado": "ERROR"},
        {"captura_id": 3, "kind": "factura_proveedor", "estado": "INCOMPLETO"},
    ]
    svc.latido("pedidos", "Ventas", "1", {"mensajes_hoy": 4})
    svc.actualizar("pedidos", {"mensaje": "hola"}, "admin")
    d = svc.detalle("pedidos")
    assert [c["captura_id"] for c in d["capturas"]] == [2]  # solo los tipos de ese bot
    assert [c["captura_id"] for c in svc.detalle("compras")["capturas"]] == [3]
    assert svc.detalle("eventos")["capturas"] == [] and svc.detalle("asana")["capturas"] == []
    assert d["historial"][0]["mensajes"] == 4 and d["bitacora"][0]["usuario"] == "admin"
    assert "gemini-2.5-flash" in d["modelos_sugeridos"]


def test_detalle_de_bot_desconocido(svc: ServicioBots):
    with pytest.raises(BotDesconocido):
        svc.detalle("nada")


# ── actualizar ───────────────────────────────────────────────────────────────


def test_actualizar_guarda_en_la_bitacora_el_antes_y_el_despues(
    svc: ServicioBots, repo: RepoBotsMemoria
):
    svc.actualizar(
        "compras", {"parametros": {"auto_envio_min": 20}, "chats": {"grupo": -100123}}, "ana@x.com"
    )
    (cambio,) = repo.log
    assert (
        cambio["entidad"] == "bot:compras"
        and cambio["accion"] == "actualizar"
        and cambio["usuario"] == "ana@x.com"
    )
    # Solo se registra lo que cambió, clave por clave (no el diccionario completo).
    assert cambio["antes"] == {"parametros": {"auto_envio_min": 10}, "chats": {"grupo": None}}
    assert cambio["despues"] == {"parametros": {"auto_envio_min": 20}, "chats": {"grupo": -100123}}


def test_un_guardado_sin_cambios_no_toca_nada_ni_deja_rastro(
    svc: ServicioBots, repo: RepoBotsMemoria
):
    svc.actualizar("pedidos", {"parametros": {"auto_envio_min": 25}, "mensaje": "Hola"}, "ana")
    assert len(repo.log) == 1
    # La pantalla manda el formulario completo: lo igual a lo vigente no es un cambio.
    d = svc.actualizar(
        "pedidos",
        {
            "habilitado": True,
            "parametros": {"auto_envio_min": 25, "gemini_model": "gemini-2.5-flash"},
            "chats": {"respaldo": None, "grupo": None},
            "url_panel": "",
            "mensaje": "Hola",
        },
        "luis",
    )
    assert len(repo.log) == 1 and repo.bots["pedidos"].actualizado_por == "ana"
    assert d["mensaje"] == "Hola" and d["parametros"]["auto_envio_min"] == 25


def test_el_formulario_completo_solo_registra_lo_que_cambio(
    svc: ServicioBots, repo: RepoBotsMemoria
):
    svc.actualizar(
        "pedidos",
        {
            "habilitado": True,
            "parametros": {"auto_envio_min": 10, "gemini_model": "gemini-2.5-pro"},
            "chats": {"respaldo": -100, "grupo": None},
            "url_panel": "",
            "mensaje": "",
        },
        "ana",
    )
    (c,) = repo.log
    assert c["antes"] == {
        "parametros": {"gemini_model": "gemini-2.5-flash"},
        "chats": {"respaldo": None},
    }
    assert c["despues"] == {
        "parametros": {"gemini_model": "gemini-2.5-pro"},
        "chats": {"respaldo": -100},
    }


def test_actualizar_combina_parametros_sin_perder_los_demas(svc: ServicioBots):
    svc.actualizar("compras", {"parametros": {"gemini_model": "gemini-2.5-pro"}}, "a")
    d = svc.actualizar("compras", {"parametros": {"auto_envio_min": 5}}, "a")
    assert d["parametros"] == {"auto_envio_min": 5, "gemini_model": "gemini-2.5-pro"}


def test_actualizar_url_panel_y_limpiarla(svc: ServicioBots):
    assert (
        svc.actualizar("asana", {"url_panel": "https://asana.example.com"}, "a")["url_panel"]
        == "https://asana.example.com"
    )
    assert svc.actualizar("asana", {"url_panel": ""}, "a")["url_panel"] == ""


@pytest.mark.parametrize(
    "cambios",
    [
        {},
        {"habilitado": "si"},
        {"parametros": {"auto_envio_min": 500}},
        {"chats": {"grupo": 0}},
        {"url_panel": "javascript:alert(1)"},
        {"mensaje": "x" * 300},
        {"parametros": {"token": "1:abc"}},
    ],
)
def test_actualizar_rechaza_datos_invalidos_y_no_deja_rastro(
    svc: ServicioBots, repo: RepoBotsMemoria, cambios
):
    with pytest.raises(DatoInvalido):
        svc.actualizar("pedidos", cambios, "a")
    assert repo.log == []


def test_actualizar_bot_desconocido(svc: ServicioBots):
    with pytest.raises(BotDesconocido):
        svc.actualizar("x", {"habilitado": False}, "a")


# ── usuarios ─────────────────────────────────────────────────────────────────


def test_alta_actualizacion_y_baja_de_usuarios_con_bitacora(
    svc: ServicioBots, repo: RepoBotsMemoria
):
    u, creado = svc.guardar_usuario(
        "5269337388", "Marco@X.com", "Marco", ["pedidos", "cobranza"], "admin"
    )
    assert (
        creado
        and u["email"] == "marco@x.com"
        and u["bots"] == ["pedidos", "cobranza"]
        and u["creado_por"] == "admin"
    )
    u2, creado2 = svc.guardar_usuario(5269337388, "marco@x.com", "Marco R.", ["pedidos"], "otro")
    assert (
        not creado2 and u2["nombre"] == "Marco R." and u2["creado_por"] == "admin"
    )  # conserva quién lo creó
    svc.borrar_usuario("5269337388", "admin")
    assert svc.usuarios() == []
    assert [(c["accion"], c["entidad"]) for c in repo.log] == [
        ("alta", "usuario:5269337388"),
        ("actualizar", "usuario:5269337388"),
        ("baja", "usuario:5269337388"),
    ]
    assert (
        repo.log[1]["antes"]["nombre"] == "Marco" and repo.log[1]["despues"]["nombre"] == "Marco R."
    )


def test_borrar_usuario_inexistente(svc: ServicioBots, repo: RepoBotsMemoria):
    with pytest.raises(UsuarioNoEncontrado):
        svc.borrar_usuario(999, "admin")
    assert repo.log == []


def test_usuario_invalido_no_deja_rastro(svc: ServicioBots, repo: RepoBotsMemoria):
    with pytest.raises(DatoInvalido):
        svc.guardar_usuario(-1, "", "x", ["pedidos"], "a")
    assert repo.users == {} and repo.log == []


# ── destinos ─────────────────────────────────────────────────────────────────


def test_destinos_resume_salud_cola_y_capturas(svc: ServicioBots, repo: RepoBotsMemoria):
    repo.conteos = {"INCOMPLETO": 3, "ERROR": 1, "ENVIADO": 40}
    svc.latido(
        "pedidos",
        "Ventas",
        "1",
        {"omni": "ok", "cxc": "error", "gemini": "ok", "outbox_pendientes": 2},
    )
    svc.latido(
        "cobranza",
        "Cobranzas",
        "1",
        {"omni": "ok", "cxc": "ok", "gemini": "ok", "outbox_pendientes": 1},
    )
    d = svc.destinos()
    assert (
        d["omni"]["estado"] == "ok"
        and d["cxc"]["estado"] == "error"
        and d["gemini"]["estado"] == "ok"
    )
    assert d["outbox_pendientes"] == 3 and d["bots_en_linea"] == 2 and d["bots_total"] == 5
    assert d["capturas"] == {"PENDIENTE": 0, "INCOMPLETO": 3, "ERROR": 1, "ENVIADO": 40}
