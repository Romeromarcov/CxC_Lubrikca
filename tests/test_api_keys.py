"""Llaves de API de solo lectura (25-sep-2026, pedido del usuario): "cómo

puedo dar acceso a otro software para que se conecte a nuestro sistema vía
API". Una llave de API nunca puede escribir -- el middleware
(``exigir_sesion_en_api``) solo la acepta en rutas ``GET``, sin importar
el endpoint.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cxc.auth import generar_api_key, hash_api_key, verificar_api_key
from cxc.web.app import app

client = TestClient(app)


def test_generar_hash_verificar_api_key_ida_y_vuelta() -> None:
    api_key = generar_api_key()
    assert api_key.startswith("cxc_live_")
    h = hash_api_key(api_key)
    assert verificar_api_key(api_key, h) is True
    assert verificar_api_key("otra-llave-cualquiera", h) is False


def _mock_repo(api_keys_rows):
    mock_repo = MagicMock()
    mock_repo.all_api_keys.return_value = api_keys_rows
    mock_repo.feriados.return_value = []
    return mock_repo


def test_get_con_llave_valida_pasa_el_middleware() -> None:
    api_key = generar_api_key()
    rows = [{"key_id": "K1", "key_hash": hash_api_key(api_key), "activo": "true"}]
    with patch("cxc.web.app.get_repo", return_value=_mock_repo(rows)):
        res = client.get(
            "/api/config/feriados", headers={"Authorization": f"Bearer {api_key}"}
        )
    assert res.status_code == 200


def test_get_con_llave_invalida_da_401() -> None:
    rows = [{"key_id": "K1", "key_hash": hash_api_key(generar_api_key()), "activo": "true"}]
    with patch("cxc.web.app.get_repo", return_value=_mock_repo(rows)):
        res = client.get(
            "/api/config/feriados", headers={"Authorization": "Bearer llave-que-no-es"}
        )
    assert res.status_code == 401


def test_get_con_llave_revocada_da_401() -> None:
    api_key = generar_api_key()
    rows = [{"key_id": "K1", "key_hash": hash_api_key(api_key), "activo": "false"}]
    with patch("cxc.web.app.get_repo", return_value=_mock_repo(rows)):
        res = client.get(
            "/api/config/feriados", headers={"Authorization": f"Bearer {api_key}"}
        )
    assert res.status_code == 401


def test_post_con_llave_valida_sigue_dando_401() -> None:
    """Una llave de API nunca escribe -- el middleware la ignora fuera de GET."""
    api_key = generar_api_key()
    rows = [{"key_id": "K1", "key_hash": hash_api_key(api_key), "activo": "true"}]
    with patch("cxc.web.app.get_repo", return_value=_mock_repo(rows)):
        res = client.post(
            "/api/config/tasas",
            json={},
            headers={"Authorization": f"Bearer {api_key}"},
        )
    assert res.status_code == 401


def test_sin_header_authorization_sigue_dando_401() -> None:
    assert client.get("/api/config/feriados").status_code == 401


def test_endpoints_de_gestion_de_llaves_exigen_sesion_no_solo_llave() -> None:
    """Administrar llaves (crear/listar/revocar) exige la SESIÓN de un

    admin, no solo una llave de API válida -- el middleware la deja pasar
    (es GET), pero el endpoint mismo exige ``cxc_session`` de un admin;
    sin cookie, rechaza con 403. Una llave de API no puede usarse para
    gestionarse a sí misma ni a otras.
    """
    api_key = generar_api_key()
    rows = [{"key_id": "K1", "key_hash": hash_api_key(api_key), "activo": "true"}]
    with patch("cxc.web.app.get_repo", return_value=_mock_repo(rows)):
        res = client.get(
            "/api/admin/api-keys", headers={"Authorization": f"Bearer {api_key}"}
        )
    assert res.status_code == 403
