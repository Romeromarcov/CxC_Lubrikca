import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  RefreshControl,
  ScrollView,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { rutaDeLista, type ListaPrecio, type LubrikcaApiClient, type ProductoCatalogo } from "@lubrikca/domain-ts";

import type { MobileSqliteRepo } from "../db/sqliteRepo";
import { COLORS, UI } from "../theme";

export type ProductoConPrecio = ProductoCatalogo & {
  stock_actualizado_en: string;
  precio_lista: number;
};

interface Props {
  repo: MobileSqliteRepo;
  api: LubrikcaApiClient;
  isOnline: boolean;
  syncing: boolean;
  syncVersion: number;
  carritoCantidades: Record<string, number>;
  onSyncNow: () => Promise<void>;
  onAgregarAlCarrito: (prod: ProductoConPrecio, listaId: string) => void;
}

export function CatalogoScreen({
  repo,
  api,
  isOnline,
  syncing,
  syncVersion,
  carritoCantidades,
  onSyncNow,
  onAgregarAlCarrito,
}: Props): React.JSX.Element {
  const [query, setQuery] = useState("");
  const [listas, setListas] = useState<ListaPrecio[]>([]);
  const [listaSel, setListaSel] = useState<string>("");
  const [soloConStock, setSoloConStock] = useState(false);
  const [items, setItems] = useState<ProductoConPrecio[]>([]);
  const [cargandoStock, setCargandoStock] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  const cargarOffline = useCallback(async (q = query, lid = listaSel): Promise<void> => {
    const ls = await repo.obtenerListasPrecio();
    setListas(ls);
    const activeId = lid && ls.some((x) => x.lista_id === lid) ? lid : ls[0]?.lista_id || "";
    if (activeId && activeId !== lid) setListaSel(activeId);
    const prods = await repo.buscarProductosOffline(q, activeId || undefined);
    setItems(prods);
  }, [repo, query, listaSel]);

  useEffect(() => {
    void cargarOffline();
  }, [cargarOffline, syncVersion]);

  const filtrados = useMemo(() => {
    if (!soloConStock) return items;
    return items.filter((p) => (p.stock_disponible ?? 0) > 0);
  }, [items, soloConStock]);

  const listaObj = listas.find((l) => l.lista_id === listaSel) ?? listas[0] ?? null;
  const monedaLista = (listaObj?.moneda || "USD").toUpperCase();
  const simbolo = monedaLista === "VES" ? "Bs." : "$";

  async function verificarStockEnVivo(): Promise<void> {
    if (!isOnline) {
      setStatusMsg("Sin conexión: mostrando stock de la última sincronización.");
      return;
    }
    setCargandoStock(true);
    setStatusMsg(null);
    try {
      const resp = await api.verificarStock({ q: query.trim() || undefined, refresh: true });
      await repo.actualizarStockEnVivo(resp.items);
      await cargarOffline();
      setStatusMsg(`Stock actualizado en vivo (${resp.total} productos).`);
    } catch (e) {
      setStatusMsg(e instanceof Error ? e.message : "No se pudo consultar stock en vivo.");
    } finally {
      setCargandoStock(false);
    }
  }

  return (
    <View style={[UI.screen, { paddingHorizontal: 14, paddingTop: 12 }]}>
      <View style={[UI.row, { marginBottom: 8 }]}>
        <View style={{ flex: 1 }}>
          <Text style={UI.title}>Catálogo y Stock</Text>
          <Text style={{ fontSize: 12, color: COLORS.muted }}>
            {filtrados.length} producto(s) · Lista: {listaObj?.nombre ?? "Predeterminada"}
          </Text>
        </View>
        <TouchableOpacity
          style={[UI.btnOutline, { marginVertical: 0, paddingVertical: 8 }]}
          onPress={() => void verificarStockEnVivo()}
          disabled={cargandoStock}
        >
          <Text style={UI.btnTextOutline}>
            {cargandoStock ? "Consultando..." : "Stock en vivo"}
          </Text>
        </TouchableOpacity>
      </View>

      <View style={UI.searchBox}>
        <TextInput
          style={UI.searchInput}
          placeholder="Buscar por código SKU, nombre o marca..."
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

      <View style={{ marginBottom: 8 }}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}>
          <TouchableOpacity
            onPress={() => setSoloConStock((v) => !v)}
            style={[
              UI.chip,
              soloConStock ? { backgroundColor: COLORS.success, borderColor: COLORS.success } : null,
            ]}
          >
            <Text style={{ fontSize: 12, fontWeight: "700", color: soloConStock ? "#FFF" : COLORS.text }}>
              {soloConStock ? "✓ Con stock" : "Solo con stock"}
            </Text>
          </TouchableOpacity>

          {listas.map((l) => {
            const ruta = rutaDeLista(l.lista_id, listas);
            const selected = l.lista_id === listaSel;
            return (
              <TouchableOpacity
                key={l.lista_id}
                onPress={() => setListaSel(l.lista_id)}
                style={[UI.chip, selected ? UI.chipActive : null]}
              >
                <Text style={{ color: selected ? "#FFF" : COLORS.text, fontSize: 12, fontWeight: "700" }}>
                  {l.nombre} · {ruta.toUpperCase()}
                </Text>
              </TouchableOpacity>
            );
          })}
        </ScrollView>
      </View>

      {statusMsg ? (
        <View style={{ backgroundColor: COLORS.primarySoft, padding: 8, borderRadius: 8, marginBottom: 8 }}>
          <Text style={{ color: COLORS.primary, fontSize: 12, fontWeight: "600" }}>{statusMsg}</Text>
        </View>
      ) : null}

      <FlatList
        data={filtrados}
        keyExtractor={(p) => p.producto_id}
        contentContainerStyle={{ paddingBottom: 24 }}
        refreshControl={
          <RefreshControl refreshing={syncing} onRefresh={() => void onSyncNow()} />
        }
        ListEmptyComponent={
          <View style={[UI.card, { alignItems: "center", paddingVertical: 28 }]}>
            {syncing ? (
              <>
                <ActivityIndicator size="large" color={COLORS.primary} />
                <Text style={{ marginTop: 10, color: COLORS.muted, fontWeight: "600" }}>
                  Descargando catálogo desde Lubrikca...
                </Text>
              </>
            ) : (
              <>
                <Text style={{ fontSize: 15, fontWeight: "700", color: COLORS.text, marginBottom: 6 }}>
                  {items.length === 0 ? "Catálogo aún vacío en este teléfono" : "Sin coincidencias"}
                </Text>
                <Text style={{ fontSize: 13, color: COLORS.muted, textAlign: "center", marginBottom: 12 }}>
                  {items.length === 0
                    ? "Sincroniza para descargar productos, listas de precio y stock disponible."
                    : "Prueba limpiando el filtro o desactivando 'Solo con stock'."}
                </Text>
                <TouchableOpacity style={UI.btnPrimary} onPress={() => void onSyncNow()}>
                  <Text style={UI.btnTextPrimary}>Sincronizar catálogo ahora</Text>
                </TouchableOpacity>
              </>
            )}
          </View>
        }
        renderItem={({ item: p }) => {
          const free = p.stock_disponible ?? 0;
          const hasStock = p.stock_disponible !== null;
          const colorStk = !hasStock
            ? COLORS.muted
            : free > 5
              ? COLORS.success
              : free > 0
                ? COLORS.warn
                : COLORS.danger;
          const bgStk = !hasStock
            ? COLORS.surface
            : free > 5
              ? COLORS.successBg
              : free > 0
                ? COLORS.warnBg
                : COLORS.dangerBg;
          const enCarrito = carritoCantidades[p.producto_id] ?? 0;

          return (
            <View style={UI.card}>
              <View style={UI.row}>
                <Text style={{ fontSize: 11, fontWeight: "800", color: COLORS.primary }}>
                  {p.codigo || `ID ${p.producto_id}`}
                </Text>
                <Text style={[UI.badge, { color: colorStk, backgroundColor: bgStk }]}>
                  {hasStock ? `Stock libre: ${p.stock_disponible}` : "Stock N/D"}
                </Text>
              </View>

              <Text style={{ fontWeight: "700", fontSize: 15, color: COLORS.text, marginTop: 4 }}>
                {p.nombre}
              </Text>

              {p.marca || p.presentacion || p.litros > 0 ? (
                <Text style={{ fontSize: 12, color: COLORS.muted, marginTop: 2 }}>
                  {[p.marca, p.presentacion, p.litros > 0 ? `${p.litros} L` : ""].filter(Boolean).join(" · ")}
                </Text>
              ) : null}

              <View style={[UI.row, { marginTop: 10 }]}>
                <View>
                  <Text style={{ fontSize: 18, fontWeight: "800", color: COLORS.text }}>
                    {simbolo} {p.precio_lista.toFixed(2)}
                  </Text>
                  <Text style={{ fontSize: 11, color: COLORS.muted }}>
                    Ref USD: ${p.precio_usd.toFixed(2)}
                  </Text>
                </View>

                <TouchableOpacity
                  onPress={() => onAgregarAlCarrito(p, listaSel)}
                  style={{
                    backgroundColor: enCarrito > 0 ? COLORS.success : COLORS.primary,
                    paddingHorizontal: 14,
                    paddingVertical: 9,
                    borderRadius: 10,
                  }}
                >
                  <Text style={{ color: "#FFF", fontWeight: "700", fontSize: 13 }}>
                    {enCarrito > 0 ? `+ Cotizar (${enCarrito})` : "+ Cotizar"}
                  </Text>
                </TouchableOpacity>
              </View>
            </View>
          );
        }}
      />
    </View>
  );
}
