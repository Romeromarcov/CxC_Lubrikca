/* Ventas Unificadas (CxC + Odoo) y Política de Auto-Envío / Límites de Crédito */
(function () {
    "use strict";

    let _pvPuedeEditar = false;
    let _pvOverrides = [];
    let _pvCargado = false;

    function _esc(v) {
        if (window.CxC && typeof window.CxC.esc === "function") return window.CxC.esc(v);
        return String(v == null ? "" : v)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function _monto(v) {
        const n = Number(v) || 0;
        return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function _fechaCorta(iso) {
        if (!iso) return "—";
        const s = String(iso);
        if (s.endsWith("T00:00:00")) return s.slice(0, 10);
        return s.slice(0, 16).replace("T", " ");
    }

    function _alertaHtml(tipo, texto) {
        const ok = tipo === "ok";
        const warn = tipo === "warn";
        const borde = ok ? "#10b981" : (warn ? "#f59e0b" : "#e11d48");
        const bg = ok ? "#ecfdf5" : (warn ? "#fffbeb" : "#fff1f2");
        const color = ok ? "#065f46" : (warn ? "#92400e" : "#9f1239");
        const ico = ok ? "✅" : "⚠️";
        return `<div style="border-left:4px solid ${borde}; background:${bg}; color:${color}; padding:0.6rem 0.9rem; border-radius:6px; font-size:0.88rem; margin-bottom:0.35rem;">${ico} ${_esc(texto)}</div>`;
    }

    async function loadVentasUnificadas() {
        const tbody = document.getElementById("docs-table-body");
        if (!tbody) return;
        const origen = (document.getElementById("docs-filtro-origen") || {}).value || "todos";
        const tipo = (document.getElementById("docs-filtro-tipo") || {}).value || "";
        const estado = (document.getElementById("docs-filtro-estado") || {}).value || "";
        const q = ((document.getElementById("docs-filtro-q") || {}).value || "").trim();

        const qs = new URLSearchParams({ origen, limit: "200" });
        if (tipo) qs.set("tipo", tipo);
        if (estado) qs.set("estado", estado);
        if (q) qs.set("q", q);

        tbody.innerHTML = `<tr><td colspan="10" class="table-empty">Cargando ventas unificadas CxC + Odoo...</td></tr>`;
        try {
            const res = await fetch(`/api/ventas/unificadas?${qs}`);
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);

            if (typeof data.puede_aprobar === "boolean") {
                window._cxcPuedeAprobarPedidos = data.puede_aprobar;
            }
            const r = data.resumen || {};
            const setTxt = (id, val) => {
                const el = document.getElementById(id);
                if (el) el.textContent = String(val ?? 0);
            };
            setTxt("docs-kpi-total", r.total || 0);
            setTxt("docs-kpi-cxc", r.origen_cxc || 0);
            setTxt("docs-kpi-odoo", r.origen_odoo || 0);
            setTxt("docs-kpi-autosend", r.auto_enviados ?? r.pedidos_auto_enviados ?? 0);
            setTxt("docs-kpi-cot", r.cotizaciones_borrador || 0);
            setTxt("docs-kpi-ped", r.pendientes_aprobacion ?? r.pedidos_pendientes_aprobacion ?? 0);
            setTxt("docs-kpi-enviados", r.total_confirmados ?? r.enviados_odoo ?? 0);
            setTxt("docs-kpi-subtotal", `$${_monto(r.subtotal_confirmado_usd || 0)}`);
            setTxt("docs-kpi-total-iva", `$${_monto(r.total_con_iva_confirmado_usd || 0)}`);

            _renderTablaUnificada(data.items || [], Boolean(data.puede_aprobar));
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="10" class="table-empty">${_esc(err.message)}</td></tr>`;
        }
    }

    async function sincronizarYRecargarVentas() {
        const aviso = document.getElementById("docs-aviso-general");
        try {
            const res = await fetch("/api/ventas/sincronizar-odoo", { method: "POST" });
            if (res.ok) {
                const st = await res.json().catch(() => ({}));
                if (aviso && (st.ajustados || st.confirmados || st.cancelados)) {
                    aviso.innerHTML = _alertaHtml(
                        "ok",
                        `Sincronización Odoo → CxC: ${st.ajustados || 0} ajustado(s), ${st.confirmados || 0} confirmado(s), ${st.cancelados || 0} cancelado(s).`,
                    );
                }
            }
        } catch (_e) { /* continuar recarga */ }
        await loadVentasUnificadas();
    }

    function _renderTablaUnificada(items, puedeAprobar) {
        const tbody = document.getElementById("docs-table-body");
        if (!tbody) return;
        if (!items.length) {
            tbody.innerHTML = `<tr><td colspan="10" class="table-empty">No hay cotizaciones ni pedidos con los filtros actuales.</td></tr>`;
            return;
        }
        tbody.innerHTML = items.map((d) => {
            const esOdooNativo = d.origen_sistema === "odoo";
            const bloqueadoOdoo = Boolean(d.solo_lectura || d.confirmado_odoo || d.estado === "ENVIADO_ODOO");
            const origenBadge = esOdooNativo
                ? `<span style="display:inline-block; background:#f3e8ff; color:#6b21a8; border:1px solid #d8b4fe; padding:1px 6px; border-radius:99px; font-size:0.72rem; font-weight:700;">🟣 Odoo</span>`
                : (d.origen === "bot"
                    ? `<span style="display:inline-block; background:#dbeafe; color:#1e40af; border:1px solid #bfdbfe; padding:1px 6px; border-radius:99px; font-size:0.72rem; font-weight:700;">📲 CxC Bot</span>`
                    : `<span style="display:inline-block; background:#e0f2fe; color:#0369a1; border:1px solid #bae6fd; padding:1px 6px; border-radius:99px; font-size:0.72rem; font-weight:700;">🏢 CxC Web</span>`);
            const syncChip = d.ajustado_desde_odoo
                ? `<div style="margin-top:2px;"><span style="font-size:0.7rem; color:#0284c7; font-weight:600;">🔄 Ajustado de Odoo</span></div>`
                : "";

            const badgeTipo = d.tipo === "cotizacion"
                ? `<span class="state-badge abierta">📄 Cotización</span>`
                : `<span class="state-badge cierre">🛒 Pedido</span>`;

            const rolBadge = d.tipo_contacto === "cliente"
                ? `<span style="font-size:0.74rem; color:#047857; font-weight:600;">🟢 Cliente</span>`
                : `<span style="font-size:0.74rem; color:#b45309; font-weight:600;">🟡 Prospecto</span>`;

            let estadoHtml = `<span class="semaphore amarillo">📋 Cotización vigente</span>`;
            if (d.estado === "PENDIENTE_APROBACION") {
                estadoHtml = `<span class="semaphore amarillo">⏳ Retenido por aprobación</span>`;
            } else if (d.estado === "ENVIADO_ODOO" || d.confirmado_odoo) {
                const refO = _esc(d.odoo_order_name || (d.odoo_order_id ? "#" + d.odoo_order_id : "Odoo"));
                const lockIco = bloqueadoOdoo ? "🔒" : "✅";
                estadoHtml = d.auto_enviado
                    ? `<span class="semaphore verde">⚡ Auto-enviado: ${refO}</span>`
                    : `<span class="semaphore verde">${lockIco} Confirmado Odoo: ${refO}</span>`;
            } else if (d.estado === "RECHAZADO") {
                estadoHtml = `<span class="semaphore rojo">${esOdooNativo ? "❌ Cancelada en Odoo" : "❌ Rechazado"}</span>`;
            } else if (d.estado === "ERROR") {
                estadoHtml = `<span class="semaphore rojo" title="${_esc(d.error_odoo)}">⚠️ Error Odoo</span>`;
            }

            const motivos = Array.isArray(d.motivos_retencion) ? d.motivos_retencion : [];
            const motivosHtml = (d.estado === "PENDIENTE_APROBACION" && motivos.length)
                ? `<div title="${_esc(motivos.join(" | "))}" style="font-size:0.73rem; color:#92400e; background:#fef3c7; border-radius:4px; padding:2px 6px; margin-top:3px; max-width:250px;">⚠️ ${_esc(motivos[0])}${motivos.length > 1 ? ` (+${motivos.length - 1})` : ""}</div>`
                : "";

            const avisoSinCat = d.lineas_sin_catalogo > 0
                ? `<div style="font-size:0.73rem; color:#dc2626;">⚠️ ${d.lineas_sin_catalogo} línea(s) sin SKU</div>`
                : "";
            const nAlertas = (d.alertas_motor || []).length;
            const avisoMotor = nAlertas > 0
                ? `<div style="font-size:0.73rem; color:#b45309; font-weight:600;">⚠️ ${nAlertas} alerta(s) motor</div>`
                : "";
            const descSub = Number(d.total_descuentos || 0) > 0
                ? `<div class="cap-sub" style="color:#15803d;">Desc. CxC: -$${_monto(d.total_descuentos)}</div>`
                : "";
            const ivaSub = Number(d.monto_iva || 0) > 0
                ? `<div class="cap-sub">IVA: $${_monto(d.monto_iva)}</div>`
                : "";

            let accionesHtml = "";
            if (esOdooNativo) {
                const soRef = _esc(d.odoo_order_name || d.numero);
                accionesHtml = typeof window.abrirModalDetalleOrden === "function"
                    ? `<button type="button" class="btn-secondary" style="padding:0.32rem 0.6rem; font-size:0.78rem;" onclick="abrirModalDetalleOrden('${soRef}')">👁 Ver SO</button>`
                    : `<span class="cap-sub">🔒 Solo lectura</span>`;
            } else {
                const editable = !bloqueadoOdoo;
                const btnPreview = d.preview_path
                    ? `<a href="${_esc(d.preview_path)}" target="_blank" rel="noopener" class="btn-secondary" style="padding:0.32rem 0.55rem; font-size:0.78rem; text-decoration:none;">🔗 Preview</a>`
                    : "";
                const btnConv = (d.tipo === "cotizacion" && editable && d.estado !== "RECHAZADO")
                    ? `<button type="button" class="btn-secondary" style="padding:0.32rem 0.55rem; font-size:0.78rem; background:#dcfce7; color:#166534;" onclick="convertirDocumentoAPedido(${d.id})">🛒 A Pedido</button>`
                    : "";
                const btnAprobar = (d.estado === "PENDIENTE_APROBACION" && puedeAprobar && editable)
                    ? `<button type="button" class="btn-primary" style="padding:0.35rem 0.65rem; font-size:0.8rem; background:#16a34a;" onclick="aprobarPedidoEnCxC(${d.id})">✅ Autorizar</button>
                       <button type="button" class="btn-secondary" style="padding:0.32rem 0.55rem; font-size:0.78rem; color:#991b1b;" onclick="rechazarPedidoEnCxC(${d.id})">✖</button>`
                    : "";
                accionesHtml = `
                    ${btnPreview}
                    <button type="button" class="btn-secondary" style="padding:0.32rem 0.55rem; font-size:0.78rem;" onclick="editarDocumentoPorId(${d.id})">${editable ? "✏️ Editar" : "🔒 Ver"}</button>
                    ${btnConv}
                    ${btnAprobar}
                `;
            }

            const lineasCell = esOdooNativo
                ? `<span class="cap-sub">${d.facturada ? "📄 Facturada" : "Por facturar"}${d.entregada_completa ? " · 📦 Entregada" : ""}</span>`
                : `${d.n_lineas} ítem(s)<div class="cap-sub">${Number(d.total_litros || 0).toFixed(2)} L</div>${avisoSinCat}`;

            return `<tr>
                <td><strong>${_esc(d.numero)}</strong><div style="margin-top:2px;">${origenBadge}</div>${syncChip}</td>
                <td>${badgeTipo}</td>
                <td><strong>${_esc(d.cliente_nombre)}</strong><div class="cap-sub">${rolBadge}${d.rif ? " · " + _esc(d.rif) : ""}</div></td>
                <td><strong>${_esc(d.lista_precios_nombre || d.moneda_lista || "—")}</strong></td>
                <td>${lineasCell}</td>
                <td><strong>$${_monto(d.subtotal || 0)}</strong>${descSub}</td>
                <td><strong>$${_monto(d.total_con_iva || d.subtotal || 0)}</strong>${ivaSub}${avisoMotor}</td>
                <td>${estadoHtml}${motivosHtml}${d.aprobado_por ? `<div class="cap-sub">Por: ${_esc(d.aprobado_por)}</div>` : ""}${d.error_odoo ? `<div class="cap-sub" style="color:#dc2626;">${_esc(d.error_odoo)}</div>` : ""}</td>
                <td style="font-size:0.8rem;">${_fechaCorta(d.actualizado_en)}</td>
                <td style="white-space:nowrap; display:flex; gap:4px; flex-wrap:wrap; align-items:center;">${accionesHtml}</td>
            </tr>`;
        }).join("");
    }

    function _renderOverridesPolitica() {
        const tbody = document.getElementById("pv-overrides-tbody");
        if (!tbody) return;
        if (!_pvOverrides.length) {
            tbody.innerHTML = `<tr><td colspan="6" class="table-empty">Sin excepciones por cliente configuradas (todos usan los límites globales).</td></tr>`;
            return;
        }
        tbody.innerHTML = _pvOverrides.map((ov) => {
            const cred = ov.limite_credito_usd != null ? `$${_monto(ov.limite_credito_usd)}` : "Global";
            const facs = ov.limite_facturas_pendientes != null ? `${ov.limite_facturas_pendientes}` : "Global";
            const mora = ov.dias_mora_max != null ? `${ov.dias_mora_max}d` : "Global";
            const bloq = ov.bloquear_auto_envio
                ? `<span class="semaphore rojo">🔒 Revisión manual</span>`
                : `<span class="semaphore verde">⚡ Auto-envío permitido</span>`;
            const btns = _pvPuedeEditar
                ? `<button type="button" class="btn-secondary" style="padding:2px 8px; font-size:0.76rem;" data-pv-edit="${ov.partner_odoo_id}">✏️</button>
                   <button type="button" class="btn-secondary" style="padding:2px 8px; font-size:0.76rem; color:#991b1b;" data-pv-del="${ov.partner_odoo_id}">🗑</button>`
                : "—";
            return `<tr>
                <td><strong>${_esc(ov.cliente_nombre || ("Cliente #" + ov.partner_odoo_id))}</strong><div class="cap-sub">ID #${ov.partner_odoo_id}</div></td>
                <td>${cred}</td>
                <td>${facs}</td>
                <td>${mora}</td>
                <td>${bloq}${ov.notas ? `<div class="cap-sub">${_esc(ov.notas)}</div>` : ""}</td>
                <td style="white-space:nowrap;">${btns}</td>
            </tr>`;
        }).join("");
    }

    async function loadPoliticaVentasConfig() {
        const card = document.getElementById("politica-ventas-card");
        if (!card) return;
        _pvCargado = true;
        try {
            const res = await fetch("/api/config/politica-ventas");
            if (!res.ok) return;
            const data = await res.json();
            const p = data.politica || {};
            _pvPuedeEditar = Boolean(data.puede_editar);
            _pvOverrides = data.overrides || [];

            const setChk = (id, v) => { const el = document.getElementById(id); if (el) el.checked = Boolean(v); };
            const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = String(v ?? 0); };
            setChk("pv-autosend-activo", p.autosend_activo !== false);
            setVal("pv-limite-credito", p.limite_credito_usd ?? 0);
            setVal("pv-limite-facturas", p.limite_facturas_pendientes ?? 0);
            setChk("pv-retener-vencidas", p.retener_si_vencidas !== false);
            setVal("pv-dias-mora-max", p.dias_mora_max ?? 0);

            const aviso = document.getElementById("pv-solo-lectura");
            if (aviso) aviso.style.display = _pvPuedeEditar ? "none" : "block";
            card.querySelectorAll("input, button[type='submit']").forEach((el) => {
                el.disabled = !_pvPuedeEditar;
            });
            _renderOverridesPolitica();
        } catch (_e) { /* silencioso */ }
    }

    document.addEventListener("DOMContentLoaded", () => {
        // Reemplaza la recarga simple por la vista consolidada CxC + Odoo
        window.loadDocumentosComerciales = loadVentasUnificadas;
        window.loadVentasUnificadas = loadVentasUnificadas;
        window.sincronizarYRecargarVentas = sincronizarYRecargarVentas;
        window.loadPoliticaVentasConfig = loadPoliticaVentasConfig;

        const pathSegV = window.location.pathname.toLowerCase().replace(/^\/+|\/+$/g, "").split("/")[0];
        if (pathSegV === "ventas") {
            loadVentasUnificadas();
        }

        const selOrig = document.getElementById("docs-filtro-origen");
        if (selOrig) selOrig.addEventListener("change", loadVentasUnificadas);

        const pvForm = document.getElementById("politica-ventas-form");
        if (pvForm) {
            pvForm.addEventListener("submit", async (e) => {
                e.preventDefault();
                const st = document.getElementById("pv-status");
                if (st) { st.style.color = "#475569"; st.textContent = "Guardando..."; }
                try {
                    const payload = {
                        autosend_activo: Boolean(document.getElementById("pv-autosend-activo")?.checked),
                        limite_credito_usd: Number(document.getElementById("pv-limite-credito")?.value || 0),
                        limite_facturas_pendientes: Number(document.getElementById("pv-limite-facturas")?.value || 0),
                        retener_si_vencidas: Boolean(document.getElementById("pv-retener-vencidas")?.checked),
                        dias_mora_max: Number(document.getElementById("pv-dias-mora-max")?.value || 0),
                    };
                    const res = await fetch("/api/config/politica-ventas", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(payload),
                    });
                    const d = await res.json().catch(() => ({}));
                    if (!res.ok) throw new Error(d.error || d.detail || `Error ${res.status}`);
                    if (st) { st.style.color = "#15803d"; st.textContent = "✅ Política guardada."; }
                    await loadPoliticaVentasConfig();
                } catch (err) {
                    if (st) { st.style.color = "#dc2626"; st.textContent = `⚠️ ${err.message}`; }
                }
            });
        }

        // Autocompletado de clientes para override de crédito
        const inpBuscar = document.getElementById("pv-ov-buscar");
        const boxRes = document.getElementById("pv-ov-resultados");
        let tOv = null;
        if (inpBuscar && boxRes) {
            inpBuscar.addEventListener("input", () => {
                clearTimeout(tOv);
                const q = inpBuscar.value.trim();
                if (q.length < 2) { boxRes.style.display = "none"; return; }
                tOv = setTimeout(async () => {
                    try {
                        const r = await fetch(`/api/odoo/contactos?q=${encodeURIComponent(q)}&limit=8`);
                        const data = await r.json();
                        const items = (data.items || []).filter((c) => c.clasificacion !== "proveedor");
                        if (!items.length) {
                            boxRes.innerHTML = `<div style="padding:0.5rem 0.75rem; color:#64748b; font-size:0.8rem;">Sin clientes encontrados.</div>`;
                        } else {
                            boxRes.innerHTML = items.map((c, idx) =>
                                `<div data-pv-pick="${idx}" style="padding:0.45rem 0.75rem; cursor:pointer; border-bottom:1px solid #f1f5f9; font-size:0.82rem;"><strong>${_esc(c.nombre)}</strong> <span style="color:#64748b;">#${c.id}${c.rif ? " · " + _esc(c.rif) : ""}</span></div>`
                            ).join("");
                            boxRes._items = items;
                        }
                        boxRes.style.display = "block";
                    } catch (_e) { boxRes.style.display = "none"; }
                }, 220);
            });
            boxRes.addEventListener("click", (e) => {
                const row = e.target.closest("[data-pv-pick]");
                if (!row || !boxRes._items) return;
                const c = boxRes._items[Number(row.dataset.pvPick)];
                if (!c) return;
                document.getElementById("pv-ov-partner-id").value = String(c.id);
                document.getElementById("pv-ov-cliente-nombre").value = c.nombre || "";
                inpBuscar.value = `${c.nombre} (#${c.id})`;
                boxRes.style.display = "none";
            });
            document.addEventListener("click", (e) => {
                if (!e.target.closest("#pv-ov-buscar") && !e.target.closest("#pv-ov-resultados")) {
                    boxRes.style.display = "none";
                }
            });
        }

        const ovForm = document.getElementById("pv-override-form");
        if (ovForm) {
            ovForm.addEventListener("submit", async (e) => {
                e.preventDefault();
                const pid = Number(document.getElementById("pv-ov-partner-id")?.value || 0);
                const st = document.getElementById("pv-ov-status");
                if (!pid) {
                    if (st) { st.style.color = "#dc2626"; st.textContent = "⚠️ Selecciona un cliente del buscador."; }
                    return;
                }
                const payload = {
                    cliente_nombre: document.getElementById("pv-ov-cliente-nombre")?.value || "",
                    limite_credito_usd: document.getElementById("pv-ov-limite-credito")?.value || null,
                    limite_facturas_pendientes: document.getElementById("pv-ov-limite-facturas")?.value || null,
                    dias_mora_max: document.getElementById("pv-ov-dias-mora")?.value || null,
                    bloquear_auto_envio: Boolean(document.getElementById("pv-ov-bloquear")?.checked),
                    notas: document.getElementById("pv-ov-notas")?.value || "",
                };
                try {
                    const res = await fetch(`/api/config/politica-ventas/clientes/${pid}`, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(payload),
                    });
                    const d = await res.json().catch(() => ({}));
                    if (!res.ok) throw new Error(d.error || d.detail || `Error ${res.status}`);
                    ovForm.reset();
                    document.getElementById("pv-ov-partner-id").value = "";
                    document.getElementById("pv-ov-cliente-nombre").value = "";
                    if (st) { st.style.color = "#15803d"; st.textContent = "✅ Excepción guardada."; }
                    await loadPoliticaVentasConfig();
                } catch (err) {
                    if (st) { st.style.color = "#dc2626"; st.textContent = `⚠️ ${err.message}`; }
                }
            });
        }

        const ovTbody = document.getElementById("pv-overrides-tbody");
        if (ovTbody) {
            ovTbody.addEventListener("click", async (e) => {
                const btnEdit = e.target.closest("[data-pv-edit]");
                if (btnEdit) {
                    const pid = Number(btnEdit.dataset.pvEdit);
                    const ov = _pvOverrides.find((x) => x.partner_odoo_id === pid);
                    if (!ov) return;
                    document.getElementById("pv-ov-partner-id").value = String(ov.partner_odoo_id);
                    document.getElementById("pv-ov-cliente-nombre").value = ov.cliente_nombre || "";
                    document.getElementById("pv-ov-buscar").value = `${ov.cliente_nombre} (#${ov.partner_odoo_id})`;
                    document.getElementById("pv-ov-limite-credito").value = ov.limite_credito_usd != null ? String(ov.limite_credito_usd) : "";
                    document.getElementById("pv-ov-limite-facturas").value = ov.limite_facturas_pendientes != null ? String(ov.limite_facturas_pendientes) : "";
                    document.getElementById("pv-ov-dias-mora").value = ov.dias_mora_max != null ? String(ov.dias_mora_max) : "";
                    document.getElementById("pv-ov-bloquear").checked = Boolean(ov.bloquear_auto_envio);
                    document.getElementById("pv-ov-notas").value = ov.notas || "";
                    return;
                }
                const btnDel = e.target.closest("[data-pv-del]");
                if (btnDel) {
                    const pid = Number(btnDel.dataset.pvDel);
                    const res = await fetch(`/api/config/politica-ventas/clientes/${pid}`, { method: "DELETE" });
                    if (res.ok) await loadPoliticaVentasConfig();
                }
            });
        }

        if (!_pvCargado) {
            const card = document.getElementById("politica-ventas-card");
            if (card && !card.classList.contains("config-subpage-hidden")) {
                loadPoliticaVentasConfig();
            }
        }
    });
})();
