/* Modal reutilizable de vinculación bidireccional Pagos ↔ Ventas y Compras/Gastos (FIFO + Odoo) */
(function () {
    "use strict";

    let _ctxActual = null;
    let _onSuccessCallback = null;

    function _esc(v) {
        if (window.CxC && typeof window.CxC.esc === "function") return window.CxC.esc(v);
        return String(v == null ? "" : v)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function _usd(v) {
        if (v == null) return "—";
        const n = Number(v);
        return Number.isFinite(n)
            ? `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
            : "—";
    }

    function _num(v) {
        const n = Number(v || 0);
        return Number.isFinite(n) ? n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "0.00";
    }

    function cerrarModalVincularDoc() {
        const modal = document.getElementById("modal-vincular-pago-doc");
        if (modal) modal.style.display = "none";
        _ctxActual = null;
    }

    function _setBanner(tipo, msg) {
        const box = document.getElementById("vinc-doc-status");
        if (!box) return;
        if (!msg) {
            box.style.display = "none";
            box.innerHTML = "";
            return;
        }
        const colores = {
            ok: ["#ecfdf5", "#10b981", "#065f46", "✅"],
            warn: ["#fffbeb", "#f59e0b", "#92400e", "⚠️"],
            err: ["#fff1f2", "#e11d48", "#9f1239", "❌"],
            info: ["#eff6ff", "#3b82f6", "#1e40af", "ℹ️"],
        }[tipo] || ["#f8fafc", "#cbd5e1", "#334155", "ℹ️"];
        box.style.display = "block";
        box.style.background = colores[0];
        box.style.borderLeft = `4px solid ${colores[1]}`;
        box.style.color = colores[2];
        box.style.padding = "0.65rem 0.9rem";
        box.style.borderRadius = "6px";
        box.style.fontSize = "0.86rem";
        box.innerHTML = `${colores[3]} ${_esc(msg)}`;
    }

    function _renderResumenCabecera(data) {
        const box = document.getElementById("vinc-doc-resumen");
        if (!box) return;
        if (_ctxActual.modo === "venta_doc" || _ctxActual.modo === "compra_doc") {
            const doc = data.documento || {};
            const nom = doc.cliente_nombre || doc.proveedor_nombre || "—";
            const ref = doc.so_id || doc.compra_ref || doc.numero || "—";
            const rutaBadge = doc.ruta_pago_label
                ? `<div><span class="cap-sub">Ruta Obligatoria</span><strong style="color:${doc.ruta_pago === "binance" ? "#92400e" : "#1e40af"};">🔒 ${_esc(doc.ruta_pago_label)}</strong></div>`
                : "";
            box.innerHTML = `
                <div><span class="cap-sub">Documento</span><strong>${_esc(ref)}</strong></div>
                <div><span class="cap-sub">Contraparte</span><strong>${_esc(nom)}</strong></div>
                ${rutaBadge}
                <div><span class="cap-sub">Total Documento</span><strong>${_usd(doc.total_usd)}</strong></div>
                <div><span class="cap-sub">Ya Vinculado</span><strong style="color:#0369a1;">${_usd(doc.vinculado_usd)}</strong></div>
                <div><span class="cap-sub">Saldo Pendiente</span><strong style="color:${Number(doc.saldo_pendiente_usd || 0) > 0 ? "#b45309" : "#15803d"};">${_usd(doc.saldo_pendiente_usd)}</strong></div>
            `;
            return;
        }
        const pag = data.pago || {};
        const nom = pag.cliente_nombre || pag.proveedor_nombre || "—";
        const mon = pag.moneda || "USD";
        box.innerHTML = `
            <div><span class="cap-sub">Pago</span><strong>${_esc(pag.numero || "—")}</strong></div>
            <div><span class="cap-sub">Contraparte</span><strong>${_esc(nom)}</strong></div>
            <div><span class="cap-sub">Fecha</span><strong>${_esc(pag.fecha || "—")}</strong></div>
            <div><span class="cap-sub">Monto Original</span><strong>${_num(pag.monto_original)} ${_esc(mon)}</strong> <span class="cap-sub">(${_usd(pag.monto_usd)})</span></div>
            <div><span class="cap-sub">Disponible</span><strong style="color:${Number(pag.disponible_original || 0) > 0 ? "#15803d" : "#64748b"};">${_num(pag.disponible_original)} ${_esc(mon)}</strong></div>
        `;
    }

    function _renderActuales(data) {
        const tbody = document.getElementById("vinc-doc-actuales-body");
        if (!tbody) return;
        const vincs = data.vinculaciones || [];
        if (!vincs.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="table-empty">Sin pagos vinculados todavía.</td></tr>';
            return;
        }
        tbody.innerHTML = vincs.map((v) => {
            const ref = _ctxActual.modo.endsWith("_pago")
                ? _esc(v.so_id || v.compra_ref || "—")
                : _esc(v.pago_ref || (v.pago_odoo_id ? `Odoo #${v.pago_odoo_id}` : `Op #${v.pago_operativo_id}`));
            const estBadge = v.conciliado_odoo
                ? '<span class="semaphore verde">🟣 Reconciliado Odoo</span>'
                : '<span class="semaphore amarillo">🔗 Vinculado CxC</span>';
            const btnDel = v.puede_desvincular
                ? `<button type="button" class="btn-secondary" style="padding:2px 8px; font-size:0.76rem; color:#991b1b;" data-vinc-del="1" data-vinc-id="${_esc(v.vinc_id)}" data-vinc-org="${_esc(v.origen_registro || "compra")}">✖ Quitar</button>`
                : '<span class="cap-sub">Bloqueado (Odoo)</span>';
            return `<tr>
                <td><strong>${ref}</strong>${v.odoo_detalle ? `<div class="cap-sub">${_esc(v.odoo_detalle)}</div>` : ""}</td>
                <td><strong>${_num(v.monto_aplicado)} ${_esc(v.moneda || "USD")}</strong></td>
                <td>${_usd(v.equiv_usd_bcv)}</td>
                <td>${estBadge}</td>
                <td class="cap-sub">${_esc(v.vinculado_por || "—")}</td>
                <td>${btnDel}</td>
            </tr>`;
        }).join("");
    }

    function _renderCandidatos(data) {
        const tbody = document.getElementById("vinc-doc-cand-body");
        const titEl = document.getElementById("vinc-doc-cand-titulo");
        if (!tbody) return;
        const cands = data.candidatos || [];
        if (titEl) {
            titEl.textContent = _ctxActual.modo.endsWith("_pago")
                ? "Órdenes / Facturas Abiertas (Sugerencia FIFO primero)"
                : "Pagos Disponibles (Sugerencia FIFO primero)";
        }
        if (!cands.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="table-empty">No hay registros abiertos con saldo disponible para vincular.</td></tr>';
            return;
        }
        tbody.innerHTML = cands.map((c, idx) => {
            const compatible = c.compatible_ruta !== false;
            const esFifo = compatible && Boolean(c.sugerido_fifo);
            const rowBg = !compatible ? "background:#fef2f2; opacity:0.75;" : (esFifo ? "background:#f0fdf4;" : "");
            const badgeFifo = esFifo
                ? '<div><span class="state-badge" style="background:#dcfce7; color:#166534; font-size:0.7rem;">⚡ Sugerido FIFO</span></div>'
                : "";
            const badgeIncomp = !compatible
                ? `<div style="color:#991b1b; font-size:0.74rem; font-weight:600; margin-top:2px;">⛔ ${_esc(c.motivo_incompatibilidad || "Ruta incompatible")}</div>`
                : "";
            const sugVal = Number(c.monto_sugerido_original || 0);
            const esDesdePago = _ctxActual.modo === "venta_pago" || _ctxActual.modo === "compra_pago";
            const pagMon = esDesdePago
                ? (data.pago ? data.pago.moneda : "USD")
                : (c.moneda || "USD");
            let defAmt = 0;
            if (sugVal > 0) {
                defAmt = sugVal;
            } else if (esDesdePago) {
                const dispOrig = Number(data.pago?.disponible_original || 0);
                defAmt = String(pagMon).toUpperCase() === "USD"
                    ? Math.min(dispOrig, Number(c.saldo_pendiente_usd || 0))
                    : dispOrig;
            } else {
                defAmt = Number(c.disponible_original != null ? c.disponible_original : (c.saldo_pendiente_usd || 0));
            }

            let tituloCol;
            let saldoCol;
            if (_ctxActual.modo === "venta_pago") {
                const rutaC = c.ruta_pago_label ? `<div class="cap-sub">🔒 ${_esc(c.ruta_pago_label)}</div>` : "";
                tituloCol = `<strong>${_esc(c.so_id)}</strong>${c.numero_cxc ? `<div class="cap-sub">CxC: ${_esc(c.numero_cxc)}</div>` : ""}${rutaC}`;
                saldoCol = `<strong>${_usd(c.saldo_pendiente_usd)}</strong><div class="cap-sub">de ${_usd(c.total_usd)}</div>`;
            } else if (_ctxActual.modo === "compra_pago") {
                tituloCol = `<strong>${_esc(c.compra_ref || c.numero)}</strong><div class="cap-sub">${_esc(c.tipo)}</div>`;
                saldoCol = `<strong>${_usd(c.saldo_pendiente_usd)}</strong><div class="cap-sub">de ${_usd(c.total_usd)}</div>`;
            } else {
                const odLbl = c.odoo_numero ? `<div class="cap-sub">🔗 ${_esc(c.odoo_numero)}</div>` : "";
                const refLbl = c.referencia ? `<div class="cap-sub">Ref: ${_esc(c.referencia)}</div>` : "";
                tituloCol = `<strong>${_esc(c.numero)}</strong>${odLbl}${refLbl}`;
                saldoCol = `<strong>${_num(c.disponible_original)} ${_esc(c.moneda || "USD")}</strong><div class="cap-sub">≈ ${_usd(c.disponible_usd)} · ${_esc(c.estado_pago || "")}</div>`;
            }

            return `<tr style="${rowBg}">
                <td>${tituloCol}${badgeFifo}${badgeIncomp}</td>
                <td>${_esc(c.fecha || "—")}</td>
                <td>${saldoCol}</td>
                <td>
                    <input type="number" step="0.01" min="0.01" id="vinc-amt-${idx}" value="${defAmt > 0 ? defAmt.toFixed(2) : ""}" ${compatible ? "" : "disabled"} style="width:115px; padding:4px 6px;" class="filter-select">
                    <span class="cap-sub">${_esc(pagMon)}</span>
                </td>
                <td>
                    <button type="button" class="btn-primary" style="padding:0.35rem 0.7rem; font-size:0.8rem; background:${!compatible ? "#94a3b8" : (esFifo ? "#16a34a" : "#2563eb")};" ${compatible ? "" : "disabled"} data-vinc-apply="${idx}" data-vinc-fifo="${esFifo ? "1" : "0"}">🔗 Vincular</button>
                </td>
            </tr>`;
        }).join("");
        tbody._candidatos = cands;
        tbody._meta = data;
    }

    async function _cargarDatosModal() {
        if (!_ctxActual) return;
        const tbodyCand = document.getElementById("vinc-doc-cand-body");
        if (tbodyCand) {
            tbodyCand.innerHTML = '<tr><td colspan="5" class="table-empty">Consultando candidatos FIFO...</td></tr>';
        }
        let url = "";
        const qs = new URLSearchParams();
        if (_ctxActual.modo === "venta_doc") {
            if (_ctxActual.soId) qs.set("so_id", _ctxActual.soId);
            if (_ctxActual.docId) qs.set("documento_id", String(_ctxActual.docId));
            url = `/api/ventas/vinculaciones/candidatos?${qs}`;
        } else if (_ctxActual.modo === "venta_pago") {
            if (_ctxActual.pagoId) qs.set("pago_id", String(_ctxActual.pagoId));
            if (_ctxActual.pagoOpId) qs.set("pago_operativo_id", String(_ctxActual.pagoOpId));
            url = `/api/pagos/vinculaciones/ordenes-candidatas?${qs}`;
        } else if (_ctxActual.modo === "compra_doc") {
            if (_ctxActual.compraId) qs.set("compra_operativo_id", String(_ctxActual.compraId));
            if (_ctxActual.moveId) qs.set("compra_odoo_move_id", String(_ctxActual.moveId));
            url = `/api/compras/vinculaciones/candidatos?${qs}`;
        } else {
            if (_ctxActual.pagoOpId) qs.set("pago_operativo_id", String(_ctxActual.pagoOpId));
            if (_ctxActual.pagoOdooId) qs.set("pago_odoo_id", String(_ctxActual.pagoOdooId));
            url = `/api/compras/vinculaciones/candidatos?${qs}`;
        }

        const res = await fetch(url);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);

        _renderResumenCabecera(data);
        _renderActuales(data);
        _renderCandidatos(data);
    }

    async function _abrirModal(ctx, onSuccess) {
        const modal = document.getElementById("modal-vincular-pago-doc");
        const tit = document.getElementById("modal-vincular-pago-doc-titulo");
        if (!modal) return;
        _ctxActual = ctx;
        _onSuccessCallback = typeof onSuccess === "function" ? onSuccess : null;
        if (tit) tit.textContent = ctx.titulo || "🔗 Vincular Pago ↔ Documento";
        _setBanner("", "");
        modal.style.display = "flex";
        try {
            await _cargarDatosModal();
        } catch (err) {
            _setBanner("err", err.message);
        }
    }

    async function _ejecutarVinculacion(idx, esFifo) {
        const tbody = document.getElementById("vinc-doc-cand-body");
        if (!tbody || !tbody._candidatos || !_ctxActual) return;
        const c = tbody._candidatos[idx];
        const meta = tbody._meta || {};
        if (!c) return;
        const inp = document.getElementById(`vinc-amt-${idx}`);
        const monto = Number(inp ? inp.value : 0);
        if (!Number.isFinite(monto) || monto <= 0) {
            _setBanner("err", "Ingresa un monto válido mayor a cero.");
            return;
        }

        _setBanner("info", "Guardando vinculación en CxC e intentando conciliar en Odoo...");
        const esVenta = _ctxActual.modo === "venta_doc" || _ctxActual.modo === "venta_pago";
        const url = esVenta ? "/api/ventas/vinculaciones" : "/api/compras/vinculaciones";
        let payload;
        if (_ctxActual.modo === "venta_doc") {
            const doc = meta.documento || {};
            payload = {
                so_id: doc.so_id || _ctxActual.soId,
                documento_comercial_id: doc.documento_comercial_id || _ctxActual.docId || null,
                pago_id: c.pago_id || null,
                pago_operativo_id: c.pago_operativo_id || null,
                monto_aplicado: monto,
                origen_vinculacion: esFifo ? "fifo" : "manual",
            };
        } else if (_ctxActual.modo === "venta_pago") {
            const pag = meta.pago || {};
            payload = {
                so_id: c.so_id,
                documento_comercial_id: c.documento_comercial_id || null,
                pago_id: pag.pago_id || _ctxActual.pagoId || null,
                pago_operativo_id: pag.pago_operativo_id || _ctxActual.pagoOpId || null,
                monto_aplicado: monto,
                origen_vinculacion: esFifo ? "fifo" : "manual",
            };
        } else if (_ctxActual.modo === "compra_doc") {
            const doc = meta.documento || {};
            payload = {
                compra_operativo_id: doc.compra_operativo_id || _ctxActual.compraId || null,
                compra_odoo_move_id: doc.compra_odoo_move_id || _ctxActual.moveId || null,
                pago_operativo_id: c.pago_operativo_id || null,
                pago_odoo_id: c.pago_odoo_id || null,
                monto_aplicado: monto,
                origen_vinculacion: esFifo ? "fifo" : "manual",
            };
        } else {
            const pag = meta.pago || {};
            payload = {
                compra_operativo_id: c.compra_operativo_id || null,
                compra_odoo_move_id: c.compra_odoo_move_id || null,
                pago_operativo_id: pag.pago_operativo_id || _ctxActual.pagoOpId || null,
                pago_odoo_id: pag.pago_odoo_id || _ctxActual.pagoOdooId || null,
                monto_aplicado: monto,
                origen_vinculacion: esFifo ? "fifo" : "manual",
            };
        }

        try {
            const res = await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const out = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(out.error || out.detail || `Error ${res.status}`);
            const msgOdoo = out.odoo_motivo || (out.odoo_reconciliado ? "Reconciliado en Odoo." : "Guardado en CxC.");
            _setBanner(out.odoo_reconciliado ? "ok" : "warn", msgOdoo);
            await _cargarDatosModal();
            if (_onSuccessCallback) await _onSuccessCallback();
        } catch (err) {
            _setBanner("err", err.message);
        }
    }

    async function _eliminarVinculacion(vincId, origenReg) {
        if (!confirm("¿Quitar esta vinculación en CxC?")) return;
        const esVenta = _ctxActual && (_ctxActual.modo === "venta_doc" || _ctxActual.modo === "venta_pago");
        const url = esVenta
            ? `/api/ventas/vinculaciones/${encodeURIComponent(origenReg || "cxc_op")}/${encodeURIComponent(vincId)}`
            : `/api/compras/vinculaciones/${encodeURIComponent(vincId)}`;
        try {
            const res = await fetch(url, { method: "DELETE" });
            const out = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(out.error || out.detail || `Error ${res.status}`);
            _setBanner("ok", "Vinculación removida en CxC.");
            await _cargarDatosModal();
            if (_onSuccessCallback) await _onSuccessCallback();
        } catch (err) {
            _setBanner("err", err.message);
        }
    }

    document.addEventListener("DOMContentLoaded", () => {
        document.getElementById("vinc-doc-cand-body")?.addEventListener("click", (e) => {
            const btn = e.target.closest("[data-vinc-apply]");
            if (!btn) return;
            _ejecutarVinculacion(Number(btn.getAttribute("data-vinc-apply")), btn.getAttribute("data-vinc-fifo") === "1");
        });
        document.getElementById("vinc-doc-actuales-body")?.addEventListener("click", (e) => {
            const btn = e.target.closest("[data-vinc-del]");
            if (!btn) return;
            _eliminarVinculacion(btn.getAttribute("data-vinc-id"), btn.getAttribute("data-vinc-org"));
        });
    });

    window.cerrarModalVincularDoc = cerrarModalVincularDoc;
    window.CxC = Object.assign(window.CxC || {}, {
        vinculaciones: {
            abrirVenta: (opts, cb) => _abrirModal({ modo: "venta_doc", ...opts }, cb),
            abrirDesdePagoRecibido: (opts, cb) => _abrirModal({ modo: "venta_pago", ...opts }, cb),
            abrirCompra: (opts, cb) => _abrirModal({ modo: "compra_doc", ...opts }, cb),
            abrirDesdePagoEnviado: (opts, cb) => _abrirModal({ modo: "compra_pago", ...opts }, cb),
            cerrar: cerrarModalVincularDoc,
        },
    });
})();
