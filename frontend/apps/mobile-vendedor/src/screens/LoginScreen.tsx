import React, { useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  Switch,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import type { LubrikcaApiClient } from "@lubrikca/domain-ts";

import type { MobileSessionStore, SessionTokens } from "../auth/sessionStore";
import { COLORS, UI } from "../theme";

interface Props {
  api: LubrikcaApiClient;
  sessionStore: MobileSessionStore;
  deviceId: string;
  onLoggedIn: (session: SessionTokens) => void;
}

export function LoginScreen({ api, sessionStore, deviceId, onLoggedIn }: Props): React.JSX.Element {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mostrarClave, setMostrarClave] = useState(false);
  const [usarBiometria, setUsarBiometria] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);

  async function handleLogin(): Promise<void> {
    if (!email.trim() || !password) {
      setError("Ingresa tu correo y contraseña.");
      return;
    }
    setError(null);
    setCargando(true);
    try {
      const resp = await api.loginVendedor({
        email: email.trim().toLowerCase(),
        password,
        device_id: deviceId,
        plataforma: Platform.OS === "ios" ? "ios" : "android",
        app_version: "0.2.0",
      });
      await sessionStore.configurarBiometria(usarBiometria);
      const saved = await sessionStore.guardarSesion(resp);
      onLoggedIn(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error de autenticación.");
    } finally {
      setCargando(false);
    }
  }

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: COLORS.navy }}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <ScrollView
        contentContainerStyle={{
          flexGrow: 1,
          justifyContent: "center",
          padding: 20,
        }}
        keyboardShouldPersistTaps="handled"
      >
        <View style={{ alignItems: "center", marginBottom: 24 }}>
          <View
            style={{
              width: 58,
              height: 58,
              borderRadius: 16,
              backgroundColor: COLORS.accent,
              alignItems: "center",
              justifyContent: "center",
              marginBottom: 12,
            }}
          >
            <Text style={{ color: "#FFF", fontSize: 28, fontWeight: "900" }}>L</Text>
          </View>
          <Text style={{ color: "#FFF", fontSize: 24, fontWeight: "800" }}>Lubrikca Vendedores</Text>
          <Text style={{ color: "#CBD5E1", fontSize: 13, marginTop: 4, textAlign: "center" }}>
            Gestión comercial, stock, pedidos y cobros offline-first
          </Text>
        </View>

        <View style={[UI.card, { padding: 18, borderRadius: 18 }]}>
          <Text style={UI.sectionLabel}>Correo corporativo</Text>
          <TextInput
            style={UI.input}
            placeholder="vendedor@lubrikca.com"
            placeholderTextColor={COLORS.muted}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="email-address"
            value={email}
            onChangeText={setEmail}
          />

          <Text style={UI.sectionLabel}>Contraseña</Text>
          <View style={UI.searchBox}>
            <TextInput
              style={UI.searchInput}
              placeholder="••••••••"
              placeholderTextColor={COLORS.muted}
              secureTextEntry={!mostrarClave}
              value={password}
              onChangeText={setPassword}
            />
            <TouchableOpacity onPress={() => setMostrarClave((v) => !v)} style={{ paddingHorizontal: 6 }}>
              <Text style={{ color: COLORS.primary, fontSize: 12, fontWeight: "700" }}>
                {mostrarClave ? "Ocultar" : "Ver"}
              </Text>
            </TouchableOpacity>
          </View>

          <View style={[UI.row, { marginVertical: 6 }]}>
            <Text style={{ color: COLORS.textSecondary, fontSize: 13, flex: 1 }}>
              Proteger con huella / FaceID
            </Text>
            <Switch value={usarBiometria} onValueChange={setUsarBiometria} />
          </View>

          {error ? (
            <View style={{ backgroundColor: COLORS.dangerBg, padding: 10, borderRadius: 10, marginVertical: 6 }}>
              <Text style={{ color: COLORS.danger, fontSize: 13, fontWeight: "600" }}>{error}</Text>
            </View>
          ) : null}

          <TouchableOpacity
            style={[UI.btnPrimary, { marginTop: 8, opacity: cargando ? 0.75 : 1 }]}
            onPress={() => void handleLogin()}
            disabled={cargando}
          >
            {cargando ? (
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <ActivityIndicator color="#FFF" size="small" />
                <Text style={UI.btnTextPrimary}>Validando acceso...</Text>
              </View>
            ) : (
              <Text style={UI.btnTextPrimary}>Iniciar Sesión</Text>
            )}
          </TouchableOpacity>
        </View>

        <Text style={{ color: "#94A3B8", fontSize: 11, textAlign: "center", marginTop: 12 }}>
          v0.2.0 · Servidor: {api.baseUrl}
        </Text>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
