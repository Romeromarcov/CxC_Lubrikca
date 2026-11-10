"""Cuentas por Cobrar agrupadas por VENDEDOR (página Reporte de Saldos).

No calcula ningún saldo: reparte los documentos que ya trae
``/api/reporte-cxc-cliente`` (cada uno con su saldo por las 4 referencias)
entre vendedores.

El reparto es **documento por documento**, no cliente por cliente: un
cliente atendido por dos vendedores aporta a cada uno solo sus propias
órdenes. Así la suma de todos los vendedores es exactamente el total de la
vista por cliente -- ningún documento queda en dos vendedores ni en
ninguno.

- Una orden va al vendedor de la orden (``documento["vendedor"]``).
- Un pago sin aplicar va al vendedor de la orden a la que el FIFO sugiere
  aplicarlo; si no hay orden sugerida, al vendedor del cliente cuando es
  uno solo; si el cliente tiene varios, a «Sin Vendedor».

La antigüedad por tramos se mide sobre el saldo de **Venta Real** de las
órdenes (la cifra con la que se sale a cobrar). Los pagos sin aplicar van
en su propia columna, negativa, en vez de mezclarse en un tramo: así
``tramos + pagos sin aplicar = saldo Venta Real``.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from datetime import date
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from cxc.web.reporte_cxc_excel import (
    _FMT_USD,
    _HEADER_ROW,
    _TOTAL_FILL,
    SALDOS,
    SALDOS_LABELS,
    _anchos,
    _encabezado,
    _fecha,
    _num,
    columnas_documento,
    tramo_antiguedad,
)

SIN_VENDEDOR = "Sin Vendedor"
TRAMOS = ("vigente", "1_30", "31_60", "61_90", "mas_90")
TRAMOS_LABELS = {
    "vigente": "Vigente",
    "1_30": "1-30 días",
    "31_60": "31-60 días",
    "61_90": "61-90 días",
    "mas_90": "+90 días",
}


def _clave_tramo(dias_vencido: int) -> str:
    if dias_vencido <= 0:
        return "vigente"
    if dias_vencido <= 30:
        return "1_30"
    if dias_vencido <= 60:
        return "31_60"
    if dias_vencido <= 90:
        return "61_90"
    return "mas_90"


def _acumulador() -> dict[str, Any]:
    return {
        "saldos": dict.fromkeys(SALDOS, 0.0),
        "antiguedad": dict.fromkeys(TRAMOS, 0.0),
        "pagos_sin_aplicar": 0.0,
        "n_ordenes": 0,
        "n_pagos": 0,
        "dias_vencido_max": 0,
    }


def _sumar(acc: dict[str, Any], doc: dict[str, Any]) -> None:
    saldos = doc.get("saldos") or {}
    for k in SALDOS:
        v = saldos.get(k)
        if v is not None:
            acc["saldos"][k] += float(v)
    venta_real = float(saldos.get("venta_real") or 0.0)
    # Una factura sin orden es deuda igual que una orden: cuenta en los tramos.
    if doc.get("tipo") in ("orden", "factura_sin_orden"):
        dv = int(doc.get("dias_vencido") or 0)
        acc["n_ordenes"] += 1
        acc["dias_vencido_max"] = max(acc["dias_vencido_max"], dv)
        acc["antiguedad"][_clave_tramo(dv)] += venta_real
    else:
        acc["n_pagos"] += 1
        acc["pagos_sin_aplicar"] += venta_real


def _redondear(acc: dict[str, Any]) -> None:
    for grupo in ("saldos", "antiguedad"):
        for k in acc[grupo]:
            acc[grupo][k] = round(acc[grupo][k], 2)
    acc["pagos_sin_aplicar"] = round(acc["pagos_sin_aplicar"], 2)
    acc["saldo_priorizacion"] = round(
        max(acc["saldos"]["venta_real"], acc["saldos"]["teorico_usd"]), 2
    )


def vendedor_del_documento(doc: dict[str, Any], cliente: dict[str, Any]) -> str:
    vend = str(doc.get("vendedor") or "").strip()
    if vend:
        return vend
    # Pago sin orden sugerida: solo se le asigna al vendedor del cliente si
    # es uno solo; con varios no hay forma honesta de elegir.
    vend_cliente = str(cliente.get("vendedor") or "").strip()
    if vend_cliente and "," not in vend_cliente:
        return vend_cliente
    return SIN_VENDEDOR


def agrupar_por_vendedor(clientes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Vendedores con su total y el desglose por cliente, mayor saldo primero."""
    vendedores: dict[str, dict[str, Any]] = {}
    for c in clientes:
        for doc in c.get("documentos") or []:
            nombre = vendedor_del_documento(doc, c)
            v = vendedores.setdefault(nombre, {"vendedor": nombre, **_acumulador(), "_cli": {}})
            cid = str(c.get("cliente_id") or "")
            vc = v["_cli"].setdefault(
                cid,
                {
                    "cliente_id": cid,
                    "cliente_nombre": c.get("cliente_nombre") or cid,
                    **_acumulador(),
                    "documentos": [],
                },
            )
            _sumar(v, doc)
            _sumar(vc, doc)
            vc["documentos"].append(doc)

    resultado: list[dict[str, Any]] = []
    for v in vendedores.values():
        clientes_v = list(v.pop("_cli").values())
        for vc in clientes_v:
            _redondear(vc)
        clientes_v.sort(key=lambda x: -x["saldo_priorizacion"])
        _redondear(v)
        v["clientes"] = clientes_v
        v["n_clientes"] = len(clientes_v)
        resultado.append(v)
    resultado.sort(key=lambda x: -x["saldo_priorizacion"])
    return resultado


def filtrar_y_ordenar(
    vendedores: list[dict[str, Any]], search: str = "", sort: str = "saldo_desc"
) -> list[dict[str, Any]]:
    """Réplica de ``applyReporteVendedorFilters`` (app.js)."""
    s = search.strip().lower()
    filtrados = [v for v in vendedores if not s or s in str(v.get("vendedor") or "").lower()]

    def _saldo(v: dict[str, Any]) -> float:
        return float(v.get("saldo_priorizacion") or 0.0)

    def _vencido(v: dict[str, Any]) -> float:
        a = v.get("antiguedad") or {}
        return sum(float(a.get(k) or 0.0) for k in TRAMOS if k != "vigente")

    orden: dict[str, tuple[Callable[[dict[str, Any]], Any], bool]] = {
        "saldo_asc": (_saldo, False),
        "vencido_desc": (_vencido, True),
        "vendedor_asc": (lambda v: str(v.get("vendedor") or "").lower(), False),
    }
    key, reverse = orden.get(sort, (_saldo, True))
    return sorted(filtrados, key=key, reverse=reverse)


def totales(vendedores: list[dict[str, Any]]) -> dict[str, Any]:
    t = _acumulador()
    for v in vendedores:
        for grupo in ("saldos", "antiguedad"):
            for k in t[grupo]:
                t[grupo][k] += float(v[grupo][k])
        t["pagos_sin_aplicar"] += float(v["pagos_sin_aplicar"])
        t["n_ordenes"] += int(v["n_ordenes"])
        t["n_pagos"] += int(v["n_pagos"])
        t["dias_vencido_max"] = max(t["dias_vencido_max"], int(v["dias_vencido_max"]))
    _redondear(t)
    t["n_vendedores"] = len(vendedores)
    return t


# ── Excel ────────────────────────────────────────────────────────────────

_COLS_MONTO = [
    *(f"{TRAMOS_LABELS[k]} ($)" for k in TRAMOS),
    "Pagos sin Aplicar ($)",
    *(SALDOS_LABELS[k] for k in ("venta_real", "teorico_usd", "teorico_bs", "factura_real")),
    "Saldo Priorización ($)",
]


def _valores_monto(acc: dict[str, Any]) -> list[float | None]:
    return [
        *(_num(acc["antiguedad"][k]) for k in TRAMOS),
        _num(acc["pagos_sin_aplicar"]),
        *(
            _num(acc["saldos"][k])
            for k in ("venta_real", "teorico_usd", "teorico_bs", "factura_real")
        ),
        _num(acc["saldo_priorizacion"]),
    ]


def _escribir_filas(
    ws: Worksheet,
    filas: list[list[Any]],
    primera_moneda: int,
    n_cols: int,
    etiqueta_total: str,
) -> None:
    fila = _HEADER_ROW
    for valores in filas:
        fila += 1
        for col, v in enumerate(valores, start=1):
            cell = ws.cell(row=fila, column=col, value=v)
            if col >= primera_moneda and isinstance(v, float):
                cell.number_format = _FMT_USD
    total_row = fila + 1
    ws.cell(row=total_row, column=1, value=etiqueta_total)
    for col in range(1, n_cols + 1):
        cell = ws.cell(row=total_row, column=col)
        cell.font = Font(bold=True)
        cell.fill = _TOTAL_FILL
        if col >= primera_moneda and filas:
            letra = get_column_letter(col)
            cell.value = f"=SUBTOTAL(9,{letra}{_HEADER_ROW + 1}:{letra}{fila})"
            cell.number_format = _FMT_USD
    if filas:
        ws.auto_filter.ref = f"A{_HEADER_ROW}:{get_column_letter(n_cols)}{fila}"


def _hoja_resumen(ws: Worksheet, vendedores: list[dict[str, Any]], parametros: str) -> None:
    columnas = ["Vendedor", "Nº Clientes", "Nº Documentos", "Días Vencido (máx)", *_COLS_MONTO]
    _encabezado(ws, "Cuentas por Cobrar por Vendedor — Lubrikca", parametros, columnas)
    filas = [
        [v["vendedor"], v["n_clientes"], v["n_ordenes"], v["dias_vencido_max"], *_valores_monto(v)]
        for v in vendedores
    ]
    _escribir_filas(ws, filas, 5, len(columnas), f"TOTAL ({len(vendedores)} vendedores)")
    _anchos(ws, [30, 10, 10, 12, *([15] * len(_COLS_MONTO))])


def _hoja_vendedor_cliente(
    ws: Worksheet, vendedores: list[dict[str, Any]], parametros: str
) -> None:
    columnas = [
        "Vendedor",
        "Cliente",
        "ID Cliente",
        "Nº Documentos",
        "Días Vencido (máx)",
        *_COLS_MONTO,
    ]
    _encabezado(ws, "Cuentas por Cobrar por Vendedor y Cliente — Lubrikca", parametros, columnas)
    filas = [
        [
            v["vendedor"],
            vc["cliente_nombre"],
            vc["cliente_id"],
            vc["n_ordenes"],
            vc["dias_vencido_max"],
            *_valores_monto(vc),
        ]
        for v in vendedores
        for vc in v["clientes"]
    ]
    _escribir_filas(ws, filas, 6, len(columnas), f"TOTAL ({len(filas)} filas)")
    _anchos(ws, [26, 40, 12, 10, 12, *([15] * len(_COLS_MONTO))])


def _hoja_detalle(ws: Worksheet, vendedores: list[dict[str, Any]], parametros: str) -> None:
    columnas = [
        "Vendedor",
        "Cliente",
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
    ]
    _encabezado(ws, "Detalle por Documento (por Vendedor) — Lubrikca", parametros, columnas)
    fila = _HEADER_ROW
    for v in vendedores:
        for vc in v["clientes"]:
            for d in vc["documentos"]:
                fila += 1
                orig = d.get("montos_originales") or {}
                saldos = d.get("saldos") or {}
                dv = int(d.get("dias_vencido") or 0)
                valores = [
                    v["vendedor"],
                    vc["cliente_nombre"],
                    *columnas_documento(d),
                    d.get("descripcion"),
                    _fecha(d.get("fecha")),
                    dv,
                    tramo_antiguedad(dv),
                    *(_num(orig.get(k)) for k in SALDOS),
                    *(_num(saldos.get(k)) for k in SALDOS),
                ]
                for col, val in enumerate(valores, start=1):
                    cell = ws.cell(row=fila, column=col, value=val)
                    if col >= 11:
                        cell.number_format = _FMT_USD
                    elif isinstance(val, date):
                        cell.number_format = "DD/MM/YYYY"
    if fila > _HEADER_ROW:
        ws.auto_filter.ref = f"A{_HEADER_ROW}:{get_column_letter(len(columnas))}{fila}"
    _anchos(ws, [26, 40, 16, 14, 12, 16, 44, 12, 10, 14, *([16] * 8)])


def construir_libro(
    vendedores: list[dict[str, Any]], search: str = "", hoy: date | None = None
) -> bytes:
    hoy = hoy or date.today()
    partes = [f"Generado: {hoy.strftime('%d/%m/%Y')}"]
    if search.strip():
        partes.append(f"Búsqueda: «{search.strip()}»")
    partes.append("Tramos de antigüedad sobre el saldo de Venta Real de las órdenes")
    parametros = " · ".join(partes)

    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen por Vendedor"
    _hoja_resumen(ws, vendedores, parametros)
    _hoja_vendedor_cliente(wb.create_sheet("Vendedor - Cliente"), vendedores, parametros)
    _hoja_detalle(wb.create_sheet("Detalle por Documento"), vendedores, parametros)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
