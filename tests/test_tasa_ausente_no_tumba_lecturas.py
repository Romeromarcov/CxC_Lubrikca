"""La ausencia de tasa para una fecha es un error duro SOLO donde se congela un equivalente.

Desde la Fase 2.1 (11-sep-2026) ``get_rate_for_datetime`` levanta ``TasaNoDisponible``
en vez de inventar 36,5/38,0. La decisión es correcta y el banco de escenarios mostró
al día siguiente la otra mitad que faltaba: quince llamadores, y en ocho de ellos un
solo pago con fecha sin tasa tumbaba algo mucho más grande que ese pago -- la bandeja
de auditoría entera (500), el balance (500), el historial de pagos (500, y con él la
página de cobranza), el bloque de Odoo de Ventas (warning y todo vacío), el sync de
aplicaciones y el resync (ciclo entero), el lote masivo y el Auto-FIFO.

La regla que queda: **donde se lee, la tasa ausente se degrada a «no se pudo mirar»,
visible en la respuesta; donde se escribe, es un 400 que nombra la fecha.** Este
archivo fija los dos que faltaban: el resumen del dashboard y los dos endpoints de
escritura (vincular / editar vinculación), que devolvían 500 en vez de 400.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import cxc.web.app as app
from cxc.models import EstadoVinculacion, Moneda, OrdenVenta, Vinculacion


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
        patch("cxc.web.app._connect", return_value=None),
        patch("cxc.web.app.AppConfig.from_env"),
    ):
        pila.enter_context(parche)
    app.invalidar_tasas()
    return pila, pila.enter_context(TestClient(app.app, raise_server_exceptions=False))


# --- el resumen del dashboard ---------------------------------------------------


def test_el_resumen_suma_los_pagos_con_tasa_y_nombra_los_que_no_pudo() -> None:
    """Antes: un pago sin fecha se convertía con la tasa de HOY, y cualquier
    excepción caía en un ``except: pass``. La tarjeta sumaba de menos sin decirlo."""
    repo = MagicMock()
    repo.all_ordenes.return_value = []
    repo.all_vinculaciones.return_value = []
    repo.all_conciliaciones.return_value = []
    repo.all_serie_tasas.return_value = []
    repo.all_tasas_historicas_auditoria.return_value = [
        {
            "fecha": "2026-07-02",
            "tasa_bcv_usd": "100.0",
            "tasa_bcv_euro": "110.0",
            "tasa_binance_promedio_diario": "120.0",
        }
    ]
    filas = [
        {"pago_id": "CON", "monto": "1000", "moneda": "VES", "fecha_pago": "2026-07-02"},
        {"pago_id": "SIN", "monto": "1000", "moneda": "VES", "fecha_pago": "2030-01-01"},
        {"pago_id": "SINFECHA", "monto": "1000", "moneda": "VES", "fecha_pago": ""},
        {"pago_id": "USD", "monto": "7", "moneda": "USD", "fecha_pago": "2030-01-01"},
    ]
    pila, c = _cliente(repo)
    with pila, patch("cxc.web.app._all_pagos_rows", return_value=filas):
        r = c.get("/api/resumen")
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    # 1000 Bs a 100 = 10 USD, más los 7 USD que no necesitan tasa para sumar en USD.
    assert abs(cuerpo["pagos_sin_asignar_usd"] - 17.0) < 0.01
    # En Bs: solo el que tiene tasa (10 USD × 100); el de dólares sin tasa no se
    # puede pasar a Bs, y por eso también figura en la lista.
    assert abs(cuerpo["pagos_sin_asignar_ves"] - 1000.0) < 0.01
    assert sorted(cuerpo["pagos_sin_tasa_para_su_fecha"]) == ["SIN", "SINFECHA", "USD"]


# --- los dos endpoints de escritura: 400, no 500 ----------------------------------


def _pago_ves_de_2030():
    from types import SimpleNamespace

    return SimpleNamespace(
        pago_id="P1", fecha_pago=date(2030, 1, 1), moneda="VES", cliente_id="C1", monto=Decimal("1")
    )


def test_vincular_sin_tasa_para_la_fecha_del_pago_es_400_que_nombra_la_fecha() -> None:
    repo = MagicMock()
    repo.get_pago.return_value = _pago_ves_de_2030()
    repo.get_orden.return_value = OrdenVenta(
        so_id="S1",
        cliente_id="C1",
        fecha=date(2026, 1, 1),
        fecha_entrega=None,
        monto_total=Decimal("100"),
        lista_precios="4",
        vendedor_email="v@x.com",
        es_primera_compra=False,
    )
    repo.all_serie_tasas.return_value = []
    repo.all_tasas_historicas_auditoria.return_value = []
    pila, c = _cliente(repo)
    with pila:
        r = c.post("/api/vincular", json={"pago_id": "P1", "so_id": "S1", "monto_aplicado": 1.0})
    assert r.status_code == 400, r.text
    assert "2030-01-01" in r.json()["detail"]
    repo.add_vinculacion.assert_not_called()
    repo.update_vinculacion.assert_not_called()


def test_editar_vinculacion_sin_tasa_para_la_fecha_es_400() -> None:
    v = Vinculacion(
        vinc_id="V1",
        pago_id="P1",
        so_id="S1",
        monto_aplicado=Decimal("1"),
        hora_pago_confirmada=datetime(2030, 1, 1),
        tasa_bcv_aplicada=Decimal("1"),
        tasa_binance_aplicada=Decimal("1"),
        es_tasa_heredada=False,
        estado=EstadoVinculacion.PENDIENTE,
        moneda_abono=Moneda.VES,
    )
    repo = MagicMock()
    repo.all_vinculaciones.return_value = [v]
    repo.get_pago.return_value = _pago_ves_de_2030()
    repo.get_orden.return_value = OrdenVenta(
        so_id="S2",
        cliente_id="C1",
        fecha=date(2026, 1, 1),
        fecha_entrega=None,
        monto_total=Decimal("100"),
        lista_precios="4",
        vendedor_email="v@x.com",
        es_primera_compra=False,
    )
    repo.all_serie_tasas.return_value = []
    repo.all_tasas_historicas_auditoria.return_value = []
    pila, c = _cliente(repo)
    with pila:
        r = c.put("/api/vinculacion/V1/editar", json={"so_id": "S2", "monto_aplicado": 1.0})
    assert r.status_code == 400, r.text
    assert "2030-01-01" in r.json()["detail"]
    repo.update_vinculacion.assert_not_called()


# --- «sin fecha» nunca es «hoy» ------------------------------------------------------


def test_una_sugerencia_para_un_pago_sin_fecha_no_se_ofrece() -> None:
    """Antes un pago sin fecha se cotizaba con la tasa de HOY, y la sugerencia que
    salía se podía aceptar con un clic congelando ese equivalente."""
    from cxc.web.app import _get_conciliaciones_sugerencias_sync

    repo = MagicMock()
    repo.all_ordenes.return_value = []
    repo.all_vinculaciones.return_value = []
    repo.all_clientes.return_value = []
    repo.all_serie_tasas.return_value = []
    repo.all_tasas_historicas_auditoria.return_value = [
        {
            "fecha": date.today().isoformat(),
            "tasa_bcv_usd": "100.0",
            "tasa_bcv_euro": "110.0",
            "tasa_binance_promedio_diario": "120.0",
        }
    ]
    filas = [{"pago_id": "SINFECHA", "cliente_id": "C1", "monto": "1000", "moneda": "VES"}]
    app.invalidar_tasas()
    with (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app._all_pagos_rows", return_value=filas),
        patch("cxc.web.app._connect", return_value=None),
        patch("cxc.web.app.AppConfig.from_env"),
    ):
        sugerencias = _get_conciliaciones_sugerencias_sync(repo)
    assert [s for s in sugerencias if s.get("pago_id") == "SINFECHA"] == [], (
        "con la tasa de HOY habría salido una sugerencia para un pago sin fecha"
    )


def test_el_equivalente_por_serie_de_una_fecha_ilegible_es_cero_y_no_el_de_hoy() -> None:
    from cxc.web.app import _eq_usd_por_serie

    hoy = [
        {
            "timestamp": f"{date.today().isoformat()} 10:00:00",
            "tasa_bcv": "100.0",
            "tasa_binance": "120.0",
        }
    ]
    with patch("cxc.web.app._tasas_historicas_cacheadas", return_value=[]):
        assert _eq_usd_por_serie("ayer", Decimal("1000"), hoy) == Decimal("0")
        assert _eq_usd_por_serie(date.today().isoformat(), Decimal("1000"), hoy) == Decimal("10")
