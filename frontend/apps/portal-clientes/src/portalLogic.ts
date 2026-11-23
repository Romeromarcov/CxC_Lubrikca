import {
  validarCompatibilidadPagoConPedido,
  validarDestinoPagoOffline,
  type CuentaReceptora,
  type RutaPago,
} from "@lubrikca/domain-ts";

export type RolPortal = "titular" | "compras" | "tesoreria";

export interface PermisosPortalUi {
  verEstadoCuenta: boolean;
  verCatalogoYCrearPedidos: boolean;
  registrarPagos: boolean;
  verTrazabilidad: boolean;
  administrarEquipo: boolean;
}

export function permisosPorRolPortal(rol: string): PermisosPortalUi {
  const r = String(rol || "").trim().toLowerCase() as RolPortal;
  if (r === "compras") {
    return {
      verEstadoCuenta: false,
      verCatalogoYCrearPedidos: true,
      registrarPagos: false,
      verTrazabilidad: true,
      administrarEquipo: false,
    };
  }
  if (r === "tesoreria") {
    return {
      verEstadoCuenta: true,
      verCatalogoYCrearPedidos: false,
      registrarPagos: true,
      verTrazabilidad: false,
      administrarEquipo: false,
    };
  }
  return {
    verEstadoCuenta: true,
    verCatalogoYCrearPedidos: true,
    registrarPagos: true,
    verTrazabilidad: true,
    administrarEquipo: true,
  };
}

export interface ItemCarritoPortal {
  producto_id: string;
  codigo: string;
  descripcion: string;
  cantidad: number;
  precio_unitario: number;
}

export interface RecompraPayload {
  referencia?: string;
  lista_precios_id?: string;
  ruta_pago?: RutaPago | string;
  lineas: Array<{
    producto_id: string;
    codigo?: string;
    descripcion?: string;
    cantidad: number;
    precio_unitario: number;
  }>;
}

export function prepararCarritoRecompra(recompra: RecompraPayload | null | undefined): {
  listaPreciosId: string;
  rutaPago: RutaPago;
  items: ItemCarritoPortal[];
} | null {
  if (!recompra || !Array.isArray(recompra.lineas) || recompra.lineas.length === 0) {
    return null;
  }
  const ruta = String(recompra.ruta_pago || "").toLowerCase() === "binance" ? "binance" : "bcv";
  const items = recompra.lineas
    .filter((l) => l.producto_id && Number(l.cantidad) > 0)
    .map((l) => ({
      producto_id: String(l.producto_id),
      codigo: String(l.codigo || ""),
      descripcion: String(l.descripcion || l.codigo || "Producto"),
      cantidad: Number(l.cantidad),
      precio_unitario: Number(l.precio_unitario || 0),
    }));
  if (items.length === 0) return null;
  return {
    listaPreciosId: String(recompra.lista_precios_id || ""),
    rutaPago: ruta,
    items,
  };
}

export function filtrarCuentasPorRutaPago(
  cuentas: ReadonlyArray<CuentaReceptora>,
  ruta: RutaPago,
): CuentaReceptora[] {
  return cuentas.filter((c) => {
    if (!c.activa || !c.es_propia) return false;
    const mon = String(c.moneda || "").toUpperCase();
    const canal = String(c.tipo_canal || "").toLowerCase();
    if (ruta === "bcv") {
      return mon === "VES";
    }
    return mon === "USD" || canal === "binance" || canal === "zelle";
  });
}

export function validarPagoPortalAntesDeEnviar(params: {
  ruta: RutaPago;
  moneda: string;
  metodoPago: string;
  bancoOrigen: string;
  bancoDestino: string;
  cuentaDestino: string;
  rifDestino?: string;
  telefonoDestino?: string;
  emailDestino?: string;
  cuentasRuta: ReadonlyArray<CuentaReceptora>;
}): { valido: boolean; error?: string } {
  const compat = validarCompatibilidadPagoConPedido(params.ruta, params.moneda, params.metodoPago);
  if (!compat.valido) {
    return { valido: false, error: compat.error };
  }
  const dest = validarDestinoPagoOffline(
    {
      banco_origen: params.bancoOrigen,
      banco_destino: params.bancoDestino,
      cuenta_destino: params.cuentaDestino,
      rif_destino: params.rifDestino,
      telefono_destino: params.telefonoDestino,
      email_destino: params.emailDestino,
      metodo_pago: params.metodoPago,
    },
    params.cuentasRuta,
  );
  if (dest.bloqueante) {
    return { valido: false, error: dest.mensaje };
  }
  return { valido: true };
}
