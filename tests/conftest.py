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
