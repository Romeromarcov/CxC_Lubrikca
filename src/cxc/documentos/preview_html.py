# ruff: noqa: E501
"""Renderizador HTML seguro e imprimible del Preview de Cotización / Pedido en CxC."""

from __future__ import annotations

import html
from typing import Any

from .modelos import COTIZACION, ENVIADO_ODOO, PENDIENTE_APROBACION, RECHAZADO, DocumentoComercial


def _esc(val: Any) -> str:
    return html.escape(str(val or ""))


def renderizar_preview_cotizacion(
    doc: DocumentoComercial, *, solo_lectura_publico: bool = True
) -> str:
    """Genera el documento HTML autónomo de preview (estrictamente solo lectura)."""
    d = doc.a_dict(incluir_lineas=True)
    es_cot = doc.tipo == COTIZACION
    titulo_tipo = "Cotización Comercial" if es_cot else "Pedido de Venta"

    if doc.estado == ENVIADO_ODOO:
        pref_auto = "⚡ Auto-aprobado" if doc.auto_enviado else "✅ Aprobado"
        badge_txt = f"{pref_auto} · Orden Odoo {doc.odoo_order_name or doc.odoo_order_id}"
        badge_cls = "badge-ok"
    elif doc.estado == PENDIENTE_APROBACION:
        badge_txt = "⏳ Pedido pendiente por aprobación en CxC"
        badge_cls = "badge-warn"
    elif doc.estado == RECHAZADO:
        badge_txt = "❌ Rechazado"
        badge_cls = "badge-err"
    else:
        badge_txt = "📋 Cotización vigente" if es_cot else f"Estado: {doc.estado}"
        badge_cls = "badge-info"

    filas_html: list[str] = []
    for ln in d["lineas"]:
        filas_html.append(
            f"""
            <tr>
              <td class="mono">{_esc(ln["codigo"] or "—")}</td>
              <td>
                <div class="prod-name">{_esc(ln["descripcion"])}</div>
                <div class="prod-meta">{_esc(ln["presentacion"] or "Unidad")} · {ln["litros_linea"]:.2f} L</div>
              </td>
              <td class="num">{ln["cantidad"]:g}</td>
              <td class="num">${ln["precio_unitario"]:,.2f}</td>
              <td class="num strong">${ln["subtotal"]:,.2f}</td>
            </tr>
            """
        )

    descuentos_html = ""
    descuentos_lista: list[dict[str, Any]] = d.get("descuentos") or []
    especiales_lista: list[dict[str, Any]] = d.get("descuentos_especiales") or []
    if descuentos_lista or especiales_lista:
        items_d: list[str] = []
        for desc in descuentos_lista:
            cod = _esc(desc.get("codigo"))
            tit = _esc(desc.get("titulo"))
            cond = _esc(desc.get("condicion"))
            cumple = bool(desc.get("cumple", True))
            alerta = _esc(desc.get("alerta"))
            es_obs = bool(desc.get("es_obsequio"))
            val_txt = (
                _esc(desc.get("obsequio_detalle") or "Obsequio")
                if es_obs
                else f"-{float(desc.get('porcentaje') or 0):.2f}% (-${float(desc.get('monto_estimado_usd') or 0):,.2f})"
            )
            est_pill = (
                '<span class="pill-ok">✓ Parámetros OK</span>'
                if cumple
                else '<span class="pill-warn">⚠ Sujeto a revisión motor</span>'
            )
            alerta_div = f'<div class="desc-alert">⚠️ {alerta}</div>' if alerta else ""
            items_d.append(
                f"""
                <div class="desc-item">
                  <div class="desc-head">
                    <div><strong>[{cod}] {tit}</strong> {est_pill}</div>
                    <div class="desc-val">{val_txt}</div>
                  </div>
                  <div class="desc-cond">{cond}</div>
                  {alerta_div}
                </div>
                """
            )
        for esp in especiales_lista:
            mot_e = _esc(esp.get("motivo") or "Descuento especial comercial")
            pct_e = float(esp.get("porcentaje") or 0)
            mto_e = float(esp.get("monto_estimado_usd") or esp.get("monto_usd") or 0)
            items_d.append(
                f"""
                <div class="desc-item">
                  <div class="desc-head">
                    <div><strong>[ESPECIAL] {mot_e}</strong> <span class="pill-warn">⚠ Aprobación especial CxC</span></div>
                    <div class="desc-val">-{pct_e:.2f}% (-${mto_e:,.2f})</div>
                  </div>
                  <div class="desc-cond">Negociación comercial especial fuera de tabla</div>
                </div>
                """
            )
        descuentos_html = f"""
        <div class="section-box">
          <div class="section-title">🏷️ Beneficios y Descuentos Comerciales (Sujetos al Motor CxC)</div>
          {''.join(items_d)}
        </div>
        """

    alertas_banner = ""
    retencion_banner = ""
    if not solo_lectura_publico:
        alertas_globales: list[str] = d.get("alertas_motor") or []
        if alertas_globales:
            lis = "".join(f"<li>{_esc(a)}</li>" for a in alertas_globales)
            alertas_banner = f"""
            <div class="alert-box">
              <strong>⚠️ Alertas de validación del Motor CxC:</strong>
              <ul>{lis}</ul>
            </div>
            """

        motivos_ret: list[str] = d.get("motivos_retencion") or []
        if doc.estado == PENDIENTE_APROBACION and motivos_ret:
            lis_r = "".join(f"<li>{_esc(m)}</li>" for m in motivos_ret)
            retencion_banner = f"""
            <div class="alert-box">
              <strong>⏳ Motivos de retención para revisión en CxC:</strong>
              <ul>{lis_r}</ul>
            </div>
            """

    val_motor = d.get("validacion_motor") or {}
    tasa_bcv = val_motor.get("tasa_bcv")
    total_ves = val_motor.get("total_con_iva_ves")
    fila_ves = ""
    if tasa_bcv and total_ves:
        fila_ves = f"""
        <div class="tot-row tot-sub">
          <span>Equivalente referencial Bs. (BCV {float(tasa_bcv):,.2f}):</span>
          <span>Bs. {float(total_ves):,.2f}</span>
        </div>
        """

    notas_html = ""
    if doc.condiciones_negociacion or doc.observaciones:
        partes_n = []
        if doc.condiciones_negociacion:
            partes_n.append(
                f"<div><strong>Condiciones de negociación:</strong> {_esc(doc.condiciones_negociacion)}</div>"
            )
        if doc.observaciones:
            partes_n.append(f"<div><strong>Observaciones:</strong> {_esc(doc.observaciones)}</div>")
        notas_html = f'<div class="section-box">{"".join(partes_n)}</div>'

    bloque_conversion = ""
    if es_cot and doc.estado not in (ENVIADO_ODOO, RECHAZADO):
        bloque_conversion = """
        <div class="convert-card no-print">
          <div class="convert-header">
            <div>
              <h3 style="margin:0 0 4px 0;font-size:15px;">Vista informativa de solo lectura</h3>
              <p style="margin:0;font-size:13px;color:#475569;">
                Para aprobar y convertir esta cotización en Pedido formal, solicita confirmación a tu asesor comercial
                o ingresa al panel administrativo de CxC Lubrikca.
              </p>
            </div>
          </div>
        </div>
        """

    fecha_str = doc.creado_en.strftime("%d/%m/%Y %H:%M")

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(doc.numero)} · {_esc(doc.cliente_nombre)} · Lubrikca</title>
  <style>
    :root {{
      --ink: #0f172a;
      --muted: #475569;
      --line: #e2e8f0;
      --brand: #1e3a8a;
      --ok: #15803d;
      --warn: #b45309;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 24px 14px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f1f5f9;
      color: var(--ink);
    }}
    .sheet {{
      max-width: 860px;
      margin: 0 auto;
      background: #fff;
      border-radius: 14px;
      box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
      padding: 28px;
    }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      border-bottom: 2px solid var(--line);
      padding-bottom: 18px;
      flex-wrap: wrap;
    }}
    .brand-title {{ font-size: 22px; font-weight: 800; color: var(--brand); letter-spacing: -0.02em; }}
    .doc-num {{ font-size: 20px; font-weight: 800; margin-top: 2px; }}
    .badge {{
      display: inline-block;
      padding: 5px 12px;
      border-radius: 999px;
      font-size: 12.5px;
      font-weight: 700;
      margin-top: 6px;
    }}
    .badge-info {{ background: #dbeafe; color: #1e40af; }}
    .badge-warn {{ background: #fef3c7; color: #92400e; }}
    .badge-ok {{ background: #dcfce7; color: #166534; }}
    .badge-err {{ background: #fee2e2; color: #991b1b; }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
      gap: 14px;
      margin: 18px 0;
      padding: 14px 16px;
      background: #f8fafc;
      border: 1px solid var(--line);
      border-radius: 10px;
      font-size: 13.5px;
    }}
    .meta-label {{ font-size: 11px; text-transform: uppercase; color: var(--muted); font-weight: 700; }}
    .meta-val {{ font-weight: 600; margin-top: 2px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13.5px; }}
    th {{
      text-align: left;
      padding: 10px 8px;
      background: #f1f5f9;
      border-bottom: 2px solid #cbd5e1;
      font-size: 12px;
      text-transform: uppercase;
      color: var(--muted);
    }}
    td {{ padding: 10px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }}
    .num {{ text-align: right; white-space: nowrap; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }}
    .strong {{ font-weight: 700; }}
    .prod-name {{ font-weight: 600; }}
    .prod-meta {{ font-size: 12px; color: var(--muted); margin-top: 2px; }}
    .section-box {{
      margin-top: 16px;
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fcfdff;
      font-size: 13.5px;
    }}
    .section-title {{ font-weight: 700; margin-bottom: 10px; color: var(--brand); }}
    .desc-item {{ padding: 8px 0; border-top: 1px dashed var(--line); }}
    .desc-item:first-of-type {{ border-top: none; padding-top: 0; }}
    .desc-head {{ display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; }}
    .desc-val {{ font-weight: 700; color: var(--ok); }}
    .desc-cond {{ font-size: 12px; color: var(--muted); margin-top: 2px; }}
    .desc-alert {{ font-size: 12px; color: #b45309; margin-top: 4px; font-weight: 600; }}
    .pill-ok {{ background: #dcfce7; color: #166534; padding: 2px 7px; border-radius: 99px; font-size: 11px; }}
    .pill-warn {{ background: #fef3c7; color: #92400e; padding: 2px 7px; border-radius: 99px; font-size: 11px; }}
    .alert-box {{
      margin-top: 16px;
      padding: 12px 16px;
      border-radius: 10px;
      background: #fffbeb;
      border: 1px solid #fde68a;
      color: #92400e;
      font-size: 13px;
    }}
    .alert-box ul {{ margin: 6px 0 0 0; padding-left: 18px; }}
    .totals-wrap {{ display: flex; justify-content: flex-end; margin-top: 18px; }}
    .totals-card {{
      width: 100%;
      max-width: 360px;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 14px 16px;
      background: #f8fafc;
    }}
    .tot-row {{ display: flex; justify-content: space-between; padding: 5px 0; font-size: 13.5px; }}
    .tot-discount {{ color: var(--ok); font-weight: 600; }}
    .tot-grand {{
      border-top: 2px solid #cbd5e1;
      margin-top: 6px;
      padding-top: 10px;
      font-size: 17px;
      font-weight: 800;
      color: var(--brand);
    }}
    .tot-sub {{ font-size: 12px; color: var(--muted); }}
    .convert-card {{
      margin-top: 22px;
      padding: 16px 18px;
      border-radius: 12px;
      background: #eff6ff;
      border: 1px solid #bfdbfe;
    }}
    .convert-header {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }}
    .form-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
    .form-grid .full {{ grid-column: 1 / -1; }}
    label {{ display: block; font-size: 12px; font-weight: 600; color: var(--muted); margin-bottom: 4px; }}
    input {{ width: 100%; padding: 8px 10px; border: 1px solid #cbd5e1; border-radius: 8px; font-size: 13.5px; }}
    .btn {{
      cursor: pointer;
      border: none;
      border-radius: 8px;
      padding: 9px 15px;
      font-size: 13.5px;
      font-weight: 700;
    }}
    .btn-primary {{ background: #16a34a; color: #fff; }}
    .btn-primary:hover {{ background: #15803d; }}
    .btn-light {{ background: #e2e8f0; color: #1e293b; }}
    .toolbar {{ display: flex; gap: 8px; justify-content: flex-end; margin-bottom: 12px; }}
    .foot-note {{ margin-top: 20px; font-size: 11.5px; color: var(--muted); text-align: center; }}
    @media (max-width: 600px) {{
      .form-grid {{ grid-template-columns: 1fr; }}
      .sheet {{ padding: 18px 14px; }}
    }}
    @media print {{
      body {{ background: #fff; padding: 0; }}
      .sheet {{ box-shadow: none; max-width: 100%; padding: 8px; }}
      .no-print {{ display: none !important; }}
    }}
  </style>
</head>
<body>
  <div class="sheet">
    <div class="toolbar no-print">
      <button type="button" class="btn btn-light" onclick="window.print()">🖨️ Imprimir / Guardar PDF</button>
    </div>

    <div class="topbar">
      <div>
        <div class="brand-title">LUBRIKCA · GESTIÓN COMERCIAL</div>
        <div style="font-size:13px;color:#475569;margin-top:2px;">{_esc(titulo_tipo)}</div>
      </div>
      <div style="text-align:right;">
        <div class="doc-num" id="lbl-doc-num">{_esc(doc.numero)}</div>
        <div class="badge {badge_cls}" id="lbl-doc-badge">{_esc(badge_txt)}</div>
        <div style="font-size:12px;color:#64748b;margin-top:4px;">Emisión: {fecha_str}</div>
      </div>
    </div>

    <div class="meta-grid">
      <div>
        <div class="meta-label">Cliente / Prospecto</div>
        <div class="meta-val">{_esc(doc.cliente_nombre)}</div>
        <div style="font-size:12.5px;color:#475569;">RIF: {_esc(doc.rif or "Por completar")}</div>
      </div>
      <div>
        <div class="meta-label">Contacto y Dirección</div>
        <div class="meta-val">{_esc(doc.telefono or "—")} · {_esc(doc.email or "—")}</div>
        <div style="font-size:12.5px;color:#475569;">{_esc(doc.direccion or "—")}</div>
      </div>
      <div>
        <div class="meta-label">Lista Comercial, Ruta y Plazo</div>
        <div class="meta-val">{_esc(doc.lista_precios_nombre)} · {_esc(d.get("ruta_pago_label") or "Ruta BCV")}</div>
        <div style="font-size:12.5px;color:#475569;">Plazo: {"Contado (0 días)" if int(d.get("dias_credito") or 0) <= 0 else f"Crédito {int(d.get('dias_credito') or 0)} día(s)"} · Vendedor: {_esc(doc.vendedor_email or "Comercial")} · Volumen: {d["total_litros"]:.2f} L</div>
      </div>
    </div>

    <table>
      <thead>
        <tr>
          <th>Código</th>
          <th>Producto / Presentación</th>
          <th class="num">Cant.</th>
          <th class="num">Precio Lista</th>
          <th class="num">Subtotal</th>
        </tr>
      </thead>
      <tbody>
        {''.join(filas_html)}
      </tbody>
    </table>

    {descuentos_html}
    {alertas_banner}
    {retencion_banner}
    {notas_html}

    <div class="totals-wrap">
      <div class="totals-card">
        <div class="tot-row">
          <span>Subtotal Lista ({_esc(doc.lista_precios_nombre)}):</span>
          <strong>${d["subtotal"]:,.2f}</strong>
        </div>
        <div class="tot-row tot-discount">
          <span>Descuentos proyectados CxC:</span>
          <span>-${d["total_descuentos"]:,.2f}</span>
        </div>
        <div class="tot-row">
          <span>Base Imponible Neta:</span>
          <strong>${d["subtotal_neto"]:,.2f}</strong>
        </div>
        <div class="tot-row">
          <span>IVA (16%):</span>
          <span>${d["monto_iva"]:,.2f}</span>
        </div>
        <div class="tot-row tot-grand">
          <span>Total Estimado:</span>
          <span>${d["total_con_iva"]:,.2f}</span>
        </div>
        {fila_ves}
      </div>
    </div>

    {bloque_conversion}

    <div class="foot-note">
      Documento generado por CxC Lubrikca · Los descuentos comerciales están sujetos al cumplimiento
      de las condiciones de pago y validación del motor de CxC al momento de facturación.
    </div>
  </div>
</body>
</html>"""
