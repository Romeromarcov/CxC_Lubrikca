"""Equivalentes congelados por abono y regla de mezcla (sección 3.9b).

Cada abono registra su equivalente en ambas tasas, calculado UNA sola vez contra
la tasa estampada de su bucket horario, y NUNCA recalculado. Esto convierte la
valoración en dato auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ..decimal_utils import q6
from ..models import Moneda, TipoTasa, Vinculacion


@dataclass(frozen=True)
class Equivalentes:
    equiv_usd_bcv: Decimal
    equiv_usd_binance: Decimal
    equiv_ves_bcv: Decimal
    equiv_ves_binance: Decimal


def equivalentes_bcv(
    monto_aplicado: Decimal, moneda_abono: Moneda, tasa_bcv: Decimal
) -> tuple[Decimal, Decimal]:
    """El par de equivalentes del lado BCV: ``(equiv_usd_bcv, equiv_ves_bcv)``.

    Decimoseptima pieza de la Fase 2.4, y existe por una divergencia medida el
    11-sep-2026: ``post_cambiar_tipo_tasa_bcv`` --el endpoint que cambia la
    variante USD/EUR de una vinculación-- reimplementaba esta misma cuenta **sin
    pasar por ``q6``**, mientras ``calcular_equivalentes`` sí redondea a seis
    decimales.

    O sea que el mismo concepto se calculaba de dos maneras, y editar la variante
    reescribía un equivalente **congelado** con otra precisión que la que tenía al
    crearse. Con 1.000 Bs a tasa 3, uno guarda ``333.333333`` y el otro
    ``333.3333333333...``.

    Ahora las dos rutas comparten esta función. Las tasas positivas se exigen acá
    por la misma razón que en ``calcular_equivalentes``: un equivalente calculado
    con una tasa en cero o negativa no significa nada y queda congelado así.
    """
    if tasa_bcv <= 0:
        raise ValueError("La tasa BCV estampada debe ser positiva")
    if moneda_abono == Moneda.VES:
        return q6(monto_aplicado / tasa_bcv), q6(monto_aplicado)
    return q6(monto_aplicado), q6(monto_aplicado * tasa_bcv)


def equivalentes_binance(
    monto_aplicado: Decimal, moneda_abono: Moneda, tasa_binance: Decimal
) -> tuple[Decimal, Decimal]:
    """El par del lado Binance: ``(equiv_usd_binance, equiv_ves_binance)``.

    El gemelo de ``equivalentes_bcv``, y existe por la misma divergencia medida el
    11-sep-2026: ``post_editar_tasa_binance`` reimplementaba esta cuenta inline
    **sin ``q6``**, igual que el endpoint de la variante BCV hacia con la suya. Los
    dos reescribian un equivalente **congelado** con otra precision que la que
    tenia al nacer.

    Los dos lados existen por separado --y no una sola funcion con un parametro de
    tasa-- porque cada endpoint edita UN lado: el de la variante toca solo el BCV y
    el de Binance solo el Binance. Una funcion que devolviera los cuatro obligaria
    a cada uno a descartar dos, y descartar invita a pisar.
    """
    if tasa_binance <= 0:
        raise ValueError("La tasa Binance estampada debe ser positiva")
    if moneda_abono == Moneda.VES:
        return q6(monto_aplicado / tasa_binance), q6(monto_aplicado)
    return q6(monto_aplicado), q6(monto_aplicado * tasa_binance)


def calcular_equivalentes(
    monto_aplicado: Decimal,
    moneda_abono: Moneda,
    tasa_bcv: Decimal,
    tasa_binance: Decimal,
) -> Equivalentes:
    """Congela los cuatro equivalentes de un abono (ver fórmulas 3.9b)."""
    if tasa_bcv <= 0 or tasa_binance <= 0:
        raise ValueError("Las tasas estampadas deben ser positivas")
    m = monto_aplicado
    # El lado BCV sale de ``equivalentes_bcv``, que es la misma función que usa el
    # endpoint de cambio de variante -- así no pueden volver a divergir.
    usd_bcv, ves_bcv = equivalentes_bcv(m, moneda_abono, tasa_bcv)
    usd_bin, ves_bin = equivalentes_binance(m, moneda_abono, tasa_binance)
    return Equivalentes(
        equiv_usd_bcv=usd_bcv,
        equiv_usd_binance=usd_bin,
        equiv_ves_bcv=ves_bcv,
        equiv_ves_binance=ves_bin,
    )


def congelar_en_vinculacion(vinc: Vinculacion) -> Vinculacion:
    """Calcula y estampa los cuatro equivalentes en la vinculación, UNA vez.

    Si ya estaban congelados, no los recalcula (inmutabilidad 3.9b).
    """
    if vinc.equiv_usd_bcv is not None:
        return vinc
    eq = calcular_equivalentes(
        vinc.monto_aplicado,
        vinc.moneda_abono,
        vinc.tasa_bcv_aplicada,
        vinc.tasa_binance_aplicada,
    )
    vinc.equiv_usd_bcv = eq.equiv_usd_bcv
    vinc.equiv_usd_binance = eq.equiv_usd_binance
    vinc.equiv_ves_bcv = eq.equiv_ves_bcv
    vinc.equiv_ves_binance = eq.equiv_ves_binance
    return vinc


def es_ruta_bcv_pura(vinculaciones: list[Vinculacion]) -> bool:
    """True si TODOS los abonos fueron en ruta BCV (sección 3.9b, mezcla).

    Sin abonos no se puede afirmar "completo en BCV" → False (conservador).
    """
    if not vinculaciones:
        return False
    return all(v.tipo_tasa_abono == TipoTasa.BCV for v in vinculaciones)


def es_pago_mixto(vinculaciones: list[Vinculacion]) -> bool:
    """¿La orden se pagó en dólares Y en bolívares a la vez?

    Regla de negocio del usuario (auditoría de septiembre 2026): una orden
    con abonos en las dos monedas cuenta como USD, y sus abonos en VES se
    valoran por su equivalente Binance. Ver ``valor_pagado_usd``.
    """
    monedas = {v.moneda_abono for v in vinculaciones}
    return Moneda.USD in monedas and Moneda.VES in monedas


def valor_pagado_usd(vinculaciones: list[Vinculacion]) -> Decimal:
    """Σ de los equivalentes USD congelados, cada uno según la ruta del abono.

    No recalcula: suma lo ya estampado (sección 4.4).
    """
    total = Decimal("0")
    for v in vinculaciones:
        if v.tipo_tasa_abono == TipoTasa.BCV:
            eq = v.equiv_usd_bcv
        elif v.tipo_tasa_abono == TipoTasa.BINANCE:
            eq = v.equiv_usd_binance
        else:  # USD directo — ambos equivalentes USD son el monto
            eq = v.equiv_usd_binance
        if eq is None:
            raise ValueError(
                f"Vinculación {v.vinc_id} sin equivalentes congelados; "
                "llamar congelar_en_vinculacion primero"
            )
        total += eq
    return total


def valor_pagado_ves_bcv(vinculaciones: list[Vinculacion]) -> Decimal:
    """Σ de los equivalentes VES congelados a tasa BCV oficial.

    Simétrico a ``valor_pagado_bcv_usd`` (mismo patrón que ya usa
    Diferencial Cambiario para medir cobertura contra el teórico USD, ver
    ``discounts.py``): sirve para medir si lo pagado cubre el teórico VES
    de una orden, que está denominado en bolívares (lista VES). No
    recalcula: suma lo ya estampado por ``congelar_en_vinculacion``.
    """
    total = Decimal("0")
    for v in vinculaciones:
        eq = v.equiv_ves_bcv
        if eq is None:
            raise ValueError(
                f"Vinculación {v.vinc_id} sin equivalentes congelados; "
                "llamar congelar_en_vinculacion primero"
            )
        total += eq
    return total


def valor_pagado_binance_usd(vinculaciones: list[Vinculacion]) -> Decimal:
    """Σ de los equivalentes USD valuados a Tasa Binance o USD Cash directo."""
    total = Decimal("0")
    for v in vinculaciones:
        eq = v.equiv_usd_binance
        if eq is None:
            eq = v.monto_aplicado
        total += eq
    return total


def valor_pagado_bcv_usd(vinculaciones: list[Vinculacion]) -> Decimal:
    """Σ de los equivalentes USD valuados a Tasa BCV oficial o USD Cash directo."""
    total = Decimal("0")
    for v in vinculaciones:
        eq = v.equiv_usd_bcv
        if eq is None:
            eq = v.monto_aplicado
        total += eq
    return total


# --- el equivalente USD de un pago a una tasa dada ---------------------------


def equivalente_usd_a_tasa(
    monto: Decimal, moneda: str, tasa: float | Decimal | None
) -> float | None:
    """Cuanto vale en dolares un pago de ``monto`` en ``moneda``, dividido por ``tasa``.

    Trigesima pieza de la Fase 2.4. En ``get_cobranza_pagos_unificado`` este cuerpo
    estaba escrito DOS veces --``monto_eur`` y ``monto_bcv_real``-- identico salvo por
    cual tasa divide, y las dos con sus caminos de error sin cubrir.

    **Las dos existen por un bug real**, y conviene que quede escrito porque explica
    la forma: agosto 2026, pago 1279 del cliente SJMG 2012 C.A., la tarjeta mostraba
    ``tasa_bcv_real`` (752,09, correcta) junto a un equivalente calculado con la tasa
    BCV-EUR (865,17, que el sistema usa como base de conversion interna para clientes
    con ordenes historicas). El numero y su tasa no se correspondian.

    ``None`` --y no cero-- cuando no hay tasa con la que convertir: un pago cuyo
    equivalente no se pudo calcular no vale cero dolares, y la pantalla tiene que
    mostrar un guion y no un monto. Es la misma distincion que
    ``RangoDelDia.verificado`` y que ``diferencial_verificable``.

    Un pago que YA esta en dolares se devuelve tal cual, sin mirar la tasa: dividirlo
    seria convertir dos veces.
    """
    if (moneda or "").upper().strip() == "USD":
        return float(monto)
    if tasa is None:
        return None
    try:
        divisor = Decimal(str(tasa))
    except (TypeError, ValueError, InvalidOperation):
        return None
    if divisor <= 0:
        return None
    return float(monto / divisor)
