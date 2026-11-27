import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Image, ScrollView, Switch, Text, TextInput, TouchableOpacity, View } from "react-native";
import * as ImageManipulator from "expo-image-manipulator";
import * as ImagePicker from "expo-image-picker";
import {
  sugerirVinculacionFifo,
  validarCompatibilidadPagoConPedido,
  validarDestinoPagoOffline,
  type CuentaReceptora,
  type CuentaTercero,
  type RutaPago,
} from "@lubrikca/domain-ts";

import { SearchPickerModal, type PickerItem } from "../components/SearchPickerModal";
import type { ClienteBuscable, MobileSqliteRepo } from "../db/sqliteRepo";
import { COLORS, UI } from "../theme";

interface Props {
  repo: MobileSqliteRepo;
  vendedorEmail: string;
  syncVersion: number;
  clientePreseleccionadoId: string | null;
  onLimpiarPreseleccion: () => void;
  onSyncNow: () => Promise<void>;
}

const METODOS_BCV = ["Transferencia Bancaria", "Pago Móvil", "Efectivo VES"];
const METODOS_BINANCE = ["Binance Pay", "Zelle", "Efectivo USD"];

export function PagoScreen({
  repo,
  vendedorEmail,
  syncVersion,
  clientePreseleccionadoId,
  onLimpiarPreseleccion,
  onSyncNow,
}: Props): React.JSX.Element {
  const [clientes, setClientes] = useState<ClienteBuscable[]>([]);
  const [cuentasPropias, setCuentasPropias] = useState<CuentaReceptora[]>([]);
  const [cuentasTerceros, setCuentasTerceros] = useState<CuentaTercero[]>([]);
  const [modoLoteEfectivo, setModoLoteEfectivo] = useState(false);

  const [clienteId, setClienteId] = useState("");
  const [modalCliente, setModalCliente] = useState(false);
  const [ruta, setRuta] = useState<RutaPago>("bcv");
  const [monto, setMonto] = useState("");
  const [moneda, setMoneda] = useState("VES");
  const [metodoPago, setMetodoPago] = useState("Transferencia Bancaria");
  const [referencia, setReferencia] = useState("");
  const [bancoOrigen, setBancoOrigen] = useState("");
  const [bancoDestino, setBancoDestino] = useState("");
  const [cuentaDestino, setCuentaDestino] = useState("");
  const [esZelleTercero, setEsZelleTercero] = useState(false);
  const [cuentaTerceroId, setCuentaTerceroId] = useState<number | null>(null);
  const [comprobanteUri, setComprobanteUri] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);

  const cargar = useCallback(async (): Promise<void> => {
    const [clis, cr, ct] = await Promise.all([
      repo.listarClientesBuscables(),
      repo.obtenerCuentasReceptorasActivas(),
      repo.obtenerCuentasTercerosActivas(),
    ]);
    setClientes(clis);
    setCuentasPropias(cr);
    setCuentasTerceros(ct);
    if (ct[0]) {
      const tid = ct[0].cuenta_tercero_id ?? ct[0].id ?? null;
      if (tid) setCuentaTerceroId((prev) => prev ?? tid);
    }
  }, [repo]);

  useEffect(() => {
    void cargar();
  }, [cargar, syncVersion]);

  useEffect(() => {
    if (clientePreseleccionadoId) {
      setClienteId(clientePreseleccionadoId);
      onLimpiarPreseleccion();
    }
  }, [clientePreseleccionadoId, onLimpiarPreseleccion]);

  const clienteSel = clientes.find((c) => c.cliente_id === clienteId) ?? null;

  const cuentasRuta = useMemo(
    () =>
      cuentasPropias.filter((c) => {
        const mon = String(c.moneda || "").toUpperCase();
        const ch = String(c.tipo_canal || "").toLowerCase();
        if (ruta === "binance") {
          return mon === "USD" || ch === "binance" || ch === "zelle";
        }
        return mon === "VES";
      }),
    [cuentasPropias, ruta],
  );

  function seleccionarCuentaPropia(c: CuentaReceptora): void {
    setBancoDestino(c.banco_nombre || c.nombre);
    setCuentaDestino(c.numero_cuenta || c.email_zelle || c.binance_id || c.telefono || "");
    if (c.tipo_canal === "pago_movil") setMetodoPago("Pago Móvil");
    else if (c.tipo_canal === "binance") setMetodoPago("Binance Pay");
    else if (c.tipo_canal === "zelle") setMetodoPago("Zelle");
  }

  const valDestino = validarDestinoPagoOffline(
    {
      banco_origen: bancoOrigen,
      banco_destino: bancoDestino,
      cuenta_destino: cuentaDestino,
      metodo_pago: modoLoteEfectivo ? (moneda === "VES" ? "Efectivo VES" : "Efectivo USD") : metodoPago,
      es_zelle_tercero: esZelleTercero,
      cuenta_tercero_id: cuentaTerceroId,
    },
    cuentasPropias,
    cuentasTerceros,
  );

  const montoNum = Number(monto || 0);
  const fifo = sugerirVinculacionFifo(montoNum, ruta, clienteSel?.ordenes_pendientes ?? []);

  const clienteItems: PickerItem[] = useMemo(
    () =>
      clientes.map((c) => ({
        id: c.cliente_id,
        titulo: c.cliente_nombre,
        subtitulo: [c.rif || "Sin RIF", c.telefono || ""].filter(Boolean).join(" · "),
        detalleIzq: `BCV: $${c.saldo_bcv_usd.toFixed(2)}  |  Binance: $${c.saldo_binance_usd.toFixed(2)}`,
        badge: c.max_dias_mora > 0 ? `Mora ${c.max_dias_mora}d` : "Al día",
        badgeColor: c.max_dias_mora > 0 ? COLORS.danger : COLORS.success,
        badgeBg: c.max_dias_mora > 0 ? COLORS.dangerBg : COLORS.successBg,
        searchBlob: `${c.cliente_nombre} ${c.rif} ${c.telefono} ${c.cliente_id}`,
      })),
    [clientes],
  );

  async function comprimirYGuardar(uri: string): Promise<void> {
    const manip = await ImageManipulator.manipulateAsync(
      uri,
      [{ resize: { width: 1280 } }],
      { compress: 0.78, format: ImageManipulator.SaveFormat.JPEG },
    );
    setComprobanteUri(manip.uri);
  }

  async function elegirGaleria(): Promise<void> {
    const res = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ["images"], quality: 0.9 });
    if (!res.canceled && res.assets[0]) await comprimirYGuardar(res.assets[0].uri);
  }

  async function tomarFoto(): Promise<void> {
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (perm.status !== "granted") {
      setMsg({ text: "Se requiere permiso de cámara para fotografiar el comprobante.", ok: false });
      return;
    }
    const res = await ImagePicker.launchCameraAsync({ quality: 0.9 });
    if (!res.canceled && res.assets[0]) await comprimirYGuardar(res.assets[0].uri);
  }

  async function guardarPagoOffline(): Promise<void> {
    setMsg(null);
    if (!clienteSel || montoNum <= 0) {
      setMsg({ text: "Selecciona un cliente e indica un monto mayor a 0.", ok: false });
      return;
    }

    if (modoLoteEfectivo) {
      await repo.encolarOperacionOutbox({
        tipo: "lote_efectivo",
        payload: {
          vendedor_email: vendedorEmail,
          vendedor_nombre: vendedorEmail,
          items: [
            {
              cliente_id: clienteSel.cliente_id,
              cliente_nombre: clienteSel.cliente_nombre,
              metodo_pago: moneda === "VES" ? "Efectivo VES" : "Efectivo USD",
              moneda,
              monto: montoNum,
              referencia: referencia.trim(),
            },
          ],
          observaciones: `Lote móvil (${clienteSel.cliente_nombre})`,
        },
      });
      setMonto("");
      setReferencia("");
      void onSyncNow();
      setMsg({ text: "Recibo de efectivo encolado en lote offline.", ok: true });
      return;
    }

    const compat = validarCompatibilidadPagoConPedido(ruta, moneda, metodoPago);
    if (!compat.valido) {
      setMsg({ text: compat.error ?? "Incompatibilidad de ruta BCV/Binance.", ok: false });
      return;
    }
    if (valDestino.bloqueante) {
      setMsg({ text: `BLOQUEADO OFFLINE: ${valDestino.mensaje}`, ok: false });
      return;
    }

    await repo.encolarOperacionOutbox({
      tipo: "pago",
      payload: {
        tipo: "pago",
        partner_odoo_id: clienteSel.partner_odoo_id,
        contraparte_nombre: clienteSel.cliente_nombre,
        vendedor_email: vendedorEmail,
        ruta_pago: ruta,
        monto: montoNum,
        moneda,
        metodo_pago: metodoPago,
        referencia: referencia.trim(),
        banco_origen: bancoOrigen.trim(),
        banco_destino: bancoDestino.trim(),
        cuenta_destino: cuentaDestino.trim(),
        es_zelle_tercero: esZelleTercero,
        cuenta_tercero_id: esZelleTercero ? cuentaTerceroId : null,
        diario_odoo_id: valDestino.diario_odoo_id,
        vinculaciones_fifo: fifo.asignaciones,
      },
      adjuntoLocalUri: comprobanteUri,
    });
    setMonto("");
    setReferencia("");
    setComprobanteUri(null);
    void onSyncNow();
    setMsg({ text: "Pago validado y guardado para conciliación.", ok: true });
  }

  return (
    <ScrollView style={UI.screen} contentContainerStyle={UI.screenPad}>
      <Text style={UI.title}>Registro de Cobros</Text>
      <Text style={UI.subtitle}>Validación SUDEBAN offline contra cuentas propias de Lubrikca.</Text>

      <View style={UI.segmentWrap}>
        {[false, true].map((lote) => {
          const active = modoLoteEfectivo === lote;
          return (
            <TouchableOpacity
              key={lote ? "lote" : "elec"}
              onPress={() => setModoLoteEfectivo(lote)}
              style={[UI.segmentBtn, active ? UI.segmentBtnActive : null]}
            >
              <Text style={{ fontWeight: "700", fontSize: 13, color: active ? COLORS.navy : COLORS.muted }}>
                {lote ? "Recibo Efectivo (Lote)" : "Transferencia / Zelle / Binance"}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>

      <View style={UI.card}>
        <Text style={UI.sectionLabel}>1. Cliente que realiza el pago</Text>
        <TouchableOpacity style={UI.pickerBtn} onPress={() => setModalCliente(true)}>
          <View style={{ flex: 1, paddingRight: 8 }}>
            <Text style={{ fontWeight: "700", fontSize: 14, color: clienteSel ? COLORS.text : COLORS.muted }}>
              {clienteSel ? clienteSel.cliente_nombre : "🔍 Tocar para buscar cliente por nombre o RIF..."}
            </Text>
            {clienteSel ? (
              <Text style={{ fontSize: 12, color: COLORS.muted, marginTop: 2 }}>
                BCV: ${clienteSel.saldo_bcv_usd.toFixed(2)} · Binance: ${clienteSel.saldo_binance_usd.toFixed(2)}
              </Text>
            ) : null}
          </View>
          <Text style={{ color: COLORS.primary, fontWeight: "800", fontSize: 13 }}>Buscar</Text>
        </TouchableOpacity>

        <Text style={UI.sectionLabel}>2. Ruta y Monto</Text>
        <View style={UI.segmentWrap}>
          {(["bcv", "binance"] as const).map((r) => {
            const active = ruta === r;
            return (
              <TouchableOpacity
                key={r}
                onPress={() => {
                  setRuta(r);
                  setMoneda(r === "binance" ? "USD" : "VES");
                  setMetodoPago(r === "binance" ? "Binance Pay" : "Transferencia Bancaria");
                }}
                style={[UI.segmentBtn, active ? UI.segmentBtnActive : null]}
              >
                <Text
                  style={{
                    fontWeight: "800",
                    fontSize: 12,
                    color: active ? (r === "bcv" ? COLORS.bcv : COLORS.binance) : COLORS.muted,
                  }}
                >
                  {r === "bcv" ? "Ruta BCV (VES)" : "Ruta Binance (USD)"}
                </Text>
              </TouchableOpacity>
            );
          })}
        </View>

        {!modoLoteEfectivo ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 10 }}>
            {(ruta === "bcv" ? METODOS_BCV : METODOS_BINANCE).map((m) => {
              const sel = metodoPago === m;
              return (
                <TouchableOpacity
                  key={m}
                  onPress={() => {
                    setMetodoPago(m);
                    if (m === "Efectivo VES") setMoneda("VES");
                    else if (m === "Efectivo USD") setMoneda("USD");
                  }}
                  style={[UI.chip, sel ? UI.chipActive : null]}
                >
                  <Text style={{ color: sel ? "#FFF" : COLORS.text, fontSize: 12, fontWeight: "700" }}>{m}</Text>
                </TouchableOpacity>
              );
            })}
          </ScrollView>
        ) : null}

        <View style={UI.row}>
          <TextInput
            style={[UI.input, { flex: 1 }]}
            placeholder={`Monto (${moneda})`}
            placeholderTextColor={COLORS.muted}
            keyboardType="numeric"
            value={monto}
            onChangeText={setMonto}
          />
          <TextInput
            style={[UI.input, { flex: 1 }]}
            placeholder="Referencia / Nro."
            placeholderTextColor={COLORS.muted}
            value={referencia}
            onChangeText={setReferencia}
          />
        </View>
      </View>

      {!modoLoteEfectivo ? (
        <View style={UI.card}>
          <View style={[UI.row, { marginBottom: 8 }]}>
            <Text style={{ color: COLORS.text, fontWeight: "700", fontSize: 13 }}>
              ¿Zelle a cuenta de Tercero Autorizado?
            </Text>
            <Switch value={esZelleTercero} onValueChange={setEsZelleTercero} />
          </View>

          {esZelleTercero ? (
            <View>
              <Text style={UI.sectionLabel}>Selecciona Tercero Autorizado</Text>
              <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 8 }}>
                {cuentasTerceros.map((ct) => {
                  const tid = ct.cuenta_tercero_id ?? ct.id ?? 0;
                  const sel = cuentaTerceroId === tid;
                  return (
                    <TouchableOpacity
                      key={tid}
                      onPress={() => setCuentaTerceroId(tid)}
                      style={[UI.chip, sel ? UI.chipActive : null]}
                    >
                      <Text style={{ color: sel ? "#FFF" : COLORS.text, fontSize: 12, fontWeight: "700" }}>
                        {ct.alias || ct.titular}
                      </Text>
                    </TouchableOpacity>
                  );
                })}
              </ScrollView>
            </View>
          ) : (
            <>
              {cuentasRuta.length > 0 ? (
                <View style={{ marginBottom: 8 }}>
                  <Text style={UI.sectionLabel}>Tocar cuenta destino de Lubrikca</Text>
                  <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                    {cuentasRuta.map((c) => {
                      const sel =
                        bancoDestino.toLowerCase() === (c.banco_nombre || c.nombre).toLowerCase() &&
                        (!c.numero_cuenta || cuentaDestino.includes(c.numero_cuenta.slice(-4)));
                      return (
                        <TouchableOpacity
                          key={c.cuenta_id}
                          onPress={() => seleccionarCuentaPropia(c)}
                          style={[UI.chip, sel ? UI.chipActive : null]}
                        >
                          <Text style={{ color: sel ? "#FFF" : COLORS.text, fontSize: 12, fontWeight: "700" }}>
                            {c.nombre}
                          </Text>
                        </TouchableOpacity>
                      );
                    })}
                  </ScrollView>
                </View>
              ) : null}

              <TextInput
                style={UI.input}
                placeholder="Banco Origen del cliente (ej. Banesco)"
                placeholderTextColor={COLORS.muted}
                value={bancoOrigen}
                onChangeText={setBancoOrigen}
              />
              <TextInput
                style={UI.input}
                placeholder="Banco Destino Lubrikca (ej. Bancamiga)"
                placeholderTextColor={COLORS.muted}
                value={bancoDestino}
                onChangeText={setBancoDestino}
              />
              <TextInput
                style={UI.input}
                placeholder="Cuenta / Móvil / Email Destino"
                placeholderTextColor={COLORS.muted}
                value={cuentaDestino}
                onChangeText={setCuentaDestino}
              />
            </>
          )}

          <View
            style={{
              padding: 10,
              borderRadius: 10,
              backgroundColor: valDestino.bloqueante ? COLORS.dangerBg : COLORS.successBg,
              marginBottom: 10,
            }}
          >
            <Text
              style={{
                fontSize: 12,
                fontWeight: "700",
                color: valDestino.bloqueante ? COLORS.danger : COLORS.success,
              }}
            >
              SUDEBAN Offline ({valDestino.estado}): {valDestino.mensaje}
            </Text>
          </View>

          <Text style={UI.sectionLabel}>Comprobante / Captura</Text>
          <View style={UI.row}>
            <TouchableOpacity style={[UI.btnOutline, { flex: 1 }]} onPress={() => void tomarFoto()}>
              <Text style={UI.btnTextOutline}>📷 Cámara</Text>
            </TouchableOpacity>
            <TouchableOpacity style={[UI.btnOutline, { flex: 1 }]} onPress={() => void elegirGaleria()}>
              <Text style={UI.btnTextOutline}>🖼️ Galería</Text>
            </TouchableOpacity>
          </View>

          {comprobanteUri ? (
            <View style={{ marginTop: 8, alignItems: "center" }}>
              <Image
                source={{ uri: comprobanteUri }}
                style={{ width: "100%", height: 150, borderRadius: 10, backgroundColor: COLORS.surface }}
                resizeMode="cover"
              />
              <TouchableOpacity onPress={() => setComprobanteUri(null)} style={{ marginTop: 6 }}>
                <Text style={{ color: COLORS.danger, fontSize: 12, fontWeight: "700" }}>
                  Quitar comprobante
                </Text>
              </TouchableOpacity>
            </View>
          ) : null}
        </View>
      ) : null}

      {fifo.asignaciones.length > 0 ? (
        <View style={UI.card}>
          <Text style={{ fontWeight: "800", color: COLORS.text, fontSize: 13, marginBottom: 4 }}>
            Vinculación FIFO sugerida (Ruta {ruta.toUpperCase()})
          </Text>
          {fifo.asignaciones.map((a) => (
            <View key={a.so_id} style={[UI.row, { paddingVertical: 3 }]}>
              <Text style={{ fontSize: 12, color: COLORS.textSecondary }}>
                {a.so_id} ({a.fecha})
              </Text>
              <Text style={{ fontSize: 12, fontWeight: "700", color: COLORS.primary }}>
                ${a.monto_asignado_usd.toFixed(2)}
              </Text>
            </View>
          ))}
        </View>
      ) : null}

      {msg ? (
        <View
          style={{
            backgroundColor: msg.ok ? COLORS.successBg : COLORS.dangerBg,
            padding: 10,
            borderRadius: 10,
            marginBottom: 8,
          }}
        >
          <Text style={{ color: msg.ok ? COLORS.success : COLORS.danger, fontWeight: "700", fontSize: 13 }}>
            {msg.text}
          </Text>
        </View>
      ) : null}

      <TouchableOpacity style={UI.btnPrimary} onPress={() => void guardarPagoOffline()}>
        <Text style={UI.btnTextPrimary}>
          {modoLoteEfectivo ? "Guardar Recibo en Lote" : "Validar y Registrar Pago"}
        </Text>
      </TouchableOpacity>

      <SearchPickerModal
        visible={modalCliente}
        titulo="Seleccionar Cliente"
        placeholder="Buscar por nombre, RIF o teléfono..."
        items={clienteItems}
        onSelect={setClienteId}
        onClose={() => setModalCliente(false)}
      />
    </ScrollView>
  );
}
