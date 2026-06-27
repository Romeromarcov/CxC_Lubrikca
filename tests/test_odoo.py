"""Tests del adaptador Odoo: mapeo puro + construcción del dominio delta."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from cxc.config import OdooConfig
from cxc.models import Moneda
from cxc.odoo.client import (
    OdooXmlRpcReader,
    map_cliente,
    map_factura,
    map_linea,
    map_orden,
    map_pago,
)


def test_map_cliente() -> None:
    c = map_cliente({"id": 7, "name": "ACME", "vendedor_email": "rep@x.com"})
    assert c.cliente_id == "7"
    assert c.vendedor_email == "rep@x.com"


def test_map_orden_con_many2one_y_fechas() -> None:
    o = map_orden(
        {
            "id": 100,
            "partner_id": [7, "ACME"],
            "date_order": "2026-06-01 09:30:00",
            "fecha_entrega": "2026-06-05",
            "amount_total": "1234.50",
            "pricelist_id": [3, "BCV"],
            "vendedor_email": "rep@x.com",
            "es_primera_compra": True,
            "facturada": False,
            "factura_id": False,
            "monto_facturado": False,
        }
    )
    assert o.so_id == "100"
    assert o.cliente_id == "7"
    assert o.fecha.isoformat() == "2026-06-01"
    assert o.fecha_entrega is not None and o.fecha_entrega.isoformat() == "2026-06-05"
    assert o.monto_total == Decimal("1234.50")
    assert o.lista_precios == "3"
    assert o.es_primera_compra is True
    assert o.factura_id is None
    assert o.monto_facturado is None


def test_map_linea() -> None:
    ln = map_linea(
        {
            "id": 5,
            "order_id": [100, "SO100"],
            "product_id": [9, "Aceite"],
            "marca": "Sinoco",
            "categoria": "Industrial",
            "product_uom_qty": "3",
            "price_unit": "20.00",
        }
    )
    assert ln.so_id == "100"
    assert ln.producto == "9"
    assert ln.marca == "Sinoco"
    assert ln.cantidad == Decimal("3")


def test_map_pago_moneda() -> None:
    p = map_pago(
        {
            "id": 1,
            "partner_id": 7,
            "amount": "500.00",
            "moneda": "usd",
            "metodo_pago": [2, "Zelle"],
            "date": "2026-06-05 14:00:00",
            "vendedor_email": "rep@x.com",
        }
    )
    assert p.moneda == Moneda.USD
    assert p.monto == Decimal("500.00")
    assert p.fecha_pago == datetime(2026, 6, 5, 14, 0)


def test_map_factura_y_nota_credito() -> None:
    fact = map_factura({"so_id": [100, "SO100"], "amount_total": "900", "move_type": "out_invoice"})
    assert fact == ("100", Decimal("900"), Decimal("0"))
    nc = map_factura({"so_id": 100, "amount_total": "50", "move_type": "out_refund"})
    assert nc == ("100", Decimal("0"), Decimal("50"))


def _config() -> OdooConfig:
    return OdooConfig(url="http://x", db="d", username="u", password="p")


def test_domain_delta_usa_write_date() -> None:
    capturado: list[Any] = []

    def fake_execute(model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        capturado.append((model, method, args, kwargs))
        return []

    reader = OdooXmlRpcReader(_config(), execute=fake_execute)
    reader.changed_ordenes(datetime(2026, 6, 1, 0, 0))
    model, method, args, kwargs = capturado[0]
    assert model == "sale.order"
    assert method == "search_read"
    assert args[0] == [["write_date", ">", "2026-06-01 00:00:00"]]
    assert "fields" in kwargs


def test_domain_full_load_sin_cursor() -> None:
    def fake_execute(model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        return [{"id": 1, "name": "ACME", "vendedor_email": "r@x.com"}]

    reader = OdooXmlRpcReader(_config(), execute=fake_execute)
    clientes = reader.changed_clientes(None)
    assert clientes[0].cliente_id == "1"
