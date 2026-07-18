"""Serialización dataclass ↔ fila de Sheets (str → str).

Cada tabla tiene su ``to_row`` / ``from_row``. Centralizar aquí la conversión de
``Decimal``/fechas/enums/booleanos mantiene el resto del adaptador trivial y deja
un único lugar para los formatos que viajan a la hoja.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal

from ..models import (
    BandejaFacturacion,
    Cliente,
    Conciliacion,
    Condicion,
    DescuentoAplicado,
    DescuentoBCVCompleto,
    DescuentoMarcaCategoria,
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
    TipoDescuento,
    TipoFeriado,
    TipoTasa,
    Vinculacion,
)

Row = dict[str, str]

# --- Conversores escalares ---------------------------------------------------
_TRUE = {"TRUE", "1", "YES", "SI", "SÍ", "VERDADERO", "TRUE "}


def s_bool(x: bool) -> str:
    return "TRUE" if x else "FALSE"


def p_bool(s: str) -> bool:
    return s.strip().upper() in _TRUE


def s_optdec(x: Decimal | None) -> str:
    return "" if x is None else str(x)


def p_dec(s: str) -> Decimal:
    return Decimal(s)


def p_optdec(s: str) -> Decimal | None:
    s = s.strip()
    return None if s in ("", "None") else Decimal(s)


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
    return {"cliente_id": c.cliente_id, "nombre": c.nombre,
            "vendedor_email": c.vendedor_email}


def cliente_from_row(r: Mapping[str, str]) -> Cliente:
    return Cliente(cliente_id=r["cliente_id"], nombre=r.get("nombre", ""),
                   vendedor_email=r.get("vendedor_email", ""))


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
        estado_entrega=r.get("estado_entrega", ""),
        entregada_completa=p_bool(r.get("entregada_completa", "FALSE")),
        tiene_devolucion=p_bool(r.get("tiene_devolucion", "FALSE")),
    )


# --- LineasOrden -------------------------------------------------------------
def linea_to_row(ln: LineaOrden) -> Row:
    return {
        "linea_id": ln.linea_id, "so_id": ln.so_id, "producto": ln.producto,
        "marca": ln.marca, "categoria": ln.categoria,
        "cantidad": str(ln.cantidad), "precio_unitario": str(ln.precio_unitario),
        "cantidad_entregada": str(ln.cantidad_entregada),
        "descuento": str(ln.descuento),
    }


def linea_from_row(r: Mapping[str, str]) -> LineaOrden:
    return LineaOrden(
        linea_id=r["linea_id"], so_id=r.get("so_id", ""),
        producto=r.get("producto", ""), marca=r.get("marca", ""),
        categoria=r.get("categoria", ""),
        cantidad=p_dec(r.get("cantidad", "0")),
        precio_unitario=p_dec(r.get("precio_unitario", "0")),
        cantidad_entregada=p_dec(r.get("cantidad_entregada", "0")),
        descuento=p_dec(r.get("descuento", "0")),
    )


# --- Pagos -------------------------------------------------------------------
def pago_to_row(p: Pago) -> Row:
    return {
        "pago_id": p.pago_id, "cliente_id": p.cliente_id, "monto": str(p.monto),
        "moneda": p.moneda.value, "metodo_pago": p.metodo_pago,
        "fecha_pago": s_dt(p.fecha_pago), "vendedor_email": p.vendedor_email,
    }


def pago_from_row(r: Mapping[str, str]) -> Pago:
    return Pago(
        pago_id=r["pago_id"], cliente_id=r.get("cliente_id", ""),
        monto=p_dec(r.get("monto", "0")), moneda=Moneda(r.get("moneda", "VES")),
        metodo_pago=r.get("metodo_pago", ""),
        fecha_pago=p_dt(r["fecha_pago"]), vendedor_email=r.get("vendedor_email", ""),
    )


# --- MetodosPago -------------------------------------------------------------
def metodo_to_row(m: MetodoPago) -> Row:
    return {
        "metodo_id": m.metodo_id, "nombre": m.nombre, "moneda": m.moneda.value,
        "tipo_tasa": m.tipo_tasa.value, "es_contado": s_bool(m.es_contado),
    }


def metodo_from_row(r: Mapping[str, str]) -> MetodoPago:
    return MetodoPago(
        metodo_id=r["metodo_id"], nombre=r.get("nombre", ""),
        moneda=Moneda(r.get("moneda", "VES")),
        tipo_tasa=TipoTasa(r.get("tipo_tasa", "N_A")),
        es_contado=p_bool(r.get("es_contado", "FALSE")),
    )


# --- SerieTasas --------------------------------------------------------------
def serie_to_row(s: SerieTasa) -> Row:
    return {
        "timestamp": s_dt(s.timestamp), "tasa_bcv": str(s.tasa_bcv),
        "tasa_binance": str(s.tasa_binance), "fuente": s.fuente,
        "es_heredada": s_bool(s.es_heredada), "capturada_ok": s_bool(s.capturada_ok),
    }


def serie_from_row(r: Mapping[str, str]) -> SerieTasa:
    return SerieTasa(
        timestamp=p_dt(r["timestamp"]), tasa_bcv=p_dec(r.get("tasa_bcv", "0")),
        tasa_binance=p_dec(r.get("tasa_binance", "0")), fuente=r.get("fuente", ""),
        es_heredada=p_bool(r.get("es_heredada", "FALSE")),
        capturada_ok=p_bool(r.get("capturada_ok", "TRUE")),
    )


# --- DescuentosMarcaCategoria ------------------------------------------------
def descuento_to_row(d: DescuentoMarcaCategoria) -> Row:
    return {
        "regla_id": d.regla_id, "marca": d.marca, "categoria": d.categoria,
        "tipo_descuento": d.tipo_descuento.value, "porcentaje": str(d.porcentaje),
        "vigencia_desde": d.vigencia_desde.isoformat(),
        "vigencia_hasta": s_optdate(d.vigencia_hasta),
        "listas_aplicables": d.listas_aplicables,
        "activo": s_bool(d.activo),
    }


def descuento_from_row(r: Mapping[str, str]) -> DescuentoMarcaCategoria:
    return DescuentoMarcaCategoria(
        regla_id=r["regla_id"], marca=r.get("marca", "*"),
        categoria=r.get("categoria", "*"),
        tipo_descuento=TipoDescuento(r.get("tipo_descuento", "contado")),
        porcentaje=p_dec(r.get("porcentaje", "0")),
        vigencia_desde=p_date(r["vigencia_desde"]),
        vigencia_hasta=p_optdate(r.get("vigencia_hasta", "")),
        listas_aplicables=r.get("listas_aplicables", "*"),
        activo=p_bool(r.get("activo", "TRUE")),
    )


# --- DescuentoBCVCompleto ----------------------------------------------------
def bcv_completo_to_row(d: DescuentoBCVCompleto) -> Row:
    return {
        "vigencia_desde": d.vigencia_desde.isoformat(),
        "porcentaje": str(d.porcentaje),
        "vigencia_hasta": s_optdate(d.vigencia_hasta),
        "activo": s_bool(d.activo),
    }


def bcv_completo_from_row(r: Mapping[str, str]) -> DescuentoBCVCompleto:
    return DescuentoBCVCompleto(
        vigencia_desde=p_date(r["vigencia_desde"]),
        porcentaje=p_dec(r.get("porcentaje", "0")),
        vigencia_hasta=p_optdate(r.get("vigencia_hasta", "")),
        activo=p_bool(r.get("activo", "TRUE")),
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
        "activo": s_bool(p.activo),
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
        activo=p_bool(r.get("activo", "TRUE")),
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
        "vigencia_desde": d.vigencia_desde.isoformat(),
        "vigencia_hasta": s_optdate(d.vigencia_hasta),
        "listas_aplicables": d.listas_aplicables,
        "activo": s_bool(d.activo)
    }

def desc_volumen_from_row(r: Mapping[str, str]) -> DescuentoVolumen:
    return DescuentoVolumen(
        regla_id=r["regla_id"],
        marca=r.get("marca", "*"),
        categoria=r.get("categoria", "*"),
        litros_minimo=p_dec(r.get("litros_minimo", "0")),
        porcentaje=p_dec(r.get("porcentaje", "0")),
        vigencia_desde=p_date(r.get("vigencia_desde", "2026-01-01")),
        vigencia_hasta=p_optdate(r.get("vigencia_hasta", "")),
        listas_aplicables=r.get("listas_aplicables", "*"),
        activo=p_bool(r.get("activo", "TRUE"))
    )


# --- ReglasRecurrencia -------------------------------------------------------
def regla_to_row(g: ReglaRecurrencia) -> Row:
    return {
        "condicion": g.condicion.value, "tipo_beneficio": g.tipo_beneficio.value,
        "valor": str(g.valor), "vigencia_desde": g.vigencia_desde.isoformat(),
        "vigencia_hasta": s_optdate(g.vigencia_hasta), "activo": s_bool(g.activo),
    }


def regla_from_row(r: Mapping[str, str]) -> ReglaRecurrencia:
    return ReglaRecurrencia(
        condicion=Condicion(r["condicion"]),
        tipo_beneficio=TipoBeneficio(r.get("tipo_beneficio", "porcentaje")),
        valor=p_dec(r.get("valor", "0")),
        vigencia_desde=p_date(r["vigencia_desde"]),
        vigencia_hasta=p_optdate(r.get("vigencia_hasta", "")),
        activo=p_bool(r.get("activo", "TRUE")),
    )


# --- Feriados ----------------------------------------------------------------
def feriado_to_row(f: Feriado) -> Row:
    return {"fecha": f.fecha.isoformat(), "descripcion": f.descripcion,
            "tipo": f.tipo.value}


def feriado_from_row(r: Mapping[str, str]) -> Feriado:
    return Feriado(fecha=p_date(r["fecha"]), descripcion=r.get("descripcion", ""),
                   tipo=TipoFeriado(r.get("tipo", "nacional")))


# --- Vinculaciones -----------------------------------------------------------
def vinculacion_to_row(v: Vinculacion) -> Row:
    return {
        "vinc_id": v.vinc_id, "pago_id": v.pago_id, "so_id": v.so_id,
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
        "timestamp_registro": "" if v.timestamp_registro is None
        else s_dt(v.timestamp_registro),
        "estado": v.estado.value,
        "moneda_abono": v.moneda_abono.value,
        "tipo_tasa_abono": v.tipo_tasa_abono.value,
    }


def vinculacion_from_row(r: Mapping[str, str]) -> Vinculacion:
    ts = r.get("timestamp_registro", "").strip()
    return Vinculacion(
        vinc_id=r["vinc_id"], pago_id=r.get("pago_id", ""), so_id=r.get("so_id", ""),
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
    )


# --- BandejaFacturacion ------------------------------------------------------
def bandeja_to_row(b: BandejaFacturacion) -> Row:
    detalle = json.dumps(
        [{"origen": d.origen, "descripcion": d.descripcion, "monto": str(d.monto)}
         for d in b.descuentos_detalle],
        ensure_ascii=False,
    )
    return {
        "so_id": b.so_id, "lista_aplicada": b.lista_aplicada,
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
                    origen=d["origen"], descripcion=d["descripcion"],
                    monto=Decimal(str(d["monto"])),
                )
            )
    return BandejaFacturacion(
        so_id=r["so_id"], lista_aplicada=r.get("lista_aplicada", ""),
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
        "so_id": c.so_id, "total_motor": str(c.total_motor),
        "monto_odoo": str(c.monto_odoo), "ncs_odoo": str(c.ncs_odoo),
        "diferencia": str(c.diferencia), "resultado": c.resultado.value,
        "revisado_por": s_optstr(c.revisado_por),
    }


def conciliacion_from_row(r: Mapping[str, str]) -> Conciliacion:
    return Conciliacion(
        so_id=r["so_id"], total_motor=p_dec(r.get("total_motor", "0")),
        monto_odoo=p_dec(r.get("monto_odoo", "0")),
        ncs_odoo=p_dec(r.get("ncs_odoo", "0")),
        diferencia=p_dec(r.get("diferencia", "0")),
        resultado=ResultadoConciliacion(r.get("resultado", "verde")),
        revisado_por=p_optstr(r.get("revisado_por", "")),
    )
