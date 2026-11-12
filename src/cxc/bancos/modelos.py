"""Modelo canónico de un estado de cuenta, independiente del banco."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

CERO = Decimal("0.00")


@dataclass(frozen=True)
class MovimientoBancario:
    """Línea canónica de un estado de cuenta.

    ``debito``/``credito`` siempre >= 0. ``saldo`` es el saldo que el BANCO reporta
    tras el movimiento: es la evidencia para validar la cadena y entra en la huella.

    ``referencia`` es la referencia bancaria real (sirve para conciliar).
    ``correlativo`` es un número de fila que algunos reportes inventan (Provincial):
    se conserva para ordenar, pero NO entra en la huella, porque cambia cada vez que
    se exporta un rango distinto y duplicaría el mismo movimiento.
    """

    banco: str
    cuenta: str
    moneda: str
    fecha: date
    referencia: str
    descripcion: str
    debito: Decimal
    credito: Decimal
    saldo: Decimal | None
    orden: int  # posición cronológica dentro del archivo (1 = más antiguo)
    codigo: str = ""  # NC/ND si el banco lo informa
    correlativo: str = ""

    @property
    def neto(self) -> Decimal:
        return self.credito - self.debito

    @property
    def huella(self) -> str:
        """Hash estable: re-importar un corte solapado produce las mismas huellas.

        Sin saldo (ningún parser actual) dos movimientos idénticos del mismo día
        colisionarían; por eso, en ese caso, la posición dentro del archivo entra
        en la huella. Con saldo no hace falta: el saldo ya los distingue.
        """
        base = "|".join(
            [
                self.banco,
                self.cuenta,
                self.fecha.isoformat(),
                self.referencia,
                self.descripcion.strip().upper(),
                f"{self.debito:.2f}",
                f"{self.credito:.2f}",
                f"{self.saldo:.2f}" if self.saldo is not None else f"#{self.orden}",
            ]
        )
        return hashlib.sha256(base.encode()).hexdigest()[:24]

    @property
    def unique_import_id(self) -> str:
        """Clave de idempotencia para ``account.bank.statement.line.unique_import_id``."""
        return f"{self.banco}-{self.cuenta}-{self.huella}"


@dataclass
class EstadoCuenta:
    banco: str
    cuenta: str
    moneda: str
    archivo: str
    movimientos: list[MovimientoBancario] = field(default_factory=list)
    saldo_inicial: Decimal | None = None  # declarado por el archivo, o derivado
    saldo_final: Decimal | None = None  # declarado por el archivo (footer), si existe
    saldo_inicial_derivado: bool = False
    avisos: list[str] = field(default_factory=list)
