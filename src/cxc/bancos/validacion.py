from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .modelos import CERO, EstadoCuenta

TOL = Decimal("0.01")


@dataclass
class Resultado:
    banco: str
    archivo: str
    n_mov: int
    saldo_inicial: Decimal | None
    saldo_final_calculado: Decimal | None
    saldo_final_reportado: Decimal | None
    total_debitos: Decimal
    total_creditos: Decimal
    saltos: list[str] = field(default_factory=list)  # filas donde la cadena de saldos se rompe
    duplicados: list[str] = field(default_factory=list)
    ok: bool = True


def validar_estado(est: EstadoCuenta) -> Resultado:
    """saldo_prev + crédito - débito == saldo reportado, fila a fila (detecta huecos,
    filas faltantes, cortes parciales y errores de parseo de montos)."""
    r = Resultado(
        est.banco,
        est.archivo,
        len(est.movimientos),
        est.saldo_inicial,
        None,
        est.saldo_final,
        sum((m.debito for m in est.movimientos), CERO),
        sum((m.credito for m in est.movimientos), CERO),
    )
    prev = est.saldo_inicial
    for m in est.movimientos:
        if m.saldo is not None and prev is not None:
            esperado = prev + m.neto
            if abs(esperado - m.saldo) > TOL:
                r.saltos.append(
                    f"#{m.orden} {m.fecha} ref={m.referencia} esperado={esperado} banco={m.saldo} "
                    f"(Δ={m.saldo - esperado})"
                )
        if m.saldo is not None:
            prev = m.saldo
        elif prev is not None:
            prev = prev + m.neto
    r.saldo_final_calculado = prev
    if est.saldo_final is not None and prev is not None and abs(est.saldo_final - prev) > TOL:
        r.saltos.append(f"saldo final declarado {est.saldo_final} != último saldo {prev}")
    vistos: dict[str, int] = {}
    for m in est.movimientos:
        vistos[m.huella] = vistos.get(m.huella, 0) + 1
    r.duplicados = [h for h, n in vistos.items() if n > 1]
    r.ok = not r.saltos and not r.duplicados
    return r
