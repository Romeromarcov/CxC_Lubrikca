// Sesión vencida o ausente -> de vuelta al login. Desde la auditoría de
// agosto 2026 el backend exige sesión en TODA ruta /api/ (ver
// exigir_sesion_en_api en web/app.py); antes respondía datos sin cookie,
// así que el frontend nunca necesitó manejar el 401. Se envuelve fetch una
// sola vez en vez de tocar las ~200 llamadas existentes.
(function redirigirAlLoginSi401() {
    const fetchOriginal = window.fetch;
    window.fetch = async function (...args) {
        const res = await fetchOriginal.apply(this, args);
        if (res.status === 401 && !window.location.pathname.startsWith("/login")) {
            window.location.href = "/login";
        }
        return res;
    };
})();

document.addEventListener("DOMContentLoaded", () => {
    function formatListasDisplay(raw) {
        if (!raw || raw === "*") return "Todas (*)";
        return raw.split(",").map(x => `#${x.trim()}`).join(", ");
    }

    // State
    let selectedPayment = null;
    let reporteData = []; // Cache for live filtering
    
    // Elements - Navigation
    const tabButtons = document.querySelectorAll(".tab-btn");
    const tabPanels = document.querySelectorAll(".tab-panel");

    // Subpáginas de Configuración (Descuentos / Usuarios / Listas de Precio /
    // Otras / Motor) -- agrupa las secciones existentes de #tab-config sin
    // mover sus endpoints ni su markup interno, solo oculta/muestra por
    // data-subpage. Ver docs/REDISENO_DESCUENTOS_UNIFICADOS.md.
    const configSubnavButtons = document.querySelectorAll(".config-subnav-btn");
    const configSubpageSections = document.querySelectorAll("#tab-config [data-subpage]");

    function applyConfigSubpage(subpage) {
        configSubpageSections.forEach((section) => {
            section.classList.toggle(
                "config-subpage-hidden",
                section.getAttribute("data-subpage") !== subpage
            );
        });
        configSubnavButtons.forEach((btn) => {
            btn.classList.toggle("active", btn.getAttribute("data-config-subpage") === subpage);
        });
        try {
            sessionStorage.setItem("cxc_config_subpage", subpage);
        } catch (_e) {
            // sessionStorage no disponible (modo privado, etc.) -- no es crítico.
        }
    }

    if (configSubnavButtons.length) {
        configSubnavButtons.forEach((btn) => {
            btn.addEventListener("click", () => {
                applyConfigSubpage(btn.getAttribute("data-config-subpage"));
            });
        });
        let initialSubpage = "descuentos";
        try {
            initialSubpage = sessionStorage.getItem("cxc_config_subpage") || "descuentos";
        } catch (_e) {
            // ignorar
        }
        applyConfigSubpage(initialSubpage);
    }

    // Elements - KPIs
    const kpiCobrables = document.getElementById("kpi-cobrables");
    const kpiSinAsignar = document.getElementById("kpi-sin-asignar");
    const kpiAlertas = document.getElementById("kpi-alertas");
    
    // Elements - Dashboard/Payments
    const formPagoId = document.getElementById("form-pago-id");
    const formClienteNombre = document.getElementById("form-cliente-nombre");
    const formClienteId = document.getElementById("form-cliente-id");
    const formPagoMoneda = document.getElementById("form-pago-moneda");
    const formPagoDisponible = document.getElementById("form-pago-disponible");
    const formSoSelect = document.getElementById("form-so-select");
    const formMontoAplicar = document.getElementById("form-monto-aplicar");
    const btnSubmit = document.getElementById("btn-submit");
    const vinculacionForm = document.getElementById("vinculacion-form");
    const bandejaTableBody = document.getElementById("bandeja-table-body");

    // Elements - Payment Allocator VES dynamic rate adjuster
    const groupFechaHoraPago = document.getElementById("group-fecha-hora-pago");
    const groupTasasReferencia = document.getElementById("group-tasas-referencia");
    const formPagoFecha = document.getElementById("form-pago-fecha");
    const formPagoHora = document.getElementById("form-pago-hora");
    const lblTasaBcv = document.getElementById("lbl-tasa-bcv");
    const lblTasaBinance = document.getElementById("lbl-tasa-binance");
    const lblEqBcv = document.getElementById("lbl-eq-bcv");
    const lblEqBinance = document.getElementById("lbl-eq-binance");

    // Elements - Config
    const settingsForm = document.getElementById("settings-form");
    const cfgMetaDays = document.getElementById("cfg-meta-days");
    const cfgMetaRecompra = document.getElementById("cfg-meta-recompra");
    const cfgMetaMarcaFallback = document.getElementById("cfg-meta-marca-fallback");
    const cfgMetaAjusteIndustrial = document.getElementById("cfg-meta-ajuste-industrial");

    const tasaForm = document.getElementById("tasa-form");
    const cfgTasaBcv = document.getElementById("cfg-tasa-bcv");
    const cfgTasaBinance = document.getElementById("cfg-tasa-binance");
    const btnSyncOdooRates = document.getElementById("btn-sync-odoo-rates");
    const tasasTableBody = document.getElementById("tasas-table-body");
    
    const feriadoForm = document.getElementById("feriado-form");
    const cfgFeriadoFecha = document.getElementById("cfg-feriado-fecha");
    const cfgFeriadoDesc = document.getElementById("cfg-feriado-desc");
    const feriadosTableBody = document.getElementById("feriados-table-body");

    const descuentoForm = document.getElementById("descuento-form");
    const cfgDescMarca = document.getElementById("cfg-desc-marca");
    const cfgDescCat = document.getElementById("cfg-desc-cat");
    const cfgDescTipo = document.getElementById("cfg-desc-tipo");
    const cfgDescPorcentaje = document.getElementById("cfg-desc-porcentaje");
    const cfgDescDesde = document.getElementById("cfg-desc-desde");
    const cfgDescHasta = document.getElementById("cfg-desc-hasta");
    const cfgDescListas = document.getElementById("cfg-desc-listas");
    const descuentosTableBody = document.getElementById("descuentos-table-body");

    const listasPrecioTableBody = document.getElementById("listas-precio-table-body");
    const productosTableBody = document.getElementById("productos-table-body");
    const clientesAuditoriaTableBody = document.getElementById("clientes-auditoria-table-body");

    // Elements - Promociones Primera Compra
    const promoForm = document.getElementById("promo-form");
    const cfgPromoTipoBeneficio = document.getElementById("cfg-promo-tipo-beneficio");
    const cfgPromoProductos = document.getElementById("cfg-promo-productos");
    const cfgPromoProductosBuscar = document.getElementById("cfg-promo-productos-buscar");
    const cfgPromoProductosCount = document.getElementById("promo-productos-count");
    const cfgPromoRegaloTipo = document.getElementById("cfg-promo-regalo-tipo");
    const cfgPromoValor = document.getElementById("cfg-promo-valor");
    const cfgPromoCompraMinima = document.getElementById("cfg-promo-compra-minima");
    const cfgPromoFallback = document.getElementById("cfg-promo-fallback");
    const cfgPromoDesde = document.getElementById("cfg-promo-desde");
    const cfgPromoHasta = document.getElementById("cfg-promo-hasta");
    const promosTableBody = document.getElementById("promos-table-body");
    const promoProductosSection = document.getElementById("promo-productos-section");
    const promoRegaloTipoSection = document.getElementById("promo-regalo-tipo-section");
    const promoPorcentajeSection = document.getElementById("promo-porcentaje-section");

    // Elements - Exclusiones
    const exclusionForm = document.getElementById("exclusion-form");
    const cfgExclTipoA = document.getElementById("cfg-excl-tipo-a");
    const cfgExclTipoB = document.getElementById("cfg-excl-tipo-b");
    const excluisionesTableBody = document.getElementById("exclusiones-table-body");

    // Elements - Descuentos por Volumen
    const descuentoVolumenForm = document.getElementById("descuento-volumen-form");
    const cfgDescVolMarca = document.getElementById("cfg-desc-vol-marca");
    const cfgDescVolCat = document.getElementById("cfg-desc-vol-cat");
    const cfgDescVolLitros = document.getElementById("cfg-desc-vol-litros");
    const cfgDescVolPorcentaje = document.getElementById("cfg-desc-vol-porcentaje");
    const cfgDescVolDesde = document.getElementById("cfg-desc-vol-desde");
    const cfgDescVolHasta = document.getElementById("cfg-desc-vol-hasta");
    const cfgDescVolListas = document.getElementById("cfg-desc-vol-listas");
    const descuentosVolumenTableBody = document.getElementById("descuentos-volumen-table-body");

    // Elements - 3 Bandejas Dashboard
    const bandeja1TableBody = document.getElementById("bandeja1-table-body");
    const bandeja2TableBody = document.getElementById("bandeja2-table-body");
    const bandeja3TableBody = document.getElementById("bandeja3-table-body");
    const bandejaAuditoriaPreciosTableBody = document.getElementById("bandeja-auditoria-precios-table-body");
    const bandejaEnProcesoDePagoTableBody = document.getElementById("bandeja-en-proceso-de-pago-table-body");
    const bandejaPendientesCerrarTableBody = document.getElementById("bandeja-pendientes-cerrar-table-body");

    // User Session & Multi-Page Initialization
    let currentUserSession = null;

    async function initUserSession() {
        try {
            const res = await fetch("/api/auth/me");
            if (res.ok) {
                currentUserSession = await res.json();
                renderUserProfile(currentUserSession);
                filterNavbarByPermissions(currentUserSession);
            }
        } catch (err) {
            console.error("Error fetching user session:", err);
        }
    }

    function renderUserProfile(user) {
        const nameEl = document.getElementById("header-user-name");
        const roleEl = document.getElementById("header-user-role");
        const avatarEl = document.getElementById("header-avatar");

        if (nameEl) nameEl.textContent = user.nombre || user.email;
        if (roleEl) roleEl.textContent = user.nombre_rol || user.rol;
        if (avatarEl) {
            const initial = (user.nombre || user.email || "U").charAt(0).toUpperCase();
            avatarEl.textContent = initial;
        }
    }

    function filterNavbarByPermissions(user) {
        const navLinks = document.querySelectorAll(".nav-link");
        const perms = user.permisos || ["reporte"];
        const isAdm = user.rol === "admin";

        navLinks.forEach(link => {
            const page = link.dataset.page;
            const isDashboardCard = link.parentElement && link.parentElement.id === "dashboard-quick-actions";
            
            if (!page || page === "dashboard" || isAdm || perms.includes(page)) {
                link.style.display = isDashboardCard ? "block" : "inline-flex";
            } else {
                link.style.display = "none";
            }
        });

        const adminPanel = document.getElementById("admin-user-mgmt-panel");
        if (adminPanel) {
            adminPanel.style.display = isAdm ? "block" : "none";
        }

        const reciboBtnContainer = document.getElementById("btn-generar-recibo-container");
        if (reciboBtnContainer) {
            const canGenerateReceipt = ["admin", "tesoreria", "gerente_ventas"].includes(user.rol);
            reciboBtnContainer.style.display = canGenerateReceipt ? "block" : "none";
        }

        const recalcularTodoContainer = document.getElementById("recalcular-todo-container");
        if (recalcularTodoContainer) {
            const canRecalcularTodo = ["admin", "gerente_ventas"].includes(user.rol);
            recalcularTodoContainer.style.display = canRecalcularTodo ? "block" : "none";
        }
    }

    // Page Route Initialization
    function initCurrentPage() {
        let rawPath = window.location.pathname.toLowerCase();
        let path = rawPath.replace(/^\/+|\/+$/g, '').split('/')[0].split('?')[0].split('#')[0].trim();
        if (!path || path === "index.html") path = "dashboard";

        const pageToTabMap = {
            "dashboard": "tab-dashboard",
            "facturacion": "tab-facturacion",
            "cobranza": "tab-cobranza",
            "ventas": "tab-ventas",
            "reporte": "tab-reporte",
            "auditoria": "tab-auditoria",
            "inventario": "tab-inventario",
            "configuracion": "tab-config"
        };

        const targetTabId = pageToTabMap[path] || "tab-dashboard";

        // Active Link Highlight
        document.querySelectorAll(".nav-link").forEach(link => {
            if (link.dataset.page === path) {
                link.classList.add("active");
            } else {
                link.classList.remove("active");
            }
        });

        // Active Tab Panel Show
        const tabPanels = document.querySelectorAll(".tab-panel");
        tabPanels.forEach(panel => {
            if (panel.id === targetTabId) {
                panel.classList.add("active");
            } else {
                panel.classList.remove("active");
            }
        });

        // Load data for active page safely
        try {
            if (path === "dashboard") {
                if (typeof loadTasasPromedios === "function") loadTasasPromedios();
                if (typeof loadReporteDiario === "function") loadReporteDiario();
                // Las tarjetas de saldos ahora viven en el Dashboard (movidas
                // desde Reporte); loadReporte() las llena vía /api/reporte-saldos.
                if (typeof loadReporte === "function") loadReporte();
            } else if (path === "facturacion") {
                if (typeof loadBandeja === "function") loadBandeja();
                if (typeof loadDiferencialCandidatos === "function") loadDiferencialCandidatos();
            } else if (path === "cobranza") {
                if (typeof loadCobranzaUnificado === "function") loadCobranzaUnificado();
            } else if (path === "ventas") {
                if (typeof loadVentas === "function") loadVentas();
            } else if (path === "reporte") {
                if (typeof loadReporte === "function") loadReporte();
                if (typeof loadReporteCxcCliente === "function") loadReporteCxcCliente();
            } else if (path === "auditoria") {
                if (typeof loadAuditoria === "function") loadAuditoria();
                if (typeof loadAuditoriaVentasAlertas === "function") loadAuditoriaVentasAlertas();
            } else if (path === "inventario") {
                if (typeof loadInventario === "function") loadInventario();
            } else if (path === "configuracion") {
                if (typeof loadConfigData === "function") loadConfigData();
                if (typeof loadPricelistMapeo === "function") loadPricelistMapeo();
                if (typeof loadReglasConsolidadas === "function") loadReglasConsolidadas();
                if (currentUserSession && currentUserSession.rol === "admin" && typeof loadAdminUsuarios === "function") {
                    loadAdminUsuarios();
                }
            }
        } catch (err) {
            console.error("Error cargando datos para la página " + path + ":", err);
        }
    }
    window.initCurrentPage = initCurrentPage;

    window.recalcularTodo = async function() {
        const btn = document.getElementById("btn-recalcular-todo");
        const statusEl = document.getElementById("recalcular-todo-status");
        if (!confirm("¿Forzar el recálculo completo del motor de descuentos para TODAS las órdenes? Puede tardar varios minutos en segundo plano.")) {
            return;
        }
        try {
            if (btn) btn.disabled = true;
            if (statusEl) statusEl.textContent = "Iniciando recálculo en segundo plano...";
            const res = await fetch("/api/admin/recalcular-todo", { method: "POST" });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                if (statusEl) statusEl.textContent = "✅ " + (data.message || "Recálculo iniciado.") + " Los reportes se actualizarán en unos minutos.";
            } else {
                if (statusEl) statusEl.textContent = "❌ Error: " + (data.detail || "No se pudo iniciar el recálculo.");
            }
        } catch (err) {
            if (statusEl) statusEl.textContent = "❌ Error de red al iniciar el recálculo.";
            console.error(err);
        } finally {
            if (btn) btn.disabled = false;
        }
    };

    window.loadAdminUsuarios = async function() {
        const tbody = document.getElementById("admin-usuarios-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="6" class="table-empty">Cargando lista de usuarios de la plataforma...</td></tr>';
            const res = await fetch("/api/admin/usuarios");
            if (res.ok) {
                const users = await res.json();
                if (users.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="6" class="table-empty">No hay usuarios registrados en la plataforma.</td></tr>';
                    return;
                }
                tbody.innerHTML = "";
                users.forEach(u => {
                    const row = document.createElement("tr");
                    row.innerHTML = `
                        <td><strong>${u.email}</strong></td>
                        <td>${u.nombre_odoo || u.email}</td>
                        <td>
                            <select onchange="cambiarRolUsuario('${u.email}', this.value)" style="padding: 0.35rem 0.65rem; border-radius: 6px; border: 1px solid #cbd5e1; font-family: inherit; font-size: 0.82rem; background: white; font-weight: 500;">
                                <option value="admin" ${u.rol === 'admin' ? 'selected' : ''}>Administrador / Gerencia</option>
                                <option value="tesoreria" ${u.rol === 'tesoreria' ? 'selected' : ''}>Tesorería y Cobranza</option>
                                <option value="auditor" ${u.rol === 'auditor' ? 'selected' : ''}>Auditoría y Contabilidad</option>
                                <option value="ventas" ${u.rol === 'ventas' ? 'selected' : ''}>Ventas y Comercial</option>
                            </select>
                        </td>
                        <td><small>${u.fecha_registro ? u.fecha_registro.replace("T", " ").split(".")[0] : '-'}</small></td>
                        <td><span class="state-badge ${u.activo ? 'cierre' : ''}">${u.activo ? 'Activo en Odoo' : 'Inactivo'}</span></td>
                        <td>
                            <button class="btn btn-secondary" onclick="adminResetPassword('${u.email}')" style="padding: 0.3rem 0.65rem; font-size: 0.78rem;">🔒 Restablecer Clave</button>
                        </td>
                    `;
                    tbody.appendChild(row);
                });
            }
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="6" class="table-empty">Error cargando lista de usuarios.</td></tr>';
            console.error(err);
        }
    };

    window.cambiarRolUsuario = async function(email, nuevoRol) {
        try {
            const res = await fetch("/api/admin/cambiar-rol", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, nuevo_rol: nuevoRol })
            });
            if (res.ok) {
                alert(`✅ Rol de ${email} actualizado correctamente.`);
            } else {
                const err = await res.json();
                alert(`❌ Error al cambiar rol: ${err.detail || 'Error en servidor'}`);
            }
        } catch (err) {
            alert("❌ Error de red al cambiar rol.");
        }
    };

    window.adminResetPassword = async function(email) {
        const newPassword = prompt(`Introduce la nueva contraseña para el usuario ${email}:`);
        if (!newPassword || newPassword.trim().length < 6) {
            if (newPassword !== null) alert("❌ La contraseña debe tener al menos 6 caracteres.");
            return;
        }
        try {
            const res = await fetch("/api/auth/reset-password", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, password: newPassword.trim() })
            });
            if (res.ok) {
                alert(`✅ Contraseña de ${email} restablecida exitosamente.`);
            } else {
                const err = await res.json();
                alert(`❌ Error: ${err.detail || 'No se pudo restablecer'}`);
            }
        } catch (err) {
            alert("❌ Error de red.");
        }
    };

    // Run user session setup
    initUserSession();

    // Fetch and render KPIs
    async function loadKPIs() {
        try {
            const res = await fetch("/api/resumen");
            if (res.ok) {
                const data = await res.json();
                if (kpiCobrables) kpiCobrables.textContent = new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(data.total_por_cobrar_usd);
                if (kpiSinAsignar) kpiSinAsignar.textContent = new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(data.pagos_sin_asignar_usd);
                const kpiSinAsignarVes = document.getElementById("kpi-sin-asignar-ves");
                if (kpiSinAsignarVes) kpiSinAsignarVes.textContent = "Bs. " + new Intl.NumberFormat('es-VE', { minimumFractionDigits: 2 }).format(data.pagos_sin_asignar_ves || 0);
                if (kpiAlertas) {
                    kpiAlertas.textContent = data.alertas_reconciliacion;
                    if (data.alertas_reconciliacion > 0) {
                        kpiAlertas.classList.add("danger");
                    } else {
                        kpiAlertas.classList.remove("danger");
                    }
                }
            }
        } catch (err) {
            console.error("Error loading KPIs:", err);
        }
    }

    // Abre el modal de vinculación manual pre-cargado con un pago de la
    // tabla unificada "Pagos Pendientes por Asociar" (fusiona lo que antes
    // eran dos paneles/listas separados con datos potencialmente distintos).
    window.abrirModalVincularManual = function(idx) {
        const item = currentSugerenciasList[idx];
        const modal = document.getElementById("modal-vincular-manual");
        if (!item || !modal) return;
        const esVes = item.moneda_pago === "VES";
        // saldo_pago ya viene en USD (equivalente BCV) desde el backend;
        // reconvertir a la moneda original para reusar selectPayment() tal
        // cual, que trabaja con el monto en moneda original + recalcula
        // los equivalentes en vivo.
        const bcv = item.tasa_bcv || 36.5;
        const binance = item.tasa_binance || bcv;
        const montoOriginalMoneda = esVes ? item.saldo_pago * bcv : item.saldo_pago;
        const payment = {
            pago_id: item.pago_id,
            cliente_id: item.cliente_id,
            cliente_nombre: item.cliente_nombre,
            moneda: item.moneda_pago,
            monto: montoOriginalMoneda,
            fecha: item.pago_fecha,
            equiv_usd_bcv: item.saldo_pago,
            equiv_usd_binance: esVes ? montoOriginalMoneda / binance : item.saldo_pago,
        };
        modal.style.display = "flex";
        selectPayment(payment, null);
    };

    window.cerrarModalVincularManual = function() {
        const modal = document.getElementById("modal-vincular-manual");
        if (modal) modal.style.display = "none";
    };

    // Handle payment selection (desde el modal de vinculación manual)
    async function selectPayment(payment, element) {
        if (element) {
            document.querySelectorAll(".payment-item").forEach(item => item.classList.remove("active"));
            element.classList.add("active");
        }

        selectedPayment = payment;
        
        formPagoId.value = payment.pago_id;
        formClienteNombre.value = payment.cliente_nombre;
        formClienteId.value = payment.cliente_id;
        formPagoMoneda.textContent = payment.moneda;
        formPagoDisponible.value = payment.monto.toFixed(2);
        
        formSoSelect.innerHTML = '<option value="">Cargando órdenes del cliente...</option>';
        formSoSelect.disabled = true;
        formMontoAplicar.value = "";
        formMontoAplicar.disabled = true;
        btnSubmit.disabled = true;

        // Reset and show/hide VES converter elements
        if (payment.moneda === "VES") {
            groupFechaHoraPago.style.display = "block";
            groupTasasReferencia.style.display = "block";
            
            // Prefill date and hour from payment
            let pDate = new Date().toISOString().split("T")[0];
            let pHour = "12:00";
            if (payment.fecha) {
                const parts = payment.fecha.split("T");
                if (parts[0]) pDate = parts[0];
                if (parts[1]) pHour = parts[1].substring(0, 5);
            }
            formPagoFecha.value = pDate;
            formPagoHora.value = pHour;
            
            // Fetch rates for this prefilled date/hour
            updateVESCalculatedEquivalents();
        } else {
            groupFechaHoraPago.style.display = "none";
            groupTasasReferencia.style.display = "none";
        }
        
        try {
            const res = await fetch(`/api/ordenes-pendientes/${payment.cliente_id}`);
            if (res.ok) {
                const orders = await res.json();
                if (orders.length === 0) {
                    formSoSelect.innerHTML = '<option value="">No hay órdenes pendientes para este cliente.</option>';
                    return;
                }
                
                formSoSelect.innerHTML = '<option value="">Selecciona una orden de venta...</option>';
                orders.forEach(o => {
                    const opt = document.createElement("option");
                    opt.value = o.so_id;
                    opt.textContent = `${o.so_id} - ${o.fecha} (Saldo: ${new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(o.saldo_pendiente)} / Total: ${new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(o.monto_total)})`;
                    formSoSelect.appendChild(opt);
                });
                
                formSoSelect.disabled = false;
                formMontoAplicar.disabled = false;
                
                // If it is VES, we recommend applying the calculated Binance USD amount
                if (payment.moneda === "VES") {
                    formMontoAplicar.value = payment.equiv_usd_binance.toFixed(2);
                } else {
                    formMontoAplicar.value = payment.monto.toFixed(2);
                }
                formMontoAplicar.max = payment.moneda === "VES" ? payment.equiv_usd_binance * 1.5 : payment.monto;
                btnSubmit.disabled = false;
            }
        } catch (err) {
            formSoSelect.innerHTML = '<option value="">Error al cargar órdenes.</option>';
            console.error("Error fetching client orders:", err);
        }
    }

    // Trigger update when date/hour changes on payment allocator
    if (formPagoFecha && formPagoHora) {
        formPagoFecha.addEventListener("change", updateVESCalculatedEquivalents);
        formPagoHora.addEventListener("change", updateVESCalculatedEquivalents);
    }

    async function updateVESCalculatedEquivalents() {
        if (!selectedPayment || selectedPayment.moneda !== "VES") return;
        const fecha = formPagoFecha.value;
        const hora = formPagoHora.value;
        if (!fecha || !hora) return;

        try {
            const res = await fetch(`/api/config/tasa-referencia?fecha=${fecha}&hora=${hora}`);
            if (res.ok) {
                const data = await res.json();
                const bcv = data.tasa_bcv;
                const binance = data.tasa_binance;

                lblTasaBcv.textContent = bcv.toFixed(4);
                lblTasaBinance.textContent = binance.toFixed(4);

                const amt = selectedPayment.monto;
                const eqBcv = amt / bcv;
                const eqBinance = amt / binance;

                const fmt = (v) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(v);
                lblEqBcv.textContent = fmt(eqBcv);
                lblEqBinance.textContent = fmt(eqBinance);

                // Auto update form amount to apply (using Binance by default)
                formMontoAplicar.value = eqBinance.toFixed(2);
                formMontoAplicar.max = eqBinance * 1.5;
            }
        } catch (err) {
            console.error("Error fetching reference rates:", err);
        }
    }

    // Submit Vinculacion
    vinculacionForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        if (!selectedPayment) return;
        
        const payload = {
            pago_id: formPagoId.value,
            so_id: formSoSelect.value,
            monto_aplicado: parseFloat(formMontoAplicar.value)
        };
        
        btnSubmit.disabled = true;
        btnSubmit.textContent = "Procesando...";
        
        try {
            const res = await fetch("/api/vincular", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            
            if (res.ok) {
                alert("✅ Cobro asignado y vinculado con éxito. El motor recalculando balances en segundo plano.");
                vinculacionForm.reset();
                formSoSelect.innerHTML = '<option value="">Selecciona una orden...</option>';
                formSoSelect.disabled = true;
                formMontoAplicar.disabled = true;
                btnSubmit.disabled = true;
                btnSubmit.textContent = "Asignar Cobro";
                selectedPayment = null;

                groupFechaHoraPago.style.display = "none";
                groupTasasReferencia.style.display = "none";
                window.cerrarModalVincularManual();

                loadKPIs();
                if (typeof loadCobranzaUnificado === "function") loadCobranzaUnificado();
                loadBandeja();
            } else {
                const errData = await res.json();
                alert(`❌ Error al vincular: ${errData.detail || "Error desconocido"}`);
                btnSubmit.disabled = false;
                btnSubmit.textContent = "Asignar Cobro";
            }
        } catch (err) {
            alert("❌ Error de red al vincular el cobro.");
            btnSubmit.disabled = false;
            btnSubmit.textContent = "Asignar Cobro";
            console.error(err);
        }
    });

    // Fase 3: aprobación de descuento de sistema (Bandeja 1) -- SOLO ajusta
    // saldos internos de CxC, NUNCA factura ni escribe nada en Odoo.
    // El "Aprobar Descuento" de la Bandeja 1 se retiró en el rediseño de
    // septiembre 2026 (decisión del usuario): la aprobación pasa a ser el
    // acto de facturar en Odoo, y ahí la orden desaparece de la bandeja.
    // El endpoint /api/facturacion/aprobar-descuento-sistema sigue vivo y
    // los descuentos ya aprobados se siguen restando de lo pendiente por
    // aplicar -- solo dejó de haber una vía para crear nuevos desde acá.


    // Cuentas por Cobrar agrupadas por cliente (estilo "Aged Receivable" de
    // Odoo) -- fila resumen por cliente, expandible a documentos. Incluye la
    // grilla de priorización de cobro por color (antigüedad x monto) y los
    // filtros/orden/buscador de la tabla, agosto 2026.
    let fullClientesCxc = [];

    // Clasifica por RANKING relativo entre los clientes con saldo pendiente
    // (no por límites de monto fijos, que quedan obsoletos apenas cambia el
    // tamaño de la cartera) -- el 25% con la combinación más urgente de
    // antigüedad + monto es "Crítico", el siguiente 25% "Alto", etc. Un
    // cliente puede pasar de Crítico a Alto simplemente porque apareció
    // otro cliente peor, no porque su propia deuda cambió.
    function _clasificarPorRanking(conSaldo) {
        const n = conSaldo.length;
        const porAntiguedad = [...conSaldo].sort((a, b) => (b.dias_vencido_max || 0) - (a.dias_vencido_max || 0));
        const porMonto = [...conSaldo].sort((a, b) => (b.saldo_priorizacion || 0) - (a.saldo_priorizacion || 0));
        const rankAntiguedad = new Map();
        porAntiguedad.forEach((c, i) => rankAntiguedad.set(c.cliente_id, i));
        const rankMonto = new Map();
        porMonto.forEach((c, i) => rankMonto.set(c.cliente_id, i));

        const clasePorCliente = new Map();
        conSaldo.forEach(c => {
            // Promedio de percentiles (0 = más urgente, 1 = menos urgente)
            const pctAnt = n > 1 ? rankAntiguedad.get(c.cliente_id) / (n - 1) : 0;
            const pctMonto = n > 1 ? rankMonto.get(c.cliente_id) / (n - 1) : 0;
            const urgencia = (pctAnt + pctMonto) / 2;

            // "orden": posición del grupo de color (0 = más urgente/arriba),
            // usada para ordenar las tarjetas por color antes que por monto.
            let clase, label, orden;
            if (urgencia < 0.25) { clase = "prioridad-critico"; label = "🔴 Crítico"; orden = 0; }
            else if (urgencia < 0.50) { clase = "prioridad-alto"; label = "🟠 Alto"; orden = 1; }
            else if (urgencia < 0.75) { clase = "prioridad-medio"; label = "🟡 Medio"; orden = 2; }
            else { clase = "prioridad-bajo"; label = "🟢 Bajo"; orden = 3; }
            clasePorCliente.set(c.cliente_id, { clase, label, orden });
        });
        return clasePorCliente;
    }

    // Tamaño de la tarjeta proporcional al monto (área ~ monto, vía raíz
    // cuadrada -- si fuera lineal, un cliente con 10x más deuda tendría un
    // cuadro 10x más ancho Y 10x más alto, dominando toda la grilla).
    // PRIORIDAD_GRID_UNIT_PX debe calzar con "minmax(...)"/"grid-auto-rows"
    // de ``.prioridad-grid`` en styles.css -- es la celda base de la que
    // cada tarjeta ocupa N columnas x M filas (``grid-column``/``grid-row:
    // span``), para que auto-flow: dense pueda reempacar sin dejar huecos.
    const PRIORIDAD_GRID_UNIT_PX = 65;
    const PRIORIDAD_CARD_MIN_PX = 130;
    const PRIORIDAD_CARD_MAX_PX = 260;

    function _tamanoCardPrioridad(monto, maxMonto) {
        if (!maxMonto || maxMonto <= 0) return PRIORIDAD_CARD_MIN_PX;
        const ratio = Math.sqrt(Math.max(0, monto) / maxMonto);
        return Math.round(PRIORIDAD_CARD_MIN_PX + ratio * (PRIORIDAD_CARD_MAX_PX - PRIORIDAD_CARD_MIN_PX));
    }

    function _ordenarPorColorYMonto(lista, clasePorCliente) {
        return [...lista].sort((a, b) => {
            const ordenA = clasePorCliente.get(a.cliente_id)?.orden ?? 9;
            const ordenB = clasePorCliente.get(b.cliente_id)?.orden ?? 9;
            if (ordenA !== ordenB) return ordenA - ordenB;
            return (b.saldo_priorizacion || 0) - (a.saldo_priorizacion || 0);
        });
    }

    function _crearCardPrioridad(c, clasePorCliente, maxMonto, fmt) {
        const { clase, label } = clasePorCliente.get(c.cliente_id) || { clase: "prioridad-bajo", label: "" };
        const px = _tamanoCardPrioridad(c.saldo_priorizacion || 0, maxMonto);
        const card = document.createElement("div");
        card.className = `prioridad-card ${clase}`;
        // Spans de grid (no ancho/alto en px) -- ver comentario de
        // PRIORIDAD_GRID_UNIT_PX: así auto-flow: dense puede reempacar
        // tarjetas chicas en los huecos que dejan las grandes.
        const colSpan = Math.max(1, Math.round(px / PRIORIDAD_GRID_UNIT_PX));
        const rowSpan = Math.max(1, Math.round((px * 0.72) / PRIORIDAD_GRID_UNIT_PX));
        card.style.gridColumn = `span ${colSpan}`;
        card.style.gridRow = `span ${rowSpan}`;
        // Tipografía también escala un poco con el tamaño, para que las
        // tarjetas chicas no queden con texto más grande que la propia tarjeta.
        const scale = (0.8 + 0.4 * (px - PRIORIDAD_CARD_MIN_PX) / (PRIORIDAD_CARD_MAX_PX - PRIORIDAD_CARD_MIN_PX)).toFixed(2);
        card.style.fontSize = `${scale}rem`;
        card.innerHTML = `
            <div class="prioridad-cliente" title="${c.cliente_nombre || c.cliente_id}">${c.cliente_nombre || c.cliente_id}</div>
            ${(c.tiene_saldo_a_favor ? `<div class="prioridad-favor" title="La empresa le debe al cliente: pagó de más o devolvió mercancía ya pagada">↩ A favor ${fmt(c.saldo_a_favor)}</div>` : "")}
            <div class="prioridad-saldos">
                <div title="Saldo contra la Venta Real de la orden en Odoo">
                    <span class="prioridad-saldo-label">Orden</span>
                    <span class="prioridad-saldo-monto">${fmt((c.saldos || {}).venta_real || 0)}</span>
                </div>
                <div title="Saldo contra el Teórico Neto de la Lista USD">
                    <span class="prioridad-saldo-label">Teórico USD</span>
                    <span class="prioridad-saldo-monto">${fmt((c.saldos || {}).teorico_usd || 0)}</span>
                </div>
            </div>
            <div class="prioridad-meta">${label} · ${c.dias_vencido_max || 0} días vencido · ${c.vendedor || 'Sin Vendedor'}</div>
        `;
        card.addEventListener("click", () => {
            document.querySelectorAll(".prioridad-card.selected").forEach(el => el.classList.remove("selected"));
            const searchEl = document.getElementById("reporte-cliente-search");
            if (searchEl) {
                const already = searchEl.value === (c.cliente_nombre || "");
                searchEl.value = already ? "" : (c.cliente_nombre || "");
                if (!already) card.classList.add("selected");
                applyReporteClienteFilters();
            }
            document.getElementById("reporte-cxc-cliente-table-body")?.scrollIntoView({ behavior: "smooth", block: "center" });
        });
        return card;
    }

    let fullPrioridadClientes = [];

    function renderPrioridadGrid(clientes) {
        fullPrioridadClientes = clientes;
        const grid = document.getElementById("reporte-prioridad-grid");
        if (!grid) return;

        const antiguedadVal = document.getElementById("reporte-prioridad-antiguedad-filter")?.value || "*";
        const vendedorVal = document.getElementById("reporte-prioridad-vendedor-filter")?.value || "*";
        const agruparVal = document.getElementById("reporte-prioridad-agrupar")?.value || "ninguno";

        // Poblar dropdown de vendedores (una sola vez por carga de datos)
        const vendedorSelect = document.getElementById("reporte-prioridad-vendedor-filter");
        if (vendedorSelect && !vendedorSelect.dataset.populated) {
            const vendedores = [...new Set(clientes.map(c => c.vendedor).filter(Boolean))].sort();
            vendedores.forEach(v => {
                const opt = document.createElement("option");
                opt.value = v;
                opt.textContent = v;
                vendedorSelect.appendChild(opt);
            });
            vendedorSelect.dataset.populated = "true";
        }

        let conSaldo = clientes.filter(c => (c.saldo_priorizacion || 0) > 0.05);

        if (vendedorVal !== "*") {
            conSaldo = conSaldo.filter(c => c.vendedor === vendedorVal);
        }
        if (antiguedadVal !== "*") {
            conSaldo = conSaldo.filter(c => {
                const dv = c.dias_vencido_max || 0;
                if (antiguedadVal === "vencido_total") return dv > 0;
                if (antiguedadVal === "vigentes") return dv <= 0;
                if (antiguedadVal === "1_30") return dv >= 1 && dv <= 30;
                if (antiguedadVal === "31_60") return dv >= 31 && dv <= 60;
                if (antiguedadVal === "61_90") return dv >= 61 && dv <= 90;
                if (antiguedadVal === "mas_90") return dv > 90;
                return true;
            });
        }

        if (conSaldo.length === 0) {
            grid.innerHTML = '<div class="table-empty">No hay clientes con saldo pendiente que coincidan con los filtros.</div>';
            return;
        }

        const fmt = (v) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(v || 0);
        const clasePorCliente = _clasificarPorRanking(conSaldo);
        const maxMonto = Math.max(...conSaldo.map(c => c.saldo_priorizacion || 0));
        grid.innerHTML = "";

        if (agruparVal === "ninguno") {
            // Prioritarios arriba: ordenado por color (grupo de urgencia)
            // primero, y dentro de cada color por monto de mayor a menor.
            const ordenados = _ordenarPorColorYMonto(conSaldo, clasePorCliente);
            ordenados.forEach(c => grid.appendChild(_crearCardPrioridad(c, clasePorCliente, maxMonto, fmt)));
            return;
        }

        // Agrupado por Vendedor o por Antigüedad: un bloque por grupo, cada
        // uno ordenado igual (color, luego monto) puertas adentro.
        const grupos = new Map();
        const grupoKey = (c) => {
            if (agruparVal === "vendedor") return c.vendedor || "Sin Vendedor";
            const dv = c.dias_vencido_max || 0;
            if (dv <= 0) return "Vigentes";
            if (dv <= 30) return "Vencidas 1-30 días";
            if (dv <= 60) return "Vencidas 31-60 días";
            if (dv <= 90) return "Vencidas 61-90 días";
            return "Vencidas +90 días";
        };
        conSaldo.forEach(c => {
            const key = grupoKey(c);
            if (!grupos.has(key)) grupos.set(key, []);
            grupos.get(key).push(c);
        });
        // Orden de los grupos: por la suma de saldo del grupo, mayor a menor.
        const gruposOrdenados = [...grupos.entries()].sort(
            (a, b) => b[1].reduce((s, c) => s + (c.saldo_priorizacion || 0), 0)
                - a[1].reduce((s, c) => s + (c.saldo_priorizacion || 0), 0)
        );
        gruposOrdenados.forEach(([key, items]) => {
            const groupDiv = document.createElement("div");
            groupDiv.className = "prioridad-group";
            const totalGrupo = items.reduce((s, c) => s + (c.saldo_priorizacion || 0), 0);
            const header = document.createElement("div");
            header.className = "prioridad-group-header";
            header.innerHTML = `<span>${key}</span><span class="prioridad-group-count">${items.length} cliente${items.length !== 1 ? 's' : ''} · ${fmt(totalGrupo)}</span>`;
            groupDiv.appendChild(header);
            const cardsWrap = document.createElement("div");
            cardsWrap.className = "prioridad-grid";
            cardsWrap.style.marginBottom = "0";
            const ordenados = _ordenarPorColorYMonto(items, clasePorCliente);
            ordenados.forEach(c => cardsWrap.appendChild(_crearCardPrioridad(c, clasePorCliente, maxMonto, fmt)));
            groupDiv.appendChild(cardsWrap);
            grid.appendChild(groupDiv);
        });
    }

    function renderReporteClienteTable(clientes) {
        const tbody = document.getElementById("reporte-cxc-cliente-table-body");
        if (!tbody) return;
        const fmt = (v) => v == null ? "-" : new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(v);
        const colorFmt = (v) => v == null ? "-" : `<span style="color:${v >= 0 ? '#dc2626' : '#059669'}"><strong>${fmt(v)}</strong></span>`;
        // Monto original / saldo pendiente en la misma celda (pedido
        // explícito del usuario) -- solo disponible para documentos
        // "orden" (montos_originales); los pagos huérfanos no tienen
        // un "original" propio distinto de su saldo.
        const cellFmt = (original, saldo) => {
            if (original == null) return colorFmt(saldo);
            return `${fmt(original)}<br><span style="font-size:0.85em">${colorFmt(saldo)}</span>`;
        };

        if (clientes.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="table-empty">No hay saldos pendientes que coincidan con los filtros.</td></tr>';
            return;
        }

        tbody.innerHTML = "";
        clientes.forEach((c, idx) => {
            const rowId = `cxc-cliente-detalle-${idx}`;
            const row = document.createElement("tr");
            row.style.cursor = "pointer";
            row.innerHTML = `
                <td><span id="${rowId}-toggle">▶</span></td>
                <td><strong>${c.cliente_nombre || c.cliente_id}</strong></td>
                <td><small>${c.vendedor || 'Sin Vendedor'}</small></td>
                <td>${c.dias_vencido_max || 0}</td>
                <td>${colorFmt(c.saldos.teorico_bs)}</td>
                <td>${colorFmt(c.saldos.teorico_usd)}</td>
                <td>${colorFmt(c.saldos.venta_real)}</td>
                <td>${colorFmt(c.saldos.factura_real)}</td>
            `;
            row.addEventListener("click", () => {
                const existing = document.getElementById(rowId);
                const toggle = document.getElementById(`${rowId}-toggle`);
                if (existing) {
                    existing.remove();
                    if (toggle) toggle.textContent = "▶";
                    return;
                }
                if (toggle) toggle.textContent = "▼";
                const detailRow = document.createElement("tr");
                detailRow.id = rowId;
                const docsHtml = (c.documentos || []).map(d => {
                    const orig = d.montos_originales || {};
                    const ref = d.tipo === 'orden'
                        ? (d.factura_numero ? `${d.so_id} / ${d.factura_numero}` : d.so_id)
                        : (d.numero_pago_odoo || d.pago_id);
                    return `
                    <tr>
                        <td>${ref}</td>
                        <td>${d.descripcion || (d.tipo === 'pago_huerfano' ? 'Pago sin aplicar' : '')}</td>
                        <td>${d.fecha || ''}</td>
                        <td>${d.dias_vencido || 0}</td>
                        <td>${cellFmt(orig.teorico_bs, d.saldos.teorico_bs)}</td>
                        <td>${cellFmt(orig.teorico_usd, d.saldos.teorico_usd)}</td>
                        <td>${cellFmt(orig.venta_real, d.saldos.venta_real)}</td>
                        <td>${cellFmt(orig.factura_real, d.saldos.factura_real)}</td>
                    </tr>
                    `;
                }).join("");
                const totalRow = `
                    <tr style="border-top:2px solid #cbd5e1;font-weight:600">
                        <td colspan="4">Total (saldo neto)</td>
                        <td>${colorFmt(c.saldos.teorico_bs)}</td>
                        <td>${colorFmt(c.saldos.teorico_usd)}</td>
                        <td>${colorFmt(c.saldos.venta_real)}</td>
                        <td>${colorFmt(c.saldos.factura_real)}</td>
                    </tr>
                `;
                detailRow.innerHTML = `
                    <td colspan="8" style="background:#f8fafc;padding:0.75rem 1.5rem">
                        <table class="cxc-table" style="margin:0">
                            <thead>
                                <tr>
                                    <th>Referencia (Orden / Factura)</th>
                                    <th>Descripción</th>
                                    <th>Fecha</th>
                                    <th>Días Vencido</th>
                                    <th>Teórico Lista BS ($)</th>
                                    <th>Teórico Lista USD ($)</th>
                                    <th>Venta Real ($)</th>
                                    <th>Factura Neta Real ($)</th>
                                </tr>
                            </thead>
                            <tbody>${docsHtml || '<tr><td colspan="8" class="table-empty">Sin documentos.</td></tr>'}${docsHtml ? totalRow : ''}</tbody>
                        </table>
                    </td>
                `;
                row.after(detailRow);
            });
            tbody.appendChild(row);
        });
    }

    function applyReporteClienteFilters() {
        const antiguedadVal = document.getElementById("reporte-cliente-antiguedad-filter")?.value || "*";
        const vendedorVal = document.getElementById("reporte-cliente-vendedor-filter")?.value || "*";
        const sortVal = document.getElementById("reporte-cliente-sort")?.value || "saldo_desc";
        const searchVal = (document.getElementById("reporte-cliente-search")?.value || "").toLowerCase().trim();

        let filtered = fullClientesCxc.filter(c => {
            const dv = c.dias_vencido_max || 0;
            const matchVendedor = (vendedorVal === "*") || (c.vendedor === vendedorVal);

            let matchAntiguedad = true;
            if (antiguedadVal === "vencido_total") matchAntiguedad = (dv > 0);
            else if (antiguedadVal === "vigentes") matchAntiguedad = (dv <= 0);
            else if (antiguedadVal === "1_30") matchAntiguedad = (dv >= 1 && dv <= 30);
            else if (antiguedadVal === "31_60") matchAntiguedad = (dv >= 31 && dv <= 60);
            else if (antiguedadVal === "61_90") matchAntiguedad = (dv >= 61 && dv <= 90);
            else if (antiguedadVal === "mas_90") matchAntiguedad = (dv > 90);

            const matchSearch = !searchVal ||
                (c.cliente_nombre && c.cliente_nombre.toLowerCase().includes(searchVal)) ||
                (c.vendedor && c.vendedor.toLowerCase().includes(searchVal));

            return matchVendedor && matchAntiguedad && matchSearch;
        });

        filtered.sort((a, b) => {
            switch (sortVal) {
                case "saldo_asc": return (a.saldo_priorizacion || 0) - (b.saldo_priorizacion || 0);
                case "antiguedad_desc": return (b.dias_vencido_max || 0) - (a.dias_vencido_max || 0);
                case "antiguedad_asc": return (a.dias_vencido_max || 0) - (b.dias_vencido_max || 0);
                case "cliente_asc": return (a.cliente_nombre || "").localeCompare(b.cliente_nombre || "");
                case "vendedor_asc": return (a.vendedor || "").localeCompare(b.vendedor || "");
                case "saldo_desc":
                default: return (b.saldo_priorizacion || 0) - (a.saldo_priorizacion || 0);
            }
        });

        renderReporteClienteTable(filtered);
    }
    window.applyReporteClienteFilters = applyReporteClienteFilters;

    async function loadReporteCxcCliente() {
        const tbody = document.getElementById("reporte-cxc-cliente-table-body");
        if (!tbody) return;
        tbody.innerHTML = '<tr><td colspan="8" class="table-empty">Cargando cuentas por cobrar por cliente...</td></tr>';
        try {
            const res = await fetch("/api/reporte-cxc-cliente");
            if (!res.ok) throw new Error("HTTP " + res.status);
            const data = await res.json();
            fullClientesCxc = data.clientes || [];

            ["reporte-prioridad-antiguedad-filter", "reporte-prioridad-vendedor-filter", "reporte-prioridad-agrupar"].forEach(id => {
                const el = document.getElementById(id);
                if (el && !el.dataset.listenerAttached) {
                    el.addEventListener("change", () => renderPrioridadGrid(fullPrioridadClientes));
                    el.dataset.listenerAttached = "true";
                }
            });
            renderPrioridadGrid(fullClientesCxc);

            const vendedorSelect = document.getElementById("reporte-cliente-vendedor-filter");
            if (vendedorSelect) {
                const currentVal = vendedorSelect.value || "*";
                const vendedores = [...new Set(fullClientesCxc.map(c => c.vendedor).filter(Boolean))].sort();
                vendedorSelect.innerHTML = '<option value="*">Todos los Vendedores</option>';
                vendedores.forEach(v => {
                    const opt = document.createElement("option");
                    opt.value = v;
                    opt.textContent = v;
                    vendedorSelect.appendChild(opt);
                });
                vendedorSelect.value = currentVal;
                if (!vendedorSelect.dataset.listenerAttached) {
                    vendedorSelect.addEventListener("change", applyReporteClienteFilters);
                    vendedorSelect.dataset.listenerAttached = "true";
                }
            }

            ["reporte-cliente-antiguedad-filter", "reporte-cliente-sort"].forEach(id => {
                const el = document.getElementById(id);
                if (el && !el.dataset.listenerAttached) {
                    el.addEventListener("change", applyReporteClienteFilters);
                    el.dataset.listenerAttached = "true";
                }
            });
            const searchEl = document.getElementById("reporte-cliente-search");
            if (searchEl && !searchEl.dataset.listenerAttached) {
                searchEl.addEventListener("input", applyReporteClienteFilters);
                searchEl.dataset.listenerAttached = "true";
            }

            applyReporteClienteFilters();
        } catch (err) {
            console.error("Error loading reporte-cxc-cliente:", err);
            tbody.innerHTML = '<tr><td colspan="8" class="table-empty">Error al cargar cuentas por cobrar por cliente.</td></tr>';
        }
    }
    window.loadReporteCxcCliente = loadReporteCxcCliente;

    // Fetch and render the 3 Dashboard Approval Trays
    async function loadBandeja() {
        try {
            if (bandeja1TableBody) bandeja1TableBody.innerHTML = '<tr><td colspan="9" class="table-empty">Cargando órdenes pendientes por facturar...</td></tr>';
            if (bandeja2TableBody) bandeja2TableBody.innerHTML = '<tr><td colspan="8" class="table-empty">Cargando órdenes pendientes por nota de crédito...</td></tr>';
            if (bandeja3TableBody) bandeja3TableBody.innerHTML = '<tr><td colspan="7" class="table-empty">Cargando facturas pendientes por IVA...</td></tr>';
            if (bandejaAuditoriaPreciosTableBody) bandejaAuditoriaPreciosTableBody.innerHTML = '<tr><td colspan="9" class="table-empty">Cargando órdenes en auditoría de precios...</td></tr>';
            if (bandejaEnProcesoDePagoTableBody) bandejaEnProcesoDePagoTableBody.innerHTML = '<tr><td colspan="10" class="table-empty">Cargando órdenes en proceso de pago...</td></tr>';
            if (bandejaPendientesCerrarTableBody) bandejaPendientesCerrarTableBody.innerHTML = '<tr><td colspan="7" class="table-empty">Cargando pendientes por cerrar...</td></tr>';

            const res = await fetch("/api/bandeja");
            if (res.ok) {
                const data = await res.json();
                const fmt = (v) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(v);

                // Handle legacy array format or structured 3 trays dict
                const tray1 = data.ordenes_por_facturar || (Array.isArray(data) ? data.filter(x => !x.facturada) : []);
                const tray2 = data.notas_credito_pendientes || (Array.isArray(data) ? data.filter(x => x.ncs_calculadas > 0) : []);
                const tray3 = data.iva_pendiente_agentes || [];
                const tray4 = data.auditoria_precios || [];
                const trayEnProceso = data.en_proceso_de_pago || [];

                // Render Tray 1
                if (bandeja1TableBody) {
                    if (tray1.length === 0) {
                        bandeja1TableBody.innerHTML = '<tr><td colspan="8" class="table-empty">No hay órdenes pendientes por facturar.</td></tr>';
                    } else {
                        bandeja1TableBody.innerHTML = "";
                        tray1.forEach(item => {
                            const row = document.createElement("tr");
                            const isAgent = item.wh_iva_agent ? `<span class="state-badge cierre" style="background:#e0f2fe;color:#0369a1">Agente (${item.wh_iva_rate || 75}%)</span>` : '<span class="state-badge">No</span>';
                            const descText = item.descuento_pendiente_por_aplicar > 0
                                ? `${fmt(item.descuento_pendiente_por_aplicar)} (${((item.descuento_pendiente_por_aplicar / (item.orden_neto_odoo || 1)) * 100).toFixed(1)}%)`
                                : '$0.00 (0%)';

                            // Estado: por cuál referencia salió de CxC, y si el
                            // pago está confirmado. Antes de facturar CONCILIADO
                            // es estructuralmente imposible (Odoo no puede
                            // reconciliar contra un documento que no existe), así
                            // que casi todo estará "en proceso de pago" -- que es
                            // justo lo que hay que poder distinguir.
                            const REFERENCIA_TXT = {
                                teorico_usd: "Teórico USD",
                                teorico_bs: "Teórico BS",
                                subtotal_sin_iva: "Subtotal (falta el IVA)",
                                venta_real: "Venta Real",
                                factura_real: "Factura Neta",
                                odoo: "Odoo la da por saldada",
                            };
                            const refTxt = REFERENCIA_TXT[item.referencia_pago] || "—";
                            const confirmado = item.pago_confirmado !== false;
                            // La rama del subtotal es la única que llega a
                            // esta bandeja SIN salir de CxC: el cliente pagó la
                            // mercancía y todavía debe el IVA, por retener o por
                            // pagar. Se factura igual para que nazca la
                            // obligación legal del impuesto.
                            const soloSubtotal = item.referencia_pago === "subtotal_sin_iva";
                            const estadoHtml = (soloSubtotal
                                ? `<span class="state-badge" style="background:#fef9c3;color:#854d0e">Pagada — IVA pendiente</span>`
                                : `<span class="state-badge cierre" style="background:#dcfce7;color:#166534">Pagada</span>`)
                                + `
                                <div style="font-size:0.7rem;opacity:0.85;margin-top:2px">vs ${refTxt}</div>` +
                                (confirmado
                                    ? ''
                                    : `<div style="font-size:0.68rem;color:#1d4ed8" title="${(item.cxc_routing_motivo || '').replace(/"/g, '&quot;')}">⏳ en proceso de pago</div>`);

                            // Sin teórico cumplido (salió por Venta Real, Factura
                            // Neta u Odoo) no se muestra ninguno: poner uno
                            // sugeriría que se cumplió y no es así.
                            const teoricoHtml = item.teorico_neto_referencia != null
                                ? `<strong>${fmt(item.teorico_neto_referencia)}</strong>`
                                : '<span style="opacity:0.5">—</span>';

                            row.innerHTML = `
                                <td><strong>${item.so_id}</strong></td>
                                <td>${item.cliente_nombre || item.so_id}</td>
                                <td>${isAgent}</td>
                                <td>${item.fecha || ''}</td>
                                <td>${fmt(item.orden_neto_odoo || 0)}</td>
                                <td>${estadoHtml}</td>
                                <td>${teoricoHtml}</td>
                                <td><strong style="color:#d97706">${descText}</strong></td>
                            `;
                            bandeja1TableBody.appendChild(row);
                        });
                    }
                }

                // Render Tray 2
                if (bandeja2TableBody) {
                    if (tray2.length === 0) {
                        bandeja2TableBody.innerHTML = '<tr><td colspan="8" class="table-empty">No hay órdenes pendientes por Nota de Crédito.</td></tr>';
                    } else {
                        bandeja2TableBody.innerHTML = "";
                        tray2.forEach(item => {
                            const row = document.createElement("tr");
                            // Misma lectura que la Bandeja 1: por cuál
                            // referencia salió de CxC y si el pago está
                            // confirmado.
                            const REF2 = {
                                teorico_usd: "Teórico USD",
                                teorico_bs: "Teórico BS",
                                venta_real: "Venta Real",
                                factura_real: "Factura Neta",
                                odoo: "Odoo la da por saldada",
                                subtotal_sin_iva: "Subtotal (falta el IVA)",
                            };
                            const estado2 = `<span class="state-badge cierre" style="background:#dcfce7;color:#166534">Pagada</span>
                                <div style="font-size:0.7rem;opacity:0.85;margin-top:2px">vs ${REF2[item.referencia_pago] || "—"}</div>`
                                + (item.pago_confirmado === false
                                    ? `<div style="font-size:0.68rem;color:#1d4ed8" title="${(item.cxc_routing_motivo || '').replace(/"/g, '&quot;')}">⏳ en proceso de pago</div>`
                                    : '');
                            const teorico2 = item.teorico_neto_referencia != null
                                ? `<strong>${fmt(item.teorico_neto_referencia)}</strong>`
                                : '<span style="opacity:0.5">—</span>';
                            // La N/C es gravable: se muestra el subtotal y,
                            // debajo, el total con su impuesto.
                            const nc2 = `<strong style="color:#dc2626">${fmt(item.nc_subtotal || 0)} (${(item.nc_porcentaje || 0).toFixed(1)}%)</strong>`
                                + `<div style="font-size:0.7rem;opacity:0.85">con IVA ${fmt(item.nc_con_iva || 0)}</div>`
                                + (item.venta_bajo_lista > 0.05
                                    ? `<div style="font-size:0.68rem;color:#b45309" title="Se facturó por debajo de la lista sin una regla que lo sustente">⚠ bajo lista ${fmt(item.venta_bajo_lista)}</div>`
                                    : '');
                            row.innerHTML = `
                                <td><strong>${item.so_id}</strong></td>
                                <td>${item.cliente_nombre || item.so_id}</td>
                                <td><span class="state-badge">${item.factura_id || 'Odoo'}</span></td>
                                <td>${item.fecha || ''}</td>
                                <td>${fmt(item.factura_neta_subtotal || 0)}</td>
                                <td>${estado2}</td>
                                <td>${teorico2}</td>
                                <td>${nc2}</td>
                            `;
                            bandeja2TableBody.appendChild(row);
                        });
                    }
                }

                // La bandeja "Descuentos Pendientes por Aprobar" se fusionó
                // dentro de la Bandeja 2 en septiembre de 2026: con el
                // criterio del usuario son el mismo trabajo, y tenerlas
                // separadas obligaba a mirar en dos lados.
                // Render Tray 3
                if (bandeja3TableBody) {
                    if (tray3.length === 0) {
                        bandeja3TableBody.innerHTML = '<tr><td colspan="7" class="table-empty">No hay facturas pendientes por comprobante de retención IVA.</td></tr>';
                    } else {
                        bandeja3TableBody.innerHTML = "";
                        tray3.forEach(item => {
                            const row = document.createElement("tr");
                            row.innerHTML = `
                                <td><span class="state-badge">${item.factura_id}</span></td>
                                <td><strong>${item.so_id}</strong></td>
                                <td>${item.cliente_nombre}</td>
                                <td><span class="state-badge cierre" style="background:#e0f2fe;color:#0369a1">${item.wh_iva_rate || 75}%</span></td>
                                <td><strong style="color:#059669">${fmt(item.base_cobrada)}</strong></td>
                                <td><strong style="color:#2563eb">${fmt(item.retencion_iva_est)}</strong></td>
                                <td><span class="state-badge abiertas">${item.estado_comprobante || 'Pendiente'}</span></td>
                            `;
                            bandeja3TableBody.appendChild(row);
                        });
                    }
                }

                // Render Tray 4 (Bandeja de Auditoría de Precios)
                if (bandejaAuditoriaPreciosTableBody) {
                    if (tray4.length === 0) {
                        bandejaAuditoriaPreciosTableBody.innerHTML = '<tr><td colspan="9" class="table-empty">No hay órdenes en auditoría de precios.</td></tr>';
                    } else {
                        bandejaAuditoriaPreciosTableBody.innerHTML = "";
                        tray4.forEach(item => {
                            const row = document.createElement("tr");
                            row.innerHTML = `
                                <td><strong>${item.so_id}</strong></td>
                                <td>${item.cliente_nombre || item.so_id}</td>
                                <td>${item.fecha || ''}</td>
                                <td>${item.lista_aplicada_label || ''}</td>
                                <td>${item.ves_neta_teorica_iva != null ? fmt(item.ves_neta_teorica_iva) : '-'}</td>
                                <td>${item.usd_neta_teorica_iva != null ? fmt(item.usd_neta_teorica_iva) : '-'}</td>
                                <td>${item.venta_neta_real != null ? fmt(item.venta_neta_real) : '-'}</td>
                                <td><strong style="color:#dc2626">${item.total_facturado_neto != null ? fmt(item.total_facturado_neto) : '-'}</strong></td>
                                <td>${item.motivo || ''}</td>
                            `;
                            bandejaAuditoriaPreciosTableBody.appendChild(row);
                        });
                    }
                }

                // Render Tray "En Proceso de Pago" (precedente de Odoo citado por
                // el usuario -- ya salió de CxC, falta la conciliación bancaria)
                if (bandejaEnProcesoDePagoTableBody) {
                    if (trayEnProceso.length === 0) {
                        bandejaEnProcesoDePagoTableBody.innerHTML = '<tr><td colspan="10" class="table-empty">No hay órdenes en proceso de pago.</td></tr>';
                    } else {
                        bandejaEnProcesoDePagoTableBody.innerHTML = "";
                        trayEnProceso.forEach(item => {
                            const row = document.createElement("tr");
                            row.innerHTML = `
                                <td><strong>${item.so_id}</strong></td>
                                <td>${item.cliente_nombre || item.so_id}</td>
                                <td>${item.fecha || ''}</td>
                                <td>${item.facturada ? 'Sí' : 'No'}</td>
                                <td>${item.lista_aplicada_label || ''}</td>
                                <td>${item.ves_neta_teorica_iva != null ? fmt(item.ves_neta_teorica_iva) : '-'}</td>
                                <td>${item.usd_neta_teorica_iva != null ? fmt(item.usd_neta_teorica_iva) : '-'}</td>
                                <td>${item.venta_neta_real != null ? fmt(item.venta_neta_real) : '-'}</td>
                                <td>${item.total_facturado_neto != null ? fmt(item.total_facturado_neto) : '-'}</td>
                                <td><span class="state-badge" style="background:#dbeafe;color:#1d4ed8" title="${item.motivo || ''}">⏳ En proceso de pago</span></td>
                            `;
                            bandejaEnProcesoDePagoTableBody.appendChild(row);
                        });
                    }
                }

                // La bandeja "Pendientes por Cerrar" se elimino en
                // septiembre de 2026: contenia a todas las demas (397 filas,
                // cero propias) y se armaba con una segunda llamada al arbol
                // en otro endpoint, con otra tolerancia.
                // Render Discrepancias de Precios / Reglas
                if (bodyDisc) {
                    if (discrepancias.length === 0) {
                        bodyDisc.innerHTML = '<tr><td colspan="11" class="table-empty" style="color:#059669">✅ No se detectaron discrepancias de precios ni descuentos en el sistema.</td></tr>';
                    } else {
                        bodyDisc.innerHTML = discrepancias.map(d => `
                            <tr>
                                <td><strong>${escapeHtml(d.so_id)}</strong></td>
                                <td><span class="state-badge">${escapeHtml(d.factura_id || 'N/A')}</span></td>
                                <td>${escapeHtml(d.cliente_nombre)}</td>
                                <td><small>${escapeHtml(d.vendedor)}</small></td>
                                <td><span class="state-badge" style="background:#fef2f2; color:#dc2626; font-weight:600;">${escapeHtml(d.tipo)}</span></td>
                                <td><small>${escapeHtml(d.detalle)}</small></td>
                                <td>${fmt(d.esperado)}</td>
                                <td>${fmt(d.actual)}</td>
                                <td><strong style="color:#dc2626;">${fmt(d.diferencia_monto)}</strong></td>
                                <td>${(d.diferencia_porcentaje || 0).toFixed(1)}%</td>
                                <td>
                                    <button class="btn btn-secondary" onclick="aceptarAnomalia('${d.anomalia_id}', '${d.so_id}', '${d.tipo}')" style="padding:0.25rem 0.6rem; font-size:0.75rem;">Aceptar Anomalía</button>
                                </td>
                            </tr>
                        `).join('');
                    }
                }

                // Render Discrepancias Saldo CxC vs Residual Factura Odoo
                if (bodyFacturas) {
                    if (discFacturas.length === 0) {
                        bodyFacturas.innerHTML = '<tr><td colspan="9" class="table-empty" style="color:#059669">✅ Excelente: Todos los saldos de deudores en CxC coinciden con las facturas de Odoo.</td></tr>';
                    } else {
                        bodyFacturas.innerHTML = discFacturas.map(d => `
                            <tr>
                                <td><strong>${escapeHtml(d.so_id)}</strong></td>
                                <td><span class="state-badge" style="background:#e0f2fe; color:#0369a1; font-weight:600;">${escapeHtml(d.factura_id)}</span></td>
                                <td>${escapeHtml(d.cliente_nombre)}</td>
                                <td><small>${escapeHtml(d.vendedor)}</small></td>
                                <td><small>${d.fecha ? d.fecha.substring(0, 10) : ''}</small></td>
                                <td><strong style="color:#6d28d9;">${fmt(d.saldo_cxc)}</strong></td>
                                <td><strong style="color:#0369a1;">${fmt(d.saldo_factura_odoo)}</strong></td>
                                <td><strong style="color:#dc2626;">${fmt(d.diferencia)}</strong></td>
                                <td><span style="font-size:0.78rem; color:#475569;">${escapeHtml(d.causa_probable)}</span></td>
                            </tr>
                        `).join('');
                    }
                }

                // Render Anomalías Aceptadas
                if (bodyAceptadas) {
                    if (aceptadas.length === 0) {
                        bodyAceptadas.innerHTML = '<tr><td colspan="9" class="table-empty">No hay anomalías aceptadas en el historial.</td></tr>';
                    } else {
                        bodyAceptadas.innerHTML = aceptadas.map(a => `
                            <tr>
                                <td><small><code>${escapeHtml(a.anomalia_id)}</code></small></td>
                                <td><strong>${escapeHtml(a.so_id)}</strong></td>
                                <td>${escapeHtml(a.factura_id)}</td>
                                <td>${escapeHtml(a.cliente_nombre)}</td>
                                <td><span class="state-badge">${escapeHtml(a.tipo)}</span></td>
                                <td><strong>${fmt(a.diferencia_monto)}</strong></td>
                                <td><small>${escapeHtml(a.justificacion || 'Aprobado sin comentario')}</small></td>
                                <td><small>${escapeHtml(a.aceptada_por)}</small></td>
                                <td><small>${a.fecha_aceptacion ? a.fecha_aceptacion.substring(0, 10) : '-'}</small></td>
                            </tr>
                        `).join('');
                    }
                }

                // Render Operaciones Conformes (paginado, ver renderConformesPage)
                if (bodyConformes) {
                    conformesFullList = conformes;
                    conformesPage = 1;
                    renderConformesPage();
                }

                // Render Pagos con Residual sin Aplicar (ver
                // _detectar_pagos_con_residual_sin_aplicar -- bug real,
                // cliente TERA, agosto 2026).
                if (bodyResidual) {
                    if (pagosResidual.length === 0) {
                        bodyResidual.innerHTML = '<tr><td colspan="3" class="table-empty" style="color:#059669">✅ Ningún pago tiene residual sin aplicar en su línea contable.</td></tr>';
                    } else {
                        bodyResidual.innerHTML = pagosResidual.map(p => `
                            <tr>
                                <td><strong>${escapeHtml(p.pago_id)}</strong></td>
                                <td>${escapeHtml(p.numero_pago_odoo)}</td>
                                <td><strong style="color:#dc2626;">${fmt(p.residual_sin_aplicar_usd)}</strong></td>
                            </tr>
                        `).join('');
                    }
                }

                // Render Ajustes de Cambio Huérfanos (ver
                // _detectar_ajustes_cambio_huerfanos -- bug real de Odoo,
                // agosto 2026: desvincular un pago no cancela su Ajuste
                // Cambio).
                if (bodyAjustesHuerfanos) {
                    if (ajustesHuerfanos.length === 0) {
                        bodyAjustesHuerfanos.innerHTML = '<tr><td colspan="6" class="table-empty" style="color:#059669">✅ No hay ajustes de cambio huérfanos detectados.</td></tr>';
                    } else {
                        const fmtVes = (v) => 'Bs. ' + Number(v || 0).toLocaleString('es-VE', { minimumFractionDigits: 2 });
                        bodyAjustesHuerfanos.innerHTML = ajustesHuerfanos.map(a => `
                            <tr>
                                <td><span class="state-badge" style="background:#fef2f2; color:#dc2626; font-weight:600;">${escapeHtml(a.move_name)}</span></td>
                                <td><strong>${escapeHtml(a.so_id || '—')}</strong></td>
                                <td>${escapeHtml(a.factura_numero || '—')}</td>
                                <td><strong style="color:#dc2626;">${fmtVes(a.residual_ves)}</strong></td>
                                <td><small>${a.fecha ? String(a.fecha).substring(0, 10) : ''}</small></td>
                                <td><small title="${escapeHtml(a.ref)}" style="color:#64748b;">${escapeHtml((a.ref || '').substring(0, 60))}${(a.ref || '').length > 60 ? '…' : ''}</small></td>
                            </tr>
                        `).join('');
                    }
                }

                // Render Pagos con Importe Local Desincronizado (ver
                // _detectar_pagos_con_importe_local_desincronizado --
                // bug real de Odoo, agosto 2026, método de detección
                // propuesto por el usuario).
                if (bodyImporteLocal) {
                    if (pagosImporteLocal.length === 0) {
                        bodyImporteLocal.innerHTML = '<tr><td colspan="5" class="table-empty" style="color:#059669">✅ Ningún pago tiene el importe local desincronizado de su asiento.</td></tr>';
                    } else {
                        const fmtVes = (v) => 'Bs. ' + Number(v || 0).toLocaleString('es-VE', { minimumFractionDigits: 2 });
                        bodyImporteLocal.innerHTML = pagosImporteLocal.map(p => `
                            <tr>
                                <td><strong>${escapeHtml(p.pago_id)}</strong></td>
                                <td>${escapeHtml(p.numero_pago_odoo)}</td>
                                <td>${fmtVes(p.importe_local_ves)}</td>
                                <td>${fmtVes(p.monto_asiento_ves)}</td>
                                <td><strong style="color:#dc2626;">${fmtVes(p.diferencia_ves)}</strong></td>
                            </tr>
                        `).join('');
                    }
                }
                // Render Vinculaciones Sobreaplicadas (ver
                // _detectar_vinculaciones_sobreaplicadas -- bug real, pago
                // 1267/Grano Agregado, agosto 2026: dos Vinculaciones
                // reclamando entre ambas más de lo que el pago vale).
                if (bodySobreaplicadas) {
                    if (vinculacionesSobreaplicadas.length === 0) {
                        bodySobreaplicadas.innerHTML = '<tr><td colspan="4" class="table-empty" style="color:#059669">✅ Ningún pago tiene Vinculaciones que sumen más de lo que vale.</td></tr>';
                    } else {
                        bodySobreaplicadas.innerHTML = vinculacionesSobreaplicadas.map(v => `
                            <tr>
                                <td><strong>${escapeHtml(v.pago_id)}</strong></td>
                                <td>${fmt(v.monto_pago_usd)}</td>
                                <td>${fmt(v.total_vinculado_usd)}</td>
                                <td><strong style="color:#dc2626;">${fmt(v.exceso_usd)}</strong></td>
                            </tr>
                        `).join('');
                    }
                }

                // Render Tasa Implícita Implausible (ver
                // _detectar_vinculaciones_tasa_implicita_implausible --
                // forma general del bug de confundir Bs con USD).
                if (bodyTasaImplausible) {
                    if (vinculacionesTasaImplausible.length === 0) {
                        bodyTasaImplausible.innerHTML = '<tr><td colspan="5" class="table-empty" style="color:#059669">✅ Ninguna Vinculación tiene una tasa implícita fuera de rango.</td></tr>';
                    } else {
                        bodyTasaImplausible.innerHTML = vinculacionesTasaImplausible.map(v => `
                            <tr>
                                <td><strong>${escapeHtml(v.vinc_id)}</strong></td>
                                <td>${escapeHtml(v.pago_id)}</td>
                                <td>${escapeHtml(v.so_id)}</td>
                                <td><strong style="color:#dc2626;">${(v.tasa_implicita || 0).toFixed(4)}</strong></td>
                                <td>${(v.tasa_real || 0).toFixed(4)} <small style="color:#94a3b8;">(${(v.diferencia_pct || 0).toFixed(1)}% off)</small></td>
                            </tr>
                        `).join('');
                    }
                }

                // Render Faltante de Devolución por Línea (ver
                // _detectar_devolucion_no_reflejada_en_cantidad -- compara
                // cantidad pedida vs entregada, igual que la propia
                // pantalla de la orden en Odoo).
                if (bodyDevolucionNoReflejada) {
                    if (devolucionNoReflejada.length === 0) {
                        bodyDevolucionNoReflejada.innerHTML = '<tr><td colspan="6" class="table-empty" style="color:#059669">✅ Ninguna orden con devolución tiene un faltante sin reflejar.</td></tr>';
                    } else {
                        bodyDevolucionNoReflejada.innerHTML = devolucionNoReflejada.map(d => `
                            <tr>
                                <td><strong>${escapeHtml(d.so_id)}</strong></td>
                                <td>${escapeHtml(d.producto_codigo)} <small style="color:#64748b;">${escapeHtml(d.producto_nombre)}</small></td>
                                <td>${d.cantidad_ordenada}</td>
                                <td>${d.cantidad_entregada}</td>
                                <td><strong style="color:#dc2626;">${d.faltante}</strong></td>
                                <td>${fmt(d.valor_potencial_afectado)}</td>
                            </tr>
                        `).join('');
                    }
                }
            }
        } catch (err) {
            console.error("Error al cargar la auditoría:", err);
        }
    }
    window.loadAuditoria = loadAuditoria;

    async function loadAuditoriaVentasAlertas() {
        const tbody = document.getElementById("auditoria-ventas-alertas-body");
        const kpiEl = document.getElementById("audit-kpi-ventas-alertas");
        if (!tbody && !kpiEl) return;
        const fmt = (val) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(val || 0);
        const escapeHtml = (str) => {
            if (str === null || str === undefined) return '';
            return String(str)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        };
        try {
            const res = await fetch("/api/ventas?t=" + Date.now(), { cache: "no-store" });
            if (!res.ok) {
                if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="table-empty">Error al cargar órdenes con alerta.</td></tr>';
                return;
            }
            const data = await res.json();
            const alertas = (data.items || []).filter(it => it.alerta);
            if (kpiEl) kpiEl.textContent = String(alertas.length);
            const badgeAlertas = document.getElementById("auditoria-subtab-badge-alertas");
            if (badgeAlertas) badgeAlertas.textContent = String(alertas.length);
            if (!tbody) return;
            if (alertas.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" class="table-empty" style="color:#059669">✅ No hay órdenes facturadas por debajo de lo debido.</td></tr>';
                return;
            }
            tbody.innerHTML = alertas.map(it => `
                <tr>
                    <td><strong>${escapeHtml(it.so_id)}</strong></td>
                    <td>${escapeHtml(it.cliente_nombre)}</td>
                    <td><small>${escapeHtml(it.vendedor)}</small></td>
                    <td><small>${escapeHtml(it.fecha)}</small></td>
                    <td>${fmt((it.total_facturado_neto || 0) + (it.diferencia || 0))}</td>
                    <td>${fmt(it.total_facturado_neto)}</td>
                    <td><strong style="color:#b91c1c;">${fmt(it.diferencia)}</strong></td>
                </tr>
            `).join('');
        } catch (err) {
            if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="table-empty">Error de red al cargar órdenes con alerta.</td></tr>';
            console.error(err);
        }
    }
    window.loadAuditoriaVentasAlertas = loadAuditoriaVentasAlertas;

    // Sub-navegación de la página Auditoría (Discrepancias / Descuentos y
    // NCs / Alertas de Venta / Histórico Conforme) -- agrupa lo que antes
    // eran 7 tablas en un solo scroll largo, sin eliminar ninguna.
    window.switchAuditoriaSubtab = function(name) {
        document.querySelectorAll(".subtab-btn[data-subtab]").forEach(btn => {
            btn.classList.toggle("active", btn.dataset.subtab === name);
        });
        document.querySelectorAll(".subtab-panel[data-subtab-panel]").forEach(panel => {
            panel.style.display = (panel.dataset.subtabPanel === name) ? "flex" : "none";
        });
    };

    window.aceptarAnomalia = async function(anomaliaId, soId, tipo) {
        const just = prompt(`Justificación para aceptar la anomalía (${soId} - ${tipo}):`, "Aceptado por gerencia");
        if (just === null) return;
        try {
            const res = await fetch("/api/auditoria/aceptar-anomalia", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ anomalia_id: anomaliaId, justificacion: just })
            });
            if (res.ok) {
                alert("✅ Anomalía aceptada e incluida en el historial de auditoría.");
                loadAuditoria();
            } else {
                alert("❌ Error al aceptar la anomalía.");
            }
        } catch (err) {
            console.error("Error aceptando anomalía:", err);
        }
    };

    // Auto-refresh Dashboard every 30 seconds
    setInterval(() => {
        const activeTab = document.querySelector(".tab-navigation .active");
        if (activeTab && activeTab.dataset.page === "dashboard") {
            if (typeof loadTasasPromedios === "function") loadTasasPromedios();
        }
    }, 30000);

    // SPA navigation click handling for nav links and dashboard cards
    document.querySelectorAll(".nav-link").forEach(link => {
        link.addEventListener("click", (e) => {
            const targetPage = link.dataset.page;
            if (targetPage) {
                e.preventDefault();
                history.pushState(null, "", "/" + targetPage);
                initCurrentPage();
            }
        });
    });

    window.addEventListener("popstate", () => {
        initCurrentPage();
    });

    // Initialize current page tab and load its data after all functions are declared
    initCurrentPage();
});
