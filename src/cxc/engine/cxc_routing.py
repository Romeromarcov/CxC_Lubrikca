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
  5. Si Odoo mismo ya considera la factura saldada (``payment_state``
     'paid'/'in_payment'/'reversed', EN VIVO) pero ninguna de las reglas
     1-4 llegó a esa conclusión por nuestra propia reconstrucción de lo
     pagado (hueco de sync, tasa mal congelada, una Vinculación que
     nunca se creó) → sale de CxC activa igual, sin auditoría de
     precios (a diferencia de la regla 4, aquí el desajuste es de nuestra
     reconstrucción, no de precio). Pedido explícito del usuario (agosto
     2026) tras confirmar en vivo 107 órdenes reales en este estado.
  6. Cualquier otro caso: permanece en CxC activa, sin enrutamiento
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

Estado "en proceso de pago" (agosto 2026, precedente citado por el
usuario -- Odoo mismo distingue 4 estados de pago de factura: sin pagar,
parcialmente pagado, EN PROCESO DE PAGO, pagado; "en proceso de pago"
significa que el cobro ya se aplicó y solo falta la conciliación
bancaria, y ESE estado ya saca la factura de Cuentas por Cobrar en Odoo,
aunque sea visiblemente distinto de "pagado" para quien lo mira).

Se replica la misma idea acá: una Vinculación PENDIENTE (vinculada a esta
orden vía FIFO o a mano, pero aún sin que Odoo la reconcilie) le da al
cliente el mismo "beneficio de la duda" que Odoo -- ya reportó el pago y
se aplicó a este pedido, falta solo la conciliación. Las 4 reglas de
arriba se re-evalúan una segunda vez con montos que INCLUYEN lo
PENDIENTE (parámetros `*_incl_pendiente`) SOLO si ninguna de las
versiones estrictas (CONCILIADO) alcanzó.

Corrección del usuario (agosto 2026) sobre el destino de esta segunda
pasada: CONCILIADO es estructuralmente IMPOSIBLE antes de que exista una
factura -- Odoo solo puede reconciliar un pago contra un documento real,
y no hay ningún documento que reconciliar hasta facturar. Si esta segunda
pasada mandara toda orden no facturada a una lista pasiva
(`EN_PROCESO_DE_PAGO`) igual que a una ya facturada, esa orden NUNCA
tendría un camino para que alguien se entere de que ya está lista para
facturar -- "en proceso de pago" es, en la práctica, la ÚNICA señal que
una orden sin facturar podrá disparar jamás. Por eso el destino se separa
según `facturada`:

  - **NO facturada**: la segunda pasada enruta a `FACTURACION_1` (acción
    real -- hay que facturarla), no a `EN_PROCESO_DE_PAGO`. Sigue
    marcada `confirmado=False` para que quede visible que el pago
    todavía no está reconciliado.
  - **YA facturada**: la segunda pasada enruta a `EN_PROCESO_DE_PAGO`
    (visibilidad, no acción) -- ahí SÍ existe un camino real a
    CONCILIADO (el resync automático de Odoo cada ciclo), así que no
    hace falta forzar ninguna acción, solo dejarlo visible mientras se
    resuelve solo.

CRÍTICO: esta re-evaluación NUNCA debe alimentarse de nada que no sea
estrictamente "vinculado a ESTA orden, aunque sin confirmar" (Vinculación
PENDIENTE) -- nunca de una adivinanza sin ningún vínculo. Y nunca debe
usarse para gates reales que no sean "sale de CxC activa" (descuentos,
saldo real, motor) -- esos siguen exigiendo CONCILIADO, sin excepción.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class ReferenciaCxC(StrEnum):
    """Contra qué referencia se dio la orden por pagada.

    Existe para que quien muestre el resultado no tenga que adivinarlo
    leyendo ``motivo``, que es prosa. Lo pidió el usuario para la Bandeja
    de Facturación: ahí el teórico que se muestra tiene que ser
    exactamente aquel por el cual la orden quedó pagada, no uno fijo.
    """

    TEORICO_USD = "teorico_usd"
    TEORICO_BS = "teorico_bs"
    VENTA_REAL = "venta_real"
    FACTURA_REAL = "factura_real"
    ODOO = "odoo"
    SUBTOTAL_SIN_IVA = "subtotal_sin_iva"
    NINGUNA = "ninguna"


class BandejaDestino(StrEnum):
    FACTURACION_1 = "facturacion_1"
    FACTURACION_2 = "facturacion_2"
    AUDITORIA_PRECIOS = "auditoria_precios"
    EN_PROCESO_DE_PAGO = "en_proceso_de_pago"


@dataclass
class ClasificacionCxC:
    """Resultado del enrutamiento para una orden."""

    so_id: str
    sale_de_cxc: bool
    bandeja_destino: BandejaDestino | None
    motivo: str
    # Cuál de las referencias alcanzó -- versión legible por código de lo
    # que ``motivo`` cuenta en prosa. Ver ``ReferenciaCxC``.
    referencia: ReferenciaCxC = ReferenciaCxC.NINGUNA
    # False cuando la salida de CxC se decidió por la versión "en proceso
    # de pago" (Vinculación PENDIENTE, aún sin reconciliar en Odoo) en vez
    # de por un pago realmente CONCILIADO. True en cualquier otro caso,
    # incluyendo cuando la orden NO sale de CxC.
    confirmado: bool = True


def clasificar_estado_cxc(
    so_id: str,
    facturada: bool,
    teorico_bs_pagado: bool,
    teorico_usd_pagado: bool,
    factura_real_pagada: bool,
    nacio_en_lista_usd: bool = False,
    venta_real_pagada: bool = False,
    teorico_bs_pagado_incl_pendiente: bool = False,
    teorico_usd_pagado_incl_pendiente: bool = False,
    venta_real_pagada_incl_pendiente: bool = False,
    factura_real_pagada_incl_pendiente: bool = False,
    factura_pagada_confirmada_odoo: bool = False,
    subtotal_pagado: bool = False,
    subtotal_pagado_incl_pendiente: bool = False,
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

    `*_incl_pendiente`: mismas 4 referencias, pero calculadas incluyendo
    una Vinculación PENDIENTE (vinculada a ESTA orden, aún sin
    reconciliar en Odoo) -- ver "Estado en proceso de pago" en el
    docstring del módulo. Solo se consultan si ninguna versión CONCILIADO
    alcanzó; si alguna de estas sí, la orden sale de CxC activa con
    `bandeja_destino=EN_PROCESO_DE_PAGO` y `confirmado=False`.

    `factura_pagada_confirmada_odoo`: True si Odoo mismo (``account.
    move.payment_state`` de TODAS las out_invoice de la orden, en vivo)
    ya considera la factura saldada -- 'paid'/'in_payment'/'reversed'.
    Pedido explícito del usuario (agosto 2026, auditoría de saldos de
    Cuentas por Cobrar): antes de este flag, la única forma de salir de
    CxC era reconstruir "pagado" de abajo hacia arriba a partir de
    nuestras propias Vinculaciones -- cualquier hueco en esa
    reconstrucción (sync incompleto, tasa mal congelada, un pago que
    nunca se vinculó localmente) dejaba la orden viéndose pendiente
    aunque Odoo, la fuente de verdad, ya la diera por saldada.
    Confirmado en vivo: 107 órdenes reales en ese estado. Esta señal es
    DIRECTA y autoritativa -- no requiere que ninguna otra columna
    calzada exactamente, así que se evalúa junto con las reglas 1-4
    (CONCILIADO), nunca junto a las `*_incl_pendiente` (esas siguen
    siendo exclusivamente sobre Vinculaciones PENDIENTE nuestras, un
    concepto distinto).

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
            referencia=ReferenciaCxC.TEORICO_USD,
        )

    if teorico_bs_pagado and not nacio_en_lista_usd:
        bandeja = BandejaDestino.FACTURACION_2 if facturada else BandejaDestino.FACTURACION_1
        return ClasificacionCxC(
            so_id=so_id,
            sale_de_cxc=True,
            bandeja_destino=bandeja,
            motivo="Pagado vs Teórico Lista BS (referencia BCV)",
            referencia=ReferenciaCxC.TEORICO_BS,
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
            referencia=ReferenciaCxC.VENTA_REAL,
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
            referencia=ReferenciaCxC.FACTURA_REAL,
        )

    if facturada and factura_pagada_confirmada_odoo:
        return ClasificacionCxC(
            so_id=so_id,
            sale_de_cxc=True,
            bandeja_destino=BandejaDestino.FACTURACION_2,
            motivo=(
                "Odoo mismo (payment_state de la factura) ya la da por "
                "saldada -- pagada/en proceso de pago/revertida -- "
                "aunque nuestra propia reconstrucción de lo pagado no "
                "haya llegado al mismo resultado (hueco de sync, tasa, o "
                "una Vinculación que nunca se creó). Se confía en Odoo "
                "como fuente de verdad directa, sin auditoría de precios "
                "-- a diferencia de la regla de Factura Neta Real, aquí "
                "el desajuste es de RECONSTRUCCIÓN nuestra, no de precio."
            ),
            referencia=ReferenciaCxC.ODOO,
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
            referencia=ReferenciaCxC.NINGUNA,
        )

    # "En proceso de pago" (segunda pasada, mismo criterio que las reglas
    # 1-4 pero incluyendo lo PENDIENTE) -- solo se llega aquí si NINGUNA
    # versión CONCILIADO alcanzó arriba. Mismo "beneficio de la duda" que
    # Odoo le da a una factura in_payment. Destino según `facturada` --
    # ver docstring del módulo: antes de facturar, CONCILIADO es
    # estructuralmente imposible, así que esta señal DEBE enrutar a
    # Facturación 1 (acción real) o la orden nunca tendría otro camino
    # para que alguien note que ya está lista.
    if teorico_usd_pagado_incl_pendiente:
        bandeja = (
            BandejaDestino.EN_PROCESO_DE_PAGO if facturada else BandejaDestino.FACTURACION_1
        )
        return ClasificacionCxC(
            so_id=so_id,
            sale_de_cxc=True,
            bandeja_destino=bandeja,
            motivo=(
                "Pagado vs Teórico Lista USD, pero vía una Vinculación "
                "aún sin reconciliar en Odoo -- equivalente a 'En "
                "proceso de pago' en Odoo"
            ),
            confirmado=False,
            referencia=ReferenciaCxC.TEORICO_USD,
        )

    if teorico_bs_pagado_incl_pendiente and not nacio_en_lista_usd:
        bandeja = (
            BandejaDestino.EN_PROCESO_DE_PAGO if facturada else BandejaDestino.FACTURACION_1
        )
        return ClasificacionCxC(
            so_id=so_id,
            sale_de_cxc=True,
            bandeja_destino=bandeja,
            motivo=(
                "Pagado vs Teórico Lista BS, pero vía una Vinculación "
                "aún sin reconciliar en Odoo -- equivalente a 'En "
                "proceso de pago' en Odoo"
            ),
            confirmado=False,
            referencia=ReferenciaCxC.TEORICO_BS,
        )

    if not facturada and venta_real_pagada_incl_pendiente:
        return ClasificacionCxC(
            so_id=so_id,
            sale_de_cxc=True,
            bandeja_destino=BandejaDestino.FACTURACION_1,
            motivo=(
                "Pagado vs Venta Real, pero vía una Vinculación aún sin "
                "reconciliar en Odoo -- equivalente a 'En proceso de "
                "pago' en Odoo, y sin facturar todavía esta es la única "
                "señal que existirá -- se envía a facturar igual"
            ),
            confirmado=False,
            referencia=ReferenciaCxC.VENTA_REAL,
        )

    if facturada and factura_real_pagada_incl_pendiente:
        return ClasificacionCxC(
            so_id=so_id,
            sale_de_cxc=True,
            bandeja_destino=BandejaDestino.EN_PROCESO_DE_PAGO,
            motivo=(
                "Pagado vs Factura Neta Real, pero vía una Vinculación "
                "aún sin reconciliar en Odoo -- equivalente a 'En "
                "proceso de pago' en Odoo"
            ),
            confirmado=False,
            referencia=ReferenciaCxC.FACTURA_REAL,
        )

    # Subtotal pagado, IVA no (decisión del usuario, septiembre 2026). Es
    # el único caso donde "sigue debiendo" y "hay que facturarla" NO son la
    # misma respuesta, y por eso la única rama que sale con
    # ``sale_de_cxc=False`` Y un destino de bandeja a la vez.
    #
    # El cliente pagó la mercancía y falta el IVA. No facturar lo empeora
    # por los dos lados:
    #
    #   - Si es agente de retención, ese IVA NUNCA se lo va a pagar a
    #     Lubrikca: lo entera al SENIAT. No es una cuenta por cobrar sino un
    #     comprobante por recibir, y sin factura no hay retención que hacer.
    #   - Si es un cliente normal, el IVA sí es deuda real, pero exigible
    #     solo DESPUÉS de facturar. Retener la factura garantiza que no se
    #     pague nunca, porque la obligación legal todavía no nació.
    #
    # Así que se enruta a facturar y se sigue cobrando: la orden aparece en
    # Bandeja 1 sin salir de CxC activa, y una vez facturada la recoge la
    # bandeja de IVA pendiente (que ya distingue agente de no-agente y
    # sigue el rastro hasta ``account.move.wh_iva``).
    #
    # Va al final a propósito: solo aplica cuando NINGUNA de las
    # referencias completas alcanzó. Una orden que cubre el total con IVA
    # sale por su rama normal y no llega hasta acá.
    if not facturada and (subtotal_pagado or subtotal_pagado_incl_pendiente):
        return ClasificacionCxC(
            so_id=so_id,
            sale_de_cxc=False,
            bandeja_destino=BandejaDestino.FACTURACION_1,
            motivo=(
                "Pagado el subtotal pero no el IVA -- se envía a facturar "
                "para que nazca la obligación legal del impuesto, y sigue "
                "en CxC activa porque ese IVA todavía se debe (por pagar o "
                "por retener)"
            ),
            confirmado=subtotal_pagado,
            referencia=ReferenciaCxC.SUBTOTAL_SIN_IVA,
        )

    return ClasificacionCxC(
        so_id=so_id,
        sale_de_cxc=False,
        bandeja_destino=None,
        motivo="Sin pago suficiente contra ninguna referencia -- permanece en CxC activa",
        referencia=ReferenciaCxC.NINGUNA,
    )
