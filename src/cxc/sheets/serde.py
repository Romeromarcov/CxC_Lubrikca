"""Serialización dataclass ↔ fila de Sheets (str → str).

Cada tabla tiene su ``to_row`` / ``from_row``. Centralizar aquí la conversión de
``Decimal``/fechas/enums/booleanos mantiene el resto del adaptador trivial y deja
un único lugar para los formatos que viajan a la hoja.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from ..models import (
    BandejaFacturacion,
    Cliente,
    Conciliacion,
    Condicion,
    DescuentoAplicado,
    DescuentoBCVCompleto,
    DescuentoDiferencialCambiario,
    DescuentoProducto,
    DescuentoProntoPago,
    DescuentoRecompra,
    DescuentoVolumen,
    EstadoBandeja,
    EstadoVinculacion,
    ExclusionRegla,
    Feriado,
    LineaOrden,
    MetodoPago,
    Moneda,
    OrdenVenta,
    Pago,
    PromocionPrimeraCompra,
    ReglaRecurrencia,
    ResultadoConciliacion,
    SerieTasa,
    TipoBeneficio,
    TipoFeriado,
    TipoTasa,
    Vinculacion,
)

Row = dict[str, str]

logger = logging.getLogger("cxc.sheets.serde")

# --- Conversores escalares ---------------------------------------------------
_TRUE = {"TRUE", "1", "YES", "SI", "SÍ", "VERDADERO", "TRUE "}


def s_bool(x: bool) -> str:
    return "TRUE" if x else "FALSE"


def p_bool(s: str) -> bool:
    return s.strip().upper() in _TRUE


def s_optdec(x: Decimal | None) -> str:
    return "" if x is None else str(x)


def p_dec(s: str) -> Decimal:
    """Parsea un Decimal desde una celda de Sheets, tolerante a datos malos.

    Una hoja editable a mano puede tener celdas vacías/corruptas en
    cualquier columna numérica de cualquier fila; que UNA fila mala tumbe
    con 500 un endpoint entero (que procesa cientos de filas) es peor que
    tratarla como 0 y dejar rastro en el log para auditarla aparte.

    Una celda vacía NO se loggea -- es el caso normal y mayoritario de
    cualquier columna numérica opcional (miles de celdas en blanco por cada
    lectura completa), no un dato corrupto; loggearla como warning ahogaba
    el log real (Railway descartaba cientos de mensajes/seg por exceder su
    límite de 500 logs/seg). Solo se loggea texto no vacío que de verdad no
    parsea (p.ej. "#REF!" o texto suelto), que sí amerita auditarse.
    """
    if not s or not s.strip():
        return Decimal("0")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError, TypeError):
        logger.warning("Valor decimal inválido en Sheets: %r -- se usa 0", s)
        return Decimal("0")


def p_pct(s: str) -> Decimal:
    val = p_dec(s)
    if val > Decimal("1.0"):
        val = val / Decimal("100.0")
    return val


def p_optdec(s: str) -> Decimal | None:
    s = s.strip()
    return None if s in ("", "None") else p_dec(s)


def s_optdate(x: date | None) -> str:
    return "" if x is None else x.isoformat()


def p_optdate(s: str) -> date | None:
    s = s.strip()
    return None if not s else date.fromisoformat(s[:10])


def p_date(s: str) -> date:
    return date.fromisoformat(s[:10])


def s_dt(x: datetime) -> str:
    return x.isoformat()


def p_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.strip())


def s_optstr(x: str | None) -> str:
    return "" if x is None else x


def p_optstr(s: str) -> str | None:
    return s if s != "" else None


# --- Clientes ----------------------------------------------------------------
def cliente_to_row(c: Cliente) -> Row:
    return {
        "cliente_id": c.cliente_id,
        "nombre": c.nombre,
        "vendedor_email": c.vendedor_email,
        "wh_iva_agent": s_bool(c.wh_iva_agent),
        "wh_iva_rate": str(c.wh_iva_rate),
    }


def cliente_from_row(r: Mapping[str, str]) -> Cliente:
    wh_rate_raw = r.get("wh_iva_rate", "").strip()
    return Cliente(
        cliente_id=r["cliente_id"],
        nombre=r.get("nombre", ""),
        vendedor_email=r.get("vendedor_email", ""),
        wh_iva_agent=p_bool(r.get("wh_iva_agent", "FALSE")),
        wh_iva_rate=float(wh_rate_raw) if wh_rate_raw else 75.0,
    )


# --- OrdenesVenta ------------------------------------------------------------
def orden_to_row(o: OrdenVenta) -> Row:
    return {
        "so_id": o.so_id,
        "cliente_id": o.cliente_id,
        "fecha": o.fecha.isoformat(),
        "fecha_entrega": s_optdate(o.fecha_entrega),
        "monto_total": str(o.monto_total),
        "lista_precios": o.lista_precios,
        "vendedor_email": o.vendedor_email,
        "es_primera_compra": s_bool(o.es_primera_compra),
        "facturada": s_bool(o.facturada),
        "factura_id": s_optstr(o.factura_id),
        "monto_facturado": s_optdec(o.monto_facturado),
        "estado_orden": getattr(o, "estado_orden", "sale"),
        "estado_entrega": o.estado_entrega,
        "entregada_completa": s_bool(o.entregada_completa),
        "tiene_devolucion": s_bool(o.tiene_devolucion),
    }


def orden_from_row(r: Mapping[str, str]) -> OrdenVenta:
    return OrdenVenta(
        so_id=r["so_id"],
        cliente_id=r.get("cliente_id", ""),
        fecha=p_date(r["fecha"]),
        fecha_entrega=p_optdate(r.get("fecha_entrega", "")),
        monto_total=p_dec(r.get("monto_total", "0")),
        lista_precios=r.get("lista_precios", ""),
        vendedor_email=r.get("vendedor_email", ""),
        es_primera_compra=p_bool(r.get("es_primera_compra", "FALSE")),
        facturada=p_bool(r.get("facturada", "FALSE")),
        factura_id=p_optstr(r.get("factura_id", "")),
        monto_facturado=p_optdec(r.get("monto_facturado", "")),
        estado_orden=r.get("estado_orden", "sale"),
        estado_entrega=r.get("estado_entrega", ""),
        entregada_completa=p_bool(r.get("entregada_completa", "FALSE")),
        tiene_devolucion=p_bool(r.get("tiene_devolucion", "FALSE")),
    )


# --- LineasOrden -------------------------------------------------------------
def linea_to_row(ln: LineaOrden) -> Row:
    return {
        "linea_id": ln.linea_id,
        "so_id": ln.so_id,
        "producto": ln.producto,
        "marca": ln.marca,
        "categoria": ln.categoria,
        "cantidad": str(ln.cantidad),
        "precio_unitario": str(ln.precio_unitario),
        "cantidad_entregada": str(ln.cantidad_entregada),
        "descuento": str(ln.descuento),
    }


def linea_from_row(r: Mapping[str, str]) -> LineaOrden:
    return LineaOrden(
        linea_id=r["linea_id"],
        so_id=r.get("so_id", ""),
        producto=r.get("producto", ""),
        marca=r.get("marca", ""),
        categoria=r.get("categoria", ""),
        cantidad=p_dec(r.get("cantidad", "0")),
        precio_unitario=p_dec(r.get("precio_unitario", "0")),
        cantidad_entregada=p_dec(r.get("cantidad_entregada", "0")),
        descuento=p_dec(r.get("descuento", "0")),
    )


# --- Pagos -------------------------------------------------------------------
def pago_to_row(p: Pago) -> Row:
    return {
        "pago_id": p.pago_id,
        "cliente_id": p.cliente_id,
        "monto": str(p.monto),
        "moneda": p.moneda.value,
        "metodo_pago": p.metodo_pago,
        "fecha_pago": s_dt(p.fecha_pago),
        "vendedor_email": p.vendedor_email,
    }


def pago_from_row(r: Mapping[str, str]) -> Pago:
    return Pago(
        pago_id=r["pago_id"],
        cliente_id=r.get("cliente_id", ""),
        monto=p_dec(r.get("monto", "0")),
        moneda=Moneda(r.get("moneda", "VES")),
        metodo_pago=r.get("metodo_pago", ""),
        fecha_pago=p_dt(r["fecha_pago"]),
        vendedor_email=r.get("vendedor_email", ""),
    )


# --- MetodosPago -------------------------------------------------------------
def metodo_to_row(m: MetodoPago) -> Row:
    return {
        "metodo_id": m.metodo_id,
        "nombre": m.nombre,
        "moneda": m.moneda.value,
        "tipo_tasa": m.tipo_tasa.value,
        "es_contado": s_bool(m.es_contado),
    }


def metodo_from_row(r: Mapping[str, str]) -> MetodoPago:
    return MetodoPago(
        metodo_id=r["metodo_id"],
        nombre=r.get("nombre", ""),
        moneda=Moneda(r.get("moneda", "VES")),
        tipo_tasa=TipoTasa(r.get("tipo_tasa", "N_A")),
        es_contado=p_bool(r.get("es_contado", "FALSE")),
    )


# --- SerieTasas --------------------------------------------------------------
def serie_to_row(s: SerieTasa) -> Row:
    return {
        "timestamp": s_dt(s.timestamp),
        "tasa_bcv": str(s.tasa_bcv),
        "tasa_binance": str(s.tasa_binance),
        "fuente": s.fuente,
        "es_heredada": s_bool(s.es_heredada),
        "capturada_ok": s_bool(s.capturada_ok),
        "tasa_binance_manana": str(s.tasa_binance_manana) if s.tasa_binance_manana else "",
        "tasa_binance_tarde": str(s.tasa_binance_tarde) if s.tasa_binance_tarde else "",
        "tasa_binance_diario": str(s.tasa_binance_diario) if s.tasa_binance_diario else "",
        "diferencial_bcv_binance_pct": str(s.diferencial_bcv_binance_pct)
        if s.diferencial_bcv_binance_pct
        else "",
        "tasa_bcv_euro": str(s.tasa_bcv_euro) if s.tasa_bcv_euro else "",
    }


def serie_from_row(r: Mapping[str, str]) -> SerieTasa:
    return SerieTasa(
        timestamp=p_dt(r["timestamp"]),
        tasa_bcv=p_dec(r.get("tasa_bcv", "0")),
        tasa_binance=p_dec(r.get("tasa_binance", "0")),
        fuente=r.get("fuente", ""),
        es_heredada=p_bool(r.get("es_heredada", "FALSE")),
        capturada_ok=p_bool(r.get("capturada_ok", "TRUE")),
        tasa_binance_manana=p_dec(r.get("tasa_binance_manana", ""))
        if r.get("tasa_binance_manana")
        else None,
        tasa_binance_tarde=p_dec(r.get("tasa_binance_tarde", ""))
        if r.get("tasa_binance_tarde")
        else None,
        tasa_binance_diario=p_dec(r.get("tasa_binance_diario", ""))
        if r.get("tasa_binance_diario")
        else None,
        diferencial_bcv_binance_pct=p_dec(r.get("diferencial_bcv_binance_pct", ""))
        if r.get("diferencial_bcv_binance_pct")
        else None,
        tasa_bcv_euro=p_dec(r.get("tasa_bcv_euro", "")) if r.get("tasa_bcv_euro") else None,
    )


# --- DescuentosProntoPago ---------------------------------------------------
def pronto_pago_to_row(d: DescuentoProntoPago) -> Row:
    return {
        "regla_id": d.regla_id,
        "marca": d.marca,
        "categoria": d.categoria,
        "dias_gracia": str(d.dias_gracia),
        "porcentaje": str(d.porcentaje),
        "monedas_aplicables": d.monedas_aplicables,
        "listas_aplicables": d.listas_aplicables,
        "vigencia_desde": d.vigencia_desde.isoformat(),
        "vigencia_hasta": s_optdate(d.vigencia_hasta),
        "activo": s_bool(d.activo),
        "requiere_pago_previo": s_bool(d.requiere_pago_previo),
    }


def pronto_pago_from_row(r: Mapping[str, str]) -> DescuentoProntoPago:
    return DescuentoProntoPago(
        regla_id=r.get("regla_id", "PRONTO_PAGO_DEFAULT"),
        marca=r.get("marca", "*"),
        categoria=r.get("categoria", "*"),
        dias_gracia=int(r.get("dias_gracia", "3")),
        porcentaje=p_pct(r.get("porcentaje", "0.05")),
        monedas_aplicables=r.get("monedas_aplicables", "*"),
        listas_aplicables=r.get("listas_aplicables", "*"),
        vigencia_desde=p_date(r.get("vigencia_desde", "2026-01-01")),
        vigencia_hasta=p_optdate(r.get("vigencia_hasta", "")),
        activo=p_bool(r.get("activo", "TRUE")),
        requiere_pago_previo=p_bool(r.get("requiere_pago_previo", "TRUE")),
    )


descuento_to_row = pronto_pago_to_row


def descuento_from_row(r: Mapping[str, str]) -> DescuentoProntoPago:
    return pronto_pago_from_row(r)


def recompra_from_row(r: Mapping[str, str]) -> DescuentoRecompra:
    return DescuentoRecompra(
        regla_id=r.get("regla_id") or "REC_1",
        marca=r.get("marca") or "*",
        categoria=r.get("categoria") or "*",
        min_cajas=int(r.get("min_cajas") or r.get("min_unidades") or "0"),
        max_cajas=int(r.get("max_cajas") or r.get("max_unidades") or "999999"),
        porcentaje=p_dec(r.get("porcentaje") or "0.03"),
        max_usos_mes=int(r.get("max_usos_mes") or "1"),
        dias_ventana=int(r.get("dias_ventana") or "30"),
        listas_aplicables=r.get("listas_aplicables") or "*",
        vigencia_desde=p_date(r.get("vigencia_desde") or "2026-01-01"),
        vigencia_hasta=p_optdate(r.get("vigencia_hasta") or ""),
        activo=p_bool(r.get("activo") or "TRUE"),
        requiere_pago_previo=p_bool(r.get("requiere_pago_previo") or "FALSE"),
    )


# --- DescuentoProducto ------------------------------------------------------
def producto_to_row(d: DescuentoProducto) -> Row:
    return {
        "regla_id": d.regla_id,
        "productos": d.productos,
        "marca": d.marca,
        "categoria": d.categoria,
        "porcentaje": str(d.porcentaje),
        "monedas_aplicables": d.monedas_aplicables,
        "listas_aplicables": d.listas_aplicables,
        "vigencia_desde": d.vigencia_desde.isoformat(),
        "vigencia_hasta": s_optdate(d.vigencia_hasta),
        "activo": s_bool(d.activo),
        "requiere_pago_previo": s_bool(d.requiere_pago_previo),
    }


def producto_from_row(r: Mapping[str, str]) -> DescuentoProducto:
    return DescuentoProducto(
        regla_id=r.get("regla_id", "PROD_DEFAULT"),
        productos=r.get("productos", "*"),
        marca=r.get("marca", "*"),
        categoria=r.get("categoria", "*"),
        porcentaje=p_pct(r.get("porcentaje", "0.05")),
        monedas_aplicables=r.get("monedas_aplicables", "*"),
        listas_aplicables=r.get("listas_aplicables", "*"),
        vigencia_desde=p_date(r.get("vigencia_desde", "2026-01-01")),
        vigencia_hasta=p_optdate(r.get("vigencia_hasta", "")),
        activo=p_bool(r.get("activo", "TRUE")),
        requiere_pago_previo=p_bool(r.get("requiere_pago_previo", "FALSE")),
    )


# --- DescuentoDiferencialCambiario -------------------------------------------
def diferencial_to_row(d: DescuentoDiferencialCambiario) -> Row:
    return {
        "regla_id": d.regla_id,
        "nombre": d.nombre,
        "tipo_diferencial": d.tipo_diferencial,
        "tipo_calculo": d.tipo_calculo,
        "porcentaje_fijo": str(d.porcentaje_fijo),
        "marca": d.marca,
        "categoria": d.categoria,
        "min_cantidad": str(d.min_cantidad),
        "max_cantidad": str(d.max_cantidad),
        "unidad_medida": d.unidad_medida or "USD",
        "monedas_aplicables": d.monedas_aplicables,
        "listas_aplicables": d.listas_aplicables,
        "vigencia_desde": d.vigencia_desde.isoformat(),
        "vigencia_hasta": s_optdate(d.vigencia_hasta),
        "activo": s_bool(d.activo),
        "requiere_pago_previo": s_bool(d.requiere_pago_previo),
    }


def diferencial_from_row(r: Mapping[str, str]) -> DescuentoDiferencialCambiario:
    tipo_dif = r.get("tipo_diferencial", "fijo_35_ves_usd")
    default_monedas = "USD" if tipo_dif == "fijo_35_ves_usd" else "*"
    return DescuentoDiferencialCambiario(
        regla_id=r.get("regla_id", "DIF_DEFAULT"),
        nombre=r.get("nombre", "Descuento Diferencial Cambiario"),
        tipo_diferencial=tipo_dif,
        tipo_calculo=r.get("tipo_calculo", "fijo"),
        porcentaje_fijo=p_dec(r.get("porcentaje_fijo", "0.35")),
        marca=r.get("marca", "*"),
        categoria=r.get("categoria", "*"),
        min_cantidad=p_dec(r.get("min_cantidad", "0")),
        max_cantidad=p_dec(r.get("max_cantidad", "999999")),
        unidad_medida=r.get("unidad_medida", "USD"),
        monedas_aplicables=r.get("monedas_aplicables", default_monedas),
        listas_aplicables=r.get("listas_aplicables", "5"),
        vigencia_desde=p_date(r.get("vigencia_desde", "2026-01-01")),
        vigencia_hasta=p_optdate(r.get("vigencia_hasta", "")),
        activo=p_bool(r.get("activo", "TRUE")),
        requiere_pago_previo=p_bool(r.get("requiere_pago_previo", "TRUE")),
    )


# --- DescuentoBCVCompleto ----------------------------------------------------
def bcv_completo_to_row(d: DescuentoBCVCompleto) -> Row:
    return {
        "vigencia_desde": d.vigencia_desde.isoformat(),
        "porcentaje": str(d.porcentaje),
        "vigencia_hasta": s_optdate(d.vigencia_hasta),
        "activo": s_bool(d.activo),
        "requiere_pago_previo": s_bool(d.requiere_pago_previo),
    }


def bcv_completo_from_row(r: Mapping[str, str]) -> DescuentoBCVCompleto:
    return DescuentoBCVCompleto(
        vigencia_desde=p_date(r["vigencia_desde"]),
        porcentaje=p_dec(r.get("porcentaje", "0")),
        vigencia_hasta=p_optdate(r.get("vigencia_hasta", "")),
        activo=p_bool(r.get("activo", "TRUE")),
        requiere_pago_previo=p_bool(r.get("requiere_pago_previo", "TRUE")),
    )


# --- PromocionPrimeraCompra --------------------------------------------------
def promocion_to_row(p: PromocionPrimeraCompra) -> Row:
    return {
        "regla_id": p.regla_id,
        "tipo_beneficio": p.tipo_beneficio,
        "productos": p.productos,
        "valor": str(p.valor),
        "compra_minima": str(p.compra_minima),
        "regalo_tipo": p.regalo_tipo,
        "vigencia_desde": p.vigencia_desde.isoformat(),
        "vigencia_hasta": s_optdate(p.vigencia_hasta),
        "descuento_fallback": str(p.descuento_fallback),
        "categorias_aplica": p.categorias_aplica,
        "solo_primera_compra": s_bool(getattr(p, "solo_primera_compra", False)),
        "activo": s_bool(p.activo),
        "requiere_pago_previo": s_bool(p.requiere_pago_previo),
    }


def promocion_from_row(r: Mapping[str, str]) -> PromocionPrimeraCompra:
    return PromocionPrimeraCompra(
        regla_id=r.get("regla_id", "LEGACY_PROMO"),
        tipo_beneficio=r.get("tipo_beneficio", "producto"),
        productos=r.get("productos", r.get("producto", "")),
        valor=p_dec(r.get("valor", "1")),
        compra_minima=p_dec(r.get("compra_minima", "3")),
        regalo_tipo=r.get("regalo_tipo", "solo_uno"),
        vigencia_desde=p_date(r["vigencia_desde"]),
        vigencia_hasta=p_optdate(r.get("vigencia_hasta", "")),
        descuento_fallback=p_dec(r.get("descuento_fallback", "0")),
        categorias_aplica=r.get("categorias_aplica", "Comercial"),
        solo_primera_compra=p_bool(r.get("solo_primera_compra", "FALSE")),
        activo=p_bool(r.get("activo", "TRUE")),
        requiere_pago_previo=p_bool(r.get("requiere_pago_previo", "FALSE")),
    )


# --- ExclusionRegla ----------------------------------------------------------
def exclusion_to_row(exc: ExclusionRegla) -> Row:
    return {
        "regla_tipo_a": exc.regla_tipo_a,
        "regla_tipo_b": exc.regla_tipo_b,
        "activo": s_bool(exc.activo),
    }


def exclusion_from_row(r: Mapping[str, str]) -> ExclusionRegla:
    return ExclusionRegla(
        regla_tipo_a=r["regla_tipo_a"],
        regla_tipo_b=r["regla_tipo_b"],
        activo=p_bool(r.get("activo", "TRUE")),
    )


# --- DescuentosVolumen --------------------------------------------------------
def desc_volumen_to_row(d: DescuentoVolumen) -> Row:
    return {
        "regla_id": d.regla_id,
        "marca": d.marca,
        "categoria": d.categoria,
        "litros_minimo": str(d.litros_minimo),
        "porcentaje": str(d.porcentaje),
        "min_cantidad": str(d.min_cantidad),
        "max_cantidad": str(d.max_cantidad),
        "unidad_medida": d.unidad_medida,
        "tipo_evaluacion": d.tipo_evaluacion,
        "dias_evaluacion": str(d.dias_evaluacion),
        "vigencia_desde": d.vigencia_desde.isoformat(),
        "vigencia_hasta": s_optdate(d.vigencia_hasta),
        "listas_aplicables": d.listas_aplicables,
        "activo": s_bool(d.activo),
        "requiere_pago_previo": s_bool(d.requiere_pago_previo),
    }


def desc_volumen_from_row(r: Mapping[str, str]) -> DescuentoVolumen:
    return DescuentoVolumen(
        regla_id=r.get("regla_id", "VOL_DEFAULT"),
        marca=r.get("marca", "*"),
        categoria=r.get("categoria", "*"),
        litros_minimo=p_dec(r.get("litros_minimo", "0")),
        porcentaje=p_pct(r.get("porcentaje", "0")),
        min_cantidad=p_dec(r.get("min_cantidad", r.get("litros_minimo", "0"))),
        max_cantidad=p_dec(r.get("max_cantidad", "999999")),
        unidad_medida=r.get("unidad_medida", "UNIDADES") or "UNIDADES",
        tipo_evaluacion=r.get("tipo_evaluacion", "orden"),
        dias_evaluacion=int(r.get("dias_evaluacion", "30")),
        vigencia_desde=p_date(r.get("vigencia_desde", "2026-01-01")),
        vigencia_hasta=p_optdate(r.get("vigencia_hasta", "")),
        listas_aplicables=r.get("listas_aplicables", "*"),
        activo=p_bool(r.get("activo", "TRUE")),
        requiere_pago_previo=p_bool(r.get("requiere_pago_previo", "FALSE")),
    )


# --- DescuentosRecompra ------------------------------------------------------
def recompra_to_row(d: DescuentoRecompra) -> Row:
    return {
        "regla_id": d.regla_id,
        "marca": d.marca,
        "categoria": d.categoria,
        "min_cajas": str(d.min_cajas),
        "max_cajas": str(d.max_cajas),
        "porcentaje": str(d.porcentaje),
        "max_usos_mes": str(d.max_usos_mes),
        "dias_ventana": str(d.dias_ventana),
        "vigencia_desde": d.vigencia_desde.isoformat(),
        "vigencia_hasta": s_optdate(d.vigencia_hasta),
        "activo": s_bool(d.activo),
        "requiere_pago_previo": s_bool(d.requiere_pago_previo),
    }


# --- ReglasRecurrencia -------------------------------------------------------
def regla_to_row(g: ReglaRecurrencia) -> Row:
    return {
        "condicion": g.condicion.value,
        "tipo_beneficio": g.tipo_beneficio.value,
        "valor": str(g.valor),
        "vigencia_desde": g.vigencia_desde.isoformat(),
        "vigencia_hasta": s_optdate(g.vigencia_hasta),
        "activo": s_bool(g.activo),
        "requiere_pago_previo": s_bool(g.requiere_pago_previo),
    }


def regla_from_row(r: Mapping[str, str]) -> ReglaRecurrencia:
    return ReglaRecurrencia(
        condicion=Condicion(r["condicion"]),
        tipo_beneficio=TipoBeneficio(r.get("tipo_beneficio", "porcentaje")),
        valor=p_dec(r.get("valor", "0")),
        vigencia_desde=p_date(r["vigencia_desde"]),
        vigencia_hasta=p_optdate(r.get("vigencia_hasta", "")),
        activo=p_bool(r.get("activo", "TRUE")),
        requiere_pago_previo=p_bool(r.get("requiere_pago_previo", "FALSE")),
    )


# --- Feriados ----------------------------------------------------------------
def feriado_to_row(f: Feriado) -> Row:
    return {"fecha": f.fecha.isoformat(), "descripcion": f.descripcion, "tipo": f.tipo.value}


def feriado_from_row(r: Mapping[str, str]) -> Feriado:
    return Feriado(
        fecha=p_date(r["fecha"]),
        descripcion=r.get("descripcion", ""),
        tipo=TipoFeriado(r.get("tipo", "nacional")),
    )


# --- Vinculaciones -----------------------------------------------------------
def vinculacion_to_row(v: Vinculacion) -> Row:
    return {
        "vinc_id": v.vinc_id,
        "pago_id": v.pago_id,
        "so_id": v.so_id,
        "monto_aplicado": str(v.monto_aplicado),
        "hora_pago_confirmada": s_dt(v.hora_pago_confirmada),
        "tasa_bcv_aplicada": str(v.tasa_bcv_aplicada),
        "tasa_binance_aplicada": str(v.tasa_binance_aplicada),
        "es_tasa_heredada": s_bool(v.es_tasa_heredada),
        "equiv_usd_bcv": s_optdec(v.equiv_usd_bcv),
        "equiv_usd_binance": s_optdec(v.equiv_usd_binance),
        "equiv_ves_bcv": s_optdec(v.equiv_ves_bcv),
        "equiv_ves_binance": s_optdec(v.equiv_ves_binance),
        "confirmado_por": v.confirmado_por,
        "timestamp_registro": "" if v.timestamp_registro is None else s_dt(v.timestamp_registro),
        "estado": v.estado.value,
        "moneda_abono": v.moneda_abono.value,
        "tipo_tasa_abono": v.tipo_tasa_abono.value,
        "bcv_variante": v.bcv_variante,
    }


def vinculacion_from_row(r: Mapping[str, str]) -> Vinculacion:
    ts = r.get("timestamp_registro", "").strip()
    return Vinculacion(
        vinc_id=r["vinc_id"],
        pago_id=r.get("pago_id", ""),
        so_id=r.get("so_id", ""),
        monto_aplicado=p_dec(r.get("monto_aplicado", "0")),
        hora_pago_confirmada=p_dt(r["hora_pago_confirmada"]),
        tasa_bcv_aplicada=p_dec(r.get("tasa_bcv_aplicada", "0")),
        tasa_binance_aplicada=p_dec(r.get("tasa_binance_aplicada", "0")),
        es_tasa_heredada=p_bool(r.get("es_tasa_heredada", "FALSE")),
        equiv_usd_bcv=p_optdec(r.get("equiv_usd_bcv", "")),
        equiv_usd_binance=p_optdec(r.get("equiv_usd_binance", "")),
        equiv_ves_bcv=p_optdec(r.get("equiv_ves_bcv", "")),
        equiv_ves_binance=p_optdec(r.get("equiv_ves_binance", "")),
        confirmado_por=r.get("confirmado_por", ""),
        timestamp_registro=p_dt(ts) if ts else None,
        estado=EstadoVinculacion(r.get("estado", "pendiente")),
        moneda_abono=Moneda(r.get("moneda_abono", "VES")),
        tipo_tasa_abono=TipoTasa(r.get("tipo_tasa_abono", "N_A")),
        bcv_variante=r.get("bcv_variante", "").strip() or "USD",
    )


# --- BandejaFacturacion ------------------------------------------------------
def bandeja_to_row(b: BandejaFacturacion) -> Row:
    detalle = json.dumps(
        [
            {"origen": d.origen, "descripcion": d.descripcion, "monto": str(d.monto)}
            for d in b.descuentos_detalle
        ],
        ensure_ascii=False,
    )
    return {
        "so_id": b.so_id,
        "lista_aplicada": b.lista_aplicada,
        "precio_base_calculado": str(b.precio_base_calculado),
        "descuentos_detalle": detalle,
        "total_descuentos": str(b.total_descuentos),
        "ncs_calculadas": str(b.ncs_calculadas),
        "total_motor": str(b.total_motor),
        "requiere_revision": s_bool(b.requiere_revision),
        "candidata_a_cierre": s_bool(b.candidata_a_cierre),
        "aprobado_por": s_optstr(b.aprobado_por),
        "estado": b.estado.value,
    }


def bandeja_from_row(r: Mapping[str, str]) -> BandejaFacturacion:
    raw = r.get("descuentos_detalle", "").strip()
    detalle = []
    if raw:
        for d in json.loads(raw):
            detalle.append(
                DescuentoAplicado(
                    origen=d["origen"],
                    descripcion=d["descripcion"],
                    monto=Decimal(str(d["monto"])),
                )
            )
    return BandejaFacturacion(
        so_id=r["so_id"],
        lista_aplicada=r.get("lista_aplicada", ""),
        precio_base_calculado=p_dec(r.get("precio_base_calculado", "0")),
        descuentos_detalle=detalle,
        total_descuentos=p_dec(r.get("total_descuentos", "0")),
        ncs_calculadas=p_dec(r.get("ncs_calculadas", "0")),
        total_motor=p_dec(r.get("total_motor", "0")),
        requiere_revision=p_bool(r.get("requiere_revision", "FALSE")),
        candidata_a_cierre=p_bool(r.get("candidata_a_cierre", "FALSE")),
        aprobado_por=p_optstr(r.get("aprobado_por", "")),
        estado=EstadoBandeja(r.get("estado", "calculado")),
    )


# --- Conciliacion ------------------------------------------------------------
def conciliacion_to_row(c: Conciliacion) -> Row:
    return {
        "so_id": c.so_id,
        "total_motor": str(c.total_motor),
        "monto_odoo": str(c.monto_odoo),
        "ncs_odoo": str(c.ncs_odoo),
        "diferencia": str(c.diferencia),
        "resultado": c.resultado.value,
        "revisado_por": s_optstr(c.revisado_por),
    }


def conciliacion_from_row(r: Mapping[str, str]) -> Conciliacion:
    return Conciliacion(
        so_id=r["so_id"],
        total_motor=p_dec(r.get("total_motor", "0")),
        monto_odoo=p_dec(r.get("monto_odoo", "0")),
        ncs_odoo=p_dec(r.get("ncs_odoo", "0")),
        diferencia=p_dec(r.get("diferencia", "0")),
        resultado=ResultadoConciliacion(r.get("resultado", "verde")),
        revisado_por=p_optstr(r.get("revisado_por", "")),
    )
