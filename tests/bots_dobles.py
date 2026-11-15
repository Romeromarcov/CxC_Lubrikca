"""Doble en memoria del repositorio del panel de bots (misma interfaz que ``RepoBotsSQL``)."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from cxc.bots.modelos import (
    BOTS,
    CHATS_POR_DEFECTO,
    PARAMETROS_POR_DEFECTO,
    Bot,
    UsuarioBot,
)


class RepoBotsMemoria:
    def __init__(self) -> None:
        self.bots: dict[str, Bot] = {}
        self.users: dict[int, UsuarioBot] = {}
        self.log: list[dict[str, Any]] = []
        self.dias: dict[str, dict[str, int]] = {}
        self.capturas: list[dict[str, Any]] = []
        self.conteos: dict[str, int] = {}

    def _asegurar(self, bot: str, ahora: datetime) -> Bot:
        if bot not in self.bots:
            self.bots[bot] = Bot(
                bot=bot,
                nombre=BOTS[bot],
                parametros=dict(PARAMETROS_POR_DEFECTO),
                chats=dict(CHATS_POR_DEFECTO),
                actualizado_en=ahora,
            )
        return self.bots[bot]

    def obtener(self, bot: str) -> Bot | None:
        b = self.bots.get(bot)
        return deepcopy(b) if b else None

    def listar(self) -> list[Bot]:
        return [deepcopy(b) for b in self.bots.values()]

    def guardar_config(
        self, bot: str, cambios: dict[str, Any], *, por: str, ahora: datetime
    ) -> tuple[Bot, Bot]:
        b = self._asegurar(bot, ahora)
        antes = deepcopy(b)
        if "habilitado" in cambios:
            b.habilitado = bool(cambios["habilitado"])
        if "parametros" in cambios:
            b.parametros = {**b.parametros, **cambios["parametros"]}
        if "chats" in cambios:
            b.chats = {**b.chats, **cambios["chats"]}
        if "url_panel" in cambios:
            b.url_panel = cambios["url_panel"]
        if "mensaje" in cambios:
            b.mensaje = cambios["mensaje"]
        b.actualizado_por, b.actualizado_en = por, ahora
        return antes, deepcopy(b)

    def registrar_latido(
        self, bot: str, *, nombre: str, version: str, estado: dict[str, Any], ahora: datetime
    ) -> Bot:
        b = self._asegurar(bot, ahora)
        b.nombre, b.version, b.estado, b.ultimo_latido = nombre or BOTS[bot], version, estado, ahora
        d = self.dias.setdefault(bot, {"latidos": 0, "mensajes": 0, "errores": 0})
        d["latidos"] += 1
        d["mensajes"] = max(d["mensajes"], int(estado.get("mensajes_hoy", 0)))
        d["errores"] = max(d["errores"], int(estado.get("errores_hoy", 0)))
        return deepcopy(b)

    def historial(self, bot: str, dias: int = 7) -> list[dict[str, Any]]:
        d = self.dias.get(bot)
        return [{"dia": datetime.now().date().isoformat(), **d}] if d else []

    def usuarios(self) -> list[UsuarioBot]:
        return [deepcopy(u) for u in sorted(self.users.values(), key=lambda u: u.nombre)]

    def guardar_usuario(
        self, u: UsuarioBot, *, por: str, ahora: datetime
    ) -> tuple[UsuarioBot | None, UsuarioBot]:
        previo = self.users.get(u.telegram_id)
        nuevo = UsuarioBot(
            telegram_id=u.telegram_id,
            email=u.email,
            nombre=u.nombre,
            bots=list(u.bots),
            creado_por=previo.creado_por if previo else por,
            creado_en=previo.creado_en if previo else ahora,
        )
        self.users[u.telegram_id] = nuevo
        return (deepcopy(previo) if previo else None), deepcopy(nuevo)

    def borrar_usuario(self, telegram_id: int) -> UsuarioBot | None:
        return self.users.pop(telegram_id, None)

    def bitacora_agregar(
        self,
        entidad: str,
        accion: str,
        antes: dict[str, Any],
        despues: dict[str, Any],
        usuario: str,
        ahora: datetime,
    ) -> None:
        self.log.append(
            {
                "entidad": entidad,
                "accion": accion,
                "antes": antes,
                "despues": despues,
                "usuario": usuario,
                "en": ahora.isoformat(),
            }
        )

    def bitacora(self, entidad: str | None = None, limite: int = 50) -> list[dict[str, Any]]:
        filas = [f for f in self.log if entidad is None or f["entidad"] == entidad]
        return list(reversed(filas))[:limite]

    def capturas_recientes(self, kinds: tuple[str, ...], limite: int = 15) -> list[dict[str, Any]]:
        return [c for c in self.capturas if c["kind"] in kinds][:limite]

    def conteo_capturas(self) -> dict[str, int]:
        return dict(self.conteos)
