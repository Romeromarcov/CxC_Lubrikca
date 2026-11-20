/* Recepción agrupada de pagos en efectivo por vendedor (solo efectivo -> REC-LOTE-...) */
(function () {
    "use strict";

    let _gruposEfectivo = [];
    let _vendedorActivoKey = "";

    function _esc(v) {
        if (window.CxC && typeof window.CxC.esc === "function") return window.CxC.esc(v);
        return String(v == null ? "" : v)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function _fmtNum(v) {
        const n = Number(v || 0);
        return Number.isFinite(n)
            ? n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
            : "0.00";
    }

    function _totalesGrupoTexto(g) {
        const parts = [];
        if (Number(g.total_usd || 0) > 0) parts.push(`$${_fmtNum(g.total_usd)} USD`);
        if (Number(g.total_ves || 0) > 0) parts.push(`Bs. ${_fmtNum(g.total_ves)}`);
        if (Number(g.total_eur || 0) > 0) parts.push(`€ ${_fmtNum(g.total_eur)}`);
        return parts.join(" · ") || `$${_fmtNum(g.total_equivalente_usd || 0)}`;
    }

    function _grupoActual() {
        if (!_gruposEfectivo.length) return null;
        return _gruposEfectivo.find((g) => g.vendedor_key === _vendedorActivoKey) || _gruposEfectivo[0];
    }

    function _actualizarTotalesSeleccion() {
        const checks = [...document.querySelectorAll(".lote-ef-chk:checked")];
        const g = _grupoActual();
        const sumEl = document.getElementById("lote-efectivo-sel-resumen");
        const btnGen = document.getElementById("lote-efectivo-btn-generar");
        if (!g || !checks.length) {
            if (sumEl) sumEl.textContent = "0 pagos seleccionados.";
            if (btnGen) btnGen.disabled = true;
            return;
        }
        let eqUsd = 0;
        let usd = 0;
        let ves = 0;
        checks.forEach((chk) => {
            const idx = Number(chk.dataset.idx);
            const p = g.pagos[idx];
            if (!p) return;
            const mon = String(p.moneda || "USD").toUpperCase();
            const m = Number(p.monto || 0);
            if (mon === "VES") ves += m;
            else usd += m;
            eqUsd += Number(p.monto_bcv_usd ?? (mon === "USD" ? m : 0));
        });
        const partes = [];
        if (usd > 0) partes.push(`$${_fmtNum(usd)} USD`);
        if (ves > 0) partes.push(`Bs. ${_fmtNum(ves)}`);
        if (sumEl) {
            sumEl.textContent = `${checks.length} pago(s) en efectivo seleccionado(s) · ${partes.join(" + ") || "$0.00"} (Equiv: $${_fmtNum(eqUsd)})`;
        }
        if (btnGen) btnGen.disabled = false;
    }

    function _renderVendedoresCards() {
        const cont = document.getElementById("lotes-efectivo-vendedores");
        if (!cont) return;
        if (!_gruposEfectivo.length) {
            cont.innerHTML = '<div style="padding:1rem; background:#ecfdf5; border:1px solid #a7f3d0; border-radius:10px; color:#065f46; font-weight:600;">✅ No hay pagos en efectivo pendientes de entrega por vendedor.</div>';
            return;
        }
        const activo = _grupoActual();
        _vendedorActivoKey = activo ? activo.vendedor_key : "";
        cont.innerHTML = _gruposEfectivo.map((g) => {
            const isSel = g.vendedor_key === _vendedorActivoKey;
            const border = isSel ? "2px solid #2563eb; background:#eff6ff;" : "1px solid #e2e8f0; background:#fff;";
            return `<button type="button" class="lote-vend-card" data-vend-key="${_esc(g.vendedor_key)}" style="${border} border-radius:10px; padding:0.75rem 1rem; text-align:left; cursor:pointer; min-width:220px; transition:all .15s;">
                <div style="font-weight:700; color:#0f172a; font-size:0.92rem;">🧑‍💼 ${_esc(g.vendedor_nombre || g.vendedor_email)}</div>
                <div style="font-size:0.76rem; color:#64748b; margin:2px 0 6px;">${_esc(g.vendedor_email)}</div>
                <div style="display:flex; justify-content:space-between; align-items:center; gap:0.5rem;">
                    <span class="state-badge abierta">${g.cantidad_pagos} efectivo(s)</span>
                    <strong style="color:#1e3a8a; font-size:0.86rem;">$${_fmtNum(g.total_equivalente_usd)}</strong>
                </div>
                <div style="font-size:0.74rem; color:#475569; margin-top:4px;">${_esc(_totalesGrupoTexto(g))}</div>
            </button>`;
        }).join("");

        cont.querySelectorAll(".lote-vend-card").forEach((btn) => {
            btn.addEventListener("click", () => {
                _vendedorActivoKey = btn.dataset.vendKey || "";
                _renderVendedoresCards();
                _renderTablaPendientesVendedor();
            });
        });
    }

    function _renderTablaPendientesVendedor() {
        const tbody = document.getElementById("lotes-efectivo-pendientes-tbody");
        const titEl = document.getElementById("lote-efectivo-vend-titulo");
        const chkAll = document.getElementById("lote-efectivo-chk-all");
        if (!tbody) return;
        const g = _grupoActual();
        if (!g) {
            if (titEl) titEl.textContent = "Pagos en efectivo pendientes";
            tbody.innerHTML = '<tr><td colspan="7" class="table-empty">No hay efectivo pendiente por recibir.</td></tr>';
            _actualizarTotalesSeleccion();
            return;
        }
        if (titEl) {
            titEl.textContent = `Efectivo pendiente de ${g.vendedor_nombre || g.vendedor_email} (${g.cantidad_pagos} pago(s))`;
        }
        if (chkAll) chkAll.checked = true;

        const trazFn = (window.CxC && typeof window.CxC.trazabilidadHtml === "function")
            ? window.CxC.trazabilidadHtml
            : (x) => _esc(x.numero || x.pago_id || "");

        tbody.innerHTML = g.pagos.map((p, idx) => {
            const mon = String(p.moneda || "USD").toUpperCase();
            const montoStr = mon === "VES" ? `Bs. ${_fmtNum(p.monto)}` : `$${_fmtNum(p.monto)} ${mon}`;
            const eqUsd = p.monto_bcv_usd ?? (mon === "USD" ? p.monto : 0);
            const docRef = p.pedido_ref ? `Pedido: ${_esc(p.pedido_ref)}` : "—";
            return `<tr>
                <td style="text-align:center;"><input type="checkbox" class="lote-ef-chk" data-idx="${idx}" checked></td>
                <td>${trazFn(p)}</td>
                <td>${_esc(p.fecha_documento || p.pago_fecha || "—")}</td>
                <td><strong>${_esc(p.contraparte_nombre || p.cliente || "Sin cliente")}</strong><div class="cap-sub">${_esc(p.rif || "")}</div></td>
                <td>${_esc(p.metodo_pago || "Efectivo")}<div class="cap-sub">${docRef}</div></td>
                <td style="text-align:right;"><strong>${montoStr}</strong></td>
                <td style="text-align:right;"><strong>$${_fmtNum(eqUsd)}</strong></td>
            </tr>`;
        }).join("");

        tbody.querySelectorAll(".lote-ef-chk").forEach((c) => {
            c.addEventListener("change", _actualizarTotalesSeleccion);
        });
        _actualizarTotalesSeleccion();
    }

    async function _cargarHistorialLotes() {
        const tbody = document.getElementById("lotes-efectivo-emitidos-tbody");
        if (!tbody) return;
        try {
            const res = await fetch("/api/pagos/efectivo/lotes?limit=60");
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
            const items = data.items || [];
            if (!items.length) {
                tbody.innerHTML = '<tr><td colspan="7" class="table-empty">Aún no se han emitido recibos de efectivo por lote.</td></tr>';
                return;
            }
            tbody.innerHTML = items.map((l) => {
                const detMon = [];
                if (Number(l.total_usd || 0) > 0) detMon.push(`$${_fmtNum(l.total_usd)} USD`);
                if (Number(l.total_ves || 0) > 0) detMon.push(`Bs. ${_fmtNum(l.total_ves)}`);
                if (Number(l.total_eur || 0) > 0) detMon.push(`€ ${_fmtNum(l.total_eur)}`);
                return `<tr>
                    <td><strong>${_esc(l.numero_recibo)}</strong></td>
                    <td>${_esc(l.fecha_lote || "—")}</td>
                    <td><strong>${_esc(l.vendedor_nombre || l.vendedor_email || "—")}</strong><div class="cap-sub">${_esc(l.vendedor_email || "")}</div></td>
                    <td>${l.cantidad_pagos} pago(s)</td>
                    <td><strong>$${_fmtNum(l.total_equivalente_usd)}</strong><div class="cap-sub">${_esc(detMon.join(" · "))}</div></td>
                    <td>${_esc(l.recibido_por || "—")}</td>
                    <td><a href="${_esc(l.recibo_path)}" target="_blank" rel="noopener" class="btn-secondary" style="padding:0.3rem 0.6rem; font-size:0.78rem; text-decoration:none; background:#eff6ff; color:#1d4ed8;">🖨️ Ver / Imprimir Lote</a></td>
                </tr>`;
            }).join("");
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="7" class="table-empty">❌ ${_esc(err.message)}</td></tr>`;
        }
    }

    async function loadRecibosLoteEfectivo() {
        const cont = document.getElementById("lotes-efectivo-vendedores");
        if (cont) cont.innerHTML = '<div style="color:#64748b; padding:0.75rem;">Cargando efectivo pendiente por vendedor...</div>';
        const fechaEl = document.getElementById("lote-efectivo-fecha");
        if (fechaEl && !fechaEl.value) {
            fechaEl.value = new Date().toISOString().slice(0, 10);
        }
        try {
            const [resP] = await Promise.all([
                fetch("/api/pagos/efectivo/lotes-pendientes"),
                _cargarHistorialLotes(),
            ]);
            const dataP = await resP.json().catch(() => ({}));
            if (!resP.ok) throw new Error(dataP.detail || `Error ${resP.status}`);
            _gruposEfectivo = dataP.grupos || [];

            const badge = document.getElementById("badge-lotes-efectivo-pend");
            if (badge) badge.textContent = String(dataP.total_pagos || 0);
            const kVend = document.getElementById("lotes-ef-kpi-vendedores");
            if (kVend) kVend.textContent = String(dataP.total_vendedores || 0);
            const kPag = document.getElementById("lotes-ef-kpi-pagos");
            if (kPag) kPag.textContent = String(dataP.total_pagos || 0);
            const totUsd = _gruposEfectivo.reduce((acc, g) => acc + Number(g.total_equivalente_usd || 0), 0);
            const kTot = document.getElementById("lotes-ef-kpi-usd");
            if (kTot) kTot.textContent = `$${_fmtNum(totUsd)}`;

            _renderVendedoresCards();
            _renderTablaPendientesVendedor();
        } catch (err) {
            if (cont) cont.innerHTML = `<div style="color:#dc2626; padding:0.75rem;">❌ ${_esc(err.message)}</div>`;
        }
    }

    async function generarReciboLoteActual() {
        const g = _grupoActual();
        if (!g) return;
        const checks = [...document.querySelectorAll(".lote-ef-chk:checked")];
        if (!checks.length) {
            alert("Selecciona al menos un pago en efectivo.");
            return;
        }
        const pagoIds = [];
        const docIds = [];
        checks.forEach((chk) => {
            const p = g.pagos[Number(chk.dataset.idx)];
            if (!p) return;
            if (p.documento_id != null && String(p.documento_id).trim() !== "") {
                docIds.push(Number(p.documento_id));
            } else if (p.pago_id) {
                pagoIds.push(String(p.pago_id));
            } else if (p.odoo_pago_id) {
                pagoIds.push(String(p.odoo_pago_id));
            }
        });
        const obs = ((document.getElementById("lote-efectivo-obs") || {}).value || "").trim();
        const fechaLote = ((document.getElementById("lote-efectivo-fecha") || {}).value || "").trim();
        const statusEl = document.getElementById("lote-efectivo-status");
        const btn = document.getElementById("lote-efectivo-btn-generar");
        if (btn) btn.disabled = true;
        if (statusEl) {
            statusEl.style.color = "#0369a1";
            statusEl.textContent = "Generando recibo de efectivo por lote...";
        }

        try {
            const res = await fetch("/api/pagos/efectivo/lotes", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    vendedor_email: g.vendedor_email,
                    vendedor_nombre: g.vendedor_nombre,
                    pago_ids: pagoIds,
                    documento_ids: docIds,
                    observaciones: obs,
                    fecha_lote: fechaLote || null,
                }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
            const lote = data.lote || {};
            if (document.getElementById("lote-efectivo-obs")) {
                document.getElementById("lote-efectivo-obs").value = "";
            }
            if (statusEl) {
                statusEl.style.color = "#15803d";
                statusEl.innerHTML = `✅ Recibo generado: <strong>${_esc(lote.numero_recibo)}</strong> · ` +
                    `<a href="${_esc(lote.recibo_path)}" target="_blank" rel="noopener" style="color:#1d4ed8; font-weight:700;">🖨️ Abrir comprobante imprimible ↗</a>`;
            }
            if (lote.recibo_path) {
                window.open(lote.recibo_path, "_blank", "noopener");
            }
            await loadRecibosLoteEfectivo();
            if (typeof window.loadPagosRecibidosUnificados === "function") window.loadPagosRecibidosUnificados();
            if (typeof window.loadCobranzaUnificado === "function") window.loadCobranzaUnificado();
        } catch (err) {
            if (statusEl) {
                statusEl.style.color = "#dc2626";
                statusEl.textContent = `❌ ${err.message}`;
            }
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    function abrirSubtabLotesEfectivo() {
        const btn = document.querySelector('.cobranza-subnav-btn[data-cobranza-subpage="lotes-efectivo"]');
        if (btn) {
            btn.click();
        } else {
            loadRecibosLoteEfectivo();
        }
    }

    window.loadRecibosLoteEfectivo = loadRecibosLoteEfectivo;
    window.abrirSubtabLotesEfectivo = abrirSubtabLotesEfectivo;
    window.generarReciboLoteActual = generarReciboLoteActual;

    document.addEventListener("DOMContentLoaded", () => {
        document.getElementById("lote-efectivo-chk-all")?.addEventListener("change", (ev) => {
            const checked = Boolean(ev.target.checked);
            document.querySelectorAll(".lote-ef-chk").forEach((c) => { c.checked = checked; });
            _actualizarTotalesSeleccion();
        });
        document.getElementById("lote-efectivo-btn-generar")?.addEventListener("click", generarReciboLoteActual);

        const seg = window.location.pathname.toLowerCase().replace(/^\/+|\/+$/g, "").split("/")[0];
        if (seg === "pagos" || seg === "cobranza") {
            loadRecibosLoteEfectivo();
        }
    });
})();
