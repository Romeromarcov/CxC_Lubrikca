/* Submódulo Pagos -> Enviados Unificados (CxC PPR-* + Espejo Odoo outbound) */
(function () {
    "use strict";

    function _cxc() {
        return window.CxC || {};
    }

    function _esc(v) {
        return typeof _cxc().esc === "function" ? _cxc().esc(v) : String(v == null ? "" : v);
    }

    function _monto(v) {
        return typeof _cxc().monto === "function" ? _cxc().monto(v) : String(v ?? "—");
    }

    async function loadOperativosPagosEnviados() {
        const tbody = document.getElementById("op-pagos-enviados-table-body");
        if (!tbody) return;
        const origen = (document.getElementById("op-env-origen") || {}).value || "todos";
        const estado = (document.getElementById("op-env-estado") || {}).value || "";
        const q = ((document.getElementById("op-env-buscar") || {}).value || "").trim();
        const qs = new URLSearchParams({ direccion: "enviados", origen, limit: "150" });
        if (estado) qs.set("estado", estado);
        if (q) qs.set("q", q);

        try {
            const res = await fetch(`/api/pagos/unificados?${qs}`);
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);

            const puedeAprobar = Boolean(data.puede_aprobar_compras);
            const r = data.resumen || {};
            const badge = document.getElementById("badge-op-enviados-pend");
            if (badge) badge.textContent = String((r.pendientes_aprobacion || 0) + (r.confirmados_cxc || 0));

            for (const [idEl, val] of [
                ["op-env-n-PENDIENTE_APROBACION", r.pendientes_aprobacion || 0],
                ["op-env-n-CONFIRMADO_CXC", r.confirmados_cxc || 0],
                ["op-env-n-INCOMPLETO", r.incompletos || 0],
                ["op-env-n-ENVIADO_ODOO", (r.enviados_odoo || 0) + (r.confirmados_odoo || 0)],
                ["op-env-n-RECHAZADO", r.rechazados || 0],
            ]) {
                const el = document.getElementById(idEl);
                if (el) el.textContent = String(val);
            }

            const resEl = document.getElementById("op-env-resumen");
            if (resEl) {
                resEl.textContent = `${data.total || 0} pago(s) enviado(s) · Origen: ${r.origen_cxc || 0} CxC · ${r.origen_odoo || 0} Odoo`;
            }

            const items = data.items || [];
            if (!items.length) {
                tbody.innerHTML = '<tr><td colspan="8" class="table-empty">No hay pagos a proveedores con estos filtros.</td></tr>';
                return;
            }

            const pagosApi = _cxc().pagos || {};
            tbody.innerHTML = items.map((d) => {
                const refTxt = d.es_efectivo
                    ? '<span style="color:#0369a1; font-weight:600;">Efectivo (memo vacío)</span>'
                    : _esc(d.referencia || "—");
                const subRef = d.factura_ref ? `Fac: ${_esc(d.factura_ref)}` : _esc(d.metodo_pago || "");
                const origHtml = typeof pagosApi.origenChip === "function"
                    ? pagosApi.origenChip(d)
                    : _esc(d.origen_sistema || d.origen || "");
                const estHtml = typeof pagosApi.estadoBadge === "function"
                    ? pagosApi.estadoBadge(d)
                    : _esc(d.estado);
                const alHtml = typeof _cxc().alertasHtml === "function" ? _cxc().alertasHtml(d) : "";
                const actHtml = typeof pagosApi.accionesHtml === "function"
                    ? pagosApi.accionesHtml(d, puedeAprobar, puedeAprobar)
                    : "";
                return `<tr>
                    <td><strong>${_esc(d.numero)}</strong><div>${origHtml}</div></td>
                    <td>${_esc(d.fecha_documento || "—")}</td>
                    <td><strong>${_esc(d.contraparte_nombre || "Sin proveedor")}</strong><div class="cap-sub">${_esc(d.rif || (d.partner_odoo_id ? ("Odoo #" + d.partner_odoo_id) : "Se creará al aprobar"))}</div></td>
                    <td><strong>${d.monto != null ? _monto(d.monto) : "—"} ${_esc(d.moneda)}</strong></td>
                    <td>${_esc(d.diario_nombre || d.banco || "Por definir")}</td>
                    <td>${refTxt}${subRef ? `<div class="cap-sub">${subRef}</div>` : ""}</td>
                    <td>${estHtml}${alHtml}</td>
                    <td style="white-space:nowrap; display:flex; gap:4px; flex-wrap:wrap;">${actHtml}</td>
                </tr>`;
            }).join("");
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="8" class="table-empty">❌ ${_esc(err.message)}</td></tr>`;
        }
    }

    window.loadOperativosPagosEnviados = loadOperativosPagosEnviados;

    document.addEventListener("DOMContentLoaded", () => {
        document.getElementById("op-env-origen")?.addEventListener("change", loadOperativosPagosEnviados);
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
