"""Cliente Odoo XML-RPC — solo lectura, delta por ``write_date`` (sección 2).

Diseño para testabilidad sin red:
  - Las funciones ``map_*`` son PURAS: convierten un registro de Odoo (dict) en
    una dataclass del modelo. Se prueban con dicts, sin conexión.
  - ``OdooXmlRpcReader`` recibe un callable ``execute`` inyectable
    (``model, method, args, kwargs``). En producción lo provee ``ServerProxy``;
    en tests, un fake. Así se cubre la construcción del dominio delta sin red.

Los nombres de campo de Odoo son específicos del entorno (ver SETUP.md). Se
declaran como constantes — único punto de ajuste si difieren.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..config import OdooConfig
from ..decimal_utils import to_decimal
from ..models import Cliente, LineaOrden, Moneda, OrdenVenta, Pago

ODOO_DATETIME_FMT = "%Y-%m-%d %H:%M:%S"
ODOO_DATE_FMT = "%Y-%m-%d"


def _m2o_id(value: Any) -> str:
    """Odoo devuelve many2one como ``[id, "nombre"]`` o ``False``."""
    if isinstance(value, list | tuple) and value:
        return str(value[0])
    if value in (False, None):
        return ""
    return str(value)


def _to_date(value: Any) -> Any:
    if value in (False, None, ""):
        return None
    return datetime.strptime(str(value)[:10], ODOO_DATE_FMT).date()


def _to_datetime(value: Any) -> datetime:
    return datetime.strptime(str(value)[:19], ODOO_DATETIME_FMT)


def _dec(value: Any) -> Decimal:
    if value in (False, None, ""):
        return Decimal("0")
    return to_decimal(str(value))


def map_cliente(rec: dict[str, Any]) -> Cliente:
    return Cliente(
        cliente_id=str(rec["id"]),
        nombre=str(rec.get("name", "")),
        vendedor_email=str(rec.get("vendedor_email", "") or ""),
    )


def map_orden(rec: dict[str, Any]) -> OrdenVenta:
    monto_fact = rec.get("monto_facturado")
    return OrdenVenta(
        so_id=str(rec["id"]),
        cliente_id=_m2o_id(rec.get("partner_id")),
        fecha=_to_date(rec.get("date_order")) or _to_datetime(rec["date_order"]).date(),
        fecha_entrega=_to_date(rec.get("fecha_entrega")),
        monto_total=_dec(rec.get("amount_total")),
        lista_precios=_m2o_id(rec.get("pricelist_id")) or str(rec.get("lista_precios", "")),
        vendedor_email=str(rec.get("vendedor_email", "") or ""),
        es_primera_compra=bool(rec.get("es_primera_compra", False)),
        facturada=bool(rec.get("facturada", False)),
        factura_id=str(rec["factura_id"]) if rec.get("factura_id") else None,
        monto_facturado=_dec(monto_fact) if monto_fact not in (False, None, "") else None,
    )


def map_linea(rec: dict[str, Any]) -> LineaOrden:
    return LineaOrden(
        linea_id=str(rec["id"]),
        so_id=_m2o_id(rec.get("order_id")),
        producto=_m2o_id(rec.get("product_id")) or str(rec.get("producto", "")),
        marca=str(rec.get("marca", "") or ""),
        categoria=str(rec.get("categoria", "") or ""),
        cantidad=_dec(rec.get("product_uom_qty", rec.get("cantidad"))),
        precio_unitario=_dec(rec.get("price_unit", rec.get("precio_unitario"))),
    )


def map_pago(rec: dict[str, Any]) -> Pago:
    moneda_raw = str(rec.get("moneda", "VES")).upper()
    moneda = Moneda.USD if moneda_raw == "USD" else Moneda.VES
    return Pago(
        pago_id=str(rec["id"]),
        cliente_id=_m2o_id(rec.get("partner_id")),
        monto=_dec(rec.get("amount", rec.get("monto"))),
        moneda=moneda,
        metodo_pago=_m2o_id(rec.get("metodo_pago")) or str(rec.get("metodo_pago", "")),
        fecha_pago=_to_datetime(rec.get("date") or rec["fecha_pago"]),
        vendedor_email=str(rec.get("vendedor_email", "") or ""),
    )


def map_factura(rec: dict[str, Any]) -> tuple[str, Decimal, Decimal]:
    """Mapea una factura/NC de Odoo a (so_id, monto_facturado, ncs).

    ``move_type`` ``out_refund`` (nota de crédito) suma a NCs; el resto a monto.
    """
    so_id = _m2o_id(rec.get("so_id")) or str(rec.get("so_id", ""))
    monto = _dec(rec.get("amount_total", rec.get("monto")))
    if str(rec.get("move_type", "")) == "out_refund":
        return so_id, Decimal("0"), monto
    return so_id, monto, Decimal("0")


ExecuteFn = Callable[[str, str, list[Any], dict[str, Any]], Any]


class OdooReader(ABC):
    """Interfaz de lectura delta. Implementaciones: XML-RPC o fakes de test."""

    @abstractmethod
    def changed_clientes(self, since: datetime | None) -> list[Cliente]: ...

    @abstractmethod
    def changed_ordenes(self, since: datetime | None) -> list[OrdenVenta]: ...

    @abstractmethod
    def changed_lineas(self, since: datetime | None) -> list[LineaOrden]: ...

    @abstractmethod
    def changed_pagos(self, since: datetime | None) -> list[Pago]: ...


# Campos a leer por modelo (único punto de ajuste de mapeo Odoo).
FIELDS_CLIENTE = ["id", "name", "vendedor_email", "write_date"]
FIELDS_ORDEN = [
    "id", "partner_id", "date_order", "fecha_entrega", "amount_total",
    "pricelist_id", "vendedor_email", "es_primera_compra", "facturada",
    "factura_id", "monto_facturado", "write_date",
]
FIELDS_LINEA = [
    "id", "order_id", "product_id", "marca", "categoria",
    "product_uom_qty", "price_unit", "write_date",
]
FIELDS_PAGO = [
    "id", "partner_id", "amount", "moneda", "metodo_pago",
    "date", "vendedor_email", "write_date",
]


class OdooXmlRpcReader(OdooReader):
    MODEL_CLIENTE = "res.partner"
    MODEL_ORDEN = "sale.order"
    MODEL_LINEA = "sale.order.line"
    MODEL_PAGO = "account.payment"

    def __init__(self, config: OdooConfig, execute: ExecuteFn | None = None) -> None:
        self._config = config
        self._execute = execute or _connect(config)

    @staticmethod
    def _domain(since: datetime | None) -> list[Any]:
        if since is None:
            return []
        return [["write_date", ">", since.strftime(ODOO_DATETIME_FMT)]]

    def _search_read(
        self, model: str, fields: list[str], since: datetime | None
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._execute(
            model, "search_read", [self._domain(since)], {"fields": fields}
        )
        return result

    def changed_clientes(self, since: datetime | None) -> list[Cliente]:
        return [
            map_cliente(r)
            for r in self._search_read(self.MODEL_CLIENTE, FIELDS_CLIENTE, since)
        ]

    def changed_ordenes(self, since: datetime | None) -> list[OrdenVenta]:
        return [
            map_orden(r)
            for r in self._search_read(self.MODEL_ORDEN, FIELDS_ORDEN, since)
        ]

    def changed_lineas(self, since: datetime | None) -> list[LineaOrden]:
        return [
            map_linea(r)
            for r in self._search_read(self.MODEL_LINEA, FIELDS_LINEA, since)
        ]

    def changed_pagos(self, since: datetime | None) -> list[Pago]:
        return [
            map_pago(r)
            for r in self._search_read(self.MODEL_PAGO, FIELDS_PAGO, since)
        ]


def _connect(config: OdooConfig) -> ExecuteFn:  # pragma: no cover - red externa
    import xmlrpc.client

    common = xmlrpc.client.ServerProxy(f"{config.url}/xmlrpc/2/common")
    uid = common.authenticate(config.db, config.username, config.password, {})
    if not uid:
        raise PermissionError("Autenticación Odoo fallida")
    models = xmlrpc.client.ServerProxy(f"{config.url}/xmlrpc/2/object")

    def execute(model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        return models.execute_kw(
            config.db, uid, config.password, model, method, args, kwargs
        )

    return execute
