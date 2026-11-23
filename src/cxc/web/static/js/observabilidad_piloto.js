/* Observabilidad del Piloto App Móvil y Portal de Clientes (Fase 4) */
(function () {
    "use strict";

    function _esc(v) {
        if (window.CxC && typeof window.CxC.esc === "function") return window.CxC.esc(v);
        return String(v == null ? "" : v)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function _setText(id, val) {
        const el = document.getElementById(id);
        if (el) el.textContent = String(val);
    }

    async function cargarMetricasPiloto() {
        const tbody = document.getElementById("piloto-seguridad-tbody");
        if (!document.getElementById("observabilidad-piloto-card")) return;
        try {
            const res = await fetch("/api/v1/observabilidad/metricas-piloto", {
                credentials: "same-origin",
                cache: "no-store",
            });
            if (!res.ok) return;
            const d = await res.json();

            const tasa = Number(d.tasa_exito_sync_pct ?? 100);
            _setText("piloto-tasa-sync", `${tasa.toFixed(2)}% (Meta >= 99%)`);
            _setText(
                "piloto-destino-ajeno",
                `${Number(d.pagos_aprobados_destino_ajeno || 0)} aprobados / ${Number(d.pagos_bloqueados_destino_ajeno || 0)} bloqueados`,
            );
            const st = d.storage || {};
            _setText(
                "piloto-storage-backend",
                st.s3_configurado ? `Cloudflare R2 / S3 (${st.bucket})` : "Local efímero (Railway)",
            );
            _setText(
                "piloto-dispositivos-portal",
                `${Number(d.dispositivos_moviles_activos || 0)} móviles / ${Number(d.cuentas_portal_activas || 0)} portal`,
            );
            const push = d.push_outbox || {};
            _setText(
                "piloto-push-outbox",
                `${Number(push.enviadas || 0)} enviadas · ${Number(push.pendientes || 0)} pend. · ${Number(push.fallidas_permanentes || 0)} perm.`,
            );

            if (tbody) {
                const evs = Array.isArray(d.ultimos_eventos_seguridad) ? d.ultimos_eventos_seguridad : [];
                if (!evs.length) {
                    tbody.innerHTML = `<tr><td colspan="4" class="table-empty">Sin eventos recientes de seguridad v1.</td></tr>`;
                } else {
                    tbody.innerHTML = evs
                        .map(
                            (e) => `<tr>
                                <td>${_esc(e.cambiado_en || "—")}</td>
                                <td>${_esc(e.email || "—")}</td>
                                <td>${_esc(e.usuario || "—")}</td>
                                <td>${_esc(e.comentario || "—")}</td>
                            </tr>`,
                        )
                        .join("");
                }
            }
        } catch (_err) {
            if (tbody) {
                tbody.innerHTML = `<tr><td colspan="4" class="table-empty">No se pudieron cargar las métricas de piloto.</td></tr>`;
            }
        }
    }

    window.cargarMetricasPiloto = cargarMetricasPiloto;

    function _init() {
        const btn = document.getElementById("btn-refresh-metricas-piloto");
        if (btn) btn.addEventListener("click", cargarMetricasPiloto);
        document.querySelectorAll('.config-subnav-btn[data-config-subpage="motor"]').forEach((b) => {
            b.addEventListener("click", () => setTimeout(cargarMetricasPiloto, 80));
        });
        cargarMetricasPiloto();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", _init);
    } else {
        _init();
    }
})();
