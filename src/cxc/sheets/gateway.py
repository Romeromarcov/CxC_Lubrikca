"""Acceso de bajo nivel a las pestañas de Google Sheets.

``SheetGateway`` abstrae "una hoja = una tabla de filas dict". El repositorio se
construye encima. ``InMemorySheetGateway`` permite probar toda la serialización
sin red; ``GspreadGateway`` es el binding real.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

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
T_BANDEJA_AUDITORIA = "BandejaAuditoria"
T_FECHAS_HISTORICAS = "FechasHistoricas"
T_META = "_Meta"  # cursor de sync y otros estados internos


class SheetGateway(ABC):
    @abstractmethod
    def read_rows(self, table: str) -> list[dict[str, str]]: ...

    @abstractmethod
    def append_row(self, table: str, row: Mapping[str, str]) -> None: ...

    @abstractmethod
    def upsert_row(self, table: str, pk_field: str, row: Mapping[str, str]) -> None: ...

    def upsert_rows(self, table: str, pk_field: str, rows: list[Mapping[str, str]]) -> None:
        """Upsert por lote. Default: fila por fila. gspread lo optimiza (1 escritura)."""
        for row in rows:
            self.upsert_row(table, pk_field, row)

    @abstractmethod
    def delete_row(self, table: str, pk_field: str, pk_value: str) -> bool: ...

    @abstractmethod
    def replace_rows(self, table: str, rows: list[Mapping[str, str]]) -> None:
        """Reemplaza el contenido completo de la pestaña (limpia + reescribe).

        Para tablas que se regeneran enteras desde una fuente externa cada
        vez (ej. TasasHistoricasAuditoria vía
        scripts/cargar_tasas_historicas.py), a diferencia de upsert_rows
        (que solo actualiza/agrega, nunca borra filas que ya no vienen en
        el lote).
        """

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

    def delete_row(self, table: str, pk_field: str, pk_value: str) -> bool:
        filas = self._tables.get(table, [])
        clave = str(pk_value).strip().lower()
        nuevas = [r for r in filas if str(r.get(pk_field, "")).strip().lower() != clave]
        eliminado = len(nuevas) < len(filas)
        self._tables[table] = nuevas
        return eliminado

    def replace_rows(self, table: str, rows: list[Mapping[str, str]]) -> None:
        self._tables[table] = [dict(r) for r in rows]

    def get_meta(self, key: str) -> str | None:
        return self._meta.get(key)

    def set_meta(self, key: str, value: str) -> None:
        self._meta[key] = value


class GspreadGateway(SheetGateway):  # pragma: no cover - red externa (Google API)
    """Binding real sobre gspread. Cada tabla es una pestaña con cabecera en fila 1."""

    _gc: Any
    _sh: Any
    _ws_cache: dict[str, Any]
    _read_cache: dict[str, Any]
    _cache_ttl_seconds: float

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
        """Autentica utilizando las variables de entorno de Service Account u OAuth token y
        secrets."""
        import json
        import os

        import gspread

        sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        sa_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        scopes = [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/spreadsheets",
        ]

        if sa_json and sa_json.startswith("{"):
            from google.oauth2.service_account import Credentials as SACredentials

            sa_info = json.loads(sa_json)
            creds = SACredentials.from_service_account_info(  # type: ignore[no-untyped-call]
                sa_info, scopes=scopes
            )
        elif sa_file and os.path.exists(sa_file):
            from google.oauth2.service_account import Credentials as SACredentials

            creds = SACredentials.from_service_account_file(  # type: ignore[no-untyped-call]
                sa_file, scopes=scopes
            )
        else:
            from google.auth.exceptions import RefreshError
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials

            token_str = os.environ.get("GOOGLE_TOKEN_JSON")
            secret_str = os.environ.get("GOOGLE_CLIENT_SECRET_JSON")

            if not token_str:
                raise ValueError(
                    "Falta GOOGLE_TOKEN_JSON o GOOGLE_SERVICE_ACCOUNT_JSON en las variables "
                    "de entorno."
                )

            token_info = json.loads(token_str)
            if secret_str:
                secret_info = json.loads(secret_str)
                client_config = secret_info.get("installed") or secret_info.get("web") or {}
                token_info["client_id"] = client_config.get("client_id")
                token_info["client_secret"] = client_config.get("client_secret")

            creds = Credentials.from_authorized_user_info(  # type: ignore[no-untyped-call]
                token_info, scopes=["https://www.googleapis.com/auth/drive"]
            )
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())  # type: ignore[no-untyped-call]
                except RefreshError as re:
                    raise RuntimeError(
                        "El token de Google OAuth expiró o fue revocado (invalid_grant). "
                        "Es necesario actualizar GOOGLE_TOKEN_JSON en Railway."
                    ) from re

        self = cls.__new__(cls)
        self._gc = gspread.Client(auth=creds)  # type: ignore[attr-defined]
        self._sh = self._gc.open_by_key(spreadsheet_id)
        self._ws_cache = {}
        self._read_cache = {}
        self._cache_ttl_seconds = 120.0
        return self

    def invalidate_cache(self, table: str | None = None) -> None:
        if not hasattr(self, "_read_cache"):
            self._read_cache = {}
        if table:
            self._read_cache.pop(table, None)
        else:
            self._read_cache.clear()

    def _ws(self, table: str):  # type: ignore[no-untyped-def]
        import gspread

        if not hasattr(self, "_ws_cache"):
            self._ws_cache = {}
        if table in self._ws_cache:
            return self._ws_cache[table]

        try:
            ws = self._sh.worksheet(table)
        except gspread.exceptions.WorksheetNotFound:
            # Auto-create sheet with headers if missing
            headers = {
                "UsuariosPlataforma": [
                    "email",
                    "nombre_odoo",
                    "password_hash",
                    "salt",
                    "rol",
                    "activo",
                    "fecha_registro",
                ],
                "DescuentosVolumen": [
                    "regla_id",
                    "marca",
                    "categoria",
                    "litros_minimo",
                    "porcentaje",
                    "vigencia_desde",
                    "vigencia_hasta",
                    "listas_aplicables",
                    "activo",
                ],
                "PromocionPrimeraCompra": [
                    "regla_id",
                    "tipo_beneficio",
                    "productos",
                    "valor",
                    "compra_minima",
                    "regalo_tipo",
                    "vigencia_desde",
                    "vigencia_hasta",
                    "activo",
                ],
                "DescuentosMarcaCategoria": [
                    "regla_id",
                    "marca",
                    "categoria",
                    "tipo_descuento",
                    "porcentaje",
                    "vigencia_desde",
                    "vigencia_hasta",
                    "listas_aplicables",
                    "activo",
                ],
                "DescuentosProntoPago": [
                    "regla_id",
                    "marca",
                    "categoria",
                    "dias_gracia",
                    "porcentaje",
                    "monedas_aplicables",
                    "listas_aplicables",
                    "vigencia_desde",
                    "vigencia_hasta",
                    "activo",
                ],
                "DescuentosRecompra": [
                    "regla_id",
                    "marca",
                    "categoria",
                    "min_cajas",
                    "max_cajas",
                    "porcentaje",
                    "max_usos_mes",
                    "dias_ventana",
                    "vigencia_desde",
                    "vigencia_hasta",
                    "activo",
                ],
                "DescuentosProducto": [
                    "regla_id",
                    "productos",
                    "marca",
                    "categoria",
                    "porcentaje",
                    "monedas_aplicables",
                    "listas_aplicables",
                    "vigencia_desde",
                    "vigencia_hasta",
                    "activo",
                ],
                "DescuentosDiferencialCambiario": [
                    "regla_id",
                    "nombre",
                    "tipo_diferencial",
                    "tipo_calculo",
                    "porcentaje_fijo",
                    "monedas_aplicables",
                    "listas_aplicables",
                    "vigencia_desde",
                    "vigencia_hasta",
                    "activo",
                ],
                "Feriados": ["fecha", "descripcion", "tipo"],
                "Exclusiones": ["regla_tipo_a", "regla_tipo_b", "activo"],
                "AnomaliasAceptadas": [
                    "anomalia_id",
                    "so_id",
                    "factura_id",
                    "tipo_anomalia",
                    "motivo_aceptacion",
                    "aprobado_por",
                    "timestamp_aprobacion",
                ],
                "PagosHuerfanosCerrados": [
                    "pago_id",
                    "motivo",
                    "cerrado_por",
                    "timestamp_cierre",
                ],
            }
            cols = headers.get(table, ["id"])
            ws = self._sh.add_worksheet(title=table, rows=1000, cols=20)
            ws.append_row(cols)

        self._ws_cache[table] = ws
        return ws

    def read_rows(self, table: str) -> list[dict[str, str]]:
        import time

        if not hasattr(self, "_read_cache"):
            self._read_cache = {}
        now = time.time()
        if table in self._read_cache:
            cached_time, cached_records = self._read_cache[table]
            if now - cached_time < getattr(self, "_cache_ttl_seconds", 120.0):
                return [dict(r) for r in cached_records]

        try:
            records = self._ws(table).get_all_records()
            res = [{k: str(v) for k, v in rec.items()} for rec in records]
            self._read_cache[table] = (now, res)
            return [dict(r) for r in res]
        except Exception:
            # Si ocurre error (como 429 Quota Exceeded de Google), retornar cache previo si existe
            if table in self._read_cache:
                _, cached_records = self._read_cache[table]
                return [dict(r) for r in cached_records]
            return []

    def append_row(self, table: str, row: Mapping[str, str]) -> None:
        self.invalidate_cache(table)
        ws = self._ws(table)
        header = ws.row_values(1)
        if not header:
            header = list(row.keys())
            ws.append_row(header)
        ws.append_row([str(row.get(col, "")) for col in header])

    def upsert_row(self, table: str, pk_field: str, row: Mapping[str, str]) -> None:
        self.invalidate_cache(table)
        ws = self._ws(table)
        header = ws.row_values(1)

        if not header or pk_field not in header:
            header = list(row.keys())
            ws.clear()
            ws.append_row(header)
        else:
            missing = [k for k in row if k not in header]
            if missing:
                header.extend(missing)
                ws.update("A1", [header])

        col_idx = header.index(pk_field) + 1
        celdas = ws.col_values(col_idx)
        valores = [str(row.get(col, "")) for col in header]
        target_pk_val = str(row[pk_field]).strip().lower()

        for fila_num, valor in enumerate(celdas[1:], start=2):
            if str(valor).strip().lower() == target_pk_val:
                ws.update(f"A{fila_num}", [valores])
                return
        ws.append_row(valores)

    def upsert_rows(self, table: str, pk_field: str, rows: list[Mapping[str, str]]) -> None:
        # Lectura + escritura por lote: 1 read + 1 update por tabla (cuota-seguro).
        if not rows:
            return
        self.invalidate_cache(table)
        ws = self._ws(table)
        header = ws.row_values(1)

        # Si las filas traen columnas que el header aun no tiene (ej. un
        # campo nuevo del dataclass), se extiende el header en vez de
        # descartar esos valores en silencio -- mismo comportamiento que
        # upsert_row (fila a fila) ya tiene mas arriba.
        columnas_nuevas = []
        for row in rows:
            for col in row:
                if col not in header and col not in columnas_nuevas:
                    columnas_nuevas.append(col)
        if columnas_nuevas:
            header = [*header, *columnas_nuevas]
            ws.update("A1", [header])

        existentes = ws.get_all_records()
        matriz = [[str(rec.get(col, "")) for col in header] for rec in existentes]
        indice = {str(rec.get(pk_field)): i for i, rec in enumerate(existentes)}
        for row in rows:
            clave = row[pk_field]
            # Columnas que la fila NO trae (ausentes del dict, no solo con
            # valor vacio) se preservan tal cual estaban -- evita que un
            # upsert parcial (ej. el sync de Pagos, que solo conoce sus 7
            # campos espejo) borre en silencio columnas de trabajo humano
            # (recibido, tasa_bcv, etc.) que otro flujo ya habia escrito.
            fila_previa = matriz[indice[clave]] if clave in indice else None
            valores = [
                row[col]
                if col in row
                else (fila_previa[i] if fila_previa is not None else "")
                for i, col in enumerate(header)
            ]
            if clave in indice:
                matriz[indice[clave]] = valores
            else:
                indice[clave] = len(matriz)
                matriz.append(valores)
        ws.update(values=matriz, range_name="A2")

    def delete_row(self, table: str, pk_field: str, pk_value: str) -> bool:
        self.invalidate_cache(table)
        ws = self._ws(table)
        header = ws.row_values(1)
        if not header or pk_field not in header:
            return False
        col_idx = header.index(pk_field) + 1
        celdas = ws.col_values(col_idx)
        target_pk_val = str(pk_value).strip().lower()
        for fila_num, valor in enumerate(celdas[1:], start=2):
            if str(valor).strip().lower() == target_pk_val:
                ws.delete_rows(fila_num)
                return True
        return False

    def replace_rows(self, table: str, rows: list[Mapping[str, str]]) -> None:
        self.invalidate_cache(table)
        ws = self._ws(table)
        ws.clear()
        if not rows:
            return
        header = list(rows[0].keys())
        matriz = [header, *[[str(row.get(col, "")) for col in header] for row in rows]]
        ws.update(range_name="A1", values=matriz)

    def get_meta(self, key: str) -> str | None:
        for rec in self.read_rows(T_META):
            if rec.get("key") == key:
                return rec.get("value")
        return None

    def set_meta(self, key: str, value: str) -> None:
        self.upsert_row(T_META, "key", {"key": key, "value": value})
