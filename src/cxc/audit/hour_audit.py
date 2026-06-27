"""Auditoría de la hora declarada del pago contra el banco (sección 6.3).

La hora declarada (``Vinculacion.hora_pago_confirmada``) es una afirmación
anclada al recibo. La verificación real ocurre contra el estado de cuenta. El
sistema marca SOLO las excepciones (no audita el universo):

  - El abono no aparece en el estado de cuenta.
  - La hora declarada difiere de la real más de un umbral (minutos).
  - El abono se valoró con tasa heredada (``es_tasa_heredada``) → se revisa
    PRIMERO (sección 5.3): si Binance se movió en la hora muerta, su descuento
    salió con una tasa que no era la real.
  - El swing de la tasa Binance entre la hora declarada y la real supera X%.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum

from ..config import HourAuditConfig
from ..models import Vinculacion


class Prioridad(str, Enum):
    ALTA = "alta"
    MEDIA = "media"


@dataclass(frozen=True)
class BankMovement:
    """Una línea del estado de cuenta importado."""

    monto: Decimal
    fecha_hora: datetime
    referencia: str = ""


@dataclass
class AuditFinding:
    vinc_id: str
    hora_declarada: datetime
    hora_real: datetime | None
    delta_minutos: int | None
    prioridad: Prioridad
    motivos: list[str] = field(default_factory=list)


# Función opcional para consultar la tasa Binance vigente en un instante (de
# SerieTasas). Devuelve None si no hay bucket aplicable.
RateLookup = Callable[[datetime], Decimal | None]


def _emparejar(
    vinc: Vinculacion, movimientos: list[BankMovement]
) -> BankMovement | None:
    """Empareja por monto exacto; si hay varios, el más cercano a la hora declarada."""
    candidatos = [m for m in movimientos if m.monto == vinc.monto_aplicado]
    if not candidatos:
        return None
    return min(
        candidatos,
        key=lambda m: abs((m.fecha_hora - vinc.hora_pago_confirmada).total_seconds()),
    )


class HourAuditor:
    def __init__(
        self, config: HourAuditConfig, rate_lookup: RateLookup | None = None
    ) -> None:
        self._config = config
        self._rate_lookup = rate_lookup

    def auditar(
        self, vinculaciones: list[Vinculacion], movimientos: list[BankMovement]
    ) -> list[AuditFinding]:
        """Devuelve SOLO las vinculaciones que requieren revisión, priorizadas."""
        hallazgos: list[AuditFinding] = []
        for v in vinculaciones:
            hallazgo = self._auditar_una(v, movimientos)
            if hallazgo is not None:
                hallazgos.append(hallazgo)
        # Heredadas y faltantes (ALTA) primero; dentro, mayor desvío primero.
        hallazgos.sort(
            key=lambda h: (
                0 if h.prioridad == Prioridad.ALTA else 1,
                -(h.delta_minutos or 0),
            )
        )
        return hallazgos

    def _auditar_una(
        self, v: Vinculacion, movimientos: list[BankMovement]
    ) -> AuditFinding | None:
        motivos: list[str] = []
        prioridad = Prioridad.MEDIA

        if v.es_tasa_heredada:
            motivos.append("abono valorado con tasa heredada — revisar primero")
            prioridad = Prioridad.ALTA

        mov = _emparejar(v, movimientos)
        if mov is None:
            motivos.append("sin movimiento bancario que calce el monto")
            return AuditFinding(
                vinc_id=v.vinc_id,
                hora_declarada=v.hora_pago_confirmada,
                hora_real=None,
                delta_minutos=None,
                prioridad=Prioridad.ALTA,
                motivos=motivos,
            )

        delta_min = int(
            abs((mov.fecha_hora - v.hora_pago_confirmada).total_seconds()) // 60
        )
        if delta_min > self._config.threshold_minutes:
            motivos.append(
                f"hora declarada difiere {delta_min} min de la real "
                f"(umbral {self._config.threshold_minutes})"
            )

        if self._supera_swing(v, mov.fecha_hora):
            motivos.append("swing de tasa Binance supera el umbral entre hora declarada y real")

        if not motivos:
            return None  # cuadra: no se reporta (no se audita el universo)
        return AuditFinding(
            vinc_id=v.vinc_id,
            hora_declarada=v.hora_pago_confirmada,
            hora_real=mov.fecha_hora,
            delta_minutos=delta_min,
            prioridad=prioridad,
            motivos=motivos,
        )

    def _supera_swing(self, v: Vinculacion, hora_real: datetime) -> bool:
        if self._rate_lookup is None:
            return False
        tasa_real = self._rate_lookup(hora_real)
        if tasa_real is None or v.tasa_binance_aplicada <= 0:
            return False
        swing = abs(tasa_real - v.tasa_binance_aplicada) / v.tasa_binance_aplicada
        return swing > self._config.rate_swing_pct
