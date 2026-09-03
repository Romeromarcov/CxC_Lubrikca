"""Fixtures compartidas de la suite."""

import os
from unittest.mock import patch

import pytest

from cxc.models import set_marca_fallback

# Variables de entorno ficticias para que la suite sea HERMÉTICA.
#
# ``AppConfig.from_env()`` exige ODOO_URL/DB/USERNAME/PASSWORD y revienta
# con KeyError si falta alguna. En una máquina de desarrollo eso no se nota
# porque hay un ``.env`` que python-dotenv carga solo; en CI no hay ``.env``
# y 16 tests fallaban con "Falta la variable de entorno requerida:
# ODOO_URL" -- pasaban en local y fallaban en el pipeline, que es parte de
# por qué el CI llevaba tanto tiempo en rojo sin que nadie lo mirara
# (auditoría de agosto 2026).
#
# El dominio es ``.test``, un TLD reservado que no resuelve: si algún test
# intentara conectarse de verdad, falla en vez de tocar un Odoo real.
# Si agregás una variable obligatoria nueva en config.py, sumala acá: hay
# un test que compara ambas listas y falla si se desincronizan
# (tests/test_entorno_hermetico.py).
_ENV_DE_PRUEBA = {
    "ODOO_URL": "https://odoo.invalido.test",
    "ODOO_DB": "cxc_test",
    "ODOO_USERNAME": "tests@cxc.test",
    "ODOO_PASSWORD": "no-es-una-credencial",
    "BINANCE_P2P_URL": "https://binance.invalido.test/p2p",
}


@pytest.fixture(autouse=True, scope="session")
def _entorno_hermetico():
    """Aísla la suite del ``.env`` de la máquina.

    Además de rellenar las credenciales, se anula la carga del ``.env``:
    ese archivo trae muchas otras variables (listas de precios, ventana de
    contado, URLs de scraper) que cambian rutas de código, así que la suite
    daba resultados y cobertura DISTINTOS en una máquina de desarrollo y en
    CI. Anulándolo, lo que corre en local es exactamente lo que corre en el
    pipeline.
    """
    faltantes = {k: v for k, v in _ENV_DE_PRUEBA.items() if not os.environ.get(k)}
    with (
        patch("cxc.config._maybe_load_dotenv", lambda: None),
        patch.dict(os.environ, faltantes),
    ):
        yield


@pytest.fixture(autouse=True)
def _sesion_valida_por_defecto(request):
    """Da por autenticada toda petición a ``/api/`` en los tests.

    Desde la auditoría de agosto 2026 el backend exige sesión en toda ruta
    ``/api/`` (ver ``exigir_sesion_en_api`` en web/app.py). Los ~130 tests
    de endpoints ya existentes verifican lógica de negocio, no
    autenticación, y montar un usuario real en cada uno (con su repo
    mockeado propio) solo agregaría ruido. Se sustituye el único punto que
    consulta el middleware.

    ``tests/test_auth_api_cerrada.py`` -- que verifica justamente el cierre
    de la API -- queda excluido para que ejerza el middleware de verdad.
    """
    if request.node.fspath.basename == "test_auth_api_cerrada.py":
        yield
        return
    with patch("cxc.web.app.hay_sesion_valida", return_value=True):
        yield


@pytest.fixture(autouse=True)
def _reset_marca_fallback():
    """Evita fugas de estado global entre tests -- ``set_marca_fallback``

    muta un default a nivel de módulo (ver models.py); si un test corre con
    un repo mockeado cuyo ``get_config`` no está configurado para
    "marca_fallback", puede devolver un MagicMock y contaminar los tests
    que corren después.
    """
    set_marca_fallback("GLOBAL OIL")
    yield
    set_marca_fallback("GLOBAL OIL")


@pytest.fixture(autouse=True)
def _reset_ventas_cache():
    """Evita fugas de estado entre tests -- ``_VENTAS_CACHE`` (agosto 2026,

    caché corta de ``/api/ventas`` para la consolidación de fuentes) es un
    dict a nivel de módulo en ``cxc.web.app``; sin este reset, un test que
    corre primero con ``vendedor=None`` deja su resultado cacheado y el
    siguiente test (con su propio repo mockeado y datos distintos) recibe
    esa copia vieja en vez de recalcular -- mismo patrón de fuga que
    ``_reset_marca_fallback`` ya evita para otro global.
    """
    from cxc.web import app as _app_module

    _app_module._VENTAS_CACHE["data"] = None
    _app_module._VENTAS_CACHE["timestamp"] = 0.0
    _app_module._ventas_computing = False
    yield
    _app_module._VENTAS_CACHE["data"] = None
    _app_module._VENTAS_CACHE["timestamp"] = 0.0
    _app_module._ventas_computing = False


@pytest.fixture(autouse=True)
def _reset_pricelist_items_cache():
    """Mismo patrón que ``_reset_ventas_cache`` para

    ``_PRICELIST_ITEMS_CACHE`` (agosto 2026, caché compartida de
    ``product.pricelist.item`` entre Reporte de Saldos y Auditoría).
    """
    from cxc.web import app as _app_module

    _app_module._PRICELIST_ITEMS_CACHE.clear()
    yield
    _app_module._PRICELIST_ITEMS_CACHE.clear()


@pytest.fixture(autouse=True)
def _reset_so_state_cache():
    """Mismo patrón para ``_SO_STATE_CACHE`` (agosto 2026, caché por-orden

    de estado en vivo entre Dashboard/Auditoría/Reporte Diario).
    """
    from cxc.web import app as _app_module

    _app_module._SO_STATE_CACHE.clear()
    yield
    _app_module._SO_STATE_CACHE.clear()


@pytest.fixture(autouse=True)
def _reset_entrega_cache():
    """Mismo patrón para ``_ENTREGA_CACHE`` (agosto 2026, caché por-orden

    de get_live_entregas_info).
    """
    from cxc.web import app as _app_module

    _app_module._ENTREGA_CACHE.clear()
    yield
    _app_module._ENTREGA_CACHE.clear()


@pytest.fixture(autouse=True)
def _reset_vendedor_por_partner_cache():
    """Mismo patrón para ``_VENDEDOR_POR_PARTNER_CACHE`` (agosto 2026,

    caché por-partner de resolve_vendedores_por_partner).
    """
    from cxc.web import app as _app_module

    _app_module._VENDEDOR_POR_PARTNER_CACHE.clear()
    yield
    _app_module._VENDEDOR_POR_PARTNER_CACHE.clear()
