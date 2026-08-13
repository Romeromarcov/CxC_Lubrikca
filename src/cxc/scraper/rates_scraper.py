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
            rows_today = self._repo.serie_tasas_del_dia(now.date())

            manana_rates = []
            tarde_rates = []
            diario_rates = []

            for s in rows_today:
                tb = s.tasa_binance
                if tb > Decimal("0"):
                    diario_rates.append(tb)
                    if 6 <= s.timestamp.hour <= 9:
                        manana_rates.append(tb)
                    elif 11 <= s.timestamp.hour <= 14:
                        tarde_rates.append(tb)

            if fila.tasa_binance > Decimal("0"):
                diario_rates.append(fila.tasa_binance)
                if 6 <= now.hour <= 9:
                    manana_rates.append(fila.tasa_binance)
                elif 11 <= now.hour <= 14:
                    tarde_rates.append(fila.tasa_binance)

            def avg(lst: list[Decimal]) -> Decimal | None:
                return sum(lst) / Decimal(str(len(lst))) if lst else None

            fila.tasa_binance_manana = avg(manana_rates)
            fila.tasa_binance_tarde = avg(tarde_rates)
            fila.tasa_binance_diario = avg(diario_rates)

            if (
                fila.tasa_binance_diario
                and fila.tasa_binance_diario > Decimal("0")
                and fila.tasa_bcv > Decimal("0")
            ):
                fila.diferencial_bcv_binance_pct = (
                    (fila.tasa_binance_diario - fila.tasa_bcv) / fila.tasa_binance_diario
                ) * Decimal("100")
        except Exception as e:
            logger.warning("Error calculando promedios de tasa Binance: %s", e)

        self._repo.append_serie_tasa(fila)
        self._chequear_alerta(fila)

        # Cierre de día (última captura programada, 22:00): el promedio
        # diario y el diferencial calculados arriba ya son, por
        # construcción, el promedio de TODAS las capturas del día (6:00 a
        # 22:00) -- se persisten en TasasHistoricasAuditoria como el
        # "promedio definitivo" para que get_binance_rate_for_date/
        # get_rate_for_datetime los usen en cálculos futuros, en vez de
        # quedar solo en SerieTasas (que un lookup por día exacto no
        # siempre revisita fila por fila).
        if now.hour == 22 and fila.tasa_binance_diario:
            self._finalizar_promedio_diario(now, fila)

        return fila

    def _finalizar_promedio_diario(self, now: datetime, fila: SerieTasa) -> None:
        try:
            rows_today = self._repo.serie_tasas_del_dia(now.date())
            capturas_ok = sum(1 for s in rows_today if s.capturada_ok) + (
                1 if fila.capturada_ok else 0
            )
            self._repo.upsert_tasa_historica_auditoria(
                {
                    "fecha": now.date().isoformat(),
                    "tasa_bcv_usd": str(fila.tasa_bcv) if fila.tasa_bcv else "",
                    "tasa_bcv_euro": str(fila.tasa_bcv_euro) if fila.tasa_bcv_euro else "",
                    "tasa_binance_promedio_diario": str(fila.tasa_binance_diario),
                    "diferencial_bcv_binance_pct": (
                        str(fila.diferencial_bcv_binance_pct)
                        if fila.diferencial_bcv_binance_pct is not None
                        else ""
                    ),
                    "fuente": "scraper (cierre de día, promedio definitivo)",
                    "notas": (
                        f"Promedio Binance definitivo {now.date().isoformat()}: "
                        f"{capturas_ok} captura(s) reales de 6:00 a 22:00."
                    ),
                }
            )
        except Exception as e:
            logger.warning("Error grabando promedio Binance definitivo de cierre de día: %s", e)

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
