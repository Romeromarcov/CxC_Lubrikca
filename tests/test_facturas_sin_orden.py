"""Facturas de cliente SIN orden de venta como deuda en los reportes de CxC.

Caso real (noviembre 2026, TERA): fletes facturados directo en Odoo, sin
``invoice_origin``. Son deuda igual que la factura de una orden, pero como
Ventas y el árbol de CxC salen de las órdenes, no aparecían en ningún reporte.
Pedido del usuario: cualquier factura sin orden (no solo fletes) y con el saldo
en USD que da Odoo (``amount_residual_usd``), no en VES.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from cxc.web.app import _facturas_sin_orden_con_saldo, app
from cxc.web.reporte_cxc_vendedor import agrupar_por_vendedor

_FLETE = {
    "id": 21404,
    "name": "00000989",
    "partner_id": [382, "CONSTRUCTORA GRANO AGREGADO, C.A"],
    "invoice_date": "2026-10-27",
    "invoice_date_due": "2026-10-27",
    "invoice_user_id": [7, "Marco Romero"],
    "amount_total_signed_usd": 450.0,
    "amount_residual_usd": 316.06,
    "amount_residual": 284341.15,  # VES: NO es el que se usa
    "invoice_origin": False,
    "move_type": "out_invoice",
}


def _leer_con(recs):
    with (
        patch("cxc.web.app.AppConfig.from_env", return_value=SimpleNamespace(odoo=None)),
        patch("cxc.web.app._connect", return_value=lambda *a, **k: recs),
    ):
        return _facturas_sin_orden_con_saldo()


def test_el_saldo_es_el_de_odoo_en_usd_no_en_ves():
    (f,) = _leer_con([_FLETE])
    assert f["saldo_usd"] == 316.06
    assert f["total_usd"] == 450.0
    assert f["cliente_id"] == "382"
    assert f["vendedor"] == "Marco Romero"
    assert f["numero"] == "00000989"


def test_cualquier_factura_sin_orden_no_solo_fletes():
    servicio = {**_FLETE, "id": 1, "name": "00000999", "amount_residual_usd": 80.0}
    assert {f["numero"] for f in _leer_con([_FLETE, servicio])} == {"00000989", "00000999"}


def test_una_factura_de_orden_nunca_se_cuela_aunque_odoo_la_devuelva():
    """Si el dominio no filtrara (o un execute falso lo ignora), la factura de una orden
    se contaría dos veces: en su orden y aquí."""
    de_orden = {**_FLETE, "id": 2, "invoice_origin": "S01025"}
    nota = {**_FLETE, "id": 3, "move_type": "out_refund"}
    assert [f["factura_id"] for f in _leer_con([_FLETE, de_orden, nota])] == ["21404"]


def test_sin_saldo_o_sin_cliente_no_aparece():
    pagada = {**_FLETE, "id": 4, "amount_residual_usd": 0.0}
    sin_partner = {**_FLETE, "id": 5, "partner_id": False}
    assert _leer_con([pagada, sin_partner]) == []


def test_si_odoo_falla_devuelve_vacio_sin_romper():
    with patch("cxc.web.app.AppConfig.from_env", side_effect=RuntimeError("sin odoo")):
        assert _facturas_sin_orden_con_saldo() == []


@pytest.fixture
def cliente_http():
    return TestClient(app)


def test_el_reporte_por_cliente_la_muestra_como_deuda(cliente_http):
    repo = MagicMock()
    repo.all_ordenes.return_value = []
    repo.all_facturas.return_value = []
    fso = _leer_con([_FLETE])
    with (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app.get_ventas", new=AsyncMock(return_value={"items": []})),
        patch("cxc.web.app.get_conciliaciones_sugerencias", new=AsyncMock(return_value=[])),
        patch("cxc.web.app._facturas_sin_orden_con_saldo", return_value=fso),
    ):
        res = cliente_http.get("/api/reporte-cxc-cliente")
    assert res.status_code == 200
    (c,) = res.json()["clientes"]
    assert c["cliente_nombre"] == "CONSTRUCTORA GRANO AGREGADO, C.A"
    assert c["vendedor"] == "Marco Romero"
    assert c["saldos"] == {
        "teorico_bs": 316.06,
        "teorico_usd": 316.06,
        "venta_real": 316.06,
        "factura_real": 316.06,
    }
    assert c["saldo_cobrable"] == 316.06
    (d,) = c["documentos"]
    assert d["tipo"] == "factura_sin_orden"
    assert d["factura_numero"] == "00000989"
    assert d["montos_originales"]["venta_real"] == 450.0


def test_en_la_vista_por_vendedor_cuenta_en_los_tramos_no_como_pago():
    doc = {
        "tipo": "factura_sin_orden",
        "factura_numero": "00000989",
        "dias_vencido": 14,
        "saldos": dict.fromkeys(
            ("teorico_bs", "teorico_usd", "venta_real", "factura_real"), 316.06
        ),
        "vendedor": "Marco Romero",
    }
    (v,) = agrupar_por_vendedor(
        [
            {
                "cliente_id": "382",
                "cliente_nombre": "X",
                "vendedor": "Marco Romero",
                "documentos": [doc],
            }
        ]
    )
    assert v["antiguedad"]["1_30"] == 316.06
    assert v["pagos_sin_aplicar"] == 0.0
    assert v["n_ordenes"] == 1


# --- Cobranza: el pago repartido entre una orden y una factura sin orden ---------------


def _sugerencias_con(vincs, residual):
    """Pago P1 de 1.000 USD; Odoo dice que le queda ``residual`` sin aplicar."""
    from decimal import Decimal

    import cxc.web.app as app_mod
    from cxc.web.app import _get_conciliaciones_sugerencias_sync

    repo = MagicMock()
    repo.all_ordenes.return_value = []
    repo.all_vinculaciones.return_value = vincs
    repo.all_clientes.return_value = []
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
        {
            "pago_id": "P1",
            "cliente_id": "C1",
            "monto": "1000",
            "moneda": "USD",
            "fecha_pago": "2026-07-02",
        }
    ]
    app_mod.invalidar_tasas()
    with (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app._all_pagos_rows", return_value=filas),
        patch("cxc.web.app._connect", return_value=lambda *a, **k: []),
        patch("cxc.web.app.AppConfig.from_env"),
        patch("cxc.web.app.get_reconciled_pago_ids_odoo", return_value=set()),
        patch(
            "cxc.web.app.residual_disponible_por_pago",
            return_value={"P1": Decimal(str(residual))},
        ),
    ):
        return [s for s in _get_conciliaciones_sugerencias_sync(None) if s["pago_id"] == "P1"]


def _vinc_pendiente(monto):
    from datetime import datetime
    from decimal import Decimal

    from cxc.models import EstadoVinculacion, Moneda, Vinculacion

    return Vinculacion(
        vinc_id="V1",
        pago_id="P1",
        so_id="S01025",
        monto_aplicado=Decimal(str(monto)),
        hora_pago_confirmada=datetime(2026, 7, 2),
        tasa_bcv_aplicada=Decimal("100"),
        tasa_binance_aplicada=Decimal("120"),
        es_tasa_heredada=False,
        estado=EstadoVinculacion.PENDIENTE,
        moneda_abono=Moneda.USD,
    )


def test_el_remanente_ya_cubierto_por_una_pendiente_no_se_vuelve_a_sugerir():
    """Caso TERA, pago 2231: Odoo aplicó 900 a una factura sin orden y deja 100;
    esos 100 ya los cubre una Vinculación PENDIENTE. Antes salía otra sugerencia
    de 100 hacia la misma orden ("Pendiente" + "Vinculado" en Cobranza)."""
    assert _sugerencias_con([_vinc_pendiente(100)], residual=100) == []


def test_si_la_pendiente_cubre_solo_parte_del_remanente_se_sugiere_lo_que_falta():
    (fila,) = _sugerencias_con([_vinc_pendiente(40)], residual=100)
    assert abs(fila["saldo_pago"] - 60.0) < 0.01


def _execute_odoo_sin_orden(model, method, args, kw=None):
    if model == "account.payment":
        return [{"id": 2231, "move_id": [9001, "PBBAN/2026/00032"]}]
    if model == "account.move.line" and method == "search_read":
        return [{"id": 501, "move_id": [9001, "PBBAN/2026/00032"]}]
    if model == "account.partial.reconcile":
        return [
            # a la factura sin orden
            {
                "credit_move_id": [501, ""],
                "debit_move_id": [701, ""],
                "credit_amount_currency": 1295994.98,
            },
            # a la factura de la orden (no se cuenta acá)
            {
                "credit_move_id": [501, ""],
                "debit_move_id": [702, ""],
                "credit_amount_currency": 1000.0,
            },
        ]
    if model == "account.move.line" and method == "read":
        return [{"id": 701, "move_id": [21000, "00000900"]}, {"id": 702, "move_id": [21001, "F1"]}]
    raise AssertionError((model, method))


def test_cobranza_muestra_la_parte_que_fue_a_la_factura_sin_orden():
    from cxc.web.app import _filas_de_facturas_sin_orden

    conciliados = [
        {
            "pago_id": "2231",
            "cliente_nombre": "TERA",
            "fecha_pago": "2026-09-25",
            "monto_ref_usd": 1550.0,
            "moneda": "VES",
            "monto_original": 1326276.88,
            "residual_pago_usd": 0.0,
            "facturas": [
                {
                    "move_id": 21000,
                    "factura_id": "00000900",
                    "so_id": "",
                    "tipo": "out_invoice",
                    "residual_usd": 0.0,
                },
                {
                    "move_id": 21001,
                    "factura_id": "F1",
                    "so_id": "S01025",
                    "tipo": "out_invoice",
                    "residual_usd": 5.0,
                },
            ],
        },
        # sin Vinculación local: ya sale por la rama "conciliado directo en Odoo"
        {"pago_id": "1813", "facturas": [{"move_id": 1, "so_id": "", "tipo": "out_invoice"}]},
    ]
    (fila,) = _filas_de_facturas_sin_orden(_execute_odoo_sin_orden, conciliados, {"2231"})
    assert fila["pago_id"] == "2231"
    assert fila["factura_id"] == "00000900"
    assert fila["so_id"] == ""
    assert fila["estado"] == "CONCILIADO"
    assert abs(fila["monto_aplicado"] - 1295994.98) < 0.01


def test_si_odoo_falla_cobranza_sigue_sin_esa_fila():
    from cxc.web.app import _filas_de_facturas_sin_orden

    def roto(*a, **k):
        raise RuntimeError("odoo caído")

    conciliados = [
        {"pago_id": "2231", "facturas": [{"move_id": 1, "so_id": "", "tipo": "out_invoice"}]}
    ]
    assert _filas_de_facturas_sin_orden(roto, conciliados, {"2231"}) == []
