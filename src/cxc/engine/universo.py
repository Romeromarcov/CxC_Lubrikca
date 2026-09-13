"""Qué órdenes entran a la cuenta por cobrar, y cuáles no.

Segunda pieza de la Fase 2.4 del plan de blindaje. Se eligió ésta porque es
**la definición del universo**: de acá dependen las dos primeras partidas del
balance de comprobación, el reporte de saldos, la bandeja de facturación, el
dashboard y el reporte diario. Que las seis páginas cuenten las mismas órdenes
es lo que hace que el balance signifique algo, y esa garantía descansa
enteramente en que las seis llamen a la misma función.

Es además una función **pura** de quince líneas, así que su lugar natural no es
un archivo de diecisiete mil.

**No cambia ningún comportamiento**: la lógica se movió tal cual de
``web/app.py``.
"""

from __future__ import annotations

from typing import Any

# Estados de ``sale.order`` que NUNCA deben entrar a un reporte, bandeja o
# cálculo de cobranza: cancelada (``cancel``) y cotización en cualquiera de sus
# dos sub-estados de Odoo (``draft`` / ``sent``). Regla global.
#
# Excepción de negocio: una orden CANCELADA cuya mercancía ya salió de almacén
# (un ``stock.picking`` saliente en estado ``done``) y el cliente no la devolvió
# sigue siendo una venta real -- Odoo permite cancelar una orden después del
# despacho, y eso no deshace la entrega. Esa excepción entra por el parámetro
# ``entrega_valida``.
#
# CORRECCIÓN (10-sep-2026). Acá decía: "hay 16 órdenes canceladas con entrega
# completa, por 11.995,68 USD. La excepción no es hipotética -- es la diferencia
# entre perseguir esa plata y no verla". **Eso estaba mal.** El chequeo
# ``orden_cancelada_con_entrega`` encuentra 16 filas, pero preguntándole a Odoo
# por cada una, las 16 tienen ``qty_delivered = 0``: no hay un dólar de mercancía
# en manos de clientes. Se leyó un conteo de filas como plata en riesgo sin
# verificar que la mercancía estuviera afuera.
#
# Lo que las 16 son, medido:
#
# * **12 salieron y volvieron** (3.460,27 USD de órdenes): salida ``done`` y
#   después devolución. Correcto, y sin nada que perseguir.
# * **4 nunca salieron** (8.535,41 USD): su única salida figura ``cancel`` en
#   Odoo Y en el propio espejo, pero ``ordenes_venta.entregada_completa`` dice
#   True. Son S00224, S00076, S00091 y S00329 -- las mismas que
#   ``web/app.py`` ya nombraba por un bug de picking interno leído como
#   devolución. Ahí el problema no es plata sin perseguir: es que el espejo se
#   contradice consigo mismo, y lo detecta el chequeo
#   ``entregada_pero_la_salida_esta_cancelada``.
#
# La excepción de abajo sigue siendo correcta como diseño -- una cancelada cuya
# mercancía salió y no volvió ES una venta -- pero en esta base **no dispara en
# ninguna orden**, porque en las 16 la mercancía no está afuera. Queda como
# defensa, no como algo que hoy rescate plata.
ESTADOS_ORDEN_EXCLUIDOS = frozenset({"cancel", "cancelled", "draft", "sent"})


def orden_excluida(
    orden: Any, live_state: str | None = None, entrega_valida: bool = False
) -> bool:
    """True si la orden debe excluirse de cualquier reporte, bandeja o cálculo.

    ``live_state`` es el estado que Odoo reporta **ahora**; cuando se pasa, gana
    sobre el ``estado_orden`` del espejo. La diferencia no es cosmética: el sync
    incremental mira una ventana de 48 horas por ``write_date``, así que una
    orden cancelada fuera de esa ventana se queda con el estado viejo en el
    espejo para siempre. Caso real verificado: S00162, por 161.679,06 USD,
    figuraba como ``sale`` en el espejo y estaba ``cancel`` en Odoo, inflando
    «Ventas del Año» en ese monto.

    ``entrega_valida`` es la excepción de negocio descrita en
    ``ESTADOS_ORDEN_EXCLUIDOS``: una orden cancelada cuya mercancía salió y no
    volvió sigue siendo una venta.
    """
    estado = (
        (
            live_state
            if live_state is not None
            else str(getattr(orden, "estado_orden", "sale") or "")
        )
        .strip()
        .lower()
    )
    if estado not in ESTADOS_ORDEN_EXCLUIDOS:
        return False
    return not (estado in ("cancel", "cancelled") and entrega_valida)
