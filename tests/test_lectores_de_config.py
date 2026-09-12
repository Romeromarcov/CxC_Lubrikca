"""Los lectores de configuración de descuentos, y el envoltorio del demonio.

Salieron del barrido por fracción sin cubrir: `get_config_descuentos_marca` (7 de 21),
`get_config_pronto_pago` (7 de 30), `get_config_dias_credito_volumen` (7 de 24) y
`_correr_auditoria_sobre_descuento_diaria` (7 de 15). Son lectores y un envoltorio,
sin concepto que extraer; lo que vale fijar es que cada uno devuelve lo que el repo
tiene, con el shape que el front espera, y que el envoltorio del demonio **nunca tumba
el ciclo**.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import cxc.web.app as app
from cxc.models import DescuentoMarcaCategoria, TipoDescuento


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


def _regla_marca(**kw) -> DescuentoMarcaCategoria:
    base = {
        "regla_id": "R1",
        "marca": "Sinoco",
        "categoria": "*",
        "porcentaje": Decimal("0.05"),
        "tipo_descuento": TipoDescuento.CONTADO,
        "vigencia_desde": date(2026, 1, 1),
        "vigencia_hasta": None,
        "listas_aplicables": "*",
        "activo": True,
    }
    base.update(kw)
    return DescuentoMarcaCategoria(**base)


# --- descuentos por marca ------------------------------------------------------


def test_descuentos_marca_devuelve_cada_regla_con_su_porcentaje_como_float() -> None:
    repo = MagicMock()
    repo.descuentos_marca_categoria.return_value = [
        _regla_marca(),
        _regla_marca(regla_id="R2", vigencia_hasta=date(2026, 12, 31), activo=False),
    ]
    pila, c = _cliente(repo)
    with pila:
        r = c.get("/api/config/descuentos-marca")
    assert r.status_code == 200
    filas = r.json()
    assert [f["regla_id"] for f in filas] == ["R1", "R2"]
    assert filas[0]["porcentaje"] == 0.05
    assert filas[0]["vigencia_hasta"] is None, "sin fin es null, no una fecha inventada"
    assert filas[1]["vigencia_hasta"] == "2026-12-31"
    assert filas[1]["activo"] is False


def test_descuentos_marca_sin_reglas_es_una_lista_vacia_y_no_un_error() -> None:
    repo = MagicMock()
    repo.descuentos_marca_categoria.return_value = []
    pila, c = _cliente(repo)
    with pila:
        r = c.get("/api/config/descuentos-marca")
    assert r.status_code == 200
    assert r.json() == []


def test_descuentos_marca_si_el_repo_falla_es_500_no_lista_vacia() -> None:
    """Una lista vacía se leería como «no hay reglas», que es lo contrario de la verdad."""
    repo = MagicMock()
    repo.descuentos_marca_categoria.side_effect = RuntimeError("base caida")
    pila, c = _cliente(repo)
    with pila:
        r = c.get("/api/config/descuentos-marca")
    assert r.status_code == 500


# --- pronto pago ---------------------------------------------------------------


def test_pronto_pago_expone_la_ventana_de_pago_y_el_tramo() -> None:
    repo = MagicMock()
    repo.descuentos_marca_categoria.return_value = [
        _regla_marca(ventana_pago_tipo="calendario", ventana_pago_dias=5)
    ]
    pila, c = _cliente(repo)
    with pila:
        r = c.get("/api/config/descuentos-pronto-pago")
    assert r.status_code == 200
    (f,) = r.json()
    assert f["ventana_pago_tipo"] == "calendario"
    assert f["ventana_pago_dias"] == 5
    assert f["min_unidades"] == 0.0
    assert f["max_unidades"] == 999999.0
    assert f["porcentaje"] == 0.05


# --- días de crédito por volumen -------------------------------------------------


def test_dias_credito_volumen_convierte_los_textos_del_repo() -> None:
    """El repo devuelve textos (la forma que tenía Sheets); el front espera números y
    `null` en el máximo abierto."""
    repo = MagicMock()
    repo.all_reglas_dias_credito_volumen.return_value = [
        {
            "regla_id": "DC1",
            "litros_minimo": "0",
            "litros_maximo": "500",
            "dias_credito_max": "15",
            "descripcion": "chicos",
            "activo": "true",
        },
        {
            "regla_id": "DC2",
            "litros_minimo": "500",
            "litros_maximo": "",
            "dias_credito_max": "45",
            "descripcion": "",
            "activo": "false",
        },
    ]
    pila, c = _cliente(repo)
    with pila:
        r = c.get("/api/config/dias-credito-volumen")
    assert r.status_code == 200
    a, b = r.json()
    assert a["litros_minimo"] == 0.0 and a["litros_maximo"] == 500.0
    assert b["litros_maximo"] is None, "máximo vacío = sin tope, no cero"
    assert b["litros_minimo"] == 500.0


def test_dias_credito_volumen_sin_reglas_es_lista_vacia() -> None:
    repo = MagicMock()
    repo.all_reglas_dias_credito_volumen.return_value = []
    pila, c = _cliente(repo)
    with pila:
        r = c.get("/api/config/dias-credito-volumen")
    assert r.status_code == 200
    assert r.json() == []


# --- el envoltorio del demonio ---------------------------------------------------


def test_el_demonio_guarda_los_sobre_descuentos_nuevos() -> None:
    repo = MagicMock()
    filas = [{"so_id": "S00010", "tipo_auditoria": "sobre_descuento"}]
    with patch("cxc.web.app._detectar_sobre_descuentos_batch", return_value=filas):
        app._correr_auditoria_sobre_descuento_diaria(repo)
    repo.append_auditoria_rows.assert_called_once_with(filas)


def test_el_demonio_no_escribe_nada_si_no_hay_sobre_descuentos() -> None:
    """Un `append` de lista vacía es una escritura sin sentido en cada ciclo."""
    repo = MagicMock()
    with patch("cxc.web.app._detectar_sobre_descuentos_batch", return_value=[]):
        app._correr_auditoria_sobre_descuento_diaria(repo)
    repo.append_auditoria_rows.assert_not_called()


def test_el_demonio_NUNCA_tumba_el_ciclo_aunque_el_detector_reviente() -> None:
    """Es la promesa del docstring: corre cada 5 minutos junto al sync, y una excepción
    acá dejaría el espejo sin actualizar por un problema de auditoría."""
    repo = MagicMock()
    with patch(
        "cxc.web.app._detectar_sobre_descuentos_batch", side_effect=RuntimeError("Odoo caido")
    ):
        app._correr_auditoria_sobre_descuento_diaria(repo)  # no lanza
    repo.append_auditoria_rows.assert_not_called()


def test_el_demonio_tampoco_tumba_el_ciclo_si_falla_el_guardado() -> None:
    repo = MagicMock()
    repo.append_auditoria_rows.side_effect = RuntimeError("base caida")
    with patch("cxc.web.app._detectar_sobre_descuentos_batch", return_value=[{"so_id": "S1"}]):
        app._correr_auditoria_sobre_descuento_diaria(repo)  # no lanza
