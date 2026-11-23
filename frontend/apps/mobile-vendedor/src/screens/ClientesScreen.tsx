import React, { useEffect, useState } from "react";
import { ScrollView, Text, TextInput, TouchableOpacity, View } from "react-native";
import * as Location from "expo-location";
import {
  detectarDuplicadosContactoOffline,
  type ClienteCartera,
  type ContactoMaestroResumen,
} from "@lubrikca/domain-ts";

import { OfflineValidationError, type MobileSqliteRepo } from "../db/sqliteRepo";
import { COLORS, UI } from "../theme";

interface Props {
  repo: MobileSqliteRepo;
  vendedorEmail: string;
}

export function ClientesScreen({ repo, vendedorEmail }: Props): React.JSX.Element {
  const [query, setQuery] = useState("");
  const [clientes, setClientes] = useState<ClienteCartera[]>([]);
  const [contactos, setContactos] = useState<ContactoMaestroResumen[]>([]);
  const [mostrarAlta, setMostrarAlta] = useState(false);

  const [nombre, setNombre] = useState("");
  const [rif, setRif] = useState("");
  const [telefono, setTelefono] = useState("");
  const [direccion, setDireccion] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  async function cargar(): Promise<void> {
    const [clis, cm] = await Promise.all([
      repo.listarClientesCarteraOffline(query),
      repo.obtenerContactosMaestro(),
    ]);
    setClientes(clis);
    setContactos(cm);
  }

  useEffect(() => {
    void cargar();
  }, [query]);

  const dups = nombre.trim() || rif.trim()
    ? detectarDuplicadosContactoOffline({ nombre, rif, telefono }, contactos)
    : [];

  async function registrarClienteOffline(): Promise<void> {
    setMsg(null);
    let gps: { latitud?: number; longitud?: number } = {};
    try {
      const perm = await Location.requestForegroundPermissionsAsync();
      if (perm.status === "granted") {
        const pos = await Location.getCurrentPositionAsync({});
        gps = { latitud: pos.coords.latitude, longitud: pos.coords.longitude };
      }
    } catch {
      gps = {};
    }

    try {
      await repo.encolarOperacionOutbox({
        tipo: "contacto",
        payload: {
          rol: "cliente",
          nombre: nombre.trim(),
          rif: rif.trim(),
          telefono: telefono.trim(),
          direccion: direccion.trim(),
          vendedor_email: vendedorEmail,
          ...gps,
        },
      });
      setNombre("");
      setRif("");
      setTelefono("");
      setDireccion("");
      setMostrarAlta(false);
      setMsg("Cliente nuevo guardado en cola offline.");
    } catch (e) {
      setMsg(e instanceof OfflineValidationError ? e.message : "Error registrando cliente.");
    }
  }

  return (
    <ScrollView style={UI.screen}>
      <View style={UI.row}>
        <Text style={UI.title}>Cartera y Maestro de Clientes</Text>
        <TouchableOpacity style={UI.btnOutline} onPress={() => setMostrarAlta((v) => !v)}>
          <Text style={UI.btnTextOutline}>{mostrarAlta ? "Cerrar" : "+ Nuevo"}</Text>
        </TouchableOpacity>
      </View>

      {mostrarAlta ? (
        <View style={UI.card}>
          <TextInput style={UI.input} placeholder="Razón social" value={nombre} onChangeText={setNombre} />
          <TextInput style={UI.input} placeholder="RIF (ej. J-40123456-7)" value={rif} onChangeText={setRif} />
          <TextInput style={UI.input} placeholder="Teléfono" value={telefono} onChangeText={setTelefono} />
          <TextInput style={UI.input} placeholder="Dirección fiscal / entrega" value={direccion} onChangeText={setDireccion} />
          {dups.length > 0 ? (
            <View style={{ backgroundColor: COLORS.warnBg, padding: 8, borderRadius: 8, marginBottom: 8 }}>
              <Text style={{ color: COLORS.warn, fontWeight: "700", fontSize: 12 }}>
                Posible duplicado ({dups[0]?.motivo}): {dups[0]?.contacto.nombre} ({dups[0]?.contacto.rif})
              </Text>
            </View>
          ) : null}
          <TouchableOpacity style={UI.btnPrimary} onPress={() => void registrarClienteOffline()}>
            <Text style={UI.btnTextPrimary}>Guardar Cliente Offline (con GPS)</Text>
          </TouchableOpacity>
        </View>
      ) : null}

      {msg ? <Text style={{ color: COLORS.primary, marginBottom: 8 }}>{msg}</Text> : null}

      <TextInput style={UI.input} placeholder="Filtrar cliente..." value={query} onChangeText={setQuery} />

      {clientes.map((c) => (
        <View key={c.cliente_id} style={UI.card}>
          <Text style={{ fontWeight: "700", color: COLORS.text }}>{c.cliente_nombre}</Text>
          <View style={[UI.row, { marginTop: 6 }]}>
            <Text style={{ color: COLORS.bcv, fontWeight: "700", fontSize: 12 }}>
              Ruta BCV: ${c.saldo_bcv_usd.toFixed(2)}
            </Text>
            <Text style={{ color: COLORS.binance, fontWeight: "700", fontSize: 12 }}>
              Ruta Binance: ${c.saldo_binance_usd.toFixed(2)}
            </Text>
            <Text style={{ color: c.max_dias_mora > 0 ? COLORS.danger : COLORS.success, fontSize: 12 }}>
              Mora: {c.max_dias_mora}d
            </Text>
          </View>
        </View>
      ))}
    </ScrollView>
  );
}
