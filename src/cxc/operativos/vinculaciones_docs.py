"""Vinculación bidireccional de pagos a Ventas y Compras/Gastos con FIFO y Odoo.

Soporta los cuatro flujos operativos:
  - Venta confirmada (``S0*`` / ``PED-*``) ↔ Pago recibido (Odoo mirror, ``PAG-*``, Zelle 3ro)
  - Pago recibido ↔ Órdenes abiertas del cliente (orden FIFO, más antigua primero)
  - Compra / Gasto (``FPR-*``, ``GAS-*``, Odoo ``in_invoice``/``entry``) ↔ Pago enviado / Zelle 3ro
  - Pago enviado ↔ Facturas / Gastos abiertos del proveedor (orden FIFO, más antigua primero)

Además, tras persistir en CxC, intenta conciliar en Odoo cuando ambas partes existen
publicadas (``account.move`` + ``account.payment`` / asiento Zelle 3ro), sin bloquear la
vinculación en CxC si alguno sigue en borrador o aún no ha sido facturado.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, delete, insert, or_, select

from ..db.schema import (
    cuentas_terceros,
    documentos_comerciales,
    documentos_operativos,
    vinculaciones,
    vinculaciones_compras,
    vinculaciones_ventas_op,
)
from ..decimal_utils import q2, q6
from ..engine.equivalents import equivalentes_bcv, equivalentes_binance
from ..engine.saldos_propios import dec, indice_tasas_bcv_local, norm_nombre, tasa_bcv_en_fecha
from ..models import EstadoVinculacion, Moneda, TipoTasa, Vinculacion


class VinculacionInvalida(ValueError):
    """Error de validación en vinculación de pagos contra documentos."""


class VinculacionNoEncontrada(KeyError):
    """No existe la vinculación o documento solicitado."""


def _fecha_iso_o_hoy(val: Any, hoy: date) -> date:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    txt = str(val or "").strip()[:10]
    if txt:
        try:
            return date.fromisoformat(txt)
        except ValueError:
            pass
    return hoy


def _f2(val: Any) -> float:
    return float(q2(dec(val)))


def _equiv_usd_y_tasa(
    monto: Decimal,
    moneda: str,
    fecha_d: date,
    por_fecha: dict[date, Decimal],
    fallback: Decimal,
) -> tuple[Decimal, Decimal | None]:
    mon = (moneda or "USD").strip().upper()
    if mon == "USD" or monto <= 0:
        return q2(monto), None
    tasa = tasa_bcv_en_fecha(fecha_d, por_fecha, fallback)
    if tasa <= 0:
        return Decimal("0.00"), None
    return q2(monto / tasa), q6(tasa)


def _monto_orig_desde_usd(
    usd: Decimal,
    moneda: str,
    fecha_d: date,
    por_fecha: dict[date, Decimal],
    fallback: Decimal,
) -> Decimal:
    mon = (moneda or "USD").strip().upper()
    if mon == "USD" or usd <= 0:
        return q2(usd)
    tasa = tasa_bcv_en_fecha(fecha_d, por_fecha, fallback)
    if tasa <= 0:
        return q2(usd)
    return q2(usd * tasa)


def _tasa_binance_cercana(repo_cxc: Any, fecha_d: date, tasa_bcv_fallback: Decimal) -> Decimal:
    try:
        series = sorted(repo_cxc.all_serie_tasas(), key=lambda x: x.timestamp)
    except Exception:
        series = []
    elegida = Decimal("0")
    for s in series:
        tb = dec(getattr(s, "tasa_binance", 0))
        if tb > 0:
            elegida = tb
            if s.timestamp.date() <= fecha_d:
                continue
            break
    if elegida > 0:
        return elegida
    return tasa_bcv_fallback if tasa_bcv_fallback > 0 else Decimal("1")


def _serializar_vinc_op_venta(row: Any) -> dict[str, Any]:
    return {
        "origen_registro": "cxc_op",
        "vinc_id": int(row.id),
        "pago_operativo_id": int(row.pago_operativo_id) if row.pago_operativo_id else None,
        "pago_odoo_id": str(row.pago_odoo_id or "") or None,
        "so_id": str(row.so_id),
        "documento_comercial_id": (
            int(row.documento_comercial_id) if row.documento_comercial_id else None
        ),
        "monto_aplicado": _f2(row.monto_aplicado),
        "moneda": str(row.moneda or "USD"),
        "equiv_usd_bcv": _f2(row.equiv_usd_bcv),
        "tasa_bcv": float(row.tasa_bcv) if row.tasa_bcv is not None else None,
        "origen_vinculacion": str(row.origen_vinculacion or "manual"),
        "estado": str(row.estado or "VINCULADO_CXC"),
        "conciliado_odoo": bool(row.odoo_reconciliado),
        "odoo_move_id": int(row.odoo_move_id) if row.odoo_move_id else None,
        "odoo_detalle": str(row.odoo_detalle or ""),
        "vinculado_por": str(row.vinculado_por or ""),
        "creado_en": row.creado_en.isoformat() if row.creado_en else "",
        "puede_desvincular": not bool(row.odoo_reconciliado),
    }


def _serializar_vinc_mirror_venta(v: Any) -> dict[str, Any]:
    est = str(getattr(v.estado, "value", v.estado) or "pendiente")
    eq_usd = v.equiv_usd_bcv if v.equiv_usd_bcv is not None else v.monto_aplicado
    return {
        "origen_registro": "mirror",
        "vinc_id": str(v.vinc_id),
        "pago_operativo_id": None,
        "pago_odoo_id": str(v.pago_id),
        "so_id": str(v.so_id),
        "documento_comercial_id": None,
        "monto_aplicado": _f2(v.monto_aplicado),
        "moneda": str(getattr(v.moneda_abono, "value", v.moneda_abono) or "VES"),
        "equiv_usd_bcv": _f2(eq_usd),
        "tasa_bcv": float(v.tasa_bcv_aplicada) if v.tasa_bcv_aplicada else None,
        "origen_vinculacion": "fifo" if "FIFO" in str(v.confirmado_por or "").upper() else "manual",
        "estado": "RECONCILIADO_ODOO"
        if est == EstadoVinculacion.CONCILIADO.value
        else "VINCULADO_CXC",
        "conciliado_odoo": est == EstadoVinculacion.CONCILIADO.value,
        "odoo_move_id": None,
        "odoo_detalle": f"Estado espejo: {est}",
        "vinculado_por": str(v.confirmado_por or ""),
        "creado_en": v.timestamp_registro.isoformat() if v.timestamp_registro else "",
        "puede_desvincular": est != EstadoVinculacion.CONCILIADO.value,
    }


def _rows_vinc_ventas_op(engine: Engine) -> list[Any]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                select(vinculaciones_ventas_op).order_by(vinculaciones_ventas_op.c.id.asc())
            ).all()
        )


def _rows_vinc_compras(engine: Engine) -> list[Any]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                select(vinculaciones_compras).order_by(vinculaciones_compras.c.id.asc())
            ).all()
        )


# ── Mapas O(1) para enriquecer tablas unificadas ─────────────────────────────


def mapa_vinculaciones_ventas(engine: Engine | None, repo_cxc: Any) -> dict[str, Any]:
    """Agrupa vinculaciones de ventas por ``so_id``, ``pago_id`` (Odoo) y ``pago_operativo_id``."""
    por_so_id: dict[str, list[dict[str, Any]]] = {}
    por_pago_id: dict[str, list[dict[str, Any]]] = {}
    por_pago_op_id: dict[int, list[dict[str, Any]]] = {}

    try:
        vincs_m = list(repo_cxc.all_vinculaciones())
    except Exception:
        vincs_m = []

    for v in vincs_m:
        d = _serializar_vinc_mirror_venta(v)
        por_so_id.setdefault(d["so_id"], []).append(d)
        if d["pago_odoo_id"]:
            por_pago_id.setdefault(str(d["pago_odoo_id"]), []).append(d)

    if engine is not None:
        try:
            for r in _rows_vinc_ventas_op(engine):
                d = _serializar_vinc_op_venta(r)
                por_so_id.setdefault(d["so_id"], []).append(d)
                if d["pago_odoo_id"]:
                    por_pago_id.setdefault(str(d["pago_odoo_id"]), []).append(d)
                if d["pago_operativo_id"]:
                    por_pago_op_id.setdefault(int(d["pago_operativo_id"]), []).append(d)
        except Exception:
            pass

    return {
        "por_so_id": por_so_id,
        "por_pago_id": por_pago_id,
        "por_pago_op_id": por_pago_op_id,
    }


def mapa_vinculaciones_compras(engine: Engine | None) -> dict[str, Any]:
    """Agrupa vinculaciones de compras/gastos por compra y por pago."""
    por_compra_op_id: dict[int, list[dict[str, Any]]] = {}
    por_compra_move_id: dict[str, list[dict[str, Any]]] = {}
    por_pago_op_id: dict[int, list[dict[str, Any]]] = {}
    por_pago_odoo_id: dict[str, list[dict[str, Any]]] = {}

    if engine is None:
        return {
            "por_compra_op_id": por_compra_op_id,
            "por_compra_move_id": por_compra_move_id,
            "por_pago_op_id": por_pago_op_id,
            "por_pago_odoo_id": por_pago_odoo_id,
        }
    try:
        rows = _rows_vinc_compras(engine)
    except Exception:
        rows = []

    for r in rows:
        item = _serializar_vinc_compra(r)
        if item["compra_operativo_id"]:
            por_compra_op_id.setdefault(int(item["compra_operativo_id"]), []).append(item)
        if item["compra_odoo_move_id"]:
            por_compra_move_id.setdefault(str(item["compra_odoo_move_id"]), []).append(item)
        if item["pago_operativo_id"]:
            por_pago_op_id.setdefault(int(item["pago_operativo_id"]), []).append(item)
        if item["pago_odoo_id"]:
            por_pago_odoo_id.setdefault(str(item["pago_odoo_id"]), []).append(item)

    return {
        "por_compra_op_id": por_compra_op_id,
        "por_compra_move_id": por_compra_move_id,
        "por_pago_op_id": por_pago_op_id,
        "por_pago_odoo_id": por_pago_odoo_id,
    }


def _serializar_vinc_compra(row: Any) -> dict[str, Any]:
    return {
        "vinc_id": int(row.id),
        "proveedor_odoo_id": int(row.proveedor_odoo_id) if row.proveedor_odoo_id else None,
        "proveedor_nombre": str(row.proveedor_nombre or ""),
        "pago_operativo_id": int(row.pago_operativo_id) if row.pago_operativo_id else None,
        "pago_odoo_id": str(row.pago_odoo_id or "") or None,
        "pago_ref": str(row.pago_ref or ""),
        "compra_operativo_id": int(row.compra_operativo_id) if row.compra_operativo_id else None,
        "compra_odoo_move_id": str(row.compra_odoo_move_id or "") or None,
        "compra_ref": str(row.compra_ref or ""),
        "monto_aplicado": _f2(row.monto_aplicado),
        "moneda": str(row.moneda or "USD"),
        "equiv_usd_bcv": _f2(row.equiv_usd_bcv),
        "tasa_bcv": float(row.tasa_bcv) if row.tasa_bcv is not None else None,
        "origen_vinculacion": str(row.origen_vinculacion or "manual"),
        "estado": str(row.estado or "VINCULADO_CXC"),
        "conciliado_odoo": bool(row.odoo_reconciliado),
        "odoo_move_id": int(row.odoo_move_id) if row.odoo_move_id else None,
        "odoo_detalle": str(row.odoo_detalle or ""),
        "vinculado_por": str(row.vinculado_por or ""),
        "creado_en": row.creado_en.isoformat() if row.creado_en else "",
        "puede_desvincular": not bool(row.odoo_reconciliado),
    }


# ── Helpers internos para recolectar órdenes/pagos ventas y compras ─────────


def _resolver_venta_objetivo(
    engine: Engine,
    repo_cxc: Any,
    *,
    so_id: str | None = None,
    documento_id: int | None = None,
) -> dict[str, Any]:
    so_norm = str(so_id or "").strip()
    doc_row = None
    with engine.connect() as conn:
        if documento_id is not None:
            doc_row = conn.execute(
                select(documentos_comerciales).where(
                    documentos_comerciales.c.documento_id == int(documento_id)
                )
            ).first()
        elif so_norm:
            doc_row = conn.execute(
                select(documentos_comerciales).where(
                    or_(
                        documentos_comerciales.c.odoo_order_name == so_norm,
                        documentos_comerciales.c.numero == so_norm,
                    )
                )
            ).first()

    if not so_norm and doc_row is not None:
        so_norm = str(doc_row.odoo_order_name or doc_row.numero).strip()

    ord_mirror = None
    if so_norm:
        try:
            ord_mirror = repo_cxc.get_orden(so_norm)
        except Exception:
            ord_mirror = None
        if ord_mirror is None:
            for o in repo_cxc.all_ordenes():
                if o.so_id == so_norm:
                    ord_mirror = o
                    break

    if doc_row is None and ord_mirror is None:
        raise VinculacionNoEncontrada(
            f"Orden o documento de venta '{so_id or documento_id}' no encontrado."
        )

    pid = None
    if doc_row is not None and doc_row.partner_odoo_id is not None:
        pid = int(doc_row.partner_odoo_id)
    elif ord_mirror is not None and str(ord_mirror.cliente_id or "").isdigit():
        pid = int(ord_mirror.cliente_id)

    cli_nombre = str(doc_row.cliente_nombre or "") if doc_row is not None else ""
    if not cli_nombre and ord_mirror is not None:
        cli = repo_cxc.get_cliente(ord_mirror.cliente_id)
        cli_nombre = cli.nombre if cli else f"Cliente #{ord_mirror.cliente_id}"

    total_usd = Decimal("0")
    if ord_mirror is not None:
        total_usd = q2(dec(ord_mirror.monto_total))
    elif doc_row is not None:
        total_usd = q2(dec(doc_row.total_con_iva or doc_row.subtotal))

    return {
        "so_id": so_norm,
        "documento_comercial_id": int(doc_row.documento_id) if doc_row is not None else None,
        "numero_cxc": str(doc_row.numero) if doc_row is not None else None,
        "odoo_order_id": (
            int(doc_row.odoo_order_id)
            if (doc_row is not None and doc_row.odoo_order_id)
            else (getattr(ord_mirror, "odoo_order_id", None) if ord_mirror else None)
        ),
        "odoo_order_name": (
            str(doc_row.odoo_order_name)
            if (doc_row is not None and doc_row.odoo_order_name)
            else (ord_mirror.so_id if ord_mirror else "")
        ),
        "factura_id": str(ord_mirror.factura_id or "") if ord_mirror else "",
        "partner_odoo_id": pid,
        "cliente_nombre": cli_nombre,
        "total_usd": total_usd,
        "en_ordenes_mirror": ord_mirror is not None,
    }


def _recolectar_pagos_cliente(
    engine: Engine,
    repo_cxc: Any,
    partner_odoo_id: int | None,
    cliente_nombre: str,
    por_fecha: dict[date, Decimal],
    fallback: Decimal,
) -> list[dict[str, Any]]:
    hoy = date.today()
    mapa_v = mapa_vinculaciones_ventas(engine, repo_cxc)
    nombre_target = norm_nombre(cliente_nombre)

    def _coincide_cliente(pid: Any, nom: str) -> bool:
        if partner_odoo_id is not None and pid is not None and str(pid).isdigit():
            return int(pid) == int(partner_odoo_id)
        return bool(nombre_target and norm_nombre(nom) == nombre_target)

    # Mapeo pago_odoo_id -> documento operativo CxC para trazabilidad unificada
    with engine.connect() as conn:
        ops_pago = list(
            conn.execute(
                select(documentos_operativos)
                .where(documentos_operativos.c.tipo == "pago")
                .where(documentos_operativos.c.estado != "RECHAZADO")
            ).all()
        )
    op_por_odoo_id: dict[str, Any] = {}
    for op in ops_pago:
        if op.odoo_res_id is not None and (not op.odoo_model or op.odoo_model == "account.payment"):
            op_por_odoo_id[str(op.odoo_res_id)] = op

    salida: list[dict[str, Any]] = []
    vistos_odoo_id: set[str] = set()

    # 1. Pagos espejo Odoo
    for row in repo_cxc.all_pagos_full():
        cid = str(row.get("cliente_id") or "").strip()
        if not _coincide_cliente(cid, ""):
            continue
        pid = str(row.get("pago_id") or "").strip()
        if not pid:
            continue
        vistos_odoo_id.add(pid)
        monto_orig = q2(dec(row.get("monto")))
        if monto_orig <= 0:
            continue
        mon = str(row.get("moneda") or "VES").strip().upper()
        f_d = _fecha_iso_o_hoy(row.get("fecha_pago"), hoy)
        monto_usd, _ = _equiv_usd_y_tasa(monto_orig, mon, f_d, por_fecha, fallback)

        vincs = mapa_v["por_pago_id"].get(pid, [])
        usado_orig = q2(sum((dec(v["monto_aplicado"]) for v in vincs), Decimal("0")))
        disp_orig = max(Decimal("0.00"), q2(monto_orig - usado_orig))
        disp_usd, _ = _equiv_usd_y_tasa(disp_orig, mon, f_d, por_fecha, fallback)
        op_match = op_por_odoo_id.get(pid)
        salida.append(
            {
                "clave": f"odoo:{pid}",
                "origen": "cxc" if op_match else "odoo",
                "pago_id": pid,
                "pago_operativo_id": int(op_match.documento_id) if op_match else None,
                "numero": str(op_match.numero)
                if op_match
                else str(row.get("numero_odoo") or f"PAGO-{pid}"),
                "odoo_numero": str(row.get("numero_odoo") or f"PAGO-{pid}"),
                "odoo_res_id": int(pid) if pid.isdigit() else None,
                "odoo_model": "account.payment",
                "es_zelle_tercero": False,
                "fecha": f_d.isoformat(),
                "moneda": mon,
                "monto_original": float(monto_orig),
                "monto_usd": float(monto_usd),
                "disponible_original": float(disp_orig),
                "disponible_usd": float(disp_usd),
                "referencia": str(row.get("referencia") or ""),
                "estado_pago": "CONFIRMADO_ODOO",
                "confirmado_prio": 0,
            }
        )

    # 2. Pagos operativos CxC aún no reflejados en espejo Odoo (incluye Zelle 3ros)
    for op in ops_pago:
        if op.estado in ("RECHAZADO", "BORRADOR", "INCOMPLETO", "ERROR"):
            continue
        if not _coincide_cliente(op.partner_odoo_id, op.contraparte_nombre):
            continue
        if (
            op.odoo_res_id is not None
            and (not op.odoo_model or op.odoo_model == "account.payment")
            and str(op.odoo_res_id) in vistos_odoo_id
        ):
            continue
        monto_orig = q2(dec(op.monto))
        if monto_orig <= 0:
            continue
        mon = str(op.moneda or "VES").strip().upper()
        f_d = _fecha_iso_o_hoy(op.fecha_documento or op.creado_en, hoy)
        monto_usd, _ = _equiv_usd_y_tasa(monto_orig, mon, f_d, por_fecha, fallback)
        vincs = mapa_v["por_pago_op_id"].get(int(op.documento_id), [])
        usado_orig = q2(sum((dec(v["monto_aplicado"]) for v in vincs), Decimal("0")))
        disp_orig = max(Decimal("0.00"), q2(monto_orig - usado_orig))
        disp_usd, _ = _equiv_usd_y_tasa(disp_orig, mon, f_d, por_fecha, fallback)
        est_txt = str(op.estado or "")
        es_conf = est_txt in ("CONFIRMADO_CXC", "ENVIADO_ODOO")
        salida.append(
            {
                "clave": f"op:{op.documento_id}",
                "origen": "cxc",
                "pago_id": (
                    str(op.odoo_res_id)
                    if (
                        op.odoo_res_id and (not op.odoo_model or op.odoo_model == "account.payment")
                    )
                    else None
                ),
                "pago_operativo_id": int(op.documento_id),
                "numero": str(op.numero),
                "odoo_numero": (
                    f"{'ASIENTO' if op.es_zelle_tercero else 'PAGO'}-{op.odoo_res_id}"
                    if op.odoo_res_id
                    else ""
                ),
                "odoo_res_id": int(op.odoo_res_id) if op.odoo_res_id else None,
                "odoo_model": str(
                    op.odoo_model or ("account.move" if op.es_zelle_tercero else "account.payment")
                ),
                "es_zelle_tercero": bool(op.es_zelle_tercero),
                "fecha": f_d.isoformat(),
                "moneda": mon,
                "monto_original": float(monto_orig),
                "monto_usd": float(monto_usd),
                "disponible_original": float(disp_orig),
                "disponible_usd": float(disp_usd),
                "referencia": str(op.referencia or op.numero_recibo or ""),
                "estado_pago": est_txt,
                "confirmado_prio": 0 if es_conf else 1,
            }
        )

    salida.sort(key=lambda x: (x["confirmado_prio"], x["fecha"], x["clave"]))
    return salida


def candidatos_pagos_para_venta(
    engine: Engine,
    repo_cxc: Any,
    *,
    so_id: str | None = None,
    documento_id: int | None = None,
) -> dict[str, Any]:
    """Devuelve resumen de la orden, vinculaciones actuales y pagos elegibles FIFO."""
    por_fecha, fallback = indice_tasas_bcv_local(repo_cxc)
    doc = _resolver_venta_objetivo(engine, repo_cxc, so_id=so_id, documento_id=documento_id)
    mapa_v = mapa_vinculaciones_ventas(engine, repo_cxc)
    actuales = list(mapa_v["por_so_id"].get(doc["so_id"], []))
    vinculado_usd = q2(sum((dec(v["equiv_usd_bcv"]) for v in actuales), Decimal("0")))
    saldo_pend_usd = max(Decimal("0.00"), q2(doc["total_usd"] - vinculado_usd))

    pagos_cli = _recolectar_pagos_cliente(
        engine, repo_cxc, doc["partner_odoo_id"], doc["cliente_nombre"], por_fecha, fallback
    )
    disponibles = [p for p in pagos_cli if p["disponible_original"] > 0.009]

    restante_usd = saldo_pend_usd
    hoy = date.today()
    for p in disponibles:
        p_disp_usd = q2(dec(p["disponible_usd"]))
        if restante_usd > 0 and p["confirmado_prio"] == 0 and p_disp_usd > 0:
            sug_usd = min(restante_usd, p_disp_usd)
            f_d = _fecha_iso_o_hoy(p["fecha"], hoy)
            sug_orig = min(
                q2(dec(p["disponible_original"])),
                _monto_orig_desde_usd(sug_usd, p["moneda"], f_d, por_fecha, fallback),
            )
            p["sugerido_fifo"] = True
            p["monto_sugerido_usd"] = float(sug_usd)
            p["monto_sugerido_original"] = float(sug_orig)
            restante_usd = max(Decimal("0.00"), q2(restante_usd - sug_usd))
        else:
            p["sugerido_fifo"] = False
            p["monto_sugerido_usd"] = 0.0
            p["monto_sugerido_original"] = 0.0

    return {
        "documento": {
            **doc,
            "total_usd": float(doc["total_usd"]),
            "vinculado_usd": float(vinculado_usd),
            "saldo_pendiente_usd": float(saldo_pend_usd),
        },
        "vinculaciones": actuales,
        "candidatos": disponibles,
    }


def candidatos_ventas_para_pago(
    engine: Engine,
    repo_cxc: Any,
    *,
    pago_id: str | None = None,
    pago_operativo_id: int | None = None,
) -> dict[str, Any]:
    """Devuelve detalle de un pago recibido, sus vinculaciones y pedidos abiertos FIFO."""
    por_fecha, fallback = indice_tasas_bcv_local(repo_cxc)
    hoy = date.today()
    pid_str = str(pago_id or "").strip()

    op_row = None
    with engine.connect() as conn:
        if pago_operativo_id is not None:
            op_row = conn.execute(
                select(documentos_operativos).where(
                    documentos_operativos.c.documento_id == int(pago_operativo_id)
                )
            ).first()
        elif pid_str.isdigit():
            op_row = conn.execute(
                select(documentos_operativos).where(
                    documentos_operativos.c.tipo == "pago",
                    documentos_operativos.c.odoo_res_id == int(pid_str),
                )
            ).first()

    if (
        not pid_str
        and op_row is not None
        and op_row.odoo_res_id is not None
        and (not op_row.odoo_model or op_row.odoo_model == "account.payment")
    ):
        pid_str = str(op_row.odoo_res_id)

    pago_mirror = repo_cxc.get_pago(pid_str) if pid_str else None
    if pago_mirror is None and op_row is None:
        raise VinculacionNoEncontrada("Pago recibido no encontrado.")

    partner_id = None
    contraparte_nom = ""
    if op_row is not None:
        partner_id = op_row.partner_odoo_id
        contraparte_nom = str(op_row.contraparte_nombre or "")
    if pago_mirror is not None:
        if partner_id is None and str(pago_mirror.cliente_id or "").isdigit():
            partner_id = int(pago_mirror.cliente_id)
        if not contraparte_nom:
            cli = repo_cxc.get_cliente(pago_mirror.cliente_id)
            contraparte_nom = cli.nombre if cli else f"Cliente #{pago_mirror.cliente_id}"

    monto_orig = q2(
        dec(pago_mirror.monto if pago_mirror is not None else op_row.monto)  # type: ignore[union-attr]
    )
    mon = str(
        getattr(pago_mirror.moneda, "value", pago_mirror.moneda)
        if pago_mirror is not None
        else op_row.moneda  # type: ignore[union-attr]
        or "VES"
    ).upper()
    f_pago = _fecha_iso_o_hoy(
        pago_mirror.fecha_pago
        if pago_mirror is not None
        else (op_row.fecha_documento or op_row.creado_en),  # type: ignore[union-attr]
        hoy,
    )
    monto_usd, _ = _equiv_usd_y_tasa(monto_orig, mon, f_pago, por_fecha, fallback)

    mapa_v = mapa_vinculaciones_ventas(engine, repo_cxc)
    actuales = list(
        mapa_v["por_pago_id"].get(pid_str, [])
        if pid_str
        else mapa_v["por_pago_op_id"].get(int(op_row.documento_id), [])  # type: ignore[union-attr]
    )
    usado_orig = q2(sum((dec(v["monto_aplicado"]) for v in actuales), Decimal("0")))
    disp_orig = max(Decimal("0.00"), q2(monto_orig - usado_orig))
    disp_usd, _ = _equiv_usd_y_tasa(disp_orig, mon, f_pago, por_fecha, fallback)

    # Recolectar órdenes confirmadas del cliente
    nom_norm = norm_nombre(contraparte_nom)
    with engine.connect() as conn:
        docs_com = list(
            conn.execute(
                select(documentos_comerciales).where(
                    documentos_comerciales.c.tipo == "pedido",
                    documentos_comerciales.c.estado == "ENVIADO_ODOO",
                )
            ).all()
        )
    doc_por_so: dict[str, Any] = {
        str(dc.odoo_order_name or dc.numero).strip(): dc for dc in docs_com
    }

    ordenes_cand: list[dict[str, Any]] = []
    vistas_so: set[str] = set()
    for o in repo_cxc.all_ordenes():
        if str(o.estado_orden or "").lower() not in ("sale", "done"):
            continue
        cid_match = (
            partner_id is not None
            and str(o.cliente_id).isdigit()
            and int(o.cliente_id) == int(partner_id)
        )
        dc = doc_por_so.get(o.so_id)
        nom_dc = norm_nombre(dc.cliente_nombre) if dc is not None else ""
        if not cid_match and not (nom_norm and nom_dc == nom_norm):
            continue
        vistas_so.add(o.so_id)
        tot = q2(dec(o.monto_total))
        vincs_so = mapa_v["por_so_id"].get(o.so_id, [])
        pag_so = q2(sum((dec(v["equiv_usd_bcv"]) for v in vincs_so), Decimal("0")))
        sal_so = max(Decimal("0.00"), q2(tot - pag_so))
        if sal_so <= Decimal("0.00"):
            continue
        ordenes_cand.append(
            {
                "so_id": o.so_id,
                "documento_comercial_id": int(dc.documento_id) if dc is not None else None,
                "numero_cxc": str(dc.numero) if dc is not None else None,
                "fecha": o.fecha.isoformat(),
                "total_usd": float(tot),
                "vinculado_usd": float(pag_so),
                "saldo_pendiente_usd": float(sal_so),
                "factura_id": o.factura_id,
                "odoo_order_id": getattr(o, "odoo_order_id", None)
                or (dc.odoo_order_id if dc else None),
            }
        )

    for so_key, dc in doc_por_so.items():
        if so_key in vistas_so:
            continue
        cid_match = (
            partner_id is not None
            and dc.partner_odoo_id is not None
            and int(dc.partner_odoo_id) == int(partner_id)
        )
        if not cid_match and not (nom_norm and norm_nombre(dc.cliente_nombre) == nom_norm):
            continue
        tot = q2(dec(dc.total_con_iva or dc.subtotal))
        vincs_so = mapa_v["por_so_id"].get(so_key, [])
        pag_so = q2(sum((dec(v["equiv_usd_bcv"]) for v in vincs_so), Decimal("0")))
        sal_so = max(Decimal("0.00"), q2(tot - pag_so))
        if sal_so <= Decimal("0.00"):
            continue
        ordenes_cand.append(
            {
                "so_id": so_key,
                "documento_comercial_id": int(dc.documento_id),
                "numero_cxc": str(dc.numero),
                "fecha": (dc.creado_en.date().isoformat() if dc.creado_en else hoy.isoformat()),
                "total_usd": float(tot),
                "vinculado_usd": float(pag_so),
                "saldo_pendiente_usd": float(sal_so),
                "factura_id": None,
                "odoo_order_id": dc.odoo_order_id,
            }
        )

    ordenes_cand.sort(key=lambda x: (x["fecha"], x["so_id"]))
    rest_usd = disp_usd
    for ord_c in ordenes_cand:
        sal = q2(dec(ord_c["saldo_pendiente_usd"]))
        if rest_usd > 0 and sal > 0:
            sug_usd = min(rest_usd, sal)
            sug_orig = min(
                disp_orig, _monto_orig_desde_usd(sug_usd, mon, f_pago, por_fecha, fallback)
            )
            ord_c["sugerido_fifo"] = True
            ord_c["monto_sugerido_usd"] = float(sug_usd)
            ord_c["monto_sugerido_original"] = float(sug_orig)
            rest_usd = max(Decimal("0.00"), q2(rest_usd - sug_usd))
        else:
            ord_c["sugerido_fifo"] = False
            ord_c["monto_sugerido_usd"] = 0.0
            ord_c["monto_sugerido_original"] = 0.0

    return {
        "pago": {
            "pago_id": pid_str or None,
            "pago_operativo_id": int(op_row.documento_id) if op_row is not None else None,
            "numero": str(op_row.numero) if op_row is not None else f"PAGO-{pid_str}",
            "partner_odoo_id": partner_id,
            "cliente_nombre": contraparte_nom,
            "fecha": f_pago.isoformat(),
            "moneda": mon,
            "monto_original": float(monto_orig),
            "monto_usd": float(monto_usd),
            "disponible_original": float(disp_orig),
            "disponible_usd": float(disp_usd),
        },
        "vinculaciones": actuales,
        "candidatos": ordenes_cand,
    }


def _intentar_reconciliar_odoo_venta(
    escritor: Any,
    *,
    so_name: str,
    odoo_order_id: int | None,
    factura_id: str | None,
    pago_model: str,
    pago_res_id: int | None,
) -> dict[str, Any]:
    if escritor is None or pago_res_id is None:
        return {
            "reconciliado": False,
            "factura_move_id": None,
            "motivo": "Vinculado en CxC (sin escritura Odoo activa o pago sin ID en Odoo).",
        }
    fact_move_id = int(factura_id) if factura_id and str(factura_id).isdigit() else None
    if fact_move_id is None:
        fact_move_id = escritor.buscar_factura_abierta_de_orden(so_name, order_id=odoo_order_id)
    if fact_move_id is None:
        return {
            "reconciliado": False,
            "factura_move_id": None,
            "motivo": (
                f"Vinculado en CxC (la orden {so_name} aún no tiene factura publicada "
                "con saldo en Odoo)."
            ),
        }
    return dict(
        escritor.reconciliar_pago_con_documento(
            pago_model=pago_model,
            pago_res_id=int(pago_res_id),
            factura_move_id=fact_move_id,
            es_cliente=True,
        )
    )


def vincular_pago_venta(
    engine: Engine,
    repo_cxc: Any,
    *,
    so_id: str | None = None,
    documento_comercial_id: int | None = None,
    pago_id: str | None = None,
    pago_operativo_id: int | None = None,
    monto_aplicado: Decimal | float | str | None = None,
    origen_vinculacion: str = "manual",
    actor: str = "sistema",
    escritor: Any = None,
) -> dict[str, Any]:
    """Vincula un pago recibido contra un pedido/orden y opcionalmente concilia en Odoo."""
    doc = _resolver_venta_objetivo(
        engine, repo_cxc, so_id=so_id, documento_id=documento_comercial_id
    )
    por_fecha, fallback = indice_tasas_bcv_local(repo_cxc)
    hoy = date.today()
    pid_str = str(pago_id or "").strip()

    op_row = None
    with engine.connect() as conn:
        if pago_operativo_id is not None:
            op_row = conn.execute(
                select(documentos_operativos).where(
                    documentos_operativos.c.documento_id == int(pago_operativo_id),
                    documentos_operativos.c.tipo == "pago",
                )
            ).first()
        elif pid_str.isdigit():
            op_row = conn.execute(
                select(documentos_operativos).where(
                    documentos_operativos.c.tipo == "pago",
                    documentos_operativos.c.odoo_res_id == int(pid_str),
                )
            ).first()

    if (
        not pid_str
        and op_row is not None
        and op_row.odoo_res_id is not None
        and (not op_row.odoo_model or op_row.odoo_model == "account.payment")
    ):
        pid_str = str(op_row.odoo_res_id)

    pago_mirror = repo_cxc.get_pago(pid_str) if pid_str else None
    if pago_mirror is None and op_row is None:
        raise VinculacionInvalida("Debes indicar un pago recibido válido.")

    monto_total_pago = q2(
        dec(pago_mirror.monto if pago_mirror is not None else op_row.monto)  # type: ignore[union-attr]
    )
    mon = str(
        getattr(pago_mirror.moneda, "value", pago_mirror.moneda)
        if pago_mirror is not None
        else op_row.moneda  # type: ignore[union-attr]
        or "VES"
    ).upper()
    f_pago = _fecha_iso_o_hoy(
        pago_mirror.fecha_pago
        if pago_mirror is not None
        else (op_row.fecha_documento or op_row.creado_en),  # type: ignore[union-attr]
        hoy,
    )

    # Calcular saldo disponible real del pago antes de vincular
    mapa_v = mapa_vinculaciones_ventas(engine, repo_cxc)
    prev_vincs = (
        mapa_v["por_pago_id"].get(pid_str, [])
        if pid_str
        else mapa_v["por_pago_op_id"].get(int(op_row.documento_id), [])  # type: ignore[union-attr]
    )
    usado_prev = q2(sum((dec(v["monto_aplicado"]) for v in prev_vincs), Decimal("0")))
    disp_orig = max(Decimal("0.00"), q2(monto_total_pago - usado_prev))

    if monto_aplicado is None or str(monto_aplicado).strip() == "":
        monto_dec = disp_orig
    else:
        monto_dec = q2(dec(monto_aplicado))
    if monto_dec <= 0:
        raise VinculacionInvalida("El monto a vincular debe ser mayor que cero.")
    if monto_total_pago > 0 and monto_dec > disp_orig + Decimal("0.05"):
        raise VinculacionInvalida(
            f"El monto ({monto_dec}) supera el saldo disponible del pago ({disp_orig} {mon})."
        )

    equiv_usd, tasa_bcv = _equiv_usd_y_tasa(monto_dec, mon, f_pago, por_fecha, fallback)

    # Intentar reconciliación en Odoo si el cliente/factura/pago existen allá
    pago_res_odoo = (
        int(pid_str)
        if pid_str.isdigit()
        else (int(op_row.odoo_res_id) if (op_row is not None and op_row.odoo_res_id) else None)
    )
    pago_model_odoo = (
        str(op_row.odoo_model or ("account.move" if op_row.es_zelle_tercero else "account.payment"))
        if op_row is not None
        else "account.payment"
    )
    so_name_odoo = doc["odoo_order_name"] or doc["so_id"]
    rec_odoo = _intentar_reconciliar_odoo_venta(
        escritor,
        so_name=so_name_odoo,
        odoo_order_id=doc["odoo_order_id"],
        factura_id=doc["factura_id"],
        pago_model=pago_model_odoo,
        pago_res_id=pago_res_odoo,
    )
    reconciliado = bool(rec_odoo.get("reconciliado"))
    odoo_motivo = str(rec_odoo.get("motivo") or "")
    fact_move_id = rec_odoo.get("factura_move_id")

    # Ruta 1: tabla espejo `vinculaciones` cuando tanto pago como orden existen en espejo
    if pago_mirror is not None and doc["en_ordenes_mirror"]:
        t_bcv_eff = tasa_bcv or tasa_bcv_en_fecha(f_pago, por_fecha, fallback) or Decimal("1")
        t_bin_eff = _tasa_binance_cercana(repo_cxc, f_pago, t_bcv_eff)
        mon_enum = Moneda.VES if mon == "VES" else Moneda.USD
        vinc_id = f"VINC_{pid_str}_{doc['so_id']}"
        try:
            prev_same = repo_cxc.get_vinculacion(vinc_id)
        except Exception:
            prev_same = None
        monto_final = (
            q2(prev_same.monto_aplicado + monto_dec) if prev_same is not None else monto_dec
        )
        usd_bcv, ves_bcv = equivalentes_bcv(monto_final, mon_enum, t_bcv_eff)
        usd_bin, ves_bin = equivalentes_binance(monto_final, mon_enum, t_bin_eff)
        vinc_obj = Vinculacion(
            vinc_id=vinc_id,
            pago_id=pid_str,
            so_id=doc["so_id"],
            monto_aplicado=monto_final,
            hora_pago_confirmada=datetime.combine(f_pago, datetime.min.time()),
            tasa_bcv_aplicada=t_bcv_eff,
            tasa_binance_aplicada=t_bin_eff,
            es_tasa_heredada=False,
            equiv_usd_bcv=usd_bcv,
            equiv_usd_binance=usd_bin,
            equiv_ves_bcv=ves_bcv,
            equiv_ves_binance=ves_bin,
            confirmado_por=(f"{actor} (FIFO)" if origen_vinculacion == "fifo" else actor),
            timestamp_registro=datetime.now(),
            estado=(EstadoVinculacion.CONCILIADO if reconciliado else EstadoVinculacion.PENDIENTE),
            moneda_abono=mon_enum,
            tipo_tasa_abono=TipoTasa.BCV,
            bcv_variante="USD",
        )
        repo_cxc.update_vinculacion(vinc_obj)
        return {
            "ok": True,
            "vinculacion": _serializar_vinc_mirror_venta(vinc_obj),
            "odoo_reconciliado": reconciliado,
            "odoo_motivo": odoo_motivo,
        }

    # Ruta 2: tabla operativa `vinculaciones_ventas_op` (PAG-*, Zelle 3ro o PED-* antes del sync)
    ahora = datetime.now()
    with engine.begin() as conn:
        fila = conn.execute(
            insert(vinculaciones_ventas_op)
            .values(
                pago_operativo_id=(int(op_row.documento_id) if op_row is not None else None),
                pago_odoo_id=pid_str or None,
                so_id=doc["so_id"],
                documento_comercial_id=doc["documento_comercial_id"],
                partner_odoo_id=doc["partner_odoo_id"],
                monto_aplicado=monto_dec,
                moneda=mon,
                equiv_usd_bcv=equiv_usd,
                tasa_bcv=tasa_bcv,
                origen_vinculacion=origen_vinculacion,
                estado="RECONCILIADO_ODOO" if reconciliado else "VINCULADO_CXC",
                odoo_reconciliado=reconciliado,
                odoo_move_id=int(fact_move_id) if fact_move_id else None,
                odoo_detalle=odoo_motivo,
                vinculado_por=actor,
                creado_en=ahora,
            )
            .returning(*vinculaciones_ventas_op.c)
        ).one()

    return {
        "ok": True,
        "vinculacion": _serializar_vinc_op_venta(fila),
        "odoo_reconciliado": reconciliado,
        "odoo_motivo": odoo_motivo,
    }


def desvincular_pago_venta(
    engine: Engine,
    repo_cxc: Any,
    *,
    origen: str,
    vinc_id: str,
) -> dict[str, Any]:
    org = (origen or "").strip().lower()
    if org in ("cxc_op", "cxc", "op"):
        if not str(vinc_id).isdigit():
            raise VinculacionInvalida("ID de vinculación inválido.")
        vid = int(vinc_id)
        with engine.begin() as conn:
            row = conn.execute(
                select(vinculaciones_ventas_op).where(vinculaciones_ventas_op.c.id == vid)
            ).first()
            if row is None:
                raise VinculacionNoEncontrada("Vinculación no encontrada.")
            if bool(row.odoo_reconciliado):
                raise VinculacionInvalida("Ya fue conciliada en Odoo; no se puede desvincular.")
            conn.execute(delete(vinculaciones_ventas_op).where(vinculaciones_ventas_op.c.id == vid))
        return {"ok": True, "eliminado": vid}

    with engine.begin() as conn:
        row_m = conn.execute(
            select(vinculaciones).where(vinculaciones.c.vinc_id == vinc_id)
        ).first()
        if row_m is None:
            raise VinculacionNoEncontrada("Vinculación espejo no encontrada.")
        if str(row_m.estado or "").lower() == EstadoVinculacion.CONCILIADO.value:
            raise VinculacionInvalida("Ya está conciliada en Odoo; no se puede desvincular.")
        conn.execute(delete(vinculaciones).where(vinculaciones.c.vinc_id == vinc_id))
    return {"ok": True, "eliminado": vinc_id}


# ── Compras / Gastos ↔ Pagos Enviados / Zelle 3ros ───────────────────────────


def _resolver_compra_objetivo(
    engine: Engine,
    repo_cxc: Any,
    *,
    compra_operativo_id: int | None = None,
    compra_odoo_move_id: str | None = None,
) -> dict[str, Any]:
    op_row = None
    mid_str = str(compra_odoo_move_id or "").strip()
    with engine.connect() as conn:
        if compra_operativo_id is not None:
            op_row = conn.execute(
                select(documentos_operativos).where(
                    documentos_operativos.c.documento_id == int(compra_operativo_id),
                    documentos_operativos.c.tipo.in_(("factura_proveedor", "gasto")),
                )
            ).first()
        elif mid_str.isdigit():
            op_row = conn.execute(
                select(documentos_operativos).where(
                    documentos_operativos.c.tipo.in_(("factura_proveedor", "gasto")),
                    documentos_operativos.c.odoo_res_id == int(mid_str),
                )
            ).first()

    if not mid_str and op_row is not None and op_row.odoo_res_id is not None:
        mid_str = str(op_row.odoo_res_id)

    odoo_row = None
    if mid_str:
        try:
            for c in repo_cxc.all_compras_gastos_odoo():
                if str(c.get("move_id") or "").strip() == mid_str:
                    odoo_row = c
                    break
        except Exception:
            odoo_row = None

    if op_row is None and odoo_row is None:
        raise VinculacionNoEncontrada("Factura de proveedor o gasto no encontrado.")

    hoy = date.today()
    por_fecha, fallback = indice_tasas_bcv_local(repo_cxc)

    if op_row is not None:
        pid = op_row.partner_odoo_id
        prov_nom = str(op_row.contraparte_nombre or "")
        monto_orig = q2(dec(op_row.monto))
        mon = str(op_row.moneda or "VES").upper()
        f_d = _fecha_iso_o_hoy(op_row.fecha_documento or op_row.creado_en, hoy)
        ref = str(op_row.numero_documento or op_row.numero or "")
        tipo_doc = str(op_row.tipo)
    else:
        assert odoo_row is not None
        pid = odoo_row.get("partner_odoo_id")
        prov_nom = str(odoo_row.get("proveedor_nombre") or "")
        monto_orig = q2(dec(odoo_row.get("monto_total")))
        mon = str(odoo_row.get("moneda") or "VES").upper()
        f_d = _fecha_iso_o_hoy(odoo_row.get("fecha_documento"), hoy)
        ref = str(odoo_row.get("numero_odoo") or odoo_row.get("referencia") or f"MOVE-{mid_str}")
        tipo_doc = str(odoo_row.get("tipo_cxc") or "factura_proveedor")

    total_usd, _ = _equiv_usd_y_tasa(monto_orig, mon, f_d, por_fecha, fallback)
    return {
        "compra_operativo_id": int(op_row.documento_id) if op_row is not None else None,
        "compra_odoo_move_id": mid_str or None,
        "numero": str(op_row.numero) if op_row is not None else ref,
        "compra_ref": ref,
        "tipo": tipo_doc,
        "proveedor_odoo_id": int(pid) if pid is not None and str(pid).isdigit() else None,
        "proveedor_nombre": prov_nom,
        "fecha": f_d.isoformat(),
        "moneda": mon,
        "monto_original": float(monto_orig),
        "total_usd": total_usd,
    }


def _terceros_proveedor_ids(engine: Engine) -> dict[int, int]:
    """Devuelve ``cuenta_id -> proveedor_odoo_id`` para Zelle a Terceros."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(select(cuentas_terceros)).all()
        return {int(r.cuenta_id): int(r.proveedor_odoo_id) for r in rows if r.proveedor_odoo_id}
    except Exception:
        return {}


def _recolectar_pagos_proveedor(
    engine: Engine,
    repo_cxc: Any,
    proveedor_odoo_id: int | None,
    proveedor_nombre: str,
    por_fecha: dict[date, Decimal],
    fallback: Decimal,
) -> list[dict[str, Any]]:
    hoy = date.today()
    mapa_c = mapa_vinculaciones_compras(engine)
    nom_norm = norm_nombre(proveedor_nombre)
    terceros_map = _terceros_proveedor_ids(engine)

    def _coincide_prov(pid: Any, nom: str) -> bool:
        if proveedor_odoo_id is not None and pid is not None and str(pid).isdigit():
            return int(pid) == int(proveedor_odoo_id)
        return bool(nom_norm and norm_nombre(nom) == nom_norm)

    with engine.connect() as conn:
        ops_out = list(
            conn.execute(
                select(documentos_operativos).where(
                    or_(
                        documentos_operativos.c.tipo == "pago_proveedor",
                        documentos_operativos.c.es_zelle_tercero.is_(True),
                    ),
                    documentos_operativos.c.estado != "RECHAZADO",
                )
            ).all()
        )

    op_por_odoo_id: dict[str, Any] = {}
    for op in ops_out:
        if op.tipo == "pago_proveedor" and op.odoo_res_id is not None:
            op_por_odoo_id[str(op.odoo_res_id)] = op

    salida: list[dict[str, Any]] = []
    vistos_odoo: set[str] = set()

    # 1. Pagos salientes espejo Odoo
    try:
        pagos_od = list(repo_cxc.all_pagos_proveedores_odoo())
    except Exception:
        pagos_od = []
    for row in pagos_od:
        if not _coincide_prov(row.get("partner_odoo_id"), str(row.get("proveedor_nombre") or "")):
            continue
        pid = str(row.get("pago_id") or "").strip()
        if not pid:
            continue
        vistos_odoo.add(pid)
        monto_orig = q2(dec(row.get("monto")))
        if monto_orig <= 0:
            continue
        mon = str(row.get("moneda") or "USD").upper()
        f_d = _fecha_iso_o_hoy(row.get("fecha_pago"), hoy)
        monto_usd, _ = _equiv_usd_y_tasa(monto_orig, mon, f_d, por_fecha, fallback)
        vincs = mapa_c["por_pago_odoo_id"].get(pid, [])
        usado_orig = q2(sum((dec(v["monto_aplicado"]) for v in vincs), Decimal("0")))
        disp_orig = max(Decimal("0.00"), q2(monto_orig - usado_orig))
        disp_usd, _ = _equiv_usd_y_tasa(disp_orig, mon, f_d, por_fecha, fallback)
        op_m = op_por_odoo_id.get(pid)
        salida.append(
            {
                "clave": f"odoo:{pid}",
                "origen": "cxc" if op_m else "odoo",
                "pago_operativo_id": int(op_m.documento_id) if op_m else None,
                "pago_odoo_id": pid,
                "numero": str(op_m.numero)
                if op_m
                else str(row.get("numero_odoo") or f"PAGO-{pid}"),
                "odoo_numero": str(row.get("numero_odoo") or f"PAGO-{pid}"),
                "odoo_model": "account.payment",
                "es_zelle_tercero": False,
                "fecha": f_d.isoformat(),
                "moneda": mon,
                "monto_original": float(monto_orig),
                "monto_usd": float(monto_usd),
                "disponible_original": float(disp_orig),
                "disponible_usd": float(disp_usd),
                "referencia": str(row.get("referencia") or ""),
                "estado_pago": "CONFIRMADO_ODOO",
                "confirmado_prio": 0,
            }
        )

    # 2. Pagos a proveedor CxC (PPR-*) y Zelle 3ros confirmados hacia el proveedor
    for op in ops_out:
        if op.estado in ("RECHAZADO", "BORRADOR", "INCOMPLETO", "ERROR"):
            continue
        es_z3 = bool(op.es_zelle_tercero)
        if es_z3:
            if op.estado not in ("CONFIRMADO_CXC", "ENVIADO_ODOO") and not bool(op.confirmado_cxc):
                continue
            pid_prov = terceros_map.get(int(op.cuenta_tercero_id)) if op.cuenta_tercero_id else None
            nom_prov = str(op.beneficiario_tercero or "")
            if not _coincide_prov(pid_prov, nom_prov):
                continue
        else:
            if op.odoo_res_id is not None and str(op.odoo_res_id) in vistos_odoo:
                continue
            if not _coincide_prov(op.partner_odoo_id, str(op.contraparte_nombre or "")):
                continue

        monto_orig = q2(dec(op.monto))
        if monto_orig <= 0:
            continue
        mon = str(op.moneda or "USD").upper()
        f_d = _fecha_iso_o_hoy(op.fecha_documento or op.creado_en, hoy)
        monto_usd, _ = _equiv_usd_y_tasa(monto_orig, mon, f_d, por_fecha, fallback)
        vincs = mapa_c["por_pago_op_id"].get(int(op.documento_id), [])
        usado_orig = q2(sum((dec(v["monto_aplicado"]) for v in vincs), Decimal("0")))
        disp_orig = max(Decimal("0.00"), q2(monto_orig - usado_orig))
        disp_usd, _ = _equiv_usd_y_tasa(disp_orig, mon, f_d, por_fecha, fallback)
        es_conf = str(op.estado or "") in ("CONFIRMADO_CXC", "ENVIADO_ODOO")
        salida.append(
            {
                "clave": f"op:{op.documento_id}",
                "origen": "cxc",
                "pago_operativo_id": int(op.documento_id),
                "pago_odoo_id": (
                    str(op.odoo_res_id)
                    if (
                        op.odoo_res_id
                        and (
                            not es_z3 and (not op.odoo_model or op.odoo_model == "account.payment")
                        )
                    )
                    else None
                ),
                "numero": str(op.numero),
                "odoo_numero": (
                    f"{'ASIENTO' if es_z3 else 'PAGO'}-{op.odoo_res_id}" if op.odoo_res_id else ""
                ),
                "odoo_model": "account.move" if es_z3 else "account.payment",
                "es_zelle_tercero": es_z3,
                "fecha": f_d.isoformat(),
                "moneda": mon,
                "monto_original": float(monto_orig),
                "monto_usd": float(monto_usd),
                "disponible_original": float(disp_orig),
                "disponible_usd": float(disp_usd),
                "referencia": str(op.referencia or op.numero_recibo or ""),
                "estado_pago": str(op.estado or ""),
                "confirmado_prio": 0 if es_conf else 1,
            }
        )

    salida.sort(key=lambda x: (x["confirmado_prio"], x["fecha"], x["clave"]))
    return salida


def candidatos_pagos_para_compra(
    engine: Engine,
    repo_cxc: Any,
    *,
    compra_operativo_id: int | None = None,
    compra_odoo_move_id: str | None = None,
) -> dict[str, Any]:
    """Devuelve resumen de la factura/gasto y pagos enviados disponibles en orden FIFO."""
    por_fecha, fallback = indice_tasas_bcv_local(repo_cxc)
    doc = _resolver_compra_objetivo(
        engine,
        repo_cxc,
        compra_operativo_id=compra_operativo_id,
        compra_odoo_move_id=compra_odoo_move_id,
    )
    mapa_c = mapa_vinculaciones_compras(engine)
    actuales = list(
        mapa_c["por_compra_op_id"].get(int(doc["compra_operativo_id"]), [])
        if doc["compra_operativo_id"] is not None
        else mapa_c["por_compra_move_id"].get(str(doc["compra_odoo_move_id"]), [])
    )
    vinc_usd = q2(sum((dec(v["equiv_usd_bcv"]) for v in actuales), Decimal("0")))
    saldo_pend_usd = max(Decimal("0.00"), q2(doc["total_usd"] - vinc_usd))

    pagos_prov = _recolectar_pagos_proveedor(
        engine, repo_cxc, doc["proveedor_odoo_id"], doc["proveedor_nombre"], por_fecha, fallback
    )
    disponibles = [p for p in pagos_prov if p["disponible_original"] > 0.009]

    rest_usd = saldo_pend_usd
    hoy = date.today()
    for p in disponibles:
        p_disp_usd = q2(dec(p["disponible_usd"]))
        if rest_usd > 0 and p["confirmado_prio"] == 0 and p_disp_usd > 0:
            sug_usd = min(rest_usd, p_disp_usd)
            f_d = _fecha_iso_o_hoy(p["fecha"], hoy)
            sug_orig = min(
                q2(dec(p["disponible_original"])),
                _monto_orig_desde_usd(sug_usd, p["moneda"], f_d, por_fecha, fallback),
            )
            p["sugerido_fifo"] = True
            p["monto_sugerido_usd"] = float(sug_usd)
            p["monto_sugerido_original"] = float(sug_orig)
            rest_usd = max(Decimal("0.00"), q2(rest_usd - sug_usd))
        else:
            p["sugerido_fifo"] = False
            p["monto_sugerido_usd"] = 0.0
            p["monto_sugerido_original"] = 0.0

    return {
        "documento": {
            **doc,
            "total_usd": float(doc["total_usd"]),
            "vinculado_usd": float(vinc_usd),
            "saldo_pendiente_usd": float(saldo_pend_usd),
        },
        "vinculaciones": actuales,
        "candidatos": disponibles,
    }


def candidatos_compras_para_pago(
    engine: Engine,
    repo_cxc: Any,
    *,
    pago_operativo_id: int | None = None,
    pago_odoo_id: str | None = None,
) -> dict[str, Any]:
    """Devuelve facturas/gastos abiertos del proveedor para aplicar un pago enviado (FIFO)."""
    por_fecha, fallback = indice_tasas_bcv_local(repo_cxc)
    hoy = date.today()
    pid_str = str(pago_odoo_id or "").strip()

    op_row = None
    with engine.connect() as conn:
        if pago_operativo_id is not None:
            op_row = conn.execute(
                select(documentos_operativos).where(
                    documentos_operativos.c.documento_id == int(pago_operativo_id)
                )
            ).first()
        elif pid_str.isdigit():
            op_row = conn.execute(
                select(documentos_operativos).where(
                    documentos_operativos.c.tipo == "pago_proveedor",
                    documentos_operativos.c.odoo_res_id == int(pid_str),
                )
            ).first()

    if (
        not pid_str
        and op_row is not None
        and op_row.odoo_res_id is not None
        and not op_row.es_zelle_tercero
        and (not op_row.odoo_model or op_row.odoo_model == "account.payment")
    ):
        pid_str = str(op_row.odoo_res_id)

    odoo_pago = None
    if pid_str:
        try:
            for r in repo_cxc.all_pagos_proveedores_odoo():
                if str(r.get("pago_id") or "").strip() == pid_str:
                    odoo_pago = r
                    break
        except Exception:
            odoo_pago = None

    if op_row is None and odoo_pago is None:
        raise VinculacionNoEncontrada("Pago a proveedor no encontrado.")

    terceros_map = _terceros_proveedor_ids(engine)
    if op_row is not None and op_row.es_zelle_tercero:
        prov_id = (
            terceros_map.get(int(op_row.cuenta_tercero_id)) if op_row.cuenta_tercero_id else None
        )
        prov_nom = str(op_row.beneficiario_tercero or "")
    elif op_row is not None:
        prov_id = op_row.partner_odoo_id
        prov_nom = str(op_row.contraparte_nombre or "")
    else:
        assert odoo_pago is not None
        prov_id = odoo_pago.get("partner_odoo_id")
        prov_nom = str(odoo_pago.get("proveedor_nombre") or "")

    monto_orig = q2(
        dec(op_row.monto if op_row is not None else odoo_pago.get("monto"))  # type: ignore[union-attr]
    )
    mon = str(
        op_row.moneda if op_row is not None else odoo_pago.get("moneda") or "USD"  # type: ignore[union-attr]
    ).upper()
    f_pago = _fecha_iso_o_hoy(
        (op_row.fecha_documento or op_row.creado_en)
        if op_row is not None
        else odoo_pago.get("fecha_pago"),  # type: ignore[union-attr]
        hoy,
    )
    monto_usd, _ = _equiv_usd_y_tasa(monto_orig, mon, f_pago, por_fecha, fallback)

    mapa_c = mapa_vinculaciones_compras(engine)
    actuales = list(
        mapa_c["por_pago_op_id"].get(int(op_row.documento_id), [])
        if op_row is not None
        else mapa_c["por_pago_odoo_id"].get(pid_str, [])
    )
    usado_orig = q2(sum((dec(v["monto_aplicado"]) for v in actuales), Decimal("0")))
    disp_orig = max(Decimal("0.00"), q2(monto_orig - usado_orig))
    disp_usd, _ = _equiv_usd_y_tasa(disp_orig, mon, f_pago, por_fecha, fallback)

    nom_norm = norm_nombre(prov_nom)

    def _coincide_p(p_pid: Any, p_nom: str) -> bool:
        if prov_id is not None and p_pid is not None and str(p_pid).isdigit():
            return int(p_pid) == int(prov_id)
        return bool(nom_norm and norm_nombre(p_nom) == nom_norm)

    with engine.connect() as conn:
        ops_cmp = list(
            conn.execute(
                select(documentos_operativos).where(
                    documentos_operativos.c.tipo.in_(("factura_proveedor", "gasto")),
                    documentos_operativos.c.estado != "RECHAZADO",
                )
            ).all()
        )

    compras_cand: list[dict[str, Any]] = []
    vistas_move: set[str] = set()
    for op_c in ops_cmp:
        if not _coincide_p(op_c.partner_odoo_id, str(op_c.contraparte_nombre or "")):
            continue
        mid = str(op_c.odoo_res_id) if op_c.odoo_res_id is not None else None
        if mid:
            vistas_move.add(mid)
        m_o = q2(dec(op_c.monto))
        mon_c = str(op_c.moneda or "VES").upper()
        f_c = _fecha_iso_o_hoy(op_c.fecha_documento or op_c.creado_en, hoy)
        tot_u, _ = _equiv_usd_y_tasa(m_o, mon_c, f_c, por_fecha, fallback)
        vincs_c = mapa_c["por_compra_op_id"].get(int(op_c.documento_id), [])
        pag_u = q2(sum((dec(v["equiv_usd_bcv"]) for v in vincs_c), Decimal("0")))
        sal_u = max(Decimal("0.00"), q2(tot_u - pag_u))
        if sal_u <= Decimal("0.00"):
            continue
        compras_cand.append(
            {
                "compra_operativo_id": int(op_c.documento_id),
                "compra_odoo_move_id": mid,
                "numero": str(op_c.numero),
                "compra_ref": str(op_c.numero_documento or op_c.numero),
                "tipo": str(op_c.tipo),
                "fecha": f_c.isoformat(),
                "moneda": mon_c,
                "monto_original": float(m_o),
                "total_usd": float(tot_u),
                "vinculado_usd": float(pag_u),
                "saldo_pendiente_usd": float(sal_u),
            }
        )

    try:
        cmp_od = list(repo_cxc.all_compras_gastos_odoo())
    except Exception:
        cmp_od = []
    for row in cmp_od:
        mid = str(row.get("move_id") or "").strip()
        if not mid or mid in vistas_move:
            continue
        if str(row.get("move_type") or "") == "in_refund":
            continue
        if not _coincide_p(row.get("partner_odoo_id"), str(row.get("proveedor_nombre") or "")):
            continue
        m_o = q2(dec(row.get("monto_total")))
        mon_c = str(row.get("moneda") or "VES").upper()
        f_c = _fecha_iso_o_hoy(row.get("fecha_documento"), hoy)
        tot_u, _ = _equiv_usd_y_tasa(m_o, mon_c, f_c, por_fecha, fallback)
        vincs_c = mapa_c["por_compra_move_id"].get(mid, [])
        pag_u = q2(sum((dec(v["equiv_usd_bcv"]) for v in vincs_c), Decimal("0")))
        sal_u = max(Decimal("0.00"), q2(tot_u - pag_u))
        if sal_u <= Decimal("0.00"):
            continue
        compras_cand.append(
            {
                "compra_operativo_id": None,
                "compra_odoo_move_id": mid,
                "numero": str(row.get("numero_odoo") or f"MOVE-{mid}"),
                "compra_ref": str(row.get("numero_odoo") or row.get("referencia") or f"MOVE-{mid}"),
                "tipo": str(row.get("tipo_cxc") or "factura_proveedor"),
                "fecha": f_c.isoformat(),
                "moneda": mon_c,
                "monto_original": float(m_o),
                "total_usd": float(tot_u),
                "vinculado_usd": float(pag_u),
                "saldo_pendiente_usd": float(sal_u),
            }
        )

    compras_cand.sort(key=lambda x: (x["fecha"], x["numero"]))
    rest_u = disp_usd
    for cmp_c in compras_cand:
        sal_u = q2(dec(cmp_c["saldo_pendiente_usd"]))
        if rest_u > 0 and sal_u > 0:
            sug_u = min(rest_u, sal_u)
            sug_o = min(disp_orig, _monto_orig_desde_usd(sug_u, mon, f_pago, por_fecha, fallback))
            cmp_c["sugerido_fifo"] = True
            cmp_c["monto_sugerido_usd"] = float(sug_u)
            cmp_c["monto_sugerido_original"] = float(sug_o)
            rest_u = max(Decimal("0.00"), q2(rest_u - sug_u))
        else:
            cmp_c["sugerido_fifo"] = False
            cmp_c["monto_sugerido_usd"] = 0.0
            cmp_c["monto_sugerido_original"] = 0.0

    return {
        "pago": {
            "pago_operativo_id": int(op_row.documento_id) if op_row is not None else None,
            "pago_odoo_id": pid_str or None,
            "numero": str(op_row.numero) if op_row is not None else f"PAGO-{pid_str}",
            "proveedor_odoo_id": prov_id,
            "proveedor_nombre": prov_nom,
            "fecha": f_pago.isoformat(),
            "moneda": mon,
            "monto_original": float(monto_orig),
            "monto_usd": float(monto_usd),
            "disponible_original": float(disp_orig),
            "disponible_usd": float(disp_usd),
        },
        "vinculaciones": actuales,
        "candidatos": compras_cand,
    }


def vincular_pago_compra(
    engine: Engine,
    repo_cxc: Any,
    *,
    compra_operativo_id: int | None = None,
    compra_odoo_move_id: str | None = None,
    pago_operativo_id: int | None = None,
    pago_odoo_id: str | None = None,
    monto_aplicado: Decimal | float | str | None = None,
    origen_vinculacion: str = "manual",
    actor: str = "sistema",
    escritor: Any = None,
) -> dict[str, Any]:
    """Vincula un pago enviado o Zelle 3ro a una factura/gasto y concilia en Odoo."""
    doc = _resolver_compra_objetivo(
        engine,
        repo_cxc,
        compra_operativo_id=compra_operativo_id,
        compra_odoo_move_id=compra_odoo_move_id,
    )
    por_fecha, fallback = indice_tasas_bcv_local(repo_cxc)
    hoy = date.today()
    pid_str = str(pago_odoo_id or "").strip()

    op_row = None
    with engine.connect() as conn:
        if pago_operativo_id is not None:
            op_row = conn.execute(
                select(documentos_operativos).where(
                    documentos_operativos.c.documento_id == int(pago_operativo_id)
                )
            ).first()
        elif pid_str.isdigit():
            op_row = conn.execute(
                select(documentos_operativos).where(
                    documentos_operativos.c.tipo == "pago_proveedor",
                    documentos_operativos.c.odoo_res_id == int(pid_str),
                )
            ).first()

    if (
        not pid_str
        and op_row is not None
        and op_row.odoo_res_id is not None
        and not op_row.es_zelle_tercero
        and (not op_row.odoo_model or op_row.odoo_model == "account.payment")
    ):
        pid_str = str(op_row.odoo_res_id)

    odoo_pago = None
    if pid_str:
        try:
            for r in repo_cxc.all_pagos_proveedores_odoo():
                if str(r.get("pago_id") or "").strip() == pid_str:
                    odoo_pago = r
                    break
        except Exception:
            odoo_pago = None

    if op_row is None and odoo_pago is None:
        raise VinculacionInvalida("Debes indicar un pago enviado válido.")

    monto_total_pago = q2(
        dec(op_row.monto if op_row is not None else odoo_pago.get("monto"))  # type: ignore[union-attr]
    )
    mon = str(
        op_row.moneda if op_row is not None else odoo_pago.get("moneda") or "USD"  # type: ignore[union-attr]
    ).upper()
    f_pago = _fecha_iso_o_hoy(
        (op_row.fecha_documento or op_row.creado_en)
        if op_row is not None
        else odoo_pago.get("fecha_pago"),  # type: ignore[union-attr]
        hoy,
    )
    pago_ref = (
        str(op_row.numero)
        if op_row is not None
        else str(odoo_pago.get("numero_odoo") or f"PAGO-{pid_str}")  # type: ignore[union-attr]
    )

    mapa_c = mapa_vinculaciones_compras(engine)
    prev_v = (
        mapa_c["por_pago_op_id"].get(int(op_row.documento_id), [])
        if op_row is not None
        else mapa_c["por_pago_odoo_id"].get(pid_str, [])
    )
    usado_prev = q2(sum((dec(v["monto_aplicado"]) for v in prev_v), Decimal("0")))
    disp_orig = max(Decimal("0.00"), q2(monto_total_pago - usado_prev))

    if monto_aplicado is None or str(monto_aplicado).strip() == "":
        monto_dec = disp_orig
    else:
        monto_dec = q2(dec(monto_aplicado))
    if monto_dec <= 0:
        raise VinculacionInvalida("El monto a vincular debe ser mayor que cero.")
    if monto_total_pago > 0 and monto_dec > disp_orig + Decimal("0.05"):
        raise VinculacionInvalida(
            f"El monto ({monto_dec}) supera el saldo disponible del pago ({disp_orig} {mon})."
        )

    equiv_usd, tasa_bcv = _equiv_usd_y_tasa(monto_dec, mon, f_pago, por_fecha, fallback)

    # Conciliación best-effort en Odoo cuando pago y factura de compra existen publicados
    es_z3 = bool(op_row is not None and op_row.es_zelle_tercero)
    pago_model_od = "account.move" if es_z3 else "account.payment"
    pago_res_od = (
        int(pid_str)
        if pid_str.isdigit()
        else (int(op_row.odoo_res_id) if (op_row is not None and op_row.odoo_res_id) else None)
    )
    fact_move_od = (
        int(doc["compra_odoo_move_id"])
        if doc["compra_odoo_move_id"] and str(doc["compra_odoo_move_id"]).isdigit()
        else None
    )

    reconciliado = False
    odoo_motivo = "Vinculado en CxC (compra o pago aún sin ID publicado en Odoo)."
    if escritor is not None and pago_res_od is not None and fact_move_od is not None:
        rec = escritor.reconciliar_pago_con_documento(
            pago_model=pago_model_od,
            pago_res_id=int(pago_res_od),
            factura_move_id=int(fact_move_od),
            es_cliente=False,
        )
        reconciliado = bool(rec.get("reconciliado"))
        odoo_motivo = str(rec.get("motivo") or "")

    ahora = datetime.now()
    with engine.begin() as conn:
        fila = conn.execute(
            insert(vinculaciones_compras)
            .values(
                proveedor_odoo_id=doc["proveedor_odoo_id"],
                proveedor_nombre=doc["proveedor_nombre"],
                pago_operativo_id=(int(op_row.documento_id) if op_row is not None else None),
                pago_odoo_id=pid_str or None,
                pago_ref=pago_ref,
                compra_operativo_id=doc["compra_operativo_id"],
                compra_odoo_move_id=doc["compra_odoo_move_id"],
                compra_ref=doc["compra_ref"],
                monto_aplicado=monto_dec,
                moneda=mon,
                equiv_usd_bcv=equiv_usd,
                tasa_bcv=tasa_bcv,
                origen_vinculacion=origen_vinculacion,
                estado="RECONCILIADO_ODOO" if reconciliado else "VINCULADO_CXC",
                odoo_reconciliado=reconciliado,
                odoo_move_id=fact_move_od,
                odoo_detalle=odoo_motivo,
                vinculado_por=actor,
                creado_en=ahora,
            )
            .returning(*vinculaciones_compras.c)
        ).one()

    return {
        "ok": True,
        "vinculacion": _serializar_vinc_compra(fila),
        "odoo_reconciliado": reconciliado,
        "odoo_motivo": odoo_motivo,
    }


def desvincular_pago_compra(engine: Engine, vinc_id: int) -> dict[str, Any]:
    with engine.begin() as conn:
        row = conn.execute(
            select(vinculaciones_compras).where(vinculaciones_compras.c.id == int(vinc_id))
        ).first()
        if row is None:
            raise VinculacionNoEncontrada("Vinculación de compra no encontrada.")
        if bool(row.odoo_reconciliado):
            raise VinculacionInvalida("Ya fue conciliada en Odoo; no se puede desvincular.")
        conn.execute(
            delete(vinculaciones_compras).where(vinculaciones_compras.c.id == int(vinc_id))
        )
    return {"ok": True, "eliminado": int(vinc_id)}
