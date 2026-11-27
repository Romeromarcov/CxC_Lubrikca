import React, { useCallback, useEffect, useMemo, useState } from "react";
import { SafeAreaView, Text, TouchableOpacity, View } from "react-native";
import NetInfo from "@react-native-community/netinfo";
import { StatusBar } from "expo-status-bar";
import { LubrikcaApiClient, type TasasVigentes } from "@lubrikca/domain-ts";

import {
  cerrarSesionYLimpiarDispositivo,
  MobileSessionStore,
  type SessionTokens,
} from "./src/auth/sessionStore";
import { AppHeader } from "./src/components/AppHeader";
import { ExpoSqliteExecutor } from "./src/db/expoAdapter";
import { MobileSqliteRepo } from "./src/db/sqliteRepo";
import { CatalogoScreen, type ProductoConPrecio } from "./src/screens/CatalogoScreen";
import { ClientesScreen } from "./src/screens/ClientesScreen";
import { CotizacionScreen, type LineaCarritoMobile } from "./src/screens/CotizacionScreen";
import { LoginScreen } from "./src/screens/LoginScreen";
import { OutboxScreen } from "./src/screens/OutboxScreen";
import { PagoScreen } from "./src/screens/PagoScreen";
import { SyncEngine, type SyncReport } from "./src/sync/syncEngine";
import { COLORS, UI } from "./src/theme";

type TabId = "catalogo" | "cotizar" | "pagos" | "clientes" | "outbox";

const API_BASE_URL =
  process.env.EXPO_PUBLIC_CXC_API_URL ?? "https://cxclubrikca-stagingv1.up.railway.app";

export default function App(): React.JSX.Element {
  const repo = useMemo(() => new MobileSqliteRepo(new ExpoSqliteExecutor()), []);
  const sessionStore = useMemo(() => new MobileSessionStore(), []);
  const [session, setSession] = useState<SessionTokens | null>(null);
  const [deviceId, setDeviceId] = useState<string>("");
  const [listo, setListo] = useState(false);
  const [isOnline, setIsOnline] = useState(true);
  const [tab, setTab] = useState<TabId>("catalogo");

  const [syncVersion, setSyncVersion] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [lastReport, setLastReport] = useState<SyncReport | null>(null);
  const [tasas, setTasas] = useState<TasasVigentes | null>(null);
  const [outboxPendientes, setOutboxPendientes] = useState(0);
  const [conflictosCount, setConflictosCount] = useState(0);

  const [carritoLineas, setCarritoLineas] = useState<LineaCarritoMobile[]>([]);
  const [clientePreseleccionadoId, setClientePreseleccionadoId] = useState<string | null>(null);

  const api = useMemo(
    () =>
      new LubrikcaApiClient({
        baseUrl: API_BASE_URL,
        getAccessToken: async () => (await sessionStore.leerSesion())?.access_token ?? null,
        getRefreshToken: async () => (await sessionStore.leerSesion())?.refresh_token ?? null,
        onTokensRefreshed: async (t) => {
          const saved = await sessionStore.guardarSesion(t);
          setSession(saved);
        },
        onSessionRevoked: async () => {
          await sessionStore.limpiarTokens();
          await repo.wipeAllTables();
          setSession(null);
        },
      }),
    [repo, sessionStore],
  );

  const syncEngine = useMemo(
    () => new SyncEngine({ api, repo, sessionStore }),
    [api, repo, sessionStore],
  );

  const refrescarMetaLocal = useCallback(async (): Promise<void> => {
    const [t, res] = await Promise.all([
      repo.obtenerTasasVigentes(),
      repo.obtenerResumenLocal(),
    ]);
    setTasas(t);
    setOutboxPendientes(res.outboxPendientes);
    setConflictosCount(res.outboxConflictos);
    setSyncVersion((v) => v + 1);
  }, [repo]);

  const ejecutarSyncManual = useCallback(
    async (devIdOverride?: string): Promise<SyncReport> => {
      const targetDev = devIdOverride || session?.device_id || deviceId;
      if (!targetDev) {
        return {
          adjuntosSubidos: 0,
          itemsEnviados: 0,
          itemsSync: 0,
          itemsConflicto: 0,
          itemsRetry: 0,
          deltaAplicado: false,
          sesionRevocada: false,
          errorMsg: "Dispositivo aún inicializándose.",
        };
      }
      setSyncing(true);
      setSyncError(null);
      try {
        const rep = await syncEngine.sincronizarCompleto(targetDev);
        setLastReport(rep);
        if (rep.sesionRevocada) {
          setSession(null);
          return rep;
        }
        if (rep.errorMsg) {
          setSyncError(rep.errorMsg);
        }
        await refrescarMetaLocal();
        return rep;
      } finally {
        setSyncing(false);
      }
    },
    [session, deviceId, syncEngine, refrescarMetaLocal],
  );

  useEffect(() => {
    void (async () => {
      await repo.init();
      const devId = await sessionStore.obtenerOCrearDeviceId();
      setDeviceId(devId);
      const s = await sessionStore.leerSesion();
      if (s && (await sessionStore.verificarBiometriaSiDisponible())) {
        setSession(s);
        await refrescarMetaLocal();
      }
      setListo(true);
    })();

    const unsubNet = NetInfo.addEventListener((state) => {
      setIsOnline(Boolean(state.isConnected && state.isInternetReachable !== false));
    });
    return () => unsubNet();
  }, [repo, sessionStore, refrescarMetaLocal]);

  useEffect(() => {
    if (!session) return undefined;
    void ejecutarSyncManual(session.device_id);
    return syncEngine.adjuntarListenerRed(session.device_id, (rep) => {
      setLastReport(rep);
      if (rep.sesionRevocada) {
        setSession(null);
        return;
      }
      setSyncError(rep.errorMsg ?? null);
      void refrescarMetaLocal();
    });
  }, [session?.device_id]);

  function agregarAlCarritoDesdeCatalogo(prod: ProductoConPrecio, activeListaId: string): void {
    setCarritoLineas((prev) => {
      const idx = prev.findIndex((x) => x.producto_id === prod.producto_id);
      if (idx >= 0) {
        return prev.map((x, i) => (i === idx ? { ...x, cantidad: x.cantidad + 1 } : x));
      }
      return [
        ...prev,
        {
          producto_id: prod.producto_id,
          codigo: prod.codigo,
          nombre: prod.nombre,
          cantidad: 1,
          precio_usd: prod.precio_usd,
          precio_ves: prod.precio_ves,
          precios_por_lista: prod.precios_por_lista,
          precio_unitario: prod.precio_lista,
          lista_id: activeListaId,
        },
      ];
    });
  }

  const carritoCantidades = useMemo(() => {
    const map: Record<string, number> = {};
    for (const l of carritoLineas) {
      map[l.producto_id] = (map[l.producto_id] ?? 0) + l.cantidad;
    }
    return map;
  }, [carritoLineas]);

  if (!listo) {
    return (
      <SafeAreaView style={[UI.screen, { justifyContent: "center", alignItems: "center" }]}>
        <Text style={UI.title}>Inicializando Lubrikca Móvil...</Text>
      </SafeAreaView>
    );
  }

  if (!session) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: COLORS.navy }}>
        <StatusBar style="light" />
        <LoginScreen
          api={api}
          sessionStore={sessionStore}
          deviceId={deviceId || "lubrikca-pending-device"}
          onLoggedIn={(s) => {
            setSession(s);
          }}
        />
      </SafeAreaView>
    );
  }

  const badgeOutbox = conflictosCount + outboxPendientes;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: COLORS.navy }}>
      <StatusBar style="light" />
      <AppHeader
        userNombre={session.user_nombre}
        userEmail={session.user_email}
        isOnline={isOnline}
        syncing={syncing}
        tasas={tasas}
        syncError={syncError}
        onSyncNow={() => void ejecutarSyncManual()}
      />

      <View style={{ flex: 1, backgroundColor: COLORS.bg }}>
        {tab === "catalogo" ? (
          <CatalogoScreen
            repo={repo}
            api={api}
            isOnline={isOnline}
            syncing={syncing}
            syncVersion={syncVersion}
            carritoCantidades={carritoCantidades}
            onSyncNow={async () => {
              await ejecutarSyncManual();
            }}
            onAgregarAlCarrito={agregarAlCarritoDesdeCatalogo}
          />
        ) : null}
        {tab === "cotizar" ? (
          <CotizacionScreen
            repo={repo}
            vendedorEmail={session.user_email}
            syncVersion={syncVersion}
            tasas={tasas}
            clientePreseleccionadoId={clientePreseleccionadoId}
            lineas={carritoLineas}
            setLineas={setCarritoLineas}
            onLimpiarPreseleccion={() => setClientePreseleccionadoId(null)}
            onSyncNow={async () => {
              await ejecutarSyncManual();
            }}
          />
        ) : null}
        {tab === "pagos" ? (
          <PagoScreen
            repo={repo}
            vendedorEmail={session.user_email}
            syncVersion={syncVersion}
            clientePreseleccionadoId={clientePreseleccionadoId}
            onLimpiarPreseleccion={() => setClientePreseleccionadoId(null)}
            onSyncNow={async () => {
              await ejecutarSyncManual();
            }}
          />
        ) : null}
        {tab === "clientes" ? (
          <ClientesScreen
            repo={repo}
            vendedorEmail={session.user_email}
            syncing={syncing}
            syncVersion={syncVersion}
            onSyncNow={async () => {
              await ejecutarSyncManual();
            }}
            onCrearPedidoPara={(cid) => {
              setClientePreseleccionadoId(cid);
              setTab("cotizar");
            }}
            onRegistrarPagoPara={(cid) => {
              setClientePreseleccionadoId(cid);
              setTab("pagos");
            }}
          />
        ) : null}
        {tab === "outbox" ? (
          <OutboxScreen
            repo={repo}
            isOnline={isOnline}
            syncing={syncing}
            syncVersion={syncVersion}
            lastReport={lastReport}
            onSyncNow={async () => ejecutarSyncManual()}
            onLogout={() => {
              void cerrarSesionYLimpiarDispositivo(api, sessionStore, repo).then(() => {
                setCarritoLineas([]);
                setSession(null);
              });
            }}
          />
        ) : null}
      </View>

      <View
        style={{
          flexDirection: "row",
          borderTopWidth: 1,
          borderColor: COLORS.border,
          backgroundColor: COLORS.card,
          paddingTop: 6,
          paddingBottom: 8,
        }}
      >
        {(
          [
            ["catalogo", "Catálogo", "📦", 0],
            ["cotizar", "Cotizar", "📝", carritoLineas.length],
            ["pagos", "Cobros", "💳", 0],
            ["clientes", "Clientes", "👥", 0],
            ["outbox", "Sync", "↻", badgeOutbox],
          ] as Array<[TabId, string, string, number]>
        ).map(([id, label, icon, badge]) => {
          const active = tab === id;
          return (
            <TouchableOpacity
              key={id}
              onPress={() => setTab(id)}
              style={{ flex: 1, alignItems: "center", paddingVertical: 2 }}
            >
              <View
                style={{
                  height: 3,
                  width: 28,
                  borderRadius: 99,
                  backgroundColor: active ? COLORS.primary : "transparent",
                  marginBottom: 4,
                }}
              />
              <View style={{ flexDirection: "row", alignItems: "center", gap: 3 }}>
                <Text style={{ fontSize: 15 }}>{icon}</Text>
                {badge > 0 ? (
                  <View
                    style={{
                      backgroundColor: id === "outbox" && conflictosCount > 0 ? COLORS.danger : COLORS.primary,
                      borderRadius: 99,
                      paddingHorizontal: 5,
                      paddingVertical: 1,
                    }}
                  >
                    <Text style={{ color: "#FFF", fontSize: 10, fontWeight: "800" }}>{badge}</Text>
                  </View>
                ) : null}
              </View>
              <Text
                style={{
                  fontWeight: active ? "800" : "600",
                  fontSize: 11,
                  color: active ? COLORS.primary : COLORS.muted,
                  marginTop: 2,
                }}
              >
                {label}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>
    </SafeAreaView>
  );
}
