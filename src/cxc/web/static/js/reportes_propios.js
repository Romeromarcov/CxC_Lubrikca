/* Reportes 100% sobre BD Propia CxC (Solo Lectura) + Siembra Histórica y Correlativos Odoo */
(function () {
    "use strict";

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

    function _usd(v) {
        const n = Number(v || 0);
        return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function _buildFiltrosParams() {
        const vendedor = (document.getElementById("rep-prop-vendedor") || {}).value || "*";
        const antiguedad = (document.getElementById("rep-prop-antiguedad") || {}).value || "*";
        const q = ((document.getElementById("rep-prop-q") || {}).value || "").trim();
        const soloSaldo = Boolean((document.getElementById("rep-prop-solo-saldo") || {}).checked);
        const qs = new URLSearchParams();
        if (vendedor && vendedor !== "*") qs.set("vendedor", vendedor);
        if (antiguedad && antiguedad !== "*") qs.set("antiguedad", antiguedad);
        if (q) qs.set("q", q);
        if (soloSaldo) qs.set("solo_con_saldo", "true");
        return qs;
    }

    async function loadReportesSaldosPropios() {
        const tbodyCli = document.getElementById("rep-prop-clientes-tbody");
        const tbodyVend = document.getElementById("rep-prop-vendedores-tbody");
        const banner = document.getElementById("rep-bd-propia-banner");
        if (!tbodyCli && !tbodyVend && !banner) return;

        const qs = _buildFiltrosParams();
        const csvLink = document.getElementById("rep-prop-csv-link");
        if (csvLink) {
            csvLink.href = `/api/reportes/saldos-propios/csv${qs.toString() ? "?" + qs.toString() : ""}`;
        }

        try {
            const res = await fetch(`/api/reportes/saldos-propios?${qs.toString()}`, { cache: "no-store" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);

            const k = data.kpis || {};
            const kg = data.kpis_globales || k;

            if (banner) {
                const syncTxt = data.ultimo_sync_bd
                    ? `Último sync BD: ${String(data.ultimo_sync_bd).slice(0, 16).replace("T", " ")}`
                    : "Sin sync incremental aún";
                banner.innerHTML =
                    `<div><strong>💾 Reporte 100% BD Propia CxC (Solo Lectura):</strong> ` +
                    `Saldo Deudor Global <strong>${_usd(kg.saldo_deudor_usd)}</strong> ` +
                    `(Ventas ${_usd(kg.ventas_confirmadas_usd)} − Desc/NC ${_usd(kg.descuentos_confirmados_usd)} − Pagos ${_usd(kg.pagos_confirmados_usd)})` +
                    `<div class="cap-sub">${_esc(syncTxt)} · Incluye Zelle 3ros (${_usd(kg.pagos_zelle_tercero_usd)}) y Efectivo CxC (${_usd(kg.pagos_efectivo_cxc_usd)})</div></div>` +
                    `<button type="button" class="btn-secondary" onclick="switchReporteSubtab('bd-propia')">Ver Detalle BD Propia →</button>`;
            }

            const setText = (id, txt) => {
                const el = document.getElementById(id);
                if (el) el.textContent = txt;
            };
            setText("rep-prop-kpi-ventas", _usd(k.ventas_confirmadas_usd));
            setText("rep-prop-kpi-descuentos", _usd(k.descuentos_confirmados_usd));
            setText(
                "rep-prop-kpi-descuentos-sub",
                `Sistema: ${_usd(k.descuentos_sistema_usd)} · NC Odoo: ${_usd(k.notas_credito_usd)}`,
            );
            setText("rep-prop-kpi-pagos", _usd(k.pagos_confirmados_usd));
            setText(
                "rep-prop-kpi-pagos-sub",
                `Odoo: ${_usd(k.pagos_odoo_usd)} · Zelle 3ro: ${_usd(k.pagos_zelle_tercero_usd)} · Efec CxC: ${_usd(k.pagos_efectivo_cxc_usd)}`,
            );
            setText("rep-prop-kpi-deudor", _usd(k.saldo_deudor_usd));
            setText(
                "rep-prop-kpi-deudor-sub",
                `${k.clientes_con_saldo || 0} cliente(s) con deuda · Vencido: ${_usd(k.vencido_usd)}`,
            );
            setText("rep-prop-kpi-favor", _usd(k.saldo_favor_usd));

            const selV = document.getElementById("rep-prop-vendedor");
            const vendedores = data.vendedores || [];
            if (selV) {
                const prev = selV.value || "*";
                selV.innerHTML = '<option value="*">Todos los vendedores</option>' +
                    vendedores.map((v) => `<option value="${_esc(v.vendedor_email)}">${_esc(v.vendedor_email)}</option>`).join("");
                selV.value = Array.from(selV.options).some((o) => o.value === prev) ? prev : "*";
            }

            const clientes = data.clientes || [];
            if (tbodyCli) {
                if (!clientes.length) {
                    tbodyCli.innerHTML = '<tr><td colspan="8" class="table-empty">Sin movimientos en BD propia con estos filtros.</td></tr>';
                } else {
                    tbodyCli.innerHTML = clientes.map((c) => {
                        const extrasPago = [];
                        if (c.pagos_zelle_tercero_usd > 0) extrasPago.push(`Zelle 3ro: ${_usd(c.pagos_zelle_tercero_usd)}`);
                        if (c.pagos_efectivo_cxc_usd > 0) extrasPago.push(`Efec CxC: ${_usd(c.pagos_efectivo_cxc_usd)}`);
                        const subPagos = extrasPago.length ? `<div class="cap-sub">${_esc(extrasPago.join(" · "))}</div>` : "";
                        const saldoCls = c.saldo_deudor_usd > 0 ? "color:#be123c; font-weight:700;" : "color:#15803d; font-weight:600;";
                        return `<tr>
                            <td><strong>${_esc(c.cliente_nombre)}</strong><div class="cap-sub">ID ${_esc(c.cliente_id)} · ${c.n_ordenes} órd. / ${c.n_pagos} pagos</div></td>
                            <td>${_esc(c.vendedor_email)}</td>
                            <td>${_usd(c.ventas_confirmadas_usd)}</td>
                            <td>${_usd(c.descuentos_confirmados_usd)}</td>
                            <td>${_usd(c.pagos_confirmados_usd)}${subPagos}</td>
                            <td style="${saldoCls}">${c.saldo_deudor_usd > 0 ? _usd(c.saldo_deudor_usd) : (c.saldo_favor_usd > 0 ? `Favor ${_usd(c.saldo_favor_usd)}` : "$0.00")}</td>
                            <td>${c.vencido_usd > 0 ? `<span class="semaphore rojo">${_usd(c.vencido_usd)}</span>` : '<span class="semaphore verde">Al día</span>'}</td>
                            <td><button type="button" class="btn-secondary" style="padding:0.28rem 0.55rem; font-size:0.78rem;" data-ec-cliente="${_esc(c.cliente_id)}">📄 Estado Cuenta</button></td>
                        </tr>`;
                    }).join("");
                }
            }

            if (tbodyVend) {
                if (!vendedores.length) {
                    tbodyVend.innerHTML = '<tr><td colspan="7" class="table-empty">Sin vendedores en BD propia.</td></tr>';
                } else {
                    tbodyVend.innerHTML = vendedores.map((v) => `<tr>
                        <td><strong>${_esc(v.vendedor_email)}</strong></td>
                        <td>${v.n_clientes} (${v.n_clientes_con_saldo} con deuda)</td>
                        <td>${_usd(v.ventas_confirmadas_usd)}</td>
                        <td>${_usd(v.descuentos_confirmados_usd)}</td>
                        <td>${_usd(v.pagos_confirmados_usd)}</td>
                        <td style="font-weight:700;">${_usd(v.saldo_deudor_usd)}</td>
                        <td>${v.vencido_usd > 0 ? `<span class="semaphore rojo">${_usd(v.vencido_usd)}</span>` : "$0.00"}</td>
                    </tr>`).join("");
                }
            }
        } catch (err) {
            if (tbodyCli) tbodyCli.innerHTML = `<tr><td colspan="8" class="table-empty">❌ ${_esc(err.message)}</td></tr>`;
        }
    }

    async function abrirEstadoCuentaCliente(clienteId) {
        const modal = document.getElementById("modal-estado-cuenta-propio");
        const tbody = document.getElementById("rep-prop-ec-tbody");
        const tit = document.getElementById("rep-prop-ec-titulo");
        const resEl = document.getElementById("rep-prop-ec-resumen");
        if (!modal || !tbody) return;
        modal.style.display = "flex";
        tbody.innerHTML = '<tr><td colspan="7" class="table-empty">Cargando estado de cuenta desde BD propia...</td></tr>';
        try {
            const res = await fetch(`/api/reportes/estado-cuenta/${encodeURIComponent(clienteId)}`, { cache: "no-store" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
            const r = data.resumen || {};
            if (tit) tit.textContent = `📄 Estado de Cuenta (BD Propia): ${r.cliente_nombre || clienteId}`;
            if (resEl) {
                resEl.innerHTML =
                    `Ventas Confirmadas: <strong>${_usd(r.ventas_confirmadas_usd)}</strong> · ` +
                    `Descuentos/NC: <strong>${_usd(r.descuentos_confirmados_usd)}</strong> · ` +
                    `Pagos Confirmados: <strong>${_usd(r.pagos_confirmados_usd)}</strong> · ` +
                    `Saldo Deudor: <strong>${_usd(r.saldo_deudor_usd)}</strong>`;
            }
            const movs = data.movimientos || [];
            if (!movs.length) {
                tbody.innerHTML = '<tr><td colspan="7" class="table-empty">Sin movimientos.</td></tr>';
                return;
            }
            tbody.innerHTML = movs.map((m) => `<tr>
                <td>${_esc(m.fecha)}</td>
                <td>${_esc(m.etiqueta)}</td>
                <td><strong>${_esc(m.referencia)}</strong></td>
                <td><span class="${m.fuente === "cxc" ? "badge-origen-cxc" : "badge-origen-odoo"}">${_esc(String(m.fuente || "").toUpperCase())}</span></td>
                <td>${m.debito_usd > 0 ? _usd(m.debito_usd) : "—"}</td>
                <td>${m.credito_usd > 0 ? _usd(m.credito_usd) : "—"}</td>
                <td><strong>${_usd(m.saldo_acumulado_usd)}</strong></td>
            </tr>`).join("");
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="7" class="table-empty">❌ ${_esc(err.message)}</td></tr>`;
        }
    }

    async function loadSiembraOdooEstado() {
        const card = document.getElementById("siembra-odoo-card");
        if (!card) return;
        const estEl = document.getElementById("siembra-estado-resumen");
        const chipsEl = document.getElementById("siembra-conteos-chips");
        const tbody = document.getElementById("siembra-correlativos-tbody");
        try {
            const res = await fetch("/api/admin/siembra-odoo/estado", { cache: "no-store" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);

            const puede = Boolean(data.puede_sembrar);
            const btnS = document.getElementById("btn-siembra-odoo");
            const btnC = document.getElementById("btn-siembra-correlativos");
            if (btnS) btnS.style.display = puede ? "inline-flex" : "none";
            if (btnC) btnC.style.display = puede ? "inline-flex" : "none";

            const u = data.ultima_siembra;
            if (estEl) {
                if (u) {
                    const cls = u.estado === "ERROR" ? "rojo" : (u.estado === "COMPLETADA_CON_AVISOS" ? "amarillo" : "verde");
                    estEl.innerHTML =
                        `<span class="semaphore ${cls}">${_esc(u.estado)}</span> ` +
                        `Actor: <strong>${_esc(u.actor)}</strong> · ` +
                        `Fecha: ${_esc(String(u.finalizada_en || u.iniciada_en || "").slice(0, 16).replace("T", " "))} ` +
                        (u.duracion_seg != null ? `(${u.duracion_seg}s)` : "");
                } else {
                    estEl.innerHTML = '<span class="semaphore amarillo">⏳ Sin corrida de siembra registrada aún</span>';
                }
            }

            const conteos = data.conteos_bd || {};
            if (chipsEl) {
                chipsEl.innerHTML = Object.entries(conteos).map(([k, v]) =>
                    `<span class="state-badge">${_esc(k)}: <strong>${v}</strong></span>`
                ).join(" ");
            }

            const corrs = data.correlativos || [];
            if (tbody) {
                if (!corrs.length) {
                    tbody.innerHTML = '<tr><td colspan="6" class="table-empty">Sin correlativos registrados todavía. Ejecuta «Recalcular Correlativos» o «Siembra Histórica Completa».</td></tr>';
                } else {
                    tbody.innerHTML = corrs.map((c) => `<tr>
                        <td><strong>${_esc(c.entidad)}</strong></td>
                        <td><code>${_esc(c.prefijo)}</code></td>
                        <td><strong>${c.ultimo_numero}</strong></td>
                        <td>${_esc(c.ejemplo_maximo)}</td>
                        <td><span class="${c.fuente === "cxc" ? "badge-origen-cxc" : "badge-origen-odoo"}">${_esc(String(c.fuente).toUpperCase())}</span></td>
                        <td>${c.total_registros}</td>
                    </tr>`).join("");
                }
            }
        } catch (err) {
            if (estEl) estEl.textContent = `❌ ${err.message}`;
        }
    }

    async function ejecutarSiembraOdoo(soloCorrelativos) {
        const st = document.getElementById("siembra-accion-status");
        const msg = soloCorrelativos
            ? "¿Recalcular y guardar la tabla de correlativos actuales sin consultar Odoo?"
            : "¿Ejecutar siembra histórica completa desde Odoo hacia Postgres conservando correlativos?";
        if (!confirm(msg)) return;
        if (st) { st.style.color = "#1e40af"; st.textContent = "Iniciando proceso..."; }
        try {
            const res = await fetch("/api/admin/siembra-odoo", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ solo_correlativos: Boolean(soloCorrelativos) }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
            if (st) { st.style.color = "#15803d"; st.textContent = `✅ ${data.message || "Iniciado."}`; }
            setTimeout(() => { loadSiembraOdooEstado(); }, 1800);
        } catch (err) {
            if (st) { st.style.color = "#dc2626"; st.textContent = `❌ ${err.message}`; }
        }
    }

    window.loadReportesSaldosPropios = loadReportesSaldosPropios;
    window.abrirEstadoCuentaCliente = abrirEstadoCuentaCliente;
    window.loadSiembraOdooEstado = loadSiembraOdooEstado;
    window.ejecutarSiembraOdoo = ejecutarSiembraOdoo;

    document.addEventListener("DOMContentLoaded", () => {
        ["rep-prop-vendedor", "rep-prop-antiguedad", "rep-prop-solo-saldo"].forEach((idEl) => {
            document.getElementById(idEl)?.addEventListener("change", loadReportesSaldosPropios);
        });
        let tRep = null;
        document.getElementById("rep-prop-q")?.addEventListener("input", () => {
            clearTimeout(tRep);
            tRep = setTimeout(loadReportesSaldosPropios, 280);
        });
        document.getElementById("rep-prop-clientes-tbody")?.addEventListener("click", (e) => {
            const btn = e.target.closest("[data-ec-cliente]");
            if (!btn) return;
            const cid = btn.getAttribute("data-ec-cliente");
            if (cid) abrirEstadoCuentaCliente(cid);
        });
        document.getElementById("rep-prop-ec-cerrar")?.addEventListener("click", () => {
            const m = document.getElementById("modal-estado-cuenta-propio");
            if (m) m.style.display = "none";
        });
    });
})();
