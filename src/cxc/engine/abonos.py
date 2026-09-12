"""De dónde sale el abono de una orden en el reporte de saldos (Fase 2.4, pieza 35).

Hay dos fuentes: las vinculaciones CONCILIADAS del espejo, con sus equivalentes
congelados, y lo que Odoo dice que se pagó contra las facturas de la orden
(``_pagos_odoo_por_orden``). La regla que rige desde hace meses, y que estaba escrita de
una forma que no la dejaba ver:

    **Si la orden tiene alguna vinculación conciliada, manda la local. Si no tiene
    ninguna, entra la de Odoo, marcada ``desde_odoo``.**

Lo que no la dejaba ver: el bloque original tenía una rama «si la orden ya está en el
mapa, quedarse con el MÁXIMO entre lo local y lo de Odoo» que **nunca se ejecutaba** --
toda entrada local llevaba ``tiene_vinc_manual = True`` y el bucle hacía ``continue``
antes de llegar. La cobertura lo decía (doce líneas sin ejecutar en años de tests) y el
nombre del campo lo escondía: no significa «vinculación manual», significa «tiene
vinculación local». Una regla que dice «máximo» y aplica «local manda» es dos reglas, y
sólo una es cierta. Acá queda la cierta; el máximo no se conserva porque no existía.

Cambiar la regla (por ejemplo a «Odoo prevalece» también en el abono) mueve el saldo de
cada orden con vinculaciones, así que es decisión del usuario; esta pieza no la toma.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class AbonoDeOrden:
    abono_bcv: Decimal = Decimal("0")
    abono_binance: Decimal = Decimal("0")
    ultimo_abono: str | None = None
    desde_odoo: bool = False
    tiene_vinc_local: bool = False
    # Pagos de Odoo que no se pudieron valorar (sin tasa para su fecha).
    pagos_sin_tasa: int = 0

    def como_dict(self) -> dict[str, Any]:
        # ``tiene_vinc_manual`` es el nombre histórico del campo; los consumidores
        # del reporte lo leen así.
        return {
            "abono_bcv": self.abono_bcv,
            "abono_binance": self.abono_binance,
            "ultimo_abono": self.ultimo_abono,
            "desde_odoo": self.desde_odoo,
            "tiene_vinc_manual": self.tiene_vinc_local,
            "pagos_sin_tasa": self.pagos_sin_tasa,
        }


@dataclass
class FusionDeAbonos:
    por_orden: dict[str, AbonoDeOrden]
    # Órdenes con vinculación local para las que Odoo también reportaba abono:
    # la cifra de Odoo NO entró. Es la lista que haría falta mirar si algún día
    # se decide que Odoo prevalezca también acá.
    odoo_descartado: list[str] = field(default_factory=list)


def _sumar_ultimo(actual: str | None, nuevo: str | None) -> str | None:
    if not nuevo:
        return actual
    if not actual or nuevo > actual:
        return nuevo
    return actual


def fusionar_abonos(
    locales: dict[str, dict[str, Any]], odoo: dict[str, dict[str, Any]]
) -> FusionDeAbonos:
    """``locales`` y ``odoo`` vienen por ``so_id``; ver el módulo para la regla."""
    por_orden: dict[str, AbonoDeOrden] = {}
    for so_id, d in locales.items():
        por_orden[so_id] = AbonoDeOrden(
            abono_bcv=Decimal(str(d.get("abono_bcv", "0"))),
            abono_binance=Decimal(str(d.get("abono_binance", "0"))),
            ultimo_abono=d.get("ultimo_abono"),
            desde_odoo=False,
            tiene_vinc_local=True,
        )
    descartados: list[str] = []
    for so_id, p in odoo.items():
        if so_id in por_orden:
            descartados.append(so_id)
            continue
        por_orden[so_id] = AbonoDeOrden(
            abono_bcv=Decimal(str(p.get("abono_bcv", "0"))),
            abono_binance=Decimal(str(p.get("abono_binance", "0"))),
            ultimo_abono=_sumar_ultimo(None, p.get("ultimo_abono")),
            desde_odoo=True,
            tiene_vinc_local=False,
            pagos_sin_tasa=int(p.get("pagos_sin_tasa", 0) or 0),
        )
    return FusionDeAbonos(por_orden=por_orden, odoo_descartado=sorted(descartados))
