"""Saldos consolidados por cuenta, convertidos a USD y VES con la tasa BCV del día.

Se arma desde los cortes (``bancos_archivos``), no sumando movimientos: el saldo que
importa es el que el BANCO reporta, y cada corte ya fue validado fila a fila.

Además de la foto, verifica la continuidad entre cortes de una misma cuenta: si dos
cortes no se solapan, el saldo inicial del siguiente tiene que ser el saldo final
del anterior. Si no lo es, faltan movimientos entre ambos (un hueco), aunque cada
archivo cuadre por separado.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from cxc.decimal_utils import q2

from .modelos import CERO
from .validacion import TOL


@dataclass(frozen=True)
class Corte:
    archivo_id: int
    banco: str
    cuenta: str
    moneda: str
    nombre_archivo: str
    fecha_desde: date | None
    fecha_hasta: date | None
    saldo_inicial: Decimal | None
    saldo_final: Decimal | None
    cuadra: bool
    cargado_en: datetime


@dataclass
class SaldoCuenta:
    banco: str
    cuenta: str
    moneda: str
    fecha_corte: date | None
    saldo: Decimal | None
    tasa_bcv: Decimal | None
    saldo_usd: Decimal | None
    saldo_ves: Decimal | None
    n_cortes: int
    cortes_sin_cuadrar: int
    alertas: list[str] = field(default_factory=list)


@dataclass
class Consolidado:
    cuentas: list[SaldoCuenta]
    total_usd: Decimal
    total_ves: Decimal
    cuentas_sin_tasa: list[str]

    @property
    def fechas_de_corte(self) -> list[date]:
        return sorted({c.fecha_corte for c in self.cuentas if c.fecha_corte})


def _convertir(
    saldo: Decimal, moneda: str, tasa: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    if moneda == "USD":
        return q2(saldo), (q2(saldo * tasa) if tasa else None)
    if moneda == "VES":
        return (q2(saldo / tasa) if tasa else None), q2(saldo)
    return None, None


def _continuidad(cortes: list[Corte]) -> list[str]:
    alertas: list[str] = []
    ordenados = sorted(
        (c for c in cortes if c.fecha_desde and c.fecha_hasta),
        key=lambda c: (c.fecha_desde, c.fecha_hasta, c.cargado_en),
    )
    for prev, sig in zip(ordenados, ordenados[1:], strict=False):
        assert prev.fecha_hasta and sig.fecha_desde
        if sig.fecha_desde <= prev.fecha_hasta:
            continue  # solapados: la huella deduplica, no hay frontera que comparar
        if prev.saldo_final is None or sig.saldo_inicial is None:
            continue
        if abs(sig.saldo_inicial - prev.saldo_final) > TOL:
            alertas.append(
                f"hueco entre {prev.fecha_hasta} y {sig.fecha_desde}: el corte "
                f"'{prev.nombre_archivo}' cierra en {q2(prev.saldo_final)} y "
                f"'{sig.nombre_archivo}' abre en {q2(sig.saldo_inicial)} "
                f"(Δ={q2(sig.saldo_inicial - prev.saldo_final)})"
            )
    return alertas


def consolidar(cortes: list[Corte], tasa_bcv: Callable[[date], Decimal | None]) -> Consolidado:
    por_cuenta: dict[tuple[str, str], list[Corte]] = defaultdict(list)
    for c in cortes:
        por_cuenta[(c.banco, c.cuenta)].append(c)

    cuentas: list[SaldoCuenta] = []
    total_usd = total_ves = CERO
    sin_tasa: list[str] = []
    for (banco, cuenta), lista in sorted(por_cuenta.items()):
        ultimo = max(lista, key=lambda c: (c.fecha_hasta or date.min, c.cargado_en))
        tasa = tasa_bcv(ultimo.fecha_hasta) if ultimo.fecha_hasta else None
        usd = ves = None
        if ultimo.saldo_final is not None:
            usd, ves = _convertir(ultimo.saldo_final, ultimo.moneda, tasa)
        if usd is None or ves is None:
            sin_tasa.append(banco)
        else:
            total_usd += usd
            total_ves += ves
        alertas = _continuidad(lista)
        if not ultimo.cuadra:
            alertas.insert(0, f"el último corte ('{ultimo.nombre_archivo}') no cuadra")
        cuentas.append(
            SaldoCuenta(
                banco=banco,
                cuenta=cuenta,
                moneda=ultimo.moneda,
                fecha_corte=ultimo.fecha_hasta,
                saldo=ultimo.saldo_final,
                tasa_bcv=tasa,
                saldo_usd=usd,
                saldo_ves=ves,
                n_cortes=len(lista),
                cortes_sin_cuadrar=sum(1 for c in lista if not c.cuadra),
                alertas=alertas,
            )
        )
    return Consolidado(cuentas, q2(total_usd), q2(total_ves), sin_tasa)
