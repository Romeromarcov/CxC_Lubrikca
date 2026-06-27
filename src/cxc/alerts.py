"""Canal de alertas — fallback del scraper (3 fallos) y desvíos de auditoría.

Abstraído tras una interfaz para no acoplar la lógica a Telegram. En tests se
usa ``CollectingAlerter`` (no red); en producción ``TelegramAlerter`` o, si no
hay credenciales, ``LoggingAlerter``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from .config import AlertConfig

logger = logging.getLogger("cxc.alerts")


class Alerter(ABC):
    @abstractmethod
    def send(self, mensaje: str) -> None: ...


class LoggingAlerter(Alerter):
    """Alerta a logs. Default seguro cuando no hay canal externo configurado."""

    def send(self, mensaje: str) -> None:
        logger.warning("ALERTA CxC: %s", mensaje)


class CollectingAlerter(Alerter):
    """Guarda los mensajes en memoria — para tests."""

    def __init__(self) -> None:
        self.mensajes: list[str] = []

    def send(self, mensaje: str) -> None:
        self.mensajes.append(mensaje)


class TelegramAlerter(Alerter):
    """Envía por Telegram Bot API. Requiere ``requests`` y credenciales."""

    def __init__(self, config: AlertConfig) -> None:
        if not config.telegram_bot_token or not config.telegram_chat_id:
            raise ValueError("TelegramAlerter requiere bot_token y chat_id")
        self._config = config

    def send(self, mensaje: str) -> None:  # pragma: no cover - red externa
        import requests

        url = (
            f"{self._config.telegram_api_url}"
            f"/bot{self._config.telegram_bot_token}/sendMessage"
        )
        requests.post(
            url,
            json={"chat_id": self._config.telegram_chat_id, "text": mensaje},
            timeout=15,
        )


def build_alerter(config: AlertConfig) -> Alerter:
    """Telegram si hay credenciales; si no, alerta a logs."""
    if config.telegram_bot_token and config.telegram_chat_id:
        return TelegramAlerter(config)  # pragma: no cover - requiere credenciales
    return LoggingAlerter()
