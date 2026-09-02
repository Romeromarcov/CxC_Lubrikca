"""La API exige sesión en TODA ruta /api/ salvo las de autenticación.

Hallazgo crítico de la auditoría de producción (agosto 2026): la
autenticación era opt-in endpoint por endpoint y de 101 endpoints solo ~8
la exigían. 33 endpoints de escritura (reglas de descuento, tasas de
cambio, vinculación de pagos, aprobación de descuentos) respondían sin
ninguna cookie. Estos tests fijan el default cerrado: si alguien agrega un
endpoint nuevo sin pensar en auth, nace protegido.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from cxc.web.app import _API_RUTAS_PUBLICAS, app

client = TestClient(app)


def test_endpoint_de_datos_sin_sesion_da_401() -> None:
    assert client.get("/api/bandeja").status_code == 401


def test_endpoint_de_configuracion_sin_sesion_da_401() -> None:
    # El caso verificado en vivo contra producción: la configuración de
    # reglas de descuento respondía 200 sin cookie.
    assert client.get("/api/config/descuentos-pronto-pago").status_code == 401


def test_escritura_sin_sesion_da_401() -> None:
    """Ninguna escritura debe pasar sin sesión -- ni siquiera con cuerpo
    inválido: el 401 tiene que ganarle a la validación del cuerpo."""
    for ruta in (
        "/api/vincular",
        "/api/config/tasas",
        "/api/config/descuentos-pronto-pago",
        "/api/facturacion/aprobar-descuento-sistema",
        "/api/auditoria/aceptar-anomalia",
    ):
        assert client.post(ruta, json={}).status_code == 401, ruta


def test_login_sigue_alcanzable_sin_sesion() -> None:
    """La lista blanca no puede dejar al usuario sin forma de entrar."""
    # Credenciales vacías -> el endpoint responde (401/422 por credenciales
    # o cuerpo, no por el middleware). Lo que importa es que NO sea el 401
    # del middleware sobre una ruta que debería ser pública.
    assert "/api/auth/login" in _API_RUTAS_PUBLICAS
    assert client.post("/api/auth/login", json={}).status_code != 500


def test_paginas_html_no_pasan_por_el_middleware() -> None:
    """El cascarón de la SPA sigue sirviéndose; ya redirige a /login solo."""
    res = client.get("/login")
    assert res.status_code == 200


def test_toda_ruta_api_del_codigo_esta_cubierta() -> None:
    """Guardia contra regresión: enumera las rutas /api/ declaradas en el

    código y confirma que ninguna queda fuera del middleware por accidente
    (o sea, que solo las de la lista blanca responden sin sesión).
    """
    with open("src/cxc/web/app.py", encoding="utf-8") as fh:
        src = fh.read()
    rutas = set(re.findall(r'@app\.(?:get|post|put|delete|api_route)\("(/api/[^"{]*)"', src))
    assert len(rutas) > 50, "no se detectaron las rutas; ¿cambió el patrón del decorador?"
    for ruta in sorted(rutas):
        if ruta in _API_RUTAS_PUBLICAS:
            continue
        assert client.get(ruta).status_code in (401, 405), ruta
