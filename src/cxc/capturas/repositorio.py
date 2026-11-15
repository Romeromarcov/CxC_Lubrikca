"""Persistencia de las capturas de Telegram (SQLAlchemy Core, mismo engine que el resto).

Fuera de ``Repository`` a propósito (igual que ``cxc.bancos.repositorio``): es un mundo aparte
que el sync de Odoo nunca toca. ``RepoCapturas`` es el contrato que usan el servicio y la API;
los tests usan un doble en memoria con la misma interfaz.

Duplicados lógicos y concurrencia
---------------------------------
«La misma factura (proveedor + número) se captura una sola vez» no se puede dejar a una
comprobación aparte del alta: con envíos simultáneos todos ven «no hay duplicado» y todos
insertan. Por eso ``crear_factura`` / ``actualizar_factura`` comprueban y escriben **en la misma
transacción**, tomando antes un ``pg_advisory_xact_lock`` por número de factura: los envíos de la
misma factura se serializan y el segundo ve lo que commiteó el primero. No se usa un índice único
porque «mismo proveedor» es «mismo RIF, o mismo nombre si falta el RIF» y eso no lo expresa un
índice; además el bloqueo es compatible con la corrección por reenvío (cambiar el número).
El bloqueo es por número (no por proveedor): dos proveedores con el mismo número de factura solo
esperan una fracción de segundo.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import Connection, Engine, cast, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from cxc.db.schema import capturas_bot, capturas_bot_eventos

from .modelos import ERROR, PENDIENTE, Captura

# Cuánto espera un envío por el bloqueo de la misma factura antes de rendirse (el bot reintenta).
ESPERA_BLOQUEO_MS = 10_000
_SQLSTATE_LOCK_NOT_AVAILABLE = "55P03"

EsMismoProveedor = Callable[[dict[str, Any]], bool]


class FacturaYaCapturada(Exception):
    """La factura (proveedor + número) ya existe en otra captura; ``existente`` es esa captura."""

    def __init__(self, existente: Captura) -> None:
        super().__init__(f"captura #{existente.captura_id}")
        self.existente = existente


class BloqueoOcupado(Exception):
    """No se obtuvo el bloqueo a tiempo (otro envío de lo mismo sigue en curso): reintentar."""


class RepoCapturas(Protocol):
    def crear(
        self,
        *,
        kind: str,
        idempotency_key: str,
        payload: dict[str, Any],
        fuente: dict[str, Any],
        auto: bool,
        estado: str,
        incompleto: list[str],
    ) -> tuple[Captura, bool]: ...

    def crear_factura(
        self,
        *,
        kind: str,
        idempotency_key: str,
        payload: dict[str, Any],
        fuente: dict[str, Any],
        auto: bool,
        estado: str,
        incompleto: list[str],
        numero_factura: str,
        es_mismo_proveedor: EsMismoProveedor,
    ) -> tuple[Captura, bool]:
        """Como ``crear`` pero atómico frente a otra captura de la misma factura.

        Lanza ``FacturaYaCapturada`` si ya hay una de ese proveedor con ese número; si la clave
        de idempotencia ya existe devuelve esa captura (``creada=False``).
        """
        ...

    def actualizar_factura(
        self,
        captura_id: int,
        *,
        numero_factura: str,
        es_mismo_proveedor: EsMismoProveedor,
        estado: str | None = None,
        payload: dict[str, Any] | None = None,
        incompleto: list[str] | None = None,
        error: str | None = None,
    ) -> Captura:
        """Como ``actualizar`` pero atómico: no deja que el nuevo número choque con otra captura."""
        ...

    def bloqueo(self, clave: str, espera_segundos: float = 30.0) -> Any:
        """Context manager de exclusión mutua por ``clave`` (``BloqueoOcupado`` si se agota)."""
        ...

    def obtener(self, captura_id: int) -> Captura | None: ...

    def obtener_por_clave(self, idempotency_key: str) -> Captura | None: ...

    def facturas_con_numero(self, numero_factura: str) -> list[Captura]:
        """Capturas ``factura_proveedor`` con ese número (sin distinguir mayúsculas ni espacios)."""
        ...

    def listar(
        self, *, estado: str | None, kind: str | None, limit: int, offset: int
    ) -> tuple[list[Captura], int]: ...

    def actualizar(
        self,
        captura_id: int,
        *,
        estado: str | None = None,
        payload: dict[str, Any] | None = None,
        incompleto: list[str] | None = None,
        resultado: dict[str, Any] | None = None,
        odoo_ref: str | None = None,
        error: str | None = None,
        sumar_intento: bool = False,
    ) -> Captura: ...

    def evento(
        self, captura_id: int, evento: str, detalle: str = "", actor: str = "sistema"
    ) -> None: ...

    def eventos(self, captura_id: int) -> list[dict[str, Any]]: ...

    def reintentables(self, *, max_intentos: int, limite: int = 20) -> list[Captura]: ...


def _a_captura(r: Any) -> Captura:
    return Captura(
        captura_id=int(r.captura_id),
        kind=str(r.kind),
        idempotency_key=str(r.idempotency_key),
        estado=str(r.estado),
        payload=json.loads(r.payload),
        fuente=json.loads(r.fuente),
        auto=bool(r.auto),
        incompleto=json.loads(r.incompleto),
        resultado=json.loads(r.resultado),
        odoo_ref=str(r.odoo_ref),
        error=str(r.error),
        intentos=int(r.intentos),
        creada_en=r.creada_en,
        actualizada_en=r.actualizada_en,
    )


def _json(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, default=str)


def _clave_bloqueo(clave: str) -> int:
    """Entero de 64 bits con signo (lo que pide ``pg_advisory_lock``) derivado de la clave."""
    return int.from_bytes(hashlib.sha256(clave.encode("utf-8")).digest()[:8], "big", signed=True)


def clave_factura(numero_factura: str) -> str:
    """Clave de exclusión de una factura: el número, sin espacios ni mayúsculas."""
    return "factura_proveedor:" + numero_factura.strip().lower()


def _bloquear_xact(conn: Connection, clave: str) -> None:
    """Toma el bloqueo asesor de la transacción actual (se libera solo al commit/rollback)."""
    conn.execute(text(f"SET LOCAL lock_timeout = '{ESPERA_BLOQUEO_MS}ms'"))
    try:
        conn.execute(select(func.pg_advisory_xact_lock(_clave_bloqueo(clave))))
    except DBAPIError as e:
        if getattr(e.orig, "sqlstate", None) == _SQLSTATE_LOCK_NOT_AVAILABLE:
            raise BloqueoOcupado(f"bloqueo ocupado: {clave}") from e
        raise


def _antes_de_escribir(conn: Connection) -> None:
    """Instante entre «comprobé que no hay duplicado» y «escribo».

    No hace nada. Existe para que los tests de concurrencia puedan ENSANCHAR esa ventana y
    demostrar que sin el bloqueo se cuelan duplicados y con él no.
    """


def _valores_actualizar(
    *,
    estado: str | None = None,
    payload: dict[str, Any] | None = None,
    incompleto: list[str] | None = None,
    resultado: dict[str, Any] | None = None,
    odoo_ref: str | None = None,
    error: str | None = None,
    sumar_intento: bool = False,
) -> dict[str, Any]:
    valores: dict[str, Any] = {"actualizada_en": datetime.now()}
    if estado is not None:
        valores["estado"] = estado
    if payload is not None:
        valores["payload"] = _json(payload)
    if incompleto is not None:
        valores["incompleto"] = _json(incompleto)
    if resultado is not None:
        valores["resultado"] = _json(resultado)
    if odoo_ref is not None:
        valores["odoo_ref"] = odoo_ref
    if error is not None:
        valores["error"] = error[:2000]
    if sumar_intento:
        valores["intentos"] = capturas_bot.c.intentos + 1
    return valores


def _insertar(
    conn: Connection,
    *,
    kind: str,
    idempotency_key: str,
    payload: dict[str, Any],
    fuente: dict[str, Any],
    auto: bool,
    estado: str,
    incompleto: list[str],
) -> tuple[Any, bool]:
    """Inserta (o devuelve la existente por clave) y anota «recibida» en la bitácora."""
    ahora = datetime.now()
    fila = conn.execute(
        pg_insert(capturas_bot)
        .values(
            kind=kind,
            idempotency_key=idempotency_key,
            estado=estado,
            payload=_json(payload),
            fuente=_json(fuente),
            auto=auto,
            incompleto=_json(incompleto),
            creada_en=ahora,
            actualizada_en=ahora,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(*capturas_bot.c)
    ).first()
    creada = fila is not None
    if fila is None:
        fila = conn.execute(
            select(capturas_bot).where(capturas_bot.c.idempotency_key == idempotency_key)
        ).one()
    if creada:
        conn.execute(
            insert(capturas_bot_eventos).values(
                captura_id=fila.captura_id,
                evento="recibida",
                detalle=f"{kind} · {estado}",
                actor=str(fuente.get("vendedor_email") or "bot"),
                en=ahora,
            )
        )
    return fila, creada


def _facturas_con_numero(conn: Connection, numero_factura: str) -> list[Any]:
    numero = func.lower(func.btrim(cast(capturas_bot.c.payload, JSONB)["numero_factura"].astext))
    return list(
        conn.execute(
            select(capturas_bot)
            .where(
                capturas_bot.c.kind == "factura_proveedor",
                numero == numero_factura.strip().lower(),
            )
            .order_by(capturas_bot.c.captura_id)
        ).all()
    )


class RepoCapturasSQL:
    """Implementación sobre Postgres."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def crear(
        self,
        *,
        kind: str,
        idempotency_key: str,
        payload: dict[str, Any],
        fuente: dict[str, Any],
        auto: bool,
        estado: str,
        incompleto: list[str],
    ) -> tuple[Captura, bool]:
        """Inserta; si la clave ya existe devuelve la existente (``creada=False``)."""
        with self._engine.begin() as conn:
            fila, creada = _insertar(
                conn,
                kind=kind,
                idempotency_key=idempotency_key,
                payload=payload,
                fuente=fuente,
                auto=auto,
                estado=estado,
                incompleto=incompleto,
            )
        return _a_captura(fila), creada

    def crear_factura(
        self,
        *,
        kind: str,
        idempotency_key: str,
        payload: dict[str, Any],
        fuente: dict[str, Any],
        auto: bool,
        estado: str,
        incompleto: list[str],
        numero_factura: str,
        es_mismo_proveedor: EsMismoProveedor,
    ) -> tuple[Captura, bool]:
        with self._engine.begin() as conn:
            _bloquear_xact(conn, clave_factura(numero_factura))
            previa = conn.execute(
                select(capturas_bot).where(capturas_bot.c.idempotency_key == idempotency_key)
            ).first()
            if previa is not None:  # el mismo mensaje de Telegram, no un duplicado
                return _a_captura(previa), False
            for f in _facturas_con_numero(conn, numero_factura):
                existente = _a_captura(f)
                if es_mismo_proveedor(existente.payload):
                    raise FacturaYaCapturada(existente)
            _antes_de_escribir(conn)
            fila, creada = _insertar(
                conn,
                kind=kind,
                idempotency_key=idempotency_key,
                payload=payload,
                fuente=fuente,
                auto=auto,
                estado=estado,
                incompleto=incompleto,
            )
        return _a_captura(fila), creada

    def actualizar_factura(
        self,
        captura_id: int,
        *,
        numero_factura: str,
        es_mismo_proveedor: EsMismoProveedor,
        estado: str | None = None,
        payload: dict[str, Any] | None = None,
        incompleto: list[str] | None = None,
        error: str | None = None,
    ) -> Captura:
        valores = _valores_actualizar(
            estado=estado, payload=payload, incompleto=incompleto, error=error
        )
        with self._engine.begin() as conn:
            _bloquear_xact(conn, clave_factura(numero_factura))
            for f in _facturas_con_numero(conn, numero_factura):
                otra = _a_captura(f)
                if otra.captura_id != captura_id and es_mismo_proveedor(otra.payload):
                    raise FacturaYaCapturada(otra)
            _antes_de_escribir(conn)
            fila = conn.execute(
                update(capturas_bot)
                .where(capturas_bot.c.captura_id == captura_id)
                .values(**valores)
                .returning(*capturas_bot.c)
            ).one()
        return _a_captura(fila)

    @contextmanager
    def bloqueo(self, clave: str, espera_segundos: float = 30.0) -> Iterator[None]:
        """Exclusión mutua entre procesos (bloqueo asesor de sesión) mientras dure el ``with``.

        Sirve para operaciones largas que no caben en una transacción (p. ej. crear un proveedor en
        Odoo sin que otra captura del mismo RIF lo cree a la vez). Espera hasta ``espera_segundos``
        y lanza ``BloqueoOcupado``; si el proceso muere, Postgres libera el bloqueo solo.
        """
        llave = _clave_bloqueo(clave)
        conn = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            limite = time.monotonic() + espera_segundos
            while not conn.execute(select(func.pg_try_advisory_lock(llave))).scalar_one():
                if time.monotonic() >= limite:
                    raise BloqueoOcupado(f"bloqueo ocupado: {clave}")
                time.sleep(0.1)
            try:
                yield
            finally:
                conn.execute(select(func.pg_advisory_unlock(llave)))
        finally:
            conn.execute(select(func.pg_advisory_unlock_all()))  # nunca devolver el bloqueo al pool
            conn.close()

    def obtener(self, captura_id: int) -> Captura | None:
        with self._engine.connect() as conn:
            fila = conn.execute(
                select(capturas_bot).where(capturas_bot.c.captura_id == captura_id)
            ).first()
        return _a_captura(fila) if fila is not None else None

    def obtener_por_clave(self, idempotency_key: str) -> Captura | None:
        with self._engine.connect() as conn:
            fila = conn.execute(
                select(capturas_bot).where(capturas_bot.c.idempotency_key == idempotency_key)
            ).first()
        return _a_captura(fila) if fila is not None else None

    def facturas_con_numero(self, numero_factura: str) -> list[Captura]:
        with self._engine.connect() as conn:
            return [_a_captura(f) for f in _facturas_con_numero(conn, numero_factura)]

    def listar(
        self, *, estado: str | None, kind: str | None, limit: int, offset: int
    ) -> tuple[list[Captura], int]:
        filtros = []
        if estado:
            filtros.append(capturas_bot.c.estado == estado)
        if kind:
            filtros.append(capturas_bot.c.kind == kind)
        with self._engine.connect() as conn:
            total = conn.execute(
                select(func.count()).select_from(capturas_bot).where(*filtros)
            ).scalar_one()
            filas = conn.execute(
                select(capturas_bot)
                .where(*filtros)
                .order_by(capturas_bot.c.captura_id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        return [_a_captura(f) for f in filas], int(total)

    def actualizar(
        self,
        captura_id: int,
        *,
        estado: str | None = None,
        payload: dict[str, Any] | None = None,
        incompleto: list[str] | None = None,
        resultado: dict[str, Any] | None = None,
        odoo_ref: str | None = None,
        error: str | None = None,
        sumar_intento: bool = False,
    ) -> Captura:
        valores = _valores_actualizar(
            estado=estado,
            payload=payload,
            incompleto=incompleto,
            resultado=resultado,
            odoo_ref=odoo_ref,
            error=error,
            sumar_intento=sumar_intento,
        )
        with self._engine.begin() as conn:
            fila = conn.execute(
                update(capturas_bot)
                .where(capturas_bot.c.captura_id == captura_id)
                .values(**valores)
                .returning(*capturas_bot.c)
            ).one()
        return _a_captura(fila)

    def evento(
        self, captura_id: int, evento: str, detalle: str = "", actor: str = "sistema"
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(capturas_bot_eventos).values(
                    captura_id=captura_id,
                    evento=evento,
                    detalle=detalle[:2000],
                    actor=actor,
                    en=datetime.now(),
                )
            )

    def eventos(self, captura_id: int) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            filas = conn.execute(
                select(capturas_bot_eventos)
                .where(capturas_bot_eventos.c.captura_id == captura_id)
                .order_by(capturas_bot_eventos.c.evento_id)
            ).all()
        return [
            {"evento": f.evento, "detalle": f.detalle, "actor": f.actor, "en": f.en.isoformat()}
            for f in filas
        ]

    def reintentables(self, *, max_intentos: int, limite: int = 20) -> list[Captura]:
        """ERROR transitorios con intentos disponibles, y PENDIENTE que quedaron sin procesar."""
        with self._engine.connect() as conn:
            filas = conn.execute(
                select(capturas_bot)
                .where(
                    capturas_bot.c.estado.in_([ERROR, PENDIENTE]),
                    capturas_bot.c.intentos < max_intentos,
                )
                .order_by(capturas_bot.c.captura_id)
                .limit(limite * 5)
            ).all()
        out = []
        for f in filas:
            c = _a_captura(f)
            if c.estado == PENDIENTE or c.resultado.get("transitorio") is True:
                out.append(c)
        return out[:limite]
