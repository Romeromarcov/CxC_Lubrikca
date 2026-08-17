"""Fixtures compartidas de la suite."""

import pytest

from cxc.models import set_marca_fallback


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
