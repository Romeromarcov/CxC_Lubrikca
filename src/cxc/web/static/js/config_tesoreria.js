/* Configuración -> Tesorería y Cuentas Zelle de Terceros (Proveedores) */
(function () {
    "use strict";

    let _ztCuentas = [];
    let _ztDiariosGen = [];
    let _ztCuentasOdoo = [];
    let _crCuentas = [];
    let _crSudeban = [];
    let _crDiariosBancos = [];

    function _esc(v) {
        if (window.CxC && typeof window.CxC.esc === "function") return window.CxC.esc(v);
        return String(v == null ? "" : v);
    }

    function _limpiarFormularioCr() {
        const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
        setVal("cr-id", "");
        setVal("cr-nombre", "");
        setVal("cr-canal", "banco");
        setVal("cr-banco-sudeban", "");
        setVal("cr-numero-cuenta", "");
        setVal("cr-titular", "");
        setVal("cr-rif", "");
        setVal("cr-telefono", "");
        setVal("cr-email", "");
        setVal("cr-binance-id", "");
        setVal("cr-moneda", "VES");
        setVal("cr-diario-id", "");
        setVal("cr-notas", "");
        const tit = document.getElementById("cr-form-titulo");
        if (tit) tit.textContent = "➕ Nueva Cuenta Receptora Propia de Lubrikca";
        const btnCan = document.getElementById("cr-cancelar");
        if (btnCan) btnCan.style.display = "none";
        const st = document.getElementById("cr-status");
        if (st) st.textContent = "";
    }

    window.editarCuentaReceptora = function (id) {
        const c = _crCuentas.find((x) => x.cuenta_id === Number(id));
        if (!c) return;
        const setVal = (eid, v) => { const el = document.getElementById(eid); if (el) el.value = v; };
        setVal("cr-id", String(c.cuenta_id));
        setVal("cr-nombre", c.nombre || "");
        setVal("cr-canal", c.tipo_canal || "banco");
        setVal("cr-banco-sudeban", c.banco_codigo_sudeban || "");
        setVal("cr-numero-cuenta", c.numero_cuenta || "");
        setVal("cr-titular", c.titular_nombre || "");
        setVal("cr-rif", c.titular_rif || "");
        setVal("cr-telefono", c.telefono || "");
        setVal("cr-email", c.email_zelle || "");
        setVal("cr-binance-id", c.binance_id || "");
        setVal("cr-moneda", c.moneda || "VES");
        setVal("cr-diario-id", c.diario_odoo_id ? String(c.diario_odoo_id) : "");
        setVal("cr-notas", c.notas || "");
        const tit = document.getElementById("cr-form-titulo");
        if (tit) tit.textContent = `✏️ Editando Cuenta Propia: ${c.nombre}`;
        const btnCan = document.getElementById("cr-cancelar");
        if (btnCan) btnCan.style.display = "inline-block";
        const st = document.getElementById("cr-status");
        if (st) st.textContent = "";
    };

    window.toggleEstadoCuentaReceptora = async function (id, activaActual) {
        try {
            const res = await fetch(`/api/tesoreria/cuentas-receptoras/${encodeURIComponent(id)}`, {
                method: activaActual ? "DELETE" : "PUT",
                headers: { "Content-Type": "application/json" },
                body: activaActual ? undefined : JSON.stringify({ activa: true }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || data.detail || `Error ${res.status}`);
            await loadCuentasReceptoras();
        } catch (err) {
            alert("⚠️ " + err.message);
        }
    };

    async function loadCuentasReceptoras() {
        const tbody = document.getElementById("cr-table-body");
        if (!tbody) return;
        try {
            const [rCr, rCat] = await Promise.all([
                fetch("/api/tesoreria/cuentas-receptoras"),
                fetch("/api/operativos/catalogos"),
            ]);
            if (rCat.ok) {
                const cat = await rCat.json().catch(() => ({}));
                _crDiariosBancos = cat.diarios_bancarios || [];
                const selDir = document.getElementById("cr-diario-id");
                if (selDir) {
                    const prev = selDir.value;
                    selDir.innerHTML = '<option value="">(Sin diario Odoo vinculado)</option>'
                        + _crDiariosBancos.map((d) => `<option value="${d.id}">${_esc((d.codigo ? d.codigo + " · " : "") + d.nombre + " (" + d.moneda + ")")}</option>`).join("");
                    if (prev) selDir.value = prev;
                }
            }
            const data = await rCr.json().catch(() => ({}));
            if (!rCr.ok) throw new Error(data.detail || data.error || `Error ${rCr.status}`);
            _crCuentas = data.items || [];
            _crSudeban = data.bancos_sudeban || [];
            const selSud = document.getElementById("cr-banco-sudeban");
            if (selSud && _crSudeban.length) {
                const prevS = selSud.value;
                selSud.innerHTML = '<option value="">(N/A o Canal Internacional)</option>'
                    + _crSudeban.map((b) => `<option value="${_esc(b.codigo)}">${_esc(b.codigo + " · " + b.nombre)}</option>`).join("");
                if (prevS) selSud.value = prevS;
            }
            if (!_crCuentas.length) {
                tbody.innerHTML = '<tr><td colspan="6" class="table-empty">No hay cuentas receptoras propias configuradas todavía.</td></tr>';
                return;
            }
            tbody.innerHTML = _crCuentas.map((c) => {
                const stBadge = c.activa
                    ? '<span class="semaphore verde">Activa</span>'
                    : '<span class="semaphore rojo">Inactiva</span>';
                const toggleLbl = c.activa ? "Desactivar" : "Reactivar";
                const bancoTxt = c.banco_codigo_sudeban
                    ? `${c.banco_codigo_sudeban} · ${c.banco_nombre || ""}`
                    : (c.banco_nombre || "Canal Digital");
                const contactoPartes = [c.titular_nombre, c.titular_rif, c.telefono, c.email_zelle, c.binance_id].filter(Boolean);
                const diarioLbl = c.diario_odoo_nombre
                    ? `${c.diario_odoo_codigo ? c.diario_odoo_codigo + " · " : ""}${c.diario_odoo_nombre} (#${c.diario_odoo_id})`
                    : (c.diario_odoo_id ? `Diario #${c.diario_odoo_id}` : "Sin diario");
                return `<tr>
                    <td><strong>${_esc(c.nombre)}</strong><div class="cap-sub">${_esc((c.tipo_canal || "banco").toUpperCase())} · ${_esc(c.moneda)}</div></td>
                    <td><strong>${_esc(bancoTxt)}</strong><div class="cap-sub">${_esc(c.numero_cuenta || "Sin número de cuenta")}</div></td>
                    <td><div class="cap-sub">${_esc(contactoPartes.join(" · ") || "Sin datos de titular")}</div></td>
                    <td>${_esc(diarioLbl)}</td>
                    <td>${stBadge}</td>
                    <td style="white-space:nowrap; display:flex; gap:4px;">
                        <button type="button" class="btn-secondary" style="padding:0.3rem 0.55rem; font-size:0.78rem;" onclick="editarCuentaReceptora(${c.cuenta_id})">✏️ Editar</button>
                        <button type="button" class="btn-secondary" style="padding:0.3rem 0.55rem; font-size:0.78rem;" onclick="toggleEstadoCuentaReceptora(${c.cuenta_id}, ${c.activa ? "true" : "false"})">${toggleLbl}</button>
                    </td>
                </tr>`;
            }).join("");
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="6" class="table-empty">❌ ${_esc(err.message)}</td></tr>`;
        }
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
        await loadCuentasReceptoras();
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
        document.getElementById("cr-cancelar")?.addEventListener("click", _limpiarFormularioCr);
        document.getElementById("cr-btn-sync")?.addEventListener("click", async () => {
            const st = document.getElementById("cr-status");
            if (st) {
                st.style.color = "#1e40af";
                st.textContent = "Sincronizando diarios de Odoo y seed inicial...";
            }
            try {
                const res = await fetch("/api/tesoreria/cuentas-receptoras/sincronizar-odoo", { method: "POST" });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
                const s = data.sync_odoo || {};
                if (st) {
                    st.style.color = data.warning ? "#b45309" : "#15803d";
                    st.textContent = data.warning
                        ? `⚠️ Sembradas: ${data.sembradas || 0}. ${data.warning}`
                        : `✅ Sincronización lista (sembradas: ${data.sembradas || 0}, nuevas Odoo: ${s.creadas || 0}, actualizadas: ${s.actualizadas || 0}).`;
                }
                await loadCuentasReceptoras();
            } catch (err) {
                if (st) {
                    st.style.color = "#b91c1c";
                    st.textContent = "⚠️ " + err.message;
                }
            }
        });
        document.getElementById("cr-form")?.addEventListener("submit", async (e) => {
            e.preventDefault();
            const st = document.getElementById("cr-status");
            const idVal = (document.getElementById("cr-id") || {}).value;
            const selSud = document.getElementById("cr-banco-sudeban");
            const sudCode = selSud ? selSud.value : "";
            const sudObj = _crSudeban.find((b) => b.codigo === sudCode);
            const selDir = document.getElementById("cr-diario-id");
            const dirIdVal = selDir && selDir.value ? Number(selDir.value) : null;
            const dirObj = _crDiariosBancos.find((d) => Number(d.id) === dirIdVal);
            const body = {
                nombre: document.getElementById("cr-nombre").value.trim(),
                tipo_canal: document.getElementById("cr-canal").value || "banco",
                banco_codigo_sudeban: sudCode,
                banco_nombre: sudObj ? sudObj.nombre : "",
                numero_cuenta: document.getElementById("cr-numero-cuenta").value.trim(),
                titular_nombre: document.getElementById("cr-titular").value.trim(),
                titular_rif: document.getElementById("cr-rif").value.trim(),
                telefono: document.getElementById("cr-telefono").value.trim(),
                email_zelle: document.getElementById("cr-email").value.trim(),
                binance_id: document.getElementById("cr-binance-id").value.trim(),
                moneda: document.getElementById("cr-moneda").value || "VES",
                diario_odoo_id: dirIdVal,
                diario_odoo_codigo: dirObj ? (dirObj.codigo || "") : "",
                diario_odoo_nombre: dirObj ? (dirObj.nombre || "") : "",
                notas: document.getElementById("cr-notas").value.trim(),
                es_propia: true,
            };
            if (st) {
                st.style.color = "#1e40af";
                st.textContent = "Guardando cuenta receptora...";
            }
            try {
                const res = await fetch(
                    idVal ? `/api/tesoreria/cuentas-receptoras/${encodeURIComponent(idVal)}` : "/api/tesoreria/cuentas-receptoras",
                    {
                        method: idVal ? "PUT" : "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body),
                    },
                );
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.error || data.detail || `Error ${res.status}`);
                _limpiarFormularioCr();
                if (st) {
                    st.style.color = "#15803d";
                    st.textContent = `✅ Cuenta receptora «${data.nombre}» guardada.`;
                }
                await loadCuentasReceptoras();
            } catch (err) {
                if (st) {
                    st.style.color = "#b91c1c";
                    st.textContent = "⚠️ " + err.message;
                }
            }
        });

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
