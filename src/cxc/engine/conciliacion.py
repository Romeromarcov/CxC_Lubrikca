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
