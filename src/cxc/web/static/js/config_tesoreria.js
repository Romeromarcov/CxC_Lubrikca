/* Configuración -> Tesorería y Cuentas Zelle de Terceros (Proveedores) */
(function () {
    "use strict";

    let _ztCuentas = [];
    let _ztDiariosGen = [];
    let _ztCuentasOdoo = [];

    function _esc(v) {
        if (window.CxC && typeof window.CxC.esc === "function") return window.CxC.esc(v);
        return String(v == null ? "" : v);
    }

    async function _cargarOpcionesOdooTesoreria() {
        try {
            const [rDir, rCtas] = await Promise.all([
                fetch("/api/config/diarios-generales"),
                fetch("/api/odoo/plan-cuentas"),
            ]);
            if (rDir.ok) {
                const d = await rDir.json().catch(() => ({}));
                _ztDiariosGen = d.items || [];
                const sel = document.getElementById("zt-diario-id");
                if (sel) {
                    sel.innerHTML = '<option value="">(Diario de Operaciones Varias por defecto)</option>'
                        + _ztDiariosGen.map((j) => `<option value="${j.id}">${_esc(j.code ? j.code + " · " + j.name : j.name)}</option>`).join("");
                }
            }
            if (rCtas.ok) {
                const c = await rCtas.json().catch(() => ({}));
                _ztCuentasOdoo = c.cuentas || c.items || [];
                const selCxp = document.getElementById("zt-cxp-override");
                const selCxc = document.getElementById("zt-cxc-override");
                const optsCxp = _ztCuentasOdoo
                    .filter((a) => !a.account_type || a.account_type.includes("payable") || String(a.code || "").startsWith("2"))
                    .map((a) => `<option value="${a.id}">${_esc((a.code || "") + " · " + (a.name || ""))}</option>`).join("");
                const optsCxc = _ztCuentasOdoo
                    .filter((a) => !a.account_type || a.account_type.includes("receivable") || String(a.code || "").startsWith("1"))
                    .map((a) => `<option value="${a.id}">${_esc((a.code || "") + " · " + (a.name || ""))}</option>`).join("");
                if (selCxp) selCxp.innerHTML = '<option value="">(Automática de la ficha del Proveedor)</option>' + optsCxp;
                if (selCxc) selCxc.innerHTML = '<option value="">(Automática de la ficha del Cliente)</option>' + optsCxc;
            }
        } catch (_e) { /* silencioso */ }
    }

    function _limpiarFormularioZt() {
        document.getElementById("zt-id").value = "";
        document.getElementById("zt-alias").value = "";
        document.getElementById("zt-titular").value = "";
        document.getElementById("zt-correo").value = "";
        document.getElementById("zt-prov-buscar").value = "";
        document.getElementById("zt-prov-id").value = "";
        document.getElementById("zt-prov-nombre").value = "";
        document.getElementById("zt-diario-id").value = "";
        document.getElementById("zt-cxp-override").value = "";
        document.getElementById("zt-cxc-override").value = "";
        document.getElementById("zt-moneda").value = "USD";
        document.getElementById("zt-notas").value = "";
        document.getElementById("zt-form-titulo").textContent = "➕ Nueva Cuenta Zelle de Tercero (Proveedor)";
        document.getElementById("zt-cancelar").style.display = "none";
        document.getElementById("zt-status").textContent = "";
    }

    window.editarCuentaTercero = function (id) {
        const c = _ztCuentas.find((x) => x.cuenta_id === Number(id));
        if (!c) return;
        document.getElementById("zt-id").value = String(c.cuenta_id);
        document.getElementById("zt-alias").value = c.alias || "";
        document.getElementById("zt-titular").value = c.titular || "";
        document.getElementById("zt-correo").value = c.correo_telefono || "";
        document.getElementById("zt-prov-buscar").value = "";
        document.getElementById("zt-prov-id").value = String(c.proveedor_odoo_id || "");
        document.getElementById("zt-prov-nombre").value = c.proveedor_nombre || "";
        document.getElementById("zt-diario-id").value = c.diario_odoo_id ? String(c.diario_odoo_id) : "";
        document.getElementById("zt-cxp-override").value = c.cuenta_cxp_override_id ? String(c.cuenta_cxp_override_id) : "";
        document.getElementById("zt-cxc-override").value = c.cuenta_cxc_override_id ? String(c.cuenta_cxc_override_id) : "";
        document.getElementById("zt-moneda").value = c.moneda || "USD";
        document.getElementById("zt-notas").value = c.notas || "";
        document.getElementById("zt-form-titulo").textContent = `✏️ Editando Cuenta: ${c.alias}`;
        document.getElementById("zt-cancelar").style.display = "inline-block";
        document.getElementById("zt-status").textContent = "";
    };

    window.toggleEstadoCuentaTercero = async function (id, activoActual) {
        try {
            const res = await fetch(`/api/config/cuentas-terceros/${encodeURIComponent(id)}`, {
                method: activoActual ? "DELETE" : "PUT",
                headers: { "Content-Type": "application/json" },
                body: activoActual ? undefined : JSON.stringify({ activo: true }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || data.detail || `Error ${res.status}`);
            await loadConfigTesoreria();
            if (window.CxC && typeof window.CxC.recargarCatalogosOperativos === "function") {
                await window.CxC.recargarCatalogosOperativos();
            }
        } catch (err) {
            alert("⚠️ " + err.message);
        }
    };

    async function loadConfigTesoreria() {
        const tbody = document.getElementById("zt-table-body");
        if (!tbody) return;
        await _cargarOpcionesOdooTesoreria();
        try {
            const res = await fetch("/api/config/cuentas-terceros");
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
            _ztCuentas = data.items || [];
            if (!_ztCuentas.length) {
                tbody.innerHTML = '<tr><td colspan="6" class="table-empty">No hay cuentas Zelle de terceros configuradas todavía.</td></tr>';
                return;
            }
            tbody.innerHTML = _ztCuentas.map((c) => {
                const stBadge = c.activo
                    ? '<span class="semaphore verde">Activa</span>'
                    : '<span class="semaphore rojo">Inactiva</span>';
                const toggleLbl = c.activo ? "Desactivar" : "Reactivar";
                return `<tr>
                    <td><strong>${_esc(c.alias)}</strong><div class="cap-sub">${_esc(c.moneda)}</div></td>
                    <td><strong>${_esc(c.titular || "—")}</strong><div class="cap-sub">${_esc(c.correo_telefono || "")}</div></td>
                    <td><strong>${_esc(c.proveedor_nombre || ("Proveedor #" + c.proveedor_odoo_id))}</strong><div class="cap-sub">Odoo #${c.proveedor_odoo_id}</div></td>
                    <td>${_esc(c.diario_odoo_nombre || (c.diario_odoo_id ? ("Diario #" + c.diario_odoo_id) : "Operaciones Varias (auto)"))}</td>
                    <td>${stBadge}</td>
                    <td style="white-space:nowrap; display:flex; gap:4px;">
                        <button type="button" class="btn-secondary" style="padding:0.3rem 0.55rem; font-size:0.78rem;" onclick="editarCuentaTercero(${c.cuenta_id})">✏️ Editar</button>
                        <button type="button" class="btn-secondary" style="padding:0.3rem 0.55rem; font-size:0.78rem;" onclick="toggleEstadoCuentaTercero(${c.cuenta_id}, ${c.activo ? "true" : "false"})">${toggleLbl}</button>
                    </td>
                </tr>`;
            }).join("");
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="6" class="table-empty">❌ ${_esc(err.message)}</td></tr>`;
        }
    }

    window.loadConfigTesoreria = loadConfigTesoreria;

    document.addEventListener("DOMContentLoaded", () => {
        const inpProv = document.getElementById("zt-prov-buscar");
        const boxProv = document.getElementById("zt-prov-resultados");
        let tProv = null;
        if (inpProv && boxProv) {
            inpProv.addEventListener("input", () => {
                clearTimeout(tProv);
                const q = inpProv.value.trim();
                if (!q) { boxProv.style.display = "none"; return; }
                tProv = setTimeout(async () => {
                    try {
                        const r = await fetch(`/api/odoo/contactos?clasificacion=proveedor&q=${encodeURIComponent(q)}&limit=8`);
                        const data = await r.json().catch(() => ({}));
                        const items = data.items || [];
                        boxProv.innerHTML = items.length
                            ? items.map((p, idx) => `<div data-zt-prov="${idx}" style="padding:0.45rem 0.7rem; cursor:pointer; border-bottom:1px solid #f1f5f9; font-size:0.84rem;">
                                <strong>${_esc(p.nombre)}</strong> <span style="color:#64748b;">(${_esc(p.rif || ("#" + p.id))})</span></div>`).join("")
                            : '<div style="padding:0.45rem 0.7rem; color:#64748b; font-size:0.82rem;">Sin proveedores coincidentes</div>';
                        boxProv._items = items;
                        boxProv.style.display = "block";
                    } catch (_e) { boxProv.style.display = "none"; }
                }, 220);
            });
            boxProv.addEventListener("click", (e) => {
                const row = e.target.closest("[data-zt-prov]");
                if (!row || !boxProv._items) return;
                const p = boxProv._items[Number(row.dataset.ztProv)];
                if (!p) return;
                document.getElementById("zt-prov-id").value = String(p.id);
                document.getElementById("zt-prov-nombre").value = p.nombre || "";
                inpProv.value = p.nombre || "";
                boxProv.style.display = "none";
            });
        }

        document.getElementById("zt-cancelar")?.addEventListener("click", _limpiarFormularioZt);
        document.getElementById("zt-form")?.addEventListener("submit", async (e) => {
            e.preventDefault();
            const st = document.getElementById("zt-status");
            const idVal = document.getElementById("zt-id").value;
            const selDiario = document.getElementById("zt-diario-id");
            const diarioNombre = selDiario && selDiario.selectedOptions[0] && selDiario.value
                ? selDiario.selectedOptions[0].textContent : "";
            const body = {
                alias: document.getElementById("zt-alias").value.trim(),
                titular: document.getElementById("zt-titular").value.trim(),
                correo_telefono: document.getElementById("zt-correo").value.trim(),
                proveedor_odoo_id: document.getElementById("zt-prov-id").value ? Number(document.getElementById("zt-prov-id").value) : null,
                proveedor_nombre: document.getElementById("zt-prov-nombre").value.trim(),
                diario_odoo_id: selDiario.value ? Number(selDiario.value) : null,
                diario_odoo_nombre: diarioNombre,
                cuenta_cxp_override_id: document.getElementById("zt-cxp-override").value ? Number(document.getElementById("zt-cxp-override").value) : null,
                cuenta_cxc_override_id: document.getElementById("zt-cxc-override").value ? Number(document.getElementById("zt-cxc-override").value) : null,
                moneda: document.getElementById("zt-moneda").value || "USD",
                notas: document.getElementById("zt-notas").value.trim(),
            };
            st.style.color = "#1e40af";
            st.textContent = "Guardando cuenta de tercero...";
            try {
                const res = await fetch(
                    idVal ? `/api/config/cuentas-terceros/${encodeURIComponent(idVal)}` : "/api/config/cuentas-terceros",
                    {
                        method: idVal ? "PUT" : "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body),
                    },
                );
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.error || data.detail || `Error ${res.status}`);
                _limpiarFormularioZt();
                st.style.color = "#15803d";
                st.textContent = `✅ Cuenta «${data.alias}» guardada.`;
                await loadConfigTesoreria();
                if (window.CxC && typeof window.CxC.recargarCatalogosOperativos === "function") {
                    await window.CxC.recargarCatalogosOperativos();
                }
            } catch (err) {
                st.style.color = "#b91c1c";
                st.textContent = "⚠️ " + err.message;
            }
        });
    });
})();
