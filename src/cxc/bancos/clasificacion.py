"""Clasificación determinística por reglas (primera que coincide gana). Lo no cubierto queda
'por_revisar' -> cola humana; un LLM solo debería actuar sobre ese remanente."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .modelos import MovimientoBancario


@dataclass(frozen=True)
class Regla:
    nombre: str
    categoria: str
    patron: str
    solo: str | None = None  # 'debito' | 'credito' | None


REGLAS: list[Regla] = [
    Regla("nomina_devuelta", "nomina_devuelta", r"N[OÓ]MINA DEVUELTO", "credito"),
    Regla("nomina", "nomina", r"N[OÓ]MINA", "debito"),
    Regla("interes_islr", "impuesto_retencion", r"INT\.?\s*ISLR"),
    Regla("interes", "interes_ganado", r"\bINTERES(ES)?\b", "credito"),
    Regla(
        "comision",
        "comision_bancaria",
        r"^COM[\s.]|^COMIS|COMISI[OÓ]N|CARGO POR SERVICIO|MANTENIMIENTO DE CUENTA|ENVIO DE SMS|"
        r"EMISION ESTADO|USO (DE )?CANAL|PIN INVALIDO|CEDULA INVALIDA|SERVICIO MENSAJERIA",
        "debito",
    ),
    Regla("impuesto", "impuesto", r"PAGO IMPUESTOS|SENIAT"),
    Regla("parafiscal", "parafiscal", r"\bIVSS\b|BANAVIH|\bINCES\b"),
    Regla(
        "servicio_publico", "servicio", r"HIDROCAPITAL|CORPOELEC|CANTV|DIGITEL|MOVISTAR|MOVILNET"
    ),
    Regla(
        "operacion_cambiaria", "operacion_cambiaria", r"COMPRA USD|VENTA USD|INTERVENCION", "debito"
    ),
    Regla("transferencia_propia", "transferencia_propia_candidata", r"MISMO CLIENTE"),
    Regla("consumo_debito", "compra_tarjeta", r"CONSUMO MASTERDEBIT|CONSUMO MAESTRO", "debito"),
    Regla("alcaldia", "impuesto", r"ALCALDIA|ALCALD[IÍ]A", "debito"),
    Regla(
        "transf_saliente",
        "pago_a_terceros",
        r"TRANSF\.?( INTERNET)? TERC|TRANSF EN LINEA|PAGO MOVIL|PAGO M[OÓ]VIL",
        "debito",
    ),
    Regla("nc_transferencia", "cobro_candidato", r"^NC (TRANSF|PAGO DE PROVEEDORES)", "credito"),
    Regla("platco_pos", "operacion_pos", r"INV\.?\s?PLATCO"),
    Regla("cobro_pos", "cobro_pos", r"^(TD|TC) POS\b", "credito"),
    Regla("compra_tarjeta", "compra_tarjeta", r"COMPRAS?\b.*\bPOS\b|COMPRA POS", "debito"),
    Regla(
        "pago_terceros",
        "pago_a_terceros",
        r"PAGO A (TERCEROS|PROVEEDORES)|CR\.?\s?I\.?/?\s?OB|DIST CLIENTE",
        "debito",
    ),
    Regla(
        "entrada_transferencia",
        "cobro_candidato",
        r"CR\.? ?INM|TRF CR INM|CREDITO INMEDIATO|PAGO RECIBIDO|TRANSF RECIBIDA|FONDOS RECIBIDOS|"
        r"PAGO A (TERCEROS|PROVEEDORES)|TRAV\d",
        "credito",
    ),
]


def clasificar(m: MovimientoBancario) -> tuple[str, str]:
    d = m.descripcion.upper()
    for r in REGLAS:
        if r.solo == "debito" and m.debito <= 0:
            continue
        if r.solo == "credito" and m.credito <= 0:
            continue
        if re.search(r.patron, d):
            return r.categoria, r.nombre
    if m.credito > 0 and m.debito == 0:
        return "ingreso_por_revisar", "sin_regla_credito"
    return "por_revisar", "sin_regla"
