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


def test_una_fila_por_categoria_construye_el_dict_completo():
    """El otro test deja las seis tablas vacías -- lo correcto para fijar la

    FORMA de la respuesta, pero deja sin ejecutar el cuerpo de cada `for`: las
    97 líneas que arman cada fila. Ahí vivía el bug real del 11-sep-2026
    (`float(r.litros_minimo)`, un `AttributeError` que este mismo `except
    Exception` convertía en 500 y dejaba en blanco la pantalla ENTERA por una
    sola regla de volumen con la unidad vacía -- ver `engine/discounts.
    unidad_de_volumen`). Una fila por tabla, para que las seis vuelvan a
    ejecutarse."""
    from datetime import date

    from cxc.models import DescuentoDiferencialCambiario
    from tests import builders as b

    mock_repo = MagicMock()
    mock_repo.descuentos_recompra.return_value = [b.descuento_recompra("REC1")]
    mock_repo.descuentos_marca_categoria.return_value = [b.descuento("PP1")]
    mock_repo.descuentos_volumen.return_value = [b.descuento_volumen("DV1")]
    mock_repo.promociones_primera_compra.return_value = [b.promo_primera()]
    mock_repo.descuentos_producto.return_value = [b.descuento_producto("PROD1")]
    mock_repo.descuentos_diferencial_cambiario.return_value = [
        DescuentoDiferencialCambiario(regla_id="DIF1", vigencia_desde=date(2026, 1, 1))
    ]

    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        res = client.get("/api/reglas-descuento")

    assert res.status_code == 200
    filas = {f["tabla"]: f for f in res.json()}
    assert set(filas) == {
        "DescuentosRecompra",
        "DescuentosProntoPago",
        "DescuentosVolumen",
        "PromocionPrimeraCompra",
        "DescuentosProducto",
        "DescuentosDiferencialCambiario",
    }
    # Las tres columnas que las seis ramas repiten literalmente -- si una se
    # desalinea (un `getattr` con el nombre viejo, por ejemplo), esto lo ve.
    for tabla, fila in filas.items():
        assert fila["activo"] is True, tabla
        assert fila["aplica_a"] == "linea", tabla
        assert isinstance(fila["descripcion"], str), tabla

    # El caso puntual del 11-sep: la unidad se INFIERE (LITROS, por el builder)
    # y la fila lo declara.
    vol = filas["DescuentosVolumen"]
    assert vol["unidad_medida"] == "LITROS"
    assert vol["unidad_declarada"] is True
    assert vol["min_unidades"] == 100.0

    dif = filas["DescuentosDiferencialCambiario"]
    assert dif["porcentaje"] == 0.35
    assert dif["campos_especiales"]["tipo_diferencial"] == "fijo_35_ves_usd"
