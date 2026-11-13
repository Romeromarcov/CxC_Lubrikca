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
    # Antes que "nomina": "Comisión servicio pago de nómina" es una comisión, no nómina.
    Regla(
        "comision",
        "comision_bancaria",
        r"^COM[\s.]|^COMIS|^COM(SERV|ENVIO)|COMISI[OÓ]N|CARGO POR SERVICIO|"
        r"MANTENIMIENTO DE CUENTA|^MANT AFIL|ENVIO DE SMS|EMISION ESTADO|USO (DE )?CANAL|"
        r"PIN INVALIDO|CEDULA INVALIDA|SERVICIO MENSAJERIA|SALDO INSUFICIENTE",
        "debito",
    ),
    Regla("nomina", "nomina", r"N[OÓ]MINA", "debito"),
    Regla("interes_islr", "impuesto_retencion", r"INT\.?\s*ISLR"),
    Regla("interes", "interes_ganado", r"\bINTERES(ES)?\b", "credito"),
    Regla("impuesto", "impuesto", r"PAGO IMPUESTOS|SENIAT"),
    Regla("parafiscal", "parafiscal", r"\bIVSS\b|BANAVIH|\bINCES\b"),
    Regla(
        "servicio_publico", "servicio", r"HIDROCAPITAL|CORPOELEC|CANTV|DIGITEL|MOVISTAR|MOVILNET"
    ),
    # Débito en la cuenta en Bs, crédito en la cuenta en divisas: las dos patas.
    Regla("operacion_cambiaria", "operacion_cambiaria", r"COMPRA USD|VENTA USD|INTERVENCION"),
    # Binance: la descripción la arma el parser ("<Operación> <moneda> (<cuenta>) - ...").
    Regla("binance_pay_entra", "cobro_candidato", r"^TRANSFER USDT.*BINANCE PAY", "credito"),
    Regla("binance_pay_sale", "pago_a_terceros", r"^TRANSFER USDT.*BINANCE PAY", "debito"),
    Regla("binance_deposito", "transferencia_propia_candidata", r"^DEPOSIT USD"),
    Regla("binance_conversion", "conversion_cripto", r"^BUY CRYPTO WITH FIAT"),
    Regla("binance_retiro", "retiro_cripto", r"^WITHDRAW USDT", "debito"),
    Regla("compra_internacional", "compra_tarjeta", r"OP\.? INTERNAC\.? MASTERDEBIT", "debito"),
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
