import { normalizarRuta } from "./rutasPago";
import type { OrdenPendienteResumen, RutaPago } from "./types";

export interface AsignacionFifo {
  so_id: string;
  ruta_pago: RutaPago;
  fecha: string;
  saldo_pendiente_usd: number;
  monto_asignado_usd: number;
  saldo_restante_usd: number;
}

export interface ResultadoFifo {
  ruta_pago: RutaPago;
  monto_recibido_usd: number;
  monto_aplicado_usd: number;
  remanente_a_favor_usd: number;
  asignaciones: AsignacionFifo[];
}

function round2(n: number): number {
  return Math.round((n + Number.EPSILON) * 100) / 100;
}

export function sugerirVinculacionFifo(
  montoUsd: number,
  rutaPago: RutaPago,
  ordenesPendientes: ReadonlyArray<OrdenPendienteResumen>,
): ResultadoFifo {
  const monto = Math.max(0, round2(montoUsd));
  const candidatas = ordenesPendientes
    .filter((o) => (normalizarRuta(o.ruta_pago) || "bcv") === rutaPago && o.saldo_pendiente_usd > 0.009)
    .slice()
    .sort((a, b) => {
      const fa = a.fecha_vencimiento ?? a.fecha;
      const fb = b.fecha_vencimiento ?? b.fecha;
      if (fa !== fb) return fa < fb ? -1 : 1;
      if (a.fecha !== b.fecha) return a.fecha < b.fecha ? -1 : 1;
      return a.so_id < b.so_id ? -1 : a.so_id > b.so_id ? 1 : 0;
    });

  let disponible = monto;
  const asignaciones: AsignacionFifo[] = [];
  let aplicado = 0;

  for (const ord of candidatas) {
    if (disponible <= 0.009) break;
    const pend = round2(ord.saldo_pendiente_usd);
    const abono = round2(Math.min(pend, disponible));
    if (abono <= 0) continue;
    disponible = round2(disponible - abono);
    aplicado = round2(aplicado + abono);
    asignaciones.push({
      so_id: ord.so_id,
      ruta_pago: rutaPago,
      fecha: ord.fecha,
      saldo_pendiente_usd: pend,
      monto_asignado_usd: abono,
      saldo_restante_usd: round2(pend - abono),
    });
  }

  return {
    ruta_pago: rutaPago,
    monto_recibido_usd: monto,
    monto_aplicado_usd: aplicado,
    remanente_a_favor_usd: round2(Math.max(0, monto - aplicado)),
    asignaciones,
  };
}
