/* Submódulo Pagos -> Enviados (pagos salientes a proveedores, PPR-*) */
(function () {
    "use strict";

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
        if (window.CxC && typeof window.CxC.monto === "function") return window.CxC.monto(v);
        const n = Number(v || 0);
        return Number.isFinite(n) ? n.toFixed(2) : "—";
    }

    function _estadoBadge(d) {
        if (window.CxC && typeof window.CxC.estadoBadge === "function") return window.CxC.estadoBadge(d);
        return `<span class="state-badge">${_esc(d.estado)}</span>`;
    }

    function _alertasHtml(d) {
        if (window.CxC && typeof window.CxC.alertasHtml === "function") return window.CxC.alertasHtml(d);
        return "";
    }

    async function loadOperativosPagosEnviados() {
        const tbody = document.getElementById("op-pagos-enviados-table-body");
        if (!tbody) return;
        const estado = (document.getElementById("op-env-estado") || {}).value || "";
        const q = ((document.getElementById("op-env-buscar") || {}).value || "").trim();
        const qs = new URLSearchParams({ grupo: "pagos_enviados", limit: "100" });
        if (estado) qs.set("estado", estado);
        if (q) qs.set("q", q);

        try {
            const res = await fetch(`/api/operativos?${qs}`);
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);

            const puedeAprobar = Boolean(data.puede_aprobar_compras);
            const r = data.resumen || {};
            const badge = document.getElementById("badge-op-enviados-pend");
            if (badge) badge.textContent = String(r.pendientes_aprobacion || 0);

            for (const [idEl, val] of [
                ["op-env-n-PENDIENTE_APROBACION", r.pendientes_aprobacion || 0],
                ["op-env-n-INCOMPLETO", r.incompletos || 0],
                ["op-env-n-ENVIADO_ODOO", r.enviados_odoo || 0],
                ["op-env-n-RECHAZADO", r.rechazados || 0],
            ]) {
                const el = document.getElementById(idEl);
                if (el) el.textContent = String(val);
            }

            const resEl = document.getElementById("op-env-resumen");
            if (resEl) resEl.textContent = `${data.total || 0} pago(s) enviado(s) encontrado(s)`;

            const items = data.items || [];
            if (!items.length) {
                tbody.innerHTML = '<tr><td colspan="8" class="table-empty">No hay pagos a proveedores con estos filtros.</td></tr>';
                return;
            }

            tbody.innerHTML = items.map((d) => {
                const refTxt = d.es_efectivo
                    ? '<span style="color:#0369a1; font-weight:600;">Efectivo (memo vacío)</span>'
                    : _esc(d.referencia || "—");
                const subRef = d.factura_ref ? `Fac: ${_esc(d.factura_ref)}` : _esc(d.metodo_pago || "");
                const btnPrev = d.preview_path
                    ? `<a href="${_esc(d.preview_path)}" target="_blank" rel="noopener" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem; text-decoration:none;">🔗 Preview</a>`
                    : "";
                const btnApr = (d.puede_aprobar && puedeAprobar)
                    ? `<button type="button" class="btn-primary" style="padding:0.32rem 0.6rem; font-size:0.78rem; background:#16a34a;" onclick="aprobarOperativo(${d.id})">✅ Autorizar</button>`
                    : "";
                const btnRec = (d.puede_rechazar && puedeAprobar)
                    ? `<button type="button" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem; color:#991b1b;" onclick="rechazarOperativo(${d.id})">✖</button>`
                    : "";
                return `<tr>
                    <td><strong>${_esc(d.numero)}</strong><div class="cap-sub">${d.origen === "bot" ? "📲 Bot" : "💻 Web"}</div></td>
                    <td>${_esc(d.fecha_documento || "—")}</td>
                    <td><strong>${_esc(d.contraparte_nombre || "Sin proveedor")}</strong><div class="cap-sub">${_esc(d.rif || (d.partner_odoo_id ? ("Odoo #" + d.partner_odoo_id) : "Se creará al aprobar"))}</div></td>
                    <td><strong>${d.monto != null ? _monto(d.monto) : "—"} ${_esc(d.moneda)}</strong></td>
                    <td>${_esc(d.diario_nombre || d.banco || "Por definir")}</td>
                    <td>${refTxt}${subRef ? `<div class="cap-sub">${subRef}</div>` : ""}</td>
                    <td>${_estadoBadge(d)}${_alertasHtml(d)}</td>
                    <td style="white-space:nowrap; display:flex; gap:4px; flex-wrap:wrap;">
                        ${btnPrev}
                        <button type="button" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem;" onclick="editarOperativoPorId(${d.id})">${d.puede_editar ? "✏️ Editar" : "👁 Ver"}</button>
                        ${btnApr}${btnRec}
                    </td>
                </tr>`;
            }).join("");
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="8" class="table-empty">❌ ${_esc(err.message)}</td></tr>`;
        }
    }

    window.loadOperativosPagosEnviados = loadOperativosPagosEnviados;

    document.addEventListener("DOMContentLoaded", () => {
        document.getElementById("op-env-estado")?.addEventListener("change", loadOperativosPagosEnviados);
        let tEnv = null;
        document.getElementById("op-env-buscar")?.addEventListener("input", () => {
            clearTimeout(tEnv);
            tEnv = setTimeout(loadOperativosPagosEnviados, 280);
        });
        document.querySelectorAll(".op-env-kpi").forEach((k) => {
            k.addEventListener("click", () => {
                const s = document.getElementById("op-env-estado");
                if (s) {
                    s.value = k.dataset.opEstado || "";
                    loadOperativosPagosEnviados();
                }
            });
        });

        const pathSeg = window.location.pathname.toLowerCase().replace(/^\/+|\/+$/g, "").split("/")[0];
        if (pathSeg === "pagos" || pathSeg === "cobranza") {
            loadOperativosPagosEnviados();
        }
    });
})();
