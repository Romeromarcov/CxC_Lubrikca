import type { ListaPrecio, RutaPago } from "./types";

export const RUTA_BCV: RutaPago = "bcv";
export const RUTA_BINANCE: RutaPago = "binance";

export function normalizarRuta(valor: unknown): RutaPago | "" {
  const txt = String(valor ?? "")
    .trim()
    .toLowerCase();
  if (!txt) return "";
  if (txt === "binance" || txt === "usd" || txt.includes("binance") || txt.includes("usdt") || txt.includes("zelle")) {
    return RUTA_BINANCE;
  }
  if (txt === "bcv" || txt === "ves" || txt === "bs" || txt.includes("bcv")) {
    return RUTA_BCV;
  }
  return "";
}

export function rutaDeLista(
  listaRef: string | number | null | undefined,
  listasPrecio: ReadonlyArray<ListaPrecio> = [],
): RutaPago {
  const raw = String(listaRef ?? "").trim();
  if (!raw) return RUTA_BCV;

  const porId = listasPrecio.find((l) => String(l.lista_id ?? l.id ?? "") === raw);
  if (porId) {
    const r = normalizarRuta(porId.ruta_pago) || normalizarRuta(porId.moneda);
    if (r) return r;
  }

  const porNombre = listasPrecio.find(
    (l) => l.nombre.trim().toLowerCase() === raw.toLowerCase(),
  );
  if (porNombre) {
    const r = normalizarRuta(porNombre.ruta_pago) || normalizarRuta(porNombre.moneda);
    if (r) return r;
  }

  const up = raw.toUpperCase();
  if (up.includes("USD") || up.includes("BINANCE") || up.includes("ZELLE")) {
    return RUTA_BINANCE;
  }
  return RUTA_BCV;
}

export function validarNoMezclaRutasDocumento(
  rutaDocumento: RutaPago,
  lineas: ReadonlyArray<{ lista_id?: string | null; lista_nombre?: string | null; ruta_pago?: string | null }>,
  listasPrecio: ReadonlyArray<ListaPrecio> = [],
): { valido: boolean; error?: string } {
  for (let i = 0; i < lineas.length; i += 1) {
    const ln = lineas[i];
    if (!ln) continue;
    const rutaLinea =
      normalizarRuta(ln.ruta_pago) ||
      (ln.lista_id ? rutaDeLista(ln.lista_id, listasPrecio) : "") ||
      (ln.lista_nombre ? rutaDeLista(ln.lista_nombre, listasPrecio) : "");
    if (rutaLinea && rutaLinea !== rutaDocumento) {
      return {
        valido: false,
        error: `La línea #${i + 1} pertenece a ruta ${rutaLinea.toUpperCase()} y no puede mezclarse en un documento de ruta ${rutaDocumento.toUpperCase()}.`,
      };
    }
  }
  return { valido: true };
}

export function validarCompatibilidadPagoConPedido(
  rutaPedido: RutaPago | "",
  monedaPago: string,
  metodoPago: string,
): { valido: boolean; error?: string } {
  if (!rutaPedido) return { valido: true };
  const mon = monedaPago.trim().toUpperCase();
  const met = metodoPago.trim().toLowerCase();
  const esPagoBinance = mon === "USD" || met.includes("binance") || met.includes("zelle");
  const esPagoBcv = mon === "VES" && !met.includes("binance") && !met.includes("zelle");

  if (rutaPedido === RUTA_BINANCE && esPagoBcv) {
    return {
      valido: false,
      error: "El pedido pertenece a Ruta Binance (USD) y no puede pagarse por Ruta BCV (VES).",
    };
  }
  if (rutaPedido === RUTA_BCV && esPagoBinance) {
    return {
      valido: false,
      error: "El pedido pertenece a Ruta BCV (VES) y no puede pagarse por Ruta Binance (USD).",
    };
  }
  return { valido: true };
}
