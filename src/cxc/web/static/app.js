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

    // Elements - Reporte
    const reporteTableBody = document.getElementById("reporte-table-body");
    const reporteSearch = document.getElementById("reporte-search");

    // Elements - Config
    const settingsForm = document.getElementById("settings-form");
    const cfgMetaDays = document.getElementById("cfg-meta-days");
    const cfgMetaRecompra = document.getElementById("cfg-meta-recompra");

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

    // Elements - Auditoria
    const auditKpiConformes = document.getElementById("audit-kpi-conformes");
    const auditKpiDiscrepancias = document.getElementById("audit-kpi-discrepancias");
    const auditKpiAceptadas = document.getElementById("audit-kpi-aceptadas");
    const auditKpiMontoDiscrepancia = document.getElementById("audit-kpi-monto-discrepancia");
    const discrepanciasTableBody = document.getElementById("discrepancias-table-body");
    const anomaliasAceptadasTableBody = document.getElementById("anomalias-aceptadas-table-body");
    const conformesTableBody = document.getElementById("conformes-table-body");

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
            } else if (path === "cobranza") {
                if (typeof loadCobranzaUnificado === "function") loadCobranzaUnificado();
            } else if (path === "ventas") {
                if (typeof loadVentas === "function") loadVentas();
            } else if (path === "reporte") {
                if (typeof loadReporte === "function") loadReporte();
            } else if (path === "auditoria") {
                if (typeof loadAuditoria === "function") loadAuditoria();
                if (typeof loadAuditoriaVentasAlertas === "function") loadAuditoriaVentasAlertas();
            } else if (path === "configuracion") {
                if (typeof loadConfigData === "function") loadConfigData();
                if (typeof loadListasMapeo === "function") loadListasMapeo();
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
    async function aprobarDescuentoSistema(soId, montoSugerido) {
        const montoStr = prompt(`Monto de descuento a aprobar para ${soId} (USD):`, (montoSugerido || 0).toFixed(2));
        if (montoStr === null) return;
        const monto = parseFloat(montoStr);
        if (isNaN(monto) || monto < 0) {
            alert("Monto inválido.");
            return;
        }
        const motivo = prompt("Motivo de la aprobación:", "Descuento aprobado en Bandeja de Facturación");
        if (motivo === null) return;

        try {
            const res = await fetch("/api/facturacion/aprobar-descuento-sistema", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ so_id: soId, monto: monto, motivo: motivo }),
            });
            if (res.ok) {
                alert(`✅ Descuento de sistema aprobado para ${soId}.`);
                loadBandeja();
            } else {
                const err = await res.json().catch(() => ({}));
                alert(`❌ Error al aprobar descuento: ${err.detail || res.statusText}`);
            }
        } catch (err) {
            alert("❌ Error de red al aprobar el descuento.");
            console.error(err);
        }
    }
    window.aprobarDescuentoSistema = aprobarDescuentoSistema;

    // Fetch and render the 3 Dashboard Approval Trays
    async function loadBandeja() {
        try {
            if (bandeja1TableBody) bandeja1TableBody.innerHTML = '<tr><td colspan="9" class="table-empty">Cargando órdenes pendientes por facturar...</td></tr>';
            if (bandeja2TableBody) bandeja2TableBody.innerHTML = '<tr><td colspan="8" class="table-empty">Cargando órdenes pendientes por nota de crédito...</td></tr>';
            if (bandeja3TableBody) bandeja3TableBody.innerHTML = '<tr><td colspan="7" class="table-empty">Cargando facturas pendientes por IVA...</td></tr>';

            const res = await fetch("/api/bandeja");
            if (res.ok) {
                const data = await res.json();
                const fmt = (v) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(v);

                // Handle legacy array format or structured 3 trays dict
                const tray1 = data.ordenes_por_facturar || (Array.isArray(data) ? data.filter(x => !x.facturada) : []);
                const tray2 = data.notas_credito_pendientes || (Array.isArray(data) ? data.filter(x => x.ncs_calculadas > 0) : []);
                const tray3 = data.iva_pendiente_agentes || [];

                // Render Tray 1
                if (bandeja1TableBody) {
                    if (tray1.length === 0) {
                        bandeja1TableBody.innerHTML = '<tr><td colspan="9" class="table-empty">No hay órdenes pendientes por facturar.</td></tr>';
                    } else {
                        bandeja1TableBody.innerHTML = "";
                        tray1.forEach(item => {
                            const row = document.createElement("tr");
                            const isAgent = item.wh_iva_agent ? `<span class="state-badge cierre" style="background:#e0f2fe;color:#0369a1">Agente (${item.wh_iva_rate || 75}%)</span>` : '<span class="state-badge">No</span>';
                            const descText = item.descuento_aplicar_monto > 0 ? `${fmt(item.descuento_aplicar_monto)} (${(item.descuento_aplicar_pct || 0).toFixed(1)}%)` : '$0.00 (0%)';
                            const sugerido = item.descuento_aplicar_monto || 0;
                            const accionHtml = item.descuento_sistema_aprobado != null
                                ? `<span class="state-badge cierre" style="background:#dcfce7;color:#166534" title="${(item.descuento_sistema_motivo || '').replace(/"/g, '&quot;')}">Descuento aprobado: ${fmt(item.descuento_sistema_aprobado)}</span>
                                   <button class="btn-primary" style="padding:4px 8px;font-size:0.7rem;margin-left:4px" onclick="aprobarDescuentoSistema('${item.so_id}', ${sugerido})">Editar</button>`
                                : `<button class="btn-primary" style="padding:4px 8px;font-size:0.75rem" onclick="aprobarDescuentoSistema('${item.so_id}', ${sugerido})">Aprobar Descuento</button>`;

                            row.innerHTML = `
                                <td><strong>${item.so_id}</strong></td>
                                <td>${item.cliente_nombre || item.so_id}</td>
                                <td>${isAgent}</td>
                                <td>${item.fecha || ''}</td>
                                <td><strong style="color:#059669">${fmt(item.monto_pagado || item.precio_base || 0)}</strong></td>
                                <td>${fmt(item.subtotal_neto || item.precio_base || 0)}</td>
                                <td><strong>${fmt(item.total_motor || 0)}</strong></td>
                                <td><strong style="color:#d97706">${descText}</strong></td>
                                <td>${accionHtml}</td>
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
                            row.innerHTML = `
                                <td><strong>${item.so_id}</strong></td>
                                <td>${item.cliente_nombre || item.so_id}</td>
                                <td><span class="state-badge">${item.factura_id || 'Odoo'}</span></td>
                                <td><strong style="color:#059669">${fmt(item.monto_pagado || 0)}</strong></td>
                                <td><strong style="color:#dc2626">${fmt(item.nc_monto || item.total_descuentos || 0)}</strong></td>
                                <td><strong style="color:#dc2626">${(item.nc_porcentaje || 0).toFixed(1)}%</strong></td>
                                <td>${item.concepto || 'Obsequio / Descuento'}</td>
                                <td><button class="btn-primary" style="padding:4px 8px;font-size:0.75rem;background:#dc2626" onclick="alert('Emitir N/C para ${item.so_id}')">Aprobar N/C</button></td>
                            `;
                            bandeja2TableBody.appendChild(row);
                        });
                    }
                }

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
            }
        } catch (err) {
            console.error("Error loading bandeja:", err);
            if (bandeja1TableBody) bandeja1TableBody.innerHTML = '<tr><td colspan="9" class="table-empty">Error al cargar bandeja 1.</td></tr>';
            if (bandeja2TableBody) bandeja2TableBody.innerHTML = '<tr><td colspan="8" class="table-empty">Error al cargar bandeja 2.</td></tr>';
            if (bandeja3TableBody) bandeja3TableBody.innerHTML = '<tr><td colspan="7" class="table-empty">Error al cargar bandeja 3.</td></tr>';
        }
    }

    window.guardarTasaBinance = async function(vincId) {
        const input = document.querySelector(`.input-tasa-binance[data-vinc="${vincId}"]`);
        if (!input) return;
        const tasa = parseFloat(input.value);
        if (!tasa || tasa <= 0) {
            alert("Ingresa una tasa Binance válida.");
            return;
        }
        try {
            const res = await fetch(`/api/vinculacion/${vincId}/tasa-binance`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ tasa_binance: tasa }),
            });
            const data = await res.json();
            if (!res.ok) {
                alert(data.detail || "No se pudo actualizar la tasa Binance.");
                return;
            }
            if (typeof loadCobranzaUnificado === "function") loadCobranzaUnificado();
        } catch (err) {
            alert("Error de red al actualizar la tasa Binance.");
            console.error(err);
        }
    };

    window.guardarTipoTasaBcv = async function(vincId) {
        const select = document.querySelector(`.select-bcv-variante[data-vinc="${vincId}"]`);
        if (!select) return;
        try {
            const res = await fetch(`/api/vinculacion/${vincId}/tasa-bcv-tipo`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ variante: select.value }),
            });
            const data = await res.json();
            if (!res.ok) {
                alert(data.detail || "No se pudo cambiar el tipo de tasa BCV.");
                return;
            }
            if (typeof loadCobranzaUnificado === "function") loadCobranzaUnificado();
        } catch (err) {
            alert("Error de red al cambiar el tipo de tasa BCV.");
            console.error(err);
        }
    };

    // Load Auditoría Panel Data
    async function loadAuditoria() {
        if (!discrepanciasTableBody || !conformesTableBody) return;
        try {
            discrepanciasTableBody.innerHTML = '<tr><td colspan="11" class="table-empty">Cargando auditoría de discrepancias...</td></tr>';
            if (anomaliasAceptadasTableBody) anomaliasAceptadasTableBody.innerHTML = '<tr><td colspan="9" class="table-empty">Cargando anomalías aceptadas...</td></tr>';
            conformesTableBody.innerHTML = '<tr><td colspan="8" class="table-empty">Cargando operaciones conformes...</td></tr>';

            const res = await fetch("/api/auditoria");
            if (res.ok) {
                const data = await res.json();
                const fmt = (v) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(v);

                // KPIs
                const summary = data.resumen_auditoria;
                if (auditKpiConformes) auditKpiConformes.textContent = summary.total_conformes;
                if (auditKpiDiscrepancias) auditKpiDiscrepancias.textContent = summary.total_discrepancias;
                if (auditKpiAceptadas) auditKpiAceptadas.textContent = summary.total_aceptadas || 0;
                // Load auditoría de descuentos y NCs table
                if (typeof loadAuditoriaDescuentos === "function") loadAuditoriaDescuentos();

                // Render Discrepancias Pendientes
                if (data.discrepancias.length === 0) {
                    discrepanciasTableBody.innerHTML = '<tr><td colspan="11" class="table-empty" style="color:#059669">✅ No se detectaron discrepancias pendientes. Todas las anomalías están revisadas o conformes.</td></tr>';
                } else {
                    discrepanciasTableBody.innerHTML = "";
                    data.discrepancias.forEach(item => {
                        const row = document.createElement("tr");
                        let tipoBadge = '<span class="state-badge abiertas" style="background:#fef3c7;color:#b45309">Discrepancia</span>';
                        if (item.tipo.includes("Precio")) {
                            tipoBadge = '<span class="state-badge" style="background:#fee2e2;color:#b91c1c;font-weight:700">Precio < Lista</span>';
                        } else if (item.tipo.includes("Descuento")) {
                            tipoBadge = '<span class="state-badge" style="background:#ffedd5;color:#c2410c">Desc. No Explicado</span>';
                        } else {
                            tipoBadge = '<span class="state-badge" style="background:#fef2f2;color:#dc2626">Orden vs Factura</span>';
                        }

                        const actionBtn = document.createElement("button");
                        actionBtn.className = "btn-primary";
                        actionBtn.style.cssText = "padding:4px 8px;font-size:0.75rem;background:#2563eb;";
                        actionBtn.textContent = "Aceptar Anomalía";
                        actionBtn.onclick = () => aceptarAnomalia(item);

                        row.innerHTML = `
                            <td><strong>${item.so_id}</strong></td>
                            <td><span class="state-badge">${item.factura_id}</span></td>
                            <td>${item.cliente_nombre}</td>
                            <td>${item.vendedor}</td>
                            <td>${tipoBadge}</td>
                            <td style="font-size:0.78rem; color:#475569">${item.detalle}</td>
                            <td>${fmt(item.esperado)}</td>
                            <td>${fmt(item.actual)}</td>
                            <td><strong style="color:#dc2626">${fmt(item.diferencia_monto)}</strong></td>
                            <td><strong style="color:#dc2626">${item.diferencia_porcentaje.toFixed(1)}%</strong></td>
                            <td></td>
                        `;
                        row.children[10].appendChild(actionBtn);
                        discrepanciasTableBody.appendChild(row);
                    });
                }

                // Render Anomalías Aceptadas
                if (anomaliasAceptadasTableBody) {
                    const aceptadas = data.anomalias_aceptadas || [];
                    if (aceptadas.length === 0) {
                        anomaliasAceptadasTableBody.innerHTML = '<tr><td colspan="9" class="table-empty">No hay anomalías aceptadas en el historial.</td></tr>';
                    } else {
                        anomaliasAceptadasTableBody.innerHTML = "";
                        aceptadas.forEach(item => {
                            const row = document.createElement("tr");
                            row.innerHTML = `
                                <td><small><strong>${item.anomalia_id}</strong></small></td>
                                <td><strong>${item.so_id}</strong></td>
                                <td><span class="state-badge">${item.factura_id}</span></td>
                                <td>${item.cliente_nombre}</td>
                                <td><span class="state-badge cierre">${item.tipo}</span></td>
                                <td><strong style="color:#2563eb">${fmt(item.diferencia_monto)}</strong></td>
                                <td>${item.motivo_aceptacion || 'Revisado y Aceptado'}</td>
                                <td>${item.aprobado_por || 'Dirección'}</td>
                                <td><small>${item.timestamp_aprobacion ? item.timestamp_aprobacion.substring(0, 10) : ''}</small></td>
                            `;
                            anomaliasAceptadasTableBody.appendChild(row);
                        });
                    }
                }

                // Render Operaciones Conformes
                if (data.operaciones_conformes.length === 0) {
                    conformesTableBody.innerHTML = '<tr><td colspan="8" class="table-empty">No hay operaciones conformes registradas aún.</td></tr>';
                } else {
                    conformesTableBody.innerHTML = "";
                    data.operaciones_conformes.forEach(item => {
                        const row = document.createElement("tr");
                        row.innerHTML = `
                            <td><strong>${item.so_id}</strong></td>
                            <td><span class="state-badge">${item.factura_id}</span></td>
                            <td>${item.cliente_nombre}</td>
                            <td>${item.fecha}</td>
                            <td>${fmt(item.monto_original)}</td>
                            <td>${fmt(item.descuentos_aplicados)}</td>
                            <td><strong style="color:#059669">${fmt(item.monto_neto_conciliado)}</strong></td>
                            <td><span class="state-badge cierre" style="background:#dcfce7;color:#15803d">Conforme 100%</span></td>
                        `;
                        conformesTableBody.appendChild(row);
                    });
                }
            }
        } catch (err) {
            console.error("Error loading auditoria:", err);
            if (discrepanciasTableBody) discrepanciasTableBody.innerHTML = '<tr><td colspan="11" class="table-empty">Error de red al cargar auditoría.</td></tr>';
            if (conformesTableBody) conformesTableBody.innerHTML = '<tr><td colspan="8" class="table-empty">Error de red al cargar auditoría.</td></tr>';
        }
    }

    async function aceptarAnomalia(item) {
        const motivo = prompt(`Ingresa el motivo de aceptación/aprobación de la anomalía en ${item.so_id}:`, "Revisado por Dirección - Negociación cerrada con cliente");
        if (!motivo) return;

        try {
            const res = await fetch("/api/auditoria/aceptar-anomalia", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    anomalia_id: item.anomalia_id,
                    so_id: item.so_id,
                    factura_id: item.factura_id,
                    tipo_anomalia: item.tipo,
                    motivo_aceptacion: motivo,
                    aprobado_por: "Dirección / Auditor Web"
                })
            });

            if (res.ok) {
                alert("✅ Anomalía aceptada y movida al historial de revisiones.");
                loadAuditoria();
            } else {
                const err = await res.json();
                alert(`❌ Error al aceptar anomalía: ${err.detail || 'Error en servidor'}`);
            }
        } catch (err) {
            alert("❌ Error de red al registrar aceptación.");
            console.error(err);
        }
    }

    // --- Tab 2: Accounts Receivable Report ---
    let fullReporteItems = [];

    async function loadReporte() {
        try {
            reporteTableBody.innerHTML = '<tr><td colspan="18" class="table-empty">Cargando reporte general de cuentas por cobrar...</td></tr>';
            const res = await fetch("/api/reporte-saldos?refresh=true&t=" + Date.now(), { cache: "no-store" });
            if (res.ok) {
                const data = await res.json();
                const kpis = data.kpis || {};
                fullReporteItems = data.items || (Array.isArray(data) ? data : []);
                const fmt = (val) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(val || 0);

                // Helper to update 4 sub-balances in a KPI card
                const setKpiSubBalances = (prefix, kpiObj) => {
                    const obj = kpiObj || { deudor_bcv: 0, desc_bcv: 0, desc_usd: 0, factura_odoo: 0 };
                    const elDeudorBCV = document.getElementById(`${prefix}-deudor-bcv`);
                    const elDescBCV = document.getElementById(`${prefix}-desc-bcv`);
                    const elDescUSD = document.getElementById(`${prefix}-desc-usd`);
                    const elFacturaOdoo = document.getElementById(`${prefix}-factura-odoo`);

                    if (elDeudorBCV) elDeudorBCV.textContent = fmt(obj.deudor_bcv);
                    if (elDescBCV) elDescBCV.textContent = fmt(obj.desc_bcv);
                    if (elDescUSD) elDescUSD.textContent = fmt(obj.desc_usd);
                    if (elFacturaOdoo) elFacturaOdoo.textContent = fmt(obj.factura_odoo);
                };

                setKpiSubBalances("kpi-total-general", kpis.total_general);
                setKpiSubBalances("kpi-total-vencido", kpis.total_vencido);
                setKpiSubBalances("kpi-vigentes", kpis.vigentes);
                setKpiSubBalances("kpi-1-30", kpis.vencidas_1_30);
                setKpiSubBalances("kpi-31-60", kpis.vencidas_31_60);
                setKpiSubBalances("kpi-61-90", kpis.vencidas_61_90);
                setKpiSubBalances("kpi-mas-90", kpis.vencidas_mas_90);

                // Attach Click Handlers to Interactive KPI Cards
                // (las tarjetas viven en el Dashboard; la tabla filtrable vive
                // en Reporte, así que un clic navega hacia allá si hace falta)
                document.querySelectorAll(".interactive-kpi").forEach(card => {
                    if (!card.dataset.listenerAttached) {
                        card.addEventListener("click", () => {
                            const targetVal = card.dataset.antiguedad;
                            const selectEl = document.getElementById("reporte-antiguedad-filter");
                            if (selectEl) {
                                selectEl.value = (selectEl.value === targetVal) ? "*" : targetVal;
                                applyReporteFilters();
                            }
                            const currentPath = window.location.pathname.toLowerCase()
                                .replace(/^\/+|\/+$/g, '').split('/')[0];
                            if (currentPath !== "reporte") {
                                history.pushState(null, "", "/reporte");
                                initCurrentPage();
                            }
                        });
                        card.dataset.listenerAttached = "true";
                    }
                });

                // Populate Vendedor Filter Dropdown
                const vendedorSelect = document.getElementById("reporte-vendedor-filter");
                if (vendedorSelect && data.vendedores) {
                    const currentVal = vendedorSelect.value || "*";
                    vendedorSelect.innerHTML = '<option value="*">Todos los Vendedores</option>';
                    data.vendedores.forEach(v => {
                        const opt = document.createElement("option");
                        opt.value = v;
                        opt.textContent = v;
                        vendedorSelect.appendChild(opt);
                    });
                    vendedorSelect.value = currentVal;

                    if (!vendedorSelect.dataset.listenerAttached) {
                        vendedorSelect.addEventListener("change", applyReporteFilters);
                        vendedorSelect.dataset.listenerAttached = "true";
                    }
                }

                const antiguedadSelect = document.getElementById("reporte-antiguedad-filter");
                if (antiguedadSelect && !antiguedadSelect.dataset.listenerAttached) {
                    antiguedadSelect.addEventListener("change", applyReporteFilters);
                    antiguedadSelect.dataset.listenerAttached = "true";
                }

                const searchInput = document.getElementById("reporte-search");
                if (searchInput && !searchInput.dataset.listenerAttached) {
                    searchInput.addEventListener("input", applyReporteFilters);
                    searchInput.dataset.listenerAttached = "true";
                }

                applyReporteFilters();
                renderCriticaTable(fullReporteItems);
                renderSaldoMinimoTable(data.saldo_minimo_pendientes || []);
            }
        } catch (err) {
            reporteTableBody.innerHTML = '<tr><td colspan="24" class="table-empty">Error de red al cargar el reporte.</td></tr>';
            console.error(err);
        }
    }

    function renderSaldoMinimoTable(items) {
        const tbody = document.getElementById("reporte-saldo-minimo-table-body");
        if (!tbody) return;

        if (!items || items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="table-empty">No hay facturas con saldo ≤ $1 pendientes por cerrar.</td></tr>';
            return;
        }

        const fmt = (val) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(val || 0);
        const esc = (v) => (v === null || v === undefined ? '' : String(v).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])));
        tbody.innerHTML = items.map(item => `
            <tr>
                <td><strong>${esc(item.so_id)}</strong></td>
                <td>${esc(item.cliente_nombre)}</td>
                <td>${esc(item.vendedor)}</td>
                <td>${esc(item.factura_id)}</td>
                <td>${fmt(item.saldo_con_descuento_bcv)}</td>
                <td>${fmt(item.saldo_con_descuento_lista_usd)}</td>
                <td>${item.saldo_factura_odoo !== null && item.saldo_factura_odoo !== undefined ? fmt(item.saldo_factura_odoo) : '-'}</td>
            </tr>
        `).join('');
    }

    function applyReporteFilters() {
        const vendedorVal = document.getElementById("reporte-vendedor-filter")?.value || "*";
        const antiguedadVal = document.getElementById("reporte-antiguedad-filter")?.value || "*";
        const searchInputEl = document.getElementById("reporte-search");
        const searchVal = searchInputEl ? searchInputEl.value.toLowerCase().trim() : "";

        // Highlight Active KPI Card
        document.querySelectorAll(".interactive-kpi").forEach(card => {
            if (card.dataset.antiguedad === antiguedadVal) {
                card.style.transform = "scale(1.03)";
                card.style.boxShadow = "0 6px 16px rgba(0,0,0,0.12)";
                card.style.borderColor = "#2563eb";
            } else {
                card.style.transform = "none";
                card.style.boxShadow = "none";
                card.style.borderColor = "";
            }
        });

        let filtered = fullReporteItems.filter(item => {
            const dv = item.dias_vencido || 0;
            const matchVendedor = (vendedorVal === "*") || (item.vendedor === vendedorVal);
            
            let matchAntiguedad = true;
            if (antiguedadVal === "vencido_total") matchAntiguedad = (dv > 0);
            else if (antiguedadVal === "vigentes") matchAntiguedad = (dv <= 0);
            else if (antiguedadVal === "1_30") matchAntiguedad = (dv >= 1 && dv <= 30);
            else if (antiguedadVal === "31_60") matchAntiguedad = (dv >= 31 && dv <= 60);
            else if (antiguedadVal === "61_90") matchAntiguedad = (dv >= 61 && dv <= 90);
            else if (antiguedadVal === "mas_90") matchAntiguedad = (dv > 90);

            const matchSearch = !searchVal || 
                (item.so_id && item.so_id.toLowerCase().includes(searchVal)) ||
                (item.cliente_nombre && item.cliente_nombre.toLowerCase().includes(searchVal)) ||
                (item.vendedor && item.vendedor.toLowerCase().includes(searchVal));

            return matchVendedor && matchAntiguedad && matchSearch;
        });

        renderReporteTable(filtered);
    }

    function renderCriticaTable(items) {
        const tbody = document.getElementById("reporte-critica-table-body");
        if (!tbody) return;

        // Filter items with mora critical (+60 days overdue) and active debt
        const criticaItems = items.filter(item => {
            const dv = item.dias_vencido || 0;
            const debtUSD = item.saldo_con_descuento_lista_usd || item.saldo_deudor_lista_usd || 0;
            return dv >= 61 && debtUSD > 0.05;
        });

        if (criticaItems.length === 0) {
            tbody.innerHTML = '<tr><td colspan="20" class="table-empty" style="color:#059669">✅ Excelente: No hay cuentas por cobrar en mora crítica (+60 días).</td></tr>';
            return;
        }

        const fmt = (val) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(val || 0);

        tbody.innerHTML = "";
        criticaItems.forEach(item => {
            const row = document.createElement("tr");
            const dv = item.dias_vencido || 0;
            const agingBadge = dv > 90 ?
                `<span class="state-badge" style="background:#f3e8ff;color:#6b21a8;font-weight:bold">+${dv} d (Más de 90d)</span>` :
                `<span class="state-badge" style="background:#ffe4e6;color:#be123c;font-weight:bold">+${dv} d (61-90d)</span>`;

            let odooHtml = '<span class="state-badge abierta">Por Facturar</span>';
            if (item.facturada) {
                odooHtml = '<span class="state-badge facturada">Facturado en Odoo</span>';
            }

            let closeHtml = '<span class="state-badge">Abierta</span>';
            if (item.candidata_a_cierre) {
                closeHtml = '<span class="state-badge cierre">Listo para Cierre</span>';
            }

            const baseTotal = item.monto_total || 0;
            const totalDesc = item.total_descuentos_monto || 0;
            const pctTotal = baseTotal > 0 ? (totalDesc / baseTotal * 100) : 0;
            let descuentosHtml = '<span style="color:#94a3b8">$0.00 (0%)</span>';

            if (totalDesc > 0) {
                let itemsList = '';
                if (item.descuentos_desglose && item.descuentos_desglose.length > 0) {
                    itemsList = item.descuentos_desglose.map(d => {
                        const itemPct = baseTotal > 0 ? (d.monto / baseTotal * 100) : (d.porcentaje || 0);
                        const label = d.descripcion || d.origen;
                        return `<div>• ${label}: <strong>${fmt(d.monto)}</strong> (${itemPct.toFixed(1)}%)</div>`;
                    }).join("");
                } else {
                    itemsList = `<div>• Descuentos: <strong>${fmt(totalDesc)}</strong> (${pctTotal.toFixed(1)}%)</div>`;
                }

                descuentosHtml = `
                    <div>
                        <strong style="color: #059669">${fmt(totalDesc)} (${pctTotal.toFixed(1)}%)</strong>
                        <div style="font-size:0.72rem; color:#475569; margin-top:2px; line-height:1.3">
                            ${itemsList}
                        </div>
                    </div>
                `;
            }

            // Venta bruta teórica (precio de lista correcto, sin descuentos)
            // vs monto real de la orden -- si difieren, es venta perdida (o
            // ganada) por un precio de línea distinto al de lista, separado
            // de los descuentos válidos que ya se muestran arriba.
            const difPrecio = item.diferencia_precio_lista || 0;
            if (Math.abs(difPrecio) > 0.05) {
                const perdida = difPrecio > 0;
                descuentosHtml += `
                    <div style="font-size:0.72rem; margin-top:4px; color:${perdida ? '#b91c1c' : '#0369a1'};" title="Venta bruta teórica (precio de lista, sin descuentos): ${fmt(item.venta_bruta_teorica)}">
                        ${perdida ? '⚠️ Vendido bajo lista' : 'Vendido sobre lista'}: <strong>${fmt(Math.abs(difPrecio))}</strong>
                    </div>
                `;
            }

            const saldoDescBCV = item.saldo_con_descuento_bcv !== undefined ? item.saldo_con_descuento_bcv : (item.saldo_deudor_con_descuentos || 0);
            const saldoDescUSD = item.saldo_con_descuento_lista_usd !== undefined ? item.saldo_con_descuento_lista_usd : 0;

            row.innerHTML = `
                <td><strong>${item.so_id}</strong></td>
                <td>${item.cliente_nombre}</td>
                <td><small><strong>${item.vendedor || 'Sin Vendedor'}</strong></small></td>
                <td><small>${item.fecha_entrega ? `<span style="color:#0369a1; font-weight:600;" title="Fecha de Entrega Efectiva (ALM/OUT)">🚚 ${item.fecha_entrega}</span>` : `<span style="color:#64748b">${item.fecha || 'Sin entrega'}</span>`}</small></td>
                <td><small>${item.terminos_pago || 'Contado'}</small></td>
                <td><small>${item.fecha_vencimiento || '-'}</small></td>
                <td>${agingBadge}</td>
                <td><small>${item.fecha_ultimo_abono || '<span style="color:#94a3b8">Sin abonos</span>'}</small></td>
                <td><strong style="color: #475569;">${fmt(item.monto_total)}</strong></td>
                <td><small><span class="state-badge" style="background:#f1f5f9;color:#334155;font-weight:600;">${item.lista_precios || item.lista_origen || 'Sin Lista (Odoo)'}</span></small></td>
                <td><strong style="color: #2563eb;">${fmt(item.monto_total_proyectado_usd)}</strong></td>
                <td>${fmt(item.abono_usd_bcv || item.monto_pagado)}</td>
                <td><strong style="color: #0891b2;">${fmt(item.abono_usd_binance)}</strong></td>
                <td><strong style="color: ${item.saldo_deudor_bcv > 0 ? '#d97706' : '#059669'}">${fmt(item.saldo_deudor_bcv)}</strong></td>
                <td><strong style="color: ${item.saldo_deudor_lista_usd > 0 ? '#d97706' : '#059669'}">${fmt(item.saldo_deudor_lista_usd)}</strong></td>
                <td>${descuentosHtml}</td>
                <td><strong style="color: ${saldoDescBCV > 0 ? '#b91c1c' : '#059669'}">${fmt(saldoDescBCV)}</strong></td>
                <td><strong style="color: ${saldoDescUSD > 0 ? '#b91c1c' : '#059669'}">${fmt(saldoDescUSD)}</strong></td>
                <td>${odooHtml}</td>
                <td>${closeHtml}</td>
            `;
            tbody.appendChild(row);
        });
    }

    function renderReporteTable(data) {
        const list = Array.isArray(data) ? data : (data && Array.isArray(data.items) ? data.items : []);
        if (!list || list.length === 0) {
            reporteTableBody.innerHTML = '<tr><td colspan="24" class="table-empty">No hay registros de cobranza que coincidan con los filtros seleccionados.</td></tr>';
            return;
        }

        reporteTableBody.innerHTML = "";
        list.forEach(item => {
            const row = document.createElement("tr");
            const fmt = (val) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(val || 0);

            // Aging badge
            const dv = item.dias_vencido || 0;
            let agingBadge = '<span class="state-badge cierre" style="background:#dcfce7;color:#15803d">Vigente (0 d)</span>';
            if (dv >= 1 && dv <= 30) {
                agingBadge = `<span class="state-badge" style="background:#fef3c7;color:#b45309">+${dv} d</span>`;
            } else if (dv >= 31 && dv <= 60) {
                agingBadge = `<span class="state-badge" style="background:#ffedd5;color:#c2410c">+${dv} d</span>`;
            } else if (dv >= 61 && dv <= 90) {
                agingBadge = `<span class="state-badge" style="background:#ffe4e6;color:#be123c">+${dv} d</span>`;
            } else if (dv > 90) {
                agingBadge = `<span class="state-badge" style="background:#f3e8ff;color:#6b21a8;font-weight:bold">+${dv} d</span>`;
            }

            // Format Odoo State
            let odooHtml = '<span class="state-badge abierta">Por Facturar</span>';
            if (item.facturada) {
                odooHtml = '<span class="state-badge facturada">Facturado en Odoo</span>';
            }

            // Format Close State
            let closeHtml = '<span class="state-badge">Abierta</span>';
            if (item.candidata_a_cierre) {
                closeHtml = '<span class="state-badge cierre">Listo para Cierre</span>';
            }

            // Format Descuentos Aplicados Breakdown (motor)
            const baseTotal = item.monto_total || 0;
            const totalDesc = item.total_descuentos_monto || 0;
            const pctTotal = baseTotal > 0 ? (totalDesc / baseTotal * 100) : 0;
            let descuentosHtml = '<span style="color:#94a3b8">$0.00 (0%)</span>';

            if (totalDesc > 0) {
                let itemsList = '';
                if (item.descuentos_desglose && item.descuentos_desglose.length > 0) {
                    itemsList = item.descuentos_desglose.map(d => {
                        const itemPct = baseTotal > 0 ? (d.monto / baseTotal * 100) : (d.porcentaje || 0);
                        const label = d.descripcion || d.origen;
                        return `<div>• ${label}: <strong>${fmt(d.monto)}</strong> (${itemPct.toFixed(1)}%)</div>`;
                    }).join("");
                } else {
                    itemsList = `<div>• Descuentos: <strong>${fmt(totalDesc)}</strong> (${pctTotal.toFixed(1)}%)</div>`;
                }

                descuentosHtml = `
                    <div>
                        <strong style="color: #059669">${fmt(totalDesc)} (${pctTotal.toFixed(1)}%)</strong>
                        <div style="font-size:0.72rem; color:#475569; margin-top:2px; line-height:1.3">
                            ${itemsList}
                        </div>
                    </div>
                `;
            }

            // Venta bruta teórica (precio de lista correcto, sin descuentos)
            // vs monto real de la orden -- si difieren, es venta perdida (o
            // ganada) por un precio de línea distinto al de lista, separado
            // de los descuentos válidos que ya se muestran arriba.
            const difPrecio = item.diferencia_precio_lista || 0;
            if (Math.abs(difPrecio) > 0.05) {
                const perdida = difPrecio > 0;
                descuentosHtml += `
                    <div style="font-size:0.72rem; margin-top:4px; color:${perdida ? '#b91c1c' : '#0369a1'};" title="Venta bruta teórica (precio de lista, sin descuentos): ${fmt(item.venta_bruta_teorica)}">
                        ${perdida ? '⚠️ Vendido bajo lista' : 'Vendido sobre lista'}: <strong>${fmt(Math.abs(difPrecio))}</strong>
                    </div>
                `;
            }

            const saldoDescBCV = item.saldo_con_descuento_bcv !== undefined ? item.saldo_con_descuento_bcv : (item.saldo_deudor_con_descuentos || 0);
            const saldoDescUSD = item.saldo_con_descuento_lista_usd !== undefined ? item.saldo_con_descuento_lista_usd : 0;

            let cellFacturaOdoo = '<span style="color:#94a3b8; font-size:0.75rem;">Sin Factura</span>';
            if (item.factura_odoo_nombre && item.factura_odoo_nombre !== "Sin Factura") {
                const saldoInv = item.saldo_factura_odoo !== null ? fmt(item.saldo_factura_odoo) : "$0.00";
                cellFacturaOdoo = `<div style="font-size:0.78rem;"><span style="color:#0369a1; font-weight:600;">${item.factura_odoo_nombre}</span><br><strong style="color:${item.saldo_factura_odoo > 0.05 ? '#0f172a' : '#059669'};">${saldoInv}</strong></div>`;
            }

            // ── Nueva columna 1: Descuentos en líneas de ORDEN Odoo ─────────────
            const dOO = item.descuentos_odoo_orden || {};
            let cellDescOrden = '<span style="color:#94a3b8;font-size:0.75rem;">Sin desc.</span>';
            if (dOO.monto_usd > 0.005) {
                const auditStatus = item.auditoria_descuentos?.estado_orden || 'ok';
                const auditColor = auditStatus === 'ok' ? '#059669' : (auditStatus === 'discrepancia' ? '#dc2626' : '#d97706');
                const auditIcon = auditStatus === 'ok' ? '✅' : (auditStatus === 'discrepancia' ? '❌' : '⚠️');
                cellDescOrden = `<div style="font-size:0.78rem;">
                    <strong style="color:#0369a1;">${fmt(dOO.monto_usd)}</strong>
                    <span style="color:${auditColor}; margin-left:4px;">${auditIcon}</span>
                    ${dOO.pct_sobre_total > 0 ? `<br><span style="color:#64748b;">${dOO.pct_sobre_total.toFixed(1)}% s/total</span>` : ''}
                    ${dOO.detalle ? `<br><span style="color:#94a3b8;font-size:0.7rem;" title="${dOO.detalle}">${dOO.detalle.substring(0,40)}${dOO.detalle.length > 40 ? '…' : ''}</span>` : ''}
                </div>`;
            }

            // ── Nueva columna 2: Descuentos en líneas de FACTURA Odoo ───────────
            const dFO = item.descuentos_odoo_factura || {};
            let cellDescFactura = '<span style="color:#94a3b8;font-size:0.75rem;">Sin desc.</span>';
            if (dFO.monto_usd > 0.005) {
                const auditStatus = item.auditoria_descuentos?.estado_factura || 'ok';
                const auditColor = auditStatus === 'ok' ? '#059669' : (auditStatus === 'discrepancia' ? '#dc2626' : '#d97706');
                const auditIcon = auditStatus === 'ok' ? '✅' : (auditStatus === 'discrepancia' ? '❌' : '⚠️');
                cellDescFactura = `<div style="font-size:0.78rem;">
                    <strong style="color:#7c3aed;">${fmt(dFO.monto_usd)}</strong>
                    <span style="color:${auditColor}; margin-left:4px;">${auditIcon}</span>
                    ${dFO.detalle ? `<br><span style="color:#94a3b8;font-size:0.7rem;" title="${dFO.detalle}">${dFO.detalle.substring(0,40)}${dFO.detalle.length > 40 ? '…' : ''}</span>` : ''}
                </div>`;
            }

            // ── Nueva columna 3: Notas de Crédito (NC) Odoo ─────────────────────
            const ncO = item.ncs_odoo || {};
            let cellNCs = '<span style="color:#94a3b8;font-size:0.75rem;">Sin NCs</span>';
            if (ncO.monto_usd > 0.005) {
                const ncEstado = ncO.auditoria_estado || 'ok';
                const ncColor = ncEstado === 'ok' ? '#059669' : (ncEstado === 'discrepancia' ? '#dc2626' : '#d97706');
                const ncIcon = ncEstado === 'ok' ? '✅' : (ncEstado === 'discrepancia' ? '❌' : '⚠️');
                const ncNombres = (ncO.nombres || []).join(', ') || 'NC';
                cellNCs = `<div style="font-size:0.78rem;">
                    <strong style="color:#dc2626;">${fmt(ncO.monto_usd)}</strong>
                    <span style="color:${ncColor}; margin-left:4px;">${ncIcon}</span>
                    <br><span style="color:#64748b;font-size:0.7rem;" title="${ncNombres}">${ncNombres.substring(0,35)}${ncNombres.length > 35 ? '…' : ''}</span>
                </div>`;
            }

            // Audit warning on SO cell
            const hasAuditWarn = item.auditoria_descuentos?.tiene_discrepancia;
            const soCell = hasAuditWarn
                ? `<strong>${item.so_id}</strong> <span style="color:#f59e0b;" title="Discrepancia en auditoría">⚠️</span>`
                : `<strong>${item.so_id}</strong>`;

            row.innerHTML = `
                <td>${soCell}</td>
                <td>${item.cliente_nombre}</td>
                <td><small><strong>${item.vendedor || 'Sin Vendedor'}</strong></small></td>
                <td><small>${item.fecha_entrega ? `<span style="color:#0369a1; font-weight:600;" title="Fecha de Entrega Efectiva (ALM/OUT)">🚚 ${item.fecha_entrega}</span>` : `<span style="color:#64748b">${item.fecha || 'Sin entrega'}</span>`}</small></td>
                <td><small>${item.terminos_pago || 'Contado'}</small></td>
                <td><small>${item.fecha_vencimiento || '-'}</small></td>
                <td>${agingBadge}</td>
                <td><small>${item.fecha_ultimo_abono || '<span style="color:#94a3b8">Sin abonos</span>'}</small></td>
                <td><strong style="color: #475569;">${fmt(item.monto_total)}</strong></td>
                <td><small><span class="state-badge" style="background:#f1f5f9;color:#334155;font-weight:600;">${item.lista_precios || item.lista_origen || 'Sin Lista (Odoo)'}</span></small></td>
                <td><strong style="color: #2563eb;">${fmt(item.monto_total_proyectado_usd)}</strong></td>
                <td>${fmt(item.abono_usd_bcv || item.monto_pagado)}</td>
                <td><strong style="color: #0891b2;">${fmt(item.abono_usd_binance)}</strong></td>
                <td><strong style="color: ${item.saldo_deudor_bcv > 0 ? '#d97706' : '#059669'}">${fmt(item.saldo_deudor_bcv)}</strong></td>
                <td><strong style="color: ${item.saldo_deudor_lista_usd > 0 ? '#d97706' : '#059669'}">${fmt(item.saldo_deudor_lista_usd)}</strong></td>
                <td>${cellFacturaOdoo}</td>
                <td>${cellDescOrden}</td>
                <td>${cellDescFactura}</td>
                <td>${cellNCs}</td>
                <td>${descuentosHtml}</td>
                <td><strong style="color: ${saldoDescBCV > 0.05 ? '#2563eb' : '#059669'}">${fmt(saldoDescBCV)}</strong></td>
                <td><strong style="color: ${saldoDescUSD > 0.05 ? '#7e22ce' : '#059669'}">${fmt(saldoDescUSD)}</strong></td>
                <td>${odooHtml}</td>
                <td>${closeHtml}</td>
            `;
            reporteTableBody.appendChild(row);
        });
    }

    // ── Página Ventas: teórico (bruta/neta, con y sin impuestos) vs real ──
    let ventasData = [];

    async function loadVentas() {
        const tbody = document.getElementById("ventas-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="36" class="table-empty">Cargando reporte de ventas...</td></tr>';
            const res = await fetch("/api/ventas?t=" + Date.now(), { cache: "no-store" });
            if (!res.ok) {
                tbody.innerHTML = '<tr><td colspan="36" class="table-empty">Error al cargar el reporte de ventas.</td></tr>';
                return;
            }
            const data = await res.json();
            ventasData = data.items || [];
            const kpis = data.kpis || {};
            const fmt = (val) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(val || 0);

            const setText = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
            setText("ventas-kpi-subtotal-real", fmt(kpis.subtotal_real_total));
            setText("ventas-kpi-ves-bruta", fmt(kpis.ves_bruta_teorica_total));
            setText("ventas-kpi-ves-neta-iva", fmt(kpis.ves_neta_teorica_iva_total));
            setText("ventas-kpi-usd-bruta", fmt(kpis.usd_bruta_teorica_total));
            setText("ventas-kpi-usd-neta-iva", fmt(kpis.usd_neta_teorica_iva_total));
            setText("ventas-kpi-neta-real", fmt(kpis.venta_neta_real_total));
            setText("ventas-kpi-facturado-neto", fmt(kpis.total_facturado_neto_total));

            const ivaPct = ((kpis.iva_rate || 0) * 100).toFixed(0);
            const igtfInfo = kpis.igtf_activo ? ` · IGTF ${((kpis.igtf_rate || 0) * 100).toFixed(0)}%` : ' · IGTF inactivo';
            setText("ventas-iva-info", `Tasas de impuesto configuradas: IVA ${ivaPct}%${igtfInfo}`);

            // Poblar filtro de vendedores
            const vendedorSelect = document.getElementById("ventas-vendedor-filter");
            if (vendedorSelect) {
                const currentVal = vendedorSelect.value || "*";
                const vendedores = [...new Set(ventasData.map(it => it.vendedor).filter(Boolean))].sort();
                vendedorSelect.innerHTML = '<option value="*">Todos los Vendedores</option>';
                vendedores.forEach(v => {
                    const opt = document.createElement("option");
                    opt.value = v;
                    opt.textContent = v;
                    vendedorSelect.appendChild(opt);
                });
                vendedorSelect.value = currentVal;
                if (!vendedorSelect.dataset.listenerAttached) {
                    vendedorSelect.addEventListener("change", applyVentasFilters);
                    vendedorSelect.dataset.listenerAttached = "true";
                }
            }

            const soloAlertasEl = document.getElementById("ventas-solo-alertas");
            if (soloAlertasEl && !soloAlertasEl.dataset.listenerAttached) {
                soloAlertasEl.addEventListener("change", applyVentasFilters);
                soloAlertasEl.dataset.listenerAttached = "true";
            }

            const searchEl = document.getElementById("ventas-search");
            if (searchEl && !searchEl.dataset.listenerAttached) {
                searchEl.addEventListener("input", applyVentasFilters);
                searchEl.dataset.listenerAttached = "true";
            }

            applyVentasFilters();
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="36" class="table-empty">Error de red al cargar el reporte de ventas.</td></tr>';
            console.error(err);
        }
    }

    function applyVentasFilters() {
        const vendedorVal = document.getElementById("ventas-vendedor-filter")?.value || "*";
        const soloAlertas = document.getElementById("ventas-solo-alertas")?.checked || false;
        const searchVal = (document.getElementById("ventas-search")?.value || "").toLowerCase().trim();

        let filtered = ventasData;
        if (vendedorVal !== "*") {
            filtered = filtered.filter(it => it.vendedor === vendedorVal);
        }
        if (soloAlertas) {
            filtered = filtered.filter(it => it.alerta);
        }
        if (searchVal) {
            filtered = filtered.filter(it =>
                (it.so_id || "").toLowerCase().includes(searchVal) ||
                (it.cliente_nombre || "").toLowerCase().includes(searchVal)
            );
        }
        renderVentasTable(filtered);
    }

    function renderVentasTable(items) {
        const tbody = document.getElementById("ventas-table-body");
        if (!tbody) return;
        if (!items || items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="36" class="table-empty">No hay órdenes que coincidan con los filtros seleccionados.</td></tr>';
            return;
        }
        const fmt = (val) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(val || 0);
        tbody.innerHTML = "";
        items.forEach(item => {
            const row = document.createElement("tr");
            if (item.alerta) {
                row.style.background = "#fef2f2";
            }
            const difColor = item.diferencia > 0.05 ? "#b91c1c" : (item.diferencia < -0.05 ? "#0369a1" : "#059669");
            const alertaCell = item.alerta
                ? '<span class="state-badge" style="background:#fee2e2;color:#991b1b;font-weight:700;">⚠️ Facturado de menos</span>'
                : (item.facturada
                    ? '<span class="state-badge" style="background:#dcfce7;color:#15803d;">OK</span>'
                    : '<span class="state-badge" style="background:#f1f5f9;color:#64748b;">Sin facturar</span>');

            const naVal = (v) => v != null ? fmt(v) : '—';
            const valColor = (v) => v === 'ok' ? '#059669' : (v === 'discrepancia_menor' ? '#b45309' : '#b91c1c');
            const valLabel = (v) => v === 'ok' ? '✓ OK' : (v === 'discrepancia_menor' ? '~ Menor' : '⚠ Discrepancia');
            const pctTxt = (p) => p != null ? ` (${(p * 100).toFixed(1)}%)` : '';
            const descMontoPct = (monto, pct) => `${fmt(monto)}<small style="color:#64748b;">${pctTxt(pct)}</small>`;
            const estatusPagoBadge = (estado) => {
                const map = {
                    pagada: ['#dcfce7', '#15803d', '✓ Pagada'],
                    parcial: ['#fef3c7', '#b45309', '~ Parcial'],
                    sin_pago: ['#fee2e2', '#991b1b', '✗ Sin pago'],
                    sin_factura: ['#f1f5f9', '#64748b', '— Sin factura'],
                };
                const [bg, fg, label] = map[estado] || ['#f1f5f9', '#64748b', estado || '—'];
                return `<span class="state-badge" style="background:${bg};color:${fg};font-weight:600;">${label}</span>`;
            };

            row.innerHTML = `
                <td><strong>${item.so_id}</strong></td>
                <td>${item.cliente_nombre}</td>
                <td><small>${item.vendedor}</small></td>
                <td><small>${item.fecha}</small></td>
                <td><small title="${item.lista_nacimiento ?? ''}">${item.lista_nacimiento_label ?? '—'}</small></td>
                <td><small title="${item.lista_aplicada ?? ''}">${item.lista_aplicada_label ?? '—'}</small></td>
                <td>${naVal(item.ves_bruta_teorica)}</td>
                <td>${naVal(item.ves_bruta_teorica_iva)}</td>
                <td><strong style="color:#2563eb;">${naVal(item.ves_neta_teorica)}</strong></td>
                <td><strong style="color:#2563eb;">${naVal(item.ves_neta_teorica_iva)}</strong></td>
                <td>${estatusPagoBadge(item.estatus_pago_teorico_ves)}</td>
                <td>${naVal(item.usd_bruta_teorica)}</td>
                <td>${naVal(item.usd_bruta_teorica_iva)}</td>
                <td><strong style="color:#2563eb;">${naVal(item.usd_neta_teorica)}</strong></td>
                <td><strong style="color:#2563eb;">${naVal(item.usd_neta_teorica_iva)}</strong></td>
                <td>${estatusPagoBadge(item.estatus_pago_teorico_usd)}</td>
                <td>${fmt(item.venta_bruta_real)}</td>
                <td>${descMontoPct(item.descuento_aplicado_orden, item.descuento_aplicado_orden_pct)}</td>
                <td><strong>${fmt(item.venta_neta_real)}</strong></td>
                <td>${estatusPagoBadge(item.estatus_pago_real_orden)}</td>
                <td>${fmt(item.total_facturado_antes_impuestos)}</td>
                <td>${descMontoPct(item.descuento_aplicado_factura, item.descuento_aplicado_factura_pct)}</td>
                <td>${fmt(item.total_facturado_con_impuestos)}</td>
                <td>${fmt(item.total_nc_aplicada)}</td>
                <td>${fmt(item.total_nd_aplicada)}</td>
                <td><strong>${fmt(item.total_facturado_neto)}</strong></td>
                <td>${estatusPagoBadge(item.estatus_pago_real_factura)}</td>
                <td><span style="color:${valColor(item.descuento_validacion_orden)};font-weight:600;">${valLabel(item.descuento_validacion_orden)}</span></td>
                <td><span style="color:${valColor(item.descuento_validacion_factura)};font-weight:600;">${valLabel(item.descuento_validacion_factura)}</span></td>
                <td>${descMontoPct(item.descuento_motor_total, item.descuento_motor_total_pct)}</td>
                <td>${descMontoPct(item.descuento_pendiente_aplicar, item.descuento_pendiente_aplicar_pct)}</td>
                <td title="${item.descuento_aplicado_sistema_motivo ?? ''}">${descMontoPct(item.descuento_aplicado_sistema, item.descuento_aplicado_sistema_pct)}</td>
                <td>${fmt(item.saldo_pendiente_cxc)}</td>
                <td><strong style="color:${difColor};">${fmt(item.diferencia)}</strong></td>
                <td>${alertaCell}</td>
                <td><button class="btn-primary" style="padding:4px 8px;font-size:0.75rem" onclick="abrirModalDetalleOrden('${item.so_id}')">Ver Detalle</button></td>
            `;
            tbody.appendChild(row);
        });
    }

    // ── Modal de detalle de orden (Fase 5) ────────────────────────────────────
    let _detalleOrdenData = null;

    function _renderDetalleOrdenModo(modo) {
        const body = document.getElementById("modal-detalle-orden-body");
        if (!body || !_detalleOrdenData) return;
        const fmt = (val) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(val || 0);

        const bloque = _detalleOrdenData[modo];
        if (!bloque || !bloque.lineas || bloque.lineas.length === 0) {
            body.innerHTML = '<p class="table-empty">Sin líneas para este modo.</p>';
            return;
        }
        const tieneDescuento = modo === "real_orden" || modo === "real_factura";
        const listaLabel = bloque.lista_label ? `<p style="color:#64748b;font-size:0.8rem;margin:0 0 0.5rem 0;">Lista: ${bloque.lista_label}</p>` : '';

        let rows = bloque.lineas.map(l => `
            <tr>
                <td>${l.producto}</td>
                <td style="text-align:right">${l.cantidad}</td>
                <td style="text-align:right">${fmt(l.precio_unitario)}</td>
                ${tieneDescuento ? `<td style="text-align:right">${fmt(l.descuento_monto)} (${(l.descuento_pct || 0).toFixed(1)}%)</td>` : ''}
                <td style="text-align:right"><strong>${fmt(l.subtotal)}</strong></td>
            </tr>
        `).join('');

        body.innerHTML = `
            ${listaLabel}
            <div style="overflow-x:auto;">
                <table class="cxc-table">
                    <thead>
                        <tr>
                            <th>Producto</th>
                            <th style="text-align:right">Cantidad</th>
                            <th style="text-align:right">Precio Unit.</th>
                            ${tieneDescuento ? '<th style="text-align:right">Descuento</th>' : ''}
                            <th style="text-align:right">Subtotal</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
            <div style="margin-top:0.75rem;text-align:right;font-size:0.9rem;">
                ${tieneDescuento ? `<div>Descuento total: <strong>${fmt(bloque.descuento_total)}</strong></div>` : ''}
                <div>Subtotal: <strong style="font-size:1.05rem;">${fmt(bloque.subtotal)}</strong></div>
            </div>
        `;
    }

    async function abrirModalDetalleOrden(soId) {
        const modal = document.getElementById("modal-detalle-orden");
        const titulo = document.getElementById("modal-detalle-orden-titulo");
        const subtitulo = document.getElementById("modal-detalle-orden-subtitulo");
        const body = document.getElementById("modal-detalle-orden-body");
        const modoSelect = document.getElementById("modal-detalle-orden-modo");
        if (!modal) return;

        titulo.textContent = `Detalle de Orden ${soId}`;
        subtitulo.textContent = "Cargando...";
        body.innerHTML = '<p class="table-empty">Cargando líneas...</p>';
        modal.style.display = "flex";
        _detalleOrdenData = null;

        try {
            const res = await fetch(`/api/ventas/${encodeURIComponent(soId)}/detalle`);
            if (!res.ok) {
                subtitulo.textContent = "";
                body.innerHTML = '<p class="table-empty">Error al cargar el detalle de la orden.</p>';
                return;
            }
            const data = await res.json();
            _detalleOrdenData = {
                real_orden: data.real_orden,
                real_factura: data.real_factura,
                teorico_ves: data.teorico_ves,
                teorico_usd: data.teorico_usd,
            };
            subtitulo.textContent = `${data.cliente_nombre || ''} — Lista nacimiento: ${data.lista_nacimiento_label || '—'}`;
            if (modoSelect) modoSelect.value = "real_orden";
            _renderDetalleOrdenModo("real_orden");
        } catch (err) {
            subtitulo.textContent = "";
            body.innerHTML = '<p class="table-empty">Error de red al cargar el detalle de la orden.</p>';
            console.error(err);
        }
    }

    function cerrarModalDetalleOrden() {
        const modal = document.getElementById("modal-detalle-orden");
        if (modal) modal.style.display = "none";
        _detalleOrdenData = null;
    }

    window.abrirModalDetalleOrden = abrirModalDetalleOrden;
    window.cerrarModalDetalleOrden = cerrarModalDetalleOrden;

    const modalDetalleOrdenModoSelect = document.getElementById("modal-detalle-orden-modo");
    if (modalDetalleOrdenModoSelect) {
        modalDetalleOrdenModoSelect.addEventListener("change", (e) => {
            _renderDetalleOrdenModo(e.target.value);
        });
    }

    // ── Bandeja Auditoría de Descuentos y NCs ─────────────────────────────────
    async function loadAuditoriaDescuentos() {
        const tbody = document.getElementById("auditoria-descuentos-body");
        if (!tbody) return;
        tbody.innerHTML = '<tr><td colspan="10" class="table-empty">Cargando...</td></tr>';

        const tipoVal = document.getElementById("audit-tipo-filter")?.value || "";
        const estadoVal = document.getElementById("audit-estado-filter")?.value || "";
        const params = new URLSearchParams();
        if (tipoVal) params.set("tipo", tipoVal);
        if (estadoVal) params.set("estado", estadoVal);

        try {
            const res = await fetch(`/api/auditoria-descuentos?${params.toString()}`);
            if (!res.ok) {
                tbody.innerHTML = '<tr><td colspan="10" class="table-empty">Error al cargar la bandeja de auditoría.</td></tr>';
                return;
            }
            const data = await res.json();
            const items = data.items || [];

            const badge = document.getElementById("audit-count-badge");
            if (badge) {
                if (items.length > 0) {
                    badge.textContent = `${items.length} discrepancia${items.length !== 1 ? 's' : ''}`;
                    badge.style.display = "inline";
                } else {
                    badge.style.display = "none";
                }
            }

            if (items.length === 0) {
                tbody.innerHTML = '<tr><td colspan="10" class="table-empty" style="color:#059669;">✅ Sin discrepancias detectadas</td></tr>';
                return;
            }

            const fmt = (val) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(val || 0);
            const tipoLabel = { descuento_orden: '📋 Desc. Orden', descuento_factura: '🧾 Desc. Factura', nota_credito: '📄 Nota de Crédito' };
            const estadoBadge = {
                pendiente: '<span style="background:#fef3c7;color:#b45309;padding:2px 8px;border-radius:999px;font-size:0.75rem;font-weight:700;">⏳ Pendiente</span>',
                revisado: '<span style="background:#dbeafe;color:#1d4ed8;padding:2px 8px;border-radius:999px;font-size:0.75rem;font-weight:700;">👁 Revisado</span>',
                aprobado: '<span style="background:#dcfce7;color:#15803d;padding:2px 8px;border-radius:999px;font-size:0.75rem;font-weight:700;">✅ Aprobado</span>',
                rechazado: '<span style="background:#fee2e2;color:#991b1b;padding:2px 8px;border-radius:999px;font-size:0.75rem;font-weight:700;">❌ Rechazado</span>',
            };

            tbody.innerHTML = "";
            items.forEach(item => {
                const tr = document.createElement("tr");
                const dif = parseFloat(item.diferencia_usd || 0);
                const difColor = dif > 0 ? '#dc2626' : (dif < 0 ? '#d97706' : '#059669');
                const difIcon = dif > 0 ? '▲' : (dif < 0 ? '▼' : '=');
                const ts = (item.timestamp_audit || '').substring(0, 16).replace('T', ' ');
                const auditId = item.audit_id || '';
                const estado = item.estado || 'pendiente';

                tr.innerHTML = `
                    <td><strong>${item.so_id || '-'}</strong></td>
                    <td>${tipoLabel[item.tipo_auditoria] || item.tipo_auditoria || '-'}</td>
                    <td><strong style="color:#2563eb;">${fmt(item.motor_calcula_usd)}</strong></td>
                    <td><strong style="color:#475569;">${fmt(item.odoo_registrado_usd)}</strong></td>
                    <td><strong style="color:${difColor};">${difIcon} ${fmt(Math.abs(dif))}</strong></td>
                    <td><small style="color:#64748b;" title="${item.detalle_odoo || ''}">${(item.detalle_odoo || '-').substring(0,50)}${(item.detalle_odoo || '').length > 50 ? '…' : ''}</small></td>
                    <td><small style="color:#64748b;" title="${item.detalle_motor || ''}">${(item.detalle_motor || '-').substring(0,50)}${(item.detalle_motor || '').length > 50 ? '…' : ''}</small></td>
                    <td>${estadoBadge[estado] || estado}</td>
                    <td><small>${ts}</small></td>
                    <td>
                        ${estado === 'pendiente' ? `
                        <button onclick="marcarAuditoria('${auditId}','revisado')" style="padding:3px 8px;border-radius:5px;background:#dbeafe;color:#1d4ed8;border:none;cursor:pointer;font-size:0.75rem;margin-bottom:3px;">Marcar Revisado</button>
                        <button onclick="marcarAuditoria('${auditId}','aprobado')" style="padding:3px 8px;border-radius:5px;background:#dcfce7;color:#15803d;border:none;cursor:pointer;font-size:0.75rem;">Aprobar</button>
                        ` : `<span style="color:#94a3b8;font-size:0.75rem;">${item.revisado_por || '-'}</span>`}
                    </td>
                `;
                tbody.appendChild(tr);
            });
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="10" class="table-empty">Error de red al cargar la bandeja de auditoría.</td></tr>';
            console.error("Error loadAuditoriaDescuentos:", err);
        }
    }

    // Exposed globally so inline onclick buttons can call it
    window.marcarAuditoria = async function(auditId, nuevoEstado) {
        try {
            const res = await fetch(`/api/auditoria-descuentos/${encodeURIComponent(auditId)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ audit_id: auditId, estado: nuevoEstado }),
            });
            if (res.ok) {
                loadAuditoriaDescuentos();
            } else {
                const data = await res.json();
                alert(`Error al actualizar: ${data.detail || res.statusText}`);
            }
        } catch (err) {
            console.error("Error marcarAuditoria:", err);
        }
    };

    // Wire up audit filters and refresh button
    const auditRefreshBtn = document.getElementById("audit-refresh-btn");
    if (auditRefreshBtn) {
        auditRefreshBtn.addEventListener("click", loadAuditoriaDescuentos);
    }
    ["audit-tipo-filter", "audit-estado-filter"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener("change", loadAuditoriaDescuentos);
    });

    // Filter report table in real-time
    const reporteSearchEl = document.getElementById("reporte-search");
    if (reporteSearchEl) {
        reporteSearchEl.addEventListener("keyup", () => {
            applyReporteFilters();
        });
    }

    // Form submit handlers for new discount panels
    const recompraForm = document.getElementById("recompra-form");
    if (recompraForm) {
        recompraForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const marcas = getM2MCheckedValues(recompraForm, ".m2m-rec-marca");
            const cats = getM2MCheckedValues(recompraForm, ".m2m-rec-cat");
            const listas = getM2MCheckedValues(recompraForm, ".m2m-rec-lista");
            const rawPct = (document.getElementById("cfg-rec-porcentaje")?.value || "0.03").replace(',', '.');
            const payload = {
                marca: marcas,
                categoria: cats,
                listas_aplicables: listas,
                porcentaje: parseFloat(rawPct),
                min_cajas: parseInt(document.getElementById("cfg-rec-min-cajas")?.value || 1),
                max_cajas: parseInt(document.getElementById("cfg-rec-max-cajas")?.value || 9999),
                max_usos_mes: parseInt(document.getElementById("cfg-rec-max-usos")?.value || 1),
                dias_ventana: parseInt(document.getElementById("cfg-rec-ventana")?.value || 30),
                unidad_medida: document.getElementById("cfg-rec-unidad")?.value || "CAJAS",
                tipo_beneficio: document.getElementById("cfg-rec-tipo-benef")?.value || "descuento",
                vigencia_desde: document.getElementById("cfg-rec-desde")?.value || new Date().toISOString().split('T')[0],
                vigencia_hasta: document.getElementById("cfg-rec-hasta")?.value || null,
                activo: true,
                requiere_pago_previo: document.getElementById("cfg-rec-requiere-pago-previo")?.checked || false,
                aplica_a: document.getElementById("cfg-rec-aplica-a")?.value || "linea"
            };
            try {
                const res = await fetch("/api/config/descuentos-recompra", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert("✅ Regla de recompra registrada correctamente.");
                    recompraForm.reset();
                    loadRecompra();
                    if (window.loadReglasConsolidadas) window.loadReglasConsolidadas();
                } else {
                    const err = await res.json();
                    alert(`❌ Error al guardar: ${err.detail || 'Error en servidor'}`);
                }
            } catch (err) {
                console.error("Error guardando recompra:", err);
                alert("❌ Error de red al guardar regla de recompra.");
            }
        });
    }

    const prontoPagoForm = document.getElementById("pronto-pago-form");
    if (prontoPagoForm) {
        prontoPagoForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const marcas = getM2MCheckedValues(prontoPagoForm, ".m2m-pp-marca");
            const cats = getM2MCheckedValues(prontoPagoForm, ".m2m-pp-cat");
            const listas = getM2MCheckedValues(prontoPagoForm, ".m2m-pp-lista");
            const rawPct = (document.getElementById("cfg-pp-porcentaje")?.value || "0.05").replace(',', '.');
            const payload = {
                dias_gracia: parseInt(document.getElementById("cfg-pp-dias-gracia")?.value || 0),
                marca: marcas,
                categoria: cats,
                min_cantidad: parseFloat(document.getElementById("cfg-pp-min")?.value || 0),
                max_cantidad: parseFloat(document.getElementById("cfg-pp-max")?.value || 999999),
                unidad_medida: document.getElementById("cfg-pp-unidad")?.value || "CAJAS",
                tipo_beneficio: document.getElementById("cfg-pp-tipo-benef")?.value || "descuento",
                porcentaje: parseFloat(rawPct),
                monedas_aplicables: document.getElementById("cfg-pp-monedas")?.value || "*",
                listas_aplicables: listas,
                vigencia_desde: document.getElementById("cfg-pp-desde")?.value || new Date().toISOString().split('T')[0],
                vigencia_hasta: document.getElementById("cfg-pp-hasta")?.value || null,
                activo: true,
                requiere_pago_previo: document.getElementById("cfg-pp-requiere-pago-previo")?.checked ?? true,
                aplica_a: document.getElementById("cfg-pp-aplica-a")?.value || "linea"
            };
            try {
                const res = await fetch("/api/config/descuentos-pronto-pago", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert("✅ Regla de pronto pago registrada correctamente.");
                    prontoPagoForm.reset();
                    loadProntoPago();
                    if (window.loadReglasConsolidadas) window.loadReglasConsolidadas();
                } else {
                    const err = await res.json();
                    alert(`❌ Error al guardar: ${err.detail || 'Error en servidor'}`);
                }
            } catch (err) {
                console.error("Error guardando pronto pago:", err);
                alert("❌ Error de red al registrar pronto pago.");
            }
        });
    }

    if (promoForm) {
        promoForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const marcas = getM2MCheckedValues(promoForm, ".m2m-promo-marca");
            const cats = getM2MCheckedValues(promoForm, ".m2m-promo-cat");
            const listas = getM2MCheckedValues(promoForm, ".m2m-promo-lista");
            const selProds = Array.from(document.getElementById("cfg-promo-productos")?.selectedOptions || []).map(o => o.value).join(",");
            const rawFallback = (document.getElementById("cfg-promo-fallback")?.value || "0.02").replace(',', '.');
            const rawValor = (document.getElementById("cfg-promo-valor")?.value || "0").replace(',', '.');
            const payload = {
                marca: marcas,
                categoria: cats,
                listas_aplicables: listas,
                tipo_beneficio: document.getElementById("cfg-promo-tipo-beneficio")?.value || "producto",
                productos: selProds || "*",
                compra_minima: parseFloat(document.getElementById("cfg-promo-compra-minima")?.value || 0),
                max_cantidad: parseFloat(document.getElementById("cfg-promo-max")?.value || 999999),
                unidad_medida: document.getElementById("cfg-promo-unidad")?.value || "CAJAS",
                regalo_tipo: document.getElementById("cfg-promo-regalo-tipo")?.value || "solo_uno",
                descuento_fallback: parseFloat(rawFallback),
                valor: parseFloat(rawValor),
                categorias_aplica: cats,
                solo_primera_compra: (document.getElementById("cfg-promo-solo-primera")?.value === "true"),
                vigencia_desde: document.getElementById("cfg-promo-desde")?.value || new Date().toISOString().split('T')[0],
                vigencia_hasta: document.getElementById("cfg-promo-hasta")?.value || null,
                activo: true,
                requiere_pago_previo: document.getElementById("cfg-promo-requiere-pago-previo")?.checked || false,
                aplica_a: document.getElementById("cfg-promo-aplica-a")?.value || "linea"
            };
            try {
                const res = await fetch("/api/config/promociones", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert("✅ Regla de obsequio / promoción registrada correctamente.");
                    promoForm.reset();
                    loadPromociones();
                    if (window.loadReglasConsolidadas) window.loadReglasConsolidadas();
                } else {
                    const err = await res.json();
                    alert(`❌ Error al guardar: ${err.detail || 'Error en servidor'}`);
                }
            } catch (err) {
                console.error("Error guardando promoción primera compra:", err);
                alert("❌ Error de red al registrar promoción.");
            }
        });
    }

    const productoPromoForm = document.getElementById("producto-promo-form");
    if (productoPromoForm) {
        productoPromoForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const marcas = getM2MCheckedValues(productoPromoForm, ".m2m-prod-marca");
            const cats = getM2MCheckedValues(productoPromoForm, ".m2m-prod-cat");
            const listas = getM2MCheckedValues(productoPromoForm, ".m2m-prod-lista");
            const selProds = Array.from(document.getElementById("cfg-prod-select")?.selectedOptions || []).map(o => o.value).join(",");
            const rawPct = (document.getElementById("cfg-prod-porcentaje")?.value || "0.05").replace(',', '.');
            const payload = {
                productos: selProds || "*",
                marca: marcas,
                categoria: cats,
                min_cantidad: parseFloat(document.getElementById("cfg-prod-min")?.value || 0),
                max_cantidad: parseFloat(document.getElementById("cfg-prod-max")?.value || 999999),
                unidad_medida: document.getElementById("cfg-prod-unidad")?.value || "CAJAS",
                tipo_beneficio: document.getElementById("cfg-prod-tipo-benef")?.value || "descuento",
                porcentaje: parseFloat(rawPct),
                monedas_aplicables: document.getElementById("cfg-prod-monedas")?.value || "*",
                listas_aplicables: listas,
                vigencia_desde: document.getElementById("cfg-prod-desde")?.value || new Date().toISOString().split('T')[0],
                vigencia_hasta: document.getElementById("cfg-prod-hasta")?.value || null,
                activo: true,
                requiere_pago_previo: document.getElementById("cfg-prod-requiere-pago-previo")?.checked || false,
                aplica_a: document.getElementById("cfg-prod-aplica-a")?.value || "linea"
            };
            try {
                const res = await fetch("/api/config/descuentos-producto", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert("✅ Regla de promoción por producto registrada.");
                    productoPromoForm.reset();
                    loadProductoPromo();
                    if (window.loadReglasConsolidadas) window.loadReglasConsolidadas();
                } else {
                    const err = await res.json();
                    alert(`❌ Error al guardar: ${err.detail || 'Error en servidor'}`);
                }
            } catch (err) {
                console.error("Error guardando descuento producto:", err);
                alert("❌ Error de red.");
            }
        });
    }

    const diferencialForm = document.getElementById("diferencial-form");
    if (diferencialForm) {
        diferencialForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const marcas = getM2MCheckedValues(diferencialForm, ".m2m-dif-marca");
            const cats = getM2MCheckedValues(diferencialForm, ".m2m-dif-cat");
            const listas = getM2MCheckedValues(diferencialForm, ".m2m-dif-lista");
            const rawPct = (document.getElementById("cfg-dif-porcentaje-fijo")?.value || "0.35").replace(',', '.');
            const payload = {
                nombre: document.getElementById("cfg-dif-nombre")?.value || "Diferencial Cambiario",
                tipo_diferencial: document.getElementById("cfg-dif-tipo-diferencial")?.value || "fijo_35_ves_usd",
                tipo_calculo: document.getElementById("cfg-dif-tipo-calculo")?.value || "fijo",
                porcentaje_fijo: parseFloat(rawPct),
                marca: marcas,
                categoria: cats,
                monedas_aplicables: document.getElementById("cfg-dif-monedas")?.value || "*",
                listas_aplicables: listas,
                unidad_medida: document.getElementById("cfg-dif-unidad")?.value || "USD",
                min_cantidad: parseFloat(document.getElementById("cfg-dif-min")?.value || 0),
                max_cantidad: parseFloat(document.getElementById("cfg-dif-max")?.value || 999999),
                vigencia_desde: document.getElementById("cfg-dif-desde")?.value || new Date().toISOString().split('T')[0],
                vigencia_hasta: document.getElementById("cfg-dif-hasta")?.value || null,
                activo: true,
                requiere_pago_previo: document.getElementById("cfg-dif-requiere-pago-previo")?.checked ?? true,
                aplica_a: document.getElementById("cfg-dif-aplica-a")?.value || "linea"
            };
            try {
                const res = await fetch("/api/config/descuentos-diferencial-cambiario", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert("✅ Regla de diferencial cambiario registrada.");
                    diferencialForm.reset();
                    loadDiferencial();
                    if (window.loadReglasConsolidadas) window.loadReglasConsolidadas();
                } else {
                    const err = await res.json();
                    alert(`❌ Error al guardar: ${err.detail || 'Error en servidor'}`);
                }
            } catch (err) {
                console.error("Error guardando diferencial cambiario:", err);
                alert("❌ Error de red.");
            }
        });
    }

    // --- Tab 3: Configuration Panels ---
    async function loadConfigData() {
        const loaders = [
            loadTasasPromedios,
            loadProntoPago,
            loadRecompra,
            loadProductoPromo,
            loadDiferencial,
            loadTasas,
            loadFeriados,
            populateBrandsAndCategories,
            loadDescuentosMarca,
            loadDescuentosVolumen,
            loadPromociones,
            loadExclusiones,
            loadListasPrecio,
            loadListasMapeo,
            loadOdooProductos,
            loadClientesAuditoria,
            loadSettingsMeta
        ];
        for (const fn of loaders) {
            try {
                if (typeof fn === "function") await fn();
            } catch (err) {
                console.error(`Error ejecutando ${fn.name || 'loader'}:`, err);
            }
        }
    }

    // Load general Settings meta variables
    async function loadSettingsMeta() {
        try {
            const res = await fetch("/api/config/meta");
            if (res.ok) {
                const data = await res.json();
                if (cfgMetaDays) cfgMetaDays.value = data.cash_window_business_days || 3;
                if (cfgMetaRecompra) cfgMetaRecompra.value = data.descuento_recompra || 0.05;
            }
        } catch (err) {
            console.error("Error loading settings meta:", err);
        }
    }

    // Save global settings variables
    if (settingsForm) {
        settingsForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const payload = {
                cash_window_business_days: parseInt(cfgMetaDays.value),
                descuento_recompra: parseFloat(cfgMetaRecompra.value)
            };

            try {
                const res = await fetch("/api/config/meta", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });

                if (res.ok) {
                    alert("✅ Ajustes generales del motor guardados correctamente en Google Sheets.");
                    loadSettingsMeta();
                } else {
                    alert("❌ Error al guardar los ajustes.");
                }
            } catch (err) {
                alert("❌ Error de red al guardar ajustes.");
                console.error(err);
            }
        });
    }

    async function loadTasas() {
        if (!tasasTableBody) return;
        try {
            tasasTableBody.innerHTML = '<tr><td colspan="4" class="table-empty">Cargando tasas...</td></tr>';
            const res = await fetch("/api/config/tasas");
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    tasasTableBody.innerHTML = '<tr><td colspan="4" class="table-empty">No hay tasas registradas.</td></tr>';
                    return;
                }
                
                tasasTableBody.innerHTML = "";
                data.forEach(t => {
                    const row = document.createElement("tr");
                    const diffBs = t.diferencia_bs !== undefined ? t.diferencia_bs : (t.tasa_binance - t.tasa_bcv);
                    const diffPct = t.diferencia_pct !== undefined ? t.diferencia_pct : (t.tasa_binance > 0 ? ((diffBs / t.tasa_binance) * 100) : 0);
                    row.innerHTML = `
                        <td>${t.timestamp}</td>
                        <td><strong>${t.tasa_bcv.toFixed(4)} Bs</strong></td>
                        <td><strong>${t.tasa_binance.toFixed(4)} Bs</strong></td>
                        <td><strong style="color: #d97706">+Bs. ${diffBs.toFixed(2)} (${diffPct.toFixed(1)}%)</strong></td>
                    `;
                    tasasTableBody.appendChild(row);
                });
            }
        } catch (err) {
            console.error(err);
        }
    }

    // Sync Odoo currency rates trigger
    if (btnSyncOdooRates) {
        btnSyncOdooRates.addEventListener("click", async () => {
            btnSyncOdooRates.disabled = true;
            btnSyncOdooRates.textContent = "Sincronizando...";
            try {
                const res = await fetch("/api/config/tasas/sync-odoo", { method: "POST" });
                if (res.ok) {
                    const data = await res.json();
                    alert(`✅ ${data.message}`);
                    loadTasas();
                } else {
                    alert("❌ Error al sincronizar tasas de Odoo.");
                }
            } catch (err) {
                alert("❌ Error de red al sincronizar tasas.");
                console.error(err);
            } finally {
                btnSyncOdooRates.disabled = false;
                btnSyncOdooRates.textContent = "🔄 Sincronizar Odoo";
            }
        });
    }

    // --- Load Rates Averages & Differential ---
    async function loadTasasPromedios() {
        try {
            const res = await fetch("/api/config/tasas-promedios");
            if (res.ok) {
                const d = await res.json();
                const bcvLbl = document.getElementById("lbl-rate-bcv-actual");
                const vigLbl = document.getElementById("lbl-rate-binance-vigente");
                const mLbl = document.getElementById("lbl-rate-binance-manana");
                const tLbl = document.getElementById("lbl-rate-binance-tarde");
                const dLbl = document.getElementById("lbl-rate-binance-diario");
                const diffLbl = document.getElementById("lbl-rate-diferencial-pct");

                if (bcvLbl) bcvLbl.textContent = d.tasa_bcv_actual ? `Bs. ${d.tasa_bcv_actual.toFixed(2)}` : "-";
                if (vigLbl) vigLbl.textContent = d.tasa_binance_vigente ? `Bs. ${d.tasa_binance_vigente.toFixed(2)}` : "-";
                if (mLbl) mLbl.textContent = d.tasa_binance_manana ? `Bs. ${d.tasa_binance_manana.toFixed(2)}` : "N/A";
                if (tLbl) tLbl.textContent = d.tasa_binance_tarde ? `Bs. ${d.tasa_binance_tarde.toFixed(2)}` : "N/A";
                if (dLbl) dLbl.textContent = d.tasa_binance_diario ? `Bs. ${d.tasa_binance_diario.toFixed(2)}` : "N/A";
                if (diffLbl) diffLbl.textContent = `${d.diferencial_bcv_binance_pct.toFixed(2)}%`;
            }
        } catch (err) {
            console.error("Error loading tasas promedios:", err);
        }
    }

    // Helper for interactive toggle switch
    async function toggleRuleActive(tabla, reglaId, newActive) {
        try {
            const res = await fetch("/api/config/toggle-descuento", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ tabla: tabla, regla_id: reglaId, activo: newActive })
            });
            if (res.ok) {
                if (window.loadReglasConsolidadas) window.loadReglasConsolidadas();
            } else {
                alert("❌ Error al cambiar estado de la regla.");
            }
        } catch (err) {
            console.error("Error toggling rule:", err);
        }
    }
    window.toggleReglaActiva = toggleRuleActive;

    window.eliminarRegla = async function(tabla, reglaId) {
        if (!confirm(`⚠️ ¿Estás seguro de que deseas ELIMINAR permanentemente la regla ${reglaId}?`)) return;
        try {
            const res = await fetch("/api/config/eliminar-descuento", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ tabla: tabla, regla_id: reglaId })
            });
            const data = await res.json();
            if (res.ok) {
                alert("🗑️ " + (data.message || "Regla eliminada permanentemente."));
                if (window.loadReglasConsolidadas) window.loadReglasConsolidadas();
                if (window.loadDescuentosVolumen) window.loadDescuentosVolumen();
                if (window.loadDescuentosProntoPago) window.loadDescuentosProntoPago();
                if (window.loadRecompra) window.loadRecompra();
                if (window.loadDescuentosProducto) window.loadDescuentosProducto();
                if (window.loadDescuentosDiferencial) window.loadDescuentosDiferencial();
            } else {
                alert("❌ Error: " + (data.detail || "No se pudo eliminar la regla."));
            }
        } catch (err) {
            console.error("Error eliminando regla:", err);
            alert("❌ Error de red al eliminar la regla.");
        }
    };

    // --- Load Pronto Pago Rules ---
    async function loadProntoPago() {
        const tbody = document.getElementById("pronto-pago-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="10" class="table-empty">Cargando pronto pago...</td></tr>';
            const res = await fetch("/api/config/descuentos-pronto-pago");
            if (res.ok) {
                const rules = await res.json();
                if (rules.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="10" class="table-empty">No hay reglas de pronto pago.</td></tr>';
                    return;
                }
                tbody.innerHTML = "";
                rules.forEach(r => tbody.appendChild(renderStandardRuleRow(r, "DescuentosProntoPago")));
            }
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="10" class="table-empty">Error al cargar pronto pago.</td></tr>';
        }
    }
    window.loadDescuentosProntoPago = loadProntoPago;

    // --- Load Recompra Rules ---
    async function loadRecompra() {
        const tbody = document.getElementById("recompra-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="10" class="table-empty">Cargando reglas de recompra...</td></tr>';
            const res = await fetch("/api/config/descuentos-recompra");
            if (res.ok) {
                const rules = await res.json();
                if (rules.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="10" class="table-empty">No hay reglas de recompra.</td></tr>';
                    return;
                }
                tbody.innerHTML = "";
                rules.forEach(r => tbody.appendChild(renderStandardRuleRow(r, "DescuentosRecompra")));
            }
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="10" class="table-empty">Error al cargar recompra.</td></tr>';
        }
    }
    window.loadRecompra = loadRecompra;

    // --- Load Producto Promo Rules ---
    async function loadProductoPromo() {
        const tbody = document.getElementById("producto-promo-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="10" class="table-empty">Cargando promociones de productos...</td></tr>';
            const res = await fetch("/api/config/descuentos-producto");
            if (res.ok) {
                const rules = await res.json();
                if (rules.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="10" class="table-empty">No hay promociones por producto.</td></tr>';
                    return;
                }
                tbody.innerHTML = "";
                rules.forEach(r => tbody.appendChild(renderStandardRuleRow(r, "DescuentosProducto")));
            }
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="10" class="table-empty">Error al cargar promociones de producto.</td></tr>';
        }
    }
    window.loadDescuentosProducto = loadProductoPromo;

    // --- Load Diferencial Rules ---
    async function loadDiferencial() {
        const tbody = document.getElementById("diferencial-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="10" class="table-empty">Cargando reglas de diferencial...</td></tr>';
            const res = await fetch("/api/config/descuentos-diferencial-cambiario");
            if (res.ok) {
                const rules = await res.json();
                if (rules.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="10" class="table-empty">No hay reglas de diferencial cambiario.</td></tr>';
                    return;
                }
                tbody.innerHTML = "";
                rules.forEach(r => tbody.appendChild(renderStandardRuleRow(r, "DescuentosDiferencialCambiario")));
            }
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="10" class="table-empty">Error al cargar diferencial cambiario.</td></tr>';
        }
    }
    window.loadDescuentosDiferencial = loadDiferencial;

    async function loadFeriados() {
        try {
            feriadosTableBody.innerHTML = '<tr><td colspan="3" class="table-empty">Cargando feriados...</td></tr>';
            const res = await fetch("/api/config/feriados");
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    feriadosTableBody.innerHTML = '<tr><td colspan="3" class="table-empty">No hay feriados registrados.</td></tr>';
                    return;
                }
                
                feriadosTableBody.innerHTML = "";
                data.forEach(f => {
                    const row = document.createElement("tr");
                    row.innerHTML = `
                        <td><strong>${f.fecha}</strong></td>
                        <td>${f.descripcion}</td>
                        <td><span class="state-badge">${f.tipo}</span></td>
                    `;
                    feriadosTableBody.appendChild(row);
                });
            }
        } catch (err) {
            console.error(err);
        }
    }

    // Populate Brands and Categories from Odoo in discount registration dropdowns
    async function populateBrandsAndCategories() {
        try {
            // Fetch brands
            const bRes = await fetch("/api/odoo/marcas");
            if (bRes.ok) {
                const brands = await bRes.json();
                const ppMarca = document.getElementById("cfg-pp-marca");
                const prodMarca = document.getElementById("cfg-prod-marca");
                if (ppMarca) ppMarca.innerHTML = '<option value="*">Todas las marcas (*)</option>';
                if (prodMarca) prodMarca.innerHTML = '<option value="*">Todas las marcas (*)</option>';
                brands.forEach(b => {
                    const opt = document.createElement("option");
                    opt.value = b;
                    opt.textContent = b;
                    if (ppMarca) ppMarca.appendChild(opt.cloneNode(true));
                    if (prodMarca) prodMarca.appendChild(opt.cloneNode(true));
                });
            }

            // Fetch categories
            const cRes = await fetch("/api/odoo/categorias");
            if (cRes.ok) {
                const cats = await cRes.json();
                const ppCat = document.getElementById("cfg-pp-categoria");
                const prodCat = document.getElementById("cfg-prod-cat");
                if (ppCat) ppCat.innerHTML = '<option value="*">Todas las categorías (*)</option>';
                if (prodCat) prodCat.innerHTML = '<option value="*">Todas las categorías (*)</option>';
                cats.forEach(c => {
                    const opt = document.createElement("option");
                    opt.value = c;
                    opt.textContent = c;
                    if (ppCat) ppCat.appendChild(opt.cloneNode(true));
                    if (prodCat) prodCat.appendChild(opt.cloneNode(true));
                });
            }

            // Fetch products for product promo multiselect
            const pRes = await fetch("/api/odoo/productos");
            if (pRes.ok) {
                const prods = await pRes.json();
                const prodSel = document.getElementById("cfg-prod-select");
                if (prodSel) {
                    prodSel.innerHTML = "";
                    prods.forEach(p => {
                        const opt = document.createElement("option");
                        const code = (p.ref_interna && p.ref_interna !== "N/A") ? p.ref_interna : (p.default_code || p.id);
                        const name = p.nombre || p.name || `Producto ${p.id}`;
                        opt.value = code;
                        opt.textContent = `[${code}] ${name}`;
                        prodSel.appendChild(opt);
                    });
                }
            }
        } catch (err) {
            console.error("Error populating dropdowns:", err);
        }
    }

    async function loadDescuentosMarca() {
        if (!descuentosTableBody) return;
        try {
            descuentosTableBody.innerHTML = '<tr><td colspan="9" class="table-empty">Cargando descuentos...</td></tr>';
            const res = await fetch("/api/config/descuentos-marca");
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    descuentosTableBody.innerHTML = '<tr><td colspan="9" class="table-empty">No hay reglas registradas.</td></tr>';
                    return;
                }

                descuentosTableBody.innerHTML = "";
                data.forEach(r => {
                    const row = document.createElement("tr");
                    const listasText = formatListasDisplay(r.listas_aplicables);
                    row.innerHTML = `
                        <td><strong>${r.regla_id}</strong></td>
                        <td>${r.marca}</td>
                        <td>${r.categoria}</td>
                        <td><span class="state-badge">${r.tipo_descuento}</span></td>
                        <td><strong>${(r.porcentaje * 100).toFixed(2)}%</strong></td>
                        <td>${r.vigencia_desde || 'N/A'}</td>
                        <td>${r.vigencia_hasta || 'N/A'}</td>
                        <td><span class="state-badge" style="background:#f3f4f6; color:#374151;">${listasText}</span></td>
                        <td><span class="semaphore ${r.activo ? 'verde' : 'rojo'}">${r.activo ? 'Activo' : 'Inactivo'}</span></td>
                    `;
                    descuentosTableBody.appendChild(row);
                });
            }
        } catch (err) {
            console.error(err);
        }
    }

    async function loadListasPrecio() {
        if (!listasPrecioTableBody) return;
        try {
            listasPrecioTableBody.innerHTML = '<tr><td colspan="6" class="table-empty">Cargando listas de precios de Odoo...</td></tr>';
            // Añadir timestamp para evitar que el browser cachee la respuesta
            const res = await fetch(`/api/config/listas-precio?_t=${Date.now()}`);
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    listasPrecioTableBody.innerHTML = '<tr><td colspan="6" class="table-empty">No hay listas de precios en Odoo.</td></tr>';
                    return;
                }

                listasPrecioTableBody.innerHTML = "";
                data.forEach(pl => {
                    const row = document.createElement("tr");

                    // Usar fechas pre-calculadas del servidor (más precisas que buscar en reglas)
                    const startVal = pl.fecha_desde || "N/A";
                    const endVal = pl.fecha_hasta || "N/A";

                    row.innerHTML = `
                        <td><strong>#${pl.id}</strong></td>
                        <td>${pl.name}</td>
                        <td><strong>${pl.moneda}</strong></td>
                        <td><span class="semaphore ${pl.active ? 'verde' : 'rojo'}">${pl.active ? 'Vigente' : 'Archivado'}</span></td>
                        <td><strong>${pl.reglas.length} reglas</strong></td>
                        <td>Desde: ${startVal} / Hasta: ${endVal}</td>
                    `;
                    listasPrecioTableBody.appendChild(row);
                });
            }
        } catch (err) {
            listasPrecioTableBody.innerHTML = '<tr><td colspan="6" class="table-empty">Error de red al cargar listas de precios.</td></tr>';
            console.error(err);
        }
    }

    // Load Odoo Product catalog with USD & VES pricelists prices & litros (Public price removed)
    async function loadOdooProductos() {
        if (!productosTableBody) return;
        try {
            productosTableBody.innerHTML = '<tr><td colspan="5" class="table-empty">Cargando catálogo de productos Odoo...</td></tr>';
            const res = await fetch("/api/odoo/productos");
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    productosTableBody.innerHTML = '<tr><td colspan="5" class="table-empty">No hay productos disponibles.</td></tr>';
                    return;
                }

                productosTableBody.innerHTML = "";
                data.forEach(p => {
                    const row = document.createElement("tr");
                    const fmt = (v) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(v);
                    
                    row.innerHTML = `
                        <td><strong>${p.ref_interna}</strong></td>
                        <td>${p.nombre}</td>
                        <td><strong>${p.litros ? p.litros.toFixed(2) : "0.00"} L</strong></td>
                        <td><strong style="color: #059669">${fmt(p.precio_usd)}</strong></td>
                        <td><strong style="color: #d97706">${fmt(p.precio_ves_usd)}</strong></td>
                    `;
                    productosTableBody.appendChild(row);
                });

                // Populate promotions products select dropdown
                if (cfgPromoProductos) {
                    cfgPromoProductos.innerHTML = '';
                    data.forEach(p => {
                        const opt = document.createElement("option");
                        opt.value = p.nombre || p.ref_interna || p.id;
                        opt.textContent = `[${p.ref_interna || 'N/A'}] ${p.nombre}`;
                        cfgPromoProductos.appendChild(opt);
                    });
                }
            } else {
                productosTableBody.innerHTML = '<tr><td colspan="5" class="table-empty">No se pudieron cargar los productos desde Odoo (Servidor retornó error).</td></tr>';
            }
        } catch (err) {
            productosTableBody.innerHTML = '<tr><td colspan="5" class="table-empty">Error de red al cargar productos de Odoo.</td></tr>';
            console.error("Error al cargar productos de Odoo:", err);
        }
    }

    // Load Odoo Client Sales stats for recurrence audit
    async function loadClientesAuditoria() {
        if (!clientesAuditoriaTableBody) return;
        try {
            clientesAuditoriaTableBody.innerHTML = '<tr><td colspan="8" class="table-empty">Cargando auditoría de clientes desde Odoo...</td></tr>';
            const res = await fetch("/api/odoo/clientes-auditoria");
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    clientesAuditoriaTableBody.innerHTML = '<tr><td colspan="8" class="table-empty">No hay clientes con estadísticas en Odoo.</td></tr>';
                    return;
                }

                clientesAuditoriaTableBody.innerHTML = "";
                data.forEach(c => {
                    const row = document.createElement("tr");
                    const litG = parseFloat(c.litros_global || 0).toLocaleString('es-VE', { minimumFractionDigits: 0, maximumFractionDigits: 1 });
                    const litS = parseFloat(c.litros_sinoco || 0).toLocaleString('es-VE', { minimumFractionDigits: 0, maximumFractionDigits: 1 });
                    row.innerHTML = `
                        <td><strong>#${c.id}</strong></td>
                        <td>${c.nombre}</td>
                        <td>${c.fecha_creacion}</td>
                        <td><strong style="color: #2563eb">${c.ventas_cantidad}</strong></td>
                        <td><strong style="color: #059669">${c.ventas_mes_actual || 0}</strong></td>
                        <td><span style="font-weight: 600; color: #1e293b">${litG} L</span></td>
                        <td><span style="font-weight: 600; color: #1e293b">${litS} L</span></td>
                        <td>${c.fecha_ultima_venta}</td>
                    `;
                    clientesAuditoriaTableBody.appendChild(row);
                });
            }
        } catch (err) {
            clientesAuditoriaTableBody.innerHTML = '<tr><td colspan="8" class="table-empty">Error de red al cargar clientes de Odoo.</td></tr>';
            console.error(err);
        }
    }

    // Save exchange rates
    tasaForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const payload = {
            tasa_bcv: parseFloat(cfgTasaBcv.value),
            tasa_binance: parseFloat(cfgTasaBinance.value)
        };

        try {
            const res = await fetch("/api/config/tasas", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });

            if (res.ok) {
                alert("✅ Tasas de cambio manuales cargadas exitosamente.");
                tasaForm.reset();
                loadTasas();
            } else {
                alert("❌ Error al guardar las tasas.");
            }
        } catch (err) {
            alert("❌ Error de red al registrar tasas.");
            console.error(err);
        }
    });

    // --- Load Master Consolidated Rules Matrix ---
    async function loadReglasConsolidadas() {
        const tbody = document.getElementById("reglas-consolidadas-table-body");
        if (!tbody) return;
        tbody.innerHTML = '<tr><td colspan="10" class="table-empty">Cargando matriz consolidada de reglas de descuento...</td></tr>';
        try {
            const res = await fetch("/api/reglas-descuento");
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="10" class="table-empty">No hay reglas de descuento registradas.</td></tr>';
                    return;
                }
                tbody.innerHTML = "";
                data.forEach(r => {
                    const row = document.createElement("tr");
                    
                    // Format Beneficio
                    let beneficioText = "";
                    if (r.tipo_beneficio === "producto" || r.tipo_beneficio === "obsequio") {
                        beneficioText = `🎁 Obsequio (${r.campos_especiales?.productos || 'Producto'})`;
                    } else {
                        const pctVal = (parseFloat(r.porcentaje || 0) * 100).toFixed(2);
                        beneficioText = `💲 ${pctVal}%`;
                    }

                    // Format Tramo
                    const minQ = r.min_cantidad !== undefined ? r.min_cantidad : 0;
                    const maxQ = r.max_cantidad !== undefined ? r.max_cantidad : 999999;
                    const tramoText = (maxQ >= 99999) ? `>= ${minQ}` : `${minQ} a ${maxQ}`;

                    // Format Listas
                    const rawListasCons = (r.listas_aplicables !== undefined && r.listas_aplicables !== null && String(r.listas_aplicables).trim() !== "" && String(r.listas_aplicables) !== "undefined")
                        ? String(r.listas_aplicables)
                        : "*";
                    const listasText = rawListasCons === "*" ? "Todas (*)" : (rawListasCons === "LISTAS_VES" ? "Listas VES (Mapeo)" : (rawListasCons === "LISTAS_USD" ? "Listas USD (Mapeo)" : (rawListasCons === "4" ? "Lista USD (#4)" : (rawListasCons === "5" ? "Lista VES (#5)" : rawListasCons))));

                    // Format Campos Especiales
                    let espArr = [];
                    if (r.campos_especiales) {
                        for (const [k, v] of Object.entries(r.campos_especiales)) {
                            if (v !== null && v !== undefined && v !== "" && v !== 0) {
                                espArr.push(`<small style="display:block; color:var(--text-muted)"><strong>${k}:</strong> ${v}</small>`);
                            }
                        }
                    }
                    const espText = espArr.length > 0 ? espArr.join("") : "—";

                    // Format Vigencia
                    const vigText = `${r.vigencia_desde || 'N/A'}<br><small style="color:var(--text-muted)">hasta ${r.vigencia_hasta || 'Indefinida'}</small>`;

                    // Format Interactive Switch
                    const switchHtml = `
                        <label class="switch" style="position:relative; display:inline-block; width:44px; height:22px;">
                            <input type="checkbox" ${r.activo ? 'checked' : ''} onchange="window.toggleReglaActiva('${r.tabla}', '${r.regla_id}', this.checked)" style="opacity:0; width:0; height:0;">
                            <span class="slider round" style="position:absolute; cursor:pointer; top:0; left:0; right:0; bottom:0; background-color:${r.activo ? '#10b981' : '#ef4444'}; transition:.3s; border-radius:22px;"></span>
                        </label>
                        <span style="font-size:0.75rem; font-weight:600; display:block; margin-top:2px; color:${r.activo ? '#10b981' : '#ef4444'}">${r.activo ? 'ACTIVA' : 'INACTIVA'}</span>
                    `;

                    const unidadCons = r.unidad_medida || (r.tipo_regla === "volumen" ? "LITROS" : (r.tabla === "DescuentosDiferencialCambiario" || r.tipo_regla === "bcv_completo" || r.tipo_regla === "diferencial" || r.tipo_diferencial ? "USD" : "UNIDADES"));

                    row.innerHTML = `
                        <td><strong>${r.tipo_nombre}</strong><br><small style="color:var(--text-muted)">${r.regla_id}</small></td>
                        <td><span class="state-badge" style="background:#e0f2fe; color:#0369a1; font-weight:600;">${r.marca || '*'}</span></td>
                        <td><span class="state-badge" style="background:#fef3c7; color:#92400e; font-weight:600;">${r.categoria || '*'}</span></td>
                        <td><strong>${tramoText}</strong></td>
                        <td><span class="state-badge" style="background:#f3f4f6; color:#374151;">${unidadCons}</span></td>
                        <td><strong style="color:#059669">${beneficioText}</strong></td>
                        <td><span class="state-badge" style="background:#f3f4f6; color:#374151;">${listasText}</span></td>
                        <td>${vigText}</td>
                        <td>${espText}</td>
                        <td>${switchHtml}</td>
                    `;
                    tbody.appendChild(row);
                });
            }
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="10" class="table-empty">Error de red al cargar la matriz de reglas.</td></tr>';
            console.error("Error cargando reglas consolidadas:", err);
        }
    }
    window.loadReglasConsolidadas = loadReglasConsolidadas;

    window.toggleReglaActiva = async function(tabla, reglaId, activo) {
        try {
            const res = await fetch("/api/config/toggle-descuento", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ tabla: tabla, regla_id: reglaId, activo: activo })
            });
            if (res.ok) {
                loadReglasConsolidadas();
                if (window.loadRecompra) window.loadRecompra();
                if (window.loadDescuentosProntoPago) window.loadDescuentosProntoPago();
                if (window.loadDescuentosVolumen) window.loadDescuentosVolumen();
                if (window.loadPromociones) window.loadPromociones();
                if (window.loadDescuentosProducto) window.loadDescuentosProducto();
                if (window.loadDescuentosDiferencial) window.loadDescuentosDiferencial();
            } else {
                alert("❌ No se pudo actualizar el estado de la regla.");
            }
        } catch (err) {
            console.error("Error toggle regla:", err);
            alert("❌ Error de comunicación al alternar estado.");
        }
    };

    // Save custom holiday
    if (feriadoForm) {
        feriadoForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const payload = {
                fecha: cfgFeriadoFecha.value,
                descripcion: cfgFeriadoDesc.value
            };

            try {
                const res = await fetch("/api/config/feriados", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });

                if (res.ok) {
                    alert("✅ Feriado registrado correctamente en Google Sheets.");
                    feriadoForm.reset();
                    loadFeriados();
                } else {
                    alert("❌ Error al guardar el feriado.");
                }
            } catch (err) {
                alert("❌ Error de red al registrar feriado.");
                console.error(err);
            }
        });
    }

    // Save Brand Discount Rule
    if (descuentoForm) {
        descuentoForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const payload = {
                marca: cfgDescMarca.value,
                categoria: cfgDescCat.value,
                tipo_descuento: cfgDescTipo.value,
                porcentaje: parseFloat(cfgDescPorcentaje.value),
                vigencia_desde: cfgDescDesde.value || new Date().toISOString().split('T')[0],
                vigencia_hasta: cfgDescHasta.value || null,
                listas_aplicables: cfgDescListas.value
            };

            try {
                const res = await fetch("/api/config/descuentos-marca", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });

                if (res.ok) {
                    alert("✅ Regla de descuento registrada correctamente en Google Sheets.");
                    descuentoForm.reset();
                    loadDescuentosMarca();
                } else {
                    alert("❌ Error al guardar la regla.");
                }
            } catch (err) {
                alert("❌ Error de red al registrar regla.");
                console.error(err);
            }
        });
    }

    // Promo tipo beneficio toggle
    if (cfgPromoTipoBeneficio) {
        cfgPromoTipoBeneficio.addEventListener("change", () => {
            const isProducto = cfgPromoTipoBeneficio.value === "producto";
            if (promoProductosSection) promoProductosSection.style.display = isProducto ? "" : "none";
            if (promoRegaloTipoSection) promoRegaloTipoSection.style.display = isProducto ? "" : "none";
            if (promoPorcentajeSection) promoPorcentajeSection.style.display = isProducto ? "none" : "";
        });
    }

    // Update product count display
    if (cfgPromoProductos) {
        cfgPromoProductos.addEventListener("change", () => {
            if (cfgPromoProductosCount) cfgPromoProductosCount.textContent = cfgPromoProductos.selectedOptions.length;
        });
    }

    // --- Helper to extract M2M Checkbox Values ---
    function getM2MCheckedValues(parentForm, selectorClass) {
        if (!parentForm) return "*";
        const cbs = parentForm.querySelectorAll(selectorClass);
        if (!cbs || cbs.length === 0) return "*";
        const checked = Array.from(cbs).filter(cb => cb.checked).map(cb => cb.value);
        const specific = checked.filter(val => val !== "*");
        if (specific.length > 0) {
            return specific.join(",");
        }
        return "*";
    }

    // --- Helper to Render Standardized 10-Column Rule Row ---
    function renderStandardRuleRow(r, tabla) {
        const row = document.createElement("tr");
        
        let beneficioText = "";
        if (r.tipo_beneficio === "producto" || r.tipo_beneficio === "obsequio") {
            beneficioText = `🎁 Obsequio (${r.productos || r.campos_especiales?.productos || 'Producto'})`;
        } else {
            const val = r.porcentaje !== undefined ? r.porcentaje : (r.porcentaje_fijo !== undefined ? r.porcentaje_fijo : (r.valor !== undefined ? r.valor : 0));
            const pctVal = (parseFloat(val || 0) * 100).toFixed(2);
            beneficioText = `💲 ${pctVal}%`;
        }

        const minQ = r.min_cantidad !== undefined ? r.min_cantidad : (r.min_cajas !== undefined ? r.min_cajas : (r.litros_minimo !== undefined ? r.litros_minimo : (r.compra_minima !== undefined ? r.compra_minima : 0)));
        const maxQ = r.max_cantidad !== undefined ? r.max_cantidad : (r.max_cajas !== undefined ? r.max_cajas : 999999);
        const tramoText = (maxQ >= 99999) ? `>= ${minQ}` : `${minQ} a ${maxQ}`;

        const rawListasStd = (r.listas_aplicables !== undefined && r.listas_aplicables !== null && String(r.listas_aplicables).trim() !== "" && String(r.listas_aplicables) !== "undefined")
            ? String(r.listas_aplicables)
            : "*";
        const listasText = formatListasDisplay(rawListasStd);

        let espArr = [];
        if (r.dias_gracia) espArr.push(`Gracia: ${r.dias_gracia}d`);
        if (r.max_usos_mes) espArr.push(`Max Usos: ${r.max_usos_mes}/mes`);
        if (r.dias_ventana) espArr.push(`Ventana: ${r.dias_ventana}d`);
        if (r.tipo_evaluacion) espArr.push(`Eval: ${r.tipo_evaluacion} (${r.dias_evaluacion || 0}d)`);
        if (r.descuento_fallback) espArr.push(`Fallback: ${(parseFloat(r.descuento_fallback)*100).toFixed(2)}%`);
        if (r.regalo_tipo) espArr.push(`Modo: ${r.regalo_tipo}`);
        if (r.monedas_aplicables && r.monedas_aplicables !== "*") espArr.push(`Monedas: ${r.monedas_aplicables}`);
        if (r.tipo_diferencial) espArr.push(`Tipo: ${r.tipo_diferencial}`);

        const espText = espArr.length > 0 ? espArr.join(" | ") : "—";
        const vigText = `${r.vigencia_desde || 'N/A'}<br><small style="color:var(--text-muted)">hasta ${r.vigencia_hasta || 'Indefinida'}</small>`;

        const actionsHtml = `
            <div style="display:flex; align-items:center; gap:8px;">
                <div>
                    <label class="switch" style="position:relative; display:inline-block; width:38px; height:20px;">
                        <input type="checkbox" ${r.activo ? 'checked' : ''} onchange="window.toggleReglaActiva('${tabla}', '${r.regla_id}', this.checked)" style="opacity:0; width:0; height:0;">
                        <span class="slider round" style="position:absolute; cursor:pointer; top:0; left:0; right:0; bottom:0; background-color:${r.activo ? '#10b981' : '#ef4444'}; transition:.3s; border-radius:20px;"></span>
                    </label>
                    <span style="font-size:0.7rem; font-weight:600; display:block; text-align:center; color:${r.activo ? '#10b981' : '#ef4444'}">${r.activo ? 'ACTIVA' : 'INACTIVA'}</span>
                </div>
                <button type="button" class="btn btn-sm" onclick="window.eliminarRegla('${tabla}', '${r.regla_id}')" style="background:#fee2e2; color:#dc2626; border:1px solid #fca5a5; padding:4px 8px; border-radius:6px; font-size:0.8rem; cursor:pointer;" title="Eliminar regla permanentemente">🗑️</button>
            </div>
        `;

        let unidadStd = r.unidad_medida;
        if (!unidadStd || String(unidadStd).trim() === "" || String(unidadStd) === "undefined") {
            const minVal = parseFloat(r.min_cantidad !== undefined ? r.min_cantidad : r.litros_minimo || 0);
            if (tabla === "DescuentosVolumen" || r.tipo_regla === "volumen") {
                unidadStd = (minVal >= 500 || (r.regla_id && String(r.regla_id).includes("FID_"))) ? "LITROS" : "UNIDADES";
            } else if (tabla === "DescuentosDiferencialCambiario" || r.tipo_regla === "bcv_completo" || r.tipo_diferencial) {
                unidadStd = "USD";
            } else {
                unidadStd = "UNIDADES";
            }
        }

        row.innerHTML = `
            <td><strong>${r.regla_id || 'REGLA'}</strong><br><small style="color:var(--text-muted)">${r.nombre || tabla}</small></td>
            <td><span class="state-badge" style="background:#e0f2fe; color:#0369a1; font-weight:600;">${r.marca || '*'}</span></td>
            <td><span class="state-badge" style="background:#fef3c7; color:#92400e; font-weight:600;">${r.categoria || r.categorias_aplica || '*'}</span></td>
            <td><strong>${tramoText}</strong></td>
            <td><span class="state-badge" style="background:#f3f4f6; color:#374151;">${unidadStd}</span></td>
            <td><strong style="color:#059669">${beneficioText}</strong></td>
            <td><span class="state-badge" style="background:#f3f4f6; color:#374151;">${listasText}</span></td>
            <td>${vigText}</td>
            <td><small style="color:var(--text-muted)">${espText}</small></td>
            <td>${actionsHtml}</td>
        `;
        return row;
    }

    // Load Promociones Primera Compra
    const TIPO_LABELS = {
        "primera_compra": "Primera Compra", "recurrencia": "Recompra",
        "contado": "Pronto Pago", "volumen": "Volumen", "bcv_completo": "Diferencial BCV"
    };

    async function loadPromociones() {
        if (!promosTableBody) return;
        try {
            promosTableBody.innerHTML = '<tr><td colspan="10" class="table-empty">Cargando promociones...</td></tr>';
            const res = await fetch("/api/config/promociones");
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    promosTableBody.innerHTML = '<tr><td colspan="10" class="table-empty">No hay promociones registradas.</td></tr>';
                    return;
                }
                promosTableBody.innerHTML = "";
                data.forEach(p => promosTableBody.appendChild(renderStandardRuleRow(p, "PromocionPrimeraCompra")));
            }
        } catch (err) {
            console.error(err);
        }
    }
    window.loadPromociones = loadPromociones;

    // Save Promotion Rule
    if (promoForm) {
        promoForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const marcas = getM2MCheckedValues(promoForm, ".m2m-promo-marca");
            const cats = getM2MCheckedValues(promoForm, ".m2m-promo-cat");
            const listas = getM2MCheckedValues(promoForm, ".m2m-promo-lista");
            const tipoBenef = cfgPromoTipoBeneficio.value;
            const productosSeleccionados = tipoBenef === "producto"
                ? Array.from(cfgPromoProductos.selectedOptions).map(o => o.value).join(",")
                : "";

            const payload = {
                tipo_beneficio: tipoBenef,
                productos: productosSeleccionados,
                valor: tipoBenef === "porcentaje" ? parseFloat(cfgPromoValor.value || 0) : 1,
                compra_minima: parseFloat(cfgPromoCompraMinima.value || 0),
                descuento_fallback: parseFloat(cfgPromoFallback.value || 0),
                regalo_tipo: cfgPromoRegaloTipo.value,
                categorias_aplica: cats,
                marca: marcas,
                listas_aplicables: listas,
                unidad_medida: document.getElementById("cfg-promo-unidad")?.value || "CAJAS",
                vigencia_desde: cfgPromoDesde.value,
                vigencia_hasta: cfgPromoHasta.value || null,
                requiere_pago_previo: document.getElementById("cfg-promo-requiere-pago-previo")?.checked || false,
                aplica_a: document.getElementById("cfg-promo-aplica-a")?.value || "linea"
            };
            try {
                const res = await fetch("/api/config/promociones", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert("✅ Promoción registrada exitosamente.");
                    promoForm.reset();
                    if (cfgPromoProductosCount) cfgPromoProductosCount.textContent = "0";
                    loadPromociones();
                    loadReglasConsolidadas();
                } else {
                    const err = await res.json();
                    alert("❌ Error: " + (err.detail || "Error al registrar la promoción."));
                }
            } catch (err) {
                alert("❌ Error de red al registrar promoción.");
                console.error(err);
            }
        });
    }

    // Load Exclusiones
    async function loadExclusiones() {
        if (!excluisionesTableBody) return;
        try {
            excluisionesTableBody.innerHTML = '<tr><td colspan="4" class="table-empty">Cargando exclusiones...</td></tr>';
            const res = await fetch("/api/config/exclusiones");
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    excluisionesTableBody.innerHTML = '<tr><td colspan="4" class="table-empty">No hay exclusiones configuradas.</td></tr>';
                    return;
                }
                excluisionesTableBody.innerHTML = "";
                data.forEach(exc => {
                    const row = document.createElement("tr");
                    row.innerHTML = `
                        <td><strong>${TIPO_LABELS[exc.regla_tipo_a] || exc.regla_tipo_a}</strong></td>
                        <td style="text-align:center">⟷</td>
                        <td><strong>${TIPO_LABELS[exc.regla_tipo_b] || exc.regla_tipo_b}</strong></td>
                        <td><span class="semaphore ${exc.activo ? 'verde' : 'rojo'}">${exc.activo ? 'Activa' : 'Inactiva'}</span></td>
                    `;
                    excluisionesTableBody.appendChild(row);
                });
            }
        } catch (err) {
            console.error(err);
        }
    }

    // Save Exclusion Rule
    if (exclusionForm) {
        exclusionForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            if (cfgExclTipoA.value === cfgExclTipoB.value) {
                alert("⚠️ Los dos descuentos no pueden ser el mismo tipo.");
                return;
            }
            const payload = {
                regla_tipo_a: cfgExclTipoA.value,
                regla_tipo_b: cfgExclTipoB.value,
                activo: true
            };
            try {
                const res = await fetch("/api/config/exclusiones", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert("✅ Exclusión registrada correctamente.");
                    exclusionForm.reset();
                    loadExclusiones();
                } else {
                    const err = await res.json();
                    alert("❌ Error: " + (err.detail || "Error al registrar la exclusión."));
                }
            } catch (err) {
                alert("❌ Error de red al registrar exclusión.");
                console.error(err);
            }
        });
    }

    // Load Volume Discount Rules
    async function loadDescuentosVolumen() {
        if (!descuentosVolumenTableBody) return;
        try {
            descuentosVolumenTableBody.innerHTML = '<tr><td colspan="10" class="table-empty">Cargando descuentos por volumen...</td></tr>';
            const res = await fetch("/api/config/descuentos-volumen");
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    descuentosVolumenTableBody.innerHTML = '<tr><td colspan="10" class="table-empty">No hay reglas de volumen registradas.</td></tr>';
                    return;
                }
                descuentosVolumenTableBody.innerHTML = "";
                data.forEach(r => descuentosVolumenTableBody.appendChild(renderStandardRuleRow(r, "DescuentosVolumen")));
            }
        } catch (err) {
            console.error(err);
        }
    }
    window.loadDescuentosVolumen = loadDescuentosVolumen;

    // Save Volume Discount Rule
    if (descuentoVolumenForm) {
        descuentoVolumenForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const marcas = getM2MCheckedValues(descuentoVolumenForm, ".m2m-vol-marca");
            const cats = getM2MCheckedValues(descuentoVolumenForm, ".m2m-vol-cat");
            const listas = getM2MCheckedValues(descuentoVolumenForm, ".m2m-vol-lista");
            const minQty = parseFloat(cfgDescVolLitros.value || 0);
            const payload = {
                marca: marcas,
                categoria: cats,
                listas_aplicables: listas,
                litros_minimo: minQty,
                min_cantidad: minQty,
                max_cantidad: parseFloat(document.getElementById("cfg-desc-vol-max")?.value || 999999),
                porcentaje: parseFloat(cfgDescVolPorcentaje.value || 0.05),
                tipo_evaluacion: document.getElementById("cfg-desc-vol-tipo-eval").value || "orden",
                dias_evaluacion: parseInt(document.getElementById("cfg-desc-vol-dias-eval").value || 30),
                unidad_medida: document.getElementById("cfg-desc-vol-unidad")?.value || "UNIDADES",
                tipo_beneficio: document.getElementById("cfg-desc-vol-tipo-benef")?.value || "descuento",
                vigencia_desde: cfgDescVolDesde.value || new Date().toISOString().split('T')[0],
                vigencia_hasta: cfgDescVolHasta.value || null,
                requiere_pago_previo: document.getElementById("cfg-desc-vol-requiere-pago-previo")?.checked || false,
                aplica_a: document.getElementById("cfg-desc-vol-aplica-a")?.value || "linea"
            };
            try {
                const res = await fetch("/api/config/descuentos-volumen", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert("✅ Regla de volumen registrada exitosamente.");
                    descuentoVolumenForm.reset();
                    loadDescuentosVolumen();
                    loadReglasConsolidadas();
                } else {
                    alert("❌ Error al registrar la regla de volumen.");
                }
            } catch (err) {
                alert("❌ Error de red al registrar regla de volumen.");
                console.error(err);
            }
        });
    }

    window.generarReciboSeleccionados = async function() {
        const checked = Array.from(document.querySelectorAll(".check-cobranza-item:checked")).map(cb => cb.value);
        if (checked.length === 0) {
            alert("⚠️ Por favor selecciona al menos un pago para generar el Recibo de Entrega.");
            return;
        }

        try {
            const res = await fetch("/api/cobranza/marcar-recibido", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ pago_ids: checked })
            });

            if (!res.ok) throw new Error("Error registrando entrega de recibo");
            const data = await res.json();

            // Populate printable receipt preview
            document.getElementById("recibo-num-1").textContent = data.numero_recibido;
            document.getElementById("recibo-num-2").textContent = data.numero_recibido;
            document.getElementById("recibo-fecha-1").textContent = `Fecha: ${data.fecha_recibido}`;
            document.getElementById("recibo-fecha-2").textContent = `Fecha: ${data.fecha_recibido}`;
            
            const firstPago = data.pagos[0] || {};
            const vendedorName = firstPago.vendedor || "Vendedor";
            document.getElementById("recibo-vendedor-1").textContent = vendedorName;
            document.getElementById("recibo-vendedor-2").textContent = vendedorName;
            document.getElementById("recibo-cajero-1").textContent = data.recibido_por;
            document.getElementById("recibo-cajero-2").textContent = data.recibido_por;

            document.getElementById("recibo-entregado-firma-1").textContent = `Firma: ${vendedorName}`;
            document.getElementById("recibo-entregado-firma-2").textContent = `Firma: ${vendedorName}`;
            document.getElementById("recibo-recibido-firma-1").textContent = `Firma: ${data.recibido_por}`;
            document.getElementById("recibo-recibido-firma-2").textContent = `Firma: ${data.recibido_por}`;

            const rowsHtml = data.pagos.map(p => `
                <tr>
                    <td><strong>${p.pago_id}</strong></td>
                    <td>${p.fecha || '-'}</td>
                    <td>${p.metodo_pago || 'Efectivo'} (${p.referencia || 'S/R'})</td>
                    <td><strong>${p.moneda || 'USD'} ${parseFloat(p.monto || 0).toFixed(2)}</strong></td>
                    <td>Bs. ${parseFloat(p.tasa_bcv || 0).toFixed(2)}</td>
                    <td><strong>$${parseFloat(p.equivalente_bcv_usd || p.monto || 0).toFixed(2)}</strong></td>
                    <td>${p.so_id || 'Pendiente'}</td>
                </tr>
            `).join('');

            document.getElementById("recibo-items-1").innerHTML = rowsHtml;
            document.getElementById("recibo-items-2").innerHTML = rowsHtml;

            document.getElementById("recibo-modal").style.display = "flex";
            if (typeof loadCobranzaUnificado === "function") loadCobranzaUnificado();
        } catch (err) {
            alert("❌ Error: " + err.message);
            console.error(err);
        }
    };

    window.cerrarReciboModal = function() {
        document.getElementById("recibo-modal").style.display = "none";
    };

    // REPORTE DIARIO DE VENTAS Y COBRANZA
    async function loadReporteDiario() {
        const vTbody = document.getElementById("diario-ventas-table-body");
        const cTbody = document.getElementById("diario-cobranza-table-body");
        if (!vTbody || !cTbody) return;

        vTbody.innerHTML = '<tr><td colspan="4" class="table-empty">Cargando reporte de ventas...</td></tr>';
        cTbody.innerHTML = '<tr><td colspan="4" class="table-empty">Cargando reporte de cobranza...</td></tr>';

        const vendedorSel = document.getElementById("dashboard-vendedor-filter");
        const fechaDesdeEl = document.getElementById("dashboard-fecha-desde");
        const fechaHastaEl = document.getElementById("dashboard-fecha-hasta");
        const vendedorVal = vendedorSel && vendedorSel.value !== "*" ? vendedorSel.value : "";
        const fechaDesdeVal = fechaDesdeEl ? fechaDesdeEl.value : "";
        const fechaHastaVal = fechaHastaEl ? fechaHastaEl.value : "";

        const params = new URLSearchParams();
        if (vendedorVal) params.set("vendedor", vendedorVal);
        if (fechaDesdeVal) params.set("fecha_desde", fechaDesdeVal);
        if (fechaHastaVal) params.set("fecha_hasta", fechaHastaVal);

        try {
            const res = await fetch("/api/reporte/diario?" + params.toString());
            if (!res.ok) throw new Error("Error consultando reporte diario");
            const data = await res.json();

            // Ventas
            if (data.ventas_diarias.length === 0) {
                vTbody.innerHTML = '<tr><td colspan="4" class="table-empty">No hay registros de ventas.</td></tr>';
            } else {
                vTbody.innerHTML = data.ventas_diarias.map(v => `
                    <tr>
                        <td><strong>${v.fecha}</strong></td>
                        <td>${v.ordenes_count} órds.</td>
                        <td><strong style="color:#2563eb">$${v.total_usd.toLocaleString('es-VE', {minimumFractionDigits:2})}</strong></td>
                        <td><strong style="color:#059669">${v.litros_totales.toLocaleString('es-VE', {minimumFractionDigits:1})} L</strong></td>
                    </tr>
                `).join('');
            }

            // Cobranza
            if (data.cobranza_diaria.length === 0) {
                cTbody.innerHTML = '<tr><td colspan="4" class="table-empty">No hay registros de cobranza.</td></tr>';
            } else {
                cTbody.innerHTML = data.cobranza_diaria.map(c => {
                    const monedaStr = Object.entries(c.por_moneda).map(([m, val]) => `${m}: ${val.toFixed(2)}`).join(' | ');
                    const metodoStr = Object.entries(c.por_metodo).map(([m, val]) => `${m}: $${val.toFixed(2)}`).join(' | ');
                    return `
                        <tr>
                            <td><strong>${c.fecha}</strong></td>
                            <td><strong style="color:#2563eb">$${c.total_eq_bcv.toLocaleString('es-VE', {minimumFractionDigits:2})}</strong></td>
                            <td><small>${monedaStr}</small></td>
                            <td><small>${metodoStr}</small></td>
                        </tr>
                    `;
                }).join('');
            }

            // Acumulados Hoy / Mes / Trimestre / Año
            const r = data.resumen || {};
            const fmtUsd = (val) => `$${(val || 0).toLocaleString('es-VE', {minimumFractionDigits:2})}`;
            const fmtL = (val) => `${(val || 0).toLocaleString('es-VE', {minimumFractionDigits:1})} L`;
            const fmtBs = (val) => `Bs. ${(val || 0).toLocaleString('es-VE', {minimumFractionDigits:2})}`;
            ["hoy", "mes", "trimestre", "anio"].forEach(periodo => {
                const vEl = document.getElementById(`dash-ventas-${periodo}-usd`);
                const lEl = document.getElementById(`dash-ventas-${periodo}-litros`);
                const cEl = document.getElementById(`dash-cobranza-${periodo}-usd`);
                const vesEl = document.getElementById(`dash-cobranza-${periodo}-ves`);
                const metodosEl = document.getElementById(`dash-cobranza-${periodo}-metodos`);
                const ventas = (r.ventas || {})[periodo] || {};
                const cobranza = (r.cobranza || {})[periodo] || {};
                if (vEl) vEl.textContent = fmtUsd(ventas.total_usd);
                if (lEl) lEl.textContent = fmtL(ventas.litros);
                if (cEl) cEl.textContent = fmtUsd(cobranza.total_eq_bcv);
                if (vesEl) vesEl.textContent = `${fmtBs(cobranza.ves_monto)} (${fmtUsd(cobranza.ves_eq_usd)})`;
                if (metodosEl) {
                    const porMetodo = cobranza.por_metodo || {};
                    const entries = Object.entries(porMetodo).sort((a, b) => b[1] - a[1]);
                    metodosEl.innerHTML = entries.length === 0
                        ? '<span style="color:#94a3b8;">Sin desglose por método.</span>'
                        : entries.map(([metodo, monto]) => `<div style="display:flex; justify-content:space-between;"><span>${metodo}:</span> <strong>${fmtUsd(monto)}</strong></div>`).join('');
                }
            });

            // Filtro de Vendedor (poblar dropdown una sola vez)
            if (vendedorSel && data.vendedores && !vendedorSel.dataset.populated) {
                const currentVal = vendedorSel.value || "*";
                data.vendedores.forEach(v => {
                    const opt = document.createElement("option");
                    opt.value = v;
                    opt.textContent = v;
                    vendedorSel.appendChild(opt);
                });
                vendedorSel.value = currentVal;
                vendedorSel.dataset.populated = "true";
            }

            if (vendedorSel && !vendedorSel.dataset.listenerAttached) {
                vendedorSel.addEventListener("change", loadReporteDiario);
                vendedorSel.dataset.listenerAttached = "true";
            }
            [fechaDesdeEl, fechaHastaEl].forEach(el => {
                if (el && !el.dataset.listenerAttached) {
                    el.addEventListener("change", loadReporteDiario);
                    el.dataset.listenerAttached = "true";
                }
            });
            const clearBtn = document.getElementById("dashboard-filter-clear");
            if (clearBtn && !clearBtn.dataset.listenerAttached) {
                clearBtn.addEventListener("click", () => {
                    if (vendedorSel) vendedorSel.value = "*";
                    if (fechaDesdeEl) fechaDesdeEl.value = "";
                    if (fechaHastaEl) fechaHastaEl.value = "";
                    loadReporteDiario();
                });
                clearBtn.dataset.listenerAttached = "true";
            }
        } catch (err) {
            console.error("Error cargando reporte diario:", err);
        }
    }

    // --- LISTAS DE PRECIOS MAPEO ---
    window.loadListasMapeo = async function() {
        const usdBox = document.getElementById("usd-pricelists-checkboxes");
        const vesBox = document.getElementById("ves-pricelists-checkboxes");
        if (!usdBox || !vesBox) return;

        try {
            usdBox.innerHTML = '<span style="font-size:0.85rem; color:#64748b;">Cargando...</span>';
            vesBox.innerHTML = '<span style="font-size:0.85rem; color:#64748b;">Cargando...</span>';

            const [plRes, mapRes] = await Promise.all([
                fetch('/api/config/listas-precio'),
                fetch('/api/config/listas-precio-mapeo')
            ]);

            const pricelists = await plRes.json();
            const mapData = await mapRes.json();

            const validUSD = (Array.isArray(mapData.valid_pricelists_usd) && mapData.valid_pricelists_usd.length > 0) ? mapData.valid_pricelists_usd.map(String) : ["4"];
            const validVES = (Array.isArray(mapData.valid_pricelists_ves) && mapData.valid_pricelists_ves.length > 0) ? mapData.valid_pricelists_ves.map(String) : ["5"];

            const histCheckbox = document.getElementById("cfg-historical-pricelist-enabled");
            if (histCheckbox) {
                histCheckbox.checked = mapData.historical_pricelist_enabled !== false;
            }

            if (!Array.isArray(pricelists) || pricelists.length === 0) {
                usdBox.innerHTML = '<span style="font-size:0.85rem; color:#94a3b8;">No se encontraron listas de precios en Odoo.</span>';
                vesBox.innerHTML = '<span style="font-size:0.85rem; color:#94a3b8;">No se encontraron listas de precios en Odoo.</span>';
                return;
            }

            usdBox.innerHTML = pricelists.map(pl => {
                const checked = validUSD.includes(String(pl.id)) ? 'checked' : '';
                return `
                    <label style="display:flex; align-items:center; gap:8px; font-size:0.88rem; cursor:pointer;">
                        <input type="checkbox" name="cfg_listas_usd" value="${pl.id}" ${checked}>
                        <span><strong>#${pl.id}</strong> - ${pl.name} (${pl.moneda})</span>
                    </label>
                `;
            }).join('');

            vesBox.innerHTML = pricelists.map(pl => {
                const checked = validVES.includes(String(pl.id)) ? 'checked' : '';
                return `
                    <label style="display:flex; align-items:center; gap:8px; font-size:0.88rem; cursor:pointer;">
                        <input type="checkbox" name="cfg_listas_ves" value="${pl.id}" ${checked}>
                        <span><strong>#${pl.id}</strong> - ${pl.name} (${pl.moneda})</span>
                    </label>
                `;
            }).join('');

            // Dynamically populate M2M Listas checkboxes in all rule forms
            const ruleFormListContainers = [
                { selector: ".m2m-rec-lista", parent: "m2m-rec-listas-box" },
                { selector: ".m2m-pp-lista", parent: "m2m-pp-listas-box" },
                { selector: ".m2m-vol-lista", parent: "m2m-vol-listas-box" },
                { selector: ".m2m-promo-lista", parent: "m2m-promo-listas-box" },
                { selector: ".m2m-prod-lista", parent: "m2m-prod-listas-box" },
                { selector: ".m2m-dif-lista", parent: "m2m-dif-listas-box" }
            ];

            ruleFormListContainers.forEach(cfg => {
                const elClass = cfg.selector.replace('.', '');
                const inputs = document.querySelectorAll(cfg.selector);
                if (inputs.length > 0) {
                    const parent = inputs[0].parentElement?.parentElement || document.getElementById(cfg.parent);
                    if (parent) {
                        const currentChecked = Array.from(inputs).filter(i => i.checked).map(i => i.value);
                        const isVesChecked = currentChecked.includes('LISTAS_VES') || (elClass === 'm2m-dif-lista' && currentChecked.length === 0);
                        const isUsdChecked = currentChecked.includes('LISTAS_USD');
                        
                        let html = `<label><input type="checkbox" class="${elClass}" value="LISTAS_VES" ${isVesChecked ? 'checked' : ''}> Listas VES (Mapeo)</label> `;
                        html += `<label><input type="checkbox" class="${elClass}" value="LISTAS_USD" ${isUsdChecked ? 'checked' : ''}> Listas USD (Mapeo)</label> `;
                        html += pricelists.map(pl => {
                            const isChecked = currentChecked.includes(String(pl.id)) ? 'checked' : '';
                            return `<label><input type="checkbox" class="${elClass}" value="${pl.id}" ${isChecked}> #${pl.id} ${pl.name}</label>`;
                        }).join(' ');
                        html += ` <label><input type="checkbox" class="${elClass}" value="*" ${currentChecked.includes('*') ? 'checked' : ''}> Todas (*)</label>`;
                        parent.innerHTML = html;
                    }
                }
            });
        } catch (err) {
            console.error("Error cargando mapeo de listas:", err);
            usdBox.innerHTML = '<span style="color:#ef4444; font-size:0.85rem;">Error al cargar listas.</span>';
            vesBox.innerHTML = '<span style="color:#ef4444; font-size:0.85rem;">Error al cargar listas.</span>';
        }
    };

    window.saveListasMapeo = async function(event) {
        if (event) event.preventDefault();
        const usdChecked = Array.from(document.querySelectorAll('input[name="cfg_listas_usd"]:checked')).map(el => el.value);
        const vesChecked = Array.from(document.querySelectorAll('input[name="cfg_listas_ves"]:checked')).map(el => el.value);

        if (usdChecked.length === 0 && vesChecked.length === 0) {
            alert("⚠️ Debes seleccionar al menos una lista de precios USD y una VES.");
            return;
        }

        const histCheckbox = document.getElementById("cfg-historical-pricelist-enabled");
        const histEnabled = histCheckbox ? histCheckbox.checked : true;

        try {
            const res = await fetch('/api/config/listas-precio-mapeo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    valid_pricelists_usd: usdChecked,
                    valid_pricelists_ves: vesChecked,
                    historical_pricelist_enabled: histEnabled
                })
            });
            const data = await res.json();
            if (res.ok) {
                // Use the saved values directly from the response to re-check boxes
                // This avoids stale cache issues from re-fetching
                const savedUSD = (data.valid_pricelists_usd || usdChecked).map(String);
                const savedVES = (data.valid_pricelists_ves || vesChecked).map(String);

                // Re-render with the confirmed saved values
                document.querySelectorAll('input[name="cfg_listas_usd"]').forEach(cb => {
                    cb.checked = savedUSD.includes(String(cb.value));
                });
                document.querySelectorAll('input[name="cfg_listas_ves"]').forEach(cb => {
                    cb.checked = savedVES.includes(String(cb.value));
                });

                alert("✅ Configuración guardada exitosamente.\nUSD: " + savedUSD.join(", ") + "\nVES: " + savedVES.join(", "));
            } else {
                alert("❌ Error: " + (data.detail || "No se pudo guardar."));
            }
        } catch (err) {
            console.error("Error guardando mapeo de listas:", err);
            alert("❌ Error de red al guardar la configuración.");
        }
    };

    // --- PAGOS PENDIENTES POR ASOCIAR (fusiona sugerencias FIFO + vinculación manual) ---
    let currentSugerenciasList = [];

    window.aprobarSugerenciaIndividual = async function(pago_id, so_id, monto_sugerido) {
        if (!confirm(`¿Confirmar asociación de $${monto_sugerido.toFixed(2)} del Pago ${pago_id} a la Orden ${so_id}?`)) return;

        try {
            const res = await fetch('/api/vincular', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    pago_id: pago_id,
                    so_id: so_id,
                    monto_aplicado: monto_sugerido
                })
            });
            const data = await res.json();
            if (res.ok) {
                alert("✅ Vinculación completada con éxito.");
                if (typeof loadCobranzaUnificado === "function") loadCobranzaUnificado();
                loadKPIs();
            } else {
                alert("❌ Error: " + (data.detail || "No se pudo vincular."));
            }
        } catch (err) {
            console.error("Error al vincular sugerencia:", err);
            alert("❌ Error de red.");
        }
    };

    window.cerrarPagoHuerfano = async function(pago_id) {
        const motivo = prompt(`¿Por qué se cierra el pago ${pago_id} a favor de la empresa?\n(Sin orden abierta del cliente para aplicarlo -- esto NO crea ningún ajuste contable en Odoo, solo lo marca como resuelto acá)`, "Sin orden abierta del cliente -- cerrado a favor de la empresa");
        if (motivo === null) return; // cancelado

        try {
            const res = await fetch('/api/conciliaciones/cerrar-pago-huerfano', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pago_id: pago_id, motivo: motivo || undefined })
            });
            const data = await res.json();
            if (res.ok) {
                alert("✅ Pago cerrado a favor de la empresa.");
                if (typeof loadCobranzaUnificado === "function") loadCobranzaUnificado();
                loadKPIs();
            } else {
                alert("❌ Error: " + (data.detail || "No se pudo cerrar el pago."));
            }
        } catch (err) {
            console.error("Error al cerrar pago huérfano:", err);
            alert("❌ Error de red.");
        }
    };

    window.aprobarSugerenciasSeleccionadas = async function() {
        const selectedChecks = Array.from(document.querySelectorAll(".check-sugerencia-item:checked"));
        if (selectedChecks.length === 0) {
            alert("Por favor selecciona al menos un pago con sugerencia para aprobar.");
            return;
        }

        const itemsToApprove = selectedChecks.map(c => {
            const idx = parseInt(c.dataset.idx);
            const item = currentSugerenciasList[idx];
            return {
                pago_id: item.pago_id,
                so_id: item.so_id,
                monto_aplicado: item.monto_sugerido
            };
        });

        const totalMonto = itemsToApprove.reduce((sum, i) => sum + i.monto_aplicado, 0);

        if (!confirm(`¿Desea aprobar masivamente ${itemsToApprove.length} vinculación(es) sugerida(s) por un total de $${totalMonto.toFixed(2)} USD?`)) return;

        try {
            const res = await fetch('/api/vincular-masivo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ items: itemsToApprove })
            });
            const data = await res.json();
            if (res.ok) {
                alert(`🎉 ${data.message}`);
                if (typeof loadCobranzaUnificado === "function") loadCobranzaUnificado();
                loadKPIs();
            } else {
                alert("❌ Error procesando lote: " + (data.detail || "Error desconocido."));
            }
        } catch (err) {
            console.error("Error en aprobación masiva:", err);
            alert("❌ Error de comunicación con el servidor.");
        }
    };

    // COBRANZA UNIFICADA -- reemplaza las 4 tablas históricas (Pagos
    // Pendientes por Asociar, Mapa de Conciliación, Pagos Conciliados y el
    // registro de Cobranza) con una sola fuente de datos (/api/cobranza/pagos)
    // y una sola tabla. Reusa TAL CUAL las acciones ya existentes
    // (aprobarSugerenciaIndividual, abrirModalVincularManual, cerrarPagoHuerfano,
    // aprobarSugerenciasSeleccionadas, generarReciboSeleccionados/toggleAllCobranza)
    // -- currentSugerenciasList se repuebla acá con los pendientes de la
    // tabla unificada (con alias de campos para que esas funciones, que
    // esperan el esquema viejo de /api/conciliaciones/sugerencias, sigan
    // funcionando sin tocarlas.
    let cobranzaUnificadaData = [];

    async function loadCobranzaUnificado() {
        const tbody = document.getElementById("cobranza-table-body");
        const vSelect = document.getElementById("cobranza-vendedor-filter");
        if (!tbody) return;

        tbody.innerHTML = '<tr><td colspan="12" class="table-empty">Cargando pagos (pendientes, vinculados y conciliados en Odoo)...</td></tr>';
        try {
            const res = await fetch("/api/cobranza/pagos");
            if (!res.ok) throw new Error("Error al obtener los pagos");
            cobranzaUnificadaData = await res.json();

            if (vSelect) {
                const curVal = vSelect.value;
                const vendedores = [...new Set(cobranzaUnificadaData.map(i => i.vendedor || "Sin Vendedor"))].sort();
                vSelect.innerHTML = '<option value="*">Todos los Vendedores</option>';
                vendedores.forEach(v => {
                    const opt = document.createElement("option");
                    opt.value = v;
                    opt.textContent = v;
                    vSelect.appendChild(opt);
                });
                vSelect.value = curVal || "*";
            }

            ["cobranza-vendedor-filter", "cobranza-estado-filter", "cobranza-moneda-filter",
             "cobranza-solo-duplicados", "cobranza-solo-alertas", "cobranza-sort"].forEach(id => {
                const el = document.getElementById(id);
                if (el && !el.dataset.wired) {
                    el.addEventListener("change", renderCobranzaUnificado);
                    el.dataset.wired = "1";
                }
            });
            const searchEl = document.getElementById("cobranza-search");
            if (searchEl && !searchEl.dataset.wired) {
                searchEl.addEventListener("input", renderCobranzaUnificado);
                searchEl.dataset.wired = "1";
            }

            renderCobranzaUnificado();
            renderCobranzaCerrados();
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="12" class="table-empty danger">Error cargando pagos: ${err.message}</td></tr>`;
            console.error(err);
        }
    }
    window.loadCobranzaUnificado = loadCobranzaUnificado;

    function renderCobranzaUnificado() {
        const tbody = document.getElementById("cobranza-table-body");
        const countBadge = document.getElementById("badge-cobranza-count");
        if (!tbody) return;

        const selVend = (document.getElementById("cobranza-vendedor-filter") || {}).value || "*";
        const selEstado = (document.getElementById("cobranza-estado-filter") || {}).value || "*";
        const selMoneda = (document.getElementById("cobranza-moneda-filter") || {}).value || "*";
        const soloDup = (document.getElementById("cobranza-solo-duplicados") || {}).checked;
        const soloAlertas = (document.getElementById("cobranza-solo-alertas") || {}).checked;
        const search = ((document.getElementById("cobranza-search") || {}).value || "").trim().toLowerCase();
        const sortBy = (document.getElementById("cobranza-sort") || {}).value || "pago_fecha_asc";

        // "Cerrados a favor de la empresa" tienen su propia bandeja (ver
        // sección debajo) -- no se muestran en la tabla principal.
        let filtered = cobranzaUnificadaData.filter(i => i.estado !== "cerrado_empresa");
        if (selVend !== "*") filtered = filtered.filter(i => (i.vendedor || "Sin Vendedor") === selVend);
        if (selEstado !== "*") filtered = filtered.filter(i => i.estado === selEstado);
        if (selMoneda !== "*") filtered = filtered.filter(i => i.moneda_pago === selMoneda);
        if (soloDup) filtered = filtered.filter(i => i.posible_duplicado);
        if (soloAlertas) filtered = filtered.filter(i => i.vendedor_mismatch || i.reasignado_por_odoo);
        if (search) {
            filtered = filtered.filter(i => [i.pago_id, i.numero_pago_odoo, i.cliente_nombre, i.so_id]
                .some(v => v && String(v).toLowerCase().includes(search)));
        }

        const sorters = {
            pago_fecha_asc: (a, b) => (a.pago_fecha || "").localeCompare(b.pago_fecha || ""),
            pago_fecha_desc: (a, b) => (b.pago_fecha || "").localeCompare(a.pago_fecha || ""),
            monto_desc: (a, b) => (b.monto_pago_bcv_usd || b.monto_pago_original || 0) - (a.monto_pago_bcv_usd || a.monto_pago_original || 0),
            cliente_asc: (a, b) => (a.cliente_nombre || "").localeCompare(b.cliente_nombre || ""),
        };
        filtered = [...filtered].sort(sorters[sortBy] || sorters.pago_fecha_asc);

        if (countBadge) countBadge.textContent = `${filtered.length} Pagos`;

        if (filtered.length === 0) {
            tbody.innerHTML = '<tr><td colspan="12" class="table-empty">No hay pagos para el filtro seleccionado.</td></tr>';
            currentSugerenciasList = [];
            return;
        }

        // currentSugerenciasList repuebla el array que ya consumen
        // aprobarSugerenciaIndividual/abrirModalVincularManual/aprobarSugerenciasSeleccionadas
        // -- alias de campos hacia el esquema viejo que esas funciones esperan.
        currentSugerenciasList = filtered
            .filter(i => i.estado === "pendiente")
            .map(i => ({
                ...i,
                saldo_pago: i.monto_por_aplicar,
                saldo_pago_original: i.monto_pago_original,
                monto_pago: i.monto_pago_bcv_usd,
                monto_pago_binance: i.monto_pago_binance_usd,
            }));
        const idxByPagoSug = {};
        currentSugerenciasList.forEach((it, idx) => { idxByPagoSug[it.sugerencia_id] = idx; });

        const fmt = (v) => v == null ? "-" : new Intl.NumberFormat("es-US", { style: "currency", currency: "USD" }).format(v);
        const pagoCounts = {};
        filtered.forEach(i => { pagoCounts[i.pago_id] = (pagoCounts[i.pago_id] || 0) + 1; });
        const pagoSeen = {};

        const estadoBadge = {
            pendiente: '<span class="badge" style="background:#fef3c7; color:#92400e; font-weight:700;">⏳ Pendiente</span>',
            vinculado_local: '<span class="badge" style="background:#dbeafe; color:#1e40af; font-weight:700;">🔗 Vinculado</span>',
            conciliado_odoo: '<span class="badge" style="background:#dcfce7; color:#166534; font-weight:700;">✓ Conciliado (Odoo)</span>',
        };

        tbody.innerHTML = filtered.map(item => {
            const total = pagoCounts[item.pago_id];
            pagoSeen[item.pago_id] = (pagoSeen[item.pago_id] || 0) + 1;
            const pagoCell = `<strong>${item.pago_id}</strong>`
                + (item.numero_pago_odoo ? `<br><small style="color:#64748b;">${item.numero_pago_odoo}</small>` : '')
                + (total > 1 ? `<br><small style="color:#64748b;" title="Este pago cubre ${total} órdenes -- no está duplicado">reparto ${pagoSeen[item.pago_id]}/${total}</small>` : '');

            const montoCell = item.moneda_pago === "VES"
                ? `Bs. ${Number(item.monto_pago_original).toLocaleString('es-VE', { minimumFractionDigits: 2 })}`
                : fmt(item.monto_pago_original);
            const tasasCell = `
                <span style="font-size:0.72rem; color:#2563eb; display:block;">BCV: ${fmt(item.monto_pago_bcv_usd)}</span>
                <span style="font-size:0.72rem; color:#d97706; display:block;">Binance: ${fmt(item.monto_pago_binance_usd)}</span>
                <span style="font-size:0.72rem; color:#7c3aed; display:block;">EUR: ${fmt(item.monto_pago_eur)}</span>`;

            const ordenCell = item.so_id
                ? `<span class="badge blue">${item.so_id}</span>` + (item.factura_id ? `<br><small>${item.factura_id}</small>` : '')
                : `<span style="color:#94a3b8; font-size:0.8rem;">Sin orden</span>`;

            const vendedorCell = `<small>${item.vendedor || 'Sin Vendedor'}</small>`
                + (item.vendedor_mismatch ? `<br><span title="El cliente cambió de vendedor -- la orden quedó con uno distinto al vigente" style="font-size:0.68rem; color:#b91c1c; font-weight:700;">⚠️ vendedor distinto en la orden</span>` : '');

            const alertas = [];
            if (item.posible_duplicado) alertas.push(`<span title="Mismo cliente/monto/moneda/método/fecha que: ${(item.duplicado_de || []).join(', ')}" style="font-size:0.68rem; color:#b91c1c; font-weight:700;">⚠️ Posible duplicado</span>`);
            if (item.reasignado_por_odoo) alertas.push(`<span title="${item.reasignado_detalle || ''}" style="font-size:0.68rem; color:#0369a1; font-weight:700;">🔄 Reasignado por Odoo</span>`);
            const alertasCell = alertas.length ? alertas.join('<br>') : '<span style="color:#94a3b8;">-</span>';

            const reciboCell = item.recibido
                ? `<span class="semaphore green" title="Entregado a Administración">✓ Recibido</span>`
                : `<span class="semaphore yellow">⏳ Pendiente</span>`;

            const tieneSugerencia = !!item.so_id;
            const sugIdx = idxByPagoSug[item.sugerencia_id];
            let accionesExtra = '';
            let checkboxCell = '<td></td>';
            if (item.estado === "pendiente") {
                if (tieneSugerencia && !item.posible_duplicado && sugIdx !== undefined) {
                    accionesExtra = `<button class="btn btn-sm btn-primary" onclick="aprobarSugerenciaIndividual('${item.pago_id}', '${item.so_id}', ${item.monto_sugerido})" style="padding:3px 8px; font-size:0.75rem;">✓ Vincular</button>
                        <button class="btn btn-sm btn-secondary" onclick="abrirModalVincularManual(${sugIdx})" style="padding:3px 8px; font-size:0.72rem;">✏️ Otra orden</button>`;
                    checkboxCell = `<td style="text-align:center;"><input type="checkbox" class="check-sugerencia-item" data-idx="${sugIdx}" checked></td>`;
                } else if (sugIdx !== undefined) {
                    accionesExtra = `<button class="btn btn-sm btn-secondary" onclick="abrirModalVincularManual(${sugIdx})" style="padding:3px 8px; font-size:0.75rem;">🔗 Vincular manualmente</button>`
                        + (!tieneSugerencia ? `<button class="btn btn-sm btn-secondary" onclick="cerrarPagoHuerfano('${item.pago_id}')" style="padding:3px 8px; font-size:0.7rem; color:#92400e;">💰 Cerrar a favor de la empresa</button>` : '');
                    checkboxCell = `<td></td>`;
                }
            } else if (item.puede_marcar_recibido) {
                checkboxCell = `<td style="text-align:center;"><input type="checkbox" class="check-cobranza-item" value="${item.pago_id}" data-vendedor="${item.vendedor || ''}"></td>`;
            }

            return `
                <tr>
                    ${checkboxCell}
                    <td>${pagoCell}</td>
                    <td>${item.pago_fecha || '-'}</td>
                    <td>${item.cliente_nombre || '-'}</td>
                    <td>${vendedorCell}</td>
                    <td>${montoCell}</td>
                    <td>${tasasCell}</td>
                    <td>${ordenCell}</td>
                    <td>${estadoBadge[item.estado] || item.estado}</td>
                    <td>${alertasCell}</td>
                    <td>${reciboCell}</td>
                    <td>
                        <div style="display:flex; flex-direction:column; gap:3px;">
                            <button class="btn btn-sm btn-secondary" onclick="abrirModalDetallePago('${item.pago_id}')" style="padding:3px 8px; font-size:0.75rem;">👁️ Detalle</button>
                            ${accionesExtra}
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    }
    window.renderCobranzaUnificado = renderCobranzaUnificado;

    // Bandeja "Cerrados a Favor de la Empresa" -- pagos huérfanos marcados
    // como resueltos (ver cerrarPagoHuerfano). Antes eran invisibles fuera
    // del filtro de exclusión de "pendientes"; ahora tienen su propia vista,
    // sourced del mismo payload unificado (sin endpoint nuevo).
    function renderCobranzaCerrados() {
        const tbody = document.getElementById("cobranza-cerrados-table-body");
        const countBadge = document.getElementById("badge-cobranza-cerrados-count");
        if (!tbody) return;

        const cerrados = cobranzaUnificadaData.filter(i => i.estado === "cerrado_empresa");
        if (countBadge) countBadge.textContent = `${cerrados.length} Pagos`;

        if (cerrados.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="table-empty">No hay pagos cerrados a favor de la empresa.</td></tr>';
            return;
        }

        const fmt = (v) => v == null ? "-" : new Intl.NumberFormat("es-US", { style: "currency", currency: "USD" }).format(v);
        tbody.innerHTML = cerrados.map(item => `
            <tr>
                <td><strong>${item.pago_id}</strong></td>
                <td>${item.cliente_nombre || '-'}</td>
                <td>${item.moneda_pago === 'VES'
                    ? 'Bs. ' + Number(item.monto_pago_original).toLocaleString('es-VE', { minimumFractionDigits: 2 })
                    : fmt(item.monto_pago_original)}</td>
                <td>${item.cerrado_motivo || '-'}</td>
                <td>${item.cerrado_por || item.confirmado_por || '-'}</td>
                <td>${(item.cerrado_timestamp || '').slice(0, 19).replace('T', ' ') || '-'}</td>
            </tr>
        `).join('');
    }
    window.renderCobranzaCerrados = renderCobranzaCerrados;

    window.toggleAllCobranza = function(el) {
        document.querySelectorAll(".check-cobranza-item, .check-sugerencia-item").forEach(cb => cb.checked = el.checked);
    };

    window.abrirModalDetallePago = function(pago_id) {
        const modal = document.getElementById("modal-detalle-pago");
        const body = document.getElementById("modal-detalle-pago-body");
        if (!modal || !body) return;
        const filas = cobranzaUnificadaData.filter(i => i.pago_id === pago_id);
        if (filas.length === 0) return;
        const fmt = (v) => v == null ? "-" : new Intl.NumberFormat("es-US", { style: "currency", currency: "USD" }).format(v);
        const base = filas[0];

        const campo = (label, valor) => `<div style="margin-bottom:0.5rem;"><span style="font-size:0.75rem; color:#64748b; display:block;">${label}</span><strong>${valor ?? '-'}</strong></div>`;

        let html = `<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px,1fr)); gap:0.5rem 1rem; margin-bottom:1rem;">
            ${campo('Pago ID', base.pago_id)}
            ${campo('N° Pago Odoo', base.numero_pago_odoo)}
            ${campo('Fecha', base.pago_fecha)}
            ${campo('Cliente', base.cliente_nombre)}
            ${campo('Vendedor', base.vendedor + (base.vendedor_mismatch ? ' ⚠️ (distinto al de la orden)' : ''))}
            ${campo('Método de Pago', base.metodo_pago)}
            ${campo('Monto Original', (base.moneda_pago === 'VES' ? 'Bs. ' + Number(base.monto_pago_original).toLocaleString('es-VE', {minimumFractionDigits:2}) : fmt(base.monto_pago_original)))}
            ${campo('Tasa BCV', base.tasa_bcv ? base.tasa_bcv.toFixed(4) : '-')}
            ${campo('Tasa Binance', base.tasa_binance ? base.tasa_binance.toFixed(4) : '-')}
            ${campo('Tasa BCV-EUR', base.tasa_bcv_eur ? base.tasa_bcv_eur.toFixed(4) : '-')}
            ${campo('Equiv. BCV', fmt(base.monto_pago_bcv_usd))}
            ${campo('Equiv. Binance', fmt(base.monto_pago_binance_usd))}
            ${campo('Equiv. EUR', fmt(base.monto_pago_eur))}
            ${campo('Estado', base.estado)}
            ${campo('Origen', base.origen)}
            ${campo('Confirmado Por', base.confirmado_por)}
            ${campo('Recibido', base.recibido ? `Sí (${base.numero_recibido || ''}, ${base.fecha_recibido || ''}, ${base.recibido_por || ''})` : 'No')}
        </div>`;

        if (base.reasignado_por_odoo) {
            html += `<div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:8px; padding:0.75rem; margin-bottom:1rem; font-size:0.85rem; color:#1e40af;">
                🔄 <strong>Odoo reasignó este pago.</strong> ${base.reasignado_detalle || ''}
            </div>`;
        }
        if (base.posible_duplicado) {
            html += `<div style="background:#fef2f2; border:1px solid #fecaca; border-radius:8px; padding:0.75rem; margin-bottom:1rem; font-size:0.85rem; color:#b91c1c;">
                ⚠️ <strong>Posible duplicado</strong> de: ${(base.duplicado_de || []).join(', ')}
            </div>`;
        }

        if (base.puede_editar_tasas && base.vinc_id) {
            html += `<div style="background:#f8fafc; border:1px dashed #cbd5e1; border-radius:8px; padding:0.75rem; margin-bottom:1rem;">
                <label style="font-weight:700; font-size:0.85rem; display:block; margin-bottom:0.5rem;">Editar Tasas Aplicadas</label>
                <div style="display:flex; gap:0.5rem; align-items:center; margin-bottom:0.5rem; flex-wrap:wrap;">
                    <input type="number" step="0.0001" class="input-tasa-binance" data-vinc="${base.vinc_id}" value="${base.tasa_binance ?? ''}" placeholder="Tasa Binance" style="width:120px; padding:4px 6px; font-size:0.8rem;">
                    <button class="btn btn-sm btn-secondary" onclick="guardarTasaBinance('${base.vinc_id}')" style="padding:4px 10px; font-size:0.75rem;">Guardar Binance</button>
                </div>
                <div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
                    <select class="select-bcv-variante" data-vinc="${base.vinc_id}" style="padding:4px 6px; font-size:0.8rem;">
                        <option value="USD">BCV USD</option>
                        <option value="EUR">BCV EUR</option>
                    </select>
                    <button class="btn btn-sm btn-secondary" onclick="guardarTipoTasaBcv('${base.vinc_id}')" style="padding:4px 10px; font-size:0.75rem;">Guardar Variante BCV</button>
                </div>
            </div>`;
        }

        html += `<h3 style="margin:1rem 0 0.5rem;">Reparto / Órdenes y Facturas</h3>
            <table class="cxc-table"><thead><tr>
                <th>Orden</th><th>Factura</th><th>Monto Aplicado</th><th>Por Aplicar</th>
                <th>Saldo Orden (CxC)</th><th>Saldo Factura (Odoo)</th>
            </tr></thead><tbody>`;
        filas.forEach(f => {
            html += `<tr>
                <td>${f.so_id || '-'}</td>
                <td>${f.factura_id || '-'}</td>
                <td>${fmt(f.monto_aplicado)}</td>
                <td>${fmt(f.monto_por_aplicar)}</td>
                <td>${fmt(f.so_saldo_pendiente)}</td>
                <td>${fmt(f.factura_saldo_odoo)}</td>
            </tr>`;
        });
        html += `</tbody></table>`;

        body.innerHTML = html;
        const bcvVarianteSelect = body.querySelector(`.select-bcv-variante[data-vinc="${base.vinc_id}"]`);
        if (bcvVarianteSelect && base.bcv_variante) bcvVarianteSelect.value = base.bcv_variante;
        modal.style.display = "flex";
    };

    window.cerrarModalDetallePago = function() {
        const modal = document.getElementById("modal-detalle-pago");
        if (modal) modal.style.display = "none";
    };

    // Initial Load for Dashboard
    loadTasasPromedios();
    
    // --- Load Auditoría Data & Invoice Residual Discrepancies ---
    async function loadAuditoria() {
        const bodyDisc = document.getElementById("discrepancias-table-body");
        const bodyFacturas = document.getElementById("discrepancias-facturas-table-body");
        const bodyAceptadas = document.getElementById("anomalias-aceptadas-table-body");
        const bodyConformes = document.getElementById("conformes-table-body");

        const elKpiConformes = document.getElementById("audit-kpi-conformes");
        const elKpiDiscrepancias = document.getElementById("audit-kpi-discrepancias");
        const elKpiAceptadas = document.getElementById("audit-kpi-aceptadas");
        const elKpiMontoDiscrepancia = document.getElementById("audit-kpi-monto-discrepancia");

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
            if (bodyDisc) bodyDisc.innerHTML = '<tr><td colspan="11" class="table-empty">Cargando auditoría de discrepancias...</td></tr>';
            if (bodyFacturas) bodyFacturas.innerHTML = '<tr><td colspan="9" class="table-empty">Cargando discrepancias con facturas Odoo...</td></tr>';

            const res = await fetch("/api/auditoria");
            if (res.ok) {
                const data = await res.json();
                
                const conformes = data.operaciones_conformes || [];
                const discrepancias = data.discrepancias || [];
                const discFacturas = data.discrepancias_facturas_odoo || [];
                const aceptadas = data.anomalias_aceptadas || [];

                if (elKpiConformes) elKpiConformes.textContent = conformes.length;
                if (elKpiDiscrepancias) elKpiDiscrepancias.textContent = discrepancias.length + discFacturas.length;
                if (elKpiAceptadas) elKpiAceptadas.textContent = aceptadas.length;

                const montoTotDisc = discrepancias.reduce((acc, x) => acc + (x.diferencia_monto || 0), 0) + discFacturas.reduce((acc, x) => acc + (x.diferencia || 0), 0);
                if (elKpiMontoDiscrepancia) elKpiMontoDiscrepancia.textContent = fmt(montoTotDisc);

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

                // Render Operaciones Conformes
                if (bodyConformes) {
                    if (conformes.length === 0) {
                        bodyConformes.innerHTML = '<tr><td colspan="8" class="table-empty">No hay operaciones conformes cargadas.</td></tr>';
                    } else {
                        bodyConformes.innerHTML = conformes.slice(0, 100).map(c => `
                            <tr>
                                <td><strong>${escapeHtml(c.so_id)}</strong></td>
                                <td>${escapeHtml(c.factura_id)}</td>
                                <td>${escapeHtml(c.cliente_nombre)}</td>
                                <td><small>${c.fecha ? c.fecha.substring(0, 10) : ''}</small></td>
                                <td>${fmt(c.monto_original)}</td>
                                <td>${fmt(c.descuentos_aplicados)}</td>
                                <td><strong style="color:#059669;">${fmt(c.monto_neto_conciliado)}</strong></td>
                                <td><span class="state-badge cierre" style="background:#dcfce7; color:#15803d; font-weight:600;">${escapeHtml(c.estado)}</span></td>
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
