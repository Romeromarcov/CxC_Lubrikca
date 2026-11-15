"""API del panel de bots de Telegram (plano de control de la familia de bots).

Dos audiencias:

- **El bot** (``POST /api/bots/heartbeat``): ``X-Bot-Key`` (``BOT_API_KEY``, tiempo constante,
  503 si no está configurada), igual que las capturas. Reporta su estado y recibe su configuración.
- **La interfaz de CxC**: sesión con ``bots.ver`` (consultar) o ``bots.gestionar`` (cambiar). Toda
  modificación queda en ``bots_bitacora`` (quién, qué, antes y después).

Nada de lo que entra o sale de aquí lleva tokens de Telegram ni API keys.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from cxc.bots.modelos import DatoInvalido
from cxc.bots.repositorio import RepoBotsSQL
from cxc.bots.servicio import BotDesconocido, ServicioBots, UsuarioNoEncontrado
from cxc.web.capturas_api import _actor, _usuario, exigir_bot_key

logger = logging.getLogger("cxc.bots.api")

router = APIRouter()

PERMISOS_VER = ("bots.ver", "bots.gestionar")
PERMISOS_GESTION = ("bots.gestionar",)


def _engine() -> Engine:
    from cxc.web.app import get_repo  # import tardío: app incluye este router

    repo: Any = get_repo()  # PostgresRepository: ``_engine`` no está en el Protocol ``Repository``
    engine: Engine = repo._engine
    return engine


def get_servicio_bots() -> ServicioBots:
    return ServicioBots(RepoBotsSQL(_engine()))


Servicio = Annotated[ServicioBots, Depends(get_servicio_bots)]


def _quien(cxc_session: str | None, *permisos: str) -> str:
    """Valida sesión + permiso y devuelve el actor para la bitácora."""
    try:
        return _actor(_usuario(cxc_session, *permisos))
    except HTTPException as e:
        if e.status_code == 403:
            raise HTTPException(status_code=403, detail="Sin permisos para el panel de bots") from e
        raise


def _invalido(e: DatoInvalido) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": str(e)})


# ── latido (lo llama el bot) ─────────────────────────────────────────────────


class LatidoIn(BaseModel):
    bot: Literal["pedidos", "cobranza", "compras", "eventos", "asana"]
    nombre: str = Field(default="", max_length=100)
    version: str = Field(default="", max_length=50)
    estado: dict[str, Any] = Field(default_factory=dict)


@router.post("/api/bots/heartbeat", dependencies=[Depends(exigir_bot_key)])
def latido(body: LatidoIn, servicio: Servicio) -> dict[str, Any]:
    return servicio.latido(body.bot, body.nombre, body.version, body.estado)


# ── usuarios autorizados (rutas fijas ANTES de ``/api/bots/{bot}``) ──────────


class UsuarioIn(BaseModel):
    telegram_id: Any
    email: str = Field(default="", max_length=254)
    nombre: str = Field(default="", max_length=200)
    bots: list[str] = Field(default_factory=list, max_length=5)


@router.get("/api/bots/usuarios")
def listar_usuarios(
    servicio: Servicio, cxc_session: str | None = Cookie(default=None)
) -> dict[str, Any]:
    _quien(cxc_session, *PERMISOS_VER)
    return {"items": servicio.usuarios()}


@router.post("/api/bots/usuarios")
def guardar_usuario(
    body: UsuarioIn, servicio: Servicio, cxc_session: str | None = Cookie(default=None)
) -> JSONResponse:
    actor = _quien(cxc_session, *PERMISOS_GESTION)
    try:
        usuario, creado = servicio.guardar_usuario(
            body.telegram_id, body.email, body.nombre, body.bots, actor
        )
    except DatoInvalido as e:
        return _invalido(e)
    return JSONResponse(status_code=201 if creado else 200, content=usuario)


@router.delete("/api/bots/usuarios/{telegram_id}")
def borrar_usuario(
    telegram_id: str, servicio: Servicio, cxc_session: str | None = Cookie(default=None)
) -> JSONResponse:
    actor = _quien(cxc_session, *PERMISOS_GESTION)
    try:
        servicio.borrar_usuario(telegram_id, actor)
    except DatoInvalido as e:
        return _invalido(e)
    except UsuarioNoEncontrado as e:
        raise HTTPException(status_code=404, detail="Usuario no encontrado") from e
    return JSONResponse(content={"ok": True})


# ── destinos ─────────────────────────────────────────────────────────────────


@router.get("/api/bots/destinos")
def destinos(servicio: Servicio, cxc_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    _quien(cxc_session, *PERMISOS_VER)
    return servicio.destinos()


# ── bots ─────────────────────────────────────────────────────────────────────


class BotPatch(BaseModel):
    habilitado: bool | None = None
    parametros: dict[str, Any] | None = None
    chats: dict[str, Any] | None = None
    url_panel: str | None = None
    mensaje: str | None = None


@router.get("/api/bots")
def listar_bots(
    servicio: Servicio, cxc_session: str | None = Cookie(default=None)
) -> dict[str, Any]:
    _quien(cxc_session, *PERMISOS_VER)
    return {"items": servicio.listar()}


@router.get("/api/bots/{bot}")
def ver_bot(
    bot: str, servicio: Servicio, cxc_session: str | None = Cookie(default=None)
) -> dict[str, Any]:
    _quien(cxc_session, *PERMISOS_VER)
    try:
        return servicio.detalle(bot)
    except BotDesconocido as e:
        raise HTTPException(status_code=404, detail="Bot no encontrado") from e


@router.patch("/api/bots/{bot}")
def actualizar_bot(
    bot: str,
    body: BotPatch,
    servicio: Servicio,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    actor = _quien(cxc_session, *PERMISOS_GESTION)
    try:
        return JSONResponse(
            content=servicio.actualizar(bot, body.model_dump(exclude_unset=True), actor)
        )
    except BotDesconocido as e:
        raise HTTPException(status_code=404, detail="Bot no encontrado") from e
    except DatoInvalido as e:
        return _invalido(e)


__all__ = ["get_servicio_bots", "router"]
