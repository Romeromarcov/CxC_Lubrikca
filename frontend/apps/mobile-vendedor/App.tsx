import React, { useEffect, useMemo, useState } from "react";
import { SafeAreaView, Text, TouchableOpacity, View } from "react-native";
import NetInfo from "@react-native-community/netinfo";
import { StatusBar } from "expo-status-bar";
import { LubrikcaApiClient } from "@lubrikca/domain-ts";

import {
  cerrarSesionYLimpiarDispositivo,
  MobileSessionStore,
  type SessionTokens,
} from "./src/auth/sessionStore";
import { ExpoSqliteExecutor } from "./src/db/expoAdapter";
import { MobileSqliteRepo } from "./src/db/sqliteRepo";
import { CatalogoScreen } from "./src/screens/CatalogoScreen";
import { ClientesScreen } from "./src/screens/ClientesScreen";
import { CotizacionScreen } from "./src/screens/CotizacionScreen";
import { LoginScreen } from "./src/screens/LoginScreen";
import { OutboxScreen } from "./src/screens/OutboxScreen";
import { PagoScreen } from "./src/screens/PagoScreen";
import { SyncEngine } from "./src/sync/syncEngine";
import { COLORS, UI } from "./src/theme";

type TabId = "catalogo" | "cotizar" | "pagos" | "clientes" | "outbox";

const API_BASE_URL =
  process.env.EXPO_PUBLIC_CXC_API_URL ?? "https://cxclubrikca-stagingv1.up.railway.app";

export default function App(): React.JSX.Element {
  const repo = useMemo(() => new MobileSqliteRepo(new ExpoSqliteExecutor()), []);
  const sessionStore = useMemo(() => new MobileSessionStore(), []);
  const [session, setSession] = useState<SessionTokens | null>(null);
  const [listo, setListo] = useState(false);
  const [isOnline, setIsOnline] = useState(true);
  const [tab, setTab] = useState<TabId>("catalogo");
  const [conflictosCount, setConflictosCount] = useState(0);

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

  useEffect(() => {
    void (async () => {
      await repo.init();
      const s = await sessionStore.leerSesion();
      if (s && (await sessionStore.verificarBiometriaSiDisponible())) {
        setSession(s);
      }
      setListo(true);
    })();

    const unsubNet = NetInfo.addEventListener((state) => {
      setIsOnline(Boolean(state.isConnected && state.isInternetReachable !== false));
    });
    return () => unsubNet();
  }, [repo, sessionStore]);

  useEffect(() => {
    if (!session) return undefined;
    void repo.listarOutbox("CONFLICT_USER_ACTION").then((c) => setConflictosCount(c.length));
    return syncEngine.adjuntarListenerRed(session.device_id, (rep) => {
      if (rep.sesionRevocada) {
        setSession(null);
        return;
      }
      void repo.listarOutbox("CONFLICT_USER_ACTION").then((c) => setConflictosCount(c.length));
    });
  }, [session, repo, syncEngine]);

  if (!listo) {
    return (
      <SafeAreaView style={UI.screen}>
        <Text style={UI.title}>Inicializando base offline...</Text>
      </SafeAreaView>
    );
  }

  if (!session) {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: COLORS.bg }}>
        <StatusBar style="dark" />
        <LoginScreen
          api={api}
          sessionStore={sessionStore}
          deviceId="lubrikca-expo-device"
          onLoggedIn={(s) => {
            setSession(s);
            void syncEngine.sincronizarCompleto(s.device_id);
          }}
        />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: COLORS.bg }}>
      <StatusBar style="dark" />
      <View
        style={{
          backgroundColor: isOnline ? COLORS.successBg : COLORS.warnBg,
          paddingHorizontal: 14,
          paddingVertical: 6,
          flexDirection: "row",
          justifyContent: "space-between",
        }}
      >
        <Text style={{ fontSize: 12, fontWeight: "700", color: isOnline ? COLORS.success : COLORS.warn }}>
          {isOnline ? "ONLINE · Sync listo" : "MODO OFFLINE · Guardando en SQLite local"}
        </Text>
        {conflictosCount > 0 ? (
          <Text style={{ fontSize: 12, fontWeight: "700", color: COLORS.danger }}>
            {conflictosCount} conflicto(s)
          </Text>
        ) : null}
      </View>

      <View style={{ flex: 1 }}>
        {tab === "catalogo" ? <CatalogoScreen repo={repo} api={api} isOnline={isOnline} /> : null}
        {tab === "cotizar" ? <CotizacionScreen repo={repo} vendedorEmail={session.user_email} /> : null}
        {tab === "pagos" ? <PagoScreen repo={repo} vendedorEmail={session.user_email} /> : null}
        {tab === "clientes" ? <ClientesScreen repo={repo} vendedorEmail={session.user_email} /> : null}
        {tab === "outbox" ? (
          <OutboxScreen
            repo={repo}
            syncEngine={syncEngine}
            deviceId={session.device_id}
            isOnline={isOnline}
            onLogout={() => {
              void cerrarSesionYLimpiarDispositivo(api, sessionStore, repo).then(() => setSession(null));
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
          paddingVertical: 8,
        }}
      >
        {(
          [
            ["catalogo", "Catálogo"],
            ["cotizar", "Cotizar"],
            ["pagos", "Cobros"],
            ["clientes", "Clientes"],
            ["outbox", "Sync"],
          ] as Array<[TabId, string]>
        ).map(([id, label]) => (
          <TouchableOpacity
            key={id}
            onPress={() => setTab(id)}
            style={{ flex: 1, alignItems: "center", paddingVertical: 4 }}
          >
            <Text style={{ fontWeight: "700", fontSize: 12, color: tab === id ? COLORS.primary : COLORS.muted }}>
              {label}
            </Text>
          </TouchableOpacity>
        ))}
      </View>
    </SafeAreaView>
  );
}
