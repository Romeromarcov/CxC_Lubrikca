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
    const bandejaDescuentosPendientesTableBody = document.getElementById("bandeja-descuentos-pendientes-table-body");

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
        card.style.width = `${px}px`;
        card.style.height = `${Math.round(px * 0.72)}px`;
        // Tipografía también escala un poco con el tamaño, para que las
        // tarjetas chicas no queden con texto más grande que la propia tarjeta.
        const scale = (0.8 + 0.4 * (px - PRIORIDAD_CARD_MIN_PX) / (PRIORIDAD_CARD_MAX_PX - PRIORIDAD_CARD_MIN_PX)).toFixed(2);
        card.style.fontSize = `${scale}rem`;
        card.innerHTML = `
            <div class="prioridad-cliente" title="${c.cliente_nombre || c.cliente_id}">${c.cliente_nombre || c.cliente_id}</div>
            <div class="prioridad-monto">${fmt(c.saldo_priorizacion)}</div>
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
                        ? (d.factura_id ? `${d.so_id} / ${d.factura_id}` : d.so_id)
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
            if (bandejaDescuentosPendientesTableBody) bandejaDescuentosPendientesTableBody.innerHTML = '<tr><td colspan="7" class="table-empty">Cargando descuentos pendientes por aprobar...</td></tr>';

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
                const tray5 = data.pendientes_por_cerrar || [];
                const trayDescPend = data.descuentos_pendientes_aprobar || [];

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
                            // cxc_confirmado === false: llegó aquí vía "en proceso
                            // de pago" (Vinculación PENDIENTE, sin reconciliar en
                            // Odoo todavía) -- antes de facturar es la única señal
                            // posible, pero se distingue de un pago confirmado.
                            const enProcesoBadge = item.cxc_confirmado === false
                                ? ` <span class="state-badge" style="background:#dbeafe;color:#1d4ed8" title="${item.cxc_routing_motivo || ''}">⏳ En proceso de pago</span>`
                                : '';

                            row.innerHTML = `
                                <td><strong>${item.so_id}</strong></td>
                                <td>${item.cliente_nombre || item.so_id}${enProcesoBadge}</td>
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
                                <td><span class="state-badge abiertas" title="La emisión de N/C se hace directamente en Odoo; esta bandeja es de seguimiento, no de acción">Pendiente en Odoo</span></td>
                            `;
                            bandeja2TableBody.appendChild(row);
                        });
                    }
                }

                // Render Tray "Descuentos Pendientes por Aprobar" (Fase 3, auditoría del ciclo CxC)
                if (bandejaDescuentosPendientesTableBody) {
                    if (trayDescPend.length === 0) {
                        bandejaDescuentosPendientesTableBody.innerHTML = '<tr><td colspan="7" class="table-empty">No hay descuentos pendientes por aprobar.</td></tr>';
                    } else {
                        bandejaDescuentosPendientesTableBody.innerHTML = "";
                        trayDescPend.forEach(item => {
                            const row = document.createElement("tr");
                            const detalle = (item.descuentos_detalle || [])
                                .map(d => `${d.descripcion}: ${fmt(d.monto)}`)
                                .join("; ");
                            row.innerHTML = `
                                <td><strong>${item.so_id}</strong></td>
                                <td>${item.cliente_nombre || item.so_id}</td>
                                <td><span class="state-badge">${item.factura_id || 'Odoo'}</span></td>
                                <td><strong style="color:#059669">${fmt(item.monto_pagado || 0)}</strong></td>
                                <td><strong style="color:#d97706">${fmt(item.descuento_pendiente_aplicar || 0)} (${(item.descuento_pendiente_pct || 0).toFixed(1)}%)</strong></td>
                                <td>${item.incluye_diferencial_cambiario ? '<span class="state-badge cierre" style="background:#e0f2fe;color:#0369a1">Sí</span>' : 'No'}</td>
                                <td style="font-size:0.8rem">${detalle || '-'}</td>
                            `;
                            bandejaDescuentosPendientesTableBody.appendChild(row);
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

                // Render Tray 5 (Pendientes por Cerrar -- movida desde Reporte de Saldos)
                if (bandejaPendientesCerrarTableBody) {
                    if (tray5.length === 0) {
                        bandejaPendientesCerrarTableBody.innerHTML = '<tr><td colspan="7" class="table-empty">No hay órdenes/facturas pendientes por cerrar.</td></tr>';
                    } else {
                        bandejaPendientesCerrarTableBody.innerHTML = "";
                        tray5.forEach(item => {
                            const row = document.createElement("tr");
                            row.innerHTML = `
                                <td><strong>${item.so_id}</strong></td>
                                <td>${item.cliente_nombre || item.so_id}</td>
                                <td>${item.vendedor || 'Sin Vendedor'}</td>
                                <td>${item.factura_id || 'N/A'}</td>
                                <td>${fmt(item.saldo_con_descuento_bcv || 0)}</td>
                                <td>${fmt(item.saldo_con_descuento_lista_usd || 0)}</td>
                                <td>${item.saldo_factura_odoo != null ? fmt(item.saldo_factura_odoo) : '-'}</td>
                            `;
                            bandejaPendientesCerrarTableBody.appendChild(row);
                        });
                    }
                }

                // Badges de conteo en las bandejas colapsables (3 y auditoría de precios)
                const badge3 = document.getElementById("bandeja3-count-badge");
                if (badge3) badge3.textContent = String(tray3.length);
                const badgeAudPrecios = document.getElementById("bandeja-auditoria-precios-count-badge");
                if (badgeAudPrecios) badgeAudPrecios.textContent = String(tray4.length);
                const badgeEnProceso = document.getElementById("bandeja-en-proceso-de-pago-count-badge");
                if (badgeEnProceso) badgeEnProceso.textContent = String(trayEnProceso.length);
            }
        } catch (err) {
            console.error("Error loading bandeja:", err);
            if (bandeja1TableBody) bandeja1TableBody.innerHTML = '<tr><td colspan="9" class="table-empty">Error al cargar bandeja 1.</td></tr>';
            if (bandeja2TableBody) bandeja2TableBody.innerHTML = '<tr><td colspan="8" class="table-empty">Error al cargar bandeja 2.</td></tr>';
            if (bandeja3TableBody) bandeja3TableBody.innerHTML = '<tr><td colspan="7" class="table-empty">Error al cargar bandeja 3.</td></tr>';
            if (bandejaAuditoriaPreciosTableBody) bandejaAuditoriaPreciosTableBody.innerHTML = '<tr><td colspan="9" class="table-empty">Error al cargar auditoría de precios.</td></tr>';
            if (bandejaPendientesCerrarTableBody) bandejaPendientesCerrarTableBody.innerHTML = '<tr><td colspan="7" class="table-empty">Error al cargar pendientes por cerrar.</td></tr>';
            if (bandejaDescuentosPendientesTableBody) bandejaDescuentosPendientesTableBody.innerHTML = '<tr><td colspan="7" class="table-empty">Error al cargar descuentos pendientes por aprobar.</td></tr>';
        }
    }

    window.guardarTasaBinance = async function(id, esVinculado) {
        const input = document.querySelector(`.input-tasa-binance[data-vinc="${id}"]`);
        if (!input) return;
        const tasa = parseFloat(input.value);
        if (!tasa || tasa <= 0) {
            alert("Ingresa una tasa Binance válida.");
            return;
        }
        // Con Vinculación real (pago ya vinculado a una orden): corrige la
        // Vinculación. Sin ella (pago aún pendiente, "sugerencia sin
        // confirmar"): guarda la corrección por pago_id -- ver POST
        // /api/pago/{pago_id}/tasa-binance.
        const endpoint = esVinculado
            ? `/api/vinculacion/${id}/tasa-binance`
            : `/api/pago/${id}/tasa-binance`;
        try {
            const res = await fetch(endpoint, {
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

    // --- Tab 2: Accounts Receivable Report ---
    // Reporte de Saldos, agosto 2026: se eliminaron las tablas "Reporte
    // General" (por orden) y "Mora Crítica" -- reemplazadas por la tabla
    // por cliente (ver loadReporteCxcCliente) + la grilla de priorización
    // por color. loadReporte() ahora solo alimenta las tarjetas KPI del
    // Dashboard (que siguen viniendo de /api/reporte-saldos).
    async function loadReporte() {
        try {
            const res = await fetch("/api/reporte-saldos?refresh=true&t=" + Date.now(), { cache: "no-store" });
            if (res.ok) {
                const data = await res.json();
                const kpis = data.kpis || {};
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

                // Attach Click Handlers to Interactive KPI Cards -- las
                // tarjetas viven en el Dashboard; un clic navega al Reporte
                // de Saldos y filtra la tabla por cliente por esa antigüedad.
                document.querySelectorAll(".interactive-kpi").forEach(card => {
                    if (!card.dataset.listenerAttached) {
                        card.addEventListener("click", () => {
                            const targetVal = card.dataset.antiguedad;
                            const selectEl = document.getElementById("reporte-cliente-antiguedad-filter");
                            if (selectEl) {
                                selectEl.value = (selectEl.value === targetVal) ? "*" : targetVal;
                                if (typeof applyReporteClienteFilters === "function") applyReporteClienteFilters();
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
            }
        } catch (err) {
            console.error("Error cargando KPIs de reporte de saldos:", err);
        }
    }

    // ── Página Ventas: teórico (bruta/neta, con y sin impuestos) vs real ──
    let ventasData = [];

    async function loadVentas() {
        const tbody = document.getElementById("ventas-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="46" class="table-empty">Cargando reporte de ventas...</td></tr>';
            const res = await fetch("/api/ventas?t=" + Date.now(), { cache: "no-store" });
            if (!res.ok) {
                tbody.innerHTML = '<tr><td colspan="46" class="table-empty">Error al cargar el reporte de ventas.</td></tr>';
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
            tbody.innerHTML = '<tr><td colspan="46" class="table-empty">Error de red al cargar el reporte de ventas.</td></tr>';
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
            tbody.innerHTML = '<tr><td colspan="46" class="table-empty">No hay órdenes que coincidan con los filtros seleccionados.</td></tr>';
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
            if (item.revisar_motivo) {
                row.style.background = "#fffbeb";
            }
            // falta_nc_por_devolucion: acción pendiente concreta (crear la
            // NC en Odoo), no solo una señal de "revisar" -- badge propio,
            // más urgente, para que no se confunda con el resto de motivos.
            const revisarCell = item.falta_nc_por_devolucion
                ? `<span class="state-badge" style="background:#fee2e2;color:#991b1b;font-weight:700;cursor:help;" title="${item.revisar_motivo}">📄 Falta NC</span>`
                : (item.revisar_motivo
                    ? `<span class="state-badge" style="background:#fef3c7;color:#92400e;font-weight:700;cursor:help;" title="${item.revisar_motivo}">🔍 Revisar</span>`
                    : '<span class="state-badge" style="background:#f1f5f9;color:#64748b;">—</span>');

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
                    sin_datos: ['#e0e7ff', '#4338ca', '? Sin datos teóricos'],
                };
                const [bg, fg, label] = map[estado] || ['#f1f5f9', '#64748b', estado || '—'];
                return `<span class="state-badge" style="background:${bg};color:${fg};font-weight:600;">${label}</span>`;
            };

            row.innerHTML = `
                <td><strong>${item.so_id}</strong></td>
                <td><small>${item.fecha}</small></td>
                <td><small>${item.fecha_entrega ?? '—'}</small></td>
                <td>${item.cliente_nombre}</td>
                <td><small>${item.vendedor}</small></td>
                <td><small title="${item.lista_nacimiento ?? ''}">${item.lista_nacimiento_label ?? '—'}</small></td>
                <td><small title="${item.lista_aplicada ?? ''}">${item.lista_aplicada_label ?? '—'}</small></td>
                <td style="text-align:right">${item.dias_credito ?? 0}</td>
                <td style="text-align:right" title="Vence: ${item.fecha_vencimiento ?? '—'}">${
                    (item.dias_vencido || 0) > 0
                        ? `<strong style="color:#dc2626">${item.dias_vencido} d</strong>`
                        : '<span style="color:#059669">Vigente</span>'
                }</td>
                <td>${naVal(item.ves_bruta_teorica)}</td>
                <td>${naVal(item.ves_bruta_teorica_iva)}</td>
                <td>${descMontoPct(item.descuento_teorico_ves, item.descuento_teorico_ves_pct)}</td>
                <td><strong style="color:#2563eb;">${naVal(item.ves_neta_teorica)}</strong></td>
                <td><strong style="color:#2563eb;">${naVal(item.ves_neta_teorica_iva)}</strong></td>
                <td>${fmt(item.monto_pagado_bcv)}</td>
                <td>${estatusPagoBadge(item.estatus_pago_teorico_ves)}</td>
                <td>${naVal(item.usd_bruta_teorica)}</td>
                <td>${naVal(item.usd_bruta_teorica_iva)}</td>
                <td>${descMontoPct(item.descuento_teorico_usd, item.descuento_teorico_usd_pct)}</td>
                <td><strong style="color:#2563eb;">${naVal(item.usd_neta_teorica)}</strong></td>
                <td><strong style="color:#2563eb;">${naVal(item.usd_neta_teorica_iva)}</strong></td>
                <td>${fmt(item.monto_pagado_usd)}</td>
                <td>${estatusPagoBadge(item.estatus_pago_teorico_usd)}</td>
                <td>${fmt(item.venta_bruta_real)}</td>
                <td>${descMontoPct(item.descuento_aplicado_orden, item.descuento_aplicado_orden_pct)}</td>
                <td><strong>${fmt(item.venta_neta_real)}</strong></td>
                <td>${descMontoPct(item.descuento_pendiente_aplicar, item.descuento_pendiente_aplicar_pct)}</td>
                <td title="${item.orden_real_subtotal_teoricos_bloqueado ? 'Bloqueado: sobre-descuento sin revisar en Auditoría -- no se resta el teórico' : ''}"><strong style="color:${item.orden_real_subtotal_teoricos_bloqueado ? '#dc2626' : '#7c3aed'};">${fmt(item.orden_real_subtotal_teoricos)}${item.orden_real_subtotal_teoricos_bloqueado ? ' 🔒' : ''}</strong></td>
                <td>${estatusPagoBadge(item.estatus_pago_real_orden)}</td>
                <td>${fmt(item.total_facturado_antes_impuestos)}</td>
                <td>${descMontoPct(item.descuento_aplicado_factura, item.descuento_aplicado_factura_pct)}</td>
                <td>${fmt(item.total_facturado_con_impuestos)}</td>
                <td>${fmt(item.total_nc_aplicada)}</td>
                <td>${fmt(item.total_nd_aplicada)}</td>
                <td><strong>${fmt(item.total_facturado_neto)}</strong></td>
                <td>${fmt(item.monto_pagado_factura_odoo)}</td>
                <td>${estatusPagoBadge(item.estatus_pago_real_factura)}</td>
                <td><span style="color:${valColor(item.descuento_validacion_orden)};font-weight:600;">${valLabel(item.descuento_validacion_orden)}</span></td>
                <td><span style="color:${valColor(item.descuento_validacion_factura)};font-weight:600;">${valLabel(item.descuento_validacion_factura)}</span></td>
                <td title="${item.descuento_aplicado_sistema_motivo ?? ''}">${descMontoPct(item.descuento_aplicado_sistema, item.descuento_aplicado_sistema_pct)}</td>
                <td>${fmt(item.saldo_pendiente_cxc)}</td>
                <td><strong style="color:${difColor};">${fmt(item.diferencia)}</strong></td>
                <td>${alertaCell}</td>
                <td>${revisarCell}</td>
                <td><button class="btn-primary" style="padding:4px 8px;font-size:0.75rem" onclick="abrirModalDetalleOrden('${item.so_id}')">Ver Detalle</button></td>
                <td><button class="btn-primary" style="padding:4px 8px;font-size:0.75rem;background:#0369a1" onclick="abrirModalPagosOrden('${item.so_id}')">Ver Pagos</button></td>
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
        const esTeorico = modo === "teorico_ves" || modo === "teorico_usd";
        const listaLabel = bloque.lista_label ? `<p style="color:#64748b;font-size:0.8rem;margin:0 0 0.5rem 0;">Lista: ${bloque.lista_label}</p>` : '';
        const fmtLitros = (v) => new Intl.NumberFormat('es-US', { minimumFractionDigits: 1, maximumFractionDigits: 3 }).format(v || 0);

        let rows = bloque.lineas.map(l => `
            <tr>
                <td>${l.producto}</td>
                <td style="text-align:right">${l.cantidad}</td>
                <td style="text-align:right">${fmtLitros(l.litros_unitario)} L</td>
                <td style="text-align:right"><strong>${fmtLitros(l.litros_total)} L</strong></td>
                <td style="text-align:right">${fmt(l.precio_unitario)}</td>
                <td style="text-align:right">${fmt(l.subtotal_antes_descuento)}</td>
                ${tieneDescuento ? `<td style="text-align:right">${fmt(l.descuento_monto)} (${(l.descuento_pct || 0).toFixed(1)}%)</td>` : ''}
                <td style="text-align:right"><strong>${fmt(l.subtotal_despues_descuento)}</strong></td>
            </tr>
        `).join('');

        // Fase 6: el motor no distribuye sus descuentos por línea (los
        // calcula por grupo/orden) -- para los modos teóricos se muestra un
        // desglose de CONCEPTOS a nivel de orden (qué reglas aplicaron:
        // recompra/contado/volumen) en vez de una columna de descuento por
        // línea, que sería inventada.
        const conceptos = bloque.conceptos || [];
        const conceptosHtml = esTeorico
            ? `<div style="margin-top:1rem;">
                <h4 style="margin:0 0 0.5rem 0;font-size:0.9rem;">Conceptos de descuento aplicados</h4>
                ${conceptos.length === 0
                    ? '<p class="table-empty" style="margin:0;">Ningún concepto de descuento aplica para esta lista.</p>'
                    : `<ul style="margin:0;padding-left:1.2rem;font-size:0.85rem;">${
                        conceptos.map(c => `<li>${c.concepto}: <strong>${fmt(c.monto)}</strong></li>`).join('')
                    }</ul>`}
              </div>`
            : '';

        body.innerHTML = `
            ${listaLabel}
            <div style="overflow-x:auto;">
                <table class="cxc-table">
                    <thead>
                        <tr>
                            <th>Producto</th>
                            <th style="text-align:right">Cantidad</th>
                            <th style="text-align:right">Litros/Unid.</th>
                            <th style="text-align:right">Litros Línea</th>
                            <th style="text-align:right">Precio Unit.</th>
                            <th style="text-align:right">Subtotal antes Desc.</th>
                            ${tieneDescuento ? '<th style="text-align:right">Descuento</th>' : ''}
                            <th style="text-align:right">Subtotal después Desc.</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
            <div style="margin-top:0.75rem;text-align:right;font-size:0.9rem;">
                ${(tieneDescuento || esTeorico) ? `<div>Descuento total: <strong>${fmt(bloque.descuento_total)}</strong></div>` : ''}
                <div>Total litros: <strong>${fmtLitros(bloque.litros_total)} L</strong></div>
                <div>Subtotal: <strong style="font-size:1.05rem;">${fmt(bloque.subtotal)}</strong></div>
            </div>
            ${conceptosHtml}
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

    // ── Modal de pagos aplicados a una orden (Fase 6) ─────────────────────────
    async function abrirModalPagosOrden(soId) {
        const modal = document.getElementById("modal-pagos-orden");
        const titulo = document.getElementById("modal-pagos-orden-titulo");
        const body = document.getElementById("modal-pagos-orden-body");
        if (!modal) return;

        titulo.textContent = `Pagos de la Orden ${soId}`;
        body.innerHTML = '<p class="table-empty">Cargando pagos...</p>';
        modal.style.display = "flex";

        const fmt = (val) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(val || 0);
        const fmtTasa = (val) => val != null ? new Intl.NumberFormat('es-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 }).format(val) : '—';
        // monto_aplicado viene en la moneda ORIGINAL del abono para
        // Vinculaciones (VES o USD, según moneda_abono) y ya en USD
        // equivalente para pagos de fuente "odoo" ("USD (equiv.)",
        // reusando get_live_pagos_conciliados de Cobranza) -- NUNCA asumir
        // USD ciegamente (bug real: mostraba montos VES con el símbolo $,
        // ej. "$943,654.40" para un pago que en realidad era Bs.
        // 943.654,40).
        const fmtMonto = (val, moneda) => {
            const num = new Intl.NumberFormat('es-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(val || 0);
            return (moneda || '').toUpperCase().startsWith('USD') ? `$${num}` : `${num} ${moneda || ''}`;
        };
        const fuenteBadge = (f) => f === 'odoo'
            ? '<span class="state-badge" style="background:#e0f2fe;color:#0369a1;">Odoo directo</span>'
            : '<span class="state-badge" style="background:#f1f5f9;color:#64748b;">Vinculación</span>';

        try {
            const res = await fetch(`/api/ventas/${encodeURIComponent(soId)}/detalle`);
            if (!res.ok) {
                body.innerHTML = '<p class="table-empty">Error al cargar los pagos de la orden.</p>';
                return;
            }
            const data = await res.json();
            const pagos = data.pagos || [];
            const totales = data.pagos_totales || null;
            if (pagos.length === 0) {
                body.innerHTML = '<p class="table-empty">Esta orden no tiene pagos vinculados todavía.</p>';
                return;
            }
            const rows = pagos.map(p => `
                <tr>
                    <td>${fuenteBadge(p.fuente)}</td>
                    <td>${p.pago_id}</td>
                    <td><small>${(p.fecha || '').substring(0, 16).replace('T', ' ') || '—'}</small></td>
                    <td style="text-align:right">${fmtMonto(p.monto_original, p.moneda_original)}</td>
                    <td style="text-align:right"><strong>${fmtMonto(p.monto_aplicado, p.moneda_abono)}</strong></td>
                    <td>${p.tipo_tasa_abono || '—'}</td>
                    <td style="text-align:right">${fmtTasa(p.tasa_bcv_aplicada)}</td>
                    <td style="text-align:right">${fmtTasa(p.tasa_binance_aplicada)}</td>
                    <td style="text-align:right">${p.equiv_usd_bcv != null ? fmt(p.equiv_usd_bcv) : '—'}</td>
                    <td style="text-align:right">${p.equiv_usd_binance != null ? fmt(p.equiv_usd_binance) : '—'}</td>
                    <td><small>${p.confirmado_por || '—'}</small></td>
                    <td><small>${p.estado || '—'}</small></td>
                </tr>
            `).join('');
            const totalesRow = totales ? `
                <tr style="font-weight:600;background:#f8fafc;">
                    <td colspan="3">Totales${totales.monedas_originales_mixtas ? ' <small style="font-weight:400;color:#b91c1c;">(monedas originales mixtas, suma referencial)</small>' : ''}</td>
                    <td style="text-align:right">${new Intl.NumberFormat('es-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(totales.monto_original || 0)}</td>
                    <td style="text-align:right">${fmt(totales.monto_aplicado)}</td>
                    <td colspan="3"></td>
                    <td style="text-align:right">${fmt(totales.equiv_usd_bcv)}</td>
                    <td style="text-align:right">${fmt(totales.equiv_usd_binance)}</td>
                    <td colspan="2"></td>
                </tr>
            ` : '';
            body.innerHTML = `
                <p style="color:#64748b;font-size:0.8rem;margin:0 0 0.5rem 0;">Montos en su moneda original (no se suman entre VES/USD; el total de importe original es referencial si hay monedas mixtas).</p>
                <div style="overflow-x:auto;">
                    <table class="cxc-table">
                        <thead>
                            <tr>
                                <th>Fuente</th>
                                <th>Pago</th>
                                <th>Fecha</th>
                                <th style="text-align:right">Importe Original</th>
                                <th style="text-align:right">Importe Referencia (Odoo)</th>
                                <th>Ruta</th>
                                <th style="text-align:right">Tasa BCV</th>
                                <th style="text-align:right">Tasa Binance</th>
                                <th style="text-align:right">Equiv. USD BCV</th>
                                <th style="text-align:right">Equiv. USD Binance</th>
                                <th>Confirmado por</th>
                                <th>Estado</th>
                            </tr>
                        </thead>
                        <tbody>${rows}${totalesRow}</tbody>
                    </table>
                </div>
            `;
        } catch (err) {
            body.innerHTML = '<p class="table-empty">Error de red al cargar los pagos de la orden.</p>';
            console.error(err);
        }
    }

    function cerrarModalPagosOrden() {
        const modal = document.getElementById("modal-pagos-orden");
        if (modal) modal.style.display = "none";
    }

    window.abrirModalPagosOrden = abrirModalPagosOrden;
    window.cerrarModalPagosOrden = cerrarModalPagosOrden;

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
            const subtabBadge = document.getElementById("auditoria-subtab-badge-descuentos");
            if (subtabBadge) subtabBadge.textContent = String(items.length);

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

    // Form submit handlers for new discount panels
    const recompraForm = document.getElementById("recompra-form");
    if (recompraForm) {
        recompraForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const marcas = getM2MCheckedValues(recompraForm, ".m2m-rec-marca");
            const cats = getCategoriaCombinada(recompraForm, "rec");
            const listas = getM2MCheckedValues(recompraForm, ".m2m-rec-lista");
            const rawPct = (document.getElementById("cfg-rec-porcentaje")?.value || "0.03").replace(',', '.');
            const payload = {
                marca: marcas,
                categoria: cats,
                listas_aplicables: listas,
                porcentaje: parseFloat(rawPct),
                min_cajas: parseInt(document.getElementById("cfg-rec-min-cajas")?.value || 1),
                max_cajas: parseInt(document.getElementById("cfg-rec-max-cajas")?.value || 9999),
                unidad_medida: document.getElementById("cfg-rec-unidad")?.value || "CAJAS",
                tipo_beneficio: document.getElementById("cfg-rec-tipo-benef")?.value || "descuento",
                vigencia_desde: document.getElementById("cfg-rec-desde")?.value || new Date().toISOString().split('T')[0],
                vigencia_hasta: document.getElementById("cfg-rec-hasta")?.value || null,
                activo: true,
                requiere_pago_previo: document.getElementById("cfg-rec-requiere-pago-previo")?.checked || false,
                aplica_a: document.getElementById("cfg-rec-aplica-a")?.value || "linea",
                descripcion: document.getElementById("cfg-rec-descripcion")?.value || "",
                ventana_pago_tipo: document.getElementById("cfg-rec-ventana-tipo")?.value || "vencimiento",
                ventana_pago_dias: parseInt(document.getElementById("cfg-rec-ventana-dias")?.value || 3)
            };
            const editId = recompraForm.dataset.editRegla;
            const url = editId ? `/api/config/descuentos-recompra/${editId}` : "/api/config/descuentos-recompra";
            const method = editId ? "PUT" : "POST";
            try {
                const res = await fetch(url, {
                    method,
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert(editId ? "✅ Regla de recompra actualizada correctamente." : "✅ Regla de recompra registrada correctamente.");
                    clearEditMode(recompraForm);
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
            const cats = getCategoriaCombinada(prontoPagoForm, "pp");
            const listas = getM2MCheckedValues(prontoPagoForm, ".m2m-pp-lista");
            const rawPct = (document.getElementById("cfg-pp-porcentaje")?.value || "0.05").replace(',', '.');
            const payload = {
                ventana_pago_tipo: document.getElementById("cfg-pp-ventana-tipo")?.value || "vencimiento",
                ventana_pago_dias: parseInt(document.getElementById("cfg-pp-ventana-dias")?.value || 3),
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
                aplica_a: document.getElementById("cfg-pp-aplica-a")?.value || "linea",
                descripcion: document.getElementById("cfg-pp-descripcion")?.value || ""
            };
            const editId = prontoPagoForm.dataset.editRegla;
            const url = editId ? `/api/config/descuentos-pronto-pago/${editId}` : "/api/config/descuentos-pronto-pago";
            const method = editId ? "PUT" : "POST";
            try {
                const res = await fetch(url, {
                    method,
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert(editId ? "✅ Regla de pronto pago actualizada correctamente." : "✅ Regla de pronto pago registrada correctamente.");
                    clearEditMode(prontoPagoForm);
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

    // NOTA: existía un segundo listener duplicado de promoForm.submit aquí
    // (mismos campos, mismo endpoint) -- cada submit creaba DOS reglas de
    // promoción. Eliminado; el listener real (más completo, con contador de
    // productos) está más abajo junto a loadExclusiones.

    const productoPromoForm = document.getElementById("producto-promo-form");
    if (productoPromoForm) {
        productoPromoForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const marcas = getM2MCheckedValues(productoPromoForm, ".m2m-prod-marca");
            const cats = getCategoriaCombinada(productoPromoForm, "prod");
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
                aplica_a: document.getElementById("cfg-prod-aplica-a")?.value || "linea",
                descripcion: document.getElementById("cfg-prod-descripcion")?.value || ""
            };
            const editId = productoPromoForm.dataset.editRegla;
            const url = editId ? `/api/config/descuentos-producto/${editId}` : "/api/config/descuentos-producto";
            const method = editId ? "PUT" : "POST";
            try {
                const res = await fetch(url, {
                    method,
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert(editId ? "✅ Regla de promoción por producto actualizada." : "✅ Regla de promoción por producto registrada.");
                    clearEditMode(productoPromoForm);
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
            const listas = getM2MCheckedValues(diferencialForm, ".m2m-dif-lista");
            const rawPct = (document.getElementById("cfg-dif-porcentaje-fijo")?.value || "0.35").replace(',', '.');
            const payload = {
                nombre: document.getElementById("cfg-dif-nombre")?.value || "Diferencial Cambiario",
                tipo_diferencial: document.getElementById("cfg-dif-tipo-diferencial")?.value || "fijo_35_ves_usd",
                tipo_calculo: document.getElementById("cfg-dif-tipo-calculo")?.value || "fijo",
                porcentaje_fijo: parseFloat(rawPct),
                marca: "*",
                categoria: "*",
                monedas_aplicables: document.getElementById("cfg-dif-monedas")?.value || "*",
                listas_aplicables: listas,
                unidad_medida: "USD",
                min_cantidad: 0,
                max_cantidad: 999999,
                vigencia_desde: document.getElementById("cfg-dif-desde")?.value || new Date().toISOString().split('T')[0],
                vigencia_hasta: document.getElementById("cfg-dif-hasta")?.value || null,
                activo: true,
                requiere_pago_previo: document.getElementById("cfg-dif-requiere-pago-previo")?.checked ?? true,
                aplica_a: document.getElementById("cfg-dif-aplica-a")?.value || "linea",
                descripcion: document.getElementById("cfg-dif-descripcion")?.value || ""
            };
            const editId = diferencialForm.dataset.editRegla;
            const url = editId ? `/api/config/descuentos-diferencial-cambiario/${editId}` : "/api/config/descuentos-diferencial-cambiario";
            const method = editId ? "PUT" : "POST";
            try {
                const res = await fetch(url, {
                    method,
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert(editId ? "✅ Regla de diferencial cambiario actualizada." : "✅ Regla de diferencial cambiario registrada.");
                    clearEditMode(diferencialForm);
                    loadDiferencial();
                    loadDiferencialCandidatos();
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
            loadDiferencialCandidatos,
            loadDiasCredito,
            loadTasas,
            loadFeriados,
            populateBrandsAndCategories,
            loadCategoriaArbolYPresentaciones,
            loadDescuentosMarca,
            loadDescuentosVolumen,
            loadPromociones,
            loadExclusiones,
            loadListasPrecio,
            loadPricelistMapeo,
            loadOdooProductos,
            loadClientesAuditoria,
            loadSettingsMeta
        ];
        // Cada loader pega a un endpoint independiente y pinta su propia
        // sección del DOM -- no comparten estado entre sí, así que corrían
        // en serie sin necesidad (Fase 5 del audit de rendimiento, agosto
        // 2026): 19 round-trips uno detrás del otro podían tardar varios
        // segundos en abrir Configuración. Promise.allSettled preserva el
        // aislamiento de errores por loader (uno que falla no frena a los
        // demás) que ya tenía el try/catch secuencial.
        const resultados = await Promise.allSettled(
            loaders.filter((fn) => typeof fn === "function").map((fn) => fn())
        );
        resultados.forEach((r, i) => {
            if (r.status === "rejected") {
                console.error(`Error ejecutando ${loaders[i].name || 'loader'}:`, r.reason);
            }
        });
    }

    // Load general Settings meta variables
    async function loadSettingsMeta() {
        try {
            const res = await fetch("/api/config/meta");
            if (res.ok) {
                const data = await res.json();
                if (cfgMetaDays) cfgMetaDays.value = data.cash_window_business_days || 3;
                if (cfgMetaRecompra) cfgMetaRecompra.value = data.descuento_recompra || 0.05;
                if (cfgMetaMarcaFallback) cfgMetaMarcaFallback.value = data.marca_fallback || "GLOBAL OIL";
                if (cfgMetaAjusteIndustrial) cfgMetaAjusteIndustrial.value = data.fallback_industrial_ajuste_pct || 0.04;
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
                descuento_recompra: parseFloat(cfgMetaRecompra.value),
                marca_fallback: (cfgMetaMarcaFallback?.value || "GLOBAL OIL").trim(),
                fallback_industrial_ajuste_pct: parseFloat(cfgMetaAjusteIndustrial?.value || "0.04")
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

    // Regla 3: candidatos a cierre de factura (reporte, no automático).
    async function loadDiferencialCandidatos() {
        const tbody = document.getElementById("dif-candidatos-table-body");
        const resumen = document.getElementById("dif-candidatos-resumen");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="7" class="table-empty">Cargando candidatos...</td></tr>';
            const res = await fetch("/api/diferencial/candidatos-cierre");
            if (!res.ok) throw new Error("HTTP " + res.status);
            const data = await res.json();
            if (!data.habilitado) {
                if (resumen) resumen.textContent = data.motivo || "Reporte deshabilitado.";
                tbody.innerHTML = '<tr><td colspan="7" class="table-empty">Reporte deshabilitado -- falta configurar las reglas necesarias.</td></tr>';
                return;
            }
            if (resumen) {
                resumen.innerHTML = `Diferencial máximo: <strong>${data.diferencial_maximo_pct}%</strong> &middot; ` +
                    `Diferencial de hoy (BCV vs Binance): <strong>${data.diferencial_hoy_pct}%</strong> &middot; ` +
                    `Umbral de % pagado para ser candidata: <strong>${data.umbral_pct_pagado}%</strong>`;
            }
            if (!data.candidatos || data.candidatos.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" class="table-empty">No hay órdenes candidatas hoy.</td></tr>';
                return;
            }
            const fmt = (v) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(v);
            tbody.innerHTML = "";
            data.candidatos.forEach(c => {
                const row = document.createElement("tr");
                row.innerHTML = `
                    <td><strong>${c.so_id}</strong></td>
                    <td>${c.cliente_nombre || ''}</td>
                    <td>${fmt(c.teorico_ves)}</td>
                    <td>${fmt(c.pagado_bcv)}</td>
                    <td>${c.pct_pagado}%</td>
                    <td>${fmt(c.monto_candidato_maximo)}</td>
                    <td><button type="button" class="btn btn-sm btn-secondary" data-so="${c.so_id}" data-max="${c.monto_candidato_maximo}">Aprobar Descuento</button></td>
                `;
                tbody.appendChild(row);
            });
            tbody.querySelectorAll("button[data-so]").forEach(btn => {
                btn.addEventListener("click", async () => {
                    const soId = btn.dataset.so;
                    const montoMax = btn.dataset.max;
                    const monto = prompt(`Monto a aprobar para ${soId} (máximo sugerido: $${montoMax}):`, montoMax);
                    if (monto === null || monto === "") return;
                    const motivo = prompt("Motivo (ej: cierre de factura por diferencial cambiario):", "Cierre de factura por diferencial cambiario") || "";
                    try {
                        const resp = await fetch("/api/facturacion/aprobar-descuento-sistema", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ so_id: soId, monto: parseFloat(monto), motivo, activo: true })
                        });
                        const respData = await resp.json();
                        if (!resp.ok) throw new Error(respData.detail || "Error");
                        alert(respData.message || "Descuento aprobado.");
                        loadDiferencialCandidatos();
                    } catch (err) {
                        alert("Error al aprobar el descuento: " + err.message);
                    }
                });
            });
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="7" class="table-empty">Error al cargar candidatos.</td></tr>';
        }
    }
    window.loadDiferencialCandidatos = loadDiferencialCandidatos;

    const diasCreditoForm = document.getElementById("dias-credito-form");
    if (diasCreditoForm) {
        diasCreditoForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const litrosMaxRaw = document.getElementById("cfg-dc-litros-max")?.value;
            const payload = {
                regla_id: document.getElementById("cfg-dc-regla-id")?.value || "",
                litros_minimo: parseFloat(document.getElementById("cfg-dc-litros-min")?.value || 0),
                litros_maximo: litrosMaxRaw ? parseFloat(litrosMaxRaw) : null,
                dias_credito_max: parseInt(document.getElementById("cfg-dc-dias-max")?.value || 0),
                descripcion: document.getElementById("cfg-dc-descripcion")?.value || "",
                activo: true
            };
            try {
                const res = await fetch("/api/config/dias-credito-volumen", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert("✅ Regla de días de crédito registrada.");
                    diasCreditoForm.reset();
                    loadDiasCredito();
                } else {
                    const err = await res.json();
                    alert(`❌ Error al guardar: ${err.detail || 'Error en servidor'}`);
                }
            } catch (err) {
                console.error("Error guardando regla de días de crédito:", err);
                alert("❌ Error de red.");
            }
        });
    }

    async function loadDiasCredito() {
        const tbody = document.getElementById("dias-credito-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="5" class="table-empty">Cargando reglas de días de crédito...</td></tr>';
            const res = await fetch("/api/config/dias-credito-volumen");
            if (res.ok) {
                const rules = await res.json();
                if (rules.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" class="table-empty">No hay reglas de días de crédito.</td></tr>';
                    return;
                }
                tbody.innerHTML = "";
                rules.forEach(r => {
                    const tr = document.createElement("tr");
                    const tramo = `${r.litros_minimo}L - ${r.litros_maximo != null ? r.litros_maximo + 'L' : 'sin tope'}`;
                    tr.innerHTML = `
                        <td><strong>${r.regla_id}</strong></td>
                        <td>${tramo}</td>
                        <td>${r.dias_credito_max}</td>
                        <td><small>${r.descripcion || ''}</small></td>
                        <td>${r.activo ? '<span class="state-badge" style="background:#dcfce7;color:#15803d;">Activo</span>' : '<span class="state-badge" style="background:#fee2e2;color:#991b1b;">Inactivo</span>'}</td>
                    `;
                    tbody.appendChild(tr);
                });
            }
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="5" class="table-empty">Error al cargar reglas de días de crédito.</td></tr>';
        }
    }
    window.loadDiasCredito = loadDiasCredito;

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
            // Prefijos de los 5 formularios de reglas de descuento que usan
            // checkboxes M2M de marca (antes harcodeados en el HTML).
            // "dif" (Diferencial Cambiario) no tiene marca/categoría -- se
            // calcula por abono, no por producto/línea (ver panel-header).
            const prefixes = ["rec", "pp", "vol", "promo", "prod"];

            const fillM2MBox = (selectorClass, values, valueToLabel) => {
                const inputs = document.querySelectorAll(selectorClass);
                if (inputs.length === 0) return;
                // inputs[0].parentElement es el <label> de ESE checkbox, no el
                // div contenedor de todo el grupo -- usar ese parent hacía que
                // el HTML nuevo quedara anidado dentro del primer <label> viejo
                // (checkboxes desalineados) y dejaba los demás labels viejos
                // (ej. "SINOCO", "Todas (*)") sueltos sin reemplazar
                // (duplicados). closest("div") sube hasta el div contenedor real.
                const parent = inputs[0].closest("div");
                if (!parent) return;
                const currentChecked = Array.from(inputs).filter(i => i.checked).map(i => i.value);
                const elClass = selectorClass.replace(".", "");
                let html = values.map(v => {
                    const checked = currentChecked.includes(v) ? "checked" : "";
                    return `<label><input type="checkbox" class="${elClass}" value="${v}" ${checked}> ${valueToLabel ? valueToLabel(v) : v}</label>`;
                }).join(" ");
                const isTodasChecked = currentChecked.includes("*") || currentChecked.length === 0;
                html += ` <label><input type="checkbox" class="${elClass}" value="*" ${isTodasChecked ? "checked" : ""}> Todas (*)</label>`;
                parent.innerHTML = html;
            };

            // Fetch brands (ya vivas desde Odoo — product.brand)
            const bRes = await fetch("/api/odoo/marcas");
            if (bRes.ok) {
                const brands = await bRes.json();
                prefixes.forEach(p => fillM2MBox(`.m2m-${p}-marca`, brands));
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

    // Buscador de productos en la promoción de obsequio -- filtra las
    // <option> ya cargadas por nombre/referencia sin volver a pedirlas.
    if (cfgPromoProductosBuscar && cfgPromoProductos) {
        cfgPromoProductosBuscar.addEventListener("input", () => {
            const q = cfgPromoProductosBuscar.value.trim().toLowerCase();
            Array.from(cfgPromoProductos.options).forEach(opt => {
                opt.hidden = q.length > 0 && !opt.textContent.toLowerCase().includes(q);
            });
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

    // --- Categoría madre -> subcategoría -> presentación (cascada, en vivo desde Odoo) ---
    window._categoriaArbol = window._categoriaArbol || {};
    window._presentacionesOdoo = window._presentacionesOdoo || [];
    const CASCADA_PREFIJOS = ["rec", "pp", "vol", "promo", "prod"];

    function madresChecked(prefix) {
        return Array.from(document.querySelectorAll(`.m2m-${prefix}-madre:checked`)).map(cb => cb.value);
    }

    // "Todas (*)" en madre no restringe -- se expande a las madres reales
    // (Comercial/Industrial) solo para decidir qué subcategorías/
    // presentaciones mostrar, nunca se manda así al backend (eso lo maneja
    // getCategoriaCombinada, que ya trata "*" como "sin restricción").
    function madresEfectivas(prefix) {
        const checked = madresChecked(prefix);
        if (checked.length === 0) return [];
        if (checked.includes("*")) return Object.keys(window._categoriaArbol || {});
        return checked;
    }

    function subsChecked(prefix) {
        return Array.from(document.querySelectorAll(`.m2m-${prefix}-sub:checked`))
            .map(cb => cb.value)
            .filter(v => v !== "*");
    }

    function refreshSubcategorias(prefix) {
        const box = document.querySelector(`.m2m-${prefix}-sub-box`);
        if (!box) return;
        const madres = madresEfectivas(prefix);
        const prevChecked = subsChecked(prefix);
        if (madres.length === 0) {
            box.innerHTML = '<small style="color:var(--text-muted)">Elige una categoría madre arriba para ver sus subcategorías.</small>';
            return;
        }
        const subs = new Set();
        madres.forEach(m => (window._categoriaArbol[m] || []).forEach(s => subs.add(s)));
        let html = Array.from(subs).sort().map(s => {
            const checked = prevChecked.includes(s) ? "checked" : "";
            return `<label><input type="checkbox" class="m2m-${prefix}-sub" value="${s}" ${checked}> ${s}</label>`;
        }).join(" ");
        html += ` <label><input type="checkbox" class="m2m-${prefix}-sub" value="*"> Todas (*)</label>`;
        box.innerHTML = html;
        box.querySelectorAll(`.m2m-${prefix}-sub`).forEach(cb => {
            cb.addEventListener("change", () => refreshPresentaciones(prefix));
        });
    }

    function refreshPresentaciones(prefix) {
        const box = document.querySelector(`.m2m-${prefix}-pres-box`);
        if (!box) return;
        const madres = madresEfectivas(prefix);
        const subs = subsChecked(prefix);
        const prevChecked = Array.from(document.querySelectorAll(`.m2m-${prefix}-pres:checked`)).map(cb => cb.value);
        if (madres.length === 0) {
            box.innerHTML = '<small style="color:var(--text-muted)">Elige una categoría madre arriba para ver sus presentaciones.</small>';
            return;
        }
        const matches = window._presentacionesOdoo.filter(p => {
            if (!madres.includes(p.madre)) return false;
            if (subs.length > 0 && !subs.includes(p.subcategoria)) return false;
            return true;
        });
        const valores = new Set(matches.map(p => p.presentacion));
        let html;
        if (valores.size === 0) {
            html = '<small style="color:var(--text-muted)">Sin presentaciones registradas para esta selección.</small>';
        } else {
            html = Array.from(valores).sort().map(v => {
                const checked = prevChecked.includes(v) ? "checked" : "";
                return `<label><input type="checkbox" class="m2m-${prefix}-pres" value="${v}" ${checked}> ${v}</label>`;
            }).join(" ");
        }
        html += ` <label><input type="checkbox" class="m2m-${prefix}-pres" value="*"> Todas (*)</label>`;
        box.innerHTML = html;
    }

    async function loadCategoriaArbolYPresentaciones() {
        try {
            const [rArbol, rPres] = await Promise.all([
                fetch("/api/odoo/categorias-arbol"),
                fetch("/api/odoo/presentaciones"),
            ]);
            if (rArbol.ok) window._categoriaArbol = await rArbol.json();
            if (rPres.ok) window._presentacionesOdoo = await rPres.json();
        } catch (err) {
            console.error("Error cargando árbol de categorías/presentaciones:", err);
        }
        CASCADA_PREFIJOS.forEach(prefix => {
            refreshSubcategorias(prefix);
            refreshPresentaciones(prefix);
            document.querySelectorAll(`.m2m-${prefix}-madre`).forEach(cb => {
                cb.addEventListener("change", () => {
                    refreshSubcategorias(prefix);
                    refreshPresentaciones(prefix);
                });
            });
        });
    }
    window.loadCategoriaArbolYPresentaciones = loadCategoriaArbolYPresentaciones;

    // Combina madre + subcategoría + presentación elegidos en el campo
    // `categoria` (CSV) que ya consume el motor (_match_categoria hace OR
    // contra categoria/categoria_madre/subcategoria/presentacion). Si se
    // eligió subcategoría/presentación (más específico), NO se agrega la
    // madre también -- agregarla ampliaría el match a TODA la madre y
    // anularía el propósito de elegir algo más específico.
    function getCategoriaCombinada(form, prefix) {
        const madres = getM2MCheckedValues(form, `.m2m-${prefix}-madre`);
        const subs = getM2MCheckedValues(form, `.m2m-${prefix}-sub`);
        const pres = getM2MCheckedValues(form, `.m2m-${prefix}-pres`);
        const especificos = [];
        if (subs && subs !== "*") especificos.push(subs);
        if (pres && pres !== "*") especificos.push(pres);
        if (especificos.length > 0) return especificos.join(",");
        if (madres && madres !== "*") return madres;
        return "*";
    }

    // --- Helper to Render Standardized 10-Column Rule Row ---
    window._reglasCache = window._reglasCache || {};

    function renderStandardRuleRow(r, tabla) {
        window._reglasCache[`${tabla}::${r.regla_id}`] = r;
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
        const VENTANA_PAGO_LABELS = { entrega: "Entrega", emision: "Emisión", vencimiento: "Vencimiento", no_aplica: "No aplica" };
        if (r.ventana_pago_tipo && r.ventana_pago_tipo !== "no_aplica") {
            espArr.push(`Ventana Pago: ${VENTANA_PAGO_LABELS[r.ventana_pago_tipo] || r.ventana_pago_tipo} +${r.ventana_pago_dias || 0}d`);
        }
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
                <button type="button" class="btn btn-sm" onclick="window.editarRegla('${tabla}', '${r.regla_id}')" style="background:#e0f2fe; color:#0369a1; border:1px solid #7dd3fc; padding:4px 8px; border-radius:6px; font-size:0.8rem; cursor:pointer;" title="Editar esta regla">✏️</button>
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

    // --- Edición de reglas existentes (antes solo se podían crear/eliminar/desactivar) ---
    function setM2MChecked(form, selectorClass, valueStr) {
        const raw = (valueStr === undefined || valueStr === null || String(valueStr).trim() === "")
            ? "*" : String(valueStr).trim();
        const values = raw.split(",").map(v => v.trim());
        form.querySelectorAll(selectorClass).forEach(cb => {
            cb.checked = values.includes(cb.value);
        });
    }

    function setFieldValue(id, value) {
        const el = document.getElementById(id);
        if (el && value !== undefined && value !== null) el.value = value;
    }

    function setEditMode(form, reglaId) {
        form.dataset.editRegla = reglaId;
        const btn = form.querySelector('button[type="submit"]');
        if (btn) {
            if (!btn.dataset.originalText) btn.dataset.originalText = btn.textContent;
            btn.textContent = `💾 Actualizar Regla ${reglaId}`;
        }
        let cancelBtn = form.querySelector(".btn-cancelar-edicion");
        if (!cancelBtn && btn) {
            cancelBtn = document.createElement("button");
            cancelBtn.type = "button";
            cancelBtn.className = "btn btn-sm btn-cancelar-edicion";
            cancelBtn.style.cssText = "margin-left:8px; background:#f3f4f6; color:#374151; border:1px solid #d1d5db; padding:8px 14px; border-radius:6px; cursor:pointer;";
            cancelBtn.textContent = "✖ Cancelar edición";
            cancelBtn.onclick = () => clearEditMode(form);
            btn.after(cancelBtn);
        }
        if (cancelBtn) cancelBtn.style.display = "inline-block";
    }

    function clearEditMode(form) {
        delete form.dataset.editRegla;
        const btn = form.querySelector('button[type="submit"]');
        if (btn && btn.dataset.originalText) btn.textContent = btn.dataset.originalText;
        const cancelBtn = form.querySelector(".btn-cancelar-edicion");
        if (cancelBtn) cancelBtn.style.display = "none";
        form.reset();
    }

    // Reconstruye la selección madre/subcategoría/presentación a partir del
    // CSV guardado en `categoria` -- infiere a qué madre pertenece cada
    // valor específico (subcategoría o presentación) cruzando contra el
    // árbol/presentaciones ya cargados, para poder marcar los checkboxes en
    // cascada al editar una regla existente.
    function prefillCategoriaCascada(form, prefix, categoriaCSV) {
        const valores = String(categoriaCSV || "*").split(",").map(v => v.trim()).filter(Boolean);
        form.querySelectorAll(`.m2m-${prefix}-madre`).forEach(cb => { cb.checked = false; });
        if (valores.length === 0 || valores.includes("*")) {
            refreshSubcategorias(prefix);
            refreshPresentaciones(prefix);
            return;
        }
        const madresDirectas = valores.filter(v => v === "Comercial" || v === "Industrial");
        const otros = valores.filter(v => !madresDirectas.includes(v));
        const madresInferidas = new Set(madresDirectas);
        otros.forEach(v => {
            Object.entries(window._categoriaArbol || {}).forEach(([madre, subs]) => {
                if (subs.includes(v)) madresInferidas.add(madre);
            });
            (window._presentacionesOdoo || []).forEach(p => {
                if (p.presentacion === v) madresInferidas.add(p.madre);
            });
        });
        form.querySelectorAll(`.m2m-${prefix}-madre`).forEach(cb => {
            cb.checked = madresInferidas.has(cb.value);
        });
        refreshSubcategorias(prefix);
        refreshPresentaciones(prefix);
        document.querySelectorAll(`.m2m-${prefix}-sub`).forEach(cb => {
            if (otros.includes(cb.value)) cb.checked = true;
        });
        document.querySelectorAll(`.m2m-${prefix}-pres`).forEach(cb => {
            if (otros.includes(cb.value)) cb.checked = true;
        });
    }

    function prefillRecompra(r, reglaId) {
        setM2MChecked(recompraForm, ".m2m-rec-marca", r.marca);
        prefillCategoriaCascada(recompraForm, "rec", r.categoria);
        setM2MChecked(recompraForm, ".m2m-rec-lista", r.listas_aplicables);
        setFieldValue("cfg-rec-min-cajas", r.min_cajas ?? 2);
        setFieldValue("cfg-rec-max-cajas", r.max_cajas ?? 4);
        setFieldValue("cfg-rec-unidad", r.unidad_medida || "CAJAS");
        setFieldValue("cfg-rec-tipo-benef", r.tipo_beneficio || "descuento");
        setFieldValue("cfg-rec-porcentaje", r.porcentaje ?? 0.03);
        setFieldValue("cfg-rec-ventana-tipo", r.ventana_pago_tipo || "vencimiento");
        setFieldValue("cfg-rec-ventana-dias", r.ventana_pago_dias ?? 3);
        setFieldValue("cfg-rec-desde", r.vigencia_desde || "");
        setFieldValue("cfg-rec-hasta", r.vigencia_hasta || "");
        const rpp = document.getElementById("cfg-rec-requiere-pago-previo");
        if (rpp) rpp.checked = !!r.requiere_pago_previo;
        setFieldValue("cfg-rec-aplica-a", r.aplica_a || "linea");
        setFieldValue("cfg-rec-descripcion", r.descripcion || "");
        setEditMode(recompraForm, reglaId);
        recompraForm.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function prefillProntoPago(r, reglaId) {
        setM2MChecked(prontoPagoForm, ".m2m-pp-marca", r.marca);
        prefillCategoriaCascada(prontoPagoForm, "pp", r.categoria);
        setM2MChecked(prontoPagoForm, ".m2m-pp-lista", r.listas_aplicables);
        setFieldValue("cfg-pp-ventana-tipo", r.ventana_pago_tipo || "entrega");
        setFieldValue("cfg-pp-ventana-dias", r.ventana_pago_dias ?? 3);
        setFieldValue("cfg-pp-min", r.min_cantidad ?? 0);
        setFieldValue("cfg-pp-max", r.max_cantidad ?? 999999);
        setFieldValue("cfg-pp-unidad", r.unidad_medida || "CAJAS");
        setFieldValue("cfg-pp-tipo-benef", r.tipo_beneficio || "descuento");
        setFieldValue("cfg-pp-porcentaje", r.porcentaje ?? 0.05);
        setFieldValue("cfg-pp-monedas", r.monedas_aplicables || "*");
        setFieldValue("cfg-pp-desde", r.vigencia_desde || "");
        setFieldValue("cfg-pp-hasta", r.vigencia_hasta || "");
        const rpp = document.getElementById("cfg-pp-requiere-pago-previo");
        if (rpp) rpp.checked = !!r.requiere_pago_previo;
        setFieldValue("cfg-pp-aplica-a", r.aplica_a || "linea");
        setFieldValue("cfg-pp-descripcion", r.descripcion || "");
        setEditMode(prontoPagoForm, reglaId);
        prontoPagoForm.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function prefillVolumen(r, reglaId) {
        setM2MChecked(descuentoVolumenForm, ".m2m-vol-marca", r.marca);
        prefillCategoriaCascada(descuentoVolumenForm, "vol", r.categoria);
        setM2MChecked(descuentoVolumenForm, ".m2m-vol-lista", r.listas_aplicables);
        if (cfgDescVolLitros) cfgDescVolLitros.value = r.litros_minimo ?? r.min_cantidad ?? 0;
        setFieldValue("cfg-desc-vol-max", r.max_cantidad ?? 999999);
        if (cfgDescVolPorcentaje) cfgDescVolPorcentaje.value = r.porcentaje ?? 0.05;
        setFieldValue("cfg-desc-vol-tipo-eval", r.tipo_evaluacion || "orden");
        setFieldValue("cfg-desc-vol-dias-eval", r.dias_evaluacion ?? 30);
        setFieldValue("cfg-desc-vol-unidad", r.unidad_medida || "UNIDADES");
        setFieldValue("cfg-desc-vol-tipo-benef", r.tipo_beneficio || "descuento");
        if (cfgDescVolDesde) cfgDescVolDesde.value = r.vigencia_desde || "";
        if (cfgDescVolHasta) cfgDescVolHasta.value = r.vigencia_hasta || "";
        const rpp = document.getElementById("cfg-desc-vol-requiere-pago-previo");
        if (rpp) rpp.checked = !!r.requiere_pago_previo;
        setFieldValue("cfg-desc-vol-aplica-a", r.aplica_a || "linea");
        setFieldValue("cfg-desc-vol-descripcion", r.descripcion || "");
        setEditMode(descuentoVolumenForm, reglaId);
        descuentoVolumenForm.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function prefillPromo(r, reglaId) {
        setM2MChecked(promoForm, ".m2m-promo-marca", r.marca);
        prefillCategoriaCascada(promoForm, "promo", r.categoria || r.categorias_aplica);
        setM2MChecked(promoForm, ".m2m-promo-lista", r.listas_aplicables);
        setFieldValue("cfg-promo-tipo-beneficio", r.tipo_beneficio || "producto");
        if (r.productos) {
            const skus = String(r.productos).split(",").map(s => s.trim());
            Array.from(document.getElementById("cfg-promo-productos")?.options || []).forEach(o => {
                o.selected = skus.includes(o.value);
            });
        }
        setFieldValue("cfg-promo-compra-minima", r.compra_minima ?? 0);
        setFieldValue("cfg-promo-max", r.max_cantidad ?? 999999);
        setFieldValue("cfg-promo-unidad", r.unidad_medida || "CAJAS");
        setFieldValue("cfg-promo-regalo-tipo", r.regalo_tipo || "solo_uno");
        setFieldValue("cfg-promo-fallback", r.descuento_fallback ?? 0.02);
        setFieldValue("cfg-promo-valor", r.valor ?? 0);
        setFieldValue("cfg-promo-solo-primera", r.solo_primera_compra ? "true" : "false");
        setFieldValue("cfg-promo-desde", r.vigencia_desde || "");
        setFieldValue("cfg-promo-hasta", r.vigencia_hasta || "");
        const rpp = document.getElementById("cfg-promo-requiere-pago-previo");
        if (rpp) rpp.checked = !!r.requiere_pago_previo;
        setFieldValue("cfg-promo-aplica-a", r.aplica_a || "linea");
        setFieldValue("cfg-promo-descripcion", r.descripcion || "");
        setEditMode(promoForm, reglaId);
        promoForm.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function prefillProducto(r, reglaId) {
        setM2MChecked(productoPromoForm, ".m2m-prod-marca", r.marca);
        prefillCategoriaCascada(productoPromoForm, "prod", r.categoria);
        setM2MChecked(productoPromoForm, ".m2m-prod-lista", r.listas_aplicables);
        if (r.productos) {
            const skus = String(r.productos).split(",").map(s => s.trim());
            Array.from(document.getElementById("cfg-prod-select")?.options || []).forEach(o => {
                o.selected = skus.includes(o.value);
            });
        }
        setFieldValue("cfg-prod-min", r.min_cantidad ?? 0);
        setFieldValue("cfg-prod-max", r.max_cantidad ?? 999999);
        setFieldValue("cfg-prod-unidad", r.unidad_medida || "CAJAS");
        setFieldValue("cfg-prod-tipo-benef", r.tipo_beneficio || "descuento");
        setFieldValue("cfg-prod-porcentaje", r.porcentaje ?? 0.05);
        setFieldValue("cfg-prod-monedas", r.monedas_aplicables || "*");
        setFieldValue("cfg-prod-desde", r.vigencia_desde || "");
        setFieldValue("cfg-prod-hasta", r.vigencia_hasta || "");
        const rpp = document.getElementById("cfg-prod-requiere-pago-previo");
        if (rpp) rpp.checked = !!r.requiere_pago_previo;
        setFieldValue("cfg-prod-aplica-a", r.aplica_a || "linea");
        setFieldValue("cfg-prod-descripcion", r.descripcion || "");
        setEditMode(productoPromoForm, reglaId);
        productoPromoForm.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function prefillDiferencial(r, reglaId) {
        setM2MChecked(diferencialForm, ".m2m-dif-lista", r.listas_aplicables);
        setFieldValue("cfg-dif-nombre", r.nombre || "Diferencial Cambiario");
        setFieldValue("cfg-dif-tipo-diferencial", r.tipo_diferencial || "fijo_35_ves_usd");
        setFieldValue("cfg-dif-tipo-calculo", r.tipo_calculo || "fijo");
        setFieldValue("cfg-dif-porcentaje-fijo", r.porcentaje_fijo ?? 0.35);
        setFieldValue("cfg-dif-monedas", r.monedas_aplicables || "*");
        setFieldValue("cfg-dif-desde", r.vigencia_desde || "");
        setFieldValue("cfg-dif-hasta", r.vigencia_hasta || "");
        const rpp = document.getElementById("cfg-dif-requiere-pago-previo");
        if (rpp) rpp.checked = !!r.requiere_pago_previo;
        setFieldValue("cfg-dif-aplica-a", r.aplica_a || "linea");
        setFieldValue("cfg-dif-descripcion", r.descripcion || "");
        setEditMode(diferencialForm, reglaId);
        diferencialForm.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    window.editarRegla = function (tabla, reglaId) {
        const r = window._reglasCache[`${tabla}::${reglaId}`];
        if (!r) {
            alert("No se encontró la regla en caché -- recarga Configuración e intenta de nuevo.");
            return;
        }
        const prefillers = {
            "DescuentosRecompra": prefillRecompra,
            "DescuentosProntoPago": prefillProntoPago,
            "DescuentosMarcaCategoria": prefillProntoPago,
            "DescuentosVolumen": prefillVolumen,
            "PromocionPrimeraCompra": prefillPromo,
            "DescuentosProducto": prefillProducto,
            "DescuentosDiferencialCambiario": prefillDiferencial,
        };
        const fn = prefillers[tabla];
        if (!fn) {
            alert(`Edición no soportada todavía para: ${tabla}`);
            return;
        }
        fn(r, reglaId);
    };

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
            const cats = getCategoriaCombinada(promoForm, "promo");
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
                aplica_a: document.getElementById("cfg-promo-aplica-a")?.value || "linea",
                descripcion: document.getElementById("cfg-promo-descripcion")?.value || ""
            };
            const editId = promoForm.dataset.editRegla;
            const url = editId ? `/api/config/promociones/${editId}` : "/api/config/promociones";
            const method = editId ? "PUT" : "POST";
            try {
                const res = await fetch(url, {
                    method,
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert(editId ? "✅ Promoción actualizada exitosamente." : "✅ Promoción registrada exitosamente.");
                    clearEditMode(promoForm);
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
            const cats = getCategoriaCombinada(descuentoVolumenForm, "vol");
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
                aplica_a: document.getElementById("cfg-desc-vol-aplica-a")?.value || "linea",
                descripcion: document.getElementById("cfg-desc-vol-descripcion")?.value || ""
            };
            const editId = descuentoVolumenForm.dataset.editRegla;
            const url = editId ? `/api/config/descuentos-volumen/${editId}` : "/api/config/descuentos-volumen";
            const method = editId ? "PUT" : "POST";
            try {
                const res = await fetch(url, {
                    method,
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    alert(editId ? "✅ Regla de volumen actualizada exitosamente." : "✅ Regla de volumen registrada exitosamente.");
                    clearEditMode(descuentoVolumenForm);
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

    // --- MAPEO UNIFICADO DE LISTAS DE PRECIO (agosto 2026) -- reemplaza los
    // 2 mapeos separados (USD/VES para el motor, Industrial/Comercial solo
    // consulta) que existían antes: una sola tabla, un solo endpoint, sin
    // dos fuentes de verdad para la misma lista de precios.
    window.loadPricelistMapeo = async function() {
        const body = document.getElementById("pricelist-mapeo-table-body");
        if (!body) return;

        try {
            body.innerHTML = '<tr><td colspan="5" class="table-empty">Cargando...</td></tr>';
            const [plRes, mapRes] = await Promise.all([
                fetch('/api/config/listas-precio'),
                fetch('/api/config/pricelist-mapeo')
            ]);
            const pricelists = await plRes.json();
            const mapData = await mapRes.json();
            const mapeo = mapData.mapeo || {};

            const histCheckbox = document.getElementById("cfg-historical-pricelist-enabled");
            if (histCheckbox) {
                histCheckbox.checked = mapData.historical_pricelist_enabled !== false;
            }

            if (!Array.isArray(pricelists) || pricelists.length === 0) {
                body.innerHTML = '<tr><td colspan="5" class="table-empty">No se encontraron listas de precios en Odoo.</td></tr>';
                return;
            }

            body.innerHTML = pricelists.map(pl => {
                const fila = mapeo[String(pl.id)] || { moneda: "", categoria: "", vigente: false };
                const estado = pl.active === false
                    ? '<span style="color:#dc2626; font-weight:700;" title="Lista archivada en Odoo -- sus precios pueden estar congelados">⚠ Archivada</span>'
                    : '<span style="color:#059669;">✓ Vigente en Odoo</span>';
                return `
                    <tr data-pricelist-id="${pl.id}">
                        <td><strong>#${pl.id}</strong> ${pl.name} (${pl.moneda})</td>
                        <td>${estado}</td>
                        <td>
                            <select class="pm-moneda" style="padding:0.3rem;">
                                <option value="" ${fila.moneda === "" ? "selected" : ""}>—</option>
                                <option value="usd" ${fila.moneda === "usd" ? "selected" : ""}>USD</option>
                                <option value="ves" ${fila.moneda === "ves" ? "selected" : ""}>VES</option>
                            </select>
                        </td>
                        <td>
                            <select class="pm-categoria" style="padding:0.3rem;">
                                <option value="" ${fila.categoria === "" ? "selected" : ""}>—</option>
                                <option value="industrial" ${fila.categoria === "industrial" ? "selected" : ""}>Industrial</option>
                                <option value="comercial" ${fila.categoria === "comercial" ? "selected" : ""}>Comercial</option>
                            </select>
                        </td>
                        <td style="text-align:center;">
                            <input type="checkbox" class="pm-vigente" ${fila.vigente ? "checked" : ""}>
                        </td>
                    </tr>
                `;
            }).join('');

            // Dynamically populate M2M Listas checkboxes in all rule forms.
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
                        // Solo los checks de mapeo (por vigencia VES/USD) + Todas --
                        // se quitaron los checks de listas individuales harcodeadas
                        // (#3, #4, #5...) por pedido explícito: las listas concretas
                        // cambian con el tiempo, el mapeo por vigencia ya resuelve
                        // cuál aplica en cada momento.
                        const currentChecked = Array.from(inputs).filter(i => i.checked).map(i => i.value);
                        const esListaEspecificaVieja = currentChecked.some(
                            v => v !== 'LISTAS_VES' && v !== 'LISTAS_USD' && v !== '*'
                        );
                        const isVesChecked = currentChecked.includes('LISTAS_VES')
                            || (elClass === 'm2m-dif-lista' && currentChecked.length === 0);
                        const isUsdChecked = currentChecked.includes('LISTAS_USD');
                        const isTodasChecked = currentChecked.includes('*') || esListaEspecificaVieja;

                        let html = `<label><input type="checkbox" class="${elClass}" value="LISTAS_VES" ${isVesChecked ? 'checked' : ''}> Listas VES (Mapeo)</label> `;
                        html += `<label><input type="checkbox" class="${elClass}" value="LISTAS_USD" ${isUsdChecked ? 'checked' : ''}> Listas USD (Mapeo)</label> `;
                        html += `<label><input type="checkbox" class="${elClass}" value="*" ${isTodasChecked ? 'checked' : ''}> Todas (*)</label>`;
                        parent.innerHTML = html;
                    }
                }
            });
        } catch (err) {
            console.error("Error cargando mapeo de listas:", err);
            body.innerHTML = '<tr><td colspan="5" class="table-empty" style="color:#ef4444;">Error al cargar listas.</td></tr>';
        }
    };

    window.savePricelistMapeo = async function(event) {
        if (event) event.preventDefault();
        const rows = document.querySelectorAll("#pricelist-mapeo-table-body tr[data-pricelist-id]");
        const mapeo = {};
        rows.forEach(row => {
            const pid = row.dataset.pricelistId;
            mapeo[pid] = {
                moneda: row.querySelector(".pm-moneda")?.value || "",
                categoria: row.querySelector(".pm-categoria")?.value || "",
                vigente: row.querySelector(".pm-vigente")?.checked || false,
            };
        });

        const hayUsd = Object.values(mapeo).some(f => f.moneda === "usd");
        const hayVes = Object.values(mapeo).some(f => f.moneda === "ves");
        if (!hayUsd && !hayVes) {
            alert("⚠️ Debes marcar al menos una lista con Moneda USD y una con VES.");
            return;
        }

        // Aviso (no bloqueante) si dos listas activas comparten el mismo
        // grupo Categoría+Moneda y ambas están "Vigente" -- ambiguo para
        // Inventario, que espera UNA vigente por grupo.
        const vigentesPorGrupo = {};
        Object.values(mapeo).forEach(f => {
            if (f.categoria && f.moneda && f.vigente) {
                const key = `${f.categoria}_${f.moneda}`;
                vigentesPorGrupo[key] = (vigentesPorGrupo[key] || 0) + 1;
            }
        });
        const gruposDuplicados = Object.entries(vigentesPorGrupo).filter(([, n]) => n > 1).map(([k]) => k);
        if (gruposDuplicados.length > 0) {
            if (!confirm(`⚠️ Hay más de una lista "Vigente" en el mismo grupo (${gruposDuplicados.join(", ")}). Inventario usará una cualquiera de ellas. ¿Guardar de todas formas?`)) {
                return;
            }
        }

        const histCheckbox = document.getElementById("cfg-historical-pricelist-enabled");
        const histEnabled = histCheckbox ? histCheckbox.checked : true;

        try {
            const res = await fetch('/api/config/pricelist-mapeo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mapeo, historical_pricelist_enabled: histEnabled })
            });
            const data = await res.json();
            if (res.ok) {
                alert("✅ Mapeo de listas guardado exitosamente.");
            } else {
                alert("❌ Error: " + (data.detail || "No se pudo guardar."));
            }
        } catch (err) {
            console.error("Error guardando mapeo de listas:", err);
            alert("❌ Error de red al guardar la configuración.");
        }
    };

    // --- INVENTARIO (Fase D, agosto 2026) ---
    let _inventarioCatalogoData = [];

    function _renderInventarioCatalogo(filtro) {
        const body = document.getElementById("inventario-catalogo-table-body");
        if (!body) return;
        const f = (filtro || "").trim().toLowerCase();
        const filas = f
            ? _inventarioCatalogoData.filter(p =>
                (p.codigo || "").toLowerCase().includes(f) || (p.nombre || "").toLowerCase().includes(f))
            : _inventarioCatalogoData;

        if (filas.length === 0) {
            body.innerHTML = '<tr><td colspan="7" class="table-empty">No hay productos que coincidan.</td></tr>';
            return;
        }
        body.innerHTML = filas.map(p => `
            <tr>
                <td><strong>${p.codigo || 'N/A'}</strong></td>
                <td>${p.nombre}</td>
                <td>${p.marca || '—'}</td>
                <td>${p.presentacion || '—'}</td>
                <td>${(p.litros || 0).toFixed(2)} L</td>
                <td>${(p.peso || 0).toFixed(2)}</td>
                <td>${p.unidades_por_paleta > 0 ? p.unidades_por_paleta : '—'}</td>
            </tr>
        `).join('');
    }

    const _inventarioComparativoCargado = { industrial: false, comercial: false };

    window.switchInventarioSubtab = function(name) {
        document.querySelectorAll(".subtab-btn[data-inv-subtab]").forEach(btn => {
            btn.classList.toggle("active", btn.dataset.invSubtab === name);
        });
        document.querySelectorAll(".subtab-panel[data-inv-subtab-panel]").forEach(panel => {
            panel.style.display = (panel.dataset.invSubtabPanel === name) ? "block" : "none";
        });
        if (!_inventarioComparativoCargado[name]) {
            _loadInventarioComparativo(name);
        }
    };

    async function _loadInventarioComparativo(categoria) {
        const header = document.getElementById(`inventario-comparativo-${categoria}-header`);
        const body = document.getElementById(`inventario-comparativo-${categoria}-body`);
        if (!body) return;
        const fmt = (v) => v == null ? '—' : new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(v);
        try {
            const res = await fetch(`/api/inventario/comparativo?categoria=${categoria}`);
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Error al cargar');

            _inventarioComparativoCargado[categoria] = true;
            if (header) {
                const usdTxt = data.usd_lista_id ? `Lista #${data.usd_lista_id} (USD)` : 'sin lista USD vigente asignada';
                const vesTxt = data.ves_lista_id ? `Lista #${data.ves_lista_id} (VES)` : 'sin lista VES vigente asignada';
                header.textContent = `Comparando: ${usdTxt} vs. ${vesTxt} -- precios con IVA (${(data.iva_rate * 100).toFixed(0)}%) incluido.`;
            }
            if (data.items.length === 0) {
                body.innerHTML = '<tr><td colspan="4" class="table-empty">Sin productos con precio en las listas vigentes de esta categoría -- revisa el Mapeo de Listas en Configuración.</td></tr>';
                return;
            }
            body.innerHTML = data.items.map(it => `
                <tr>
                    <td><strong>${it.codigo || 'N/A'}</strong></td>
                    <td>${it.nombre}</td>
                    <td>${fmt(it.precio_usd_con_iva)}</td>
                    <td>${fmt(it.precio_ves_con_iva)}</td>
                </tr>
            `).join('');
        } catch (err) {
            console.error(`Error cargando comparativo Inventario (${categoria}):`, err);
            if (header) header.textContent = 'Error al cargar.';
            body.innerHTML = '<tr><td colspan="4" class="table-empty" style="color:#dc2626;">Error de red al cargar el comparativo.</td></tr>';
        }
    }

    window.loadInventario = async function() {
        const catalogoBody = document.getElementById("inventario-catalogo-table-body");
        if (!catalogoBody && !document.getElementById("inventario-comparativo-industrial-body")) return;

        // Sub-pestaña activa (default "industrial") + catálogo se cargan en paralelo.
        _inventarioComparativoCargado.industrial = false;
        _inventarioComparativoCargado.comercial = false;
        const activeSubtab = document.querySelector(".subtab-btn[data-inv-subtab].active")?.dataset.invSubtab || "industrial";
        _loadInventarioComparativo(activeSubtab);

        if (catalogoBody) {
            try {
                const res = await fetch('/api/inventario/catalogo');
                _inventarioCatalogoData = await res.json();
                _renderInventarioCatalogo(document.getElementById("inventario-catalogo-search")?.value);
            } catch (err) {
                console.error("Error cargando catálogo de Inventario:", err);
                catalogoBody.innerHTML = '<tr><td colspan="7" class="table-empty">Error de red al cargar el catálogo.</td></tr>';
            }
        }
    };

    const _inventarioSearchEl = document.getElementById("inventario-catalogo-search");
    if (_inventarioSearchEl) {
        _inventarioSearchEl.addEventListener("input", () => _renderInventarioCatalogo(_inventarioSearchEl.value));
    }

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

    // COBRANZA UNIFICADA -- reemplaza las 4 tablas históricas (Pagos
    // Pendientes por Asociar, Mapa de Conciliación, Pagos Conciliados y el
    // registro de Cobranza) con una sola fuente de datos (/api/cobranza/pagos)
    // y una sola tabla. Reusa TAL CUAL las acciones ya existentes
    // (aprobarSugerenciaIndividual, abrirModalVincularManual, cerrarPagoHuerfano,
    // generarReciboSeleccionados/toggleAllCobranza)
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
        const sortBy = (document.getElementById("cobranza-sort") || {}).value || "pago_fecha_desc";

        // "Cerrados a favor de la empresa" tienen su propia bandeja (ver
        // sección debajo) -- no se muestran en la tabla principal.
        let filtered = cobranzaUnificadaData.filter(i => i.estado !== "cerrado_empresa");
        if (selVend !== "*") filtered = filtered.filter(i => (i.vendedor || "Sin Vendedor") === selVend);
        if (selEstado !== "*") filtered = filtered.filter(i => i.estado === selEstado);
        if (selMoneda !== "*") filtered = filtered.filter(i => i.moneda_pago === selMoneda);
        if (soloDup) filtered = filtered.filter(i => i.posible_duplicado);
        if (soloAlertas) filtered = filtered.filter(i => i.reasignado_por_odoo);
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
        filtered = [...filtered].sort(sorters[sortBy] || sorters.pago_fecha_desc);

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

            const vendedorCell = `<small>${item.vendedor || 'Sin Vendedor'}</small>`;

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
                    // Aprobación masiva retirada (pedido del usuario,
                    // 2026-08-22): Auto-FIFO (daemon) ya vincula estas
                    // filas automáticamente cada ciclo -- el botón "Bulk"
                    // solo aprobaba EXACTAMENTE este mismo subconjunto
                    // (con so_id, no duplicado), nunca los casos que sí
                    // requieren juicio humano (duplicados, sin orden). El
                    // botón individual "✓ Vincular" sigue para quien no
                    // quiera esperar al siguiente ciclo.
                    accionesExtra = `<button class="btn btn-sm btn-primary" onclick="aprobarSugerenciaIndividual('${item.pago_id}', '${item.so_id}', ${item.monto_sugerido})" style="padding:3px 8px; font-size:0.75rem;">✓ Vincular</button>
                        <button class="btn btn-sm btn-secondary" onclick="abrirModalVincularManual(${sugIdx})" style="padding:3px 8px; font-size:0.72rem;">✏️ Otra orden</button>`;
                } else if (sugIdx !== undefined) {
                    accionesExtra = `<button class="btn btn-sm btn-secondary" onclick="abrirModalVincularManual(${sugIdx})" style="padding:3px 8px; font-size:0.75rem;">🔗 Vincular manualmente</button>`
                        + (!tieneSugerencia ? `<button class="btn btn-sm btn-secondary" onclick="cerrarPagoHuerfano('${item.pago_id}')" style="padding:3px 8px; font-size:0.7rem; color:#92400e;">💰 Cerrar a favor de la empresa</button>` : '');
                }
            }
            // Independiente del estado (corrección del usuario,
            // 2026-08-22): un pago YA reportado se puede recibir/generar
            // recibo aunque todavía esté "pendiente" de vincular a una
            // orden -- puede_marcar_recibido ya viene así calculado desde
            // el backend para filas "pendiente" también, el checkbox no
            // debe depender de a qué rama de vinculación cayó la fila.
            if (item.puede_marcar_recibido) {
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
        document.querySelectorAll(".check-cobranza-item").forEach(cb => cb.checked = el.checked);
    };

    window.abrirModalDetallePago = function(pago_id) {
        const modal = document.getElementById("modal-detalle-pago");
        const body = document.getElementById("modal-detalle-pago-body");
        if (!modal || !body) return;
        const filas = cobranzaUnificadaData.filter(i => i.pago_id === pago_id);
        if (filas.length === 0) return;
        const fmt = (v) => v == null ? "-" : new Intl.NumberFormat("es-US", { style: "currency", currency: "USD" }).format(v);
        const base = filas[0];

        const campo = (icon, label, valor) => `<div style="margin-bottom:0.5rem;"><span style="font-size:0.75rem; color:#64748b; display:block;">${icon ? icon + ' ' : ''}${label}</span><strong>${valor ?? '-'}</strong></div>`;

        // Tarjeta tasa + equivalente SIEMPRE emparejados (pedido explícito
        // del usuario) -- una por cada una de las 3 rutas de conversión.
        const tarjetaTasa = (icon, nombre, color, tasa, equiv) => `
            <div style="background:#fff; border:1px solid #e2e8f0; border-radius:10px; padding:0.75rem 1rem; text-align:center;">
                <div style="font-size:0.75rem; color:#64748b; font-weight:600; margin-bottom:0.35rem;">${icon} ${nombre}</div>
                <div style="font-size:1.05rem; font-weight:700; color:${color};">${tasa != null ? tasa.toFixed(4) : '-'}</div>
                <div style="border-top:1px dashed #e2e8f0; margin:0.4rem 0;"></div>
                <div style="font-size:0.7rem; color:#94a3b8;">Equivalente</div>
                <div style="font-size:0.95rem; font-weight:600; color:#0f172a;">${fmt(equiv)}</div>
            </div>`;

        let html = `<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px,1fr)); gap:0.5rem 1rem; margin-bottom:1rem;">
            ${campo('🆔', 'Pago ID', base.pago_id)}
            ${campo('🧾', 'N° Pago Odoo', base.numero_pago_odoo)}
            ${campo('📅', 'Fecha', base.pago_fecha)}
            ${campo('👤', 'Cliente', base.cliente_nombre)}
            ${campo('🧑‍💼', 'Vendedor', base.vendedor)}
            ${campo('💳', 'Método de Pago', base.metodo_pago)}
            ${campo('💰', 'Monto Original', (base.moneda_pago === 'VES' ? 'Bs. ' + Number(base.monto_pago_original).toLocaleString('es-VE', {minimumFractionDigits:2}) : fmt(base.monto_pago_original)))}
            ${campo('📌', 'Estado', base.estado)}
            ${campo('📥', 'Origen', base.origen)}
            ${campo('✅', 'Confirmado Por', base.confirmado_por)}
            ${campo('🧾', 'Recibido', base.recibido ? `Sí (${base.numero_recibido || ''}, ${base.fecha_recibido || ''}, ${base.recibido_por || ''})` : 'No')}
        </div>`;

        html += `<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(150px,1fr)); gap:0.75rem; margin-bottom:1rem;">
            ${tarjetaTasa('🏦', 'Tasa BCV', '#0369a1', base.tasa_bcv, base.monto_pago_bcv_usd)}
            ${tarjetaTasa('🟡', 'Tasa Binance', '#b45309', base.tasa_binance, base.monto_pago_binance_usd)}
            ${tarjetaTasa('💶', 'Tasa BCV-EUR', '#7c3aed', base.tasa_bcv_eur, base.monto_pago_eur)}
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

        if (base.puede_editar_tasas && (base.vinc_id || base.pago_id)) {
            // Sin vinc_id (pago aún pendiente, "sugerencia sin confirmar"):
            // se guarda en pagos_tasa_binance_override por pago_id -- ver
            // POST /api/pago/{pago_id}/tasa-binance. Con vinc_id (pago ya
            // vinculado a una orden): se corrige la Vinculación real.
            const targetId = base.vinc_id || base.pago_id;
            const esVinculado = !!base.vinc_id;
            html += `<div style="background:#f8fafc; border:1px dashed #cbd5e1; border-radius:8px; padding:0.75rem; margin-bottom:1rem;">
                <label style="font-weight:700; font-size:0.85rem; display:block; margin-bottom:0.5rem;">Editar Tasas Aplicadas</label>
                <div style="display:flex; gap:0.5rem; align-items:center; margin-bottom:0.5rem; flex-wrap:wrap;">
                    <input type="number" step="0.0001" class="input-tasa-binance" data-vinc="${targetId}" data-es-vinculado="${esVinculado}" value="${base.tasa_binance ?? ''}" placeholder="Tasa Binance" style="width:120px; padding:4px 6px; font-size:0.8rem;">
                    <button class="btn btn-sm btn-secondary" onclick="guardarTasaBinance('${targetId}', ${esVinculado})" style="padding:4px 10px; font-size:0.75rem;">Guardar Binance</button>
                </div>
                ${esVinculado ? `
                <div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
                    <select class="select-bcv-variante" data-vinc="${targetId}" style="padding:4px 6px; font-size:0.8rem;">
                        <option value="USD">BCV USD</option>
                        <option value="EUR">BCV EUR</option>
                    </select>
                    <button class="btn btn-sm btn-secondary" onclick="guardarTipoTasaBcv('${targetId}')" style="padding:4px 10px; font-size:0.75rem;">Guardar Variante BCV</button>
                </div>` : ''}
            </div>`;
        }

        // Pedido explícito del usuario (2026-08-22): editar a qué orden y
        // con qué monto aplica un pago YA vinculado, mientras Odoo no lo
        // haya conciliado todavía -- "Odoo prevalece" una vez conciliado,
        // ya no se edita a mano (ver PUT /api/vinculacion/{vinc_id}/editar).
        if (base.vinc_id && base.estado !== 'conciliado_odoo') {
            html += `<div style="background:#f8fafc; border:1px dashed #cbd5e1; border-radius:8px; padding:0.75rem; margin-bottom:1rem;">
                <label style="font-weight:700; font-size:0.85rem; display:block; margin-bottom:0.5rem;">✏️ Editar Orden y Monto Aplicado</label>
                <div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap;">
                    <select id="edit-vinc-so-select" data-vinc="${base.vinc_id}" data-cliente="${base.cliente_id}" style="min-width:220px; padding:4px 6px; font-size:0.8rem;">
                        <option value="${base.so_id || ''}">${base.so_id || 'Cargando órdenes...'}</option>
                    </select>
                    <input type="number" step="0.01" id="edit-vinc-monto" value="${base.monto_aplicado ?? ''}" placeholder="Monto aplicado" style="width:140px; padding:4px 6px; font-size:0.8rem;">
                    <button class="btn btn-sm btn-secondary" onclick="guardarEdicionVinculacion('${base.vinc_id}')" style="padding:4px 10px; font-size:0.75rem;">Guardar Cambios</button>
                </div>
            </div>`;
        }

        html += `<h3 style="margin:1rem 0 0.5rem;">📋 Reparto / Órdenes y Facturas</h3>
            <div style="overflow-x:auto;">
            <table class="cxc-table"><thead><tr>
                <th>Orden</th><th>Factura</th><th>Monto Aplicado</th><th>Por Aplicar</th>
                <th>Saldo Teórico BS</th><th>Saldo Teórico USD</th>
                <th>Saldo Venta Real</th><th>Saldo Factura (Odoo)</th>
            </tr></thead><tbody>`;
        filas.forEach(f => {
            html += `<tr>
                <td>${f.so_id || '-'}</td>
                <td>${f.factura_id || '-'}</td>
                <td>${fmt(f.monto_aplicado)}</td>
                <td>${fmt(f.monto_por_aplicar)}</td>
                <td>${fmt(f.so_saldo_teorico_bs)}</td>
                <td>${fmt(f.so_saldo_teorico_usd)}</td>
                <td>${fmt(f.so_saldo_pendiente)}</td>
                <td>${fmt(f.factura_saldo_odoo)}</td>
            </tr>`;
        });
        html += `</tbody></table></div>`;

        body.innerHTML = html;
        const bcvVarianteSelect = body.querySelector(`.select-bcv-variante[data-vinc="${base.vinc_id}"]`);
        if (bcvVarianteSelect && base.bcv_variante) bcvVarianteSelect.value = base.bcv_variante;

        // Poblar el selector de "Editar Orden" con las órdenes reales del
        // cliente -- la orden actual siempre queda como primera opción
        // (aunque ya no aparezca "pendiente" en Odoo) para que el campo
        // nunca se quede vacío ni pierda el valor actual mientras carga.
        const editSoSelect = document.getElementById("edit-vinc-so-select");
        if (editSoSelect && base.cliente_id) {
            fetch(`/api/ordenes-pendientes/${base.cliente_id}`)
                .then(res => res.ok ? res.json() : [])
                .then(orders => {
                    const actual = base.so_id || '';
                    const vistas = new Set();
                    let opts = '';
                    if (actual) {
                        opts += `<option value="${actual}">${actual} (orden actual)</option>`;
                        vistas.add(actual);
                    }
                    orders.forEach(o => {
                        if (vistas.has(o.so_id)) return;
                        vistas.add(o.so_id);
                        opts += `<option value="${o.so_id}">${o.so_id} - ${o.fecha} (Saldo: ${new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(o.saldo_pendiente)})</option>`;
                    });
                    editSoSelect.innerHTML = opts || `<option value="${actual}">${actual}</option>`;
                    editSoSelect.value = actual;
                })
                .catch(err => console.error("Error cargando órdenes para editar vinculación:", err));
        }

        modal.style.display = "flex";
    };

    window.guardarEdicionVinculacion = async function(vincId) {
        const soSelect = document.getElementById("edit-vinc-so-select");
        const montoInput = document.getElementById("edit-vinc-monto");
        if (!soSelect || !montoInput) return;
        const so_id = soSelect.value;
        const monto_aplicado = parseFloat(montoInput.value);
        if (!so_id || !monto_aplicado || monto_aplicado <= 0) {
            alert("Selecciona una orden e indica un monto válido.");
            return;
        }
        try {
            const res = await fetch(`/api/vinculacion/${vincId}/editar`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ so_id, monto_aplicado })
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                alert("✅ Vinculación editada. Recálculo en segundo plano iniciado.");
                cerrarModalDetallePago();
                if (typeof loadCobranzaUnificado === "function") loadCobranzaUnificado();
            } else {
                alert(`❌ ${data.detail || "Error al editar la vinculación."}`);
            }
        } catch (err) {
            console.error("Error editando vinculación:", err);
            alert("❌ Error al editar la vinculación.");
        }
    };

    window.cerrarModalDetallePago = function() {
        const modal = document.getElementById("modal-detalle-pago");
        if (modal) modal.style.display = "none";
    };

    // Initial Load for Dashboard
    loadTasasPromedios();
    
    // --- Load Auditoría Data & Invoice Residual Discrepancies ---
    // Paginación real de "Operaciones Conformes" (antes se cortaba a las
    // primeras 100 filas sin forma de ver el resto) -- 50 filas por página.
    let conformesFullList = [];
    let conformesPage = 1;
    const CONFORMES_PAGE_SIZE = 50;

    function renderConformesPage() {
        const bodyConformes = document.getElementById("conformes-table-body");
        const pagerEl = document.getElementById("conformes-pager");
        if (!bodyConformes) return;
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

        if (conformesFullList.length === 0) {
            bodyConformes.innerHTML = '<tr><td colspan="8" class="table-empty">No hay operaciones conformes cargadas.</td></tr>';
            if (pagerEl) pagerEl.innerHTML = "";
            return;
        }

        const totalPages = Math.max(1, Math.ceil(conformesFullList.length / CONFORMES_PAGE_SIZE));
        conformesPage = Math.min(Math.max(1, conformesPage), totalPages);
        const start = (conformesPage - 1) * CONFORMES_PAGE_SIZE;
        const pageItems = conformesFullList.slice(start, start + CONFORMES_PAGE_SIZE);

        bodyConformes.innerHTML = pageItems.map(c => `
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

        if (pagerEl) {
            pagerEl.innerHTML = `
                <button type="button" class="btn-secondary" id="conformes-prev-btn" ${conformesPage <= 1 ? 'disabled' : ''}>← Anterior</button>
                <span>Página ${conformesPage} de ${totalPages} (${conformesFullList.length} operaciones conformes)</span>
                <button type="button" class="btn-secondary" id="conformes-next-btn" ${conformesPage >= totalPages ? 'disabled' : ''}>Siguiente →</button>
            `;
            const prevBtn = document.getElementById("conformes-prev-btn");
            const nextBtn = document.getElementById("conformes-next-btn");
            if (prevBtn) prevBtn.addEventListener("click", () => { conformesPage -= 1; renderConformesPage(); });
            if (nextBtn) nextBtn.addEventListener("click", () => { conformesPage += 1; renderConformesPage(); });
        }
    }

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

                const badgeDiscrepancias = document.getElementById("auditoria-subtab-badge-discrepancias");
                if (badgeDiscrepancias) badgeDiscrepancias.textContent = String(discrepancias.length + discFacturas.length);
                const badgeHistorico = document.getElementById("auditoria-subtab-badge-historico");
                if (badgeHistorico) badgeHistorico.textContent = String(aceptadas.length + conformes.length);

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

                // Render Operaciones Conformes (paginado, ver renderConformesPage)
                if (bodyConformes) {
                    conformesFullList = conformes;
                    conformesPage = 1;
                    renderConformesPage();
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
