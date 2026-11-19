/* Shell de Navegación (Fase 1): Sidebar colapsable, Topbar, /inicio y Buscador Ctrl+K */
(function () {
    "use strict";

    const TITULOS_MODULO = {
        inicio: "🏠 Inicio",
        pagos: "💳 Pagos",
        ventas: "💰 Ventas",
        compras: "🛒 Compras",
        reportes: "📑 Reportes (Solo Lectura)",
        auditoria: "🔍 Auditoría",
        facturacion: "🧾 Facturación",
        inventario: "📦 Inventario",
        contactos: "👥 Contactos",
        configuracion: "⚙️ Configuración",
    };

    const LS_SIDEBAR_KEY = "cxc_sidebar_collapsed";
    let _searchTimer = null;

    function _esc(v) {
        if (window.CxC && typeof window.CxC.esc === "function") return window.CxC.esc(v);
        return String(v == null ? "" : v)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function _moduloDeRuta() {
        const seg = window.location.pathname.toLowerCase().replace(/^\/+|\/+$/g, "").split("/")[0] || "inicio";
        const alias = {
            dashboard: "inicio",
            cobranza: "pagos",
            conciliaciones: "pagos",
            bancos: "pagos",
            capturas: "compras",
            bots: "configuracion",
            reporte: "reportes",
        };
        return alias[seg] || seg;
    }

    function _cerrarSidebarMovil() {
        const shell = document.getElementById("app-shell");
        if (shell) shell.classList.remove("sidebar-mobile-open");
    }

    function _actualizarBadgeSidebar(id, valor) {
        const el = document.getElementById(id);
        if (!el) return;
        const n = Number(valor) || 0;
        el.textContent = String(n);
        el.classList.toggle("visible", n > 0);
    }

    function _actualizarBadgeTarjeta(id, valor) {
        const el = document.getElementById(id);
        if (!el) return;
        const n = Number(valor) || 0;
        el.textContent = n > 0 ? `${n} pendiente${n === 1 ? "" : "s"}` : "Al día";
        el.classList.toggle("alerta", n > 0);
    }

    async function loadInicioResumen() {
        try {
            const res = await fetch("/api/inicio/resumen", { cache: "no-store" });
            if (!res.ok) return;
            const data = await res.json();
            const m = data.modulos || {};
            const c = data.contadores || {};

            _actualizarBadgeSidebar("nav-badge-pagos", m.pagos);
            _actualizarBadgeSidebar("nav-badge-ventas", m.ventas);
            _actualizarBadgeSidebar("nav-badge-compras", m.compras);
            _actualizarBadgeSidebar("nav-badge-auditoria", m.auditoria);

            _actualizarBadgeTarjeta("inicio-badge-pagos", m.pagos);
            _actualizarBadgeTarjeta("inicio-badge-ventas", m.ventas);
            _actualizarBadgeTarjeta("inicio-badge-compras", m.compras);
            _actualizarBadgeTarjeta("inicio-badge-auditoria", m.auditoria);

            const bEnv = document.getElementById("badge-op-enviados-pend");
            if (bEnv && c.pagos_enviados_pendientes != null) {
                bEnv.textContent = String(c.pagos_enviados_pendientes);
            }
        } catch (_err) {
            // Ignorar fallos silenciosos al cargar contadores auxiliares.
        }
    }

    function _abrirBuscadorGlobal() {
        const modal = document.getElementById("modal-global-search");
        const input = document.getElementById("global-search-input");
        if (!modal) return;
        modal.style.display = "flex";
        if (input) {
            input.focus();
            input.select();
        }
    }

    function _cerrarBuscadorGlobal() {
        const modal = document.getElementById("modal-global-search");
        if (modal) modal.style.display = "none";
    }

    function _navegarDesdeUrl(urlStr) {
        try {
            const u = new URL(urlStr, window.location.origin);
            const path = u.pathname.replace(/^\/+|\/+$/g, "") || "inicio";
            const tab = u.searchParams.get("tab") || "";
            if (window.CxC && typeof window.CxC.navigate === "function") {
                window.CxC.navigate(path, tab);
                return;
            }
        } catch (_e) { /* fallback abajo */ }
        window.location.href = urlStr;
    }

    async function _ejecutarBusquedaGlobal(termino) {
        const box = document.getElementById("global-search-results");
        if (!box) return;
        if (termino.trim().length < 2) {
            box.innerHTML = '<div style="color:#64748b; font-size:0.84rem; padding:0.5rem;">Escribe al menos 2 caracteres para buscar en CxC.</div>';
            return;
        }
        box.innerHTML = '<div style="color:#64748b; font-size:0.84rem; padding:0.5rem;">Buscando...</div>';
        try {
            const res = await fetch(`/api/inicio/buscar?q=${encodeURIComponent(termino.trim())}`);
            const data = await res.json().catch(() => ({}));
            const items = data.items || [];
            if (!items.length) {
                box.innerHTML = '<div style="color:#64748b; font-size:0.84rem; padding:0.5rem;">Sin resultados locales para esta búsqueda.</div>';
                return;
            }
            box.innerHTML = items.map((it) => `
                <button type="button" class="global-search-item" data-url="${_esc(it.url)}" style="text-align:left; width:100%; border:1px solid #e2e8f0; background:#f8fafc; border-radius:8px; padding:0.55rem 0.8rem; cursor:pointer; display:flex; justify-content:space-between; align-items:center; gap:0.75rem;">
                    <div>
                        <div style="font-weight:600; color:#0f172a; font-size:0.88rem;">${_esc(it.titulo)}</div>
                        <div style="font-size:0.76rem; color:#64748b;">${_esc(it.tipo)}${it.subtitulo ? " · " + _esc(it.subtitulo) : ""}</div>
                    </div>
                    <span class="state-badge">${_esc(it.estado || "Abrir")}</span>
                </button>
            `).join("");
        } catch (_err) {
            box.innerHTML = '<div style="color:#be123c; font-size:0.84rem; padding:0.5rem;">Error de conexión al buscar.</div>';
        }
    }

    function onPageChange(path) {
        const tit = document.getElementById("topbar-module-title");
        if (tit) tit.textContent = TITULOS_MODULO[path] || "Lubrikca CxC";
        _cerrarSidebarMovil();
    }

    window.CxC = Object.assign(window.CxC || {}, {
        onPageChange,
        loadInicioResumen,
    });

    document.addEventListener("DOMContentLoaded", () => {
        const shell = document.getElementById("app-shell");
        if (shell && window.innerWidth > 960) {
            try {
                if (localStorage.getItem(LS_SIDEBAR_KEY) === "1") {
                    shell.classList.add("sidebar-collapsed");
                }
            } catch (_e) { /* ignorar */ }
        }

        document.getElementById("btn-sidebar-toggle")?.addEventListener("click", () => {
            if (!shell) return;
            if (window.innerWidth <= 960) {
                shell.classList.toggle("sidebar-mobile-open");
            } else {
                const col = shell.classList.toggle("sidebar-collapsed");
                try {
                    localStorage.setItem(LS_SIDEBAR_KEY, col ? "1" : "0");
                } catch (_e) { /* ignorar */ }
            }
        });

        document.getElementById("sidebar-backdrop")?.addEventListener("click", _cerrarSidebarMovil);

        document.addEventListener("click", (ev) => {
            const subLink = ev.target.closest(".nav-sub-link");
            if (subLink) {
                const page = subLink.dataset.page;
                if (page && window.CxC && typeof window.CxC.navigate === "function") {
                    ev.preventDefault();
                    ev.stopPropagation();
                    window.CxC.navigate(page, subLink.dataset.tab || "");
                }
                return;
            }
            const resItem = ev.target.closest(".global-search-item");
            if (resItem && resItem.dataset.url) {
                _cerrarBuscadorGlobal();
                _navegarDesdeUrl(resItem.dataset.url);
            }
        });

        document.getElementById("btn-global-search")?.addEventListener("click", _abrirBuscadorGlobal);
        document.getElementById("global-search-close")?.addEventListener("click", _cerrarBuscadorGlobal);
        const modalSearch = document.getElementById("modal-global-search");
        modalSearch?.addEventListener("click", (ev) => {
            if (ev.target === modalSearch) _cerrarBuscadorGlobal();
        });

        document.getElementById("global-search-input")?.addEventListener("input", (ev) => {
            clearTimeout(_searchTimer);
            const val = ev.target.value;
            _searchTimer = setTimeout(() => _ejecutarBusquedaGlobal(val), 250);
        });

        document.addEventListener("keydown", (ev) => {
            if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
                ev.preventDefault();
                _abrirBuscadorGlobal();
            } else if (ev.key === "Escape") {
                _cerrarBuscadorGlobal();
                _cerrarSidebarMovil();
            }
        });

        onPageChange(_moduloDeRuta());
        loadInicioResumen();
    });
})();
