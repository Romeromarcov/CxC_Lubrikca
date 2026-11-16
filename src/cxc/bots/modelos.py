"""Modelo y validación del panel de bots (plano de control de la familia de bots de Telegram).

Todo es puro (sin red ni base). Reglas de oro:

- Los bots son procesos aparte: reportan latido y reciben su configuración.
- NUNCA se guardan ni se devuelven tokens de Telegram ni API keys: viven en el entorno de cada bot.
  Lo que un bot reporta de texto libre (``ultimo_error``) se trunca y se redacta antes de guardarse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

# Familia (ids fijos) y nombre por defecto.
BOTS: dict[str, str] = {
    "pedidos": "Ventas",
    "cobranza": "Cobranzas",
    "compras": "Compras",
    "eventos": "Eventos",
    "asana": "Asana",
}

# Tipos de captura que llegan a CxC por cada bot (``capturas_bot.kind``). Eventos y Asana no
# mandan capturas a CxC.
KINDS_POR_BOT: dict[str, tuple[str, ...]] = {
    "pedidos": ("contacto", "pedido"),
    "cobranza": ("pago",),
    "compras": ("pago_proveedor", "factura_proveedor", "proveedor", "gasto"),
    "eventos": (),
    "asana": (),
}

EN_LINEA_SEGUNDOS = 180  # un bot está «en línea» si su último latido tiene menos de 3 min
RETENCION_LATIDOS_DIAS = 30

PARAMETROS_POR_DEFECTO: dict[str, Any] = {"auto_envio_min": 10, "gemini_model": "gemini-2.5-flash"}
CHATS_POR_DEFECTO: dict[str, int | None] = {"respaldo": None, "grupo": None}
MODELOS_GEMINI_SUGERIDOS: tuple[str, ...] = (
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-flash-latest",
    "gemini-pro-latest",
)

ESTADOS_DESTINO = ("ok", "error", "desactivado")
ESTADOS_GEMINI = ("ok", "error", "sin_clave")


class DatoInvalido(ValueError):
    """Dato rechazado por el panel (mensaje en español, mostrable tal cual)."""


# ── redacción de secretos ────────────────────────────────────────────────────

_SECRETOS = (
    # Token de bot de Telegram. Sin \b delante a propósito: en las URL va pegado a «bot»
    # (``/bot0000000000:AAG…``) y entre la «t» y el «9» no hay frontera de palabra.
    re.compile(r"(?<!\d)\d{8,12}:[A-Za-z0-9_-]{30,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),  # API key de Google (idem: puede ir pegada a otro texto)
    re.compile(r"\bcxc_live_[A-Za-z0-9_-]{10,}\b"),  # llave de API de CxC
    re.compile(r"(?i)\b(bearer|x-bot-key)\s*[:=]?\s*[A-Za-z0-9._~+/=-]{12,}"),
)


def redactar(texto: str) -> str:
    """Oculta cualquier cosa que parezca un token o una llave."""
    for rx in _SECRETOS:
        texto = rx.sub("[oculto]", texto)
    return texto


# ── validaciones ─────────────────────────────────────────────────────────────

_MODELO_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,59}$")
_EMAIL_RX = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")


def _entero(valor: Any, campo: str, minimo: int, maximo: int) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int | str):
        raise DatoInvalido(f"{campo} debe ser un número entero.")
    try:
        n = int(valor)
    except ValueError as e:
        raise DatoInvalido(f"{campo} debe ser un número entero.") from e
    if not minimo <= n <= maximo:
        raise DatoInvalido(f"{campo} debe estar entre {minimo} y {maximo}.")
    return n


def validar_parametros(nuevos: dict[str, Any]) -> dict[str, Any]:
    """Valida los parámetros que se quieren cambiar; solo se aceptan claves conocidas."""
    out: dict[str, Any] = {}
    for clave, valor in nuevos.items():
        if clave == "auto_envio_min":
            out[clave] = _entero(valor, "auto_envio_min", 1, 120)
        elif clave == "gemini_model":
            if not isinstance(valor, str) or not _MODELO_RX.match(valor.strip()):
                raise DatoInvalido("gemini_model no es un nombre de modelo válido.")
            out[clave] = valor.strip()
        else:
            raise DatoInvalido(f"Parámetro desconocido: {clave}.")
    return out


def validar_chats(nuevos: dict[str, Any]) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for clave, valor in nuevos.items():
        if clave not in CHATS_POR_DEFECTO:
            raise DatoInvalido(f"Chat desconocido: {clave}.")
        if valor in (None, ""):
            out[clave] = None
            continue
        n = _entero(valor, f"chats.{clave}", -(10**15), 10**15)
        if n == 0:
            raise DatoInvalido(f"chats.{clave} no puede ser 0.")
        out[clave] = n
    return out


def validar_url_panel(valor: str) -> str:
    v = valor.strip()
    if not v:
        return ""
    u = urlparse(v)
    if (
        u.scheme not in ("http", "https")
        or not u.netloc
        or len(v) > 300
        or any(c.isspace() for c in v)
    ):
        raise DatoInvalido("url_panel debe ser una dirección http(s) válida.")
    return v


def validar_mensaje(valor: str) -> str:
    v = valor.strip()
    if len(v) > 280:
        raise DatoInvalido("El mensaje no puede pasar de 280 caracteres.")
    return v


def validar_telegram_id(valor: Any) -> int:
    n = _entero(valor, "telegram_id", 1, 10**13)
    return n


def validar_usuario(
    telegram_id: Any, email: str, nombre: str, bots: list[str]
) -> tuple[int, str, str, list[str]]:
    tid = validar_telegram_id(telegram_id)
    email = (email or "").strip().lower()
    if email and not _EMAIL_RX.match(email):
        raise DatoInvalido("El correo no es válido.")
    nombre = (nombre or "").strip()
    if len(nombre) > 100:
        raise DatoInvalido("El nombre no puede pasar de 100 caracteres.")
    if not bots:
        raise DatoInvalido("Indica al menos un bot permitido.")
    desconocidos = [b for b in bots if b not in BOTS]
    if desconocidos:
        raise DatoInvalido(f"Bot desconocido: {', '.join(desconocidos)}.")
    return tid, email, nombre, sorted(set(bots), key=list(BOTS).index)


def _texto(valor: Any, maximo: int) -> str:
    return redactar(str(valor))[:maximo] if valor is not None else ""


def _contador(valor: Any) -> int:
    try:
        return max(0, min(int(valor), 10**9))
    except (TypeError, ValueError):
        return 0


def sanear_estado(estado: dict[str, Any]) -> dict[str, Any]:
    """Lo que el bot reporta de sí mismo, normalizado y sin secretos."""

    def _opcion(valor: Any, validas: tuple[str, ...], defecto: str) -> str:
        return str(valor) if valor in validas else defecto

    ultimo = estado.get("ultimo_error")
    return {
        "modo": _texto(estado.get("modo"), 30),
        "gemini": _opcion(estado.get("gemini"), ESTADOS_GEMINI, "sin_clave"),
        "omni": _opcion(estado.get("omni"), ESTADOS_DESTINO, "desactivado"),
        "cxc": _opcion(estado.get("cxc"), ESTADOS_DESTINO, "desactivado"),
        "outbox_pendientes": _contador(estado.get("outbox_pendientes")),
        "mensajes_hoy": _contador(estado.get("mensajes_hoy")),
        "errores_hoy": _contador(estado.get("errores_hoy")),
        "ultimo_error": _texto(ultimo, 300) if ultimo else None,
    }


# ── entidades ────────────────────────────────────────────────────────────────


@dataclass
class Bot:
    bot: str
    nombre: str
    habilitado: bool = True
    parametros: dict[str, Any] = field(default_factory=lambda: dict(PARAMETROS_POR_DEFECTO))
    chats: dict[str, int | None] = field(default_factory=lambda: dict(CHATS_POR_DEFECTO))
    url_panel: str = ""
    mensaje: str = ""
    version: str = ""
    estado: dict[str, Any] = field(default_factory=dict)
    ultimo_latido: datetime | None = None
    actualizado_por: str = "sistema"
    actualizado_en: datetime | None = None

    def en_linea(self, ahora: datetime) -> bool:
        return self.ultimo_latido is not None and ahora - self.ultimo_latido < timedelta(
            seconds=EN_LINEA_SEGUNDOS
        )

    def configuracion(self) -> dict[str, Any]:
        """Lo que el bot recibe en la respuesta del latido (sin usuarios; los añade el servicio)."""
        return {
            "habilitado": self.habilitado,
            "parametros": {**PARAMETROS_POR_DEFECTO, **self.parametros},
            "chats": {**CHATS_POR_DEFECTO, **self.chats},
            "mensaje": self.mensaje or None,
        }

    def a_dict(self, ahora: datetime) -> dict[str, Any]:
        return {
            "bot": self.bot,
            "nombre": self.nombre,
            "habilitado": self.habilitado,
            "en_linea": self.en_linea(ahora),
            "ultimo_latido": self.ultimo_latido.isoformat() if self.ultimo_latido else None,
            # El servidor calcula la antigüedad: la pantalla no depende de zonas horarias.
            "hace_segundos": (
                int((ahora - self.ultimo_latido).total_seconds()) if self.ultimo_latido else None
            ),
            "version": self.version,
            "estado": self.estado,
            "parametros": {**PARAMETROS_POR_DEFECTO, **self.parametros},
            "chats": {**CHATS_POR_DEFECTO, **self.chats},
            "url_panel": self.url_panel,
            "mensaje": self.mensaje,
            "actualizado_por": self.actualizado_por,
            "actualizado_en": self.actualizado_en.isoformat() if self.actualizado_en else None,
        }


@dataclass
class UsuarioBot:
    telegram_id: int
    email: str
    nombre: str
    bots: list[str]
    creado_por: str = "sistema"
    creado_en: datetime | None = None

    def a_dict(self) -> dict[str, Any]:
        return {
            "telegram_id": self.telegram_id,
            "email": self.email,
            "nombre": self.nombre,
            "bots": self.bots,
            "creado_por": self.creado_por,
            "creado_en": self.creado_en.isoformat() if self.creado_en else None,
        }


def resumen_destinos(bots: list[Bot], ahora: datetime) -> dict[str, dict[str, Any]]:
    """Salud de Omni, CxC y Gemini según el último latido de los bots EN LÍNEA.

    ``error`` si algún bot en línea lo reporta en error; ``ok`` si alguno lo reporta ok;
    ``desactivado`` si todos lo tienen desactivado; ``sin_datos`` si no hay bots en línea.
    """
    out: dict[str, dict[str, Any]] = {}
    for destino, desactivado in (
        ("omni", "desactivado"),
        ("cxc", "desactivado"),
        ("gemini", "sin_clave"),
    ):
        reportes = {
            b.bot: b.estado.get(destino)
            for b in bots
            if b.en_linea(ahora) and b.estado.get(destino)
        }
        valores = set(reportes.values())
        if not reportes:
            estado = "sin_datos"
        elif "error" in valores:
            estado = "error"
        elif "ok" in valores:
            estado = "ok"
        elif valores == {desactivado}:
            estado = "desactivado" if destino != "gemini" else "sin_clave"
        else:
            estado = "sin_datos"
        out[destino] = {"estado": estado, "por_bot": reportes}
    return out
