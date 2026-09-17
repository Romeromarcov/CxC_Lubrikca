"""Un `raise HTTPException(404)` adentro de un `try` que atrapa `Exception` sale como 500.

`HTTPException` **es** una `Exception`, así que este patrón

    try:
        ...
        raise HTTPException(status_code=404, detail="no encontrada")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

convierte todo 404 y todo 400 en «error del servidor». El cliente ve un 500 con el
texto del 404 adentro, y quien mire los logs ve un traceback por una regla que no
existe. Se arregló tres veces en esta sesión —`post_cambiar_tipo_tasa_bcv`,
`post_eliminar_descuento`, `post_vincular`— y la tercera era un 400 que **yo** había
agregado ese mismo día y probado mirando el texto del archivo en vez de la respuesta.

La guarda de abajo recorre todos los `try` de `app.py` con AST: si un `try` contiene un
`raise HTTPException(...)` y atrapa `Exception` sin atrapar `HTTPException` antes,
falla y nombra la función.
"""

from __future__ import annotations

import ast
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import cxc.web.app as app

APP = Path("src/cxc/web/app.py")


def _handlers(tr: ast.Try) -> list[str]:
    tipos: list[str] = []
    for h in tr.handlers:
        if h.type is None:
            tipos.append("bare")
        elif isinstance(h.type, ast.Name):
            tipos.append(h.type.id)
        elif isinstance(h.type, ast.Tuple):
            tipos.extend(getattr(e, "id", "?") for e in h.type.elts)
    return tipos


def _lanza_http(nodos: list[ast.stmt]) -> bool:
    for n in ast.walk(ast.Module(body=nodos, type_ignores=[])):
        if (
            isinstance(n, ast.Raise)
            and isinstance(n.exc, ast.Call)
            and getattr(n.exc.func, "id", "") == "HTTPException"
        ):
            return True
    return False


def test_ningun_try_de_app_se_traga_su_propio_HTTPException() -> None:
    arbol = ast.parse(APP.read_text(encoding="utf-8"))
    culpables = []
    for fn in ast.walk(arbol):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for tr in ast.walk(fn):
            if not isinstance(tr, ast.Try) or not _lanza_http(tr.body):
                continue
            tipos = _handlers(tr)
            atrapa_todo = "Exception" in tipos or "bare" in tipos
            if atrapa_todo and "HTTPException" not in tipos:
                culpables.append(f"{fn.name} (try en la línea {tr.lineno})")
    assert culpables == [], (
        "Estos `try` lanzan un HTTPException y lo atrapan con `except Exception`, así que "
        f"sale como 500. Falta un `except HTTPException: raise` antes: {culpables}"
    )


# --- los tres arreglados, por HTTP -------------------------------------------


def _cliente(repo):
    async def _nada():
        return None

    pila = ExitStack()
    for parche in (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app.hay_sesion_valida", return_value=True),
        patch("cxc.web.app.run_scraper_in_background", _nada),
        patch("cxc.web.app.run_sync_in_background", _nada),
        patch("cxc.web.app._aplicar_migraciones_pendientes"),
    ):
        pila.enter_context(parche)
    cliente = pila.enter_context(TestClient(app.app, raise_server_exceptions=False))
    return pila, cliente


def test_eliminar_una_regla_que_no_existe_es_404_y_no_500() -> None:
    repo = MagicMock()
    repo.delete_regla.return_value = False
    pila, c = _cliente(repo)
    with pila:
        r = c.post(
            "/api/config/eliminar-descuento",
            json={"tabla": "DescuentosVolumen", "regla_id": "NO_EXISTE"},
        )
    assert r.status_code == 404, r.text
    assert "no encontrada" in r.json()["detail"].lower()


def test_eliminar_una_regla_que_existe_la_borra_y_dice_success() -> None:
    repo = MagicMock()
    repo.delete_regla.return_value = True
    pila, c = _cliente(repo)
    with pila:
        r = c.post(
            "/api/config/eliminar-descuento", json={"tabla": "DescuentosVolumen", "regla_id": "R1"}
        )
    assert r.status_code == 200
    repo.delete_regla.assert_called_once_with("DescuentosVolumen", "R1")


def test_vincular_un_pago_que_no_existe_es_404_y_no_500() -> None:
    repo = MagicMock()
    repo.get_pago.return_value = None
    pila, c = _cliente(repo)
    with pila:
        r = c.post(
            "/api/vincular", json={"pago_id": "NO", "so_id": "S00010", "monto_aplicado": 10.0}
        )
    assert r.status_code == 404, r.text


def test_vincular_sin_tasa_valida_es_400_y_no_500() -> None:
    """El 400 que agregué hoy, ahora probado por la respuesta y no por el texto."""
    from datetime import date
    from decimal import Decimal
    from types import SimpleNamespace

    repo = MagicMock()
    repo.get_pago.return_value = SimpleNamespace(
        pago_id="P1", fecha_pago=date(2026, 9, 11), moneda="VES", cliente_id="C1"
    )
    repo.get_orden.return_value = SimpleNamespace(so_id="S00010", cliente_id="C1")
    pila, c = _cliente(repo)
    with (
        pila,
        patch("cxc.web.app.get_rate_for_datetime", return_value=(Decimal("0"), Decimal("961"))),
        patch("cxc.web.app.resolver_tasa_bcv_vinculacion", return_value=(Decimal("0"), "USD")),
    ):
        r = c.post(
            "/api/vincular", json={"pago_id": "P1", "so_id": "S00010", "monto_aplicado": 10.0}
        )
    assert r.status_code == 400, r.text
    assert "positiva" in r.json()["detail"].lower()


@pytest.mark.parametrize(
    "fecha,hora", [("2026-13-01", "10:00"), ("ayer", "10:00"), ("2026-09-11", "25:99")]
)
def test_tasa_referencia_con_fecha_ilegible_es_400(fecha, hora) -> None:
    pila, c = _cliente(MagicMock())
    with pila:
        r = c.get(f"/api/config/tasa-referencia?fecha={fecha}&hora={hora}")
    assert r.status_code == 400


def test_tasa_referencia_sin_tasa_para_esa_fecha_es_400_y_no_un_numero_inventado() -> None:
    """Antes de la Fase 2.1 esto devolvía 36,5 / 38,0 --las tasas de 2019-- para
    cualquier fecha sin dato. Ahora es un error, y el endpoint lo dice."""
    from cxc.rates import TasaNoDisponible

    pila, c = _cliente(MagicMock())
    with pila, patch("cxc.web.app.get_rate_for_datetime", side_effect=TasaNoDisponible("sin tasa")):
        r = c.get("/api/config/tasa-referencia?fecha=2019-01-01&hora=10:00")
    assert r.status_code == 400
    assert "sin tasa" in r.json()["detail"]


def test_tasa_referencia_devuelve_las_dos_tasas_del_momento() -> None:
    from decimal import Decimal

    pila, c = _cliente(MagicMock())
    with (
        pila,
        patch(
            "cxc.web.app.get_rate_for_datetime", return_value=(Decimal("827.74"), Decimal("961.67"))
        ),
    ):
        r = c.get("/api/config/tasa-referencia?fecha=2026-09-11&hora=10:00")
    assert r.status_code == 200
    assert r.json() == {"tasa_bcv": 827.74, "tasa_binance": 961.67}


def test_tasas_historicas_lista_lo_que_hay_con_su_conteo() -> None:
    repo = MagicMock()
    repo.all_tasas_historicas_auditoria.return_value = [
        {"fecha": "2026-09-10"},
        {"fecha": "2026-09-11"},
    ]
    pila, c = _cliente(repo)
    with pila:
        r = c.get("/api/tasas-historicas")
    assert r.status_code == 200
    assert r.json()["count"] == 2
    assert len(r.json()["items"]) == 2
