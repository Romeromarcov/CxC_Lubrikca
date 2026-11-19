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
        if (subpage === "bots" && typeof loadBots === "function") {
            loadBots();
        }
        if (subpage === "tesoreria") {
            if (typeof window.loadConfigTesoreria === "function") {
                window.loadConfigTesoreria();
            }
            if (typeof window.loadConfigGastosContabilidad === "function") {
                window.loadConfigGastosContabilidad();
            }
        }
        if (subpage === "motor") {
            if (typeof window.loadPoliticaVentasConfig === "function") {
                window.loadPoliticaVentasConfig();
            }
            if (typeof window.loadSiembraOdooEstado === "function") {
                window.loadSiembraOdooEstado();
            }
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
    const cfgMetaUmbralLitrosIndustrial = document.getElementById("cfg-meta-umbral-litros-industrial");

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
    const bandejaEnProcesoDePagoTableBody = document.getElementById("bandeja-en-proceso-de-pago-table-body");

    // User Session & Multi-Page Initialization
    let currentUserSession = null;

    async function initUserSession() {
        try {
            const res = await fetch("/api/auth/me");
            if (res.ok) {
                currentUserSession = await res.json();
                renderUserProfile(currentUserSession);
                filterNavbarByPermissions(currentUserSession);
                // Bug real (25-sep-2026, reportado por el usuario): en una
                // carga directa de /configuracion (F5, o entrar por URL en
                // vez de clic en el menú), initCurrentPage() ya corrió y
                // decidió si cargar la lista de usuarios ANTES de que esta
                // función (fetch a /api/auth/me, siempre asíncrono) supiera
                // el rol -- currentUserSession todavía era null, así que el
                // gate "es admin" nunca se cumplía y el panel se quedaba
                // colgado en "Cargando..." para siempre, sin ni siquiera
                // pedir /api/admin/usuarios. Si para cuando el rol SÍ se
                // conoce ya estamos en Configuración y es admin, se dispara
                // acá -- la misma condición que initCurrentPage() ya
                // evalúa, solo que esta vez con el dato correcto.
                const rutaActual = _normalizarModuloRuta(window.location.pathname);
                if (currentUserSession.rol === "admin" && rutaActual === "configuracion") {
                    if (typeof loadAdminUsuarios === "function") loadAdminUsuarios();
                    if (typeof loadApiKeys === "function") loadApiKeys();
                }
                if (rutaActual === "pagos") {
                    const subPag = document.querySelector(".pagos-subnav-btn.active")?.dataset?.pagosSubpage;
                    if (subPag === "conciliacion" && typeof loadBancos === "function") loadBancos();
                } else if (rutaActual === "configuracion") {
                    const subCfg = document.querySelector(".config-subnav-btn.active")?.dataset?.configSubpage;
                    if (subCfg === "bots" && typeof loadBots === "function") loadBots();
                }
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

    const _LEGACY_PATH_MAP = {
        dashboard: "inicio",
        cobranza: "pagos",
        conciliaciones: "pagos",
        bancos: "pagos",
        capturas: "compras",
        bots: "configuracion",
        reporte: "reportes",
    };

    function _normalizarModuloRuta(rawPath) {
        let seg = String(rawPath || "").toLowerCase().replace(/^\/+|\/+$/g, "").split("/")[0].split("?")[0].split("#")[0].trim();
        if (!seg || seg === "index.html") seg = "inicio";
        return _LEGACY_PATH_MAP[seg] || seg;
    }

    function _tienePermisoUI(user, permiso) {
        if (!user) return false;
        if (user.rol === "admin") return true;
        const perms = user.permisos || [];
        return perms.includes(permiso);
    }

    function filterNavbarByPermissions(user) {
        const navLinks = document.querySelectorAll(".nav-link");
        const perms = user.permisos || ["inicio"];
        const isAdm = user.rol === "admin";

        navLinks.forEach(link => {
            const page = link.dataset.page;
            const visible = !page || page === "inicio" || isAdm || perms.includes(page);
            link.classList.toggle("perm-hidden", !visible);
        });

        const btnConc = document.getElementById("btn-pagos-sub-conciliacion");
        if (btnConc) {
            const verConc = isAdm || _tienePermisoUI(user, "bancos.cargar") || _tienePermisoUI(user, "bancos.ver") || _tienePermisoUI(user, "bancos.conciliar");
            btnConc.classList.toggle("perm-hidden", !verConc);
        }
        const btnAudInt = document.getElementById("btn-aud-sub-integraciones");
        if (btnAudInt) {
            const verInt = isAdm || _tienePermisoUI(user, "capturas.ver") || _tienePermisoUI(user, "capturas.gestionar");
            btnAudInt.classList.toggle("perm-hidden", !verInt);
        }
        const btnCfgBots = document.getElementById("btn-cfg-sub-bots");
        if (btnCfgBots) {
            const verBots = isAdm || _tienePermisoUI(user, "bots.ver") || _tienePermisoUI(user, "bots.gestionar");
            btnCfgBots.classList.toggle("perm-hidden", !verBots);
        }

        const adminPanel = document.getElementById("admin-user-mgmt-panel");
        if (adminPanel) {
            adminPanel.style.display = isAdm ? "block" : "none";
        }
        const apiKeysPanel = document.getElementById("admin-api-keys-panel");
        if (apiKeysPanel) {
            apiKeysPanel.style.display = isAdm ? "block" : "none";
        }
        const bitacoraAccesos = document.getElementById("admin-accesos-bitacora-panel");
        if (bitacoraAccesos) {
            bitacoraAccesos.style.display = isAdm ? "block" : "none";
        }

        const reciboBtnContainer = document.getElementById("btn-generar-recibo-container");
        if (reciboBtnContainer) {
            const canGenerateReceipt = isAdm || perms.includes("cobranza.recibo");
            reciboBtnContainer.style.display = canGenerateReceipt ? "block" : "none";
        }

        const recalcularTodoContainer = document.getElementById("recalcular-todo-container");
        if (recalcularTodoContainer) {
            const canRecalcularTodo = isAdm || perms.includes("sistema.recalcular");
            recalcularTodoContainer.style.display = canRecalcularTodo ? "block" : "none";
        }
    }

    function applyPagosSubpage(subpage) {
        const target = ["recibidos", "enviados", "conciliacion"].includes(subpage) ? subpage : "recibidos";
        document.querySelectorAll("#tab-pagos [data-pagos-panel]").forEach((panel) => {
            panel.classList.toggle("config-subpage-hidden", panel.getAttribute("data-pagos-panel") !== target);
        });
        document.querySelectorAll(".pagos-subnav-btn").forEach((btn) => {
            btn.classList.toggle("active", btn.getAttribute("data-pagos-subpage") === target);
        });
        if (target === "recibidos") {
            if (typeof loadCobranzaUnificado === "function") loadCobranzaUnificado();
            if (typeof loadOperativosCobranza === "function") loadOperativosCobranza();
        } else if (target === "enviados") {
            if (typeof window.loadOperativosPagosEnviados === "function") window.loadOperativosPagosEnviados();
        } else if (target === "conciliacion") {
            if (typeof loadBancos === "function") loadBancos();
            if (typeof window.loadConciliacionGastos === "function") window.loadConciliacionGastos();
        }
    }

    function applyComprasSubpage(subpage) {
        if (subpage === "conciliacion") {
            if (window.CxC && window.CxC.shell && typeof window.CxC.shell.navigate === "function") {
                window.CxC.shell.navigate("pagos", "conciliacion");
            } else {
                applyPagosSubpage("conciliacion");
            }
            return;
        }
        const target = ["facturas", "gastos", "cuentas"].includes(subpage) ? subpage : "facturas";
        const panelFacturas = target === "facturas" || target === "gastos";
        document.querySelectorAll("#tab-compras [data-compras-panel]").forEach((panel) => {
            const nombre = panel.getAttribute("data-compras-panel");
            const visible = panelFacturas ? nombre === "facturas" : nombre === target;
            panel.classList.toggle("config-subpage-hidden", !visible);
        });
        const bannerGastos = document.getElementById("compras-gastos-banner");
        if (bannerGastos) bannerGastos.classList.toggle("config-subpage-hidden", target !== "gastos");
        const cajaSection = document.getElementById("compras-cajachica-section");
        if (cajaSection) cajaSection.classList.toggle("config-subpage-hidden", target !== "gastos");
        document.querySelectorAll(".compras-subnav-btn").forEach((btn) => {
            btn.classList.toggle("active", btn.getAttribute("data-compras-subpage") === target);
        });
        const tipoSel = document.getElementById("op-cmp-tipo");
        if (target === "gastos" && tipoSel) {
            tipoSel.value = "gasto";
        } else if (target === "facturas" && tipoSel && tipoSel.value === "gasto") {
            tipoSel.value = "";
        }
        if (panelFacturas) {
            if (typeof loadOperativosCompras === "function") loadOperativosCompras();
            if (target === "gastos" && typeof window.loadConciliacionGastos === "function") {
                window.loadConciliacionGastos();
            }
        } else if (target === "cuentas") {
            if (typeof loadPlanCuentasOdoo === "function" && !_ctaCargadoUnaVez) loadPlanCuentasOdoo(false);
        }
    }

    // Page Route Initialization
    function initCurrentPage() {
        const rawFirstSeg = window.location.pathname.toLowerCase().replace(/^\/+|\/+$/g, "").split("/")[0].trim();
        const path = _normalizarModuloRuta(window.location.pathname);
        const urlParams = new URLSearchParams(window.location.search || "");
        let tabParam = (urlParams.get("tab") || "").toLowerCase();
        if (!tabParam) {
            if (rawFirstSeg === "bancos") tabParam = "conciliacion";
            else if (rawFirstSeg === "bots") tabParam = "bots";
        }

        const pageToTabMap = {
            "inicio": "tab-inicio",
            "auditoria": "tab-auditoria",
            "pagos": "tab-pagos",
            "ventas": "tab-ventas",
            "compras": "tab-compras",
            "reportes": "tab-reportes",
            "facturacion": "tab-facturacion",
            "inventario": "tab-inventario",
            "contactos": "tab-contactos",
            "configuracion": "tab-config"
        };

        const targetTabId = pageToTabMap[path] || "tab-inicio";

        // Active Link Highlight
        document.querySelectorAll(".nav-link").forEach(link => {
            link.classList.toggle("active", link.dataset.page === path);
        });

        // Active Tab Panel Show
        document.querySelectorAll(".tab-panel").forEach(panel => {
            panel.classList.toggle("active", panel.id === targetTabId);
        });

        if (window.CxC && typeof window.CxC.onPageChange === "function") {
            window.CxC.onPageChange(path, tabParam);
        }

        // Load data for active page safely
        try {
            if (path === "inicio") {
                if (typeof loadTasasPromedios === "function") loadTasasPromedios();
                if (window.CxC && typeof window.CxC.loadInicioResumen === "function") window.CxC.loadInicioResumen();
            } else if (path === "facturacion") {
                if (typeof loadBandeja === "function") loadBandeja();
            } else if (path === "pagos") {
                applyPagosSubpage(tabParam || "recibidos");
                if (typeof window.loadOperativosPagosEnviados === "function" && tabParam !== "enviados") {
                    window.loadOperativosPagosEnviados();
                }
            } else if (path === "ventas") {
                const vSub = tabParam === "seguimiento" ? "seguimiento" : "documentos";
                if (typeof applyVentasSubpage === "function") applyVentasSubpage(vSub);
                if (typeof loadVentas === "function") loadVentas();
            } else if (path === "compras") {
                applyComprasSubpage(tabParam || "facturas");
            } else if (path === "reportes") {
                const repTab = ["consolidado", "cxc", "cxp", "resumen", "bd-propia", "cliente", "vendedor"].includes(tabParam)
                    ? tabParam
                    : "consolidado";
                if (typeof switchReporteSubtab === "function") switchReporteSubtab(repTab);
                if (typeof loadTasasPromedios === "function") loadTasasPromedios();
                if (typeof loadReporteDiario === "function") loadReporteDiario();
                if (typeof loadReporte === "function") loadReporte();
                if (typeof loadReporteCxcCliente === "function") loadReporteCxcCliente();
                if (typeof window.loadReportesSaldosPropios === "function") window.loadReportesSaldosPropios();
                if (typeof window.loadReportesCxPPropios === "function") window.loadReportesCxPPropios();
            } else if (path === "auditoria") {
                const aSub = ["ordenes", "pagos", "entregas", "historico", "clientes", "integraciones"].includes(tabParam) ? tabParam : "ordenes";
                if (typeof switchAuditoriaSubtab === "function") switchAuditoriaSubtab(aSub);
                if (typeof loadBalanceComprobacion === "function") loadBalanceComprobacion();
                if (typeof loadAuditoria === "function") loadAuditoria();
            } else if (path === "inventario") {
                if (typeof loadInventario === "function") loadInventario();
            } else if (path === "contactos") {
                if (typeof loadContactosOdoo === "function") loadContactosOdoo(false);
            } else if (path === "configuracion") {
                if (["descuentos", "usuarios", "listas", "otras", "bots", "motor", "tesoreria"].includes(tabParam)) {
                    applyConfigSubpage(tabParam);
                }
                if (typeof loadConfigData === "function") loadConfigData();
                if (typeof loadPricelistMapeo === "function") loadPricelistMapeo();
                if (typeof loadReglasConsolidadas === "function") loadReglasConsolidadas();
                const subCfgAct = document.querySelector(".config-subnav-btn.active")?.dataset?.configSubpage;
                if (subCfgAct === "bots" && typeof loadBots === "function") loadBots();
                if (currentUserSession && currentUserSession.rol === "admin") {
                    if (typeof loadAdminUsuarios === "function") loadAdminUsuarios();
                    if (typeof loadApiKeys === "function") loadApiKeys();
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

    // --- Panel de accesos (nov-2026): rol como base + ajustes por persona. ---
    // Ver cxc/permisos.py. Cada cambio queda en la bitácora de accesos.
    const _accEsc = (v) => String(v == null ? "" : v)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    let _accCatalogo = null;
    let _accUsuarios = [];
    let _accAbierto = null;

    async function _accCargarCatalogo() {
        if (_accCatalogo) return _accCatalogo;
        const res = await fetch("/api/admin/permisos/catalogo");
        if (!res.ok) throw new Error("No se pudo leer el catálogo de permisos");
        _accCatalogo = await res.json();
        return _accCatalogo;
    }

    function _accBase(rol) {
        const r = (_accCatalogo.roles || []).find(x => x.rol === rol);
        return new Set(r ? r.base : []);
    }

    function _accNombreRol(rol) {
        const r = _accCatalogo ? (_accCatalogo.roles || []).find(x => x.rol === rol) : null;
        return r ? r.nombre : rol;
    }

    function _accNombres() {
        return _accCatalogo ? Object.fromEntries(_accCatalogo.permisos.map(p => [p.clave, p.nombre])) : {};
    }

    function _accResumen(u) {
        if (u.rol === "admin") return '<span style="color:#475569;">Todo (Administrador)</span>';
        const nombres = _accNombres();
        const ajustes = Object.entries(u.ajustes || {});
        const mas = ajustes.filter(([, v]) => v).map(([k]) => nombres[k] || k);
        const menos = ajustes.filter(([, v]) => !v).map(([k]) => nombres[k] || k);
        if (!mas.length && !menos.length) return '<span style="color:#64748b;">Lo del rol</span>';
        return [
            ...mas.map(n => `<span class="acc-chip acc-mas">+ ${_accEsc(n)}</span>`),
            ...menos.map(n => `<span class="acc-chip acc-menos">− ${_accEsc(n)}</span>`),
        ].join(" ");
    }

    function _accFila(u, i) {
        const nombre = u.nombre_odoo && u.nombre_odoo !== u.email
            ? `<div style="font-size:0.78rem; color:#64748b;">${_accEsc(u.nombre_odoo)}</div>` : "";
        return `<tr>
            <td><strong>${_accEsc(u.email)}</strong>${nombre}</td>
            <td>${_accEsc(_accNombreRol(u.rol))}</td>
            <td style="max-width:360px;">${_accResumen(u)}</td>
            <td><span class="state-badge ${u.activo ? "cierre" : ""}">${u.activo ? "Activo" : "Desactivado"}</span></td>
            <td style="white-space:nowrap;">
                <button type="button" class="btn btn-primary" data-acc-editar="${i}" style="padding:0.3rem 0.65rem; font-size:0.78rem;">${_accAbierto === i ? "Cerrar" : "Editar accesos"}</button>
                <button type="button" class="btn btn-secondary" data-acc-clave="${i}" style="padding:0.3rem 0.65rem; font-size:0.78rem;">🔒 Restablecer clave</button>
            </td>
        </tr>` + (_accAbierto === i ? `<tr class="acc-editor-fila"><td colspan="5">${_accEditor(u, i)}</td></tr>` : "");
    }

    function _accCasillas(rol, marcados) {
        const base = _accBase(rol);
        const esAdmin = rol === "admin";
        const grupos = {};
        _accCatalogo.permisos.forEach(p => { (grupos[p.grupo] = grupos[p.grupo] || []).push(p); });
        return Object.entries(grupos).map(([grupo, ps]) => `
            <fieldset class="acc-grupo">
                <legend>${_accEsc(grupo)}</legend>
                ${ps.map(p => {
                    let on = esAdmin || marcados.has(p.clave);
                    if (p.clave === "inicio" || p.clave === "dashboard") on = true;
                    const fijo = esAdmin || !p.editable;
                    const difiere = !esAdmin && p.editable && on !== base.has(p.clave);
                    const marca = difiere
                        ? ` <span class="acc-chip ${on ? "acc-mas" : "acc-menos"}">${on ? "agregado" : "quitado"}</span>` : "";
                    return `<label class="acc-permiso${fijo ? " acc-fijo" : ""}">
                        <input type="checkbox" data-acc-permiso="${_accEsc(p.clave)}" ${on ? "checked" : ""} ${fijo ? "disabled" : ""}>
                        <span><strong>${_accEsc(p.nombre)}</strong>${marca}<small>${_accEsc(p.descripcion)}</small></span>
                    </label>`;
                }).join("")}
            </fieldset>`).join("");
    }

    function _accEditor(u, i) {
        const opciones = _accCatalogo.roles.map(r =>
            `<option value="${_accEsc(r.rol)}" ${r.rol === u.rol ? "selected" : ""}>${_accEsc(r.nombre)}</option>`).join("");
        const yo = !!(currentUserSession && currentUserSession.email === u.email);
        const bloqueo = yo ? ' disabled title="No puedes cambiar tu propio rol ni desactivarte"' : "";
        return `<div class="acc-editor" data-acc-i="${i}">
            <div class="acc-editor-top">
                <label>Rol (base) <select data-acc-rol class="filter-select"${bloqueo}>${opciones}</select></label>
                <label class="acc-activo"><input type="checkbox" data-acc-activo ${u.activo ? "checked" : ""}${bloqueo}> Activo</label>
            </div>
            <p class="acc-nota">Marca lo que esta persona debe poder hacer. Lo que difiere de su rol queda señalado como <em>agregado</em> o <em>quitado</em>. Si cambias el rol, se cargan los permisos base de ese rol.</p>
            <div class="acc-casillas">${_accCasillas(u.rol, new Set(u.permisos))}</div>
            <div class="acc-editor-pie">
                <input type="text" data-acc-comentario class="filter-select" placeholder="Motivo del cambio (queda en la bitácora)" maxlength="300">
                <button type="button" class="btn btn-primary" data-acc-guardar="${i}">Guardar</button>
                <button type="button" class="btn btn-secondary" data-acc-cancelar>Cancelar</button>
                <span data-acc-msg class="acc-msg"></span>
            </div>
        </div>`;
    }

    function _accRender() {
        const tbody = document.getElementById("admin-usuarios-table-body");
        if (!tbody) return;
        if (!_accUsuarios.length) {
            tbody.innerHTML = '<tr><td colspan="5" class="table-empty">No hay usuarios registrados en la plataforma.</td></tr>';
            return;
        }
        tbody.innerHTML = _accUsuarios.map(_accFila).join("");
    }

    window.loadAdminUsuarios = async function() {
        const tbody = document.getElementById("admin-usuarios-table-body");
        if (!tbody) return;
        tbody.innerHTML = '<tr><td colspan="5" class="table-empty">Cargando usuarios...</td></tr>';
        try {
            await _accCargarCatalogo();
            const res = await fetch("/api/admin/usuarios");
            if (!res.ok) throw new Error((await res.json()).detail || "Error en servidor");
            _accUsuarios = await res.json();
            _accAbierto = null;
            _accRender();
            loadAccesosBitacora();
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="5" class="table-empty">❌ ${_accEsc(err.message)}</td></tr>`;
            console.error(err);
        }
    };

    async function _accGuardar(i, editor) {
        const u = _accUsuarios[i];
        const msg = editor.querySelector("[data-acc-msg]");
        const permisos = [...editor.querySelectorAll("[data-acc-permiso]")]
            .filter(c => c.checked && !c.disabled).map(c => c.dataset.accPermiso);
        const cuerpo = {
            rol: editor.querySelector("[data-acc-rol]").value,
            activo: editor.querySelector("[data-acc-activo]").checked,
            permisos,
            comentario: editor.querySelector("[data-acc-comentario]").value,
        };
        msg.textContent = "Guardando...";
        msg.style.color = "#475569";
        try {
            const res = await fetch(`/api/admin/usuarios/${encodeURIComponent(u.email)}/acceso`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(cuerpo),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || "Error en servidor");
            Object.assign(u, { rol: data.rol, activo: data.activo, permisos: data.permisos, ajustes: data.ajustes });
            _accAbierto = null;
            _accRender();
            loadAccesosBitacora();
        } catch (err) {
            msg.textContent = "❌ " + err.message;
            msg.style.color = "#be123c";
        }
    }

    document.addEventListener("click", (ev) => {
        const t = ev.target.closest("[data-acc-editar],[data-acc-clave],[data-acc-guardar],[data-acc-cancelar]");
        if (!t) return;
        if (t.dataset.accEditar !== undefined) {
            const i = Number(t.dataset.accEditar);
            _accAbierto = _accAbierto === i ? null : i;
            _accRender();
        } else if (t.dataset.accClave !== undefined) {
            adminResetPassword(_accUsuarios[Number(t.dataset.accClave)].email);
        } else if (t.dataset.accGuardar !== undefined) {
            _accGuardar(Number(t.dataset.accGuardar), t.closest(".acc-editor"));
        } else {
            _accAbierto = null;
            _accRender();
        }
    });

    document.addEventListener("change", (ev) => {
        const editor = ev.target.closest(".acc-editor");
        if (!editor) return;
        const rol = editor.querySelector("[data-acc-rol]").value;
        let marcados;
        if (ev.target.matches("[data-acc-rol]")) {
            marcados = _accBase(rol);
        } else if (ev.target.matches("[data-acc-permiso]")) {
            marcados = new Set([...editor.querySelectorAll("[data-acc-permiso]")]
                .filter(c => c.checked).map(c => c.dataset.accPermiso));
        } else {
            return;
        }
        editor.querySelector(".acc-casillas").innerHTML = _accCasillas(rol, marcados);
    });

    function _accDiff(c) {
        const a = c.antes, d = c.despues, partes = [];
        const nombres = _accNombres();
        if (a.rol !== d.rol) partes.push(`Rol: ${_accEsc(_accNombreRol(a.rol))} → ${_accEsc(_accNombreRol(d.rol))}`);
        if (a.activo !== d.activo) partes.push(d.activo ? "Activado" : "Desactivado");
        const antes = new Set(a.permisos), despues = new Set(d.permisos);
        const mas = d.permisos.filter(p => !antes.has(p)).map(p => nombres[p] || p);
        const menos = a.permisos.filter(p => !despues.has(p)).map(p => nombres[p] || p);
        if (mas.length) partes.push(`<span class="acc-chip acc-mas">+ ${mas.map(_accEsc).join(", ")}</span>`);
        if (menos.length) partes.push(`<span class="acc-chip acc-menos">− ${menos.map(_accEsc).join(", ")}</span>`);
        return partes.join(" · ") || "Sin cambios visibles";
    }

    window.loadAccesosBitacora = async function() {
        const tbody = document.getElementById("admin-accesos-bitacora-body");
        if (!tbody) return;
        try {
            const res = await fetch("/api/admin/accesos/bitacora?limite=100");
            if (!res.ok) throw new Error((await res.json()).detail || "Error en servidor");
            const { cambios } = await res.json();
            tbody.innerHTML = cambios.length ? cambios.map(c => `<tr>
                <td style="white-space:nowrap;">${_accEsc(c.cambiado_en.replace("T", " "))}</td>
                <td>${_accEsc(c.email)}</td>
                <td>${_accDiff(c)}</td>
                <td>${_accEsc(c.comentario || "")}</td>
                <td>${_accEsc(c.usuario)}</td>
            </tr>`).join("") : '<tr><td colspan="5" class="table-empty">Todavía no hay cambios de acceso registrados.</td></tr>';
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="5" class="table-empty">❌ ${_accEsc(err.message)}</td></tr>`;
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

    // --- Llaves de API de solo lectura (septiembre 2026) -----------------
    window.loadApiKeys = async function() {
        const tbody = document.getElementById("admin-api-keys-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="5" class="table-empty">Cargando llaves de API...</td></tr>';
            const res = await fetch("/api/admin/api-keys");
            if (!res.ok) throw new Error("HTTP " + res.status);
            const keys = await res.json();
            if (keys.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" class="table-empty">No hay llaves de API creadas.</td></tr>';
                return;
            }
            tbody.innerHTML = "";
            keys.forEach(k => {
                const row = document.createElement("tr");
                row.innerHTML = `
                    <td><strong>${k.nombre}</strong></td>
                    <td><small>${k.creado_por || '-'}</small></td>
                    <td><small>${k.fecha_creacion ? k.fecha_creacion.replace("T", " ").split(".")[0] : '-'}</small></td>
                    <td><span class="state-badge ${k.activo ? 'cierre' : ''}">${k.activo ? 'Activa' : 'Revocada'}</span></td>
                    <td>
                        <button class="btn btn-secondary" onclick="toggleApiKey('${k.key_id}', ${!k.activo})" style="padding: 0.3rem 0.65rem; font-size: 0.78rem;">${k.activo ? '🚫 Revocar' : '♻️ Reactivar'}</button>
                    </td>
                `;
                tbody.appendChild(row);
            });
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="5" class="table-empty">Error cargando llaves de API.</td></tr>';
            console.error(err);
        }
    };

    window.toggleApiKey = async function(keyId, nuevoActivo) {
        if (!nuevoActivo && !confirm("¿Revocar esta llave? El sistema externo que la use dejará de poder conectarse de inmediato.")) return;
        try {
            const res = await fetch("/api/admin/api-keys/revocar", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ key_id: keyId, activo: nuevoActivo })
            });
            if (res.ok) {
                loadApiKeys();
            } else {
                const err = await res.json();
                alert(`❌ Error: ${err.detail || 'No se pudo actualizar la llave'}`);
            }
        } catch (err) {
            alert("❌ Error de red.");
        }
    };

    const crearApiKeyForm = document.getElementById("crear-api-key-form");
    if (crearApiKeyForm) {
        crearApiKeyForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const nombreInput = document.getElementById("api-key-nombre");
            const aviso = document.getElementById("api-key-nueva-aviso");
            try {
                const res = await fetch("/api/admin/api-keys", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ nombre: nombreInput ? nombreInput.value : "" })
                });
                const data = await res.json();
                if (res.ok && aviso) {
                    aviso.style.display = "block";
                    aviso.innerHTML = `
                        <strong>✅ Llave creada -- guárdala ahora, no se vuelve a mostrar:</strong>
                        <div style="margin-top:0.5rem;font-family:monospace;background:white;padding:0.6rem;border-radius:6px;border:1px solid #ddd6fe;word-break:break-all;user-select:all;">${data.api_key}</div>
                        <div style="margin-top:0.5rem;font-size:0.82rem;color:#475569;">Úsala como header <code>Authorization: Bearer ${data.api_key}</code>. Solo sirve para leer (GET) -- nunca puede crear, editar ni borrar nada.</div>
                    `;
                    if (nombreInput) nombreInput.value = "";
                    loadApiKeys();
                } else {
                    alert(`❌ Error: ${data.detail || 'No se pudo crear la llave'}`);
                }
            } catch (err) {
                alert("❌ Error de red al crear la llave.");
            }
        });
    }

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
                
                // Bug real (27-sep-2026, pago 1187): este campo se manda tal
                // cual a /api/vincular como "monto_aplicado", que exige la
                // moneda PROPIA del pago (VES si el pago es en bolívares) --
                // acá se pre-llenaba con el equivalente en USD, no en VES.
                // payment.monto ya viene en la moneda nativa (ver
                // abrirModalVincularManual / selectPayment más arriba).
                formMontoAplicar.value = payment.monto.toFixed(2);
                formMontoAplicar.max = payment.monto * 1.5;
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

                // El monto a aplicar va en la moneda NATIVA del pago (VES
                // acá), no en su equivalente USD -- mismo bug que el de
                // arriba (selectPayment/fetch de órdenes).
                formMontoAplicar.value = amt.toFixed(2);
                formMontoAplicar.max = amt * 1.5;
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
            <!-- Acá iban dos líneas más: "Cobrable X · facturado Y (NC
                 pendiente Z)" y "A favor $N (por aplicar)". Las quitó el
                 usuario (septiembre 2026): "el saldo a favor ya lo muestra
                 el saldo neto, y lo cobrable vs facturado eso es para uso
                 de facturación, para el vendedor es ruido".
                 Tenía razón en las dos. Esta tarjeta la usa el vendedor
                 para decidir a quién llamar, y ahí lo único que importa es
                 cuánto debe: el saldo a favor ya está descontado en los
                 montos de abajo, y la brecha entre cobrable y facturado es
                 un trámite de administración que él no puede resolver.
                 Los dos datos siguen estando, cada uno donde le sirve a
                 quien lo necesita: la NC pendiente en la Bandeja 2 de
                 Facturación, y el saldo a favor en el Reporte por Cliente. -->
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
                        : d.tipo === 'factura_sin_orden'
                            ? `Factura ${d.factura_numero} (sin orden)`
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

    // Excel de Cuentas por Cobrar: el servidor aplica los mismos filtros y
    // orden que applyReporteClienteFilters, así el archivo coincide con la tabla.
    // Se baja con fetch (no window.location) para poder mostrar "Generando..."
    // durante los ~5 s que tarda el servidor y avisar si falla.
    async function _descargarExcel(url, btn, nombrePorDefecto) {
        const textoOriginal = btn ? btn.textContent : "";
        if (btn) { btn.disabled = true; btn.textContent = "⏳ Generando Excel..."; }
        try {
            const res = await fetch(url, { cache: "no-store" });
            if (!res.ok) throw new Error("HTTP " + res.status);
            const blob = await res.blob();
            const cd = res.headers.get("content-disposition") || "";
            const m = cd.match(/filename="?([^";]+)"?/);
            const nombre = m ? m[1] : `${nombrePorDefecto}_${new Date().toISOString().slice(0, 10)}.xlsx`;
            const objUrl = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = objUrl;
            a.download = nombre;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
        } catch (err) {
            console.error("Error descargando el Excel:", err);
            alert("No se pudo generar el Excel (" + err.message + "). Intenta de nuevo.");
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = textoOriginal; }
        }
    }

    function descargarReporteCxcExcel(btn) {
        const params = new URLSearchParams();
        const antiguedadVal = document.getElementById("reporte-cliente-antiguedad-filter")?.value || "*";
        const vendedorVal = document.getElementById("reporte-cliente-vendedor-filter")?.value || "*";
        const sortVal = document.getElementById("reporte-cliente-sort")?.value || "saldo_desc";
        const searchVal = (document.getElementById("reporte-cliente-search")?.value || "").trim();
        if (antiguedadVal !== "*") params.set("antiguedad", antiguedadVal);
        if (vendedorVal !== "*") params.set("vendedor", vendedorVal);
        if (sortVal !== "saldo_desc") params.set("sort", sortVal);
        if (searchVal) params.set("search", searchVal);
        return _descargarExcel("/api/reporte-cxc-cliente/excel?" + params.toString(), btn, "cuentas_por_cobrar");
    }
    window.descargarReporteCxcExcel = descargarReporteCxcExcel;

    // ── Reporte de Saldos: sub-pestañas Por Cliente / Por Vendedor ────────
    let fullVendedoresCxc = null;  // null = todavía no cargado

    window.switchReporteSubtab = function(name) {
        const aliasMap = {
            resumen: "consolidado",
            "bd-propia": "cxc",
            cliente: "cxc",
            vendedor: "cxc",
        };
        const canon = aliasMap[name] || (["consolidado", "cxc", "cxp"].includes(name) ? name : "consolidado");
        document.querySelectorAll(".subtab-btn[data-rep-subtab]").forEach(btn => {
            btn.classList.toggle("active", btn.dataset.repSubtab === canon);
        });
        document.querySelectorAll(".subtab-panel[data-rep-subtab-panel]").forEach(panel => {
            panel.style.display = (panel.dataset.repSubtabPanel === canon) ? "block" : "none";
        });
        if (name === "vendedor" && typeof window.switchCxcVista === "function") {
            window.switchCxcVista("vendedores");
        } else if (name === "cliente" && typeof window.switchCxcVista === "function") {
            window.switchCxcVista("clientes");
        }
        if (canon === "cxc") {
            if (typeof window.loadReportesSaldosPropios === "function") window.loadReportesSaldosPropios();
            if (fullVendedoresCxc === null && typeof loadReporteCxcVendedor === "function") loadReporteCxcVendedor();
        } else if (canon === "cxp") {
            if (typeof window.loadReportesCxPPropios === "function") window.loadReportesCxPPropios();
        } else if (canon === "consolidado") {
            if (typeof window.loadReportesSaldosPropios === "function") window.loadReportesSaldosPropios();
            if (typeof window.loadReportesCxPPropios === "function") window.loadReportesCxPPropios();
        }
    };

    const _TRAMOS = ["vigente", "1_30", "31_60", "61_90", "mas_90"];

    function _celdasMontoVendedor(acc, colorFmt) {
        const a = acc.antiguedad || {};
        const s = acc.saldos || {};
        return _TRAMOS.map(k => `<td>${colorFmt(a[k])}</td>`).join("") +
            `<td>${colorFmt(acc.pagos_sin_aplicar)}</td>` +
            `<td>${colorFmt(s.venta_real)}</td>` +
            `<td>${colorFmt(s.teorico_usd)}</td>` +
            `<td>${colorFmt(s.teorico_bs)}</td>` +
            `<td>${colorFmt(s.factura_real)}</td>`;
    }

    function renderReporteVendedorTable(vendedores) {
        const tbody = document.getElementById("reporte-cxc-vendedor-table-body");
        if (!tbody) return;
        const fmt = (v) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(v || 0);
        const colorFmt = (v) => {
            if (v == null) return "-";
            if (Math.abs(v) < 0.005) return '<span style="color:#94a3b8">$0.00</span>';
            return `<span style="color:${v > 0 ? '#dc2626' : '#059669'}"><strong>${fmt(v)}</strong></span>`;
        };
        const esc = (t) => String(t ?? "").replace(/[&<>"]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));

        if (!vendedores.length) {
            tbody.innerHTML = '<tr><td colspan="14" class="table-empty">No hay vendedores que coincidan con la búsqueda.</td></tr>';
            return;
        }
        tbody.innerHTML = "";
        vendedores.forEach((v, idx) => {
            const row = document.createElement("tr");
            row.style.cursor = "pointer";
            const toggleId = `cxc-vend-toggle-${idx}`;
            row.innerHTML = `
                <td><span id="${toggleId}">▶</span></td>
                <td><strong>${esc(v.vendedor)}</strong></td>
                <td>${v.n_clientes}</td>
                <td>${v.dias_vencido_max || 0}</td>
                ${_celdasMontoVendedor(v, colorFmt)}
            `;
            const filasClientes = [];
            row.addEventListener("click", () => {
                const toggle = document.getElementById(toggleId);
                if (filasClientes.length) {
                    filasClientes.splice(0).forEach(r => r.remove());
                    if (toggle) toggle.textContent = "▶";
                    return;
                }
                if (toggle) toggle.textContent = "▼";
                let ultimo = row;
                (v.clientes || []).forEach(c => {
                    const tr = document.createElement("tr");
                    tr.style.background = "#f8fafc";
                    tr.style.fontSize = "0.9em";
                    tr.innerHTML = `
                        <td></td>
                        <td style="padding-left:1.5rem">${esc(c.cliente_nombre)}</td>
                        <td><small>${c.n_ordenes} doc.</small></td>
                        <td>${c.dias_vencido_max || 0}</td>
                        ${_celdasMontoVendedor(c, colorFmt)}
                    `;
                    ultimo.after(tr);
                    ultimo = tr;
                    filasClientes.push(tr);
                });
            });
            tbody.appendChild(row);
        });

        // Fila de totales de lo que se está viendo.
        const t = { antiguedad: {}, saldos: {}, pagos_sin_aplicar: 0 };
        _TRAMOS.forEach(k => { t.antiguedad[k] = vendedores.reduce((s, v) => s + (v.antiguedad[k] || 0), 0); });
        ["venta_real", "teorico_usd", "teorico_bs", "factura_real"].forEach(k => {
            t.saldos[k] = vendedores.reduce((s, v) => s + (v.saldos[k] || 0), 0);
        });
        t.pagos_sin_aplicar = vendedores.reduce((s, v) => s + (v.pagos_sin_aplicar || 0), 0);
        const totalRow = document.createElement("tr");
        totalRow.style.borderTop = "2px solid #cbd5e1";
        totalRow.style.fontWeight = "600";
        totalRow.innerHTML = `
            <td></td>
            <td>TOTAL (${vendedores.length} vendedor${vendedores.length !== 1 ? 'es' : ''})</td>
            <td>${vendedores.reduce((s, v) => s + v.n_clientes, 0)}</td>
            <td></td>
            ${_celdasMontoVendedor(t, colorFmt)}
        `;
        tbody.appendChild(totalRow);
    }

    function applyReporteVendedorFilters() {
        if (!fullVendedoresCxc) return;
        const sortVal = document.getElementById("reporte-vendedor-sort")?.value || "saldo_desc";
        const searchVal = (document.getElementById("reporte-vendedor-search")?.value || "").toLowerCase().trim();
        const vencido = (v) => _TRAMOS.filter(k => k !== "vigente").reduce((s, k) => s + (v.antiguedad[k] || 0), 0);
        const filtered = fullVendedoresCxc
            .filter(v => !searchVal || (v.vendedor || "").toLowerCase().includes(searchVal))
            .sort((a, b) => {
                switch (sortVal) {
                    case "saldo_asc": return (a.saldo_priorizacion || 0) - (b.saldo_priorizacion || 0);
                    case "vencido_desc": return vencido(b) - vencido(a);
                    case "vendedor_asc": return (a.vendedor || "").localeCompare(b.vendedor || "");
                    default: return (b.saldo_priorizacion || 0) - (a.saldo_priorizacion || 0);
                }
            });
        renderReporteVendedorTable(filtered);
    }

    async function loadReporteCxcVendedor() {
        const tbody = document.getElementById("reporte-cxc-vendedor-table-body");
        if (!tbody) return;
        tbody.innerHTML = '<tr><td colspan="14" class="table-empty">Cargando cuentas por cobrar por vendedor...</td></tr>';
        try {
            const res = await fetch("/api/reporte-cxc-vendedor", { cache: "no-store" });
            if (!res.ok) throw new Error("HTTP " + res.status);
            const data = await res.json();
            fullVendedoresCxc = data.vendedores || [];
            const sortEl = document.getElementById("reporte-vendedor-sort");
            if (sortEl && !sortEl.dataset.listenerAttached) {
                sortEl.addEventListener("change", applyReporteVendedorFilters);
                sortEl.dataset.listenerAttached = "true";
            }
            const searchEl = document.getElementById("reporte-vendedor-search");
            if (searchEl && !searchEl.dataset.listenerAttached) {
                searchEl.addEventListener("input", applyReporteVendedorFilters);
                searchEl.dataset.listenerAttached = "true";
            }
            applyReporteVendedorFilters();
        } catch (err) {
            console.error("Error loading reporte-cxc-vendedor:", err);
            tbody.innerHTML = '<tr><td colspan="14" class="table-empty">Error al cargar cuentas por cobrar por vendedor.</td></tr>';
        }
    }
    window.loadReporteCxcVendedor = loadReporteCxcVendedor;

    function descargarReporteVendedorExcel(btn) {
        const params = new URLSearchParams();
        const sortVal = document.getElementById("reporte-vendedor-sort")?.value || "saldo_desc";
        const searchVal = (document.getElementById("reporte-vendedor-search")?.value || "").trim();
        if (sortVal !== "saldo_desc") params.set("sort", sortVal);
        if (searchVal) params.set("search", searchVal);
        return _descargarExcel("/api/reporte-cxc-vendedor/excel?" + params.toString(), btn, "cuentas_por_cobrar_por_vendedor");
    }
    window.descargarReporteVendedorExcel = descargarReporteVendedorExcel;

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
            if (bandeja2TableBody) bandeja2TableBody.innerHTML = '<tr><td colspan="9" class="table-empty">Cargando órdenes pendientes por nota de crédito...</td></tr>';
            if (bandeja3TableBody) bandeja3TableBody.innerHTML = '<tr><td colspan="7" class="table-empty">Cargando facturas pendientes por IVA...</td></tr>';
            if (bandejaEnProcesoDePagoTableBody) bandejaEnProcesoDePagoTableBody.innerHTML = '<tr><td colspan="10" class="table-empty">Cargando órdenes en proceso de pago...</td></tr>';

            const res = await fetch("/api/bandeja");
            if (res.ok) {
                const data = await res.json();
                const fmt = (v) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(v);

                // Handle legacy array format or structured 3 trays dict
                const tray1 = data.ordenes_por_facturar || (Array.isArray(data) ? data.filter(x => !x.facturada) : []);
                const tray2 = data.notas_credito_pendientes || (Array.isArray(data) ? data.filter(x => x.ncs_calculadas > 0) : []);
                const tray3 = data.iva_pendiente_agentes || [];
                const trayEnProceso = data.en_proceso_de_pago || [];

                // Render Tray 1
                if (bandeja1TableBody) {
                    if (tray1.length === 0) {
                        bandeja1TableBody.innerHTML = '<tr><td colspan="9" class="table-empty">No hay órdenes pendientes por facturar.</td></tr>';
                    } else {
                        bandeja1TableBody.innerHTML = "";
                        tray1.forEach(item => {
                            const row = document.createElement("tr");
                            const isAgent = item.wh_iva_agent ? `<span class="state-badge cierre" style="background:#e0f2fe;color:#0369a1">Agente (${item.wh_iva_rate || 75}%)</span>` : '<span class="state-badge">No</span>';
                            // Sin descuento pendiente no se muestra "$0.00
                            // (0%)": ese badge parecía anunciar un
                            // descuento de cero en vez de decir que no hay
                            // nada pendiente que aprobar, y confundía
                            // contra el modal de Detalle (item 4, pedido
                            // del usuario, 23-sep -- caso real S00743: ya
                            // cubierto, nada por aplicar).
                            const sinPendiente = !(item.descuento_pendiente_por_aplicar > 0);
                            const descText = sinPendiente
                                ? '<span style="opacity:0.5">—</span>'
                                : `${fmt(item.descuento_pendiente_por_aplicar)} (${((item.descuento_pendiente_por_aplicar / (item.orden_neto_odoo || 1)) * 100).toFixed(1)}%)`;

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
                                <td>${sinPendiente ? descText : `<strong style="color:#d97706">${descText}</strong>`}</td>
                                <td>${btnDetalleReglas(item)}</td>
                            `;
                            bandeja1TableBody.appendChild(row);
                        });
                    }
                }

                // Render Tray 2
                if (bandeja2TableBody) {
                    if (tray2.length === 0) {
                        bandeja2TableBody.innerHTML = '<tr><td colspan="9" class="table-empty">No hay órdenes pendientes por Nota de Crédito.</td></tr>';
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
                                <td>${btnDetalleReglas(item)} ${btnDescuentoNoOtorgado(item, 'loadBandeja')}</td>
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
                // septiembre de 2026: contenia a todas las demas (397 filas
                // sobre 796 ordenes vivas, cero propias) y se armaba con una
                // segunda llamada al arbol en otro endpoint, con otra
                // tolerancia.

                // Badges de conteo en las bandejas colapsables (3 y auditoría de precios)
                const badge3 = document.getElementById("bandeja3-count-badge");
                if (badge3) badge3.textContent = String(tray3.length);
                const badgeEnProceso = document.getElementById("bandeja-en-proceso-de-pago-count-badge");
                if (badgeEnProceso) badgeEnProceso.textContent = String(trayEnProceso.length);
            }
        } catch (err) {
            console.error("Error loading bandeja:", err);
            if (bandeja1TableBody) bandeja1TableBody.innerHTML = '<tr><td colspan="9" class="table-empty">Error al cargar bandeja 1.</td></tr>';
            if (bandeja2TableBody) bandeja2TableBody.innerHTML = '<tr><td colspan="8" class="table-empty">Error al cargar bandeja 2.</td></tr>';
            if (bandeja3TableBody) bandeja3TableBody.innerHTML = '<tr><td colspan="7" class="table-empty">Error al cargar bandeja 3.</td></tr>';
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

                // Attach Click Handlers to Interactive KPI Cards -- las tarjetas
                // viven en Reportes → Resumen; un clic abre la subpestaña Por Cliente
                // y filtra la tabla por esa antigüedad.
                document.querySelectorAll(".interactive-kpi").forEach(card => {
                    if (!card.dataset.listenerAttached) {
                        card.addEventListener("click", () => {
                            const targetVal = card.dataset.antiguedad;
                            const selectEl = document.getElementById("reporte-cliente-antiguedad-filter");
                            if (selectEl) {
                                selectEl.value = (selectEl.value === targetVal) ? "*" : targetVal;
                                if (typeof applyReporteClienteFilters === "function") applyReporteClienteFilters();
                            }
                            const currentPath = _normalizarModuloRuta(window.location.pathname);
                            if (currentPath !== "reportes") {
                                history.pushState(null, "", "/reportes?tab=cliente");
                                initCurrentPage();
                            } else if (typeof switchReporteSubtab === "function") {
                                switchReporteSubtab("cliente");
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
    // Reintento mientras el backend calcula (ver "calculando" en loadVentas).
    let ventasRecalculoTimer = null;

    async function loadVentas() {
        const tbody = document.getElementById("ventas-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="16" class="table-empty">Cargando reporte de ventas...</td></tr>';
            const res = await fetch("/api/ventas?t=" + Date.now(), { cache: "no-store" });
            if (!res.ok) {
                tbody.innerHTML = '<tr><td colspan="16" class="table-empty">Error al cargar el reporte de ventas.</td></tr>';
                return;
            }
            const data = await res.json();
            ventasData = data.items || [];

            // El backend avisa que hay un cálculo en vuelo. Sin caché previo
            // (tras un despliegue o un ciclo de sync con cambios) tarda
            // varios minutos, y antes se veía una tabla vacía: idéntico a
            // "no hay ventas". Se muestra el estado real y se reintenta solo.
            if (ventasRecalculoTimer) { clearTimeout(ventasRecalculoTimer); ventasRecalculoTimer = null; }
            if (data.calculando && ventasData.length === 0) {
                tbody.innerHTML = '<tr><td colspan="16" class="table-empty">'
                    + 'Calculando el reporte de ventas… Puede tardar varios minutos '
                    + 'después de una actualización del sistema. Esta página se '
                    + 'refresca sola cuando esté listo.</td></tr>';
                ventasRecalculoTimer = setTimeout(loadVentas, 30000);
                return;
            }
            if (data.calculando) {
                // Hay datos, pero son los del cálculo anterior.
                ventasRecalculoTimer = setTimeout(loadVentas, 30000);
            }

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
            tbody.innerHTML = '<tr><td colspan="16" class="table-empty">Error de red al cargar el reporte de ventas.</td></tr>';
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

    function descargarVentasExcel() {
        const vendedorVal = document.getElementById("ventas-vendedor-filter")?.value || "*";
        const soloAlertas = document.getElementById("ventas-solo-alertas")?.checked || false;
        const searchVal = (document.getElementById("ventas-search")?.value || "").trim();

        const params = new URLSearchParams();
        if (vendedorVal !== "*") params.set("vendedor", vendedorVal);
        if (soloAlertas) params.set("solo_alertas", "true");
        if (searchVal) params.set("search", searchVal);

        window.location.href = "/api/ventas/excel?" + params.toString();
    }
    window.descargarVentasExcel = descargarVentasExcel;

    function renderVentasTable(items) {
        const tbody = document.getElementById("ventas-table-body");
        if (!tbody) return;
        if (!items || items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="17" class="table-empty">No hay órdenes que coincidan con los filtros seleccionados.</td></tr>';
            return;
        }
        const fmt = (val) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(val || 0);
        tbody.innerHTML = "";
        items.forEach(item => {
            const row = document.createElement("tr");
            if (item.alerta) {
                row.style.background = "#fef2f2";
            }
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
            const facturadaCell = item.facturada
                ? '<span class="state-badge" style="background:#dcfce7;color:#15803d;">✓ Sí</span>'
                : '<span class="state-badge" style="background:#f1f5f9;color:#64748b;">Sin facturar</span>';
            // Saldo a favor: la empresa le debe al cliente (pagó de más, o
            // devolvió mercancía ya pagada). Gana sobre los demás estados --
            // es lo que hay que resolver con ese cliente.
            const favorCell = item.tiene_saldo_a_favor
                ? `<span class="state-badge" style="background:#ede9fe;color:#5b21b6;font-weight:600;" title="Plata del cliente que no está cobrando esta deuda: pagó de más, devolvió mercancía ya pagada, o tiene un pago sin asignar a ninguna orden. Lo 'por aplicar' lo asigna el FIFO cuando haya una orden contra la cual aplicarlo.">↩ A favor ${fmt((item.saldo_a_favor || 0) + (item.saldo_pendiente_por_aplicar || 0))}${(item.saldo_pendiente_por_aplicar > 0.05 ? ` · ${fmt(item.saldo_pendiente_por_aplicar)} por aplicar` : "")}</span>`
                : null;
            // Subtotal pagado y falta el IVA: ni "pagada" ni "pendiente" --
            // el cliente cumplió con la mercancía y debe el impuesto, por
            // pagar o por retener. Ver la rama SUBTOTAL_SIN_IVA del árbol.
            const ivaPendCell = item.iva_pendiente_sin_facturar
                ? '<span class="state-badge" style="background:#fef9c3;color:#854d0e;font-weight:600;" title="Pagó el subtotal; falta el IVA (por pagar o por retener)">Pagada — IVA pendiente</span>'
                : null;
            const pagadaCell = favorCell || ivaPendCell || (item.pagada
                ? `<span class="state-badge" style="background:#dcfce7;color:#15803d;font-weight:600;" title="${item.cxc_confirmado ? 'Pago conciliado en Odoo' : 'En proceso de pago -- vinculado, aún sin conciliar en Odoo'}">✓ ${item.cxc_confirmado ? 'Pagada' : 'En proceso'}</span>`
                : '<span class="state-badge" style="background:#fee2e2;color:#991b1b;font-weight:600;">✗ Pendiente</span>');

            // Item 6 (clasificación Comercial/Industrial, septiembre 2026):
            // "≥2 de 4" señales -- solo un badge informativo, con el
            // desglose en el title. No cambia ningún cálculo todavía.
            const ciDet = item.clasificacion_ci_detalle || {};
            const esIndustrial = item.clasificacion_comercial_industrial === "industrial";
            const ciTitle = `${ciDet.criterios_cumplidos || 0}/4 señales -- ` +
                `vendedor:${ciDet.vendedor_industrial ? 'sí' : 'no'}, cliente:${ciDet.cliente_industrial ? 'sí' : 'no'}, ` +
                `lista:${ciDet.lista_industrial ? 'sí' : 'no'}, volumen:${ciDet.volumen_industrial ? 'sí' : 'no'} ` +
                `(${(ciDet.litros_industriales || 0).toFixed(2)} L de ${(ciDet.umbral_litros || 0).toFixed(2)} L)`;
            const clasifCell = esIndustrial
                ? `<span class="state-badge" style="background:#ede9fe;color:#5b21b6;" title="${ciTitle}">🏭 Industrial</span>`
                : `<span class="state-badge" style="background:#f1f5f9;color:#64748b;" title="${ciTitle}">Comercial</span>`;

            const subtotalVal = item.venta_teorica_subtotal != null
                ? Number(item.venta_teorica_subtotal)
                : Number(item.venta_neta_real || 0) / 1.16;

            row.innerHTML = `
                <td><strong>${item.so_id}</strong></td>
                <td><small>${item.fecha_entrega ?? '—'}</small></td>
                <td>${item.cliente_nombre}</td>
                <td>${clasifCell}</td>
                <td><small title="${item.lista_nacimiento ?? ''}">${item.lista_nacimiento_label ?? '—'}</small></td>
                <td style="text-align:right">${item.dias_credito ?? 0}</td>
                <td style="text-align:right" title="Vence: ${item.fecha_vencimiento ?? '—'}">${
                    (item.saldo_cxc || 0) <= 0
                        ? '<span style="color:#059669">Pagada</span>'
                        : (item.dias_vencido || 0) > 0
                            ? `<strong style="color:#dc2626">${item.dias_vencido} d</strong>`
                            : '<span style="color:#059669">Vigente</span>'
                }</td>
                <td>${facturadaCell}</td>
                <td style="text-align:right">${fmt(subtotalVal)}</td>
                <td><strong>${fmt(item.venta_neta_real)}</strong></td>
                <td><strong>${fmt(item.total_facturado_neto)}</strong></td>
                <td><strong style="color:#2563eb;">${naVal(item.ves_neta_teorica_iva)}</strong></td>
                <td><strong style="color:#2563eb;">${naVal(item.usd_neta_teorica_iva)}</strong></td>
                <td>${pagadaCell}</td>
                <td><strong style="color:${item.saldo_cxc > 0 ? '#b91c1c' : '#059669'};">${fmt(item.saldo_cxc)}</strong></td>
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

    // Rediseño de Ventas (agosto 2026): la tabla principal se redujo a lo
    // esencial (orden/cliente/pago) -- todo lo demás que antes vivía como
    // columna propia (vendedor, teóricos brutos/descuentos por lista,
    // validación orden/factura, desglose de descuentos, N/C-N/D, montos
    // pagados por ruta, Diferencia/Alerta) se muestra aquí, en el resumen
    // del modal de Detalle. Lee del mismo `ventasData` que ya alimenta la
    // tabla (sin pegarle de nuevo al backend) -- el item ya trae todos
    // estos campos desde /api/ventas.
    function _renderDetalleOrdenResumen(item) {
        const cont = document.getElementById("modal-detalle-orden-resumen");
        if (!cont) return;
        if (!item) {
            cont.innerHTML = "";
            return;
        }
        const fmt = (val) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(val || 0);
        const naVal = (v) => v != null ? fmt(v) : '—';
        const pctTxt = (p) => p != null ? ` (${(p * 100).toFixed(1)}%)` : '';
        const descMontoPct = (monto, pct) => `${fmt(monto)}<small style="color:#64748b;">${pctTxt(pct)}</small>`;
        const valColor = (v) => v === 'ok' ? '#059669' : (v === 'discrepancia_menor' ? '#b45309' : '#b91c1c');
        const valLabel = (v) => v === 'ok' ? '✓ OK' : (v === 'discrepancia_menor' ? '~ Menor' : '⚠ Discrepancia');
        const difColor = item.diferencia > 0.05 ? "#b91c1c" : (item.diferencia < -0.05 ? "#0369a1" : "#059669");

        const campo = (label, valorHtml, title) => `
            <div style="min-width:150px;" ${title ? `title="${title}"` : ''}>
                <div style="font-size:0.72rem;color:#64748b;text-transform:uppercase;letter-spacing:0.03em;">${label}</div>
                <div style="font-size:0.9rem;">${valorHtml}</div>
            </div>`;

        // Saldo por fila -- MISMO target que usa cada estatus_pago_* en el
        // backend (ver _get_ventas_sync): los teóricos comparan contra su
        // propia neta+imp (sin restar Desc. Sistema, que solo aplica a lo
        // real); Real Orden/Real Factura restan Desc. Sistema antes de
        // comparar. Nunca deben coincidir entre sí -- son 4 referencias
        // distintas -- por diseño solo UNA de ellas alimenta el "Saldo CxC"
        // consolidado (ver estrella más abajo).
        const saldoTeoricoVes = item.ves_neta_teorica_iva != null
            ? Math.max(0, item.ves_neta_teorica_iva - (item.monto_pagado_bcv || 0)) : null;
        const saldoTeoricoUsd = item.usd_neta_teorica_iva != null
            ? Math.max(0, item.usd_neta_teorica_iva - (item.monto_pagado_usd || 0)) : null;
        const targetOrden = Math.max(0, (item.venta_neta_real || 0) - (item.descuento_aplicado_sistema || 0));
        const saldoRealOrden = Math.max(0, targetOrden - (item.monto_pagado_factura_odoo || 0));
        const targetFactura = Math.max(0, (item.total_facturado_neto || 0) - (item.descuento_aplicado_sistema || 0));
        const saldoRealFactura = item.facturada
            ? Math.max(0, targetFactura - (item.monto_pagado_factura_odoo || 0)) : null;

        // ¿Cuál de las 4 referencias es la que realmente alimenta el
        // "Saldo CxC" consolidado de la fila principal? MISMA lógica que
        // el backend (ver saldo_cxc en app.py): la lista con la que nació
        // la orden, o Real Factura/Real Orden como fallback si esa lista
        // no tiene teórico calculado todavía.
        const esUsdNacimiento = !!item.nacio_en_lista_usd;
        const netaNativaTeorica = esUsdNacimiento ? item.usd_neta_teorica_iva : item.ves_neta_teorica_iva;
        const refKey = netaNativaTeorica != null
            ? (esUsdNacimiento ? 'usd' : 'ves')
            : (item.facturada ? 'factura' : 'orden');
        const refBadge = (key) => key === refKey
            ? ' <span title="Esta es la referencia que alimenta el Saldo CxC consolidado de la orden" style="color:#b45309;">★</span>'
            : '';

        cont.innerHTML = `
            <div style="display:flex;flex-wrap:wrap;gap:0.9rem 1.5rem;padding:0.85rem 1rem;background:#f8fafc;border-radius:10px;border:1px solid hsl(var(--border-card));margin-bottom:0.25rem;">
                ${campo('Vendedor', item.vendedor || 'Sin Vendedor')}
                ${campo('Fecha de orden', item.fecha || '—')}
                ${campo('Lista aplicada', item.lista_aplicada_label || '—', item.lista_aplicada ?? '')}
                ${campo('Validación Orden', `<span style="color:${valColor(item.descuento_validacion_orden)};font-weight:600;">${valLabel(item.descuento_validacion_orden)}</span>`)}
                ${campo('Validación Factura', `<span style="color:${valColor(item.descuento_validacion_factura)};font-weight:600;">${valLabel(item.descuento_validacion_factura)}</span>`)}
                ${campo('Diferencia', `<strong style="color:${difColor};">${fmt(item.diferencia)}</strong>`, 'Venta teórica (lista aplicada) + IVA − Facturado Neto')}
                ${item.alerta ? campo('Alerta', '<span class="state-badge" style="background:#fee2e2;color:#991b1b;font-weight:700;">⚠️ Facturado de menos</span>') : ''}
            </div>
            <div style="overflow-x:auto;margin-bottom:0.25rem;">
                <table class="cxc-table" style="font-size:0.82rem;">
                    <thead>
                        <tr>
                            <th></th>
                            <th style="text-align:right">Bruta</th>
                            <th style="text-align:right">Desc. Motor</th>
                            <th style="text-align:right">Neta</th>
                            <th style="text-align:right">Neta + Imp.</th>
                            <th style="text-align:right">Pagado</th>
                            <th style="text-align:right">Saldo</th>
                            <th style="text-align:right">Estatus</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td>Teórico VES (BCV)${refBadge('ves')}</td>
                            <td style="text-align:right">${naVal(item.ves_bruta_teorica)}</td>
                            <td style="text-align:right">${descMontoPct(item.descuento_teorico_ves, item.descuento_teorico_ves_pct)}</td>
                            <td style="text-align:right">${naVal(item.ves_neta_teorica)}</td>
                            <td style="text-align:right"><strong>${naVal(item.ves_neta_teorica_iva)}</strong></td>
                            <td style="text-align:right">${fmt(item.monto_pagado_bcv)}</td>
                            <td style="text-align:right"><strong>${naVal(saldoTeoricoVes)}</strong></td>
                            <td style="text-align:right">${item.estatus_pago_teorico_ves ?? '—'}</td>
                        </tr>
                        <tr>
                            <td>Teórico USD (Binance)${refBadge('usd')}</td>
                            <td style="text-align:right">${naVal(item.usd_bruta_teorica)}</td>
                            <td style="text-align:right">${descMontoPct(item.descuento_teorico_usd, item.descuento_teorico_usd_pct)}</td>
                            <td style="text-align:right">${naVal(item.usd_neta_teorica)}</td>
                            <td style="text-align:right"><strong>${naVal(item.usd_neta_teorica_iva)}</strong></td>
                            <td style="text-align:right">${fmt(item.monto_pagado_usd)}</td>
                            <td style="text-align:right"><strong>${naVal(saldoTeoricoUsd)}</strong></td>
                            <td style="text-align:right">${item.estatus_pago_teorico_usd ?? '—'}</td>
                        </tr>
                        <tr>
                            <td>Real Orden${refBadge('orden')}</td>
                            <td style="text-align:right">${fmt(item.venta_bruta_real + (item.descuento_aplicado_orden || 0))}</td>
                            <td style="text-align:right">${descMontoPct(item.descuento_aplicado_orden, item.descuento_aplicado_orden_pct)}</td>
                            <td style="text-align:right">${fmt(item.venta_bruta_real)}</td>
                            <td style="text-align:right"><strong>${fmt(item.venta_neta_real)}</strong></td>
                            <td style="text-align:right">${fmt(item.monto_pagado_factura_odoo)}</td>
                            <td style="text-align:right"><strong>${fmt(saldoRealOrden)}</strong></td>
                            <td style="text-align:right">${item.estatus_pago_real_orden ?? '—'}</td>
                        </tr>
                        <tr>
                            <td>Real Factura${refBadge('factura')}</td>
                            <td style="text-align:right">${fmt(item.total_facturado_antes_impuestos + (item.descuento_aplicado_factura || 0))}</td>
                            <td style="text-align:right">${descMontoPct(item.descuento_aplicado_factura, item.descuento_aplicado_factura_pct)}</td>
                            <td style="text-align:right">${fmt(item.total_facturado_antes_impuestos)}</td>
                            <td style="text-align:right"><strong>${fmt(item.total_facturado_neto)}</strong></td>
                            <td style="text-align:right">${fmt(item.monto_pagado_factura_odoo)}</td>
                            <td style="text-align:right"><strong>${naVal(saldoRealFactura)}</strong></td>
                            <td style="text-align:right">${item.estatus_pago_real_factura ?? '—'}</td>
                        </tr>
                    </tbody>
                </table>
            </div>
            <div style="display:flex;flex-wrap:wrap;gap:0.9rem 1.5rem;padding:0.6rem 1rem;font-size:0.85rem;">
                ${campo('N/C Aplicada', fmt(item.total_nc_aplicada))}
                ${campo('N/D Aplicada', fmt(item.total_nd_aplicada))}
                ${campo('Desc. Pendiente', descMontoPct(item.descuento_pendiente_aplicar, item.descuento_pendiente_aplicar_pct)
                    + (item.descuento_pendiente_aplicar > 0.05 ? `<div style="margin-top:4px">${btnDescuentoNoOtorgado(item, 'loadVentas')}</div>` : ''))}
                ${campo('Desc. Sistema', descMontoPct(item.descuento_aplicado_sistema, item.descuento_aplicado_sistema_pct), item.descuento_aplicado_sistema_motivo ?? '')}
                ${campo('Orden Real c/ Desc. Teóricos', `${fmt(item.orden_real_subtotal_teoricos)}${item.orden_real_subtotal_teoricos_bloqueado ? ' 🔒' : ''}`, 'Cuánto debería costar la orden si se aplicaran TODOS los descuentos que calcula el motor (aplicados o no) -- valor hipotético, no el saldo real')}
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
        _renderDetalleOrdenResumen(ventasData.find(it => it.so_id === soId));

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
        _renderDetalleOrdenResumen(null);
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

    // Form submit handlers for new discount panels
    const recompraForm = document.getElementById("recompra-form");
    // El editor viejo de esta familia se retiro el 11-sep-2026: habia dos
    // maneras de guardar la misma regla. El formulario unico (POST
    // /api/config/regla) es el unico editor; el listado de abajo se queda.

    const prontoPagoForm = document.getElementById("pronto-pago-form");
    // El editor viejo de esta familia se retiro el 11-sep-2026: habia dos
    // maneras de guardar la misma regla. El formulario unico (POST
    // /api/config/regla) es el unico editor; el listado de abajo se queda.

    // NOTA: existía un segundo listener duplicado de promoForm.submit aquí
    // (mismos campos, mismo endpoint) -- cada submit creaba DOS reglas de
    // promoción. Eliminado; el listener real (más completo, con contador de
    // productos) está más abajo junto a loadExclusiones.

    const productoPromoForm = document.getElementById("producto-promo-form");
    // El editor viejo de esta familia se retiro el 11-sep-2026: habia dos
    // maneras de guardar la misma regla. El formulario unico (POST
    // /api/config/regla) es el unico editor; el listado de abajo se queda.

    const diferencialForm = document.getElementById("diferencial-form");
    // El editor viejo de esta familia se retiro el 11-sep-2026: habia dos
    // maneras de guardar la misma regla. El formulario unico (POST
    // /api/config/regla) es el unico editor; el listado de abajo se queda.

    // --- Tab 3: Configuration Panels ---
    async function loadConfigData() {
        const loaders = [
            loadTasasPromedios,
            loadProntoPago,
            loadRecompra,
            loadProductoPromo,
            loadDiferencial,
            loadDiasCredito,
            loadVendedores,
            loadClasificacionClientes,
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
                if (cfgMetaUmbralLitrosIndustrial) cfgMetaUmbralLitrosIndustrial.value = data.umbral_litros_clasificacion_industrial || 18.92;
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
                fallback_industrial_ajuste_pct: parseFloat(cfgMetaAjusteIndustrial?.value || "0.04"),
                umbral_litros_clasificacion_industrial: parseFloat(cfgMetaUmbralLitrosIndustrial?.value || "18.92")
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
                const bcvStr = d.tasa_bcv_actual ? `Bs. ${Number(d.tasa_bcv_actual).toFixed(2)}` : "-";
                const eurStr = d.tasa_bcv_euro ? `Bs. ${Number(d.tasa_bcv_euro).toFixed(2)}` : "-";
                const vigStr = d.tasa_binance_vigente ? `Bs. ${Number(d.tasa_binance_vigente).toFixed(2)}` : "-";
                const mStr = d.tasa_binance_manana ? `Bs. ${Number(d.tasa_binance_manana).toFixed(2)}` : "N/A";
                const tStr = d.tasa_binance_tarde ? `Bs. ${Number(d.tasa_binance_tarde).toFixed(2)}` : "N/A";
                const dStr = d.tasa_binance_diario ? `Bs. ${Number(d.tasa_binance_diario).toFixed(2)}` : "N/A";
                const diffStr = d.diferencial_bcv_binance_pct != null ? `${Number(d.diferencial_bcv_binance_pct).toFixed(2)}%` : "-";

                const setTxt = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
                setTxt("lbl-rate-bcv-actual", bcvStr);
                setTxt("lbl-rate-binance-vigente", vigStr);
                setTxt("lbl-rate-binance-manana", mStr);
                setTxt("lbl-rate-binance-tarde", tStr);
                setTxt("lbl-rate-binance-diario", dStr);
                setTxt("lbl-rate-diferencial-pct", diffStr);

                setTxt("inicio-rate-bcv-actual", bcvStr);
                setTxt("inicio-rate-bcv-euro", eurStr);
                setTxt("inicio-rate-binance-vigente", vigStr);
                setTxt("inicio-rate-binance-manana", mStr);
                setTxt("inicio-rate-binance-tarde", tStr);
                setTxt("inicio-rate-binance-diario", dStr);
                setTxt("inicio-rate-diferencial-pct", diffStr);
                if (d.ultima_actualizacion) {
                    setTxt("inicio-rate-actualizado", `· Act. ${String(d.ultima_actualizacion).slice(0, 16).replace("T", " ")}`);
                }

                const topBcv = document.getElementById("topbar-bcv");
                const topBin = document.getElementById("topbar-binance");
                if (topBcv && d.tasa_bcv_actual) topBcv.textContent = bcvStr;
                if (topBin && d.tasa_binance_vigente) topBin.textContent = vigStr;
            }
        } catch (err) {
            console.error("Error loading tasas promedios:", err);
        }
    }
    window.loadTasasPromedios = loadTasasPromedios;

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

    const diasCreditoForm = document.getElementById("dias-credito-form");
    // El editor viejo de esta familia se retiro el 11-sep-2026: habia dos
    // maneras de guardar la misma regla. El formulario unico (POST
    // /api/config/regla) es el unico editor; el listado de abajo se queda.

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

    // --- Vendedores / Clasificación de Clientes (item 6, clasificación
    // Comercial/Industrial) --------------------------------------------
    let _vendedoresCache = [];
    let _clasifClientesCache = [];

    function _renderVendedores(filtro) {
        const tbody = document.getElementById("vendedores-table-body");
        if (!tbody) return;
        const f = (filtro || "").trim().toLowerCase();
        const filas = _vendedoresCache.filter(v =>
            !f || v.vendedor_email.toLowerCase().includes(f) || (v.nombre || "").toLowerCase().includes(f)
        );
        if (filas.length === 0) {
            tbody.innerHTML = '<tr><td colspan="3" class="table-empty">Sin vendedores que coincidan.</td></tr>';
            return;
        }
        tbody.innerHTML = "";
        filas.forEach(v => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong>${v.vendedor_email}</strong></td>
                <td>${v.nombre || ''}</td>
                <td><label style="display:flex;align-items:center;gap:0.4rem;cursor:pointer;">
                    <input type="checkbox" data-vendedor="${v.vendedor_email}" ${v.es_industrial ? 'checked' : ''}>
                    Industrial
                </label></td>
            `;
            tbody.appendChild(tr);
        });
        tbody.querySelectorAll("input[data-vendedor]").forEach(cb => {
            cb.addEventListener("change", async () => {
                const email = cb.dataset.vendedor;
                const v = _vendedoresCache.find(x => x.vendedor_email === email);
                try {
                    const res = await fetch("/api/config/vendedores", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            vendedor_email: email,
                            nombre: v ? v.nombre : "",
                            es_industrial: cb.checked
                        })
                    });
                    if (!res.ok) throw new Error("HTTP " + res.status);
                    if (v) v.es_industrial = cb.checked;
                } catch (err) {
                    alert("❌ No se pudo actualizar " + email + ": " + err.message);
                    cb.checked = !cb.checked;
                }
            });
        });
    }

    async function loadVendedores() {
        const tbody = document.getElementById("vendedores-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="3" class="table-empty">Cargando vendedores...</td></tr>';
            const res = await fetch("/api/config/vendedores");
            if (!res.ok) throw new Error("HTTP " + res.status);
            const data = await res.json();
            _vendedoresCache = data.vendedores || [];
            const search = document.getElementById("vendedores-search");
            if (search && !search.dataset.listenerAttached) {
                search.addEventListener("input", () => _renderVendedores(search.value));
                search.dataset.listenerAttached = "true";
            }
            _renderVendedores(search ? search.value : "");
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="3" class="table-empty">Error al cargar vendedores.</td></tr>';
        }
    }
    window.loadVendedores = loadVendedores;

    function _renderClasificacionClientes(filtro) {
        const tbody = document.getElementById("clasificacion-clientes-table-body");
        if (!tbody) return;
        const f = (filtro || "").trim().toLowerCase();
        const filas = _clasifClientesCache.filter(c =>
            !f || (c.nombre || "").toLowerCase().includes(f) || c.cliente_id.toLowerCase().includes(f)
        );
        if (filas.length === 0) {
            tbody.innerHTML = '<tr><td colspan="3" class="table-empty">Sin clientes que coincidan.</td></tr>';
            return;
        }
        tbody.innerHTML = "";
        filas.slice(0, 200).forEach(c => {
            const tr = document.createElement("tr");
            const marcado = c.marcado_por ? `<small>${c.marcado_por}${c.motivo ? ' — ' + c.motivo : ''}</small>` : '';
            tr.innerHTML = `
                <td><strong>${c.nombre}</strong><div style="font-size:0.7rem;opacity:0.7">${c.cliente_id}</div></td>
                <td><input type="checkbox" data-cliente="${c.cliente_id}" ${c.es_industrial ? 'checked' : ''}></td>
                <td>${marcado}</td>
            `;
            tbody.appendChild(tr);
        });
        if (filas.length > 200) {
            const tr = document.createElement("tr");
            tr.innerHTML = `<td colspan="3" class="table-empty">... y ${filas.length - 200} más. Refina la búsqueda.</td>`;
            tbody.appendChild(tr);
        }
        tbody.querySelectorAll("input[data-cliente]").forEach(cb => {
            cb.addEventListener("change", async () => {
                const clienteId = cb.dataset.cliente;
                let motivo = "";
                if (cb.checked) {
                    motivo = prompt(`¿Por qué ${clienteId} es un cliente industrial?`, "") || "";
                    if (motivo === null) { cb.checked = false; return; }
                }
                try {
                    const res = await fetch("/api/config/clasificacion-cliente", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ cliente_id: clienteId, es_industrial: cb.checked, motivo })
                    });
                    if (!res.ok) throw new Error("HTTP " + res.status);
                    loadClasificacionClientes();
                } catch (err) {
                    alert("❌ No se pudo actualizar " + clienteId + ": " + err.message);
                    cb.checked = !cb.checked;
                }
            });
        });
    }

    async function loadClasificacionClientes() {
        const tbody = document.getElementById("clasificacion-clientes-table-body");
        if (!tbody) return;
        try {
            tbody.innerHTML = '<tr><td colspan="3" class="table-empty">Cargando clientes...</td></tr>';
            const res = await fetch("/api/config/clasificacion-clientes");
            if (!res.ok) throw new Error("HTTP " + res.status);
            const data = await res.json();
            _clasifClientesCache = data.clientes || [];
            const search = document.getElementById("clasificacion-clientes-search");
            if (search && !search.dataset.listenerAttached) {
                search.addEventListener("input", () => _renderClasificacionClientes(search.value));
                search.dataset.listenerAttached = "true";
            }
            _renderClasificacionClientes(search ? search.value : "");
        } catch (err) {
            tbody.innerHTML = '<tr><td colspan="3" class="table-empty">Error al cargar clientes.</td></tr>';
        }
    }
    window.loadClasificacionClientes = loadClasificacionClientes;

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
                        // Bug real (agosto 2026): usar p.nombre/p.id aquí guardaba
                        // el NOMBRE del producto o el id de product.template (Odoo)
                        // en la regla -- ninguno de los dos coincide con
                        // LineaOrden.producto (product.product, el id real que
                        // compara el motor de descuentos) ni con el que espera el
                        // backend, dejando la regla de obsequio sin efecto nunca.
                        // ref_interna (codigo/default_code) es estable y el
                        // backend lo resuelve al producto_id correcto al guardar.
                        opt.value = p.ref_interna || p.id;
                        opt.textContent = `[${p.ref_interna || 'N/A'}] ${p.nombre}`;
                        cfgPromoProductos.appendChild(opt);
                    });
                }

                // El mismo catálogo para el formulario único.
                const selRU = document.getElementById("ru-productos");
                if (selRU) {
                    selRU.innerHTML = '';
                    data.forEach(p => {
                        const opt = document.createElement("option");
                        opt.value = p.ref_interna || p.id;
                        opt.textContent = `[${p.ref_interna || 'N/A'}] ${p.nombre}`;
                        selRU.appendChild(opt);
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
                    const minQ = r.min_unidades !== undefined ? r.min_unidades : 0;
                    const maxQ = r.max_unidades !== undefined ? r.max_unidades : 999999;
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
    // El formulario de descuento por marca ya no existe en la pagina (su id
    // `descuento-form` no esta en index.html), asi que este manejador nunca se
    // enganchaba y su POST a /api/config/descuentos-marca era inalcanzable.
    // Retirado el 11-sep-2026 con el resto de los editores viejos.

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

    // Buscador y contador del selector de productos del formulario único
    // -- misma mecánica que el de la promoción: filtra las <option> ya
    // cargadas sin volver a pedirlas.
    const selProdRU = document.getElementById("ru-productos");
    const buscarProdRU = document.getElementById("ru-productos-buscar");
    if (selProdRU) {
        selProdRU.addEventListener("change", () => {
            const c = document.getElementById("ru-productos-count");
            if (c) c.textContent = selProdRU.selectedOptions.length;
        });
    }
    if (buscarProdRU && selProdRU) {
        buscarProdRU.addEventListener("input", () => {
            const q = buscarProdRU.value.trim().toLowerCase();
            Array.from(selProdRU.options).forEach(opt => {
                opt.hidden = q.length > 0 && !opt.textContent.toLowerCase().includes(q);
            });
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
    const CASCADA_PREFIJOS = ["rec", "pp", "vol", "promo", "prod", "ru"];

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
            const [rArbol, rPres, rMarcas] = await Promise.all([
                fetch("/api/odoo/categorias-arbol"),
                fetch("/api/odoo/presentaciones"),
                fetch("/api/odoo/marcas"),
            ]);
            if (rArbol.ok) window._categoriaArbol = await rArbol.json();
            if (rPres.ok) window._presentacionesOdoo = await rPres.json();
            // Las marcas del catálogo de Odoo (product.brand), para que el
            // formulario único no dependa de una lista escrita a mano.
            if (rMarcas.ok) window._marcasOdoo = await rMarcas.json();
        } catch (err) {
            console.error("Error cargando árbol de categorías/presentaciones:", err);
        }
        const cajaMarcas = document.querySelector(".m2m-ru-marca-box");
        if (cajaMarcas && Array.isArray(window._marcasOdoo) && window._marcasOdoo.length) {
            const previas = Array.from(document.querySelectorAll(".m2m-ru-marca:checked")).map(c => c.value);
            cajaMarcas.innerHTML = window._marcasOdoo.map(m =>
                `<label><input type="checkbox" class="m2m-ru-marca" value="${m}" ${previas.includes(m) ? "checked" : ""}> ${m}</label>`
            ).join(" ") + ` <label><input type="checkbox" class="m2m-ru-marca" value="*" ${previas.length && !previas.includes("*") ? "" : "checked"}> Todas (*)</label>`;
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

        // min_cajas/max_cajas se unificaron en min_unidades/max_unidades
        // (migración c9e1f2a3b4d5). Quedan litros_minimo y compra_minima como
        // respaldo: volumen conserva el primero y primera compra usa el
        // segundo como su criterio real.
        const minQ = r.min_unidades !== undefined ? r.min_unidades : (r.litros_minimo !== undefined ? r.litros_minimo : (r.compra_minima !== undefined ? r.compra_minima : 0));
        const maxQ = r.max_unidades !== undefined ? r.max_unidades : 999999;
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
            const minVal = parseFloat(r.min_unidades !== undefined ? r.min_unidades : r.litros_minimo || 0);
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
        setFieldValue("cfg-rec-min-cajas", r.min_unidades ?? 2);
        setFieldValue("cfg-rec-max-cajas", r.max_unidades ?? 4);
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
        if (cfgDescVolLitros) cfgDescVolLitros.value = r.litros_minimo ?? r.min_unidades ?? 0;
        setFieldValue("cfg-desc-vol-max", r.max_unidades ?? 999999);
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
        setFieldValue("cfg-promo-max", r.max_unidades ?? 999999);
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
        setFieldValue("cfg-prod-min", r.min_unidades ?? 0);
        setFieldValue("cfg-prod-max", r.max_unidades ?? 999999);
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
        setFieldValue("cfg-dif-nombre", r.descripcion || "Diferencial Cambiario");
        setFieldValue("cfg-dif-tipo-diferencial", r.tipo_diferencial || "fijo_35_ves_usd");
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
        "contado": "Pronto Pago", "contado_ves": "Pronto Pago en VES (camino BCV)", "volumen": "Volumen", "bcv_completo": "Diferencial BCV"
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
    // El editor viejo de esta familia se retiro el 11-sep-2026: habia dos
    // maneras de guardar la misma regla. El formulario unico (POST
    // /api/config/regla) es el unico editor; el listado de abajo se queda.

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
    // El editor viejo de esta familia se retiro el 11-sep-2026: habia dos
    // maneras de guardar la misma regla. El formulario unico (POST
    // /api/config/regla) es el unico editor; el listado de abajo se queda.

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
    // El editor viejo de esta familia se retiro el 11-sep-2026: habia dos
    // maneras de guardar la misma regla. El formulario unico (POST
    // /api/config/regla) es el unico editor; el listado de abajo se queda.

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

            // Aviso de modo degradado. `fuente` lo agrega el backend (Fase 5
            // del blindaje): antes existia como variable local y no se exponia,
            // asi que con Odoo caido la pantalla mostraba numeros de respaldo
            // sin decirlo.
            const avisoEl = document.getElementById("dashboard-degradado");
            if (avisoEl) {
                const f = data.fuente || {};
                if (f.degradado) {
                    const partes = [];
                    if (!f.odoo_respondio) {
                        partes.push("Odoo no respondio: los litros se calcularon localmente y el estado de las ordenes sale del espejo, no en vivo");
                    }
                    if (f.cobranza !== "odoo") {
                        partes.push("la cobranza se calculo con nuestra serie de tasas, no con el equivalente que estampa Odoo");
                    }
                    avisoEl.innerHTML = "<strong>Datos de respaldo.</strong> " + partes.join("; ") + ".";
                    avisoEl.hidden = false;
                } else {
                    avisoEl.hidden = true;
                }
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
        // Aviso de cobertura: tramos donde un grupo se queda sin lista de
        // referencia. Antes había que salir a buscarlos a mano -- el hueco
        // de USD entre el 1 y el 6 de abril lo encontró el usuario leyendo
        // la tabla.
        const pintarHuecos = (huecos) => {
            const caja = document.getElementById("pricelist-huecos");
            if (!caja) return;
            if (!huecos || !huecos.length) {
                caja.innerHTML = '<div style="color:#059669; font-size:0.85rem;">✓ Todos los períodos están cubiertos: no hay días sin lista de referencia.</div>';
                return;
            }
            caja.innerHTML = huecos.map(h => `
                <div style="background:#fef3c7; border-left:3px solid #b45309; padding:0.5rem 0.75rem; margin-bottom:0.4rem; font-size:0.85rem;">
                    <strong>${h.desde} a ${h.hasta}</strong> — sin referencia
                    <strong>${h.moneda}</strong> para ${h.categoria}.
                    <div style="opacity:.8; font-size:0.8rem;">${h.detalle}</div>
                </div>`).join('');
        };

        if (typeof cambiarTipoRegla === "function") cambiarTipoRegla();
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
            pintarHuecos(mapData.huecos_cobertura);
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
                        <td><input type="date" class="pm-desde" value="${fila.desde || ""}" style="padding:0.25rem;"></td>
                        <td><input type="date" class="pm-hasta" value="${fila.hasta || ""}" style="padding:0.25rem;" title="Vacío = sigue siendo la referencia"></td>
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
            body.innerHTML = '<tr><td colspan="7" class="table-empty" style="color:#ef4444;">Error al cargar listas.</td></tr>';
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
                // Sin estas dos, guardar el mapeo borraba las vigencias y
                // el teórico volvía a compararse contra las listas de hoy.
                desde: row.querySelector(".pm-desde")?.value || "",
                hasta: row.querySelector(".pm-hasta")?.value || "",
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

    // monto_sugerido_nativo: bug real (27-sep-2026, pago 1187) -- /api/vincular
    // exige el monto en la moneda PROPIA del pago (VES si el pago es en
    // bolívares), no en USD. monto_sugerido sigue siendo USD y solo se usa
    // para el texto de confirmación.
    window.aprobarSugerenciaIndividual = async function(pago_id, so_id, monto_sugerido, monto_sugerido_nativo) {
        if (!confirm(`¿Confirmar asociación de $${monto_sugerido.toFixed(2)} del Pago ${pago_id} a la Orden ${so_id}?`)) return;

        try {
            const res = await fetch('/api/vincular', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    pago_id: pago_id,
                    so_id: so_id,
                    monto_aplicado: monto_sugerido_nativo
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

    function descargarCobranzaExcel() {
        const params = new URLSearchParams();
        const selVend = (document.getElementById("cobranza-vendedor-filter") || {}).value || "*";
        const selEstado = (document.getElementById("cobranza-estado-filter") || {}).value || "*";
        const selMoneda = (document.getElementById("cobranza-moneda-filter") || {}).value || "*";
        const soloDup = (document.getElementById("cobranza-solo-duplicados") || {}).checked;
        const soloAlertas = (document.getElementById("cobranza-solo-alertas") || {}).checked;
        const search = ((document.getElementById("cobranza-search") || {}).value || "").trim();

        if (selVend !== "*") params.set("vendedor", selVend);
        if (selEstado !== "*") params.set("estado", selEstado);
        if (selMoneda !== "*") params.set("moneda", selMoneda);
        if (soloDup) params.set("solo_duplicados", "true");
        if (soloAlertas) params.set("solo_alertas", "true");
        if (search) params.set("search", search);

        window.location.href = "/api/cobranza/pagos/excel?" + params.toString();
    }
    window.descargarCobranzaExcel = descargarCobranzaExcel;

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
        const universoCob = cobranzaUnificadaData.filter(i => i.estado !== "cerrado_empresa");
        const _pagosUnicosSet = (arr) => new Set(arr.map(x => x.pago_id)).size;
        const _setCobKpi = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = String(v); };
        _setCobKpi("cob-kpi-total", _pagosUnicosSet(universoCob));
        _setCobKpi("cob-kpi-pendientes", _pagosUnicosSet(universoCob.filter(x => x.estado === "pendiente")));
        _setCobKpi("cob-kpi-vinculados", _pagosUnicosSet(universoCob.filter(x => x.estado === "vinculado_local")));
        _setCobKpi("cob-kpi-conciliados", _pagosUnicosSet(universoCob.filter(x => x.estado === "conciliado_odoo")));
        _setCobKpi("cob-kpi-recibidos", _pagosUnicosSet(universoCob.filter(x => x.recibido)));
        _setCobKpi("cob-kpi-duplicados", _pagosUnicosSet(universoCob.filter(x => x.posible_duplicado)));

        document.querySelectorAll(".cob-kpi").forEach(card => {
            if (card.dataset.wired) return;
            card.dataset.wired = "1";
            const handler = () => {
                const estK = card.getAttribute("data-cob-estado");
                const dupK = card.getAttribute("data-cob-duplicado");
                const recK = card.getAttribute("data-cob-recibido");
                const selE = document.getElementById("cobranza-estado-filter");
                const chkD = document.getElementById("cobranza-solo-duplicados");
                if (estK && selE) {
                    selE.value = estK;
                    if (chkD) chkD.checked = false;
                } else if (dupK && chkD) {
                    if (selE) selE.value = "*";
                    chkD.checked = true;
                } else if (recK && selE) {
                    selE.value = "*";
                    if (chkD) chkD.checked = false;
                }
                window._cobSoloRecibidoKpi = Boolean(recK);
                renderCobranzaUnificado();
            };
            card.addEventListener("click", handler);
            card.addEventListener("keydown", (e) => {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handler(); }
            });
        });

        let filtered = universoCob;
        if (window._cobSoloRecibidoKpi) filtered = filtered.filter(i => i.recibido);
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

        // Una sola fila por pago (pedido del usuario, 30-sep-2026): un pago repartido
        // entre varias órdenes aparece una vez, con sus órdenes dentro de la fila.
        const grupos = [];
        const idxGrupo = {};
        filtered.forEach(i => {
            if (idxGrupo[i.pago_id] === undefined) { idxGrupo[i.pago_id] = grupos.length; grupos.push([]); }
            grupos[idxGrupo[i.pago_id]].push(i);
        });
        if (countBadge) countBadge.textContent = `${grupos.length} Pagos`;

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
        const estadoBadge = {
            pendiente: '<span class="badge" style="background:#fef3c7; color:#92400e; font-weight:700;">⏳ Pendiente</span>',
            vinculado_local: '<span class="badge" style="background:#dbeafe; color:#1e40af; font-weight:700;">🔗 Vinculado</span>',
            conciliado_odoo: '<span class="badge" style="background:#dcfce7; color:#166534; font-weight:700;">✓ Conciliado (Odoo)</span>',
        };

        tbody.innerHTML = grupos.map(grupo => {
            const item = grupo[0];
            const total = grupo.length;
            const origPill = item.origen_sistema === "cxc"
                ? ' <span class="badge-origen-cxc" title="Registrado vía CxC / Bot">CxC</span>'
                : ' <span class="badge-origen-odoo" title="Creado directo en Odoo">Odoo</span>';
            const numOdooLbl = item.numero_pago_odoo || item.odoo_numero_espejo || "";
            const enlaceOdoo = item.odoo_url
                ? `<a href="${item.odoo_url}" target="_blank" rel="noopener" title="Abrir pago #${item.pago_id} en Odoo" style="color:#0284c7; text-decoration:underline;">${numOdooLbl || ('#' + item.pago_id)}</a>`
                : (numOdooLbl ? `<span>${numOdooLbl}</span>` : '');
            const pagoCell = `<strong>${item.pago_id}</strong>${origPill}`
                + (enlaceOdoo ? `<br><small style="color:#64748b;">Odoo: ${enlaceOdoo}</small>` : '')
                + (total > 1 ? `<br><small style="color:#64748b;" title="Este pago cubre ${total} documentos -- no está duplicado">cubre ${total} ${grupo.every(g => g.so_id) ? 'órdenes' : 'documentos'}</small>` : '');

            const montoCell = item.moneda_pago === "VES"
                ? `Bs. ${Number(item.monto_pago_original).toLocaleString('es-VE', { minimumFractionDigits: 2 })}`
                : fmt(item.monto_pago_original);
            const tasasCell = `
                <span style="font-size:0.72rem; color:#2563eb; display:block;">BCV: ${fmt(item.monto_pago_bcv_usd)}</span>
                <span style="font-size:0.72rem; color:#d97706; display:block;">Binance: ${fmt(item.monto_pago_binance_usd)}</span>
                <span style="font-size:0.72rem; color:#7c3aed; display:block;">EUR: ${fmt(item.monto_pago_eur)}</span>`;

            const estadoCorto = {
                pendiente: '⏳',
                vinculado_local: '🔗',
                conciliado_odoo: '✓',
            };
            // Sin orden pero conciliado en Odoo contra una factura: es una factura
            // sin orden de venta (flete, servicio...). Se muestra la factura, no
            // "Sin orden" -- eso queda para el pago que de verdad no tiene destino.
            const esFacturaSinOrden = (it) => !it.so_id && it.estado === 'conciliado_odoo'
                && it.factura_id && it.factura_id !== 'N/A';
            const ordenCell = grupo.map(it => it.so_id
                ? `<div style="margin-bottom:3px;"><span class="badge blue">${it.so_id}</span>`
                    + (total > 1 ? ` <small title="${(estadoBadge[it.estado] || it.estado).replace(/<[^>]*>/g, '')}">${estadoCorto[it.estado] || ''}</small>` : '')
                    + (it.factura_id ? `<br><small>${it.factura_id}</small>` : '') + `</div>`
                : esFacturaSinOrden(it)
                    ? `<div style="margin-bottom:3px;"><span class="badge" style="background:#f1f5f9; color:#334155;" title="Factura sin orden de venta">Fact. ${it.factura_id}</span>`
                        + (total > 1 ? ` <small title="Conciliado (Odoo)">✓</small>` : '')
                        + `<br><small style="color:#64748b;">sin orden</small></div>`
                    : `<span style="color:#94a3b8; font-size:0.8rem;">Sin orden</span>`).join('');

            const regNom = item.registrado_por_nombre || item.vendedor || 'Sin Vendedor';
            const recPorNom = item.aprobado_o_recibido_por_nombre || item.recibido_por || '';
            const vendedorCell = `<small><strong>Reg:</strong> ${regNom}</small>`
                + (recPorNom ? `<br><small style="color:#0369a1;"><strong>Rec:</strong> ${recPorNom}</small>` : '');

            const alertas = [];
            if (grupo.some(g => g.posible_duplicado)) alertas.push(`<span title="Mismo cliente/monto/moneda/método/fecha que: ${(item.duplicado_de || []).join(', ')}" style="font-size:0.68rem; color:#b91c1c; font-weight:700;">⚠️ Posible duplicado</span>`);
            // Evento pasado, no estado actual: sin la fecha se leia como
            // una contradiccion junto a "Pendiente". No lo es -- Odoo pudo
            // haber aplicado parte del pago a otra orden y el resto seguir
            // sin conciliar.
            if (grupo.some(g => g.reasignado_por_odoo)) alertas.push(`<span title="${item.reasignado_detalle || ''}" style="font-size:0.68rem; color:#0369a1; font-weight:600;">🔄 Odoo lo movió${item.reasignado_fecha ? ` el ${item.reasignado_fecha}` : ''}</span>`);
            const alertasCell = alertas.length ? alertas.join('<br>') : '<span style="color:#94a3b8;">-</span>';

            const reciboCell = item.recibido
                ? `<span class="semaphore green" title="Entregado a Administración">✓ Recibido</span>`
                : `<span class="semaphore yellow">⏳ Pendiente</span>`;

            let accionesExtra = '';
            let checkboxCell = '<td></td>';
            for (const it of grupo.filter(g => g.estado === "pendiente")) {
                const tieneSugerencia = !!it.so_id;
                const sugIdx = idxByPagoSug[it.sugerencia_id];
                const etiquetaOrden = total > 1 && it.so_id ? ` ${it.so_id}` : '';
                if (tieneSugerencia && !it.posible_duplicado && sugIdx !== undefined) {
                    // Aprobación masiva retirada (pedido del usuario,
                    // 2026-08-22): Auto-FIFO (daemon) ya vincula estas
                    // filas automáticamente cada ciclo -- el botón "Bulk"
                    // solo aprobaba EXACTAMENTE este mismo subconjunto
                    // (con so_id, no duplicado), nunca los casos que sí
                    // requieren juicio humano (duplicados, sin orden). El
                    // botón individual "✓ Vincular" sigue para quien no
                    // quiera esperar al siguiente ciclo.
                    accionesExtra += `<button class="btn btn-sm btn-primary" onclick="aprobarSugerenciaIndividual('${it.pago_id}', '${it.so_id}', ${it.monto_sugerido}, ${it.monto_sugerido_nativo})" style="padding:3px 8px; font-size:0.75rem;">✓ Vincular${etiquetaOrden}</button>
                        <button class="btn btn-sm btn-secondary" onclick="abrirModalVincularManual(${sugIdx})" style="padding:3px 8px; font-size:0.72rem;">✏️ Otra orden${etiquetaOrden}</button>`;
                } else if (sugIdx !== undefined) {
                    accionesExtra += `<button class="btn btn-sm btn-secondary" onclick="abrirModalVincularManual(${sugIdx})" style="padding:3px 8px; font-size:0.75rem;">🔗 Vincular manualmente${etiquetaOrden}</button>`
                        + (!tieneSugerencia ? `<button class="btn btn-sm btn-secondary" onclick="cerrarPagoHuerfano('${it.pago_id}')" style="padding:3px 8px; font-size:0.7rem; color:#92400e;">💰 Cerrar a favor de la empresa</button>` : '');
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
                    <td>${[...new Set(grupo.map(g => g.estado))].map(e => estadoBadge[e] || e).join('<br>')}</td>
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

        const numOdooModal = base.numero_pago_odoo || base.odoo_numero_espejo || '';
        const odooModalVal = base.odoo_url
            ? `<a href="${base.odoo_url}" target="_blank" rel="noopener" style="color:#0284c7;">${numOdooModal || ('Pago #' + base.pago_id)} (#${base.pago_id}) ↗</a>`
            : (numOdooModal || '-');

        let html = `<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px,1fr)); gap:0.5rem 1rem; margin-bottom:1rem;">
            ${campo('🆔', 'Pago ID', base.pago_id)}
            ${campo('🧾', 'N° / Enlace Odoo', odooModalVal)}
            ${campo('📅', 'Fecha', base.pago_fecha)}
            ${campo('👤', 'Cliente', base.cliente_nombre)}
            ${campo('🧑‍💼', 'Registró / Vendedor', base.registrado_por_nombre || base.vendedor)}
            ${campo('🤝', 'Aprobó / Recibió', base.aprobado_o_recibido_por_nombre || base.confirmado_por || base.recibido_por || '-')}
            ${campo('💳', 'Método de Pago', base.metodo_pago)}
            ${campo('💰', 'Monto Original', (base.moneda_pago === 'VES' ? 'Bs. ' + Number(base.monto_pago_original).toLocaleString('es-VE', {minimumFractionDigits:2}) : fmt(base.monto_pago_original)))}
            ${campo('📌', 'Estado', base.estado)}
            ${campo('📥', 'Origen', base.origen)}
            ${campo('🧾', 'Recibido', base.recibido ? `Sí (${base.numero_recibido || ''}, ${base.fecha_recibido || ''}, ${base.recibido_por || ''})` : 'No')}
        </div>`;

        html += `<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(150px,1fr)); gap:0.75rem; margin-bottom:1rem;">
            ${tarjetaTasa('🏦', 'Tasa BCV', '#0369a1', base.tasa_bcv, base.monto_pago_bcv_usd)}
            ${tarjetaTasa('🟡', 'Tasa Binance', '#b45309', base.tasa_binance, base.monto_pago_binance_usd)}
            ${tarjetaTasa('💶', 'Tasa BCV-EUR', '#7c3aed', base.tasa_bcv_eur, base.monto_pago_eur)}
        </div>`;

        if (base.reasignado_por_odoo) {
            html += `<div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:8px; padding:0.75rem; margin-bottom:1rem; font-size:0.85rem; color:#1e40af;">
                🔄 <strong>Odoo movió este pago${base.reasignado_fecha ? ` el ${base.reasignado_fecha}` : ''}.</strong>
                ${base.reasignado_detalle || ''}
                <div style="margin-top:0.4rem; opacity:0.85;">Es el registro de un movimiento pasado, no el estado de hoy: el estado actual de cada vinculación se ve más abajo.</div>
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
    
    // --- Auditoría consolidada (9-oct-2026) --------------------------------------------
    // Una fila por orden con TODOS sus hallazgos (antes ~12 paneles listaban la misma realidad
    // varias veces), y los pagos y las entregas en sus propias tablas. Fuente única:
    // /api/auditoria/consolidada.
    let audData = null;
    const AUD_COLOR = {
        PRECIO: ["#fee2e2", "#b91c1c"], FACTURA: ["#ffedd5", "#c2410c"],
        DESCUENTO: ["#fef3c7", "#92400e"], NC: ["#ede9fe", "#6d28d9"],
        ENTREGA: ["#dbeafe", "#1d4ed8"], SALDO: ["#e2e8f0", "#475569"],
    };
    const audFmt = (v) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(v || 0);
    const audEsc = (str) => (str === null || str === undefined) ? '' : String(str)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#039;");

    async function loadAuditoria() {
        const body = document.getElementById("aud-ordenes-body");
        if (body) body.innerHTML = '<tr><td colspan="6" class="table-empty">Cargando auditoría...</td></tr>';
        const minimo = parseFloat(document.getElementById("aud-monto-minimo")?.value || "25");
        try {
            const res = await fetch(
                `/api/auditoria/consolidada?monto_minimo=${isNaN(minimo) ? 25 : minimo}`,
                { cache: "no-store" });
            if (!res.ok) {
                if (body) body.innerHTML = '<tr><td colspan="6" class="table-empty">Error al cargar la auditoría.</td></tr>';
                return;
            }
            audData = await res.json();
            renderAuditoria();
        } catch (err) {
            console.error("Error al cargar la auditoría:", err);
            if (body) body.innerHTML = '<tr><td colspan="6" class="table-empty">Error de red al cargar la auditoría.</td></tr>';
        }
    }
    window.loadAuditoria = loadAuditoria;

    function renderAuditoria() {
        if (!audData) return;
        const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
        const r = audData.resumen || {};

        // --- Órdenes (filtros del lado del cliente) ---
        const activos = new Set(Array.from(document.querySelectorAll("[data-aud-codigo]:checked"))
            .map(c => c.dataset.audCodigo));
        const q = (document.getElementById("aud-ordenes-buscar")?.value || "").trim().toLowerCase();
        const ordenes = (audData.ordenes || []).map(o => Object.assign({}, o, {
            visibles: o.hallazgos.filter(h => activos.has(h.codigo)),
        })).filter(o => o.visibles.length > 0 && (!q ||
            [o.so_id, o.cliente, o.vendedor].some(t => String(t || "").toLowerCase().includes(q))));
        const body = document.getElementById("aud-ordenes-body");
        if (body) {
            if (ordenes.length === 0) {
                body.innerHTML = '<tr><td colspan="6" class="table-empty" style="color:#059669">✅ Nada por revisar con estos filtros.</td></tr>';
            } else {
                body.innerHTML = ordenes.slice(0, 400).map(o => {
                    const chips = o.visibles.map(h => {
                        const [bg, fg] = AUD_COLOR[h.codigo] || ["#f1f5f9", "#334155"];
                        const monto = h.monto > 0 ? ` ${audFmt(h.monto)}` : '';
                        return `<span class="state-badge" style="background:${bg}; color:${fg}; font-weight:600; margin:1px; display:inline-block;" title="Fuentes: ${audEsc((h.fuentes || []).join(', '))}">${audEsc(h.etiqueta)}${monto}</span>`;
                    }).join(' ');
                    const detalle = o.visibles.map(h =>
                        `<div><small><strong>${audEsc(h.etiqueta)}:</strong> ${audEsc((h.detalle || '').substring(0, 160))}</small></div>`).join('');
                    const aceptables = o.visibles.flatMap(h => h.aceptar || []).filter(a => a && a.discrepancia_id);
                    const tieneNC = o.visibles.some(h => h.codigo === 'NC');
                    const acciones = [
                        aceptables.length ? `<button class="btn btn-secondary" onclick="aceptarHallazgos('${encodeURIComponent(JSON.stringify(aceptables.map(audPayloadAceptar)))}')" style="padding:0.25rem 0.6rem; font-size:0.75rem;">Aceptar${aceptables.length > 1 ? ' (' + aceptables.length + ')' : ''}</button>` : '',
                        tieneNC ? `<button class="btn btn-secondary" onclick="decisionComercial('${audEsc(o.so_id)}')" title="Documenta que la NC difiere del motor a propósito" style="padding:0.25rem 0.6rem; font-size:0.75rem;">Decisión comercial</button>` : '',
                    ].filter(Boolean).join(' ');
                    return `<tr>
                        <td><strong>${audEsc(o.so_id)}</strong><br><small style="color:#64748b;">${audEsc(o.fecha)}</small></td>
                        <td>${audEsc(o.cliente)}</td>
                        <td><small>${audEsc(o.vendedor)}</small></td>
                        <td>${chips}</td>
                        <td>${detalle}</td>
                        <td>${acciones}</td>
                    </tr>`;
                }).join('') + (ordenes.length > 400 ? `<tr><td colspan="6" class="table-empty">Mostrando 400 de ${ordenes.length}: afina con los filtros o la búsqueda.</td></tr>` : '');
            }
        }
        setTxt("aud-ordenes-conteo", `${ordenes.length} de ${r.ordenes || 0} órdenes`);

        // --- Pagos y vinculaciones ---
        const pagos = audData.pagos || [];
        const bodyPagos = document.getElementById("aud-pagos-body");
        if (bodyPagos) {
            bodyPagos.innerHTML = pagos.length === 0
                ? '<tr><td colspan="7" class="table-empty" style="color:#059669">✅ Sin pagos ni vinculaciones por revisar.</td></tr>'
                : pagos.map(p => {
                    const monto = p.moneda === 'VES'
                        ? 'Bs. ' + Number(p.monto || 0).toLocaleString('es-VE', { minimumFractionDigits: 2 })
                        : (p.monto ? audFmt(p.monto) : '—');
                    return `<tr>
                        <td><span class="state-badge">${audEsc(p.tipo)}</span></td>
                        <td><strong>${audEsc(p.pago)}</strong></td>
                        <td><small>${audEsc(p.referencia)}</small></td>
                        <td>${audEsc(p.cliente)}</td>
                        <td><small>${audEsc((p.detalle || '').substring(0, 160))}</small></td>
                        <td>${monto}</td>
                        <td>${p.aceptar ? btnAceptarDiscrepancia(p.aceptar) : ''}</td>
                    </tr>`;
                }).join('');
        }

        // --- Entregas y devoluciones ---
        const entregas = audData.entregas || [];
        const bodyEntregas = document.getElementById("aud-entregas-body");
        if (bodyEntregas) {
            bodyEntregas.innerHTML = entregas.length === 0
                ? '<tr><td colspan="5" class="table-empty" style="color:#059669">✅ Sin entregas ni devoluciones por revisar.</td></tr>'
                : entregas.map(e => `<tr>
                    <td><span class="state-badge">${audEsc(e.tipo)}</span></td>
                    <td><strong>${audEsc(e.so_id)}</strong></td>
                    <td><small>${audEsc((e.detalle || '').substring(0, 200))}</small></td>
                    <td>${e.monto ? audFmt(e.monto) : '—'}</td>
                    <td>${e.aceptar ? btnAceptarDiscrepancia(e.aceptar) : ''}</td>
                </tr>`).join('');
        }

        // --- Aceptadas ---
        const aceptadas = audData.aceptadas || [];
        const bodyAceptadas = document.getElementById("discrepancias-aceptadas-table-body");
        if (bodyAceptadas) {
            bodyAceptadas.innerHTML = aceptadas.length === 0
                ? '<tr><td colspan="9" class="table-empty">No hay discrepancias aceptadas en el historial.</td></tr>'
                : aceptadas.map(a => `<tr>
                    <td><small><code>${audEsc(a.discrepancia_id || '')}</code></small></td>
                    <td><strong>${audEsc(a.so_id || '')}</strong></td>
                    <td>${audEsc(a.factura_id || 'N/A')}</td>
                    <td><span class="state-badge">${audEsc(a.tipo_discrepancia || '')}</span></td>
                    <td><small>${audEsc(a.detalle || a.detalle_aceptado || 'Sin detalle registrado')}</small></td>
                    <td><small>${audEsc(a.motivo_aceptacion || '')}</small></td>
                    <td><small><strong>${audEsc(a.aprobado_por || a.aceptada_por || '')}</strong></small></td>
                    <td><small>${(a.timestamp_aprobacion || a.aceptada_en) ? String(a.timestamp_aprobacion || a.aceptada_en).substring(0, 16).replace('T', ' ') : '-'}</small></td>
                    <td><small><code style="font-size:0.68rem; color:#64748b;">${audEsc(a.huella || '')}</code></small></td>
                </tr>`).join('');
        }

        // --- KPIs y contadores ---
        const montoOrdenes = (audData.ordenes || []).reduce((a, o) => a + (o.monto_mayor || 0), 0);
        const porCodigo = r.ordenes_por_codigo || {};
        setTxt("audit-kpi-ordenes", String(r.ordenes || 0));
        setTxt("audit-kpi-ordenes-sub", Object.keys(porCodigo).length
            ? Object.entries(porCodigo).map(([c, n]) => `${(AUD_COLOR[c] ? c : c)} ${n}`).join(' · ') : "");
        setTxt("audit-kpi-pagos", String(r.pagos || 0));
        setTxt("audit-kpi-pagos-sub", "residuales, ajustes, sobreaplicadas, propuestas");
        setTxt("audit-kpi-entregas", String(r.entregas || 0));
        setTxt("audit-kpi-entregas-sub", "faltantes de devolución y entregas sin fecha");
        const reabiertas = [].concat(pagos.map(p => p.aceptar), entregas.map(e => e.aceptar))
            .filter(x => x && x.reabierta).length;
        setTxt("audit-kpi-aceptadas", String(r.aceptadas || 0));
        setTxt("audit-kpi-aceptadas-sub", reabiertas > 0 ? `${reabiertas} reabiertas: cambiaron los montos` : "ninguna reabierta");
        setTxt("auditoria-subtab-badge-ordenes", String(r.ordenes || 0));
        setTxt("auditoria-subtab-badge-pagos", String(r.pagos || 0));
        setTxt("auditoria-subtab-badge-entregas", String(r.entregas || 0));
        setTxt("auditoria-subtab-badge-historico", String(r.aceptadas || 0));
    }

    function audPayloadAceptar(item) {
        return {
            discrepancia_id: item.discrepancia_id,
            so_id: String(item.so_id || ''),
            factura_id: String(item.factura_id || 'N/A'),
            tipo_discrepancia: String(item.tipo_discrepancia || ''),
            huella: String(item.huella || ''),
            detalle: String(item.detalle || item.causa_probable || '')
        };
    }

    // Acepta de una vez todas las filas aceptables de una orden: pide el motivo UNA vez.
    window.aceptarHallazgos = async function(encoded) {
        const filas = JSON.parse(decodeURIComponent(encoded));
        const motivo = prompt(
            `Motivo para aceptar ${filas.length} hallazgo(s) de ${filas[0].so_id}:\n\n` +
            `Salen de la lista y pasan al historial. Si los montos que los originaron cambian, vuelven a aparecer.`,
            "Revisado y aceptado en auditoría");
        if (motivo === null) return;
        const quien = prompt("¿Quién lo acepta?", "Dirección / Auditor");
        if (quien === null) return;
        try {
            for (const f of filas) {
                const res = await fetch("/api/auditoria/aceptar-discrepancia", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(Object.assign({}, f, { motivo_aceptacion: motivo, aprobado_por: quien }))
                });
                if (!res.ok) { alert("❌ Error al aceptar un hallazgo."); break; }
            }
            loadAuditoria();
        } catch (err) {
            console.error("Error aceptando hallazgos:", err);
        }
    };

    // Documenta que la NC de la orden difiere del motor a propósito (no cambia ningún monto).
    window.decisionComercial = async function(soId) {
        const motivo = prompt(`Decisión comercial sobre ${soId}: ¿por qué la NC difiere del motor?`, "");
        if (motivo === null || !motivo.trim()) return;
        try {
            const res = await fetch("/api/ventas/decision-comercial", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ so_id: soId, motivo: motivo.trim() })
            });
            if (res.ok) loadAuditoria(); else alert("❌ No se pudo registrar la decisión comercial.");
        } catch (err) {
            console.error("Error registrando la decisión comercial:", err);
        }
    };

    document.querySelectorAll("[data-aud-codigo]").forEach(el => {
        el.addEventListener("change", () => renderAuditoria());
    });
    document.getElementById("aud-ordenes-buscar")?.addEventListener("input", () => renderAuditoria());
    document.getElementById("aud-monto-minimo")?.addEventListener("change", () => loadAuditoria());

    // Balance de comprobación: enfrenta dos páginas por partida y dice si
    // cuadran. Lo pidió el usuario para poder auditar que todo cierre sin
    // ir comparando pantallas a mano.
    async function loadBalanceComprobacion() {
        const body = document.getElementById("balance-comprobacion-body");
        const resumen = document.getElementById("balance-resumen");
        if (!body) return;
        const fmt = (v) => new Intl.NumberFormat('es-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v || 0);
        const esc = (t) => String(t ?? '').replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        try {
            const res = await fetch("/api/auditoria/balance-comprobacion?t=" + Date.now(), { cache: "no-store" });
            if (!res.ok) {
                body.innerHTML = '<tr><td colspan="6" class="table-empty">No se pudo calcular el balance.</td></tr>';
                return;
            }
            const data = await res.json();
            // El balance se abstiene cuando Ventas todavía se está
            // recalculando (tras cada despliegue tarda varios minutos). Sin
            // esto la tabla mostraba 17 partidas en verde comparando 0,00
            // contra 0,00 y dos en rojo con TODAS sus filas: un falso verde
            // y un falso rojo a la vez.
            if (data.evaluable === false) {
                if (resumen) {
                    resumen.textContent = "Todavía no se puede calcular";
                    resumen.style.color = "#b45309";
                }
                body.innerHTML = `<tr><td colspan="6" class="table-empty">${esc(data.motivo || "Sin datos suficientes para cuadrar.")}</td></tr>`;
                return;
            }
            if (resumen) {
                const ok = data.descuadres === 0;
                resumen.textContent = ok
                    ? `✓ ${data.cuadran} de ${data.total} partidas cuadran`
                    : `${data.descuadres} de ${data.total} no cuadran`;
                resumen.style.color = ok ? "#059669" : "#dc2626";
            }
            body.innerHTML = (data.partidas || []).map(p => `
                <tr style="${p.cuadra ? '' : 'background:#fef2f2;'}">
                    <td>${p.cuadra ? '<span style="color:#059669">✓</span>' : '<span style="color:#dc2626">✕</span>'}
                        <strong>${esc(p.concepto)}</strong>
                        ${p.nota ? `<div style="font-size:.75rem;opacity:.75">${esc(p.nota)}</div>` : ''}</td>
                    <td><small>${esc(p.izquierda.vista)}</small></td>
                    <td style="text-align:right">${fmt(p.izquierda.valor)}</td>
                    <td><small>${esc(p.derecha.vista)}</small></td>
                    <td style="text-align:right">${fmt(p.derecha.valor)}</td>
                    <td style="text-align:right; font-weight:600; color:${p.cuadra ? '#64748b' : '#dc2626'}">${fmt(p.diferencia)}</td>
                </tr>`).join('');
        } catch (err) {
            body.innerHTML = '<tr><td colspan="6" class="table-empty">Error de red al calcular el balance.</td></tr>';
            console.error("Error en el balance de comprobación:", err);
        }
    }
    window.loadBalanceComprobacion = loadBalanceComprobacion;


    // Sub-navegación de la página Auditoría (Discrepancias / Descuentos y
    // NCs / Alertas de Venta / Histórico Conforme) -- agrupa lo que antes
    // eran 7 tablas en un solo scroll largo, sin eliminar ninguna.
    let _audClientesCargado = false;
    let _audIntegracionesCargado = false;

    window.switchAuditoriaSubtab = function(name) {
        document.querySelectorAll(".subtab-btn[data-subtab]").forEach(btn => {
            btn.classList.toggle("active", btn.dataset.subtab === name);
        });
        document.querySelectorAll(".subtab-panel[data-subtab-panel]").forEach(panel => {
            panel.style.display = (panel.dataset.subtabPanel === name) ? "flex" : "none";
        });
        if (name === "clientes" && !_audClientesCargado && typeof loadClientesAuditoria === "function") {
            _audClientesCargado = true;
            loadClientesAuditoria();
        } else if (name === "integraciones" && !_audIntegracionesCargado && typeof loadCapturas === "function") {
            _audIntegracionesCargado = true;
            loadCapturas();
        }
    };

    // El boton de "aceptar" que va en las SIETE bandejas de discrepancias.
    // Manda la huella que el servidor ya calculo y puso en la fila: es la
    // unica forma de que la aceptacion tape exactamente la discrepancia
    // que el usuario esta viendo. Ver separar_discrepancias_aceptadas.
    // "No se le otorgó el descuento" -- la excepción a la regla de que el
    // descuento se asume comprometido con el cliente. Vive en dos lados
    // porque son conjuntos DISTINTOS de órdenes (cero solapamiento medido
    // en producción): en Ventas están las 152 órdenes sin pagar cuyo
    // descuento infla la cuenta por cobrar ($14.675,59), y en la Bandeja 2
    // las 216 ya pagadas que esperan la nota de crédito.
    // ── Detalle de reglas: de dónde viene el descuento sugerido ───────────
    // Pedido del usuario (septiembre 2026): "me gustaría ver en el detalle
    // de la orden cómo cada regla se aplica en monto y en %". Antes el
    // motor no registraba QUÉ regla producía cada descuento -- solo el
    // origen ("volumen") -- y con cinco reglas de volumen activas era
    // imposible auditar de dónde salía el monto. Ahora cada componente
    // trae su regla_id, su porcentaje y su base.
    window.verDetalleReglas = function(soId, detalleJson, ctxJson) {
        let detalle = [];
        try { detalle = JSON.parse(decodeURIComponent(detalleJson)) || []; } catch (e) { detalle = []; }
        let ctx = { sugerido: null, motivo: null };
        try { ctx = Object.assign(ctx, JSON.parse(decodeURIComponent(ctxJson || '')) || {}); } catch (e) { /* sin contexto */ }
        const fmt2 = (v) => new Intl.NumberFormat('es-US', { style: 'currency', currency: 'USD' }).format(v || 0);
        const pct = (v) => (v === null || v === undefined) ? '—' : (v * 100).toFixed(2) + '%';
        const esc = (t) => String(t === null || t === undefined ? '' : t)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

        let filas = '';
        let total = 0;
        for (const d of detalle) {
            total += (d.monto || 0);
            const comps = (d.componentes && d.componentes.length) ? d.componentes : null;
            if (comps) {
                // Un descuento que suma varias reglas: se abre una fila por
                // regla, que es lo que permite auditarlo.
                filas += `<tr style="background:#f8fafc;"><td colspan="5"><strong>${esc(d.origen)}</strong> — ${esc(d.descripcion)} · total <strong>${fmt2(d.monto)}</strong></td></tr>`;
                for (const c of comps) {
                    filas += `<tr>
                        <td style="padding-left:1.5rem;">${esc(d.origen)}</td>
                        <td><code>${esc(c.regla_id) || '<em style="opacity:.6">sin regla</em>'}</code></td>
                        <td>${esc(c.descripcion)}${c.alcance ? ` <small style="opacity:.7">(${esc(c.alcance)})</small>` : ''}</td>
                        <td style="text-align:right">${pct(c.porcentaje !== undefined && c.porcentaje !== null ? Number(c.porcentaje) : null)}</td>
                        <td style="text-align:right"><strong>${fmt2(Number(c.monto))}</strong>${c.base ? `<div style="font-size:.7rem;opacity:.7">sobre ${fmt2(Number(c.base))}</div>` : ''}</td>
                    </tr>`;
                }
            } else {
                filas += `<tr>
                    <td>${esc(d.origen)}</td>
                    <td><code>${esc(d.regla_id) || '<em style="opacity:.6">sin regla</em>'}</code></td>
                    <td>${esc(d.descripcion)}</td>
                    <td style="text-align:right">${pct(d.porcentaje)}</td>
                    <td style="text-align:right"><strong>${fmt2(d.monto)}</strong>${d.base ? `<div style="font-size:.7rem;opacity:.7">sobre ${fmt2(d.base)}</div>` : ''}</td>
                </tr>`;
            }
        }
        if (!filas) filas = '<tr><td colspan="5" class="table-empty">Esta orden no tiene descuentos calculados por el motor.</td></tr>';

        // Motivo de por qué lo que hay que aplicar AHORA es distinto del
        // total que calculó el motor por reglas (ver btnDetalleReglas).
        const MOTIVO_TXT = {
            topada_por_brecha: "topado: no se puede acreditar más que la brecha real entre lo facturado y lo pagado",
            sin_brecha: "sin brecha: lo facturado ya coincide con lo pagado, no hay NC que emitir",
            facturado_bajo_teorico: "lo facturado ya quedó por debajo del teórico -- no hay NC que emitir",
            sin_facturado: "no se sabe cuánto se facturó todavía -- sale el total del motor, sin topar",
            cabe_en_la_brecha: "cabe entera en la brecha facturado-pagado",
        };
        const hayPendiente = ctx.sugerido !== null && ctx.sugerido !== undefined;
        const difiere = hayPendiente && Math.abs(ctx.sugerido - total) > 0.01;
        const bannerPendiente = hayPendiente ? `
            <div style="margin:0 0 0.75rem;padding:0.6rem 0.8rem;border-radius:6px;background:${difiere ? '#fffbeb' : '#f0fdf4'};border:1px solid ${difiere ? '#fde68a' : '#bbf7d0'};font-size:0.85rem;">
                <strong>Pendiente por aplicar ahora: ${fmt2(ctx.sugerido)}</strong>
                ${difiere ? ` -- distinto del total que calculó el motor por reglas (${fmt2(total)}).` : ''}
                ${ctx.motivo ? `<div style="margin-top:2px;color:#475569;">${esc(MOTIVO_TXT[ctx.motivo] || ctx.motivo)}</div>` : (difiere ? `<div style="margin-top:2px;color:#475569;">Ya cubierto en lo que Odoo/una NC/un descuento de sistema ya reconoció.</div>` : '')}
            </div>` : '';

        const cont = document.getElementById("modal-detalle-reglas-body");
        if (cont) {
            cont.innerHTML = `
                <p style="margin:0 0 0.75rem;color:#475569;font-size:0.85rem;">
                    Orden <strong>${esc(soId)}</strong> — de dónde sale el descuento que el motor calculó.
                    Una fila por regla: si dice <em>sin regla</em>, el monto no viene de ninguna regla configurada.
                </p>
                ${bannerPendiente}
                <div class="table-wrapper"><table class="cxc-table">
                    <thead><tr><th>Origen</th><th>Regla</th><th>Detalle</th><th style="text-align:right">%</th><th style="text-align:right">Monto</th></tr></thead>
                    <tbody>${filas}</tbody>
                    <tfoot><tr><td colspan="4" style="text-align:right"><strong>Total calculado por el motor</strong></td><td style="text-align:right"><strong>${fmt2(total)}</strong></td></tr></tfoot>
                </table></div>`;
        }
        const modal = document.getElementById("modal-detalle-reglas");
        if (modal) modal.style.display = "flex";
    };

    window.cerrarDetalleReglas = function() {
        const modal = document.getElementById("modal-detalle-reglas");
        if (modal) modal.style.display = "none";
    };

    // ── Formulario único de reglas ────────────────────────────────────────
    // Paso 4 del plan de unificación. Antes había seis formularios, uno por
    // tabla, y cada uno ofrecía solo su propio subconjunto de campos: no se
    // podía armar un descuento por producto con ventana de pago, aunque el
    // motor lo soporta. Los 37 campos son un núcleo común más un bloque
    // corto por tipo; el desplegable decide cuál se muestra.
    window.cambiarTipoRegla = function() {
        const tipo = document.getElementById("ru-tipo")?.value || "contado";
        document.querySelectorAll(".ru-bloque").forEach(b => {
            const tipos = (b.dataset.tipos || "").split(/\s+/);
            b.style.display = tipos.includes(tipo) ? "" : "none";
        });
        // La unidad de medida solo tiene sentido donde hay un tramo que
        // contar; en días de crédito son siempre litros.
        const uni = document.getElementById("ru-unidad");
        if (uni && tipo === "credito") uni.value = "LITROS";
    };

    window.guardarReglaUnificada = async function() {
        const v = (id, def) => {
            const el = document.getElementById(id);
            if (!el) return def;
            if (el.type === "checkbox") return el.checked;
            return el.value === "" ? def : el.value;
        };
        const num = (id, def) => {
            const raw = String(v(id, def)).replace(",", ".");
            const n = parseFloat(raw);
            return Number.isFinite(n) ? n : def;
        };
        const tipo = v("ru-tipo", "contado");
        const msg = document.getElementById("ru-mensaje");
        const payload = {
            tipo_regla: tipo,
            regla_id: v("ru-regla-id", ""),
            descripcion: v("ru-descripcion", ""),
            marca: getM2MCheckedValues(document.getElementById("form-regla-unificada"), ".m2m-ru-marca") || "*",
            categoria: getCategoriaCombinada(document.getElementById("form-regla-unificada"), "ru"),
            unidad_medida: v("ru-unidad", "UNIDADES"),
            vigencia_desde: v("ru-desde", ""),
            vigencia_hasta: v("ru-hasta", ""),
            activo: v("ru-activo", true),
            listas_aplicables: v("ru-listas", "*"),
            listas_excluidas: v("ru-listas-excluidas", ""),
            monedas_excluidas: v("ru-monedas-excluidas", ""),
            requiere_pago_previo: v("ru-pago-previo", false),
            ventana_pago_tipo: v("ru-ventana-tipo", "no_aplica"),
            ventana_pago_dias: num("ru-ventana-dias", 0),
            solo_primera_compra: v("ru-frecuencia", "recurrente") === "una_vez",
            porcentaje: num("ru-porcentaje", 0),
            min_unidades: num("ru-min", 0),
            max_unidades: num("ru-max", 999999),
            tipo_evaluacion: v("ru-evaluacion", "orden"),
            dias_evaluacion: num("ru-dias-eval", 30),
            productos: Array.from(
                document.getElementById("ru-productos")?.selectedOptions || []
            ).map(o => o.value).join(","),
            regalo_tipo: v("ru-regalo", "solo_uno"),
            valor: num("ru-valor", 0),
            compra_minima: num("ru-compra-minima", 0),
                // Sobre QUE lineas se aplica el porcentaje (vacio = todas).
                // Distinto del alcance, que dice que unidades califican.
                categorias_descuento: document.getElementById("ru-categorias-descuento")?.value || "",
            descuento_fallback: num("ru-fallback", 0),
            tipo_diferencial: v("ru-tipo-dif", "fijo_35_ves_usd"),
            porcentaje_fijo: num("ru-tope", 0.35),
            dias_credito_max: num("ru-dias-credito", 30)
        };
        if (msg) { msg.textContent = "Guardando..."; msg.style.color = "#64748b"; }
        try {
            const res = await fetch("/api/config/regla", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (res.ok) {
                if (msg) { msg.textContent = "✓ " + (data.message || "Guardada."); msg.style.color = "#059669"; }
                document.getElementById("ru-regla-id").value = "";
                if (typeof loadReglasConsolidadas === "function") loadReglasConsolidadas();
            } else {
                if (msg) { msg.textContent = "✕ " + (data.detail || "No se pudo guardar."); msg.style.color = "#dc2626"; }
            }
        } catch (err) {
            if (msg) { msg.textContent = "✕ Error de red."; msg.style.color = "#dc2626"; }
            console.error("Error guardando la regla:", err);
        }
    };

    window.btnDetalleReglas = function(item) {
        const det = item && item.descuentos_detalle ? item.descuentos_detalle : [];
        if (!det.length) return '<span style="opacity:.5;font-size:.75rem">sin reglas</span>';
        const payload = encodeURIComponent(JSON.stringify(det));
        // La suma de `det` es el TOTAL que calculó el motor por reglas --
        // no necesariamente lo que hay que aplicar AHORA. Bandeja 1 ya lo
        // neta contra lo que Odoo/NC/descuento de sistema cubrieron
        // (`descuento_pendiente_por_aplicar`); Bandeja 2 además lo topa a
        // la brecha real facturado-pagado (`nc_subtotal`/`nc_motivo`, ver
        // `topar_nota_de_credito`). Sin esta cifra, el modal parecía
        // contradecir la fila (item 4, pedido del usuario, órdenes reales
        // S00743/S00596: la fila decía $0.00 o $19.53 y el modal sumaba
        // $27.77 / $38.03).
        const sugerido = (item.nc_subtotal !== undefined && item.nc_subtotal !== null)
            ? item.nc_subtotal
            : item.descuento_pendiente_por_aplicar;
        const ctx = encodeURIComponent(JSON.stringify({
            sugerido: (typeof sugerido === "number") ? sugerido : null,
            motivo: item.nc_motivo || null,
        }));
        return `<button class="btn btn-secondary" onclick="verDetalleReglas('${item.so_id}', '${payload}', '${ctx}')" style="padding:0.25rem 0.6rem;font-size:0.75rem;" title="Ver de qué reglas viene el descuento total que calculó el motor">Detalle</button>`;
    };

    window.btnDescuentoNoOtorgado = function(item, recargar) {
        if (!item || !item.so_id) return '';
        if (item.descuento_no_otorgado) {
            const quien = (item.descuento_no_otorgado_por || '').replace(/"/g, '&quot;');
            const motivo = (item.descuento_no_otorgado_motivo || '').replace(/"/g, '&quot;');
            return `<span class="state-badge" style="background:#fef2f2;color:#991b1b;font-weight:600;" title="Marcada por ${quien}. Motivo: ${motivo || 'sin motivo'}">Sin descuento</span>
                <button class="btn btn-secondary" onclick="marcarDescuentoNoOtorgado('${item.so_id}', false, '${recargar}')" style="padding:0.2rem 0.5rem;font-size:0.7rem;margin-left:0.3rem;">Reactivar</button>`;
        }
        return `<button class="btn btn-secondary" onclick="marcarDescuentoNoOtorgado('${item.so_id}', true, '${recargar}')" style="padding:0.25rem 0.6rem;font-size:0.75rem;" title="El descuento deja de bajar la cuenta por cobrar y no genera nota de crédito">No se otorgó</button>`;
    };

    window.marcarDescuentoNoOtorgado = async function(soId, noOtorgado, recargar) {
        let motivo = '';
        let quien = '';
        if (noOtorgado) {
            motivo = prompt(
                `${soId} — ¿por qué NO se le otorgó el descuento?

` +
                `El monto vuelve a la cuenta por cobrar y la orden deja de pedir nota de crédito.`,
                "El cliente pagó completo, no se le ofreció descuento");
            if (motivo === null) return;
            quien = prompt("¿Quién lo marca?", "Dirección / Administración");
            if (quien === null) return;
        } else if (!confirm(`${soId}: el descuento vuelve a contar como comprometido y baja de la cuenta por cobrar. ¿Confirmas?`)) {
            return;
        }
        try {
            const res = await fetch("/api/ventas/descuento-no-otorgado", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    so_id: soId, no_otorgado: noOtorgado,
                    motivo: motivo, marcado_por: quien || "Dirección / Administración"
                })
            });
            if (res.ok) {
                const data = await res.json();
                alert("✅ " + (data.message || "Listo."));
                if (typeof window[recargar] === "function") window[recargar]();
            } else {
                alert("❌ No se pudo registrar el cambio.");
            }
        } catch (err) {
            console.error("Error marcando descuento no otorgado:", err);
        }
    };

    window.btnAceptarDiscrepancia = function(item) {
        if (!item || !item.discrepancia_id) return '';
        const reabierta = item.reabierta
            ? '<span class="state-badge" style="background:#fef3c7; color:#92400e; margin-right:0.35rem;" title="Se acepto antes, pero los montos cambiaron">reabierta</span>'
            : '';
        const payload = encodeURIComponent(JSON.stringify({
            discrepancia_id: item.discrepancia_id,
            so_id: String(item.so_id || ''),
            factura_id: String(item.factura_id || 'N/A'),
            tipo_discrepancia: String(item.tipo_discrepancia || ''),
            huella: String(item.huella || ''),
            detalle: String(item.detalle || item.causa_probable || '')
        }));
        return reabierta + `<button class="btn btn-secondary" onclick="aceptarDiscrepancia('${payload}')" style="padding:0.25rem 0.6rem; font-size:0.75rem;">Aceptar</button>`;
    };

    window.aceptarDiscrepancia = async function(payloadJson) {
        const base = JSON.parse(decodeURIComponent(payloadJson));
        const motivo = prompt(
            `Motivo para aceptar esta discrepancia (${base.so_id} - ${base.tipo_discrepancia}):

` +
            `Sale de su bandeja y pasa al historial. Si los montos que la originaron cambian, vuelve a aparecer.`,
            "Revisado y aceptado en auditoría");
        if (motivo === null) return;
        const quien = prompt("¿Quién la acepta?", "Dirección / Auditor");
        if (quien === null) return;
        try {
            const res = await fetch("/api/auditoria/aceptar-discrepancia", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(Object.assign({}, base, {
                    motivo_aceptacion: motivo, aprobado_por: quien
                }))
            });
            if (res.ok) {
                const data = await res.json();
                alert("✅ " + (data.message || "Discrepancia aceptada."));
                loadAuditoria();
            } else {
                alert("❌ Error al aceptar la discrepancia.");
            }
        } catch (err) {
            console.error("Error aceptando discrepancia:", err);
        }
    };

    // Auto-refresh Dashboard every 30 seconds
    setInterval(() => {
        const activeTab = document.querySelector(".tab-navigation .active");
        if (activeTab && activeTab.dataset.page === "dashboard") {
            if (typeof loadTasasPromedios === "function") loadTasasPromedios();
        }
    }, 30000);

    // ===================================================================
    // BANCOS: ingesta de estados de cuenta (fase 1). Ver docs/BANCOS.md.
    // ===================================================================
    const _bancosEsc = (v) => String(v == null ? "" : v)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    const _bancosNum = new Intl.NumberFormat("es-VE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const _bancosTasa = new Intl.NumberFormat("es-VE", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
    const _bancosMonto = (v) => v == null ? "—" : _bancosNum.format(v);
    const _bancosFecha = (iso) => {
        if (!iso) return "—";
        const [a, m, d] = String(iso).slice(0, 10).split("-");
        return `${d}/${m}/${a}`;
    };
    const _BANCOS_MAX_FILAS = 1000;
    let _bancosMovimientos = [];
    let _bancosParsersCargados = false;

    async function _bancosGet(url) {
        const res = await fetch(url);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
        return data;
    }

    async function _bancosCargarParsers() {
        if (_bancosParsersCargados) return;
        const data = await _bancosGet("/api/bancos/parsers");
        const opciones = data.bancos.map(b => `<option value="${_bancosEsc(b)}">${_bancosEsc(b)}</option>`).join("");
        document.getElementById("bancos-carga-banco").innerHTML = `<option value="">Banco...</option>${opciones}`;
        document.getElementById("bancos-mov-banco").innerHTML = `<option value="">Todos los bancos</option>${opciones}`;
        document.getElementById("bancos-conc-banco").innerHTML = `<option value="">Todos los bancos</option>${opciones}`;
        _bancosParsersCargados = true;
    }

    async function loadBancosSaldos() {
        const body = document.getElementById("bancos-saldos-body");
        try {
            const data = await _bancosGet("/api/bancos/saldos");
            const alertas = data.cuentas.reduce((n, c) => n + c.alertas.length, 0);
            document.getElementById("bancos-total-usd").textContent = "$ " + _bancosMonto(data.total_usd);
            document.getElementById("bancos-total-ves").textContent = "Bs " + _bancosMonto(data.total_ves);
            document.getElementById("bancos-n-cuentas").textContent = data.cuentas.length;
            document.getElementById("bancos-sin-tasa").textContent = data.cuentas_sin_tasa.length
                ? "Sin tasa (fuera del total): " + data.cuentas_sin_tasa.join(", ")
                : "Todas convertidas";
            const nAlertas = document.getElementById("bancos-n-alertas");
            nAlertas.textContent = alertas;
            nAlertas.style.color = alertas ? "#e11d48" : "";
            if (!data.cuentas.length) {
                body.innerHTML = `<tr><td colspan="9" class="table-empty">Todavía no hay estados de cuenta cargados.</td></tr>`;
                return;
            }
            body.innerHTML = data.cuentas.map(c => {
                const estado = c.alertas.length
                    ? `<span class="semaphore rojo">Revisar</span><div style="font-size:0.72rem; color:#be123c; margin-top:0.3rem;">${c.alertas.map(_bancosEsc).join("<br>")}</div>`
                    : `<span class="semaphore verde">Cuadra</span>`;
                return `<tr>
                    <td><strong>${_bancosEsc(c.banco)}</strong></td>
                    <td>${_bancosEsc(c.moneda)}</td>
                    <td>${_bancosFecha(c.fecha_corte)}</td>
                    <td style="text-align:right;">${_bancosMonto(c.saldo)}</td>
                    <td style="text-align:right;">${c.tasa_bcv == null ? "—" : _bancosTasa.format(c.tasa_bcv)}</td>
                    <td style="text-align:right;">${_bancosMonto(c.saldo_usd)}</td>
                    <td style="text-align:right;">${_bancosMonto(c.saldo_ves)}</td>
                    <td>${c.cortes}${c.cortes_sin_cuadrar ? ` <span style="color:#be123c;">(${c.cortes_sin_cuadrar} sin cuadrar)</span>` : ""}</td>
                    <td>${estado}</td>
                </tr>`;
            }).join("");
        } catch (err) {
            body.innerHTML = `<tr><td colspan="9" class="table-empty">❌ ${_bancosEsc(err.message)}</td></tr>`;
        }
    }

    async function loadBancosArchivos() {
        const body = document.getElementById("bancos-archivos-body");
        try {
            const data = await _bancosGet("/api/bancos/archivos");
            if (!data.archivos.length) {
                body.innerHTML = `<tr><td colspan="10" class="table-empty">Sin archivos cargados.</td></tr>`;
                return;
            }
            body.innerHTML = data.archivos.map(a => `<tr>
                <td style="white-space:nowrap;">${_bancosFecha(a.cargado_en)} ${_bancosEsc(String(a.cargado_en || "").slice(11, 16))}</td>
                <td><strong>${_bancosEsc(a.banco)}</strong></td>
                <td>${_bancosEsc(a.nombre_archivo)}</td>
                <td style="white-space:nowrap;">${_bancosFecha(a.fecha_desde)} – ${_bancosFecha(a.fecha_hasta)}</td>
                <td style="text-align:right;">${a.n_movimientos}</td>
                <td style="text-align:right;">${a.n_nuevos}</td>
                <td style="text-align:right;">${_bancosMonto(a.saldo_final)}</td>
                <td>${a.cuadra ? '<span class="semaphore verde">Sí</span>' : '<span class="semaphore rojo">No</span>'}</td>
                <td style="font-size:0.75rem; color:#475569; max-width:360px;">${_bancosEsc(a.observaciones) || "—"}</td>
                <td style="font-size:0.75rem;">${_bancosEsc(a.cargado_por)}</td>
            </tr>`).join("");
        } catch (err) {
            body.innerHTML = `<tr><td colspan="10" class="table-empty">❌ ${_bancosEsc(err.message)}</td></tr>`;
        }
    }

    function _bancosFiltros(conCategoria) {
        const p = new URLSearchParams();
        const banco = document.getElementById("bancos-mov-banco").value;
        const desde = document.getElementById("bancos-mov-desde").value;
        const hasta = document.getElementById("bancos-mov-hasta").value;
        if (banco) p.set("banco", banco);
        if (desde) p.set("desde", desde);
        if (hasta) p.set("hasta", hasta);
        if (conCategoria) {
            const cat = document.getElementById("bancos-mov-categoria").value;
            if (cat) p.set("categoria", cat);
        }
        return p;
    }

    function _renderBancosMovimientos() {
        const body = document.getElementById("bancos-mov-body");
        const q = (document.getElementById("bancos-mov-buscar").value || "").trim().toUpperCase();
        const filas = q
            ? _bancosMovimientos.filter(m => m.descripcion.toUpperCase().includes(q) || m.referencia.toUpperCase().includes(q))
            : _bancosMovimientos;
        const deb = filas.reduce((s, m) => s + m.debito, 0);
        const cre = filas.reduce((s, m) => s + m.credito, 0);
        document.getElementById("bancos-mov-resumen").textContent =
            `${filas.length} movimientos · Débitos ${_bancosMonto(deb)} · Créditos ${_bancosMonto(cre)}` +
            (filas.length > _BANCOS_MAX_FILAS ? ` · se muestran los primeros ${_BANCOS_MAX_FILAS}; usa los filtros o el Excel` : "");
        if (!filas.length) {
            body.innerHTML = `<tr><td colspan="8" class="table-empty">Sin movimientos para estos filtros.</td></tr>`;
            return;
        }
        body.innerHTML = filas.slice(0, _BANCOS_MAX_FILAS).map(m => {
            const porRevisar = m.categoria.includes("por_revisar");
            return `<tr>
                <td style="white-space:nowrap;">${_bancosFecha(m.fecha)}</td>
                <td>${_bancosEsc(m.banco)}</td>
                <td>${_bancosEsc(m.referencia) || "—"}</td>
                <td>${_bancosEsc(m.descripcion)}</td>
                <td style="text-align:right; color:${m.debito ? "#be123c" : "#94a3b8"};">${m.debito ? _bancosMonto(m.debito) : "—"}</td>
                <td style="text-align:right; color:${m.credito ? "#047857" : "#94a3b8"};">${m.credito ? _bancosMonto(m.credito) : "—"}</td>
                <td style="text-align:right;">${_bancosMonto(m.saldo)}</td>
                <td><span class="state-badge${porRevisar ? " abierta" : ""}" title="Regla: ${_bancosEsc(m.regla)}">${_bancosEsc(m.categoria)}</span></td>
            </tr>`;
        }).join("");
    }

    async function loadBancosMovimientos() {
        const body = document.getElementById("bancos-mov-body");
        body.innerHTML = `<tr><td colspan="8" class="table-empty">Cargando...</td></tr>`;
        try {
            const data = await _bancosGet("/api/bancos/movimientos?" + _bancosFiltros(true).toString());
            _bancosMovimientos = data.movimientos;
            const sel = document.getElementById("bancos-mov-categoria");
            const actual = sel.value;
            const cats = new Set([...sel.options].map(o => o.value).filter(Boolean));
            _bancosMovimientos.forEach(m => cats.add(m.categoria));
            sel.innerHTML = `<option value="">Todas las categorías</option>` +
                [...cats].sort().map(c => `<option value="${_bancosEsc(c)}">${_bancosEsc(c)}</option>`).join("");
            sel.value = actual;
            _renderBancosMovimientos();
        } catch (err) {
            body.innerHTML = `<tr><td colspan="8" class="table-empty">❌ ${_bancosEsc(err.message)}</td></tr>`;
        }
    }
    window.loadBancosMovimientos = loadBancosMovimientos;

    async function _bancosSesion() {
        // En una carga directa de /bancos, initCurrentPage() corre antes de que
        // initUserSession() termine (mismo caso que Configuración, 25-sep-2026):
        // si la sesión todavía no está, se pide acá.
        if (currentUserSession) return currentUserSession;
        try {
            const res = await fetch("/api/auth/me");
            return res.ok ? await res.json() : null;
        } catch (err) {
            return null;
        }
    }

    async function loadBancos() {
        // Cada sección según el permiso de la persona (cxc/permisos.py); la API
        // exige lo mismo, esto solo evita mostrar lo que respondería 403.
        const u = await _bancosSesion();
        const puede = (p) => !!u && (u.rol === "admin" || (u.permisos || []).includes(p));
        const cargar = puede("bancos.cargar"), ver = puede("bancos.ver"), conciliar = puede("bancos.conciliar");
        const mostrar = (id, si) => { const el = document.getElementById(id); if (el) el.style.display = si ? "" : "none"; };
        mostrar("bancos-carga-panel", cargar);
        mostrar("bancos-saldos-panel", ver);
        mostrar("bancos-archivos-panel", cargar || ver);
        mostrar("bancos-mov-panel", ver);
        mostrar("bancos-conc-panel", ver || conciliar);
        mostrar("bancos-conc-generar", conciliar);
        _bancosConcAdmin = conciliar;
        try {
            await _bancosCargarParsers();
        } catch (err) {
            console.error("Bancos: no se pudo cargar la lista de bancos", err);
        }
        await Promise.all([
            ver ? loadBancosSaldos() : null,
            cargar || ver ? loadBancosArchivos() : null,
            ver ? loadBancosMovimientos() : null,
            ver || conciliar ? loadBancosConciliacion() : null,
        ]);
    }
    window.loadBancos = loadBancos;

    window.descargarBancosExcel = function() {
        window.location.href = "/api/bancos/consolidado.xlsx";
    };

    window.descargarBancosCsvOdoo = function() {
        const p = _bancosFiltros(false).toString();
        window.location.href = "/api/bancos/odoo-extracto.csv" + (p ? "?" + p : "");
    };

    // --- Pre-conciliación (fase 2): las reglas proponen, una persona decide. ---
    let _bancosConc = [];
    let _bancosConcAdmin = false;
    const _BANCOS_CONC_ESTADO = {
        pendiente: ["Pendiente", ""],
        aprobada: ["Aprobada", "verde"],
        corregida: ["Corregida", "verde"],
        rechazada: ["Rechazada", "rojo"],
    };
    const _bancosChico = (html) => `<div style="font-size:0.72rem; color:#64748b; margin-top:0.2rem;">${html}</div>`;

    function _bancosConcPropuesta(p) {
        const cands = p.candidatos || [];
        if (p.tipo === "pago") {
            const c = cands.find(x => x.id === p.pago_id) || {};
            const detalle = [c.partner, c.fecha ? _bancosFecha(c.fecha) : "", c.memo].filter(Boolean).map(_bancosEsc).join(" · ");
            return `<strong>${_bancosEsc(p.pago_nombre || ("Pago #" + p.pago_id))}</strong>` +
                (detalle ? _bancosChico(detalle) : "") +
                (c.ya_conciliado ? `<div style="font-size:0.72rem; color:#b45309;">Ya conciliado en Odoo</div>` : "") +
                _bancosChico(_bancosEsc(String(p.motivo || "").replace(" (ya conciliado en Odoo)", "")));
        }
        if (p.tipo === "cuenta") {
            return `<strong>${_bancosEsc(p.cuenta)}</strong>` + _bancosChico(_bancosEsc(`${p.regla} · ${p.motivo}`));
        }
        if (p.tipo === "ambiguo") {
            return `<span style="color:#b45309; font-weight:600;">Ambigua</span>: ${cands.length} pagos posibles` +
                _bancosChico(_bancosEsc(p.motivo));
        }
        return `<span style="color:#94a3b8;">Sin propuesta</span>` + _bancosChico(_bancosEsc(p.motivo || ""));
    }

    function _bancosConcAcciones(p, i) {
        if (!_bancosConcAdmin) return "—";
        const b = (accion, texto, clase) =>
            `<button type="button" class="${clase}" data-conc-accion="${accion}" data-conc-i="${i}" style="padding:0.25rem 0.6rem; font-size:0.75rem; margin:0.1rem;">${texto}</button>`;
        if (p.estado !== "pendiente") return b("reabrir", "Reabrir", "btn-secondary");
        return (["pago", "cuenta"].includes(p.tipo) ? b("aprobar", "✓ Aprobar", "btn-primary") : "") +
            b("corregir", "Corregir", "btn-secondary") + b("rechazar", "Rechazar", "btn-secondary");
    }

    function _renderBancosConciliacion(resumen) {
        const body = document.getElementById("bancos-conc-body");
        const est = resumen ? Object.entries(resumen.estado).map(([k, v]) => `${v} ${k}`).join(" · ") : "";
        const tip = resumen ? Object.entries(resumen.tipo).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(" · ") : "";
        document.getElementById("bancos-conc-resumen").textContent =
            `${_bancosConc.length} movimientos` + (est ? ` · ${est}` : "") + (tip ? ` · propuestas: ${tip}` : "") +
            (_bancosConc.length > _BANCOS_MAX_FILAS ? ` · se muestran los primeros ${_BANCOS_MAX_FILAS}` : "");
        if (!_bancosConc.length) {
            body.innerHTML = `<tr><td colspan="7" class="table-empty">Sin propuestas para estos filtros${_bancosConcAdmin ? ". Usa “Generar propuestas”." : "."}</td></tr>`;
            return;
        }
        body.innerHTML = _bancosConc.slice(0, _BANCOS_MAX_FILAS).map((p, i) => {
            const [etq, color] = _BANCOS_CONC_ESTADO[p.estado] || [p.estado, ""];
            const quien = p.decidida_por ? _bancosChico(`${_bancosEsc(p.decidida_por)} · ${_bancosFecha(p.decidida_en)}`) : "";
            return `<tr id="bancos-conc-fila-${i}">
                <td style="white-space:nowrap;">${_bancosFecha(p.fecha)}</td>
                <td>${_bancosEsc(p.banco)}</td>
                <td>${_bancosEsc(p.descripcion)}${p.referencia ? _bancosChico("Ref. " + _bancosEsc(p.referencia)) : ""}</td>
                <td style="text-align:right; white-space:nowrap; color:${p.monto < 0 ? "#be123c" : "#047857"};">${_bancosMonto(p.monto)} <span style="color:#94a3b8; font-size:0.7rem;">${_bancosEsc(p.moneda)}</span></td>
                <td style="max-width:340px;">${_bancosConcPropuesta(p)}</td>
                <td><span class="semaphore ${color}">${_bancosEsc(etq)}</span>${quien}</td>
                <td style="white-space:nowrap;">${_bancosConcAcciones(p, i)}</td>
            </tr>`;
        }).join("");
    }

    async function loadBancosConciliacion() {
        const body = document.getElementById("bancos-conc-body");
        body.innerHTML = `<tr><td colspan="7" class="table-empty">Cargando...</td></tr>`;
        const q = new URLSearchParams();
        for (const [param, id] of [["banco", "bancos-conc-banco"], ["estado", "bancos-conc-estado"], ["tipo", "bancos-conc-tipo"]]) {
            const v = document.getElementById(id).value;
            if (v) q.set(param, v);
        }
        try {
            const data = await _bancosGet("/api/bancos/conciliacion?" + q.toString());
            _bancosConc = data.items;
            _renderBancosConciliacion(data.resumen);
            if (typeof window.loadConciliacionGastos === "function") {
                window.loadConciliacionGastos();
            }
        } catch (err) {
            body.innerHTML = `<tr><td colspan="7" class="table-empty">❌ ${_bancosEsc(err.message)}</td></tr>`;
        }
    }
    window.loadBancosConciliacion = loadBancosConciliacion;

    window.descargarBancosAprobadas = function() {
        // Las fechas son de cuándo se DECIDIÓ: "lo aprobado hoy" sin repetir lo de ayer.
        const p = new URLSearchParams();
        const banco = document.getElementById("bancos-conc-banco").value;
        const desde = document.getElementById("bancos-conc-exp-desde").value;
        const hasta = document.getElementById("bancos-conc-exp-hasta").value;
        if (banco) p.set("banco", banco);
        if (desde) p.set("desde", desde);
        if (hasta) p.set("hasta", hasta);
        const q = p.toString();
        window.location.href = "/api/bancos/conciliacion/aprobadas.xlsx" + (q ? "?" + q : "");
    };

    window.generarBancosPropuestas = async function() {
        const btn = document.getElementById("bancos-conc-generar");
        const out = document.getElementById("bancos-conc-resumen");
        const banco = document.getElementById("bancos-conc-banco").value;
        btn.disabled = true;
        out.textContent = "Leyendo pagos de Odoo y corriendo las reglas...";
        try {
            const res = await fetch("/api/bancos/conciliacion/generar" + (banco ? "?banco=" + encodeURIComponent(banco) : ""), { method: "POST" });
            const r = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(r.detail || `Error ${res.status}`);
            await loadBancosConciliacion();
            if (typeof window.loadConciliacionGastos === "function") window.loadConciliacionGastos();
            out.textContent = `Propuestas: ${r.nuevas} nuevas, ${r.actualizadas} actualizadas, ${r.ya_decididas} ya decididas (no se tocan). ` + out.textContent;
        } catch (err) {
            out.textContent = "❌ " + err.message;
        } finally {
            btn.disabled = false;
        }
    };

    function _bancosConcEditor(i, accion) {
        const p = _bancosConc[i];
        document.querySelectorAll(".bancos-conc-editor").forEach(el => el.remove());
        const fila = document.getElementById(`bancos-conc-fila-${i}`);
        let campos = "";
        if (accion === "corregir") {
            const ops = (p.candidatos || []).map(c =>
                `<option value="${c.id}">${_bancosEsc([c.nombre || ("#" + c.id), c.partner, c.fecha ? _bancosFecha(c.fecha) : "", c.memo].filter(Boolean).join(" · "))}</option>`).join("");
            campos = `<label style="font-size:0.8rem;">Pago de Odoo
                    <select class="filter-select" data-conc-campo="pago"><option value="">—</option>${ops}</select></label>
                <label style="font-size:0.8rem;">o id de pago <input type="number" min="1" class="filter-select" data-conc-campo="pago_libre" style="width:7rem; cursor:text;"></label>
                <label style="font-size:0.8rem;">o cuenta <input type="text" class="filter-select" data-conc-campo="cuenta" placeholder="6.2.1.1.01.02 Bank Fees" style="min-width:16rem; cursor:text;"></label>`;
        }
        const obligatorio = accion === "rechazar" || accion === "reabrir";
        const textoBtn = { corregir: "Guardar corrección", rechazar: "Rechazar", reabrir: "Reabrir" }[accion];
        const tr = document.createElement("tr");
        tr.className = "bancos-conc-editor";
        tr.innerHTML = `<td colspan="7" style="background:#f8fafc;">
            <div style="display:flex; gap:0.75rem; align-items:center; flex-wrap:wrap;">
                ${campos}
                <label style="font-size:0.8rem;">Comentario${obligatorio ? " (obligatorio)" : ""}
                    <input type="text" class="filter-select" data-conc-campo="comentario" style="min-width:16rem; cursor:text;"></label>
                <button type="button" class="btn-primary" data-conc-confirmar="${accion}" data-conc-i="${i}" style="padding:0.3rem 0.8rem; font-size:0.8rem;">${textoBtn}</button>
                <button type="button" class="btn-secondary" data-conc-cancelar="1" style="padding:0.3rem 0.8rem; font-size:0.8rem;">Cancelar</button>
                <span data-conc-error style="color:#be123c; font-size:0.8rem;"></span>
            </div></td>`;
        fila.after(tr);
    }

    async function _bancosConcDecidir(i, accion, editor) {
        const p = _bancosConc[i];
        const valor = (campo) => {
            const el = editor && editor.querySelector(`[data-conc-campo="${campo}"]`);
            return el ? el.value.trim() : "";
        };
        const body = { accion, comentario: valor("comentario") };
        if (accion === "corregir") {
            const pago = valor("pago") || valor("pago_libre");
            if (pago) body.pago_id = Number(pago);
            if (valor("cuenta")) body.cuenta = valor("cuenta");
        }
        const error = editor ? editor.querySelector("[data-conc-error]") : null;
        try {
            const res = await fetch(`/api/bancos/conciliacion/${encodeURIComponent(p.huella)}/decidir`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
            const r = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(typeof r.detail === "string" ? r.detail : `Error ${res.status}`);
            await loadBancosConciliacion();
        } catch (err) {
            if (error) error.textContent = "❌ " + err.message;
            else document.getElementById("bancos-conc-resumen").textContent = "❌ " + err.message;
        }
    }

    const _bancosConcBody = document.getElementById("bancos-conc-body");
    if (_bancosConcBody) {
        _bancosConcBody.addEventListener("click", (e) => {
            const btn = e.target.closest("button");
            if (!btn) return;
            if (btn.dataset.concCancelar) {
                btn.closest(".bancos-conc-editor").remove();
                return;
            }
            const i = Number(btn.dataset.concI);
            if (btn.dataset.concConfirmar) {
                _bancosConcDecidir(i, btn.dataset.concConfirmar, btn.closest(".bancos-conc-editor"));
                return;
            }
            const accion = btn.dataset.concAccion;
            if (accion === "aprobar") _bancosConcDecidir(i, "aprobar", null);
            else if (accion) _bancosConcEditor(i, accion);
        });
    }
    for (const id of ["bancos-conc-estado", "bancos-conc-tipo", "bancos-conc-banco"]) {
        const el = document.getElementById(id);
        if (el) el.addEventListener("change", loadBancosConciliacion);
    }

    const _bancosBuscarEl = document.getElementById("bancos-mov-buscar");
    if (_bancosBuscarEl) _bancosBuscarEl.addEventListener("input", _renderBancosMovimientos);
    const _bancosCatEl = document.getElementById("bancos-mov-categoria");
    if (_bancosCatEl) _bancosCatEl.addEventListener("change", loadBancosMovimientos);

    const _bancosForm = document.getElementById("bancos-carga-form");
    if (_bancosForm) {
        _bancosForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const banco = document.getElementById("bancos-carga-banco").value;
            const input = document.getElementById("bancos-carga-archivo");
            const btn = document.getElementById("bancos-carga-btn");
            const out = document.getElementById("bancos-carga-resultado");
            if (!banco || !input.files.length) return;
            const fd = new FormData();
            fd.append("banco", banco);
            fd.append("archivo", input.files[0]);
            btn.disabled = true;
            out.innerHTML = `<p style="color:#475569;">Procesando ${_bancosEsc(input.files[0].name)}...</p>`;
            try {
                const res = await fetch("/api/bancos/importar", { method: "POST", body: fd });
                const r = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(r.detail || `Error ${res.status}`);
                const color = r.ya_cargado ? "#475569" : (r.cuadra ? "#047857" : "#b45309");
                const titulo = r.ya_cargado
                    ? "Este archivo ya estaba cargado: no se agregó nada."
                    : `${r.nuevos} movimientos nuevos de ${r.movimientos} (${_bancosFecha(r.desde)} – ${_bancosFecha(r.hasta)}).`;
                const cuadra = r.cuadra
                    ? "La cadena de saldos cuadra al céntimo."
                    : "⚠️ La cadena de saldos NO cuadra: se guardó igual, revisa las observaciones.";
                out.innerHTML = `<div style="border-left:4px solid ${color}; padding:0.5rem 0.85rem; background:#f8fafc; border-radius:6px;">
                    <strong style="color:${color};">${_bancosEsc(r.banco)} · ${_bancosEsc(r.archivo)}</strong>
                    <div>${titulo} ${cuadra}</div>
                    ${r.observaciones ? `<div style="font-size:0.75rem; color:#475569; margin-top:0.25rem;">${_bancosEsc(r.observaciones)}</div>` : ""}
                </div>`;
                input.value = "";
                await Promise.all([loadBancosSaldos(), loadBancosArchivos(), loadBancosMovimientos()]);
            } catch (err) {
                out.innerHTML = `<div style="border-left:4px solid #e11d48; padding:0.5rem 0.85rem; background:#fff1f2; border-radius:6px; color:#9f1239;">❌ ${_bancosEsc(err.message)}</div>`;
            } finally {
                btn.disabled = false;
            }
        });
    }

    // ===================================================================
    // CAPTURAS TELEGRAM: borradores de los bots y su envío a Odoo.
    // API: GET /api/bots/capturas, GET /api/bots/capturas/{id},
    //      POST /api/bots/capturas/{id}/reintentar. Ver docs del PR.
    // ===================================================================
    const _CAP_PAGINA = 25;
    const _CAP_MAX_BUSQUEDA = 500;   // la API no filtra por texto: con búsqueda se traen hasta N y se filtra aquí
    // «Completar» necesita POST /api/bots/capturas/{id}/completar, que hoy solo acepta la llave del bot
    // (X-Bot-Key), no la sesión de una persona. Cuando el backend lo abra a sesión, basta poner esto en true.
    const _CAP_COMPLETAR_POR_SESION = false;
    const _CAP_ESTADOS = {
        PENDIENTE: ["Pendiente", "amarillo"],
        INCOMPLETO: ["Incompleto", "amarillo"],
        ERROR: ["Error", "rojo"],
        ENVIADO: ["Enviado", "verde"],
    };
    const _CAP_TIPOS = {
        pedido: "Pedido",
        cotizacion: "Cotización",
        contacto: "Contacto (Prospecto)",
        contacto_editar: "Edición de contacto",
        pago: "Pago de cliente",
        pago_proveedor: "Pago a proveedor",
        proveedor: "Proveedor",
        factura_proveedor: "Factura de proveedor",
        gasto: "Gasto (compra menor)",
    };
    let _capOffset = 0;
    let _capItems = [];          // filas visibles en la tabla (para abrir el detalle)
    let _capPuedeGestionar = false;
    let _capCargaId = 0;         // descarta respuestas viejas si el usuario cambia de filtro rápido

    const _capEsc = (v) => _bancosEsc(v);
    const _capTexto = (v) => (v == null || v === "" ? "—" : _capEsc(v));
    const _capMonto = (v) => {
        const n = Number(v);
        return v == null || v === "" || !Number.isFinite(n) ? "—" : _bancosNum.format(n);
    };
    const _capFechaHora = (iso) => {
        if (!iso) return "—";
        const d = new Date(iso);
        if (isNaN(d)) return _capEsc(String(iso).slice(0, 16).replace("T", " "));
        return d.toLocaleString("es-VE", {
            timeZone: "America/Caracas", day: "2-digit", month: "2-digit", year: "numeric",
            hour: "2-digit", minute: "2-digit", hour12: false,
        });
    };
    const _capFechaSolo = (iso) => (iso ? _bancosFecha(iso) : "—");

    async function _capSesion() { return _bancosSesion(); }

    async function _capGet(url) {
        const res = await fetch(url);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
        return data;
    }

    function _capBadge(estado) {
        const [txt, clase] = _CAP_ESTADOS[estado] || [estado || "—", ""];
        return `<span class="semaphore ${clase}">${_capEsc(txt)}</span>`;
    }

    // Una línea de resumen legible por tipo (lo que la gente busca de un vistazo).
    function _capResumen(c) {
        const p = c.payload || {};
        const gris = (t) => `<div class="cap-sub">${t}</div>`;
        if (c.kind === "pedido" || c.kind === "cotizacion") {
            const n = Array.isArray(p.lineas) ? p.lineas.length : 0;
            return `<strong>${_capTexto(p.cliente)}</strong>${gris(`${_capEsc(p.rif || (c.kind === "cotizacion" ? "cotización" : "sin RIF"))} · ${n} producto${n === 1 ? "" : "s"}`)}`;
        }
        if (c.kind === "contacto" || c.kind === "contacto_editar") {
            return `<strong>${_capTexto(p.nombre || p.nombre_buscar)}</strong>${gris(_capEsc([p.telefono, p.email, p.rif].filter(Boolean).join(" · ") || "contacto"))}`;
        }
        if (c.kind === "pago") {
            const quien = p.cliente || p.rif || p.pedido_ref;
            return `<strong>${_capTexto(quien)}</strong>${gris(`${_capMonto(p.monto)} ${_capEsc(p.moneda || "")} · ref ${_capEsc(p.referencia || "—")}`)}`;
        }
        if (c.kind === "pago_proveedor") {
            return `<strong>${_capTexto(p.proveedor)}</strong>${gris(`${_capMonto(p.monto)} ${_capEsc(p.moneda || "")} · ref ${_capEsc(p.referencia || "—")}`)}`;
        }
        if (c.kind === "proveedor") {
            return `<strong>${_capTexto(p.nombre)}</strong>${gris(_capEsc([p.rif, p.telefono].filter(Boolean).join(" · ") || "sin RIF"))}`;
        }
        if (c.kind === "factura_proveedor" || c.kind === "gasto") {
            return `<strong>${_capTexto(p.proveedor)}</strong>${gris(`${_capMonto(p.monto_total)} ${_capEsc(p.moneda || "")} · doc ${_capEsc(p.numero_factura || "sin número")}`)}`;
        }
        return "—";
    }

    // Texto plano del payload para la búsqueda (cliente, proveedor, referencias, RIF, nombre…).
    function _capTextoBusqueda(c) {
        const p = c.payload || {};
        return [p.cliente, p.proveedor, p.nombre, p.rif, p.referencia, p.pedido_ref, p.factura_ref, p.telefono, p.email,
                c.odoo_ref]
            .filter(Boolean).join(" ").toUpperCase();
    }

    function _capFila(c, i) {
        const aviso = c.error
            ? `<div class="cap-sub" style="color:#be123c;">${_capEsc(c.error)}</div>`
            : (c.estado === "INCOMPLETO" && (c.incompleto || []).length
                ? `<div class="cap-sub" style="color:#b45309;">Falta: ${(c.incompleto || []).map(_capEsc).join(", ")}</div>` : "");
        const auto = c.auto ? `<div class="cap-sub">Enviada sola (sin respuesta en el grupo)</div>` : "";
        return `<tr>
            <td style="white-space:nowrap;">${_capFechaHora(c.fecha_creacion)}</td>
            <td>${_capEsc(_CAP_TIPOS[c.kind] || c.kind)}</td>
            <td>${_capResumen(c)}</td>
            <td>${_capBadge(c.estado)}${aviso}${auto}</td>
            <td style="font-size:0.78rem;">${c.odoo_ref ? _capEsc(c.odoo_ref) : "—"}</td>
            <td style="white-space:nowrap;"><button type="button" class="btn-secondary" data-cap-ver="${i}">Ver detalle</button></td>
        </tr>`;
    }

    async function _capContadores() {
        const estados = Object.keys(_CAP_ESTADOS);
        const kind = document.getElementById("capturas-kind").value;
        await Promise.all(estados.map(async (e) => {
            const el = document.getElementById("capturas-n-" + e);
            if (!el) return;
            try {
                const p = new URLSearchParams({ estado: e, limit: "1", offset: "0" });
                if (kind) p.set("kind", kind);
                const data = await _capGet("/api/bots/capturas?" + p.toString());
                el.textContent = data.total ?? 0;
                el.style.color = (e === "ERROR" || e === "INCOMPLETO") && data.total ? "#e11d48" : "";
            } catch (err) {
                el.textContent = "—";
            }
        }));
    }

    function _capPaginacion(total, desde, hasta, local) {
        const cont = document.getElementById("capturas-paginacion");
        if (!total) { cont.innerHTML = ""; return; }
        const ant = desde > 0, sig = hasta < total;
        cont.innerHTML = `<span>${desde + 1}–${hasta} de ${total}</span>
            <button type="button" class="btn-secondary" data-cap-pag="ant" ${ant ? "" : "disabled"}>← Anterior</button>
            <button type="button" class="btn-secondary" data-cap-pag="sig" ${sig ? "" : "disabled"}>Siguiente →</button>`;
        cont.dataset.local = local ? "1" : "";
    }

    async function loadCapturasLista() {
        const body = document.getElementById("capturas-body");
        const resumen = document.getElementById("capturas-resumen");
        const estado = document.getElementById("capturas-estado").value;
        const kind = document.getElementById("capturas-kind").value;
        const q = (document.getElementById("capturas-buscar").value || "").trim().toUpperCase();
        const miCarga = ++_capCargaId;
        const p = new URLSearchParams();
        if (estado) p.set("estado", estado);
        if (kind) p.set("kind", kind);
        if (q) { p.set("limit", String(_CAP_MAX_BUSQUEDA)); p.set("offset", "0"); }
        else { p.set("limit", String(_CAP_PAGINA)); p.set("offset", String(_capOffset)); }
        try {
            const data = await _capGet("/api/bots/capturas?" + p.toString());
            if (miCarga !== _capCargaId) return;   // llegó una respuesta de un filtro anterior
            let filas = data.items || [];
            let total = data.total ?? filas.length;
            let desde = _capOffset;
            if (q) {
                filas = filas.filter(c => _capTextoBusqueda(c).includes(q));
                total = filas.length;
                desde = Math.min(_capOffset, Math.max(0, total - 1));
                desde = desde - (desde % _CAP_PAGINA);
                _capOffset = desde;
                filas = filas.slice(desde, desde + _CAP_PAGINA);
                resumen.textContent = `${total} coincidencia${total === 1 ? "" : "s"} entre las últimas ${_CAP_MAX_BUSQUEDA} capturas del filtro.`;
            } else {
                resumen.textContent = total ? `${total} captura${total === 1 ? "" : "s"}` : "";
            }
            _capItems = filas;
            if (!filas.length) {
                body.innerHTML = `<tr><td colspan="6" class="table-empty">${
                    (estado || kind || q) ? "No hay capturas con esos filtros." : "Todavía no llegan capturas desde Telegram."}</td></tr>`;
                _capPaginacion(0, 0, 0, !!q);
                return;
            }
            body.innerHTML = filas.map(_capFila).join("");
            _capPaginacion(total, desde, desde + filas.length, !!q);
        } catch (err) {
            if (miCarga !== _capCargaId) return;
            _capItems = [];
            body.innerHTML = `<tr><td colspan="6" class="table-empty">❌ ${_capEsc(err.message)}</td></tr>`;
            document.getElementById("capturas-paginacion").innerHTML = "";
            resumen.textContent = "";
        }
    }

    async function loadCapturas() {
        const u = await _capSesion();
        _capPuedeGestionar = !!u && (u.rol === "admin" || (u.permisos || []).includes("capturas.gestionar"));
        await Promise.all([_capContadores(), loadCapturasLista()]);
    }
    window.loadCapturas = loadCapturas;

    // --- Detalle ---------------------------------------------------------------
    const _capFilaDato = (etiqueta, valorHtml) =>
        `<tr><th style="text-align:left; width:38%; color:#475569; font-weight:600; padding:0.35rem 0.5rem 0.35rem 0; vertical-align:top;">${etiqueta}</th>` +
        `<td style="padding:0.35rem 0;">${valorHtml}</td></tr>`;
    const _capTablaDatos = (filas) =>
        `<table style="width:100%; border-collapse:collapse; font-size:0.88rem;">${filas.join("")}</table>`;

    function _capGps(u) {
        if (!u || u.latitud == null || u.longitud == null) return "—";
        const lat = Number(u.latitud), lon = Number(u.longitud);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return "—";
        const prec = u.precision_m != null && Number.isFinite(Number(u.precision_m)) ? ` (±${Math.round(Number(u.precision_m))} m)` : "";
        return `<a href="https://www.google.com/maps?q=${lat},${lon}" target="_blank" rel="noopener noreferrer">${lat.toFixed(5)}, ${lon.toFixed(5)}</a>${prec}`;
    }

    function _capDetallePedido(p) {
        const lineas = (p.lineas || []).map(l => `<tr>
            <td>${_capTexto(l.producto)}</td>
            <td style="text-align:right;">${_capMonto(l.cantidad)}</td>
            <td style="text-align:right;">${l.precio_unitario == null ? "—" : _capMonto(l.precio_unitario)}</td>
        </tr>`).join("");
        const entrega = p.entrega_igual_fiscal ? "La misma dirección fiscal" : _capTexto(p.direccion_entrega);
        return _capTablaDatos([
            _capFilaDato("Cliente", _capTexto(p.cliente)),
            _capFilaDato("RIF", _capTexto(p.rif)),
            _capFilaDato("Correo", _capTexto(p.email)),
            _capFilaDato("Teléfono", _capTexto(p.telefono)),
            _capFilaDato("Dirección fiscal", _capTexto(p.direccion_fiscal)),
            _capFilaDato("Dirección de entrega", entrega),
            _capFilaDato("Ubicación GPS", _capGps(p.ubicacion)),
            _capFilaDato("Fecha de entrega", _capFechaSolo(p.fecha_entrega)),
            _capFilaDato("Observaciones", _capTexto(p.observaciones)),
        ]) + `<h4 style="margin:1rem 0 0.4rem 0;">Productos</h4>
            <div class="table-wrapper"><table class="cxc-table"><thead><tr><th>Producto</th><th style="text-align:right;">Cantidad</th><th style="text-align:right;">Precio</th></tr></thead>
            <tbody>${lineas || `<tr><td colspan="3" class="table-empty">Sin productos.</td></tr>`}</tbody></table></div>`;
    }

    function _capDetallePago(p, proveedor) {
        return _capTablaDatos([
            proveedor ? _capFilaDato("Proveedor", _capTexto(p.proveedor)) : _capFilaDato("Cliente", _capTexto(p.cliente)),
            proveedor ? _capFilaDato("Factura", _capTexto(p.factura_ref)) : _capFilaDato("RIF", _capTexto(p.rif)),
            proveedor ? "" : _capFilaDato("Pedido o factura", _capTexto(p.pedido_ref)),
            _capFilaDato("Monto", `<strong>${_capMonto(p.monto)}</strong> ${_capEsc(p.moneda || "")}`),
            _capFilaDato("Método de pago", _capTexto(p.metodo_pago)),
            _capFilaDato("Referencia", _capTexto(p.referencia)),
            _capFilaDato("Banco", _capTexto(p.banco)),
            _capFilaDato("Fecha del pago", _capFechaSolo(p.fecha_pago)),
        ].filter(Boolean));
    }

    function _capDetalleContacto(p) {
        return _capTablaDatos([
            _capFilaDato("Nombre", _capTexto(p.nombre)),
            _capFilaDato("Teléfono", _capTexto(p.telefono)),
            _capFilaDato("Correo", _capTexto(p.email)),
            _capFilaDato("Ubicación GPS", _capGps(p.ubicacion)),
            _capFilaDato("Notas", _capTexto(p.notas)),
        ]);
    }

    // Gasto (compra menor ya pagada) o factura de proveedor: todo escapado con _capTexto/_capMonto.
    function _capDetalleGasto(p) {
        return _capTablaDatos([
            _capFilaDato("Proveedor", _capTexto(p.proveedor)),
            _capFilaDato("RIF", _capTexto(p.rif)),
            _capFilaDato("Dirección", _capTexto(p.proveedor_direccion)),
            _capFilaDato("Teléfono", _capTexto(p.proveedor_telefono)),
            _capFilaDato("Correo", _capTexto(p.proveedor_email)),
            _capFilaDato("N.º de documento", _capTexto(p.numero_factura)),
            _capFilaDato("N.º de control", _capTexto(p.numero_control)),
            _capFilaDato("Fecha de emisión", _capTexto(p.fecha_emision)),
            _capFilaDato("Moneda", _capTexto(p.moneda)),
            _capFilaDato("Base imponible", _capMonto(p.base_imponible)),
            _capFilaDato("IVA", _capMonto(p.iva)),
            _capFilaDato("Monto total", _capMonto(p.monto_total)),
            _capFilaDato("Categoría", _capTexto(p.categoria)),
            _capFilaDato("Concepto", _capTexto(p.concepto)),
            _capFilaDato("Notas", _capTexto(p.notas)),
        ]);
    }

    // Campos que se pueden completar en un pago incompleto (formulario deshabilitado hasta que el backend lo permita).
    const _CAP_CAMPOS_COMPLETAR = {
        pago: [["monto", "Monto"], ["moneda", "Moneda"], ["metodo_pago", "Método de pago"], ["referencia", "Referencia"],
               ["banco", "Banco"], ["fecha_pago", "Fecha del pago"], ["cliente", "Cliente"], ["rif", "RIF"], ["pedido_ref", "Pedido"]],
        pago_proveedor: [["proveedor", "Proveedor"], ["monto", "Monto"], ["moneda", "Moneda"], ["metodo_pago", "Método de pago"],
                         ["referencia", "Referencia"], ["banco", "Banco"], ["fecha_pago", "Fecha del pago"], ["factura_ref", "Factura"]],
    };

    function _capFormCompletar(c) {
        const campos = _CAP_CAMPOS_COMPLETAR[c.kind];
        if (!campos || c.estado !== "INCOMPLETO") return "";
        const dis = _CAP_COMPLETAR_POR_SESION && _capPuedeGestionar ? "" : "disabled";
        const aviso = !_CAP_COMPLETAR_POR_SESION
            ? "Por ahora los datos que faltan se completan desde el grupo de Telegram («✏️ Completar») o directamente en Odoo; desde esta pantalla todavía no."
            : (!_capPuedeGestionar ? "Necesitas el permiso «gestionar capturas» para completar." : "");
        const inputs = campos.map(([k, et]) => {
            const falta = (c.incompleto || []).some(x => String(x).toLowerCase().startsWith(k.split("_")[0]));
            const tipo = k === "fecha_pago" ? "date" : "text";
            return `<label style="display:flex; flex-direction:column; font-size:0.78rem; color:#475569; gap:0.2rem;">
                ${_capEsc(et)}${falta ? ' <span style="color:#b45309;">(falta)</span>' : ""}
                <input type="${tipo}" class="filter-select" data-cap-campo="${k}" ${dis} style="cursor:text;" value="${_capEsc((c.payload || {})[k] || "")}">
            </label>`;
        }).join("");
        return `<h4 style="margin:1rem 0 0.4rem 0;">Completar datos</h4>
            ${aviso ? `<p style="color:#92400e; background:#fffbeb; border-left:4px solid #f59e0b; padding:0.5rem 0.75rem; border-radius:6px; font-size:0.82rem; margin:0 0 0.75rem 0;">${_capEsc(aviso)}</p>` : ""}
            <form id="cap-form-completar" data-cap-id="${_capEsc(c.id)}" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:0.6rem;">
                ${inputs}
                <div style="grid-column:1/-1;"><button type="submit" class="btn-primary" ${dis} style="padding:0.5rem 1rem;">Guardar datos</button></div>
            </form>`;
    }

    function _capDetalle(c) {
        const p = c.payload || {};
        const fuente = c.fuente || {};
        const vendedor = c.vendedor || c.vendedor_email || fuente.vendedor_email || p.vendedor;
        let contenido = "";
        if (c.kind === "pedido") contenido = _capDetallePedido(p);
        else if (c.kind === "pago") contenido = _capDetallePago(p, false);
        else if (c.kind === "pago_proveedor") contenido = _capDetallePago(p, true);
        else if (c.kind === "contacto") contenido = _capDetalleContacto(p);
        else if (c.kind === "gasto" || c.kind === "factura_proveedor") contenido = _capDetalleGasto(p);
        else contenido = `<pre style="white-space:pre-wrap; font-size:0.8rem;">${_capEsc(JSON.stringify(p, null, 2))}</pre>`;

        const faltan = (c.incompleto || []).length
            ? `<div style="border-left:4px solid #f59e0b; background:#fffbeb; padding:0.5rem 0.85rem; border-radius:6px; color:#92400e; margin-bottom:0.75rem;">
                 <strong>Falta:</strong> ${(c.incompleto || []).map(_capEsc).join(", ")}</div>` : "";
        const error = c.error
            ? `<div style="border-left:4px solid #e11d48; background:#fff1f2; padding:0.5rem 0.85rem; border-radius:6px; color:#9f1239; margin-bottom:0.75rem;">
                 <strong>Error:</strong> ${_capEsc(c.error)}</div>` : "";
        const reintentar = _capPuedeGestionar && (c.estado === "ERROR" || c.estado === "PENDIENTE")
            ? `<button type="button" class="btn-primary" data-cap-reintentar="${_capEsc(c.id)}" style="padding:0.5rem 1rem;">↻ Reintentar envío a Odoo</button>` : "";
        const resultado = c.resultado && Object.keys(c.resultado).length
            ? `<details style="margin-top:1rem;"><summary style="cursor:pointer; color:#475569; font-size:0.85rem;">Resultado técnico</summary>
                 <pre style="white-space:pre-wrap; font-size:0.78rem; background:#f8fafc; padding:0.6rem; border-radius:6px;">${_capEsc(JSON.stringify(c.resultado, null, 2))}</pre></details>` : "";

        return `<div style="display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap; margin-bottom:0.75rem;">
                ${_capBadge(c.estado)}
                <span class="state-badge">${_capEsc(_CAP_TIPOS[c.kind] || c.kind)}</span>
                ${c.auto ? '<span class="state-badge abierta" title="Nadie respondió en el grupo a tiempo y se envió sola">Enviada sola</span>' : ""}
            </div>
            ${faltan}${error}
            ${_capTablaDatos([
                _capFilaDato("Recibida", _capFechaHora(c.fecha_creacion)),
                _capFilaDato("Última actualización", _capFechaHora(c.fecha_actualizacion)),
                _capFilaDato("Vendedor", _capTexto(vendedor)),
                _capFilaDato("En Odoo", c.odoo_ref ? `<strong>${_capEsc(c.odoo_ref)}</strong>` : "Todavía no"),
            ])}
            <h4 style="margin:1rem 0 0.4rem 0;">Borrador</h4>
            ${contenido}
            ${_capFormCompletar(c)}
            ${resultado}
            <div style="margin-top:1.25rem; display:flex; gap:0.6rem; flex-wrap:wrap;">${reintentar}
                <button type="button" class="btn-secondary" onclick="cerrarModalCaptura()">Cerrar</button></div>
            <div id="cap-accion-resultado" style="margin-top:0.75rem;"></div>`;
    }

    function _capMostrarModal(titulo, html) {
        document.getElementById("modal-captura-titulo").textContent = titulo;
        document.getElementById("modal-captura-body").innerHTML = html;
        document.getElementById("modal-captura").style.display = "flex";
    }

    window.cerrarModalCaptura = function() {
        document.getElementById("modal-captura").style.display = "none";
    };

    async function _capAbrirDetalle(id, previo) {
        _capMostrarModal("Captura", `<p class="table-empty" style="padding:1rem !important;">Cargando...</p>`);
        try {
            const c = await _capGet("/api/bots/capturas/" + encodeURIComponent(id));
            _capMostrarModal(`${_CAP_TIPOS[c.kind] || "Captura"} · ${_capFechaHora(c.fecha_creacion)}`, _capDetalle(c));
        } catch (err) {
            if (previo) {   // sin el detalle individual, se muestra lo que ya trae la lista
                _capMostrarModal(`${_CAP_TIPOS[previo.kind] || "Captura"} · ${_capFechaHora(previo.fecha_creacion)}`, _capDetalle(previo));
            } else {
                _capMostrarModal("Captura", `<div style="border-left:4px solid #e11d48; padding:0.5rem 0.85rem; background:#fff1f2; border-radius:6px; color:#9f1239;">❌ ${_capEsc(err.message)}</div>`);
            }
        }
    }

    async function _capReintentar(id, btn) {
        if (!confirm("¿Reintentar el envío de esta captura a Odoo?")) return;
        const out = document.getElementById("cap-accion-resultado");
        btn.disabled = true;
        try {
            const res = await fetch(`/api/bots/capturas/${encodeURIComponent(id)}/reintentar`, { method: "POST" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
            await _capAbrirDetalle(id);
            const out2 = document.getElementById("cap-accion-resultado");
            if (out2) out2.innerHTML = `<div style="border-left:4px solid #10b981; padding:0.5rem 0.85rem; background:#ecfdf5; border-radius:6px; color:#065f46;">✅ Reintento solicitado. El envío a Odoo se hace en segundo plano: actualiza en unos segundos.</div>`;
            await loadCapturas();
        } catch (err) {
            if (out) out.innerHTML = `<div style="border-left:4px solid #e11d48; padding:0.5rem 0.85rem; background:#fff1f2; border-radius:6px; color:#9f1239;">❌ ${_capEsc(err.message)}</div>`;
            btn.disabled = false;
        }
    }

    async function _capCompletar(form) {
        const id = form.dataset.capId;
        const cambios = {};
        form.querySelectorAll("[data-cap-campo]").forEach(inp => {
            const v = inp.value.trim();
            if (v) cambios[inp.dataset.capCampo] = v;
        });
        const out = document.getElementById("cap-accion-resultado");
        if (!Object.keys(cambios).length) {
            out.innerHTML = `<div style="color:#9f1239;">Escribe al menos un dato.</div>`;
            return;
        }
        try {
            const res = await fetch(`/api/bots/capturas/${encodeURIComponent(id)}/completar`, {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cambios }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
            await _capAbrirDetalle(id);
            await loadCapturas();
        } catch (err) {
            out.innerHTML = `<div style="border-left:4px solid #e11d48; padding:0.5rem 0.85rem; background:#fff1f2; border-radius:6px; color:#9f1239;">❌ ${_capEsc(err.message)}</div>`;
        }
    }

    // --- Eventos de la pantalla ---------------------------------------------------
    (function _capEventos() {
        const estadoEl = document.getElementById("capturas-estado");
        const kindEl = document.getElementById("capturas-kind");
        const buscarEl = document.getElementById("capturas-buscar");
        if (!estadoEl) return;
        const refiltrar = () => { _capOffset = 0; loadCapturasLista(); };
        estadoEl.addEventListener("change", refiltrar);
        kindEl.addEventListener("change", () => { _capOffset = 0; _capContadores(); loadCapturasLista(); });
        let t = null;
        buscarEl.addEventListener("input", () => { clearTimeout(t); t = setTimeout(refiltrar, 300); });

        const irAEstado = (e) => { estadoEl.value = e; refiltrar(); };
        document.querySelectorAll(".cap-kpi").forEach(k => {
            k.addEventListener("click", () => irAEstado(k.dataset.capEstado));
            k.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); irAEstado(k.dataset.capEstado); } });
        });

        document.getElementById("capturas-body").addEventListener("click", (e) => {
            const b = e.target.closest("[data-cap-ver]");
            if (!b) return;
            const c = _capItems[Number(b.dataset.capVer)];
            if (c) _capAbrirDetalle(c.id, c);
        });
        document.getElementById("capturas-paginacion").addEventListener("click", (e) => {
            const b = e.target.closest("[data-cap-pag]");
            if (!b || b.disabled) return;
            _capOffset = Math.max(0, _capOffset + (b.dataset.capPag === "sig" ? _CAP_PAGINA : -_CAP_PAGINA));
            loadCapturasLista();
        });
        const modal = document.getElementById("modal-captura");
        modal.addEventListener("click", (e) => {
            if (e.target === modal) window.cerrarModalCaptura();
            const r = e.target.closest("[data-cap-reintentar]");
            if (r) _capReintentar(r.dataset.capReintentar, r);
        });
        modal.addEventListener("submit", (e) => {
            if (e.target.id === "cap-form-completar") { e.preventDefault(); _capCompletar(e.target); }
        });
        document.addEventListener("keydown", (e) => { if (e.key === "Escape") window.cerrarModalCaptura(); });
    })();

    // ===================================================================
    // DIRECTORIO DE CONTACTOS Y PLAN DE CUENTAS EN ODOO (Solo lectura)
    // API: GET /api/odoo/contactos, GET /api/odoo/plan-cuentas
    // Clasifica contactos en Prospectos, Clientes, Proveedores y Ambos, y
    // propone cómo clasificar compras y gastos contra el plan de cuentas.
    // ===================================================================
    const _DIR_PAGINA = 50;
    let _dirOffset = 0;
    let _dirCargaId = 0;
    let _dirCargadoUnaVez = false;
    let _ctaCargaId = 0;
    let _ctaCargadoUnaVez = false;
    let _ctaTodasCuentas = [];

    function _dirBadgeClasificacion(c) {
        const mapa = {
            prospecto: ["amarillo", "Prospecto"],
            cliente: ["verde", "Cliente"],
            proveedor: ["abierta", "Proveedor"],
            ambos: ["cierre", "Cliente y proveedor"],
        };
        const [clase, et] = mapa[c.clasificacion] || ["", c.clasificacion_etiqueta || c.clasificacion];
        const bot = c.es_borrador_telegram
            ? ` <span class="state-badge" style="background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe;" title="Creado como borrador desde los bots de Telegram">📲 Borrador Telegram</span>`
            : "";
        return `<span class="semaphore ${clase}">${_capEsc(et)}</span>${bot}`;
    }

    function _dirFilaContacto(c) {
        const tipoPersona = c.es_empresa ? "🏢 Empresa" : "👤 Persona";
        const contactoInfo = [
            c.telefono ? `📞 ${_capEsc(c.telefono)}` : "",
            c.email ? `✉️ ${_capEsc(c.email)}` : "",
        ].filter(Boolean).join("<br>") || "—";
        const ubicacionVend = [
            c.ciudad ? `📍 ${_capEsc(c.ciudad)}` : "",
            c.vendedor ? `🧑‍💼 ${_capEsc(c.vendedor)}` : "",
        ].filter(Boolean).join("<br>") || "—";
        const origenNotas = [
            c.referencia ? `<code>${_capEsc(c.referencia)}</code>` : "",
            c.notas ? `<span style="color:#475569; font-size:0.78rem;">${_capEsc(c.notas.slice(0, 120))}${c.notas.length > 120 ? "…" : ""}</span>` : "",
        ].filter(Boolean).join("<br>") || "—";
        return `<tr>
            <td style="white-space:nowrap; color:#64748b;">#${Number(c.id)}</td>
            <td><strong>${_capEsc(c.nombre)}</strong><div style="font-size:0.75rem; color:#64748b;">${tipoPersona}</div></td>
            <td style="white-space:nowrap;">${c.rif ? `<code>${_capEsc(c.rif)}</code>` : '<span style="color:#94a3b8;">Sin RIF</span>'}</td>
            <td>${_dirBadgeClasificacion(c)}</td>
            <td style="font-size:0.82rem;">${contactoInfo}</td>
            <td style="font-size:0.82rem;">${ubicacionVend}</td>
            <td style="font-size:0.82rem; max-width:260px;">${origenNotas}</td>
        </tr>`;
    }

    function _dirPaginacion(total, desde, hasta) {
        const cont = document.getElementById("dir-paginacion");
        if (!cont) return;
        if (!total) { cont.innerHTML = ""; return; }
        const ant = desde > 0, sig = hasta < total;
        cont.innerHTML = `<span>${desde + 1}–${hasta} de ${total}</span>
            <button type="button" class="btn-secondary" data-dir-pag="ant" ${ant ? "" : "disabled"}>← Anterior</button>
            <button type="button" class="btn-secondary" data-dir-pag="sig" ${sig ? "" : "disabled"}>Siguiente →</button>`;
    }

    async function loadContactosOdoo(refresh = false) {
        const body = document.getElementById("dir-contactos-body");
        const resumenEl = document.getElementById("dir-resumen");
        if (!body) return;
        const clasificacion = (document.getElementById("dir-clasificacion")?.value || "").trim();
        const q = (document.getElementById("dir-buscar")?.value || "").trim();
        const miCarga = ++_dirCargaId;

        if (refresh) _dirOffset = 0;
        body.innerHTML = `<tr><td colspan="7" class="table-empty">Consultando contactos en Odoo...</td></tr>`;

        const p = new URLSearchParams({
            limit: String(_DIR_PAGINA),
            offset: String(_dirOffset),
        });
        if (clasificacion) p.set("clasificacion", clasificacion);
        if (q) p.set("q", q);
        if (refresh) p.set("refresh", "true");

        try {
            const data = await _capGet("/api/odoo/contactos?" + p.toString());
            if (miCarga !== _dirCargaId) return;
            _dirCargadoUnaVez = true;
            const r = data.resumen || {};
            const setKpi = (id, val) => {
                const el = document.getElementById(id);
                if (el) el.textContent = val ?? 0;
            };
            setKpi("dir-n-total", r.total);
            setKpi("dir-n-prospectos", r.prospectos);
            setKpi("dir-n-clientes", r.clientes_total ?? r.clientes);
            setKpi("dir-n-proveedores", r.proveedores_total ?? r.proveedores);
            setKpi("dir-n-ambos", r.ambos);
            setKpi("dir-n-borradores", r.borradores_telegram);

            const filas = data.items || [];
            const totalFiltrado = data.total_filtrado ?? filas.length;
            if (resumenEl) {
                resumenEl.textContent = `${totalFiltrado} contacto${totalFiltrado === 1 ? "" : "s"} encontrado${totalFiltrado === 1 ? "" : "s"}`;
            }
            if (!filas.length) {
                body.innerHTML = `<tr><td colspan="7" class="table-empty">No se encontraron contactos con esos filtros.</td></tr>`;
                _dirPaginacion(0, 0, 0);
                return;
            }
            body.innerHTML = filas.map(_dirFilaContacto).join("");
            _dirPaginacion(totalFiltrado, _dirOffset, _dirOffset + filas.length);
        } catch (err) {
            if (miCarga !== _dirCargaId) return;
            body.innerHTML = `<tr><td colspan="7" class="table-empty">❌ ${_capEsc(err.message)}</td></tr>`;
            if (resumenEl) resumenEl.textContent = "";
            _dirPaginacion(0, 0, 0);
        }
    }
    window.loadContactosOdoo = loadContactosOdoo;

    function _ctaChipCuenta(cta) {
        if (!cta) return `<span style="color:#94a3b8;">Sin cuenta sugerida en el plan actual</span>`;
        const codigo = cta.code ? `<strong>${_capEsc(cta.code)}</strong> · ` : "";
        return `<div style="background:#eff6ff; border:1px solid #bfdbfe; color:#1e3a8a; padding:0.35rem 0.6rem; border-radius:6px; font-size:0.82rem; display:inline-block;">
            ${codigo}${_capEsc(cta.name)}
            <div style="font-size:0.72rem; color:#3b82f6;">${_capEsc(cta.tipo_nombre || cta.account_type)}</div>
        </div>`;
    }

    function _ctaFilaPropuesta(p) {
        const tipoBot = p.kind_bot === "factura_proveedor"
            ? `<span class="state-badge cierre">Factura de proveedor</span>`
            : `<span class="state-badge abierta">Gasto (compra menor)</span>`;
        const ejemplos = (p.ejemplos || []).map(e => `<li>${_capEsc(e)}</li>`).join("");
        const alts = (p.alternativas || []).length
            ? (p.alternativas || []).map(a => `<div style="font-size:0.78rem; color:#334155; margin-bottom:0.2rem;">• <code>${_capEsc(a.code || "—")}</code> ${_capEsc(a.name)}</div>`).join("")
            : `<span style="color:#94a3b8; font-size:0.78rem;">—</span>`;
        const cxp = p.cuenta_cxp_sugerida
            ? `<div style="margin-top:0.35rem; font-size:0.76rem; color:#0f766e;"><strong>Haber (CxP):</strong> <code>${_capEsc(p.cuenta_cxp_sugerida.code)}</code> ${_capEsc(p.cuenta_cxp_sugerida.name)}</div>`
            : "";
        return `<tr>
            <td>
                <strong style="font-size:0.92rem; color:#0f172a; text-transform:capitalize;">${_capEsc(p.categoria)}</strong>
                <div style="font-size:0.78rem; color:#475569; margin:0.15rem 0 0.35rem 0;">${_capEsc(p.titulo)}</div>
                ${tipoBot}
            </td>
            <td><ul style="margin:0; padding-left:1.1rem; font-size:0.8rem; color:#334155;">${ejemplos}</ul></td>
            <td>${_ctaChipCuenta(p.cuenta_sugerida)}</td>
            <td>${alts}</td>
            <td style="font-size:0.8rem; color:#475569; max-width:290px;">${_capEsc(p.criterio_contable || "")}${cxp}</td>
        </tr>`;
    }

    function _ctaRenderCatalogoLocal() {
        const body = document.getElementById("cta-catalogo-body");
        const resumenEl = document.getElementById("cta-resumen");
        if (!body) return;
        const grupo = (document.getElementById("cta-grupo")?.value || "").trim().toLowerCase();
        const q = (document.getElementById("cta-buscar")?.value || "").trim().toLowerCase();

        let filtradas = _ctaTodasCuentas;
        if (grupo) {
            filtradas = filtradas.filter(c => (c.grupo || "").toLowerCase() === grupo);
        }
        if (q) {
            filtradas = filtradas.filter(c =>
                `${c.code || ""} ${c.name || ""} ${c.tipo_nombre || ""} ${c.account_type || ""}`.toLowerCase().includes(q)
            );
        }
        if (resumenEl) {
            resumenEl.textContent = `${filtradas.length} cuenta${filtradas.length === 1 ? "" : "s"} mostrada${filtradas.length === 1 ? "" : "s"}`;
        }
        if (!filtradas.length) {
            body.innerHTML = `<tr><td colspan="5" class="table-empty">No hay cuentas contables que coincidan con el filtro.</td></tr>`;
            return;
        }
        const grupoBadge = {
            gastos: ["amarillo", "Gastos / Costos"],
            pasivo: ["rojo", "Pasivo / CxP"],
            activo: ["verde", "Activo"],
            ingreso: ["abierta", "Ingreso"],
            otros: ["", "Otros"],
        };
        body.innerHTML = filtradas.map(c => {
            const [cl, et] = grupoBadge[c.grupo] || ["", c.grupo];
            return `<tr>
                <td style="white-space:nowrap;"><code><strong>${_capEsc(c.code || "—")}</strong></code></td>
                <td><strong>${_capEsc(c.name)}</strong></td>
                <td style="font-size:0.82rem; color:#475569;">${_capEsc(c.tipo_nombre || c.account_type)} <small style="color:#94a3b8;">(${_capEsc(c.account_type)})</small></td>
                <td><span class="semaphore ${cl}">${_capEsc(et)}</span></td>
                <td>${c.reconcile ? "Sí" : "No"}</td>
            </tr>`;
        }).join("");
    }

    async function loadPlanCuentasOdoo(refresh = false) {
        const propBody = document.getElementById("cta-propuestas-body");
        const catBody = document.getElementById("cta-catalogo-body");
        if (!propBody || !catBody) return;
        const miCarga = ++_ctaCargaId;
        propBody.innerHTML = `<tr><td colspan="5" class="table-empty">Consultando plan de cuentas en Odoo...</td></tr>`;
        catBody.innerHTML = `<tr><td colspan="5" class="table-empty">Cargando catálogo contable...</td></tr>`;

        const p = new URLSearchParams();
        if (refresh) p.set("refresh", "true");

        try {
            const data = await _capGet("/api/odoo/plan-cuentas?" + p.toString());
            if (miCarga !== _ctaCargaId) return;
            _ctaCargadoUnaVez = true;
            const r = data.resumen || {};
            const setKpi = (id, val) => {
                const el = document.getElementById(id);
                if (el) el.textContent = val ?? 0;
            };
            setKpi("cta-n-total", r.total);
            setKpi("cta-n-gastos", r.gastos);
            setKpi("cta-n-costo", r.costo_directo);
            setKpi("cta-n-pasivo", r.pasivo);
            setKpi("cta-n-activo", r.activo);

            const propuestas = data.propuestas || [];
            propBody.innerHTML = propuestas.length
                ? propuestas.map(_ctaFilaPropuesta).join("")
                : `<tr><td colspan="5" class="table-empty">No se encontraron cuentas en Odoo para armar propuestas.</td></tr>`;

            _ctaTodasCuentas = data.cuentas || [];
            _ctaRenderCatalogoLocal();
        } catch (err) {
            if (miCarga !== _ctaCargaId) return;
            propBody.innerHTML = `<tr><td colspan="5" class="table-empty">❌ ${_capEsc(err.message)}</td></tr>`;
            catBody.innerHTML = `<tr><td colspan="5" class="table-empty">❌ ${_capEsc(err.message)}</td></tr>`;
        }
    }
    window.loadPlanCuentasOdoo = loadPlanCuentasOdoo;

    async function _ctaSimularConcepto(ev) {
        ev.preventDefault();
        const inp = document.getElementById("cta-concepto-input");
        const out = document.getElementById("cta-simulador-resultado");
        if (!inp || !out) return;
        const concepto = inp.value.trim();
        if (!concepto) {
            out.innerHTML = `<div style="color:#9f1239; font-size:0.85rem;">Escribe el concepto de la compra o gasto para clasificarlo.</div>`;
            return;
        }
        out.innerHTML = `<div style="color:#475569; font-size:0.85rem;">Clasificando concepto...</div>`;
        try {
            const data = await _capGet("/api/odoo/plan-cuentas?concepto=" + encodeURIComponent(concepto));
            const s = data.sugerencia_concepto;
            if (!s) {
                out.innerHTML = "";
                return;
            }
            const tipoBot = s.kind_bot === "factura_proveedor" ? "Factura de proveedor (CxP)" : "Gasto (compra menor)";
            const kws = (s.palabras_detectadas || []).length
                ? `Palabras clave detectadas: <code>${s.palabras_detectadas.map(_capEsc).join(", ")}</code>`
                : "Clasificación por defecto (revisar categoría si aplica otra específica)";
            out.innerHTML = `<div style="background:#ffffff; border:1px solid #cbd5e1; border-left:4px solid #10b981; border-radius:8px; padding:0.85rem 1rem; display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:0.75rem; align-items:start;">
                <div>
                    <div style="font-size:0.75rem; color:#64748b; text-transform:uppercase; font-weight:600;">Categoría Propuesta</div>
                    <strong style="font-size:1rem; color:#0f172a; text-transform:capitalize;">${_capEsc(s.categoria_sugerida)}</strong>
                    <div style="font-size:0.8rem; color:#475569;">${_capEsc(s.titulo_categoria)} · <strong>${_capEsc(tipoBot)}</strong></div>
                    <div style="font-size:0.75rem; color:#64748b; margin-top:0.25rem;">${kws}</div>
                </div>
                <div>
                    <div style="font-size:0.75rem; color:#64748b; text-transform:uppercase; font-weight:600; margin-bottom:0.25rem;">Cuenta Contable Sugerida en Odoo</div>
                    ${_ctaChipCuenta(s.cuenta_sugerida)}
                </div>
                <div style="font-size:0.8rem; color:#334155;">
                    <div style="font-size:0.75rem; color:#64748b; text-transform:uppercase; font-weight:600;">Criterio Contable</div>
                    ${_capEsc(s.criterio_contable || "")}
                </div>
            </div>`;
        } catch (err) {
            out.innerHTML = `<div style="border-left:4px solid #e11d48; padding:0.5rem 0.85rem; background:#fff1f2; border-radius:6px; color:#9f1239;">❌ ${_capEsc(err.message)}</div>`;
        }
    }

    function applyCapturasSubpage(subpage) {
        if (subpage === "contactos") {
            window.CxC?.navigate("contactos");
        } else if (subpage === "cuentas" || subpage === "aprobacion") {
            window.CxC?.navigate("compras", subpage === "cuentas" ? "cuentas" : "facturas");
        } else {
            window.CxC?.navigate("auditoria", "integraciones");
        }
    }

    window.irACapturasSubpage = function(subpage) {
        applyCapturasSubpage(subpage);
    };

    (function _dirYPlanEventos() {
        document.querySelectorAll(".capturas-subnav-btn").forEach((btn) => {
            btn.addEventListener("click", () => {
                applyCapturasSubpage(btn.getAttribute("data-capturas-subpage"));
            });
        });

        const dirClasif = document.getElementById("dir-clasificacion");
        const dirBuscar = document.getElementById("dir-buscar");
        if (dirClasif && dirBuscar) {
            const refiltrarDir = () => { _dirOffset = 0; loadContactosOdoo(false); };
            dirClasif.addEventListener("change", refiltrarDir);
            let tDir = null;
            dirBuscar.addEventListener("input", () => {
                clearTimeout(tDir);
                tDir = setTimeout(refiltrarDir, 300);
            });
            document.querySelectorAll(".dir-kpi").forEach((k) => {
                const activar = () => {
                    dirClasif.value = k.dataset.dirClasificacion || "";
                    refiltrarDir();
                };
                k.addEventListener("click", activar);
                k.addEventListener("keydown", (ev) => {
                    if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); activar(); }
                });
            });
            document.getElementById("dir-paginacion")?.addEventListener("click", (e) => {
                const b = e.target.closest("[data-dir-pag]");
                if (!b || b.disabled) return;
                _dirOffset = Math.max(0, _dirOffset + (b.dataset.dirPag === "sig" ? _DIR_PAGINA : -_DIR_PAGINA));
                loadContactosOdoo(false);
            });
        }

        const ctaGrupo = document.getElementById("cta-grupo");
        const ctaBuscar = document.getElementById("cta-buscar");
        if (ctaGrupo && ctaBuscar) {
            ctaGrupo.addEventListener("change", _ctaRenderCatalogoLocal);
            ctaBuscar.addEventListener("input", _ctaRenderCatalogoLocal);
        }
        document.getElementById("cta-form-simulador")?.addEventListener("submit", _ctaSimularConcepto);
    })();

    // ===================================================================
    // BOTS DE TELEGRAM: panel de administración de la familia de bots.
    // API: GET /api/bots, GET /api/bots/{bot}, PATCH /api/bots/{bot}, GET /api/bots/destinos,
    //      GET|POST /api/bots/usuarios, DELETE /api/bots/usuarios/{telegram_id}.
    // Permisos: bots.ver (consultar) y bots.gestionar (cambiar). No maneja tokens ni claves.
    // ===================================================================
    const _BT_BOTS = {
        pedidos: { nombre: "Ventas", detalle: "Pedidos, cotizaciones y contactos" },
        cobranza: { nombre: "Cobranzas", detalle: "Pagos de clientes" },
        compras: { nombre: "Compras", detalle: "Pagos, facturas y proveedores" },
        eventos: { nombre: "Eventos", detalle: "Activaciones y calendario" },
        asana: { nombre: "Asana", detalle: "Tareas del equipo en Asana" },
    };
    const _BT_TIPOS_CAPTURA = {
        pedido: "Pedido", contacto: "Contacto", pago: "Pago de cliente",
        pago_proveedor: "Pago a proveedor", proveedor: "Proveedor", factura_proveedor: "Factura de proveedor",
        gasto: "Gasto (compra menor)",
    };
    const _BT_ESTADO_DESTINO = {
        ok: ["verde", "Conectado"], error: ["rojo", "Con error"], desactivado: ["gris", "Desactivado"],
        sin_clave: ["amarillo", "Sin clave"], sin_datos: ["gris", "Sin datos"],
    };
    const _BT_REFRESCO_MS = 30000;
    let _btPuedeGestionar = false;
    let _btBots = [];
    let _btUsuarios = [];
    let _btCarga = 0;
    let _btTimer = null;
    let _btModalBot = null;

    const _btEsc = (v) => _bancosEsc(v);

    async function _btFetch(url, opts) {
        const res = await fetch(url, opts);
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || data.detail || `Error ${res.status}`);
        return data;
    }

    const _btJson = (metodo, cuerpo) => ({
        method: metodo, headers: { "Content-Type": "application/json" }, body: JSON.stringify(cuerpo),
    });

    function _btMensaje(tipo, texto) {
        const [borde, fondo, color, icono] = tipo === "ok"
            ? ["#10b981", "#ecfdf5", "#065f46", "✅"] : ["#e11d48", "#fff1f2", "#9f1239", "❌"];
        return `<div style="border-left:4px solid ${borde}; padding:0.5rem 0.85rem; background:${fondo}; border-radius:6px; color:${color};">${icono} ${_btEsc(texto)}</div>`;
    }

    function _btHace(segundos) {
        if (segundos == null) return "nunca";
        if (segundos < 60) return `hace ${Math.max(segundos, 0)} s`;
        if (segundos < 3600) return `hace ${Math.floor(segundos / 60)} min`;
        if (segundos < 86400) return `hace ${Math.floor(segundos / 3600)} h`;
        return `hace ${Math.floor(segundos / 86400)} d`;
    }

    function _btSemaforo(estado) {
        const [clase, texto] = _BT_ESTADO_DESTINO[estado] || ["gris", estado || "—"];
        return `<span class="bt-dot ${clase}" aria-hidden="true"></span>${_btEsc(texto)}`;
    }

    // ── Salud de destinos ─────────────────────────────────────────────────────
    function _btRenderDestinos(d) {
        const tarjeta = (titulo, bloque, pie) => `<div class="bt-dest"><h4>${_btEsc(titulo)}</h4><strong>${_btSemaforo(bloque.estado)}</strong><small>${pie}</small></div>`;
        const porBot = (b) => {
            const lista = Object.entries(b.por_bot || {}).map(([bot, e]) => `${_btEsc((_BT_BOTS[bot] || {}).nombre || bot)}: ${_btEsc(e)}`);
            return lista.length ? lista.join(" · ") : "Ningún bot en línea lo reporta";
        };
        const c = d.capturas || {};
        document.getElementById("bots-destinos").innerHTML = [
            tarjeta("Omni ERP", d.omni, porBot(d.omni)),
            tarjeta("CxC", d.cxc, porBot(d.cxc)),
            tarjeta("Gemini (IA)", d.gemini, porBot(d.gemini)),
            `<div class="bt-dest"><h4>Cola de reintentos</h4><strong>${Number(d.outbox_pendientes) || 0} pendiente${Number(d.outbox_pendientes) === 1 ? "" : "s"}</strong><small>Envíos que esperan a que un destino vuelva</small></div>`,
            `<div class="bt-dest"><h4>Capturas con problema</h4><strong>${Number(c.INCOMPLETO) || 0} incompletas · ${Number(c.ERROR) || 0} con error</strong><small><a href="/capturas" data-page="capturas" class="nav-link">Ver capturas →</a></small></div>`,
            `<div class="bt-dest"><h4>Bots en línea</h4><strong>${Number(d.bots_en_linea) || 0} de ${Number(d.bots_total) || 0}</strong><small>Latido en los últimos 3 minutos</small></div>`,
        ].join("");
    }

    // ── Tarjetas de bots ──────────────────────────────────────────────────────
    function _btTarjeta(b) {
        const meta = _BT_BOTS[b.bot] || { nombre: b.nombre, detalle: "" };
        const estado = !b.visto_alguna_vez ? ["gris", "Nunca visto"]
            : b.en_linea ? ["verde", "En línea"] : ["rojo", "Fuera de línea"];
        const e = b.estado || {};
        const pausa = !b.habilitado;
        const visto = b.visto_alguna_vez ? `Último latido ${_btHace(b.hace_segundos)}${b.version ? " · v" + _btEsc(b.version) : ""}` : "Todavía no se ha conectado";
        const chips = ["omni", "cxc", "gemini"].map((k) => {
            const [clase, texto] = _BT_ESTADO_DESTINO[e[k]] || ["gris", "—"];
            return `<span class="bt-chip"><span class="bt-dot ${clase}" aria-hidden="true"></span>${k === "gemini" ? "Gemini" : k === "omni" ? "Omni" : "CxC"}: ${_btEsc(texto)}</span>`;
        }).join("");
        const seguro = typeof b.url_panel === "string" && /^https?:\/\//i.test(b.url_panel);
        const enlace = b.bot === "asana" && seguro
            ? `<a class="btn-secondary" href="${_btEsc(b.url_panel)}" target="_blank" rel="noopener noreferrer">Abrir su panel ↗</a>`
            : (b.bot === "asana" ? `<span class="cap-sub">Configura la dirección de su panel en «Configurar».</span>` : "");
        return `<article class="card bt-card${pausa ? " bt-pausa" : ""}" data-bt-bot="${_btEsc(b.bot)}">
            <div class="bt-card-cab">
                <div><h3>${_btEsc(meta.nombre)}</h3><p>${_btEsc(meta.detalle)}</p></div>
                <span class="bt-estado"><span class="bt-dot ${estado[0]}" aria-hidden="true"></span>${estado[1]}</span>
            </div>
            <div class="cap-sub" style="margin:0;">${_btEsc(visto)}${e.modo ? " · " + _btEsc(e.modo) : ""}</div>
            ${pausa ? `<div class="bt-aviso">⏸ En pausa: el bot no atiende mensajes hasta que lo reanudes.</div>` : ""}
            ${b.mensaje ? `<div class="cap-sub">📣 ${_btEsc(b.mensaje)}</div>` : ""}
            <div class="bt-metricas">
                <div><b>${Number(e.mensajes_hoy) || 0}</b><span>Mensajes hoy</span></div>
                <div><b>${Number(e.errores_hoy) || 0}</b><span>Errores hoy</span></div>
                <div><b>${Number(e.outbox_pendientes) || 0}</b><span>En cola</span></div>
            </div>
            <div class="bt-chips">${chips}<span class="bt-chip">👥 ${Number(b.n_usuarios) || 0} usuario${Number(b.n_usuarios) === 1 ? "" : "s"}</span></div>
            ${e.ultimo_error ? `<div class="bt-error">Último error: ${_btEsc(e.ultimo_error)}</div>` : ""}
            <div class="bt-acciones">
                <button type="button" class="btn-secondary" data-bt-config="${_btEsc(b.bot)}">${_btPuedeGestionar ? "Configurar" : "Ver detalle"}</button>
                ${_btPuedeGestionar ? `<button type="button" class="btn-secondary" data-bt-pausar="${_btEsc(b.bot)}" data-bt-habilitado="${b.habilitado ? "1" : "0"}">${b.habilitado ? "⏸ Pausar" : "▶ Reanudar"}</button>` : ""}
                ${enlace}
            </div>
        </article>`;
    }

    // ── Usuarios autorizados ──────────────────────────────────────────────────
    function _btRenderUsuarios() {
        const body = document.getElementById("bots-usuarios-body");
        if (!_btUsuarios.length) {
            body.innerHTML = `<tr><td colspan="5" class="table-empty">Todavía no hay usuarios autorizados: los bots ignoran a todo el mundo hasta que agregues a alguien.</td></tr>`;
            return;
        }
        body.innerHTML = _btUsuarios.map((u, i) => `<tr>
            <td style="white-space:nowrap;">${_btEsc(u.telegram_id)}</td>
            <td>${u.nombre ? _btEsc(u.nombre) : "—"}</td>
            <td>${u.email ? _btEsc(u.email) : "—"}</td>
            <td><div class="bt-chips">${(u.bots || []).map((b) => `<span class="bt-chip">${_btEsc((_BT_BOTS[b] || {}).nombre || b)}</span>`).join("")}</div></td>
            <td style="white-space:nowrap;">${_btPuedeGestionar
                ? `<button type="button" class="btn-secondary" data-bt-editar="${i}">Editar</button> <button type="button" class="btn-secondary" data-bt-borrar="${i}">Quitar</button>` : ""}</td>
        </tr>`).join("");
    }

    function _btPrepararFormUsuario() {
        const form = document.getElementById("bots-form-usuario");
        form.style.display = _btPuedeGestionar ? "block" : "none";
        document.getElementById("bots-u-bots").innerHTML = Object.entries(_BT_BOTS).map(([id, m]) =>
            `<label><input type="checkbox" value="${id}" data-bt-u-bot> ${_btEsc(m.nombre)}</label>`).join("");
    }

    function _btLimpiarFormUsuario() {
        document.getElementById("bots-form-usuario").reset();
        document.getElementById("bots-u-id").readOnly = false;
        document.getElementById("bots-form-titulo").textContent = "Agregar usuario";
        document.getElementById("bots-u-cancelar").style.display = "none";
    }

    async function _btGuardarUsuario(ev) {
        ev.preventDefault();
        const out = document.getElementById("bots-u-resultado");
        const bots = [...document.querySelectorAll("[data-bt-u-bot]:checked")].map((c) => c.value);
        out.innerHTML = "";
        try {
            await _btFetch("/api/bots/usuarios", _btJson("POST", {
                telegram_id: document.getElementById("bots-u-id").value.trim(),
                nombre: document.getElementById("bots-u-nombre").value.trim(),
                email: document.getElementById("bots-u-email").value.trim(),
                bots,
            }));
            _btLimpiarFormUsuario();
            out.innerHTML = _btMensaje("ok", "Usuario guardado. Los bots lo aplican en menos de un minuto.");
            await loadBots();
        } catch (err) {
            out.innerHTML = _btMensaje("error", err.message);
        }
    }

    async function _btBorrarUsuario(u) {
        if (!confirm(`¿Quitar a ${u.nombre || u.telegram_id} de los bots? Dejará de poder usarlos.`)) return;
        try {
            await _btFetch(`/api/bots/usuarios/${encodeURIComponent(u.telegram_id)}`, { method: "DELETE" });
            await loadBots();
        } catch (err) {
            document.getElementById("bots-aviso").innerHTML = _btMensaje("error", err.message);
        }
    }

    // ── Pausar / reanudar ─────────────────────────────────────────────────────
    async function _btPausar(bot, habilitado) {
        const nombre = (_BT_BOTS[bot] || {}).nombre || bot;
        if (habilitado && !confirm(`¿Pausar el bot de ${nombre}? Dejará de atender mensajes hasta que lo reanudes.`)) return;
        try {
            await _btFetch(`/api/bots/${encodeURIComponent(bot)}`, _btJson("PATCH", { habilitado: !habilitado }));
            await loadBots();
        } catch (err) {
            document.getElementById("bots-aviso").innerHTML = _btMensaje("error", err.message);
        }
    }

    // ── Detalle y configuración (modal) ───────────────────────────────────────
    function _btCapturasHtml(lista) {
        if (!lista.length) return `<p class="cap-sub">Este bot todavía no ha enviado capturas a CxC.</p>`;
        return `<div class="table-wrapper"><table class="cxc-table"><thead><tr><th>Recibida</th><th>Tipo</th><th>Estado</th><th>Detalle</th></tr></thead><tbody>${lista.map((c) => {
            const clase = { ENVIADO: "verde", ERROR: "rojo", INCOMPLETO: "amarillo", PENDIENTE: "amarillo" }[c.estado] || "";
            const nota = c.error ? _btEsc(c.error) : ((c.incompleto || []).length ? "Falta: " + c.incompleto.map(_btEsc).join(", ") : (c.odoo_ref ? _btEsc(c.odoo_ref) : "—"));
            return `<tr><td style="white-space:nowrap;">${_capFechaHora(c.creada_en)}</td><td>${_btEsc(_BT_TIPOS_CAPTURA[c.kind] || c.kind)}</td><td><span class="semaphore ${clase}">${_btEsc(c.estado)}</span></td><td>${nota}</td></tr>`;
        }).join("")}</tbody></table></div>`;
    }

    function _btResumenCambio(c) {
        const antes = c.antes || {};
        const despues = c.despues || {};
        const claves = [...new Set([...Object.keys(antes), ...Object.keys(despues)])];
        const corto = (v) => (v && typeof v === "object" ? JSON.stringify(v) : String(v == null ? "—" : v));
        return claves.map((k) => `${_btEsc(k)}: ${_btEsc(corto(antes[k]))} → ${_btEsc(corto(despues[k]))}`).join("; ") || "—";
    }

    function _btDetalleHtml(d) {
        const p = d.parametros || {};
        const ch = d.chats || {};
        const dis = _btPuedeGestionar ? "" : " disabled";
        const modelos = (d.modelos_sugeridos || []).map((m) => `<option value="${_btEsc(m)}"></option>`).join("");
        const hist = (d.historial || []).length
            ? `<table class="cxc-table"><thead><tr><th>Día</th><th>Latidos</th><th>Mensajes</th><th>Errores</th></tr></thead><tbody>${d.historial.map((h) => `<tr><td>${_btEsc(h.dia)}</td><td>${Number(h.latidos) || 0}</td><td>${Number(h.mensajes) || 0}</td><td>${Number(h.errores) || 0}</td></tr>`).join("")}</tbody></table>`
            : `<p class="cap-sub">Sin actividad registrada en los últimos días.</p>`;
        const bit = (d.bitacora || []).length
            ? `<table class="cxc-table"><thead><tr><th>Cuándo</th><th>Quién</th><th>Cambio</th></tr></thead><tbody>${d.bitacora.map((c) => `<tr><td style="white-space:nowrap;">${_capFechaHora(c.en)}</td><td>${_btEsc(c.usuario)}</td><td>${_btResumenCambio(c)}</td></tr>`).join("")}</tbody></table>`
            : `<p class="cap-sub">Nadie ha cambiado la configuración de este bot.</p>`;
        const claveGemini = (d.estado || {}).gemini;
        return `<form id="bot-form-config" data-bt-bot="${_btEsc(d.bot)}" autocomplete="off">
            <div class="bt-cfg">
                <label>Estado del bot
                    <span><label style="flex-direction:row; align-items:center; gap:0.4rem;"><input type="checkbox" id="bot-c-habilitado"${d.habilitado ? " checked" : ""}${dis}> Activo (atiende mensajes)</label></span>
                </label>
                <label>Envío automático tras (minutos)<input type="number" id="bot-c-auto" min="1" max="120" value="${_btEsc(p.auto_envio_min)}"${dis}></label>
                <label>Modelo de Gemini<input type="text" id="bot-c-modelo" list="bot-modelos" maxlength="60" value="${_btEsc(p.gemini_model)}"${dis}><datalist id="bot-modelos">${modelos}</datalist></label>
                <label>Chat de respaldo (id)<input type="text" id="bot-c-respaldo" inputmode="numeric" placeholder="-100…" value="${ch.respaldo == null ? "" : _btEsc(ch.respaldo)}"${dis}></label>
                <label>Chat del grupo (id)<input type="text" id="bot-c-grupo" inputmode="numeric" placeholder="-100…" value="${ch.grupo == null ? "" : _btEsc(ch.grupo)}"${dis}></label>
                <label>Dirección de su panel<input type="url" id="bot-c-url" maxlength="300" placeholder="https://…" value="${_btEsc(d.url_panel)}"${dis}></label>
                <label class="bt-ancho">Mensaje para el equipo (opcional, hasta 280 caracteres)<input type="text" id="bot-c-mensaje" maxlength="280" value="${_btEsc(d.mensaje)}"${dis}></label>
            </div>
            <p class="cap-sub">La clave de Gemini y el token de Telegram no se guardan aquí: se configuran en el entorno del bot${claveGemini === "sin_clave" ? " (este bot reporta que <b>no tiene clave de Gemini</b>)" : ""}.</p>
            ${_btPuedeGestionar ? `<div style="display:flex; gap:0.6rem; align-items:center; flex-wrap:wrap;"><button type="submit" class="btn btn-primary" style="padding:0.4rem 0.9rem;">Guardar cambios</button><span id="bot-c-resultado"></span></div>` : `<p class="cap-sub">Necesitas el permiso «administrar los bots» para cambiar esto.</p>`}
        </form>
        <div class="bt-sec"><h4>Actividad de los últimos días</h4>${hist}</div>
        <div class="bt-sec"><h4>Capturas recientes de este bot</h4>${_btCapturasHtml(d.capturas || [])}</div>
        <div class="bt-sec"><h4>Cambios de configuración</h4>${bit}</div>`;
    }

    function _btAbrirModal(titulo, html) {
        document.getElementById("modal-bot-titulo").textContent = titulo;
        document.getElementById("modal-bot-body").innerHTML = html;
        document.getElementById("modal-bot").style.display = "flex";
    }

    window.cerrarModalBot = function() {
        document.getElementById("modal-bot").style.display = "none";
        _btModalBot = null;
    };

    async function _btAbrirDetalle(bot) {
        _btModalBot = bot;
        const nombre = (_BT_BOTS[bot] || {}).nombre || bot;
        _btAbrirModal(`Bot de ${nombre}`, `<p class="table-empty" style="padding:1rem !important;">Cargando...</p>`);
        try {
            const d = await _btFetch(`/api/bots/${encodeURIComponent(bot)}`);
            if (_btModalBot === bot) _btAbrirModal(`Bot de ${nombre}`, _btDetalleHtml(d));
        } catch (err) {
            _btAbrirModal(`Bot de ${nombre}`, _btMensaje("error", err.message));
        }
    }

    async function _btGuardarConfig(form) {
        const bot = form.dataset.btBot;
        const out = document.getElementById("bot-c-resultado");
        const valor = (id) => document.getElementById(id).value.trim();
        const cuerpo = {
            habilitado: document.getElementById("bot-c-habilitado").checked,
            parametros: { auto_envio_min: valor("bot-c-auto"), gemini_model: valor("bot-c-modelo") },
            chats: { respaldo: valor("bot-c-respaldo"), grupo: valor("bot-c-grupo") },
            url_panel: valor("bot-c-url"),
            mensaje: valor("bot-c-mensaje"),
        };
        out.innerHTML = "";
        try {
            await _btFetch(`/api/bots/${encodeURIComponent(bot)}`, _btJson("PATCH", cuerpo));
            await _btAbrirDetalle(bot);
            const nuevo = document.getElementById("bot-c-resultado");
            if (nuevo) nuevo.innerHTML = _btMensaje("ok", "Guardado. El bot lo aplica en menos de un minuto.");
            await loadBots();
        } catch (err) {
            out.innerHTML = _btMensaje("error", err.message);
        }
    }

    // ── Carga de la pantalla ──────────────────────────────────────────────────
    async function loadBots() {
        const miCarga = ++_btCarga;
        const u = await _bancosSesion();
        _btPuedeGestionar = !!u && (u.rol === "admin" || (u.permisos || []).includes("bots.gestionar"));
        _btPrepararFormUsuarioUnaVez();
        const aviso = document.getElementById("bots-aviso");
        try {
            const [lista, destinos, usuarios] = await Promise.all([
                _btFetch("/api/bots"), _btFetch("/api/bots/destinos"), _btFetch("/api/bots/usuarios"),
            ]);
            if (miCarga !== _btCarga) return;   // llegó una carga más nueva
            _btBots = lista.items || [];
            _btUsuarios = usuarios.items || [];
            aviso.innerHTML = "";
            _btRenderDestinos(destinos);
            document.getElementById("bots-grid").innerHTML = _btBots.map(_btTarjeta).join("");
            _btRenderUsuarios();
        } catch (err) {
            if (miCarga !== _btCarga) return;
            aviso.innerHTML = _btMensaje("error", err.message);
            document.getElementById("bots-grid").innerHTML = "";
        }
        clearTimeout(_btTimer);
        _btTimer = setTimeout(() => {   // se refresca solo mientras Configuración → Bots esté abierta
            const botsVis = !document.querySelector('#tab-config [data-subpage="bots"]')?.classList.contains("config-subpage-hidden");
            if (_normalizarModuloRuta(location.pathname) === "configuracion" && botsVis && !document.hidden && !_btModalBot) loadBots();
        }, _BT_REFRESCO_MS);
    }
    window.loadBots = loadBots;

    let _btFormListo = false;
    function _btPrepararFormUsuarioUnaVez() {
        if (!_btFormListo) { _btPrepararFormUsuario(); _btFormListo = true; }
        else document.getElementById("bots-form-usuario").style.display = _btPuedeGestionar ? "block" : "none";
    }

    // --- Eventos de la pantalla ---------------------------------------------------
    (function _btEventos() {
        const grid = document.getElementById("bots-grid");
        if (!grid) return;
        grid.addEventListener("click", (e) => {
            const cfg = e.target.closest("[data-bt-config]");
            if (cfg) { _btAbrirDetalle(cfg.dataset.btConfig); return; }
            const pa = e.target.closest("[data-bt-pausar]");
            if (pa) _btPausar(pa.dataset.btPausar, pa.dataset.btHabilitado === "1");
        });
        document.getElementById("bots-usuarios-body").addEventListener("click", (e) => {
            const ed = e.target.closest("[data-bt-editar]");
            const bo = e.target.closest("[data-bt-borrar]");
            if (bo) { const u = _btUsuarios[Number(bo.dataset.btBorrar)]; if (u) _btBorrarUsuario(u); return; }
            if (!ed) return;
            const u = _btUsuarios[Number(ed.dataset.btEditar)];
            if (!u) return;
            document.getElementById("bots-u-id").value = u.telegram_id;
            document.getElementById("bots-u-id").readOnly = true;
            document.getElementById("bots-u-nombre").value = u.nombre || "";
            document.getElementById("bots-u-email").value = u.email || "";
            document.querySelectorAll("[data-bt-u-bot]").forEach((c) => { c.checked = (u.bots || []).includes(c.value); });
            document.getElementById("bots-form-titulo").textContent = `Editar a ${u.nombre || u.telegram_id}`;
            document.getElementById("bots-u-cancelar").style.display = "inline-block";
            document.getElementById("bots-u-id").scrollIntoView({ block: "center" });
        });
        document.getElementById("bots-form-usuario").addEventListener("submit", _btGuardarUsuario);
        document.getElementById("bots-u-cancelar").addEventListener("click", _btLimpiarFormUsuario);
        const modal = document.getElementById("modal-bot");
        modal.addEventListener("click", (e) => { if (e.target === modal) window.cerrarModalBot(); });
        modal.addEventListener("submit", (e) => {
            if (e.target.id === "bot-form-config") { e.preventDefault(); _btGuardarConfig(e.target); }
        });
        document.addEventListener("keydown", (e) => { if (e.key === "Escape") window.cerrarModalBot(); });
    })();

    // =========================================================================
    // VENTAS → DOCUMENTOS DE COTIZACIONES Y PEDIDOS (Catálogo Real + Envío Odoo)
    // =========================================================================
    let _docsItems = [];
    let _docsLineasEdit = [];
    let _docsCatalogoCache = [];
    let _docsDescuentosCatalogo = [];
    let _docsDescuentosSel = new Set();
    let _docsPuedeAprobar = false;
    let _docsListaDefecto = { id: "21", nombre: "Pago VES Nov 2026", moneda: "VES" };
    let _docsCargadoUnaVez = false;
    let _tEvalDescuentos = null;

    function applyVentasSubpage(subpage) {
        const panels = document.querySelectorAll("#tab-ventas [data-ventas-panel]");
        const buttons = document.querySelectorAll(".ventas-subnav-btn");
        panels.forEach((p) => {
            p.classList.toggle("config-subpage-hidden", p.getAttribute("data-ventas-panel") !== subpage);
        });
        buttons.forEach((b) => {
            b.classList.toggle("active", b.getAttribute("data-ventas-subpage") === subpage);
        });
        if (subpage === "documentos" && !_docsCargadoUnaVez) {
            _docsCargadoUnaVez = true;
            if (typeof window.loadDocumentosComerciales === "function") {
                window.loadDocumentosComerciales();
            }
            _cargarCatalogoInicialDocs();
        }
    }
    window.applyVentasSubpage = applyVentasSubpage;

    function _actualizarEtiquetaListaDefecto(listaDef) {
        if (!listaDef || !listaDef.nombre) return;
        _docsListaDefecto = listaDef;
        const opt = document.getElementById("doc-opt-lista-ves");
        if (opt) opt.textContent = `${listaDef.nombre} (Comercial por defecto)`;
    }

    async function _cargarCatalogoInicialDocs() {
        try {
            const [resCat, resDesc] = await Promise.all([
                _docsCatalogoCache.length ? Promise.resolve(null) : fetch("/api/documentos/catalogo?limit=500"),
                _docsDescuentosCatalogo.length ? Promise.resolve(null) : fetch("/api/documentos/descuentos/catalogo"),
            ]);
            if (resCat && resCat.ok) {
                const data = await resCat.json();
                _docsCatalogoCache = data.items || [];
                _actualizarEtiquetaListaDefecto(data.lista_defecto);
            }
            if (resDesc && resDesc.ok) {
                const dataD = await resDesc.json();
                _docsDescuentosCatalogo = dataD.items || [];
                _actualizarEtiquetaListaDefecto(dataD.lista_defecto);
            }
            _renderSelectorDescuentos();
        } catch (_e) { /* silencioso */ }
        return _docsCatalogoCache;
    }

    function _docAlertaHtml(tipo, texto) {
        const ok = tipo === "ok";
        const warn = tipo === "warn";
        const borde = ok ? "#10b981" : (warn ? "#f59e0b" : "#e11d48");
        const bg = ok ? "#ecfdf5" : (warn ? "#fffbeb" : "#fff1f2");
        const color = ok ? "#065f46" : (warn ? "#92400e" : "#9f1239");
        const icono = ok ? "✅" : "⚠️";
        return `<div style="border-left:4px solid ${borde}; background:${bg}; color:${color}; padding:0.6rem 0.9rem; border-radius:6px; font-size:0.88rem; margin-bottom:0.35rem;">${icono} ${_capEsc(texto)}</div>`;
    }

    function _renderSelectorDescuentos(soloLectura = false) {
        const cont = document.getElementById("doc-descuentos-lista");
        if (!cont) return;
        if (!_docsDescuentosCatalogo.length) {
            cont.innerHTML = `<span style="color:#64748b; font-size:0.84rem;">No hay reglas activas en el catálogo de descuentos.</span>`;
            return;
        }
        cont.innerHTML = _docsDescuentosCatalogo.map((d) => {
            const checked = _docsDescuentosSel.has(d.codigo) ? "checked" : "";
            const benef = d.es_obsequio
                ? _capEsc(d.obsequio_detalle || "Obsequio")
                : `-${Number(d.porcentaje || 0).toFixed(2)}%`;
            return `<label style="display:flex; align-items:flex-start; gap:0.5rem; padding:0.5rem 0.65rem; background:#fff; border:1px solid ${checked ? "#2563eb" : "#e2e8f0"}; border-radius:6px; cursor:${soloLectura ? "default" : "pointer"}; font-size:0.82rem;">
                <input type="checkbox" data-doc-desc-cod="${_capEsc(d.codigo)}" ${checked} ${soloLectura ? "disabled" : ""} style="margin-top:3px;">
                <div>
                    <div><strong>[${_capEsc(d.codigo)}]</strong> ${_capEsc(d.titulo)} <strong style="color:#15803d;">(${benef})</strong></div>
                    <div style="color:#64748b; font-size:0.75rem;">${_capEsc(d.condicion || "")}</div>
                </div>
            </label>`;
        }).join("");
    }

    function _programarEvaluacionMotor() {
        clearTimeout(_tEvalDescuentos);
        _tEvalDescuentos = setTimeout(_evaluarMotorEnVivo, 220);
    }

    async function _evaluarMotorEnVivo() {
        const alertBox = document.getElementById("doc-descuentos-alertas");
        const elDesc = document.getElementById("doc-resumen-descuentos");
        const elNeto = document.getElementById("doc-resumen-neto");
        const elIva = document.getElementById("doc-resumen-iva");
        const elTot = document.getElementById("doc-resumen-total-iva");
        if (!elTot) return;

        let subBruto = 0;
        let descManual = 0;
        _docsLineasEdit.forEach((ln) => {
            const c = Math.max(0, Number(ln.cantidad) || 0);
            const p = Math.max(0, Number(ln.precio_unitario) || 0);
            const dp = Math.min(100, Math.max(0, Number(ln.descuento_pct) || 0));
            const b = c * p;
            subBruto += b;
            descManual += b * (dp / 100);
        });

        if (!_docsLineasEdit.length) {
            if (alertBox) alertBox.innerHTML = "";
            if (elDesc) elDesc.textContent = "-$0.00";
            if (elNeto) elNeto.textContent = "$0.00";
            if (elIva) elIva.textContent = "$0.00";
            elTot.textContent = "$0.00";
            return;
        }

        const pidRaw = (document.getElementById("doc-f-partner-id") || {}).value || "";
        const mon = (document.getElementById("doc-f-moneda") || {}).value || "VES";
        try {
            const res = await fetch("/api/documentos/descuentos/evaluar", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    moneda_lista: mon,
                    lista_precios_id: mon === "VES" ? _docsListaDefecto.id : "USD",
                    lista_precios_nombre: mon === "VES" ? _docsListaDefecto.nombre : "Pago USD Comercial",
                    partner_odoo_id: pidRaw ? Number(pidRaw) : null,
                    cliente_nombre: (document.getElementById("doc-f-cliente") || {}).value || "",
                    rif: (document.getElementById("doc-f-rif") || {}).value || "",
                    es_primera_compra: !pidRaw || (document.getElementById("doc-f-tipo-contacto") || {}).value === "prospecto",
                    descuentos_codigos: Array.from(_docsDescuentosSel),
                    lineas: _docsLineasEdit,
                }),
            });
            if (!res.ok) return;
            const ev = await res.json();
            const totDesc = Math.min(subBruto, Number(ev.total_descuentos || 0) + descManual);
            const neto = Math.max(0, subBruto - totDesc);
            const iva = neto * 0.16;
            const tot = neto + iva;
            if (elDesc) elDesc.textContent = `-$${totDesc.toFixed(2)}`;
            if (elNeto) elNeto.textContent = `$${neto.toFixed(2)}`;
            if (elIva) elIva.textContent = `$${iva.toFixed(2)}`;
            elTot.textContent = `$${tot.toFixed(2)}`;
            if (alertBox) {
                const alertas = ev.alertas || [];
                alertBox.innerHTML = alertas.length
                    ? alertas.map((a) => _docAlertaHtml("warn", a)).join("")
                    : (_docsDescuentosSel.size
                        ? _docAlertaHtml("ok", "Todos los descuentos seleccionados cumplen los parámetros actuales del motor CxC.")
                        : "");
            }
        } catch (_e) { /* silencioso */ }
    }

    async function loadDocumentosComerciales() {
        const tbody = document.getElementById("docs-table-body");
        if (!tbody) return;
        _docsCargadoUnaVez = true;
        const tipo = (document.getElementById("docs-filtro-tipo") || {}).value || "";
        const estado = (document.getElementById("docs-filtro-estado") || {}).value || "";
        const q = ((document.getElementById("docs-filtro-q") || {}).value || "").trim();
        const qs = new URLSearchParams({ limit: "100" });
        if (tipo) qs.set("tipo", tipo);
        if (estado) qs.set("estado", estado);
        if (q) qs.set("q", q);
        tbody.innerHTML = `<tr><td colspan="9" class="table-empty">Cargando documentos...</td></tr>`;
        try {
            const res = await fetch(`/api/documentos?${qs}`);
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
            _docsItems = data.items || [];
            _docsPuedeAprobar = Boolean(data.puede_aprobar);
            _actualizarEtiquetaListaDefecto(data.lista_defecto);
            const r = data.resumen || {};
            document.getElementById("docs-kpi-total").textContent = String(r.total || 0);
            document.getElementById("docs-kpi-cot").textContent = String(r.cotizaciones_borrador || 0);
            document.getElementById("docs-kpi-ped").textContent = String(
                r.pedidos_pendientes_aprobacion != null ? r.pedidos_pendientes_aprobacion : (r.pedidos_borrador || 0)
            );
            document.getElementById("docs-kpi-enviados").textContent = String(r.enviados_odoo || 0);
            _renderTablaDocumentos(_docsItems);
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="9" class="table-empty">${_capEsc(err.message)}</td></tr>`;
        }
    }
    window.loadDocumentosComerciales = loadDocumentosComerciales;

    function _renderTablaDocumentos(items) {
        const tbody = document.getElementById("docs-table-body");
        if (!items.length) {
            tbody.innerHTML = `<tr><td colspan="9" class="table-empty">No hay cotizaciones ni pedidos aún. Crea uno nuevo arriba o desde el bot de Ventas en Telegram.</td></tr>`;
            return;
        }
        tbody.innerHTML = items.map((d) => {
            const badgeTipo = d.tipo === "cotizacion"
                ? `<span class="state-badge abierta">📄 Cotización</span>`
                : `<span class="state-badge cierre">🛒 Pedido</span>`;
            const rolBadge = d.tipo_contacto === "cliente"
                ? `<span style="font-size:0.74rem; color:#047857; font-weight:600;">🟢 Cliente</span>`
                : `<span style="font-size:0.74rem; color:#b45309; font-weight:600;">🟡 Prospecto</span>`;
            let estadoHtml = `<span class="semaphore amarillo">📋 Cotización vigente</span>`;
            if (d.estado === "PENDIENTE_APROBACION") {
                estadoHtml = `<span class="semaphore amarillo">⏳ Pedido pendiente por aprobación</span>`;
            } else if (d.estado === "ENVIADO_ODOO") {
                estadoHtml = `<span class="semaphore verde">✅ En Odoo: ${_capEsc(d.odoo_order_name || ("#" + d.odoo_order_id))}</span>`;
            } else if (d.estado === "RECHAZADO") {
                estadoHtml = `<span class="semaphore rojo">❌ Rechazado</span>`;
            } else if (d.estado === "ERROR") {
                estadoHtml = `<span class="semaphore rojo" title="${_capEsc(d.error_odoo)}">⚠️ Error Odoo</span>`;
            }
            const avisoSinCat = d.lineas_sin_catalogo > 0
                ? `<div style="font-size:0.73rem; color:#dc2626;">⚠️ ${d.lineas_sin_catalogo} línea(s) sin SKU</div>`
                : "";
            const nAlertas = (d.alertas_motor || []).length;
            const avisoMotor = nAlertas > 0
                ? `<div style="font-size:0.73rem; color:#b45309; font-weight:600;">⚠️ ${nAlertas} alerta(s) motor</div>`
                : "";
            const descSub = Number(d.total_descuentos || 0) > 0
                ? `<div class="cap-sub" style="color:#15803d;">Desc. CxC: -$${_capMonto(d.total_descuentos)}</div>`
                : "";
            const origenTxt = d.origen === "bot" ? "📲 Bot" : "💻 Web";
            const editable = d.estado !== "ENVIADO_ODOO";
            const btnPreview = d.preview_path
                ? `<a href="${_capEsc(d.preview_path)}" target="_blank" rel="noopener" class="btn-secondary" style="padding:0.32rem 0.55rem; font-size:0.78rem; text-decoration:none;">🔗 Preview</a>`
                : "";
            const btnConv = (d.tipo === "cotizacion" && editable && d.estado !== "RECHAZADO")
                ? `<button type="button" class="btn-secondary" style="padding:0.32rem 0.55rem; font-size:0.78rem; background:#dcfce7; color:#166534;" onclick="convertirDocumentoAPedido(${d.id})">🛒 A Pedido</button>`
                : "";
            const btnAprobar = (d.estado === "PENDIENTE_APROBACION" && _docsPuedeAprobar)
                ? `<button type="button" class="btn-primary" style="padding:0.35rem 0.65rem; font-size:0.8rem; background:#16a34a;" onclick="aprobarPedidoEnCxC(${d.id})">✅ Autorizar</button>
                   <button type="button" class="btn-secondary" style="padding:0.32rem 0.55rem; font-size:0.78rem; color:#991b1b;" onclick="rechazarPedidoEnCxC(${d.id})">✖</button>`
                : "";
            return `<tr>
                <td><strong>${_capEsc(d.numero)}</strong><div class="cap-sub">${origenTxt}</div></td>
                <td>${badgeTipo}</td>
                <td><strong>${_capEsc(d.cliente_nombre)}</strong><div class="cap-sub">${rolBadge}${d.rif ? " · " + _capEsc(d.rif) : ""}</div></td>
                <td><strong>${_capEsc(d.lista_precios_nombre || d.moneda_lista)}</strong></td>
                <td>${d.n_lineas} ítem(s)<div class="cap-sub">${Number(d.total_litros || 0).toFixed(2)} L</div>${avisoSinCat}</td>
                <td><strong>$${_capMonto(d.total_con_iva || d.subtotal)}</strong><div class="cap-sub">Lista: $${_capMonto(d.subtotal)}</div>${descSub}${avisoMotor}</td>
                <td>${estadoHtml}${d.aprobado_por ? `<div class="cap-sub">Por: ${_capEsc(d.aprobado_por)}</div>` : ""}${d.error_odoo ? `<div class="cap-sub" style="color:#dc2626;">${_capEsc(d.error_odoo)}</div>` : ""}</td>
                <td style="font-size:0.8rem;">${_capFechaHora(d.actualizado_en)}</td>
                <td style="white-space:nowrap; display:flex; gap:4px; flex-wrap:wrap; align-items:center;">
                    ${btnPreview}
                    <button type="button" class="btn-secondary" style="padding:0.32rem 0.55rem; font-size:0.78rem;" onclick="editarDocumentoPorId(${d.id})">${editable ? "✏️ Editar" : "👁 Ver"}</button>
                    ${btnConv}
                    ${btnAprobar}
                </td>
            </tr>`;
        }).join("");
    }

    function _actualizarEtiquetasTipoDoc() {
        const tipo = (document.getElementById("doc-f-tipo") || {}).value || "cotizacion";
        const lblRif = document.getElementById("doc-lbl-req-rif");
        if (lblRif) {
            lblRif.textContent = tipo === "pedido" ? "* (obligatorio al enviar a Odoo si es nuevo)" : "(opcional en cotización)";
        }
        const btnEnv = document.getElementById("doc-btn-enviar-odoo");
        if (btnEnv) {
            if (tipo === "pedido" && !_docsPuedeAprobar) {
                btnEnv.style.display = "none";
            } else {
                btnEnv.style.display = "inline-block";
                btnEnv.textContent = tipo === "pedido"
                    ? "✅ Autorizar y Enviar a Odoo"
                    : "🚀 Guardar y Enviar Cotización a Odoo";
            }
        }
    }

    function _renderLineasEditor(soloLectura = false) {
        const tbody = document.getElementById("doc-lineas-body");
        if (!tbody) return;
        if (!_docsLineasEdit.length) {
            tbody.innerHTML = `<tr><td colspan="9" class="table-empty">Usa el buscador de arriba para agregar productos reales del catálogo de Odoo.</td></tr>`;
            document.getElementById("doc-resumen-litros").textContent = "0.00 L";
            document.getElementById("doc-resumen-subtotal").textContent = "$0.00";
            _programarEvaluacionMotor();
            return;
        }
        let totalLitros = 0;
        let totalSub = 0;
        tbody.innerHTML = _docsLineasEdit.map((ln, i) => {
            const cant = Math.max(0, Number(ln.cantidad) || 0);
            const precio = Math.max(0, Number(ln.precio_unitario) || 0);
            const desc = Math.min(100, Math.max(0, Number(ln.descuento_pct) || 0));
            const litrosU = Math.max(0, Number(ln.litros_unidad) || 0);
            const litrosLn = cant * litrosU;
            const subLn = cant * precio * (1 - desc / 100);
            totalLitros += litrosLn;
            totalSub += cant * precio;
            const alertaSku = !(ln.producto_id && /^\d+$/.test(String(ln.producto_id)))
                ? `<div style="color:#dc2626; font-size:0.74rem;">⚠️ Sin vincular al catálogo: busca arriba y reemplázala</div>`
                : "";
            return `<tr>
                <td><code>${_capEsc(ln.codigo || "—")}</code></td>
                <td><strong>${_capEsc(ln.descripcion)}</strong>${alertaSku}</td>
                <td>${_capEsc(ln.presentacion || "—")}</td>
                <td><input type="number" min="0.01" step="any" value="${cant}" ${soloLectura ? "disabled" : ""} data-doc-ln="${i}" data-doc-campo="cantidad" style="width:82px; padding:4px 6px;"></td>
                <td><input type="number" min="0" step="0.01" value="${precio.toFixed(2)}" ${soloLectura ? "disabled" : ""} data-doc-ln="${i}" data-doc-campo="precio_unitario" style="width:105px; padding:4px 6px;"></td>
                <td><input type="number" min="0" max="100" step="0.1" value="${desc}" ${soloLectura ? "disabled" : ""} data-doc-ln="${i}" data-doc-campo="descuento_pct" style="width:76px; padding:4px 6px;"></td>
                <td>${litrosLn.toFixed(2)} L</td>
                <td><strong>$${subLn.toFixed(2)}</strong></td>
                <td>${soloLectura ? "" : `<button type="button" class="btn-secondary" data-doc-quitar="${i}" style="padding:2px 8px;">✖</button>`}</td>
            </tr>`;
        }).join("");
        document.getElementById("doc-resumen-litros").textContent = `${totalLitros.toFixed(2)} L`;
        document.getElementById("doc-resumen-subtotal").textContent = `$${totalSub.toFixed(2)}`;
        _programarEvaluacionMotor();
    }

    function abrirNuevoDocumento(_tipoInicial = "cotizacion") {
        // Regla Fase 5B: todo documento comercial nace como Cotización y se convierte a Pedido.
        const tipo = "cotizacion";
        _cargarCatalogoInicialDocs();
        _docsDescuentosSel = new Set();
        document.getElementById("doc-f-id").value = "";
        document.getElementById("doc-f-partner-id").value = "";
        document.getElementById("doc-f-tipo-contacto").value = "prospecto";
        document.getElementById("doc-f-tipo").value = tipo;
        document.getElementById("doc-f-tipo").disabled = true;
        document.getElementById("doc-f-moneda").value = "VES";
        document.getElementById("doc-buscar-contacto").value = "";
        document.getElementById("doc-f-cliente").value = "";
        document.getElementById("doc-f-rif").value = "";
        document.getElementById("doc-f-telefono").value = "";
        document.getElementById("doc-f-email").value = "";
        document.getElementById("doc-f-direccion").value = "";
        document.getElementById("doc-f-lat").value = "";
        document.getElementById("doc-f-lon").value = "";
        document.getElementById("doc-f-plus").value = "";
        document.getElementById("doc-f-fecha-entrega").value = "";
        document.getElementById("doc-f-condiciones").value = "";
        document.getElementById("doc-f-observaciones").value = "";
        document.getElementById("doc-editor-mensaje").innerHTML = "";
        document.getElementById("doc-descuentos-alertas").innerHTML = "";
        document.getElementById("doc-btn-preview").style.display = "none";
        document.getElementById("doc-btn-convertir").style.display = "none";
        document.getElementById("doc-editor-acciones").style.display = "flex";
        document.getElementById("doc-editor-titulo").textContent = "➕ Nueva Cotización (convertible a Pedido)";
        document.getElementById("doc-editor-subtitulo").textContent = `Lista por defecto: ${_docsListaDefecto.nombre} · Catálogo real y motor de descuentos CxC`;
        _docsLineasEdit = [];
        _renderSelectorDescuentos(false);
        _actualizarEtiquetasTipoDoc();
        _renderLineasEditor(false);
        const card = document.getElementById("doc-editor-card");
        card.style.display = "block";
        card.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    window.abrirNuevoDocumento = abrirNuevoDocumento;

    async function editarDocumentoPorId(docId) {
        await _cargarCatalogoInicialDocs();
        try {
            const res = await fetch(`/api/documentos/${docId}`);
            const d = await res.json();
            if (!res.ok) throw new Error(d.detail || d.error || `Error ${res.status}`);
            if (typeof d.puede_aprobar === "boolean") _docsPuedeAprobar = d.puede_aprobar;
            const enviado = d.estado === "ENVIADO_ODOO";
            const vmSync = (d.validacion_motor && d.validacion_motor.sync_odoo) ? d.validacion_motor.sync_odoo : {};
            const bloqueado = enviado || Boolean(d.confirmado_odoo) || Boolean(d.solo_lectura) || Boolean(vmSync.confirmado_odoo);
            _docsDescuentosSel = new Set((d.descuentos || []).map((x) => x.codigo).filter(Boolean));
            document.getElementById("doc-f-id").value = String(d.id);
            document.getElementById("doc-f-partner-id").value = d.partner_odoo_id ? String(d.partner_odoo_id) : "";
            document.getElementById("doc-f-tipo-contacto").value = d.tipo_contacto || "prospecto";
            document.getElementById("doc-f-tipo").value = d.tipo || "cotizacion";
            document.getElementById("doc-f-tipo").disabled = bloqueado;
            document.getElementById("doc-f-moneda").value = d.moneda_lista || "VES";
            document.getElementById("doc-buscar-contacto").value = "";
            document.getElementById("doc-f-cliente").value = d.cliente_nombre || "";
            document.getElementById("doc-f-rif").value = d.rif || "";
            document.getElementById("doc-f-telefono").value = d.telefono || "";
            document.getElementById("doc-f-email").value = d.email || "";
            document.getElementById("doc-f-direccion").value = d.direccion || "";
            document.getElementById("doc-f-lat").value = d.latitud != null ? String(d.latitud) : "";
            document.getElementById("doc-f-lon").value = d.longitud != null ? String(d.longitud) : "";
            document.getElementById("doc-f-plus").value = d.plus_code || "";
            document.getElementById("doc-f-fecha-entrega").value = d.fecha_entrega || "";
            document.getElementById("doc-f-condiciones").value = d.condiciones_negociacion || "";
            document.getElementById("doc-f-observaciones").value = d.observaciones || "";

            let msgHtml = "";
            if (bloqueado) {
                const prefAuto = d.auto_enviado ? "⚡ Auto-enviado a Odoo" : "🔒 Confirmado en Odoo (solo lectura)";
                const notaSync = vmSync.ajustado_desde_odoo ? " · Ajustado automáticamente con cambios desde Odoo." : "";
                msgHtml = _docAlertaHtml("ok", `${prefAuto} como ${d.odoo_order_name || ("#" + (d.odoo_order_id || ""))} (no editable en CxC; descuentos en chatter).${notaSync}`);
            } else if (d.estado === "PENDIENTE_APROBACION") {
                const mots = Array.isArray(d.motivos_retencion) && d.motivos_retencion.length
                    ? ` Motivos de retención: ${d.motivos_retencion.join(" · ")}`
                    : " Cuando una persona autorizada lo apruebe, se enviará a Odoo con precios normales y la nota de descuentos en el chatter.";
                msgHtml = _docAlertaHtml("warn", `Pedido retenido en CxC pendiente por aprobación.${mots}`);
            } else if (d.error_odoo) {
                msgHtml = _docAlertaHtml("err", d.error_odoo);
            }
            document.getElementById("doc-editor-mensaje").innerHTML = msgHtml;

            const btnPrev = document.getElementById("doc-btn-preview");
            if (btnPrev) {
                if (d.preview_path) {
                    btnPrev.href = d.preview_path;
                    btnPrev.style.display = "inline-block";
                } else {
                    btnPrev.style.display = "none";
                }
            }
            const btnConv = document.getElementById("doc-btn-convertir");
            if (btnConv) {
                btnConv.style.display = (!bloqueado && d.tipo === "cotizacion" && d.estado !== "RECHAZADO") ? "inline-block" : "none";
            }

            document.getElementById("doc-editor-acciones").style.display = bloqueado ? "none" : "flex";
            document.getElementById("doc-editor-titulo").textContent = `${bloqueado ? "🔒" : "✏️"} ${d.numero} · ${d.cliente_nombre}`;
            document.getElementById("doc-editor-subtitulo").textContent = `Lista: ${d.lista_precios_nombre || d.moneda_lista} · Origen: ${d.origen === "bot" ? "Bot Telegram" : "Web CxC"} · Actualizado: ${_capFechaHora(d.actualizado_en)}`;
            _docsLineasEdit = (d.lineas || []).map((ln) => ({
                producto_id: ln.producto_id || "",
                codigo: ln.codigo || "",
                descripcion: ln.descripcion || "",
                presentacion: ln.presentacion || "",
                litros_unidad: Number(ln.litros_unidad) || 0,
                cantidad: Number(ln.cantidad) || 1,
                precio_unitario: Number(ln.precio_unitario) || 0,
                descuento_pct: Number(ln.descuento_pct) || 0,
            }));
            _renderSelectorDescuentos(bloqueado);
            _actualizarEtiquetasTipoDoc();
            _renderLineasEditor(bloqueado);
            const card = document.getElementById("doc-editor-card");
            card.style.display = "block";
            card.scrollIntoView({ behavior: "smooth", block: "start" });
        } catch (err) {
            document.getElementById("docs-aviso-general").innerHTML = _docAlertaHtml("err", err.message);
        }
    }
    window.editarDocumentoPorId = editarDocumentoPorId;

    function cerrarEditorDocumento() {
        const card = document.getElementById("doc-editor-card");
        if (card) card.style.display = "none";
    }
    window.cerrarEditorDocumento = cerrarEditorDocumento;

    async function guardarDocumentoComercial(enviarOdoo = false) {
        const msg = document.getElementById("doc-editor-mensaje");
        msg.innerHTML = "";
        const docId = document.getElementById("doc-f-id").value;
        const pidRaw = document.getElementById("doc-f-partner-id").value;
        const latRaw = document.getElementById("doc-f-lat").value;
        const lonRaw = document.getElementById("doc-f-lon").value;
        const mon = document.getElementById("doc-f-moneda").value;
        const tipoDoc = document.getElementById("doc-f-tipo").value;
        const payload = {
            tipo: tipoDoc,
            moneda_lista: mon,
            lista_precios_id: mon === "VES" ? _docsListaDefecto.id : "USD",
            lista_precios_nombre: mon === "VES" ? _docsListaDefecto.nombre : "Pago USD Comercial",
            partner_odoo_id: pidRaw ? Number(pidRaw) : null,
            tipo_contacto: document.getElementById("doc-f-tipo-contacto").value || "prospecto",
            es_primera_compra: !pidRaw || document.getElementById("doc-f-tipo-contacto").value === "prospecto",
            cliente_nombre: document.getElementById("doc-f-cliente").value.trim(),
            rif: document.getElementById("doc-f-rif").value.trim(),
            telefono: document.getElementById("doc-f-telefono").value.trim(),
            email: document.getElementById("doc-f-email").value.trim(),
            direccion: document.getElementById("doc-f-direccion").value.trim(),
            latitud: latRaw !== "" ? Number(latRaw) : null,
            longitud: lonRaw !== "" ? Number(lonRaw) : null,
            plus_code: document.getElementById("doc-f-plus").value.trim(),
            fecha_entrega: document.getElementById("doc-f-fecha-entrega").value || null,
            condiciones_negociacion: document.getElementById("doc-f-condiciones").value.trim(),
            observaciones: document.getElementById("doc-f-observaciones").value.trim(),
            descuentos_codigos: Array.from(_docsDescuentosSel),
            lineas: _docsLineasEdit,
        };
        try {
            const url = docId ? `/api/documentos/${docId}` : "/api/documentos";
            const metodo = docId ? "PUT" : "POST";
            const res = await fetch(url, {
                method: metodo,
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const guardado = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(guardado.error || guardado.detail || `Error ${res.status}`);
            document.getElementById("doc-f-id").value = String(guardado.id);
            const btnPrev = document.getElementById("doc-btn-preview");
            if (btnPrev && guardado.preview_path) {
                btnPrev.href = guardado.preview_path;
                btnPrev.style.display = "inline-block";
            }
            if (enviarOdoo) {
                let envData = guardado;
                if (guardado.estado !== "ENVIADO_ODOO") {
                    const endpointAccion = tipoDoc === "pedido"
                        ? `/api/documentos/${guardado.id}/aprobar`
                        : `/api/documentos/${guardado.id}/enviar-odoo`;
                    const resEnv = await fetch(endpointAccion, { method: "POST" });
                    envData = await resEnv.json().catch(() => ({}));
                    if (!resEnv.ok) throw new Error(envData.error || envData.detail || `Error al enviar a Odoo (${resEnv.status})`);
                }
                const prefEnv = envData.auto_enviado
                    ? `⚡ Pedido ${envData.numero} cumplió todos los parámetros y se auto-envió a Odoo`
                    : `Documento ${envData.numero} autorizado y enviado a Odoo`;
                document.getElementById("docs-aviso-general").innerHTML = _docAlertaHtml(
                    "ok",
                    `${prefEnv} → Orden ${envData.odoo_order_name || ("#" + envData.odoo_order_id)} (precios normales + descuentos en chatter).`
                );
                cerrarEditorDocumento();
            } else if (guardado.estado === "ENVIADO_ODOO" && guardado.auto_enviado) {
                msg.innerHTML = _docAlertaHtml(
                    "ok",
                    `⚡ Pedido ${guardado.numero} cumplió todos los parámetros y se envió automáticamente a Odoo → ${guardado.odoo_order_name || ("#" + guardado.odoo_order_id)}.`
                );
            } else {
                const motsG = Array.isArray(guardado.motivos_retencion) ? guardado.motivos_retencion : [];
                const estadoLbl = guardado.estado === "PENDIENTE_APROBACION"
                    ? (motsG.length ? `retenido para aprobación (${motsG.join(" · ")})` : "en estado «Pedido pendiente por aprobación»")
                    : "con enlace de Preview listo para compartir";
                msg.innerHTML = _docAlertaHtml(guardado.estado === "PENDIENTE_APROBACION" ? "warn" : "ok", `Documento ${guardado.numero} guardado en CxC ${estadoLbl}.`);
            }
            await (window.loadDocumentosComerciales || loadDocumentosComerciales)();
        } catch (err) {
            msg.innerHTML = _docAlertaHtml("err", err.message);
        }
    }
    window.guardarDocumentoComercial = guardarDocumentoComercial;

    async function convertirDocumentoAPedido(docId) {
        const aviso = document.getElementById("docs-aviso-general");
        aviso.innerHTML = "";
        try {
            const res = await fetch(`/api/documentos/${docId}/convertir-pedido`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({}),
            });
            const d = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(d.error || d.detail || `Error ${res.status}`);
            if (d.estado === "ENVIADO_ODOO") {
                aviso.innerHTML = _docAlertaHtml(
                    "ok",
                    `⚡ Pedido ${d.numero} cumplió todos los parámetros y se envió automáticamente a Odoo → ${d.odoo_order_name || ("#" + d.odoo_order_id)}.`
                );
            } else {
                const mots = Array.isArray(d.motivos_retencion) && d.motivos_retencion.length
                    ? ` Motivos de retención: ${d.motivos_retencion.join(" · ")}`
                    : "";
                aviso.innerHTML = _docAlertaHtml(
                    "warn",
                    `Convertido a Pedido ${d.numero}. Quedó retenido como «Pedido pendiente por aprobación».${mots}`
                );
            }
            await (window.loadDocumentosComerciales || loadDocumentosComerciales)();
        } catch (err) {
            aviso.innerHTML = _docAlertaHtml("err", err.message);
        }
    }
    window.convertirDocumentoAPedido = convertirDocumentoAPedido;

    async function convertirDocumentoEditorAPedido() {
        const docId = document.getElementById("doc-f-id").value;
        if (!docId) return;
        document.getElementById("doc-f-tipo").value = "pedido";
        await guardarDocumentoComercial(false);
    }
    window.convertirDocumentoEditorAPedido = convertirDocumentoEditorAPedido;

    async function aprobarPedidoEnCxC(docId) {
        const aviso = document.getElementById("docs-aviso-general");
        aviso.innerHTML = "";
        try {
            const res = await fetch(`/api/documentos/${docId}/aprobar`, { method: "POST" });
            const d = await res.json().catch(() => ({}));
            if (!res.ok) {
                aviso.innerHTML = _docAlertaHtml("err", d.error || d.detail || `Error ${res.status}`);
                await editarDocumentoPorId(docId);
                return;
            }
            aviso.innerHTML = _docAlertaHtml(
                "ok",
                `Pedido ${d.numero} autorizado y enviado a Odoo → ${d.odoo_order_name || ("#" + d.odoo_order_id)} (precios normales de lista; descuentos registrados en chatter de Odoo y en CxC).`
            );
            await (window.loadDocumentosComerciales || loadDocumentosComerciales)();
        } catch (err) {
            aviso.innerHTML = _docAlertaHtml("err", err.message);
        }
    }
    window.aprobarPedidoEnCxC = aprobarPedidoEnCxC;

    async function rechazarPedidoEnCxC(docId) {
        const motivo = window.prompt("Motivo del rechazo del pedido (opcional):", "") || "";
        const aviso = document.getElementById("docs-aviso-general");
        try {
            const res = await fetch(`/api/documentos/${docId}/rechazar`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ motivo }),
            });
            const d = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(d.error || d.detail || `Error ${res.status}`);
            aviso.innerHTML = _docAlertaHtml("warn", `Pedido ${d.numero} marcado como rechazado.`);
            await (window.loadDocumentosComerciales || loadDocumentosComerciales)();
        } catch (err) {
            aviso.innerHTML = _docAlertaHtml("err", err.message);
        }
    }
    window.rechazarPedidoEnCxC = rechazarPedidoEnCxC;

    (function _docsEventos() {
        document.querySelectorAll(".ventas-subnav-btn").forEach((btn) => {
            btn.addEventListener("click", () => applyVentasSubpage(btn.getAttribute("data-ventas-subpage")));
        });
        const fTipo = document.getElementById("docs-filtro-tipo");
        const fEstado = document.getElementById("docs-filtro-estado");
        const fQ = document.getElementById("docs-filtro-q");
        const recargar = () => (window.loadDocumentosComerciales || loadDocumentosComerciales)();
        if (fTipo && fEstado && fQ) {
            fTipo.addEventListener("change", recargar);
            fEstado.addEventListener("change", recargar);
            let tDocs = null;
            fQ.addEventListener("input", () => {
                clearTimeout(tDocs);
                tDocs = setTimeout(recargar, 250);
            });
        }
        const selTipoDoc = document.getElementById("doc-f-tipo");
        if (selTipoDoc) selTipoDoc.addEventListener("change", _actualizarEtiquetasTipoDoc);

        const selMonedaDoc = document.getElementById("doc-f-moneda");
        if (selMonedaDoc) {
            selMonedaDoc.addEventListener("change", () => {
                const mon = selMonedaDoc.value;
                _docsLineasEdit.forEach((ln) => {
                    const prod = _docsCatalogoCache.find((p) => String(p.producto_id) === String(ln.producto_id));
                    if (prod) {
                        ln.precio_unitario = Number(mon === "VES" ? prod.precio_ves : prod.precio_usd) || 0;
                    }
                });
                _renderLineasEditor(false);
            });
        }

        const boxDescuentos = document.getElementById("doc-descuentos-lista");
        if (boxDescuentos) {
            boxDescuentos.addEventListener("change", (e) => {
                const chk = e.target.closest("[data-doc-desc-cod]");
                if (!chk) return;
                const cod = chk.dataset.docDescCod;
                if (chk.checked) _docsDescuentosSel.add(cod);
                else _docsDescuentosSel.delete(cod);
                _renderSelectorDescuentos(false);
                _programarEvaluacionMotor();
            });
        }

        const inpRif = document.getElementById("doc-f-rif");
        const inpCli = document.getElementById("doc-f-cliente");
        if (inpRif) inpRif.addEventListener("input", _programarEvaluacionMotor);
        if (inpCli) inpCli.addEventListener("input", _programarEvaluacionMotor);

        const bodyLineas = document.getElementById("doc-lineas-body");
        if (bodyLineas) {
            bodyLineas.addEventListener("input", (e) => {
                const inp = e.target.closest("[data-doc-ln]");
                if (!inp) return;
                const idx = Number(inp.dataset.docLn);
                const campo = inp.dataset.docCampo;
                if (_docsLineasEdit[idx] && campo) {
                    _docsLineasEdit[idx][campo] = Number(inp.value) || 0;
                    _renderLineasEditor(false);
                }
            });
            bodyLineas.addEventListener("click", (e) => {
                const btn = e.target.closest("[data-doc-quitar]");
                if (!btn) return;
                _docsLineasEdit.splice(Number(btn.dataset.docQuitar), 1);
                _renderLineasEditor(false);
            });
        }

        // Autocompletado de Contactos en vivo desde Odoo
        const inpCont = document.getElementById("doc-buscar-contacto");
        const boxCont = document.getElementById("doc-resultados-contacto");
        let tCont = null;
        if (inpCont && boxCont) {
            inpCont.addEventListener("input", () => {
                clearTimeout(tCont);
                const q = inpCont.value.trim();
                if (q.length < 2) { boxCont.style.display = "none"; return; }
                tCont = setTimeout(async () => {
                    try {
                        const r = await fetch(`/api/odoo/contactos?q=${encodeURIComponent(q)}&limit=10`);
                        const data = await r.json();
                        const items = (data.items || []).filter((c) => c.clasificacion !== "proveedor");
                        if (!items.length) {
                            boxCont.innerHTML = `<div style="padding:0.6rem 0.85rem; color:#64748b; font-size:0.84rem;">Sin coincidencias en Odoo (puedes llenar los datos abajo para crearlo nuevo).</div>`;
                        } else {
                            boxCont.innerHTML = items.map((c, idx) => {
                                const etq = c.clasificacion === "prospecto" ? "🟡 Prospecto" : "🟢 Cliente";
                                return `<div data-doc-pick-cont="${idx}" style="padding:0.55rem 0.85rem; cursor:pointer; border-bottom:1px solid #f1f5f9; font-size:0.85rem;">
                                    <strong>${_capEsc(c.nombre)}</strong> <span style="color:#475569;">(${etq}${c.rif ? " · " + _capEsc(c.rif) : ""})</span>
                                    <div style="font-size:0.76rem; color:#64748b;">${_capEsc([c.telefono, c.email, c.direccion].filter(Boolean).join(" · ") || "Sin datos adicionales")}</div>
                                </div>`;
                            }).join("");
                            boxCont._items = items;
                        }
                        boxCont.style.display = "block";
                    } catch (_e) { boxCont.style.display = "none"; }
                }, 220);
            });
            boxCont.addEventListener("click", (e) => {
                const row = e.target.closest("[data-doc-pick-cont]");
                if (!row || !boxCont._items) return;
                const c = boxCont._items[Number(row.dataset.docPickCont)];
                if (!c) return;
                document.getElementById("doc-f-partner-id").value = String(c.id);
                document.getElementById("doc-f-tipo-contacto").value = c.clasificacion === "prospecto" ? "prospecto" : "cliente";
                document.getElementById("doc-f-cliente").value = c.nombre || "";
                document.getElementById("doc-f-rif").value = c.rif || "";
                document.getElementById("doc-f-telefono").value = c.telefono || "";
                document.getElementById("doc-f-email").value = c.email || "";
                document.getElementById("doc-f-direccion").value = c.direccion || "";
                document.getElementById("doc-f-plus").value = c.plus_code || "";
                document.getElementById("doc-f-lat").value = c.latitud != null ? String(c.latitud) : "";
                document.getElementById("doc-f-lon").value = c.longitud != null ? String(c.longitud) : "";
                inpCont.value = `${c.nombre} (${c.clasificacion_etiqueta})`;
                boxCont.style.display = "none";
                _programarEvaluacionMotor();
            });
        }

        // Buscador en vivo del Catálogo Real de Productos de Odoo
        const inpProd = document.getElementById("doc-buscar-producto");
        const boxProd = document.getElementById("doc-resultados-producto");
        let tProd = null;
        if (inpProd && boxProd) {
            inpProd.addEventListener("input", () => {
                clearTimeout(tProd);
                const q = inpProd.value.trim();
                if (!q) { boxProd.style.display = "none"; return; }
                tProd = setTimeout(async () => {
                    try {
                        const r = await fetch(`/api/documentos/catalogo?q=${encodeURIComponent(q)}&limit=15`);
                        const data = await r.json();
                        const items = data.items || [];
                        if (!items.length) {
                            boxProd.innerHTML = `<div style="padding:0.6rem 0.85rem; color:#64748b; font-size:0.84rem;">No se encontraron productos con «${_capEsc(q)}» en el catálogo de Odoo.</div>`;
                        } else {
                            const mon = document.getElementById("doc-f-moneda").value;
                            boxProd.innerHTML = items.map((p, idx) => {
                                const pr = Number(mon === "VES" ? p.precio_ves : p.precio_usd) || 0;
                                let stockBadge = "";
                                if (p.stock_disponible != null) {
                                    const qty = Number(p.stock_disponible) || 0;
                                    const qtyTxt = Number.isInteger(qty) ? String(qty) : qty.toFixed(1);
                                    if (qty <= 0) {
                                        stockBadge = `<span style="background:#fee2e2; color:#991b1b; padding:1px 6px; border-radius:99px; font-size:0.72rem; font-weight:700;">🔴 Agotado</span>`;
                                    } else if (qty <= 5) {
                                        stockBadge = `<span style="background:#fef3c7; color:#92400e; padding:1px 6px; border-radius:99px; font-size:0.72rem; font-weight:700;">🟡 ${qtyTxt} disp.</span>`;
                                    } else {
                                        stockBadge = `<span style="background:#dcfce7; color:#166534; padding:1px 6px; border-radius:99px; font-size:0.72rem; font-weight:700;">🟢 ${qtyTxt} disp.</span>`;
                                    }
                                }
                                return `<div data-doc-pick-prod="${idx}" style="padding:0.55rem 0.85rem; cursor:pointer; border-bottom:1px solid #f1f5f9; display:flex; justify-content:space-between; align-items:center; gap:0.5rem; font-size:0.85rem;">
                                    <div>
                                        <code>${_capEsc(p.codigo || "S/C")}</code> <strong>${_capEsc(p.nombre)}</strong>
                                        <div style="font-size:0.75rem; color:#64748b;">${_capEsc(p.marca || "")} · ${Number(p.litros || 0).toFixed(2)} L</div>
                                    </div>
                                    <div style="display:flex; align-items:center; gap:0.5rem; white-space:nowrap;">
                                        ${stockBadge}
                                        <strong style="color:#1e40af;">$${pr.toFixed(2)}</strong>
                                    </div>
                                </div>`;
                            }).join("");
                            boxProd._items = items;
                        }
                        boxProd.style.display = "block";
                    } catch (_e) { boxProd.style.display = "none"; }
                }, 180);
            });
            boxProd.addEventListener("click", (e) => {
                const row = e.target.closest("[data-doc-pick-prod]");
                if (!row || !boxProd._items) return;
                const p = boxProd._items[Number(row.dataset.docPickProd)];
                if (!p) return;
                const mon = document.getElementById("doc-f-moneda").value;
                const precio = Number(mon === "VES" ? p.precio_ves : p.precio_usd) || 0;
                const existente = _docsLineasEdit.find((x) => String(x.producto_id) === String(p.producto_id));
                if (existente) {
                    existente.cantidad = (Number(existente.cantidad) || 0) + 1;
                } else {
                    _docsLineasEdit.push({
                        producto_id: String(p.producto_id),
                        codigo: p.codigo || "",
                        descripcion: p.nombre,
                        presentacion: p.presentacion || "",
                        litros_unidad: Number(p.litros) || 0,
                        cantidad: 1,
                        precio_unitario: precio,
                        descuento_pct: 0,
                    });
                }
                inpProd.value = "";
                boxProd.style.display = "none";
                _renderLineasEditor(false);
            });
        }

        document.addEventListener("click", (e) => {
            if (boxCont && !e.target.closest("#doc-buscar-contacto") && !e.target.closest("#doc-resultados-contacto")) {
                boxCont.style.display = "none";
            }
            if (boxProd && !e.target.closest("#doc-buscar-producto") && !e.target.closest("#doc-resultados-producto")) {
                boxProd.style.display = "none";
            }
        });
    })();

    // =========================================================================
    // DOCUMENTOS OPERATIVOS: Pagos Recibidos, Facturas de Proveedor,
    // Pagos a Proveedor y Gastos sin Factura (Bot <-> CxC <-> Odoo).
    // =========================================================================
    let _opCatalogos = { diarios_bancarios: [], diarios_compras: [], cuentas_contables: [] };
    let _opCatalogosCargados = false;
    let _opPuedeAprobarPagos = false;
    let _opPuedeAprobarCompras = false;
    let _opValidTimer = null;

    function applyCobranzaSubpage(subpage) {
        document.querySelectorAll("#tab-cobranza [data-cobranza-panel]").forEach((panel) => {
            panel.classList.toggle(
                "config-subpage-hidden",
                panel.getAttribute("data-cobranza-panel") !== subpage
            );
        });
        document.querySelectorAll(".cobranza-subnav-btn").forEach((btn) => {
            btn.classList.toggle("active", btn.getAttribute("data-cobranza-subpage") === subpage);
        });
        if (subpage === "aprobacion") loadOperativosCobranza();
    }

    function _opEstadoBadge(d) {
        if (d.estado === "ENVIADO_ODOO") {
            return `<span class="semaphore verde">✅ En Odoo (${_capEsc(d.odoo_ref || "")})</span>`;
        }
        if (d.estado === "CONFIRMADO_ODOO") {
            return `<span class="semaphore verde">🟣 Confirmado Odoo</span>`;
        }
        if (d.estado === "CONFIRMADO_CXC") {
            return `<span class="semaphore verde badge-confirmado-cxc">✔️ Confirmado CxC</span>`;
        }
        if (d.estado === "PENDIENTE_APROBACION") {
            return `<span class="semaphore amarillo">⏳ Pendiente aprobación</span>`;
        }
        if (d.estado === "INCOMPLETO" || d.estado === "BORRADOR") {
            return `<span class="semaphore amarillo">⚠️ Incompleto</span>`;
        }
        if (d.estado === "RECHAZADO") {
            return `<span class="semaphore rojo">❌ Rechazado</span>`;
        }
        return `<span class="semaphore rojo">⚠️ Error Odoo</span>`;
    }

    function _opTipoBadge(tipo) {
        const mapa = {
            pago: ["💵 Pago recibido", "abierta"],
            factura_proveedor: ["🧾 Factura prov.", "cierre"],
            pago_proveedor: ["💸 Pago a prov.", "cierre"],
            gasto: ["🛒 Gasto menor", "abierta"],
        };
        const [txt, cls] = mapa[tipo] || [tipo, ""];
        return `<span class="state-badge ${cls}">${_capEsc(txt)}</span>`;
    }

    function _opAlertasHtml(d) {
        const partes = [];
        if ((d.faltantes || []).length) {
            partes.push(`<div class="cap-sub" style="color:#b45309;">Falta: ${d.faltantes.map(_capEsc).join(", ")}</div>`);
        }
        if ((d.alertas || []).length) {
            partes.push(`<div class="cap-sub" style="color:#92400e;">⚠️ ${d.alertas.length} alerta(s): ${_capEsc(d.alertas[0])}</div>`);
        }
        if (d.error_odoo) {
            partes.push(`<div class="cap-sub" style="color:#dc2626;">${_capEsc(d.error_odoo)}</div>`);
        }
        if (d.aprobado_por) {
            partes.push(`<div class="cap-sub">Por: ${_capEsc(d.aprobado_por)}</div>`);
        }
        return partes.join("");
    }

    async function _opCargarCatalogos(forzar = false) {
        if (_opCatalogosCargados && !forzar) return _opCatalogos;
        try {
            const res = await fetch("/api/operativos/catalogos");
            if (res.ok) {
                _opCatalogos = await res.json();
                _opCatalogosCargados = true;
            }
        } catch (_e) { /* silencioso */ }
        return _opCatalogos;
    }

    function _opPoblarSelectCatalogos(tipo, diarioSel, cuentaSel, terceroSel) {
        const selD = document.getElementById("op-f-diario");
        const selC = document.getElementById("op-f-cuenta");
        const selZt = document.getElementById("op-f-cuenta-tercero");
        if (selD) {
            const diarios = _opCatalogos.diarios_bancarios || [];
            selD.innerHTML = `<option value="">(Detección automática)</option>` + diarios.map((d) =>
                `<option value="${d.id}">${_capEsc(d.nombre)} (${_capEsc(d.moneda || d.codigo)})</option>`
            ).join("");
            if (diarioSel != null && diarioSel !== "") selD.value = String(diarioSel);
        }
        if (selC) {
            const cuentas = _opCatalogos.cuentas_contables || [];
            selC.innerHTML = `<option value="">(Por defecto del producto / proveedor en Odoo)</option>` + cuentas.map((c) =>
                `<option value="${c.id}">${_capEsc(c.etiqueta || ((c.code || "") + " " + c.name))}</option>`
            ).join("");
            if (cuentaSel != null && cuentaSel !== "") selC.value = String(cuentaSel);
        }
        if (selZt) {
            const ctasZt = _opCatalogos.cuentas_terceros || [];
            selZt.innerHTML = `<option value="">Selecciona la cuenta del tercero…</option>` + ctasZt.map((c) =>
                `<option value="${c.id}" data-titular="${_capEsc(c.titular || c.correo_telefono || c.alias)}">${_capEsc(c.alias)} — ${_capEsc(c.proveedor_nombre || ("Prov #" + c.proveedor_odoo_id))}${c.correo_telefono ? " (" + _capEsc(c.correo_telefono) + ")" : ""}</option>`
            ).join("");
            if (terceroSel != null && terceroSel !== "") selZt.value = String(terceroSel);
        }
        _opActualizarCamposVisibles(tipo);
    }

    function _opActualizarCamposVisibles(tipo) {
        const esPago = tipo === "pago" || tipo === "pago_proveedor";
        document.querySelectorAll(".op-grp-pago").forEach((el) => {
            if (!el.style.gridTemplateColumns) el.style.display = esPago ? "block" : "none";
            else el.style.display = esPago ? "grid" : "none";
        });
        document.querySelectorAll(".op-grp-compra").forEach((el) => {
            el.style.display = esPago ? "none" : "flex";
        });
        const wPed = document.getElementById("op-wrap-pedido-ref");
        const wFac = document.getElementById("op-wrap-factura-ref");
        if (wPed) wPed.style.display = tipo === "pago" ? "block" : "none";
        if (wFac) wFac.style.display = tipo === "pago_proveedor" ? "block" : "none";
        const wSf = document.getElementById("op-wrap-sin-factura");
        if (wSf) wSf.style.display = tipo === "gasto" ? "flex" : "none";
        const lblC = document.getElementById("op-lbl-contraparte");
        if (lblC) lblC.textContent = tipo === "pago" ? "Cliente" : (tipo === "gasto" ? "Comercio / Proveedor" : "Proveedor");
    }

    async function loadOperativosCobranza() {
        if (typeof window.loadPagosRecibidosUnificados === "function" && window.loadPagosRecibidosUnificados !== loadOperativosCobranza) {
            return window.loadPagosRecibidosUnificados();
        }
        const tbody = document.getElementById("op-pagos-table-body");
        const estado = (document.getElementById("op-cob-estado") || {}).value || "";
        const q = ((document.getElementById("op-cob-buscar") || {}).value || "").trim();
        const qs = new URLSearchParams({ grupo: "cobranza", limit: "100" });
        if (estado) qs.set("estado", estado);
        if (q) qs.set("q", q);
        try {
            const res = await fetch(`/api/operativos?${qs}`);
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
            _opPuedeAprobarPagos = Boolean(data.puede_aprobar_pagos);
            const r = data.resumen || {};
            const badge = document.getElementById("badge-op-pagos-pend");
            if (badge) badge.textContent = String(r.pendientes_aprobacion || 0);
            for (const [idEl, val] of [
                ["op-cob-n-PENDIENTE_APROBACION", r.pendientes_aprobacion || 0],
                ["op-cob-n-INCOMPLETO", r.incompletos || 0],
                ["op-cob-n-ENVIADO_ODOO", r.enviados_odoo || 0],
                ["op-cob-n-RECHAZADO", r.rechazados || 0],
            ]) {
                const el = document.getElementById(idEl);
                if (el) el.textContent = String(val);
            }
            const resEl = document.getElementById("op-cob-resumen");
            if (resEl) resEl.textContent = `${data.total || 0} pago(s) encontrado(s)`;
            if (!tbody) return;
            const items = data.items || [];
            if (!items.length) {
                tbody.innerHTML = `<tr><td colspan="9" class="table-empty">No hay pagos recibidos en espera o con estos filtros.</td></tr>`;
                return;
            }
            tbody.innerHTML = items.map((d) => {
                const eq = d.validacion && d.validacion.equivalente_usd && d.validacion.equivalente_usd.usd_bcv != null
                    ? `<div class="cap-sub">≈ $${Number(d.validacion.equivalente_usd.usd_bcv).toFixed(2)} BCV</div>` : "";
                const refTxt = d.es_efectivo ? `<span style="color:#0369a1; font-weight:600;">Efectivo (memo vacío)</span>` : _capEsc(d.referencia || "—");
                const btnPrev = d.preview_path ? `<a href="${_capEsc(d.preview_path)}" target="_blank" rel="noopener" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem; text-decoration:none;">🔗 Preview</a>` : "";
                const btnApr = (d.puede_aprobar && _opPuedeAprobarPagos)
                    ? `<button type="button" class="btn-primary" style="padding:0.32rem 0.6rem; font-size:0.78rem; background:#16a34a;" onclick="aprobarOperativo(${d.id})">✅ Autorizar</button>` : "";
                const btnRec = (d.puede_rechazar && _opPuedeAprobarPagos)
                    ? `<button type="button" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem; color:#991b1b;" onclick="rechazarOperativo(${d.id})">✖</button>` : "";
                return `<tr>
                    <td><strong>${_capEsc(d.numero)}</strong><div class="cap-sub">${d.origen === "bot" ? "📲 Bot" : "💻 Web"}</div></td>
                    <td>${_capEsc(d.fecha_documento || "—")}</td>
                    <td><strong>${_capEsc(d.contraparte_nombre || "Sin cliente")}</strong><div class="cap-sub">${_capEsc(d.rif || (d.partner_odoo_id ? ("Odoo #" + d.partner_odoo_id) : "Sin vincular"))}</div></td>
                    <td><strong>${d.monto != null ? _capMonto(d.monto) : "—"} ${_capEsc(d.moneda)}</strong>${eq}</td>
                    <td>${_capEsc(d.diario_nombre || d.banco || "Por definir")}</td>
                    <td>${refTxt}<div class="cap-sub">${_capEsc(d.metodo_pago || "")}</div></td>
                    <td>${_capEsc(d.pedido_ref || "—")}</td>
                    <td>${_opEstadoBadge(d)}${_opAlertasHtml(d)}</td>
                    <td style="white-space:nowrap; display:flex; gap:4px; flex-wrap:wrap;">
                        ${btnPrev}
                        <button type="button" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem;" onclick="editarOperativoPorId(${d.id})">${d.puede_editar ? "✏️ Editar" : "👁 Ver"}</button>
                        ${btnApr}${btnRec}
                    </td>
                </tr>`;
            }).join("");
        } catch (err) {
            if (tbody) tbody.innerHTML = `<tr><td colspan="9" class="table-empty">❌ ${_capEsc(err.message)}</td></tr>`;
        }
    }
    window.loadOperativosCobranza = loadOperativosCobranza;

    async function loadOperativosCompras() {
        if (typeof window.loadComprasUnificadas === "function" && window.loadComprasUnificadas !== loadOperativosCompras) {
            return window.loadComprasUnificadas();
        }
        const tbody = document.getElementById("op-compras-table-body");
        const tipo = (document.getElementById("op-cmp-tipo") || {}).value || "";
        const estado = (document.getElementById("op-cmp-estado") || {}).value || "";
        const q = ((document.getElementById("op-cmp-buscar") || {}).value || "").trim();
        const qs = new URLSearchParams({ grupo: "compras_solo", limit: "100" });
        if (tipo) qs.set("tipo", tipo);
        if (estado) qs.set("estado", estado);
        if (q) qs.set("q", q);
        try {
            const res = await fetch(`/api/operativos?${qs}`);
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
            _opPuedeAprobarCompras = Boolean(data.puede_aprobar_compras);
            const r = data.resumen || {};
            const badge = document.getElementById("badge-op-compras-pend");
            if (badge) badge.textContent = String(r.pendientes_aprobacion || 0);
            for (const [idEl, val] of [
                ["op-cmp-n-PENDIENTE_APROBACION", r.pendientes_aprobacion || 0],
                ["op-cmp-n-INCOMPLETO", r.incompletos || 0],
                ["op-cmp-n-ENVIADO_ODOO", r.enviados_odoo || 0],
                ["op-cmp-n-RECHAZADO", r.rechazados || 0],
            ]) {
                const el = document.getElementById(idEl);
                if (el) el.textContent = String(val);
            }
            const resEl = document.getElementById("op-cmp-resumen");
            if (resEl) resEl.textContent = `${data.total || 0} documento(s) de compras/gastos`;
            if (!tbody) return;
            const items = data.items || [];
            if (!items.length) {
                tbody.innerHTML = `<tr><td colspan="9" class="table-empty">No hay facturas de proveedor, pagos a proveedores ni gastos con estos filtros.</td></tr>`;
                return;
            }
            tbody.innerHTML = items.map((d) => {
                const docRef = d.tipo === "pago_proveedor"
                    ? (d.es_efectivo ? "Efectivo (memo vacío)" : (d.referencia || "—"))
                    : (d.numero_documento || (d.sin_factura ? "Sin factura (S/N)" : "—"));
                const subRef = d.tipo === "pago_proveedor"
                    ? (d.factura_ref ? `Fac: ${d.factura_ref}` : d.metodo_pago)
                    : (d.numero_control ? `Ctrl: ${d.numero_control}` : d.categoria);
                const diarioCta = d.tipo === "pago_proveedor"
                    ? _capEsc(d.diario_nombre || d.banco || "Por definir")
                    : `${_capEsc(d.diario_nombre || "Diario Compras VES")}${d.cuenta_contable_codigo ? `<div class="cap-sub">${_capEsc(d.cuenta_contable_codigo)} · ${_capEsc(d.cuenta_contable_nombre)}</div>` : ""}`;
                const btnPrev = d.preview_path ? `<a href="${_capEsc(d.preview_path)}" target="_blank" rel="noopener" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem; text-decoration:none;">🔗 Preview</a>` : "";
                const btnApr = (d.puede_aprobar && _opPuedeAprobarCompras)
                    ? `<button type="button" class="btn-primary" style="padding:0.32rem 0.6rem; font-size:0.78rem; background:#16a34a;" onclick="aprobarOperativo(${d.id})">✅ Autorizar</button>` : "";
                const btnRec = (d.puede_rechazar && _opPuedeAprobarCompras)
                    ? `<button type="button" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem; color:#991b1b;" onclick="rechazarOperativo(${d.id})">✖</button>` : "";
                return `<tr>
                    <td><strong>${_capEsc(d.numero)}</strong><div class="cap-sub">${d.origen === "bot" ? "📲 Bot" : "💻 Web"}</div></td>
                    <td>${_opTipoBadge(d.tipo)}</td>
                    <td>${_capEsc(d.fecha_documento || "—")}</td>
                    <td><strong>${_capEsc(d.contraparte_nombre || "Sin proveedor")}</strong><div class="cap-sub">${_capEsc(d.rif || (d.partner_odoo_id ? ("Odoo #" + d.partner_odoo_id) : "Se creará al aprobar"))}</div></td>
                    <td><strong>${_capEsc(docRef)}</strong>${subRef ? `<div class="cap-sub">${_capEsc(subRef)}</div>` : ""}</td>
                    <td><strong>${d.monto != null ? _capMonto(d.monto) : "—"} ${_capEsc(d.moneda)}</strong></td>
                    <td>${diarioCta}</td>
                    <td>${_opEstadoBadge(d)}${_opAlertasHtml(d)}</td>
                    <td style="white-space:nowrap; display:flex; gap:4px; flex-wrap:wrap;">
                        ${btnPrev}
                        <button type="button" class="btn-secondary" style="padding:0.3rem 0.5rem; font-size:0.78rem;" onclick="editarOperativoPorId(${d.id})">${d.puede_editar ? "✏️ Editar" : "👁 Ver"}</button>
                        ${btnApr}${btnRec}
                    </td>
                </tr>`;
            }).join("");
        } catch (err) {
            if (tbody) tbody.innerHTML = `<tr><td colspan="9" class="table-empty">❌ ${_capEsc(err.message)}</td></tr>`;
        }
    }
    window.loadOperativosCompras = loadOperativosCompras;

    function _opRecolectarFormulario() {
        const tipo = document.getElementById("op-f-tipo").value;
        const pid = document.getElementById("op-f-partner-id").value;
        const jid = document.getElementById("op-f-diario").value;
        const cid = document.getElementById("op-f-cuenta").value;
        const chkZt = document.getElementById("op-f-es-zelle-tercero");
        const selZt = document.getElementById("op-f-cuenta-tercero");
        const inpBenef = document.getElementById("op-f-beneficiario-tercero");
        const esZt = Boolean(tipo === "pago" && chkZt && chkZt.checked);
        return {
            tipo,
            partner_odoo_id: pid ? Number(pid) : null,
            contraparte_nombre: document.getElementById("op-f-contraparte").value.trim(),
            rif: document.getElementById("op-f-rif").value.trim(),
            monto: document.getElementById("op-f-monto").value || null,
            moneda: document.getElementById("op-f-moneda").value,
            fecha_documento: document.getElementById("op-f-fecha").value || null,
            metodo_pago: document.getElementById("op-f-metodo").value.trim(),
            referencia: document.getElementById("op-f-referencia").value.trim(),
            banco: esZt ? "" : document.getElementById("op-f-banco").value.trim(),
            diario_id: esZt ? null : (jid ? Number(jid) : null),
            es_zelle_tercero: esZt,
            cuenta_tercero_id: esZt && selZt && selZt.value ? Number(selZt.value) : null,
            beneficiario_tercero: esZt && inpBenef ? inpBenef.value.trim() : "",
            pedido_ref: document.getElementById("op-f-pedido-ref").value.trim(),
            factura_ref: document.getElementById("op-f-factura-ref").value.trim(),
            numero_documento: document.getElementById("op-f-num-doc").value.trim(),
            numero_control: document.getElementById("op-f-num-control").value.trim(),
            base_imponible: document.getElementById("op-f-base").value || null,
            iva: document.getElementById("op-f-iva").value || null,
            fecha_vencimiento: document.getElementById("op-f-vencimiento").value || null,
            categoria: document.getElementById("op-f-categoria").value,
            cuenta_contable_id: cid ? Number(cid) : null,
            concepto: document.getElementById("op-f-concepto").value.trim(),
            sin_factura: document.getElementById("op-f-sin-factura").checked,
            notas: document.getElementById("op-f-notas").value.trim(),
        };
    }

    function _opRenderAlertasValidacion(val) {
        const box = document.getElementById("op-modal-alertas");
        if (!box || !val) return;
        const alertas = val.alertas || [];
        const falt = val.faltantes || [];
        const partes = [];
        if (falt.length) {
            partes.push(`<div style="border-left:4px solid #e11d48; background:#fff1f2; padding:0.5rem 0.85rem; border-radius:6px; color:#9f1239; font-size:0.84rem; margin-bottom:0.4rem;"><strong>Faltan datos:</strong> ${falt.map(_capEsc).join(", ")}</div>`);
        }
        if (alertas.length) {
            partes.push(`<div style="border-left:4px solid #f59e0b; background:#fffbeb; padding:0.5rem 0.85rem; border-radius:6px; color:#92400e; font-size:0.84rem; margin-bottom:0.4rem;"><strong>⚠️ Validaciones CxC:</strong><ul style="margin:0.25rem 0 0 1rem; padding:0;">${alertas.map((a) => `<li>${_capEsc(a)}</li>`).join("")}</ul></div>`);
        }
        if (val.regla_odoo) {
            partes.push(`<div style="border-left:4px solid #2563eb; background:#eff6ff; padding:0.45rem 0.85rem; border-radius:6px; color:#1e3a8a; font-size:0.82rem;"><strong>Regla Odoo:</strong> ${_capEsc(val.regla_odoo)}</div>`);
        }
        box.innerHTML = partes.join("");
    }

    function _opProgramarValidacion() {
        clearTimeout(_opValidTimer);
        _opValidTimer = setTimeout(async () => {
            const payload = _opRecolectarFormulario();
            const idVal = document.getElementById("op-f-id").value;
            try {
                const res = await fetch("/api/operativos/validar", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        tipo: payload.tipo,
                        excluir_id: idVal ? Number(idVal) : null,
                        datos: payload,
                    }),
                });
                const val = await res.json().catch(() => null);
                if (res.ok && val) _opRenderAlertasValidacion(val);
            } catch (_e) { /* silencioso */ }
        }, 260);
    }

    window.cerrarModalOperativo = function() {
        const m = document.getElementById("modal-operativo");
        if (m) m.style.display = "none";
    };

    function _opLlenarFormulario(d) {
        const tipo = d.tipo || "pago";
        document.getElementById("op-f-id").value = d.id ? String(d.id) : "";
        document.getElementById("op-f-tipo").value = tipo;
        document.getElementById("op-f-tipo").disabled = Boolean(d.id);
        document.getElementById("op-f-partner-id").value = d.partner_odoo_id != null ? String(d.partner_odoo_id) : "";
        document.getElementById("op-f-buscar-contacto").value = "";
        document.getElementById("op-f-contraparte").value = d.contraparte_nombre || "";
        document.getElementById("op-f-rif").value = d.rif || "";
        document.getElementById("op-f-monto").value = d.monto != null ? String(d.monto) : "";
        document.getElementById("op-f-moneda").value = d.moneda || "VES";
        document.getElementById("op-f-fecha").value = d.fecha_documento || new Date().toISOString().slice(0, 10);
        document.getElementById("op-f-metodo").value = d.metodo_pago || "";
        document.getElementById("op-f-referencia").value = d.referencia || "";
        document.getElementById("op-f-banco").value = d.banco || "";
        document.getElementById("op-f-pedido-ref").value = d.pedido_ref || "";
        document.getElementById("op-f-factura-ref").value = d.factura_ref || "";
        document.getElementById("op-f-num-doc").value = d.numero_documento || "";
        document.getElementById("op-f-num-control").value = d.numero_control || "";
        document.getElementById("op-f-base").value = d.base_imponible != null ? String(d.base_imponible) : "";
        document.getElementById("op-f-iva").value = d.iva != null ? String(d.iva) : "";
        document.getElementById("op-f-vencimiento").value = d.fecha_vencimiento || "";
        document.getElementById("op-f-categoria").value = d.categoria || "";
        document.getElementById("op-f-concepto").value = d.concepto || "";
        document.getElementById("op-f-sin-factura").checked = Boolean(d.sin_factura);
        document.getElementById("op-f-notas").value = d.notas || "";
        const chkZt = document.getElementById("op-f-es-zelle-tercero");
        const inpBenef = document.getElementById("op-f-beneficiario-tercero");
        if (chkZt) chkZt.checked = Boolean(d.es_zelle_tercero);
        if (inpBenef) inpBenef.value = d.beneficiario_tercero || "";
        _opPoblarSelectCatalogos(tipo, d.diario_id, d.cuenta_contable_id, d.cuenta_tercero_id);
        if (window.CxC && window.CxC.pagos && typeof window.CxC.pagos.syncZelleTerceroModalUI === "function") {
            window.CxC.pagos.syncZelleTerceroModalUI();
        }
        _opRenderAlertasValidacion(d.validacion || { faltantes: d.faltantes || [], alertas: d.alertas || [] });

        const tit = document.getElementById("op-modal-titulo");
        const sub = document.getElementById("op-modal-sub");
        if (tit) tit.textContent = d.id ? `${d.numero} · ${d.tipo_etiqueta || tipo}` : "Nuevo Documento Operativo";
        if (sub) sub.textContent = d.id ? `Estado: ${d.estado} · Origen: ${d.origen}${d.numero_recibo ? (" · Recibo: " + d.numero_recibo) : ""}` : "Validación en vivo contra CxC y Odoo antes de aprobar";

        const btnPrev = document.getElementById("op-btn-preview");
        if (btnPrev) {
            if (d.preview_path) { btnPrev.href = d.preview_path; btnPrev.style.display = "inline-block"; }
            else btnPrev.style.display = "none";
        }
        const btnRecDoc = document.getElementById("op-btn-recibo");
        if (btnRecDoc) {
            if (d.recibo_path && d.numero_recibo) {
                btnRecDoc.href = d.recibo_path;
                btnRecDoc.textContent = `🧾 Recibo ${d.numero_recibo}`;
                btnRecDoc.style.display = "inline-block";
            } else {
                btnRecDoc.style.display = "none";
            }
        }
        const editable = !d.id || d.puede_editar;
        document.getElementById("op-btn-guardar").style.display = editable ? "inline-block" : "none";
        const puedeCxc = Boolean(d.id && d.puede_confirmar_cxc && (d.tipo === "pago" ? _opPuedeAprobarPagos : _opPuedeAprobarCompras));
        const btnConfCxc = document.getElementById("op-btn-confirmar-cxc");
        if (btnConfCxc) btnConfCxc.style.display = puedeCxc ? "inline-block" : "none";
        const puedeApr = Boolean(d.id && d.puede_aprobar && (d.tipo === "pago" ? _opPuedeAprobarPagos : _opPuedeAprobarCompras));
        const puedeRec = Boolean(d.id && d.puede_rechazar && (d.tipo === "pago" ? _opPuedeAprobarPagos : _opPuedeAprobarCompras));
        const btnApr = document.getElementById("op-btn-aprobar");
        btnApr.style.display = puedeApr ? "inline-block" : "none";
        btnApr.textContent = d.es_zelle_tercero ? "📒 Crear Asiento Tercero en Odoo" : "✅ Autorizar y Enviar a Odoo";
        document.getElementById("op-btn-rechazar").style.display = puedeRec ? "inline-block" : "none";
        document.getElementById("op-modal-status").textContent = "";
    }

    window.abrirModalNuevoOperativo = async function(tipo = "pago") {
        await _opCargarCatalogos(false);
        _opLlenarFormulario({ tipo, moneda: "VES" });
        document.getElementById("modal-operativo").style.display = "flex";
    };

    window.editarOperativoPorId = async function(id) {
        await _opCargarCatalogos(false);
        try {
            const res = await fetch(`/api/operativos/${encodeURIComponent(id)}`);
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.detail || data.error || `Error ${res.status}`);
            _opLlenarFormulario(data);
            document.getElementById("modal-operativo").style.display = "flex";
        } catch (err) {
            alert("❌ " + err.message);
        }
    };

    async function _opRefrescarTablas() {
        const tareas = [loadOperativosCobranza(), loadOperativosCompras()];
        if (typeof window.loadOperativosPagosEnviados === "function") {
            tareas.push(window.loadOperativosPagosEnviados());
        }
        if (window.CxC && typeof window.CxC.loadInicioResumen === "function") {
            tareas.push(window.CxC.loadInicioResumen());
        }
        await Promise.all(tareas);
    }

    window.aprobarOperativo = async function(id) {
        if (!confirm("¿Autorizar este documento y enviarlo como borrador a Odoo?")) return;
        try {
            const res = await fetch(`/api/operativos/${encodeURIComponent(id)}/aprobar`, { method: "POST" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || data.detail || `Error ${res.status}`);
            alert(`✅ ${data.numero} enviado a Odoo (${data.odoo_ref}).`);
            await _opRefrescarTablas();
        } catch (err) {
            alert("⚠️ " + err.message);
        }
    };

    window.rechazarOperativo = async function(id) {
        const motivo = prompt("Motivo de rechazo (opcional):", "") || "";
        if (motivo === null) return;
        try {
            const res = await fetch(`/api/operativos/${encodeURIComponent(id)}/rechazar`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ motivo }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || data.detail || `Error ${res.status}`);
            await _opRefrescarTablas();
        } catch (err) {
            alert("⚠️ " + err.message);
        }
    };

    window.aprobarOperativoModal = async function() {
        const id = document.getElementById("op-f-id").value;
        if (!id) return;
        const st = document.getElementById("op-modal-status");
        st.style.color = "#1e40af";
        st.textContent = "Enviando a Odoo...";
        try {
            const res = await fetch(`/api/operativos/${encodeURIComponent(id)}/aprobar`, { method: "POST" });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || data.detail || `Error ${res.status}`);
            _opLlenarFormulario(data);
            st.style.color = "#15803d";
            st.textContent = `✅ Autorizado y creado en Odoo (${data.odoo_ref}).`;
            await _opRefrescarTablas();
        } catch (err) {
            st.style.color = "#b91c1c";
            st.textContent = "⚠️ " + err.message;
        }
    };

    window.rechazarOperativoModal = async function() {
        const id = document.getElementById("op-f-id").value;
        if (!id) return;
        const motivo = prompt("Motivo del rechazo:", "") || "";
        const st = document.getElementById("op-modal-status");
        try {
            const res = await fetch(`/api/operativos/${encodeURIComponent(id)}/rechazar`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ motivo }),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok) throw new Error(data.error || data.detail || `Error ${res.status}`);
            _opLlenarFormulario(data);
            st.style.color = "#991b1b";
            st.textContent = "❌ Documento marcado como rechazado.";
            await _opRefrescarTablas();
        } catch (err) {
            st.style.color = "#b91c1c";
            st.textContent = "⚠️ " + err.message;
        }
    };

    (function _initOperativosUI() {
        document.querySelectorAll(".pagos-subnav-btn").forEach((btn) => {
            btn.addEventListener("click", () => applyPagosSubpage(btn.getAttribute("data-pagos-subpage")));
        });
        document.querySelectorAll(".compras-subnav-btn").forEach((btn) => {
            btn.addEventListener("click", () => applyComprasSubpage(btn.getAttribute("data-compras-subpage")));
        });
        document.querySelectorAll(".cobranza-subnav-btn").forEach((btn) => {
            btn.addEventListener("click", () => applyCobranzaSubpage(btn.getAttribute("data-cobranza-subpage")));
        });
        document.getElementById("op-cob-estado")?.addEventListener("change", () => loadOperativosCobranza());
        let tCob = null;
        document.getElementById("op-cob-buscar")?.addEventListener("input", () => {
            clearTimeout(tCob);
            tCob = setTimeout(() => loadOperativosCobranza(), 280);
        });

        document.getElementById("op-cmp-tipo")?.addEventListener("change", loadOperativosCompras);
        document.getElementById("op-cmp-estado")?.addEventListener("change", loadOperativosCompras);
        let tCmp = null;
        document.getElementById("op-cmp-buscar")?.addEventListener("input", () => {
            clearTimeout(tCmp);
            tCmp = setTimeout(loadOperativosCompras, 280);
        });
        document.querySelectorAll(".op-cmp-kpi").forEach((k) => {
            k.addEventListener("click", () => {
                const s = document.getElementById("op-cmp-estado");
                if (s) { s.value = k.dataset.opEstado || ""; loadOperativosCompras(); }
            });
        });

        const fTipo = document.getElementById("op-f-tipo");
        fTipo?.addEventListener("change", () => {
            _opActualizarCamposVisibles(fTipo.value);
            _opProgramarValidacion();
        });
        ["op-f-contraparte", "op-f-rif", "op-f-monto", "op-f-moneda", "op-f-metodo",
         "op-f-referencia", "op-f-banco", "op-f-diario", "op-f-num-doc", "op-f-base",
         "op-f-iva", "op-f-categoria", "op-f-concepto", "op-f-sin-factura"].forEach((idEl) => {
            document.getElementById(idEl)?.addEventListener("input", _opProgramarValidacion);
            document.getElementById(idEl)?.addEventListener("change", _opProgramarValidacion);
        });

        const inpC = document.getElementById("op-f-buscar-contacto");
        const boxC = document.getElementById("op-resultados-contacto");
        let tBus = null;
        if (inpC && boxC) {
            inpC.addEventListener("input", () => {
                clearTimeout(tBus);
                const q = inpC.value.trim();
                if (!q) { boxC.style.display = "none"; return; }
                tBus = setTimeout(async () => {
                    try {
                        const r = await fetch(`/api/odoo/contactos?q=${encodeURIComponent(q)}&limit=8`);
                        const data = await r.json().catch(() => ({}));
                        const items = data.items || [];
                        boxC.innerHTML = items.length
                            ? items.map((c, i) => `<div data-op-pick="${i}" style="padding:0.45rem 0.75rem; cursor:pointer; border-bottom:1px solid #f1f5f9; font-size:0.84rem;">
                                <strong>${_capEsc(c.nombre)}</strong> <span style="color:#64748b;">(${_capEsc(c.clasificacion_etiqueta || c.clasificacion)}${c.rif ? " · " + _capEsc(c.rif) : ""})</span></div>`).join("")
                            : `<div style="padding:0.5rem 0.75rem; color:#64748b; font-size:0.82rem;">Sin coincidencias en Odoo</div>`;
                        boxC._items = items;
                        boxC.style.display = "block";
                    } catch (_e) { boxC.style.display = "none"; }
                }, 220);
            });
            boxC.addEventListener("click", (e) => {
                const row = e.target.closest("[data-op-pick]");
                if (!row || !boxC._items) return;
                const c = boxC._items[Number(row.dataset.opPick)];
                if (!c) return;
                document.getElementById("op-f-partner-id").value = String(c.id);
                document.getElementById("op-f-contraparte").value = c.nombre || "";
                document.getElementById("op-f-rif").value = c.rif || "";
                inpC.value = c.nombre || "";
                boxC.style.display = "none";
                _opProgramarValidacion();
            });
        }

        document.getElementById("op-modal-form")?.addEventListener("submit", async (e) => {
            e.preventDefault();
            const st = document.getElementById("op-modal-status");
            const idVal = document.getElementById("op-f-id").value;
            const body = _opRecolectarFormulario();
            st.style.color = "#1e40af";
            st.textContent = "Guardando...";
            try {
                const res = await fetch(idVal ? `/api/operativos/${encodeURIComponent(idVal)}` : "/api/operativos", {
                    method: idVal ? "PUT" : "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.error || data.detail || `Error ${res.status}`);
                _opLlenarFormulario(data);
                st.style.color = "#15803d";
                st.textContent = `✅ Guardado (${data.numero}).`;
                await _opRefrescarTablas();
            } catch (err) {
                st.style.color = "#b91c1c";
                st.textContent = "⚠️ " + err.message;
            }
        });
    })();

    window.CxC = Object.assign(window.CxC || {}, {
        esc: _capEsc,
        monto: _capMonto,
        estadoBadge: _opEstadoBadge,
        alertasHtml: _opAlertasHtml,
        navigate: (page, tab = "") => {
            const p = _normalizarModuloRuta(page);
            const url = tab ? `/${p}?tab=${encodeURIComponent(tab)}` : `/${p}`;
            history.pushState(null, "", url);
            initCurrentPage();
        },
        applyPagosSubpage,
        applyComprasSubpage,
        applyConfigSubpage,
        switchReporteSubtab: window.switchReporteSubtab,
        switchAuditoriaSubtab: window.switchAuditoriaSubtab,
        getSession: () => currentUserSession,
        puedeAprobarPagos: () => _opPuedeAprobarPagos,
        puedeAprobarCompras: () => _opPuedeAprobarCompras,
        setPuedeAprobarPagos: (val) => { _opPuedeAprobarPagos = Boolean(val); },
        llenarModalOperativo: _opLlenarFormulario,
        refrescarOperativos: _opRefrescarTablas,
        programarValidacionOperativo: _opProgramarValidacion,
        recargarCatalogosOperativos: () => _opCargarCatalogos(true),
        abrirModalNuevoOperativoCustom: async (defaults) => {
            await _opCargarCatalogos(true);
            _opLlenarFormulario(defaults || { tipo: "pago", moneda: "VES" });
            document.getElementById("modal-operativo").style.display = "flex";
        },
    });

    // SPA navigation click handling for nav links and launchpad module cards
    document.querySelectorAll(".nav-link").forEach(link => {
        link.addEventListener("click", (e) => {
            if (e.target.closest(".nav-sub-link")) return;
            const targetPage = link.dataset.page;
            if (targetPage) {
                e.preventDefault();
                window.CxC.navigate(targetPage);
            }
        });
    });

    window.addEventListener("popstate", () => {
        initCurrentPage();
    });

    // Initialize current page tab and load its data after all functions are declared
    initCurrentPage();
});
