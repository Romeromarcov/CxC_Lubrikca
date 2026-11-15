"""Persistencia del panel de bots (SQLAlchemy Core, mismo engine que el resto).

Fuera de ``Repository`` a propósito (igual que ``cxc.capturas`` y ``cxc.bancos``): es un mundo
aparte que el sync de Odoo nunca toca. ``RepoBots`` es el contrato que usan el servicio y la API;
los tests de la API usan un doble en memoria con la misma interfaz y los de persistencia corren
contra Postgres.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import Engine, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cxc.db.schema import (
    bots_bitacora,
    bots_latidos,
    bots_registro,
    bots_usuarios,
    capturas_bot,
)

from .modelos import (
    BOTS,
    CHATS_POR_DEFECTO,
    PARAMETROS_POR_DEFECTO,
    RETENCION_LATIDOS_DIAS,
    Bot,
    UsuarioBot,
)


class RepoBots(Protocol):
    def obtener(self, bot: str) -> Bot | None: ...

    def listar(self) -> list[Bot]: ...

    def guardar_config(
        self, bot: str, cambios: dict[str, Any], *, por: str, ahora: datetime
    ) -> tuple[Bot, Bot]:
        """Aplica ``cambios`` (habilitado, parametros, chats, url_panel, mensaje) y devuelve
        ``(antes, despues)``. Crea el registro con valores por defecto si no existía."""
        ...

    def registrar_latido(
        self, bot: str, *, nombre: str, version: str, estado: dict[str, Any], ahora: datetime
    ) -> Bot: ...

    def historial(self, bot: str, dias: int = 7) -> list[dict[str, Any]]: ...

    def usuarios(self) -> list[UsuarioBot]: ...

    def guardar_usuario(
        self, u: UsuarioBot, *, por: str, ahora: datetime
    ) -> tuple[UsuarioBot | None, UsuarioBot]:
        """Alta o actualización por ``telegram_id``. Devuelve ``(antes_o_None, despues)``."""
        ...

    def borrar_usuario(self, telegram_id: int) -> UsuarioBot | None: ...

    def bitacora_agregar(
        self,
        entidad: str,
        accion: str,
        antes: dict[str, Any],
        despues: dict[str, Any],
        usuario: str,
        ahora: datetime,
    ) -> None: ...

    def bitacora(self, entidad: str | None = None, limite: int = 50) -> list[dict[str, Any]]: ...

    def capturas_recientes(
        self, kinds: tuple[str, ...], limite: int = 15
    ) -> list[dict[str, Any]]: ...

    def conteo_capturas(self) -> dict[str, int]: ...


def _json(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, default=str)


def _a_bot(r: Any) -> Bot:
    return Bot(
        bot=str(r.bot),
        nombre=str(r.nombre),
        habilitado=bool(r.habilitado),
        parametros=json.loads(r.parametros or "{}"),
        chats=json.loads(r.chats or "{}"),
        url_panel=str(r.url_panel or ""),
        mensaje=str(r.mensaje or ""),
        version=str(r.version or ""),
        estado=json.loads(r.estado or "{}"),
        ultimo_latido=r.ultimo_latido,
        actualizado_por=str(r.actualizado_por),
        actualizado_en=r.actualizado_en,
    )


def _a_usuario(r: Any) -> UsuarioBot:
    return UsuarioBot(
        telegram_id=int(r.telegram_id),
        email=str(r.email or ""),
        nombre=str(r.nombre or ""),
        bots=json.loads(r.bots or "[]"),
        creado_por=str(r.creado_por),
        creado_en=r.creado_en,
    )


def _defaults(bot: str, ahora: datetime) -> dict[str, Any]:
    return {
        "bot": bot,
        "nombre": BOTS[bot],
        "habilitado": True,
        "parametros": _json(PARAMETROS_POR_DEFECTO),
        "chats": _json(CHATS_POR_DEFECTO),
        "actualizado_por": "sistema",
        "actualizado_en": ahora,
        "creado_en": ahora,
    }


class RepoBotsSQL:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ── bots ─────────────────────────────────────────────────────────────────
    def obtener(self, bot: str) -> Bot | None:
        with self._engine.connect() as c:
            r = c.execute(select(bots_registro).where(bots_registro.c.bot == bot)).first()
        return _a_bot(r) if r else None

    def listar(self) -> list[Bot]:
        with self._engine.connect() as c:
            filas = c.execute(select(bots_registro)).all()
        return [_a_bot(r) for r in filas]

    def _asegurar(self, c: Any, bot: str, ahora: datetime) -> None:
        c.execute(pg_insert(bots_registro).values(**_defaults(bot, ahora)).on_conflict_do_nothing())

    def guardar_config(
        self, bot: str, cambios: dict[str, Any], *, por: str, ahora: datetime
    ) -> tuple[Bot, Bot]:
        with self._engine.begin() as c:
            self._asegurar(c, bot, ahora)
            fila = c.execute(
                select(bots_registro).where(bots_registro.c.bot == bot).with_for_update()
            ).one()
            antes = _a_bot(fila)
            valores: dict[str, Any] = {"actualizado_por": por, "actualizado_en": ahora}
            if "habilitado" in cambios:
                valores["habilitado"] = bool(cambios["habilitado"])
            if "parametros" in cambios:
                valores["parametros"] = _json({**antes.parametros, **cambios["parametros"]})
            if "chats" in cambios:
                valores["chats"] = _json({**antes.chats, **cambios["chats"]})
            if "url_panel" in cambios:
                valores["url_panel"] = cambios["url_panel"]
            if "mensaje" in cambios:
                valores["mensaje"] = cambios["mensaje"]
            c.execute(update(bots_registro).where(bots_registro.c.bot == bot).values(**valores))
            despues = _a_bot(
                c.execute(select(bots_registro).where(bots_registro.c.bot == bot)).one()
            )
        return antes, despues

    def registrar_latido(
        self, bot: str, *, nombre: str, version: str, estado: dict[str, Any], ahora: datetime
    ) -> Bot:
        with self._engine.begin() as c:
            self._asegurar(c, bot, ahora)
            c.execute(
                update(bots_registro)
                .where(bots_registro.c.bot == bot)
                .values(
                    nombre=nombre or BOTS[bot],
                    version=version,
                    estado=_json(estado),
                    ultimo_latido=ahora,
                )
            )
            dia = ahora.date()
            mensajes, errores = (
                int(estado.get("mensajes_hoy", 0)),
                int(estado.get("errores_hoy", 0)),
            )
            ins = pg_insert(bots_latidos).values(
                bot=bot, dia=dia, latidos=1, mensajes=mensajes, errores=errores, ultimo=ahora
            )
            c.execute(
                ins.on_conflict_do_update(
                    index_elements=["bot", "dia"],
                    set_={
                        "latidos": bots_latidos.c.latidos + 1,
                        # El bot reporta acumulados del día: se queda el mayor (resiste reinicios).
                        "mensajes": func.greatest(bots_latidos.c.mensajes, mensajes),
                        "errores": func.greatest(bots_latidos.c.errores, errores),
                        "ultimo": ahora,
                    },
                )
            )
            c.execute(
                delete(bots_latidos).where(
                    bots_latidos.c.dia < dia - timedelta(days=RETENCION_LATIDOS_DIAS)
                )
            )
            fila = c.execute(select(bots_registro).where(bots_registro.c.bot == bot)).one()
        return _a_bot(fila)

    def historial(self, bot: str, dias: int = 7) -> list[dict[str, Any]]:
        desde: date = date.today() - timedelta(days=dias - 1)
        with self._engine.connect() as c:
            filas = c.execute(
                select(bots_latidos)
                .where(bots_latidos.c.bot == bot, bots_latidos.c.dia >= desde)
                .order_by(bots_latidos.c.dia)
            ).all()
        return [
            {
                "dia": f.dia.isoformat(),
                "latidos": f.latidos,
                "mensajes": f.mensajes,
                "errores": f.errores,
            }
            for f in filas
        ]

    # ── usuarios ─────────────────────────────────────────────────────────────
    def usuarios(self) -> list[UsuarioBot]:
        with self._engine.connect() as c:
            filas = c.execute(select(bots_usuarios).order_by(bots_usuarios.c.nombre)).all()
        return [_a_usuario(r) for r in filas]

    def guardar_usuario(
        self, u: UsuarioBot, *, por: str, ahora: datetime
    ) -> tuple[UsuarioBot | None, UsuarioBot]:
        with self._engine.begin() as c:
            previo = c.execute(
                select(bots_usuarios)
                .where(bots_usuarios.c.telegram_id == u.telegram_id)
                .with_for_update()
            ).first()
            valores = {"email": u.email, "nombre": u.nombre, "bots": _json(u.bots)}
            if previo is None:
                c.execute(
                    insert(bots_usuarios).values(
                        telegram_id=u.telegram_id, creado_por=por, creado_en=ahora, **valores
                    )
                )
            else:
                c.execute(
                    update(bots_usuarios)
                    .where(bots_usuarios.c.telegram_id == u.telegram_id)
                    .values(**valores)
                )
            despues = c.execute(
                select(bots_usuarios).where(bots_usuarios.c.telegram_id == u.telegram_id)
            ).one()
        return (_a_usuario(previo) if previo else None), _a_usuario(despues)

    def borrar_usuario(self, telegram_id: int) -> UsuarioBot | None:
        with self._engine.begin() as c:
            previo = c.execute(
                select(bots_usuarios).where(bots_usuarios.c.telegram_id == telegram_id)
            ).first()
            if previo is None:
                return None
            c.execute(delete(bots_usuarios).where(bots_usuarios.c.telegram_id == telegram_id))
        return _a_usuario(previo)

    # ── bitácora ─────────────────────────────────────────────────────────────
    def bitacora_agregar(
        self,
        entidad: str,
        accion: str,
        antes: dict[str, Any],
        despues: dict[str, Any],
        usuario: str,
        ahora: datetime,
    ) -> None:
        with self._engine.begin() as c:
            c.execute(
                insert(bots_bitacora).values(
                    entidad=entidad,
                    accion=accion,
                    antes=_json(antes),
                    despues=_json(despues),
                    usuario=usuario,
                    cambiado_en=ahora,
                )
            )

    def bitacora(self, entidad: str | None = None, limite: int = 50) -> list[dict[str, Any]]:
        q = select(bots_bitacora).order_by(bots_bitacora.c.cambio_id.desc()).limit(limite)
        if entidad:
            q = q.where(bots_bitacora.c.entidad == entidad)
        with self._engine.connect() as c:
            filas = c.execute(q).all()
        return [
            {
                "entidad": f.entidad,
                "accion": f.accion,
                "antes": json.loads(f.antes),
                "despues": json.loads(f.despues),
                "usuario": f.usuario,
                "en": f.cambiado_en.isoformat(),
            }
            for f in filas
        ]

    # ── capturas (solo lectura, para la actividad de cada bot) ───────────────
    def capturas_recientes(self, kinds: tuple[str, ...], limite: int = 15) -> list[dict[str, Any]]:
        if not kinds:
            return []
        with self._engine.connect() as c:
            filas = c.execute(
                select(capturas_bot)
                .where(capturas_bot.c.kind.in_(kinds))
                .order_by(capturas_bot.c.captura_id.desc())
                .limit(limite)
            ).all()
        out: list[dict[str, Any]] = []
        for f in filas:
            fuente = json.loads(f.fuente or "{}")
            out.append(
                {
                    "captura_id": f.captura_id,
                    "kind": f.kind,
                    "estado": f.estado,
                    "creada_en": f.creada_en.isoformat(),
                    "odoo_ref": f.odoo_ref,
                    "error": f.error,
                    "incompleto": json.loads(f.incompleto or "[]"),
                    "vendedor": fuente.get("vendedor_email", ""),
                    "auto": bool(f.auto),
                }
            )
        return out

    def conteo_capturas(self) -> dict[str, int]:
        with self._engine.connect() as c:
            filas = c.execute(
                select(capturas_bot.c.estado, func.count()).group_by(capturas_bot.c.estado)
            ).all()
        return {str(e): int(n) for e, n in filas}


__all__ = ["RepoBots", "RepoBotsSQL"]
