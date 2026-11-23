import React, { useEffect, useState } from "react";
import { ScrollView, Text, TextInput, TouchableOpacity, View } from "react-native";
import { rutaDeLista, type ListaPrecio, type LubrikcaApiClient, type ProductoCatalogo } from "@lubrikca/domain-ts";

import type { MobileSqliteRepo } from "../db/sqliteRepo";
import { COLORS, UI } from "../theme";

interface Props {
  repo: MobileSqliteRepo;
  api: LubrikcaApiClient;
  isOnline: boolean;
}

export function CatalogoScreen({ repo, api, isOnline }: Props): React.JSX.Element {
  const [query, setQuery] = useState("");
  const [listas, setListas] = useState<ListaPrecio[]>([]);
  const [listaSel, setListaSel] = useState<string>("");
  const [items, setItems] = useState<Array<ProductoCatalogo & { stock_actualizado_en: string; precio_lista: number }>>([]);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  async function cargarOffline(q = query, lid = listaSel): Promise<void> {
    const ls = await repo.obtenerListasPrecio();
    setListas(ls);
    const activeId = lid || ls[0]?.lista_id || "";
    if (!lid && activeId) setListaSel(activeId);
    const prods = await repo.buscarProductosOffline(q, activeId || undefined);
    setItems(prods);
  }

  useEffect(() => {
    void cargarOffline();
  }, [query, listaSel]);

  async function verificarStockEnVivo(): Promise<void> {
    if (!isOnline) {
      setStatusMsg("Sin señal: mostrando última caché local de stock.");
      return;
    }
    try {
      const resp = await api.verificarStock({ q: query.trim() || undefined, refresh: true });
      await repo.actualizarStockEnVivo(resp.items);
      await cargarOffline();
      setStatusMsg(`Stock actualizado en vivo (${resp.total} productos).`);
    } catch (e) {
      setStatusMsg(e instanceof Error ? e.message : "No se pudo consultar stock en vivo.");
    }
  }

  return (
    <View style={UI.screen}>
      <Text style={UI.title}>Catálogo y Stock Libre</Text>
      <TextInput
        style={UI.input}
        placeholder="Buscar por SKU, nombre o marca..."
        value={query}
        onChangeText={setQuery}
      />
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ maxHeight: 42, marginBottom: 8 }}>
        {listas.map((l) => {
          const ruta = rutaDeLista(l.lista_id, listas);
          const selected = l.lista_id === listaSel;
          return (
            <TouchableOpacity
              key={l.lista_id}
              onPress={() => setListaSel(l.lista_id)}
              style={[
                UI.btnOutline,
                {
                  marginRight: 8,
                  paddingVertical: 6,
                  backgroundColor: selected ? COLORS.primary : COLORS.card,
                },
              ]}
            >
              <Text style={{ color: selected ? "#FFF" : COLORS.text, fontSize: 12, fontWeight: "600" }}>
                {l.nombre} ({ruta.toUpperCase()})
              </Text>
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      <TouchableOpacity style={UI.btnOutline} onPress={() => void verificarStockEnVivo()}>
        <Text style={UI.btnTextOutline}>Verificar stock en vivo (/api/v1/stock/verificar)</Text>
      </TouchableOpacity>
      {statusMsg ? <Text style={{ color: COLORS.muted, fontSize: 12, marginVertical: 4 }}>{statusMsg}</Text> : null}

      <ScrollView>
        {items.map((p) => {
          const free = p.stock_disponible ?? 0;
          const colorStk = free > 5 ? COLORS.success : free > 0 ? COLORS.warn : COLORS.danger;
          return (
            <View key={p.producto_id} style={UI.card}>
              <View style={UI.row}>
                <Text style={{ fontWeight: "700", color: COLORS.text, flex: 1 }}>
                  {p.codigo} · {p.nombre}
                </Text>
                <Text style={[UI.badge, { color: colorStk, backgroundColor: COLORS.bg }]}>
                  free_qty: {p.stock_disponible ?? "N/D"}
                </Text>
              </View>
              <View style={[UI.row, { marginTop: 4 }]}>
                <Text style={{ color: COLORS.primary, fontWeight: "700" }}>
                  Lista: ${p.precio_lista.toFixed(2)}
                </Text>
                <Text style={{ color: COLORS.muted, fontSize: 11 }}>
                  Cache: {p.stock_actualizado_en ? p.stock_actualizado_en.slice(0, 16) : "sin sync"}
                </Text>
              </View>
            </View>
          );
        })}
      </ScrollView>
    </View>
  );
}
