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

    **Se imponen TODAS, no solo las que faltan.** Hasta el 11-sep-2026 esto
    rellenaba únicamente las variables ausentes del entorno, así que un valor
    real ya presente se colaba y el aislamiento no existía para él. Se descubrió
    con ``.env.qa`` cargado en la shell: ``test_la_config_se_construye_sin_dotenv``
    --de ``test_entorno_hermetico.py``, o sea el test que existe para esto-- falló
    porque ``config.odoo.url`` traía el Odoo de QA. El docstring prometía que lo
    local es igual a lo del pipeline y el código solo lo cumplía cuando el entorno
    venía vacío.

    ``DATABASE_URL`` queda deliberadamente afuera de ``_ENV_DE_PRUEBA``: la suite
    necesita una base real y el ``conftest`` no debe inventarla.

    **Y el mapeo de listas tampoco se lee del disco.** ``get_pricelist_mapeo`` tiene un
    caché en ``secrets/pricelist_mapeo.json``, y hasta el 11-sep-2026 la suite lo leía:
    un test que llegara a las listas veía la configuración de la máquina del
    desarrollador (16 listas en la mía) en vez del default de prueba. Se notó al hacer
    que cuatro endpoints más leyeran del mapeo: cuatro tests que asumían las listas 4
    y 5 pasaron a ver la 7, la 8 y la 11. Anulado, ``get_valid_pricelists_usd_and_ves``
    cae al env de prueba, que es lo que esos tests siempre supusieron.
    """
    with (
        patch("cxc.config._maybe_load_dotenv", lambda: None),
        patch.dict(os.environ, _ENV_DE_PRUEBA),
        patch("cxc.web.app._load_pricelist_mapeo_from_json", return_value=None),
        patch("cxc.web.app._save_pricelist_mapeo_to_json"),
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


@pytest.fixture(autouse=True)
def _cache_de_tasas_limpio():
    """El caché de TasasHistoricasAuditoria no debe cruzarse entre tests.

    ``get_rate_for_datetime`` cachea esa tabla por proceso (ver
    ``_tasas_historicas_cacheadas``) para no consultarla una vez por pago
    -- ese N+1 hacía que la página de Auditoría tardara ~18 minutos. Pero
    en la suite cada test monta su propio repo falso, y sin limpiar el
    caché un test heredaba las tasas del anterior: dos pasaban aislados y
    fallaban en conjunto.
    """
    from cxc.web import app as _app

    # ``invalidar_tasas`` limpia los DOS cachés de tasas: el del histórico
    # y el del objeto ``Tasas`` ya indexado (septiembre 2026). Cuando se
    # agregó el segundo sin limpiarlo acá, dos tests e2e volvieron a
    # fallar solo en conjunto -- exactamente el mismo síntoma que este
    # fixture existía para evitar.
    _app.invalidar_tasas()
    yield
    _app.invalidar_tasas()
