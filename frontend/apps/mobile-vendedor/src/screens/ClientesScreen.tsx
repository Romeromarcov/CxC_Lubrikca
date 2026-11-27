import React, { useCallback, useEffect, useMemo, useState } from "react";
import { FlatList, RefreshControl, Text, TextInput, TouchableOpacity, View } from "react-native";
import * as Location from "expo-location";
import { detectarDuplicadosContactoOffline, type ContactoMaestroResumen } from "@lubrikca/domain-ts";

import { OfflineValidationError, type ClienteBuscable, type MobileSqliteRepo } from "../db/sqliteRepo";
import { COLORS, UI } from "../theme";

interface Props {
  repo: MobileSqliteRepo;
  vendedorEmail: string;
  syncing: boolean;
  syncVersion: number;
  onSyncNow: () => Promise<void>;
  onCrearPedidoPara: (clienteId: string) => void;
  onRegistrarPagoPara: (clienteId: string) => void;
}

export function ClientesScreen({
  repo,
  vendedorEmail,
  syncing,
  syncVersion,
  onSyncNow,
  onCrearPedidoPara,
  onRegistrarPagoPara,
}: Props): React.JSX.Element {
  const [query, setQuery] = useState("");
  const [filtro, setFiltro] = useState<"todos" | "con_saldo" | "mora">("todos");
  const [clientes, setClientes] = useState<ClienteBuscable[]>([]);
  const [contactos, setContactos] = useState<ContactoMaestroResumen[]>([]);
  const [mostrarAlta, setMostrarAlta] = useState(false);

  const [nombre, setNombre] = useState("");
  const [rif, setRif] = useState("");
  const [telefono, setTelefono] = useState("");
  const [direccion, setDireccion] = useState("");
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);

  const cargar = useCallback(async (): Promise<void> => {
    const [clis, cm] = await Promise.all([
      repo.listarClientesBuscables(query),
      repo.obtenerContactosMaestro(),
    ]);
    setClientes(clis);
    setContactos(cm);
  }, [repo, query]);

  useEffect(() => {
    void cargar();
  }, [cargar, syncVersion]);

  const filtrados = useMemo(() => {
    if (filtro === "con_saldo") return clientes.filter((c) => c.saldo_deudor_usd > 0.01);
    if (filtro === "mora") return clientes.filter((c) => c.max_dias_mora > 0);
    return clientes;
  }, [clientes, filtro]);

  const totales = useMemo(
    () =>
      clientes.reduce(
        (acc, c) => ({
          bcv: acc.bcv + c.saldo_bcv_usd,
          binance: acc.binance + c.saldo_binance_usd,
          mora: acc.mora + (c.max_dias_mora > 0 ? 1 : 0),
        }),
        { bcv: 0, binance: 0, mora: 0 },
      ),
    [clientes],
  );

  const dups =
    nombre.trim() || rif.trim()
      ? detectarDuplicadosContactoOffline({ nombre, rif, telefono }, contactos)
      : [];

  async function registrarClienteOffline(): Promise<void> {
    setMsg(null);
    if (!nombre.trim()) {
      setMsg({ text: "Ingresa la razón social del cliente.", ok: false });
      return;
    }
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
      void onSyncNow();
      setMsg({ text: "Cliente guardado en cola offline con verificación anti-duplicados.", ok: true });
    } catch (e) {
      setMsg({
        text: e instanceof OfflineValidationError ? e.message : "Error registrando cliente.",
        ok: false,
      });
    }
  }

  return (
    <View style={[UI.screen, { paddingHorizontal: 14, paddingTop: 12 }]}>
      <View style={[UI.row, { marginBottom: 8 }]}>
        <View style={{ flex: 1 }}>
          <Text style={UI.title}>Clientes y Cartera</Text>
          <Text style={{ fontSize: 12, color: COLORS.muted }}>
            {clientes.length} clientes · {totales.mora} en mora
          </Text>
        </View>
        <TouchableOpacity
          style={[UI.btnPrimary, { marginVertical: 0, paddingVertical: 8, paddingHorizontal: 14 }]}
          onPress={() => setMostrarAlta((v) => !v)}
        >
          <Text style={UI.btnTextPrimary}>{mostrarAlta ? "Cerrar ✕" : "+ Nuevo"}</Text>
        </TouchableOpacity>
      </View>

      <View style={[UI.row, { marginBottom: 10 }]}>
        <View style={[UI.card, { flex: 1, marginBottom: 0, padding: 10, backgroundColor: COLORS.bcvBg }]}>
          <Text style={{ fontSize: 10, fontWeight: "700", color: COLORS.bcv }}>SALDO RUTA BCV</Text>
          <Text style={{ fontSize: 16, fontWeight: "800", color: COLORS.bcv }}>${totales.bcv.toFixed(2)}</Text>
        </View>
        <View style={[UI.card, { flex: 1, marginBottom: 0, padding: 10, backgroundColor: COLORS.binanceBg }]}>
          <Text style={{ fontSize: 10, fontWeight: "700", color: COLORS.binance }}>SALDO RUTA BINANCE</Text>
          <Text style={{ fontSize: 16, fontWeight: "800", color: COLORS.binance }}>
            ${totales.binance.toFixed(2)}
          </Text>
        </View>
      </View>

      {mostrarAlta ? (
        <View style={UI.card}>
          <Text style={UI.sectionLabel}>Nuevo cliente (con GPS y anti-duplicado)</Text>
          <TextInput
            style={UI.input}
            placeholder="Razón social"
            placeholderTextColor={COLORS.muted}
            value={nombre}
            onChangeText={setNombre}
          />
          <TextInput
            style={UI.input}
            placeholder="RIF (ej. J-40123456-7)"
            placeholderTextColor={COLORS.muted}
            value={rif}
            onChangeText={setRif}
          />
          <TextInput
            style={UI.input}
            placeholder="Teléfono"
            placeholderTextColor={COLORS.muted}
            value={telefono}
            onChangeText={setTelefono}
          />
          <TextInput
            style={UI.input}
            placeholder="Dirección fiscal / entrega"
            placeholderTextColor={COLORS.muted}
            value={direccion}
            onChangeText={setDireccion}
          />
          {dups.length > 0 ? (
            <View style={{ backgroundColor: COLORS.warnBg, padding: 8, borderRadius: 8, marginBottom: 8 }}>
              <Text style={{ color: COLORS.warn, fontWeight: "700", fontSize: 12 }}>
                Posible duplicado ({dups[0]?.motivo}): {dups[0]?.contacto.nombre} ({dups[0]?.contacto.rif})
              </Text>
            </View>
          ) : null}
          <TouchableOpacity style={UI.btnPrimary} onPress={() => void registrarClienteOffline()}>
            <Text style={UI.btnTextPrimary}>Guardar Cliente</Text>
          </TouchableOpacity>
        </View>
      ) : null}

      {msg ? (
        <View
          style={{
            backgroundColor: msg.ok ? COLORS.successBg : COLORS.dangerBg,
            padding: 9,
            borderRadius: 10,
            marginBottom: 8,
          }}
        >
          <Text style={{ color: msg.ok ? COLORS.success : COLORS.danger, fontSize: 12, fontWeight: "700" }}>
            {msg.text}
          </Text>
        </View>
      ) : null}

      <View style={UI.searchBox}>
        <TextInput
          style={UI.searchInput}
          placeholder="Buscar cliente por nombre, RIF o teléfono..."
          placeholderTextColor={COLORS.muted}
          value={query}
          onChangeText={setQuery}
        />
        {query ? (
          <TouchableOpacity onPress={() => setQuery("")} style={{ padding: 4 }}>
            <Text style={{ color: COLORS.muted, fontWeight: "800" }}>✕</Text>
          </TouchableOpacity>
        ) : null}
      </View>

      <View style={{ flexDirection: "row", marginBottom: 10 }}>
        {(
          [
            ["todos", "Todos"],
            ["con_saldo", "Con saldo"],
            ["mora", "En mora"],
          ] as const
        ).map(([id, label]) => {
          const sel = filtro === id;
          return (
            <TouchableOpacity
              key={id}
              onPress={() => setFiltro(id)}
              style={[UI.chip, sel ? UI.chipActive : null]}
            >
              <Text style={{ fontSize: 12, fontWeight: "700", color: sel ? "#FFF" : COLORS.text }}>{label}</Text>
            </TouchableOpacity>
          );
        })}
      </View>

      <FlatList
        data={filtrados}
        keyExtractor={(c) => c.cliente_id}
        contentContainerStyle={{ paddingBottom: 24 }}
        refreshControl={<RefreshControl refreshing={syncing} onRefresh={() => void onSyncNow()} />}
        ListEmptyComponent={
          <View style={[UI.card, { alignItems: "center", paddingVertical: 24 }]}>
            <Text style={{ color: COLORS.muted, fontSize: 13 }}>
              No hay clientes con este filtro. Tira hacia abajo para sincronizar.
            </Text>
          </View>
        }
        renderItem={({ item: c }) => (
          <View style={UI.card}>
            <View style={UI.row}>
              <Text style={{ fontWeight: "800", fontSize: 14, color: COLORS.text, flex: 1 }}>
                {c.cliente_nombre}
              </Text>
              <Text
                style={[
                  UI.badge,
                  {
                    color: c.max_dias_mora > 0 ? COLORS.danger : COLORS.success,
                    backgroundColor: c.max_dias_mora > 0 ? COLORS.dangerBg : COLORS.successBg,
                  },
                ]}
              >
                {c.max_dias_mora > 0 ? `Mora ${c.max_dias_mora}d` : "Al día"}
              </Text>
            </View>

            {c.rif || c.telefono ? (
              <Text style={{ fontSize: 12, color: COLORS.muted, marginTop: 2 }}>
                {[c.rif, c.telefono].filter(Boolean).join(" · ")}
              </Text>
            ) : null}

            <View style={[UI.row, { marginTop: 8 }]}>
              <Text style={{ color: COLORS.bcv, fontWeight: "700", fontSize: 12 }}>
                BCV: ${c.saldo_bcv_usd.toFixed(2)}
              </Text>
              <Text style={{ color: COLORS.binance, fontWeight: "700", fontSize: 12 }}>
                Binance: ${c.saldo_binance_usd.toFixed(2)}
              </Text>
            </View>

            <View style={[UI.row, { marginTop: 10 }]}>
              <TouchableOpacity
                style={[UI.btnOutline, { flex: 1, marginVertical: 0, paddingVertical: 8 }]}
                onPress={() => onCrearPedidoPara(c.cliente_id)}
              >
                <Text style={UI.btnTextOutline}>+ Pedido</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[UI.btnPrimary, { flex: 1, marginVertical: 0, paddingVertical: 8 }]}
                onPress={() => onRegistrarPagoPara(c.cliente_id)}
              >
                <Text style={[UI.btnTextPrimary, { fontSize: 13 }]}>+ Cobro</Text>
              </TouchableOpacity>
            </View>
          </View>
        )}
      />
    </View>
  );
}
