import React from "react";
import { ActivityIndicator, Text, TouchableOpacity, View } from "react-native";
import type { TasasVigentes } from "@lubrikca/domain-ts";

import { COLORS } from "../theme";

interface Props {
  userNombre: string;
  userEmail: string;
  isOnline: boolean;
  syncing: boolean;
  tasas: TasasVigentes | null;
  syncError: string | null;
  onSyncNow: () => void;
}

export function AppHeader({
  userNombre,
  userEmail,
  isOnline,
  syncing,
  tasas,
  syncError,
  onSyncNow,
}: Props): React.JSX.Element {
  const saludo = userNombre.trim() || userEmail.split("@")[0] || "Vendedor";
  const badgeBg = syncing ? "#1E3A8A" : isOnline ? "#065F46" : "#92400E";
  const badgeLabel = syncing ? "Sincronizando..." : isOnline ? "En línea" : "Modo Offline";

  return (
    <View style={{ backgroundColor: COLORS.navy }}>
      <View
        style={{
          paddingHorizontal: 14,
          paddingTop: 10,
          paddingBottom: 8,
          flexDirection: "row",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <View style={{ flex: 1, paddingRight: 8 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <View
              style={{
                backgroundColor: COLORS.accent,
                width: 24,
                height: 24,
                borderRadius: 6,
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Text style={{ color: "#FFF", fontWeight: "900", fontSize: 13 }}>L</Text>
            </View>
            <Text style={{ color: "#FFF", fontWeight: "800", fontSize: 15 }}>Lubrikca Móvil</Text>
          </View>
          <Text style={{ color: "#CBD5E1", fontSize: 11, marginTop: 2 }} numberOfLines={1}>
            {saludo}
          </Text>
        </View>

        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <View
            style={{
              backgroundColor: badgeBg,
              paddingHorizontal: 9,
              paddingVertical: 4,
              borderRadius: 999,
              flexDirection: "row",
              alignItems: "center",
              gap: 5,
            }}
          >
            {syncing ? <ActivityIndicator size="small" color="#FFF" /> : null}
            <Text style={{ color: "#FFF", fontSize: 11, fontWeight: "700" }}>{badgeLabel}</Text>
          </View>
          <TouchableOpacity
            onPress={onSyncNow}
            disabled={syncing || !isOnline}
            style={{
              backgroundColor: COLORS.navySoft,
              paddingHorizontal: 10,
              paddingVertical: 6,
              borderRadius: 8,
              borderWidth: 1,
              borderColor: "rgba(255,255,255,0.18)",
              opacity: syncing || !isOnline ? 0.55 : 1,
            }}
          >
            <Text style={{ color: "#FFF", fontSize: 12, fontWeight: "700" }}>↻ Sync</Text>
          </TouchableOpacity>
        </View>
      </View>

      <View
        style={{
          backgroundColor: COLORS.navySoft,
          paddingHorizontal: 14,
          paddingVertical: 6,
          flexDirection: "row",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Text style={{ color: "#93C5FD", fontSize: 11, fontWeight: "700" }}>
          BCV: {tasas && tasas.tasa_bcv > 0 ? `Bs. ${tasas.tasa_bcv.toFixed(2)}` : "Sin sync"}
        </Text>
        <Text style={{ color: "#DDD6FE", fontSize: 11, fontWeight: "700" }}>
          Binance: {tasas && tasas.tasa_binance > 0 ? `Bs. ${tasas.tasa_binance.toFixed(2)}` : "Sin sync"}
        </Text>
      </View>

      {syncError ? (
        <View
          style={{
            backgroundColor: COLORS.dangerBg,
            paddingHorizontal: 14,
            paddingVertical: 8,
            flexDirection: "row",
            alignItems: "center",
            justifyContent: "space-between",
            borderBottomWidth: 1,
            borderColor: "#FECACA",
          }}
        >
          <Text style={{ color: COLORS.danger, fontSize: 12, fontWeight: "600", flex: 1, paddingRight: 8 }}>
            Sync pendiente: {syncError}
          </Text>
          <TouchableOpacity
            onPress={onSyncNow}
            style={{
              backgroundColor: COLORS.danger,
              paddingHorizontal: 10,
              paddingVertical: 5,
              borderRadius: 6,
            }}
          >
            <Text style={{ color: "#FFF", fontSize: 11, fontWeight: "700" }}>Reintentar</Text>
          </TouchableOpacity>
        </View>
      ) : null}
    </View>
  );
}
