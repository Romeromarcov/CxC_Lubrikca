export type RutaPago = "bcv" | "binance";

export type OutboxEstado =
  | "PENDING"
  | "SYNCING"
  | "SYNCED"
  | "CONFLICT_USER_ACTION"
  | "FAILED_RETRY";

export type PushTipo =
  | "cotizacion"
  | "pedido"
  | "pago"
  | "contacto"
  | "lote_efectivo";

export interface TasasVigentes {
  tasa_bcv: number;
  tasa_binance: number;
  tasa_bcv_euro?: number | null;
  tasa_binance_manana?: number | null;
  tasa_binance_tarde?: number | null;
  tasa_binance_diario?: number | null;
  timestamp: string | null;
}

export interface ListaPrecio {
  lista_id: string;
  nombre: string;
  moneda: string;
  ruta_pago: string;
  es_defecto?: boolean;
}

export interface ProductoCatalogo {
  producto_id: string;
  codigo: string;
  nombre: string;
  marca: string;
  presentacion: string;
  tipo_envase?: string;
  litros: number;
  precio_usd: number;
  precio_ves: number;
  precios_por_lista: Record<string, number>;
  stock_disponible: number | null;
  etiqueta: string;
  disponibilidad?: "DISPONIBLE" | "BAJO_STOCK" | "BAJO_PEDIDO";
}

export interface DescuentoCatalogo {
  codigo: string;
  nombre: string;
  porcentaje?: number;
  max_pct?: number;
  aplica_lista_ids?: string[];
  notas?: string;
}

export interface CuentaReceptora {
  id: number;
  cuenta_id: number;
  nombre: string;
  tipo_canal: "banco" | "pago_movil" | "zelle" | "binance" | "efectivo" | "otro" | string;
  banco_codigo_sudeban: string;
  banco_nombre: string;
  numero_cuenta: string;
  titular_nombre: string;
  titular_rif: string;
  telefono: string;
  email_zelle: string;
  binance_id: string;
  moneda: string;
  diario_odoo_id: number | null;
  diario_odoo_codigo: string;
  diario_odoo_nombre: string;
  activa: boolean;
  es_propia: boolean;
  notas?: string;
  actualizado_en: string;
}

export interface CuentaTercero {
  id?: number;
  cuenta_tercero_id?: number;
  titular: string;
  alias?: string;
  email?: string;
  moneda?: string;
  activa: boolean;
}

export interface ContactoMaestroResumen {
  contacto_id: number | null;
  odoo_partner_id: number | null;
  rol: string;
  nombre: string;
  nombre_normalizado: string;
  rif: string;
  rif_normalizado: string;
  telefono: string;
  vendedor_email: string;
  actualizado_en: string | null;
}

export interface OrdenPendienteResumen {
  so_id: string;
  fecha: string;
  fecha_vencimiento: string | null;
  dias_credito: number;
  ruta_pago: RutaPago | string;
  monto_total_usd: number;
  saldo_pendiente_usd: number;
}

export interface PoliticaVentasGlobal {
  autosend_activo: boolean;
  limite_credito_usd: number;
  limite_facturas_pendientes: number;
  retener_si_vencidas: boolean;
  dias_mora_max: number;
}

export interface PoliticaCreditoCliente {
  partner_odoo_id?: number | null;
  autosend_activo?: boolean;
  limite_credito_usd: number | null;
  limite_facturas_pendientes: number | null;
  retener_si_vencidas?: boolean;
  dias_mora_max: number | null;
  bloquear_auto_envio?: boolean;
}

export interface ClienteCartera {
  cliente_id: string;
  partner_odoo_id: number | null;
  cliente_nombre: string;
  vendedor_email: string;
  saldo_deudor_usd: number;
  saldo_favor_usd: number;
  saldo_bcv_usd: number;
  saldo_binance_usd: number;
  ventas_bcv_usd: number;
  pagos_bcv_usd: number;
  ventas_binance_usd: number;
  pagos_binance_usd: number;
  vencido_usd?: number;
  vigente_usd?: number;
  max_dias_mora: number;
  tramos: Record<string, number>;
  politica_credito: PoliticaCreditoCliente | PoliticaVentasGlobal;
  ordenes_pendientes?: OrdenPendienteResumen[];
}

export interface BootstrapResponse {
  modo: "bootstrap" | "delta";
  cursor: string;
  desde_cursor?: string | null;
  tasas_vigentes: TasasVigentes;
  politica_ventas?: PoliticaVentasGlobal;
  lista_defecto: ListaPrecio;
  listas_precio: ListaPrecio[];
  descuentos_catalogo: DescuentoCatalogo[];
  cuentas_receptoras: CuentaReceptora[];
  cuentas_terceros: CuentaTercero[];
  contactos_maestro?: ContactoMaestroResumen[];
  catalogo: ProductoCatalogo[];
  clientes: ClienteCartera[];
  tumbstones: {
    cuentas_receptoras_inactivas: number[];
  };
}

export interface PushItem {
  idempotency_key: string;
  tipo: PushTipo;
  payload: Record<string, unknown>;
}

export interface PushItemResultado {
  idempotency_key: string;
  tipo: string;
  http_status: number;
  estado:
    | "OK"
    | "DUPLICADO_IDEMPOTENTE"
    | "CONFLICTO_NEGOCIO"
    | "ERROR_VALIDACION"
    | "ERROR_TRANSITORIO"
    | string;
  codigo_negocio?: string;
  reintentable?: boolean;
  error?: string;
  faltan?: string[];
  retenido_aprobacion?: boolean;
  documento?: Record<string, unknown>;
  operativo?: Record<string, unknown>;
  contacto?: Record<string, unknown>;
  lote?: Record<string, unknown>;
  destino_validacion?: Record<string, unknown>;
  posibles_duplicados?: Array<Record<string, unknown>>;
  candidatos?: Array<Record<string, unknown>>;
}

export interface PushBatchResponse {
  procesados: number;
  resultados: PushItemResultado[];
  cursor: string;
}

export interface LoteEfectivo {
  lote_id: number;
  numero_recibo: string;
  vendedor_email: string;
  vendedor_nombre: string;
  fecha_lote?: string | null;
  total_usd?: number;
  total_ves?: number;
  total_eq_usd?: number;
  estado?: string;
  items?: Array<Record<string, unknown>>;
}

export interface StockVerificarResponse {
  total: number;
  actualizado_en: string;
  items: Array<ProductoCatalogo & { actualizado_en?: string }>;
}

export interface CarteraClienteRuta {
  ventas_usd: number;
  pagos_usd: number;
  saldo_usd: number;
  ordenes_pendientes: OrdenPendienteResumen[];
}

export interface VendedorCarteraResponse {
  vendedor_email: string;
  alcance_global: boolean;
  tasas_vigentes: TasasVigentes;
  resumen: {
    total_clientes: number;
    saldo_bcv_usd: number;
    saldo_binance_usd: number;
    vencido_usd: number;
  };
  clientes: Array<{
    cliente_id: string;
    partner_odoo_id: number | null;
    cliente_nombre: string;
    vendedor_email: string;
    saldo_deudor_usd: number;
    saldo_favor_usd: number;
    vencido_usd: number;
    max_dias_mora: number;
    tramos: Record<string, number>;
    ruta_bcv: CarteraClienteRuta;
    ruta_binance: CarteraClienteRuta;
  }>;
}

export interface AuthTokensResponse {
  token_type: "Bearer";
  access_token: string;
  refresh_token: string;
  expires_in: number;
  device_id: string;
  user?: {
    email: string;
    nombre: string;
    rol: string;
    permisos: string[];
  };
  cuenta?: {
    cuenta_id: number;
    cliente_id: string;
    partner_odoo_id: number | null;
    cliente_nombre: string;
    email: string;
    rol_portal: "titular" | "compras" | "tesoreria" | string;
  };
}

export interface EstadoCuentaPortalResponse {
  cliente_id: string;
  cliente_nombre: string;
  resumen: Record<string, unknown>;
  ruta_bcv: {
    ventas_usd: number;
    pagos_usd: number;
    saldo_usd: number;
    movimientos: Array<Record<string, unknown>>;
  };
  ruta_binance: {
    ventas_usd: number;
    pagos_usd: number;
    saldo_usd: number;
    movimientos: Array<Record<string, unknown>>;
  };
  pagos_en_revision: Array<Record<string, unknown>>;
}
