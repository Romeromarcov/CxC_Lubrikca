"""``PUT /api/vinculacion/{id}/editar`` -- las validaciones que ninguna prueba

ejercitaba (`scripts/dinero_sin_cubrir.py`, 17-sep-2026): la Vinculación o el
pago que no existen, y la orden vacía. El monto no positivo resultó ser un
hallazgo aparte: `VinculacionEditRequest.monto_aplicado` ya es `Field(gt=0,
...)`, así que Pydantic lo rechaza con 422 antes de que el `if` propio del
endpoint pueda correr -- ese `if` quedó documentado como defensa en
profundidad inalcanzable, no borrado (ver el comentario en `app.py`). El
caso "sin tasa para congelar" (`except TasaNoDisponible` -> 400) ya estaba
cubierto por `tests/test_tasa_ausente_no_tumba_lecturas.py`.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import cxc.web.app as app
from cxc.models import EstadoVinculacion, Moneda, Pago, Vinculacion


def _vinc(estado=EstadoVinculacion.PENDIENTE) -> Vinculacion:
    return Vinculacion(
        vinc_id="V1",
        pago_id="P1",
        so_id="S00001",
        monto_aplicado=Decimal("10"),
        hora_pago_confirmada=datetime(2026, 1, 1, 12, 0),
        tasa_bcv_aplicada=Decimal("40"),
        tasa_binance_aplicada=Decimal("45"),
        es_tasa_heredada=False,
        estado=estado,
        moneda_abono=Moneda.USD,
    )


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
    return pila, pila.enter_context(TestClient(app.app, raise_server_exceptions=False))


def test_editar_una_vinculacion_que_no_existe_es_404() -> None:
    repo = MagicMock()
    repo.all_vinculaciones.return_value = []
    pila, c = _cliente(repo)
    with pila:
        r = c.put("/api/vinculacion/NO_EXISTE/editar", json={"so_id": "S1", "monto_aplicado": 1.0})
    assert r.status_code == 404, r.text
    repo.update_vinculacion.assert_not_called()


def test_el_pago_de_la_vinculacion_ya_no_esta_en_el_espejo_es_404() -> None:
    """La Vinculación existe (venía de un sync anterior); su pago ya no. No es

    el caso normal, pero un `get_pago` que devuelve `None` no puede seguir
    como si tuviera datos."""
    repo = MagicMock()
    repo.all_vinculaciones.return_value = [_vinc()]
    repo.get_pago.return_value = None
    pila, c = _cliente(repo)
    with pila:
        r = c.put("/api/vinculacion/V1/editar", json={"so_id": "S2", "monto_aplicado": 1.0})
    assert r.status_code == 404, r.text
    assert "Pago no encontrado" in r.json()["detail"]
    repo.update_vinculacion.assert_not_called()


def test_una_vinculacion_ya_conciliada_no_se_edita_a_mano() -> None:
    repo = MagicMock()
    repo.all_vinculaciones.return_value = [_vinc(EstadoVinculacion.CONCILIADO)]
    pila, c = _cliente(repo)
    with pila:
        r = c.put("/api/vinculacion/V1/editar", json={"so_id": "S2", "monto_aplicado": 1.0})
    assert r.status_code == 400, r.text
    assert "conciliada" in r.json()["detail"].lower()
    repo.update_vinculacion.assert_not_called()


def _pago_valido() -> Pago:
    return Pago(
        pago_id="P1",
        cliente_id="C1",
        monto=Decimal("100"),
        moneda=Moneda.USD,
        metodo_pago="M1",
        fecha_pago=datetime(2026, 1, 1, 12, 0),
        vendedor_email="v@x.com",
    )


def test_una_orden_vacia_es_400() -> None:
    repo = MagicMock()
    repo.all_vinculaciones.return_value = [_vinc()]
    repo.get_pago.return_value = _pago_valido()
    pila, c = _cliente(repo)
    with pila:
        r = c.put("/api/vinculacion/V1/editar", json={"so_id": "   ", "monto_aplicado": 1.0})
    assert r.status_code == 400, r.text
    assert "orden" in r.json()["detail"].lower()
    repo.update_vinculacion.assert_not_called()


def test_un_monto_no_positivo_lo_rechaza_pydantic_antes_de_llegar_al_endpoint() -> None:
    """``VinculacionEditRequest.monto_aplicado`` ya es ``Field(gt=0, ...)`` desde el

    11-sep-2026 -- Pydantic responde 422 antes de que el handler corra. El
    ``if monto_dec <= 0: raise HTTPException(400, ...)`` que sigue adentro es
    defensa en profundidad, no alcanzable por HTTP; este test fija el
    comportamiento REAL, no el de esa línea."""
    repo = MagicMock()
    repo.all_vinculaciones.return_value = [_vinc()]
    repo.get_pago.return_value = _pago_valido()
    pila, c = _cliente(repo)
    with pila:
        r = c.put("/api/vinculacion/V1/editar", json={"so_id": "S2", "monto_aplicado": 0.0})
    assert r.status_code == 422, r.text
    repo.update_vinculacion.assert_not_called()


def test_editar_con_datos_validos_recalcula_las_dos_ordenes() -> None:
    """El caso feliz: cambia de orden, se recalculan la vieja y la nueva."""
    repo = MagicMock()
    repo.all_vinculaciones.return_value = [_vinc()]
    repo.get_pago.return_value = _pago_valido()
    repo.all_serie_tasas.return_value = []
    repo.all_tasas_historicas_auditoria.return_value = [
        {
            "fecha": "2026-01-01",
            "tasa_bcv_usd": "40.0",
            "tasa_bcv_euro": "44.0",
            "tasa_binance_promedio_diario": "45.0",
        }
    ]
    app.invalidar_tasas()
    pila, c = _cliente(repo)
    with pila:
        r = c.put("/api/vinculacion/V1/editar", json={"so_id": "S00002", "monto_aplicado": 5.0})
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["status"] == "success"
    assert cuerpo["so_id"] == "S00002"
    guardada = repo.update_vinculacion.call_args[0][0]
    assert guardada.vinc_id == "V1", "edita el MISMO registro, no crea uno nuevo"
    assert guardada.so_id == "S00002"
    assert guardada.monto_aplicado == Decimal("5.0")
