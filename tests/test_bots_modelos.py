"""Modelo y validación del panel de bots (puro, sin red ni base)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from cxc.bots.modelos import (
    BOTS,
    EN_LINEA_SEGUNDOS,
    Bot,
    DatoInvalido,
    redactar,
    resumen_destinos,
    sanear_estado,
    validar_chats,
    validar_mensaje,
    validar_parametros,
    validar_telegram_id,
    validar_url_panel,
    validar_usuario,
)

AHORA = datetime(2026, 11, 14, 12, 0, 0)
TOKEN = "1234567890:AAFakeFakeFakeFakeFakeFakeFakeFake000"  # forma de un token (no es real)
GOOGLE = "AIzaSyFakeFakeFakeFakeFakeFakeFakeFake000"  # forma de una API key (no es real)


def test_la_familia_tiene_los_cinco_bots():
    assert list(BOTS) == ["pedidos", "cobranza", "compras", "eventos", "asana"]


def test_en_linea_depende_de_la_edad_del_ultimo_latido():
    b = Bot(bot="pedidos", nombre="Ventas")
    assert not b.en_linea(AHORA)  # nunca visto
    b.ultimo_latido = AHORA - timedelta(seconds=EN_LINEA_SEGUNDOS - 1)
    assert b.en_linea(AHORA)
    b.ultimo_latido = AHORA - timedelta(seconds=EN_LINEA_SEGUNDOS)
    assert not b.en_linea(AHORA)


# ── redacción ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "secreto", [TOKEN, GOOGLE, "cxc_live_abcdefghijklmnop", "Bearer abcdefghijklmnop123"]
)
def test_redactar_oculta_tokens_y_llaves(secreto):
    r = redactar(f"falló con {secreto} al conectar")
    assert secreto not in r and "[oculto]" in r


def test_redactar_no_toca_texto_normal():
    assert redactar("Omni respondió 502 a las 12:30") == "Omni respondió 502 a las 12:30"


def test_el_estado_reportado_se_sanea_y_no_filtra_secretos():
    e = sanear_estado(
        {
            "modo": "polling",
            "gemini": "raro",
            "omni": "ok",
            "cxc": "error",
            "outbox_pendientes": "7",
            "mensajes_hoy": -3,
            "errores_hoy": "abc",
            "ultimo_error": f"url https://api.telegram.org/bot{TOKEN}/getMe falló " + "x" * 500,
            "campo_extra": "no entra",
        }
    )
    assert e["gemini"] == "sin_clave" and e["omni"] == "ok" and e["cxc"] == "error"
    assert e["outbox_pendientes"] == 7 and e["mensajes_hoy"] == 0 and e["errores_hoy"] == 0
    assert (
        TOKEN not in e["ultimo_error"] and len(e["ultimo_error"]) <= 300 and "campo_extra" not in e
    )


def test_estado_vacio_tiene_valores_seguros():
    e = sanear_estado({})
    assert (e["gemini"], e["omni"], e["cxc"], e["ultimo_error"]) == (
        "sin_clave",
        "desactivado",
        "desactivado",
        None,
    )


# ── parámetros, chats, url, mensaje ──────────────────────────────────────────


def test_parametros_validos():
    assert validar_parametros({"auto_envio_min": "15", "gemini_model": " gemini-2.5-pro "}) == {
        "auto_envio_min": 15,
        "gemini_model": "gemini-2.5-pro",
    }


@pytest.mark.parametrize(
    "mal",
    [
        {"auto_envio_min": 0},
        {"auto_envio_min": 121},
        {"auto_envio_min": "diez"},
        {"auto_envio_min": True},
        {"auto_envio_min": 1.5},
        {"gemini_model": ""},
        {"gemini_model": "modelo con espacios"},
        {"gemini_model": "../../etc/passwd"},
        {"gemini_model": 5},
        {"token": "123:abc"},
        {"api_key": "AIza..."},
    ],
)
def test_parametros_invalidos_se_rechazan(mal):
    with pytest.raises(DatoInvalido):
        validar_parametros(mal)


def test_chats():
    assert validar_chats({"respaldo": "-1003931474743", "grupo": None}) == {
        "respaldo": -1003931474743,
        "grupo": None,
    }
    assert validar_chats({"grupo": ""}) == {"grupo": None}
    for mal in ({"otro": 1}, {"grupo": 0}, {"grupo": "abc"}, {"grupo": True}):
        with pytest.raises(DatoInvalido):
            validar_chats(mal)


def test_url_panel():
    assert (
        validar_url_panel("  https://asana-bot.example.com/panel ")
        == "https://asana-bot.example.com/panel"
    )
    assert validar_url_panel("") == ""
    for mal in (
        "javascript:alert(1)",
        "ftp://x.com",
        "no es url",
        "https://",
        "https://a.com/ b",
        "https://a.com/" + "x" * 300,
    ):
        with pytest.raises(DatoInvalido):
            validar_url_panel(mal)


def test_mensaje():
    assert validar_mensaje(" Mantenimiento hasta las 3 ") == "Mantenimiento hasta las 3"
    with pytest.raises(DatoInvalido):
        validar_mensaje("x" * 281)


# ── usuarios ─────────────────────────────────────────────────────────────────


def test_telegram_id():
    assert validar_telegram_id("5269337388") == 5269337388
    for mal in (0, -5, "abc", None, True, 1.5, 10**14):
        with pytest.raises(DatoInvalido):
            validar_telegram_id(mal)


def test_usuario_valido_normaliza():
    assert validar_usuario("7", " Ana@X.com ", " Ana ", ["compras", "pedidos", "pedidos"]) == (
        7,
        "ana@x.com",
        "Ana",
        ["pedidos", "compras"],
    )


@pytest.mark.parametrize(
    "args",
    [
        (7, "sin-arroba", "Ana", ["pedidos"]),
        (7, "a@x.com", "A" * 101, ["pedidos"]),
        (7, "a@x.com", "Ana", []),
        (7, "a@x.com", "Ana", ["inexistente"]),
        ("x", "a@x.com", "Ana", ["pedidos"]),
    ],
)
def test_usuario_invalido(args):
    with pytest.raises(DatoInvalido):
        validar_usuario(*args)


# ── salud de destinos ────────────────────────────────────────────────────────


def _bot(nombre, **estado):
    return Bot(
        bot=nombre, nombre=nombre, estado=estado, ultimo_latido=AHORA - timedelta(seconds=30)
    )


def test_resumen_sin_bots_en_linea():
    r = resumen_destinos(
        [Bot(bot="pedidos", nombre="V", estado={"omni": "ok"})], AHORA
    )  # nunca latió
    assert r["omni"]["estado"] == r["cxc"]["estado"] == r["gemini"]["estado"] == "sin_datos"


def test_resumen_ok_error_y_desactivado():
    bots = [
        _bot("pedidos", omni="ok", cxc="error", gemini="ok"),
        _bot("cobranza", omni="ok", cxc="ok", gemini="ok"),
        _bot("asana", omni="desactivado", cxc="desactivado", gemini="sin_clave"),
    ]
    r = resumen_destinos(bots, AHORA)
    assert r["omni"]["estado"] == "ok"
    assert (
        r["cxc"]["estado"] == "error" and r["cxc"]["por_bot"]["pedidos"] == "error"
    )  # un error pesa más que un ok
    assert r["gemini"]["estado"] == "ok"
    solo = resumen_destinos(
        [_bot("asana", omni="desactivado", cxc="desactivado", gemini="sin_clave")], AHORA
    )
    assert (solo["omni"]["estado"], solo["gemini"]["estado"]) == ("desactivado", "sin_clave")


def test_un_bot_fuera_de_linea_no_cuenta_en_la_salud():
    viejo = Bot(
        bot="pedidos",
        nombre="V",
        estado={"omni": "error"},
        ultimo_latido=AHORA - timedelta(hours=1),
    )
    r = resumen_destinos([viejo, _bot("cobranza", omni="ok")], AHORA)
    assert r["omni"]["estado"] == "ok" and "pedidos" not in r["omni"]["por_bot"]


@pytest.mark.parametrize(
    "texto",
    [
        f"https://api.telegram.org/bot{TOKEN}/getMe",  # pegado a «bot»: el caso real de las URL
        f"POST /bot{TOKEN}/sendMessage 502",
        f"clave={GOOGLE}&x=1",
        f"x{GOOGLE}",
    ],
)
def test_redactar_oculta_secretos_pegados_a_otro_texto(texto):
    r = redactar(texto)
    assert TOKEN not in r and GOOGLE not in r and "[oculto]" in r
