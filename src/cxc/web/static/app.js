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
    const descuentosTableBody = document.getElementById("descuentos-table-body");

    const listasPrecioTableBody = document.getElementById("listas-precio-table-body");

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
            if (targetTab === "tab-reporte") {
                loadReporte();
                loadMapa();
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
                    item.innerHTML = `
                        <div class="p-header">
                            <span class="p-id">#${p.pago_id}</span>
                            <span class="p-amount">${new Intl.NumberFormat('es-US', { style: 'currency', currency: p.moneda }).format(p.monto)}</span>
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
                    // Start by defaulting to the Binance equivalent value
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

    // Fetch and render Bandeja / Approval Table
    async function loadBandeja() {
        try {
            const res = await fetch("/api/bandeja");
            if (res.ok) {
                const items = await res.json();
                if (items.length === 0) {
                    bandejaTableBody.innerHTML = '<tr><td colspan="7" class="table-empty">No hay propuestas pendientes en la bandeja.</td></tr>';
                    return;
                }
                
                bandejaTableBody.innerHTML = "";
                items.forEach(item => {
                    const row = document.createElement("tr");
                    
                    let semHtml = '<span class="semaphore">Ninguno</span>';
                    if (item.reconciliacion) {
                        const resVal = item.reconciliacion.resultado;
                        semHtml = `<span class="semaphore ${resVal.toLowerCase()}">${resVal}</span>`;
                    }
                    
                    let closeHtml = '<span class="state-badge">Abierta</span>';
                    if (item.candidata_a_cierre) {
                        closeHtml = '<span class="state-badge cierre">Listo para Cierre</span>';
                    }
                    
                    row.innerHTML = `
                        <td><strong>${item.so_id}</strong></td>
                        <td>${item.lista_aplicada == "4" ? "USD" : "BCV"}</td>
                        <td>${new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(item.precio_base)}</td>
                        <td>${new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(item.total_descuentos)}</td>
                        <td><strong>${new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(item.total_motor)}</strong></td>
                        <td>${semHtml}</td>
                        <td>${closeHtml}</td>
                    `;
                    bandejaTableBody.appendChild(row);
                });
            }
        } catch (err) {
            bandejaTableBody.innerHTML = '<tr><td colspan="7" class="table-empty">Error al conectar con la bandeja de facturación.</td></tr>';
            console.error("Error loading bandeja:", err);
        }
    }

    // --- Tab 2: Accounts Receivable Report ---
    async function loadReporte() {
        try {
            reporteTableBody.innerHTML = '<tr><td colspan="9" class="table-empty">Cargando reporte general de cuentas por cobrar...</td></tr>';
            const res = await fetch("/api/reporte-saldos");
            if (res.ok) {
                reporteData = await res.json();
                renderReporteTable(reporteData);
            }
        } catch (err) {
            reporteTableBody.innerHTML = '<tr><td colspan="9" class="table-empty">Error de red al cargar el reporte.</td></tr>';
            console.error(err);
        }
    }

    function renderReporteTable(data) {
        if (data.length === 0) {
            reporteTableBody.innerHTML = '<tr><td colspan="9" class="table-empty">No hay registros de cobranza en el sistema.</td></tr>';
            return;
        }

        reporteTableBody.innerHTML = "";
        data.forEach(item => {
            const row = document.createElement("tr");

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

            row.innerHTML = `
                <td><strong>${item.so_id}</strong></td>
                <td>${item.cliente_nombre}</td>
                <td>${item.fecha}</td>
                <td>${new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(item.monto_total)}</td>
                <td>${new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(item.monto_pagado)}</td>
                <td><strong style="color: ${item.saldo_deudor > 0 ? '#fbbf24' : '#10b981'}">${new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(item.saldo_deudor)}</strong></td>
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

    // --- Tab 3: Configuration Panels ---
    async function loadConfigData() {
        loadSettingsMeta();
        loadTasas();
        loadFeriados();
        loadDescuentosMarca();
        loadListasPrecio();
        populateBrandsAndCategories();
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
                    row.innerHTML = `
                        <td>${t.timestamp}</td>
                        <td><strong>${t.tasa_bcv.toFixed(4)} Bs</strong></td>
                        <td><strong>${t.tasa_binance.toFixed(4)} Bs</strong></td>
                        <td>${t.fuente}</td>
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
                cfgDescMarca.innerHTML = '<option value="ALL">Todas las marcas (ALL)</option>';
                brands.forEach(b => {
                    const opt = document.createElement("option");
                    opt.value = b;
                    opt.textContent = b;
                    cfgDescMarca.appendChild(opt);
                });
            }

            // Fetch categories
            const cRes = await fetch("/api/odoo/categorias");
            if (cRes.ok) {
                const cats = await cRes.json();
                cfgDescCat.innerHTML = '<option value="ALL">Todas las categorías (ALL)</option>';
                cats.forEach(c => {
                    const opt = document.createElement("option");
                    opt.value = c;
                    opt.textContent = c;
                    cfgDescCat.appendChild(opt);
                });
            }
        } catch (err) {
            console.error("Error populating dropdowns:", err);
        }
    }

    async function loadDescuentosMarca() {
        try {
            descuentosTableBody.innerHTML = '<tr><td colspan="6" class="table-empty">Cargando descuentos...</td></tr>';
            const res = await fetch("/api/config/descuentos-marca");
            if (res.ok) {
                const data = await res.json();
                if (data.length === 0) {
                    descuentosTableBody.innerHTML = '<tr><td colspan="6" class="table-empty">No hay reglas registradas.</td></tr>';
                    return;
                }

                descuentosTableBody.innerHTML = "";
                data.forEach(r => {
                    const row = document.createElement("tr");
                    row.innerHTML = `
                        <td><strong>${r.regla_id}</strong></td>
                        <td>${r.marca}</td>
                        <td>${r.categoria}</td>
                        <td><span class="state-badge">${r.tipo_descuento}</span></td>
                        <td><strong>${(r.porcentaje * 100).toFixed(2)}%</strong></td>
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
            porcentaje: parseFloat(cfgDescPorcentaje.value)
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

    // Initial Load for Dashboard
    loadKPIs();
    loadPayments();
    loadBandeja();
    
    // Auto-refresh Dashboard every 30 seconds
    setInterval(() => {
        // Only refresh if active tab is dashboard to save Google Sheets API quota
        const activeTab = document.querySelector(".tab-navigation .active").dataset.tab;
        if (activeTab === "tab-dashboard") {
            loadKPIs();
            loadBandeja();
        }
    }, 30000);
});
