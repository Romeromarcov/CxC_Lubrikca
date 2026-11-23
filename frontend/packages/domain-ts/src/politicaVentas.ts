import type { ClienteCartera, PoliticaCreditoCliente, PoliticaVentasGlobal } from "./types";

export interface EvaluacionPoliticaInput {
  tipoDocumento: "cotizacion" | "pedido";
  montoPedidoUsd: number;
  diasCredito: number;
  tieneDescuentoManual?: boolean;
  tieneDescuentoEspecial?: boolean;
  fichaClienteCompleta?: boolean;
  cliente?: ClienteCartera | null;
  politicaGlobal?: PoliticaVentasGlobal | null;
}

export interface EvaluacionPoliticaResultado {
  estadoSugerido: "APROBACION_AUTO" | "PENDIENTE_APROBACION";
  requiereAprobacion: boolean;
  alertas: string[];
  deudaProyectadaUsd: number;
}

export function evaluarPoliticaComercialOffline(
  input: EvaluacionPoliticaInput,
): EvaluacionPoliticaResultado {
  const alertas: string[] = [];
  const polGlobal: PoliticaVentasGlobal = input.politicaGlobal ?? {
    autosend_activo: true,
    limite_credito_usd: 0,
    limite_facturas_pendientes: 0,
    retener_si_vencidas: true,
    dias_mora_max: 0,
  };
  const cli = input.cliente ?? null;
  const ov = (cli?.politica_credito ?? {}) as Partial<PoliticaCreditoCliente>;

  const autosendActivo = ov.autosend_activo ?? polGlobal.autosend_activo;
  const limCredito = ov.limite_credito_usd ?? polGlobal.limite_credito_usd ?? 0;
  const limFacturas = ov.limite_facturas_pendientes ?? polGlobal.limite_facturas_pendientes ?? 0;
  const retenerVenc = ov.retener_si_vencidas ?? polGlobal.retener_si_vencidas;
  const diasMoraMax = ov.dias_mora_max ?? polGlobal.dias_mora_max ?? 0;
  const bloquearAuto = Boolean(ov.bloquear_auto_envio);

  const deudaActual = cli?.saldo_deudor_usd ?? 0;
  const deudaProyectadaUsd = Number((deudaActual + Math.max(0, input.montoPedidoUsd)).toFixed(2));

  if (input.tipoDocumento !== "pedido") {
    alertas.push("Las cotizaciones se envían para revisión comercial antes de convertirse en pedido.");
  }
  if (!autosendActivo) {
    alertas.push("Auto-envío a Odoo desactivado en Política de Ventas.");
  }
  if (bloquearAuto) {
    alertas.push("Cliente marcado con revisión manual obligatoria.");
  }
  if (input.tieneDescuentoManual) {
    alertas.push("Contiene descuento manual en línea fuera del catálogo del motor.");
  }
  if (input.tieneDescuentoEspecial) {
    alertas.push("Incluye descuento comercial especial: requiere aprobación gerencial.");
  }
  if (input.diasCredito > 0 && !cli?.partner_odoo_id) {
    alertas.push(`Plazo de crédito (${input.diasCredito}d) para cliente sin ficha verificada en Odoo.`);
  }
  if (input.fichaClienteCompleta === false) {
    alertas.push("Ficha fiscal/geográfica incompleta para auto-envío.");
  }

  if (cli) {
    if (retenerVenc && cli.max_dias_mora > diasMoraMax) {
      alertas.push(
        `Cartera vencida: ${cli.max_dias_mora} día(s) de mora supera el máximo permitido (${diasMoraMax}d).`,
      );
    }
    const abiertas = (cli.ordenes_pendientes ?? []).length;
    if (limFacturas > 0 && abiertas >= limFacturas) {
      alertas.push(
        `El cliente tiene ${abiertas} orden(es) pendientes (límite permitido: ${limFacturas}).`,
      );
    }
    if (limCredito > 0 && deudaProyectadaUsd > limCredito) {
      alertas.push(
        `Excede el límite de crédito ($${deudaProyectadaUsd.toFixed(2)} > $${limCredito.toFixed(2)}).`,
      );
    }
  }

  const requiereAprobacion = alertas.length > 0;
  return {
    estadoSugerido: requiereAprobacion ? "PENDIENTE_APROBACION" : "APROBACION_AUTO",
    requiereAprobacion,
    alertas,
    deudaProyectadaUsd,
  };
}
