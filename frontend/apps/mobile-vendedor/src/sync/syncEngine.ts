import NetInfo from "@react-native-community/netinfo";
import {
  ApiHttpError,
  clasificarErrorRed,
  clasificarResultadoPush,
  type LubrikcaApiClient,
  type PushItem,
  type PushItemResultado,
} from "@lubrikca/domain-ts";

import type { MobileSessionStore } from "../auth/sessionStore";
import type { MobileSqliteRepo } from "../db/sqliteRepo";

export interface SyncReport {
  adjuntosSubidos: number;
  itemsEnviados: number;
  itemsSync: number;
  itemsConflicto: number;
  itemsRetry: number;
  deltaAplicado: boolean;
  sesionRevocada: boolean;
}

export interface AttachmentByteReader {
  readBytes(localUri: string): Promise<Uint8Array>;
}

export class SyncEngine {
  private readonly api: LubrikcaApiClient;
  private readonly repo: MobileSqliteRepo;
  private readonly sessionStore: MobileSessionStore;
  private readonly byteReader?: AttachmentByteReader;
  private readonly uploadFetch: typeof fetch;
  private readonly maxIntentos: number;

  constructor(params: {
    api: LubrikcaApiClient;
    repo: MobileSqliteRepo;
    sessionStore: MobileSessionStore;
    byteReader?: AttachmentByteReader;
    uploadFetch?: typeof fetch;
    maxIntentos?: number;
  }) {
    this.api = params.api;
    this.repo = params.repo;
    this.sessionStore = params.sessionStore;
    this.byteReader = params.byteReader;
    this.uploadFetch = params.uploadFetch ?? globalThis.fetch.bind(globalThis);
    this.maxIntentos = params.maxIntentos ?? 5;
  }

  private async manejarErrorHttp(err: unknown): Promise<boolean> {
    if (err instanceof ApiHttpError && (err.status === 401 || err.status === 403)) {
      await this.sessionStore.limpiarTokens();
      await this.repo.wipeAllTables();
      return true;
    }
    return false;
  }

  async subirAdjuntosPendientes(): Promise<number> {
    if (!this.byteReader) return 0;
    const pendientes = await this.repo.listarAdjuntosPendientes();
    let subidos = 0;
    for (const adj of pendientes) {
      const presign = await this.api.presignAdjunto({
        sha256: adj.sha256,
        mime: adj.mime,
        bytes: adj.bytes,
        nombre_archivo: adj.outbox_key,
      });
      let adjuntoId: number | undefined = presign.adjunto_id ?? undefined;
      let storageKey = presign.storage_key;
      if (!presign.ya_existe && presign.upload_url) {
        const bytes = await this.byteReader.readBytes(adj.local_uri);
        const targetUrl = presign.upload_url.startsWith("/")
          ? `${this.api.baseUrl}${presign.upload_url}`
          : presign.upload_url;
        const putRes = await this.uploadFetch(targetUrl, {
          method: presign.upload_method || "PUT",
          headers: { "Content-Type": adj.mime },
          body: new Uint8Array(bytes).buffer,
        });
        if (!putRes.ok) {
          throw new Error(`Error subiendo comprobante (${putRes.status})`);
        }
        try {
          const parsed = (await putRes.json()) as {
            adjunto_id?: number;
            storage_key?: string;
          } | null;
          if (typeof parsed?.adjunto_id === "number") adjuntoId = parsed.adjunto_id;
          if (typeof parsed?.storage_key === "string" && parsed.storage_key) {
            storageKey = parsed.storage_key;
          }
        } catch {
          // R2/S3 devuelve cuerpo vacío en 200 OK y adjunto_id ya vino en presign
        }
      }
      await this.repo.marcarAdjuntoSubido(
        adj.adjunto_local_id,
        adj.outbox_key,
        storageKey,
        adjuntoId,
      );
      subidos += 1;
    }
    return subidos;
  }

  async sincronizarCompleto(deviceId: string, ahora: Date = new Date()): Promise<SyncReport> {
    const rep: SyncReport = {
      adjuntosSubidos: 0,
      itemsEnviados: 0,
      itemsSync: 0,
      itemsConflicto: 0,
      itemsRetry: 0,
      deltaAplicado: false,
      sesionRevocada: false,
    };

    try {
      rep.adjuntosSubidos = await this.subirAdjuntosPendientes();

      const cola = await this.repo.listarPendientesParaSync(ahora.toISOString(), this.maxIntentos);
      if (cola.length > 0) {
        rep.itemsEnviados = cola.length;
        const pushItems: PushItem[] = cola.map((it) => ({
          idempotency_key: it.idempotency_key,
          tipo: it.tipo,
          payload: it.payload,
        }));

        try {
          const resp = await this.api.syncPush(deviceId, pushItems);
          const porKey = new Map<string, PushItemResultado>();
          for (const r of resp.resultados ?? []) {
            porKey.set(r.idempotency_key, r);
          }

          for (const orig of cola) {
            const r = porKey.get(orig.idempotency_key);
            if (!r) continue;
            const cls = clasificarResultadoPush(r, orig.intentos, this.maxIntentos);
            const proximoIso =
              cls.esperaSegundos !== null
                ? new Date(ahora.getTime() + cls.esperaSegundos * 1000).toISOString()
                : null;
            let payloadNext = orig.payload;
            if (cls.limpiarDiarioSugerido) {
              const { diario_odoo_id: _d, diario_odoo: _dn, banco_destino: _b, ...resto } = orig.payload;
              payloadNext = resto;
            }
            await this.repo.actualizarEstadoOutbox({
              idempotencyKey: orig.idempotency_key,
              estado: cls.nuevoEstado,
              intentos: orig.intentos + 1,
              ultimoErrorCodigo: cls.codigoError || null,
              ultimoErrorMsg: cls.mensaje || null,
              proximoReintentoEn: proximoIso,
              retenidoAprobacion: cls.retenidoAprobacion,
              payloadActualizado: payloadNext,
              respuesta: r as unknown as Record<string, unknown>,
            });
            if (cls.nuevoEstado === "SYNCED") rep.itemsSync += 1;
            else if (cls.nuevoEstado === "CONFLICT_USER_ACTION") rep.itemsConflicto += 1;
            else rep.itemsRetry += 1;
          }
        } catch (err) {
          if (await this.manejarErrorHttp(err)) {
            rep.sesionRevocada = true;
            return rep;
          }
          for (const orig of cola) {
            const cls = clasificarErrorRed(err, orig.intentos, this.maxIntentos);
            const proximoIso =
              cls.esperaSegundos !== null
                ? new Date(ahora.getTime() + cls.esperaSegundos * 1000).toISOString()
                : null;
            await this.repo.actualizarEstadoOutbox({
              idempotencyKey: orig.idempotency_key,
              estado: cls.nuevoEstado,
              intentos: orig.intentos + 1,
              ultimoErrorCodigo: cls.codigoError,
              ultimoErrorMsg: cls.mensaje,
              proximoReintentoEn: proximoIso,
            });
            rep.itemsRetry += 1;
          }
          return rep;
        }
      }

      const cursor = await this.repo.obtenerCursor();
      const snap = cursor ? await this.api.syncDelta(cursor) : await this.api.syncBootstrap();
      await this.repo.aplicarDelta(snap);
      rep.deltaAplicado = true;
    } catch (err) {
      if (await this.manejarErrorHttp(err)) {
        rep.sesionRevocada = true;
      }
    }
    return rep;
  }

  async registrarPushToken(deviceId: string, pushToken: string): Promise<boolean> {
    if (!deviceId.trim() || !pushToken.trim()) return false;
    try {
      await this.api.registrarPushToken(deviceId, pushToken);
      return true;
    } catch (err) {
      await this.manejarErrorHttp(err);
      return false;
    }
  }

  adjuntarListenerRed(deviceId: string, onSynced?: (report: SyncReport) => void): () => void {
    let previoOnline = false;
    return NetInfo.addEventListener((state) => {
      const online = Boolean(state.isConnected && state.isInternetReachable !== false);
      if (online && !previoOnline) {
        void this.sincronizarCompleto(deviceId).then((r) => onSynced?.(r));
      }
      previoOnline = online;
    });
  }
}
