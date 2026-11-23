import {
  detectarDuplicadosContactoOffline,
  generarUuidV7,
  validarDestinoPagoOffline,
  type BootstrapResponse,
  type ClienteCartera,
  type ContactoMaestroResumen,
  type CuentaReceptora,
  type CuentaTercero,
  type ListaPrecio,
  type OutboxEstado,
  type PoliticaVentasGlobal,
  type ProductoCatalogo,
  type PushTipo,
  type TasasVigentes,
} from "@lubrikca/domain-ts";

import { ALL_TABLES, SCHEMA_DDL_STATEMENTS } from "./schema";
import type { SqlExecutor } from "./types";

export interface OutboxRegistro {
  idempotency_key: string;
  tipo: PushTipo;
  payload: Record<string, unknown>;
  adjunto_local_uri: string | null;
  estado: OutboxEstado;
  intentos: number;
  ultimo_error_codigo: string | null;
  ultimo_error_msg: string | null;
  proximo_reintento_en: string | null;
  retenido_aprobacion: boolean;
  respuesta: Record<string, unknown> | null;
  creado_en: string;
  actualizado_en: string;
}

export interface AdjuntoPendienteRegistro {
  adjunto_local_id: string;
  outbox_key: string;
  local_uri: string;
  sha256: string;
  mime: string;
  bytes: number;
  storage_key: string | null;
  adjunto_id: number | null;
  estado: "PENDING" | "UPLOADED" | "FAILED";
  creado_en: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

export class OfflineValidationError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = "OfflineValidationError";
    this.code = code;
  }
}

export class MobileSqliteRepo {
  private readonly db: SqlExecutor;

  constructor(db: SqlExecutor) {
    this.db = db;
  }

  async init(): Promise<void> {
    for (const stmt of SCHEMA_DDL_STATEMENTS) {
      await this.db.exec(stmt);
    }
  }

  async obtenerCursor(): Promise<string> {
    const row = await this.db.getFirst<{ valor: string }>(
      "SELECT valor FROM sync_meta WHERE clave = ?",
      ["cursor"],
    );
    return row?.valor ?? "";
  }

  async guardarCursor(cursor: string): Promise<void> {
    const ts = nowIso();
    await this.db.run(
      `INSERT INTO sync_meta (clave, valor, actualizado_en)
       VALUES (?, ?, ?)
       ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor, actualizado_en = excluded.actualizado_en`,
      ["cursor", cursor, ts],
    );
  }

  async aplicarBootstrap(snap: BootstrapResponse): Promise<void> {
    const ts = nowIso();
    await this.db.withTransaction(async () => {
      if (snap.tasas_vigentes) {
        await this.db.run(
          `INSERT INTO tasas_cambio (id, tasa_bcv, tasa_binance, tasa_bcv_euro, timestamp)
           VALUES (1, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             tasa_bcv = excluded.tasa_bcv,
             tasa_binance = excluded.tasa_binance,
             tasa_bcv_euro = excluded.tasa_bcv_euro,
             timestamp = excluded.timestamp`,
          [
            snap.tasas_vigentes.tasa_bcv,
            snap.tasas_vigentes.tasa_binance,
            snap.tasas_vigentes.tasa_bcv_euro ?? null,
            snap.tasas_vigentes.timestamp ?? ts,
          ],
        );
      }

      if (snap.politica_ventas) {
        await this.db.run(
          `INSERT INTO politica_ventas_cache (id, json_data)
           VALUES (1, ?)
           ON CONFLICT(id) DO UPDATE SET json_data = excluded.json_data`,
          [JSON.stringify(snap.politica_ventas)],
        );
      }

      if (snap.modo === "bootstrap") {
        await this.db.run("DELETE FROM listas_precio");
      }
      for (const l of snap.listas_precio ?? []) {
        const esDef = snap.lista_defecto?.lista_id === l.lista_id ? 1 : 0;
        await this.db.run(
          `INSERT INTO listas_precio (lista_id, nombre, moneda, ruta_pago, es_defecto)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(lista_id) DO UPDATE SET
             nombre = excluded.nombre,
             moneda = excluded.moneda,
             ruta_pago = excluded.ruta_pago,
             es_defecto = excluded.es_defecto`,
          [String(l.lista_id), l.nombre, l.moneda, l.ruta_pago, esDef],
        );
      }

      for (const d of snap.descuentos_catalogo ?? []) {
        await this.db.run(
          `INSERT INTO reglas_descuento (codigo, nombre, json_data)
           VALUES (?, ?, ?)
           ON CONFLICT(codigo) DO UPDATE SET nombre = excluded.nombre, json_data = excluded.json_data`,
          [d.codigo, d.nombre, JSON.stringify(d)],
        );
      }

      if (snap.modo === "bootstrap") {
        await this.db.run("DELETE FROM productos");
      }
      for (const p of snap.catalogo ?? []) {
        await this.db.run(
          `INSERT INTO productos (producto_id, codigo, nombre, marca, presentacion, litros, precio_usd, precio_ves, precios_por_lista_json, etiqueta)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(producto_id) DO UPDATE SET
             codigo = excluded.codigo,
             nombre = excluded.nombre,
             marca = excluded.marca,
             presentacion = excluded.presentacion,
             litros = excluded.litros,
             precio_usd = excluded.precio_usd,
             precio_ves = excluded.precio_ves,
             precios_por_lista_json = excluded.precios_por_lista_json,
             etiqueta = excluded.etiqueta`,
          [
            String(p.producto_id),
            p.codigo ?? "",
            p.nombre ?? "",
            p.marca ?? "",
            p.presentacion ?? "",
            p.litros ?? 0,
            p.precio_usd ?? 0,
            p.precio_ves ?? 0,
            JSON.stringify(p.precios_por_lista ?? {}),
            p.etiqueta ?? "",
          ],
        );
        await this.db.run(
          `INSERT INTO stock_cache (producto_id, free_qty, actualizado_en)
           VALUES (?, ?, ?)
           ON CONFLICT(producto_id) DO UPDATE SET free_qty = excluded.free_qty, actualizado_en = excluded.actualizado_en`,
          [String(p.producto_id), p.stock_disponible ?? null, snap.cursor || ts],
        );
      }

      if (snap.modo === "bootstrap") {
        await this.db.run("DELETE FROM clientes");
        await this.db.run("DELETE FROM saldos_cliente");
      }
      for (const c of snap.clientes ?? []) {
        await this.db.run(
          `INSERT INTO clientes (cliente_id, partner_odoo_id, cliente_nombre, vendedor_email, max_dias_mora, politica_credito_json, ordenes_pendientes_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(cliente_id) DO UPDATE SET
             partner_odoo_id = excluded.partner_odoo_id,
             cliente_nombre = excluded.cliente_nombre,
             vendedor_email = excluded.vendedor_email,
             max_dias_mora = excluded.max_dias_mora,
             politica_credito_json = excluded.politica_credito_json,
             ordenes_pendientes_json = excluded.ordenes_pendientes_json`,
          [
            String(c.cliente_id),
            c.partner_odoo_id ?? null,
            c.cliente_nombre ?? "",
            c.vendedor_email ?? "",
            c.max_dias_mora ?? 0,
            JSON.stringify(c.politica_credito ?? {}),
            JSON.stringify(c.ordenes_pendientes ?? []),
          ],
        );
        await this.db.run(
          `INSERT INTO saldos_cliente (cliente_id, ruta_pago, ventas_usd, pagos_usd, saldo_usd, saldo_favor_usd)
           VALUES (?, 'bcv', ?, ?, ?, ?)
           ON CONFLICT(cliente_id, ruta_pago) DO UPDATE SET
             ventas_usd = excluded.ventas_usd,
             pagos_usd = excluded.pagos_usd,
             saldo_usd = excluded.saldo_usd,
             saldo_favor_usd = excluded.saldo_favor_usd`,
          [
            String(c.cliente_id),
            c.ventas_bcv_usd ?? 0,
            c.pagos_bcv_usd ?? 0,
            c.saldo_bcv_usd ?? 0,
            c.saldo_favor_usd ?? 0,
          ],
        );
        await this.db.run(
          `INSERT INTO saldos_cliente (cliente_id, ruta_pago, ventas_usd, pagos_usd, saldo_usd, saldo_favor_usd)
           VALUES (?, 'binance', ?, ?, ?, ?)
           ON CONFLICT(cliente_id, ruta_pago) DO UPDATE SET
             ventas_usd = excluded.ventas_usd,
             pagos_usd = excluded.pagos_usd,
             saldo_usd = excluded.saldo_usd,
             saldo_favor_usd = excluded.saldo_favor_usd`,
          [
            String(c.cliente_id),
            c.ventas_binance_usd ?? 0,
            c.pagos_binance_usd ?? 0,
            c.saldo_binance_usd ?? 0,
            0,
          ],
        );
      }

      if (snap.modo === "bootstrap") {
        await this.db.run("DELETE FROM cuentas_receptoras");
      }
      for (const cr of snap.cuentas_receptoras ?? []) {
        await this.db.run(
          `INSERT INTO cuentas_receptoras (
             cuenta_id, nombre, tipo_canal, banco_codigo_sudeban, banco_nombre, numero_cuenta,
             titular_nombre, titular_rif, telefono, email_zelle, binance_id, moneda,
             diario_odoo_id, diario_odoo_codigo, diario_odoo_nombre, activa, es_propia, actualizado_en
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(cuenta_id) DO UPDATE SET
             nombre = excluded.nombre,
             tipo_canal = excluded.tipo_canal,
             banco_codigo_sudeban = excluded.banco_codigo_sudeban,
             banco_nombre = excluded.banco_nombre,
             numero_cuenta = excluded.numero_cuenta,
             titular_nombre = excluded.titular_nombre,
             titular_rif = excluded.titular_rif,
             telefono = excluded.telefono,
             email_zelle = excluded.email_zelle,
             binance_id = excluded.binance_id,
             moneda = excluded.moneda,
             diario_odoo_id = excluded.diario_odoo_id,
             diario_odoo_codigo = excluded.diario_odoo_codigo,
             diario_odoo_nombre = excluded.diario_odoo_nombre,
             activa = excluded.activa,
             es_propia = excluded.es_propia,
             actualizado_en = excluded.actualizado_en`,
          [
            cr.cuenta_id,
            cr.nombre,
            cr.tipo_canal,
            cr.banco_codigo_sudeban ?? "",
            cr.banco_nombre ?? "",
            cr.numero_cuenta ?? "",
            cr.titular_nombre ?? "",
            cr.titular_rif ?? "",
            cr.telefono ?? "",
            cr.email_zelle ?? "",
            cr.binance_id ?? "",
            cr.moneda ?? "VES",
            cr.diario_odoo_id ?? null,
            cr.diario_odoo_codigo ?? "",
            cr.diario_odoo_nombre ?? "",
            cr.activa ? 1 : 0,
            cr.es_propia ? 1 : 0,
            cr.actualizado_en ?? ts,
          ],
        );
      }

      for (const inactId of snap.tumbstones?.cuentas_receptoras_inactivas ?? []) {
        await this.db.run("UPDATE cuentas_receptoras SET activa = 0 WHERE cuenta_id = ?", [
          inactId,
        ]);
      }

      if (snap.modo === "bootstrap") {
        await this.db.run("DELETE FROM cuentas_terceros");
      }
      for (const ct of snap.cuentas_terceros ?? []) {
        const tid = Number(ct.cuenta_tercero_id ?? ct.id ?? 0);
        if (!tid) continue;
        await this.db.run(
          `INSERT INTO cuentas_terceros (cuenta_tercero_id, titular, alias, email, moneda, activa)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(cuenta_tercero_id) DO UPDATE SET
             titular = excluded.titular,
             alias = excluded.alias,
             email = excluded.email,
             moneda = excluded.moneda,
             activa = excluded.activa`,
          [tid, ct.titular, ct.alias ?? "", ct.email ?? "", ct.moneda ?? "USD", ct.activa ? 1 : 0],
        );
      }

      if (snap.modo === "bootstrap") {
        await this.db.run("DELETE FROM contactos_maestro");
      }
      for (const cm of snap.contactos_maestro ?? []) {
        const key = cm.contacto_id ? `c:${cm.contacto_id}` : `p:${cm.odoo_partner_id ?? cm.nombre_normalizado}`;
        await this.db.run(
          `INSERT INTO contactos_maestro (
             contacto_key, contacto_id, odoo_partner_id, rol, nombre, nombre_normalizado,
             rif, rif_normalizado, telefono, vendedor_email, actualizado_en
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(contacto_key) DO UPDATE SET
             nombre = excluded.nombre,
             nombre_normalizado = excluded.nombre_normalizado,
             rif = excluded.rif,
             rif_normalizado = excluded.rif_normalizado,
             telefono = excluded.telefono,
             vendedor_email = excluded.vendedor_email,
             actualizado_en = excluded.actualizado_en`,
          [
            key,
            cm.contacto_id ?? null,
            cm.odoo_partner_id ?? null,
            cm.rol ?? "cliente",
            cm.nombre,
            cm.nombre_normalizado ?? "",
            cm.rif ?? "",
            cm.rif_normalizado ?? "",
            cm.telefono ?? "",
            cm.vendedor_email ?? "",
            cm.actualizado_en ?? null,
          ],
        );
      }

      if (snap.cursor) {
        await this.guardarCursor(snap.cursor);
      }
    });
  }

  async aplicarDelta(delta: BootstrapResponse): Promise<void> {
    await this.aplicarBootstrap(delta);
  }

  async buscarProductosOffline(
    query: string = "",
    listaId?: string,
  ): Promise<Array<ProductoCatalogo & { stock_actualizado_en: string; precio_lista: number }>> {
    const q = query.trim().toLowerCase();
    const rows = await this.db.getAll<{
      producto_id: string;
      codigo: string;
      nombre: string;
      marca: string;
      presentacion: string;
      litros: number;
      precio_usd: number;
      precio_ves: number;
      precios_por_lista_json: string;
      etiqueta: string;
      free_qty: number | null;
      stock_actualizado_en: string | null;
    }>(
      `SELECT p.*, s.free_qty, s.actualizado_en AS stock_actualizado_en
       FROM productos p
       LEFT JOIN stock_cache s ON s.producto_id = p.producto_id
       ORDER BY p.nombre ASC`,
    );

    const out: Array<ProductoCatalogo & { stock_actualizado_en: string; precio_lista: number }> = [];
    for (const r of rows) {
      if (q) {
        const hay = `${r.codigo} ${r.nombre} ${r.marca} ${r.presentacion}`.toLowerCase();
        if (!hay.includes(q)) continue;
      }
      const preciosLista = JSON.parse(r.precios_por_lista_json || "{}") as Record<string, number>;
      const precioLista = listaId && preciosLista[listaId] !== undefined ? preciosLista[listaId] : r.precio_usd;
      out.push({
        producto_id: r.producto_id,
        codigo: r.codigo,
        nombre: r.nombre,
        marca: r.marca,
        presentacion: r.presentacion,
        litros: Number(r.litros),
        precio_usd: Number(r.precio_usd),
        precio_ves: Number(r.precio_ves),
        precios_por_lista: preciosLista,
        stock_disponible: r.free_qty !== null ? Number(r.free_qty) : null,
        etiqueta: r.etiqueta,
        stock_actualizado_en: r.stock_actualizado_en ?? "",
        precio_lista: Number(precioLista ?? r.precio_usd),
      });
    }
    return out;
  }

  async actualizarStockEnVivo(
    items: ReadonlyArray<{ producto_id: string; stock_disponible: number | null; actualizado_en?: string }>,
  ): Promise<void> {
    const ts = nowIso();
    await this.db.withTransaction(async () => {
      for (const it of items) {
        await this.db.run(
          `INSERT INTO stock_cache (producto_id, free_qty, actualizado_en)
           VALUES (?, ?, ?)
           ON CONFLICT(producto_id) DO UPDATE SET free_qty = excluded.free_qty, actualizado_en = excluded.actualizado_en`,
          [String(it.producto_id), it.stock_disponible ?? null, it.actualizado_en ?? ts],
        );
      }
    });
  }

  async listarClientesCarteraOffline(query: string = ""): Promise<ClienteCartera[]> {
    const q = query.trim().toLowerCase();
    const rows = await this.db.getAll<{
      cliente_id: string;
      partner_odoo_id: number | null;
      cliente_nombre: string;
      vendedor_email: string;
      max_dias_mora: number;
      politica_credito_json: string;
      ordenes_pendientes_json: string;
    }>("SELECT * FROM clientes ORDER BY cliente_nombre ASC");

    const saldos = await this.db.getAll<{
      cliente_id: string;
      ruta_pago: string;
      ventas_usd: number;
      pagos_usd: number;
      saldo_usd: number;
      saldo_favor_usd: number;
    }>("SELECT * FROM saldos_cliente");

    const porCli = new Map<string, Record<string, typeof saldos[number]>>();
    for (const s of saldos) {
      const m = porCli.get(s.cliente_id) ?? {};
      m[s.ruta_pago] = s;
      porCli.set(s.cliente_id, m);
    }

    const out: ClienteCartera[] = [];
    for (const r of rows) {
      if (q && !r.cliente_nombre.toLowerCase().includes(q) && !r.cliente_id.includes(q)) {
        continue;
      }
      const sMap = porCli.get(r.cliente_id) ?? {};
      const sb = sMap.bcv;
      const sn = sMap.binance;
      const saldoBcv = Number(sb?.saldo_usd ?? 0);
      const saldoBin = Number(sn?.saldo_usd ?? 0);
      out.push({
        cliente_id: r.cliente_id,
        partner_odoo_id: r.partner_odoo_id,
        cliente_nombre: r.cliente_nombre,
        vendedor_email: r.vendedor_email,
        saldo_deudor_usd: Number((saldoBcv + saldoBin).toFixed(2)),
        saldo_favor_usd: Number(sb?.saldo_favor_usd ?? 0),
        saldo_bcv_usd: saldoBcv,
        saldo_binance_usd: saldoBin,
        ventas_bcv_usd: Number(sb?.ventas_usd ?? 0),
        pagos_bcv_usd: Number(sb?.pagos_usd ?? 0),
        ventas_binance_usd: Number(sn?.ventas_usd ?? 0),
        pagos_binance_usd: Number(sn?.pagos_usd ?? 0),
        max_dias_mora: Number(r.max_dias_mora),
        tramos: {},
        politica_credito: JSON.parse(r.politica_credito_json || "{}"),
        ordenes_pendientes: JSON.parse(r.ordenes_pendientes_json || "[]"),
      });
    }
    return out;
  }

  async obtenerCuentasReceptorasActivas(): Promise<CuentaReceptora[]> {
    const rows = await this.db.getAll<Record<string, unknown>>(
      "SELECT * FROM cuentas_receptoras WHERE activa = 1 AND es_propia = 1 ORDER BY nombre ASC",
    );
    return rows.map((r) => ({
      id: Number(r.cuenta_id),
      cuenta_id: Number(r.cuenta_id),
      nombre: String(r.nombre),
      tipo_canal: String(r.tipo_canal),
      banco_codigo_sudeban: String(r.banco_codigo_sudeban ?? ""),
      banco_nombre: String(r.banco_nombre ?? ""),
      numero_cuenta: String(r.numero_cuenta ?? ""),
      titular_nombre: String(r.titular_nombre ?? ""),
      titular_rif: String(r.titular_rif ?? ""),
      telefono: String(r.telefono ?? ""),
      email_zelle: String(r.email_zelle ?? ""),
      binance_id: String(r.binance_id ?? ""),
      moneda: String(r.moneda ?? "VES"),
      diario_odoo_id: r.diario_odoo_id !== null ? Number(r.diario_odoo_id) : null,
      diario_odoo_codigo: String(r.diario_odoo_codigo ?? ""),
      diario_odoo_nombre: String(r.diario_odoo_nombre ?? ""),
      activa: Boolean(r.activa),
      es_propia: Boolean(r.es_propia),
      actualizado_en: String(r.actualizado_en ?? ""),
    }));
  }

  async obtenerCuentasTercerosActivas(): Promise<CuentaTercero[]> {
    const rows = await this.db.getAll<Record<string, unknown>>(
      "SELECT * FROM cuentas_terceros WHERE activa = 1 ORDER BY titular ASC",
    );
    return rows.map((r) => ({
      id: Number(r.cuenta_tercero_id),
      cuenta_tercero_id: Number(r.cuenta_tercero_id),
      titular: String(r.titular),
      alias: String(r.alias ?? ""),
      email: String(r.email ?? ""),
      moneda: String(r.moneda ?? "USD"),
      activa: Boolean(r.activa),
    }));
  }

  async obtenerContactosMaestro(): Promise<ContactoMaestroResumen[]> {
    const rows = await this.db.getAll<Record<string, unknown>>(
      "SELECT * FROM contactos_maestro ORDER BY nombre ASC",
    );
    return rows.map((r) => ({
      contacto_id: r.contacto_id !== null ? Number(r.contacto_id) : null,
      odoo_partner_id: r.odoo_partner_id !== null ? Number(r.odoo_partner_id) : null,
      rol: String(r.rol ?? "cliente"),
      nombre: String(r.nombre),
      nombre_normalizado: String(r.nombre_normalizado ?? ""),
      rif: String(r.rif ?? ""),
      rif_normalizado: String(r.rif_normalizado ?? ""),
      telefono: String(r.telefono ?? ""),
      vendedor_email: String(r.vendedor_email ?? ""),
      actualizado_en: r.actualizado_en ? String(r.actualizado_en) : null,
    }));
  }

  async obtenerListasPrecio(): Promise<ListaPrecio[]> {
    const rows = await this.db.getAll<Record<string, unknown>>(
      "SELECT * FROM listas_precio ORDER BY es_defecto DESC, nombre ASC",
    );
    return rows.map((r) => ({
      lista_id: String(r.lista_id),
      nombre: String(r.nombre),
      moneda: String(r.moneda),
      ruta_pago: String(r.ruta_pago),
      es_defecto: Boolean(r.es_defecto),
    }));
  }

  async obtenerTasasVigentes(): Promise<TasasVigentes | null> {
    const r = await this.db.getFirst<{
      tasa_bcv: number;
      tasa_binance: number;
      tasa_bcv_euro: number | null;
      timestamp: string | null;
    }>("SELECT tasa_bcv, tasa_binance, tasa_bcv_euro, timestamp FROM tasas_cambio WHERE id = 1");
    if (!r) return null;
    return {
      tasa_bcv: Number(r.tasa_bcv),
      tasa_binance: Number(r.tasa_binance),
      tasa_bcv_euro: r.tasa_bcv_euro !== null ? Number(r.tasa_bcv_euro) : null,
      timestamp: r.timestamp,
    };
  }

  async obtenerPoliticaVentas(): Promise<PoliticaVentasGlobal | null> {
    const r = await this.db.getFirst<{ json_data: string }>(
      "SELECT json_data FROM politica_ventas_cache WHERE id = 1",
    );
    return r ? (JSON.parse(r.json_data) as PoliticaVentasGlobal) : null;
  }

  async encolarOperacionOutbox(input: {
    tipo: PushTipo;
    payload: Record<string, unknown>;
    adjuntoLocalUri?: string | null;
    adjuntoMeta?: { sha256: string; mime: string; bytes: number };
    omitirValidacionOfflinePago?: boolean;
  }): Promise<OutboxRegistro> {
    if (input.tipo === "pago" && !input.omitirValidacionOfflinePago) {
      const cuentas = await this.obtenerCuentasReceptorasActivas();
      const terceros = await this.obtenerCuentasTercerosActivas();
      const val = validarDestinoPagoOffline(
        {
          banco_origen: input.payload.banco_origen as string | undefined,
          banco_destino: input.payload.banco_destino as string | undefined,
          cuenta_destino: input.payload.cuenta_destino as string | undefined,
          rif_destino: input.payload.rif_destino as string | undefined,
          telefono_destino: input.payload.telefono_destino as string | undefined,
          email_destino: input.payload.email_destino as string | undefined,
          titular_destino: input.payload.titular_destino as string | undefined,
          metodo_pago: input.payload.metodo_pago as string | undefined,
          es_zelle_tercero: Boolean(input.payload.es_zelle_tercero),
          cuenta_tercero_id: input.payload.cuenta_tercero_id as number | undefined,
        },
        cuentas,
        terceros,
      );
      if (val.bloqueante) {
        throw new OfflineValidationError(val.estado, val.mensaje);
      }
    }

    if (input.tipo === "contacto" && !input.payload.forzar_duplicado) {
      const existentes = await this.obtenerContactosMaestro();
      const dups = detectarDuplicadosContactoOffline(
        {
          nombre: String(input.payload.nombre ?? ""),
          rif: input.payload.rif as string | undefined,
          telefono: input.payload.telefono as string | undefined,
        },
        existentes,
      );
      if (dups.some((d) => d.bloqueante)) {
        throw new OfflineValidationError(
          "CONTACTO_DUPLICADO",
          `El contacto coincide por RIF con ${dups[0]?.contacto.nombre}.`,
        );
      }
    }

    const key = generarUuidV7();
    const ts = nowIso();
    const payloadStr = JSON.stringify(input.payload);
    await this.db.withTransaction(async () => {
      await this.db.run(
        `INSERT INTO outbox_operaciones (
           idempotency_key, tipo, payload_json, adjunto_local_uri, estado, intentos,
           ultimo_error_codigo, ultimo_error_msg, proximo_reintento_en, retenido_aprobacion,
           respuesta_json, creado_en, actualizado_en
         ) VALUES (?, ?, ?, ?, 'PENDING', 0, NULL, NULL, NULL, 0, NULL, ?, ?)`,
        [key, input.tipo, payloadStr, input.adjuntoLocalUri ?? null, ts, ts],
      );
      await this.db.run(
        `INSERT INTO borradores_locales (borrador_id, tipo, titulo, payload_json, estado, actualizado_en)
         VALUES (?, ?, ?, ?, 'PENDING', ?)`,
        [
          key,
          input.tipo,
          String(input.payload.cliente ?? input.payload.nombre ?? input.payload.referencia ?? input.tipo),
          payloadStr,
          ts,
        ],
      );
      if (input.adjuntoLocalUri && input.adjuntoMeta) {
        await this.db.run(
          `INSERT INTO adjuntos_pendientes (
             adjunto_local_id, outbox_key, local_uri, sha256, mime, bytes, storage_key, adjunto_id, estado, creado_en
           ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 'PENDING', ?)`,
          [
            generarUuidV7(),
            key,
            input.adjuntoLocalUri,
            input.adjuntoMeta.sha256,
            input.adjuntoMeta.mime,
            input.adjuntoMeta.bytes,
            ts,
          ],
        );
      }
    });

    const creado = await this.obtenerOutboxPorKey(key);
    if (!creado) throw new Error("No se pudo leer la operación recién encolada.");
    return creado;
  }

  private mapOutboxRow(r: Record<string, unknown>): OutboxRegistro {
    return {
      idempotency_key: String(r.idempotency_key),
      tipo: String(r.tipo) as PushTipo,
      payload: JSON.parse(String(r.payload_json || "{}")),
      adjunto_local_uri: r.adjunto_local_uri ? String(r.adjunto_local_uri) : null,
      estado: String(r.estado) as OutboxEstado,
      intentos: Number(r.intentos ?? 0),
      ultimo_error_codigo: r.ultimo_error_codigo ? String(r.ultimo_error_codigo) : null,
      ultimo_error_msg: r.ultimo_error_msg ? String(r.ultimo_error_msg) : null,
      proximo_reintento_en: r.proximo_reintento_en ? String(r.proximo_reintento_en) : null,
      retenido_aprobacion: Boolean(r.retenido_aprobacion),
      respuesta: r.respuesta_json ? JSON.parse(String(r.respuesta_json)) : null,
      creado_en: String(r.creado_en),
      actualizado_en: String(r.actualizado_en),
    };
  }

  async obtenerOutboxPorKey(idempotencyKey: string): Promise<OutboxRegistro | null> {
    const r = await this.db.getFirst<Record<string, unknown>>(
      "SELECT * FROM outbox_operaciones WHERE idempotency_key = ?",
      [idempotencyKey],
    );
    return r ? this.mapOutboxRow(r) : null;
  }

  async listarOutbox(estado?: OutboxEstado): Promise<OutboxRegistro[]> {
    const rows = estado
      ? await this.db.getAll<Record<string, unknown>>(
          "SELECT * FROM outbox_operaciones WHERE estado = ? ORDER BY creado_en ASC",
          [estado],
        )
      : await this.db.getAll<Record<string, unknown>>(
          "SELECT * FROM outbox_operaciones ORDER BY creado_en ASC",
        );
    return rows.map((r) => this.mapOutboxRow(r));
  }

  async listarPendientesParaSync(ahoraIso: string = nowIso(), maxIntentos: number = 5): Promise<OutboxRegistro[]> {
    const rows = await this.db.getAll<Record<string, unknown>>(
      `SELECT * FROM outbox_operaciones
       WHERE estado = 'PENDING'
          OR (estado = 'FAILED_RETRY' AND intentos < ? AND (proximo_reintento_en IS NULL OR proximo_reintento_en <= ?))
       ORDER BY creado_en ASC`,
      [maxIntentos, ahoraIso],
    );
    return rows.map((r) => this.mapOutboxRow(r));
  }

  async actualizarEstadoOutbox(params: {
    idempotencyKey: string;
    estado: OutboxEstado;
    intentos: number;
    ultimoErrorCodigo?: string | null;
    ultimoErrorMsg?: string | null;
    proximoReintentoEn?: string | null;
    retenidoAprobacion?: boolean;
    payloadActualizado?: Record<string, unknown>;
    respuesta?: Record<string, unknown> | null;
  }): Promise<void> {
    const ts = nowIso();
    const prev = await this.obtenerOutboxPorKey(params.idempotencyKey);
    const payloadJson = params.payloadActualizado
      ? JSON.stringify(params.payloadActualizado)
      : JSON.stringify(prev?.payload ?? {});
    await this.db.run(
      `UPDATE outbox_operaciones
       SET estado = ?, intentos = ?, ultimo_error_codigo = ?, ultimo_error_msg = ?,
           proximo_reintento_en = ?, retenido_aprobacion = ?, payload_json = ?,
           respuesta_json = ?, actualizado_en = ?
       WHERE idempotency_key = ?`,
      [
        params.estado,
        params.intentos,
        params.ultimoErrorCodigo ?? null,
        params.ultimoErrorMsg ?? null,
        params.proximoReintentoEn ?? null,
        params.retenidoAprobacion ? 1 : 0,
        payloadJson,
        params.respuesta ? JSON.stringify(params.respuesta) : null,
        ts,
        params.idempotencyKey,
      ],
    );
  }

  async corregirYReencolarConflicto(
    idempotencyKey: string,
    nuevoPayload: Record<string, unknown>,
  ): Promise<void> {
    const ts = nowIso();
    const payloadJson = JSON.stringify(nuevoPayload);
    await this.db.withTransaction(async () => {
      await this.db.run(
        `UPDATE outbox_operaciones
         SET payload_json = ?, estado = 'PENDING', intentos = 0,
             ultimo_error_codigo = NULL, ultimo_error_msg = NULL,
             proximo_reintento_en = NULL, actualizado_en = ?
         WHERE idempotency_key = ?`,
        [payloadJson, ts, idempotencyKey],
      );
      await this.db.run(
        "UPDATE borradores_locales SET payload_json = ?, estado = 'PENDING', actualizado_en = ? WHERE borrador_id = ?",
        [payloadJson, ts, idempotencyKey],
      );
    });
  }

  async descartarConflicto(idempotencyKey: string): Promise<void> {
    await this.db.withTransaction(async () => {
      await this.db.run("DELETE FROM adjuntos_pendientes WHERE outbox_key = ?", [idempotencyKey]);
      await this.db.run("DELETE FROM outbox_operaciones WHERE idempotency_key = ?", [idempotencyKey]);
      await this.db.run("DELETE FROM borradores_locales WHERE borrador_id = ?", [idempotencyKey]);
    });
  }

  async listarAdjuntosPendientes(): Promise<AdjuntoPendienteRegistro[]> {
    const rows = await this.db.getAll<Record<string, unknown>>(
      "SELECT * FROM adjuntos_pendientes WHERE estado = 'PENDING' ORDER BY creado_en ASC",
    );
    return rows.map((r) => ({
      adjunto_local_id: String(r.adjunto_local_id),
      outbox_key: String(r.outbox_key),
      local_uri: String(r.local_uri),
      sha256: String(r.sha256),
      mime: String(r.mime),
      bytes: Number(r.bytes),
      storage_key: r.storage_key ? String(r.storage_key) : null,
      adjunto_id: r.adjunto_id !== null ? Number(r.adjunto_id) : null,
      estado: String(r.estado) as "PENDING" | "UPLOADED" | "FAILED",
      creado_en: String(r.creado_en),
    }));
  }

  async marcarAdjuntoSubido(
    adjuntoLocalId: string,
    outboxKey: string,
    storageKey: string,
    adjuntoId?: number,
  ): Promise<void> {
    await this.db.withTransaction(async () => {
      await this.db.run(
        "UPDATE adjuntos_pendientes SET estado = 'UPLOADED', storage_key = ?, adjunto_id = ? WHERE adjunto_local_id = ?",
        [storageKey, adjuntoId ?? null, adjuntoLocalId],
      );
      const reg = await this.obtenerOutboxPorKey(outboxKey);
      if (reg) {
        const nextPayload = {
          ...reg.payload,
          adjunto_storage_key: storageKey,
          ...(adjuntoId ? { adjunto_id: adjuntoId } : {}),
        };
        await this.db.run("UPDATE outbox_operaciones SET payload_json = ? WHERE idempotency_key = ?", [
          JSON.stringify(nextPayload),
          outboxKey,
        ]);
      }
    });
  }

  async wipeAllTables(): Promise<void> {
    await this.db.withTransaction(async () => {
      for (const tbl of ALL_TABLES) {
        await this.db.run(`DELETE FROM ${tbl}`);
      }
    });
  }
}
