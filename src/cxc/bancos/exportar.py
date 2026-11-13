"""Salidas de la fase 1: libro Excel consolidado y CSV de líneas de extracto para Odoo.

El CSV es para el asistente de importación de Odoo (modelo
``account.bank.statement.line``). Este sistema no escribe en Odoo: la carga la hace
una persona, en el diario del banco, y la conciliación nativa de Odoo genera los
asientos. ``unique_import_id`` hace que re-importar el mismo CSV no duplique.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from .diarios import DIARIOS
from .ingesta import MovimientoClasificado
from .modelos import CERO
from .saldos import Consolidado

NUM = "#,##0.00;[Red]-#,##0.00"
COLUMNAS_ODOO = ["unique_import_id", "journal_id", "date", "payment_ref", "ref", "amount"]


def _f(d: Decimal | None) -> float | None:
    return None if d is None else float(d)


def _hoja(ws: Worksheet, encabezado: list[str], anchos: list[int]) -> None:
    ws.append(encabezado)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3864")
    ws.freeze_panes = "A2"
    for i, w in enumerate(anchos):
        ws.column_dimensions[chr(ord("A") + i)].width = w


def libro_consolidado(movs: list[MovimientoClasificado], cons: Consolidado) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Saldos"
    _hoja(
        ws,
        [
            "Banco",
            "Cuenta",
            "Moneda",
            "Fecha corte",
            "Saldo",
            "Tasa BCV",
            "Saldo USD",
            "Saldo VES",
            "Cortes",
            "Cortes sin cuadrar",
            "Alertas",
        ],
        [12, 12, 8, 12, 18, 12, 16, 18, 8, 10, 80],
    )
    for c in cons.cuentas:
        ws.append(
            [
                c.banco,
                c.cuenta,
                c.moneda,
                c.fecha_corte,
                _f(c.saldo),
                _f(c.tasa_bcv),
                _f(c.saldo_usd),
                _f(c.saldo_ves),
                c.n_cortes,
                c.cortes_sin_cuadrar,
                "; ".join(c.alertas),
            ]
        )
    ws.append([])
    ws.append(
        [
            "TOTAL",
            None,
            None,
            None,
            None,
            None,
            float(cons.total_usd),
            float(cons.total_ves),
            None,
            None,
            "cada cuenta a su fecha de corte; tasa BCV de esa fecha"
            + (f"; sin tasa: {', '.join(cons.cuentas_sin_tasa)}" if cons.cuentas_sin_tasa else ""),
        ]
    )
    for row in ws.iter_rows(min_row=2):
        row[3].number_format = "dd/mm/yyyy"
        for cell in row[4:8]:
            cell.number_format = NUM

    ws2 = wb.create_sheet("Movimientos")
    _hoja(
        ws2,
        [
            "Banco",
            "Cuenta",
            "Moneda",
            "Fecha",
            "Referencia",
            "Descripción",
            "Débito",
            "Crédito",
            "Neto",
            "Saldo banco",
            "Categoría",
            "Regla",
            "unique_import_id",
        ],
        [12, 12, 8, 11, 16, 52, 14, 14, 14, 16, 26, 22, 40],
    )
    resumen: dict[tuple[str, str], list[Decimal]] = defaultdict(lambda: [CERO, CERO, CERO])
    for mc in movs:
        m = mc.mov
        ws2.append(
            [
                m.banco,
                m.cuenta,
                m.moneda,
                m.fecha,
                m.referencia,
                m.descripcion,
                float(m.debito),
                float(m.credito),
                float(m.neto),
                _f(m.saldo),
                mc.categoria,
                mc.regla,
                m.unique_import_id,
            ]
        )
        r = resumen[(m.banco, mc.categoria)]
        r[0] += m.debito
        r[1] += m.credito
        r[2] += 1
    for row in ws2.iter_rows(min_row=2):
        row[3].number_format = "dd/mm/yyyy"
        for cell in row[6:10]:
            cell.number_format = NUM
    ws2.auto_filter.ref = ws2.dimensions

    ws3 = wb.create_sheet("Categorías")
    _hoja(ws3, ["Banco", "Categoría", "Movs", "Débitos", "Créditos"], [12, 30, 7, 16, 16])
    for (b, cat), (d, cr, n) in sorted(resumen.items()):
        ws3.append([b, cat, int(n), float(d), float(cr)])
    for row in ws3.iter_rows(min_row=2):
        for cell in row[3:5]:
            cell.number_format = NUM

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def csv_odoo(movs: list[MovimientoClasificado]) -> str:
    """Una línea de extracto por movimiento. ``journal_id`` va por NOMBRE del diario."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNAS_ODOO)
    for mc in movs:
        m = mc.mov
        if m.banco not in DIARIOS or DIARIOS[m.banco]["journal_id"] is None:
            raise KeyError(
                f"El banco {m.banco} no tiene diario en Odoo: filtrá por otro banco "
                "o creá el diario primero"
            )
        w.writerow(
            [
                m.unique_import_id,
                DIARIOS[m.banco]["nombre"],
                m.fecha.isoformat(),
                m.descripcion,
                m.referencia,
                f"{m.neto:.2f}",
            ]
        )
    return buf.getvalue()
