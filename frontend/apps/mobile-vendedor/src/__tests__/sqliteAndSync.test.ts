import { DatabaseSync } from "node:sqlite";
import { describe, expect, it, vi } from "vitest";
import { LubrikcaApiClient, type BootstrapResponse } from "@lubrikca/domain-ts";

vi.mock("expo-secure-store", () => ({
  getItemAsync: async () => null,
  setItemAsync: async () => undefined,
  deleteItemAsync: async () => undefined,
}));

vi.mock("expo-local-authentication", () => ({
  hasHardwareAsync: async () => false,
  isEnrolledAsync: async () => false,
  authenticateAsync: async () => ({ success: true }),
}));

vi.mock("@react-native-community/netinfo", () => ({
  default: { addEventListener: () => () => undefined },
}));

import { MobileSessionStore, type SecureKv } from "../auth/sessionStore";
import { MobileSqliteRepo, OfflineValidationError } from "../db/sqliteRepo";
import type { SqlBind, SqlExecutor } from "../db/types";
import { SyncEngine } from "../sync/syncEngine";

class NodeSqliteExecutor implements SqlExecutor {
  private readonly db = new DatabaseSync(":memory:");

  async exec(sql: string): Promise<void> {
    this.db.exec(sql);
  }

  async run(
    sql: string,
    params: ReadonlyArray<SqlBind> = [],
  ): Promise<{ changes: number; lastInsertRowId: number }> {
    const stmt = this.db.prepare(sql);
    const res = stmt.run(...params);
    return {
      changes: Number(res.changes),
      lastInsertRowId: Number(res.lastInsertRowid),
    };
  }

  async getAll<T = Record<string, unknown>>(
    sql: string,
    params: ReadonlyArray<SqlBind> = [],
  ): Promise<T[]> {
    return this.db.prepare(sql).all(...params) as T[];
  }

  async getFirst<T = Record<string, unknown>>(
    sql: string,
    params: ReadonlyArray<SqlBind> = [],
  ): Promise<T | null> {
    const row = this.db.prepare(sql).get(...params) as T | undefined;
    return row ?? null;
  }

  async withTransaction<T>(fn: () => Promise<T>): Promise<T> {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const res = await fn();
      this.db.exec("COMMIT");
      return res;
    } catch (e) {
      this.db.exec("ROLLBACK");
      throw e;
    }
  }
}

class MemoryKv implements SecureKv {
  private readonly map = new Map<string, string>();
  async getItemAsync(k: string): Promise<string | null> {
    return this.map.get(k) ?? null;
  }
  async setItemAsync(k: string, v: string): Promise<void> {
    this.map.set(k, v);
  }
  async deleteItemAsync(k: string): Promise<void> {
    this.map.delete(k);
  }
}

function makeBootstrap(): BootstrapResponse {
  return {
    modo: "bootstrap",
    cursor: "2026-11-22T12:00:00",
    tasas_vigentes: { tasa_bcv: 920, tasa_binance: 1090, timestamp: "2026-11-22T12:00:00" },
    politica_ventas: {
      autosend_activo: true,
      limite_credito_usd: 1000,
      limite_facturas_pendientes: 5,
      retener_si_vencidas: true,
      dias_mora_max: 7,
    },
    lista_defecto: { lista_id: "21", nombre: "Pago VES", moneda: "VES", ruta_pago: "bcv" },
    listas_precio: [
      { lista_id: "21", nombre: "Pago VES", moneda: "VES", ruta_pago: "bcv" },
      { lista_id: "22", nombre: "Pago USD", moneda: "USD", ruta_pago: "binance" },
    ],
    descuentos_catalogo: [{ codigo: "VOL", nombre: "Volumen 5%", porcentaje: 5 }],
    cuentas_receptoras: [
      {
        id: 1,
        cuenta_id: 1,
        nombre: "Bancamiga Lubrikca",
        tipo_canal: "banco",
        banco_codigo_sudeban: "0172",
        banco_nombre: "Bancamiga",
        numero_cuenta: "01720111223344556677",
        titular_nombre: "LUBRIKCA C.A.",
        titular_rif: "J-40123456-7",
        telefono: "04141112233",
        email_zelle: "",
        binance_id: "",
        moneda: "VES",
        diario_odoo_id: 15,
        diario_odoo_codigo: "BAMI",
        diario_odoo_nombre: "Bancamiga",
        activa: true,
        es_propia: true,
        actualizado_en: "2026-11-22T12:00:00",
      },
      {
        id: 2,
        cuenta_id: 2,
        nombre: "Banco Provincial Lubrikca",
        tipo_canal: "banco",
        banco_codigo_sudeban: "0108",
        banco_nombre: "Banco Provincial",
        numero_cuenta: "01080099887766554433",
        titular_nombre: "LUBRIKCA C.A.",
        titular_rif: "J-40123456-7",
        telefono: "",
        email_zelle: "",
        binance_id: "",
        moneda: "VES",
        diario_odoo_id: 16,
        diario_odoo_codigo: "PROV",
        diario_odoo_nombre: "Banco Provincial",
        activa: true,
        es_propia: true,
        actualizado_en: "2026-11-22T12:00:00",
      },
    ],
    cuentas_terceros: [],
    contactos_maestro: [
      {
        contacto_id: 10,
        odoo_partner_id: 197,
        rol: "cliente",
        nombre: "1080 ZIP MARKET, C.A",
        nombre_normalizado: "1080 zip market",
        rif: "J-50332770-7",
        rif_normalizado: "J503327707",
        telefono: "04141112233",
        vendedor_email: "vendedor@lubrikca.com",
        actualizado_en: "2026-11-22T12:00:00",
      },
    ],
    catalogo: [
      {
        producto_id: "1033",
        codigo: "0761",
        nombre: "LIGA FRENOS DOT3",
        marca: "GLOBAL OIL",
        presentacion: "1X12",
        litros: 3.5,
        precio_usd: 18,
        precio_ves: 24,
        precios_por_lista: { "21": 24, "22": 18 },
        stock_disponible: 10,
        etiqueta: "0761 LIGA FRENOS",
      },
    ],
    clientes: [
      {
        cliente_id: "197",
        partner_odoo_id: 197,
        cliente_nombre: "1080 ZIP MARKET, C.A",
        vendedor_email: "vendedor@lubrikca.com",
        saldo_deudor_usd: 300,
        saldo_favor_usd: 0,
        saldo_bcv_usd: 150,
        saldo_binance_usd: 150,
        ventas_bcv_usd: 200,
        pagos_bcv_usd: 50,
        ventas_binance_usd: 150,
        pagos_binance_usd: 0,
        max_dias_mora: 3,
        tramos: { vigente: 300 },
        politica_credito: { limite_credito_usd: 1000, limite_facturas_pendientes: 5, dias_mora_max: 7 },
      },
    ],
    tumbstones: { cuentas_receptoras_inactivas: [] },
  };
}

describe("MobileSqliteRepo + SyncEngine offline-first", () => {
  it("aplica bootstrap, busca catálogo, aplica tombstones y bloquea pago 0108->0104 offline", async () => {
    const repo = new MobileSqliteRepo(new NodeSqliteExecutor());
    await repo.init();
    await repo.aplicarBootstrap(makeBootstrap());

    const prods = await repo.buscarProductosOffline("0761", "22");
    expect(prods).toHaveLength(1);
    expect(prods[0]?.precio_lista).toBe(18);

    // Tombstone desactiva cuenta Provincial (id=2)
    await repo.aplicarDelta({
      ...makeBootstrap(),
      modo: "delta",
      cursor: "2026-11-22T13:00:00",
      cuentas_receptoras: [],
      tumbstones: { cuentas_receptoras_inactivas: [2] },
    });
    const activas = await repo.obtenerCuentasReceptorasActivas();
    expect(activas.map((c) => c.cuenta_id)).toEqual([1]);

    // Pago offline hacia banco ajeno 0104 Venezolano de Crédito -> bloqueado antes de encolar
    await expect(
      repo.encolarOperacionOutbox({
        tipo: "pago",
        payload: {
          banco_origen: "Banco Provincial",
          banco_destino: "Venezolano de Crédito",
          cuenta_destino: "01040011223344556677",
          monto: 100,
        },
      }),
    ).rejects.toBeInstanceOf(OfflineValidationError);
  });

  it("ejecuta anti-loop en CONFLICT_USER_ACTION (409/422), permite corregir y limpia al revocar (401)", async () => {
    const repo = new MobileSqliteRepo(new NodeSqliteExecutor());
    await repo.init();
    await repo.aplicarBootstrap(makeBootstrap());

    const kv = new MemoryKv();
    const sessionStore = new MobileSessionStore(kv);
    await sessionStore.guardarSesion({
      token_type: "Bearer",
      access_token: "acc-1",
      refresh_token: "ref-1",
      expires_in: 900,
      device_id: "dev-test",
      user: { email: "vendedor@lubrikca.com", nombre: "Vendedor", rol: "ventas", permisos: [] },
    });

    // Encolamos 1 pago válido offline y 1 pago con override para simular rechazo 422 del backend
    const regOkLuegoConflicto = await repo.encolarOperacionOutbox({
      tipo: "pago",
      payload: {
        banco_origen: "Banesco",
        banco_destino: "Bancamiga",
        referencia: "REF-DUP",
        monto: 50,
      },
    });
    const regDestinoMal = await repo.encolarOperacionOutbox({
      tipo: "pago",
      payload: {
        banco_origen: "Provincial",
        banco_destino: "Banco Ajeno",
        diario_odoo_id: 99,
        monto: 40,
      },
      omitirValidacionOfflinePago: true,
    });

    let pushCalls = 0;
    const mockFetch: typeof fetch = async (input, init) => {
      const url = String(input);
      if (url.endsWith("/api/v1/sync/push")) {
        pushCalls += 1;
        const body = JSON.parse(String(init?.body ?? "{}")) as { items: Array<{ idempotency_key: string }> };
        if (pushCalls === 1) {
          return new Response(
            JSON.stringify({
              procesados: 2,
              cursor: "2026-11-22T13:10:00",
              resultados: [
                {
                  idempotency_key: regOkLuegoConflicto.idempotency_key,
                  tipo: "pago",
                  http_status: 409,
                  estado: "CONFLICTO_NEGOCIO",
                  codigo_negocio: "POSIBLE_DUPLICADO",
                  reintentable: false,
                  error: "Referencia REF-DUP ya existe.",
                },
                {
                  idempotency_key: regDestinoMal.idempotency_key,
                  tipo: "pago",
                  http_status: 422,
                  estado: "CONFLICTO_NEGOCIO",
                  codigo_negocio: "DESTINO_NO_PERTENECE_LUBRIKCA",
                  reintentable: false,
                  error: "Cuenta destino no pertenece a Lubrikca.",
                },
              ],
            }),
            { status: 200 },
          );
        }
        // En el 2do push solo debe llegar el item corregido (regOkLuegoConflicto), JAMÁS regDestinoMal (anti-loop)
        expect(body.items.map((i) => i.idempotency_key)).toEqual([regOkLuegoConflicto.idempotency_key]);
        return new Response(
          JSON.stringify({
            procesados: 1,
            cursor: "2026-11-22T13:15:00",
            resultados: [
              {
                idempotency_key: regOkLuegoConflicto.idempotency_key,
                tipo: "pago",
                http_status: 200,
                estado: "OK",
                reintentable: false,
              },
            ],
          }),
          { status: 200 },
        );
      }
      if (url.includes("/api/v1/sync/delta")) {
        return new Response(JSON.stringify({ ...makeBootstrap(), modo: "delta", cursor: "2026-11-22T13:20:00" }), {
          status: 200,
        });
      }
      return new Response("{}", { status: 404 });
    };

    const api = new LubrikcaApiClient({
      baseUrl: "https://cxc.test",
      fetchImpl: mockFetch,
      getAccessToken: async () => (await sessionStore.leerSesion())?.access_token ?? null,
    });
    const engine = new SyncEngine({ api, repo, sessionStore, uploadFetch: mockFetch });

    // Ciclo 1: ambos pasan a CONFLICT_USER_ACTION y regDestinoMal limpia diario_odoo_id
    const rep1 = await engine.sincronizarCompleto("dev-test");
    expect(rep1.itemsConflicto).toBe(2);
    const malDb = await repo.obtenerOutboxPorKey(regDestinoMal.idempotency_key);
    expect(malDb?.estado).toBe("CONFLICT_USER_ACTION");
    expect(malDb?.payload.diario_odoo_id).toBeUndefined();

    // Ciclo 2 sin intervención del usuario -> pushCalls sigue en 1 (CERO reintentos en loop!)
    await engine.sincronizarCompleto("dev-test");
    expect(pushCalls).toBe(1);

    // Vendedor corrige referencia del duplicado y reencola -> Ciclo 3 sincroniza solo ese ítem -> SYNCED
    await repo.corregirYReencolarConflicto(regOkLuegoConflicto.idempotency_key, {
      ...regOkLuegoConflicto.payload,
      referencia: "REF-NUEVA-999",
    });
    const rep3 = await engine.sincronizarCompleto("dev-test");
    expect(pushCalls).toBe(2);
    expect(rep3.itemsSync).toBe(1);
    const fixedDb = await repo.obtenerOutboxPorKey(regOkLuegoConflicto.idempotency_key);
    expect(fixedDb?.estado).toBe("SYNCED");

    // Revocación remota (401 sin refresh) -> limpia SQLite y SecureStore
    const apiRevoked = new LubrikcaApiClient({
      baseUrl: "https://cxc.test",
      fetchImpl: async () => new Response(JSON.stringify({ detail: "Sesión revocada" }), { status: 401 }),
      getAccessToken: async () => "bad-tok",
    });
    const engineRevoked = new SyncEngine({ api: apiRevoked, repo, sessionStore });
    const repRev = await engineRevoked.sincronizarCompleto("dev-test");
    expect(repRev.sesionRevocada).toBe(true);
    expect(await sessionStore.leerSesion()).toBeNull();
    expect(await repo.listarOutbox()).toHaveLength(0);
    expect(await repo.buscarProductosOffline()).toHaveLength(0);
  });

  it("maneja FAILED_RETRY con backoff exponencial y tope de 5 intentos", async () => {
    const repo = new MobileSqliteRepo(new NodeSqliteExecutor());
    await repo.init();
    await repo.aplicarBootstrap(makeBootstrap());
    const sessionStore = new MobileSessionStore(new MemoryKv());

    const reg = await repo.encolarOperacionOutbox({
      tipo: "cotizacion",
      payload: { cliente: "Test", lineas: [] },
    });

    let pushCalls = 0;
    const failingApi = new LubrikcaApiClient({
      baseUrl: "https://cxc.test",
      fetchImpl: async (input) => {
        if (String(input).includes("/api/v1/sync/push")) {
          pushCalls += 1;
        }
        throw new Error("Network down");
      },
    });
    const engine = new SyncEngine({ api: failingApi, repo, sessionStore, maxIntentos: 5 });

    // Si reintentamos 1s despues del primer fallo, backoff (5s) impide envio inmediato
    const t0 = new Date("2026-11-22T12:00:00Z");
    await engine.sincronizarCompleto("dev-test", t0);
    await engine.sincronizarCompleto("dev-test", new Date(t0.getTime() + 1000));
    expect(pushCalls).toBe(1);

    // Ejecutamos ciclos avanzando 10m cada vez: debe topar en 5 intentos totales
    let t = new Date(t0.getTime() + 10 * 60 * 1000);
    for (let i = 0; i < 6; i += 1) {
      await engine.sincronizarCompleto("dev-test", t);
      t = new Date(t.getTime() + 10 * 60 * 1000);
    }

    expect(pushCalls).toBe(5);
    const finalRow = await repo.obtenerOutboxPorKey(reg.idempotency_key);
    expect(finalRow?.estado).toBe("FAILED_RETRY");
    expect(finalRow?.intentos).toBe(5);
  });
});
