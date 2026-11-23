import React, { useEffect, useState } from "react";
import { ScrollView, Switch, Text, TextInput, TouchableOpacity, View } from "react-native";
import * as ImageManipulator from "expo-image-manipulator";
import * as ImagePicker from "expo-image-picker";
import {
  sugerirVinculacionFifo,
  validarCompatibilidadPagoConPedido,
  validarDestinoPagoOffline,
  type ClienteCartera,
  type CuentaReceptora,
  type CuentaTercero,
  type RutaPago,
} from "@lubrikca/domain-ts";

import type { MobileSqliteRepo } from "../db/sqliteRepo";
import { COLORS, UI } from "../theme";

interface Props {
  repo: MobileSqliteRepo;
  vendedorEmail: string;
}

export function PagoScreen({ repo, vendedorEmail }: Props): React.JSX.Element {
  const [clientes, setClientes] = useState<ClienteCartera[]>([]);
  const [cuentasPropias, setCuentasPropias] = useState<CuentaReceptora[]>([]);
  const [cuentasTerceros, setCuentasTerceros] = useState<CuentaTercero[]>([]);
  const [modoLoteEfectivo, setModoLoteEfectivo] = useState(false);

  const [clienteId, setClienteId] = useState("");
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
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const [clis, cr, ct] = await Promise.all([
        repo.listarClientesCarteraOffline(),
        repo.obtenerCuentasReceptorasActivas(),
        repo.obtenerCuentasTercerosActivas(),
      ]);
      setClientes(clis);
      setCuentasPropias(cr);
      setCuentasTerceros(ct);
      if (clis[0]) setClienteId(clis[0].cliente_id);
      if (ct[0]?.id) setCuentaTerceroId(ct[0].id);
    })();
  }, []);

  const clienteSel = clientes.find((c) => c.cliente_id === clienteId);
  const valDestino = validarDestinoPagoOffline(
    {
      banco_origen: bancoOrigen,
      banco_destino: bancoDestino,
      cuenta_destino: cuentaDestino,
      metodo_pago: modoLoteEfectivo ? "Efectivo USD" : metodoPago,
      es_zelle_tercero: esZelleTercero,
      cuenta_tercero_id: cuentaTerceroId,
    },
    cuentasPropias,
    cuentasTerceros,
  );

  const montoNum = Number(monto || 0);
  const fifo = sugerirVinculacionFifo(
    montoNum,
    ruta,
    clienteSel?.ordenes_pendientes ?? [],
  );

  async function adjuntarComprobante(): Promise<void> {
    const res = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      quality: 0.9,
    });
    if (res.canceled || !res.assets[0]) return;
    const manip = await ImageManipulator.manipulateAsync(
      res.assets[0].uri,
      [{ resize: { width: 1280 } }],
      { compress: 0.78, format: ImageManipulator.SaveFormat.JPEG },
    );
    setComprobanteUri(manip.uri);
  }

  async function guardarPagoOffline(): Promise<void> {
    setMsg(null);
    if (!clienteSel || montoNum <= 0) {
      setMsg("Selecciona un cliente y un monto válido.");
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
      setMsg("Lote de recibo en efectivo encolado offline.");
      return;
    }

    const compat = validarCompatibilidadPagoConPedido(ruta, moneda, metodoPago);
    if (!compat.valido) {
      setMsg(compat.error ?? "Incompatibilidad de ruta BCV/Binance.");
      return;
    }
    if (valDestino.bloqueante) {
      setMsg(`BLOQUEADO OFFLINE: ${valDestino.mensaje}`);
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
    setReferencia("");
    setComprobanteUri(null);
    setMsg("Pago validado contra catálogo SUDEBAN de Lubrikca y encolado offline.");
  }

  return (
    <ScrollView style={UI.screen}>
      <Text style={UI.title}>Registro de Pagos y Efectivo</Text>

      <View style={[UI.card, UI.row]}>
        <Text style={{ fontWeight: "700", color: COLORS.text }}>Modo Lote Recibos Efectivo</Text>
        <Switch value={modoLoteEfectivo} onValueChange={setModoLoteEfectivo} />
      </View>

      <View style={UI.card}>
        <Text style={{ fontSize: 12, color: COLORS.muted, marginBottom: 6 }}>
          Cliente: {clienteSel?.cliente_nombre ?? "Sin cartera cargada"}
        </Text>
        <View style={[UI.row, { marginBottom: 8 }]}>
          {(["bcv", "binance"] as const).map((r) => (
            <TouchableOpacity
              key={r}
              onPress={() => {
                setRuta(r);
                setMoneda(r === "binance" ? "USD" : "VES");
              }}
              style={[UI.btnOutline, { flex: 1, backgroundColor: ruta === r ? COLORS.primary : "#FFF" }]}
            >
              <Text style={{ color: ruta === r ? "#FFF" : COLORS.primary, fontWeight: "700" }}>
                Ruta {r.toUpperCase()}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        <TextInput
          style={UI.input}
          placeholder={`Monto (${moneda})`}
          keyboardType="numeric"
          value={monto}
          onChangeText={setMonto}
        />
        <TextInput
          style={UI.input}
          placeholder="Referencia / Comprobante"
          value={referencia}
          onChangeText={setReferencia}
        />

        {!modoLoteEfectivo ? (
          <>
            <View style={[UI.row, { marginBottom: 6 }]}>
              <Text style={{ color: COLORS.text }}>Zelle de Tercero Autorizado</Text>
              <Switch value={esZelleTercero} onValueChange={setEsZelleTercero} />
            </View>
            {!esZelleTercero ? (
              <>
                <TextInput
                  style={UI.input}
                  placeholder="Banco Origen del cliente (ej. Banco Provincial)"
                  value={bancoOrigen}
                  onChangeText={setBancoOrigen}
                />
                <TextInput
                  style={UI.input}
                  placeholder="Banco Destino Lubrikca (ej. Bancamiga)"
                  value={bancoDestino}
                  onChangeText={setBancoDestino}
                />
                <TextInput
                  style={UI.input}
                  placeholder="Cuenta Destino (20 dígitos o últimos 4)"
                  value={cuentaDestino}
                  onChangeText={setCuentaDestino}
                />
                <View
                  style={{
                    padding: 8,
                    borderRadius: 8,
                    backgroundColor: valDestino.bloqueante ? COLORS.dangerBg : COLORS.successBg,
                    marginBottom: 8,
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
              </>
            ) : null}

            <TouchableOpacity style={UI.btnOutline} onPress={() => void adjuntarComprobante()}>
              <Text style={UI.btnTextOutline}>
                {comprobanteUri ? "Comprobante comprimido listo (JPEG <=1280px)" : "Adjuntar foto de comprobante"}
              </Text>
            </TouchableOpacity>
          </>
        ) : null}
      </View>

      {fifo.asignaciones.length > 0 ? (
        <View style={UI.card}>
          <Text style={{ fontWeight: "700", color: COLORS.text, marginBottom: 4 }}>
            Sugerencia FIFO (Ruta {ruta.toUpperCase()}):
          </Text>
          {fifo.asignaciones.map((a) => (
            <Text key={a.so_id} style={{ fontSize: 12, color: COLORS.muted }}>
              • {a.so_id} ({a.fecha}): ${a.monto_asignado_usd.toFixed(2)}
            </Text>
          ))}
        </View>
      ) : null}

      {msg ? <Text style={{ color: valDestino.bloqueante ? COLORS.danger : COLORS.primary, marginBottom: 8 }}>{msg}</Text> : null}

      <TouchableOpacity style={UI.btnPrimary} onPress={() => void guardarPagoOffline()}>
        <Text style={UI.btnTextPrimary}>
          {modoLoteEfectivo ? "Encolar Lote de Efectivo" : "Validar y Encolar Pago Offline"}
        </Text>
      </TouchableOpacity>
    </ScrollView>
  );
}
