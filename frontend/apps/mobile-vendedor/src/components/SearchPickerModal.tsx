import React, { useMemo, useState } from "react";
import {
  FlatList,
  Modal,
  SafeAreaView,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";

import { COLORS, UI } from "../theme";

export interface PickerItem {
  id: string;
  titulo: string;
  subtitulo?: string;
  detalleIzq?: string;
  detalleDer?: string;
  badge?: string;
  badgeColor?: string;
  badgeBg?: string;
  searchBlob: string;
}

interface Props {
  visible: boolean;
  titulo: string;
  placeholder: string;
  items: PickerItem[];
  emptyText?: string;
  onSelect: (id: string) => void;
  onClose: () => void;
}

export function SearchPickerModal({
  visible,
  titulo,
  placeholder,
  items,
  emptyText = "No hay resultados con esa búsqueda.",
  onSelect,
  onClose,
}: Props): React.JSX.Element {
  const [query, setQuery] = useState("");

  const filtrados = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((it) => it.searchBlob.toLowerCase().includes(q));
  }, [items, query]);

  function handleSelect(id: string): void {
    setQuery("");
    onSelect(id);
    onClose();
  }

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onClose}>
      <SafeAreaView style={{ flex: 1, backgroundColor: COLORS.bg }}>
        <View
          style={{
            backgroundColor: COLORS.navy,
            paddingHorizontal: 14,
            paddingVertical: 12,
            flexDirection: "row",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Text style={{ color: "#FFF", fontSize: 16, fontWeight: "800" }}>{titulo}</Text>
          <TouchableOpacity
            onPress={onClose}
            style={{
              paddingHorizontal: 10,
              paddingVertical: 5,
              borderRadius: 8,
              backgroundColor: COLORS.navySoft,
            }}
          >
            <Text style={{ color: "#FFF", fontWeight: "700", fontSize: 13 }}>Cerrar ✕</Text>
          </TouchableOpacity>
        </View>

        <View style={{ padding: 14, paddingBottom: 4 }}>
          <View style={UI.searchBox}>
            <TextInput
              style={UI.searchInput}
              placeholder={placeholder}
              placeholderTextColor={COLORS.muted}
              value={query}
              onChangeText={setQuery}
              autoFocus
            />
            {query ? (
              <TouchableOpacity onPress={() => setQuery("")} style={{ padding: 4 }}>
                <Text style={{ color: COLORS.muted, fontWeight: "800", fontSize: 14 }}>✕</Text>
              </TouchableOpacity>
            ) : null}
          </View>
          <Text style={{ fontSize: 11, color: COLORS.muted, marginBottom: 6 }}>
            {filtrados.length} resultado(s) disponible(s)
          </Text>
        </View>

        <FlatList
          data={filtrados}
          keyExtractor={(item) => item.id}
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={{ paddingHorizontal: 14, paddingBottom: 24 }}
          ListEmptyComponent={
            <View style={[UI.card, { alignItems: "center", paddingVertical: 24 }]}>
              <Text style={{ color: COLORS.muted, textAlign: "center", fontSize: 13 }}>{emptyText}</Text>
            </View>
          }
          renderItem={({ item }) => (
            <TouchableOpacity
              style={UI.card}
              activeOpacity={0.75}
              onPress={() => handleSelect(item.id)}
            >
              <View style={UI.row}>
                <Text style={{ fontWeight: "700", fontSize: 14, color: COLORS.text, flex: 1 }}>
                  {item.titulo}
                </Text>
                {item.badge ? (
                  <Text
                    style={[
                      UI.badge,
                      {
                        color: item.badgeColor ?? COLORS.primary,
                        backgroundColor: item.badgeBg ?? COLORS.primarySoft,
                      },
                    ]}
                  >
                    {item.badge}
                  </Text>
                ) : null}
              </View>
              {item.subtitulo ? (
                <Text style={{ fontSize: 12, color: COLORS.muted, marginTop: 3 }}>
                  {item.subtitulo}
                </Text>
              ) : null}
              {item.detalleIzq || item.detalleDer ? (
                <View style={[UI.row, { marginTop: 6 }]}>
                  <Text style={{ fontSize: 12, fontWeight: "700", color: COLORS.primary }}>
                    {item.detalleIzq ?? ""}
                  </Text>
                  <Text style={{ fontSize: 12, fontWeight: "600", color: COLORS.textSecondary }}>
                    {item.detalleDer ?? ""}
                  </Text>
                </View>
              ) : null}
            </TouchableOpacity>
          )}
        />
      </SafeAreaView>
    </Modal>
  );
}
