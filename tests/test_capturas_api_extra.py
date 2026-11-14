"""Capturas: completar desde la pantalla, configuración por entorno y demonio de reintentos."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from cxc.web import capturas_api as m
from tests.test_capturas_api import (
    ADMIN,
    AUDITOR,
    BOT,
    TESORERIA,
    VENTAS,
    _como,
    _incompleto,
    client,
    entorno,  # noqa: F401  (fixture)
)

# ── completar: bot o sesión con capturas.gestionar ───────────────────────────


def test_completar_desde_la_pantalla_con_sesion_y_permiso(entorno: Any) -> None:  # noqa: F811
    _, repo, odoo = entorno
    cid = _incompleto(entorno)
    with _como(TESORERIA):
        r = client.post(
            f"/api/bots/capturas/{cid}/completar", json={"cambios": {"referencia": "R5"}}
        )
    assert r.status_code == 200 and r.json()["payload"]["referencia"] == "R5"
    assert repo.filas[cid].estado == "ENVIADO" and odoo.creados_de("account.payment")
    # la bitácora registra a la persona, no al bot
    assert (cid, "completada", "referencia", "t@x.test") in repo.bitacora


def test_completar_por_el_bot_registra_al_bot(entorno: Any) -> None:  # noqa: F811
    _, repo, _ = entorno
    cid = _incompleto(entorno)
    client.post(
        f"/api/bots/capturas/{cid}/completar", json={"cambios": {"referencia": "R5"}}, headers=BOT
    )
    assert (cid, "completada", "referencia", "bot") in repo.bitacora


def test_completar_con_sesion_exige_gestionar(entorno: Any) -> None:  # noqa: F811
    cid = _incompleto(entorno)
    body = {"cambios": {"referencia": "R5"}}
    with _como(AUDITOR):  # solo puede ver
        assert client.post(f"/api/bots/capturas/{cid}/completar", json=body).status_code == 403
    with _como(VENTAS):
        assert client.post(f"/api/bots/capturas/{cid}/completar", json=body).status_code == 403
    with _como(None):
        assert client.post(f"/api/bots/capturas/{cid}/completar", json=body).status_code == 401
    with _como(ADMIN):
        assert client.post(f"/api/bots/capturas/{cid}/completar", json=body).status_code == 200


def test_clave_de_bot_mala_no_cae_a_la_sesion(entorno: Any) -> None:  # noqa: F811
    cid = _incompleto(entorno)
    with _como(ADMIN):  # aunque haya una sesión con todos los permisos
        r = client.post(
            f"/api/bots/capturas/{cid}/completar",
            json={"cambios": {"referencia": "R"}},
            headers={"X-Bot-Key": "mala"},
        )
    assert r.status_code == 401


def test_recibir_sigue_siendo_solo_del_bot(entorno: Any) -> None:  # noqa: F811
    """La sesión con gestionar completa, pero no puede crear capturas como si fuera el bot."""
    body = {
        "kind": "pago",
        "draft": {"cliente": "Taller", "monto": "5", "moneda": "USD", "referencia": "R"},
        "source": {},
        "idempotency_key": "s1",
    }
    with _como(ADMIN):
        assert client.post("/api/bots/capturas", json=body).status_code == 401


# ── configuración por entorno ────────────────────────────────────────────────


def test_diarios_env_tolera_json_valido_e_invalido(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPTURAS_DIARIOS", raising=False)
    assert m._diarios_env() == {}
    monkeypatch.setenv("CAPTURAS_DIARIOS", '{"default": 14, "bancamiga_usd": "41"}')
    assert m._diarios_env() == {"default": 14, "bancamiga_usd": 41}
    for malo in ("no es json", "[1, 2]", '{"a": "x"}'):
        monkeypatch.setenv("CAPTURAS_DIARIOS", malo)
        assert m._diarios_env() == {}


def test_max_intentos_por_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPTURAS_MAX_INTENTOS", raising=False)
    assert m._max_intentos() == 5
    monkeypatch.setenv("CAPTURAS_MAX_INTENTOS", "2")
    assert m._max_intentos() == 2
    monkeypatch.setenv("CAPTURAS_MAX_INTENTOS", "0")
    assert m._max_intentos() == 1
    monkeypatch.setenv("CAPTURAS_MAX_INTENTOS", "muchos")
    assert m._max_intentos() == 5


def test_fabrica_de_escritor_es_none_sin_escritura_activa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAPTURAS_ODOO_ESCRITURA", raising=False)
    assert m._fabrica_escritor() is None  # sin conectar a nada


def test_la_escritura_viene_apagada_por_defecto(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("CAPTURAS_ODOO_ESCRITURA", "ODOO_WRITE_USERNAME", "ODOO_WRITE_PASSWORD"):
        monkeypatch.delenv(v, raising=False)
    with patch.object(m, "conectar") as conectar:
        assert m._fabrica_escritor() is None
    conectar.assert_not_called()


def test_get_servicio_arma_el_servicio_con_la_configuracion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPTURAS_DIARIOS", '{"default": 9}')
    monkeypatch.setenv("CAPTURAS_MAX_INTENTOS", "3")
    with patch.object(m, "_engine", return_value=MagicMock()):
        svc = m.get_servicio()
    assert svc._diarios == {"default": 9} and svc._max_intentos == 3


# ── demonio de reintentos ────────────────────────────────────────────────────


def _correr_demonio(activa: bool, servicio: Any) -> None:
    cfg = object() if activa else None
    with (
        patch.object(m, "INTERVALO_REINTENTO_SEGUNDOS", 0),
        patch.object(m.EscrituraConfig, "from_env", return_value=cfg),
        patch.object(m, "get_servicio", return_value=servicio),
        pytest.raises(asyncio.TimeoutError),
    ):
        asyncio.run(asyncio.wait_for(m.run_reintentos_capturas_en_background(), 0.05))


def test_demonio_no_toca_nada_con_la_escritura_desactivada() -> None:
    svc = MagicMock()
    _correr_demonio(False, svc)
    svc.reintentar_pendientes.assert_not_called()


def test_demonio_reintenta_con_la_escritura_activa_y_sobrevive_a_los_fallos() -> None:
    svc = MagicMock()
    svc.reintentar_pendientes.side_effect = [2, RuntimeError("base caída"), 0, 0, 0]
    _correr_demonio(True, svc)
    assert svc.reintentar_pendientes.call_count >= 3  # el fallo no detuvo el ciclo
