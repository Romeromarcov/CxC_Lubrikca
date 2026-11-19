/* Maestro Único de Contactos (Odoo + Bots + CxC) y Motor Anti-Duplicados (Fase 6) */
(function () {
    "use strict";

    const PAGE_SIZE = 50;
    let _offset = 0;
    let _loadSeq = 0;
    let _puedeGestionar = false;
    let _itemsPorId = new Map();
    let _dupTimer = null;
    let _filterTimer = null;
    let _filtroSoloDuplicados = false;
    let _subtabActual = "maestro";

    function _esc(v) {
        if (window.CxC && typeof window.CxC.esc === "function") return window.CxC.esc(v);
        return String(v == null ? "" : v)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    async function _reqJson(url, opts) {
        const res = await fetch(url, { credentials: "same-origin", ...(opts || {}) });
        let data = {};
        try {
            data = await res.json();
        } catch (_e) {
            data = {};
        }
        if (!res.ok) {
            const err = new Error(data.error || data.detail || `Error HTTP ${res.status}`);
            err.status = res.status;
            err.data = data;
            throw err;
        }
        return data;
    }

    function _mostrarAviso(msg, tipo) {
        const el = document.getElementById("contactos-maestro-aviso");
        if (!el) return;
        if (!msg) {
            el.style.display = "none";
            el.textContent = "";
            return;
        }
        const colores = {
            ok: { bg: "#ecfdf5", fg: "#065f46", bd: "#a7f3d0" },
            warn: { bg: "#fffbeb", fg: "#92400e", bd: "#fcd34d" },
            err: { bg: "#fef2f2", fg: "#991b1b", bd: "#fecaca" },
        };
        const c = colores[tipo || "warn"] || colores.warn;
        el.style.display = "block";
        el.style.background = c.bg;
        el.style.color = c.fg;
        el.style.border = `1px solid ${c.bd}`;
        el.textContent = msg;
    }

    function _badgeOrigen(c) {
        if (c.origen === "bot" || c.es_borrador_telegram) {
            return `<span class="state-badge" style="background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe;">📲 Bot</span>`;
        }
        if (c.origen === "odoo") {
            return `<span class="state-badge" style="background:#f1f5f9; color:#334155; border:1px solid #cbd5e1;">🏢 Odoo</span>`;
        }
        return `<span class="state-badge" style="background:#f5f3ff; color:#6d28d9; border:1px solid #ddd6fe;">🖥️ CxC</span>`;
    }

    function _badgeRol(rol) {
        const mapa = {
            prospecto: ["amarillo", "Prospecto"],
            cliente: ["verde", "Cliente"],
            proveedor: ["abierta", "Proveedor"],
            ambos: ["cierre", "Cliente y Prov."],
        };
        const [cls, lbl] = mapa[rol] || ["", rol || "—"];
        return `<span class="semaphore ${cls}">${_esc(lbl)}</span>`;
    }

    function _barraCompletitud(c) {
        const pct = Math.max(0, Math.min(100, Number(c.completitud_pct) || 0));
        const color = pct >= 80 ? "#16a34a" : pct >= 50 ? "#d97706" : "#dc2626";
        const falt = Array.isArray(c.campos_faltantes) && c.campos_faltantes.length
            ? `Falta: ${c.campos_faltantes.join(", ")}`
            : "Ficha completa";
        return `
            <div title="${_esc(falt)}" style="min-width:110px;">
                <div style="display:flex; justify-content:space-between; font-size:0.78rem; font-weight:600; color:${color}; margin-bottom:2px;">
                    <span>${pct}%</span>
                    <span>${pct >= 80 ? "✓" : "⚠️"}</span>
                </div>
                <div style="height:6px; background:#e2e8f0; border-radius:99px; overflow:hidden;">
                    <div style="width:${pct}%; height:100%; background:${color};"></div>
                </div>
                ${falt && pct < 100 ? `<div style="font-size:0.71rem; color:#64748b; margin-top:2px;">${_esc(c.campos_faltantes.slice(0, 2).join(", "))}${c.campos_faltantes.length > 2 ? "…" : ""}</div>` : ""}
            </div>
        `;
    }

    function _badgeEstadoYDuplicados(c) {
        const estMap = {
            PENDIENTE: { bg: "#fef3c7", fg: "#92400e", lbl: "Pendiente" },
            VALIDADO: { bg: "#dbeafe", fg: "#1e40af", lbl: "Validado CxC" },
            ENVIADO_ODOO: { bg: "#dcfce7", fg: "#166534", lbl: "En Odoo" },
            DUPLICADO: { bg: "#fee2e2", fg: "#991b1b", lbl: "Duplicado" },
            DESCARTADO: { bg: "#f1f5f9", fg: "#475569", lbl: "Descartado" },
        };
        const st = estMap[c.estado_validacion] || estMap.PENDIENTE;
        let html = `<span class="state-badge" style="background:${st.bg}; color:${st.fg};">${_esc(st.lbl)}</span>`;
        if (c.odoo_partner_id) {
            html += `<div style="font-size:0.72rem; color:#64748b; margin-top:2px;">Odoo #${Number(c.odoo_partner_id)}</div>`;
        }
        const dups = Array.isArray(c.posibles_duplicados) ? c.posibles_duplicados : [];
        if (dups.length) {
            const top = dups[0];
            html += `<div style="margin-top:4px; font-size:0.74rem; color:#991b1b; background:#fef2f2; border:1px solid #fecaca; border-radius:6px; padding:2px 6px;">
                ⚠️ Posible dup. (${Number(top.score)}%): <strong>${_esc(top.nombre)}</strong>
            </div>`;
        }
        return html;
    }

    function _filaContacto(c) {
        const ub = c.ubicacion;
        const gpsHtml = ub && ub.maps_url
            ? `<a href="${_esc(ub.maps_url)}" target="_blank" rel="noopener" style="color:#0284c7; text-decoration:none; font-size:0.76rem;">📍 Ver GPS Maps${ub.plus_code ? ` (${_esc(ub.plus_code)})` : ""}</a>`
            : `<span style="color:#94a3b8; font-size:0.74rem;">Sin GPS</span>`;
        const info = [
            c.telefono ? `📞 ${_esc(c.telefono)}` : "",
            c.email ? `✉️ ${_esc(c.email)}` : "",
            gpsHtml,
        ].filter(Boolean).join("<br>");

        const btns = _puedeGestionar ? `
            <div style="display:flex; gap:0.3rem; flex-wrap:wrap;">
                <button type="button" class="btn-secondary" style="padding:0.25rem 0.5rem; font-size:0.76rem;" onclick="abrirModalContactoMaestro(${Number(c.contacto_id)})" title="Editar contacto">✏️</button>
                ${!c.odoo_partner_id ? `<button type="button" class="btn-primary" style="padding:0.25rem 0.5rem; font-size:0.76rem; background:#16a34a;" onclick="validarEnviarContactoOdoo(${Number(c.contacto_id)})" title="Validar y enviar a Odoo">✅ Odoo</button>` : ""}
                <button type="button" class="btn-secondary" style="padding:0.25rem 0.5rem; font-size:0.76rem;" onclick="abrirModalFusionarContacto(${Number(c.contacto_id)})" title="Fusionar como duplicado">🔀</button>
            </div>
        ` : `<span style="color:#94a3b8; font-size:0.76rem;">Solo lectura</span>`;

        return `<tr>
            <td style="white-space:nowrap;">${_badgeOrigen(c)}<div style="font-size:0.7rem; color:#64748b; margin-top:2px;">ID #${Number(c.contacto_id)}</div></td>
            <td>
                <strong>${_esc(c.nombre)}</strong>
                <div style="font-size:0.78rem; color:#475569;">${c.rif ? `<code>${_esc(c.rif)}</code>` : '<span style="color:#94a3b8;">Sin RIF</span>'}</div>
                ${c.ciudad ? `<div style="font-size:0.72rem; color:#64748b;">🏙️ ${_esc(c.ciudad)}</div>` : ""}
            </td>
            <td>${_badgeRol(c.rol)}</td>
            <td style="font-size:0.8rem;">${info}</td>
            <td>${_barraCompletitud(c)}</td>
            <td>${_badgeEstadoYDuplicados(c)}</td>
            <td>${btns}</td>
        </tr>`;
    }

    function _renderPaginacion(total, desde, hasta) {
        const cont = document.getElementById("contactos-maestro-paginacion");
        if (!cont) return;
        if (!total) {
            cont.innerHTML = "";
            return;
        }
        const ant = desde > 0;
        const sig = hasta < total;
        cont.innerHTML = `
            <span>${desde + 1}–${hasta} de ${total}</span>
            <button type="button" class="btn-secondary" data-cm-pag="ant" ${ant ? "" : "disabled"}>← Anterior</button>
            <button type="button" class="btn-secondary" data-cm-pag="sig" ${sig ? "" : "disabled"}>Siguiente →</button>
        `;
    }

    function _actualizarKpis(r) {
        const set = (id, val) => {
            const el = document.getElementById(id);
            if (el) el.textContent = String(val ?? 0);
        };
        set("cm-n-total", r.activos ?? r.total);
        set("cm-n-odoo", r.en_odoo);
        set("cm-n-bots", (Number(r.origen_bot) || 0) + (Number(r.origen_cxc) || 0));
        set("cm-n-prospectos", r.prospectos);
        set("cm-n-clientes", r.clientes);
        set("cm-n-proveedores", r.proveedores);
        set("cm-n-incompletos", r.incompletos);
        set("cm-n-duplicados", r.con_duplicados);
        const badge = document.getElementById("contactos-duplicados-badge");
        if (badge) badge.textContent = String(r.con_duplicados ?? 0);
    }

    async function loadContactosMaestro(refresh = false) {
        const body = document.getElementById("contactos-maestro-body");
        if (!body) return;
        const seq = ++_loadSeq;
        if (refresh) _offset = 0;

        const q = (document.getElementById("contacto-maestro-buscar")?.value || "").trim();
        const origen = (document.getElementById("contacto-maestro-origen")?.value || "").trim();
        const rol = (document.getElementById("contacto-maestro-rol")?.value || "").trim();
        const estado = (document.getElementById("contacto-maestro-estado")?.value || "").trim();
        const soloInc = Boolean(document.getElementById("contacto-maestro-incompletos")?.checked);

        const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(_offset) });
        if (q) params.set("q", q);
        if (origen) params.set("origen", origen);
        if (rol) params.set("rol", rol);
        if (estado) params.set("estado_validacion", estado);
        if (soloInc) params.set("solo_incompletos", "true");
        if (_filtroSoloDuplicados) params.set("solo_duplicados", "true");
        if (refresh) params.set("refresh", "true");

        body.innerHTML = `<tr><td colspan="7" class="table-empty">Consultando Maestro Único de Contactos...</td></tr>`;
        try {
            const data = await _reqJson("/api/contactos/maestro?" + params.toString());
            if (seq !== _loadSeq) return;
            _puedeGestionar = Boolean(data.puede_gestionar);
            const btnNuevo = document.getElementById("btn-nuevo-contacto-maestro");
            const btnSync = document.getElementById("btn-sync-contactos-maestro");
            if (btnNuevo) btnNuevo.style.display = _puedeGestionar ? "" : "none";
            if (btnSync) btnSync.style.display = _puedeGestionar ? "" : "none";

            if (data.sync_warning) {
                _mostrarAviso(`ℹ️ ${data.sync_warning}`, "warn");
            } else {
                _mostrarAviso("", "ok");
            }

            _actualizarKpis(data.resumen || {});
            const filas = Array.isArray(data.items) ? data.items : [];
            filas.forEach((it) => _itemsPorId.set(Number(it.contacto_id), it));

            const total = Number(data.total_filtrado ?? filas.length);
            const resEl = document.getElementById("contacto-maestro-resumen");
            if (resEl) {
                resEl.textContent = `${total} contacto${total === 1 ? "" : "s"} en el maestro${_filtroSoloDuplicados ? " (con posibles duplicados)" : ""}`;
            }

            if (!filas.length) {
                body.innerHTML = `<tr><td colspan="7" class="table-empty">No se encontraron contactos con los filtros actuales.</td></tr>`;
                _renderPaginacion(0, 0, 0);
                return;
            }
            body.innerHTML = filas.map(_filaContacto).join("");
            _renderPaginacion(total, _offset, _offset + filas.length);
        } catch (err) {
            if (seq !== _loadSeq) return;
            body.innerHTML = `<tr><td colspan="7" class="table-empty">❌ ${_esc(err.message)}</td></tr>`;
            _renderPaginacion(0, 0, 0);
        }
    }
    window.loadContactosMaestro = loadContactosMaestro;

    window.sincronizarContactosMaestro = async function () {
        const btn = document.getElementById("btn-sync-contactos-maestro");
        if (btn) btn.disabled = true;
        _mostrarAviso("Sincronizando directorio desde Odoo y registros locales...", "warn");
        try {
            const data = await _reqJson("/api/contactos/maestro/sincronizar", { method: "POST" });
            const st = data.stats || {};
            const msg = data.warning
                ? `Sincronizado con aviso: ${data.warning}`
                : `✅ Sincronización completa: ${st.creados || 0} nuevos, ${st.actualizados || 0} actualizados, ${st.locales_importados || 0} locales, ${st.pares_duplicados || 0} pares duplicados detectados.`;
            _mostrarAviso(msg, data.warning ? "warn" : "ok");
            await loadContactosMaestro(false);
            if (_subtabActual === "duplicados") await window.cargarColaDuplicados();
        } catch (err) {
            _mostrarAviso(`❌ ${err.message}`, "err");
        } finally {
            if (btn) btn.disabled = false;
        }
    };

    window.switchContactosSubtab = function (sub) {
        _subtabActual = sub;
        document.querySelectorAll("[data-contactos-subtab]").forEach((b) => {
            b.classList.toggle("active", b.dataset.contactosSubtab === sub);
        });
        const panels = {
            maestro: "contactos-subtab-maestro",
            duplicados: "contactos-subtab-duplicados",
            "odoo-raw": "contactos-subtab-odoo-raw",
        };
        Object.entries(panels).forEach(([k, id]) => {
            const el = document.getElementById(id);
            if (el) el.style.display = k === sub ? "block" : "none";
        });
        if (sub === "duplicados") {
            window.cargarColaDuplicados();
        } else if (sub === "odoo-raw" && typeof window.loadContactosOdoo === "function") {
            window.loadContactosOdoo(false);
        } else if (sub === "maestro") {
            loadContactosMaestro(false);
        }
    };

    window.cargarColaDuplicados = async function () {
        const lista = document.getElementById("contactos-duplicados-lista");
        if (!lista) return;
        lista.innerHTML = `<p class="table-empty">Analizando similitudes entre contactos...</p>`;
        try {
            const data = await _reqJson("/api/contactos/maestro/duplicados");
            _puedeGestionar = Boolean(data.puede_gestionar);
            const pares = Array.isArray(data.pares) ? data.pares : [];
            const badge = document.getElementById("contactos-duplicados-badge");
            if (badge) badge.textContent = String(pares.length);
            if (!pares.length) {
                lista.innerHTML = `<div style="padding:1.25rem; background:#ecfdf5; border:1px solid #a7f3d0; border-radius:10px; color:#065f46;">✅ No hay pares duplicados pendientes de revisión.</div>`;
                return;
            }
            lista.innerHTML = pares.map((p) => {
                const m = p.maestro || {};
                const d = p.duplicado || {};
                _itemsPorId.set(Number(m.contacto_id), m);
                _itemsPorId.set(Number(d.contacto_id), d);
                const motivos = (p.motivos || []).map((x) => _esc(x)).join(" · ");
                const color = p.bloqueante ? "#dc2626" : "#d97706";
                const btnFusion = _puedeGestionar
                    ? `<button type="button" class="btn-primary" style="background:#7c3aed;" onclick="fusionDirectaPar(${Number(d.contacto_id)}, ${Number(m.contacto_id)})">🔀 Fusionar #${Number(d.contacto_id)} → #${Number(m.contacto_id)}</button>`
                    : "";
                return `
                    <div class="card" style="padding:1rem; border-left:4px solid ${color};">
                        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem; margin-bottom:0.65rem;">
                            <div>
                                <span class="state-badge" style="background:${p.bloqueante ? "#fee2e2" : "#fef3c7"}; color:${color};">Coincidencia ${Number(p.score)}% (${_esc(p.nivel)})</span>
                                <span style="margin-left:0.5rem; font-size:0.84rem; color:#475569; font-weight:600;">${motivos}</span>
                            </div>
                            ${btnFusion}
                        </div>
                        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:0.75rem;">
                            <div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:8px; padding:0.75rem;">
                                <div style="font-size:0.74rem; color:#166534; font-weight:700;">✅ MAESTRO SUGERIDO (#${Number(m.contacto_id)} · ${m.odoo_partner_id ? `Odoo #${Number(m.odoo_partner_id)}` : "Local"})</div>
                                <div style="font-weight:700; margin:0.2rem 0;">${_esc(m.nombre)}</div>
                                <div style="font-size:0.8rem; color:#334155;">RIF: ${_esc(m.rif || "—")} · Tel: ${_esc(m.telefono || "—")}</div>
                                <div style="font-size:0.78rem; color:#64748b;">Origen: ${_esc(m.origen)} · Rol: ${_esc(m.rol)} · Completitud: ${Number(m.completitud_pct)}%</div>
                            </div>
                            <div style="background:#fff7ed; border:1px solid #fed7aa; border-radius:8px; padding:0.75rem;">
                                <div style="font-size:0.74rem; color:#9a3412; font-weight:700;">⚠️ POSIBLE DUPLICADO (#${Number(d.contacto_id)} · ${d.odoo_partner_id ? `Odoo #${Number(d.odoo_partner_id)}` : "Local"})</div>
                                <div style="font-weight:700; margin:0.2rem 0;">${_esc(d.nombre)}</div>
                                <div style="font-size:0.8rem; color:#334155;">RIF: ${_esc(d.rif || "—")} · Tel: ${_esc(d.telefono || "—")}</div>
                                <div style="font-size:0.78rem; color:#64748b;">Origen: ${_esc(d.origen)} · Rol: ${_esc(d.rol)} · Completitud: ${Number(d.completitud_pct)}%</div>
                            </div>
                        </div>
                    </div>
                `;
            }).join("");
        } catch (err) {
            lista.innerHTML = `<p class="table-empty">❌ ${_esc(err.message)}</p>`;
        }
    };

    window.fusionDirectaPar = async function (duplicadoId, maestroId) {
        if (!window.confirm(`¿Confirmas fusionar el contacto #${duplicadoId} dentro del maestro #${maestroId}?`)) return;
        try {
            await _reqJson(`/api/contactos/maestro/${duplicadoId}/fusionar`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ maestro_id: maestroId }),
            });
            _mostrarAviso(`✅ Contacto #${duplicadoId} fusionado en #${maestroId}.`, "ok");
            await window.cargarColaDuplicados();
            await loadContactosMaestro(false);
        } catch (err) {
            _mostrarAviso(`❌ ${err.message}`, "err");
        }
    };

    window.abrirModalContactoMaestro = function (contactoId) {
        const modal = document.getElementById("modal-contacto-maestro");
        if (!modal) return;
        const c = contactoId ? _itemsPorId.get(Number(contactoId)) : null;
        document.getElementById("cm-f-id").value = c ? String(c.contacto_id) : "";
        document.getElementById("cm-modal-titulo").textContent = c ? `✏️ Editar Contacto #${c.contacto_id}` : "➕ Nuevo Contacto en Maestro Único";
        document.getElementById("cm-f-nombre").value = c?.nombre || "";
        document.getElementById("cm-f-rol").value = c?.rol || "prospecto";
        document.getElementById("cm-f-rif").value = c?.rif || "";
        document.getElementById("cm-f-telefono").value = c?.telefono || "";
        document.getElementById("cm-f-email").value = c?.email || "";
        document.getElementById("cm-f-direccion").value = c?.direccion || "";
        document.getElementById("cm-f-ciudad").value = c?.ciudad || "";
        document.getElementById("cm-f-lat").value = c?.latitud != null ? String(c.latitud) : "";
        document.getElementById("cm-f-lon").value = c?.longitud != null ? String(c.longitud) : "";
        document.getElementById("cm-f-pluscode").value = c?.plus_code || "";
        document.getElementById("cm-f-notas").value = c?.notas || "";
        document.getElementById("cm-f-forzar").checked = false;
        document.getElementById("cm-wrap-forzar").style.display = "none";
        document.getElementById("contacto-dup-alerta").style.display = "none";
        document.getElementById("cm-modal-status").textContent = "";
        modal.style.display = "flex";
        if (c) window.chequearDuplicadosModal();
    };

    window.cerrarModalContactoMaestro = function () {
        const modal = document.getElementById("modal-contacto-maestro");
        if (modal) modal.style.display = "none";
    };

    window.programarChequeoDuplicados = function () {
        clearTimeout(_dupTimer);
        _dupTimer = setTimeout(window.chequearDuplicadosModal, 350);
    };

    window.chequearDuplicadosModal = async function () {
        const alerta = document.getElementById("contacto-dup-alerta");
        if (!alerta) return;
        const nombre = (document.getElementById("cm-f-nombre")?.value || "").trim();
        const rif = (document.getElementById("cm-f-rif")?.value || "").trim();
        const telefono = (document.getElementById("cm-f-telefono")?.value || "").trim();
        const email = (document.getElementById("cm-f-email")?.value || "").trim();
        if (!nombre && !rif && !telefono && !email) {
            alerta.style.display = "none";
            return;
        }
        const idRaw = document.getElementById("cm-f-id")?.value || "";
        try {
            const data = await _reqJson("/api/contactos/maestro/verificar-duplicados", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    nombre,
                    rif,
                    telefono,
                    email,
                    rol: document.getElementById("cm-f-rol")?.value || "prospecto",
                    excluir_id: idRaw ? Number(idRaw) : null,
                }),
            });
            const cands = data.candidatos || [];
            if (!cands.length) {
                alerta.style.display = "none";
                document.getElementById("cm-wrap-forzar").style.display = "none";
                return;
            }
            const bloqueante = Boolean(data.duplicado_bloqueante);
            alerta.style.display = "block";
            alerta.style.background = bloqueante ? "#fef2f2" : "#fffbeb";
            alerta.style.borderColor = bloqueante ? "#ef4444" : "#f59e0b";
            alerta.style.color = bloqueante ? "#991b1b" : "#78350f";
            const lista = cands.slice(0, 3).map((d) =>
                `<li><strong>${_esc(d.nombre)}</strong> (${_esc(d.rif || "Sin RIF")} · Tel: ${_esc(d.telefono || "—")}) — <em>${_esc((d.motivos || []).join(", "))} (${Number(d.score)}%)</em></li>`
            ).join("");
            alerta.innerHTML = `<strong>${bloqueante ? "🚫 Duplicado bloqueante por RIF:" : "⚠️ Posibles contactos similares detectados:"}</strong><ul style="margin:0.35rem 0 0 1.2rem; padding:0;">${lista}</ul>`;
            document.getElementById("cm-wrap-forzar").style.display = (!idRaw && !bloqueante) ? "inline-flex" : "none";
        } catch (_err) {
            alerta.style.display = "none";
        }
    };

    window.guardarContactoMaestro = async function (enviarOdoo) {
        const idRaw = (document.getElementById("cm-f-id")?.value || "").trim();
        const status = document.getElementById("cm-modal-status");
        const latRaw = (document.getElementById("cm-f-lat")?.value || "").trim();
        const lonRaw = (document.getElementById("cm-f-lon")?.value || "").trim();
        const payload = {
            nombre: (document.getElementById("cm-f-nombre")?.value || "").trim(),
            rol: document.getElementById("cm-f-rol")?.value || "prospecto",
            rif: (document.getElementById("cm-f-rif")?.value || "").trim(),
            telefono: (document.getElementById("cm-f-telefono")?.value || "").trim(),
            email: (document.getElementById("cm-f-email")?.value || "").trim(),
            direccion: (document.getElementById("cm-f-direccion")?.value || "").trim(),
            ciudad: (document.getElementById("cm-f-ciudad")?.value || "").trim(),
            latitud: latRaw !== "" ? Number(latRaw) : null,
            longitud: lonRaw !== "" ? Number(lonRaw) : null,
            plus_code: (document.getElementById("cm-f-pluscode")?.value || "").trim(),
            notas: (document.getElementById("cm-f-notas")?.value || "").trim(),
        };
        if (status) {
            status.style.color = "#475569";
            status.textContent = enviarOdoo ? "Validando y enviando a Odoo..." : "Guardando contacto...";
        }
        try {
            let contactoId = idRaw ? Number(idRaw) : null;
            if (contactoId) {
                const res = await _reqJson(`/api/contactos/maestro/${contactoId}`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ ...payload, sincronizar_odoo: false }),
                });
                contactoId = res.contacto?.contacto_id || contactoId;
                if (enviarOdoo) {
                    await _reqJson(`/api/contactos/maestro/${contactoId}/validar-odoo`, { method: "POST" });
                }
            } else {
                const res = await _reqJson("/api/contactos/maestro", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        ...payload,
                        forzar_duplicado: Boolean(document.getElementById("cm-f-forzar")?.checked),
                        enviar_a_odoo: Boolean(enviarOdoo),
                    }),
                });
                contactoId = res.contacto?.contacto_id;
            }
            window.cerrarModalContactoMaestro();
            _mostrarAviso(enviarOdoo ? `✅ Contacto #${contactoId} validado y enviado a Odoo.` : `✅ Contacto #${contactoId} guardado en el Maestro Único.`, "ok");
            await loadContactosMaestro(false);
        } catch (err) {
            if (status) {
                status.style.color = "#dc2626";
                status.textContent = `❌ ${err.message}`;
            }
            if (err.status === 409 && !idRaw) {
                document.getElementById("cm-wrap-forzar").style.display = "inline-flex";
                window.chequearDuplicadosModal();
            }
        }
    };

    window.validarEnviarContactoOdoo = async function (contactoId) {
        if (!window.confirm(`¿Validar y enviar el contacto #${contactoId} a Odoo?`)) return;
        try {
            const data = await _reqJson(`/api/contactos/maestro/${contactoId}/validar-odoo`, { method: "POST" });
            const c = data.contacto || {};
            _mostrarAviso(`✅ Contacto #${contactoId} (${c.nombre || ""}) enviado a Odoo #${c.odoo_partner_id || "—"}.`, "ok");
            await loadContactosMaestro(false);
        } catch (err) {
            _mostrarAviso(`❌ ${err.message}`, "err");
        }
    };

    window.abrirModalFusionarContacto = function (duplicadoId) {
        const modal = document.getElementById("modal-fusionar-contacto");
        if (!modal) return;
        const c = _itemsPorId.get(Number(duplicadoId));
        document.getElementById("cm-fus-duplicado-id").value = String(duplicadoId);
        document.getElementById("cm-fus-info").innerHTML = c
            ? `Contacto a absorber: <strong>#${Number(c.contacto_id)} · ${_esc(c.nombre)}</strong> (${_esc(c.rif || "Sin RIF")})`
            : `Contacto #${duplicadoId}`;
        const sel = document.getElementById("cm-fus-sugerido");
        const dups = Array.isArray(c?.posibles_duplicados) ? c.posibles_duplicados : [];
        sel.innerHTML = `<option value="">-- Elegir entre candidatos sugeridos --</option>` +
            dups.map((d) => `<option value="${Number(d.contacto_id)}">#${Number(d.contacto_id)} · ${_esc(d.nombre)} (${_esc(d.rif || "Sin RIF")}) — ${Number(d.score)}%</option>`).join("");
        document.getElementById("cm-fus-maestro-id").value = dups[0]?.contacto_id ? String(dups[0].contacto_id) : "";
        document.getElementById("cm-fus-status").textContent = "";
        modal.style.display = "flex";
    };

    window.cerrarModalFusionarContacto = function () {
        const modal = document.getElementById("modal-fusionar-contacto");
        if (modal) modal.style.display = "none";
    };

    window.confirmarFusionContacto = async function () {
        const dupId = Number(document.getElementById("cm-fus-duplicado-id")?.value || 0);
        const maeId = Number(document.getElementById("cm-fus-maestro-id")?.value || 0);
        const st = document.getElementById("cm-fus-status");
        if (!dupId || !maeId) {
            if (st) {
                st.style.color = "#dc2626";
                st.textContent = "❌ Debes indicar el ID del contacto maestro destino.";
            }
            return;
        }
        try {
            await _reqJson(`/api/contactos/maestro/${dupId}/fusionar`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ maestro_id: maeId }),
            });
            window.cerrarModalFusionarContacto();
            _mostrarAviso(`✅ Contacto #${dupId} fusionado dentro del maestro #${maeId}.`, "ok");
            await loadContactosMaestro(false);
        } catch (err) {
            if (st) {
                st.style.color = "#dc2626";
                st.textContent = `❌ ${err.message}`;
            }
        }
    };

    function _enlazarEventos() {
        const kpis = document.getElementById("contactos-maestro-kpis");
        if (kpis) {
            kpis.addEventListener("click", (e) => {
                const card = e.target.closest("[data-cm-filtro]");
                if (!card) return;
                const f = card.dataset.cmFiltro || "all";
                const selOri = document.getElementById("contacto-maestro-origen");
                const selRol = document.getElementById("contacto-maestro-rol");
                const selEst = document.getElementById("contacto-maestro-estado");
                const chkInc = document.getElementById("contacto-maestro-incompletos");
                if (selOri) selOri.value = "";
                if (selRol) selRol.value = "";
                if (selEst) selEst.value = "";
                if (chkInc) chkInc.checked = false;
                _filtroSoloDuplicados = false;
                if (f === "origen:odoo" && selOri) selOri.value = "odoo";
                else if (f === "origen:bot" && selOri) selOri.value = "bot";
                else if (f === "rol:prospecto" && selRol) selRol.value = "prospecto";
                else if (f === "rol:cliente" && selRol) selRol.value = "cliente";
                else if (f === "rol:proveedor" && selRol) selRol.value = "proveedor";
                else if (f === "incompletos" && chkInc) chkInc.checked = true;
                else if (f === "duplicados") _filtroSoloDuplicados = true;
                _offset = 0;
                loadContactosMaestro(false);
            });
        }
        const refiltrar = () => {
            _offset = 0;
            loadContactosMaestro(false);
        };
        const inpBuscar = document.getElementById("contacto-maestro-buscar");
        if (inpBuscar) {
            inpBuscar.addEventListener("input", () => {
                clearTimeout(_filterTimer);
                _filterTimer = setTimeout(refiltrar, 250);
            });
        }
        ["contacto-maestro-origen", "contacto-maestro-rol", "contacto-maestro-estado", "contacto-maestro-incompletos"]
            .forEach((id) => {
                const el = document.getElementById(id);
                if (el) el.addEventListener("change", () => {
                    _filtroSoloDuplicados = false;
                    refiltrar();
                });
            });
        const pag = document.getElementById("contactos-maestro-paginacion");
        if (pag) {
            pag.addEventListener("click", (e) => {
                const b = e.target.closest("[data-cm-pag]");
                if (!b || b.disabled) return;
                if (b.dataset.cmPag === "ant") _offset = Math.max(0, _offset - PAGE_SIZE);
                else _offset += PAGE_SIZE;
                loadContactosMaestro(false);
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", _enlazarEventos);
    } else {
        _enlazarEventos();
    }
})();
