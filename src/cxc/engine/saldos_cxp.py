"""Motor de Reportes de Cuentas por Pagar (CxP) 100% sobre Base de Datos propia.

Fórmula inversa a CxC, calculada localmente sin consultar Odoo:

    Saldo por Pagar = Compras Confirmadas − Notas de Crédito Proveedor − Pagos Confirmados

Incluye facturas de proveedor confirmadas (espejo ``compras_gastos_odoo`` + ``factura_proveedor``
enviadas a Odoo sin espejo aún), notas de crédito de proveedor (``in_refund``), pagos salientes
a proveedores (Odoo ``outbound`` + ``pago_proveedor`` confirmados en CxC) y cobros Zelle a
Terceros confirmados (contrapartida que rebaja CxP del proveedor configurado). Los gastos sin
factura (``move_type='entry'``) se reportan aparte sin inflar la deuda por pagar.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..decimal_utils import q2
from ..repositories import Repository
from .saldos_propios import (
    TRAMOS_MORA,
    _indice_tasas_bcv_y_eur_local,
    a_usd_local,
    cumple_antiguedad,
    dec,
    fifo_antiguedad,
    norm_nombre,
    round_usd,
)

ESTADOS_PAGO_PROV_CONFIRMADO = frozenset({"posted", "paid", "in_process"})


def _fecha_de(val: Any, fallback: date) -> date:
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
    return fallback


def _bucket_proveedor(pid: str, nombre: str, rif: str) -> dict[str, Any]:
    return {
        "proveedor_id": pid,
        "proveedor_nombre": nombre or f"Proveedor #{pid}",
        "rif": rif,
        "compras_confirmadas_usd": Decimal("0"),
        "notas_credito_usd": Decimal("0"),
        "pagos_odoo_usd": Decimal("0"),
        "pagos_cxc_usd": Decimal("0"),
        "pagos_zelle_tercero_usd": Decimal("0"),
        "pagos_confirmados_usd": Decimal("0"),
        "gastos_sin_factura_usd": Decimal("0"),
        "n_facturas": 0,
        "n_pagos": 0,
        "facturas_fifo": [],  # list[tuple[date, date, Decimal, str]]
        "movimientos": [],
    }


def _recolectar_cxp_bd(
    repo: Repository,
    *,
    docs_operativos: Sequence[Any] = (),
    cuentas_terceros: Sequence[Any] = (),
    hoy: date | None = None,
) -> dict[str, Any]:
    hoy_d = hoy or date.today()
    por_fecha_tasa, fallback_tasa, eur_por_fecha, fallback_eur = _indice_tasas_bcv_y_eur_local(
        repo
    )

    def _usd_cxp(monto_val: Decimal, mon_val: str, fecha_val: date) -> Decimal:
        return a_usd_local(
            monto_val,
            mon_val,
            fecha_val,
            por_fecha_tasa,
            fallback_tasa,
            eur_por_fecha=eur_por_fecha,
            fallback_eur=fallback_eur,
        )

    # Mapa de cuentas Zelle de terceros -> proveedor configurado
    terceros_por_id: dict[int, dict[str, Any]] = {}
    for ct in cuentas_terceros:
        cid_raw = ct.get("cuenta_id") if isinstance(ct, dict) else getattr(ct, "cuenta_id", None)
        if cid_raw is None:
            continue
        prov_pid = (
            ct.get("proveedor_odoo_id")
            if isinstance(ct, dict)
            else getattr(ct, "proveedor_odoo_id", None)
        )
        prov_nom = (
            ct.get("proveedor_nombre")
            if isinstance(ct, dict)
            else getattr(ct, "proveedor_nombre", "")
        )
        alias = ct.get("alias") if isinstance(ct, dict) else getattr(ct, "alias", "")
        terceros_por_id[int(cid_raw)] = {
            "proveedor_odoo_id": prov_pid,
            "proveedor_nombre": str(prov_nom or "").strip(),
            "alias": str(alias or "").strip(),
        }

    buckets: dict[str, dict[str, Any]] = {}
    nombre_a_pid: dict[str, str] = {}

    def ensure_bucket(pid: str, nombre: str = "", rif: str = "") -> dict[str, Any]:
        b = buckets.get(pid)
        if b is None:
            b = _bucket_proveedor(pid, nombre, rif)
            buckets[pid] = b
        else:
            if nombre and b["proveedor_nombre"].startswith("Proveedor #"):
                b["proveedor_nombre"] = nombre
            if rif and not b["rif"]:
                b["rif"] = rif
        norm = norm_nombre(b["proveedor_nombre"])
        if norm and not pid.startswith("cxp:"):
            nombre_a_pid.setdefault(norm, pid)
        return b

    def resolver_pid(
        partner_odoo_id: Any, nombre_prov: str, rif: str = ""
    ) -> tuple[str, dict[str, Any]]:
        if partner_odoo_id is not None and str(partner_odoo_id).strip():
            pid = str(partner_odoo_id).strip()
            return pid, ensure_bucket(pid, nombre_prov, rif)
        norm = norm_nombre(nombre_prov)
        if norm and norm in nombre_a_pid:
            pid = nombre_a_pid[norm]
            return pid, ensure_bucket(pid, nombre_prov, rif)
        pid_sint = f"cxp:{norm or 'sin_nombre'}"
        return pid_sint, ensure_bucket(pid_sint, nombre_prov, rif)

    # 1. Facturas de proveedor, NC y gastos contabilizados desde espejo Odoo
    try:
        compras_odoo = list(repo.all_compras_gastos_odoo())
    except NotImplementedError:
        compras_odoo = []

    compras_odoo_move_ids: set[str] = set()
    for r in compras_odoo:
        mid = str(r.get("move_id") or "").strip()
        if mid:
            compras_odoo_move_ids.add(mid)
        if str(r.get("estado_odoo") or "").strip().lower() != "posted":
            continue
        mtype = str(r.get("move_type") or "in_invoice").strip().lower()
        monto_raw = abs(dec(r.get("monto_total")))
        if monto_raw <= 0:
            continue
        mon = str(r.get("moneda") or "VES").strip().upper()
        f_doc = _fecha_de(r.get("fecha_documento"), hoy_d)
        monto_usd = _usd_cxp(monto_raw, mon, f_doc)
        if monto_usd <= 0:
            continue

        _pid, b = resolver_pid(
            r.get("partner_odoo_id"),
            str(r.get("proveedor_nombre") or ""),
            str(r.get("rif") or ""),
        )
        ref = str(r.get("numero_odoo") or r.get("referencia") or f"MOVE-{mid}").strip()
        if mtype == "in_refund":
            b["notas_credito_usd"] += monto_usd
            b["movimientos"].append(
                {
                    "fecha": f_doc.isoformat(),
                    "tipo": "nc_proveedor_odoo",
                    "etiqueta": "Nota de Crédito Proveedor (Odoo)",
                    "referencia": ref,
                    "fuente": "odoo",
                    "debito_usd": 0.0,
                    "credito_usd": float(monto_usd),
                }
            )
        elif mtype == "entry":
            # Gasto menor pagado sin cuenta por pagar abierta
            b["gastos_sin_factura_usd"] += monto_usd
        else:
            f_venc = _fecha_de(r.get("fecha_vencimiento"), f_doc)
            b["compras_confirmadas_usd"] += monto_usd
            b["n_facturas"] += 1
            b["facturas_fifo"].append((f_doc, f_venc, monto_usd, ref))
            b["movimientos"].append(
                {
                    "fecha": f_doc.isoformat(),
                    "tipo": "compra_odoo",
                    "etiqueta": "Factura Proveedor (Odoo)",
                    "referencia": ref,
                    "fuente": "odoo",
                    "debito_usd": float(monto_usd),
                    "credito_usd": 0.0,
                }
            )

    # 1b. Facturas proveedor nacidas en CxC enviadas a Odoo aún ausentes del espejo
    for dop in docs_operativos:
        if getattr(dop, "tipo", "") != "factura_proveedor":
            continue
        if str(getattr(dop, "estado", "") or "") != "ENVIADO_ODOO":
            continue
        odoo_rid = getattr(dop, "odoo_res_id", None)
        if odoo_rid is not None and str(odoo_rid) in compras_odoo_move_ids:
            continue
        monto_raw = abs(dec(getattr(dop, "monto", 0)))
        if monto_raw <= 0:
            continue
        mon = str(getattr(dop, "moneda", "VES") or "VES").upper()
        f_doc = _fecha_de(
            getattr(dop, "fecha_documento", None) or getattr(dop, "creado_en", None), hoy_d
        )
        f_venc = _fecha_de(getattr(dop, "fecha_vencimiento", None), f_doc)
        monto_usd = _usd_cxp(monto_raw, mon, f_doc)
        if monto_usd <= 0:
            continue
        _pid, b = resolver_pid(
            getattr(dop, "partner_odoo_id", None),
            str(getattr(dop, "contraparte_nombre", "") or ""),
            str(getattr(dop, "rif", "") or ""),
        )
        ref = str(getattr(dop, "numero_documento", "") or getattr(dop, "numero", "")).strip()
        b["compras_confirmadas_usd"] += monto_usd
        b["n_facturas"] += 1
        b["facturas_fifo"].append((f_doc, f_venc, monto_usd, ref))
        b["movimientos"].append(
            {
                "fecha": f_doc.isoformat(),
                "tipo": "compra_cxc",
                "etiqueta": "Factura Proveedor (CxC)",
                "referencia": ref,
                "fuente": "cxc",
                "debito_usd": float(monto_usd),
                "credito_usd": 0.0,
            }
        )

    # 2. Pagos salientes a proveedores desde espejo Odoo (account.payment outbound)
    try:
        pagos_prov_odoo = list(repo.all_pagos_proveedores_odoo())
    except NotImplementedError:
        pagos_prov_odoo = []

    pagos_odoo_ids: set[str] = set()
    for p in pagos_prov_odoo:
        pid_p = str(p.get("pago_id") or "").strip()
        if pid_p:
            pagos_odoo_ids.add(pid_p)
        est_p = str(p.get("estado_odoo") or "posted").strip().lower()
        if est_p not in ESTADOS_PAGO_PROV_CONFIRMADO:
            continue
        monto_raw = abs(dec(p.get("monto")))
        if monto_raw <= 0:
            continue
        mon = str(p.get("moneda") or "USD").strip().upper()
        f_p = _fecha_de(p.get("fecha_pago"), hoy_d)
        monto_usd = _usd_cxp(monto_raw, mon, f_p)
        if monto_usd <= 0:
            continue
        _pid, b = resolver_pid(
            p.get("partner_odoo_id"),
            str(p.get("proveedor_nombre") or ""),
            str(p.get("rif") or ""),
        )
        b["pagos_odoo_usd"] += monto_usd
        b["pagos_confirmados_usd"] += monto_usd
        b["n_pagos"] += 1
        ref = str(p.get("numero_odoo") or p.get("referencia") or f"PPROV-{pid_p}").strip()
        b["movimientos"].append(
            {
                "fecha": f_p.isoformat(),
                "tipo": "pago_prov_odoo",
                "etiqueta": f"Pago a Proveedor Odoo ({mon})",
                "referencia": ref,
                "fuente": "odoo",
                "debito_usd": 0.0,
                "credito_usd": float(monto_usd),
            }
        )

    # 2b. Pagos a proveedor CxC-first confirmados + Zelle 3ros confirmados (contrapartida CxP)
    for dop in docs_operativos:
        tipo_op = getattr(dop, "tipo", "")
        est = str(getattr(dop, "estado", "") or "").upper()
        if est == "RECHAZADO":
            continue
        confirmado = bool(getattr(dop, "confirmado_cxc", False)) or est in (
            "CONFIRMADO_CXC",
            "ENVIADO_ODOO",
        )
        if not confirmado:
            continue

        es_zt = tipo_op == "pago" and bool(getattr(dop, "es_zelle_tercero", False))
        if tipo_op != "pago_proveedor" and not es_zt:
            continue

        odoo_mod = str(getattr(dop, "odoo_model", "") or "")
        odoo_rid = getattr(dop, "odoo_res_id", None)
        if (
            tipo_op == "pago_proveedor"
            and odoo_mod in ("", "account.payment")
            and odoo_rid is not None
            and str(odoo_rid) in pagos_odoo_ids
        ):
            continue

        monto_raw = abs(dec(getattr(dop, "monto", 0)))
        if monto_raw <= 0:
            continue
        mon = str(getattr(dop, "moneda", "USD") or "USD").upper()
        f_p = _fecha_de(
            getattr(dop, "fecha_documento", None) or getattr(dop, "creado_en", None), hoy_d
        )
        monto_usd = _usd_cxp(monto_raw, mon, f_p)
        if monto_usd <= 0:
            continue

        if es_zt:
            cta_id = getattr(dop, "cuenta_tercero_id", None)
            info_cta = terceros_por_id.get(int(cta_id)) if cta_id is not None else None
            prov_pid = info_cta["proveedor_odoo_id"] if info_cta else None
            prov_nom = (info_cta["proveedor_nombre"] if info_cta else "") or str(
                getattr(dop, "beneficiario_tercero", "") or ""
            )
            if prov_pid is None and not prov_nom:
                continue
            _pid, b = resolver_pid(prov_pid, prov_nom, "")
            b["pagos_zelle_tercero_usd"] += monto_usd
            etq = "Cobro Zelle a Tercero (Contrapartida CxP)"
            t_mov = "zelle_tercero_cxp"
        else:
            _pid, b = resolver_pid(
                getattr(dop, "partner_odoo_id", None),
                str(getattr(dop, "contraparte_nombre", "") or ""),
                str(getattr(dop, "rif", "") or ""),
            )
            b["pagos_cxc_usd"] += monto_usd
            etq = "Pago a Proveedor Confirmado (CxC)"
            t_mov = "pago_prov_cxc"

        b["pagos_confirmados_usd"] += monto_usd
        b["n_pagos"] += 1
        rec = str(getattr(dop, "numero_recibo", "") or "").strip()
        num = str(getattr(dop, "numero", "") or "").strip()
        b["movimientos"].append(
            {
                "fecha": f_p.isoformat(),
                "tipo": t_mov,
                "etiqueta": etq,
                "referencia": f"{num} ({rec})" if rec else num,
                "fuente": "cxc",
                "debito_usd": 0.0,
                "credito_usd": float(monto_usd),
            }
        )

    # 3. Cierre por proveedor + FIFO mora CxP
    proveedores_out: list[dict[str, Any]] = []
    tot_tramos: dict[str, Decimal] = dict.fromkeys(TRAMOS_MORA, Decimal("0"))

    for pid, b in buckets.items():
        compras = q2(b["compras_confirmadas_usd"])
        ncs = q2(b["notas_credito_usd"])
        pagos_c = q2(b["pagos_confirmados_usd"])
        gastos = q2(b["gastos_sin_factura_usd"])
        if compras == 0 and ncs == 0 and pagos_c == 0 and gastos == 0:
            continue

        saldo_bruto = q2(compras - ncs - pagos_c)
        saldo_pagar = max(Decimal("0.00"), saldo_bruto)
        saldo_favor = max(Decimal("0.00"), -saldo_bruto)

        tramos_p, max_dias_mora = fifo_antiguedad(b["facturas_fifo"], ncs + pagos_c, hoy_d)
        for tr in TRAMOS_MORA:
            tot_tramos[tr] = q2(tot_tramos[tr] + tramos_p[tr])
        vencido_p = q2(sum((tramos_p[t] for t in TRAMOS_MORA if t != "vigente"), Decimal("0")))

        proveedores_out.append(
            {
                "proveedor_id": pid,
                "proveedor_nombre": b["proveedor_nombre"],
                "rif": b["rif"],
                "n_facturas": int(b["n_facturas"]),
                "n_compras": int(b["n_facturas"]),
                "n_pagos": int(b["n_pagos"]),
                "compras_confirmadas_usd": round_usd(compras),
                "notas_credito_usd": round_usd(ncs),
                "descuentos_confirmados_usd": round_usd(ncs),
                "pagos_odoo_usd": round_usd(b["pagos_odoo_usd"]),
                "pagos_cxc_usd": round_usd(b["pagos_cxc_usd"]),
                "pagos_proveedor_usd": round_usd(b["pagos_odoo_usd"] + b["pagos_cxc_usd"]),
                "pagos_zelle_tercero_usd": round_usd(b["pagos_zelle_tercero_usd"]),
                "pagos_confirmados_usd": round_usd(pagos_c),
                "gastos_sin_factura_usd": round_usd(gastos),
                "saldo_neto_usd": round_usd(saldo_bruto),
                "saldo_por_pagar_usd": round_usd(saldo_pagar),
                "saldo_favor_usd": round_usd(saldo_favor),
                "vencido_usd": round_usd(vencido_p),
                "vigente_usd": round_usd(tramos_p["vigente"]),
                "tramos": {k: round_usd(v) for k, v in tramos_p.items()},
                "dias_mora_max": max_dias_mora,
                "_movimientos": b["movimientos"],
            }
        )

    proveedores_out.sort(key=lambda x: (-x["saldo_por_pagar_usd"], x["proveedor_nombre"]))
    last_sync = repo.get_last_sync()
    return {
        "generado_en": datetime.now().isoformat(),
        "ultimo_sync_bd": last_sync.isoformat() if last_sync else None,
        "tasa_bcv_referencia": float(fallback_tasa),
        "proveedores": proveedores_out,
    }


def _resumir_kpis_cxp(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    tramos = {k: round(sum(float(r["tramos"][k]) for r in rows), 2) for k in TRAMOS_MORA}
    return {
        "total_proveedores": len(rows),
        "proveedores_con_saldo": sum(1 for r in rows if r["saldo_por_pagar_usd"] > 0),
        "compras_confirmadas_usd": round(sum(float(r["compras_confirmadas_usd"]) for r in rows), 2),
        "notas_credito_usd": round(sum(float(r["notas_credito_usd"]) for r in rows), 2),
        "descuentos_confirmados_usd": round(
            sum(float(r["descuentos_confirmados_usd"]) for r in rows), 2
        ),
        "pagos_odoo_usd": round(sum(float(r["pagos_odoo_usd"]) for r in rows), 2),
        "pagos_cxc_usd": round(sum(float(r["pagos_cxc_usd"]) for r in rows), 2),
        "pagos_proveedor_usd": round(sum(float(r["pagos_proveedor_usd"]) for r in rows), 2),
        "pagos_zelle_tercero_usd": round(sum(float(r["pagos_zelle_tercero_usd"]) for r in rows), 2),
        "pagos_confirmados_usd": round(sum(float(r["pagos_confirmados_usd"]) for r in rows), 2),
        "gastos_sin_factura_usd": round(sum(float(r["gastos_sin_factura_usd"]) for r in rows), 2),
        "saldo_por_pagar_usd": round(sum(float(r["saldo_por_pagar_usd"]) for r in rows), 2),
        "saldo_favor_usd": round(sum(float(r["saldo_favor_usd"]) for r in rows), 2),
        "vencido_usd": round(sum(float(r["vencido_usd"]) for r in rows), 2),
        "vigente_usd": round(sum(float(r["vigente_usd"]) for r in rows), 2),
        "tramos": tramos,
    }


def calcular_saldos_cxp(
    repo: Repository,
    *,
    docs_operativos: Sequence[Any] = (),
    cuentas_terceros: Sequence[Any] = (),
    hoy: date | None = None,
    antiguedad: str | None = None,
    q: str | None = None,
    solo_con_saldo: bool = False,
) -> dict[str, Any]:
    """Reporte completo de Saldos CxP calculado 100% sobre BD propia."""
    base = _recolectar_cxp_bd(
        repo,
        docs_operativos=docs_operativos,
        cuentas_terceros=cuentas_terceros,
        hoy=hoy,
    )
    limpios = [{k: v for k, v in p.items() if k != "_movimientos"} for p in base["proveedores"]]
    filtrados = limpios
    if antiguedad and antiguedad != "*":
        filtrados = [p for p in filtrados if cumple_antiguedad(p, antiguedad)]
    if solo_con_saldo:
        filtrados = [p for p in filtrados if p["saldo_por_pagar_usd"] > 0]
    if q and q.strip():
        qn = q.strip().lower()
        filtrados = [
            p
            for p in filtrados
            if qn in p["proveedor_nombre"].lower()
            or qn in p["proveedor_id"].lower()
            or qn in p["rif"].lower()
        ]
    return {
        "fuente": "bd_propia_cxc",
        "solo_lectura": True,
        "formula": "Saldo por Pagar = Compras Confirmadas - NC Proveedores - Pagos Confirmados",
        "generado_en": base["generado_en"],
        "ultimo_sync_bd": base["ultimo_sync_bd"],
        "tasa_bcv_referencia": base["tasa_bcv_referencia"],
        "kpis": _resumir_kpis_cxp(filtrados),
        "kpis_globales": _resumir_kpis_cxp(limpios),
        "proveedores": filtrados,
    }


def estado_cuenta_proveedor(
    repo: Repository,
    proveedor_id: str,
    *,
    docs_operativos: Sequence[Any] = (),
    cuentas_terceros: Sequence[Any] = (),
    hoy: date | None = None,
) -> dict[str, Any] | None:
    """Devuelve el estado de cuenta cronológico de un proveedor desde BD propia."""
    base = _recolectar_cxp_bd(
        repo,
        docs_operativos=docs_operativos,
        cuentas_terceros=cuentas_terceros,
        hoy=hoy,
    )
    target = next(
        (p for p in base["proveedores"] if str(p["proveedor_id"]) == str(proveedor_id)),
        None,
    )
    if target is None:
        return None
    movs_ord = sorted(
        target["_movimientos"],
        key=lambda m: (str(m["fecha"]), 0 if m["debito_usd"] > 0 else 1, str(m["referencia"])),
    )
    saldo_corrido = Decimal("0.00")
    movs_con_saldo: list[dict[str, Any]] = []
    for m in movs_ord:
        saldo_corrido = q2(saldo_corrido + dec(m["debito_usd"]) - dec(m["credito_usd"]))
        movs_con_saldo.append({**m, "saldo_acumulado_usd": float(saldo_corrido)})
    resumen = {k: v for k, v in target.items() if k != "_movimientos"}
    return {
        "fuente": "bd_propia_cxc",
        "solo_lectura": True,
        "resumen": resumen,
        "movimientos": movs_con_saldo,
    }


def exportar_cxp_csv(reporte: dict[str, Any]) -> bytes:
    """Genera CSV UTF-8 con BOM listo para Excel con el reporte de CxP."""
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Proveedor ID",
            "Proveedor",
            "RIF",
            "Facturas",
            "Pagos",
            "Compras Confirmadas USD",
            "Notas Credito USD",
            "Pagos Odoo USD",
            "Pagos CxC USD",
            "Zelle Terceros CxP USD",
            "Pagos Confirmados USD",
            "Saldo por Pagar USD",
            "Saldo a Favor USD",
            "Gastos Sin Factura USD",
            "Vencido USD",
        ]
    )
    for p in reporte.get("proveedores") or []:
        writer.writerow(
            [
                p.get("proveedor_id", ""),
                p.get("proveedor_nombre", ""),
                p.get("rif", ""),
                p.get("n_facturas", 0),
                p.get("n_pagos", 0),
                f"{p.get('compras_confirmadas_usd', 0):.2f}",
                f"{p.get('notas_credito_usd', 0):.2f}",
                f"{p.get('pagos_odoo_usd', 0):.2f}",
                f"{p.get('pagos_cxc_usd', 0):.2f}",
                f"{p.get('pagos_zelle_tercero_usd', 0):.2f}",
                f"{p.get('pagos_confirmados_usd', 0):.2f}",
                f"{p.get('saldo_por_pagar_usd', 0):.2f}",
                f"{p.get('saldo_favor_usd', 0):.2f}",
                f"{p.get('gastos_sin_factura_usd', 0):.2f}",
                f"{p.get('vencido_usd', 0):.2f}",
            ]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")
