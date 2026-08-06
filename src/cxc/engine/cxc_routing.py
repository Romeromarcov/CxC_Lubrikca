"""Árbol de enrutamiento de CxC — lógica pura (sin I/O).

Determina, para una orden, si debe salir de "CxC activa" y a qué bandeja
se enruta, comparando lo pagado contra los 4 puntos de referencia de la
Matriz de Saldos Netos:

  Col 1: Teórico Neta Lista BS (referencia BCV)
  Col 2: Teórico Neta Lista USD (referencia Binance)
  Col 3: Venta Real (orden Odoo)
  Col 4: Factura Neta Real (Odoo, neta de NC/ND)

Regla de negocio (Manual del Proceso Administrativo, sección 5):

  1. Si lo pagado cubre el Teórico USD (Col 2) → sale de CxC activa,
     rumbo a Bandeja de Facturación 2 (si ya facturada) o Bandeja de
     Facturación 1 (si no facturada).
  2. Si no cubre el Teórico USD pero sí cubre el Teórico BS (Col 1) →
     mismo destino que el caso 1 (misma regla facturada/no-facturada).
  3. Si no cubre NINGÚN teórico pero sí cubre la Factura Neta Real
     (Col 4, solo aplica si ya está facturada) → el pago es insuficiente
     contra el precio de lista pero Odoo ya lo dio por pagado: sospecha
     de facturación con precio/lista por debajo del estándar autorizado.
     La orden PERMANECE en CxC activa y además se enruta a la nueva
     Bandeja de Auditoría de Precios.
  4. Cualquier otro caso: permanece en CxC activa, sin enrutamiento
     especial.

Nota: la comparación es binaria (pagada / no pagada) contra cada
columna con una tolerancia; no se involucra aquí el concepto de "parcial"
— eso es responsabilidad de las columnas de estatus de pago ya existentes
en `/api/ventas`. Este árbol solo decide el destino final de la orden.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class BandejaDestino(StrEnum):
    FACTURACION_1 = "facturacion_1"
    FACTURACION_2 = "facturacion_2"
    AUDITORIA_PRECIOS = "auditoria_precios"


@dataclass
class ClasificacionCxC:
    """Resultado del enrutamiento para una orden."""

    so_id: str
    sale_de_cxc: bool
    bandeja_destino: BandejaDestino | None
    motivo: str


def clasificar_estado_cxc(
    so_id: str,
    facturada: bool,
    teorico_bs_pagado: bool,
    teorico_usd_pagado: bool,
    factura_real_pagada: bool,
    tolerance: Decimal = Decimal("0.05"),
) -> ClasificacionCxC:
    """Clasifica una orden según el árbol de enrutamiento de CxC.

    Los tres flags `*_pagado`/`*_pagada` deben venir ya calculados
    (ej. desde las columnas `estatus_pago_teorico_ves`/`_usd`/
    `estatus_pago_real_factura` de `/api/ventas`, colapsando su estado
    a booleano: True solo si el estado es "pagada").

    `tolerance` se documenta pero no se usa dentro de esta función —
    la tolerancia ya debe haberse aplicado al calcular los flags de
    entrada (misma epsilon 0.05 que usan las columnas de estatus de
    pago existentes). Se recibe como parámetro para dejar explícito el
    contrato y facilitar tests que quieran variarla en el futuro.
    """
    if teorico_usd_pagado:
        bandeja = BandejaDestino.FACTURACION_2 if facturada else BandejaDestino.FACTURACION_1
        return ClasificacionCxC(
            so_id=so_id,
            sale_de_cxc=True,
            bandeja_destino=bandeja,
            motivo="Pagado vs Teórico Lista USD (referencia Binance)",
        )

    if teorico_bs_pagado:
        bandeja = BandejaDestino.FACTURACION_2 if facturada else BandejaDestino.FACTURACION_1
        return ClasificacionCxC(
            so_id=so_id,
            sale_de_cxc=True,
            bandeja_destino=bandeja,
            motivo="Pagado vs Teórico Lista BS (referencia BCV)",
        )

    if facturada and factura_real_pagada:
        return ClasificacionCxC(
            so_id=so_id,
            sale_de_cxc=False,
            bandeja_destino=BandejaDestino.AUDITORIA_PRECIOS,
            motivo=(
                "Pagado vs Factura Neta Real en Odoo pero NO vs ningún "
                "teórico -- posible facturación con precio/lista por "
                "debajo del estándar autorizado"
            ),
        )

    return ClasificacionCxC(
        so_id=so_id,
        sale_de_cxc=False,
        bandeja_destino=None,
        motivo="Sin pago suficiente contra ninguna referencia -- permanece en CxC activa",
    )
