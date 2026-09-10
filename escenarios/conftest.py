"""Fixtures del banco de escenarios (Fase 3 del plan de blindaje).

Este suite NO es parte de la suite normal: escribe en un Odoo de prueba por
la red y tarda minutos. Vive fuera de ``testpaths`` a propósito y se corre
aparte:

    ./scripts/escenarios.sh
    ./scripts/escenarios.sh escenarios/test_ordenes.py -k cancelan

Tres barreras, y ninguna es opcional:

1. ``ODOO_URL`` tiene que llevar ``.dev.odoo.com``. Sin eso no arranca.
2. ``DATABASE_URL`` tiene que ser local y NO puede ser la base de CI
   (``cxc_ci``): el espejo de escenarios es otro.
3. El **canario fiscal** se lee antes y después de cada escenario. Si crece,
   el escenario emitió un documento fiscal real contra el proveedor de la
   empresa y la corrida falla ahí mismo. Ver ``odoo_qa`` para el por qué.

Y un timeout de red, que no es una barrera de seguridad pero sin él el banco
no se puede correr -- ver ``TIMEOUT_ODOO_SEGUNDOS``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ))

HOSTS_LOCALES = {"localhost", "127.0.0.1", "::1", "postgres"}
BASE_PROHIBIDA = "cxc_ci"


def _cargar_env_qa() -> None:
    ruta = RAIZ / ".env.qa"
    if not ruta.exists():
        pytest.exit(
            "Falta .env.qa. Copiá .env.qa.example y completá las credenciales del "
            "Odoo de prueba. El banco de escenarios no corre sin eso.",
            returncode=2,
        )
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, _, valor = linea.partition("=")
            os.environ[clave.strip()] = valor.strip()


def _verificar_barreras() -> None:
    from urllib.parse import urlparse

    url_odoo = os.environ.get("ODOO_URL", "")
    if ".dev.odoo.com" not in url_odoo:
        pytest.exit(
            f"ODOO_URL es {url_odoo!r}. El banco de escenarios ESCRIBE en Odoo y "
            "solo corre contra un entorno de prueba (.dev.odoo.com).",
            returncode=2,
        )
    url_db = os.environ.get("DATABASE_URL", "")
    partes = urlparse(url_db)
    if (partes.hostname or "") not in HOSTS_LOCALES:
        pytest.exit(
            f"DATABASE_URL apunta a {partes.hostname!r}, que no es local.", returncode=2
        )
    if (partes.path or "").lstrip("/") == BASE_PROHIBIDA:
        pytest.exit(
            "DATABASE_URL apunta a la base de CI. El espejo de escenarios es otro "
            "(cxc_qa): los escenarios la modifican y romperían la suite normal.",
            returncode=2,
        )


# Ninguna llamada a Odoo puede colgarse para siempre. ``xmlrpc.client`` no
# expone un timeout, asi que se pone en el socket: sin esto, una corrida se
# quedo 37 minutos parada despues de que un escenario fallara -- pytest ya
# habia escrito la "F" y la limpieza del canario esperaba una respuesta que
# nunca llego. Un banco que se puede colgar no se corre.
#
# 300 s es holgado a proposito: la consulta de ``sale.report`` sobre 950
# ordenes es la mas lenta del banco y tarda bastante menos que eso.
TIMEOUT_ODOO_SEGUNDOS = 300.0


def pytest_configure(config: pytest.Config) -> None:
    import socket

    socket.setdefaulttimeout(TIMEOUT_ODOO_SEGUNDOS)
    _cargar_env_qa()
    _verificar_barreras()
    config.addinivalue_line(
        "markers", "escenario(fila): la fila de la tabla de la Fase 3 que cubre"
    )


# --- Odoo de prueba ---------------------------------------------------------


@pytest.fixture(scope="session")
def odoo():
    from escenarios.odoo_qa import conectar_qa

    qa = conectar_qa()
    # Se toca el diario de pruebas una vez al arrancar: crea el diario si no
    # existe y, sobre todo, verifica que no tenga imprenta digital conectada.
    # Mejor fallar acá que a mitad del primer escenario.
    _ = qa.diario_pruebas
    return qa


@pytest.fixture(autouse=True)
def canario_fiscal(odoo):
    """Ningún escenario puede emitir un documento fiscal.

    El Odoo de prueba comparte el proveedor de imprenta digital con
    producción. Si un escenario logra emitir, el problema no es el escenario:
    es que una factura se fue por el diario equivocado, y hay que enterarse en
    el acto.
    """
    antes = odoo.control_fiscal_emitidos()
    yield
    try:
        despues = odoo.control_fiscal_emitidos()
    except Exception as exc:  # noqa: BLE001 -- se reporta, no se traga
        pytest.fail(
            "No se pudo verificar el canario fiscal al terminar el escenario "
            f"({str(exc)[:200]}). Sin esa lectura no se puede afirmar que no se "
            "emitio nada, y afirmarlo sin mirar seria justamente lo que este "
            "blindaje persigue."
        )
    if despues != antes:
        from escenarios.odoo_qa import EmisionFiscalDetectada

        raise EmisionFiscalDetectada(
            f"Se emitieron {despues - antes} documento(s) fiscal(es) durante este "
            "escenario. Alguna factura salió por un diario con imprenta digital. "
            "Revisar antes de seguir."
        )


# --- el sistema ------------------------------------------------------------


@pytest.fixture(scope="session")
def cliente_web():
    """``TestClient`` sobre la app real, con la sesión dada por válida.

    Se parchea ``hay_sesion_valida`` por el mismo motivo que la suite normal:
    los escenarios verifican lógica de negocio, no autenticación, y montar un
    usuario en cada uno solo agregaría ruido.
    """
    from fastapi.testclient import TestClient

    from cxc.web.app import app

    with patch("cxc.web.app.hay_sesion_valida", return_value=True), TestClient(app) as cliente:
        yield cliente


@pytest.fixture
def sistema(cliente_web):
    from escenarios.sistema import Sistema, limpiar_caches

    limpiar_caches()
    return Sistema(cliente=cliente_web)


# --- material de trabajo ---------------------------------------------------


@pytest.fixture(scope="session")
def listas(odoo):
    """Los ids de las listas VES y USD vigentes de este Odoo.

    No se toman de ``ENGINE_LISTA_*``: en esta base esas apuntan a las listas
    4 y 5, que están ARCHIVADAS (y que 635 de las 950 órdenes usan
    históricamente). Las vigentes son otras, y el mapeo unificado es quien
    sabe cuáles -- se le pregunta a él, igual que hace el motor.
    """
    from cxc.web.app import get_pricelist_mapeo

    mapeo = get_pricelist_mapeo(None)
    vigentes = {
        int(pid): m for pid, m in mapeo.items() if m.get("vigente") and str(pid).isdigit()
    }
    ves = sorted(p for p, m in vigentes.items() if m.get("moneda") == "ves")
    usd = sorted(p for p, m in vigentes.items() if m.get("moneda") == "usd")
    assert ves and usd, f"El mapeo no tiene listas vigentes de las dos monedas: {mapeo}"
    return {"ves": ves[0], "usd": usd[0], "todas_ves": ves, "todas_usd": usd}


@pytest.fixture
def escenario(odoo, listas, request):
    """Constructor de la situación inicial de cada escenario.

    Cada escenario arma su propia orden en vez de tocar una copiada de
    producción. Es más lento, y es lo que hace el banco REEJECUTABLE: correrlo
    dos veces da lo mismo, que es justo la propiedad que la Fase 4 le exige al
    sync.
    """
    from escenarios.fabrica import Fabrica

    return Fabrica(odoo=odoo, listas=listas, etiqueta=request.node.name)
