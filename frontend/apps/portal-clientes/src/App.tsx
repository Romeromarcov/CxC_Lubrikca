import React, { useEffect, useMemo, useState } from "react";
import { LubrikcaApiClient, type AuthTokensResponse } from "@lubrikca/domain-ts";

import { AuthView } from "./components/AuthView";
import { CatalogoPedidosView } from "./components/CatalogoPedidosView";
import { EquipoPortalView } from "./components/EquipoPortalView";
import { EstadoCuentaView } from "./components/EstadoCuentaView";
import { RegistrarPagoView } from "./components/RegistrarPagoView";
import { TrazabilidadView, type PedidoTrazabilidad } from "./components/TrazabilidadView";
import { permisosPorRolPortal, type RecompraPayload } from "./portalLogic";

type TabPortal = "estado" | "catalogo" | "pago" | "pedidos" | "equipo";
const STORAGE_KEY = "lubrikca_portal_tokens_v1";

function leerTokensStorage(): AuthTokensResponse | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as AuthTokensResponse) : null;
  } catch {
    return null;
  }
}

export default function App(): React.JSX.Element {
  const [tokens, setTokens] = useState<AuthTokensResponse | null>(() => leerTokensStorage());
  const [tab, setTab] = useState<TabPortal>("estado");
  const [pedidos, setPedidos] = useState<PedidoTrazabilidad[]>([]);
  const [recompra, setRecompra] = useState<RecompraPayload | null>(null);
  const [notifs, setNotifs] = useState<Array<Record<string, unknown>>>([]);
  const [noLeidas, setNoLeidas] = useState(0);
  const [mostrarNotifs, setMostrarNotifs] = useState(false);
  const [pushMsg, setPushMsg] = useState<string | null>(null);

  const initialTokenInv = useMemo(() => {
    const sp = new URLSearchParams(window.location.search);
    return sp.get("token") || "";
  }, []);

  const api = useMemo(
    () =>
      new LubrikcaApiClient({
        baseUrl: window.location.origin,
        getAccessToken: () => leerTokensStorage()?.access_token ?? null,
        getRefreshToken: () => leerTokensStorage()?.refresh_token ?? null,
        onTokensRefreshed: (next) => {
          window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
          setTokens(next);
        },
        onSessionRevoked: () => {
          window.localStorage.removeItem(STORAGE_KEY);
          setTokens(null);
        },
      }),
    [],
  );

  const permisos = useMemo(
    () => permisosPorRolPortal(tokens?.cuenta?.rol_portal ?? "titular"),
    [tokens],
  );

  useEffect(() => {
    if (!tokens) return;
    if (!permisos.verEstadoCuenta && permisos.verCatalogoYCrearPedidos && tab === "estado") {
      setTab("catalogo");
    }
    void api
      .portalPedidos()
      .then((res) => {
        setPedidos((res.pedidos as PedidoTrazabilidad[]) || []);
        setRecompra((res.ultimo_pedido_recompra as RecompraPayload) ?? null);
      })
      .catch(() => undefined);
    void api
      .listarNotificaciones("portal", 20)
      .then((res) => {
        setNotifs((res.items as Array<Record<string, unknown>>) || []);
        setNoLeidas(Number(res.no_leidas || 0));
      })
      .catch(() => undefined);
  }, [tokens, api, permisos, tab]);

  if (!tokens) {
    return (
      <AuthView
        api={api}
        initialTokenInvitacion={initialTokenInv}
        onAuthenticated={(auth) => {
          window.localStorage.setItem(STORAGE_KEY, JSON.stringify(auth));
          setTokens(auth);
        }}
      />
    );
  }

  const navItems: Array<{ id: TabPortal; label: string; visible: boolean }> = [
    { id: "estado", label: "Estado Cuenta", visible: permisos.verEstadoCuenta },
    { id: "catalogo", label: "Catálogo", visible: permisos.verCatalogoYCrearPedidos },
    { id: "pago", label: "Pagar", visible: permisos.registrarPagos },
    { id: "pedidos", label: "Pedidos", visible: permisos.verTrazabilidad },
    { id: "equipo", label: "Equipo", visible: permisos.administrarEquipo },
  ];

  async function cerrarSesion(): Promise<void> {
    const cur = leerTokensStorage();
    if (cur) {
      await api.logoutPortal(cur.refresh_token, cur.device_id).catch(() => undefined);
    }
    window.localStorage.removeItem(STORAGE_KEY);
    setTokens(null);
  }

  async function activarWebPush(): Promise<void> {
    setPushMsg(null);
    const ep = `https://webpush.lubrikca.local/sub/${tokens?.cuenta?.cuenta_id || "0"}`;
    try {
      await api.portalSuscribirWebPush({
        endpoint: ep,
        user_agent: navigator.userAgent.slice(0, 180),
      });
      setPushMsg("Alertas Web Push activadas para tu cuenta.");
    } catch (e) {
      setPushMsg(e instanceof Error ? e.message : "No se pudieron activar las alertas.");
    }
  }

  async function marcarLeida(nid: number): Promise<void> {
    await api.marcarNotificacionLeida(nid, "portal").catch(() => undefined);
    setNotifs((prev) =>
      prev.map((n) => (Number(n.notificacion_id) === nid ? { ...n, leida_en: "leida" } : n)),
    );
    setNoLeidas((c) => Math.max(0, c - 1));
  }

  return (
    <div className="portal-shell">
      <header className="portal-topbar">
        <div>
          <h1>Lubrikca · {tokens.cuenta?.cliente_nombre || "Portal Clientes"}</h1>
          <small>{tokens.cuenta?.email} · Rol: {tokens.cuenta?.rol_portal}</small>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setMostrarNotifs((v) => !v)}
          >
            🔔 Notificaciones {noLeidas > 0 ? `(${noLeidas})` : ""}
          </button>
          <button type="button" className="btn btn-sm" onClick={() => void cerrarSesion()}>
            Cerrar Sesión
          </button>
        </div>
      </header>

      <main className="portal-container">
        {mostrarNotifs ? (
          <div className="card" style={{ borderColor: "var(--primary)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
              <strong>Notificaciones de tus pedidos y pagos</strong>
              <button type="button" className="btn btn-sm" onClick={() => void activarWebPush()}>
                Activar Alertas Web Push
              </button>
            </div>
            {pushMsg ? <div className="badge badge-ok" style={{ marginBottom: 8 }}>{pushMsg}</div> : null}
            {notifs.length === 0 ? (
              <div style={{ color: "var(--muted)", fontSize: "0.86rem" }}>Sin notificaciones nuevas.</div>
            ) : (
              notifs.map((n) => (
                <div key={String(n.notificacion_id)} style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid var(--border)", gap: 8 }}>
                  <div>
                    <strong>{String(n.titulo || "")}</strong>
                    <div style={{ fontSize: "0.82rem", color: "var(--muted)" }}>{String(n.cuerpo || "")}</div>
                  </div>
                  {!n.leida_en ? (
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => void marcarLeida(Number(n.notificacion_id))}
                    >
                      Leída
                    </button>
                  ) : null}
                </div>
              ))
            )}
          </div>
        ) : null}
        <nav className="portal-nav-desktop" aria-label="Navegación Portal">
          {navItems.filter((n) => n.visible).map((n) => (
            <button
              key={n.id}
              type="button"
              className={`btn btn-tab ${tab === n.id ? "active" : ""}`}
              onClick={() => setTab(n.id)}
            >
              {n.label}
            </button>
          ))}
        </nav>

        {tab === "estado" && permisos.verEstadoCuenta ? <EstadoCuentaView api={api} /> : null}
        {tab === "catalogo" && permisos.verCatalogoYCrearPedidos ? (
          <CatalogoPedidosView api={api} recompra={recompra} />
        ) : null}
        {tab === "pago" && permisos.registrarPagos ? <RegistrarPagoView api={api} /> : null}
        {tab === "pedidos" && permisos.verTrazabilidad ? <TrazabilidadView pedidos={pedidos} /> : null}
        {tab === "equipo" && permisos.administrarEquipo ? (
          <EquipoPortalView api={api} miCuentaId={Number(tokens.cuenta?.cuenta_id || 0)} />
        ) : null}
      </main>

      <nav className="portal-nav-mobile" aria-label="Menú inferior móvil">
        {navItems.filter((n) => n.visible).map((n) => (
          <button
            key={n.id}
            type="button"
            className={`btn btn-sm btn-tab ${tab === n.id ? "active" : ""}`}
            onClick={() => setTab(n.id)}
          >
            {n.label}
          </button>
        ))}
      </nav>
    </div>
  );
}
