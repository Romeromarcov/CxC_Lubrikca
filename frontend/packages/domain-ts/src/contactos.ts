import { normalizarRif, normalizarTelefono, sinAcentosLower } from "./sudeban";
import type { ContactoMaestroResumen } from "./types";

const SUFIJOS_SOCIETARIOS = new Set([
  "ca",
  "sa",
  "srl",
  "llc",
  "inc",
  "compania",
  "anonima",
  "cia",
  "ltda",
]);

export function normalizarNombreContacto(nombre: unknown): string {
  const tokens = sinAcentosLower(nombre)
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((t) => t && !SUFIJOS_SOCIETARIOS.has(t));
  return tokens.join(" ");
}

export interface CandidatoDuplicadoContacto {
  motivo: "RIF_IDENTICO" | "TELEFONO_IDENTICO" | "NOMBRE_SIMILAR";
  bloqueante: boolean;
  contacto: ContactoMaestroResumen;
}

export function detectarDuplicadosContactoOffline(
  nuevo: { nombre: string; rif?: string; telefono?: string },
  existentes: ReadonlyArray<ContactoMaestroResumen>,
): CandidatoDuplicadoContacto[] {
  const rifN = normalizarRif(nuevo.rif);
  const telN = normalizarTelefono(nuevo.telefono);
  const nomN = normalizarNombreContacto(nuevo.nombre);
  const nomTokens = new Set(nomN.split(" ").filter((t) => t.length >= 3));

  const duplicados: CandidatoDuplicadoContacto[] = [];
  for (const ex of existentes) {
    const exRif = normalizarRif(ex.rif_normalizado || ex.rif);
    if (rifN && exRif && rifN === exRif) {
      duplicados.push({ motivo: "RIF_IDENTICO", bloqueante: true, contacto: ex });
      continue;
    }
    const exTel = normalizarTelefono(ex.telefono);
    if (telN && exTel && telN.length >= 10 && telN === exTel) {
      duplicados.push({ motivo: "TELEFONO_IDENTICO", bloqueante: false, contacto: ex });
      continue;
    }
    const exNom = normalizarNombreContacto(ex.nombre_normalizado || ex.nombre);
    if (nomN && exNom) {
      if (nomN === exNom) {
        duplicados.push({ motivo: "NOMBRE_SIMILAR", bloqueante: false, contacto: ex });
        continue;
      }
      if (nomTokens.size >= 2) {
        const exTokens = new Set(exNom.split(" ").filter((t) => t.length >= 3));
        const comun = [...nomTokens].filter((t) => exTokens.has(t)).length;
        const ratio = comun / Math.max(nomTokens.size, exTokens.size);
        if (ratio >= 0.75) {
          duplicados.push({ motivo: "NOMBRE_SIMILAR", bloqueante: false, contacto: ex });
        }
      }
    }
  }
  return duplicados;
}
