"""API de las capturas de los bots de Telegram.

Dos audiencias:

- **El bot** (``POST /api/bots/capturas`` y ``…/{id}/completar``): se autentica con la cabecera
  ``X-Bot-Key`` (variable ``BOT_API_KEY``, comparación en tiempo constante). Sin la variable, 503.
  Las llaves de API normales de CxC siguen siendo SOLO GET: esta clave solo abre estas dos rutas.
- **La interfaz de CxC** (listar, ver, reintentar): sesión con ``capturas.ver`` o
  ``capturas.gestionar``. ``…/{id}/completar`` también acepta sesión con ``capturas.gestionar``
  (para que la pantalla complete pagos); ``POST /api/bots/capturas`` es SOLO del bot.

Los endpoints son síncronos (``def``) a propósito: FastAPI los corre en un hilo y los accesos a
Postgres/Odoo no bloquean el loop de eventos. El envío a Odoo corre en segundo plano.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from cxc.capturas.espejo import EspejoSQL
from cxc.capturas.modelos import ESTADOS, KINDS, PENDIENTE, CapturaInvalida
from cxc.capturas.repositorio import RepoCapturasSQL
from cxc.capturas.servicio import (
    CapturaCerrada,
    NoCompletable,
    NoEncontrada,
    ServicioCapturas,
)
from cxc.odoo.escritura import EscritorOdoo, EscrituraConfig, conectar
from cxc.permisos import tiene

logger = logging.getLogger("cxc.capturas.api")

router = APIRouter()

_RUTA_BOT = re.compile(r"^/api/bots/capturas(/\d+/completar)?$")
PERMISOS_VER = ("capturas.ver", "capturas.gestionar")
INTERVALO_REINTENTO_SEGUNDOS = 120


def es_ruta_bot(metodo: str, ruta: str) -> bool:
    """Rutas que el middleware deja pasar con ``X-Bot-Key`` (el handler valida la clave)."""
    return metodo == "POST" and _RUTA_BOT.match(ruta) is not None


# ── dependencias ─────────────────────────────────────────────────────────────


def _engine() -> Engine:
    from cxc.web.app import get_repo  # import tardío: app incluye este router

    repo: Any = get_repo()  # PostgresRepository: ``_engine`` no está en el Protocol ``Repository``
    engine: Engine = repo._engine
    return engine


def _fabrica_escritor() -> EscritorOdoo | None:
    """Conexión de escritura SOLO con interruptor y credencial; si no, ``None`` (no se escribe)."""
    cfg = EscrituraConfig.from_env()
    if cfg is None:
        return None
    return EscritorOdoo(conectar(cfg))


def _diarios_env() -> dict[str, int]:
    raw = os.environ.get("CAPTURAS_DIARIOS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k): int(v) for k, v in data.items()}
    except (ValueError, TypeError, AttributeError):
        logger.error("CAPTURAS_DIARIOS no es un JSON {nombre: journal_id}; se ignora")
        return {}


def _max_intentos() -> int:
    try:
        return max(1, int(os.environ.get("CAPTURAS_MAX_INTENTOS", "5")))
    except ValueError:
        return 5


def get_servicio() -> ServicioCapturas:
    engine = _engine()
    return ServicioCapturas(
        RepoCapturasSQL(engine),
        EspejoSQL(engine),
        _fabrica_escritor,
        diarios=_diarios_env(),
        max_intentos=_max_intentos(),
    )


Servicio = Annotated[ServicioCapturas, Depends(get_servicio)]


def exigir_bot_key(x_bot_key: Annotated[str | None, Header()] = None) -> None:
    esperada = os.environ.get("BOT_API_KEY", "")
    if not esperada:
        raise HTTPException(status_code=503, detail="BOT_API_KEY no está configurada")
    if not x_bot_key or not hmac.compare_digest(x_bot_key.encode(), esperada.encode()):
        raise HTTPException(status_code=401, detail="Clave de bot inválida")


def _usuario(cxc_session: str | None, *permisos: str) -> dict[str, Any]:
    from cxc.web.app import get_current_user_from_cookie

    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if not any(tiene(user, p) for p in permisos):
        raise HTTPException(status_code=403, detail="Sin permisos para capturas")
    return user


def _actor(user: dict[str, Any]) -> str:
    return str(user.get("email") or user.get("nombre") or "desconocido")


def bot_o_gestion(
    x_bot_key: Annotated[str | None, Header()] = None,
    cxc_session: Annotated[str | None, Cookie()] = None,
) -> str:
    """Quién completa: el bot (``X-Bot-Key``) o una persona con sesión y ``capturas.gestionar``.

    Si llega la cabecera ``X-Bot-Key`` se valida como clave de bot (503 sin ``BOT_API_KEY``, 401 si
    no coincide) y NO se cae a la sesión: una clave mala nunca se «perdona». Sin la cabecera se
    exige la sesión con permiso. Devuelve el actor para la bitácora.
    """
    if x_bot_key is not None:
        exigir_bot_key(x_bot_key)
        return "bot"
    return _actor(_usuario(cxc_session, "capturas.gestionar"))


# ── entrada del bot ──────────────────────────────────────────────────────────


class CapturaIn(BaseModel):
    kind: str
    draft: dict[str, Any]
    source: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=200)


class CompletarIn(BaseModel):
    cambios: dict[str, Any]


def _invalida(e: CapturaInvalida) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": e.error, "faltan": e.faltan})


@router.post("/api/bots/capturas", dependencies=[Depends(exigir_bot_key)])
def recibir_captura(body: CapturaIn, tareas: BackgroundTasks, servicio: Servicio) -> JSONResponse:
    try:
        captura, creada = servicio.recibir(body.kind, body.draft, body.source, body.idempotency_key)
    except CapturaInvalida as e:
        return _invalida(e)
    if creada and captura.estado == PENDIENTE:
        tareas.add_task(servicio.procesar, captura.captura_id)  # a Odoo, sin hacer esperar al bot
    return JSONResponse(status_code=201 if creada else 200, content=captura.a_dict())


@router.post("/api/bots/capturas/{captura_id}/completar")
def completar_captura(
    captura_id: int,
    body: CompletarIn,
    tareas: BackgroundTasks,
    servicio: Servicio,
    actor: Annotated[str, Depends(bot_o_gestion)],
) -> JSONResponse:
    """Completa un pago: lo llama el bot (``X-Bot-Key``) o la pantalla (``capturas.gestionar``)."""
    try:
        captura = servicio.completar(captura_id, body.cambios, actor=actor, enviar=False)
    except NoEncontrada as e:
        raise HTTPException(status_code=404, detail="Captura no encontrada") from e
    except NoCompletable as e:
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": []})
    except CapturaCerrada as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except CapturaInvalida as e:
        return _invalida(e)
    if captura.estado == PENDIENTE:
        tareas.add_task(servicio.procesar, captura.captura_id)
    return JSONResponse(content=captura.a_dict())


# ── interfaz de CxC (sesión) ─────────────────────────────────────────────────


@router.get("/api/bots/capturas")
def listar_capturas(
    servicio: Servicio,
    estado: str | None = None,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _usuario(cxc_session, *PERMISOS_VER)
    if estado and estado not in ESTADOS:
        raise HTTPException(status_code=422, detail=f"estado debe ser uno de {', '.join(ESTADOS)}")
    if kind and kind not in KINDS:
        raise HTTPException(status_code=422, detail=f"kind debe ser uno de {', '.join(KINDS)}")
    items, total = servicio.listar(
        estado=estado, kind=kind, limit=max(1, min(limit, 200)), offset=max(0, offset)
    )
    return {"items": [c.a_dict() for c in items], "total": total}


@router.get("/api/bots/capturas/{captura_id}")
def ver_captura(
    captura_id: int, servicio: Servicio, cxc_session: str | None = Cookie(default=None)
) -> dict[str, Any]:
    _usuario(cxc_session, *PERMISOS_VER)
    try:
        c, eventos = servicio.detalle(captura_id)
    except NoEncontrada as e:
        raise HTTPException(status_code=404, detail="Captura no encontrada") from e
    return {**c.a_dict(), "eventos": eventos}


@router.post("/api/bots/capturas/{captura_id}/reintentar")
def reintentar_captura(
    captura_id: int,
    servicio: Servicio,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario(cxc_session, "capturas.gestionar")
    try:
        c = servicio.reintentar(captura_id, _actor(user))
    except NoEncontrada as e:
        raise HTTPException(status_code=404, detail="Captura no encontrada") from e
    except CapturaCerrada as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    return JSONResponse(content=c.a_dict())


# ── reintento periódico ──────────────────────────────────────────────────────


async def run_reintentos_capturas_en_background() -> None:
    """Cada ~2 min reintenta los ERROR transitorios y los PENDIENTE (con escritura activa)."""
    while True:
        await asyncio.sleep(INTERVALO_REINTENTO_SEGUNDOS)
        if EscrituraConfig.from_env() is None:
            continue
        try:
            n = await asyncio.to_thread(lambda: get_servicio().reintentar_pendientes())
            if n:
                logger.info("Capturas reintentadas: %d", n)
        except Exception:
            logger.exception("Falló el ciclo de reintento de capturas")


__all__ = [
    "es_ruta_bot",
    "get_servicio",
    "router",
    "run_reintentos_capturas_en_background",
]
