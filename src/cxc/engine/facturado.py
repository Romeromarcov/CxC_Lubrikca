"""Qué le queda facturado a una orden, y qué le falta.

Undécima pieza de la Fase 2.4, y la elegí por el diagnóstico y no por una
medición de tamaño: las tres salidas de este bloque son exactamente las que usé
para dictaminar el caso S00372 del plan, y ninguna tenía una prueba propia. Los
e2e las ejercitan a través del endpoint, que es otra cosa -- prueban que la
pantalla muestre algo coherente, no que la regla sea la regla.

**Las cuatro reglas, y por qué ninguna es obvia.**

1. **Lo facturado neto suma las notas de débito y resta las de crédito.** Las
   dos se aplican sobre el total **con** impuestos, porque ya son documentos con
   su propio IVA.

2. **La retención de IVA baja el saldo objetivo por el IVA completo.** No por el
   porcentaje retenido: el documento puede retener del 0 al 100 %, y asumir una
   cifra fija sería inventar. Una vez confirmada la retención, el cliente ya no
   debe ese IVA *en efectivo* -- lo cierra el comprobante. Sin esta regla, una
   orden con retención se ve «parcialmente pagada» para siempre.

   Confirmado contra un caso real: S00372 tiene factura por 644,12 USD con
   retención aplicada, el cliente pagó 555,29, y el residual de 45.193,11 VES
   que Odoo sigue mostrando **es** el IVA retenido. 644,12 / 1,16 = 555,28, que
   calza con lo pagado al centavo. Comparar el pago contra el total sin este
   ajuste diría que debe 88,84 USD que nadie va a cobrar en efectivo.

3. **Falta la nota de crédito** cuando la orden está facturada, hubo devolución
   (o se canceló con la mercancía afuera) y **no** se aplicó ninguna NC. Sólo
   aplica a órdenes ya facturadas: antes de facturar la corrección pasa por las
   líneas reales de Odoo y nunca por una NC.

4. **«Tiene factura» mira el bruto, no el neto.** Una orden facturada y luego
   acreditada por completo tiene neto cero y **sigue teniendo factura**. Usar el
   neto la haría parecer nunca facturada, que es justo el caso donde más
   importa saber que sí lo estuvo.

Ojo con el orden entre la 2 y la 3: el ajuste por retención se aplica **después**
de decidir si falta la NC. Si se hiciera antes, una orden cuya NC dejó el neto en
cero cambiaría de veredicto según cuánto IVA se le hubiera retenido, que no tiene
nada que ver con si el documento existe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Umbral por debajo del cual un monto es cero contable. Es el mismo que usaba el
# cuerpo original en sus tres comparaciones, y se preserva: con montos que vienen
# de sumar decenas de líneas convertidas, un cero exacto casi nunca lo es.
CERO = 0.005


def _num(valor: Any) -> float:
    """Lo que no se puede leer como número vale cero.

    Los mapas de entrada salen de ``.get(so_id, 0.0)``, así que una orden sin
    facturas llega como cero legítimo; esto cubre el otro caso, un valor
    presente pero ilegible.
    """
    if valor is None or valor is False or valor == "":
        return 0.0
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class Facturado:
    """Lo que una orden tiene facturado, después de notas y retención.

    ``neto`` es la cifra contra la que se mide si la factura está saldada, así
    que es un camino de dinero: si sale de más, la orden se ve impaga cuando no
    lo está; si sale de menos, se da por cobrada antes de tiempo.
    """

    neto: float
    bruto_con_impuestos: float
    bruto_sin_impuestos: float
    nc_aplicada: float
    nd_aplicada: float
    iva_retenido_confirmado: float
    tiene_factura: bool
    falta_nc_por_devolucion: bool


def facturado_de_orden(
    *,
    facturado_sin_impuestos: Any,
    facturado_con_impuestos: Any,
    nc_aplicada: Any,
    nd_aplicada: Any,
    iva_rate: float,
    wh_iva_aplicado: bool = False,
    facturada: bool = False,
    tiene_devolucion: bool = False,
    cancelada_sin_devolver: bool = False,
) -> Facturado:
    """Las cuatro reglas de arriba, en el orden en que el cuerpo original las aplica.

    ``cancelada_sin_devolver`` es la orden cancelada en Odoo cuya mercancía salió
    y no volvió: cuenta como devolución pendiente de formalizar igual que una
    devolución explícita, porque el efecto es el mismo -- hay una factura viva
    por mercancía que el cliente no se quedó.
    """
    bruto_con = _num(facturado_con_impuestos)
    bruto_sin = _num(facturado_sin_impuestos)
    nc = _num(nc_aplicada)
    nd = _num(nd_aplicada)

    neto = bruto_con - nc + nd

    # Regla 3 ANTES de la 2: ver la nota del docstring del módulo.
    falta_nc = (
        bool(facturada) and (bool(tiene_devolucion) or bool(cancelada_sin_devolver)) and nc <= CERO
    )

    retenido = 0.0
    if wh_iva_aplicado and neto > CERO:
        retenido = neto - (neto / (1 + iva_rate))
        neto = max(0.0, neto - retenido)

    return Facturado(
        neto=neto,
        bruto_con_impuestos=bruto_con,
        bruto_sin_impuestos=bruto_sin,
        nc_aplicada=nc,
        nd_aplicada=nd,
        iva_retenido_confirmado=retenido,
        # Regla 4: el bruto, no el neto.
        tiene_factura=bruto_con > CERO,
        falta_nc_por_devolucion=falta_nc,
    )
