import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ScrollView, Text, TextInput, TouchableOpacity, View } from "react-native";
import * as Location from "expo-location";
import {
  evaluarPoliticaComercialOffline,
  rutaDeLista,
  validarNoMezclaRutasDocumento,
  type ListaPrecio,
  type PoliticaVentasGlobal,
  type TasasVigentes,
} from "@lubrikca/domain-ts";

import { SearchPickerModal, type PickerItem } from "../components/SearchPickerModal";
import type { ClienteBuscable, MobileSqliteRepo } from "../db/sqliteRepo";
import { COLORS, UI } from "../theme";
import type { ProductoConPrecio } from "./CatalogoScreen";

export interface LineaCarritoMobile {
  producto_id: string;
  codigo: string;
  nombre: string;
  cantidad: number;
  precio_usd: number;
  precio_ves: number;
  precios_por_lista: Record<string, number>;
  precio_unitario: number;
  lista_id: string;
}

interface Props {
  repo: MobileSqliteRepo;
  vendedorEmail: string;
  syncVersion: number;
  tasas: TasasVigentes | null;
  clientePreseleccionadoId: string | null;
  lineas: LineaCarritoMobile[];
  setLineas: React.Dispatch<React.SetStateAction<LineaCarritoMobile[]>>;
  onLimpiarPreseleccion: () => void;
  onSyncNow: () => Promise<void>;
}

function resolverPrecioEnLista(
  prod: { precio_usd: number; precio_ves: number; precios_por_lista: Record<string, number>; precio_lista?: number },
  lista: ListaPrecio | null,
): number {
  if (lista) {
    const lid = String(lista.lista_id ?? lista.id ?? "");
    if (lid && prod.precios_por_lista[lid] !== undefined) {
      return Number(prod.precios_por_lista[lid]);
    }
    const mon = String(lista.moneda || "").toUpperCase();
    if (mon === "VES" && prod.precio_ves > 0) return Number(prod.precio_ves);
  }
  return Number(prod.precio_lista ?? prod.precio_usd ?? 0);
}

export function CotizacionScreen({
  repo,
  vendedorEmail,
  syncVersion,
  tasas,
  clientePreseleccionadoId,
  lineas,
  setLineas,
  onLimpiarPreseleccion,
  onSyncNow,
}: Props): React.JSX.Element {
  const [clientes, setClientes] = useState<ClienteBuscable[]>([]);
  const [listas, setListas] = useState<ListaPrecio[]>([]);
  const [productos, setProductos] = useState<ProductoConPrecio[]>([]);
  const [politicaGlobal, setPoliticaGlobal] = useState<PoliticaVentasGlobal | null>(null);

  const [clienteId, setClienteId] = useState("");
  const [modalCliente, setModalCliente] = useState(false);
  const [modalProducto, setModalProducto] = useState(false);

  const [tipoDoc, setTipoDoc] = useState<"cotizacion" | "pedido">("pedido");
  const [listaId, setListaId] = useState("");
  const [diasCredito, setDiasCredito] = useState("0");
  const [descMotivo, setDescMotivo] = useState("");
  const [descPct, setDescPct] = useState("");
  const [notas, setNotas] = useState("");
  const [guardando, setGuardando] = useState(false);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);

  const cargarBase = useCallback(async (): Promise<void> => {
    const [clis, ls, pol] = await Promise.all([
      repo.listarClientesBuscables(),
      repo.obtenerListasPrecio(),
      repo.obtenerPoliticaVentas(),
    ]);
    setClientes(clis);
    setListas(ls);
    setPoliticaGlobal(pol);
    const activeListaId = listaId && ls.some((x) => x.lista_id === listaId)
      ? listaId
      : ls[0]?.lista_id ?? "";
    if (activeListaId && activeListaId !== listaId) {
      setListaId(activeListaId);
    }
    const prods = await repo.buscarProductosOffline("", activeListaId || undefined);
    setProductos(prods);
  }, [repo, listaId]);

  useEffect(() => {
    void cargarBase();
  }, [cargarBase, syncVersion]);

  useEffect(() => {
    if (clientePreseleccionadoId) {
      setClienteId(clientePreseleccionadoId);
      onLimpiarPreseleccion();
    }
  }, [clientePreseleccionadoId, onLimpiarPreseleccion]);

  const listaObj = useMemo(
    () => listas.find((l) => l.lista_id === listaId) ?? listas[0] ?? null,
    [listas, listaId],
  );
  const rutaActual = rutaDeLista(listaId, listas);
  const monedaLista = (listaObj?.moneda || "USD").toUpperCase();
  const simbolo = monedaLista === "VES" ? "Bs." : "$";

  function cambiarListaPrecio(nuevaListaId: string): void {
    setListaId(nuevaListaId);
    const nuevaLista = listas.find((l) => l.lista_id === nuevaListaId) ?? null;
    setLineas((prev) =>
      prev.map((ln) => ({
        ...ln,
        lista_id: nuevaListaId,
        precio_unitario: resolverPrecioEnLista(ln, nuevaLista),
      })),
    );
  }

  const clienteObj = clientes.find((c) => c.cliente_id === clienteId) ?? null;

  const subtotal = lineas.reduce((acc, l) => acc + l.cantidad * l.precio_unitario, 0);
  const pctDesc = Math.max(0, Math.min(100, Number(descPct || 0)));
  const montoDesc = pctDesc > 0 ? subtotal * (pctDesc / 100) : 0;
  const subtotalNeto = Math.max(0, subtotal - montoDesc);
  const iva = subtotalNeto * 0.16;
  const totalDoc = subtotalNeto + iva;

  const tasaRef = rutaActual === "binance" ? tasas?.tasa_binance ?? 0 : tasas?.tasa_bcv ?? 0;
  const totalUsdEstimado =
    monedaLista === "VES" && tasaRef > 0 ? totalDoc / tasaRef : totalDoc;
  const totalBsEstimado =
    monedaLista === "USD" && tasaRef > 0 ? totalDoc * tasaRef : totalDoc;

  const evalPol = evaluarPoliticaComercialOffline({
    tipoDocumento: tipoDoc,
    montoPedidoUsd: totalUsdEstimado,
    diasCredito: Number(diasCredito || 0),
    tieneDescuentoEspecial: Boolean(descMotivo.trim()) || pctDesc > 0,
    cliente: clienteObj,
    politicaGlobal,
  });

  const clienteItems: PickerItem[] = useMemo(
    () =>
      clientes.map((c) => ({
        id: c.cliente_id,
        titulo: c.cliente_nombre,
        subtitulo: [c.rif || "Sin RIF", c.telefono || ""].filter(Boolean).join(" · "),
        detalleIzq: `BCV: $${c.saldo_bcv_usd.toFixed(2)}  |  Binance: $${c.saldo_binance_usd.toFixed(2)}`,
        badge: c.max_dias_mora > 0 ? `Mora ${c.max_dias_mora}d` : c.fuente === "maestro" ? "Maestro" : "Al día",
        badgeColor: c.max_dias_mora > 0 ? COLORS.danger : COLORS.success,
        badgeBg: c.max_dias_mora > 0 ? COLORS.dangerBg : COLORS.successBg,
        searchBlob: `${c.cliente_nombre} ${c.rif} ${c.telefono} ${c.cliente_id}`,
      })),
    [clientes],
  );

  const productoItems: PickerItem[] = useMemo(
    () =>
      productos.map((p) => {
        const free = p.stock_disponible ?? 0;
        const precio = resolverPrecioEnLista(p, listaObj);
        return {
          id: p.producto_id,
          titulo: `[${p.codigo || p.producto_id}] ${p.nombre}`,
          subtitulo: [p.marca, p.presentacion, p.litros > 0 ? `${p.litros} L` : ""].filter(Boolean).join(" · "),
          detalleIzq: `${simbolo} ${precio.toFixed(2)}`,
          detalleDer: `Ref $${p.precio_usd.toFixed(2)}`,
          badge: p.stock_disponible !== null ? `Stock: ${p.stock_disponible}` : "Stock N/D",
          badgeColor: free > 5 ? COLORS.success : free > 0 ? COLORS.warn : COLORS.danger,
          badgeBg: free > 5 ? COLORS.successBg : free > 0 ? COLORS.warnBg : COLORS.dangerBg,
          searchBlob: `${p.codigo} ${p.nombre} ${p.marca} ${p.presentacion}`,
        };
      }),
    [productos, listaObj, simbolo],
  );

  function agregarProductoDesdeModal(prodId: string): void {
    const p = productos.find((x) => x.producto_id === prodId);
    if (!p) return;
    const precio = resolverPrecioEnLista(p, listaObj);
    setLineas((prev) => {
      const idx = prev.findIndex((x) => x.producto_id === p.producto_id);
      if (idx >= 0) {
        return prev.map((x, i) => (i === idx ? { ...x, cantidad: x.cantidad + 1, precio_unitario: precio } : x));
      }
      return [
        ...prev,
        {
          producto_id: p.producto_id,
          codigo: p.codigo,
          nombre: p.nombre,
          cantidad: 1,
          precio_usd: p.precio_usd,
          precio_ves: p.precio_ves,
          precios_por_lista: p.precios_por_lista,
          precio_unitario: precio,
          lista_id: listaId,
        },
      ];
    });
  }

  function ajustarCantidad(prodId: string, delta: number): void {
    setLineas((prev) =>
      prev
        .map((x) => (x.producto_id === prodId ? { ...x, cantidad: Math.max(0, x.cantidad + delta) } : x))
        .filter((x) => x.cantidad > 0),
    );
  }

  function fijarCantidadTexto(prodId: string, val: string): void {
    const num = Math.max(1, Number(val.replace(/[^0-9.]/g, "")) || 1);
    setLineas((prev) => prev.map((x) => (x.producto_id === prodId ? { ...x, cantidad: num } : x)));
  }

  async function guardarOffline(): Promise<void> {
    setMsg(null);
    if (!clienteObj) {
      setMsg({ text: "Primero busca y selecciona un cliente.", ok: false });
      return;
    }
    if (lineas.length === 0) {
      setMsg({ text: "Agrega al menos un producto al pedido.", ok: false });
      return;
    }
    const checkRutas = validarNoMezclaRutasDocumento(rutaActual, lineas, listas);
    if (!checkRutas.valido) {
      setMsg({ text: checkRutas.error ?? "No puedes mezclar rutas BCV y Binance.", ok: false });
      return;
    }

    setGuardando(true);
    try {
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
          rif: clienteObj.rif,
          vendedor_email: vendedorEmail,
          lista_precios_id: listaId,
          ruta_pago: rutaActual,
          dias_credito: Number(diasCredito || 0),
          notas: notas.trim(),
          descuentos_especiales:
            descMotivo.trim() || pctDesc > 0
              ? [{ motivo: descMotivo.trim() || "Descuento comercial", porcentaje: pctDesc }]
              : [],
          lineas: lineas.map((l) => ({
            producto_id: l.producto_id,
            codigo: l.codigo,
            descripcion: l.nombre,
            cantidad: l.cantidad,
            precio_unitario: l.precio_unitario,
          })),
          ...coords,
        },
      });
      setLineas([]);
      setDescMotivo("");
      setDescPct("");
      setNotas("");
      void onSyncNow();
      setMsg({
        text: `${tipoDoc.toUpperCase()} guardado en cola (${evalPol.estadoSugerido}).`,
        ok: true,
      });
    } finally {
      setGuardando(false);
    }
  }

  return (
    <ScrollView style={UI.screen} contentContainerStyle={UI.screenPad}>
      <Text style={UI.title}>Nueva Cotización / Pedido</Text>
      <Text style={UI.subtitle}>Funciona online u offline con evaluación de crédito y ruta.</Text>

      <View style={UI.segmentWrap}>
        {(
          [
            ["pedido", "Pedido Confirmado"],
            ["cotizacion", "Cotización"],
          ] as const
        ).map(([id, label]) => {
          const active = tipoDoc === id;
          return (
            <TouchableOpacity
              key={id}
              onPress={() => setTipoDoc(id)}
              style={[UI.segmentBtn, active ? UI.segmentBtnActive : null]}
            >
              <Text style={{ fontWeight: "700", fontSize: 13, color: active ? COLORS.navy : COLORS.muted }}>
                {label}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>

      <View style={UI.card}>
        <Text style={UI.sectionLabel}>1. Cliente</Text>
        <TouchableOpacity style={UI.pickerBtn} onPress={() => setModalCliente(true)}>
          <View style={{ flex: 1, paddingRight: 8 }}>
            <Text style={{ fontWeight: "700", fontSize: 14, color: clienteObj ? COLORS.text : COLORS.muted }}>
              {clienteObj ? clienteObj.cliente_nombre : "🔍 Tocar para buscar cliente por nombre o RIF..."}
            </Text>
            {clienteObj ? (
              <Text style={{ fontSize: 12, color: COLORS.muted, marginTop: 2 }}>
                {clienteObj.rif || "Sin RIF"} · BCV ${clienteObj.saldo_bcv_usd.toFixed(2)} · Binance $
                {clienteObj.saldo_binance_usd.toFixed(2)}
              </Text>
            ) : null}
          </View>
          <Text style={{ color: COLORS.primary, fontWeight: "800", fontSize: 13 }}>Buscar</Text>
        </TouchableOpacity>

        {clienteObj && clienteObj.max_dias_mora > 0 ? (
          <View style={{ backgroundColor: COLORS.warnBg, padding: 8, borderRadius: 8, marginBottom: 8 }}>
            <Text style={{ color: COLORS.warn, fontSize: 12, fontWeight: "700" }}>
              Atención: cliente registra {clienteObj.max_dias_mora} día(s) de mora.
            </Text>
          </View>
        ) : null}

        <Text style={UI.sectionLabel}>2. Lista de Precios y Ruta de Pago</Text>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 4 }}>
          {listas.map((l) => {
            const r = rutaDeLista(l.lista_id, listas);
            const sel = l.lista_id === listaId;
            return (
              <TouchableOpacity
                key={l.lista_id}
                onPress={() => cambiarListaPrecio(l.lista_id)}
                style={[UI.chip, sel ? UI.chipActive : null]}
              >
                <Text style={{ color: sel ? "#FFF" : COLORS.text, fontSize: 12, fontWeight: "700" }}>
                  {l.nombre} ({r.toUpperCase()})
                </Text>
              </TouchableOpacity>
            );
          })}
        </ScrollView>
      </View>

      <View style={UI.card}>
        <View style={[UI.row, { marginBottom: 8 }]}>
          <Text style={[UI.sectionLabel, { marginBottom: 0 }]}>3. Productos ({lineas.length})</Text>
          <TouchableOpacity
            onPress={() => setModalProducto(true)}
            style={{
              backgroundColor: COLORS.primarySoft,
              paddingHorizontal: 12,
              paddingVertical: 7,
              borderRadius: 8,
            }}
          >
            <Text style={{ color: COLORS.primary, fontWeight: "800", fontSize: 12 }}>
              🔍 + Agregar producto
            </Text>
          </TouchableOpacity>
        </View>

        {lineas.length === 0 ? (
          <TouchableOpacity
            style={[
              UI.pickerBtn,
              { justifyContent: "center", borderStyle: "dashed", backgroundColor: COLORS.card },
            ]}
            onPress={() => setModalProducto(true)}
          >
            <Text style={{ color: COLORS.primary, fontWeight: "700", fontSize: 13 }}>
              Toca aquí para buscar productos en el catálogo
            </Text>
          </TouchableOpacity>
        ) : (
          lineas.map((ln) => (
            <View
              key={ln.producto_id}
              style={{
                borderTopWidth: 1,
                borderColor: COLORS.border,
                paddingVertical: 10,
              }}
            >
              <View style={UI.row}>
                <View style={{ flex: 1, paddingRight: 8 }}>
                  <Text style={{ fontWeight: "700", fontSize: 13, color: COLORS.text }}>
                    [{ln.codigo || ln.producto_id}] {ln.nombre}
                  </Text>
                  <Text style={{ fontSize: 12, color: COLORS.muted, marginTop: 2 }}>
                    Unit: {simbolo} {ln.precio_unitario.toFixed(2)} · Sub:{" "}
                    {simbolo} {(ln.cantidad * ln.precio_unitario).toFixed(2)}
                  </Text>
                </View>

                <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                  <TouchableOpacity
                    onPress={() => ajustarCantidad(ln.producto_id, -1)}
                    style={{
                      width: 30,
                      height: 30,
                      borderRadius: 8,
                      backgroundColor: COLORS.border,
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <Text style={{ fontWeight: "800", fontSize: 16, color: COLORS.text }}>−</Text>
                  </TouchableOpacity>
                  <TextInput
                    style={{
                      minWidth: 40,
                      textAlign: "center",
                      borderWidth: 1,
                      borderColor: COLORS.borderStrong,
                      borderRadius: 8,
                      paddingVertical: 4,
                      fontWeight: "700",
                      color: COLORS.text,
                    }}
                    keyboardType="numeric"
                    value={String(ln.cantidad)}
                    onChangeText={(v) => fijarCantidadTexto(ln.producto_id, v)}
                  />
                  <TouchableOpacity
                    onPress={() => ajustarCantidad(ln.producto_id, 1)}
                    style={{
                      width: 30,
                      height: 30,
                      borderRadius: 8,
                      backgroundColor: COLORS.primary,
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <Text style={{ fontWeight: "800", fontSize: 16, color: "#FFF" }}>+</Text>
                  </TouchableOpacity>
                </View>
              </View>
            </View>
          ))
        )}
      </View>

      <View style={UI.card}>
        <Text style={UI.sectionLabel}>4. Condiciones y Descuentos</Text>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 8 }}>
          {["0", "7", "15", "30"].map((d) => {
            const sel = diasCredito === d;
            return (
              <TouchableOpacity
                key={d}
                onPress={() => setDiasCredito(d)}
                style={[UI.chip, sel ? UI.chipActive : null]}
              >
                <Text style={{ color: sel ? "#FFF" : COLORS.text, fontSize: 12, fontWeight: "700" }}>
                  {d === "0" ? "Contado (0d)" : `${d} días`}
                </Text>
              </TouchableOpacity>
            );
          })}
        </ScrollView>

        <View style={UI.row}>
          <TextInput
            style={[UI.input, { flex: 1 }]}
            placeholder="Días crédito"
            placeholderTextColor={COLORS.muted}
            keyboardType="numeric"
            value={diasCredito}
            onChangeText={setDiasCredito}
          />
          <TextInput
            style={[UI.input, { flex: 1 }]}
            placeholder="% Desc. especial"
            placeholderTextColor={COLORS.muted}
            keyboardType="numeric"
            value={descPct}
            onChangeText={setDescPct}
          />
        </View>
        <TextInput
          style={UI.input}
          placeholder="Motivo descuento especial / notas (opcional)"
          placeholderTextColor={COLORS.muted}
          value={descMotivo}
          onChangeText={setDescMotivo}
        />
        <TextInput
          style={[UI.input, { marginBottom: 0 }]}
          placeholder="Observaciones de entrega / orden (opcional)"
          placeholderTextColor={COLORS.muted}
          value={notas}
          onChangeText={setNotas}
        />
      </View>

      <View style={UI.card}>
        <Text style={UI.sectionLabel}>Resumen Estimado</Text>
        <View style={UI.row}>
          <Text style={{ color: COLORS.muted, fontSize: 13 }}>Subtotal neto</Text>
          <Text style={{ color: COLORS.text, fontWeight: "700", fontSize: 13 }}>
            {simbolo} {subtotalNeto.toFixed(2)}
          </Text>
        </View>
        <View style={[UI.row, { marginTop: 4 }]}>
          <Text style={{ color: COLORS.muted, fontSize: 13 }}>IVA (16%)</Text>
          <Text style={{ color: COLORS.text, fontWeight: "700", fontSize: 13 }}>
            {simbolo} {iva.toFixed(2)}
          </Text>
        </View>
        <View style={[UI.row, { marginTop: 6, paddingTop: 6, borderTopWidth: 1, borderColor: COLORS.border }]}>
          <Text style={{ color: COLORS.text, fontWeight: "800", fontSize: 16 }}>Total estimado</Text>
          <Text style={{ color: COLORS.primary, fontWeight: "800", fontSize: 18 }}>
            {simbolo} {totalDoc.toFixed(2)}
          </Text>
        </View>
        {tasaRef > 0 ? (
          <Text style={{ fontSize: 11, color: COLORS.muted, textAlign: "right", marginTop: 2 }}>
            {monedaLista === "USD"
              ? `≈ Bs. ${totalBsEstimado.toFixed(2)} (${rutaActual.toUpperCase()} @ ${tasaRef.toFixed(2)})`
              : `≈ $${totalUsdEstimado.toFixed(2)} (${rutaActual.toUpperCase()} @ ${tasaRef.toFixed(2)})`}
          </Text>
        ) : null}
      </View>

      <View
        style={[
          UI.card,
          { backgroundColor: evalPol.requiereAprobacion ? COLORS.warnBg : COLORS.successBg },
        ]}
      >
        <Text style={{ fontWeight: "800", fontSize: 13, color: evalPol.requiereAprobacion ? COLORS.warn : COLORS.success }}>
          Evaluación Offline: {evalPol.estadoSugerido}
        </Text>
        {evalPol.alertas.map((a, idx) => (
          <Text key={idx} style={{ fontSize: 12, color: COLORS.text, marginTop: 3 }}>
            • {a}
          </Text>
        ))}
      </View>

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

      <TouchableOpacity
        style={[UI.btnPrimary, { opacity: guardando ? 0.7 : 1 }]}
        disabled={guardando}
        onPress={() => void guardarOffline()}
      >
        <Text style={UI.btnTextPrimary}>
          {guardando ? "Guardando..." : `Guardar ${tipoDoc === "pedido" ? "Pedido" : "Cotización"}`}
        </Text>
      </TouchableOpacity>

      <SearchPickerModal
        visible={modalCliente}
        titulo="Seleccionar Cliente"
        placeholder="Buscar por nombre, RIF o teléfono..."
        items={clienteItems}
        emptyText="No se encontraron clientes. Sincroniza o crea el cliente en la pestaña Clientes."
        onSelect={setClienteId}
        onClose={() => setModalCliente(false)}
      />

      <SearchPickerModal
        visible={modalProducto}
        titulo={`Agregar Producto (${listaObj?.nombre ?? "Lista"})`}
        placeholder="Buscar por SKU, nombre o marca..."
        items={productoItems}
        emptyText="Catálogo sin resultados. Sincroniza en la pestaña Catálogo."
        onSelect={agregarProductoDesdeModal}
        onClose={() => setModalProducto(false)}
      />
    </ScrollView>
  );
}
