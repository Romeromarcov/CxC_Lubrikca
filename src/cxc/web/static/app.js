document.addEventListener("DOMContentLoaded", () => {
    // State
    let selectedPayment = null;
    let reporteData = []; // Cache for live filtering
    
    // Elements - Navigation
    const tabButtons = document.querySelectorAll(".tab-btn");
    const tabPanels = document.querySelectorAll(".tab-panel");

    // Elements - KPIs
    const kpiCobrables = document.getElementById("kpi-cobrables");
    const kpiSinAsignar = document.getElementById("kpi-sin-asignar");
    const kpiAlertas = document.getElementById("kpi-alertas");
    
    // Elements - Dashboard/Payments
    const paymentsList = document.getElementById("payments-list");
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

    // Elements - Reporte & Mapa
    const reporteTableBody = document.getElementById("reporte-table-body");
    const mapaTableBody = document.getElementById("mapa-table-body");
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

    // Elements - Conciliaciones & Historial
    const historialTableBody = document.getElementById("historial-table-body");

    // Elements - Auditoria
    const auditKpiConformes = document.getElementById("audit-kpi-conformes");
    const auditKpiDiscrepancias = document.getElementById("audit-kpi-discrepancias");
    const auditKpiAceptadas = document.getElementById("audit-kpi-aceptadas");
    const auditKpiMontoDiscrepancia = document.getElementById("audit-kpi-monto-discrepancia");
    const discrepanciasTableBody = document.getElementById("discrepancias-table-body");
    const anomaliasAceptadasTableBody = document.getElementById("anomalias-aceptadas-table-body");
    const conformesTableBody = document.getElementById("conformes-table-body");

    // Tab Navigation Logic
    tabButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            const targetTab = btn.dataset.tab;
            
            // Toggle active buttons
            tabButtons.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            
            // Toggle active panels
            tabPanels.forEach(panel => {
                panel.classList.remove("active");
                if (panel.id === targetTab) {
                    panel.classList.add("active");
                }
            });

            // Lazy load tab data
            if (targetTab === "tab-dashboard") {
                loadKPIs();
                loadBandeja();
                loadTasasPromedios();
            } else if (targetTab === "tab-reporte") {
                loadReporte();
            } else if (targetTab === "tab-conciliaciones") {
                loadPayments();
                loadMapa();
                loadHistorialPagos();
            } else if (targetTab === "tab-auditoria") {
                loadAuditoria();
            } else if (targetTab === "tab-config") {
                loadConfigData();
            }
        });
    });

    // Fetch and render KPIs
    async function loadKPIs() {
        try {
            const res = await fetch("/api/resumen");
            if (res.ok) {
                const data = await res.json();
                kpiCobrables.textContent = new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(data.total_por_cobrar_usd);
                kpiSinAsignar.textContent = new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(data.pagos_sin_asignar_usd);
                kpiAlertas.textContent = data.alertas_reconciliacion;
                
                if (data.alertas_reconciliacion > 0) {
                    kpiAlertas.classList.add("danger");
                } else {
                    kpiAlertas.classList.remove("danger");
                }
            }
        } catch (err) {
            console.error("Error loading KPIs:", err);
        }
    }

    // Fetch and render pending payments
    async function loadPayments() {
        try {
            paymentsList.innerHTML = '<div class="loading-spinner">Cargando pagos...</div>';
            const res = await fetch("/api/pagos-pendientes");
            if (res.ok) {
                const payments = await res.json();
                if (payments.length === 0) {
                    paymentsList.innerHTML = '<div class="loading-spinner">No hay pagos pendientes de asignar.</div>';
                    return;
                }
                
                paymentsList.innerHTML = "";
                payments.forEach(p => {
                    const item = document.createElement("div");
                    item.className = "payment-item";
                    item.dataset.pagoId = p.pago_id;
                    
                    const fmt = (v) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(v);
                    let amountHtml = "";
                    if (p.moneda === "VES") {
                        amountHtml = `
                            <span class="p-amount text-ves" style="font-size: 0.9rem; text-align: right; line-height: 1.35;">
                                VES ${p.monto.toLocaleString('es-VE', { minimumFractionDigits: 2 })}
                                <span style="font-size: 0.72rem; font-weight: normal; color: #059669; display: block;">
                                    BCV: ~ ${fmt(p.equiv_usd_bcv)} USD
                                </span>
                                <span style="font-size: 0.72rem; font-weight: normal; color: #d97706; display: block;">
                                    Binance: ~ ${fmt(p.equiv_usd_binance)} USD
                                </span>
                            </span>`;
                    } else {
                        amountHtml = `<span class="p-amount">${new Intl.NumberFormat('es-US', { style: 'currency', currency: p.moneda }).format(p.monto)}</span>`;
                    }

                    item.innerHTML = `
                        <div class="p-header">
                            <span class="p-id">#${p.pago_id}</span>
                            ${amountHtml}
                        </div>
                        <div class="p-client">${p.cliente_nombre}</div>
                        <div class="p-meta">
                            <span>Fecha: ${p.fecha}</span>
                            <span>Módulo: ${p.metodo_pago}</span>
                        </div>
                    `;
                    item.addEventListener("click", () => selectPayment(p, item));
                    paymentsList.appendChild(item);
                });
            }
        } catch (err) {
            paymentsList.innerHTML = '<div class="loading-spinner">Error al conectar con el servidor.</div>';
            console.error("Error loading payments:", err);
        }
    }

    // Handle payment selection
    async function selectPayment(payment, element) {
        document.querySelectorAll(".payment-item").forEach(item => item.classList.remove("active"));
        element.classList.add("active");
        
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
                
                loadKPIs();
                loadPayments();
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
                            
                            row.innerHTML = `
                                <td><strong>${item.so_id}</strong></td>
                                <td>${item.cliente_nombre || item.so_id}</td>
                                <td>${isAgent}</td>
                                <td>${item.fecha || ''}</td>
                                <td><strong style="color:#059669">${fmt(item.monto_pagado || item.precio_base || 0)}</strong></td>
                                <td>${fmt(item.subtotal_neto || item.precio_base || 0)}</td>
                                <td><strong>${fmt(item.total_motor || 0)}</strong></td>
                                <td><strong style="color:#d97706">${descText}</strong></td>
                                <td><button class="btn-primary" style="padding:4px 8px;font-size:0.75rem" onclick="alert('Facturar SO ${item.so_id} en Odoo')">Aprobar Factura</button></td>
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

    // Load Historial de Pagos Asignados
    async function loadHistorialPagos() {
        if (!historialTableBody) return;
        try {
            historialTableBody.innerHTML = '<tr><td colspan="9" class="table-empty">Cargando historial de pagos asignados...</td></tr>';
            const res = await fetch("/api/pagos-historial");
            if (res.ok) {
                const items = await res.json();
                if (items.length === 0) {
                    historialTableBody.innerHTML = '<tr><td colspan="9" class="table-empty">No hay pagos asignados en el historial.</td></tr>';
                    return;
                }
                const fmt = (v) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(v);
                historialTableBody.innerHTML = "";
                items.forEach(item => {
                    const row = document.createElement("tr");
                    row.innerHTML = `
                        <td><strong>#${item.pago_id}</strong></td>
                        <td>${item.cliente_nombre}</td>
                        <td>${item.fecha_pago}</td>
                        <td><strong style="color: #059669">${fmt(item.monto_aplicado)}</strong></td>
                        <td><span class="state-badge">${item.moneda}</span></td>
                        <td><strong>${item.so_id}</strong></td>
                        <td><span class="state-badge">${item.factura_id}</span></td>
                        <td>${item.confirmado_por}</td>
                        <td><span class="state-badge cierre">${item.estado}</span></td>
                    `;
                    historialTableBody.appendChild(row);
                });
            }
        } catch (err) {
            historialTableBody.innerHTML = '<tr><td colspan="9" class="table-empty">Error al cargar el historial.</td></tr>';
            console.error("Error loading historial:", err);
        }
    }

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
                if (auditKpiMontoDiscrepancia) auditKpiMontoDiscrepancia.textContent = fmt(summary.monto_discrepancia_total);

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
    async function loadReporte() {
        try {
            reporteTableBody.innerHTML = '<tr><td colspan="12" class="table-empty">Cargando reporte general de cuentas por cobrar...</td></tr>';
            const res = await fetch("/api/reporte-saldos");
            if (res.ok) {
                reporteData = await res.json();
                renderReporteTable(reporteData);
            }
        } catch (err) {
            reporteTableBody.innerHTML = '<tr><td colspan="12" class="table-empty">Error de red al cargar el reporte.</td></tr>';
            console.error(err);
        }
    }

    function renderReporteTable(data) {
        if (data.length === 0) {
            reporteTableBody.innerHTML = '<tr><td colspan="14" class="table-empty">No hay registros de cobranza en el sistema.</td></tr>';
            return;
        }

        reporteTableBody.innerHTML = "";
        data.forEach(item => {
            const row = document.createElement("tr");

            const fmt = (val) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(val);

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

            // Format Semaphore
            let semHtml = '<span class="semaphore">Ninguno</span>';
            if (item.reconciliacion) {
                const resVal = item.reconciliacion.resultado;
                semHtml = `<span class="semaphore ${resVal.toLowerCase()}">${resVal}</span>`;
            }

            // Format Descuentos Aplicados Breakdown
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

            // Format Saldo con Descuentos
            const saldoNeto = item.saldo_deudor_con_descuentos !== undefined ? item.saldo_deudor_con_descuentos : Math.max(0, (item.total_con_descuentos || baseTotal) - (item.monto_pagado || 0));
            const totalNetoMotor = item.total_con_descuentos !== undefined ? item.total_con_descuentos : baseTotal;
            const saldoNetoColor = saldoNeto > 0.05 ? '#2563eb' : '#059669';

            row.innerHTML = `
                <td><strong>${item.so_id}</strong></td>
                <td>${item.cliente_nombre}</td>
                <td><span class="state-badge">${item.lista_precios}</span></td>
                <td>${item.fecha}</td>
                <td>${fmt(item.subtotal)}</td>
                <td>${fmt(item.monto_total)}</td>
                <td><strong style="color: #2563eb;">${fmt(item.monto_total_proyectado_usd)}</strong></td>
                <td>${fmt(item.monto_pagado)}</td>
                <td><strong style="color: ${item.saldo_deudor > 0 ? '#d97706' : '#059669'}">${fmt(item.saldo_deudor)}</strong></td>
                <td>
                    <strong style="color: ${saldoNetoColor}">${fmt(saldoNeto)}</strong>
                    <div style="font-size:0.7rem; color:#64748b">Neto Motor: ${fmt(totalNetoMotor)}</div>
                </td>
                <td>${descuentosHtml}</td>
                <td>${odooHtml}</td>
                <td>${closeHtml}</td>
                <td>${semHtml}</td>
            `;
            reporteTableBody.appendChild(row);
        });
    }

    // Load Cross-Referenced Mapping (Pago ↔ SO ↔ Invoice)
    async function loadMapa() {
        try {
            mapaTableBody.innerHTML = '<tr><td colspan="10" class="table-empty">Cargando mapa de vinculación...</td></tr>';
            const res = await fetch("/api/mapa-vinculaciones");
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    mapaTableBody.innerHTML = '<tr><td colspan="10" class="table-empty">No hay vinculaciones registradas.</td></tr>';
                    return;
                }

                mapaTableBody.innerHTML = "";
                data.forEach(item => {
                    const row = document.createElement("tr");

                    const fmt = (val) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(val);

                    row.innerHTML = `
                        <td><strong>#${item.pago_id}</strong></td>
                        <td>${item.cliente_nombre}</td>
                        <td>${item.fecha_pago}</td>
                        <td>
                            <strong style="color: #10b981">${new Intl.NumberFormat('es-US', { style: 'currency', currency: item.moneda }).format(item.monto_aplicado)}</strong>
                            ${item.moneda === "VES" ? `<div style="font-size:0.7rem; color:#64748b">Eq. Binance: ${fmt(item.monto_aplicado)}</div>` : ""}
                        </td>
                        <td><strong>${item.so_id}</strong></td>
                        <td>
                            <div>Total: ${fmt(item.order_details.total)}</div>
                            <div style="font-size:0.7rem; color:#64748b">Base: ${fmt(item.order_details.subtotal)}</div>
                        </td>
                        <td><span class="state-badge">${item.invoice_id}</span></td>
                        <td>
                            <div>Base: ${fmt(item.invoice_details.subtotal)}</div>
                            <div style="font-size:0.7rem; color:#64748b">IVA: ${fmt(item.invoice_details.iva)}</div>
                        </td>
                        <td>
                            <div>Total: ${fmt(item.invoice_details.total)}</div>
                            <div style="font-size:0.7rem; color:#d97706; font-weight:700">Saldo: ${fmt(item.invoice_details.saldo_deudor)}</div>
                        </td>
                        <td><strong>${fmt(item.invoice_details.retencion_iva_est)}</strong></td>
                    `;
                    mapaTableBody.appendChild(row);
                });
            }
        } catch (err) {
            mapaTableBody.innerHTML = '<tr><td colspan="10" class="table-empty">Error de red al cargar el mapa.</td></tr>';
            console.error(err);
        }
    }

    // Filter report table in real-time
    reporteSearch.addEventListener("keyup", () => {
        const query = reporteSearch.value.toLowerCase().trim();
        if (!query) {
            renderReporteTable(reporteData);
            return;
        }

        const filtered = reporteData.filter(item => 
            item.so_id.toLowerCase().includes(query) || 
            item.cliente_nombre.toLowerCase().includes(query)
        );
        renderReporteTable(filtered);
    });

    // Form submit handlers for new discount panels
    const recompraForm = document.getElementById("recompra-form");
    if (recompraForm) {
        recompraForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const payload = {
                porcentaje: parseFloat(document.getElementById("cfg-rec-porcentaje").value),
                max_usos_mes: parseInt(document.getElementById("cfg-rec-max-usos").value),
                dias_ventana: parseInt(document.getElementById("cfg-rec-ventana").value),
                vigencia_desde: document.getElementById("cfg-rec-desde").value || new Date().toISOString().split('T')[0],
                vigencia_hasta: document.getElementById("cfg-rec-hasta").value || null,
                activo: true
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
                } else {
                    const err = await res.json();
                    alert(`❌ Error al guardar: ${err.detail || 'Error en servidor'}`);
                }
            } catch (err) {
                alert("❌ Error de red.");
            }
        });
    }

    const prontoPagoForm = document.getElementById("pronto-pago-form");
    if (prontoPagoForm) {
        prontoPagoForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const payload = {
                dias_gracia: parseInt(document.getElementById("cfg-pp-dias-gracia").value),
                marca: document.getElementById("cfg-pp-marca").value,
                categoria: document.getElementById("cfg-pp-categoria").value,
                porcentaje: parseFloat(document.getElementById("cfg-pp-porcentaje").value),
                monedas_aplicables: document.getElementById("cfg-pp-monedas").value,
                listas_aplicables: document.getElementById("cfg-pp-listas").value,
                vigencia_desde: document.getElementById("cfg-pp-desde").value || new Date().toISOString().split('T')[0],
                vigencia_hasta: document.getElementById("cfg-pp-hasta").value || null,
                activo: true
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
                } else {
                    const err = await res.json();
                    alert(`❌ Error al guardar: ${err.detail || 'Error en servidor'}`);
                }
            } catch (err) {
                alert("❌ Error de red.");
            }
        });
    }

    const productoPromoForm = document.getElementById("producto-promo-form");
    if (productoPromoForm) {
        productoPromoForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const selProds = Array.from(document.getElementById("cfg-prod-select").selectedOptions).map(o => o.value).join(",");
            const payload = {
                productos: selProds || "*",
                marca: document.getElementById("cfg-prod-marca").value,
                categoria: document.getElementById("cfg-prod-cat").value,
                porcentaje: parseFloat(document.getElementById("cfg-prod-porcentaje").value),
                monedas_aplicables: document.getElementById("cfg-prod-monedas").value,
                listas_aplicables: document.getElementById("cfg-prod-listas").value,
                vigencia_desde: document.getElementById("cfg-prod-desde").value || new Date().toISOString().split('T')[0],
                vigencia_hasta: document.getElementById("cfg-prod-hasta").value || null,
                activo: true
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
                } else {
                    const err = await res.json();
                    alert(`❌ Error al guardar: ${err.detail || 'Error en servidor'}`);
                }
            } catch (err) {
                alert("❌ Error de red.");
            }
        });
    }

    const diferencialForm = document.getElementById("diferencial-form");
    if (diferencialForm) {
        diferencialForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const payload = {
                nombre: document.getElementById("cfg-dif-nombre").value,
                tipo_diferencial: document.getElementById("cfg-dif-tipo-diferencial").value,
                tipo_calculo: document.getElementById("cfg-dif-tipo-calculo").value,
                porcentaje_fijo: parseFloat(document.getElementById("cfg-dif-porcentaje-fijo").value || 0),
                monedas_aplicables: document.getElementById("cfg-dif-monedas").value,
                listas_aplicables: document.getElementById("cfg-dif-listas") ? document.getElementById("cfg-dif-listas").value : "*",
                vigencia_desde: document.getElementById("cfg-dif-desde").value || new Date().toISOString().split('T')[0],
                vigencia_hasta: document.getElementById("cfg-dif-hasta").value || null,
                activo: true
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
                } else {
                    const err = await res.json();
                    alert(`❌ Error al guardar: ${err.detail || 'Error en servidor'}`);
                }
            } catch (err) {
                alert("❌ Error de red.");
            }
        });
    }

    // --- Tab 3: Configuration Panels ---
    async function loadConfigData() {
        loadTasasPromedios();
        loadProntoPago();
        loadRecompra();
        loadProductoPromo();
        loadDiferencial();
        loadTasas();
        loadFeriados();
        populateBrandsAndCategories();
        loadDescuentosMarca();
        loadDescuentosVolumen();
        loadPromociones();
        loadExclusiones();
        loadListasPrecio();
        loadOdooProductos();
        loadClientesAuditoria();
        loadSettingsMeta();
    }

    // Load general Settings meta variables
    async function loadSettingsMeta() {
        try {
            const res = await fetch("/api/config/meta");
            if (res.ok) {
                const data = await res.json();
                cfgMetaDays.value = data.cash_window_business_days || 3;
                cfgMetaRecompra.value = data.descuento_recompra || 0.05;
            }
        } catch (err) {
            console.error("Error loading settings meta:", err);
        }
    }

    // Save global settings variables
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

    async function loadTasas() {
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

    // --- Load Rates Averages & Differential ---
    async function loadTasasPromedios() {
        try {
            const res = await fetch("/api/config/tasas-promedios");
            if (res.ok) {
                const d = await res.json();
                const bcvLbl = document.getElementById("lbl-rate-bcv-actual");
                const mLbl = document.getElementById("lbl-rate-binance-manana");
                const tLbl = document.getElementById("lbl-rate-binance-tarde");
                const dLbl = document.getElementById("lbl-rate-binance-diario");
                const diffLbl = document.getElementById("lbl-rate-diferencial-pct");

                if (bcvLbl) bcvLbl.textContent = d.tasa_bcv_actual ? `Bs. ${d.tasa_bcv_actual.toFixed(2)}` : "-";
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
    async function toggleRuleActive(tabla, reglaId, currentActive) {
        try {
            const res = await fetch("/api/config/toggle-descuento", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ tabla: tabla, regla_id: reglaId, activo: !currentActive })
            });
            if (res.ok) {
                loadConfigData();
            } else {
                alert("❌ Error al cambiar estado de la regla.");
            }
        } catch (err) {
            console.error("Error toggling rule:", err);
        }
    }

    // --- Load Pronto Pago Rules ---
    async function loadProntoPago() {
        const tbody = document.getElementById("pronto-pago-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="9" class="table-empty">Cargando pronto pago...</td></tr>';
            const res = await fetch("/api/config/descuentos-pronto-pago");
            if (res.ok) {
                const rules = await res.json();
                if (rules.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="9" class="table-empty">No hay reglas de pronto pago.</td></tr>';
                    return;
                }
                tbody.innerHTML = "";
                rules.forEach(r => {
                    const row = document.createElement("tr");
                    const statusBtn = document.createElement("button");
                    statusBtn.className = `state-badge ${r.activo ? 'cierre' : 'abiertas'}`;
                    statusBtn.style.cssText = `cursor:pointer; border:none; background:${r.activo ? '#dcfce7' : '#fee2e2'}; color:${r.activo ? '#15803d' : '#b91c1c'}; font-weight:600;`;
                    statusBtn.textContent = r.activo ? "Activo" : "Inactivo";
                    statusBtn.onclick = () => toggleRuleActive("DescuentosProntoPago", r.regla_id, r.activo);

                    row.innerHTML = `
                        <td><strong>${r.regla_id}</strong></td>
                        <td>${r.marca}</td>
                        <td>${r.categoria}</td>
                        <td><strong>${r.dias_gracia} días</strong></td>
                        <td><strong style="color:#059669">${(r.porcentaje * 100).toFixed(2)}%</strong></td>
                        <td><span class="state-badge">${r.monedas_aplicables}</span></td>
                        <td><span class="state-badge">${r.listas_aplicables}</span></td>
                        <td><small>Desde: ${r.vigencia_desde}</small></td>
                        <td></td>
                    `;
                    row.children[8].appendChild(statusBtn);
                    tbody.appendChild(row);
                });
            }
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="9" class="table-empty">Error al cargar pronto pago.</td></tr>';
        }
    }

    // --- Load Recompra Rules ---
    async function loadRecompra() {
        const tbody = document.getElementById("recompra-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="6" class="table-empty">Cargando reglas de recompra...</td></tr>';
            const res = await fetch("/api/config/descuentos-recompra");
            if (res.ok) {
                const rules = await res.json();
                if (rules.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="6" class="table-empty">No hay reglas de recompra.</td></tr>';
                    return;
                }
                tbody.innerHTML = "";
                rules.forEach(r => {
                    const row = document.createElement("tr");
                    const statusBtn = document.createElement("button");
                    statusBtn.className = `state-badge ${r.activo ? 'cierre' : 'abiertas'}`;
                    statusBtn.style.cssText = `cursor:pointer; border:none; background:${r.activo ? '#dcfce7' : '#fee2e2'}; color:${r.activo ? '#15803d' : '#b91c1c'}; font-weight:600;`;
                    statusBtn.textContent = r.activo ? "Activo" : "Inactivo";
                    statusBtn.onclick = () => toggleRuleActive("DescuentosRecompra", r.regla_id, r.activo);

                    row.innerHTML = `
                        <td><strong style="color:#059669">${(r.porcentaje * 100).toFixed(2)}%</strong></td>
                        <td><strong>${r.max_usos_mes}</strong></td>
                        <td><strong>${r.dias_ventana} días</strong></td>
                        <td><small>${r.vigencia_desde}</small></td>
                        <td><small>${r.vigencia_hasta || 'N/A'}</small></td>
                        <td></td>
                    `;
                    row.children[5].appendChild(statusBtn);
                    tbody.appendChild(row);
                });
            }
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="6" class="table-empty">Error al cargar recompra.</td></tr>';
        }
    }

    // --- Load Producto Promo Rules ---
    async function loadProductoPromo() {
        const tbody = document.getElementById("producto-promo-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="9" class="table-empty">Cargando promociones de productos...</td></tr>';
            const res = await fetch("/api/config/descuentos-producto");
            if (res.ok) {
                const rules = await res.json();
                if (rules.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="9" class="table-empty">No hay promociones por producto.</td></tr>';
                    return;
                }
                tbody.innerHTML = "";
                rules.forEach(r => {
                    const row = document.createElement("tr");
                    const statusBtn = document.createElement("button");
                    statusBtn.className = `state-badge ${r.activo ? 'cierre' : 'abiertas'}`;
                    statusBtn.style.cssText = `cursor:pointer; border:none; background:${r.activo ? '#dcfce7' : '#fee2e2'}; color:${r.activo ? '#15803d' : '#b91c1c'}; font-weight:600;`;
                    statusBtn.textContent = r.activo ? "Activo" : "Inactivo";
                    statusBtn.onclick = () => toggleRuleActive("DescuentosProducto", r.regla_id, r.activo);

                    row.innerHTML = `
                        <td><strong>${r.regla_id}</strong></td>
                        <td><small>${r.productos}</small></td>
                        <td>${r.marca}</td>
                        <td>${r.categoria}</td>
                        <td><strong style="color:#059669">${(r.porcentaje * 100).toFixed(2)}%</strong></td>
                        <td><span class="state-badge">${r.monedas_aplicables}</span></td>
                        <td><span class="state-badge">${r.listas_aplicables}</span></td>
                        <td><small>Desde: ${r.vigencia_desde}</small></td>
                        <td></td>
                    `;
                    row.children[8].appendChild(statusBtn);
                    tbody.appendChild(row);
                });
            }
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="9" class="table-empty">Error al cargar promociones de producto.</td></tr>';
        }
    }

    // --- Load Diferencial Rules ---
    async function loadDiferencial() {
        const tbody = document.getElementById("diferencial-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="9" class="table-empty">Cargando reglas de diferencial...</td></tr>';
            const res = await fetch("/api/config/descuentos-diferencial-cambiario");
            if (res.ok) {
                const rules = await res.json();
                if (rules.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="9" class="table-empty">No hay reglas de diferencial cambiario.</td></tr>';
                    return;
                }
                tbody.innerHTML = "";
                rules.forEach(r => {
                    const row = document.createElement("tr");
                    const statusBtn = document.createElement("button");
                    statusBtn.className = `state-badge ${r.activo ? 'cierre' : 'abiertas'}`;
                    statusBtn.style.cssText = `cursor:pointer; border:none; background:${r.activo ? '#dcfce7' : '#fee2e2'}; color:${r.activo ? '#15803d' : '#b91c1c'}; font-weight:600;`;
                    statusBtn.textContent = r.activo ? "Activo" : "Inactivo";
                    statusBtn.onclick = () => toggleRuleActive("DescuentosDiferencialCambiario", r.regla_id, r.activo);

                    row.innerHTML = `
                        <td><strong>${r.regla_id}</strong></td>
                        <td>${r.nombre}</td>
                        <td><span class="state-badge">${r.tipo_diferencial}</span></td>
                        <td><span class="state-badge">${r.tipo_calculo}</span></td>
                        <td><strong>${(r.porcentaje_fijo * 100).toFixed(1)}%</strong></td>
                        <td><span class="state-badge">${r.monedas_aplicables}</span></td>
                        <td><span class="state-badge">${r.listas_aplicables || '*'}</span></td>
                        <td><small>Desde: ${r.vigencia_desde}</small></td>
                        <td></td>
                    `;
                    row.children[8].appendChild(statusBtn);
                    tbody.appendChild(row);
                });
            }
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="9" class="table-empty">Error al cargar diferencial cambiario.</td></tr>';
        }
    }

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
                    const listasText = r.listas_aplicables === "*" ? "Todas (*)" : (r.listas_aplicables === "4" ? "Lista USD (#4)" : (r.listas_aplicables === "5" ? "Lista VES (#5)" : r.listas_aplicables));
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
        try {
            listasPrecioTableBody.innerHTML = '<tr><td colspan="6" class="table-empty">Cargando listas de precios de Odoo...</td></tr>';
            const res = await fetch("/api/config/listas-precio");
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    listasPrecioTableBody.innerHTML = '<tr><td colspan="6" class="table-empty">No hay listas de precios en Odoo.</td></tr>';
                    return;
                }

                listasPrecioTableBody.innerHTML = "";
                data.forEach(pl => {
                    const row = document.createElement("tr");

                    let startVal = "N/A";
                    let endVal = "N/A";
                    if (pl.reglas && pl.reglas.length > 0) {
                        const nonNaStarts = pl.reglas.map(r => r.fecha_inicio).filter(d => d !== "N/A");
                        const nonNaEnds = pl.reglas.map(r => r.fecha_fin).filter(d => d !== "N/A");
                        if (nonNaStarts.length > 0) startVal = nonNaStarts[0].split(" ")[0];
                        if (nonNaEnds.length > 0) endVal = nonNaEnds[0].split(" ")[0];
                    }

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
        try {
            clientesAuditoriaTableBody.innerHTML = '<tr><td colspan="5" class="table-empty">Cargando auditoría de clientes desde Odoo...</td></tr>';
            const res = await fetch("/api/odoo/clientes-auditoria");
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    clientesAuditoriaTableBody.innerHTML = '<tr><td colspan="5" class="table-empty">No hay clientes con estadísticas en Odoo.</td></tr>';
                    return;
                }

                clientesAuditoriaTableBody.innerHTML = "";
                data.forEach(c => {
                    const row = document.createElement("tr");
                    row.innerHTML = `
                        <td><strong>#${c.id}</strong></td>
                        <td>${c.nombre}</td>
                        <td>${c.fecha_creacion}</td>
                        <td><strong style="color: #2563eb">${c.ventas_cantidad}</strong></td>
                        <td>${c.fecha_ultima_venta}</td>
                    `;
                    clientesAuditoriaTableBody.appendChild(row);
                });
            }
        } catch (err) {
            clientesAuditoriaTableBody.innerHTML = '<tr><td colspan="5" class="table-empty">Error de red al cargar clientes de Odoo.</td></tr>';
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

    // Save custom holiday
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

    // Save Brand Discount Rule
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

    // Promo tipo beneficio toggle
    cfgPromoTipoBeneficio.addEventListener("change", () => {
        const isProducto = cfgPromoTipoBeneficio.value === "producto";
        promoProductosSection.style.display = isProducto ? "" : "none";
        promoRegaloTipoSection.style.display = isProducto ? "" : "none";
        promoPorcentajeSection.style.display = isProducto ? "none" : "";
    });

    // Update product count display
    cfgPromoProductos.addEventListener("change", () => {
        cfgPromoProductosCount.textContent = cfgPromoProductos.selectedOptions.length;
    });

    // Load Promociones Primera Compra
    const TIPO_LABELS = {
        "primera_compra": "Primera Compra", "recurrencia": "Recompra",
        "contado": "Pronto Pago", "volumen": "Volumen", "bcv_completo": "Diferencial BCV"
    };

    async function loadPromociones() {
        try {
            promosTableBody.innerHTML = '<tr><td colspan="8" class="table-empty">Cargando promociones...</td></tr>';
            const res = await fetch("/api/config/promociones");
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    promosTableBody.innerHTML = '<tr><td colspan="8" class="table-empty">No hay promociones registradas.</td></tr>';
                    return;
                }
                promosTableBody.innerHTML = "";
                data.forEach(p => {
                    const row = document.createElement("tr");
                    const tipoLabel = p.tipo_beneficio === "producto" ? "🎁 Obsequio" : "💲 Porcentaje";
                    const valorLabel = p.tipo_beneficio === "producto"
                        ? p.productos
                        : (parseFloat(p.valor) * 100).toFixed(2) + "%";
                    const fallbackPct = parseFloat(p.descuento_fallback || 0);
                    const fallbackLabel = fallbackPct > 0 ? (fallbackPct * 100).toFixed(2) + "%" : "—";
                    row.innerHTML = `
                        <td>${tipoLabel}</td>
                        <td><strong>${valorLabel}</strong></td>
                        <td>${p.compra_minima || "—"}</td>
                        <td>${fallbackLabel}</td>
                        <td>${p.categorias_aplica || "Comercial"}</td>
                        <td>${p.vigencia_desde}</td>
                        <td>${p.vigencia_hasta || "N/A"}</td>
                        <td><span class="semaphore ${p.activo ? 'verde' : 'rojo'}">${p.activo ? 'Activa' : 'Inactiva'}</span></td>
                    `;
                    promosTableBody.appendChild(row);
                });
            }
        } catch (err) {
            console.error(err);
        }
    }

    // Save Promotion Rule
    promoForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const tipoBenef = cfgPromoTipoBeneficio.value;
        const productosSeleccionados = tipoBenef === "producto"
            ? Array.from(cfgPromoProductos.selectedOptions).map(o => o.value).join(",")
            : "";
        const cats = ["cat-comercial", "cat-industrial", "cat-todos"]
            .map(id => document.getElementById(id))
            .filter(cb => cb && cb.checked)
            .map(cb => cb.value);
        const categoriasAplica = cats.includes("*") ? "*" : cats.join(",") || "Comercial";

        if (tipoBenef === "producto" && productosSeleccionados === "") {
            alert("⚠️ Selecciona al menos un producto de obsequio.");
            return;
        }

        const payload = {
            tipo_beneficio: tipoBenef,
            productos: productosSeleccionados,
            valor: tipoBenef === "porcentaje" ? parseFloat(cfgPromoValor.value || 0) : 1,
            compra_minima: parseFloat(cfgPromoCompraMinima.value || 0),
            descuento_fallback: parseFloat(cfgPromoFallback.value || 0),
            regalo_tipo: cfgPromoRegaloTipo.value,
            categorias_aplica: categoriasAplica,
            vigencia_desde: cfgPromoDesde.value,
            vigencia_hasta: cfgPromoHasta.value || null
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
                cfgPromoProductosCount.textContent = "0";
                loadPromociones();
            } else {
                const err = await res.json();
                alert("❌ Error: " + (err.detail || "Error al registrar la promoción."));
            }
        } catch (err) {
            alert("❌ Error de red al registrar promoción.");
            console.error(err);
        }
    });

    // Load Exclusiones
    async function loadExclusiones() {
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



    // Load Volume Discount Rules
    async function loadDescuentosVolumen() {
        try {
            descuentosVolumenTableBody.innerHTML = '<tr><td colspan="9" class="table-empty">Cargando descuentos por volumen...</td></tr>';
            const res = await fetch("/api/config/descuentos-volumen");
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    descuentosVolumenTableBody.innerHTML = '<tr><td colspan="9" class="table-empty">No hay reglas de volumen registradas.</td></tr>';
                    return;
                }
                descuentosVolumenTableBody.innerHTML = "";
                data.forEach(r => {
                    const row = document.createElement("tr");
                    const listasText = r.listas_aplicables === "*" ? "Todas (*)" : (r.listas_aplicables === "4" ? "Lista USD (#4)" : (r.listas_aplicables === "5" ? "Lista VES (#5)" : r.listas_aplicables));
                    row.innerHTML = `
                        <td><strong>${r.regla_id}</strong></td>
                        <td>${r.marca}</td>
                        <td>${r.categoria}</td>
                        <td><strong>${r.litros_minimo} L</strong></td>
                        <td><strong>${(r.porcentaje * 100).toFixed(2)}%</strong></td>
                        <td>${r.vigencia_desde || 'N/A'}</td>
                        <td>${r.vigencia_hasta || 'N/A'}</td>
                        <td><span class="state-badge" style="background:#f3f4f6; color:#374151;">${listasText}</span></td>
                        <td><span class="semaphore ${r.activo ? 'verde' : 'rojo'}">${r.activo ? 'Activo' : 'Inactivo'}</span></td>
                    `;
                    descuentosVolumenTableBody.appendChild(row);
                });
            }
        } catch (err) {
            console.error(err);
        }
    }

    // Save Volume Discount Rule
    descuentoVolumenForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const payload = {
            marca: cfgDescVolMarca.value,
            categoria: cfgDescVolCat.value,
            litros_minimo: parseFloat(cfgDescVolLitros.value),
            porcentaje: parseFloat(cfgDescVolPorcentaje.value),
            vigencia_desde: cfgDescVolDesde.value || new Date().toISOString().split('T')[0],
            vigencia_hasta: cfgDescVolHasta.value || null,
            listas_aplicables: cfgDescVolListas.value
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
            } else {
                alert("❌ Error al registrar la regla de volumen.");
            }
        } catch (err) {
            alert("❌ Error de red al registrar regla de volumen.");
            console.error(err);
        }
    });

    // Initial Load for Dashboard
    loadKPIs();
    loadPayments();
    loadBandeja();
    
    // Auto-refresh Dashboard every 30 seconds
    setInterval(() => {
        const activeTab = document.querySelector(".tab-navigation .active").dataset.tab;
        if (activeTab === "tab-dashboard") {
            loadKPIs();
            loadBandeja();
        }
    }, 30000);
});
