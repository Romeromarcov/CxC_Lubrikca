"""Cliente Odoo XML-RPC — solo lectura, delta por ``write_date`` (sección 2).

Mapeo calibrado contra el Odoo 18 QA de Lubrikca (ver docs/ODOO_MAPEO.md):
  - SO identificada por ``name`` (S00553), no por id numérico.
  - ``vendedor_email`` = ``user_id.login`` (resuelto con 2ª consulta a res.users).
  - ``fecha_entrega`` = ``stock.picking.date_done`` del despacho saliente.
  - marca/categoría salen del PRODUCTO (``brand_id`` / raíz de ``categ_id``).
  - ``metodo_pago`` = ``journal_id`` (el diario lleva la identidad real).
  - ``es_primera_compra`` se calcula (primera SO del partner).

Diseño para testabilidad sin red:
  - ``map_*`` son PURAS: convierten un dict ya enriquecido en una dataclass.
  - ``OdooXmlRpcReader`` recibe un ``execute`` inyectable y hace el enriquecimiento
    (resolución de relaciones) con consultas batch. En tests se prueba con un
    ``execute`` falso que despacha por modelo.
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..config import OdooConfig
from ..decimal_utils import to_decimal
from ..models import Cliente, LineaOrden, Moneda, OrdenVenta, Pago

ODOO_DATETIME_FMT = "%Y-%m-%d %H:%M:%S"
ODOO_DATE_FMT = "%Y-%m-%d"

# account.payment no tiene estado "posted" (ese existe en account.move). Sus
# estados validos son draft/in_process/paid/canceled/rejected; un pago
# confirmado/procesado queda en in_process o paid (ver docs/ODOO_MAPEO.md).
PAGO_ESTADOS_CONFIRMADOS = ["in_process", "paid"]


def _m2o_id(value: Any) -> str:
    """Odoo devuelve many2one como ``[id, "nombre"]`` o ``False``."""
    if isinstance(value, list | tuple) and value:
        return str(value[0])
    if value in (False, None):
        return ""
    return str(value)


def _m2o_name(value: Any) -> str:
    if isinstance(value, list | tuple) and len(value) > 1:
        return str(value[1])
    if value in (False, None):
        return ""
    return str(value)


def _to_date(value: Any) -> Any:
    if value in (False, None, ""):
        return None
    return datetime.strptime(str(value)[:10], ODOO_DATE_FMT).date()


def _to_datetime(value: Any) -> datetime:
    """Tolera fecha pura (``YYYY-MM-DD``) o datetime de Odoo."""
    s = str(value)
    if len(s) <= 10:
        return datetime.strptime(s[:10], ODOO_DATE_FMT)
    return datetime.strptime(s[:19], ODOO_DATETIME_FMT)


def _dec(value: Any) -> Decimal:
    if value in (False, None, ""):
        return Decimal("0")
    return to_decimal(str(value))


def _ids_of(recs: list[dict[str, Any]], field: str) -> set[int]:
    """Conjunto de ids (int) de un campo many2one a lo largo de varios registros."""
    out: set[int] = set()
    for r in recs:
        raw = _m2o_id(r.get(field))
        if raw:
            out.add(int(raw))
    return out


def map_cliente(rec: dict[str, Any]) -> Cliente:
    return Cliente(
        cliente_id=str(rec["id"]),
        nombre=str(rec.get("name", "")),
        vendedor_email=str(rec.get("vendedor_email", "") or ""),
        wh_iva_agent=bool(rec.get("wh_iva_agent")),
        wh_iva_rate=float(rec.get("wh_iva_rate") or 75.0)
        if rec.get("wh_iva_rate") is not None
        else 75.0,
    )


_FECHAS_HISTORICAS_DATA: dict[str, str] = {}
try:
    _json_path = os.path.join(
        os.path.dirname(__file__), "..", "data", "fechas_historicas_ordenes.json"
    )
    if os.path.exists(_json_path):
        with open(_json_path, encoding="utf-8") as _f:
            _d = json.load(_f)
            _FECHAS_HISTORICAS_DATA = _d.get("fechas_por_numero", {})
except Exception:
    pass


def _resolve_fecha_orden(so_name: str, odoo_date_val: Any) -> date:
    """Retorna la fecha histórica del CSV si existe para la orden; si no, la fecha de Odoo."""
    if so_name and _FECHAS_HISTORICAS_DATA:
        digits = re.sub(r"[^\d]", "", str(so_name).strip())
        if digits:
            norm_key = str(int(digits))
            if norm_key in _FECHAS_HISTORICAS_DATA:
                try:
                    return date.fromisoformat(_FECHAS_HISTORICAS_DATA[norm_key])
                except Exception:
                    pass
    return _to_datetime(odoo_date_val).date()


def map_orden(rec: dict[str, Any]) -> OrdenVenta:
    estado_entrega = str(rec.get("delivery_status", "") or "")
    entregada_completa = estado_entrega == "full"
    # El plazo de contado solo arranca con la entrega completa.
    fecha_entrega = _to_date(rec.get("fecha_entrega")) if entregada_completa else None

    so_name = str(rec.get("name", ""))
    fecha_orden = _resolve_fecha_orden(so_name, rec.get("date_order"))

    return OrdenVenta(
        so_id=so_name,
        cliente_id=_m2o_id(rec.get("partner_id")),
        fecha=fecha_orden,
        fecha_entrega=fecha_entrega,
        monto_total=_dec(rec.get("amount_total")),
        lista_precios=_m2o_id(rec.get("pricelist_id")),
        vendedor_email=str(rec.get("vendedor_email", "") or ""),
        es_primera_compra=bool(rec.get("es_primera_compra", False)),
        facturada=str(rec.get("invoice_status", "")) == "invoiced",
        factura_id=str(rec["factura_id"]) if rec.get("factura_id") else None,
        monto_facturado=None,  # lo computa la conciliación (USD vía tasa de factura)
        estado_orden=str(rec.get("state", "sale") or ""),
        estado_entrega=estado_entrega,
        entregada_completa=entregada_completa,
        tiene_devolucion=bool(rec.get("tiene_devolucion", False)),
    )


def map_linea(rec: dict[str, Any]) -> LineaOrden:
    return LineaOrden(
        linea_id=str(rec["id"]),
        so_id=_m2o_name(rec.get("order_id")),  # nombre de la SO (calza OrdenesVenta.so_id)
        producto=_m2o_id(rec.get("product_id")),
        marca=str(rec.get("marca", "") or ""),
        categoria=str(rec.get("categoria", "") or ""),
        cantidad=_dec(rec.get("product_uom_qty")),
        precio_unitario=_dec(rec.get("price_unit")),
        cantidad_entregada=_dec(rec.get("qty_delivered")),
        descuento=_dec(rec.get("discount")),
    )


def map_pago(rec: dict[str, Any]) -> Pago:
    moneda = Moneda.USD if _m2o_name(rec.get("currency_id")) == "USD" else Moneda.VES
    return Pago(
        pago_id=str(rec["id"]),
        cliente_id=_m2o_id(rec.get("partner_id")),
        monto=_dec(rec.get("amount")),
        moneda=moneda,
        metodo_pago=_m2o_id(rec.get("journal_id")),
        fecha_pago=_to_datetime(rec.get("date") or rec["fecha_pago"]),
        vendedor_email=str(rec.get("vendedor_email", "") or ""),
    )


def map_factura(rec: dict[str, Any]) -> tuple[str, Decimal, Decimal]:
    """Mapea una factura/NC de Odoo a (so_id, monto_usd, ncs_usd).

    Usa ``amount_total_signed_usd`` (equivalente USD a la tasa registrada en la
    factura — la compañía factura en VES). ``out_refund`` (NC) suma a NCs.
    """
    so_id = str(rec.get("invoice_origin", "") or "")
    usd = abs(_dec(rec.get("amount_total_signed_usd")))
    if str(rec.get("move_type", "")) == "out_refund":
        return so_id, Decimal("0"), usd
    return so_id, usd, Decimal("0")


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


class OdooXmlRpcReader(OdooReader):
    MODEL_PARTNER = "res.partner"
    MODEL_USERS = "res.users"
    MODEL_ORDEN = "sale.order"
    MODEL_LINEA = "sale.order.line"
    MODEL_PAGO = "account.payment"
    MODEL_PICKING = "stock.picking"
    MODEL_PRODUCT = "product.product"
    MODEL_MOVE = "account.move"

    def __init__(self, config: OdooConfig, execute: ExecuteFn | None = None) -> None:
        self._config = config
        self._execute = execute or _connect(config)

    @staticmethod
    def _delta(since: datetime | None) -> list[Any]:
        if since is None:
            return []
        from datetime import timedelta

        effective_since = since - timedelta(hours=48)
        return [["write_date", ">", effective_since.strftime(ODOO_DATETIME_FMT)]]

    def _search_read(
        self, model: str, domain: list[Any], fields: list[str]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._execute(
            model, "search_read", [domain], {"fields": fields}
        )
        return result

    def _read(self, model: str, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        result: list[dict[str, Any]] = self._execute(model, "read", [ids], {"fields": fields})
        return result

    # --- resolución de relaciones -------------------------------------------
    def _user_logins(self, user_ids: set[int]) -> dict[int, str]:
        recs = self._read(self.MODEL_USERS, sorted(user_ids), ["id", "name", "login"])
        return {int(r["id"]): str(r.get("name") or r.get("login") or "") for r in recs}

    # --- Clientes ------------------------------------------------------------
    def changed_clientes(self, since: datetime | None) -> list[Cliente]:
        recs = self._search_read(
            self.MODEL_PARTNER,
            self._delta(since),
            ["id", "name", "user_id", "wh_iva_agent", "wh_iva_rate"],
        )
        uids = {int(_m2o_id(r.get("user_id"))) for r in recs if _m2o_id(r.get("user_id"))}
        logins = self._user_logins(uids)
        for r in recs:
            uid = _m2o_id(r.get("user_id"))
            r["vendedor_email"] = logins.get(int(uid), "") if uid else ""
        return [map_cliente(r) for r in recs]

    # --- OrdenesVenta --------------------------------------------------------
    def changed_ordenes(self, since: datetime | None) -> list[OrdenVenta]:
        recs = self._search_read(
            self.MODEL_ORDEN,
            self._delta(since),
            [
                "id",
                "name",
                "partner_id",
                "date_order",
                "amount_total",
                "pricelist_id",
                "user_id",
                "invoice_status",
                "delivery_status",
                "state",
            ],
        )
        if not recs:
            return []
        so_ids = [int(r["id"]) for r in recs]
        so_names = [str(r["name"]) for r in recs]
        partner_ids = _ids_of(recs, "partner_id")
        uids = _ids_of(recs, "user_id")

        logins = self._user_logins(uids)
        entregas = self._fechas_entrega(so_ids)
        primeras = self._primeras_compras(partner_ids)
        facturas = self._facturas_por_origen(so_names)
        con_devolucion = self._ordenes_con_devolucion(so_ids)

        for r in recs:
            uid = _m2o_id(r.get("user_id"))
            r["vendedor_email"] = logins.get(int(uid), "") if uid else ""
            r["fecha_entrega"] = entregas.get(int(r["id"]))
            partner = _m2o_id(r.get("partner_id"))
            r["es_primera_compra"] = bool(partner) and primeras.get(int(partner)) == str(r["name"])
            r["factura_id"] = facturas.get(str(r["name"]))
            r["tiene_devolucion"] = int(r["id"]) in con_devolucion
        return [map_orden(r) for r in recs]

    def _ordenes_con_devolucion(self, so_ids: list[int]) -> set[int]:
        """Ids de SO con al menos un picking de devolución procesado (state == 'done')."""
        if not so_ids:
            return set()
        pickings = self._search_read(
            self.MODEL_PICKING,
            [
                ["state", "=", "done"],
                "|",
                ["return_id", "!=", False],
                ["picking_type_code", "=", "incoming"],
            ],
            ["id", "sale_id", "return_id", "origin"],
        )
        p_by_id = {p["id"]: p for p in pickings}
        res = set()
        for p in pickings:
            sid = _m2o_id(p.get("sale_id"))
            if sid and int(sid) in so_ids:
                res.add(int(sid))
            elif p.get("return_id"):
                ret_parent_id = (
                    p["return_id"][0] if isinstance(p["return_id"], (list, tuple)) else None
                )
                if ret_parent_id and ret_parent_id in p_by_id:
                    parent_p = p_by_id[ret_parent_id]
                    parent_sid = _m2o_id(parent_p.get("sale_id"))
                    if parent_sid and int(parent_sid) in so_ids:
                        res.add(int(parent_sid))
        return res

    def _fechas_entrega(self, so_ids: list[int]) -> dict[int, str]:
        """Mapa id de SO → fecha de entrega (date_done del despacho saliente)."""
        if not so_ids:
            return {}
        pickings = self._search_read(
            self.MODEL_PICKING,
            [["sale_id", "in", so_ids], ["picking_type_code", "=", "outgoing"]],
            ["sale_id", "date_done", "scheduled_date", "state"],
        )
        out: dict[int, str] = {}
        for p in pickings:
            sid = _m2o_id(p.get("sale_id"))
            if not sid:
                continue
            fecha = p.get("date_done") or p.get("scheduled_date")
            if not fecha:
                continue
            fecha_s = str(fecha)[:10]
            prev = out.get(int(sid))
            # Quedarse con la entrega más reciente si hay varias.
            if prev is None or fecha_s > prev:
                out[int(sid)] = fecha_s
        return out

    def _primeras_compras(self, partner_ids: set[int]) -> dict[int, str]:
        """Mapa partner → nombre de su SO más antigua (para es_primera_compra)."""
        if not partner_ids:
            return {}
        recs = self._search_read(
            self.MODEL_ORDEN,
            [["partner_id", "in", sorted(partner_ids)]],
            ["name", "partner_id", "date_order"],
        )
        primeras: dict[int, tuple[str, str]] = {}
        for r in recs:
            pid = _m2o_id(r.get("partner_id"))
            if not pid:
                continue
            fecha = str(r.get("date_order", ""))
            actual = primeras.get(int(pid))
            if actual is None or fecha < actual[1]:
                primeras[int(pid)] = (str(r["name"]), fecha)
        return {k: v[0] for k, v in primeras.items()}

    def _facturas_por_origen(self, so_names: list[str]) -> dict[str, str]:
        if not so_names:
            return {}
        recs = self._search_read(
            self.MODEL_MOVE,
            [
                ["invoice_origin", "in", so_names],
                ["move_type", "=", "out_invoice"],
                ["state", "=", "posted"],
            ],
            ["id", "invoice_origin"],
        )
        return {str(r["invoice_origin"]): str(r["id"]) for r in recs if r.get("invoice_origin")}

    # --- LineasOrden ---------------------------------------------------------
    def changed_lineas(self, since: datetime | None) -> list[LineaOrden]:
        domain = self._delta(since) + [["display_type", "=", False]]
        recs = self._search_read(
            self.MODEL_LINEA,
            domain,
            [
                "id",
                "order_id",
                "product_id",
                "product_uom_qty",
                "price_unit",
                "qty_delivered",
                "discount",
            ],
        )
        prod_ids = _ids_of(recs, "product_id")
        productos = self._productos(prod_ids)
        for r in recs:
            pid = _m2o_id(r.get("product_id"))
            marca, categoria = productos.get(int(pid), ("", "")) if pid else ("", "")
            r["marca"] = marca
            r["categoria"] = categoria
        return [map_linea(r) for r in recs]

    def _productos(self, prod_ids: set[int]) -> dict[int, tuple[str, str]]:
        """Mapa producto → (marca, categoría raíz). categoría = Comercial / Industrial."""
        recs = self._read(self.MODEL_PRODUCT, sorted(prod_ids), ["id", "brand_id", "categ_id"])
        out: dict[int, tuple[str, str]] = {}
        for r in recs:
            marca = _m2o_name(r.get("brand_id"))
            categoria_full = _m2o_name(r.get("categ_id"))
            categoria = ""
            if categoria_full:
                parts = [p.strip() for p in categoria_full.split("/")]
                if "Comercial" in parts:
                    categoria = "Comercial"
                elif "Industrial" in parts:
                    categoria = "Industrial"
                else:
                    # Fallback to first non-All part
                    non_all = [p for p in parts if p != "All"]
                    categoria = non_all[0] if non_all else parts[0]
            out[int(r["id"])] = (marca, categoria)
        return out

    # --- Pagos ---------------------------------------------------------------
    def changed_pagos(self, since: datetime | None) -> list[Pago]:
        domain = self._delta(since) + [
            ["payment_type", "=", "inbound"],
            ["state", "in", PAGO_ESTADOS_CONFIRMADOS],
            ["is_reconciled", "=", False],
        ]
        recs = self._search_read(
            self.MODEL_PAGO,
            domain,
            ["id", "partner_id", "amount", "currency_id", "journal_id", "date", "is_reconciled"],
        )
        partner_ids = _ids_of(recs, "partner_id")
        vendedores = self._vendedor_por_partner(partner_ids)
        for r in recs:
            pid = _m2o_id(r.get("partner_id"))
            r["vendedor_email"] = vendedores.get(int(pid), "") if pid else ""
        return [map_pago(r) for r in recs]

    def pagos_reconciliados_por_orden(
        self, so_names: list[str] | None = None
    ) -> dict[str, list[dict[str, Any]]]:  # pragma: no cover - red externa
        """Resuelve la cadena account.payment → invoice_ids →
        account.move.invoice_origin → sale.order.name.

        Retorna un mapa {so_name: [lista de pagos aplicados a sus facturas]}.
        Esto permite mostrar en el reporte los pagos que ya fueron reconciliados en Odoo
        aunque no tengan una Vinculacion manual en Google Sheets.

        Args:
            so_names: Si se provee, filtra solo los pagos asociados a esas órdenes.
                      Si es None, trae todos los pagos reconciliados.
        """
        # 1. Leer pagos reconciliados de Odoo
        pagos = self._search_read(
            self.MODEL_PAGO,
            [
                ["payment_type", "=", "inbound"],
                ["state", "in", PAGO_ESTADOS_CONFIRMADOS],
                ["is_reconciled", "=", True],
            ],
            ["id", "partner_id", "amount", "currency_id", "journal_id", "date", "invoice_ids"],
        )
        if not pagos:
            return {}

        # 2. Recolectar todos los invoice IDs referenciados por esos pagos
        all_inv_ids: list[int] = []
        pago_inv_map: dict[int, list[int]] = {}
        for p in pagos:
            inv_ids = p.get("invoice_ids") or []
            if isinstance(inv_ids, (list, tuple)) and inv_ids:
                all_inv_ids.extend(inv_ids)
                pago_inv_map[p["id"]] = list(inv_ids)

        if not all_inv_ids:
            return {}

        # 3. Leer facturas para obtener invoice_origin (SO name)
        facturas = self._read(
            self.MODEL_MOVE,
            list(set(all_inv_ids)),
            ["id", "invoice_origin", "move_type", "state"],
        )
        inv_to_so: dict[int, str] = {
            f["id"]: str(f.get("invoice_origin", "") or "").strip()
            for f in facturas
            if f.get("state") == "posted"
            and f.get("move_type") == "out_invoice"
            and f.get("invoice_origin")
        }

        # 4. Construir mapa so_name → [pagos]
        result: dict[str, list[dict[str, Any]]] = {}
        for p in pagos:
            inv_ids_for_p = pago_inv_map.get(p["id"], [])
            so_names_for_p = {inv_to_so[i] for i in inv_ids_for_p if i in inv_to_so}
            for so_name in so_names_for_p:
                if so_names is not None and so_name not in so_names:
                    continue
                result.setdefault(so_name, []).append(p)

        return result

    def _vendedor_por_partner(self, partner_ids: set[int]) -> dict[int, str]:
        if not partner_ids:
            return {}
        partners = self._read(self.MODEL_PARTNER, sorted(partner_ids), ["id", "user_id"])
        uids = {int(_m2o_id(p.get("user_id"))) for p in partners if _m2o_id(p.get("user_id"))}
        logins = self._user_logins(uids)
        out: dict[int, str] = {}
        for p in partners:
            uid = _m2o_id(p.get("user_id"))
            out[int(p["id"])] = logins.get(int(uid), "") if uid else ""
        return out


def _connect(config: OdooConfig) -> ExecuteFn:  # pragma: no cover - red externa
    import xmlrpc.client

    common = xmlrpc.client.ServerProxy(f"{config.url}/xmlrpc/2/common")
    uid = common.authenticate(config.db, config.username, config.password, {})
    if not uid:
        raise PermissionError("Autenticación Odoo fallida")
    models = xmlrpc.client.ServerProxy(f"{config.url}/xmlrpc/2/object")

    def execute(model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        return models.execute_kw(config.db, uid, config.password, model, method, args, kwargs)

    return execute
