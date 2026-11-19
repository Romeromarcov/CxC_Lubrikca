"""Pruebas Fase 1: rutas canónicas (/inicio + 9 módulos), redirects 302 y /api/inicio/*."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cxc.operativos.servicio import ServicioOperativos
from cxc.permisos import PAGINAS, efectivos
from cxc.web.app import app
from cxc.web.operativos_api import get_servicio_operativos
from tests.test_documentos_operativos import RepoOperativosMemoria

client = TestClient(app)

ADMIN = {"email": "admin@x.test", "rol": "admin", "permisos": efectivos("admin")}


@pytest.mark.parametrize("ruta", [f"/{clave}" for clave, _ in PAGINAS])
def test_rutas_canonicas_sirven_spa_con_sesion_y_redirigen_sin_sesion(ruta: str) -> None:
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=None):
        r_anon = client.get(ruta, follow_redirects=False)
        assert r_anon.status_code in (302, 307)
        assert r_anon.headers["location"] == "/login"

    with patch("cxc.web.app.get_current_user_from_cookie", return_value=ADMIN):
        r_ok = client.get(ruta, follow_redirects=False)
        assert r_ok.status_code == 200
        assert 'id="app-sidebar"' in r_ok.text
        assert 'id="tab-inicio"' in r_ok.text


@pytest.mark.parametrize(
    ("legado", "destino"),
    [
        ("/dashboard", "/inicio"),
        ("/cobranza", "/pagos?tab=recibidos"),
        ("/conciliaciones", "/pagos?tab=recibidos"),
        ("/bancos", "/pagos?tab=conciliacion"),
        ("/capturas", "/compras?tab=facturas"),
        ("/bots", "/configuracion?tab=bots"),
        ("/reporte", "/reportes"),
    ],
)
def test_redirecciones_302_rutas_legadas(legado: str, destino: str) -> None:
    r = client.get(legado, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == destino


def test_endpoints_inicio_exigen_sesion_y_devuelven_contrato() -> None:
    with patch("cxc.web.app.hay_sesion_valida", return_value=False):
        assert client.get("/api/inicio/resumen").status_code == 401
        assert client.get("/api/inicio/buscar?q=lub").status_code == 401

    with patch("cxc.web.app._inicio_engine_seguro", return_value=None):
        r_res = client.get("/api/inicio/resumen")
        assert r_res.status_code == 200
        body = r_res.json()
        assert set(body) == {"contadores", "modulos"}
        assert set(body["modulos"]) == {"pagos", "ventas", "compras", "auditoria"}

        r_bus_corto = client.get("/api/inicio/buscar?q=a")
        assert r_bus_corto.status_code == 200 and r_bus_corto.json() == {"q": "a", "items": []}

        r_bus = client.get("/api/inicio/buscar?q=Lubrikca")
        assert r_bus.status_code == 200 and r_bus.json() == {"q": "Lubrikca", "items": []}


def test_operativos_filtra_pagos_enviados_y_compras_solo() -> None:
    repo = RepoOperativosMemoria()
    svc = ServicioOperativos(repo, lambda: None)
    app.dependency_overrides[get_servicio_operativos] = lambda: svc
    try:
        svc.crear(
            {
                "tipo": "pago",
                "cliente": "Cliente Uno",
                "monto": "100",
                "moneda": "USD",
                "referencia": "REF-REC-1",
            },
            actor="tester",
        )
        svc.crear(
            {
                "tipo": "pago_proveedor",
                "proveedor": "Prov Uno",
                "monto": "200",
                "moneda": "USD",
                "referencia": "REF-ENV-1",
            },
            actor="tester",
        )
        svc.crear(
            {
                "tipo": "factura_proveedor",
                "proveedor": "Prov Dos",
                "numero_documento": "FAC-01",
                "fecha_documento": "2026-11-18",
                "monto": "300",
                "moneda": "VES",
            },
            actor="tester",
        )
        svc.crear(
            {
                "tipo": "gasto",
                "proveedor": "Estacion Servicio",
                "fecha_documento": "2026-11-18",
                "monto": "50",
                "moneda": "VES",
            },
            actor="tester",
        )

        with patch("cxc.web.app.get_current_user_from_cookie", return_value=ADMIN):
            r_env = client.get("/api/operativos?grupo=pagos_enviados")
            assert r_env.status_code == 200
            assert [it["tipo"] for it in r_env.json()["items"]] == ["pago_proveedor"]

            r_cmp = client.get("/api/operativos?grupo=compras_solo")
            assert r_cmp.status_code == 200
            assert {it["tipo"] for it in r_cmp.json()["items"]} == {
                "factura_proveedor",
                "gasto",
            }
    finally:
        app.dependency_overrides.pop(get_servicio_operativos, None)


def test_permisos_sin_db_no_rompen_inicio_en_test() -> None:
    """Smoke rápido de que `/` redirige a `/inicio` cuando el usuario es admin."""
    with patch("cxc.web.app.get_current_user_from_cookie", return_value=ADMIN):
        r = client.get("/", follow_redirects=False)
        assert r.status_code in (302, 307)
        assert r.headers["location"] == "/inicio"
