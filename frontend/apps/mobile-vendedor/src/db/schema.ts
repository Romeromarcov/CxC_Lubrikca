export const SCHEMA_DDL_STATEMENTS: ReadonlyArray<string> = [
  "PRAGMA journal_mode = WAL;",
  `CREATE TABLE IF NOT EXISTS sync_meta (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL,
    actualizado_en TEXT NOT NULL
  );`,
  `CREATE TABLE IF NOT EXISTS productos (
    producto_id TEXT PRIMARY KEY,
    codigo TEXT NOT NULL,
    nombre TEXT NOT NULL,
    marca TEXT NOT NULL,
    presentacion TEXT NOT NULL,
    litros REAL NOT NULL DEFAULT 0,
    precio_usd REAL NOT NULL DEFAULT 0,
    precio_ves REAL NOT NULL DEFAULT 0,
    precios_por_lista_json TEXT NOT NULL DEFAULT '{}',
    etiqueta TEXT NOT NULL DEFAULT ''
  );`,
  `CREATE TABLE IF NOT EXISTS stock_cache (
    producto_id TEXT PRIMARY KEY,
    free_qty REAL,
    actualizado_en TEXT NOT NULL
  );`,
  `CREATE TABLE IF NOT EXISTS listas_precio (
    lista_id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    moneda TEXT NOT NULL,
    ruta_pago TEXT NOT NULL,
    es_defecto INTEGER NOT NULL DEFAULT 0
  );`,
  `CREATE TABLE IF NOT EXISTS reglas_descuento (
    codigo TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    json_data TEXT NOT NULL
  );`,
  `CREATE TABLE IF NOT EXISTS clientes (
    cliente_id TEXT PRIMARY KEY,
    partner_odoo_id INTEGER,
    cliente_nombre TEXT NOT NULL,
    vendedor_email TEXT NOT NULL,
    max_dias_mora INTEGER NOT NULL DEFAULT 0,
    politica_credito_json TEXT NOT NULL DEFAULT '{}',
    ordenes_pendientes_json TEXT NOT NULL DEFAULT '[]'
  );`,
  `CREATE TABLE IF NOT EXISTS saldos_cliente (
    cliente_id TEXT NOT NULL,
    ruta_pago TEXT NOT NULL,
    ventas_usd REAL NOT NULL DEFAULT 0,
    pagos_usd REAL NOT NULL DEFAULT 0,
    saldo_usd REAL NOT NULL DEFAULT 0,
    saldo_favor_usd REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (cliente_id, ruta_pago)
  );`,
  `CREATE TABLE IF NOT EXISTS tasas_cambio (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    tasa_bcv REAL NOT NULL,
    tasa_binance REAL NOT NULL,
    tasa_bcv_euro REAL,
    timestamp TEXT
  );`,
  `CREATE TABLE IF NOT EXISTS cuentas_receptoras (
    cuenta_id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    tipo_canal TEXT NOT NULL,
    banco_codigo_sudeban TEXT NOT NULL DEFAULT '',
    banco_nombre TEXT NOT NULL DEFAULT '',
    numero_cuenta TEXT NOT NULL DEFAULT '',
    titular_nombre TEXT NOT NULL DEFAULT '',
    titular_rif TEXT NOT NULL DEFAULT '',
    telefono TEXT NOT NULL DEFAULT '',
    email_zelle TEXT NOT NULL DEFAULT '',
    binance_id TEXT NOT NULL DEFAULT '',
    moneda TEXT NOT NULL,
    diario_odoo_id INTEGER,
    diario_odoo_codigo TEXT NOT NULL DEFAULT '',
    diario_odoo_nombre TEXT NOT NULL DEFAULT '',
    activa INTEGER NOT NULL DEFAULT 1,
    es_propia INTEGER NOT NULL DEFAULT 1,
    actualizado_en TEXT NOT NULL
  );`,
  `CREATE TABLE IF NOT EXISTS cuentas_terceros (
    cuenta_tercero_id INTEGER PRIMARY KEY,
    titular TEXT NOT NULL,
    alias TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    moneda TEXT NOT NULL DEFAULT 'USD',
    activa INTEGER NOT NULL DEFAULT 1
  );`,
  `CREATE TABLE IF NOT EXISTS contactos_maestro (
    contacto_key TEXT PRIMARY KEY,
    contacto_id INTEGER,
    odoo_partner_id INTEGER,
    rol TEXT NOT NULL,
    nombre TEXT NOT NULL,
    nombre_normalizado TEXT NOT NULL,
    rif TEXT NOT NULL DEFAULT '',
    rif_normalizado TEXT NOT NULL DEFAULT '',
    telefono TEXT NOT NULL DEFAULT '',
    vendedor_email TEXT NOT NULL DEFAULT '',
    actualizado_en TEXT
  );`,
  `CREATE TABLE IF NOT EXISTS politica_ventas_cache (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    json_data TEXT NOT NULL
  );`,
  `CREATE TABLE IF NOT EXISTS borradores_locales (
    borrador_id TEXT PRIMARY KEY,
    tipo TEXT NOT NULL,
    titulo TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    estado TEXT NOT NULL,
    actualizado_en TEXT NOT NULL
  );`,
  `CREATE TABLE IF NOT EXISTS outbox_operaciones (
    idempotency_key TEXT PRIMARY KEY,
    tipo TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    adjunto_local_uri TEXT,
    estado TEXT NOT NULL,
    intentos INTEGER NOT NULL DEFAULT 0,
    ultimo_error_codigo TEXT,
    ultimo_error_msg TEXT,
    proximo_reintento_en TEXT,
    retenido_aprobacion INTEGER NOT NULL DEFAULT 0,
    respuesta_json TEXT,
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL
  );`,
  `CREATE TABLE IF NOT EXISTS adjuntos_pendientes (
    adjunto_local_id TEXT PRIMARY KEY,
    outbox_key TEXT NOT NULL,
    local_uri TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    mime TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    storage_key TEXT,
    adjunto_id INTEGER,
    estado TEXT NOT NULL,
    creado_en TEXT NOT NULL
  );`,
];

export const ALL_TABLES: ReadonlyArray<string> = [
  "sync_meta",
  "productos",
  "stock_cache",
  "listas_precio",
  "reglas_descuento",
  "clientes",
  "saldos_cliente",
  "tasas_cambio",
  "cuentas_receptoras",
  "cuentas_terceros",
  "contactos_maestro",
  "politica_ventas_cache",
  "borradores_locales",
  "outbox_operaciones",
  "adjuntos_pendientes",
];
