# ruff: noqa: E501
"""Renderizador HTML seguro e imprimible del Preview de Documentos Operativos en CxC."""

from __future__ import annotations

import html
from typing import Any

from cxc.capturas.modelos import es_efectivo

from .modelos import (
    BORRADOR,
    CONFIRMADO_CXC,
    ENVIADO_ODOO,
    ERROR_ODOO,
    INCOMPLETO,
    PENDIENTE_APROBACION,
    RECHAZADO,
    TIPO_FACTURA_PROVEEDOR,
    TIPO_GASTO,
    TIPO_PAGO,
    TIPO_PAGO_PROVEEDOR,
    DocumentoOperativo,
)


def _esc(val: Any) -> str:
    return html.escape(str(val if val not in (None, "") else ""))


def renderizar_preview_operativo(doc: DocumentoOperativo) -> str:
    """Genera el HTML autónomo del preview (/comprobante/<token>) para Pagos, Compras y Gastos."""
    d = doc.a_dict()
    tipo = doc.tipo

    if tipo == TIPO_PAGO:
        icono_tipo = "💵 Pago Recibido de Cliente"
        contraparte_lbl = "Cliente"
        donde_aprobar = "Cobranza → Pagos Recibidos por Aprobar"
    elif tipo == TIPO_FACTURA_PROVEEDOR:
        icono_tipo = "🧾 Factura / Recibo de Proveedor"
        contraparte_lbl = "Proveedor"
        donde_aprobar = "Capturas Telegram → Aprobación: Facturas, Pagos Prov. y Gastos"
    elif tipo == TIPO_PAGO_PROVEEDOR:
        icono_tipo = "💸 Pago a Proveedor"
        contraparte_lbl = "Proveedor"
        donde_aprobar = "Capturas Telegram → Aprobación: Facturas, Pagos Prov. y Gastos"
    else:
        icono_tipo = "🛒 Gasto sin Factura / Compra Menor"
        contraparte_lbl = "Comercio / Proveedor"
        donde_aprobar = "Capturas Telegram → Aprobación: Facturas, Pagos Prov. y Gastos"

    if doc.estado == ENVIADO_ODOO:
        badge_txt = f"✅ Autorizado y Enviado a Odoo ({doc.odoo_ref})"
        badge_cls = "badge-ok"
    elif doc.estado == CONFIRMADO_CXC:
        rec_lbl = f" · Recibo {doc.numero_recibo}" if doc.numero_recibo else ""
        badge_txt = f"✔️ Confirmado en CxC{rec_lbl}"
        badge_cls = "badge-ok"
    elif doc.estado == PENDIENTE_APROBACION:
        badge_txt = "⏳ Pendiente por aprobación en CxC"
        badge_cls = "badge-warn"
    elif doc.estado == INCOMPLETO:
        badge_txt = "⚠️ Borrador Incompleto (faltan datos)"
        badge_cls = "badge-warn"
    elif doc.estado in (RECHAZADO, ERROR_ODOO):
        badge_txt = "❌ Rechazado" if doc.estado == RECHAZADO else "⚠️ Error al enviar a Odoo"
        badge_cls = "badge-err"
    else:
        badge_txt = f"📋 Borrador ({doc.estado})"
        badge_cls = "badge-info"

    val = doc.validacion or {}
    alertas: list[str] = list(val.get("alertas") or [])
    faltantes: list[str] = list(doc.faltantes or [])
    alertas_html = ""
    if faltantes or alertas:
        items_a: list[str] = []
        if faltantes:
            items_a.append(
                f"<li><strong>Campos faltantes:</strong> {_esc(', '.join(faltantes))}</li>"
            )
        for a in alertas:
            items_a.append(f"<li>{_esc(a)}</li>")
        alertas_html = f"""
        <div class="alert-box">
          <strong>⚠️ Validación y Alertas CxC:</strong>
          <ul>{''.join(items_a)}</ul>
        </div>
        """

    equiv = val.get("equivalente_usd") if isinstance(val.get("equivalente_usd"), dict) else None
    equiv_html = ""
    if equiv:
        partes_eq = []
        if equiv.get("usd_bcv") is not None:
            partes_eq.append(
                f"${float(equiv['usd_bcv']):,.2f} BCV (tasa {float(equiv['tasa_bcv']):,.2f})"
            )
        if equiv.get("usd_binance") is not None:
            partes_eq.append(
                f"${float(equiv['usd_binance']):,.2f} Binance (tasa {float(equiv['tasa_binance']):,.2f})"
            )
        if partes_eq:
            equiv_html = (
                f'<div class="meta-sub">Ref. equivalente: {_esc(" · ".join(partes_eq))}</div>'
            )

    filas_detalle: list[tuple[str, str]] = [
        (
            contraparte_lbl,
            f"{doc.contraparte_nombre or '—'}"
            + (
                f" (Odoo #{doc.partner_odoo_id})"
                if doc.partner_odoo_id
                else " (sin vincular / nuevo)"
            ),
        ),
        ("RIF / Cédula", doc.rif or "—"),
        ("Fecha", doc.fecha_documento.isoformat() if doc.fecha_documento else "—"),
        ("Monto Total", f"{float(doc.monto):,.2f} {doc.moneda}" if doc.monto is not None else "—"),
    ]
    if tipo in (TIPO_PAGO, TIPO_PAGO_PROVEEDOR):
        if tipo == TIPO_PAGO and doc.es_zelle_tercero:
            zt_raw = val.get("zelle_tercero")
            zt: dict[str, Any] = zt_raw if isinstance(zt_raw, dict) else {}
            filas_detalle.extend(
                [
                    ("Modalidad", "💵 Zelle a Cuenta de Tercero (Sin tocar bancos)"),
                    (
                        "Cuenta / Beneficiario",
                        doc.beneficiario_tercero or zt.get("titular") or zt.get("alias") or "—",
                    ),
                    (
                        "Proveedor CxP Odoo",
                        zt.get("proveedor_nombre") or f"#{doc.cuenta_tercero_id or '?'}",
                    ),
                    ("Referencia Zelle", doc.referencia or "—"),
                ]
            )
        else:
            filas_detalle.extend(
                [
                    ("Método de pago", doc.metodo_pago or "—"),
                    (
                        "Referencia",
                        "Efectivo (memo vacío en Odoo)"
                        if d["es_efectivo"]
                        else (doc.referencia or "—"),
                    ),
                    ("Banco / Diario", doc.diario_nombre or doc.banco or "Por seleccionar en CxC"),
                ]
            )
        if doc.numero_recibo:
            filas_detalle.append(("Nº Recibo CxC", doc.numero_recibo))
        if tipo == TIPO_PAGO:
            filas_detalle.append(("Pedido / Factura", doc.pedido_ref or "—"))
        else:
            filas_detalle.append(("Factura proveedor", doc.factura_ref or "—"))
    else:
        num_txt = (
            "Sin factura (generará ref automática en Odoo)"
            if (tipo == TIPO_GASTO and not doc.numero_documento)
            else (doc.numero_documento or "—")
        )
        filas_detalle.append(("Nº Factura / Recibo", num_txt))
        if doc.numero_control:
            filas_detalle.append(("Nº Control", doc.numero_control))
        if doc.fecha_vencimiento:
            filas_detalle.append(("Fecha Vencimiento", doc.fecha_vencimiento.isoformat()))
        if doc.base_imponible is not None:
            filas_detalle.append(
                ("Base Imponible", f"{float(doc.base_imponible):,.2f} {doc.moneda}")
            )
        if doc.iva is not None:
            filas_detalle.append(("IVA", f"{float(doc.iva):,.2f} {doc.moneda}"))
        if tipo == TIPO_GASTO and doc.categoria:
            filas_detalle.append(("Categoría gasto", doc.categoria))
        filas_detalle.append(("Concepto", doc.concepto or "—"))
        filas_detalle.append(
            ("Diario de Compras", doc.diario_nombre or "Diario Compras VES (Por defecto)")
        )
        if doc.cuenta_contable_codigo or doc.cuenta_contable_nombre:
            filas_detalle.append(
                (
                    "Cuenta Contable Sugerida",
                    f"{doc.cuenta_contable_codigo} · {doc.cuenta_contable_nombre}".strip(" ·"),
                )
            )
        if tipo == TIPO_GASTO and (doc.metodo_pago or doc.referencia):
            filas_detalle.append(("Pago del gasto", f"{doc.metodo_pago} {doc.referencia}".strip()))
        if doc.orden_ref:
            filas_detalle.append(("Orden de Compra", doc.orden_ref))

    if doc.notas:
        filas_detalle.append(("Notas", doc.notas))
    if doc.aprobado_por:
        filas_detalle.append(
            (
                "Revisado por",
                f"{doc.aprobado_por} ({doc.aprobado_en.isoformat()[:16].replace('T', ' ') if doc.aprobado_en else ''})",
            )
        )
    if doc.rechazo_motivo:
        filas_detalle.append(("Motivo rechazo", doc.rechazo_motivo))

    rows_html = "".join(f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>" for k, v in filas_detalle)

    ordenes_html = ""
    ordenes = val.get("ordenes_abiertas") or []
    if tipo == TIPO_PAGO and ordenes:
        rows_o = "".join(
            f"<tr><td><strong>{_esc(o.get('so_id'))}</strong></td>"
            f"<td>{_esc(o.get('fecha'))}</td>"
            f"<td class='num'>${float(o.get('monto_total') or 0):,.2f}</td>"
            f"<td>{_esc(o.get('lista_precios') or '—')}</td></tr>"
            for o in ordenes
        )
        ordenes_html = f"""
        <div class="section-box">
          <div class="section-title">🛒 Órdenes Abiertas del Cliente en CxC</div>
          <table>
            <thead><tr><th>Orden</th><th>Fecha</th><th class="num">Total</th><th>Lista</th></tr></thead>
            <tbody>{rows_o}</tbody>
          </table>
        </div>
        """

    regla_odoo = _esc(
        val.get("regla_odoo") or "Se enviará en borrador a Odoo tras aprobación autorizada en CxC."
    )

    completar_html = ""
    if doc.estado in (INCOMPLETO, BORRADOR):
        extra_field = (
            f"""
            <div>
              <label>Referencia de pago</label>
              <input id="cmp-referencia" value="{_esc(doc.referencia)}" placeholder="Nº operación">
            </div>
            <div>
              <label>Banco</label>
              <input id="cmp-banco" value="{_esc(doc.banco)}" placeholder="Banesco, BDV, Zelle…">
            </div>
            <div>
              <label>Método</label>
              <input id="cmp-metodo" value="{_esc(doc.metodo_pago)}" placeholder="Transferencia, Efectivo…">
            </div>
            """
            if tipo in (TIPO_PAGO, TIPO_PAGO_PROVEEDOR)
            else f"""
            <div>
              <label>Nº Factura / Recibo</label>
              <input id="cmp-num-doc" value="{_esc(doc.numero_documento)}" placeholder="Opcional en gastos">
            </div>
            <div>
              <label>Concepto</label>
              <input id="cmp-concepto" value="{_esc(doc.concepto)}" placeholder="Detalle del gasto/compra">
            </div>
            """
        )
        completar_html = f"""
        <div class="section-box no-print" id="box-completar" style="background:#f8fafc;">
          <div class="section-title">✏️ Completar Datos y Enviar a Cola de Aprobación CxC</div>
          <div class="grid-form">
            <div>
              <label>{_esc(contraparte_lbl)}</label>
              <input id="cmp-contraparte" value="{_esc(doc.contraparte_nombre)}">
            </div>
            <div>
              <label>RIF</label>
              <input id="cmp-rif" value="{_esc(doc.rif)}">
            </div>
            <div>
              <label>Monto ({_esc(doc.moneda)})</label>
              <input id="cmp-monto" type="number" step="0.01" value="{_esc(doc.monto)}">
            </div>
            <div>
              <label>Fecha (AAAA-MM-DD)</label>
              <input id="cmp-fecha" type="date" value="{_esc(doc.fecha_documento.isoformat() if doc.fecha_documento else '')}">
            </div>
            {extra_field}
          </div>
          <div style="margin-top:0.85rem; display:flex; gap:0.65rem; flex-wrap:wrap; align-items:center;">
            <button class="btn-ok" id="btn-confirmar-op" onclick="confirmarOperativo()">
              ✅ Completar y Enviar a Aprobación en CxC
            </button>
            <span id="msg-confirmar" style="font-size:0.86rem; font-weight:600;"></span>
          </div>
        </div>
        """

    script_js = f"""
    <script>
      async function confirmarOperativo() {{
        const btn = document.getElementById('btn-confirmar-op');
        const msg = document.getElementById('msg-confirmar');
        if (!btn) return;
        btn.disabled = true;
        msg.textContent = 'Enviando a CxC...';
        const body = {{
          contraparte_nombre: (document.getElementById('cmp-contraparte') || {{}}).value || '',
          rif: (document.getElementById('cmp-rif') || {{}}).value || '',
          monto: (document.getElementById('cmp-monto') || {{}}).value || null,
          fecha_documento: (document.getElementById('cmp-fecha') || {{}}).value || null,
        }};
        if (document.getElementById('cmp-referencia')) body.referencia = document.getElementById('cmp-referencia').value;
        if (document.getElementById('cmp-banco')) body.banco = document.getElementById('cmp-banco').value;
        if (document.getElementById('cmp-metodo')) body.metodo_pago = document.getElementById('cmp-metodo').value;
        if (document.getElementById('cmp-num-doc')) body.numero_documento = document.getElementById('cmp-num-doc').value;
        if (document.getElementById('cmp-concepto')) body.concepto = document.getElementById('cmp-concepto').value;
        try {{
          const res = await fetch('/comprobante/{_esc(doc.token_preview)}/confirmar', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(body),
          }});
          const data = await res.json().catch(() => ({{}}));
          if (!res.ok) throw new Error(data.error || data.detail || ('Error ' + res.status));
          msg.style.color = '#15803d';
          msg.textContent = '✅ Listo en cola de aprobación CxC (' + (data.numero || '') + '). Recargando...';
          setTimeout(() => location.reload(), 700);
        }} catch (e) {{
          btn.disabled = false;
          msg.style.color = '#b91c1c';
          msg.textContent = '⚠️ ' + e.message;
        }}
      }}
    </script>
    """

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(doc.numero)} · {_esc(icono_tipo)} · Lubrikca CxC</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 1.25rem; background: #f1f5f9; color: #0f172a; }}
    .doc-card {{ max-width: 820px; margin: 0 auto; background: #fff; border-radius: 14px; box-shadow: 0 10px 28px rgba(15,23,42,0.08); overflow: hidden; border: 1px solid #e2e8f0; }}
    .doc-header {{ background: linear-gradient(135deg, #0f172a, #1e3a8a); color: #fff; padding: 1.35rem 1.5rem; display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; flex-wrap: wrap; }}
    .doc-brand {{ font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.08em; opacity: 0.85; }}
    .doc-title {{ font-size: 1.35rem; font-weight: 800; margin: 0.25rem 0; }}
    .doc-sub {{ font-size: 0.86rem; opacity: 0.9; }}
    .badge {{ display: inline-block; padding: 0.35rem 0.75rem; border-radius: 999px; font-size: 0.8rem; font-weight: 700; margin-top: 0.25rem; }}
    .badge-ok {{ background: #dcfce7; color: #166534; }}
    .badge-warn {{ background: #fef3c7; color: #92400e; }}
    .badge-err {{ background: #fee2e2; color: #991b1b; }}
    .badge-info {{ background: #e0e7ff; color: #1e40af; }}
    .doc-body {{ padding: 1.4rem 1.5rem; }}
    .hero-amt {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 1rem 1.15rem; margin-bottom: 1rem; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem; }}
    .hero-amt .big {{ font-size: 1.55rem; font-weight: 800; color: #0f172a; }}
    .meta-sub {{ font-size: 0.84rem; color: #475569; margin-top: 0.2rem; }}
    .alert-box {{ background: #fffbeb; border-left: 4px solid #f59e0b; border-radius: 8px; padding: 0.85rem 1rem; margin-bottom: 1rem; color: #92400e; font-size: 0.88rem; }}
    .alert-box ul {{ margin: 0.4rem 0 0 1.2rem; padding: 0; }}
    .rule-box {{ background: #eff6ff; border-left: 4px solid #2563eb; border-radius: 8px; padding: 0.75rem 1rem; margin-bottom: 1rem; color: #1e3a8a; font-size: 0.84rem; }}
    .section-box {{ border: 1px solid #e2e8f0; border-radius: 10px; padding: 1rem 1.15rem; margin-bottom: 1rem; }}
    .section-title {{ font-weight: 700; font-size: 0.92rem; color: #1e293b; margin-bottom: 0.65rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
    th, td {{ padding: 0.48rem 0.5rem; border-bottom: 1px solid #f1f5f9; text-align: left; }}
    th {{ color: #475569; width: 38%; font-weight: 600; }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .grid-form {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 0.75rem; }}
    .grid-form label {{ display: block; font-size: 0.78rem; color: #475569; font-weight: 600; margin-bottom: 0.2rem; }}
    .grid-form input {{ width: 100%; padding: 0.45rem 0.6rem; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 0.88rem; }}
    .actions {{ display: flex; justify-content: space-between; align-items: center; gap: 0.75rem; flex-wrap: wrap; margin-top: 1rem; }}
    .btn-sec {{ background: #e2e8f0; color: #1e293b; border: none; padding: 0.55rem 0.95rem; border-radius: 8px; font-weight: 600; cursor: pointer; }}
    .btn-ok {{ background: #16a34a; color: #fff; border: none; padding: 0.6rem 1.15rem; border-radius: 8px; font-weight: 700; cursor: pointer; }}
    .btn-ok:disabled {{ opacity: 0.6; cursor: not-allowed; }}
    @media print {{
      body {{ background: #fff; padding: 0; }}
      .doc-card {{ box-shadow: none; border: none; }}
      .no-print {{ display: none !important; }}
    }}
  </style>
</head>
<body>
  <div class="doc-card">
    <div class="doc-header">
      <div>
        <div class="doc-brand">Lubrikca C.A. · Panel de CxC y Tesorería</div>
        <div class="doc-title">{_esc(doc.numero)} · {_esc(icono_tipo)}</div>
        <div class="doc-sub">{_esc(contraparte_lbl)}: <strong>{_esc(doc.contraparte_nombre or 'Sin definir')}</strong> · Origen: {_esc(doc.origen)}</div>
      </div>
      <div>
        <span class="badge {badge_cls}">{_esc(badge_txt)}</span>
      </div>
    </div>
    <div class="doc-body">
      <div class="hero-amt">
        <div>
          <div style="font-size:0.78rem; color:#64748b; text-transform:uppercase; font-weight:600;">Monto del Documento</div>
          <div class="big">{f"{float(doc.monto):,.2f}" if doc.monto is not None else "—"} {_esc(doc.moneda)}</div>
          {equiv_html}
        </div>
        <div style="text-align:right; font-size:0.82rem; color:#475569;">
          <div>Panel de autorización:</div>
          <strong>{_esc(donde_aprobar)}</strong>
        </div>
      </div>
      {alertas_html}
      <div class="rule-box"><strong>Regla Odoo al autorizar:</strong> {regla_odoo}</div>
      <div class="section-box">
        <div class="section-title">📋 Detalle del Documento</div>
        <table><tbody>{rows_html}</tbody></table>
      </div>
      {ordenes_html}
      {completar_html}
      <div class="actions no-print">
        <div style="display:flex; gap:0.5rem; flex-wrap:wrap;">
          <button class="btn-sec" onclick="window.print()">🖨️ Imprimir / Guardar PDF</button>
          {f'<a class="btn-ok" style="text-decoration:none;" href="/recibo/{_esc(doc.token_preview)}">🧾 Ver Recibo {_esc(doc.numero_recibo)}</a>' if doc.numero_recibo else ""}
        </div>
        <span style="font-size:0.8rem; color:#64748b;">Token: {_esc(doc.token_preview[:10])}… · CxC Lubrikca</span>
      </div>
    </div>
  </div>
  {script_js}
</body>
</html>"""


def renderizar_recibo_operativo(
    doc: DocumentoOperativo, cuenta_tercero: dict[str, Any] | None = None
) -> str:
    """Recibo oficial imprimible CxC-first (``REC-...`` o ``EGR-...``) para pagos confirmados."""
    es_ingreso = doc.tipo == TIPO_PAGO
    titulo_rec = "Recibo Oficial de Cobro" if es_ingreso else "Comprobante Oficial de Egreso"
    rol_contraparte = "Recibido de (Cliente)" if es_ingreso else "Pagado a (Proveedor)"
    val = doc.validacion or {}
    equiv = val.get("equivalente_usd") if isinstance(val.get("equivalente_usd"), dict) else None
    eq_txt = ""
    if equiv and equiv.get("usd_bcv") is not None:
        eq_txt = f"Ref. equivalente: ${float(equiv['usd_bcv']):,.2f} BCV (tasa {float(equiv['tasa_bcv']):,.2f})"

    zt = cuenta_tercero or (
        val.get("zelle_tercero") if isinstance(val.get("zelle_tercero"), dict) else None
    )
    if doc.es_zelle_tercero:
        modalidad_txt = "Zelle a Cuenta de Tercero (Proveedor)"
        destino_lbl = (
            f"{doc.beneficiario_tercero or (zt or {}).get('titular') or '—'} · "
            f"Proveedor: {(zt or {}).get('proveedor_nombre') or 'Configurado'}"
        )
    elif es_efectivo(doc.metodo_pago):
        modalidad_txt = "Efectivo"
        destino_lbl = doc.diario_nombre or doc.banco or "Caja / Custodia Efectivo CxC"
    else:
        modalidad_txt = doc.metodo_pago or "Transferencia / Digital"
        destino_lbl = doc.diario_nombre or doc.banco or "—"

    fecha_conf = (
        doc.confirmado_cxc_en.isoformat()[:16].replace("T", " ")
        if doc.confirmado_cxc_en
        else (
            doc.recibo_emitido_en.isoformat()[:16].replace("T", " ")
            if doc.recibo_emitido_en
            else "—"
        )
    )
    odoo_txt = (
        f"{doc.odoo_ref} ({doc.odoo_model})"
        if doc.odoo_ref
        else "Confirmado en CxC · Pendiente despacho a Odoo"
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(doc.numero_recibo)} · {_esc(titulo_rec)} · Lubrikca</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 1.5rem; background: #f1f5f9; color: #0f172a; }}
    .rec-card {{ max-width: 720px; margin: 0 auto; background: #fff; border: 1px solid #cbd5e1; border-radius: 12px; padding: 1.75rem; box-shadow: 0 8px 24px rgba(15,23,42,0.08); }}
    .rec-head {{ display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid #0f172a; padding-bottom: 1rem; margin-bottom: 1rem; gap: 1rem; flex-wrap: wrap; }}
    .rec-brand {{ font-size: 1.25rem; font-weight: 800; color: #0f172a; }}
    .rec-sub {{ font-size: 0.85rem; color: #475569; margin-top: 0.2rem; }}
    .rec-num {{ text-align: right; }}
    .rec-num .code {{ font-size: 1.2rem; font-weight: 800; color: #1d4ed8; }}
    .rec-amt {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 0.95rem 1.1rem; display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; flex-wrap: wrap; gap: 0.5rem; }}
    .rec-amt .val {{ font-size: 1.65rem; font-weight: 800; color: #0f172a; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; margin-bottom: 1.25rem; }}
    th, td {{ padding: 0.5rem; border-bottom: 1px solid #f1f5f9; text-align: left; }}
    th {{ width: 38%; color: #475569; font-weight: 600; }}
    .firmas {{ display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; margin-top: 2rem; padding-top: 1rem; }}
    .firma-box {{ border-top: 1px solid #334155; padding-top: 0.4rem; text-align: center; font-size: 0.84rem; color: #475569; }}
    .actions {{ margin-top: 1.25rem; display: flex; justify-content: space-between; align-items: center; }}
    .btn {{ background: #1e3a8a; color: #fff; border: none; border-radius: 8px; padding: 0.55rem 1rem; font-weight: 700; cursor: pointer; text-decoration: none; font-size: 0.88rem; }}
    .btn-sec {{ background: #e2e8f0; color: #0f172a; }}
    @media print {{ body {{ background: #fff; padding: 0; }} .rec-card {{ box-shadow: none; border: none; }} .no-print {{ display: none !important; }} }}
  </style>
</head>
<body>
  <div class="rec-card">
    <div class="rec-head">
      <div>
        <div class="rec-brand">Lubrikca C.A. · {_esc(titulo_rec)}</div>
        <div class="rec-sub">Documento origen CxC: <strong>{_esc(doc.numero)}</strong> · Canal: {_esc(doc.origen)}</div>
      </div>
      <div class="rec-num">
        <div style="font-size:0.75rem; color:#64748b; text-transform:uppercase;">Nº de Recibo</div>
        <div class="code">{_esc(doc.numero_recibo)}</div>
        <div class="rec-sub">Emitido: {_esc(fecha_conf)}</div>
      </div>
    </div>
    <div class="rec-amt">
      <div>
        <div style="font-size:0.75rem; color:#64748b; text-transform:uppercase; font-weight:600;">Monto Confirmado</div>
        <div class="val">{f"{float(doc.monto):,.2f}" if doc.monto is not None else "—"} {_esc(doc.moneda)}</div>
        {f'<div class="rec-sub">{_esc(eq_txt)}</div>' if eq_txt else ""}
      </div>
      <div class="rec-sub" style="text-align:right;">
        <div>Confirmado en CxC por:</div>
        <strong>{_esc(doc.confirmado_cxc_por or "sistema")}</strong>
      </div>
    </div>
    <table>
      <tbody>
        <tr><th>{_esc(rol_contraparte)}</th><td><strong>{_esc(doc.contraparte_nombre or "—")}</strong> {_esc(f"({doc.rif})" if doc.rif else "")}</td></tr>
        <tr><th>Fecha de pago</th><td>{_esc(doc.fecha_documento.isoformat() if doc.fecha_documento else "—")}</td></tr>
        <tr><th>Modalidad / Método</th><td>{_esc(modalidad_txt)}</td></tr>
        <tr><th>Referencia</th><td>{_esc(doc.referencia or ("Efectivo sin referencia" if es_efectivo(doc.metodo_pago) else "—"))}</td></tr>
        <tr><th>Diario / Destino</th><td>{_esc(destino_lbl)}</td></tr>
        <tr><th>{"Pedido / Factura" if es_ingreso else "Factura proveedor"}</th><td>{_esc((doc.pedido_ref if es_ingreso else doc.factura_ref) or "—")}</td></tr>
        <tr><th>Estado en Odoo</th><td>{_esc(odoo_txt)}</td></tr>
        {f"<tr><th>Notas</th><td>{_esc(doc.notas)}</td></tr>" if doc.notas else ""}
      </tbody>
    </table>
    <div class="firmas">
      <div class="firma-box">Entregado por</div>
      <div class="firma-box">Recibido / Conforme ({_esc(doc.confirmado_cxc_por or "CxC")})</div>
    </div>
    <div class="actions no-print">
      <div style="display:flex; gap:0.5rem;">
        <button class="btn" onclick="window.print()">🖨️ Imprimir / Guardar PDF</button>
        <a class="btn btn-sec" href="/comprobante/{_esc(doc.token_preview)}">📋 Ver Comprobante</a>
      </div>
      <span class="rec-sub">CxC Lubrikca · Trazabilidad Contable</span>
    </div>
  </div>
</body>
</html>"""
