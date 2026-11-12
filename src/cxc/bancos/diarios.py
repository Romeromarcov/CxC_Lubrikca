"""Banco -> diario de Odoo (``account.journal``) y moneda de la cuenta.

Fuente: export 'Diario_account.journal' de Odoo (12-nov-2026). ``nombre`` tiene que
coincidir EXACTO con el nombre del diario en Odoo: es lo que el asistente de
importación usa para resolver ``journal_id`` en el CSV de líneas de extracto.
"""

from __future__ import annotations

from typing import TypedDict


class Diario(TypedDict):
    codigo: str
    nombre: str
    cuenta: str
    moneda: str


DIARIOS: dict[str, Diario] = {
    "VENEZUELA": {
        "codigo": "BDV",
        "nombre": "Banco de Venezuela 6891",
        "cuenta": "1.1.1.2.00.01 Banco de Venezuela 6891",
        "moneda": "VES",
    },
    # Ojo: en Odoo el diario dice 7806 y la cuenta contable dice 8988. Se copia tal cual.
    "BANCAMIGA": {
        "codigo": "BAMI",
        "nombre": "Banco Bancamiga 7806",
        "cuenta": "1.1.1.2.00.02 Banco Bancamiga 8988",
        "moneda": "VES",
    },
    "BDT": {
        "codigo": "BDT",
        "nombre": "Banco Digital de los Trabajadores",
        "cuenta": "1.1.1.2000003 Banco Digital de los Trabajadores",
        "moneda": "VES",
    },
    "BANESCO": {
        "codigo": "BBAN",
        "nombre": "Banco Banesco",
        "cuenta": "1.1.1.2.00.04 Banco Banesco",
        "moneda": "VES",
    },
    "PROVINCIAL": {
        "codigo": "BPRO",
        "nombre": "Banco Provincial",
        "cuenta": "1.1.1.2.00.05 Banco Provincial",
        "moneda": "VES",
    },
    # Sin archivo aún / futuros conectores:
    "BNC": {
        "codigo": "BNC1",
        "nombre": "Banco Nacional de Crédito",
        "cuenta": "1.1.1.2.00.03 Banco Nacional de Credito",
        "moneda": "VES",
    },
    "BINANCE": {
        "codigo": "BNB",
        "nombre": "Binance",
        "cuenta": "1.1.1.2000002 Binance",
        "moneda": "USD",
    },
    "ZELLE_FLUASA": {
        "codigo": "ZEFLU",
        "nombre": "Zelle Fluasa",
        "cuenta": "1.1.1.2000001 Zelle Fluasa",
        "moneda": "USD",
    },
}
