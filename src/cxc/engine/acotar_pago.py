"""Un pago no puede aplicar más de lo que vale, y hay dos maneras de repartirlo.

Décimotercera pieza de la Fase 2.4, y existe para que una decisión del usuario se
tome sobre los dos resultados y no sobre una descripción de los dos métodos.

**El defecto.** Las aplicaciones de un pago se leen de los ``account.partial.reconcile``
de Odoo, tomando ``credit_amount_currency`` de cada uno. Medido el 11-sep-2026 sobre
la copia de producción: **diez pagos, todos en dólares, cuyas aplicaciones suman más
que el pago — 1.269,25 USD de exceso**. El peor es el pago 200: vale 134,00 y tiene
aplicados 715,04 repartidos en nueve parciales, contra cuatro órdenes distintas.

**Por qué pasa.** Los parciales posteriores al primero son **revaluación cambiaria**.
Las tasas implícitas del pago 200 van de 4,1 a 709,7 dentro del mismo pago, cuando la
oficial de esa fecha es 483,9. El docstring del lector dice que los asientos de
diferencial cambiario «quedan fuera por construcción» porque no tienen un
``account.payment`` detrás — pero los nueve parciales del pago 200 están todos contra
facturas de cliente reales, así que el filtro no los excluye.

**La cota no se discute: la suma no puede superar el pago.** Lo que sí se discute es
cómo repartirla, y las dos maneras dan resultados muy distintos:

``POR_ORDEN``
    Se recorren los parciales en el orden en que Odoo los devuelve y se corta al
    agotar el pago. En **7 de los 10** casos el primer parcial ya **es** el pago
    completo, así que el pago va entero a esa orden y las demás reciben cero.

``PROPORCIONAL``
    Cada orden recibe la fracción que le toca según el peso de su parcial. Todas
    reciben algo, todas reciben menos.

La diferencia no es cosmética: con ``POR_ORDEN`` varias órdenes dejan de recibir
crédito del todo; con ``PROPORCIONAL`` todas lo ven bajar.

**Esto no aplica nada.** Devuelve los dos repartos para que se puedan comparar sobre
los datos reales. Elegir mueve el crédito de veinte órdenes, y eso es una decisión
del usuario (regla de la Fase 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

# Cuánto puede exceder la suma y seguir siendo redondeo. La misma que usan el
# detector y la novena invariante, para que los tres no puedan discrepar sobre el
# mismo pago.
TOLERANCIA = Decimal("0.05")

POR_ORDEN = "por_orden"
PROPORCIONAL = "proporcional"


def _dec(valor: Any) -> Decimal:
    if valor is None or valor is False or valor == "":
        return Decimal("0")
    try:
        return Decimal(str(valor))
    except (TypeError, ValueError, InvalidOperation):
        return Decimal("0")


@dataclass(frozen=True)
class Aplicacion:
    """Lo que un parcial dice que se aplicó a una orden."""

    so_id: str
    monto: Decimal


def repartir_por_orden(aplicaciones: list[Aplicacion], tope: Decimal) -> list[Aplicacion]:
    """Reparte hasta agotar el tope, en el orden recibido.

    El orden lo decide quien llama -- acá es el que Odoo devuelve, que es el
    cronológico de la conciliación. Una orden que queda fuera recibe **cero**, y
    no aparece con monto cero: lo que no se aplicó no existe como aplicación.
    """
    restante = tope
    salida: list[Aplicacion] = []
    for a in aplicaciones:
        if restante <= Decimal("0"):
            break
        cabe = min(a.monto, restante)
        if cabe <= Decimal("0"):
            continue
        salida.append(Aplicacion(a.so_id, cabe))
        restante -= cabe
    return salida


def repartir_proporcional(aplicaciones: list[Aplicacion], tope: Decimal) -> list[Aplicacion]:
    """Reparte el tope segun el peso de cada aplicacion.

    El ultimo se lleva el resto para que la suma cierre EXACTA contra el tope: con
    tres partes iguales de un tercio, redondear cada una por separado deja un
    centavo suelto, y un centavo suelto en un camino de dinero es una divergencia
    que despues alguien persigue.
    """
    total = sum((a.monto for a in aplicaciones), Decimal("0"))
    if total <= Decimal("0"):
        return []
    salida: list[Aplicacion] = []
    acumulado = Decimal("0")
    for i, a in enumerate(aplicaciones):
        if i == len(aplicaciones) - 1:
            parte = tope - acumulado
        else:
            parte = (a.monto / total * tope).quantize(Decimal("0.01"))
            acumulado += parte
        if parte > Decimal("0"):
            salida.append(Aplicacion(a.so_id, parte))
    return salida


def _por_orden_de_venta(aplicaciones: list[Aplicacion]) -> dict[str, Decimal]:
    total: dict[str, Decimal] = {}
    for a in aplicaciones:
        total[a.so_id] = total.get(a.so_id, Decimal("0")) + a.monto
    return total


@dataclass(frozen=True)
class DiagnosticoDeReparto:
    """Los dos repartos de un mismo pago, para poder compararlos.

    ``pierden_todo`` es la cifra que separa las dos opciones en la practica: son
    las ordenes que con ``POR_ORDEN`` dejan de recibir cualquier credito y con
    ``PROPORCIONAL`` siguen recibiendo algo.
    """

    pago_id: str
    monto_del_pago: Decimal
    aplicado_hoy: Decimal
    exceso: Decimal
    sobreaplicado: bool
    por_orden: dict[str, Decimal]
    proporcional: dict[str, Decimal]
    pierden_todo: tuple[str, ...]
    nota: str


def diagnostico_de_reparto(
    pago_id: str, aplicaciones: list[Aplicacion], monto_del_pago: Any
) -> DiagnosticoDeReparto:
    """Compara los dos repartos sobre las aplicaciones de UN pago.

    Si el pago no esta sobreaplicado, los dos repartos son el reparto actual y no
    hay nada que decidir -- se dice asi, en vez de mostrar dos columnas iguales
    como si hubiera una eleccion.
    """
    tope = _dec(monto_del_pago)
    hoy = sum((a.monto for a in aplicaciones), Decimal("0"))
    exceso = hoy - tope
    sobreaplicado = tope > Decimal("0") and exceso > TOLERANCIA

    if not sobreaplicado:
        actual = _por_orden_de_venta(aplicaciones)
        return DiagnosticoDeReparto(
            pago_id=pago_id,
            monto_del_pago=tope,
            aplicado_hoy=hoy,
            exceso=exceso,
            sobreaplicado=False,
            por_orden=actual,
            proporcional=actual,
            pierden_todo=(),
            nota=(
                f"El pago {pago_id} no esta sobreaplicado: aplica {hoy} de {tope}. "
                "Los dos repartos dan lo mismo, no hay nada que elegir."
            ),
        )

    a_orden = _por_orden_de_venta(repartir_por_orden(aplicaciones, tope))
    a_prop = _por_orden_de_venta(repartir_proporcional(aplicaciones, tope))
    pierden = tuple(
        sorted(so for so in a_prop if a_orden.get(so, Decimal("0")) <= Decimal("0"))
    )
    return DiagnosticoDeReparto(
        pago_id=pago_id,
        monto_del_pago=tope,
        aplicado_hoy=hoy,
        exceso=exceso,
        sobreaplicado=True,
        por_orden=a_orden,
        proporcional=a_prop,
        pierden_todo=pierden,
        nota=(
            f"El pago {pago_id} vale {tope} y tiene aplicados {hoy}: {exceso} de "
            f"exceso. Por orden, {len(a_orden)} orden(es) reciben algo y "
            f"{len(pierden)} pierden todo el credito. Proporcional, las "
            f"{len(a_prop)} reciben menos."
        ),
    )
