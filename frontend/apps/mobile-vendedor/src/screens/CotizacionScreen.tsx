import React, { useEffect, useState } from "react";
import { ScrollView, Text, TextInput, TouchableOpacity, View } from "react-native";
import * as Location from "expo-location";
import {
  evaluarPoliticaComercialOffline,
  rutaDeLista,
  validarNoMezclaRutasDocumento,
  type ClienteCartera,
  type ListaPrecio,
  type PoliticaVentasGlobal,
  type ProductoCatalogo,
} from "@lubrikca/domain-ts";

import type { MobileSqliteRepo } from "../db/sqliteRepo";
import { COLORS, UI } from "../theme";

interface Props {
  repo: MobileSqliteRepo;
  vendedorEmail: string;
}

export function CotizacionScreen({ repo, vendedorEmail }: Props): React.JSX.Element {
  const [clientes, setClientes] = useState<ClienteCartera[]>([]);
  const [listas, setListas] = useState<ListaPrecio[]>([]);
  const [productos, setProductos] = useState<Array<ProductoCatalogo & { precio_lista: number }>>([]);
  const [politicaGlobal, setPoliticaGlobal] = useState<PoliticaVentasGlobal | null>(null);

  const [clienteId, setClienteId] = useState("");
  const [tipoDoc, setTipoDoc] = useState<"cotizacion" | "pedido">("pedido");
  const [listaId, setListaId] = useState("");
  const [diasCredito, setDiasCredito] = useState("0");
  const [descEspecial, setDescEspecial] = useState("");
  const [prodIdSel, setProdIdSel] = useState("");
  const [cantidad, setCantidad] = useState("1");
  const [lineas, setLineas] = useState<Array<{ producto_id: string; nombre: string; cantidad: number; precio_unitario: number; lista_id: string }>>([]);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const [clis, ls, pol] = await Promise.all([
        repo.listarClientesCarteraOffline(),
        repo.obtenerListasPrecio(),
        repo.obtenerPoliticaVentas(),
      ]);
      setClientes(clis);
      setListas(ls);
      setPoliticaGlobal(pol);
      const defLista = ls[0]?.lista_id ?? "";
      setListaId(defLista);
      if (clis[0]) setClienteId(clis[0].cliente_id);
      const prods = await repo.buscarProductosOffline("", defLista);
      setProductos(prods);
      if (prods[0]) setProdIdSel(prods[0].producto_id);
    })();
  }, []);

  const rutaActual = rutaDeLista(listaId, listas);
  const clienteObj = clientes.find((c) => c.cliente_id === clienteId) ?? null;
  const totalUsd = lineas.reduce((acc, l) => acc + l.cantidad * l.precio_unitario, 0);

  const evalPol = evaluarPoliticaComercialOffline({
    tipoDocumento: tipoDoc,
    montoPedidoUsd: totalUsd,
    diasCredito: Number(diasCredito || 0),
    tieneDescuentoEspecial: Boolean(descEspecial.trim()),
    cliente: clienteObj,
    politicaGlobal,
  });

  function agregarLinea(): void {
    const p = productos.find((x) => x.producto_id === prodIdSel);
    const cant = Number(cantidad || 0);
    if (!p || cant <= 0) return;
    setLineas((prev) => [
      ...prev,
      {
        producto_id: p.producto_id,
        nombre: p.nombre,
        cantidad: cant,
        precio_unitario: p.precio_lista,
        lista_id: listaId,
      },
    ]);
  }

  async function guardarOffline(): Promise<void> {
    setMsg(null);
    if (!clienteObj || lineas.length === 0) {
      setMsg("Selecciona un cliente y al menos un producto.");
      return;
    }
    const checkRutas = validarNoMezclaRutasDocumento(rutaActual, lineas, listas);
    if (!checkRutas.valido) {
      setMsg(checkRutas.error ?? "No puedes mezclar rutas BCV y Binance.");
      return;
    }

    let coords: { latitud?: number; longitud?: number } = {};
    try {
      const perm = await Location.requestForegroundPermissionsAsync();
      if (perm.status === "granted") {
        const loc = await Location.getCurrentPositionAsync({});
        coords = { latitud: loc.coords.latitude, longitud: loc.coords.longitude };
      }
    } catch {
      coords = {};
    }

    await repo.encolarOperacionOutbox({
      tipo: tipoDoc,
      payload: {
        tipo: tipoDoc,
        partner_odoo_id: clienteObj.partner_odoo_id,
        cliente: clienteObj.cliente_nombre,
        vendedor_email: vendedorEmail,
        lista_precios_id: listaId,
        ruta_pago: rutaActual,
        dias_credito: Number(diasCredito || 0),
        descuentos_especiales: descEspecial.trim() ? [{ motivo: descEspecial.trim(), porcentaje: 0 }] : [],
        lineas: lineas.map((l) => ({
          producto_id: l.producto_id,
          cantidad: l.cantidad,
          precio_unitario: l.precio_unitario,
        })),
        ...coords,
      },
    });
    setLineas([]);
    setMsg(`${tipoDoc.toUpperCase()} guardado en cola offline (estado estimado: ${evalPol.estadoSugerido}).`);
  }

  return (
    <ScrollView style={UI.screen}>
      <Text style={UI.title}>Nueva Cotización / Pedido (Offline)</Text>
      <View style={UI.card}>
        <View style={[UI.row, { marginBottom: 8 }]}>
          {(["pedido", "cotizacion"] as const).map((t) => (
            <TouchableOpacity
              key={t}
              onPress={() => setTipoDoc(t)}
              style={[UI.btnOutline, { flex: 1, backgroundColor: tipoDoc === t ? COLORS.primary : "#FFF" }]}
            >
              <Text style={{ color: tipoDoc === t ? "#FFF" : COLORS.primary, fontWeight: "700" }}>
                {t.toUpperCase()}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
        <Text style={{ fontSize: 12, color: COLORS.muted }}>
          Cliente: {clienteObj?.cliente_nombre ?? "Sin seleccionar"} · Ruta: {rutaActual.toUpperCase()}
        </Text>
        <TextInput
          style={[UI.input, { marginTop: 6 }]}
          placeholder="Días de crédito (ej. 0, 7, 15)"
          keyboardType="numeric"
          value={diasCredito}
          onChangeText={setDiasCredito}
        />
        <TextInput
          style={UI.input}
          placeholder="Descuento especial negociado (opcional)"
          value={descEspecial}
          onChangeText={setDescEspecial}
        />
        <View style={UI.row}>
          <TextInput
            style={[UI.input, { flex: 1 }]}
            placeholder="Cantidad"
            keyboardType="numeric"
            value={cantidad}
            onChangeText={setCantidad}
          />
          <TouchableOpacity style={UI.btnOutline} onPress={agregarLinea}>
            <Text style={UI.btnTextOutline}>+ Agregar ítem</Text>
          </TouchableOpacity>
        </View>
        <Text style={{ fontWeight: "700", marginVertical: 4 }}>
          Ítems: {lineas.length} · Total USD: ${totalUsd.toFixed(2)}
        </Text>
      </View>

      <View
        style={[
          UI.card,
          { backgroundColor: evalPol.requiereAprobacion ? COLORS.warnBg : COLORS.successBg },
        ]}
      >
        <Text style={{ fontWeight: "700", color: evalPol.requiereAprobacion ? COLORS.warn : COLORS.success }}>
          Evaluación Offline: {evalPol.estadoSugerido}
        </Text>
        {evalPol.alertas.map((a, idx) => (
          <Text key={idx} style={{ fontSize: 12, color: COLORS.text, marginTop: 2 }}>
            • {a}
          </Text>
        ))}
      </View>

      {msg ? <Text style={{ color: COLORS.primary, marginBottom: 8 }}>{msg}</Text> : null}

      <TouchableOpacity style={UI.btnPrimary} onPress={() => void guardarOffline()}>
        <Text style={UI.btnTextPrimary}>Encolar en Outbox Offline</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}
