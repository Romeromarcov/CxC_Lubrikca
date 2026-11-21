/* Submódulo Compras Unificadas (CxC + Odoo), Conciliación Regla R4, Caja Chica y Config Gastos */
(function () {
    "use strict";

    let _gastosConfigCache = null;

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

    function _origenBadge(d) {
        const partes = [];
        if (d.origen_sistema === "odoo") {
            partes.push('<span class="badge-origen-odoo">🟣 Odoo</span>');
        } else if (d.origen === "bot") {
            partes.push('<span class="badge-origen-cxc">📲 CxC Bot</span>');
        } else {
            partes.push('<span class="badge-origen-cxc">🏢 CxC Web</span>');
        }
        if (d.en_espejo_odoo && d.origen_sistema === "cxc") {
            partes.push('<div><span class="cap-sub" style="color:#6b21a8; font-weight:600;">🔗 En Odoo</span></div>');
        }
        return partes.join("");
    }

    function _tipoContableBadge(d) {
        const partes = [];
        if (d.asiento_contable || d.tipo === "gasto") {
            if (d.asiento_contable) {
                partes.push('<span class="state-badge abierta">📒 Asiento (entry)</span>');
            } else {
                partes.push('<span class="state-badge abierta">🛒 Gasto menor</span>');
            }
        } else {
            partes.push('<span class="state-badge cierre">🧾 Factura (in_invoice)</span>');
        }
        if (d.conciliado_banco_huella) {
            partes.push('<div><span class="cap-sub" style="color:#0f766e; font-weight:600;">🏦 Conciliado</span></div>');
        }
        if (d.lote_caja_chica_id) {
            partes.push(`<div><span class="cap-sub">🧺 Lote #${_esc(d.lote_caja_chica_id)}</span></div>`);
        }
        return partes.join("");
    }

    function _estadoBadge(d) {
        if (d.estado === "CONFIRMADO_ODOO") {
            const lbl = d.estado_odoo === "draft" ? "🟣 Borrador Odoo" : "🟣 Confirmado Odoo";
            return `<span class="semaphore ${d.estado_odoo === "draft" ? "amarillo" : "verde"}">${lbl}</span>`;
        }
        if (typeof _cxc().estadoBadge === "function") {
            return _cxc().estadoBadge(d);
        }
        return `<span class="state-badge">${_esc(d.estado)}</span>`;
    }

    async function loadComprasUnificadas() {
        const tbody = document.getElementById("op-compras-table-body");
        if (!tbody) return;

        const origen = (document.getElementById("op-cmp-origen") || {}).value || "todos";
        const tipo = (document.getElementById("op-cmp-tipo") || {}).value || "";
        const estado = (document.getElementById("op-cmp-estado") || {}).value || "";
        const q = ((document.getElementById("op-cmp-buscar") || {}).value || "").trim();

        const qs = new URLSearchParams({ origen, limit: "200" });
        if (tipo) qs.set("tipo", tipo);
        if (estado) qs.set("estado", estado);
        if (q) qs.set("q", q);

        try {
            const res = await fetch(`/api/compras/unificadas?${qs}`);
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);

            const puedeAprobar = Boolean(data.puede_aprobar_compras);
            const r = data.resumen || {};

            const badge = document.getElementById("badge-op-compras-pend");
            if (badge) badge.textContent = String(r.pendientes_aprobacion || 0);

            for (const [idEl, val] of [
                ["op-cmp-n-TOTAL", r.total || 0],
                ["op-cmp-n-ORIGEN_CXC", r.origen_cxc || 0],
                ["op-cmp-n-ORIGEN_ODOO", r.origen_odoo || 0],
                ["op-cmp-n-PENDIENTE_APROBACION", r.pendientes_aprobacion || 0],
                ["op-cmp-n-ASIENTOS", r.asientos_sin_factura || 0],
                ["op-cmp-n-INCOMPLETO", r.incompletos || 0],
                ["op-cmp-n-ENVIADO_ODOO", (r.enviados_odoo || 0) + (r.confirmados_odoo || 0)],
                ["op-cmp-n-RECHAZADO", r.rechazados || 0],
            ]) {
                const el = document.getElementById(idEl);
                if (el) el.textContent = String(val);
            }

            const resEl = document.getElementById("op-cmp-resumen");
            if (resEl) {
                resEl.textContent =
                    `${data.total || 0} documento(s) · Origen: ${r.origen_cxc || 0} CxC · ` +
                    `${r.origen_odoo || 0} Odoo · ${r.asientos_sin_factura || 0} asiento(s) sin factura`;
            }

            const items = data.items || [];
            if (!items.length) {
                tbody.innerHTML = '<tr><td colspan="9" class="table-empty">No hay facturas de proveedor ni gastos con estos filtros.</td></tr>';
                return;
            }

            const alertFn = typeof _cxc().alertasHtml === "function" ? _cxc().alertasHtml : () => "";
            tbody.innerHTML = items.map((d) => {
                const docRef = d.numero_documento || (d.sin_factura ? "S/N (Sin factura)" : (d.referencia || "—"));
                const subRef = d.numero_control
                    ? `Ctrl: ${d.numero_control}`
                    : (d.concepto || d.categoria || d.referencia || "");
                const diarioCta =
                    `${_esc(d.diario_nombre || (d.asiento_contable ? "Diario Operaciones Varias" : "Diario Compras VES"))}` +
                    (d.cuenta_contable_codigo
                        ? `<div class="cap-sub">${_esc(d.cuenta_contable_codigo)} · ${_esc(d.cuenta_contable_nombre || "")}</div>`
                        : "");

                let accionesHtml;
                if (d.solo_lectura || d.origen_sistema === "odoo") {
                    accionesHtml = `<span class="cap-sub">Solo lectura (Odoo)</span>` +
                        (d.odoo_ref ? `<div class="cap-sub">${_esc(d.odoo_ref)}</div>` : "");
                } else {
                    const btnPrev = d.preview_path
                        ? `<a href="${_esc(d.preview_path)}" target="_blank" rel="noopener" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem; text-decoration:none;">🔗 Preview</a>`
                        : "";
                    const btnEdit = `<button type="button" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem;" onclick="editarOperativoPorId(${d.id})">${d.puede_editar ? "✏️ Editar" : "👁 Ver"}</button>`;
                    const btnApr = (d.puede_aprobar && puedeAprobar)
                        ? `<button type="button" class="btn-primary" style="padding:0.32rem 0.6rem; font-size:0.78rem; background:#16a34a;" onclick="aprobarOperativo(${d.id})">${d.asiento_contable ? "📒 Autorizar Asiento" : "✅ Autorizar"}</button>`
                        : "";
                    const btnRec = (d.puede_rechazar && puedeAprobar)
                        ? `<button type="button" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem; color:#991b1b;" onclick="rechazarOperativo(${d.id})">✖</button>`
                        : "";
                    accionesHtml = `${btnPrev}${btnEdit}${btnApr}${btnRec}`;
                }

                const trazColC = (typeof _cxc().trazabilidadHtml === "function")
                    ? _cxc().trazabilidadHtml(d)
                    : `<strong>${_esc(d.numero)}</strong><div>${_origenBadge(d)}</div>`;
                const provSub = d.rif || (d.partner_odoo_id ? `Odoo #${d.partner_odoo_id}` : "Se creará al aprobar");
                return `<tr>
                    <td>${trazColC}</td>
                    <td>${_tipoContableBadge(d)}</td>
                    <td>${_esc(d.fecha_documento || "—")}</td>
                    <td><strong>${_esc(d.contraparte_nombre || "Sin proveedor")}</strong><div class="cap-sub">${_esc(provSub)}</div></td>
                    <td><strong>${_esc(docRef)}</strong>${subRef ? `<div class="cap-sub">${_esc(subRef)}</div>` : ""}</td>
                    <td><strong>${d.monto != null ? _monto(d.monto) : "—"} ${_esc(d.moneda)}</strong></td>
                    <td>${diarioCta}</td>
                    <td>${_estadoBadge(d)}${alertFn(d)}</td>
                    <td style="white-space:nowrap; display:flex; gap:4px; flex-wrap:wrap;">${accionesHtml}</td>
                </tr>`;
            }).join("");
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="9" class="table-empty">❌ ${_esc(err.message)}</td></tr>`;
        }
    }

    async function loadConciliacionGastos() {
        const r4Tbody = document.getElementById("cmp-r4-tbody");
        const sysDocsTbody = document.getElementById("sys-conc-docs-tbody");
        const egTbody = document.getElementById("cmp-egresos-tbody");
        const lotesTbody = document.getElementById("cmp-lotes-tbody");
        if (!r4Tbody && !sysDocsTbody && !egTbody && !lotesTbody) return;

        try {
            const [rConc, rLotes] = await Promise.all([
                fetch("/api/bancos/conciliacion/operativos-sistema"),
                fetch("/api/compras/lotes-caja-chica"),
            ]);
            const dConc = await rConc.json().catch(() => ({}));
            const dLotes = await rLotes.json().catch(() => ({}));
            if (!rConc.ok) throw new Error(dConc.detail || dConc.error || `Error ${rConc.status}`);

            const puedeConciliar = Boolean(dConc.puede_conciliar);
            const dirFiltro = (document.getElementById("sys-conc-dir") || {}).value || "todos";
            const sugsAll = dConc.sugerencias_sistema || dConc.sugerencias_r4 || [];
            const sugs = dirFiltro === "todos"
                ? sugsAll
                : sugsAll.filter((s) => s.direccion === dirFiltro);
            const egresos = dConc.egresos || [];
            const ingresos = dConc.ingresos || [];
            const lotes = dLotes.items || [];

            const totEgr = dConc.total_egresos || 0;
            const totIng = dConc.total_ingresos || 0;
            const concEgr = dConc.conciliados_count || 0;
            const concIng = dConc.ingresos_conciliados_count || 0;
            const pendTot = (dConc.pendientes_count || 0) + (dConc.ingresos_pendientes_count || 0);

            for (const [idEl, val] of [
                ["cmp-r4-total", totEgr],
                ["sys-ing-total", totIng],
                ["cmp-r4-conciliados", concEgr + concIng],
                ["cmp-r4-pendientes", pendTot],
                ["cmp-r4-sugerencias", sugs.length],
            ]) {
                const el = document.getElementById(idEl);
                if (el) el.textContent = String(val);
            }

            if (r4Tbody) {
                if (!sugs.length) {
                    r4Tbody.innerHTML = '<tr><td colspan="6" class="table-empty">No hay sugerencias automáticas R4/R5 pendientes entre movimientos bancarios y documentos del sistema.</td></tr>';
                } else {
                    r4Tbody.innerHTML = sugs.map((s) => {
                        const dirBadge = s.direccion === "ingreso" || s.regla === "R5"
                            ? '<span class="semaphore verde">📥 Ingreso · R5</span>'
                            : '<span class="semaphore amarillo">📤 Egreso · R4</span>';
                        const btn = puedeConciliar
                            ? `<button type="button" class="btn-primary" style="padding:0.32rem 0.65rem; font-size:0.78rem; background:#0284c7;" data-r4-vincular="1" data-doc-id="${s.documento_id}" data-huella="${_esc(s.huella)}">🔗 Conciliar</button>`
                            : '<span class="cap-sub">Solo lectura</span>';
                        const trazSug = typeof _cxc().trazabilidadHtml === "function"
                            ? _cxc().trazabilidadHtml(s)
                            : `<strong>${_esc(s.numero)}</strong>`;
                        return `<tr>
                            <td>${dirBadge}</td>
                            <td><strong>${_esc(s.banco)} · ${_esc(s.fecha_banco || "—")}</strong><div class="cap-sub">Ref: ${_esc(s.referencia_banco || "—")} · ${_esc(s.descripcion_banco || "")}</div></td>
                            <td><strong>${_monto(s.monto_banco)} ${_esc(s.moneda_banco || s.moneda_doc)}</strong></td>
                            <td>${trazSug}<div class="cap-sub">${_esc(s.tipo)} · ${_esc(s.contraparte || "Sin contraparte")} · Ref: ${_esc(s.referencia_doc || "S/N")}</div></td>
                            <td><span class="state-badge abierta">Pts ${s.puntaje}</span><div class="cap-sub">${_esc(s.motivo || "")}</div></td>
                            <td>${btn}</td>
                        </tr>`;
                    }).join("");
                }
            }

            if (sysDocsTbody) {
                const comb = [
                    ...ingresos.map((d) => ({ ...d, _dir: "ingreso" })),
                    ...egresos.map((d) => ({ ...d, _dir: "egreso" })),
                ];
                const filtDocs = dirFiltro === "todos" ? comb : comb.filter((x) => x._dir === dirFiltro);
                filtDocs.sort((a, b) => String(b.fecha_documento || "").localeCompare(String(a.fecha_documento || "")));
                if (!filtDocs.length) {
                    sysDocsTbody.innerHTML = '<tr><td colspan="7" class="table-empty">Sin ingresos o egresos reportados para este filtro.</td></tr>';
                } else {
                    sysDocsTbody.innerHTML = filtDocs.slice(0, 120).map((d) => {
                        const dirChip = d._dir === "ingreso"
                            ? '<span class="semaphore verde">📥 Ingreso</span>'
                            : '<span class="semaphore amarillo">📤 Egreso</span>';
                        const estBanco = d.conciliado_banco_huella
                            ? `<span class="semaphore verde">🏦 Conciliado</span><div class="cap-sub">${_esc(d.conciliado_banco_huella.slice(0, 14))}…</div>` +
                              (puedeConciliar ? `<button type="button" class="btn-secondary" style="margin-top:2px; padding:2px 6px; font-size:0.72rem; color:#991b1b;" data-r4-desvincular="1" data-doc-id="${d.documento_id}">✖ Desvincular</button>` : "")
                            : '<span class="semaphore rojo">⏳ Sin conciliar</span>';
                        const trazDoc = typeof _cxc().trazabilidadHtml === "function"
                            ? _cxc().trazabilidadHtml(d)
                            : `<strong>${_esc(d.numero)}</strong>`;
                        return `<tr>
                            <td>${dirChip}</td>
                            <td>${trazDoc}<div class="cap-sub">${_esc(d.tipo)}</div></td>
                            <td>${_esc(d.fecha_documento || "—")}</td>
                            <td><strong>${_esc(d.contraparte_nombre || "Sin contraparte")}</strong><div class="cap-sub">${_esc(d.concepto || d.categoria || "")}</div></td>
                            <td><strong>${d.monto != null ? _monto(d.monto) : "—"} ${_esc(d.moneda)}</strong></td>
                            <td>${_esc(d.referencia || "—")}</td>
                            <td>${estBanco}</td>
                        </tr>`;
                    }).join("");
                }
            }

            if (egTbody) {
                if (!egresos.length) {
                    egTbody.innerHTML = '<tr><td colspan="7" class="table-empty">No hay gastos menores o pagos a proveedor elegibles en CxC.</td></tr>';
                } else {
                    egTbody.innerHTML = egresos.map((d) => {
                        const elegibleLote = d.tipo === "gasto" && !d.lote_caja_chica_id;
                        const chk = elegibleLote
                            ? `<input type="checkbox" class="cmp-lote-chk" data-lote-doc-id="${d.documento_id}">`
                            : (d.lote_caja_chica_id ? `<span class="cap-sub">Lote #${_esc(d.lote_caja_chica_id)}</span>` : "—");
                        const estadoBanco = d.conciliado_banco_huella
                            ? `<span class="semaphore verde">🏦 Conciliado</span><div class="cap-sub">${_esc(d.conciliado_banco_huella.slice(0, 14))}…</div>`
                            : '<span class="semaphore amarillo">⏳ Sin conciliar</span>';
                        const btnDesv = (d.conciliado_banco_huella && puedeConciliar)
                            ? `<button type="button" class="btn-secondary" style="padding:0.25rem 0.5rem; font-size:0.76rem; color:#991b1b;" data-r4-desvincular="1" data-doc-id="${d.documento_id}">✖ Desvincular</button>`
                            : `<button type="button" class="btn-secondary" style="padding:0.25rem 0.5rem; font-size:0.76rem;" onclick="editarOperativoPorId(${d.documento_id})">👁 Ver</button>`;
                        return `<tr>
                            <td style="text-align:center;">${chk}</td>
                            <td><strong>${_esc(d.numero)}</strong><div class="cap-sub">${_esc(d.tipo)}</div></td>
                            <td>${_esc(d.fecha_documento || "—")}</td>
                            <td><strong>${_esc(d.contraparte_nombre || "Sin contraparte")}</strong><div class="cap-sub">${_esc(d.concepto || d.categoria || d.referencia || "")}</div></td>
                            <td><strong>${d.monto != null ? _monto(d.monto) : "—"} ${_esc(d.moneda)}</strong></td>
                            <td>${estadoBanco}</td>
                            <td>${btnDesv}</td>
                        </tr>`;
                    }).join("");
                }
            }

            if (lotesTbody) {
                if (!lotes.length) {
                    lotesTbody.innerHTML = '<tr><td colspan="7" class="table-empty">Aún no se han creado lotes de rendición de caja chica.</td></tr>';
                } else {
                    const puedeGest = Boolean(dLotes.puede_gestionar);
                    lotesTbody.innerHTML = lotes.map((l) => {
                        const abierto = l.estado === "ABIERTO";
                        let acts = '<span class="cap-sub">Cerrado</span>';
                        if (abierto && puedeGest) {
                            acts = `<div style="display:flex; gap:4px; flex-wrap:wrap;">
                                <button type="button" class="btn-secondary" style="padding:0.28rem 0.55rem; font-size:0.76rem;" data-lote-cerrar="${l.lote_id}" data-contabilizar="0">✅ Cerrar</button>
                                <button type="button" class="btn-primary" style="padding:0.28rem 0.55rem; font-size:0.76rem; background:#16a34a;" data-lote-cerrar="${l.lote_id}" data-contabilizar="1">📒 Cerrar y Contabilizar</button>
                            </div>`;
                        }
                        const badgeCls = abierto ? "amarillo" : "verde";
                        return `<tr>
                            <td><strong>${_esc(l.codigo)}</strong></td>
                            <td>${_esc(l.responsable || "—")}</td>
                            <td>${_esc(l.moneda || "USD")}</td>
                            <td>${_esc(l.n_gastos ?? 0)}</td>
                            <td><strong>${_monto(l.monto_total)} ${_esc(l.moneda || "USD")}</strong></td>
                            <td><span class="semaphore ${badgeCls}">${_esc(l.estado)}</span></td>
                            <td>${acts}</td>
                        </tr>`;
                    }).join("");
                }
            }
        } catch (err) {
            if (r4Tbody) r4Tbody.innerHTML = `<tr><td colspan="5" class="table-empty">❌ ${_esc(err.message)}</td></tr>`;
        }
    }

    function _renderSelectCuentas(selectId, cuentasFiltradas, todasCuentas, selectedId) {
        const optsBase = cuentasFiltradas.length ? cuentasFiltradas : todasCuentas;
        const selVal = selectedId != null ? String(selectedId) : "";
        let opts = '<option value="">(Automática / heurística por Plan de Cuentas)</option>';
        for (const c of optsBase) {
            const label = `${c.code || ""} · ${c.name || ""}`;
            const sel = selVal && String(c.id) === selVal ? " selected" : "";
            opts += `<option value="${c.id}" data-code="${_esc(c.code || "")}" data-name="${_esc(c.name || "")}"${sel}>${_esc(label)}</option>`;
        }
        return `<select class="filter-select" id="${selectId}" style="width:100%;">${opts}</select>`;
    }

    async function loadConfigGastosContabilidad() {
        const card = document.getElementById("cfg-gastos-contab-card");
        if (!card) return;
        const st = document.getElementById("cfg-gas-status");
        try {
            const [rCfg, rDir, rCtas] = await Promise.all([
                fetch("/api/config/gastos-contabilidad"),
                fetch("/api/config/diarios-generales"),
                fetch("/api/odoo/plan-cuentas"),
            ]);
            const dCfg = await rCfg.json().catch(() => ({}));
            if (!rCfg.ok) throw new Error(dCfg.detail || dCfg.error || `Error ${rCfg.status}`);

            const dDir = rDir.ok ? await rDir.json().catch(() => ({})) : { items: [] };
            const dCtas = rCtas.ok ? await rCtas.json().catch(() => ({})) : { cuentas: [] };

            const cfg = dCfg.config || {};
            const categorias = dCfg.categorias || [];
            const metodos = dCfg.metodos || [];
            const diarios = dDir.items || [];
            const cuentas = dCtas.cuentas || dCtas.items || [];
            _gastosConfigCache = { categorias, metodos };

            const selDiario = document.getElementById("cfg-gas-diario-id");
            if (selDiario) {
                const actualD = cfg.diario_general_id != null ? String(cfg.diario_general_id) : "";
                selDiario.innerHTML = '<option value="">(Primer diario General MISC en Odoo)</option>' +
                    diarios.map((j) => {
                        const sel = actualD && String(j.id) === actualD ? " selected" : "";
                        const lbl = j.code ? `${j.code} · ${j.name}` : j.name;
                        return `<option value="${j.id}"${sel}>${_esc(lbl)}</option>`;
                    }).join("");
            }

            const cuentasLiquidez = cuentas.filter((c) => {
                const t = String(c.account_type || "").toLowerCase();
                const code = String(c.code || "");
                const nm = String(c.name || "").toLowerCase();
                return t === "asset_cash" || code.startsWith("1.1.01") || code.startsWith("11") ||
                    nm.includes("caja") || nm.includes("banco") || nm.includes("efectivo");
            });
            const cuentasGasto = cuentas.filter((c) => {
                const t = String(c.account_type || "").toLowerCase();
                const code = String(c.code || "");
                return t.includes("expense") || code.startsWith("5") || code.startsWith("6");
            });

            const mapaCred = cfg.cuentas_credito_por_metodo || {};
            const gridMet = document.getElementById("cfg-gas-metodos-grid");
            if (gridMet) {
                gridMet.innerHTML = metodos.map((m) => {
                    const clave = m.clave;
                    const entry = mapaCred[clave] || {};
                    const selHtml = _renderSelectCuentas(
                        `cfg-gas-met-${_esc(clave)}`,
                        cuentasLiquidez,
                        cuentas,
                        entry.account_id,
                    );
                    return `<div>
                        <label style="font-size:0.78rem; font-weight:600; color:#475569;">Crédito · ${_esc(m.nombre)}</label>
                        ${selHtml}
                    </div>`;
                }).join("");
            }

            const mapaDeb = cfg.cuentas_debito_por_categoria || {};
            const gridCat = document.getElementById("cfg-gas-categorias-grid");
            if (gridCat) {
                gridCat.innerHTML = categorias.map((cat) => {
                    const entry = mapaDeb[cat] || {};
                    const selHtml = _renderSelectCuentas(
                        `cfg-gas-cat-${_esc(cat)}`,
                        cuentasGasto,
                        cuentas,
                        entry.account_id,
                    );
                    return `<div>
                        <label style="font-size:0.78rem; font-weight:600; color:#475569;">Débito · ${_esc(cat)}</label>
                        ${selHtml}
                    </div>`;
                }).join("");
            }

            const btnSave = document.getElementById("cfg-gas-btn-guardar");
            if (btnSave) btnSave.disabled = !dCfg.puede_editar;
            if (st && !dCfg.puede_editar) {
                st.style.color = "#64748b";
                st.textContent = "Solo lectura: necesitas permiso de aprobación de compras para modificar.";
            }
        } catch (err) {
            if (st) {
                st.style.color = "#dc2626";
                st.textContent = `❌ ${err.message}`;
            }
        }
    }

    function _recolectarMapaSelects(prefix, keys) {
        const out = {};
        for (const k of keys) {
            const sel = document.getElementById(`${prefix}${k}`);
            if (!sel || !sel.value) continue;
            const opt = sel.selectedOptions && sel.selectedOptions[0];
            out[k] = {
                account_id: Number(sel.value),
                codigo: opt ? (opt.getAttribute("data-code") || "") : "",
                nombre: opt ? (opt.getAttribute("data-name") || "") : "",
            };
        }
        return out;
    }

    window.loadComprasUnificadas = loadComprasUnificadas;
    window.loadOperativosCompras = loadComprasUnificadas;
    window.loadConciliacionGastos = loadConciliacionGastos;
    window.loadConfigGastosContabilidad = loadConfigGastosContabilidad;

    document.addEventListener("DOMContentLoaded", () => {
        document.getElementById("op-cmp-origen")?.addEventListener("change", loadComprasUnificadas);
        document.querySelectorAll(".op-cmp-origen-kpi").forEach((k) => {
            k.addEventListener("click", () => {
                const sOrig = document.getElementById("op-cmp-origen");
                const sEst = document.getElementById("op-cmp-estado");
                const sTip = document.getElementById("op-cmp-tipo");
                if (sOrig) sOrig.value = k.dataset.opOrigen || "todos";
                if (sEst) sEst.value = "";
                if (sTip) sTip.value = "";
                loadComprasUnificadas();
            });
        });
        document.getElementById("op-cmp-kpi-asientos")?.addEventListener("click", () => {
            const sTip = document.getElementById("op-cmp-tipo");
            const sEst = document.getElementById("op-cmp-estado");
            if (sTip) sTip.value = "gasto";
            if (sEst) sEst.value = "";
            loadComprasUnificadas();
        });

        document.getElementById("cmp-r4-tbody")?.addEventListener("click", async (e) => {
            const btn = e.target.closest("[data-r4-vincular]");
            if (!btn) return;
            const docId = Number(btn.getAttribute("data-doc-id"));
            const huella = btn.getAttribute("data-huella") || "";
            if (!docId || !huella) return;
            btn.disabled = true;
            try {
                const res = await fetch("/api/compras/conciliacion-gastos/vincular", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ documento_id: docId, huella }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
                await Promise.all([loadConciliacionGastos(), loadComprasUnificadas()]);
            } catch (err) {
                alert("❌ " + err.message);
                btn.disabled = false;
            }
        });

        const _handleDesvincularClick = async (e) => {
            const btn = e.target.closest("[data-r4-desvincular]");
            if (!btn) return;
            const docId = Number(btn.getAttribute("data-doc-id"));
            if (!docId) return;
            try {
                const res = await fetch("/api/compras/conciliacion-gastos/vincular", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ documento_id: docId, huella: "" }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
                await Promise.all([loadConciliacionGastos(), loadComprasUnificadas()]);
            } catch (err) {
                alert("❌ " + err.message);
            }
        };
        document.getElementById("cmp-egresos-tbody")?.addEventListener("click", _handleDesvincularClick);
        document.getElementById("sys-conc-docs-tbody")?.addEventListener("click", _handleDesvincularClick);

        document.getElementById("cmp-cajachica-form")?.addEventListener("submit", async (e) => {
            e.preventDefault();
            const st = document.getElementById("cmp-lote-status");
            const ids = Array.from(document.querySelectorAll(".cmp-lote-chk:checked"))
                .map((el) => Number(el.getAttribute("data-lote-doc-id")))
                .filter((n) => n > 0);
            if (!ids.length) {
                if (st) {
                    st.style.color = "#b45309";
                    st.textContent = "⚠️ Selecciona al menos un gasto menor en la tabla de egresos.";
                }
                return;
            }
            const body = {
                responsable: (document.getElementById("cmp-lote-responsable") || {}).value || "",
                moneda: (document.getElementById("cmp-lote-moneda") || {}).value || "USD",
                notas: (document.getElementById("cmp-lote-notas") || {}).value || "",
                documento_ids: ids,
            };
            if (st) { st.style.color = "#1e40af"; st.textContent = "Creando lote de caja chica..."; }
            try {
                const res = await fetch("/api/compras/lotes-caja-chica", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.error || data.detail || `Error ${res.status}`);
                if (st) {
                    st.style.color = "#15803d";
                    st.textContent = `✅ Lote ${data.codigo} creado con ${data.n_gastos || ids.length} gasto(s).`;
                }
                document.getElementById("cmp-cajachica-form").reset();
                await Promise.all([loadConciliacionGastos(), loadComprasUnificadas()]);
            } catch (err) {
                if (st) { st.style.color = "#dc2626"; st.textContent = `❌ ${err.message}`; }
            }
        });

        document.getElementById("cmp-lotes-tbody")?.addEventListener("click", async (e) => {
            const btn = e.target.closest("[data-lote-cerrar]");
            if (!btn) return;
            const loteId = Number(btn.getAttribute("data-lote-cerrar"));
            const contabilizar = btn.getAttribute("data-contabilizar") === "1";
            if (!loteId) return;
            const msg = contabilizar
                ? "¿Cerrar este lote y enviar los gastos pendientes como asientos borrador a Odoo?"
                : "¿Cerrar este lote de caja chica?";
            if (!confirm(msg)) return;
            const st = document.getElementById("cmp-lote-status");
            try {
                const res = await fetch(`/api/compras/lotes-caja-chica/${loteId}/cerrar`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ contabilizar }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
                const envTxt = (data.enviados || []).length
                    ? ` · Contabilizados: ${data.enviados.join(", ")}`
                    : "";
                const errTxt = (data.errores || []).length
                    ? ` · Errores: ${data.errores.join("; ")}`
                    : "";
                if (st) {
                    st.style.color = (data.errores || []).length ? "#b45309" : "#15803d";
                    st.textContent = `✅ Lote ${data.lote ? data.lote.codigo : loteId} ${data.lote ? data.lote.estado : "CERRADO"}${envTxt}${errTxt}`;
                }
                await Promise.all([loadConciliacionGastos(), loadComprasUnificadas()]);
            } catch (err) {
                if (st) { st.style.color = "#dc2626"; st.textContent = `❌ ${err.message}`; }
            }
        });

        document.getElementById("cfg-gastos-contab-form")?.addEventListener("submit", async (e) => {
            e.preventDefault();
            const st = document.getElementById("cfg-gas-status");
            const diarioVal = (document.getElementById("cfg-gas-diario-id") || {}).value || "";
            const metKeys = (_gastosConfigCache ? _gastosConfigCache.metodos : []).map((m) => m.clave);
            const catKeys = _gastosConfigCache ? _gastosConfigCache.categorias : [];
            const body = {
                diario_general_id: diarioVal ? Number(diarioVal) : null,
                cuentas_credito_por_metodo: _recolectarMapaSelects("cfg-gas-met-", metKeys),
                cuentas_debito_por_categoria: _recolectarMapaSelects("cfg-gas-cat-", catKeys),
            };
            if (st) { st.style.color = "#1e40af"; st.textContent = "Guardando configuración contable..."; }
            try {
                const res = await fetch("/api/config/gastos-contabilidad", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.error || data.detail || `Error ${res.status}`);
                if (st) { st.style.color = "#15803d"; st.textContent = "✅ Contabilidad de gastos guardada."; }
                await loadConfigGastosContabilidad();
            } catch (err) {
                if (st) { st.style.color = "#dc2626"; st.textContent = `❌ ${err.message}`; }
            }
        });
    });
})();
