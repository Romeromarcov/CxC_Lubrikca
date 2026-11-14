"""Persistencia de las capturas de Telegram (SQLAlchemy Core, mismo engine que el resto).

Fuera de ``Repository`` a propósito (igual que ``cxc.bancos.repositorio``): es un mundo aparte
que el sync de Odoo nunca toca. ``RepoCapturas`` es el contrato que usan el servicio y la API;
los tests usan un doble en memoria con la misma interfaz.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cxc.db.schema import capturas_bot, capturas_bot_eventos

from .modelos import ERROR, PENDIENTE, Captura


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

    def obtener(self, captura_id: int) -> Captura | None: ...

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
        ahora = datetime.now()
        with self._engine.begin() as conn:
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
        return _a_captura(fila), creada

    def obtener(self, captura_id: int) -> Captura | None:
        with self._engine.connect() as conn:
            fila = conn.execute(
                select(capturas_bot).where(capturas_bot.c.captura_id == captura_id)
            ).first()
        return _a_captura(fila) if fila is not None else None

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
