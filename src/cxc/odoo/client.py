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
from ..models import Cliente, Entrega, Factura, LineaOrden, Moneda, OrdenVenta, Pago, Producto

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
    """``name`` sale ``False`` (booleano de Odoo, NUNCA como ``str(False)`` --

    bug real detectado en S00768: el partner de la orden es un subcontacto
    -- ej. una dirección de entrega, ``type != "contact"`` -- sin nombre
    propio) para contactos hijos/direcciones sin nombre propio. En esos
    casos se usa ``commercial_partner_id`` (campo nativo de Odoo que
    resuelve SIEMPRE a la entidad comercial raíz -- la empresa/contacto
    principal, o el mismo partner si ya es la raíz), igual que hace Odoo en
    contabilidad para agrupar subcontactos bajo su empresa.
    """
    nombre = str(rec.get("name") or "").strip()
    if not nombre:
        nombre = _m2o_name(rec.get("commercial_partner_id"))
    return Cliente(
        cliente_id=str(rec["id"]),
        nombre=nombre,
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


def parse_payment_term_days(t_name: str) -> int:
    """Días de crédito otorgados según el nombre del payment term de Odoo.

    Usado para poblar ``OrdenVenta.dias_credito`` (Recompra -- ventana de
    días de crédito + gracia -- y la alerta de días de crédito máximo por
    volumen). Misma heurística de regex que ``web/app.py`` usa para el
    reporte de CxC (nombre suele ser "30 días"/"Contado"/"Immediate
    Payment")."""
    if not t_name:
        return 0
    t_low = t_name.lower().strip()
    if "immediate" in t_low or "contado" in t_low:
        return 0
    m = re.search(r"(\d+)\s*(dias|días|days|day|día)", t_low)
    return int(m.group(1)) if m else 0


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
        dias_credito=int(rec.get("dias_credito") or 0),
    )


def map_factura_espejo(rec: dict[str, Any]) -> Factura:
    move_type = str(rec.get("move_type", "") or "out_invoice")
    debit_origin_id = _m2o_id(rec.get("debit_origin_id"))
    reversed_entry_id = _m2o_id(rec.get("reversed_entry_id"))
    # Verificado en vivo (ver _leer_notas_debito_odoo en cxc.web.app): las
    # notas de débito reales en Odoo 18 traen move_type == "out_debit"
    # (journal dedicado), NUNCA "out_invoice" -- debit_origin_id sigue
    # apuntando a la factura original en ambos casos.
    es_nota_debito = move_type == "out_debit"
    factura_origen_id = debit_origin_id or reversed_entry_id or None
    currency = rec.get("currency_id")
    moneda = (
        currency[1] if isinstance(currency, list | tuple) and len(currency) > 1 else "USD"
    )
    fecha_raw = rec.get("invoice_date") or rec.get("date")
    # fecha NOT NULL en el espejo -- invoice_date/date casi siempre vienen
    # seteados (Odoo los defaultea a hoy), pero un draft recién creado
    # podría no tenerlos todavía; hoy como fallback conservador en vez de
    # violar la restricción NOT NULL de la tabla.
    fecha = _to_date(fecha_raw) or date.today()
    return Factura(
        factura_id=str(rec["id"]),
        numero=str(rec.get("name", "") or ""),
        so_id=str(rec["invoice_origin"]) if rec.get("invoice_origin") else None,
        move_type=move_type,
        es_nota_debito=es_nota_debito,
        fecha=fecha,
        moneda=str(moneda),
        monto_total=_dec(rec.get("amount_total")),
        monto_sin_impuestos=_dec(rec.get("amount_untaxed")),
        estado=str(rec.get("state", "") or "draft"),
        factura_origen_id=factura_origen_id or None,
        monto_total_signed_usd=_dec(rec.get("amount_total_signed_usd")),
        monto_sin_impuestos_signed_usd=_dec(rec.get("amount_untaxed_signed_usd")),
    )


def map_entrega_espejo(rec: dict[str, Any]) -> Entrega:
    tipo = str(rec.get("picking_type_code", "") or "outgoing")
    return_id = _m2o_id(rec.get("return_id"))
    es_devolucion = bool(return_id) or tipo == "incoming"
    return Entrega(
        entrega_id=str(rec["id"]),
        so_id=str(_m2o_name(rec.get("sale_id"))) or None,
        tipo=tipo,
        fecha=_to_date(rec.get("date_done")),
        estado=str(rec.get("state", "") or "draft"),
        es_devolucion=es_devolucion,
        entrega_origen_id=return_id or None,
    )


def map_producto_espejo(rec: dict[str, Any]) -> Producto:
    # "product_volume" (custom, litros de producto), NO "volume" (genérico
    # de logística/empaque de Odoo) -- verificado en vivo (agosto 2026,
    # producto 1034: volume=6.0 vs product_volume=5.67, campos genuinamente
    # distintos) que _get_ventas_sync/motor de descuentos usan
    # product_volume, no volume, para el cálculo de litros.
    volumen = _dec(rec.get("product_volume"))
    peso = _dec(rec.get("weight"))
    marca_info = rec.get("brand_id")
    marca = marca_info[1] if isinstance(marca_info, list | tuple) and len(marca_info) > 1 else ""
    return Producto(
        producto_id=str(rec["id"]),
        codigo=str(rec.get("default_code", "") or ""),
        nombre=str(rec.get("name", "") or ""),
        marca=str(marca),
        volumen=volumen,
        peso=peso,
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
        subcategoria=str(rec.get("subcategoria", "") or ""),
        presentacion_odoo=str(rec.get("presentacion_odoo", "") or ""),
        nombre=str(rec.get("name", "") or ""),
        subtotal=_dec(rec.get("price_subtotal")),
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

    NO confundir con ``map_factura_espejo`` (más abajo): esta función es un
    helper de reconciliación puntual (usado por ``OdooFacturasReader``),
    aquella mapea el espejo inmutable completo de ``account.move`` para el
    sync incremental (``Factura`` de ``cxc.models``).
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

    @abstractmethod
    def lineas_vigentes_por_orden(self, so_ids: list[str]) -> dict[str, set[str]]: ...

    # Facturas (Fase 0 del plan de consolidación de fuentes, agosto 2026):
    # NO @abstractmethod -- default "sin cambios" en vez de forzar a cada
    # fake/implementación de test a soportarlo. Solo OdooXmlRpcReader lo
    # sobrescribe con la lectura real.
    def changed_facturas(self, since: datetime | None) -> list[Factura]:
        return []

    # Entregas (Fase 0 del plan de consolidación de fuentes, agosto 2026):
    # mismo criterio que changed_facturas -- default "sin cambios".
    def changed_entregas(self, since: datetime | None) -> list[Entrega]:
        return []

    # Catálogo (Fase 0, agosto 2026): mismo criterio, default "sin cambios".
    def changed_catalogo(self, since: datetime | None) -> list[Producto]:
        return []


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
        # active_test=False: un contacto archivado en Odoo puede seguir
        # siendo referenciado por órdenes históricas (sale.order.partner_id
        # no se limpia al archivar el contacto) -- sin esto, un sync
        # completo (since=None) puede traer una orden cuyo cliente nunca
        # se sincroniza, violando la FK ordenes_venta.cliente_id.
        recs = self._execute(
            self.MODEL_PARTNER,
            "search_read",
            [self._delta(since)],
            {
                "fields": ["id", "name", "user_id", "wh_iva_agent", "wh_iva_rate",
                           "commercial_partner_id"],
                "context": {"active_test": False},
            },
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
                "payment_term_id",
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
            r["dias_credito"] = parse_payment_term_days(_m2o_name(r.get("payment_term_id")))
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
                    p["return_id"][0] if isinstance(p["return_id"], list | tuple) else None
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

    # --- Facturas (espejo inmutable, Fase 0 del plan de consolidación de
    # fuentes) -----------------------------------------------------------------
    def changed_facturas(self, since: datetime | None) -> list[Factura]:
        """Facturas/NC/ND de cliente (``account.move``, ``move_type`` de

        salida) cambiadas desde ``since``. Solo contenido inmutable --
        ``amount_residual`` NUNCA se lee aquí a propósito (varía con cada
        pago aplicado; eso sigue siendo dominio de Cobranza, en vivo). Se
        incluyen ``draft`` para que el espejo refleje una factura tan pronto
        se crea, no solo cuando se publica -- ``estado`` queda expuesto para
        que el consumidor decida si la usa o no.
        """
        recs = self._search_read(
            self.MODEL_MOVE,
            self._delta(since)
            # "out_debit" incluido -- ver map_factura_espejo: las notas de
            # débito reales de Odoo 18 usan ese move_type, no "out_invoice"
            # (bug real detectado y corregido en agosto 2026).
            + [["move_type", "in", ["out_invoice", "out_refund", "out_debit"]]],
            [
                "id",
                "name",
                "invoice_origin",
                "move_type",
                "invoice_date",
                "date",
                "currency_id",
                "amount_total",
                "amount_untaxed",
                "amount_total_signed_usd",
                "amount_untaxed_signed_usd",
                "state",
                "reversed_entry_id",
                "debit_origin_id",
            ],
        )
        return [map_factura_espejo(r) for r in recs]

    # --- Entregas (espejo inmutable, Fase 0 del plan de consolidación de
    # fuentes) -----------------------------------------------------------------
    def changed_entregas(self, since: datetime | None) -> list[Entrega]:
        """Despachos/devoluciones (``stock.picking``) cambiados desde

        ``since``, por ``write_date`` PROPIO del picking (ver docstring de
        ``Entrega``) -- captura devoluciones procesadas después de la
        entrega original aunque la SO padre no se haya vuelto a tocar.
        """
        recs = self._search_read(
            self.MODEL_PICKING,
            self._delta(since)
            + [["picking_type_code", "in", ["outgoing", "incoming"]]],
            [
                "id",
                "sale_id",
                "picking_type_code",
                "date_done",
                "state",
                "return_id",
            ],
        )
        return [map_entrega_espejo(r) for r in recs]

    # --- Catálogo (espejo, Fase 0 del plan de consolidación de fuentes) -------
    def changed_catalogo(self, since: datetime | None) -> list[Producto]:
        """Productos (``product.product``) cambiados desde ``since`` --

        volumen/peso/marca, referencia de baja frecuencia de cambio usada
        hoy para litros y otras columnas que varias páginas piden en vivo
        por separado (Ventas, Reporte Diario). ``product_volume`` (NO
        ``volume``, ver ``map_producto_espejo``) es el campo real que usa
        ese cálculo.

        ``["active", "in", [True, False]]`` -- verificado en vivo (agosto
        2026): ``search_read`` excluye productos archivados por defecto,
        a diferencia del ``read`` por id explícito que usa la consulta en
        vivo de litros (``_get_ventas_sync``) -- sin este filtro, un
        producto descontinuado referenciado en una orden histórica
        desaparece silenciosamente del espejo y su litraje queda en 0.
        """
        recs = self._search_read(
            self.MODEL_PRODUCT,
            self._delta(since) + [["active", "in", [True, False]]],
            ["id", "default_code", "name", "product_volume", "weight", "brand_id"],
        )
        return [map_producto_espejo(r) for r in recs]

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
                "name",
                "price_subtotal",
            ],
        )
        prod_ids = _ids_of(recs, "product_id")
        productos = self._productos(prod_ids)
        for r in recs:
            pid = _m2o_id(r.get("product_id"))
            marca, categoria, subcategoria, presentacion = (
                productos.get(int(pid), ("", "", "", "")) if pid else ("", "", "", "")
            )
            r["marca"] = marca
            r["categoria"] = categoria
            r["subcategoria"] = subcategoria
            r["presentacion_odoo"] = presentacion
        return [map_linea(r) for r in recs]

    def lineas_vigentes_por_orden(self, so_ids: list[str]) -> dict[str, set[str]]:
        """Ids de línea ACTUALMENTE activas en Odoo para cada SO dada.

        A diferencia de ``changed_lineas`` (delta por ``write_date``), esta
        consulta no tiene filtro de fecha -- una línea BORRADA en Odoo nunca
        aparece en un delta (no hay ``write_date`` de un registro que ya no
        existe), así que el espejo local nunca se enteraba de la eliminación
        y la línea quedaba fantasma para siempre (hallazgo real orden
        S00792, agosto 2026: producto sacado de la orden seguía apareciendo
        en el teórico). El caller usa el resultado para borrar del espejo
        local cualquier línea que ya no esté en este set.
        """
        if not so_ids:
            return {}
        recs = self._search_read(
            self.MODEL_LINEA,
            [["order_id.name", "in", so_ids], ["display_type", "=", False]],
            ["id", "order_id"],
        )
        vigentes: dict[str, set[str]] = {so_id: set() for so_id in so_ids}
        for r in recs:
            so_name = _m2o_name(r.get("order_id"))
            if so_name:
                vigentes.setdefault(so_name, set()).add(str(r["id"]))
        return vigentes

    def _productos(self, prod_ids: set[int]) -> dict[int, tuple[str, str, str, str]]:
        """Mapa producto → (marca, categoría raíz, subcategoría, presentación).

        categoría raíz = Comercial / Industrial (1er nivel del path de
        ``categ_id``). subcategoría = 2do nivel (ej. "Comercial/Elite" →
        "Elite"). presentación = contenido entre paréntesis al final del
        NOMBRE del producto (ej. "ELITE API SP SAE 0W-20 (1x6)" → "1X6";
        "... (Tambor)" → "TAMBOR") -- es la presentación/envase real que
        usa el negocio, vacía si el producto no la trae en el nombre (ej.
        ítems que no son de venta de lubricante: fletes, filtros, etc.).
        Todas usadas para afinar el match de reglas de descuento por debajo
        de Comercial/Industrial (ver engine/discounts.py).
        """
        recs = self._read(
            self.MODEL_PRODUCT, sorted(prod_ids), ["id", "name", "brand_id", "categ_id"]
        )
        out: dict[int, tuple[str, str, str, str]] = {}
        for r in recs:
            marca = _m2o_name(r.get("brand_id"))
            categoria_full = _m2o_name(r.get("categ_id"))
            categoria = ""
            subcategoria = ""
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
                if categoria in parts:
                    idx = parts.index(categoria)
                    if idx + 1 < len(parts):
                        subcategoria = parts[idx + 1]
            presentacion = ""
            m = re.search(r"\(([^)]*)\)\s*$", str(r.get("name") or "").strip())
            if m:
                presentacion = m.group(1).strip().upper()
            out[int(r["id"])] = (marca, categoria, subcategoria, presentacion)
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
        # 1. Leer pagos reconciliados de Odoo. OJO: "invoice_ids" (Invoices)
        # es el campo que llena el wizard "Registrar Pago" -- en pagos
        # reconciliados por matching de banco/manual (el caso normal aquí)
        # queda SIEMPRE vacío. El campo que sí refleja la reconciliación
        # real es "reconciled_invoice_ids" (Reconciled Invoices).
        # Verificado en vivo: de 673 pagos con is_reconciled=True, 0 tenían
        # invoice_ids poblado y los 673 tenían reconciled_invoice_ids -- la
        # tabla de "Pagos Conciliados" quedaba siempre vacía.
        pagos = self._search_read(
            self.MODEL_PAGO,
            [
                ["payment_type", "=", "inbound"],
                ["state", "in", PAGO_ESTADOS_CONFIRMADOS],
                ["is_reconciled", "=", True],
            ],
            [
                "id",
                "partner_id",
                "amount",
                "currency_id",
                "journal_id",
                "date",
                "reconciled_invoice_ids",
            ],
        )
        if not pagos:
            return {}

        # 2. Recolectar todos los invoice IDs referenciados por esos pagos
        all_inv_ids: list[int] = []
        pago_inv_map: dict[int, list[int]] = {}
        for p in pagos:
            inv_ids = p.get("reconciled_invoice_ids") or []
            if isinstance(inv_ids, list | tuple) and inv_ids:
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
