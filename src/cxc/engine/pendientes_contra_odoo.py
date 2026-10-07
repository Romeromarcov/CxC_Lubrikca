"""Vinculaciones PENDIENTE que Odoo ya contradijo.

Regla del usuario (30-sep-2026): **Odoo manda**. Una vinculación PENDIENTE es una
propuesta del sistema (auto-FIFO o manual) mientras Odoo no concilia el pago; cuando
Odoo concilia, lo que diga Odoo prevalece y la propuesta debe desaparecer o achicarse
a lo que Odoo dejó sin conciliar. Al final todas las vinculaciones terminan conciliadas
por Odoo y solo quedan pendientes las que legítimamente esperan.

Por pago, con ``restante = monto del pago - conciliado por Odoo``:

- ``restante`` es ~0 (las conciliadas cubren el pago): TODAS sus pendientes se retiran.
  Caso real: pago 2018, pendiente en S00470 y conciliado en S00790 por el mismo monto.
- Si no: las pendientes se recorren en el orden FIFO del propio sistema (orden más
  antigua primero, y por registro a igualdad) repartiendo ``restante``. La que cabe entera
  se queda; la que lo excede se ACHICA a lo que queda; las que ya no tienen de dónde se
  retiran. Caso real: el FIFO asignó el pago completo (677) a S00614 y Odoo ya había
  conciliado 252,91 en S00719 -> la pendiente queda en 424,09.

La tolerancia es RELATIVA (0,01% del pago, mínimo 0,01): las pendientes en VES nacen de
convertir USD y difieren del pago en unos pocos bolívares (0,0003% a 0,003% en los 24 casos
reales), que no son un exceso.

Función pura: no lee ni escribe el repositorio.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal

from ..models import EstadoVinculacion, Pago, Vinculacion

TOLERANCIA_ABSOLUTA = Decimal("0.01")
TOLERANCIA_RELATIVA = Decimal("0.0001")


@dataclass(frozen=True)
class DepuracionPendientes:
    a_retirar: list[Vinculacion] = field(default_factory=list)
    a_ajustar: list[Vinculacion] = field(default_factory=list)  # ya con el monto nuevo


def _tolerancia(monto: Decimal) -> Decimal:
    return max(TOLERANCIA_ABSOLUTA, monto * TOLERANCIA_RELATIVA)


def _con_monto(v: Vinculacion, nuevo: Decimal) -> Vinculacion:
    """La misma vinculación con ``monto_aplicado = nuevo``: las tasas son las mismas, así
    que los equivalentes escalan en proporción."""
    factor = nuevo / v.monto_aplicado

    def escalar(x: Decimal | None) -> Decimal | None:
        return None if x is None else x * factor

    return replace(
        v,
        monto_aplicado=nuevo,
        equiv_usd_bcv=escalar(v.equiv_usd_bcv),
        equiv_usd_binance=escalar(v.equiv_usd_binance),
        equiv_ves_bcv=escalar(v.equiv_ves_bcv),
        equiv_ves_binance=escalar(v.equiv_ves_binance),
    )


def clasificar_pendientes(
    vinculaciones: list[Vinculacion],
    pagos: list[Pago],
    fechas_orden: dict[str, date] | None = None,
) -> DepuracionPendientes:
    fechas_orden = fechas_orden or {}
    monto_por_pago = {str(p.pago_id): p.monto for p in pagos}
    por_pago: dict[str, list[Vinculacion]] = {}
    for v in vinculaciones:
        por_pago.setdefault(str(v.pago_id), []).append(v)

    a_retirar: list[Vinculacion] = []
    a_ajustar: list[Vinculacion] = []
    for pago_id, vincs in por_pago.items():
        pendientes = [v for v in vincs if v.estado == EstadoVinculacion.PENDIENTE]
        if not pendientes or pago_id not in monto_por_pago:
            continue
        monto = monto_por_pago[pago_id]
        tol = _tolerancia(monto)
        conciliado = sum(
            (v.monto_aplicado for v in vincs if v.estado == EstadoVinculacion.CONCILIADO),
            Decimal("0"),
        )
        if conciliado > 0 and conciliado >= monto - tol:
            a_retirar.extend(pendientes)
            continue

        queda = monto - conciliado
        orden_fifo = sorted(
            pendientes,
            key=lambda v: (
                fechas_orden.get(v.so_id, date.max),
                v.timestamp_registro or datetime.max,
                v.vinc_id,
            ),
        )
        for v in orden_fifo:
            if queda <= tol:
                a_retirar.append(v)
            elif v.monto_aplicado > queda + tol:
                a_ajustar.append(_con_monto(v, queda))
                queda = Decimal("0")
            else:
                queda -= v.monto_aplicado
    return DepuracionPendientes(a_retirar=a_retirar, a_ajustar=a_ajustar)


# --- Odoo manda tambien sobre las CONCILIADAS y sobre las discrepancias abiertas ----------


def conciliadas_que_odoo_no_tiene(
    vinculaciones: list[Vinculacion],
    totales_odoo: dict[tuple[str, str], Decimal],
) -> list[Vinculacion]:
    """Vinculaciones CONCILIADAS cuyo par (pago, orden) Odoo ya no reconcilia.

    Solo se miran los pagos que Odoo SI reconcilia hoy (tienen al menos un par en
    ``totales_odoo``): ahi la lista de pares de Odoo es completa, y una conciliada local que
    no esta en ella es un residuo de un reparto anterior. Caso real: pago 14, Odoo reparte
    129,96 a S00046 y 0,04 a S01135, y quedaban dos filas locales de 0,04 a S00188 y S00170.
    Un pago que Odoo ya no reconcilia en absoluto lo maneja el resync (pasa a PENDIENTE).
    """
    pagos_con_reparto = {pago for pago, _ in totales_odoo}
    return [
        v
        for v in vinculaciones
        if v.estado == EstadoVinculacion.CONCILIADO
        and str(v.pago_id) in pagos_con_reparto
        and (str(v.pago_id), v.so_id) not in totales_odoo
    ]


def discrepancias_resueltas(
    pagos_auditados: list[str],
    vinculaciones: list[Vinculacion],
    totales_odoo: dict[tuple[str, str], Decimal],
) -> list[str]:
    """Pagos cuya discrepancia «multi-orden» ya no existe porque lo local coincide con Odoo.

    Se cierra cuando (a) Odoo ya no reconcilia el pago (lo local queda pendiente, no hay nada
    que comparar) o (b) las conciliadas locales son exactamente los pares y montos del reparto
    de Odoo. Medido el 7-oct-2026: 194 de 196 filas abiertas estaban en uno de esos casos.
    """
    odoo_por_pago: dict[str, dict[str, Decimal]] = {}
    for (pago, so), monto in totales_odoo.items():
        odoo_por_pago.setdefault(pago, {})[so] = monto
    locales: dict[str, dict[str, Decimal]] = {}
    for v in vinculaciones:
        if v.estado == EstadoVinculacion.CONCILIADO:
            locales.setdefault(str(v.pago_id), {})[v.so_id] = v.monto_aplicado

    resueltos: list[str] = []
    for pago in pagos_auditados:
        odoo = odoo_por_pago.get(str(pago), {})
        local = locales.get(str(pago), {})
        if not odoo or set(odoo) == set(local) and all(
            abs(odoo[so] - local[so]) <= Decimal("0.02") for so in odoo
        ):
            resueltos.append(str(pago))
    return resueltos
