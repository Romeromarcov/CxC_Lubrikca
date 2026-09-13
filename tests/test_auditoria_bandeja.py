"""La bandeja de auditoría, y las diez guardas que podían vaciarla en silencio.

`get_auditoria_descuentos` y `patch_auditoria_estado` salieron del barrido de
cobertura (14 de 25 líneas sin cubrir la primera).

**Lo que había, y por qué importa.** Diez sitios de `app.py` envolvían las llamadas de
auditoría en `hasattr(repo, "...")`:

    rows = repo.all_auditoria() if hasattr(repo, "all_auditoria") else []
    if hasattr(repo, "update_auditoria_estado"):
        repo.update_auditoria_estado(...)
    return {"status": "ok", ...}

El comentario del protocolo explica de dónde venían: *«el backend Postgres nunca los
implementó pese a que la tabla `bandeja_auditoria` sí existe desde el esquema inicial
— en Postgres, la persistencia de auditoría era un no-op silencioso»*. Esos `hasattr`
**eran** el mecanismo de ese no-op.

Los tres métodos son `@abstractmethod` en el protocolo desde que eso se arregló, así
que las diez guardas no podían ser falsas nunca. Quedaban como el fósil del bug, listo
para esconder el siguiente: una bandeja vacía se lee como «no hay nada que auditar», y
el `PATCH` devolvía `"ok"` con el estado nuevo **sin haber guardado nada**.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import cxc.web.app as app

_FILAS = [
    {"audit_id": "A1", "estado": "pendiente", "tipo_auditoria": "descuento_orden"},
    {"audit_id": "A2", "estado": "aprobado", "tipo_auditoria": "descuento_orden"},
    {"audit_id": "A3", "estado": "pendiente", "tipo_auditoria": "nota_credito"},
]


@pytest.fixture
def repo():
    r = MagicMock()
    r.all_auditoria.return_value = list(_FILAS)
    return r


def _parches(repo, usuario):
    async def _nada():
        return None

    return (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app.get_current_user_from_cookie", return_value=usuario),
        patch("cxc.web.app.run_scraper_in_background", _nada),
        patch("cxc.web.app.run_sync_in_background", _nada),
        patch("cxc.web.app._aplicar_migraciones_pendientes"),
    )


def _pedir(repo, usuario, metodo, ruta, **kw):
    """Un solo lugar que arma los parches y el cliente, para no repetirlos por test."""
    with ExitStack() as pila:
        for parche in _parches(repo, usuario):
            pila.enter_context(parche)
        cliente = pila.enter_context(TestClient(app.app, raise_server_exceptions=False))
        return getattr(cliente, metodo)(ruta, **kw)


def _get(repo, query="", usuario=None):
    if usuario is None:
        usuario = {"rol": "admin", "nombre": "Ana"}
    return _pedir(repo, usuario, "get", f"/api/auditoria-descuentos{query}")


def test_la_bandeja_devuelve_todas_las_filas_con_su_total(repo) -> None:
    r = _get(repo)
    assert r.status_code == 200
    assert r.json()["total"] == 3
    repo.all_auditoria.assert_called_once(), "se lee del repo, no de un default vacío"


def test_el_filtro_por_estado_no_cuenta_las_demas(repo) -> None:
    cuerpo = _get(repo, "?estado=pendiente").json()
    assert cuerpo["total"] == 2
    assert {x["audit_id"] for x in cuerpo["items"]} == {"A1", "A3"}


def test_el_filtro_por_tipo_es_independiente_del_de_estado(repo) -> None:
    cuerpo = _get(repo, "?tipo=nota_credito").json()
    assert [x["audit_id"] for x in cuerpo["items"]] == ["A3"]


def test_los_dos_filtros_se_combinan(repo) -> None:
    cuerpo = _get(repo, "?estado=pendiente&tipo=descuento_orden").json()
    assert [x["audit_id"] for x in cuerpo["items"]] == ["A1"]


def test_un_filtro_que_no_matchea_da_cero_y_no_todas(repo) -> None:
    """Un filtro mal escrito tiene que dar vacío, no la bandeja entera."""
    cuerpo = _get(repo, "?estado=inventado").json()
    assert cuerpo == {"items": [], "total": 0}


def test_sin_sesion_la_bandeja_no_se_sirve(repo) -> None:
    r = _get(repo, usuario=False)
    assert r.status_code == 401
    repo.all_auditoria.assert_not_called()


# --- el PATCH, que devolvía "ok" sin guardar ---------------------------------


def _patch(repo, usuario, estado="aprobado", audit_id_cuerpo="A1"):
    # `AuditoriaEstadoRequest` exige `audit_id` en el CUERPO además del de la ruta, y
    # el endpoint usa el de la ruta ignorando el del cuerpo. Ver el test de abajo.
    return _pedir(
        repo,
        usuario,
        "patch",
        "/api/auditoria-descuentos/A1",
        json={"audit_id": audit_id_cuerpo, "estado": estado},
    )


def test_el_patch_guarda_de_verdad_antes_de_decir_ok(repo) -> None:
    """La guarda que se fue: `if hasattr(...)` podía saltear el guardado y devolver "ok".

    No podía ocurrir --el método es `@abstractmethod`-- pero la forma estaba ahí, y una
    respuesta que dice el estado nuevo sin haberlo guardado es la peor clase de bug:
    la pantalla muestra lo que el usuario pidió y la base no lo tiene.
    """
    r = _patch(repo, {"rol": "admin", "nombre": "Ana"})
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "audit_id": "A1", "nuevo_estado": "aprobado"}
    repo.update_auditoria_estado.assert_called_once_with(
        audit_id="A1", estado="aprobado", revisado_por="Ana"
    )


def test_si_el_guardado_falla_el_patch_NO_dice_ok(repo) -> None:
    """El 500 es lo correcto acá: el estado no cambió y hay que saberlo."""
    repo.update_auditoria_estado.side_effect = RuntimeError("base caida")
    r = _patch(repo, {"rol": "admin", "nombre": "Ana"})
    assert r.status_code == 500


@pytest.mark.parametrize("rol", ["vendedor", "cobranza", ""])
def test_solo_admin_y_contabilidad_pueden_cambiar_el_estado(repo, rol) -> None:
    r = _patch(repo, {"rol": rol, "nombre": "Quien"})
    assert r.status_code == 403
    repo.update_auditoria_estado.assert_not_called()


def test_sin_sesion_el_patch_da_401_y_no_403(repo) -> None:
    """Distinguirlos importa: 401 es «identificate», 403 es «no te alcanza el rol»."""
    r = _patch(repo, False)
    assert r.status_code == 401
    repo.update_auditoria_estado.assert_not_called()


def test_el_audit_id_que_manda_es_el_de_la_RUTA_no_el_del_cuerpo(repo) -> None:
    """El modelo exige `audit_id` en el cuerpo y el endpoint lo ignora.

    Si los dos no coinciden gana el de la ruta, en silencio. No es un defecto de
    dinero, pero es una API que invita a la confusión, y queda fijado para que quien
    la cambie sepa qué comportamiento estaba en pie.
    """
    _patch(repo, {"rol": "admin", "nombre": "Ana"}, audit_id_cuerpo="OTRO")
    assert repo.update_auditoria_estado.call_args.kwargs["audit_id"] == "A1"


def test_quien_reviso_sale_del_nombre_y_si_no_del_email(repo) -> None:
    """Y nunca queda vacío: una fila de auditoría sin revisor no sirve para auditar."""
    _patch(repo, {"rol": "contabilidad", "email": "c@lubrikca.test"})
    assert repo.update_auditoria_estado.call_args.kwargs["revisado_por"] == "c@lubrikca.test"

    repo.update_auditoria_estado.reset_mock()
    _patch(repo, {"rol": "contabilidad"})
    assert repo.update_auditoria_estado.call_args.kwargs["revisado_por"] == "desconocido"
