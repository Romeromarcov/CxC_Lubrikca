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
from datetime import date, datetime
from decimal import Decimal
from typing import Any

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


_ENCABEZADO_APROBADAS = [
    "Banco",
    "Diario Odoo",
    "Fecha",
    "Referencia",
    "Descripción",
    "Monto",
    "Moneda",
    "Destino",
    "Pago Odoo",
    "Cliente / proveedor",
    "Fecha pago",
    "Cuenta contable",
    "Regla",
    "Estado",
    "Decidida por",
    "Decidida el",
    "Comentario",
    "Huella",
]
_ANCHOS_APROBADAS = [12, 26, 11, 16, 40, 15, 8, 9, 20, 30, 11, 30, 7, 11, 26, 17, 30, 26]


def _info_pago(f: dict[str, Any]) -> dict[str, Any]:
    if f.get("tipo") != "pago":
        return {}
    return next((c for c in f.get("candidatos") or [] if c.get("id") == f.get("pago_id")), {})


def _ya_conciliado(f: dict[str, Any]) -> bool:
    return bool(_info_pago(f).get("ya_conciliado"))


def _hoja_aprobadas(ws: Worksheet, filas: list[dict[str, Any]]) -> None:
    _hoja(ws, _ENCABEZADO_APROBADAS, _ANCHOS_APROBADAS)
    for f in filas:
        pago = _info_pago(f)
        diario = DIARIOS.get(f["banco"])
        fecha_pago = pago.get("fecha")
        decidida = f.get("decidida_en")
        ws.append(
            [
                f["banco"],
                diario["nombre"] if diario and diario["journal_id"] is not None else "(sin diario)",
                f["fecha"],
                f.get("referencia") or "",
                f.get("descripcion") or "",
                _f(f["monto"]),
                f.get("moneda") or "",
                "Pago" if f["tipo"] == "pago" else "Cuenta",
                f.get("pago_nombre") or (f"#{f['pago_id']}" if f.get("pago_id") else ""),
                pago.get("partner") or "",
                date.fromisoformat(fecha_pago) if fecha_pago else None,
                f.get("cuenta") or "",
                f.get("regla") or "",
                f["estado"],
                f.get("decidida_por") or "",
                decidida.replace(microsecond=0) if isinstance(decidida, datetime) else decidida,
                f.get("comentario") or "",
                f["huella"],
            ]
        )
        fila = ws[ws.max_row]
        fila[2].number_format = fila[10].number_format = "dd/mm/yyyy"
        fila[15].number_format = "dd/mm/yyyy hh:mm"
        fila[5].number_format = NUM
    ws.auto_filter.ref = ws.dimensions


def libro_aprobadas(filas: list[dict[str, Any]]) -> bytes:
    """Lo aprobado en la cola, listo para que contabilidad lo aplique a mano en Odoo.

    Una fila por movimiento con el destino decidido: el pago de Odoo contra el que se
    concilia la línea de extracto, o la cuenta contable a la que va. Lo que casó con un
    pago que YA estaba conciliado en Odoo (al generar las propuestas) va en otra hoja:
    es lo que contabilidad ya hizo, sirve para verificar pero no hay que aplicarlo.
    """
    para_aplicar = [f for f in filas if not _ya_conciliado(f)]
    ya_hechas = [f for f in filas if _ya_conciliado(f)]
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Para aplicar"
    _hoja_aprobadas(ws, para_aplicar)
    _hoja_aprobadas(wb.create_sheet("Ya conciliadas en Odoo"), ya_hechas)

    res = wb.create_sheet("Resumen")
    _hoja(
        res,
        ["Banco", "Destino", "Para aplicar", "Monto para aplicar", "Ya conciliadas en Odoo"],
        [14, 10, 13, 20, 22],
    )
    grupos: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for f in filas:
        grupos[(f["banco"], "Pago" if f["tipo"] == "pago" else "Cuenta")].append(f)
    for (banco, destino), fs in sorted(grupos.items()):
        aplicar = [x for x in fs if not _ya_conciliado(x)]
        res.append(
            [
                banco,
                destino,
                len(aplicar),
                _f(sum((x["monto"] for x in aplicar), CERO)),
                len(fs) - len(aplicar),
            ]
        )
        res[res.max_row][3].number_format = NUM
    if not filas:
        res.append(["No hay propuestas aprobadas ni corregidas con estos filtros."])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
