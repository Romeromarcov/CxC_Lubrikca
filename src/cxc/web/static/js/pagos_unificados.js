/* Submódulo Pagos -> Recibidos Unificados (Bot/CxC + Odoo + CxC-first + Recibos + Zelle Terceros) */
(function () {
    "use strict";

    let _recibidosCache = [];

    function _cxc() {
        return window.CxC || {};
    }

    function _esc(v) {
        if (typeof _cxc().esc === "function") return _cxc().esc(v);
        return String(v == null ? "" : v)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function _monto(v) {
        if (typeof _cxc().monto === "function") return _cxc().monto(v);
        const n = Number(v || 0);
        return Number.isFinite(n) ? n.toFixed(2) : "—";
    }

    function _usd(v) {
        if (v == null) return "—";
        const n = Number(v);
        return Number.isFinite(n) ? `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—";
    }

    function odooLinkHtml(d) {
        const num = d.odoo_numero || d.numero_pago_odoo || "";
        const oid = d.odoo_pago_id || (d.odoo_res_id != null ? String(d.odoo_res_id) : "");
        if (!oid && !num) return "";
        const etiqueta = num ? `${_esc(num)} (#${_esc(oid || num)})` : `Odoo #${_esc(oid)}`;
        if (d.odoo_url) {
            return `<div class="cap-sub"><a href="${_esc(d.odoo_url)}" target="_blank" rel="noopener" style="color:#0284c7; text-decoration:underline;">🔗 ${etiqueta} ↗</a></div>`;
        }
        return `<div class="cap-sub" style="color:#475569;">🔗 ${etiqueta}</div>`;
    }

    function equivalentesHtml(d) {
        const eq = d.equivalentes || {};
        const uBcv = d.monto_bcv_usd ?? eq.monto_bcv_usd;
        const uBin = d.monto_binance_usd ?? eq.monto_binance_usd;
        const uEur = d.monto_eur_usd ?? eq.monto_eur_usd;
        const tBcv = d.tasa_bcv ?? eq.tasa_bcv;
        const tBin = d.tasa_binance ?? eq.tasa_binance;
        const tEur = d.tasa_bcv_eur ?? eq.tasa_bcv_eur;
        return `
            <span style="font-size:0.74rem; color:#2563eb; display:block;" title="${tBcv != null ? "Tasa BCV: " + tBcv : ""}">BCV: <strong>${_usd(uBcv)}</strong></span>
            <span style="font-size:0.74rem; color:#d97706; display:block;" title="${tBin != null ? "Tasa Binance: " + tBin : ""}">Binance: <strong>${_usd(uBin)}</strong></span>
            <span style="font-size:0.74rem; color:#7c3aed; display:block;" title="${tEur != null ? "Tasa EUR: " + tEur : ""}">EUR: <strong>${_usd(uEur)}</strong></span>`;
    }

    function usuariosPagoHtml(d, direccion = "recibidos") {
        const reg = d.registrado_por_nombre || d.creado_por || "—";
        const apr = d.aprobado_o_recibido_por_nombre || d.aprobado_por || d.confirmado_cxc_por || "";
        const lbl1 = direccion === "enviados" ? "Generó" : "Registró";
        const lbl2 = direccion === "enviados" ? "Aprobó" : "Recibió/Aprobó";
        return `<div><strong>${lbl1}:</strong> ${_esc(reg)}</div>`
            + `<div class="cap-sub"><strong>${lbl2}:</strong> ${_esc(apr || "Pendiente")}</div>`;
    }

    function cerrarDetallePagoUnificado() {
        const modal = document.getElementById("modal-detalle-pago-unificado");
        if (modal) modal.style.display = "none";
    }

    function abrirDetallePagoUnificado(d, direccion = "recibidos") {
        const modal = document.getElementById("modal-detalle-pago-unificado");
        const tit = document.getElementById("modal-detalle-pago-unificado-titulo");
        const body = document.getElementById("modal-detalle-pago-unificado-body");
        if (!modal || !body || !d) return;

        const eq = d.equivalentes || {};
        const tBcv = d.tasa_bcv ?? eq.tasa_bcv;
        const tBin = d.tasa_binance ?? eq.tasa_binance;
        const tEur = d.tasa_bcv_eur ?? eq.tasa_bcv_eur;
        const uBcv = d.monto_bcv_usd ?? eq.monto_bcv_usd;
        const uBin = d.monto_binance_usd ?? eq.monto_binance_usd;
        const uEur = d.monto_eur_usd ?? eq.monto_eur_usd;

        const campo = (ico, lbl, val) =>
            `<div style="margin-bottom:0.45rem;"><span style="font-size:0.74rem; color:#64748b; display:block;">${ico} ${lbl}</span><strong>${val}</strong></div>`;
        const tarjeta = (ico, nom, col, tasa, equiv) => `
            <div style="background:#fff; border:1px solid #e2e8f0; border-radius:10px; padding:0.75rem 1rem; text-align:center;">
                <div style="font-size:0.75rem; color:#64748b; font-weight:600; margin-bottom:0.35rem;">${ico} ${nom}</div>
                <div style="font-size:1.05rem; font-weight:700; color:${col};">${tasa != null ? Number(tasa).toFixed(4) : "—"}</div>
                <div style="border-top:1px dashed #e2e8f0; margin:0.4rem 0;"></div>
                <div style="font-size:0.7rem; color:#94a3b8;">Equivalente USD</div>
                <div style="font-size:0.95rem; font-weight:700; color:#0f172a;">${_usd(equiv)}</div>
            </div>`;

        const numOdoo = d.odoo_numero || d.numero_pago_odoo || "";
        const oid = d.odoo_pago_id || (d.odoo_res_id != null ? String(d.odoo_res_id) : "");
        const odooVal = d.odoo_url
            ? `<a href="${_esc(d.odoo_url)}" target="_blank" rel="noopener" style="color:#0284c7;">${_esc(numOdoo || "Pago #" + oid)} (#${_esc(oid)}) ↗</a>`
            : _esc(numOdoo || (oid ? `#${oid}` : "—"));

        const montoOrig = d.monto != null
            ? (String(d.moneda || "").toUpperCase() === "VES"
                ? `Bs. ${_monto(d.monto)}`
                : `${_usd(d.monto)} (${_esc(d.moneda || "USD")})`)
            : "—";

        const lblContra = direccion === "enviados" ? "Proveedor" : "Cliente";
        const lblReg = direccion === "enviados" ? "Generó recibo" : "Registró / Vendedor";
        const lblApr = direccion === "enviados" ? "Aprobó" : "Aprobó / Recibió efectivo";

        if (tit) tit.textContent = `Detalle de Pago ${d.numero || ""}`;
        let html = `<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(175px, 1fr)); gap:0.5rem 1rem; margin-bottom:1rem;">
            ${campo("📄", "Documento / Nº", _esc(d.numero || "—"))}
            ${campo("🔗", "N° y ID en Odoo", odooVal)}
            ${campo("📅", "Fecha", _esc(d.fecha_documento || "—"))}
            ${campo("👤", lblContra, _esc(d.contraparte_nombre || "—"))}
            ${campo("✍️", lblReg, _esc(d.registrado_por_nombre || d.creado_por || "—"))}
            ${campo("✅", lblApr, _esc(d.aprobado_o_recibido_por_nombre || d.aprobado_por || d.confirmado_cxc_por || "Pendiente"))}
            ${campo("💰", "Monto Original", montoOrig)}
            ${campo("🏦", "Diario / Método", _esc(d.diario_nombre || d.banco || d.metodo_pago || "—"))}
            ${campo("🔖", "Referencia", _esc(d.referencia || "—"))}
            ${campo("🧾", "Comprobante Recibo", _esc(d.numero_recibo || "—"))}
            ${campo("📌", "Estado", _esc(d.estado || "—"))}
            ${campo("🌐", "Origen", _esc(d.origen_sistema === "odoo" ? "Odoo" : (d.origen || "CxC")))}
        </div>`;

        html += `<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:0.75rem; margin-bottom:1rem;">
            ${tarjeta("🏦", "Tasa BCV", "#0369a1", tBcv, uBcv)}
            ${tarjeta("🟡", "Tasa Binance", "#b45309", tBin, uBin)}
            ${tarjeta("💶", "Tasa BCV-EUR", "#7c3aed", tEur, uEur)}
        </div>`;

        if (d.notas) {
            html += `<div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:0.75rem; margin-bottom:0.75rem; font-size:0.86rem;"><strong>Notas:</strong> ${_esc(d.notas)}</div>`;
        }
        const enlaces = [];
        if (d.preview_path) enlaces.push(`<a href="${_esc(d.preview_path)}" target="_blank" rel="noopener" class="btn-secondary" style="text-decoration:none;">🔗 Abrir Preview</a>`);
        if (d.recibo_path) enlaces.push(`<a href="${_esc(d.recibo_path)}" target="_blank" rel="noopener" class="btn-primary" style="text-decoration:none;">🧾 Abrir Recibo</a>`);
        if (enlaces.length) {
            html += `<div style="display:flex; gap:0.5rem; flex-wrap:wrap; margin-top:0.5rem;">${enlaces.join("")}</div>`;
        }
        body.innerHTML = html;
        modal.style.display = "flex";
    }

    function origenChip(d) {
        const partes = [];
        if (d.origen_sistema === "odoo") {
            partes.push('<span class="badge-origen-odoo">🟣 Odoo</span>');
        } else if (d.origen === "bot") {
            partes.push('<span class="badge-origen-cxc">📲 Bot → CxC</span>');
        } else {
            partes.push('<span class="badge-origen-cxc">💻 CxC</span>');
        }
        if (d.es_zelle_tercero) {
            const alias = d.cuenta_tercero_alias ? `: ${_esc(d.cuenta_tercero_alias)}` : "";
            partes.push(`<div><span class="badge-zelle3">💵 Zelle 3ro${alias}</span></div>`);
        }
        if (d.numero_recibo) {
            partes.push(`<div class="cap-sub">🧾 ${_esc(d.numero_recibo)}</div>`);
        }
        return partes.join("");
    }

    function estadoBadgeUnificado(d) {
        if (d.estado === "CONFIRMADO_ODOO") {
            return '<span class="semaphore verde">🟣 Confirmado Odoo</span>';
        }
        if (d.estado === "CONFIRMADO_CXC") {
            return '<span class="semaphore verde badge-confirmado-cxc">✔️ Confirmado CxC</span>';
        }
        if (typeof _cxc().estadoBadge === "function") {
            return _cxc().estadoBadge(d);
        }
        return `<span class="state-badge">${_esc(d.estado)}</span>`;
    }

    function alertasHtml(d) {
        if (typeof _cxc().alertasHtml === "function") {
            return _cxc().alertasHtml(d);
        }
        return "";
    }

    function accionesHtml(d, puedeAprobar, puedeRecibo, idx = -1, dir = "recibidos") {
        const btns = [
            `<button type="button" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem;" data-unif-detalle="${idx}" data-unif-dir="${dir}">👁️ Detalle</button>`,
        ];
        if (d.origen_sistema === "odoo") {
            return btns.join("") + `<span class="cap-sub">${_esc(d.odoo_numero || d.odoo_ref || "Odoo")}</span>`;
        }
        if (d.preview_path) {
            btns.push(`<a href="${_esc(d.preview_path)}" target="_blank" rel="noopener" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem; text-decoration:none;">🔗 Preview</a>`);
        }
        if (d.recibo_path && d.numero_recibo) {
            btns.push(`<a href="${_esc(d.recibo_path)}" target="_blank" rel="noopener" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem; text-decoration:none; background:#eff6ff; color:#1d4ed8;">🧾 Recibo</a>`);
        }
        btns.push(`<button type="button" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem;" onclick="editarOperativoPorId(${d.id})">${d.puede_editar ? "✏️ Editar" : "👁 Ver"}</button>`);
        if (d.puede_confirmar_cxc && (puedeAprobar || puedeRecibo)) {
            btns.push(`<button type="button" class="btn-primary" style="padding:0.32rem 0.55rem; font-size:0.78rem; background:#0284c7;" onclick="confirmarPagoCxc(${d.id}, false)" title="Confirmar primero en CxC y emitir recibo numerado">✔️ Confirmar CxC</button>`);
        }
        if (d.puede_aprobar && puedeAprobar) {
            const lbl = d.es_zelle_tercero ? "📒 Asiento Odoo" : "✅ Enviar a Odoo";
            btns.push(`<button type="button" class="btn-primary" style="padding:0.32rem 0.6rem; font-size:0.78rem; background:#16a34a;" onclick="aprobarOperativo(${d.id})">${lbl}</button>`);
        }
        if (d.puede_rechazar && puedeAprobar) {
            btns.push(`<button type="button" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem; color:#991b1b;" onclick="rechazarOperativo(${d.id})">✖</button>`);
        }
        return btns.join("");
    }

    async function loadPagosRecibidosUnificados() {
        const tbody = document.getElementById("op-pagos-table-body");
        if (!tbody) return;
        const origen = (document.getElementById("op-cob-origen") || {}).value || "todos";
        const estado = (document.getElementById("op-cob-estado") || {}).value || "";
        const q = ((document.getElementById("op-cob-buscar") || {}).value || "").trim();
        const qs = new URLSearchParams({ direccion: "recibidos", origen, limit: "150" });
        if (estado) qs.set("estado", estado);
        if (q) qs.set("q", q);

        try {
            const res = await fetch(`/api/pagos/unificados?${qs}`);
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);

            const puedeAprobar = Boolean(data.puede_aprobar_pagos);
            const puedeRecibo = Boolean(data.puede_recibo);
            if (typeof _cxc().setPuedeAprobarPagos === "function") {
                _cxc().setPuedeAprobarPagos(puedeAprobar);
            }
            const r = data.resumen || {};
            const badge = document.getElementById("badge-op-pagos-pend");
            if (badge) badge.textContent = String((r.pendientes_aprobacion || 0) + (r.confirmados_cxc || 0));

            for (const [idEl, val] of [
                ["op-cob-n-PENDIENTE_APROBACION", r.pendientes_aprobacion || 0],
                ["op-cob-n-CONFIRMADO_CXC", r.confirmados_cxc || 0],
                ["op-cob-n-INCOMPLETO", r.incompletos || 0],
                ["op-cob-n-ENVIADO_ODOO", (r.enviados_odoo || 0) + (r.confirmados_odoo || 0)],
                ["op-cob-n-ZELLE_TERCERO", r.zelle_terceros || 0],
                ["op-cob-n-RECHAZADO", r.rechazados || 0],
            ]) {
                const el = document.getElementById(idEl);
                if (el) el.textContent = String(val);
            }

            const resEl = document.getElementById("op-cob-resumen");
            if (resEl) {
                resEl.textContent = `${data.total || 0} pago(s) mostrado(s) · Origen: ${r.origen_cxc || 0} CxC · ${r.origen_odoo || 0} Odoo · ${r.zelle_terceros || 0} Zelle 3ros`;
            }

            const items = data.items || [];
            _recibidosCache = items;
            if (!items.length) {
                tbody.innerHTML = '<tr><td colspan="9" class="table-empty">No hay pagos recibidos con estos filtros.</td></tr>';
                return;
            }

            tbody.innerHTML = items.map((d, idx) => {
                const montoOrig = d.monto != null
                    ? (String(d.moneda || "").toUpperCase() === "VES"
                        ? `Bs. ${_monto(d.monto)}`
                        : `${_usd(d.monto)} <small>${_esc(d.moneda || "USD")}</small>`)
                    : "—";
                const refTxt = d.es_efectivo
                    ? '<span style="color:#0369a1; font-weight:600;">Efectivo (memo vacío)</span>'
                    : _esc(d.referencia || "—");
                const diarioTxt = d.es_zelle_tercero
                    ? `<span style="color:#7c3aed; font-weight:600;">Asiento Tercero (CxP/CxC)</span><div class="cap-sub">${_esc(d.beneficiario_tercero || d.proveedor_tercero_nombre || "Sin banco")}</div>`
                    : _esc(d.diario_nombre || d.banco || "Por definir");
                return `<tr>
                    <td><strong>${_esc(d.numero)}</strong>${odooLinkHtml(d)}<div>${origenChip(d)}</div></td>
                    <td>${_esc(d.fecha_documento || "—")}</td>
                    <td><strong>${_esc(d.contraparte_nombre || "Sin cliente")}</strong><div class="cap-sub">${_esc(d.rif || (d.partner_odoo_id ? ("Odoo #" + d.partner_odoo_id) : "Sin vincular"))}${d.pedido_ref ? " · Ped: " + _esc(d.pedido_ref) : ""}</div></td>
                    <td>${usuariosPagoHtml(d, "recibidos")}</td>
                    <td><strong>${montoOrig}</strong></td>
                    <td>${equivalentesHtml(d)}</td>
                    <td>${diarioTxt}<div class="cap-sub">${refTxt}${d.metodo_pago ? " · " + _esc(d.metodo_pago) : ""}</div></td>
                    <td>${estadoBadgeUnificado(d)}${alertasHtml(d)}</td>
                    <td style="white-space:nowrap; display:flex; gap:4px; flex-wrap:wrap;">${accionesHtml(d, puedeAprobar, puedeRecibo, idx, "recibidos")}</td>
                </tr>`;
            }).join("");
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="9" class="table-empty">❌ ${_esc(err.message)}</td></tr>`;
        }
    }

    async function confirmarPagoCxc(id, enviarOdoo = false) {
        const msg = enviarOdoo
            ? "¿Confirmar en CxC (generar recibo) y enviar borrador a Odoo?"
            : "¿Confirmar este pago en CxC y generar su recibo oficial numerado?";
        if (!confirm(msg)) return;
        try {
            const res = await fetch(`/api/operativos/${encodeURIComponent(id)}/confirmar-cxc`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ enviar_a_odoo: Boolean(enviarOdoo) }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || data.detail || `Error ${res.status}`);
            alert(`✔️ ${data.numero} confirmado en CxC.\nRecibo generado: ${data.numero_recibo || "OK"}`);
            if (typeof _cxc().refrescarOperativos === "function") {
                await _cxc().refrescarOperativos();
            } else {
                await loadPagosRecibidosUnificados();
            }
        } catch (err) {
            alert("⚠️ " + err.message);
        }
    }

    async function confirmarOperativoModal(enviarOdoo = false) {
        const id = (document.getElementById("op-f-id") || {}).value;
        if (!id) return;
        const st = document.getElementById("op-modal-status");
        if (st) {
            st.style.color = "#0369a1";
            st.textContent = "Confirmando en CxC y generando recibo...";
        }
        try {
            const res = await fetch(`/api/operativos/${encodeURIComponent(id)}/confirmar-cxc`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ enviar_a_odoo: Boolean(enviarOdoo) }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || data.detail || `Error ${res.status}`);
            if (typeof _cxc().llenarModalOperativo === "function") {
                _cxc().llenarModalOperativo(data);
            }
            if (st) {
                st.style.color = "#15803d";
                st.textContent = `✔️ Confirmado en CxC · Recibo: ${data.numero_recibo || ""}`;
            }
            if (typeof _cxc().refrescarOperativos === "function") {
                await _cxc().refrescarOperativos();
            }
        } catch (err) {
            if (st) {
                st.style.color = "#b91c1c";
                st.textContent = "⚠️ " + err.message;
            }
        }
    }

    function syncZelleTerceroModalUI() {
        const tipo = (document.getElementById("op-f-tipo") || {}).value || "pago";
        const wrapZt = document.getElementById("op-wrap-zelle-tercero");
        const chkZt = document.getElementById("op-f-es-zelle-tercero");
        const selZt = document.getElementById("op-f-cuenta-tercero");
        const subZt = document.getElementById("op-sub-zelle-tercero");
        const selDiario = document.getElementById("op-f-diario");
        const inpBanco = document.getElementById("op-f-banco");
        if (!wrapZt || !chkZt) return;

        const esPagoCliente = tipo === "pago";
        wrapZt.style.display = esPagoCliente ? "flex" : "none";
        if (!esPagoCliente) {
            chkZt.checked = false;
        }
        const activo = esPagoCliente && chkZt.checked;
        if (subZt) subZt.style.display = activo ? "grid" : "none";
        if (selZt) selZt.required = activo;
        if (selDiario) {
            selDiario.disabled = activo;
            if (activo) selDiario.value = "";
        }
        if (inpBanco) {
            inpBanco.disabled = activo;
            if (activo) inpBanco.value = "";
        }
    }

    async function abrirModalNuevoZelleTercero() {
        if (typeof _cxc().abrirModalNuevoOperativoCustom === "function") {
            await _cxc().abrirModalNuevoOperativoCustom({
                tipo: "pago",
                moneda: "USD",
                metodo_pago: "zelle",
                es_zelle_tercero: true,
            });
            syncZelleTerceroModalUI();
        }
    }

    window.CxC = Object.assign(window.CxC || {}, {
        pagos: {
            origenChip,
            odooLinkHtml,
            equivalentesHtml,
            usuariosPagoHtml,
            estadoBadge: estadoBadgeUnificado,
            accionesHtml,
            syncZelleTerceroModalUI,
            abrirDetallePagoUnificado,
            cerrarDetallePagoUnificado,
        },
    });
    window.loadPagosRecibidosUnificados = loadPagosRecibidosUnificados;
    window.loadOperativosCobranza = loadPagosRecibidosUnificados;
    window.confirmarPagoCxc = confirmarPagoCxc;
    window.confirmarOperativoModal = confirmarOperativoModal;
    window.abrirModalNuevoZelleTercero = abrirModalNuevoZelleTercero;

    document.addEventListener("DOMContentLoaded", () => {
        document.getElementById("op-cob-origen")?.addEventListener("change", loadPagosRecibidosUnificados);
        document.getElementById("op-f-es-zelle-tercero")?.addEventListener("change", () => {
            const chk = document.getElementById("op-f-es-zelle-tercero");
            if (chk && chk.checked) {
                const mon = document.getElementById("op-f-moneda");
                const met = document.getElementById("op-f-metodo");
                if (mon) mon.value = "USD";
                if (met && !met.value) met.value = "zelle";
            }
            syncZelleTerceroModalUI();
            if (typeof _cxc().programarValidacionOperativo === "function") {
                _cxc().programarValidacionOperativo();
            }
        });
        document.getElementById("op-f-cuenta-tercero")?.addEventListener("change", () => {
            const sel = document.getElementById("op-f-cuenta-tercero");
            const opt = sel && sel.selectedOptions ? sel.selectedOptions[0] : null;
            const benef = document.getElementById("op-f-beneficiario-tercero");
            if (opt && opt.dataset && opt.dataset.titular && benef && !benef.value.trim()) {
                benef.value = opt.dataset.titular;
            }
            if (typeof _cxc().programarValidacionOperativo === "function") {
                _cxc().programarValidacionOperativo();
            }
        });
        document.getElementById("op-f-beneficiario-tercero")?.addEventListener("input", () => {
            if (typeof _cxc().programarValidacionOperativo === "function") {
                _cxc().programarValidacionOperativo();
            }
        });
        document.getElementById("op-f-tipo")?.addEventListener("change", syncZelleTerceroModalUI);

        document.querySelectorAll(".op-cob-kpi").forEach((k) => {
            k.addEventListener("click", () => {
                const estSel = document.getElementById("op-cob-estado");
                const oriSel = document.getElementById("op-cob-origen");
                if (k.dataset.opOrigen && oriSel) {
                    oriSel.value = k.dataset.opOrigen;
                    if (estSel) estSel.value = "";
                } else if (estSel) {
                    estSel.value = k.dataset.opEstado || "";
                    if (oriSel && oriSel.value === "zelle_tercero") oriSel.value = "todos";
                }
                loadPagosRecibidosUnificados();
            });
        });

        document.getElementById("op-pagos-table-body")?.addEventListener("click", (e) => {
            const btn = e.target.closest("[data-unif-detalle][data-unif-dir='recibidos']");
            if (!btn) return;
            const d = _recibidosCache[Number(btn.getAttribute("data-unif-detalle"))];
            if (d) abrirDetallePagoUnificado(d, "recibidos");
        });

        const pathSeg = window.location.pathname.toLowerCase().replace(/^\/+|\/+$/g, "").split("/")[0];
        if (pathSeg === "pagos" || pathSeg === "cobranza") {
            loadPagosRecibidosUnificados();
        }
    });
})();
