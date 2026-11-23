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

  return (
    <div className="portal-shell">
      <header className="portal-topbar">
        <div>
          <h1>Lubrikca · {tokens.cuenta?.cliente_nombre || "Portal Clientes"}</h1>
          <small>{tokens.cuenta?.email} · Rol: {tokens.cuenta?.rol_portal}</small>
        </div>
        <button type="button" className="btn btn-sm" onClick={() => void cerrarSesion()}>
          Cerrar Sesión
        </button>
      </header>

      <main className="portal-container">
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
