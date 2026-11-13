"""Banco -> diario de Odoo (``account.journal``) y moneda de la cuenta.

Fuente: leído de Odoo (producción, solo lectura) el 13-nov-2026. ``nombre`` tiene que
coincidir EXACTO con el nombre del diario en Odoo: es lo que el asistente de
importación usa para resolver ``journal_id`` en el CSV de líneas de extracto.
``journal_id`` es el id numérico, que usa la pre-conciliación para buscar los pagos
del mismo diario. ``pagos_pendientes`` es la cuenta a la que contabilidad lleva las
compras con tarjeta y la nómina mientras no hay un pago que las cierre.

Una cuenta sin diario en Odoo (``journal_id`` None) se puede cargar, ver y conciliar
contra nada; no se exporta al CSV.
"""

from __future__ import annotations

from typing import TypedDict


class Diario(TypedDict):
    journal_id: int | None
    codigo: str
    nombre: str
    cuenta: str
    moneda: str
    pagos_pendientes: str | None


DIARIOS: dict[str, Diario] = {
    # El diario se llama "Bank" en Odoo. Su cuenta bancaria registrada termina en 6891;
    # el estado de cuenta recibido es de la ...646642 (pendiente de confirmar).
    "VENEZUELA": {
        "journal_id": 14,
        "codigo": "BDV",
        "nombre": "Bank",
        "cuenta": "1.1.1.2.00.01 Bank",
        "moneda": "VES",
        "pagos_pendientes": "1.1.1.3.00.09 Pagos pendientes Banco de Venezuela 6891",
    },
    "BANCAMIGA": {
        "journal_id": 30,
        "codigo": "BAMI",
        "nombre": "Banco Bancamiga 7806",
        "cuenta": "1.1.1.2.00.02 Banco Bancamiga 7806",
        "moneda": "VES",
        "pagos_pendientes": "1.1.1.3.00.12 Pagos pendientes Bancamiga",
    },
    # Cuenta en divisas 0172-0110-75-1101276747: en Odoo existen sus cuentas de
    # pendientes, pero no un diario.
    "BANCAMIGA_USD": {
        "journal_id": None,
        "codigo": "",
        "nombre": "",
        "cuenta": "",
        "moneda": "USD",
        "pagos_pendientes": "1.1.1.3.00.32 Pagos pendientes Bancamiga moneda extranjera Digital",
    },
    "BDT": {
        "journal_id": 37,
        "codigo": "BDT",
        "nombre": "0175*****9321",
        "cuenta": "1.1.1.2000003 0175*****9321",
        "moneda": "VES",
        "pagos_pendientes": "1.1.1.3.00.29 Pagos pendientes Banco Digital de los Trabajadores",
    },
    "BANESCO": {
        "journal_id": 32,
        "codigo": "BBAN",
        "nombre": "Banco Banesco",
        "cuenta": "1.1.1.2.00.04 Banco Banesco",
        "moneda": "VES",
        "pagos_pendientes": "1.1.1.3.00.18 Pagos pendientes Banesco",
    },
    "PROVINCIAL": {
        "journal_id": 33,
        "codigo": "BPRO",
        "nombre": "Banco Provincial",
        "cuenta": "1.1.1.2.00.05 Banco Provincial",
        "moneda": "VES",
        "pagos_pendientes": "1.1.1.3.00.21 Pagos pendientes Banco Provincial",
    },
    "BINANCE": {
        "journal_id": 35,
        "codigo": "BNB",
        "nombre": "Binance",
        "cuenta": "1.1.1.2000002 Binance",
        "moneda": "USD",
        "pagos_pendientes": None,
    },
    # Sin archivo aún / futuros conectores:
    "BNC": {
        "journal_id": 31,
        "codigo": "BNC1",
        "nombre": "Banco Nacional de Credito",
        "cuenta": "1.1.1.2.00.03 Banco Nacional de Credito",
        "moneda": "VES",
        "pagos_pendientes": "1.1.1.3.00.15 Pagos pendientes Banco Nacional de Cred",
    },
    "ZELLE_FLUASA": {
        "journal_id": 34,
        "codigo": "ZEFLU",
        "nombre": "Zelle Fluasa",
        "cuenta": "1.1.1.2000001 Zelle Fluasa",
        "moneda": "USD",
        "pagos_pendientes": None,
    },
}
