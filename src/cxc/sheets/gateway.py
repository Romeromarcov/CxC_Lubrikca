"""Acceso de bajo nivel a las pestañas de Google Sheets.

``SheetGateway`` abstrae "una hoja = una tabla de filas dict". El repositorio se
construye encima. ``InMemorySheetGateway`` permite probar toda la serialización
sin red; ``GspreadGateway`` es el binding real.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

# Nombres de las pestañas (deben coincidir con el Google Sheet real).
T_CLIENTES = "Clientes"
T_ORDENES = "OrdenesVenta"
T_LINEAS = "LineasOrden"
T_PAGOS = "Pagos"
T_METODOS = "MetodosPago"
T_SERIE = "SerieTasas"
T_DESCUENTOS = "DescuentosMarcaCategoria"
T_REGLAS = "ReglasRecurrencia"
T_FERIADOS = "Feriados"
T_VINCULACIONES = "Vinculaciones"
T_BANDEJA = "BandejaFacturacion"
T_CONCILIACION = "Conciliacion"
T_META = "_Meta"  # cursor de sync y otros estados internos


class SheetGateway(ABC):
    @abstractmethod
    def read_rows(self, table: str) -> list[dict[str, str]]: ...

    @abstractmethod
    def append_row(self, table: str, row: Mapping[str, str]) -> None: ...

    @abstractmethod
    def upsert_row(self, table: str, pk_field: str, row: Mapping[str, str]) -> None: ...

    @abstractmethod
    def get_meta(self, key: str) -> str | None: ...

    @abstractmethod
    def set_meta(self, key: str, value: str) -> None: ...


class InMemorySheetGateway(SheetGateway):
    """Backend en memoria — espejo del comportamiento de gspread para tests."""

    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, str]]] = {}
        self._meta: dict[str, str] = {}

    def seed(self, table: str, rows: list[dict[str, str]]) -> None:
        self._tables[table] = [dict(r) for r in rows]

    def read_rows(self, table: str) -> list[dict[str, str]]:
        return [dict(r) for r in self._tables.get(table, [])]

    def append_row(self, table: str, row: Mapping[str, str]) -> None:
        self._tables.setdefault(table, []).append(dict(row))

    def upsert_row(self, table: str, pk_field: str, row: Mapping[str, str]) -> None:
        filas = self._tables.setdefault(table, [])
        clave = row[pk_field]
        for i, existente in enumerate(filas):
            if existente.get(pk_field) == clave:
                filas[i] = dict(row)
                return
        filas.append(dict(row))

    def get_meta(self, key: str) -> str | None:
        return self._meta.get(key)

    def set_meta(self, key: str, value: str) -> None:
        self._meta[key] = value


class GspreadGateway(SheetGateway):  # pragma: no cover - red externa (Google API)
    """Binding real sobre gspread. Cada tabla es una pestaña con cabecera en fila 1."""

    def __init__(self, spreadsheet_id: str, service_account_file: str) -> None:
        import gspread

        self._gc = gspread.service_account(  # type: ignore[attr-defined]
            filename=service_account_file
        )
        self._sh = self._gc.open_by_key(spreadsheet_id)

    def _ws(self, table: str):  # type: ignore[no-untyped-def]
        return self._sh.worksheet(table)

    def read_rows(self, table: str) -> list[dict[str, str]]:
        records = self._ws(table).get_all_records()
        return [{k: str(v) for k, v in rec.items()} for rec in records]

    def append_row(self, table: str, row: Mapping[str, str]) -> None:
        ws = self._ws(table)
        header = ws.row_values(1)
        ws.append_row([row.get(col, "") for col in header])

    def upsert_row(self, table: str, pk_field: str, row: Mapping[str, str]) -> None:
        ws = self._ws(table)
        header = ws.row_values(1)
        col_idx = header.index(pk_field) + 1
        celdas = ws.col_values(col_idx)
        valores = [row.get(col, "") for col in header]
        for fila_num, valor in enumerate(celdas[1:], start=2):
            if valor == row[pk_field]:
                ws.update(f"A{fila_num}", [valores])
                return
        ws.append_row(valores)

    def get_meta(self, key: str) -> str | None:
        for rec in self.read_rows(T_META):
            if rec.get("key") == key:
                return rec.get("value")
        return None

    def set_meta(self, key: str, value: str) -> None:
        self.upsert_row(T_META, "key", {"key": key, "value": value})
