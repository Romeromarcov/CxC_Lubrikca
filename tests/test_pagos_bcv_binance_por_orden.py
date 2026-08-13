"""``_pagos_bcv_binance_por_orden`` -- columnas nuevas "Monto pagado BCV"/

"Monto pagado USD (Binance)" de Ventas, con SU PROPIA tasa por ruta (a
diferencia de ``_pagos_odoo_por_orden``/``_pagos_por_so_desde_cobranza``,
que dan el mismo número para ambas rutas -- decisión explícita del usuario
de NO tocar esas otras dos, solo esta función nueva)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from cxc.web.app import PAGO_ESTADOS_CONFIRMADOS, _pagos_bcv_binance_por_orden

assert PAGO_ESTADOS_CONFIRMADOS  # solo para confirmar el import compartido


@pytest.fixture(autouse=True)
def _mock_get_repo():
    # get_rate_for_datetime (usado internamente) siempre llama get_repo()
    # -- se mockea para no tocar infraestructura real en estos tests.
    mock_repo = MagicMock()
    mock_repo.all_tasas_historicas_auditoria.return_value = []
    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        yield


def test_pago_usd_es_1_a_1_en_ambas_rutas() -> None:
    def _fake_execute(model, method, args, kwargs=None):
        if model == "account.payment":
            return [
                {
                    "id": 1,
                    "amount": 100.0,
                    "currency_id": [1, "USD"],
                    "date": "2026-06-01",
                    "reconciled_invoice_ids": [900],
                }
            ]
        return []

    result = _pagos_bcv_binance_por_orden(
        _fake_execute,
        invoice_ids_all=[900],
        inv_id_to_so={900: "SO_USD"},
        es_historica_map={},
        tasas_rows=[],
        hist_rows=[],
    )
    assert result["SO_USD"]["monto_pagado_bcv"] == 100.0
    assert result["SO_USD"]["monto_pagado_usd_binance"] == 100.0


def test_pago_ves_usa_tasa_distinta_por_ruta() -> None:
    def _fake_execute(model, method, args, kwargs=None):
        if model == "account.payment":
            return [
                {
                    "id": 2,
                    "amount": 1000.0,
                    "currency_id": [166, "VES"],
                    "date": "2026-06-01",
                    "reconciled_invoice_ids": [901],
                }
            ]
        return []

    tasas_rows = [
        {"timestamp": "2026-06-01 10:00:00", "tasa_bcv": "500.0", "tasa_binance": "550.0"}
    ]
    result = _pagos_bcv_binance_por_orden(
        _fake_execute,
        invoice_ids_all=[901],
        inv_id_to_so={901: "SO_VES"},
        es_historica_map={"SO_VES": False},
        tasas_rows=tasas_rows,
        hist_rows=[],
    )
    assert result["SO_VES"]["monto_pagado_bcv"] == 2.0  # 1000/500
    assert round(result["SO_VES"]["monto_pagado_usd_binance"], 4) == round(1000 / 550, 4)
    # Las dos rutas dan numeros DISTINTOS (a diferencia de la logica vieja).
    assert result["SO_VES"]["monto_pagado_bcv"] != result["SO_VES"]["monto_pagado_usd_binance"]


def test_pago_ves_en_ventana_historica_usa_tasa_euro_para_bcv() -> None:
    """Orden en la ventana de la Lista Historica de Auditoria (Euro): la

    ruta BCV usa la tasa BCV-Euro del dia, no la BCV-USD normal -- Binance
    sigue usando la tasa Binance normal (el Euro solo sustituye BCV)."""

    def _fake_execute(model, method, args, kwargs=None):
        if model == "account.payment":
            return [
                {
                    "id": 3,
                    "amount": 16606.59,
                    "currency_id": [166, "VES"],
                    "date": "2026-03-12",
                    "reconciled_invoice_ids": [902],
                }
            ]
        return []

    tasas_rows = [
        {
            "timestamp": "2026-03-12 10:00:00",
            "tasa_bcv": "440.9657",
            "tasa_binance": "516.8118",
            "tasa_bcv_euro": "510.4884",
        }
    ]
    result = _pagos_bcv_binance_por_orden(
        _fake_execute,
        invoice_ids_all=[902],
        inv_id_to_so={902: "SO_EUR"},
        es_historica_map={"SO_EUR": True},
        tasas_rows=tasas_rows,
        hist_rows=[],
    )
    # 16606.59 / 510.4884 ~= 32.53 (coincide con lo que Odoo aplico -- ver
    # verificacion en vivo contra el pago real de S00020).
    assert round(result["SO_EUR"]["monto_pagado_bcv"], 2) == 32.53
    # Binance NO usa la tasa euro -- usa la tasa binance normal del dia.
    assert round(result["SO_EUR"]["monto_pagado_usd_binance"], 2) == round(16606.59 / 516.8118, 2)


def test_pago_reconcilia_varias_ordenes_se_prorratea_por_facturado() -> None:
    """Hallazgo real (agosto 2026, reporte de candidatos a cierre de

    Diferencial Cambiario): un pago que reconcilia facturas de VARIAS
    ordenes ya NO se cuenta completo en cada una -- se reparte proporcional
    al monto facturado (con impuestos) de cada orden."""

    def _fake_execute(model, method, args, kwargs=None):
        if model == "account.payment":
            return [
                {
                    "id": 4,
                    "amount": 1000.0,
                    "currency_id": [1, "USD"],
                    "date": "2026-06-01",
                    "reconciled_invoice_ids": [910, 911],
                }
            ]
        return []

    result = _pagos_bcv_binance_por_orden(
        _fake_execute,
        invoice_ids_all=[910, 911],
        inv_id_to_so={910: "SO_A", 911: "SO_B"},
        es_historica_map={},
        tasas_rows=[],
        hist_rows=[],
        # SO_A facturo 600, SO_B facturo 400 -- 60/40.
        facturado_con_imp_por_so={"SO_A": 600.0, "SO_B": 400.0},
    )
    assert result["SO_A"]["monto_pagado_bcv"] == 600.0
    assert result["SO_B"]["monto_pagado_bcv"] == 400.0
    # Suma total = el monto real del pago, no el doble.
    assert result["SO_A"]["monto_pagado_bcv"] + result["SO_B"]["monto_pagado_bcv"] == 1000.0


def test_pago_reconcilia_varias_ordenes_sin_pesos_reparte_equitativo() -> None:
    # Sin facturado_con_imp_por_so (o todo en 0) -- reparte equitativo como
    # ultimo recurso, en vez de duplicar el monto completo en cada orden.
    def _fake_execute(model, method, args, kwargs=None):
        if model == "account.payment":
            return [
                {
                    "id": 5,
                    "amount": 1000.0,
                    "currency_id": [1, "USD"],
                    "date": "2026-06-01",
                    "reconciled_invoice_ids": [920, 921],
                }
            ]
        return []

    result = _pagos_bcv_binance_por_orden(
        _fake_execute,
        invoice_ids_all=[920, 921],
        inv_id_to_so={920: "SO_C", 921: "SO_D"},
        es_historica_map={},
        tasas_rows=[],
        hist_rows=[],
    )
    assert result["SO_C"]["monto_pagado_bcv"] == 500.0
    assert result["SO_D"]["monto_pagado_bcv"] == 500.0
