"""Tarea 3c-3g: nuevas columnas de ``/api/ventas``.

Cubre la matriz pedida por el /goal: lista USD/VES x con/sin descuento en
Odoo x con/sin N/C x con/sin N/D. Ninguno de estos tests recalcula
descuentos -- solo verifica que ``/api/ventas`` lea correctamente lo que
Odoo y el motor (``BandejaFacturacion``) ya tienen, y que la comparación
(Tarea 3c/3d) use las mismas funciones puras de ``discount_audit`` que
``/api/auditoria``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cxc.config import EngineConfig
from cxc.models import BandejaFacturacion, OrdenVenta
from cxc.web.app import app

client = TestClient(app)


def _orden(so_id: str, *, lista: str, monto_total: str, facturada: bool = True) -> OrdenVenta:
    return OrdenVenta(
        so_id=so_id,
        cliente_id="CLI_3C",
        vendedor_email="ana@lubrikca.com",
        fecha=date(2026, 7, 1),
        fecha_entrega=None,
        monto_total=Decimal(monto_total),
        lista_precios=lista,
        es_primera_compra=False,
        estado_orden="sale",
        facturada=facturada,
    )


def _fake_execute(model, method, args, kwargs=None):
    domain = args[0] if args else []

    if model == "sale.order":
        return [
            {"name": "SO_OK", "state": "sale", "amount_untaxed": 95.0},
            {"name": "SO_PENDIENTE", "state": "sale", "amount_untaxed": 97.0},
            {"name": "SO_NC", "state": "sale", "amount_untaxed": 200.0},
            {"name": "SO_ND", "state": "sale", "amount_untaxed": 90.0},
        ]

    if model == "account.move":
        # Dos llamadas distintas al mismo modelo: la principal (facturas +
        # NC por invoice_origin) y la de notas de débito (por
        # debit_origin_id). Se distinguen por el campo del dominio.
        is_debit_notes_query = any(
            isinstance(clause, list | tuple) and clause and clause[0] == "debit_origin_id"
            for clause in domain
        )
        if is_debit_notes_query:
            # SO_ND: la factura original es id=901 (ver abajo).
            return [
                {
                    "debit_origin_id": [901, "FAC/901"],
                    "amount_total_signed_usd": 20.0,
                }
            ]
        return [
            {
                "id": 900,
                "invoice_origin": "SO_OK",
                "move_type": "out_invoice",
                "amount_untaxed_signed_usd": 95.0,
                "amount_total_signed_usd": 95.0,
            },
            {
                "id": 902,
                "invoice_origin": "SO_PENDIENTE",
                "move_type": "out_invoice",
                "amount_untaxed_signed_usd": 97.0,
                "amount_total_signed_usd": 97.0,
            },
            {
                "id": 903,
                "invoice_origin": "SO_NC",
                "move_type": "out_invoice",
                "amount_untaxed_signed_usd": 200.0,
                "amount_total_signed_usd": 200.0,
            },
            {
                "id": 904,
                "invoice_origin": "SO_NC",
                "move_type": "out_refund",
                "amount_untaxed_signed_usd": 30.0,
                "amount_total_signed_usd": 30.0,
            },
            {
                "id": 901,
                "invoice_origin": "SO_ND",
                "move_type": "out_invoice",
                "amount_untaxed_signed_usd": 90.0,
                "amount_total_signed_usd": 90.0,
            },
        ]

    if model == "sale.order.line":
        return [
            # SO_OK: Odoo ya tiene exactamente el 5% que el motor calculó.
            {
                "order_id": [1, "SO_OK"],
                "product_uom_qty": 1.0,
                "price_unit": 100.0,
                "discount": 5.0,
                "price_subtotal": 95.0,
            },
            # SO_PENDIENTE: Odoo solo tiene 3, el motor exige 10 -> pendiente.
            {
                "order_id": [2, "SO_PENDIENTE"],
                "product_uom_qty": 1.0,
                "price_unit": 100.0,
                "discount": 3.0,
                "price_subtotal": 97.0,
            },
        ]

    return []


def _run_get_ventas():
    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        _orden("SO_OK", lista="4", monto_total="95.00"),
        _orden("SO_PENDIENTE", lista="4", monto_total="97.00"),
        _orden("SO_NC", lista="4", monto_total="200.00"),
        _orden("SO_ND", lista="5", monto_total="90.00"),
    ]
    mock_repo.all_bandeja.return_value = [
        BandejaFacturacion(
            so_id="SO_OK",
            lista_aplicada="4",
            precio_base_calculado=Decimal("100.00"),
            total_descuentos=Decimal("5.00"),
            total_motor=Decimal("95.00"),
        ),
        BandejaFacturacion(
            so_id="SO_PENDIENTE",
            lista_aplicada="4",
            precio_base_calculado=Decimal("100.00"),
            total_descuentos=Decimal("10.00"),
            total_motor=Decimal("90.00"),
        ),
        BandejaFacturacion(
            so_id="SO_NC",
            lista_aplicada="4",
            precio_base_calculado=Decimal("200.00"),
            total_motor=Decimal("200.00"),
        ),
        BandejaFacturacion(
            so_id="SO_ND",
            lista_aplicada="5",
            precio_base_calculado=Decimal("90.00"),
            total_motor=Decimal("90.00"),
        ),
    ]

    fake_config = MagicMock()
    fake_config.engine = EngineConfig(
        cash_window_business_days=3,
        bcv_complete_formula="differential_over_binance",
    )

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=_fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=fake_config),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        return {it["so_id"]: it for it in res.json()["items"]}


def test_descuento_aplicado_orden_coincide_con_motor_es_ok() -> None:
    """Lista USD, sin NC, sin ND: Odoo ya tiene el mismo % que dictó el motor."""
    by_so = _run_get_ventas()
    ok = by_so["SO_OK"]
    assert ok["descuento_aplicado_orden"] == 5.0
    assert ok["descuento_motor_total"] == 5.0
    assert ok["descuento_validacion_orden"] == "ok"
    assert ok["descuento_pendiente_aplicar"] == 0.0
    assert ok["descuento_aplicado_sistema"] is None


def test_descuento_pendiente_aplicar_cuando_motor_exige_mas_que_odoo() -> None:
    """El motor calculó 10 de descuento; Odoo solo tiene 3 -> pendiente = 7."""
    by_so = _run_get_ventas()
    pend = by_so["SO_PENDIENTE"]
    assert pend["descuento_aplicado_orden"] == 3.0
    assert pend["descuento_motor_total"] == 10.0
    assert pend["descuento_pendiente_aplicar"] == 7.0
    assert pend["descuento_validacion_orden"] == "discrepancia"


def test_nota_credito_reduce_facturado_neto_logica_reutilizada() -> None:
    """N/C (lógica ya existente): factura $200 - NC $30 = neto $170, sin N/D."""
    by_so = _run_get_ventas()
    nc = by_so["SO_NC"]
    assert nc["total_facturado_con_impuestos"] == 200.0
    assert nc["total_nc_aplicada"] == 30.0
    assert nc["total_nd_aplicada"] == 0.0
    assert nc["total_facturado_neto"] == 170.0


def test_nota_debito_atada_a_factura_incrementa_facturado_neto() -> None:
    """Lista VES: N/D $20 atada a la factura de SO_ND -> neto = 90 - 0 + 20 = 110."""
    by_so = _run_get_ventas()
    nd = by_so["SO_ND"]
    assert nd["total_facturado_con_impuestos"] == 90.0
    assert nd["total_nc_aplicada"] == 0.0
    assert nd["total_nd_aplicada"] == 20.0
    assert nd["total_facturado_neto"] == 110.0


def test_descuento_aplicado_sistema_siempre_none_hasta_que_exista_facturacion() -> None:
    """Tarea 3e: campo listo, sin dato real todavía (depende de Facturación)."""
    by_so = _run_get_ventas()
    for it in by_so.values():
        assert it["descuento_aplicado_sistema"] is None
