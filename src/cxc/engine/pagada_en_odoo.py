"""Cuándo Odoo dice que una orden ya está pagada, y las DOS respuestas que hay.

Duodécima pieza de la Fase 2.4, y como las dos anteriores la eligió un diagnóstico
y no una medición de tamaño: ``so_pagada_en_odoo`` está escrito tres veces en
``app.py``, y **una de las tres es una regla distinta**.

| dónde | regla |
|---|---|
| reporte de saldos | ``all(payment_state in ("paid", "in_payment"))`` |
| auditoría | igual, y su comentario dice «misma regla que /api/reporte-saldos» |
| sugerencias de conciliación | ``sum(amount_residual_usd) <= 0,05`` |

La tercera no es la misma regla, y el nombre compartido lo esconde. Decide si una
orden sale de la cuenta por cobrar, así que es un camino de dinero.

**Medido sobre la copia de producción (11-sep-2026): 66 de 796 órdenes con factura
discrepan**, todas en la misma dirección — la regla por residual las da por pagadas
y la de estado no. Y se parten en tres causas que apuntan a lados **distintos**:

1. **15 con alguna factura anulada.** Una factura ``reversed`` tiene residual cero
   porque la reversó una nota de crédito, no porque entró un bolívar. Acá la regla
   por residual está **mal**: es el mismo defecto del abono fantasma, en otro lugar.
2. **47 ``partial`` con residual de centavos** (0,01 a 0,03). Acá la regla por
   residual es la **razonable**: un centavo no es una deuda. La de estado deja la
   orden en la cuenta por cobrar para siempre por tres centavos.
3. **4 con residual NEGATIVO** — Odoo dice ``partial`` y el residual es −116,69,
   −56,63, −46,44 y −38,98: **258,74 USD de sobrepago**. Ninguna de las dos reglas
   dice «hay saldo a favor»; una lo llama pagado y la otra impago, y las dos se
   pierden la plata que hay que devolver o acreditar.

**Esto no unifica nada.** Elegir una de las dos mueve el universo de órdenes de
tres pantallas, y la medición dice que ninguna es correcta en los tres casos: la
respuesta buena probablemente sea por estado **más** una tolerancia de centavos
**más** una señal de sobrepago. Eso es una decisión del usuario (regla de la Fase
1). Lo que hay acá son las dos lecturas, el diagnóstico que las compara y la causa
de cada divergencia.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

# Los ``payment_state`` que la regla por estado acepta como pagado. ``in_payment``
# entra porque Odoo lo usa mientras el asiento de cobro se concilia: la plata ya
# entró aunque el documento todavía no cierre.
ESTADOS_PAGADOS = frozenset({"paid", "in_payment"})

# El ``payment_state`` de una factura que una nota de crédito reversó. Su residual
# es cero y **no** significa que se haya cobrado -- ver ``engine/reversadas.py``.
ESTADO_ANULADA = "reversed"

# Cuánto residual puede quedar y seguir contando como pagado por la regla del
# residual. Es el valor que usa ``_get_conciliaciones_sugerencias_sync``, y se
# preserva: cambiarlo mueve el universo de órdenes de esa pantalla.
TOLERANCIA_RESIDUAL = Decimal("0.05")


def _dec(valor: Any) -> Decimal:
    """Decimal tolerante. XML-RPC manda ``False`` por un campo vacío, no ``None``."""
    if valor is None or valor is False or valor == "":
        return Decimal("0")
    try:
        return Decimal(str(valor))
    except (TypeError, ValueError, InvalidOperation):
        return Decimal("0")


def pagada_por_estado(facturas: list[dict[str, Any]]) -> bool:
    """La regla del reporte de saldos y de la auditoría.

    **Todas** las facturas tienen que estar pagadas; si queda una sin pagar, la
    orden sigue visible. Sin facturas devuelve ``False``: una orden sin factura no
    está pagada, está sin facturar, y confundirlas la sacaría de la cuenta por
    cobrar sin que nadie haya cobrado nada.
    """
    estados = [str(f.get("payment_state") or "").strip().lower() for f in facturas]
    return bool(estados) and all(e in ESTADOS_PAGADOS for e in estados)


def pagada_por_residual(
    facturas: list[dict[str, Any]], *, tolerancia: Decimal = TOLERANCIA_RESIDUAL
) -> bool:
    """La regla de las sugerencias de conciliación.

    Suma ``amount_residual_usd`` de todas las facturas y pregunta si lo que queda
    cabe en la tolerancia. Sin facturas la suma es cero, que **cae dentro** de la
    tolerancia -- o sea que esta regla dice «pagada» sobre una orden sin facturar.
    Es una diferencia de fondo con la otra y está acá a propósito, no corregida:
    corregirla cambia qué órdenes ve esa pantalla.
    """
    return sum((_dec(f.get("amount_residual_usd")) for f in facturas), Decimal("0")) <= tolerancia


@dataclass(frozen=True)
class DiagnosticoPagada:
    """Las dos lecturas de la misma orden, y por qué difieren cuando difieren.

    ``causa`` es lo que hace este diagnóstico útil para decidir: las tres causas
    medidas apuntan a lados distintos sobre cuál regla es la correcta, así que un
    conteo de divergencias sin la causa no alcanza para elegir.
    """

    por_estado: bool
    por_residual: bool
    residual_total: Decimal
    estados: tuple[str, ...]
    causa: str
    nota: str

    @property
    def coinciden(self) -> bool:
        return self.por_estado == self.por_residual

    @property
    def hay_sobrepago(self) -> bool:
        """Residual negativo: se cobró más de lo facturado.

        Ninguna de las dos reglas lo reporta -- una lo llama pagado y la otra
        impago, y las dos se pierden la plata que hay que devolver o acreditar.
        """
        return self.residual_total < Decimal("0")


def diagnostico_de_pagada(
    facturas: list[dict[str, Any]], *, tolerancia: Decimal = TOLERANCIA_RESIDUAL
) -> DiagnosticoPagada:
    """Compara las dos reglas sobre las facturas de UNA orden y nombra la causa.

    ``facturas`` son dicts como los que ``app.py`` ya arma: ``payment_state`` y
    ``amount_residual_usd``. Solo ``out_invoice`` -- las notas de crédito no
    entran, igual que en los tres sitios originales.
    """
    estados = tuple(str(f.get("payment_state") or "").strip().lower() for f in facturas)
    residual = sum((_dec(f.get("amount_residual_usd")) for f in facturas), Decimal("0"))
    por_estado = pagada_por_estado(facturas)
    por_residual = pagada_por_residual(facturas, tolerancia=tolerancia)

    if not facturas:
        causa = "sin_facturas"
        nota = (
            "La orden no tiene ninguna factura. La regla por estado dice NO pagada "
            "(correcto: está sin facturar) y la del residual dice pagada, porque una "
            "suma vacía cabe en la tolerancia."
        )
    elif por_estado == por_residual:
        causa = "coinciden"
        nota = f"Las dos reglas dicen {'pagada' if por_estado else 'no pagada'}."
    elif ESTADO_ANULADA in estados:
        causa = "factura_anulada"
        nota = (
            "Hay una factura ANULADA: su residual es cero porque la reversó una nota "
            "de crédito, no porque entró un bolívar. Acá la regla del residual está "
            "MAL -- es el mismo defecto del abono fantasma."
        )
    elif Decimal("0") <= residual <= tolerancia:
        causa = "residual_de_centavos"
        nota = (
            f"Residual de {residual} sin llegar a la tolerancia. Acá la regla del "
            "residual es la razonable: un centavo no es una deuda, y la de estado "
            "deja la orden en la cuenta por cobrar para siempre."
        )
    elif residual < Decimal("0"):
        causa = "sobrepago"
        nota = (
            f"Residual NEGATIVO de {residual}: se cobró más de lo facturado. Ninguna "
            "de las dos reglas dice 'hay saldo a favor' -- una lo llama pagado y la "
            "otra impago, y las dos se pierden la plata a devolver."
        )
    else:
        causa = "otra"
        nota = (
            f"Las dos reglas difieren y la causa no es ninguna de las tres medidas "
            f"(estados {estados}, residual {residual}). Vale mirarla a mano."
        )

    return DiagnosticoPagada(
        por_estado=por_estado,
        por_residual=por_residual,
        residual_total=residual,
        estados=estados,
        causa=causa,
        nota=nota,
    )


# --- la propuesta para que las tres pantallas coincidan (12-sep-2026) -----------
#
# El usuario respondió el quiz: el escenario que no le cerraba era éste, y pidió
# «proponme una solución para que coincida la información». Ésta es la propuesta,
# y el mismo día dijo «aplicalo»: desde entonces es la regla de las tres pantallas
# (reporte de saldos, sugerencias de conciliación, auditoría). Lo que mueve, medido
# sobre la copia: 47 órdenes pasan a cobradas por centavos, 15 anuladas dejan de
# contar como pagadas en las sugerencias, 4 quedan pagadas con la señal de
# sobreaplicación. Las dos lecturas viejas quedan para el diagnóstico de la
# vigilancia diaria.

CAUSA_SIN_FACTURAS = "sin_facturas"
CAUSA_ESTADO = "por_estado"
CAUSA_CENTAVOS = "residual_de_centavos"
CAUSA_DEBE = "residual_pendiente"
CAUSA_ANULADA = "anulada_sin_reemplazo"


@dataclass(frozen=True)
class PagadaUnificada:
    """Una sola respuesta, con su causa y la señal de sobrepago aparte."""

    pagada: bool
    causa: str
    residual_total: Decimal
    # Residual negativo en alguna factura: se aplicó más de lo facturado. Con lo
    # medido el 12-sep-2026 (los cuatro casos son parciales «Ajuste Dif»), es una
    # señal para auditoría, no plata a devolver -- pero la regla la reporta igual
    # en vez de esconderla bajo «pagada».
    sobreaplicada: bool


def pagada_unificada(
    facturas: list[dict[str, Any]], *, tolerancia: Decimal = TOLERANCIA_RESIDUAL
) -> PagadaUnificada:
    """Por estado, MÁS una tolerancia de centavos, MÁS la señal de sobrepago, y
    las anuladas nunca cuentan como cobradas.

    - Sin facturas: **no** está pagada (está sin facturar). Corrige la regla del
      residual, que decía «pagada» sobre una orden sin factura.
    - Las facturas ``reversed`` se apartan: no cuentan como pagadas ni como deuda.
      Si eran las únicas, la orden no está pagada: la reversó una nota de crédito,
      no un cobro.
    - Cada factura viva cuenta como pagada si su estado está en
      ``ESTADOS_PAGADOS``, **o** si está ``partial`` con residual dentro de la
      tolerancia (los 47 casos de 0,01 a 0,03).
    - ``sobreaplicada`` es verdadera si alguna factura viva tiene residual por
      debajo de ``-tolerancia``. La orden cuenta como pagada (no debe nada) pero la
      señal viaja aparte, que es lo que ninguna de las dos reglas hacía.
    """
    vivas = [
        f for f in facturas if str(f.get("payment_state") or "").strip().lower() != ESTADO_ANULADA
    ]
    if not facturas:
        return PagadaUnificada(False, CAUSA_SIN_FACTURAS, Decimal("0"), False)
    if not vivas:
        return PagadaUnificada(False, CAUSA_ANULADA, Decimal("0"), False)

    residual_total = sum((_dec(f.get("amount_residual_usd")) for f in vivas), Decimal("0"))
    sobreaplicada = any(_dec(f.get("amount_residual_usd")) < -tolerancia for f in vivas)

    todas_por_estado = True
    todas_cubiertas = True
    for f in vivas:
        estado = str(f.get("payment_state") or "").strip().lower()
        residual = _dec(f.get("amount_residual_usd"))
        if estado in ESTADOS_PAGADOS:
            continue
        todas_por_estado = False
        if residual <= tolerancia:
            continue
        todas_cubiertas = False

    if todas_por_estado:
        return PagadaUnificada(True, CAUSA_ESTADO, residual_total, sobreaplicada)
    if todas_cubiertas:
        return PagadaUnificada(True, CAUSA_CENTAVOS, residual_total, sobreaplicada)
    return PagadaUnificada(False, CAUSA_DEBE, residual_total, sobreaplicada)
