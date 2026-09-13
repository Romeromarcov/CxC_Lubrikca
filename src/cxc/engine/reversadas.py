"""Una factura anulada no es una factura cobrada.

Décima pieza de la Fase 2.4, y de las que existen por un defecto medido y no por
prolijidad.

**El defecto.** ``_pagos_odoo_por_orden`` tiene dos caminos para saber cuánto se
cobró de una orden. El principal pregunta por los ``account.payment``
reconciliados, que es el importe real. Cuando no hay ninguno, cae al fallback:

.. code-block:: python

    paid_inv = max(Decimal("0"), tot - res)   # amount_total - amount_residual

La resta es correcta para una factura que se está cobrando: lo que ya no se debe,
se cobró. Pero **una factura anulada tiene residual cero por definición** -- lo que
la dejó en cero fue la nota de crédito que la reversa, no un bolívar que entró. El
fallback lee la anulación como cobro completo.

No depende de ninguna tasa. El fallback deduce la tasa dividiendo el total de la
factura por el total de la orden, así que el abono fantasma sale exactamente igual
al monto de la orden en dólares, con la tasa que sea.

**Medido en la copia de producción (10-sep-2026).** 20 facturas ``out_invoice``
``posted`` con ``payment_state='reversed'`` y sin ningún ``account.payment``
reconciliado; 17 son órdenes reales (3 son del banco de pruebas, clientes ``ZZ
BLINDAJE``) y **las 17 salieron de la cuenta por cobrar**. De esas:

* 16 fueron refacturadas y tienen una segunda factura viva -- el ciclo se hizo
  bien, pero el abono fantasma de la anulada se le suma igual. **7 de ellas tienen
  residual real sin cobrar, 2.628,64 USD**, y ninguno se explica por retención de
  IVA.
* 1 no tiene ninguna factura viva: **S00573, 1.860,48 USD**, con sus 29 unidades
  entregadas y no devueltas. Es el ítem que el plan lista como "refacturar".

El caso más limpio es **S00886**: factura 00000677 anulada, refacturada como
00000701 en estado ``not_paid`` con el residual **completo** (596.091,85 VES) y
**cero pagos reconciliados**. La resta da (581.034,93 − 0) + (596.091,85 −
596.091,85) = 581.034,93, o sea el total de la orden. Nadie pagó nada y el reporte
la clasifica "Pagado vs Teórico Lista USD".

**Esto no corrige nada.** Aplicar la distinción devolvería 17 órdenes a la cuenta
por cobrar, y eso mueve montos: decisión del usuario, no efecto colateral de una
extracción (regla de la Fase 1). Lo que hay acá es el instrumento que mide el
hueco y los tests que fijan su forma, para que la decisión se tome sobre un número
y no sobre una impresión.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# ``payment_state`` que Odoo le pone a una factura que una nota de crédito reversó.
# Su ``amount_residual`` es cero y no significa que se haya cobrado.
ESTADOS_ANULADA = frozenset({"reversed"})


def _dec(valor: Any) -> Decimal:
    """Decimal tolerante: lo que no se puede leer como número vale cero.

    Los campos llegan de XML-RPC, donde un campo ausente es ``False`` y no
    ``None``, así que la conversión directa revienta con el valor equivocado.
    """
    if valor is None or valor is False or valor == "":
        return Decimal("0")
    try:
        return Decimal(str(valor))
    except (TypeError, ValueError, ArithmeticError):
        return Decimal("0")


@dataclass(frozen=True)
class LecturaDeAbono:
    """Las dos lecturas del mismo par ``(amount_total, amount_residual)``.

    ``segun_la_resta`` es lo que el código hace hoy; ``sin_anuladas`` es lo mismo
    salvo que una factura anulada aporta cero. ``fantasma`` es la diferencia, o
    sea el abono que el reporte se inventa por esta factura.
    """

    segun_la_resta: Decimal
    sin_anuladas: Decimal
    anulada: bool
    fantasma: Decimal


def abono_implicito(
    amount_total: Any, amount_residual: Any, payment_state: Any = ""
) -> LecturaDeAbono:
    """Cuánto dice esta factura que se cobró, leída de las dos maneras.

    El ``max(0, ...)`` se preserva a propósito: una factura con residual mayor que
    su total (pasa con notas de débito mal encadenadas) daría un abono negativo, y
    un abono negativo aumentaría la deuda en vez de dejarla quieta.
    """
    tot = _dec(amount_total)
    res = _dec(amount_residual)
    resta = max(Decimal("0"), tot - res)
    anulada = str(payment_state or "").strip().lower() in ESTADOS_ANULADA
    sin_anuladas = Decimal("0") if anulada else resta
    return LecturaDeAbono(
        segun_la_resta=resta,
        sin_anuladas=sin_anuladas,
        anulada=anulada,
        fantasma=resta - sin_anuladas,
    )


@dataclass(frozen=True)
class DiagnosticoReversadas:
    """Lo que las facturas de UNA orden dicen sobre su cobro, por los dos caminos.

    ``residual_vivo`` es la plata que sigue sin cobrarse según las facturas **no**
    anuladas. Es el número que importa para decidir: si hay residual vivo y el
    abono fantasma alcanza para tapar el total de la orden, la orden sale de la
    cuenta por cobrar debiendo plata.
    """

    numeros_anulados: tuple[str, ...]
    numeros_vivos: tuple[str, ...]
    abono_segun_la_resta: Decimal
    abono_sin_anuladas: Decimal
    fantasma: Decimal
    residual_vivo: Decimal
    nota: str

    @property
    def hay_fantasma(self) -> bool:
        return self.fantasma > Decimal("0")

    @property
    def sin_factura_viva(self) -> bool:
        """Nada que cobrar porque no quedó ninguna factura: hay que refacturar."""
        return not self.numeros_vivos


def diagnostico_de_reversadas(facturas: list[dict[str, Any]]) -> DiagnosticoReversadas:
    """Agrupa las facturas ``out_invoice`` de una orden y dice qué esconde la resta.

    ``facturas`` son dicts como los que ``_pagos_odoo_por_orden`` ya recorre:
    ``name``, ``amount_total``, ``amount_residual``, ``payment_state``. Las notas
    de crédito **no** entran acá -- el fallback tampoco las mira, porque itera
    ``invoices_by_so``, que sólo tiene ``out_invoice``.
    """
    anulados: list[str] = []
    vivos: list[str] = []
    resta = Decimal("0")
    limpio = Decimal("0")
    residual_vivo = Decimal("0")
    for f in facturas:
        lec = abono_implicito(
            f.get("amount_total"), f.get("amount_residual"), f.get("payment_state")
        )
        resta += lec.segun_la_resta
        limpio += lec.sin_anuladas
        numero = str(f.get("name") or f.get("numero") or "?")
        if lec.anulada:
            anulados.append(numero)
        else:
            vivos.append(numero)
            residual_vivo += _dec(f.get("amount_residual"))
    fantasma = resta - limpio

    if not facturas:
        nota = "La orden no tiene ninguna factura out_invoice; no hay nada que leer."
    elif not anulados:
        nota = f"Ninguna factura anulada: las dos lecturas coinciden en {resta}."
    elif not vivos:
        nota = (
            f"Las {len(anulados)} factura(s) están anuladas y no quedó ninguna viva: "
            f"la resta acredita {fantasma} que nadie pagó, y la orden no tiene con qué "
            f"cobrarse. HAY QUE REFACTURAR."
        )
    else:
        nota = (
            f"{len(anulados)} anulada(s) y {len(vivos)} viva(s): la resta acredita "
            f"{fantasma} que nadie pagó, encima de un residual vivo de {residual_vivo} "
            f"que sí se debe."
        )
    return DiagnosticoReversadas(
        numeros_anulados=tuple(anulados),
        numeros_vivos=tuple(vivos),
        abono_segun_la_resta=resta,
        abono_sin_anuladas=limpio,
        fantasma=fantasma,
        residual_vivo=residual_vivo,
        nota=nota,
    )
