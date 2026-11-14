"""Permisos por usuario: base del rol + ajustes, el panel de accesos y su bitácora."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from cxc import permisos as acc
from cxc.web.app import app

client = TestClient(app)

ADMIN = {"email": "admin@x.test", "rol": "admin", "permisos": acc.efectivos("admin")}
TESORERIA = {"email": "teso@x.test", "rol": "tesoreria", "permisos": acc.efectivos("tesoreria")}


def _como(usuario):
    return patch("cxc.web.app.get_current_user_from_cookie", return_value=usuario)


# ------------------------------------------------------------------- reglas puras

# Lo que cada rol podía ver ANTES de los ajustes (ROLES_PERMISOS original): la base
# de cada rol tiene que reproducirlo, si no el despliegue le cambia el acceso a la gente.
_PAGINAS = [
    "dashboard",
    "facturacion",
    "conciliaciones",
    "cobranza",
    "ventas",
    "reporte",
    "auditoria",
    "inventario",
    "configuracion",
    "bancos",
]
_SIN_BANCOS = [p for p in _PAGINAS if p != "bancos"]


@pytest.mark.parametrize(
    "rol,paginas",
    [
        ("admin", _PAGINAS),
        ("auditor", _PAGINAS),
        ("tesoreria", _SIN_BANCOS),
        ("gerente_ventas", _SIN_BANCOS),
        ("ventas", _SIN_BANCOS),
    ],
)
def test_la_base_de_cada_rol_conserva_sus_paginas(rol, paginas):
    ef = acc.efectivos(rol)
    assert [p for p in ef if "." not in p] == paginas
    assert ef[0] == "dashboard"  # página de entrada


def test_acciones_que_antes_eran_por_rol():
    assert acc.tiene(TESORERIA, "cobranza.recibo")
    assert not acc.tiene(TESORERIA, "sistema.recalcular")
    gerente = {"rol": "gerente_ventas", "permisos": acc.efectivos("gerente_ventas")}
    assert acc.tiene(gerente, "sistema.recalcular")
    auditor = {"rol": "auditor", "permisos": acc.efectivos("auditor")}
    assert acc.tiene(auditor, "bancos.ver") and acc.tiene(auditor, "auditoria.decidir")
    assert not acc.tiene(auditor, "bancos.cargar")
    assert not acc.tiene(auditor, "bancos.conciliar")


def test_conceder_cargar_bancos_abre_la_pagina_sin_dar_saldos():
    ef = acc.efectivos("tesoreria", {"bancos.cargar": True})
    assert "bancos.cargar" in ef and "bancos" in ef
    assert "bancos.ver" not in ef


def test_quitar_todo_lo_de_bancos_cierra_la_pagina():
    ef = acc.efectivos("auditor", {"bancos.ver": False})
    assert "bancos" not in ef


def test_inicio_y_pagina_derivada_no_se_tocan_con_ajustes():
    ef = acc.efectivos("ventas", {"dashboard": False, "bancos": True, "inventado": True})
    assert ef[0] == "dashboard"
    assert "bancos" not in ef and "inventado" not in ef


def test_admin_tiene_todo_aunque_tenga_ajustes():
    assert acc.efectivos("admin", {"bancos.ver": False}) == acc.efectivos("admin")
    assert acc.tiene({"rol": "admin", "permisos": []}, "bancos.conciliar")


def test_rol_desconocido_solo_ve_el_inicio():
    assert acc.efectivos("vendedor") == ["dashboard"]


def test_ajustes_guarda_solo_la_diferencia_con_el_rol():
    deseados = set(acc.efectivos("tesoreria")) - {"inventario"} | {"bancos.cargar"}
    assert acc.ajustes_para("tesoreria", deseados) == {"bancos.cargar": True, "inventario": False}
    assert acc.ajustes_para("tesoreria", acc.efectivos("tesoreria")) == {}
    assert acc.ajustes_para("admin", []) == {}


def test_ajustes_rechaza_permisos_o_roles_inexistentes():
    with pytest.raises(acc.AccesoInvalido):
        acc.ajustes_para("tesoreria", ["borrar.todo"])
    with pytest.raises(acc.AccesoInvalido):
        acc.ajustes_para("jefe", [])


def test_tiene_sin_sesion():
    assert not acc.tiene(None, "bancos.ver")


# ------------------------------------------------------------------- sesión


def test_la_sesion_aplica_los_ajustes_de_la_persona():
    from cxc.web import app as web

    repo = MagicMock()
    repo.get_usuario_plataforma.return_value = {
        "email": "teso@x.test",
        "rol": "tesoreria",
        "activo": "TRUE",
        "nombre_odoo": "Teso",
    }
    with (
        patch.object(web, "get_repo", return_value=repo),
        patch.object(web, "verificar_session_token", return_value="teso@x.test"),
        patch.object(web.accesos, "ajustes_de", return_value={"bancos.cargar": True}) as aj,
    ):
        user = web.get_current_user_from_cookie("token")
    aj.assert_called_once_with(repo._engine, "teso@x.test")
    assert user is not None
    assert "bancos.cargar" in user["permisos"] and "bancos.ver" not in user["permisos"]


def test_si_no_se_pueden_leer_los_ajustes_no_hay_sesion():
    from cxc.web import app as web

    repo = MagicMock()
    repo.get_usuario_plataforma.return_value = {
        "email": "a@x.test",
        "rol": "ventas",
        "activo": "TRUE",
    }
    with (
        patch.object(web, "get_repo", return_value=repo),
        patch.object(web, "verificar_session_token", return_value="a@x.test"),
        patch.object(web.accesos, "ajustes_de", side_effect=RuntimeError("sin base")),
    ):
        assert web.get_current_user_from_cookie("token") is None


# ------------------------------------------------------------------- API del panel


@pytest.mark.parametrize("usuario,esperado", [(None, 401), (TESORERIA, 403)])
def test_panel_solo_admin(usuario, esperado):
    with _como(usuario):
        assert client.get("/api/admin/permisos/catalogo").status_code == esperado
        assert client.get("/api/admin/accesos/bitacora").status_code == esperado
        r = client.put("/api/admin/usuarios/x@x.test/acceso", json={"permisos": []})
        assert r.status_code == esperado


def test_catalogo():
    with _como(ADMIN):
        r = client.get("/api/admin/permisos/catalogo")
    assert r.status_code == 200
    datos = r.json()
    roles = {x["rol"] for x in datos["roles"]}
    assert roles == {"admin", "gerente_ventas", "tesoreria", "auditor", "ventas"}
    por_clave = {p["clave"]: p for p in datos["permisos"]}
    assert por_clave["bancos.cargar"]["editable"]
    assert not por_clave["dashboard"]["editable"] and not por_clave["bancos"]["editable"]


def test_cambiar_acceso_pasa_quien_lo_hizo():
    with (
        _como(ADMIN),
        patch("cxc.web.app.get_repo", return_value=MagicMock()),
        patch.object(acc, "cambiar_acceso", return_value={"rol": "tesoreria"}) as cambio,
    ):
        r = client.put(
            "/api/admin/usuarios/Alexandra@X.test/acceso",
            json={"permisos": ["bancos.cargar"], "comentario": "sube estados"},
        )
    assert r.status_code == 200
    args, kw = cambio.call_args
    assert args[1] == "alexandra@x.test"
    assert kw["por"] == "admin@x.test" and kw["permisos"] == ["bancos.cargar"]
    assert kw["comentario"] == "sube estados"


@pytest.mark.parametrize("cuerpo", [{"rol": "ventas"}, {"activo": False}])
def test_el_admin_no_se_saca_a_si_mismo(cuerpo):
    with _como(ADMIN), patch.object(acc, "cambiar_acceso") as cambio:
        r = client.put("/api/admin/usuarios/admin@x.test/acceso", json=cuerpo)
    assert r.status_code == 400
    cambio.assert_not_called()


@pytest.mark.parametrize(
    "error,esperado", [(LookupError("x"), 404), (acc.AccesoInvalido("Rol 'x' no es válido."), 400)]
)
def test_cambiar_acceso_errores(error, esperado):
    with (
        _como(ADMIN),
        patch("cxc.web.app.get_repo", return_value=MagicMock()),
        patch.object(acc, "cambiar_acceso", side_effect=error),
    ):
        r = client.put("/api/admin/usuarios/x@x.test/acceso", json={"rol": "x"})
    assert r.status_code == esperado


# ------------------------------------------------------------------- contraseñas


def test_restablecer_clave_ya_no_es_publico():
    # Antes cualquiera con un correo activo en Odoo le cambiaba la clave a otro.
    with _como(None):
        r = client.post(
            "/api/auth/reset-password", json={"email": "a@x.test", "password": "123456"}
        )
    assert r.status_code == 401
    with _como(TESORERIA):
        r = client.post(
            "/api/auth/reset-password", json={"email": "a@x.test", "password": "123456"}
        )
    assert r.status_code == 403


def test_primer_ingreso_no_pisa_una_clave_existente():
    repo = MagicMock()
    repo.get_usuario_plataforma.return_value = {"email": "a@x.test", "password_hash": "h"}
    with (
        patch("cxc.web.app.AppConfig.from_env", return_value=MagicMock()),
        patch("cxc.web.app._connect", return_value=MagicMock()),
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app.registrar_o_actualizar_usuario") as registrar,
    ):
        r = client.post(
            "/api/auth/register-password", json={"email": "a@x.test", "password": "123456"}
        )
    assert r.status_code == 409
    registrar.assert_not_called()


# ------------------------------------------------------------------- bancos por permiso


@pytest.fixture
def sin_base():
    with patch("cxc.web.app._engine_bancos", return_value=MagicMock()):
        yield


def test_quien_solo_carga_no_ve_saldos(sin_base):
    carga = {**TESORERIA, "permisos": acc.efectivos("tesoreria", {"bancos.cargar": True})}
    with _como(carga):
        assert client.get("/api/bancos/parsers").status_code == 200
        with patch("cxc.web.app.bancos_repo.archivos", return_value=[]):
            assert client.get("/api/bancos/archivos").status_code == 200
        assert client.get("/api/bancos/saldos").status_code == 403
        assert client.get("/api/bancos/movimientos").status_code == 403
        assert client.get("/api/bancos/conciliacion").status_code == 403
        assert client.post("/api/bancos/conciliacion/generar").status_code == 403


def test_tesoreria_sin_ajustes_no_entra_a_bancos(sin_base):
    with _como(TESORERIA):
        assert client.get("/api/bancos/parsers").status_code == 403


# ------------------------------------------------------------------- base de datos

_URL = os.environ.get("DATABASE_URL", "")
_EMAILS = ("alexandra@x.test", "otro@x.test")


@pytest.fixture
def engine():
    if not _URL:
        pytest.skip("Sin DATABASE_URL: los ajustes de acceso necesitan Postgres.")
    from cxc.db.engine import make_engine

    eng = make_engine(_URL)
    with eng.connect() as c:
        if not c.execute(text("SELECT to_regclass('accesos_bitacora')")).scalar():
            pytest.skip("Las tablas las crea la migración (alembic upgrade head).")

    def limpiar():
        with eng.begin() as c:
            c.execute(
                text("DELETE FROM usuarios_permisos WHERE email = ANY(:e)"), {"e": list(_EMAILS)}
            )
            c.execute(
                text("DELETE FROM usuarios_plataforma WHERE email = ANY(:e)"), {"e": list(_EMAILS)}
            )
            c.execute(text("TRUNCATE accesos_bitacora RESTART IDENTITY"))

    limpiar()
    with eng.begin() as c:
        for e in _EMAILS:
            c.execute(
                text(
                    "INSERT INTO usuarios_plataforma (email, nombre, rol, activo) "
                    "VALUES (:e, 'X', 'tesoreria', true)"
                ),
                {"e": e},
            )
    yield eng
    limpiar()
    eng.dispose()


def test_conceder_y_quitar_queda_en_la_bitacora(engine):
    deseados = set(acc.efectivos("tesoreria")) | {"bancos.cargar"}
    r = acc.cambiar_acceso(
        engine, "Alexandra@x.test", por="admin@x.test", permisos=deseados, comentario="estados"
    )
    assert r["cambio"] and r["ajustes"] == {"bancos.cargar": True}
    assert acc.ajustes_de(engine, "alexandra@x.test") == {"bancos.cargar": True}
    assert acc.todos_los_ajustes(engine) == {"alexandra@x.test": {"bancos.cargar": True}}

    log = acc.bitacora(engine, "alexandra@x.test")
    assert len(log) == 1
    assert log[0]["usuario"] == "admin@x.test" and log[0]["comentario"] == "estados"
    assert "bancos.cargar" not in log[0]["antes"]["permisos"]
    assert "bancos.cargar" in log[0]["despues"]["permisos"]

    # Lo mismo otra vez: nada que registrar.
    r = acc.cambiar_acceso(engine, "alexandra@x.test", por="admin@x.test", permisos=deseados)
    assert not r["cambio"]
    assert len(acc.bitacora(engine)) == 1


def test_cambiar_rol_conserva_ajustes_y_admin_los_borra(engine):
    acc.cambiar_acceso(
        engine,
        "alexandra@x.test",
        por="a",
        permisos=set(acc.efectivos("tesoreria")) | {"bancos.cargar"},
    )
    r = acc.cambiar_acceso(engine, "alexandra@x.test", por="a", rol="ventas")
    assert r["rol"] == "ventas" and "bancos.cargar" in r["permisos"]
    r = acc.cambiar_acceso(engine, "alexandra@x.test", por="a", rol="admin")
    assert r["ajustes"] == {} and acc.ajustes_de(engine, "alexandra@x.test") == {}


def test_desactivar(engine):
    r = acc.cambiar_acceso(engine, "otro@x.test", por="a", activo=False)
    assert r["activo"] is False
    with engine.connect() as c:
        assert (
            c.execute(
                text("SELECT activo FROM usuarios_plataforma WHERE email = 'otro@x.test'")
            ).scalar()
            is False
        )
    assert acc.bitacora(engine, "otro@x.test")[0]["despues"]["activo"] is False


def test_usuario_inexistente_o_rol_invalido(engine):
    with pytest.raises(LookupError):
        acc.cambiar_acceso(engine, "nadie@x.test", por="a", rol="ventas")
    with pytest.raises(acc.AccesoInvalido):
        acc.cambiar_acceso(engine, "otro@x.test", por="a", rol="jefe")
    assert acc.bitacora(engine) == []


def test_la_bitacora_no_se_edita_ni_se_borra(engine):
    acc.cambiar_acceso(engine, "otro@x.test", por="a", rol="ventas")
    for sql in ("UPDATE accesos_bitacora SET usuario = 'x'", "DELETE FROM accesos_bitacora"):
        with pytest.raises(Exception, match="solo-append"), engine.begin() as c:
            c.execute(text(sql))
