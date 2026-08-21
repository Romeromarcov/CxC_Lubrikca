"""Árbol de enrutamiento de CxC — lógica pura (sin I/O).

Determina, para una orden, si debe salir de "CxC activa" y a qué bandeja
se enruta, comparando lo pagado contra los 4 puntos de referencia de la
Matriz de Saldos Netos:

  Col 1: Teórico Neta Lista BS (referencia BCV)
  Col 2: Teórico Neta Lista USD (referencia Binance)
  Col 3: Venta Real (orden Odoo)
  Col 4: Factura Neta Real (Odoo, neta de NC/ND)

Regla de negocio (Manual del Proceso Administrativo, sección 5 -- con dos
correcciones del usuario, agosto 2026, sobre la primera versión de este
árbol):

  1. Si lo pagado cubre el Teórico USD (Col 2) → sale de CxC activa,
     rumbo a Bandeja de Facturación 2 (si ya facturada) o Bandeja de
     Facturación 1 (si no facturada).
  2. Si no cubre el Teórico USD pero sí cubre el Teórico BS (Col 1) →
     mismo destino que el caso 1 (misma regla facturada/no-facturada).
  3. Si la orden AÚN NO está facturada pero lo pagado ya cubre la Venta
     Real (Col 3, el monto real de la orden en Odoo, sin depender de
     ningún teórico) → sale de CxC activa, rumbo a Bandeja de
     Facturación 1. Corrección del usuario: después de emitida la
     factura, la segunda fuente de verdad es la propia orden real -- los
     teóricos existen para calcular descuentos y para auditoría, no para
     bloquear que una orden ya pagada al monto real pase a facturarse.
  4. Si no cubre NINGÚN teórico pero sí cubre la Factura Neta Real
     (Col 4, solo aplica si ya está facturada) → el pago es insuficiente
     contra el precio de lista, pero legalmente lo que vale es la
     factura: si el cliente ya la pagó, no hay mucho que reclamarle.
     Corrección del usuario: la orden SALE de CxC activa (ya está
     saldada) y ADEMÁS se enruta a la Bandeja de Auditoría de Precios,
     para revisar internamente por qué se facturó con un precio/lista
     por debajo del estándar autorizado -- la auditoría es un tema
     posterior de control interno, no una condición para cerrar la
     cobranza.
  5. Cualquier otro caso: permanece en CxC activa, sin enrutamiento
     especial.

Aclaratoria del usuario (agosto 2026) sobre las reglas 1/2: la lista con
la que NACIÓ la orden importa. Una orden nacida en Lista USD NO se puede
considerar pagada solo porque el Teórico BS esté pagado si el Teórico USD
(su referencia nativa) NO lo está -- exige el Teórico USD específicamente.
Una orden nacida en Lista VES (o en la ventana histórica) sí puede salir
con cualquiera de los dos teóricos pagado (el OR original, sin cambios).
Ver el parámetro `nacio_en_lista_usd`.

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
    nacio_en_lista_usd: bool = False,
    venta_real_pagada: bool = False,
    tolerance: Decimal = Decimal("0.05"),
) -> ClasificacionCxC:
    """Clasifica una orden según el árbol de enrutamiento de CxC.

    Los flags `*_pagado`/`*_pagada` deben venir ya calculados (ej. desde
    las columnas `estatus_pago_teorico_ves`/`_usd`/`estatus_pago_real_
    orden`/`_factura` de `/api/ventas`, colapsando su estado a booleano:
    True solo si el estado es "pagada" CONFIRMADO -- nunca una Vinculación
    PENDIENTE sin reconciliar en Odoo, ver Fase 0 del plan de arquitectura
    de pagos).

    `nacio_en_lista_usd`: True si la orden nació en una lista de precios
    USD (no VES, no ventana histórica). Cambia la evaluación de las
    reglas 1/2: para una orden USD, `teorico_usd_pagado` es la ÚNICA
    condición que exporta por teórico (BS pagado solo, sin USD pagado, NO
    alcanza -- ver aclaratoria en el docstring del módulo). Para una orden
    VES/histórica (`False`, default), se mantiene el OR original: cualquiera
    de los dos teóricos pagado es suficiente.

    `venta_real_pagada`: True si lo pagado cubre el monto real de la
    orden en Odoo (Col 3), independiente de cualquier teórico -- solo
    tiene efecto para órdenes AÚN NO facturadas (regla 3).

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

    if teorico_bs_pagado and not nacio_en_lista_usd:
        bandeja = BandejaDestino.FACTURACION_2 if facturada else BandejaDestino.FACTURACION_1
        return ClasificacionCxC(
            so_id=so_id,
            sale_de_cxc=True,
            bandeja_destino=bandeja,
            motivo="Pagado vs Teórico Lista BS (referencia BCV)",
        )

    if not facturada and venta_real_pagada:
        return ClasificacionCxC(
            so_id=so_id,
            sale_de_cxc=True,
            bandeja_destino=BandejaDestino.FACTURACION_1,
            motivo=(
                "Pagado vs Venta Real (orden Odoo), sin cubrir ningún "
                "teórico -- los teóricos son referencia de descuento/"
                "auditoría, no un requisito para facturar una orden ya "
                "pagada al monto real"
            ),
        )

    if facturada and factura_real_pagada:
        return ClasificacionCxC(
            so_id=so_id,
            sale_de_cxc=True,
            bandeja_destino=BandejaDestino.AUDITORIA_PRECIOS,
            motivo=(
                "Pagado vs Factura Neta Real en Odoo pero NO vs ningún "
                "teórico -- legalmente la factura ya está saldada, sale "
                "de CxC activa; se enruta ADEMÁS a Auditoría de Precios "
                "para revisar internamente por qué se facturó con un "
                "precio/lista por debajo del estándar autorizado"
            ),
        )

    if teorico_bs_pagado and nacio_en_lista_usd:
        return ClasificacionCxC(
            so_id=so_id,
            sale_de_cxc=False,
            bandeja_destino=None,
            motivo=(
                "Pagado vs Teórico Lista BS pero la orden nació en Lista "
                "USD -- no alcanza sin el Teórico USD (su referencia "
                "nativa) también pagado"
            ),
        )

    return ClasificacionCxC(
        so_id=so_id,
        sale_de_cxc=False,
        bandeja_destino=None,
        motivo="Sin pago suficiente contra ninguna referencia -- permanece en CxC activa",
    )
