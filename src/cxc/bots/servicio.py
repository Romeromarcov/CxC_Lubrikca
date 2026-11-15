"""Servicio del panel de bots: latido, configuración, usuarios autorizados y salud de destinos.

Toda modificación hecha desde el panel queda en la bitácora (quién, qué, antes y después). Nada de
lo que entra o sale de aquí contiene tokens ni API keys.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from .modelos import (
    BOTS,
    CHATS_POR_DEFECTO,
    KINDS_POR_BOT,
    MODELOS_GEMINI_SUGERIDOS,
    PARAMETROS_POR_DEFECTO,
    Bot,
    DatoInvalido,
    UsuarioBot,
    resumen_destinos,
    sanear_estado,
    validar_chats,
    validar_mensaje,
    validar_parametros,
    validar_telegram_id,
    validar_url_panel,
    validar_usuario,
)
from .repositorio import RepoBots


class BotDesconocido(LookupError):
    pass


class UsuarioNoEncontrado(LookupError):
    pass


def _bot_por_defecto(bot: str) -> Bot:
    return Bot(bot=bot, nombre=BOTS[bot])


class ServicioBots:
    def __init__(self, repo: RepoBots, reloj: Callable[[], datetime] = datetime.now) -> None:
        self._repo = repo
        self._reloj = reloj

    # ── helpers ──────────────────────────────────────────────────────────────
    def _validar_id(self, bot: str) -> str:
        if bot not in BOTS:
            raise BotDesconocido(bot)
        return bot

    def _todos(self) -> list[Bot]:
        existentes = {b.bot: b for b in self._repo.listar()}
        return [existentes.get(b) or _bot_por_defecto(b) for b in BOTS]

    def _usuarios_de(self, bot: str) -> list[dict[str, Any]]:
        return [
            {"telegram_id": u.telegram_id, "email": u.email, "nombre": u.nombre}
            for u in self._repo.usuarios()
            if bot in u.bots
        ]

    # ── latido (lo llama el bot) ─────────────────────────────────────────────
    def latido(self, bot: str, nombre: str, version: str, estado: dict[str, Any]) -> dict[str, Any]:
        """Registra el estado del bot y devuelve la configuración vigente DE ESE BOT."""
        self._validar_id(bot)
        registrado = self._repo.registrar_latido(
            bot,
            nombre=(nombre or "").strip()[:60] or BOTS[bot],
            version=(version or "").strip()[:30],
            estado=sanear_estado(estado),
            ahora=self._reloj(),
        )
        return {**registrado.configuracion(), "usuarios": self._usuarios_de(bot)}

    # ── panel ────────────────────────────────────────────────────────────────
    def listar(self) -> list[dict[str, Any]]:
        ahora = self._reloj()
        usuarios = self._repo.usuarios()
        out = []
        for b in self._todos():
            d = b.a_dict(ahora)
            d["n_usuarios"] = sum(1 for u in usuarios if b.bot in u.bots)
            d["visto_alguna_vez"] = b.ultimo_latido is not None
            out.append(d)
        return out

    def detalle(self, bot: str) -> dict[str, Any]:
        self._validar_id(bot)
        ahora = self._reloj()
        b = self._repo.obtener(bot) or _bot_por_defecto(bot)
        return {
            **b.a_dict(ahora),
            "visto_alguna_vez": b.ultimo_latido is not None,
            "usuarios": [u.a_dict() for u in self._repo.usuarios() if bot in u.bots],
            "capturas": self._repo.capturas_recientes(KINDS_POR_BOT[bot]),
            "historial": self._repo.historial(bot),
            "bitacora": self._repo.bitacora(f"bot:{bot}", 20),
            "modelos_sugeridos": list(MODELOS_GEMINI_SUGERIDOS),
        }

    def actualizar(self, bot: str, cambios: dict[str, Any], actor: str) -> dict[str, Any]:
        """``cambios``: habilitado, parametros, chats, url_panel, mensaje (solo lo presente)."""
        self._validar_id(bot)
        limpios: dict[str, Any] = {}
        if "habilitado" in cambios:
            if not isinstance(cambios["habilitado"], bool):
                raise DatoInvalido("habilitado debe ser verdadero o falso.")
            limpios["habilitado"] = cambios["habilitado"]
        if cambios.get("parametros") is not None:
            limpios["parametros"] = validar_parametros(cambios["parametros"])
        if cambios.get("chats") is not None:
            limpios["chats"] = validar_chats(cambios["chats"])
        if cambios.get("url_panel") is not None:
            limpios["url_panel"] = validar_url_panel(cambios["url_panel"])
        if cambios.get("mensaje") is not None:
            limpios["mensaje"] = validar_mensaje(cambios["mensaje"])
        if not limpios:
            raise DatoInvalido("No hay nada que cambiar.")
        # Solo se guarda y se registra lo que REALMENTE cambia (la pantalla manda el formulario
        # completo): un guardado sin cambios no toca nada ni deja rastro en la bitácora.
        actual = self._repo.obtener(bot) or _bot_por_defecto(bot)
        antes, despues = _diferencias(actual, limpios)
        if not despues:
            return self.detalle(bot)
        ahora = self._reloj()
        self._repo.guardar_config(bot, despues, por=actor, ahora=ahora)
        self._repo.bitacora_agregar(f"bot:{bot}", "actualizar", antes, despues, actor, ahora)
        return self.detalle(bot)

    # ── usuarios ─────────────────────────────────────────────────────────────
    def usuarios(self) -> list[dict[str, Any]]:
        return [u.a_dict() for u in self._repo.usuarios()]

    def guardar_usuario(
        self, telegram_id: Any, email: str, nombre: str, bots: list[str], actor: str
    ) -> tuple[dict[str, Any], bool]:
        tid, email, nombre, bots = validar_usuario(telegram_id, email, nombre, bots)
        ahora = self._reloj()
        antes, despues = self._repo.guardar_usuario(
            UsuarioBot(telegram_id=tid, email=email, nombre=nombre, bots=bots),
            por=actor,
            ahora=ahora,
        )
        self._repo.bitacora_agregar(
            f"usuario:{tid}",
            "alta" if antes is None else "actualizar",
            antes.a_dict() if antes else {},
            despues.a_dict(),
            actor,
            ahora,
        )
        return despues.a_dict(), antes is None

    def borrar_usuario(self, telegram_id: Any, actor: str) -> None:
        tid = validar_telegram_id(telegram_id)
        previo = self._repo.borrar_usuario(tid)
        if previo is None:
            raise UsuarioNoEncontrado(str(tid))
        self._repo.bitacora_agregar(
            f"usuario:{tid}", "baja", previo.a_dict(), {}, actor, self._reloj()
        )

    # ── destinos ─────────────────────────────────────────────────────────────
    def destinos(self) -> dict[str, Any]:
        ahora = self._reloj()
        bots = self._todos()
        conteo = self._repo.conteo_capturas()
        return {
            **resumen_destinos(bots, ahora),
            "outbox_pendientes": sum(
                int(b.estado.get("outbox_pendientes", 0)) for b in bots if b.en_linea(ahora)
            ),
            "capturas": {
                e: conteo.get(e, 0) for e in ("PENDIENTE", "INCOMPLETO", "ERROR", "ENVIADO")
            },
            "bots_en_linea": sum(1 for b in bots if b.en_linea(ahora)),
            "bots_total": len(bots),
        }


def _diferencias(actual: Bot, nuevos: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(antes, despues)`` solo con lo que cambia; en parámetros y chats, clave por clave."""
    antes: dict[str, Any] = {}
    despues: dict[str, Any] = {}
    for campo, nuevo in nuevos.items():
        if campo in ("parametros", "chats"):
            base = {**(PARAMETROS_POR_DEFECTO if campo == "parametros" else CHATS_POR_DEFECTO)}
            base.update(getattr(actual, campo))
            cambios = {k: v for k, v in nuevo.items() if base.get(k) != v}
            if cambios:
                antes[campo] = {k: base.get(k) for k in cambios}
                despues[campo] = cambios
        elif getattr(actual, campo) != nuevo:
            antes[campo] = getattr(actual, campo)
            despues[campo] = nuevo
    return antes, despues
