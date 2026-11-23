import React, { useEffect, useState } from "react";
import { ScrollView, Text, TextInput, TouchableOpacity, View } from "react-native";
import { describirResolucionConflicto } from "@lubrikca/domain-ts";

import type { MobileSqliteRepo, OutboxRegistro } from "../db/sqliteRepo";
import type { SyncEngine, SyncReport } from "../sync/syncEngine";
import { COLORS, UI } from "../theme";

interface Props {
  repo: MobileSqliteRepo;
  syncEngine: SyncEngine;
  deviceId: string;
  isOnline: boolean;
  onLogout: () => void;
}

export function OutboxScreen({ repo, syncEngine, deviceId, isOnline, onLogout }: Props): React.JSX.Element {
  const [items, setItems] = useState<OutboxRegistro[]>([]);
  const [cursor, setCursor] = useState("");
  const [editKey, setEditKey] = useState<string | null>(null);
  const [editRef, setEditRef] = useState("");
  const [editBancoDest, setEditBancoDest] = useState("");
  const [report, setReport] = useState<SyncReport | null>(null);

  async function recargar(): Promise<void> {
    const [lista, cur] = await Promise.all([repo.listarOutbox(), repo.obtenerCursor()]);
    setItems(lista);
    setCursor(cur);
  }

  useEffect(() => {
    void recargar();
  }, []);

  async function sincronizarAhora(): Promise<void> {
    const r = await syncEngine.sincronizarCompleto(deviceId);
    setReport(r);
    if (r.sesionRevocada) {
      onLogout();
      return;
    }
    await recargar();
  }

  async function guardarCorreccion(reg: OutboxRegistro): Promise<void> {
    const next = {
      ...reg.payload,
      ...(editRef.trim() ? { referencia: editRef.trim() } : {}),
      ...(editBancoDest.trim() ? { banco_destino: editBancoDest.trim() } : {}),
    };
    await repo.corregirYReencolarConflicto(reg.idempotency_key, next);
    setEditKey(null);
    setEditRef("");
    setEditBancoDest("");
    await recargar();
  }

  return (
    <ScrollView style={UI.screen}>
      <Text style={UI.title}>Cola Outbox y Sincronización</Text>
      <Text style={UI.subtitle}>
        Estado red: {isOnline ? "EN LÍNEA" : "OFFLINE"} · Último cursor: {cursor || "Sin bootstrap"}
      </Text>

      <TouchableOpacity style={UI.btnPrimary} onPress={() => void sincronizarAhora()}>
        <Text style={UI.btnTextPrimary}>Sincronizar Ahora (Push + Delta)</Text>
      </TouchableOpacity>
      <TouchableOpacity style={UI.btnDanger} onPress={onLogout}>
        <Text style={UI.btnTextPrimary}>Cerrar Sesión y Borrar SQLite Local</Text>
      </TouchableOpacity>

      {report ? (
        <View style={UI.card}>
          <Text style={{ fontSize: 12, color: COLORS.text }}>
            Última sincronización: {report.itemsSync} OK · {report.itemsConflicto} Conflictos ·{" "}
            {report.itemsRetry} Reintentos · Delta: {report.deltaAplicado ? "Sí" : "No"}
          </Text>
        </View>
      ) : null}

      {items.map((it) => {
        const guia = describirResolucionConflicto({
          codigoError: it.ultimo_error_codigo,
          mensaje: it.ultimo_error_msg,
          retenidoAprobacion: it.retenido_aprobacion,
        });
        const esConflicto = it.estado === "CONFLICT_USER_ACTION";
        return (
          <View
            key={it.idempotency_key}
            style={[
              UI.card,
              {
                borderColor: esConflicto ? COLORS.danger : COLORS.border,
                backgroundColor: esConflicto ? COLORS.dangerBg : COLORS.card,
              },
            ]}
          >
            <View style={UI.row}>
              <Text style={{ fontWeight: "700", color: COLORS.text }}>{it.tipo.toUpperCase()}</Text>
              <Text style={[UI.badge, { color: esConflicto ? COLORS.danger : COLORS.primary, backgroundColor: "#FFF" }]}>
                {it.estado} (intentos: {it.intentos})
              </Text>
            </View>
            {it.ultimo_error_msg ? (
              <Text style={{ color: COLORS.danger, fontSize: 12, marginTop: 4 }}>
                {guia.titulo}: {it.ultimo_error_msg}
              </Text>
            ) : null}
            {esConflicto ? (
              <View style={{ marginTop: 6 }}>
                <Text style={{ fontSize: 12, color: COLORS.muted, marginBottom: 4 }}>
                  Acción: {guia.accionRecomendada}
                </Text>
                {editKey === it.idempotency_key ? (
                  <>
                    <TextInput
                      style={UI.input}
                      placeholder="Nueva referencia / dato"
                      value={editRef}
                      onChangeText={setEditRef}
                    />
                    <TextInput
                      style={UI.input}
                      placeholder="Nuevo banco destino Lubrikca"
                      value={editBancoDest}
                      onChangeText={setEditBancoDest}
                    />
                    <TouchableOpacity style={UI.btnPrimary} onPress={() => void guardarCorreccion(it)}>
                      <Text style={UI.btnTextPrimary}>Guardar y Reencolar</Text>
                    </TouchableOpacity>
                  </>
                ) : (
                  <View style={UI.row}>
                    <TouchableOpacity
                      style={[UI.btnOutline, { flex: 1 }]}
                      onPress={() => setEditKey(it.idempotency_key)}
                    >
                      <Text style={UI.btnTextOutline}>Corregir Borrador</Text>
                    </TouchableOpacity>
                    <TouchableOpacity
                      style={[UI.btnDanger, { flex: 1 }]}
                      onPress={() => void repo.descartarConflicto(it.idempotency_key).then(recargar)}
                    >
                      <Text style={UI.btnTextPrimary}>Descartar</Text>
                    </TouchableOpacity>
                  </View>
                )}
              </View>
            ) : null}
          </View>
        );
      })}
    </ScrollView>
  );
}
