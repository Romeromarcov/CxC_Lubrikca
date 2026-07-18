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
T_BCV_COMPLETO = "DescuentoBCVCompleto"
T_PROMO_PRIMERA = "PromocionPrimeraCompra"
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

    def upsert_rows(
        self, table: str, pk_field: str, rows: list[Mapping[str, str]]
    ) -> None:
        """Upsert por lote. Default: fila por fila. gspread lo optimiza (1 escritura)."""
        for row in rows:
            self.upsert_row(table, pk_field, row)

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

    @classmethod
    def from_oauth(
        cls,
        spreadsheet_id: str,
        client_secret_path: str,
        token_path: str = "authorized_user.json",
    ) -> GspreadGateway:
        """Autentica como usuario (OAuth) en vez de cuenta de servicio.

        Útil cuando la organización bloquea la creación de claves de cuenta de
        servicio (``iam.disableServiceAccountKeyCreation``). La 1ª corrida abre el
        navegador para consentir; el token se cachea en ``token_path``.
        """
        import gspread

        self = cls.__new__(cls)
        self._gc = gspread.oauth(  # type: ignore[attr-defined]
            credentials_filename=client_secret_path,
            authorized_user_filename=token_path,
        )
        self._sh = self._gc.open_by_key(spreadsheet_id)
        return self

    @classmethod
    def from_env_vars(cls, spreadsheet_id: str) -> GspreadGateway:
        """Autentica utilizando las variables de entorno de OAuth token y secrets."""
        import gspread
        import json
        import os
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        token_str = os.environ.get("GOOGLE_TOKEN_JSON")
        secret_str = os.environ.get("GOOGLE_CLIENT_SECRET_JSON")

        if not token_str:
            raise ValueError("Falta GOOGLE_TOKEN_JSON en las variables de entorno.")

        token_info = json.loads(token_str)
        if secret_str:
            secret_info = json.loads(secret_str)
            client_config = secret_info.get("installed") or secret_info.get("web") or {}
            token_info["client_id"] = client_config.get("client_id")
            token_info["client_secret"] = client_config.get("client_secret")

        creds = Credentials.from_authorized_user_info(token_info, scopes=['https://www.googleapis.com/auth/drive'])
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

        self = cls.__new__(cls)
        self._gc = gspread.Client(auth=creds)
        self._sh = self._gc.open_by_key(spreadsheet_id)
        return self

    def _ws(self, table: str):  # type: ignore[no-untyped-def]
        import gspread
        try:
            return self._sh.worksheet(table)
        except gspread.exceptions.WorksheetNotFound:
            # Auto-create sheet with headers if missing
            headers = {
                "DescuentosVolumen": ["regla_id", "marca", "categoria", "litros_minimo", "porcentaje", "activo"],
                "PromocionPrimeraCompra": ["producto", "vigencia_desde", "vigencia_hasta", "activo"],
                "DescuentosMarcaCategoria": ["regla_id", "marca", "categoria", "tipo_descuento", "porcentaje", "vigencia_desde", "vigencia_hasta", "activo"],
                "Feriados": ["fecha", "descripcion", "tipo"]
            }
            cols = headers.get(table, ["id"])
            ws = self._sh.add_worksheet(title=table, rows=1000, cols=20)
            ws.append_row(cols)
            return ws

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

    def upsert_rows(
        self, table: str, pk_field: str, rows: list[Mapping[str, str]]
    ) -> None:
        # Lectura + escritura por lote: 1 read + 1 update por tabla (cuota-seguro).
        if not rows:
            return
        ws = self._ws(table)
        header = ws.row_values(1)
        existentes = ws.get_all_records()
        matriz = [[str(rec.get(col, "")) for col in header] for rec in existentes]
        indice = {
            str(rec.get(pk_field)): i for i, rec in enumerate(existentes)
        }
        for row in rows:
            valores = [row.get(col, "") for col in header]
            clave = row[pk_field]
            if clave in indice:
                matriz[indice[clave]] = valores
            else:
                indice[clave] = len(matriz)
                matriz.append(valores)
        ws.update(values=matriz, range_name="A2")

    def get_meta(self, key: str) -> str | None:
        for rec in self.read_rows(T_META):
            if rec.get("key") == key:
                return rec.get("value")
        return None

    def set_meta(self, key: str, value: str) -> None:
        self.upsert_row(T_META, "key", {"key": key, "value": value})
