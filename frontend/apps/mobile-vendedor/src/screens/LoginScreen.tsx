import React, { useState } from "react";
import { Platform, Switch, Text, TextInput, TouchableOpacity, View } from "react-native";
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
  const [usarBiometria, setUsarBiometria] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);

  async function handleLogin(): Promise<void> {
    setError(null);
    setCargando(true);
    try {
      const resp = await api.loginVendedor({
        email: email.trim().toLowerCase(),
        password,
        device_id: deviceId,
        plataforma: Platform.OS === "ios" ? "ios" : "android",
        app_version: "0.1.0",
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
    <View style={UI.screen}>
      <Text style={UI.title}>Lubrikca · App Vendedores</Text>
      <Text style={UI.subtitle}>Acceso offline-first conectado a CxC (/api/v1)</Text>
      <View style={UI.card}>
        <TextInput
          style={UI.input}
          placeholder="Correo vendedor"
          autoCapitalize="none"
          keyboardType="email-address"
          value={email}
          onChangeText={setEmail}
        />
        <TextInput
          style={UI.input}
          placeholder="Contraseña"
          secureTextEntry
          value={password}
          onChangeText={setPassword}
        />
        <View style={[UI.row, { marginVertical: 6 }]}>
          <Text style={{ color: COLORS.text }}>Bloquear con biometría / FaceID</Text>
          <Switch value={usarBiometria} onValueChange={setUsarBiometria} />
        </View>
        {error ? <Text style={{ color: COLORS.danger, marginBottom: 8 }}>{error}</Text> : null}
        <TouchableOpacity style={UI.btnPrimary} onPress={() => void handleLogin()} disabled={cargando}>
          <Text style={UI.btnTextPrimary}>{cargando ? "Ingresando..." : "Iniciar Sesión"}</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}
