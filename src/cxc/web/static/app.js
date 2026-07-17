document.addEventListener("DOMContentLoaded", () => {
    // State
    let selectedPayment = null;
    
    // Elements
    const kpiCobrables = document.getElementById("kpi-cobrables");
    const kpiSinAsignar = document.getElementById("kpi-sin-asignar");
    const kpiAlertas = document.getElementById("kpi-alertas");
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

    // Fetch and render KPIs
    async function loadKPIs() {
        try {
            const res = await fetch("/api/resumen");
            if (res.ok) {
                const data = await res.json();
                kpiCobrables.textContent = new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(data.total_por_cobrar_usd);
                kpiSinAsignar.textContent = new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(data.pagos_sin_asignar_usd);
                kpiAlertas.textContent = data.alertas_reconciliacion;
                
                // Classify alerts danger state
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
        // Highlight active item
        document.querySelectorAll(".payment-item").forEach(item => item.classList.remove("active"));
        element.classList.add("active");
        
        selectedPayment = payment;
        
        // Fill form fields
        formPagoId.value = payment.pago_id;
        formClienteNombre.value = payment.cliente_nombre;
        formClienteId.value = payment.cliente_id;
        formPagoMoneda.textContent = payment.moneda;
        formPagoDisponible.value = payment.monto.toFixed(2);
        
        // Reset and lock SO select and amount input
        formSoSelect.innerHTML = '<option value="">Cargando órdenes del cliente...</option>';
        formSoSelect.disabled = true;
        formMontoAplicar.value = "";
        formMontoAplicar.disabled = true;
        btnSubmit.disabled = true;
        
        // Fetch client orders
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
                
                // Enable inputs
                formSoSelect.disabled = false;
                formMontoAplicar.disabled = false;
                formMontoAplicar.value = payment.monto.toFixed(2); // Prefill full payment amount
                formMontoAplicar.max = payment.monto;
                btnSubmit.disabled = false;
            }
        } catch (err) {
            formSoSelect.innerHTML = '<option value="">Error al cargar órdenes.</option>';
            console.error("Error fetching client orders:", err);
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
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(payload)
            });
            
            if (res.ok) {
                alert("✅ Cobro asignado y vinculado con éxito. El motor recalculando balances en segundo plano.");
                // Reset form
                vinculacionForm.reset();
                formSoSelect.innerHTML = '<option value="">Selecciona una orden de venta...</option>';
                formSoSelect.disabled = true;
                formMontoAplicar.disabled = true;
                btnSubmit.disabled = true;
                btnSubmit.textContent = "Asignar Cobro";
                selectedPayment = null;
                
                // Reload page data
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

    // Fetch and render Bandeja / Audit Table
    async function loadBandeja() {
        try {
            const res = await fetch("/api/bandeja");
            if (res.ok) {
                const items = await res.json();
                if (items.length === 0) {
                    bandejaTableBody.innerHTML = '<tr><td colspan="7" class="table-empty">No hay registros auditados en la bandeja.</td></tr>';
                    return;
                }
                
                bandejaTableBody.innerHTML = "";
                items.forEach(item => {
                    const row = document.createElement("tr");
                    
                    // Format semaphore
                    let semHtml = '<span class="semaphore">Ninguno</span>';
                    if (item.reconciliacion) {
                        const resVal = item.reconciliacion.resultado;
                        semHtml = `<span class="semaphore ${resVal.toLowerCase()}">${resVal}</span>`;
                    }
                    
                    // Format close candidacy
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

    // Initial Load
    loadKPIs();
    loadPayments();
    loadBandeja();
    
    // Auto-refresh every 30 seconds for dynamic KPIs and tables
    setInterval(() => {
        loadKPIs();
        loadBandeja();
    }, 30000);
});
