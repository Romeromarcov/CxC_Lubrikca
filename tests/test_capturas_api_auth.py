"""Cierre de la API frente a las capturas: la clave del bot solo abre DOS rutas POST.

Va aparte (y excluido del parche de sesión de ``conftest``) para ejercer el middleware de verdad,
igual que ``test_api_keys.py`` y ``test_auth_api_cerrada.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from cxc.auth import generar_api_key, hash_api_key
from cxc.capturas.servicio import ServicioCapturas
from cxc.odoo.escritura import EscritorOdoo
from cxc.web.app import app
from cxc.web.capturas_api import es_ruta_bot, get_servicio
from tests.capturas_dobles import EspejoFalso, OdooFalso, RepoMemoria

client = TestClient(app)
KEY = "clave-de-prueba-del-bot"
FUENTE = {"telegram_user_id": 7, "vendedor_email": "ana@x.com"}
CUERPO = {
    "kind": "pago",
    "draft": {"cliente": "T", "monto": "5", "moneda": "USD", "referencia": "R"},
    "source": FUENTE,
    "idempotency_key": "k1",
}


@pytest.fixture
def servicio(monkeypatch: pytest.MonkeyPatch) -> Iterator[RepoMemoria]:
    monkeypatch.setenv("BOT_API_KEY", KEY)
    repo = RepoMemoria()
    svc = ServicioCapturas(
        repo,
        EspejoFalso(clientes={"T": ["1"]}),
        lambda: EscritorOdoo(OdooFalso()),
        diarios={"default": 14},
    )
    app.dependency_overrides[get_servicio] = lambda: svc
    yield repo
    app.dependency_overrides.pop(get_servicio, None)


# ── es_ruta_bot ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "metodo,ruta,esperado",
    [
        ("POST", "/api/bots/capturas", True),
        ("POST", "/api/bots/capturas/12/completar", True),
        ("GET", "/api/bots/capturas", False),
        ("POST", "/api/bots/capturas/12/reintentar", False),
        ("POST", "/api/bots/capturas/12", False),
        ("POST", "/api/bots/capturas/x/completar", False),
        ("POST", "/api/bots/capturas/", False),
        ("POST", "/api/bots/capturas/12/completar/extra", False),
        ("POST", "/api/config/tasas", False),
        ("DELETE", "/api/bots/capturas/12/completar", False),
    ],
)
def test_solo_dos_rutas_post_aceptan_la_clave_del_bot(
    metodo: str, ruta: str, esperado: bool
) -> None:
    assert es_ruta_bot(metodo, ruta) is esperado


# ── middleware ───────────────────────────────────────────────────────────────


def test_el_bot_entra_sin_sesion_con_su_clave(servicio: RepoMemoria) -> None:
    r = client.post("/api/bots/capturas", json=CUERPO, headers={"X-Bot-Key": KEY})
    assert r.status_code == 201 and len(servicio.filas) == 1


def test_sin_sesion_y_sin_clave_da_401_del_middleware(servicio: RepoMemoria) -> None:
    assert client.post("/api/bots/capturas", json=CUERPO).status_code == 401
    assert servicio.filas == {}


def test_clave_mala_la_rechaza_el_endpoint(servicio: RepoMemoria) -> None:
    assert (
        client.post("/api/bots/capturas", json=CUERPO, headers={"X-Bot-Key": "mala"}).status_code
        == 401
    )
    assert servicio.filas == {}


def test_con_la_clave_del_bot_no_se_abre_nada_mas(servicio: RepoMemoria) -> None:
    h = {"X-Bot-Key": KEY}
    assert client.post("/api/bots/capturas/1/reintentar", headers=h).status_code == 401
    assert client.get("/api/bots/capturas", headers=h).status_code == 401
    assert client.get("/api/bots/capturas/1", headers=h).status_code == 401
    assert client.post("/api/config/tasas", json={}, headers=h).status_code == 401
    assert client.get("/api/config/feriados", headers=h).status_code == 401


def test_una_sesion_sola_no_basta_para_enviar_como_bot(servicio: RepoMemoria) -> None:
    """El endpoint del bot exige SIEMPRE X-Bot-Key: una sesión no puede inyectar capturas."""
    with patch("cxc.web.app.hay_sesion_valida", return_value=True):
        assert client.post("/api/bots/capturas", json=CUERPO).status_code == 401
    assert servicio.filas == {}


def test_una_llave_de_api_normal_sigue_siendo_solo_lectura(servicio: RepoMemoria) -> None:
    api_key = generar_api_key()
    repo = MagicMock()
    repo.all_api_keys.return_value = [
        {"key_id": "K1", "key_hash": hash_api_key(api_key), "activo": "true"}
    ]
    repo.feriados.return_value = []
    with patch("cxc.web.app.get_repo", return_value=repo):
        r = client.post(
            "/api/bots/capturas", json=CUERPO, headers={"Authorization": f"Bearer {api_key}"}
        )
        assert r.status_code == 401
        # y tampoco abre los GET de capturas (necesitan sesión con permiso)
        g = client.get("/api/bots/capturas", headers={"Authorization": f"Bearer {api_key}"})
    assert g.status_code in (401, 403) and servicio.filas == {}


def test_la_clave_del_bot_no_sirve_como_llave_de_api(servicio: RepoMemoria) -> None:
    repo = MagicMock()
    repo.all_api_keys.return_value = []
    with patch("cxc.web.app.get_repo", return_value=repo):
        r = client.get("/api/config/feriados", headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 401


def test_completar_con_clave_del_bot_pasa_el_middleware(servicio: RepoMemoria) -> None:
    h = {"X-Bot-Key": KEY}
    cid = client.post(
        "/api/bots/capturas", json={**CUERPO, "draft": {"cliente": "T", "monto": "5"}}, headers=h
    ).json()["id"]
    r = client.post(
        f"/api/bots/capturas/{cid}/completar",
        json={"cambios": {"moneda": "USD", "referencia": "R"}},
        headers=h,
    )
    assert r.status_code == 200 and servicio.filas[cid].estado == "ENVIADO"


def test_sesion_con_gestionar_completa_pero_no_crea_capturas(servicio: RepoMemoria) -> None:
    """Con el middleware real: la pantalla puede completar (sesión + permiso) y nada más."""
    from cxc.permisos import efectivos

    usuario = {"email": "t@x.test", "rol": "tesoreria", "permisos": efectivos("tesoreria")}
    incompleto = {**CUERPO, "draft": {"cliente": "T", "monto": "5"}}
    cid = client.post("/api/bots/capturas", json=incompleto, headers={"X-Bot-Key": KEY}).json()[
        "id"
    ]
    cambios = {"cambios": {"moneda": "USD", "referencia": "R9"}}
    with (
        patch("cxc.web.app.hay_sesion_valida", return_value=True),
        patch("cxc.web.app.get_current_user_from_cookie", return_value=usuario),
    ):
        r = client.post(f"/api/bots/capturas/{cid}/completar", json=cambios)
        assert r.status_code == 200 and servicio.filas[cid].estado == "ENVIADO"
        assert client.post("/api/bots/capturas", json=CUERPO).status_code == 401
    # sin sesión ni clave, el middleware la cierra
    assert client.post(f"/api/bots/capturas/{cid}/completar", json=cambios).status_code == 401
