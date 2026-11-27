import React, { useCallback, useEffect, useState } from "react";
import { Alert, ScrollView, Text, TextInput, TouchableOpacity, View } from "react-native";
import { describirResolucionConflicto } from "@lubrikca/domain-ts";

import type { MobileSqliteRepo, OutboxRegistro, ResumenCacheLocal } from "../db/sqliteRepo";
import type { SyncReport } from "../sync/syncEngine";
import { COLORS, UI } from "../theme";

interface Props {
  repo: MobileSqliteRepo;
  isOnline: boolean;
  syncing: boolean;
  syncVersion: number;
  lastReport: SyncReport | null;
  onSyncNow: () => Promise<SyncReport>;
  onLogout: () => void;
}

export function OutboxScreen({
  repo,
  isOnline,
  syncing,
  syncVersion,
  lastReport,
  onSyncNow,
  onLogout,
}: Props): React.JSX.Element {
  const [items, setItems] = useState<OutboxRegistro[]>([]);
  const [cursor, setCursor] = useState("");
  const [resumen, setResumen] = useState<ResumenCacheLocal | null>(null);
  const [editKey, setEditKey] = useState<string | null>(null);
  const [editRef, setEditRef] = useState("");
  const [editBancoDest, setEditBancoDest] = useState("");

  const recargar = useCallback(async (): Promise<void> => {
    const [lista, cur, res] = await Promise.all([
      repo.listarOutbox(),
      repo.obtenerCursor(),
      repo.obtenerResumenLocal(),
    ]);
    setItems(lista);
    setCursor(cur);
    setResumen(res);
  }, [repo]);

  useEffect(() => {
    void recargar();
  }, [recargar, syncVersion]);

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

  function confirmarLogout(): void {
    Alert.alert(
      "Cerrar Sesión",
      "Se cerrará tu sesión y se borrarán los datos locales en este teléfono.",
      [
        { text: "Cancelar", style: "cancel" },
        { text: "Cerrar Sesión", style: "destructive", onPress: onLogout },
      ],
    );
  }

  return (
    <ScrollView style={UI.screen} contentContainerStyle={UI.screenPad}>
      <Text style={UI.title}>Sincronización y Cola</Text>
      <Text style={UI.subtitle}>
        Red: {isOnline ? "En línea" : "Sin señal"} · Último sync: {cursor ? cursor.slice(0, 16) : "Sin datos"}
      </Text>

      {resumen ? (
        <View style={[UI.card, { backgroundColor: COLORS.navy }]}>
          <Text style={{ color: "#93C5FD", fontSize: 11, fontWeight: "700", marginBottom: 8 }}>
            DATOS DESCARGADOS EN EL TELÉFONO (SQLITE OFFLINE)
          </Text>
          <View style={UI.row}>
            <View style={{ flex: 1 }}>
              <Text style={{ color: "#FFF", fontSize: 18, fontWeight: "800" }}>{resumen.productos}</Text>
              <Text style={{ color: "#CBD5E1", fontSize: 11 }}>Productos</Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ color: "#FFF", fontSize: 18, fontWeight: "800" }}>{resumen.listas}</Text>
              <Text style={{ color: "#CBD5E1", fontSize: 11 }}>Listas precio</Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ color: "#FFF", fontSize: 18, fontWeight: "800" }}>
                {Math.max(resumen.clientes, resumen.contactos)}
              </Text>
              <Text style={{ color: "#CBD5E1", fontSize: 11 }}>Clientes</Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ color: resumen.outboxConflictos > 0 ? "#FCA5A5" : "#FFF", fontSize: 18, fontWeight: "800" }}>
                {resumen.outboxPendientes}/{resumen.outboxConflictos}
              </Text>
              <Text style={{ color: "#CBD5E1", fontSize: 11 }}>Pend./Conf.</Text>
            </View>
          </View>
        </View>
      ) : null}

      <TouchableOpacity
        style={[UI.btnPrimary, { opacity: syncing || !isOnline ? 0.65 : 1 }]}
        disabled={syncing || !isOnline}
        onPress={() => void onSyncNow()}
      >
        <Text style={UI.btnTextPrimary}>
          {syncing ? "Sincronizando catálogo y cola..." : "↻ Sincronizar Ahora (Push + Catálogo)"}
        </Text>
      </TouchableOpacity>

      {lastReport ? (
        <View
          style={[
            UI.card,
            { backgroundColor: lastReport.errorMsg ? COLORS.dangerBg : COLORS.successBg },
          ]}
        >
          <Text
            style={{
              fontSize: 12,
              fontWeight: "700",
              color: lastReport.errorMsg ? COLORS.danger : COLORS.success,
            }}
          >
            {lastReport.errorMsg
              ? `Error de sincronización: ${lastReport.errorMsg}`
              : `Sync OK · Enviados: ${lastReport.itemsSync} · Conflictos: ${lastReport.itemsConflicto} · Delta: ${lastReport.deltaAplicado ? "Actualizado" : "No"}`}
          </Text>
        </View>
      ) : null}

      <Text style={[UI.sectionLabel, { marginTop: 6 }]}>Operaciones en Cola ({items.length})</Text>

      {items.length === 0 ? (
        <View style={[UI.card, { alignItems: "center", paddingVertical: 20 }]}>
          <Text style={{ color: COLORS.muted, fontSize: 13 }}>
            No hay operaciones pendientes ni conflictos en cola.
          </Text>
        </View>
      ) : (
        items.map((it) => {
          const guia = describirResolucionConflicto({
            codigoError: it.ultimo_error_codigo,
            mensaje: it.ultimo_error_msg,
            retenidoAprobacion: it.retenido_aprobacion,
          });
          const esConflicto = it.estado === "CONFLICT_USER_ACTION";
          const esSynced = it.estado === "SYNCED";
          const tituloCli = String(
            it.payload.cliente ?? it.payload.contraparte_nombre ?? it.payload.nombre ?? "",
          );
          return (
            <View
              key={it.idempotency_key}
              style={[
                UI.card,
                {
                  borderColor: esConflicto ? COLORS.danger : esSynced ? COLORS.success : COLORS.border,
                  backgroundColor: esConflicto ? COLORS.dangerBg : COLORS.card,
                },
              ]}
            >
              <View style={UI.row}>
                <Text style={{ fontWeight: "800", color: COLORS.text, fontSize: 14 }}>
                  {it.tipo.toUpperCase()}
                  {tituloCli ? ` · ${tituloCli}` : ""}
                </Text>
                <Text
                  style={[
                    UI.badge,
                    {
                      color: esConflicto ? COLORS.danger : esSynced ? COLORS.success : COLORS.primary,
                      backgroundColor: esConflicto ? "#FFF" : esSynced ? COLORS.successBg : COLORS.primarySoft,
                    },
                  ]}
                >
                  {it.estado}
                </Text>
              </View>
              <Text style={{ fontSize: 11, color: COLORS.muted, marginTop: 3 }}>
                Creado: {it.creado_en.slice(0, 16)} · Intentos: {it.intentos}
              </Text>
              {it.ultimo_error_msg ? (
                <Text style={{ color: COLORS.danger, fontSize: 12, marginTop: 4, fontWeight: "600" }}>
                  {guia.titulo}: {it.ultimo_error_msg}
                </Text>
              ) : null}
              {esConflicto ? (
                <View style={{ marginTop: 8 }}>
                  <Text style={{ fontSize: 12, color: COLORS.muted, marginBottom: 6 }}>
                    Acción: {guia.accionRecomendada}
                  </Text>
                  {editKey === it.idempotency_key ? (
                    <>
                      <TextInput
                        style={UI.input}
                        placeholder="Nueva referencia / dato"
                        placeholderTextColor={COLORS.muted}
                        value={editRef}
                        onChangeText={setEditRef}
                      />
                      <TextInput
                        style={UI.input}
                        placeholder="Nuevo banco destino Lubrikca"
                        placeholderTextColor={COLORS.muted}
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
                        <Text style={UI.btnTextOutline}>Corregir</Text>
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
        })
      )}

      <TouchableOpacity style={[UI.btnDanger, { marginTop: 12 }]} onPress={confirmarLogout}>
        <Text style={UI.btnTextPrimary}>Cerrar Sesión y Borrar Datos Locales</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}
