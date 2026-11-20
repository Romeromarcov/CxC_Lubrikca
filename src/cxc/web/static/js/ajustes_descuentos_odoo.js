/* Ajustes temporales por regla y descuentos especiales sobre órdenes existentes de Odoo */
(function () {
    "use strict";

    let _soActual = "";
    let _modalData = null;

    function _esc(v) {
        if (window.CxC && typeof window.CxC.esc === "function") return window.CxC.esc(v);
        return String(v == null ? "" : v)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function _fmtUsd(v) {
        const n = Number(v || 0);
        return Number.isFinite(n)
            ? `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
            : "$0.00";
    }

    function _refrescarVistasVentas() {
        if (typeof window.loadVentasUnificadas === "function") window.loadVentasUnificadas();
        if (typeof window.loadVentas === "function") window.loadVentas();
    }

    function _renderModal(data) {
        _modalData = data;
        const titEl = document.getElementById("aj-desc-titulo");
        const subEl = document.getElementById("aj-desc-subtitulo");
        const kpiEl = document.getElementById("aj-desc-kpis");
        const bloqEl = document.getElementById("aj-desc-bloqueo");
        const motorTbody = document.getElementById("aj-desc-motor-tbody");
        const espTbody = document.getElementById("aj-desc-especiales-tbody");
        const formEsp = document.getElementById("aj-desc-form-especial");

        if (titEl) {
            titEl.textContent = `Ajuste de Descuentos · Orden ${data.so_id || ""}`;
        }
        if (subEl) {
            const oidTxt = data.odoo_order_id ? `Odoo ID #${data.odoo_order_id} · ` : "";
            subEl.textContent = `${oidTxt}Fecha: ${data.fecha || "—"} · Base imponible referencia: ${_fmtUsd(data.subtotal_base_usd)}`;
        }
        const r = data.resumen || {};
        if (kpiEl) {
            kpiEl.innerHTML = `
                <div class="card kpi-card" style="padding:0.65rem 0.85rem;"><h3>Motor Original</h3><p>${_fmtUsd(r.total_motor_original)}</p></div>
                <div class="card kpi-card" style="padding:0.65rem 0.85rem;"><h3>Excluidos (${r.count_excluidos || 0})</h3><p style="color:#dc2626;">-${_fmtUsd(r.total_excluido)}</p></div>
                <div class="card kpi-card" style="padding:0.65rem 0.85rem;"><h3>Especiales (${r.count_especiales || 0})</h3><p style="color:#2563eb;">+${_fmtUsd(r.total_especial)}</p></div>
                <div class="card kpi-card" style="padding:0.65rem 0.85rem;"><h3>Descuento Neto Ajustado</h3><p style="color:#15803d;">${_fmtUsd(r.total_descuento_ajustado)}</p></div>
            `;
        }

        const editable = Boolean(data.puede_editar);
        if (bloqEl) {
            if (!editable) {
                bloqEl.style.display = "block";
                bloqEl.textContent = `🔒 Solo lectura: ${data.motivo_bloqueo || "Ajustes deshabilitados."}`;
            } else {
                bloqEl.style.display = "none";
                bloqEl.textContent = "";
            }
        }
        if (formEsp) {
            formEsp.querySelectorAll("input, select, button").forEach((el) => {
                el.disabled = !editable;
            });
        }

        const motorItems = data.motor_items || [];
        if (motorTbody) {
            if (!motorItems.length) {
                motorTbody.innerHTML = '<tr><td colspan="6" class="table-empty">El motor no calculó descuentos sugeridos para esta orden.</td></tr>';
            } else {
                motorTbody.innerHTML = motorItems.map((m) => {
                    const pctTxt = m.porcentaje != null ? `${(Number(m.porcentaje) * 100).toFixed(2)}%` : "—";
                    const stHtml = m.excluido
                        ? `<span class="semaphore rojo">✖ Excluido</span><div class="cap-sub">${_esc(m.motivo_exclusion || "")}</div>`
                        : '<span class="semaphore verde">✓ Vigente</span>';
                    let btnHtml = '<span class="cap-sub">—</span>';
                    if (editable) {
                        if (m.excluido && m.ajuste_exclusion_id) {
                            btnHtml = `<button type="button" class="btn-secondary" style="padding:0.28rem 0.55rem; font-size:0.78rem; color:#15803d;" data-aj-restaurar="${m.ajuste_exclusion_id}">↩ Restaurar</button>`;
                        } else if (!m.excluido) {
                            btnHtml = `<button type="button" class="btn-secondary" style="padding:0.28rem 0.55rem; font-size:0.78rem; color:#991b1b;" data-aj-excluir-org="${_esc(m.origen)}" data-aj-excluir-rid="${_esc(m.regla_id)}">✖ Quitar solo este</button>`;
                        }
                    }
                    return `<tr style="${m.excluido ? "opacity:0.65; text-decoration:line-through;" : ""}">
                        <td><strong>${_esc(m.origen || "—")}</strong></td>
                        <td><code>${_esc(m.regla_id || "—")}</code></td>
                        <td>${_esc(m.descripcion || "—")}</td>
                        <td style="text-align:right;">${pctTxt}</td>
                        <td style="text-align:right;"><strong>${_fmtUsd(m.monto_usd)}</strong></td>
                        <td>${stHtml}</td>
                        <td>${btnHtml}</td>
                    </tr>`;
                }).join("");
            }
        }

        const especiales = data.especiales || [];
        if (espTbody) {
            if (!especiales.length) {
                espTbody.innerHTML = '<tr><td colspan="6" class="table-empty">Sin descuentos comerciales especiales agregados.</td></tr>';
            } else {
                espTbody.innerHTML = especiales.map((e) => {
                    const valorTxt = e.tipo_valor === "porcentaje"
                        ? `${Number(e.porcentaje || 0).toFixed(2)}% s/ base`
                        : `Fijo ${_fmtUsd(e.monto_usd)}`;
                    const btnDel = editable
                        ? `<button type="button" class="btn-secondary" style="padding:0.28rem 0.55rem; font-size:0.78rem; color:#991b1b;" data-aj-restaurar="${e.ajuste_id}">🗑 Quitar</button>`
                        : "—";
                    return `<tr>
                        <td><strong>${_esc(e.descripcion || "Descuento especial")}</strong></td>
                        <td>${_esc(valorTxt)}</td>
                        <td style="text-align:right;"><strong style="color:#2563eb;">${_fmtUsd(e.monto_calculado_usd)}</strong></td>
                        <td>${_esc(e.motivo || "—")}</td>
                        <td><small>${_esc(e.creado_por || "—")}</small></td>
                        <td>${btnDel}</td>
                    </tr>`;
                }).join("");
            }
        }
    }

    async function abrirModalAjustesDescuentos(soId) {
        const modal = document.getElementById("modal-ajustes-descuentos-orden");
        const stEl = document.getElementById("aj-desc-status");
        if (!modal || !soId) return;
        _soActual = String(soId);
        if (stEl) stEl.textContent = "";
        modal.style.display = "flex";
        try {
            const res = await fetch(`/api/ordenes/${encodeURIComponent(_soActual)}/ajustes-descuentos`);
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
            _renderModal(data);
        } catch (err) {
            if (stEl) {
                stEl.style.color = "#dc2626";
                stEl.textContent = `❌ ${err.message}`;
            }
        }
    }

    function cerrarModalAjustesDescuentos() {
        const modal = document.getElementById("modal-ajustes-descuentos-orden");
        if (modal) modal.style.display = "none";
        _soActual = "";
        _modalData = null;
    }

    async function _excluirReglaMotor(origen, reglaId) {
        if (!_soActual) return;
        const motivo = window.prompt(
            `Motivo para quitar únicamente «${origen || reglaId}» de ${_soActual}:`,
            "No otorgado en negociación comercial",
        );
        if (motivo === null) return;
        const stEl = document.getElementById("aj-desc-status");
        try {
            const res = await fetch(
                `/api/ordenes/${encodeURIComponent(_soActual)}/ajustes-descuentos/excluir-motor`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        descuento_origen: origen || "",
                        regla_id: reglaId || "",
                        motivo: motivo.trim(),
                    }),
                },
            );
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
            _renderModal(data);
            if (stEl) {
                stEl.style.color = "#15803d";
                stEl.textContent = "✅ Regla excluida correctamente (los demás descuentos se mantienen).";
            }
            _refrescarVistasVentas();
        } catch (err) {
            if (stEl) {
                stEl.style.color = "#dc2626";
                stEl.textContent = `❌ ${err.message}`;
            }
        }
    }

    async function _anularAjuste(ajusteId) {
        if (!_soActual || !ajusteId) return;
        const stEl = document.getElementById("aj-desc-status");
        try {
            const res = await fetch(
                `/api/ordenes/${encodeURIComponent(_soActual)}/ajustes-descuentos/${encodeURIComponent(ajusteId)}`,
                { method: "DELETE" },
            );
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
            _renderModal(data);
            if (stEl) {
                stEl.style.color = "#15803d";
                stEl.textContent = "✅ Ajuste actualizado correctamente.";
            }
            _refrescarVistasVentas();
        } catch (err) {
            if (stEl) {
                stEl.style.color = "#dc2626";
                stEl.textContent = `❌ ${err.message}`;
            }
        }
    }

    async function _guardarEspecial(ev) {
        ev.preventDefault();
        if (!_soActual) return;
        const tipoVal = (document.getElementById("aj-esp-tipo") || {}).value || "porcentaje";
        const numVal = Number((document.getElementById("aj-esp-valor") || {}).value || 0);
        const desc = ((document.getElementById("aj-esp-desc") || {}).value || "").trim();
        const motivo = ((document.getElementById("aj-esp-motivo") || {}).value || "").trim();
        const stEl = document.getElementById("aj-desc-status");

        const payload = {
            tipo_valor: tipoVal,
            porcentaje: tipoVal === "porcentaje" ? numVal : null,
            monto_usd: tipoVal === "monto_fijo" ? numVal : null,
            descripcion: desc,
            motivo,
        };
        try {
            const res = await fetch(
                `/api/ordenes/${encodeURIComponent(_soActual)}/ajustes-descuentos/especial`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                },
            );
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
            document.getElementById("aj-desc-form-especial")?.reset();
            _renderModal(data);
            if (stEl) {
                stEl.style.color = "#15803d";
                stEl.textContent = "✅ Descuento comercial especial agregado.";
            }
            _refrescarVistasVentas();
        } catch (err) {
            if (stEl) {
                stEl.style.color = "#dc2626";
                stEl.textContent = `❌ ${err.message}`;
            }
        }
    }

    async function cargarToggleLimpiezaDescuentos() {
        const chk = document.getElementById("cfg-limpieza-descuentos-odoo");
        const stEl = document.getElementById("cfg-limpieza-descuentos-status");
        if (!chk) return;
        try {
            const res = await fetch("/api/config/limpieza-descuentos-odoo");
            if (!res.ok) return;
            const data = await res.json();
            chk.checked = Boolean(data.activa);
            if (stEl) {
                stEl.style.color = data.activa ? "#15803d" : "#64748b";
                stEl.textContent = data.activa
                    ? "🔓 Limpieza activa: se pueden ajustar reglas y descuentos especiales en órdenes Odoo existentes."
                    : "🔒 Período de limpieza cerrado (solo lectura histórica).";
            }
        } catch (_e) { /* ignorar */ }
    }

    async function guardarToggleLimpiezaDescuentos() {
        const chk = document.getElementById("cfg-limpieza-descuentos-odoo");
        const stEl = document.getElementById("cfg-limpieza-descuentos-status");
        if (!chk) return;
        try {
            const res = await fetch("/api/config/limpieza-descuentos-odoo", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ activa: Boolean(chk.checked) }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
            if (stEl) {
                stEl.style.color = data.activa ? "#15803d" : "#92400e";
                stEl.textContent = data.activa
                    ? "✅ Guardado: ajustes de descuentos Odoo existentes HABILITADOS."
                    : "✅ Guardado: limpieza finalizada — órdenes Odoo existentes CONGELADAS.";
            }
            _refrescarVistasVentas();
        } catch (err) {
            if (stEl) {
                stEl.style.color = "#dc2626";
                stEl.textContent = `❌ ${err.message}`;
            }
        }
    }

    window.abrirModalAjustesDescuentos = abrirModalAjustesDescuentos;
    window.cerrarModalAjustesDescuentos = cerrarModalAjustesDescuentos;
    window.cargarToggleLimpiezaDescuentos = cargarToggleLimpiezaDescuentos;
    window.guardarToggleLimpiezaDescuentos = guardarToggleLimpiezaDescuentos;

    document.addEventListener("DOMContentLoaded", () => {
        const modal = document.getElementById("modal-ajustes-descuentos-orden");
        modal?.addEventListener("click", (ev) => {
            const btnEx = ev.target.closest("[data-aj-excluir-org], [data-aj-excluir-rid]");
            if (btnEx) {
                _excluirReglaMotor(
                    btnEx.getAttribute("data-aj-excluir-org") || "",
                    btnEx.getAttribute("data-aj-excluir-rid") || "",
                );
                return;
            }
            const btnRest = ev.target.closest("[data-aj-restaurar]");
            if (btnRest) {
                _anularAjuste(btnRest.getAttribute("data-aj-restaurar"));
                return;
            }
            if (ev.target === modal) cerrarModalAjustesDescuentos();
        });

        document.getElementById("aj-desc-form-especial")?.addEventListener("submit", _guardarEspecial);
        document.getElementById("cfg-btn-guardar-limpieza-desc")?.addEventListener("click", guardarToggleLimpiezaDescuentos);
        cargarToggleLimpiezaDescuentos();
    });
})();
