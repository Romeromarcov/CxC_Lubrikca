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
        tasa_binance, tasa_bcv, tasa_bcv_euro = self._intentar_captura()

        if tasa_binance is not None and tasa_bcv is not None:
            fila = SerieTasa(
                timestamp=now,
                tasa_bcv=tasa_bcv,
                tasa_binance=tasa_binance,
                tasa_bcv_euro=tasa_bcv_euro,
                fuente=self._fuente,
                es_heredada=False,
                capturada_ok=True,
            )
        else:
            fila = self._heredar(now)

        # Compute morning, afternoon, and daily averages for the current day
        try:
            today_str = now.strftime("%Y-%m-%d")
            rows_today = [
                r for r in self._repo._g.read_rows("SerieTasas")
                if r.get("timestamp", "").startswith(today_str)
            ]
            
            manana_rates = []
            tarde_rates = []
            diario_rates = []

            for r in rows_today:
                try:
                    tb = Decimal(str(r.get("tasa_binance", "0")))
                    if tb > Decimal("0"):
                        diario_rates.append(tb)
                        ts_str = str(r.get("timestamp", "00:00"))
                        time_part = ts_str.split("T")[-1].split(" ")[-1]
                        ts_hour = int(time_part.split(":")[0])
                        if 6 <= ts_hour <= 9:
                            manana_rates.append(tb)
                        elif 10 <= ts_hour <= 13:
                            tarde_rates.append(tb)
                except:
                    pass

            if fila.tasa_binance > Decimal("0"):
                diario_rates.append(fila.tasa_binance)
                if 6 <= now.hour <= 9:
                    manana_rates.append(fila.tasa_binance)
                elif 10 <= now.hour <= 13:
                    tarde_rates.append(fila.tasa_binance)

            avg = lambda lst: sum(lst) / Decimal(str(len(lst))) if lst else None
            fila.tasa_binance_manana = avg(manana_rates)
            fila.tasa_binance_tarde = avg(tarde_rates)
            fila.tasa_binance_diario = avg(diario_rates)
            
            if fila.tasa_binance_diario and fila.tasa_binance_diario > Decimal("0") and fila.tasa_bcv > Decimal("0"):
                fila.diferencial_bcv_binance_pct = ((fila.tasa_binance_diario - fila.tasa_bcv) / fila.tasa_binance_diario) * Decimal("100")
        except Exception as e:
            logger.warning("Error calculando promedios de tasa Binance: %s", e)

        self._repo.append_serie_tasa(fila)
        self._chequear_alerta(fila)
        return fila

    def _intentar_captura(self) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
        tasa_binance: Decimal | None = None
        tasa_bcv: Decimal | None = None
        tasa_bcv_euro: Decimal | None = None
        try:
            tasa_binance = self._binance.fetch_rate()
        except Exception as exc:  # noqa: BLE001 - cualquier fallo => fallback
            logger.warning("Fallo captura Binance: %s", exc)
        try:
            if hasattr(self._bcv, "fetch_rates"):
                tasa_bcv, tasa_bcv_euro = self._bcv.fetch_rates()
            else:
                tasa_bcv = self._bcv.fetch_rate()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fallo captura BCV: %s", exc)
        return tasa_binance, tasa_bcv, tasa_bcv_euro

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
            tasa_bcv_euro=ultima.tasa_bcv_euro,
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
