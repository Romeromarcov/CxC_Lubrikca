import type { OutboxEstado, PushItemResultado } from "./types";

let ultimoMs = 0;
let seqEnMs = 0;

function hexByte(n: number): string {
  return (n & 0xff).toString(16).padStart(2, "0");
}

function randomBytes(count: number): Uint8Array {
  const buf = new Uint8Array(count);
  if (typeof globalThis.crypto?.getRandomValues === "function") {
    globalThis.crypto.getRandomValues(buf);
  } else {
    for (let i = 0; i < count; i += 1) {
      buf[i] = Math.floor(Math.random() * 256);
    }
  }
  return buf;
}

export function generarUuidV7(ahoraMs: number = Date.now()): string {
  let ms = Math.floor(ahoraMs);
  if (ms === ultimoMs) {
    seqEnMs = (seqEnMs + 1) & 0x0fff;
    if (seqEnMs === 0) ms += 1;
  } else {
    ultimoMs = ms;
    seqEnMs = 0;
  }

  const bytes = randomBytes(16);
  // 48-bit big-endian timestamp
  bytes[0] = Math.floor(ms / 0x10000000000) & 0xff;
  bytes[1] = Math.floor(ms / 0x100000000) & 0xff;
  bytes[2] = Math.floor(ms / 0x1000000) & 0xff;
  bytes[3] = Math.floor(ms / 0x10000) & 0xff;
  bytes[4] = Math.floor(ms / 0x100) & 0xff;
  bytes[5] = ms & 0xff;

  // Version 7 (0111xxxx) + 12-bit monotonic counter
  bytes[6] = 0x70 | ((seqEnMs >> 8) & 0x0f);
  bytes[7] = seqEnMs & 0xff;

  // Variant RFC 4122 (10xxxxxx)
  bytes[8] = 0x80 | ((bytes[8] ?? 0) & 0x3f);

  const hex = Array.from(bytes, hexByte).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

export interface ClasificacionOutbox {
  nuevoEstado: OutboxEstado;
  reintentarAuto: boolean;
  esperaSegundos: number | null;
  limpiarDiarioSugerido: boolean;
  codigoError: string;
  mensaje: string;
  retenidoAprobacion: boolean;
}

const HTTP_DETERMINISTICOS = new Set([400, 403, 404, 409, 422]);

export function calcularBackoffSegundos(intentos: number): number {
  const exp = Math.max(0, intentos - 1);
  return Math.min(300, 5 * 2 ** exp);
}

export function clasificarResultadoPush(
  resultado: PushItemResultado,
  intentosPrevios: number,
  maxIntentos: number = 5,
): ClasificacionOutbox {
  if (resultado.estado === "OK" || resultado.estado === "DUPLICADO_IDEMPOTENTE") {
    return {
      nuevoEstado: "SYNCED",
      reintentarAuto: false,
      esperaSegundos: null,
      limpiarDiarioSugerido: false,
      codigoError: "",
      mensaje: resultado.retenido_aprobacion
        ? "Sincronizado en CxC (retenido: requiere aprobación gerencial)."
        : "Sincronizado con éxito.",
      retenidoAprobacion: Boolean(resultado.retenido_aprobacion),
    };
  }

  // Regla Anti-Loop: errores determinísticos de negocio nunca se reintentan automáticamente.
  if (
    HTTP_DETERMINISTICOS.has(resultado.http_status) ||
    resultado.reintentable === false ||
    resultado.estado === "CONFLICTO_NEGOCIO" ||
    resultado.estado === "ERROR_VALIDACION"
  ) {
    const cod =
      resultado.codigo_negocio ||
      (resultado.http_status === 409 ? "CONFLICTO_409" : `ERROR_${resultado.http_status}`);
    return {
      nuevoEstado: "CONFLICT_USER_ACTION",
      reintentarAuto: false,
      esperaSegundos: null,
      limpiarDiarioSugerido: cod === "DESTINO_NO_PERTENECE_LUBRIKCA" || cod === "DESTINO_INVALIDO",
      codigoError: cod,
      mensaje: resultado.error || "Conflicto de negocio requiere revisión del vendedor.",
      retenidoAprobacion: false,
    };
  }

  const siguienteIntento = intentosPrevios + 1;
  const reintentarAuto = siguienteIntento < maxIntentos;
  return {
    nuevoEstado: "FAILED_RETRY",
    reintentarAuto,
    esperaSegundos: reintentarAuto ? calcularBackoffSegundos(siguienteIntento) : null,
    limpiarDiarioSugerido: false,
    codigoError: `HTTP_${resultado.http_status || 503}`,
    mensaje: resultado.error || "Error temporal de red o servidor.",
    retenidoAprobacion: false,
  };
}

export function clasificarErrorRed(
  error: unknown,
  intentosPrevios: number,
  maxIntentos: number = 5,
): ClasificacionOutbox {
  const msg = error instanceof Error ? error.message : String(error || "Sin conectividad");
  const siguienteIntento = intentosPrevios + 1;
  const reintentarAuto = siguienteIntento < maxIntentos;
  return {
    nuevoEstado: "FAILED_RETRY",
    reintentarAuto,
    esperaSegundos: reintentarAuto ? calcularBackoffSegundos(siguienteIntento) : null,
    limpiarDiarioSugerido: false,
    codigoError: "NETWORK_ERROR",
    mensaje: msg,
    retenidoAprobacion: false,
  };
}

export interface GuiaResolucionConflicto {
  escenario:
    | "STOCK_INSUFICIENTE"
    | "REFERENCIA_DUPLICADA"
    | "DESTINO_NO_LUBRIKCA"
    | "CAMBIO_TASA"
    | "CONTACTO_DUPLICADO"
    | "PENDIENTE_APROBACION"
    | "GENERICO";
  titulo: string;
  accionRecomendada: string;
  permiteReenviarDirecto: boolean;
}

export function describirResolucionConflicto(item: {
  codigoError?: string | null;
  mensaje?: string | null;
  retenidoAprobacion?: boolean;
}): GuiaResolucionConflicto {
  const cod = String(item.codigoError ?? "").toUpperCase();
  const msg = String(item.mensaje ?? "").toLowerCase();

  if (item.retenidoAprobacion) {
    return {
      escenario: "PENDIENTE_APROBACION",
      titulo: "Pedido retenido para aprobación",
      accionRecomendada: "Sincronizado en CxC; quedará pendiente de revisión gerencial.",
      permiteReenviarDirecto: false,
    };
  }
  if (cod.includes("DESTINO")) {
    return {
      escenario: "DESTINO_NO_LUBRIKCA",
      titulo: "Cuenta destino no pertenece a Lubrikca",
      accionRecomendada: "Corrige el banco/cuenta receptora real de Lubrikca o solicita revisión administrativa.",
      permiteReenviarDirecto: false,
    };
  }
  if (cod.includes("DUPLICAD") && cod.includes("CONTACTO")) {
    return {
      escenario: "CONTACTO_DUPLICADO",
      titulo: "Cliente ya existe en Maestro Único",
      accionRecomendada: "Vincula al cliente existente o justifica si se trata de otra sucursal.",
      permiteReenviarDirecto: false,
    };
  }
  if (cod.includes("DUPLICAD") || msg.includes("duplicad")) {
    return {
      escenario: "REFERENCIA_DUPLICADA",
      titulo: "Referencia de pago duplicada",
      accionRecomendada: "Verifica la referencia/recibo existente; corrige o descarta el duplicado.",
      permiteReenviarDirecto: false,
    };
  }
  if (cod.includes("STOCK") || msg.includes("stock") || msg.includes("free_qty")) {
    return {
      escenario: "STOCK_INSUFICIENTE",
      titulo: "Stock libre insuficiente en Odoo",
      accionRecomendada: "Ajusta las cantidades al disponible real o guarda como cotización.",
      permiteReenviarDirecto: false,
    };
  }
  if (cod.includes("TASA") || msg.includes("tasa")) {
    return {
      escenario: "CAMBIO_TASA",
      titulo: "Variación de tasa fuera de tolerancia",
      accionRecomendada: "Actualiza las tasas del día y confirma el equivalente pactado.",
      permiteReenviarDirecto: true,
    };
  }
  return {
    escenario: "GENERICO",
    titulo: "Validación requerida antes de sincronizar",
    accionRecomendada: item.mensaje || "Revisa los campos obligatorios del borrador antes de reenviar.",
    permiteReenviarDirecto: false,
  };
}
