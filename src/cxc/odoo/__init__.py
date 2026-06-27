"""Adaptador Odoo (XML-RPC) — SOLO LECTURA. Odoo es la única autoridad contable."""

from .client import (
    OdooReader,
    OdooXmlRpcReader,
    map_cliente,
    map_factura,
    map_linea,
    map_orden,
    map_pago,
)

__all__ = [
    "OdooReader",
    "OdooXmlRpcReader",
    "map_cliente",
    "map_orden",
    "map_linea",
    "map_pago",
    "map_factura",
]
