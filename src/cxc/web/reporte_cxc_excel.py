"""Export a Excel de «Cuentas por Cobrar por Cliente» (página Reporte de Saldos).

No calcula ningún saldo: recibe la lista ``clientes`` que ya devuelve
``/api/reporte-cxc-cliente`` y la vuelca a un libro de dos hojas:

- **Resumen por Cliente**: una fila por cliente con sus 4 saldos, lo
  cobrable, el descuento comprometido y los créditos (saldo a favor y
  pendiente por aplicar), con una fila de totales al pie.
- **Detalle por Documento**: una fila por orden o pago sin aplicar, con el
  monto original y el saldo de cada referencia y el tramo de antigüedad,
  para poder pivotear por vendedor o por tramo en Excel.

Los filtros y el orden son los MISMOS que ``applyReporteClienteFilters``
aplica en el navegador (ver app.js), para que el archivo coincida con lo
que el usuario ve en pantalla.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

SALDOS = ("teorico_bs", "teorico_usd", "venta_real", "factura_real")
SALDOS_LABELS = {
    "teorico_bs": "Teórico Lista BS ($)",
    "teorico_usd": "Teórico Lista USD ($)",
    "venta_real": "Venta Real ($)",
    "factura_real": "Factura Neta Real ($)",
}

ANTIGUEDAD_LABELS = {
    "*": "Todas las antigüedades",
    "vencido_total": "Total vencidas",
    "vigentes": "Vigentes (sin vencer)",
    "1_30": "Vencidas 1 - 30 días",
    "31_60": "Vencidas 31 - 60 días",
    "61_90": "Vencidas 61 - 90 días",
    "mas_90": "Vencidas +90 días",
}

_FMT_USD = "#,##0.00;[Red]-#,##0.00"
_HEADER_FILL = PatternFill("solid", fgColor="5B21B6")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_TOTAL_FILL = PatternFill("solid", fgColor="EDE9FE")
_HEADER_ROW = 4  # filas 1-2: título y parámetros; fila 3 en blanco


def tramo_antiguedad(dias_vencido: int) -> str:
    if dias_vencido <= 0:
        return "Vigente"
    if dias_vencido <= 30:
        return "1-30 días"
    if dias_vencido <= 60:
        return "31-60 días"
    if dias_vencido <= 90:
        return "61-90 días"
    return "+90 días"


TIPOS_DOCUMENTO = {
    "orden": "Orden",
    "factura_sin_orden": "Factura sin orden",
    "pago_huerfano": "Pago sin aplicar",
}


def columnas_documento(d: dict[str, Any]) -> list[Any]:
    """Tipo, Orden, Factura y Pago de un documento, para las hojas de detalle."""
    tipo = str(d.get("tipo") or "")
    es_pago = tipo == "pago_huerfano"
    return [
        TIPOS_DOCUMENTO.get(tipo, tipo),
        d.get("so_id") if tipo == "orden" else None,
        None if es_pago else d.get("factura_numero"),
        (d.get("numero_pago_odoo") or d.get("pago_id")) if es_pago else None,
    ]


def _match_antiguedad(dv: int, antiguedad: str) -> bool:
    checks: dict[str, Callable[[int], bool]] = {
        "vencido_total": lambda d: d > 0,
        "vigentes": lambda d: d <= 0,
        "1_30": lambda d: 1 <= d <= 30,
        "31_60": lambda d: 31 <= d <= 60,
        "61_90": lambda d: 61 <= d <= 90,
        "mas_90": lambda d: d > 90,
    }
    check = checks.get(antiguedad)
    return check(dv) if check else True


def filtrar_y_ordenar(
    clientes: list[dict[str, Any]],
    antiguedad: str = "*",
    vendedor: str = "*",
    search: str = "",
    sort: str = "saldo_desc",
) -> list[dict[str, Any]]:
    """Réplica de ``applyReporteClienteFilters`` (app.js)."""
    search_l = search.strip().lower()

    def _ok(c: dict[str, Any]) -> bool:
        dv = int(c.get("dias_vencido_max") or 0)
        if vendedor != "*" and c.get("vendedor") != vendedor:
            return False
        if not _match_antiguedad(dv, antiguedad):
            return False
        if search_l:
            nombre = str(c.get("cliente_nombre") or "").lower()
            vend = str(c.get("vendedor") or "").lower()
            if search_l not in nombre and search_l not in vend:
                return False
        return True

    filtrados = [c for c in clientes if _ok(c)]

    def _saldo(c: dict[str, Any]) -> float:
        return float(c.get("saldo_priorizacion") or 0.0)

    def _dias(c: dict[str, Any]) -> int:
        return int(c.get("dias_vencido_max") or 0)

    orden: dict[str, tuple[Callable[[dict[str, Any]], Any], bool]] = {
        "saldo_asc": (_saldo, False),
        "antiguedad_desc": (_dias, True),
        "antiguedad_asc": (_dias, False),
        "cliente_asc": (lambda c: str(c.get("cliente_nombre") or "").lower(), False),
        "vendedor_asc": (lambda c: str(c.get("vendedor") or "").lower(), False),
    }
    key, reverse = orden.get(sort, (_saldo, True))
    return sorted(filtrados, key=key, reverse=reverse)


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _fecha(v: Any) -> Any:
    if isinstance(v, str) and len(v) >= 10:
        try:
            return datetime.strptime(v[:10], "%Y-%m-%d").date()
        except ValueError:
            return v
    return v


def _encabezado(ws: Worksheet, titulo: str, parametros: str, columnas: list[str]) -> None:
    ws.cell(row=1, column=1, value=titulo).font = Font(bold=True, size=14, color="5B21B6")
    ws.cell(row=2, column=1, value=parametros).font = Font(italic=True, color="64748B")
    for i, label in enumerate(columnas, start=1):
        cell = ws.cell(row=_HEADER_ROW, column=i, value=label)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[_HEADER_ROW].height = 32
    ws.freeze_panes = ws.cell(row=_HEADER_ROW + 1, column=2)


def _anchos(ws: Worksheet, anchos: list[int]) -> None:
    for i, w in enumerate(anchos, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _hoja_resumen(ws: Worksheet, clientes: list[dict[str, Any]], parametros: str) -> None:
    columnas = [
        "Cliente",
        "ID Cliente",
        "Vendedor",
        "Días Vencido (máx)",
        "Tramo Antigüedad",
        *(SALDOS_LABELS[k] for k in SALDOS),
        "Saldo Priorización ($)",
        "Descuento Comprometido ($)",
        "Saldo Cobrable ($)",
        "Saldo a Favor ($)",
        "Pendiente por Aplicar ($)",
        "Nº Documentos",
    ]
    _encabezado(ws, "Cuentas por Cobrar por Cliente — Lubrikca", parametros, columnas)
    primera_moneda, ultima_moneda = 6, 6 + len(SALDOS) + 5 - 1

    fila = _HEADER_ROW
    for c in clientes:
        fila += 1
        saldos = c.get("saldos") or {}
        dv = int(c.get("dias_vencido_max") or 0)
        valores = [
            c.get("cliente_nombre") or c.get("cliente_id"),
            c.get("cliente_id"),
            c.get("vendedor") or "Sin Vendedor",
            dv,
            tramo_antiguedad(dv),
            *(_num(saldos.get(k)) for k in SALDOS),
            _num(c.get("saldo_priorizacion")),
            _num(c.get("descuento_comprometido")),
            _num(c.get("saldo_cobrable")),
            _num(c.get("saldo_a_favor")),
            _num(c.get("saldo_pendiente_por_aplicar")),
            len(c.get("documentos") or []),
        ]
        for col, v in enumerate(valores, start=1):
            cell = ws.cell(row=fila, column=col, value=v)
            if primera_moneda <= col <= ultima_moneda:
                cell.number_format = _FMT_USD

    # Totales con fórmula: si el usuario borra o filtra filas, siguen cuadrando.
    total_row = fila + 1
    ws.cell(row=total_row, column=1, value=f"TOTAL ({len(clientes)} clientes)")
    for col in range(1, len(columnas) + 1):
        cell = ws.cell(row=total_row, column=col)
        cell.font = Font(bold=True)
        cell.fill = _TOTAL_FILL
        if primera_moneda <= col <= ultima_moneda and clientes:
            letra = get_column_letter(col)
            cell.value = f"=SUBTOTAL(9,{letra}{_HEADER_ROW + 1}:{letra}{fila})"
            cell.number_format = _FMT_USD

    if clientes:
        ws.auto_filter.ref = f"A{_HEADER_ROW}:{get_column_letter(len(columnas))}{fila}"
    _anchos(ws, [40, 12, 26, 12, 14, 16, 16, 16, 16, 16, 16, 16, 14, 16, 12])


def _hoja_detalle(ws: Worksheet, clientes: list[dict[str, Any]], parametros: str) -> None:
    columnas = [
        "Cliente",
        "Vendedor",
        "Tipo",
        "Orden (SO)",
        "Factura",
        "Pago",
        "Descripción",
        "Fecha",
        "Días Vencido",
        "Tramo Antigüedad",
        *(f"Original {SALDOS_LABELS[k]}" for k in SALDOS),
        *(f"Saldo {SALDOS_LABELS[k]}" for k in SALDOS),
        "Bandeja Destino",
    ]
    _encabezado(ws, "Detalle por Documento — Lubrikca", parametros, columnas)
    primera_moneda, ultima_moneda = 11, 11 + 2 * len(SALDOS) - 1

    fila = _HEADER_ROW
    for c in clientes:
        for d in c.get("documentos") or []:
            fila += 1
            originales = d.get("montos_originales") or {}
            saldos = d.get("saldos") or {}
            dv = int(d.get("dias_vencido") or 0)
            valores = [
                c.get("cliente_nombre") or c.get("cliente_id"),
                c.get("vendedor") or "Sin Vendedor",
                *columnas_documento(d),
                d.get("descripcion"),
                _fecha(d.get("fecha")),
                dv,
                tramo_antiguedad(dv),
                *(_num(originales.get(k)) for k in SALDOS),
                *(_num(saldos.get(k)) for k in SALDOS),
                d.get("bandeja_destino"),
            ]
            for col, v in enumerate(valores, start=1):
                cell = ws.cell(row=fila, column=col, value=v)
                if primera_moneda <= col <= ultima_moneda:
                    cell.number_format = _FMT_USD
                elif isinstance(v, date):
                    cell.number_format = "DD/MM/YYYY"

    if fila > _HEADER_ROW:
        ws.auto_filter.ref = f"A{_HEADER_ROW}:{get_column_letter(len(columnas))}{fila}"
    _anchos(ws, [40, 26, 16, 14, 12, 16, 44, 12, 10, 14, *([16] * 8), 16])


def construir_libro(
    clientes: list[dict[str, Any]],
    antiguedad: str = "*",
    vendedor: str = "*",
    search: str = "",
    hoy: date | None = None,
) -> bytes:
    hoy = hoy or date.today()
    partes = [
        f"Generado: {hoy.strftime('%d/%m/%Y')}",
        f"Antigüedad: {ANTIGUEDAD_LABELS.get(antiguedad, antiguedad)}",
        f"Vendedor: {'Todos' if vendedor == '*' else vendedor}",
    ]
    if search.strip():
        partes.append(f"Búsqueda: «{search.strip()}»")
    parametros = " · ".join(partes)

    wb = Workbook()
    ws_resumen = wb.active
    ws_resumen.title = "Resumen por Cliente"
    _hoja_resumen(ws_resumen, clientes, parametros)
    _hoja_detalle(wb.create_sheet("Detalle por Documento"), clientes, parametros)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
