"""Las dos referencias de un mismo residual, para sugerir una conciliación.

Sexta pieza de la Fase 2.4 del plan de blindaje, y la primera del área que el plan
nombra explícitamente: "sacar los cálculos de saldos, **la conciliación** y el
armado de reportes".

**Por qué esta parte.** ``usd_bcv_to_binance`` y los tres campos de saldo que
``_get_conciliaciones_sugerencias_sync`` muestra por cada pago sin aplicar son
puros: entran cuatro números y salen tres. Pero vivían de una forma que valía
arreglar por sí sola -- ``saldo_fields`` estaba **definida adentro de un ``for``**,
y para no capturar la variable de loop de la iteración siguiente usaba argumentos
por defecto como amarre:

.. code-block:: python

    for p in unallocated_pagos:
        bcv_rate, binance_rate, moneda_p = p["tasa_bcv"], p["tasa_binance"], p["moneda"]

        def saldo_fields(restante, moneda_r=moneda_p, bcv_r=bcv_rate, ...):
            ...

El comentario del código explicaba el truco, así que el peligro estaba
identificado. Pero un truco que hay que recordar es una trampa esperando: alcanza
con que alguien agregue un cuarto valor del loop y se olvide de amarrarlo para que
la sugerencia muestre los números del pago siguiente. Una función a nivel de módulo
no tiene closure que amarrar, así que el problema **deja de existir** en vez de
quedar esquivado.

**Qué significan los tres campos.** Un pago en bolívares no tiene una única
referencia en dólares: depende de qué tasa se le aplique, y la que aplique al
vincular puede ajustarse (por hora) antes de confirmar. Mostrar las dos evita que
una parezca faltante. Para un pago en dólares las dos coinciden, porque no hay tasa
que aplicar.

**No cambia ningún monto**: la lógica se movió tal cual.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

CERO = Decimal("0")
MONEDA_LOCAL = "VES"


def usd_bcv_a_binance(
    usd_via_bcv: Decimal, moneda: str, tasa_bcv: Decimal, tasa_binance: Decimal
) -> Decimal:
    """Reexpresa un equivalente USD calculado con tasa BCV usando la de Binance.

    Ancla el monto en bolívares (``usd_via_bcv * tasa_bcv``) y lo reconvierte con
    ``tasa_binance``, para mostrar las dos referencias del mismo pago sin
    recalcular desde la celda cruda.

    Para un pago en dólares devuelve el valor sin tocar: no hay tasa que aplicar.
    Con ``tasa_binance`` en cero o negativa **también** devuelve el valor sin
    tocar, y eso es deliberado -- dividir por cero reventaría, y devolver cero
    haría ver el saldo como inexistente, que es justamente la clase de error que
    este blindaje persigue. Devolver la referencia BCV es la opción que no
    inventa un número ni oculta el saldo.
    """
    if moneda == MONEDA_LOCAL and tasa_binance > CERO:
        return usd_via_bcv * tasa_bcv / tasa_binance
    return usd_via_bcv


def campos_de_saldo(
    restante_usd_bcv: Decimal,
    moneda: str,
    tasa_bcv: Decimal,
    tasa_binance: Decimal,
) -> dict[str, Any]:
    """Las tres referencias del residual de un pago, para la sugerencia.

    - ``saldo_pago``: el residual en USD vía tasa BCV;
    - ``saldo_pago_binance``: el mismo residual vía tasa Binance;
    - ``saldo_pago_original``: el residual en la moneda en que se pagó.

    Antes era una función anidada en un ``for`` con argumentos por defecto para
    no capturar la variable de loop. Acá no hay loop del que capturar nada: los
    cuatro valores entran por parámetro, siempre.
    """
    return {
        "saldo_pago": float(restante_usd_bcv),
        "saldo_pago_binance": float(
            usd_bcv_a_binance(restante_usd_bcv, moneda, tasa_bcv, tasa_binance)
        ),
        "saldo_pago_original": float(
            restante_usd_bcv * tasa_bcv if moneda == MONEDA_LOCAL else restante_usd_bcv
        ),
    }


# --- el reparto de un pago entre las órdenes abiertas de su cliente --------

# Debajo de esto un saldo se considera cubierto. Un centavo de residuo no es una
# deuda, y ofrecer una sugerencia de 0,003 es ruido.
UMBRAL_CUBIERTO = Decimal("0.05")


def repartir_pago_entre_ordenes(
    restante: Decimal,
    ordenes: list[dict[str, Any]],
    *,
    fila: Callable[[Decimal, dict[str, Any], Decimal], dict[str, Any] | None],
) -> tuple[list[dict[str, Any]], Decimal]:
    """Reparte ``restante`` en orden, y devuelve (filas, lo que sobró).

    Séptima pieza de la Fase 2.4. El reparto FIFO de un pago entre las órdenes
    abiertas del mismo cliente: la sugerencia que el usuario ve y acepta con un
    clic, así que es un camino de dinero de los directos.

    ``ordenes`` se recorre **en el orden en que viene** -- el llamador decide la
    política (hoy es por fecha, FIFO) y ésta no la reinventa. Cada orden es un
    dict con ``saldo_pendiente``, y **se muta**: el saldo baja a medida que se
    reparte. Eso es a propósito y el llamador depende de ello, porque el mismo
    dict de orden se comparte entre los pagos del mismo cliente y así un segundo
    pago no vuelve a ofrecer lo que el primero ya cubrió.

    ``fila`` recibe ``(restante_antes, orden, monto_a_aplicar)`` y devuelve la fila
    a mostrar, **o ``None`` para no ofrecerla**. Ese ``None`` es la parte sutil, y
    tiene consecuencias que conviene decir en voz alta: una sugerencia no ofrecida
    **no consume** el pago ni el saldo de la orden. Es coherente -- lo que no se
    ofrece no se puede aceptar, así que no puede haber comprometido nada -- y es
    exactamente lo que el código hacía con un ``continue`` antes de los dos
    descuentos. Acá queda dicho en vez de depender de dónde está el ``continue``.

    El ``restante_antes`` que recibe ``fila`` es el residual **justo antes** de
    aplicar esta orden, no el total del pago: si un pago cubre tres órdenes, cada
    fila muestra cuánto le quedaba disponible en ese momento.
    """
    filas: list[dict[str, Any]] = []
    for orden in ordenes:
        if restante <= UMBRAL_CUBIERTO:
            break
        saldo = Decimal(str(orden["saldo_pendiente"]))
        if saldo <= UMBRAL_CUBIERTO:
            continue
        a_aplicar = min(restante, saldo)
        propuesta = fila(restante, orden, a_aplicar)
        if propuesta is None:
            # No se ofrece, así que no compromete nada. Ver el docstring.
            continue
        filas.append(propuesta)
        restante -= a_aplicar
        orden["saldo_pendiente"] = saldo - a_aplicar
    return filas, restante
