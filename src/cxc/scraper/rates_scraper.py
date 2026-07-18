"""Orquestador del scraper horario (secciones 5.2–5.4).

Captura Binance y BCV cada hora y hace **solo append** a SerieTasas. Si la
captura falla, hereda la última fila (es_heredada=TRUE, capturada_ok=FALSE). Tres
capturas fallidas consecutivas disparan alerta.

Decisión de diseño (anotada en TODO.md): ante fallo de CUALQUIERA de las dos
fuentes se hereda la fila completa del último bucket, para no mezclar una tasa
fresca con una heredada dentro de la misma fila (consistencia de auditoría).
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from ..alerts import Alerter
from ..config import ScraperPolicy
from ..models import SerieTasa
from ..repositories import Repository
from .bcv import BcvClient, OdooBcvClient
from .binance import BinanceClient

logger = logging.getLogger("cxc.scraper")


class ScraperError(RuntimeError):
    """No se pudo capturar ni heredar (sin tasa previa)."""


class RatesScraper:
    def __init__(
        self,
        repo: Repository,
        binance: BinanceClient,
        bcv: BcvClient | OdooBcvClient,
        alerter: Alerter,
        policy: ScraperPolicy,
        fuente: str = "binance+bcv",
    ) -> None:
        self._repo = repo
        self._binance = binance
        self._bcv = bcv
        self._alerter = alerter
        self._policy = policy
        self._fuente = fuente

    def run(self, now: datetime) -> SerieTasa:
        """Captura un bucket horario y lo agrega a SerieTasas. Devuelve la fila."""
        tasa_binance, tasa_bcv = self._intentar_captura()

        if tasa_binance is not None and tasa_bcv is not None:
            fila = SerieTasa(
                timestamp=now,
                tasa_bcv=tasa_bcv,
                tasa_binance=tasa_binance,
                fuente=self._fuente,
                es_heredada=False,
                capturada_ok=True,
            )
        else:
            fila = self._heredar(now)

        self._repo.append_serie_tasa(fila)
        self._chequear_alerta(fila)
        return fila

    def _intentar_captura(self) -> tuple[Decimal | None, Decimal | None]:
        tasa_binance: Decimal | None = None
        tasa_bcv: Decimal | None = None
        try:
            tasa_binance = self._binance.fetch_rate()
        except Exception as exc:  # noqa: BLE001 - cualquier fallo => fallback
            logger.warning("Fallo captura Binance: %s", exc)
        try:
            tasa_bcv = self._bcv.fetch_rate()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fallo captura BCV: %s", exc)
        return tasa_binance, tasa_bcv

    def _heredar(self, now: datetime) -> SerieTasa:
        ultima = self._repo.last_serie_tasa()
        if ultima is None:
            self._alerter.send(
                "Scraper de tasas: primera captura falló y no hay tasa previa "
                "para heredar. SerieTasas quedó sin fila para este bucket."
            )
            raise ScraperError("Captura falló y no hay tasa previa para heredar")
        return SerieTasa(
            timestamp=now,
            tasa_bcv=ultima.tasa_bcv,
            tasa_binance=ultima.tasa_binance,
            fuente=f"heredada de {ultima.timestamp.isoformat()}",
            es_heredada=True,
            capturada_ok=False,
        )

    def _chequear_alerta(self, fila: SerieTasa) -> None:
        if fila.capturada_ok:
            return
        fallidas = self._repo.trailing_failed_captures()
        if fallidas >= self._policy.fail_alert_threshold:
            self._alerter.send(
                f"Scraper de tasas: {fallidas} capturas fallidas consecutivas. "
                "Binance/BCV podrían haber cambiado de formato o estar caídos. "
                "Revisar configuración del scraper."
            )
