"""``GET /api/pagos-historial`` -- bug real (agosto 2026): para pagos YA

reconciliados en Odoo (la mayoría, item "Odoo (automático vía factura)"),
``tasa_binance`` quedaba hardcodeada en ``None`` -- Binance no es un campo
de Odoo, así que a diferencia de ``tasa_bcv`` (que sí tenía un fallback
separado) esta tasa nunca se calculaba, dejando el equivalente Binance en
blanco para todo pago en VES ya conciliado.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cxc.web.app import app

client = TestClient(app)


def _fake_execute_pago_ves_conciliado(model, method, args, kwargs=None):
    if model == "account.payment":
        return [
            {
                "id": 17,
                "partner_id": [202, "TERA INGENIERIA"],
                "amount": 16606.59,
                "amount_ref": 32.53,
                "amount_available_for_refund": 0.0,
                "currency_id": [166, "VES"],
                "journal_id": [5, "Banco Bancamiga"],
                "date": "2026-03-12",
                "reconciled_invoice_ids": [4824],
            }
        ]
    if model == "account.move":
        return [
            {
                "id": 4824,
                "name": "00000270",
                "invoice_origin": "S00020",
                "move_type": "out_invoice",
                "state": "posted",
                "amount_total_signed_usd": 32.53,
                "amount_residual_usd": 0.0,
            }
        ]
    if model == "res.partner":
        return [{"id": 202, "user_id": False}]
    return []


def test_pagos_historial_calcula_tasa_binance_para_pagos_ya_conciliados() -> None:
    mock_repo = MagicMock()
    mock_repo.all_vinculaciones.return_value = []
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_pagos.return_value = []
    mock_repo.all_clientes.return_value = []
    mock_repo.all_ordenes.return_value = []
    mock_repo.all_serie_tasas.return_value = []
    mock_repo.all_tasas_historicas_auditoria.return_value = [
        {
            "fecha": "2026-03-12",
            "tasa_bcv_usd": "440.9657",
            "tasa_bcv_euro": "510.4884",
            "tasa_binance_promedio_diario": "516.8118",
        }
    ]

    fake_config = MagicMock()
    fake_config.odoo = MagicMock()

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=_fake_execute_pago_ves_conciliado),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/pagos-historial")
        assert res.status_code == 200
        data = res.json()

    pago = next(p for p in data if p["pago_id"] == "17")
    assert pago["origen"] == "Odoo (automático vía factura)"
    assert pago["tasa_binance"] == 516.8118


def _historial(mock_repo, execute):
    fake_config = MagicMock()
    fake_config.odoo = MagicMock()
    import cxc.web.app as app_module

    app_module.invalidar_tasas()
    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/pagos-historial")
    return res


def test_un_pago_conciliado_en_odoo_con_fecha_sin_tasa_no_borra_a_todos_los_demas() -> None:
    """Misma familia que /api/auditoria (12-sep-2026): ``TasaNoDisponible`` subía
    hasta el ``except`` que envuelve la lectura de Odoo, y ese ``except`` se tragaba
    con un warning TODOS los pagos conciliados solo en Odoo. La fila sin tasa queda,
    marcada, con Binance en ``None``."""
    mock_repo = MagicMock()
    mock_repo.all_vinculaciones.return_value = []
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_pagos.return_value = []
    mock_repo.all_clientes.return_value = []
    mock_repo.all_ordenes.return_value = []
    mock_repo.all_serie_tasas.return_value = []
    mock_repo.all_tasas_historicas_auditoria.return_value = []  # ninguna tasa

    res = _historial(mock_repo, _fake_execute_pago_ves_conciliado)
    assert res.status_code == 200
    (fila,) = res.json()
    assert fila["pago_id"] == "17"
    assert fila["sin_tasa_para_su_fecha"] is True
    assert fila["tasa_binance"] is None


def test_un_pago_local_en_bolivares_sin_tasa_conserva_el_equivalente_congelado() -> None:
    """La vinculación local trae su equivalente congelado; sin tasa para la fecha
    del pago no se reconvierte (antes levantaba y el endpoint era 500), y sin fecha
    tampoco se usa HOY como si fuera la fecha del pago."""
    from datetime import datetime
    from decimal import Decimal

    from cxc.models import EstadoVinculacion, Moneda, Vinculacion

    v = Vinculacion(
        vinc_id="V1",
        pago_id="P1",
        so_id="S00020",
        monto_aplicado=Decimal("16606.59"),
        hora_pago_confirmada=datetime(2026, 3, 12, 10, 0),
        tasa_bcv_aplicada=Decimal("440.9657"),
        tasa_binance_aplicada=Decimal("516.8118"),
        es_tasa_heredada=False,
        equiv_usd_bcv=Decimal("37.66"),
        estado=EstadoVinculacion.CONCILIADO,
        moneda_abono=Moneda.VES,
    )
    # ``Pago.fecha_pago`` no admite vacío en el espejo; la fecha vacía o ilegible
    # se simula en la fila serializada, que es lo que el endpoint lee.
    for fecha in ("2026-03-12", "", "ayer"):
        mock_repo = MagicMock()
        mock_repo.all_vinculaciones.return_value = [v]
        mock_repo._g.read_rows.return_value = []
        mock_repo.all_pagos.return_value = []
        mock_repo.all_clientes.return_value = []
        mock_repo.all_ordenes.return_value = []
        mock_repo.all_serie_tasas.return_value = []
        mock_repo.all_tasas_historicas_auditoria.return_value = []
        fila_pago = {
            "pago_id": "P1",
            "cliente_id": "C1",
            "monto": "16606.59",
            "moneda": "VES",
            "fecha_pago": fecha,
        }
        with patch("cxc.web.app._all_pagos_rows", return_value=[fila_pago]):
            res = _historial(mock_repo, lambda *a, **k: [])
        assert res.status_code == 200, (fecha, res.text)
        fila = next(f for f in res.json() if f["vinc_id"] == "V1")
        assert fila["sin_tasa_para_su_fecha"] is True, fecha
        assert abs(fila["monto_pago_usd"] - 37.66) < 0.01, "el congelado, no una conversión de hoy"
