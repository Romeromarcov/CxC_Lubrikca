# ruff: noqa: E501
"""Conciliación automática de pagos electrónicos y lotes de recibos en efectivo por vendedor."""

from __future__ import annotations

import html
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection, Engine, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from cxc.capturas.modelos import es_efectivo
from cxc.db.schema import (
    bancos_movimientos,
    bancos_propuestas,
    documentos_operativos,
    lotes_recibo_efectivo,
    pagos,
)
from cxc.decimal_utils import q2

from .modelos import CONFIRMADO_CXC, PENDIENTE_APROBACION

_ESTADOS_CONCILIADOS = ("aprobada", "corregida")


def es_pago_efectivo(metodo_pago: Any = "", *, diario_nombre: Any = "") -> bool:
    """True si el método o diario corresponde a caja / efectivo."""
    if es_efectivo(metodo_pago) or es_efectivo(diario_nombre):
        return True
    diario_txt = str(diario_nombre or "").strip().lower()
    if not diario_txt:
        return False
    return "caja" in diario_txt and "banco" not in diario_txt


def es_pago_electronico_conocido(metodo_pago: Any = "", *, diario_nombre: Any = "") -> bool:
    """True solo cuando hay método/diario explícito y NO es efectivo."""
    hay_texto = bool(str(metodo_pago or "").strip() or str(diario_nombre or "").strip())
    if not hay_texto:
        return False
    return not es_pago_efectivo(metodo_pago, diario_nombre=diario_nombre)


def _meta_banco_fila(r: Any) -> dict[str, Any]:
    fecha_m = getattr(r, "fecha", None)
    dec_en = getattr(r, "decidida_en", None)
    return {
        "huella": str(r.huella or ""),
        "estado": str(r.estado or ""),
        "regla": str(getattr(r, "regla", "") or ""),
        "pago_id": str(r.pago_id) if getattr(r, "pago_id", None) is not None else "",
        "banco": str(getattr(r, "banco", "") or ""),
        "fecha": fecha_m.isoformat() if isinstance(fecha_m, date | datetime) else "",
        "referencia": str(getattr(r, "referencia", "") or ""),
        "decidida_por": str(getattr(r, "decidida_por", "") or ""),
        "decidida_en": dec_en.isoformat() if isinstance(dec_en, datetime) else "",
    }


def mapa_conciliacion_pagos(engine: Engine | None) -> dict[str, dict[str, dict[str, Any]]]:
    """Construye mapas ``por_pago_id`` y ``por_huella`` desde ``bancos_propuestas``.

    Considera conciliados los movimientos cuya propuesta fue ``aprobada`` o
    ``corregida``. Captura con seguridad errores de esquema parcial en tests.
    """
    por_pago_id: dict[str, dict[str, Any]] = {}
    por_huella: dict[str, dict[str, Any]] = {}
    if engine is None:
        return {"por_pago_id": por_pago_id, "por_huella": por_huella}

    p = bancos_propuestas
    m = bancos_movimientos
    q = (
        select(
            p.c.huella,
            p.c.estado,
            p.c.regla,
            p.c.pago_id,
            p.c.decidida_por,
            p.c.decidida_en,
            m.c.banco,
            m.c.fecha,
            m.c.referencia,
        )
        .select_from(p.outerjoin(m, p.c.huella == m.c.huella))
        .where(p.c.estado.in_(_ESTADOS_CONCILIADOS))
    )
    try:
        with engine.connect() as conn:
            for r in conn.execute(q).all():
                meta = _meta_banco_fila(r)
                huella = meta["huella"]
                if huella:
                    por_huella[huella] = meta
                pid = meta["pago_id"]
                if pid:
                    por_pago_id[pid] = meta
            if sa.inspect(conn).has_table("documentos_operativos"):
                rows_op = conn.execute(
                    select(
                        documentos_operativos.c.odoo_res_id,
                        documentos_operativos.c.conciliado_banco_huella,
                        documentos_operativos.c.confirmado_cxc_por,
                        documentos_operativos.c.confirmado_cxc_en,
                    ).where(
                        documentos_operativos.c.tipo == "pago",
                        documentos_operativos.c.conciliado_banco_huella != "",
                    )
                ).all()
                for ro in rows_op:
                    huella = str(ro[1] or "").strip()
                    oid = str(ro[0]) if ro[0] is not None else ""
                    if oid and oid not in por_pago_id and huella:
                        base_meta = por_huella.get(huella)
                        por_pago_id[oid] = base_meta or {
                            "huella": huella,
                            "estado": "conciliada",
                            "regla": "R5",
                            "pago_id": oid,
                            "banco": "",
                            "fecha": "",
                            "referencia": "",
                            "decidida_por": str(ro[2] or ""),
                            "decidida_en": ro[3].isoformat() if ro[3] else "",
                        }
    except SQLAlchemyError:
        return {"por_pago_id": por_pago_id, "por_huella": por_huella}

    return {"por_pago_id": por_pago_id, "por_huella": por_huella}


def aplicar_conciliacion_a_operativos(
    conn: Connection,
    *,
    huella: str,
    estado: str,
    pago_id: int | None,
    usuario: str,
    ahora: datetime,
) -> None:
    """Sincroniza la confirmación bancaria de pagos electrónicos en ``documentos_operativos``."""
    if not sa.inspect(conn).has_table("documentos_operativos"):
        return
    d = documentos_operativos

    if estado in _ESTADOS_CONCILIADOS and pago_id is not None:
        candidatos = conn.execute(
            select(
                d.c.documento_id,
                d.c.metodo_pago,
                d.c.diario_nombre,
                d.c.es_zelle_tercero,
                d.c.confirmado_cxc,
            ).where(d.c.tipo == "pago", d.c.odoo_res_id == int(pago_id))
        ).all()
        for row in candidatos:
            if row.es_zelle_tercero or es_pago_efectivo(
                row.metodo_pago, diario_nombre=row.diario_nombre
            ):
                continue
            valores: dict[str, Any] = {
                "conciliado_banco_huella": huella[:64],
                "actualizado_en": ahora,
            }
            if not row.confirmado_cxc:
                valores["confirmado_cxc"] = True
                valores["confirmado_cxc_por"] = usuario
                valores["confirmado_cxc_en"] = ahora
            conn.execute(update(d).where(d.c.documento_id == row.documento_id).values(**valores))
        return

    if estado in ("pendiente", "rechazada"):
        afectados = conn.execute(
            select(
                d.c.documento_id,
                d.c.tipo,
                d.c.metodo_pago,
                d.c.diario_nombre,
                d.c.numero_recibo,
            ).where(d.c.conciliado_banco_huella == huella)
        ).all()
        for row in afectados:
            valores_limp: dict[str, Any] = {
                "conciliado_banco_huella": "",
                "actualizado_en": ahora,
            }
            if (
                row.tipo == "pago"
                and not (row.numero_recibo or "").strip()
                and not es_pago_efectivo(row.metodo_pago, diario_nombre=row.diario_nombre)
            ):
                valores_limp["confirmado_cxc"] = False
                valores_limp["confirmado_cxc_por"] = ""
                valores_limp["confirmado_cxc_en"] = None
            conn.execute(
                update(d).where(d.c.documento_id == row.documento_id).values(**valores_limp)
            )


def agrupar_efectivo_pendiente_por_vendedor(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa por vendedor pagos en efectivo pendientes de recibo."""
    grupos: dict[str, dict[str, Any]] = {}
    for it in items:
        if it.get("es_zelle_tercero"):
            continue
        if not es_pago_efectivo(it.get("metodo_pago"), diario_nombre=it.get("diario_nombre")):
            continue
        ya_recibido = bool(
            it.get("recibido") or it.get("confirmado_cxc") or it.get("numero_recibo")
        )
        if ya_recibido:
            continue
        estado = str(it.get("estado") or "").upper()
        if estado in ("RECHAZADO", "CANCELADO"):
            continue

        vend_email = str(it.get("vendedor") or it.get("registrado_por") or "").strip()
        vend_nombre = str(
            it.get("vendedor_nombre")
            or it.get("registrado_por_nombre")
            or vend_email
            or "Sin vendedor"
        ).strip()
        clave = (vend_email or vend_nombre).lower()

        g = grupos.setdefault(
            clave,
            {
                "vendedor_key": clave,
                "vendedor_email": vend_email or vend_nombre,
                "vendedor_nombre": vend_nombre,
                "cantidad_pagos": 0,
                "total_usd": Decimal("0"),
                "total_ves": Decimal("0"),
                "total_eur": Decimal("0"),
                "total_equivalente_usd": Decimal("0"),
                "pagos": [],
            },
        )
        mon = str(it.get("moneda") or "USD").upper().strip() or "USD"
        monto = Decimal(str(it.get("monto") or 0))
        eq_usd = it.get("monto_bcv_usd")
        if eq_usd is None and mon in ("USD", "EUR"):
            eq_val = monto if mon == "USD" else Decimal(str(it.get("monto_eur_usd") or monto))
        else:
            eq_val = Decimal(str(eq_usd or 0))

        g["cantidad_pagos"] += 1
        if mon == "VES":
            g["total_ves"] += monto
        elif mon == "EUR":
            g["total_eur"] += monto
        else:
            g["total_usd"] += monto
        g["total_equivalente_usd"] += eq_val
        g["pagos"].append(it)

    salida = []
    for g in grupos.values():
        g["total_usd"] = float(q2(g["total_usd"]))
        g["total_ves"] = float(q2(g["total_ves"]))
        g["total_eur"] = float(q2(g["total_eur"]))
        g["total_equivalente_usd"] = float(q2(g["total_equivalente_usd"]))
        salida.append(g)
    salida.sort(key=lambda x: (-x["cantidad_pagos"], x["vendedor_nombre"].lower()))
    return salida


def _row_lote_a_dict(r: Any) -> dict[str, Any]:
    try:
        pagos_ref = json.loads(r.pagos_ref_json or "[]")
        if not isinstance(pagos_ref, list):
            pagos_ref = []
    except Exception:
        pagos_ref = []
    return {
        "lote_id": int(r.lote_id),
        "numero_recibo": str(r.numero_recibo),
        "recibo_path": f"/recibo-lote/{int(r.lote_id)}",
        "vendedor_email": str(r.vendedor_email or ""),
        "vendedor_nombre": str(r.vendedor_nombre or ""),
        "fecha_lote": r.fecha_lote.isoformat() if r.fecha_lote else "",
        "cantidad_pagos": int(r.cantidad_pagos or 0),
        "total_usd": float(r.total_usd or 0),
        "total_ves": float(r.total_ves or 0),
        "total_eur": float(r.total_eur or 0),
        "total_equivalente_usd": float(r.total_equivalente_usd or 0),
        "pagos": pagos_ref,
        "recibido_por": str(r.recibido_por or ""),
        "recibido_en": r.recibido_en.isoformat()[:19] if r.recibido_en else "",
        "observaciones": str(r.observaciones or ""),
        "estado": str(r.estado or "EMITIDO"),
    }


class RepoLotesEfectivo:
    """Persistencia SQLAlchemy Core para recibos agrupados de efectivo por vendedor."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def _siguiente_numero_recibo(self, conn: Connection, fecha: date) -> str:
        pref = f"REC-LOTE-{fecha.strftime('%Y%m%d')}-"
        ultimo = conn.execute(
            select(func.max(lotes_recibo_efectivo.c.numero_recibo)).where(
                lotes_recibo_efectivo.c.numero_recibo.like(f"{pref}%")
            )
        ).scalar_one_or_none()
        n = 1
        if ultimo and str(ultimo).startswith(pref):
            suf = str(ultimo)[len(pref) :]
            if suf.isdigit():
                n = int(suf) + 1
        return f"{pref}{n:03d}"

    def listar(
        self, *, vendedor_email: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        q = select(lotes_recibo_efectivo).order_by(lotes_recibo_efectivo.c.lote_id.desc())
        if vendedor_email and vendedor_email.strip():
            q = q.where(lotes_recibo_efectivo.c.vendedor_email == vendedor_email.strip())
        q = q.limit(min(max(limit, 1), 300))
        with self._engine.connect() as conn:
            rows = conn.execute(q).all()
        return [_row_lote_a_dict(r) for r in rows]

    def obtener(self, lote_id: int) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(lotes_recibo_efectivo).where(lotes_recibo_efectivo.c.lote_id == int(lote_id))
            ).one_or_none()
        return _row_lote_a_dict(row) if row is not None else None

    def crear_lote(
        self,
        *,
        vendedor_email: str,
        vendedor_nombre: str,
        items: list[dict[str, Any]],
        recibido_por: str,
        observaciones: str = "",
        fecha_lote: date | None = None,
    ) -> dict[str, Any]:
        """Emite un recibo por lote estrictamente para pagos en efectivo."""
        if not items:
            raise ValueError("Selecciona al menos un pago en efectivo para generar el lote.")

        total_usd = Decimal("0")
        total_ves = Decimal("0")
        total_eur = Decimal("0")
        total_eq_usd = Decimal("0")
        pagos_mirror_ids: list[str] = []
        doc_ids: list[int] = []
        resumen_items: list[dict[str, Any]] = []

        for it in items:
            if it.get("es_zelle_tercero") or not es_pago_efectivo(
                it.get("metodo_pago"), diario_nombre=it.get("diario_nombre")
            ):
                raise ValueError(
                    "Solo los pagos en efectivo pueden incluirse en recibos por lote. "
                    "Las transacciones electrónicas se confirman por conciliación bancaria."
                )
            mon = str(it.get("moneda") or "USD").upper().strip() or "USD"
            monto = Decimal(str(it.get("monto") or 0))
            if mon == "VES":
                total_ves += monto
            elif mon == "EUR":
                total_eur += monto
            else:
                total_usd += monto

            eq_raw = it.get("monto_bcv_usd")
            if eq_raw is not None:
                eq_val = Decimal(str(eq_raw))
            elif mon == "EUR" and it.get("monto_eur_usd") is not None:
                eq_val = Decimal(str(it.get("monto_eur_usd")))
            elif mon == "USD":
                eq_val = monto
            else:
                eq_val = Decimal("0")
            total_eq_usd += eq_val

            pid_mirror = str(it.get("pago_id") or "").strip()
            did_op = it.get("documento_id")
            oid = it.get("odoo_pago_id") or it.get("odoo_res_id")
            if pid_mirror:
                pagos_mirror_ids.append(pid_mirror)
            elif oid and str(oid).isdigit():
                pagos_mirror_ids.append(str(oid))
            if did_op is not None and str(did_op).isdigit():
                doc_ids.append(int(str(did_op)))

            resumen_items.append(
                {
                    "origen": str(it.get("origen_sistema") or it.get("origen") or "cxc"),
                    "ref_id": pid_mirror or (f"OP-{did_op}" if did_op else ""),
                    "numero_cxc": str(it.get("numero_cxc") or it.get("numero") or ""),
                    "odoo_numero": str(it.get("odoo_numero") or ""),
                    "odoo_id": str(oid) if oid else "",
                    "cliente": str(it.get("contraparte_nombre") or it.get("cliente") or ""),
                    "pedido_ref": str(it.get("pedido_ref") or ""),
                    "metodo_pago": str(it.get("metodo_pago") or "Efectivo"),
                    "fecha_pago": str(it.get("fecha_documento") or it.get("fecha_pago") or "")[:10],
                    "moneda": mon,
                    "monto": float(q2(monto)),
                    "equiv_usd": float(q2(eq_val)),
                }
            )

        ahora = datetime.now()
        f_lote = fecha_lote or ahora.date()
        with self._engine.begin() as conn:
            num_recibo = self._siguiente_numero_recibo(conn, f_lote)
            row = conn.execute(
                insert(lotes_recibo_efectivo)
                .values(
                    numero_recibo=num_recibo,
                    vendedor_email=vendedor_email.strip(),
                    vendedor_nombre=(vendedor_nombre or vendedor_email).strip(),
                    fecha_lote=f_lote,
                    cantidad_pagos=len(resumen_items),
                    total_usd=q2(total_usd),
                    total_ves=q2(total_ves),
                    total_eur=q2(total_eur),
                    total_equivalente_usd=q2(total_eq_usd),
                    pagos_ref_json=json.dumps(resumen_items, ensure_ascii=False),
                    recibido_por=recibido_por.strip(),
                    recibido_en=ahora,
                    observaciones=observaciones.strip(),
                    estado="EMITIDO",
                )
                .returning(lotes_recibo_efectivo)
            ).one()
            lote_id = int(row.lote_id)

            if pagos_mirror_ids:
                conn.execute(
                    update(pagos)
                    .where(pagos.c.pago_id.in_(list(dict.fromkeys(pagos_mirror_ids))))
                    .values(
                        recibido=True,
                        numero_recibido=num_recibo,
                        fecha_recibido=ahora,
                        recibido_por=recibido_por.strip(),
                    )
                )

            if doc_ids:
                docs_actuales = conn.execute(
                    select(
                        documentos_operativos.c.documento_id,
                        documentos_operativos.c.estado,
                    ).where(documentos_operativos.c.documento_id.in_(list(dict.fromkeys(doc_ids))))
                ).all()
                for doc_r in docs_actuales:
                    vals_op: dict[str, Any] = {
                        "confirmado_cxc": True,
                        "confirmado_cxc_por": recibido_por.strip(),
                        "confirmado_cxc_en": ahora,
                        "numero_recibo": num_recibo,
                        "recibo_emitido_en": ahora,
                        "lote_recibo_id": lote_id,
                        "actualizado_en": ahora,
                    }
                    if doc_r.estado == PENDIENTE_APROBACION:
                        vals_op["estado"] = CONFIRMADO_CXC
                    conn.execute(
                        update(documentos_operativos)
                        .where(documentos_operativos.c.documento_id == doc_r.documento_id)
                        .values(**vals_op)
                    )

        return _row_lote_a_dict(row)


def _esc(val: Any) -> str:
    return html.escape(str(val if val not in (None, "") else ""))


def _bloque_copia_recibo(lote: dict[str, Any], etiqueta_copia: str) -> str:
    filas = []
    for idx, p in enumerate(lote.get("pagos") or [], start=1):
        filas.append(
            "<tr>"
            f"<td>{idx}</td>"
            f"<td>{_esc((p.get('origen') or 'cxc').upper())}</td>"
            f"<td>{_esc(p.get('numero_cxc') or '—')}</td>"
            f"<td>{_esc(p.get('odoo_numero') or '—')} {_esc('#' + str(p['odoo_id']) if p.get('odoo_id') else '')}</td>"
            f"<td>{_esc(p.get('cliente') or '—')}</td>"
            f"<td>{_esc(p.get('pedido_ref') or '—')}</td>"
            f"<td class='num'>{float(p.get('monto') or 0):,.2f} {_esc(p.get('moneda') or '')}</td>"
            f"<td class='num'>${float(p.get('equiv_usd') or 0):,.2f}</td>"
            "</tr>"
        )
    totales_partes = []
    if float(lote.get("total_usd") or 0) > 0:
        totales_partes.append(f"${float(lote['total_usd']):,.2f} USD")
    if float(lote.get("total_ves") or 0) > 0:
        totales_partes.append(f"Bs. {float(lote['total_ves']):,.2f}")
    if float(lote.get("total_eur") or 0) > 0:
        totales_partes.append(f"€ {float(lote['total_eur']):,.2f}")
    totales_str = " · ".join(totales_partes) or "$0.00"
    obs_html = (
        f'<div class="obs"><strong>Observaciones:</strong> {_esc(lote.get("observaciones"))}</div>'
        if lote.get("observaciones")
        else ""
    )

    return f"""
    <section class="rec-card">
      <div class="rec-head">
        <div>
          <div class="rec-brand">Lubrikca C.A. · Recibo de Efectivo por Lote</div>
          <div class="rec-sub">Vendedor: <strong>{_esc(lote.get("vendedor_nombre") or lote.get("vendedor_email") or "—")}</strong> ({_esc(lote.get("vendedor_email") or "")})</div>
        </div>
        <div class="rec-num">
          <div class="copia-pill">{_esc(etiqueta_copia)}</div>
          <div class="code">{_esc(lote.get("numero_recibo"))}</div>
          <div class="rec-sub">Fecha: {_esc(lote.get("fecha_lote"))} · Emitido: {_esc(str(lote.get("recibido_en") or "").replace("T", " "))}</div>
        </div>
      </div>
      <div class="rec-amt">
        <div>
          <div class="lbl">Total Recibido ({int(lote.get("cantidad_pagos") or 0)} pagos en efectivo)</div>
          <div class="val">{_esc(totales_str)}</div>
          <div class="rec-sub">Equivalente BCV/USD total: <strong>${float(lote.get("total_equivalente_usd") or 0):,.2f} USD</strong></div>
        </div>
        <div class="rec-sub" style="text-align:right;">
          <div>Recibido en caja por:</div>
          <strong>{_esc(lote.get("recibido_por") or "—")}</strong>
        </div>
      </div>
      <table>
        <thead>
          <tr>
            <th>#</th><th>Origen</th><th>Nº CxC</th><th>Odoo (Nº / ID)</th>
            <th>Cliente</th><th>Pedido/Ref</th><th class="num">Monto</th><th class="num">Equiv. USD</th>
          </tr>
        </thead>
        <tbody>{"".join(filas)}</tbody>
      </table>
      {obs_html}
      <div class="firmas">
        <div class="firma-box">Entregado por Vendedor ({_esc(lote.get("vendedor_nombre") or "Vendedor")})</div>
        <div class="firma-box">Recibido Conforme ({_esc(lote.get("recibido_por") or "Caja / CxC")})</div>
      </div>
    </section>
    """


def render_recibo_lote_html(lote: dict[str, Any]) -> str:
    """Genera el HTML imprimible (Original + Copia) para un lote de recibo de efectivo."""
    num = _esc(lote.get("numero_recibo") or "REC-LOTE")
    original = _bloque_copia_recibo(lote, "ORIGINAL · EMPRESA / CAJA")
    copia = _bloque_copia_recibo(lote, "COPIA · VENDEDOR")
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{num} · Recibo de Efectivo por Lote · Lubrikca</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 1.25rem; background: #f1f5f9; color: #0f172a; }}
    .toolbar {{ max-width: 920px; margin: 0 auto 1rem; display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; }}
    .btn {{ background: #1e3a8a; color: #fff; border: none; border-radius: 8px; padding: 0.55rem 1rem; font-weight: 700; cursor: pointer; font-size: 0.88rem; }}
    .rec-card {{ max-width: 920px; margin: 0 auto 1.25rem; background: #fff; border: 1px solid #cbd5e1; border-radius: 12px; padding: 1.35rem; box-shadow: 0 6px 20px rgba(15,23,42,0.06); }}
    .rec-head {{ display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid #0f172a; padding-bottom: 0.75rem; margin-bottom: 0.85rem; gap: 1rem; flex-wrap: wrap; }}
    .rec-brand {{ font-size: 1.15rem; font-weight: 800; }}
    .rec-sub {{ font-size: 0.82rem; color: #475569; margin-top: 0.15rem; }}
    .rec-num {{ text-align: right; }}
    .copia-pill {{ display: inline-block; font-size: 0.7rem; font-weight: 700; padding: 0.15rem 0.5rem; border-radius: 999px; background: #e2e8f0; color: #334155; margin-bottom: 0.25rem; }}
    .rec-num .code {{ font-size: 1.15rem; font-weight: 800; color: #1d4ed8; }}
    .rec-amt {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 0.75rem 1rem; display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.85rem; flex-wrap: wrap; gap: 0.5rem; }}
    .rec-amt .lbl {{ font-size: 0.74rem; color: #64748b; text-transform: uppercase; font-weight: 700; }}
    .rec-amt .val {{ font-size: 1.35rem; font-weight: 800; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-bottom: 0.85rem; }}
    th, td {{ padding: 0.42rem 0.5rem; border-bottom: 1px solid #e2e8f0; text-align: left; }}
    th {{ background: #f8fafc; color: #475569; font-weight: 700; }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .obs {{ font-size: 0.84rem; color: #334155; margin-bottom: 0.85rem; }}
    .firmas {{ display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; margin-top: 1.5rem; padding-top: 0.5rem; }}
    .firma-box {{ border-top: 1px solid #334155; padding-top: 0.35rem; text-align: center; font-size: 0.8rem; color: #475569; }}
    @media print {{
      body {{ background: #fff; padding: 0; }}
      .no-print {{ display: none !important; }}
      .rec-card {{ box-shadow: none; border: 1px solid #94a3b8; page-break-inside: avoid; }}
    }}
  </style>
</head>
<body>
  <div class="toolbar no-print">
    <strong>Comprobante Agrupado de Entrega de Efectivo · {num}</strong>
    <button class="btn" onclick="window.print()">🖨️ Imprimir / Guardar PDF (Original + Copia)</button>
  </div>
  {original}
  {copia}
</body>
</html>"""
