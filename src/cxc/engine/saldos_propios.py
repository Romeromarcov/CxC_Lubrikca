"""Motor de Reportes 100% sobre Base de Datos propia de CxC (sin consultar Odoo).

Calcula saldos reales por cliente, por vendedor, antigüedad FIFO y estado de cuenta
exclusivamente con datos confirmados en las tablas locales de CxC:

    Saldo Deudor = Ventas Confirmadas − Descuentos Confirmados − Pagos Confirmados

Incluye ventas espejo de Odoo + pedidos ``ENVIADO_ODOO`` aún no re-sincronizados,
descuentos aprobados en sistema + Notas de Crédito publicadas, pagos espejo de Odoo
(convertidos a USD con tasas BCV locales cuando son VES), pagos Zelle de Terceros
confirmados y cobros de efectivo confirmados en CxC (CxC-first).
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..ajustes_descuentos import (
    ServicioAjustesDescuentos,
    aplicar_ajustes_a_descuentos_orden,
)
from ..decimal_utils import q2
from ..models import (
    EstadoBandeja,
    EstadoVinculacion,
    OrdenVenta,
    TipoTasa,
)
from ..repositories import Repository
from .rutas_pago import RUTA_BCV, RUTA_BINANCE, normalizar_ruta, ruta_de_lista

ESTADOS_ORDEN_CONFIRMADA = ("sale", "done")
ESTADOS_VINC_CONFIRMADA = frozenset(
    {EstadoVinculacion.APROBADO, EstadoVinculacion.FACTURADO, EstadoVinculacion.CONCILIADO}
)
TRAMOS_MORA = ("vigente", "1_30", "31_60", "61_90", "mas_90")


def _norm_nombre(txt: str) -> str:
    nfd = unicodedata.normalize("NFD", (txt or "").strip().lower())
    limpio = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return " ".join(limpio.split())


def _es_activo(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    return str(val or "").strip().lower() in ("true", "1", "yes", "si", "sí")


def _dec(val: Any) -> Decimal:
    try:
        return Decimal(str(val or "0"))
    except Exception:
        return Decimal("0")


def _f(val: Decimal | float | int) -> float:
    return float(q2(_dec(val)))


def _indice_tasas_bcv_y_eur_local(
    repo: Repository,
) -> tuple[dict[date, Decimal], Decimal, dict[date, Decimal], Decimal]:
    """Construye mapas ``fecha -> tasa_bcv_usd`` y ``fecha -> tasa_bcv_euro`` desde BD CxC."""
    por_fecha: dict[date, Decimal] = {}
    eur_por_fecha: dict[date, Decimal] = {}
    ultima_tasa = Decimal("0")
    ultima_eur = Decimal("0")
    ultima_fecha: date | None = None
    ultima_eur_fecha: date | None = None

    try:
        hist = repo.all_tasas_historicas_auditoria()
    except Exception:
        hist = []
    for row in hist:
        f_raw = str(row.get("fecha") or "")[:10]
        t_val = _dec(row.get("tasa_bcv_usd"))
        e_val = _dec(row.get("tasa_bcv_eur") or row.get("tasa_bcv_euro"))
        if not f_raw:
            continue
        try:
            f_d = date.fromisoformat(f_raw)
        except ValueError:
            continue
        if t_val > 0:
            por_fecha[f_d] = t_val
            if ultima_fecha is None or f_d >= ultima_fecha:
                ultima_fecha = f_d
                ultima_tasa = t_val
        if e_val > 0:
            eur_por_fecha[f_d] = e_val
            if ultima_eur_fecha is None or f_d >= ultima_eur_fecha:
                ultima_eur_fecha = f_d
                ultima_eur = e_val

    try:
        series = repo.all_serie_tasas()
    except Exception:
        series = []
    for s in sorted(series, key=lambda x: x.timestamp):
        f_d = s.timestamp.date()
        if s.tasa_bcv and s.tasa_bcv > 0:
            por_fecha[f_d] = Decimal(str(s.tasa_bcv))
            if ultima_fecha is None or f_d >= ultima_fecha:
                ultima_fecha = f_d
                ultima_tasa = Decimal(str(s.tasa_bcv))
        t_eu = _dec(getattr(s, "tasa_bcv_euro", 0))
        if t_eu > 0:
            eur_por_fecha[f_d] = t_eu
            if ultima_eur_fecha is None or f_d >= ultima_eur_fecha:
                ultima_eur_fecha = f_d
                ultima_eur = t_eu

    return por_fecha, ultima_tasa, eur_por_fecha, ultima_eur


def _indice_tasas_bcv_local(repo: Repository) -> tuple[dict[date, Decimal], Decimal]:
    """Construye mapa ``fecha → tasa_bcv`` y tasa fallback más reciente solo desde BD CxC."""
    por_fecha, ultima_tasa, _eur, _u_eur = _indice_tasas_bcv_y_eur_local(repo)
    return por_fecha, ultima_tasa


def _tasa_bcv_en_fecha(
    fecha_doc: date, por_fecha: dict[date, Decimal], fallback: Decimal
) -> Decimal:
    if fecha_doc in por_fecha:
        return por_fecha[fecha_doc]
    anteriores = [f for f in por_fecha if f <= fecha_doc]
    if anteriores:
        return por_fecha[max(anteriores)]
    if por_fecha:
        return por_fecha[min(por_fecha)]
    return fallback


def _a_usd_local(
    monto: Decimal,
    moneda: str,
    fecha_doc: date,
    por_fecha: dict[date, Decimal],
    fallback_tasa: Decimal,
    *,
    eur_por_fecha: dict[date, Decimal] | None = None,
    fallback_eur: Decimal = Decimal("0"),
    usd_bcv_por_fecha: dict[date, Decimal] | None = None,
    fallback_usd_bcv: Decimal = Decimal("0"),
) -> Decimal:
    mon = (moneda or "USD").strip().upper()
    if mon == "USD" or monto <= 0:
        return q2(monto)
    if mon == "EUR":
        t_usd = _tasa_bcv_en_fecha(
            fecha_doc, usd_bcv_por_fecha if usd_bcv_por_fecha is not None else por_fecha,
            fallback_usd_bcv if fallback_usd_bcv > 0 else fallback_tasa,
        )
        t_eur = (
            _tasa_bcv_en_fecha(fecha_doc, eur_por_fecha or {}, fallback_eur)
            if (eur_por_fecha is not None or fallback_eur > 0)
            else Decimal("0")
        )
        if t_eur > 0 and t_usd > 0:
            return q2((monto * t_eur) / t_usd)
        return q2(monto)
    tasa = _tasa_bcv_en_fecha(fecha_doc, por_fecha, fallback_tasa)
    if tasa <= 0:
        return Decimal("0.00")
    return q2(monto / tasa)


def _indice_tasas_binance_local(repo: Repository) -> tuple[dict[date, Decimal], Decimal]:
    """Construye mapa ``fecha -> tasa_binance`` y tasa fallback más reciente solo desde BD CxC."""
    por_fecha: dict[date, Decimal] = {}
    ultima_tasa = Decimal("0")
    ultima_fecha: date | None = None

    try:
        hist = repo.all_tasas_historicas_auditoria()
    except Exception:
        hist = []
    for row in hist:
        f_raw = str(row.get("fecha") or "")[:10]
        t_val = _dec(
            row.get("tasa_binance")
            or row.get("tasa_binance_tarde")
            or row.get("tasa_binance_manana")
        )
        if f_raw and t_val > 0:
            try:
                f_d = date.fromisoformat(f_raw)
                por_fecha[f_d] = t_val
                if ultima_fecha is None or f_d >= ultima_fecha:
                    ultima_fecha = f_d
                    ultima_tasa = t_val
            except ValueError:
                continue

    try:
        series = repo.all_serie_tasas()
    except Exception:
        series = []
    for s in sorted(series, key=lambda x: x.timestamp):
        if s.tasa_binance and s.tasa_binance > 0:
            f_d = s.timestamp.date()
            por_fecha[f_d] = Decimal(str(s.tasa_binance))
            if ultima_fecha is None or f_d >= ultima_fecha:
                ultima_fecha = f_d
                ultima_tasa = Decimal(str(s.tasa_binance))

    return por_fecha, ultima_tasa


def _a_usd_por_ruta(
    monto: Decimal,
    moneda: str,
    fecha_doc: date,
    ruta: str,
    por_fecha_bcv: dict[date, Decimal],
    fallback_bcv: Decimal,
    por_fecha_binance: dict[date, Decimal],
    fallback_binance: Decimal,
) -> Decimal:
    if normalizar_ruta(ruta) == RUTA_BINANCE and (por_fecha_binance or fallback_binance > 0):
        return _a_usd_local(monto, moneda, fecha_doc, por_fecha_binance, fallback_binance)
    return _a_usd_local(monto, moneda, fecha_doc, por_fecha_bcv, fallback_bcv)


def _cargar_mapeo_listas_repo(repo: Repository) -> dict[str, Any]:
    try:
        raw = repo.get_config("pricelist_mapeo_unificado") if hasattr(repo, "get_config") else None
        if raw and isinstance(raw, str):
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
    except Exception:
        pass
    return {}


def _so_ids_csv(so_id_raw: str | None) -> list[str]:
    return [s.strip() for s in str(so_id_raw or "").split(",") if s.strip()]


def _tramo_mora(dias_mora: int) -> str:
    if dias_mora <= 0:
        return "vigente"
    if dias_mora <= 30:
        return "1_30"
    if dias_mora <= 60:
        return "31_60"
    if dias_mora <= 90:
        return "61_90"
    return "mas_90"


def fifo_antiguedad(
    items: Sequence[tuple[date, date, Decimal, str]],
    credito_total: Decimal,
    hoy_d: date,
) -> tuple[dict[str, Decimal], int]:
    """Reparte ``credito_total`` FIFO sobre obligaciones y clasifica mora remanente."""
    credito_rest = q2(credito_total)
    tramos: dict[str, Decimal] = dict.fromkeys(TRAMOS_MORA, Decimal("0"))
    max_dias_mora = 0
    for _f_ord, f_venc, m_ord, _ref in sorted(items, key=lambda x: (x[0], x[3])):
        cubierto = min(m_ord, credito_rest)
        credito_rest = q2(credito_rest - cubierto)
        rem = q2(m_ord - cubierto)
        if rem <= 0:
            continue
        dias_m = (hoy_d - f_venc).days
        if dias_m > max_dias_mora:
            max_dias_mora = dias_m
        tr = _tramo_mora(dias_m)
        tramos[tr] = q2(tramos[tr] + rem)
    return tramos, max_dias_mora


# Aliases públicos compartidos con ``saldos_cxp.py``
norm_nombre = _norm_nombre
dec = _dec
round_usd = _f
indice_tasas_bcv_local = _indice_tasas_bcv_local
tasa_bcv_en_fecha = _tasa_bcv_en_fecha
a_usd_local = _a_usd_local
tramo_mora = _tramo_mora


def _bucket_cliente(cid: str, nombre: str, vendedor: str) -> dict[str, Any]:
    return {
        "cliente_id": cid,
        "cliente_nombre": nombre or f"Cliente #{cid}",
        "vendedor_email": vendedor,
        "ventas_confirmadas_usd": Decimal("0"),
        "ventas_bcv_usd": Decimal("0"),
        "ventas_binance_usd": Decimal("0"),
        "descuentos_sistema_usd": Decimal("0"),
        "notas_credito_usd": Decimal("0"),
        "descuentos_confirmados_usd": Decimal("0"),
        "pagos_odoo_usd": Decimal("0"),
        "pagos_bcv_usd": Decimal("0"),
        "pagos_binance_usd": Decimal("0"),
        "pagos_zelle_tercero_usd": Decimal("0"),
        "pagos_efectivo_cxc_usd": Decimal("0"),
        "pagos_otros_cxc_usd": Decimal("0"),
        "pagos_confirmados_usd": Decimal("0"),
        "n_ordenes": 0,
        "n_pagos": 0,
        "ordenes_fifo": [],  # list[tuple[date, date, Decimal, str]]
        "movimientos": [],
    }


def _recolectar_estado_bd(
    repo: Repository,
    *,
    docs_comerciales: Sequence[Any] = (),
    docs_operativos: Sequence[Any] = (),
    ajustes_descuentos_por_so: dict[str, list[dict[str, Any]]] | None = None,
    hoy: date | None = None,
) -> dict[str, Any]:
    hoy_d = hoy or date.today()
    por_fecha_tasa, fallback_tasa, eur_por_fecha, fallback_eur = _indice_tasas_bcv_y_eur_local(
        repo
    )
    por_fecha_binance, fallback_binance = _indice_tasas_binance_local(repo)
    mapeo_listas = _cargar_mapeo_listas_repo(repo)
    ruta_por_so: dict[str, str] = {}

    if ajustes_descuentos_por_so is None:
        eng_repo = getattr(repo, "_engine", None)
        if eng_repo is not None:
            ajustes_descuentos_por_so = ServicioAjustesDescuentos(eng_repo).listar_activos_por_so()
        else:
            ajustes_descuentos_por_so = {}

    clientes_map = {c.cliente_id: c for c in repo.all_clientes()}
    nombre_a_cid = {_norm_nombre(c.nombre): c.cliente_id for c in clientes_map.values() if c.nombre}

    buckets: dict[str, dict[str, Any]] = {}

    def ensure_bucket(cid: str, nombre: str = "", vendedor: str = "") -> dict[str, Any]:
        c_obj = clientes_map.get(cid)
        nom = nombre or (c_obj.nombre if c_obj else "")
        vend = vendedor or (c_obj.vendedor_email if c_obj else "")
        b = buckets.get(cid)
        if b is None:
            b = _bucket_cliente(cid, nom, vend)
            buckets[cid] = b
        else:
            if nom and b["cliente_nombre"].startswith("Cliente #"):
                b["cliente_nombre"] = nom
            if vend and not b["vendedor_email"]:
                b["vendedor_email"] = vend
        return b

    def resolver_cid(
        partner_odoo_id: Any, nombre_contraparte: str, vendedor: str = ""
    ) -> tuple[str, str]:
        if partner_odoo_id is not None and str(partner_odoo_id).strip():
            cid = str(partner_odoo_id)
            return cid, ensure_bucket(cid, nombre_contraparte, vendedor)["cliente_nombre"]
        norm = _norm_nombre(nombre_contraparte)
        if norm and norm in nombre_a_cid:
            cid = nombre_a_cid[norm]
            return cid, ensure_bucket(cid, nombre_contraparte, vendedor)["cliente_nombre"]
        cid_sint = f"cxc:{norm or 'sin_nombre'}"
        return cid_sint, ensure_bucket(cid_sint, nombre_contraparte, vendedor)["cliente_nombre"]

    # 1. Ventas confirmadas desde espejo Odoo (ordenes_venta)
    ordenes_all: list[OrdenVenta] = list(repo.all_ordenes())
    ordenes_por_so: dict[str, OrdenVenta] = {o.so_id: o for o in ordenes_all}
    for o in ordenes_all:
        if o.estado_orden not in ESTADOS_ORDEN_CONFIRMADA:
            continue
        b = ensure_bucket(o.cliente_id, "", o.vendedor_email)
        monto = q2(_dec(o.monto_total))
        if monto <= 0:
            continue
        ruta_o = ruta_de_lista(o.lista_precios, mapeo_listas)
        ruta_por_so[o.so_id] = ruta_o
        f_base = o.fecha_entrega or o.fecha
        f_venc = f_base + timedelta(days=int(o.dias_credito or 0))
        b["ventas_confirmadas_usd"] += monto
        if ruta_o == RUTA_BINANCE:
            b["ventas_binance_usd"] += monto
        else:
            b["ventas_bcv_usd"] += monto
        b["n_ordenes"] += 1
        b["ordenes_fifo"].append((o.fecha, f_venc, monto, o.so_id))
        b["movimientos"].append(
            {
                "fecha": o.fecha.isoformat(),
                "tipo": "venta_odoo",
                "etiqueta": "Venta Confirmada (Odoo)",
                "referencia": o.so_id,
                "fuente": "odoo",
                "debito_usd": float(monto),
                "credito_usd": 0.0,
            }
        )

    # 1b. Pedidos nacidos en CxC enviados a Odoo todavía ausentes del espejo
    for dc in docs_comerciales:
        if getattr(dc, "tipo", "") != "pedido" or getattr(dc, "estado", "") != "ENVIADO_ODOO":
            continue
        so_name = str(getattr(dc, "odoo_order_name", "") or "").strip()
        if so_name and so_name in ordenes_por_so:
            continue
        monto = q2(_dec(getattr(dc, "total_con_iva", 0)))
        if monto <= 0:
            continue
        cid, _ = resolver_cid(
            getattr(dc, "partner_odoo_id", None),
            str(getattr(dc, "cliente_nombre", "") or ""),
            str(getattr(dc, "vendedor_email", "") or ""),
        )
        b = buckets[cid]
        f_ent = getattr(dc, "fecha_entrega", None)
        f_cre = getattr(dc, "creado_en", None)
        f_doc = f_ent or (f_cre.date() if isinstance(f_cre, datetime) else hoy_d)
        ref_doc = so_name or str(getattr(dc, "numero", ""))
        ruta_dc = normalizar_ruta(getattr(dc, "ruta_pago", "")) or ruta_de_lista(
            getattr(dc, "lista_precios_id", None),
            mapeo_listas,
            lista_nombre=str(getattr(dc, "lista_precios_nombre", "") or ""),
            moneda_lista=str(getattr(dc, "moneda_lista", "") or ""),
        )
        if ref_doc:
            ruta_por_so[ref_doc] = ruta_dc
        if getattr(dc, "numero", None):
            ruta_por_so[str(dc.numero)] = ruta_dc
        b["ventas_confirmadas_usd"] += monto
        if ruta_dc == RUTA_BINANCE:
            b["ventas_binance_usd"] += monto
        else:
            b["ventas_bcv_usd"] += monto
        b["n_ordenes"] += 1
        f_venc_dc = f_doc + timedelta(days=int(getattr(dc, "dias_credito", 0) or 0))
        b["ordenes_fifo"].append((f_doc, f_venc_dc, monto, ref_doc))
        b["movimientos"].append(
            {
                "fecha": f_doc.isoformat(),
                "tipo": "venta_cxc",
                "etiqueta": "Pedido CxC enviado a Odoo",
                "referencia": ref_doc,
                "fuente": "cxc",
                "debito_usd": float(monto),
                "credito_usd": 0.0,
            }
        )

    # 2. Descuentos confirmados (sistema aprobados + bandeja aprobada/facturada + NC posted)
    no_otorgados_fn = getattr(repo, "all_descuentos_no_otorgados", None)
    try:
        no_otorgados = set((no_otorgados_fn() or {}).keys()) if callable(no_otorgados_fn) else set()
    except Exception:
        no_otorgados = set()

    so_con_desc_aprobado: set[str] = set()
    for d_row in repo.all_descuentos_sistema_aprobados():
        if not _es_activo(d_row.get("activo", True)):
            continue
        so_id = str(d_row.get("so_id") or "").strip()
        if not so_id or so_id in no_otorgados:
            continue
        ord_obj = ordenes_por_so.get(so_id)
        if ord_obj is None or ord_obj.estado_orden not in ESTADOS_ORDEN_CONFIRMADA:
            continue
        monto_d = q2(_dec(d_row.get("monto")))
        if monto_d <= 0:
            continue
        so_con_desc_aprobado.add(so_id)
        b = ensure_bucket(ord_obj.cliente_id, "", ord_obj.vendedor_email)
        b["descuentos_sistema_usd"] += monto_d
        b["descuentos_confirmados_usd"] += monto_d
        b["movimientos"].append(
            {
                "fecha": ord_obj.fecha.isoformat(),
                "tipo": "descuento_aprobado",
                "etiqueta": "Descuento Aprobado en Sistema",
                "referencia": so_id,
                "fuente": "cxc",
                "debito_usd": 0.0,
                "credito_usd": float(monto_d),
            }
        )

    try:
        facturas_all = repo.all_facturas()
    except NotImplementedError:
        facturas_all = []
    so_con_nc_odoo: set[str] = set()
    for fac in facturas_all:
        if fac.move_type != "out_refund" or fac.estado != "posted":
            continue
        if q2(abs(_dec(fac.monto_total_signed_usd))) > 0:
            for s in _so_ids_csv(fac.so_id):
                if s in ordenes_por_so:
                    so_con_nc_odoo.add(s)

    so_con_especiales_aplicados: set[str] = set()
    for bf in repo.all_bandeja():
        if bf.so_id in so_con_desc_aprobado or bf.so_id in no_otorgados:
            continue
        if bf.estado not in (EstadoBandeja.APROBADO, EstadoBandeja.FACTURADO):
            continue
        if bf.estado == EstadoBandeja.FACTURADO and bf.so_id in so_con_nc_odoo:
            continue
        ord_obj = ordenes_por_so.get(bf.so_id)
        if ord_obj is None or ord_obj.estado_orden not in ESTADOS_ORDEN_CONFIRMADA:
            continue
        ajustes_so = ajustes_descuentos_por_so.get(bf.so_id, [])
        if ajustes_so:
            base_aj = bf.precio_base_calculado or ord_obj.monto_total
            calc_aj = aplicar_ajustes_a_descuentos_orden(bf.descuentos_detalle, base_aj, ajustes_so)
            monto_d = q2(_dec(calc_aj["total_descuento_ajustado"]))
            so_con_especiales_aplicados.add(bf.so_id)
        else:
            monto_d = q2(_dec(bf.total_descuentos))
        if monto_d <= 0:
            continue
        b = ensure_bucket(ord_obj.cliente_id, "", ord_obj.vendedor_email)
        b["descuentos_sistema_usd"] += monto_d
        b["descuentos_confirmados_usd"] += monto_d
        b["movimientos"].append(
            {
                "fecha": ord_obj.fecha.isoformat(),
                "tipo": "descuento_bandeja",
                "etiqueta": f"Descuento Confirmado ({bf.estado.value})",
                "referencia": bf.so_id,
                "fuente": "cxc",
                "debito_usd": 0.0,
                "credito_usd": float(monto_d),
            }
        )

    for so_id, ajustes_so in ajustes_descuentos_por_so.items():
        if so_id in so_con_especiales_aplicados or so_id in no_otorgados or not ajustes_so:
            continue
        ord_obj = ordenes_por_so.get(so_id)
        if ord_obj is None or ord_obj.estado_orden not in ESTADOS_ORDEN_CONFIRMADA:
            continue
        calc_esp = aplicar_ajustes_a_descuentos_orden([], ord_obj.monto_total, ajustes_so)
        monto_esp = q2(_dec(calc_esp["total_especial"]))
        if monto_esp <= 0:
            continue
        b = ensure_bucket(ord_obj.cliente_id, "", ord_obj.vendedor_email)
        b["descuentos_sistema_usd"] += monto_esp
        b["descuentos_confirmados_usd"] += monto_esp
        b["movimientos"].append(
            {
                "fecha": ord_obj.fecha.isoformat(),
                "tipo": "descuento_especial",
                "etiqueta": "Descuento Comercial Especial (CxC)",
                "referencia": so_id,
                "fuente": "cxc",
                "debito_usd": 0.0,
                "credito_usd": float(monto_esp),
            }
        )

    for fac in facturas_all:
        if fac.move_type != "out_refund" or fac.estado != "posted":
            continue
        monto_nc = q2(abs(_dec(fac.monto_total_signed_usd)))
        if monto_nc <= 0:
            continue
        ord_match = next(
            (ordenes_por_so[s] for s in _so_ids_csv(fac.so_id) if s in ordenes_por_so),
            None,
        )
        if ord_match is None:
            continue
        b = ensure_bucket(ord_match.cliente_id, "", ord_match.vendedor_email)
        b["notas_credito_usd"] += monto_nc
        b["descuentos_confirmados_usd"] += monto_nc
        b["movimientos"].append(
            {
                "fecha": fac.fecha.isoformat(),
                "tipo": "nota_credito",
                "etiqueta": "Nota de Crédito (Odoo)",
                "referencia": fac.numero or fac.factura_id,
                "fuente": "odoo",
                "debito_usd": 0.0,
                "credito_usd": float(monto_nc),
            }
        )

    # 3. Pagos confirmados desde espejo Odoo (usando vinculaciones confirmadas + tasas locales)
    vincs_por_pago: dict[str, list[Any]] = {}
    for v in repo.all_vinculaciones():
        if v.estado in ESTADOS_VINC_CONFIRMADA:
            vincs_por_pago.setdefault(str(v.pago_id), []).append(v)

    pagos_all = repo.all_pagos()
    pagos_odoo_ids = {str(p.pago_id) for p in pagos_all}
    for p in pagos_all:
        b = ensure_bucket(p.cliente_id, "", p.vendedor_email)
        f_p = p.fecha_pago.date()
        vincs = vincs_por_pago.get(str(p.pago_id), [])
        vinc_congelado = Decimal("0")
        aplicado_nativo = Decimal("0")
        for v in vincs:
            aplicado_nativo += _dec(v.monto_aplicado)
            ruta_v = ruta_por_so.get(str(v.so_id), "")
            usa_binance = (
                ruta_v == RUTA_BINANCE
                or getattr(v, "tipo_tasa_abono", None) == TipoTasa.BINANCE
            )
            if usa_binance and getattr(v, "equiv_usd_binance", None) is not None:
                eq_v = q2(_dec(v.equiv_usd_binance))
            elif usa_binance:
                eq_v = _a_usd_por_ruta(
                    _dec(v.monto_aplicado),
                    p.moneda.value,
                    f_p,
                    RUTA_BINANCE,
                    por_fecha_tasa,
                    fallback_tasa,
                    por_fecha_binance,
                    fallback_binance,
                )
            elif v.equiv_usd_bcv is not None:
                eq_v = q2(_dec(v.equiv_usd_bcv))
            else:
                eq_v = _a_usd_local(
                    _dec(v.monto_aplicado), p.moneda.value, f_p, por_fecha_tasa, fallback_tasa
                )
            vinc_congelado += eq_v
            if usa_binance:
                b["pagos_binance_usd"] += eq_v
            else:
                b["pagos_bcv_usd"] += eq_v
        remanente_nativo = max(Decimal("0"), _dec(p.monto) - aplicado_nativo)
        met_p = None
        if getattr(p, "metodo_pago", None) and hasattr(repo, "get_metodo_pago"):
            try:
                met_p = repo.get_metodo_pago(str(p.metodo_pago))
            except Exception:
                met_p = None
        ruta_rem = (
            RUTA_BINANCE
            if (
                met_p is not None
                and (
                    getattr(met_p, "tipo_tasa", None) == TipoTasa.BINANCE
                    or "binance" in str(getattr(met_p, "nombre", "") or "").lower()
                )
            )
            else RUTA_BCV
        )
        remanente_usd = _a_usd_por_ruta(
            remanente_nativo,
            p.moneda.value,
            f_p,
            ruta_rem,
            por_fecha_tasa,
            fallback_tasa,
            por_fecha_binance,
            fallback_binance,
        )
        if ruta_rem == RUTA_BINANCE:
            b["pagos_binance_usd"] += remanente_usd
        else:
            b["pagos_bcv_usd"] += remanente_usd
        monto_usd = q2(vinc_congelado + remanente_usd)
        if monto_usd <= 0:
            continue
        b["pagos_odoo_usd"] += monto_usd
        b["pagos_confirmados_usd"] += monto_usd
        b["n_pagos"] += 1
        ref_lbl = p.numero_odoo or p.referencia or f"PAGO-{p.pago_id}"
        b["movimientos"].append(
            {
                "fecha": f_p.isoformat(),
                "tipo": "pago_odoo",
                "etiqueta": f"Pago Confirmado Odoo ({p.moneda.value})",
                "referencia": ref_lbl,
                "fuente": "odoo",
                "debito_usd": 0.0,
                "credito_usd": float(monto_usd),
            }
        )

    # 3b. Pagos confirmados CxC-first (Zelle de Terceros + Efectivo CxC + pagos no reflejados)
    for dop in docs_operativos:
        if getattr(dop, "tipo", "") != "pago":
            continue
        est = str(getattr(dop, "estado", "") or "").upper()
        if est == "RECHAZADO":
            continue
        huella_banco = str(getattr(dop, "conciliado_banco_huella", "") or "").strip()
        confirmado = (
            bool(getattr(dop, "confirmado_cxc", False))
            or est in ("CONFIRMADO_CXC", "ENVIADO_ODOO")
            or bool(huella_banco)
        )
        if not confirmado:
            continue
        es_zt = bool(getattr(dop, "es_zelle_tercero", False))
        odoo_mod = str(getattr(dop, "odoo_model", "") or "")
        odoo_rid = getattr(dop, "odoo_res_id", None)
        if (
            not es_zt
            and odoo_mod in ("", "account.payment")
            and odoo_rid is not None
            and str(odoo_rid) in pagos_odoo_ids
        ):
            continue
        monto_raw = _dec(getattr(dop, "monto", 0))
        if monto_raw <= 0:
            continue
        mon_op = str(getattr(dop, "moneda", "VES") or "VES").upper()
        f_doc_op = getattr(dop, "fecha_documento", None)
        if not isinstance(f_doc_op, date):
            cre = getattr(dop, "creado_en", None)
            f_doc_op = cre.date() if isinstance(cre, datetime) else hoy_d
        pedido_dop = str(getattr(dop, "pedido_ref", "") or "").strip()
        ruta_dop = normalizar_ruta(getattr(dop, "ruta_pago", "")) or ruta_por_so.get(pedido_dop, "")
        if not ruta_dop and "binance" in str(getattr(dop, "metodo_pago", "") or "").lower():
            ruta_dop = RUTA_BINANCE
        if ruta_dop == RUTA_BINANCE and (por_fecha_binance or fallback_binance > 0):
            monto_usd = _a_usd_local(
                monto_raw,
                mon_op,
                f_doc_op,
                por_fecha_binance,
                fallback_binance,
                eur_por_fecha=eur_por_fecha,
                fallback_eur=fallback_eur,
                usd_bcv_por_fecha=por_fecha_tasa,
                fallback_usd_bcv=fallback_tasa,
            )
        else:
            monto_usd = _a_usd_local(
                monto_raw,
                mon_op,
                f_doc_op,
                por_fecha_tasa,
                fallback_tasa,
                eur_por_fecha=eur_por_fecha,
                fallback_eur=fallback_eur,
            )
        if monto_usd <= 0:
            continue

        cid, _ = resolver_cid(
            getattr(dop, "partner_odoo_id", None),
            str(getattr(dop, "contraparte_nombre", "") or ""),
        )
        b = buckets[cid]
        met = str(getattr(dop, "metodo_pago", "") or "").lower()
        if es_zt:
            b["pagos_zelle_tercero_usd"] += monto_usd
            etq = "Pago Zelle Tercero Confirmado (CxC)"
            tipo_mov = "pago_zelle_tercero"
        elif "efectivo" in met or "cash" in met:
            b["pagos_efectivo_cxc_usd"] += monto_usd
            etq = "Cobro Efectivo Confirmado (CxC)"
            tipo_mov = "pago_efectivo_cxc"
        elif huella_banco:
            b["pagos_otros_cxc_usd"] += monto_usd
            etq = "Cobro Electrónico Conciliado en Banco"
            tipo_mov = "pago_cxc_banco"
        else:
            b["pagos_otros_cxc_usd"] += monto_usd
            etq = "Cobro Confirmado en CxC"
            tipo_mov = "pago_cxc"

        b["pagos_confirmados_usd"] += monto_usd
        b["n_pagos"] += 1
        rec = str(getattr(dop, "numero_recibo", "") or "").strip()
        num = str(getattr(dop, "numero", "") or "").strip()
        b["movimientos"].append(
            {
                "fecha": f_doc_op.isoformat(),
                "tipo": tipo_mov,
                "etiqueta": etq,
                "referencia": f"{num} ({rec})" if rec else num,
                "fuente": "cxc",
                "debito_usd": 0.0,
                "credito_usd": float(monto_usd),
            }
        )

    # 4. Cierre por cliente + antigüedad FIFO
    clientes_out: list[dict[str, Any]] = []
    por_vendedor: dict[str, dict[str, Any]] = {}
    tot_tramos: dict[str, Decimal] = dict.fromkeys(TRAMOS_MORA, Decimal("0"))

    for cid, b in buckets.items():
        ventas = q2(b["ventas_confirmadas_usd"])
        descuentos = q2(b["descuentos_confirmados_usd"])
        pagos_c = q2(b["pagos_confirmados_usd"])
        if ventas == 0 and descuentos == 0 and pagos_c == 0:
            continue

        saldo_bruto = q2(ventas - descuentos - pagos_c)
        saldo_deudor = max(Decimal("0.00"), saldo_bruto)
        saldo_favor = max(Decimal("0.00"), -saldo_bruto)

        # FIFO de órdenes abiertas para tramos de mora
        tramos_cli, max_dias_mora = fifo_antiguedad(b["ordenes_fifo"], descuentos + pagos_c, hoy_d)

        for tr in TRAMOS_MORA:
            tot_tramos[tr] = q2(tot_tramos[tr] + tramos_cli[tr])

        vencido_cli = q2(sum((tramos_cli[t] for t in TRAMOS_MORA if t != "vigente"), Decimal("0")))
        fila_cli = {
            "cliente_id": cid,
            "cliente_nombre": b["cliente_nombre"],
            "vendedor_email": b["vendedor_email"] or "Sin asignar",
            "n_ordenes": int(b["n_ordenes"]),
            "n_pagos": int(b["n_pagos"]),
            "ventas_confirmadas_usd": _f(ventas),
            "ventas_bcv_usd": _f(b["ventas_bcv_usd"]),
            "ventas_binance_usd": _f(b["ventas_binance_usd"]),
            "descuentos_sistema_usd": _f(b["descuentos_sistema_usd"]),
            "notas_credito_usd": _f(b["notas_credito_usd"]),
            "descuentos_confirmados_usd": _f(descuentos),
            "pagos_odoo_usd": _f(b["pagos_odoo_usd"]),
            "pagos_bcv_usd": _f(b["pagos_bcv_usd"]),
            "pagos_binance_usd": _f(b["pagos_binance_usd"]),
            "pagos_zelle_tercero_usd": _f(b["pagos_zelle_tercero_usd"]),
            "pagos_efectivo_cxc_usd": _f(b["pagos_efectivo_cxc_usd"]),
            "pagos_otros_cxc_usd": _f(b["pagos_otros_cxc_usd"]),
            "pagos_confirmados_usd": _f(pagos_c),
            "saldo_neto_usd": _f(saldo_bruto),
            "saldo_deudor_usd": _f(saldo_deudor),
            "saldo_favor_usd": _f(saldo_favor),
            "vencido_usd": _f(vencido_cli),
            "vigente_usd": _f(tramos_cli["vigente"]),
            "tramos": {k: _f(v) for k, v in tramos_cli.items()},
            "dias_mora_max": max_dias_mora,
            "_movimientos": b["movimientos"],
        }
        clientes_out.append(fila_cli)

        vend = fila_cli["vendedor_email"]
        bv = por_vendedor.setdefault(
            vend,
            {
                "vendedor_email": vend,
                "n_clientes": 0,
                "n_clientes_con_saldo": 0,
                "ventas_confirmadas_usd": 0.0,
                "descuentos_confirmados_usd": 0.0,
                "pagos_confirmados_usd": 0.0,
                "saldo_deudor_usd": 0.0,
                "vencido_usd": 0.0,
                "clientes": [],
            },
        )
        bv["n_clientes"] += 1
        if saldo_deudor > 0:
            bv["n_clientes_con_saldo"] += 1
        bv["ventas_confirmadas_usd"] = round(
            bv["ventas_confirmadas_usd"] + fila_cli["ventas_confirmadas_usd"], 2
        )
        bv["descuentos_confirmados_usd"] = round(
            bv["descuentos_confirmados_usd"] + fila_cli["descuentos_confirmados_usd"], 2
        )
        bv["pagos_confirmados_usd"] = round(
            bv["pagos_confirmados_usd"] + fila_cli["pagos_confirmados_usd"], 2
        )
        bv["saldo_deudor_usd"] = round(bv["saldo_deudor_usd"] + fila_cli["saldo_deudor_usd"], 2)
        bv["vencido_usd"] = round(bv["vencido_usd"] + fila_cli["vencido_usd"], 2)
        bv["clientes"].append(
            {
                "cliente_id": cid,
                "cliente_nombre": fila_cli["cliente_nombre"],
                "ventas_confirmadas_usd": fila_cli["ventas_confirmadas_usd"],
                "descuentos_confirmados_usd": fila_cli["descuentos_confirmados_usd"],
                "pagos_confirmados_usd": fila_cli["pagos_confirmados_usd"],
                "saldo_deudor_usd": fila_cli["saldo_deudor_usd"],
                "vencido_usd": fila_cli["vencido_usd"],
            }
        )

    clientes_out.sort(key=lambda x: (-x["saldo_deudor_usd"], x["cliente_nombre"]))
    vendedores_out = sorted(
        por_vendedor.values(), key=lambda x: (-x["saldo_deudor_usd"], x["vendedor_email"])
    )
    last_sync = repo.get_last_sync()
    return {
        "generado_en": datetime.now().isoformat(),
        "ultimo_sync_bd": last_sync.isoformat() if last_sync else None,
        "tasa_bcv_referencia": float(fallback_tasa),
        "tasa_binance_referencia": float(fallback_binance),
        "clientes": clientes_out,
        "vendedores": vendedores_out,
        "tramos_globales": {k: _f(v) for k, v in tot_tramos.items()},
    }


def _resumir_kpis(clientes_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    ventas = round(sum(float(c["ventas_confirmadas_usd"]) for c in clientes_rows), 2)
    descuentos = round(sum(float(c["descuentos_confirmados_usd"]) for c in clientes_rows), 2)
    desc_sis = round(sum(float(c["descuentos_sistema_usd"]) for c in clientes_rows), 2)
    nc_odoo = round(sum(float(c["notas_credito_usd"]) for c in clientes_rows), 2)
    pagos = round(sum(float(c["pagos_confirmados_usd"]) for c in clientes_rows), 2)
    pagos_odoo = round(sum(float(c["pagos_odoo_usd"]) for c in clientes_rows), 2)
    zelle3 = round(sum(float(c["pagos_zelle_tercero_usd"]) for c in clientes_rows), 2)
    efec_cxc = round(sum(float(c["pagos_efectivo_cxc_usd"]) for c in clientes_rows), 2)
    deudor = round(sum(float(c["saldo_deudor_usd"]) for c in clientes_rows), 2)
    favor = round(sum(float(c["saldo_favor_usd"]) for c in clientes_rows), 2)
    vencido = round(sum(float(c["vencido_usd"]) for c in clientes_rows), 2)
    vigente = round(sum(float(c["vigente_usd"]) for c in clientes_rows), 2)
    tramos = {k: round(sum(float(c["tramos"][k]) for c in clientes_rows), 2) for k in TRAMOS_MORA}
    return {
        "total_clientes": len(clientes_rows),
        "clientes_con_saldo": sum(1 for c in clientes_rows if c["saldo_deudor_usd"] > 0),
        "ventas_confirmadas_usd": ventas,
        "descuentos_sistema_usd": desc_sis,
        "notas_credito_usd": nc_odoo,
        "descuentos_confirmados_usd": descuentos,
        "pagos_odoo_usd": pagos_odoo,
        "pagos_zelle_tercero_usd": zelle3,
        "pagos_efectivo_cxc_usd": efec_cxc,
        "pagos_confirmados_usd": pagos,
        "saldo_deudor_usd": deudor,
        "saldo_favor_usd": favor,
        "vencido_usd": vencido,
        "vigente_usd": vigente,
        "tramos": tramos,
    }


def _cumple_antiguedad(c: dict[str, Any], filtro: str) -> bool:
    if not filtro or filtro == "*":
        return True
    if filtro == "vencido_total":
        return float(c["vencido_usd"]) > 0
    if filtro == "vigentes":
        return float(c["vigente_usd"]) > 0
    if filtro in c["tramos"]:
        return float(c["tramos"][filtro]) > 0
    return True


cumple_antiguedad = _cumple_antiguedad


def calcular_saldos_propios(
    repo: Repository,
    *,
    docs_comerciales: Sequence[Any] = (),
    docs_operativos: Sequence[Any] = (),
    ajustes_descuentos_por_so: dict[str, list[dict[str, Any]]] | None = None,
    hoy: date | None = None,
    vendedor: str | None = None,
    antiguedad: str | None = None,
    q: str | None = None,
    solo_con_saldo: bool = False,
) -> dict[str, Any]:
    """Reporte completo de Saldos CxC calculado 100% sobre la base de datos propia."""
    base = _recolectar_estado_bd(
        repo,
        docs_comerciales=docs_comerciales,
        docs_operativos=docs_operativos,
        ajustes_descuentos_por_so=ajustes_descuentos_por_so,
        hoy=hoy,
    )
    clientes_limpios = [
        {k: v for k, v in c.items() if k != "_movimientos"} for c in base["clientes"]
    ]
    filtrados = clientes_limpios
    if vendedor and vendedor != "*":
        v_low = vendedor.strip().lower()
        filtrados = [c for c in filtrados if c["vendedor_email"].lower() == v_low]
    if antiguedad and antiguedad != "*":
        filtrados = [c for c in filtrados if _cumple_antiguedad(c, antiguedad)]
    if solo_con_saldo:
        filtrados = [c for c in filtrados if c["saldo_deudor_usd"] > 0]
    if q and q.strip():
        qn = q.strip().lower()
        filtrados = [
            c
            for c in filtrados
            if qn in c["cliente_nombre"].lower()
            or qn in c["cliente_id"].lower()
            or qn in c["vendedor_email"].lower()
        ]

    return {
        "fuente": "bd_propia_cxc",
        "solo_lectura": True,
        "formula": "Saldo Deudor = Ventas Confirmadas - Descuentos Confirmados - Pagos Confirmados",
        "generado_en": base["generado_en"],
        "ultimo_sync_bd": base["ultimo_sync_bd"],
        "tasa_bcv_referencia": base["tasa_bcv_referencia"],
        "tasa_binance_referencia": base["tasa_binance_referencia"],
        "kpis": _resumir_kpis(filtrados),
        "kpis_globales": _resumir_kpis(clientes_limpios),
        "vendedores": base["vendedores"],
        "clientes": filtrados,
    }


def estado_cuenta_cliente(
    repo: Repository,
    cliente_id: str,
    *,
    docs_comerciales: Sequence[Any] = (),
    docs_operativos: Sequence[Any] = (),
    ajustes_descuentos_por_so: dict[str, list[dict[str, Any]]] | None = None,
    hoy: date | None = None,
) -> dict[str, Any] | None:
    """Devuelve el estado de cuenta cronológico de un cliente desde BD propia."""
    base = _recolectar_estado_bd(
        repo,
        docs_comerciales=docs_comerciales,
        docs_operativos=docs_operativos,
        ajustes_descuentos_por_so=ajustes_descuentos_por_so,
        hoy=hoy,
    )
    target = next((c for c in base["clientes"] if str(c["cliente_id"]) == str(cliente_id)), None)
    if target is None:
        return None
    movs_ord = sorted(
        target["_movimientos"],
        key=lambda m: (str(m["fecha"]), 0 if m["debito_usd"] > 0 else 1, str(m["referencia"])),
    )
    saldo_corrido = Decimal("0.00")
    movs_con_saldo: list[dict[str, Any]] = []
    for m in movs_ord:
        saldo_corrido = q2(saldo_corrido + _dec(m["debito_usd"]) - _dec(m["credito_usd"]))
        movs_con_saldo.append({**m, "saldo_acumulado_usd": float(saldo_corrido)})
    resumen = {k: v for k, v in target.items() if k != "_movimientos"}
    return {
        "fuente": "bd_propia_cxc",
        "solo_lectura": True,
        "resumen": resumen,
        "movimientos": movs_con_saldo,
    }


def comparativo_bd_vs_odoo(
    repo: Repository,
    *,
    docs_comerciales: Sequence[Any] = (),
    docs_operativos: Sequence[Any] = (),
    hoy: date | None = None,
) -> dict[str, Any]:
    """Compara la cartera neta de BD propia CxC contra los documentos espejo de Odoo."""
    rep = calcular_saldos_propios(
        repo, docs_comerciales=docs_comerciales, docs_operativos=docs_operativos, hoy=hoy
    )
    kg = rep["kpis_globales"]

    try:
        facturas_all = repo.all_facturas()
    except NotImplementedError:
        facturas_all = []
    fact_out_usd = Decimal("0")
    nc_out_usd = Decimal("0")
    for f in facturas_all:
        if f.estado != "posted":
            continue
        if f.move_type in ("out_invoice", "out_debit"):
            fact_out_usd += q2(abs(_dec(f.monto_total_signed_usd)))
        elif f.move_type == "out_refund":
            nc_out_usd += q2(abs(_dec(f.monto_total_signed_usd)))

    saldo_teorico_odoo = _f(
        max(Decimal("0"), fact_out_usd - nc_out_usd - _dec(kg["pagos_odoo_usd"]))
    )
    brecha = round(kg["saldo_deudor_usd"] - saldo_teorico_odoo, 2)
    return {
        "generado_en": rep["generado_en"],
        "bd_propia": kg,
        "espejo_odoo": {
            "facturado_bruto_usd": _f(fact_out_usd),
            "notas_credito_usd": _f(nc_odoo := nc_out_usd),
            "facturado_neto_usd": _f(max(Decimal("0"), fact_out_usd - nc_odoo)),
            "pagos_odoo_usd": kg["pagos_odoo_usd"],
            "saldo_referencial_odoo_usd": saldo_teorico_odoo,
        },
        "explicacion_diferencias": {
            "descuentos_sistema_sin_nc_usd": kg["descuentos_sistema_usd"],
            "pagos_zelle_tercero_cxc_usd": kg["pagos_zelle_tercero_usd"],
            "pagos_efectivo_cxc_usd": kg["pagos_efectivo_cxc_usd"],
            "brecha_neta_usd": brecha,
        },
    }


def exportar_saldos_csv(reporte: dict[str, Any]) -> bytes:
    """Genera CSV UTF-8 con BOM listo para Excel con el reporte de BD propia."""
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Cliente ID",
            "Cliente",
            "Vendedor",
            "Ordenes",
            "Pagos",
            "Ventas Confirmadas USD",
            "Descuentos Sistema USD",
            "Notas Credito USD",
            "Descuentos Confirmados USD",
            "Pagos Odoo USD",
            "Zelle Terceros USD",
            "Efectivo CxC USD",
            "Pagos Confirmados USD",
            "Saldo Deudor USD",
            "Saldo a Favor USD",
            "Vigente USD",
            "1-30 Dias USD",
            "31-60 Dias USD",
            "61-90 Dias USD",
            "+90 Dias USD",
        ]
    )
    for c in reporte.get("clientes") or []:
        t = c.get("tramos") or {}
        writer.writerow(
            [
                c.get("cliente_id", ""),
                c.get("cliente_nombre", ""),
                c.get("vendedor_email", ""),
                c.get("n_ordenes", 0),
                c.get("n_pagos", 0),
                f"{c.get('ventas_confirmadas_usd', 0):.2f}",
                f"{c.get('descuentos_sistema_usd', 0):.2f}",
                f"{c.get('notas_credito_usd', 0):.2f}",
                f"{c.get('descuentos_confirmados_usd', 0):.2f}",
                f"{c.get('pagos_odoo_usd', 0):.2f}",
                f"{c.get('pagos_zelle_tercero_usd', 0):.2f}",
                f"{c.get('pagos_efectivo_cxc_usd', 0):.2f}",
                f"{c.get('pagos_confirmados_usd', 0):.2f}",
                f"{c.get('saldo_deudor_usd', 0):.2f}",
                f"{c.get('saldo_favor_usd', 0):.2f}",
                f"{t.get('vigente', 0):.2f}",
                f"{t.get('1_30', 0):.2f}",
                f"{t.get('31_60', 0):.2f}",
                f"{t.get('61_90', 0):.2f}",
                f"{t.get('mas_90', 0):.2f}",
            ]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")
