import type { CuentaReceptora, CuentaTercero } from "./types";

export const BANCOS_SUDEBAN: Record<string, string> = {
  "0102": "Banco de Venezuela",
  "0104": "Venezolano de Crédito",
  "0105": "Banco Mercantil",
  "0108": "Banco Provincial",
  "0114": "Bancaribe",
  "0115": "Banco Exterior",
  "0128": "Banco Caroní",
  "0134": "Banesco",
  "0137": "Banco Sofitasa",
  "0138": "Banco Plaza",
  "0146": "Bangente",
  "0151": "BFC Banco Fondo Común",
  "0156": "100% Banco",
  "0157": "DelSur",
  "0163": "Banco del Tesoro",
  "0166": "Banco Agrícola de Venezuela",
  "0168": "Bancrecer",
  "0169": "R4 Banco Microfinanciero",
  "0171": "Banco Activo",
  "0172": "Bancamiga",
  "0173": "Banco Internacional de Desarrollo",
  "0174": "Banplus",
  "0175": "Banco Digital de los Trabajadores",
  "0177": "Banfanb",
  "0178": "N58 Banco Digital",
  "0191": "Banco Nacional de Crédito",
};

const ALIASES_SUDEBAN: Array<[string, string[]]> = [
  ["0102", ["banco de venezuela", "bdv", "venezuela"]],
  ["0104", ["venezolano de credito", "banco venezolano de credito", "bvc", "venezolano credito"]],
  ["0105", ["mercantil", "banco mercantil"]],
  ["0108", ["provincial", "bbva", "bbva provincial", "banco provincial"]],
  ["0114", ["bancaribe", "banco del caribe"]],
  ["0115", ["exterior", "banco exterior"]],
  ["0128", ["caroni", "banco caroni"]],
  ["0134", ["banesco", "bban"]],
  ["0137", ["sofitasa", "banco sofitasa"]],
  ["0138", ["plaza", "banco plaza"]],
  ["0146", ["bangente"]],
  ["0151", ["fondo comun", "bfc"]],
  ["0156", ["100% banco", "100x100", "cien por ciento"]],
  ["0157", ["delsur", "del sur"]],
  ["0163", ["tesoro", "banco del tesoro"]],
  ["0166", ["agricola de venezuela"]],
  ["0168", ["bancrecer"]],
  ["0169", ["mi banco", "mibanco", "r4 banco"]],
  ["0171", ["banco activo"]],
  ["0172", ["bancamiga", "bami"]],
  ["0173", ["internacional de desarrollo", "bid"]],
  ["0174", ["banplus"]],
  ["0175", ["bdt", "bicentenario", "trabajadores", "digital de los trabajadores"]],
  ["0177", ["banfanb", "fanb"]],
  ["0178", ["n58"]],
  ["0191", ["bnc", "nacional de credito", "banco nacional de credito"]],
];

export function sinAcentosLower(valor: unknown): string {
  return String(valor ?? "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

export function normalizarCuentaBancaria(valor: unknown): string {
  return String(valor ?? "").replace(/\D+/g, "");
}

export function normalizarRif(valor: unknown): string {
  return String(valor ?? "")
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "");
}

export function normalizarTelefono(valor: unknown): string {
  const digits = String(valor ?? "").replace(/\D+/g, "");
  if (!digits) return "";
  if (digits.startsWith("58") && digits.length >= 12) {
    return `0${digits.slice(2)}`;
  }
  return digits.length >= 10 && !digits.startsWith("0") ? `0${digits}` : digits;
}

export function normalizarEmail(valor: unknown): string {
  return String(valor ?? "").trim().toLowerCase();
}

export function codigoSudebanDeCuenta(cuenta: unknown): string {
  const norm = normalizarCuentaBancaria(cuenta);
  if (norm.length >= 4) {
    const pref = norm.slice(0, 4);
    if (pref in BANCOS_SUDEBAN) return pref;
  }
  return "";
}

export function codigoSudebanDeBanco(valor: unknown): string {
  const raw = String(valor ?? "").trim();
  if (!raw) return "";
  const match = raw.match(/\b(01\d{2})\b/);
  if (match?.[1] && match[1] in BANCOS_SUDEBAN) {
    return match[1];
  }
  const low = sinAcentosLower(raw);
  for (const [cod, aliases] of ALIASES_SUDEBAN) {
    if (aliases.some((a) => low.includes(a))) {
      return cod;
    }
  }
  return "";
}

function canalExplicito(texto: string): string {
  const txt = sinAcentosLower(texto);
  if (txt.includes("binance") || txt.includes("usdt") || txt.includes("binance pay")) {
    return "binance";
  }
  if (txt.includes("zelle")) {
    return "zelle";
  }
  return "";
}

export interface DatosPagoValidacionDestino {
  banco_origen?: string;
  banco_destino?: string;
  cuenta_destino?: string;
  rif_destino?: string;
  telefono_destino?: string;
  email_destino?: string;
  titular_destino?: string;
  metodo_pago?: string;
  es_zelle_tercero?: boolean;
  cuenta_tercero_id?: number | null;
}

export interface ResultadoValidacionDestino {
  estado:
    | "VALIDADO_LUBRIKCA"
    | "DESTINO_NO_PERTENECE_LUBRIKCA"
    | "EXENTO_ZELLE_TERCERO"
    | "EXENTO_EFECTIVO"
    | "SIN_CATALOGO_PROPIO"
    | "SIN_DATOS_DESTINO";
  valido: boolean;
  bloqueante: boolean;
  mensaje: string;
  limpiar_diario: boolean;
  cuenta_id: number | null;
  cuenta_nombre: string | null;
  diario_odoo_id: number | null;
  diario_odoo_nombre: string | null;
}

function rechazoNoLubrikca(mensaje: string): ResultadoValidacionDestino {
  return {
    estado: "DESTINO_NO_PERTENECE_LUBRIKCA",
    valido: false,
    bloqueante: true,
    mensaje,
    limpiar_diario: true,
    cuenta_id: null,
    cuenta_nombre: null,
    diario_odoo_id: null,
    diario_odoo_nombre: null,
  };
}

export function validarDestinoPagoOffline(
  datos: DatosPagoValidacionDestino,
  cuentasPropias: ReadonlyArray<CuentaReceptora>,
  cuentasTerceros: ReadonlyArray<CuentaTercero> = [],
): ResultadoValidacionDestino {
  const metodoLow = sinAcentosLower(datos.metodo_pago);
  if (metodoLow.includes("efectivo") || metodoLow.includes("cash")) {
    return {
      estado: "EXENTO_EFECTIVO",
      valido: true,
      bloqueante: false,
      mensaje: "Cobro en efectivo.",
      limpiar_diario: false,
      cuenta_id: null,
      cuenta_nombre: null,
      diario_odoo_id: null,
      diario_odoo_nombre: null,
    };
  }

  if (datos.es_zelle_tercero) {
    if (datos.cuenta_tercero_id) {
      const ter = cuentasTerceros.find(
        (t) => t.activa && (t.id === datos.cuenta_tercero_id || t.cuenta_tercero_id === datos.cuenta_tercero_id),
      );
      if (!ter) {
        return rechazoNoLubrikca("La cuenta Zelle de tercero seleccionada no está activa o autorizada.");
      }
    }
    return {
      estado: "EXENTO_ZELLE_TERCERO",
      valido: true,
      bloqueante: false,
      mensaje: "Pago a cuenta Zelle de tercero autorizado.",
      limpiar_diario: false,
      cuenta_id: null,
      cuenta_nombre: null,
      diario_odoo_id: null,
      diario_odoo_nombre: null,
    };
  }

  const activas = cuentasPropias.filter((c) => c.activa && c.es_propia);
  if (activas.length === 0) {
    return {
      estado: "SIN_CATALOGO_PROPIO",
      valido: true,
      bloqueante: false,
      mensaje: "Sin catálogo local de cuentas propias; se validará al sincronizar.",
      limpiar_diario: false,
      cuenta_id: null,
      cuenta_nombre: null,
      diario_odoo_id: null,
      diario_odoo_nombre: null,
    };
  }

  const cuentaDestNorm = normalizarCuentaBancaria(datos.cuenta_destino);
  const rifDest = normalizarRif(datos.rif_destino);
  const telDest = normalizarTelefono(datos.telefono_destino);
  const titularDest = String(datos.titular_destino ?? "").trim();
  const emailDest =
    normalizarEmail(datos.email_destino) ||
    (titularDest.includes("@") ? normalizarEmail(titularDest) : "");
  const bancoDest = String(datos.banco_destino ?? "").trim();

  if (![bancoDest, cuentaDestNorm, rifDest, telDest, emailDest, titularDest].some(Boolean)) {
    return {
      estado: "SIN_DATOS_DESTINO",
      valido: false,
      bloqueante: true,
      mensaje:
        "Debes indicar banco, cuenta o dato destino de Lubrikca (nunca se asume desde el banco origen).",
      limpiar_diario: true,
      cuenta_id: null,
      cuenta_nombre: null,
      diario_odoo_id: null,
      diario_odoo_nombre: null,
    };
  }

  const codCuenta = codigoSudebanDeCuenta(cuentaDestNorm);
  const codBanco = codigoSudebanDeBanco(bancoDest);
  if (codCuenta && codBanco && codCuenta !== codBanco) {
    const nomCta = BANCOS_SUDEBAN[codCuenta] ?? codCuenta;
    return rechazoNoLubrikca(
      `La cuenta destino (${codCuenta} · ${nomCta}) no coincide con el banco destino indicado (${bancoDest}).`,
    );
  }
  const codDest = codCuenta || codBanco;

  let candidatos = [...activas];

  if (cuentaDestNorm) {
    const conNum = candidatos.filter((c) => normalizarCuentaBancaria(c.numero_cuenta));
    const exactos = candidatos.filter((c) => {
      const cNorm = normalizarCuentaBancaria(c.numero_cuenta);
      if (!cNorm) return false;
      if (cuentaDestNorm.length >= 16) return cNorm === cuentaDestNorm;
      if (cuentaDestNorm.length >= 4) return cNorm.endsWith(cuentaDestNorm);
      return false;
    });
    if (exactos.length > 0) {
      candidatos = exactos;
    } else if (conNum.length > 0) {
      return rechazoNoLubrikca(
        `El número de cuenta destino (${datos.cuenta_destino ?? cuentaDestNorm}) no coincide con ninguna cuenta receptora de Lubrikca.`,
      );
    }
  }

  const canalB = canalExplicito(bancoDest);
  if (codDest) {
    const mBanco = candidatos.filter((c) => c.banco_codigo_sudeban === codDest);
    if (mBanco.length === 0) {
      const bNom = BANCOS_SUDEBAN[codDest] ?? bancoDest ?? codDest;
      return rechazoNoLubrikca(
        `Banco destino «${bNom}» (${codDest}) no pertenece a las cuentas receptoras configuradas de Lubrikca.`,
      );
    }
    candidatos = mBanco;
  } else if (canalB) {
    const mCanal = candidatos.filter((c) => c.tipo_canal === canalB);
    if (mCanal.length === 0) {
      return rechazoNoLubrikca(
        `Canal destino «${bancoDest}» no tiene cuentas propias activas en Lubrikca.`,
      );
    }
    candidatos = mCanal;
  } else if (bancoDest) {
    const bLow = sinAcentosLower(bancoDest);
    const mNom = candidatos.filter(
      (c) =>
        sinAcentosLower(c.nombre).includes(bLow) ||
        sinAcentosLower(c.banco_nombre).includes(bLow) ||
        sinAcentosLower(c.diario_odoo_nombre).includes(bLow),
    );
    if (mNom.length === 0) {
      return rechazoNoLubrikca(
        `Banco/canal destino «${bancoDest}» no coincide con ninguna cuenta de Lubrikca.`,
      );
    }
    candidatos = mNom;
  }

  if (rifDest) {
    const conRif = candidatos.filter((c) => normalizarRif(c.titular_rif));
    if (conRif.length > 0) {
      const mRif = conRif.filter((c) => normalizarRif(c.titular_rif) === rifDest);
      if (mRif.length === 0) {
        return rechazoNoLubrikca(
          `El RIF/cédula destino (${datos.rif_destino}) no pertenece a Lubrikca para ese banco.`,
        );
      }
      candidatos = mRif;
    }
  }

  if (telDest) {
    const conTel = candidatos.filter((c) => normalizarTelefono(c.telefono));
    if (conTel.length > 0) {
      const mTel = conTel.filter((c) => normalizarTelefono(c.telefono) === telDest);
      if (mTel.length === 0) {
        return rechazoNoLubrikca(
          `El teléfono Pago Móvil destino (${datos.telefono_destino}) no pertenece a Lubrikca.`,
        );
      }
      candidatos = mTel;
    }
  }

  if (emailDest) {
    const conEmail = candidatos.filter((c) => normalizarEmail(c.email_zelle));
    if (conEmail.length > 0) {
      const mEmail = conEmail.filter((c) => normalizarEmail(c.email_zelle) === emailDest);
      if (mEmail.length === 0) {
        return rechazoNoLubrikca(
          `El correo destino (${emailDest}) no coincide con las cuentas Zelle de Lubrikca.`,
        );
      }
      candidatos = mEmail;
    }
  }

  const elegida = candidatos[0];
  if (!elegida) {
    return rechazoNoLubrikca("No se encontró cuenta receptora compatible de Lubrikca.");
  }
  return {
    estado: "VALIDADO_LUBRIKCA",
    valido: true,
    bloqueante: false,
    mensaje: `Cuenta receptora verificada en Lubrikca: ${elegida.nombre}.`,
    limpiar_diario: false,
    cuenta_id: elegida.cuenta_id,
    cuenta_nombre: elegida.nombre,
    diario_odoo_id: elegida.diario_odoo_id,
    diario_odoo_nombre: elegida.diario_odoo_nombre || elegida.nombre,
  };
}
