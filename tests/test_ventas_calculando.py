"""``/api/ventas`` distingue "calculando" de "no hay ventas".

Bug real (auditoría de producción, agosto 2026): mientras un cálculo
estaba en vuelo, las demás peticiones recibían ``{"items": []}`` a secas
si todavía no había caché -- exactamente lo que pasa tras cada despliegue
y tras cada ciclo de sync que detecta cambios (el daemon invalida el
caché). Medido contra producción, ese cálculo tarda ~624 s con 783
órdenes, así que la página de Ventas se veía VACÍA durante ~10 minutos,
indistinguible de haber perdido los datos.
"""

from __future__ import annotations

import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from cxc.web import app as app_module
from cxc.web.app import app

client = TestClient(app)


def _reset_cache(data=None, timestamp=0.0):
    app_module._VENTAS_CACHE["data"] = data
    app_module._VENTAS_CACHE["timestamp"] = timestamp


def test_calculando_sin_cache_previo_no_parece_vacio() -> None:
    """Sin datos previos hay que decir que se está calculando, no devolver
    un listado vacío pelado."""
    _reset_cache(None)
    with patch.object(app_module, "_ventas_computing", True):
        res = client.get("/api/ventas")
    assert res.status_code == 200
    data = res.json()
    assert data["items"] == []
    assert data["calculando"] is True


def test_calculando_con_cache_previo_devuelve_lo_ultimo_bueno() -> None:
    """Si hay un resultado anterior se sirve ese, marcado como en curso."""
    previo = {"items": [{"so_id": "SO1"}], "kpis": {"venta_neta_real_total": 10.0}}
    _reset_cache(previo, timestamp=0.0)  # timestamp viejo -> caché expirado
    with patch.object(app_module, "_ventas_computing", True):
        res = client.get("/api/ventas")
    data = res.json()
    assert data["items"] == [{"so_id": "SO1"}]
    assert data["calculando"] is True
    # No se contamina el caché con la marca de estado.
    assert "calculando" not in app_module._VENTAS_CACHE["data"]


def test_cache_fresco_no_se_marca_como_calculando() -> None:
    previo = {"items": [{"so_id": "SO1"}], "kpis": {}}
    _reset_cache(previo, timestamp=time.time())
    res = client.get("/api/ventas")
    data = res.json()
    assert data["items"] == [{"so_id": "SO1"}]
    assert not data.get("calculando")
    _reset_cache(None)
