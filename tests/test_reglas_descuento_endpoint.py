"""GET /api/reglas-descuento (agosto 2026) -- smoke test agregado tras

encontrar y eliminar una segunda definición de esta misma ruta que era
código muerto (Starlette resuelve rutas duplicadas por orden de registro;
la primera, get_todas_reglas_descuento, siempre ganó). Sin este test, un
futuro cambio podría reintroducir el duplicado sin que nada lo detecte.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cxc.web.app import app

client = TestClient(app)


def test_devuelve_lista_plana_no_dict_por_categoria():
    """Confirma la forma de respuesta que el frontend realmente consume

    (loadReglasConsolidadas en app.js espera `data.length`/`data.forEach`,
    una lista -- no un dict agrupado por categoría, que era lo que
    devolvía la definición duplicada ya eliminada)."""
    mock_repo = MagicMock()
    mock_repo.descuentos_recompra.return_value = []
    mock_repo.descuentos_marca_categoria.return_value = []
    mock_repo.descuentos_volumen.return_value = []
    mock_repo.descuentos_producto.return_value = []
    mock_repo.descuentos_diferencial_cambiario.return_value = []
    mock_repo.promociones_primera_compra.return_value = []

    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        res = client.get("/api/reglas-descuento")

    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert data == []


def test_solo_una_ruta_registrada_para_este_path():
    """Verifica programáticamente que no se reintrodujo el duplicado --

    Starlette permitiría registrar dos rutas para el mismo (método, path)
    sin avisar, así que solo un chequeo explícito lo detecta."""
    matches = [
        r
        for r in app.routes
        if getattr(r, "path", None) == "/api/reglas-descuento"
        and "GET" in getattr(r, "methods", set())
    ]
    assert len(matches) == 1


def test_ninguna_ruta_de_la_app_esta_duplicada():
    """Guardia general (no solo /api/reglas-descuento): Starlette resuelve

    (método, path) duplicados por orden de registro sin avisar -- la
    segunda definición queda 100% inalcanzable y silenciosa, como ya pasó
    una vez con esta misma ruta y antes con /api/config/descuentos-volumen.
    Este test falla si alguien vuelve a introducir ese patrón en cualquier
    endpoint, no solo en el que ya se corrigió."""
    vistos: dict[tuple[str, str], int] = {}
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None)
        if path is None or not methods:
            continue
        for method in methods:
            key = (method, path)
            vistos[key] = vistos.get(key, 0) + 1
    duplicados = {k: v for k, v in vistos.items() if v > 1}
    assert duplicados == {}, f"Rutas duplicadas encontradas: {duplicados}"
