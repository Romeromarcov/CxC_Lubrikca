"""Cuánto puede valer la nota de crédito que la bandeja sugiere (Fase 2.4, pieza 34).

La bandeja de facturación sugiere una NC por el descuento pendiente de aplicar. Ese
descuento se calcula sobre el teórico; la NC que se puede emitir de verdad tiene dos
techos, y los dos salieron de casos reales:

1. **La brecha.** Lo facturado (neto de NC/ND) menos lo pagado es, a lo sumo, lo que
   el cliente todavía no puso; si el descuento ya se dio en el precio o en la línea,
   la factura salió más baja, el cliente la pagó completa y la brecha es cero. El
   caso que lo destapó: S00010 (TERA), una NC sugerida de 3.949,79 USD con una brecha
   de −0,52. Medido contra producción: 92 de 215 órdenes sugerían más que su brecha,
   22.393,84 USD en total.
2. **El teórico.** Una orden facturada por DEBAJO de su teórico ya entregó el
   descuento y de más; acreditarle una NC encima sería descontar dos veces. Regla
   general del usuario (septiembre 2026): esas órdenes no sugieren nada y se ven en
   Auditoría, donde administración decide si corresponde una nota de débito.

Y una regla previa a las dos: **el techo solo aplica si se sabe cuánto se facturó**.
Sin ese dato no hay brecha que calcular, y «no sé» no es «cero» -- tratarlo como cero
vaciaba la bandeja entera cuando Odoo no respondía.

La brecha viene con impuesto y el descuento se calcula sobre el subtotal, así que se
llevan a la misma unidad con el factor real de esa factura (con impuestos / sin
impuestos). Si la factura no trae los dos totales, se usa el IVA configurado -- y el
resultado lo dice, porque un techo calculado con una tasa supuesta no es lo mismo que
uno calculado con la de la factura.
"""

from __future__ import annotations

from dataclasses import dataclass

TOLERANCIA = 0.05

MOTIVO_SIN_FACTURADO = "sin_facturado"
MOTIVO_FACTURADO_BAJO_TEORICO = "facturado_bajo_teorico"
MOTIVO_SIN_BRECHA = "sin_brecha"
MOTIVO_TOPADA_POR_BRECHA = "topada_por_brecha"
MOTIVO_CABE_EN_LA_BRECHA = "cabe_en_la_brecha"


@dataclass(frozen=True)
class NotaDeCreditoTopada:
    """El subtotal que se puede sugerir, y por qué es ese y no otro."""

    subtotal: float
    motivo: str
    brecha_con_impuesto: float | None
    factor_iva: float | None
    iva_asumido: bool

    @property
    def hay_nc(self) -> bool:
        return self.subtotal > TOLERANCIA


def factor_de_impuesto(
    facturado_sin_impuestos: float, facturado_con_impuestos: float, *, iva_configurado: float
) -> tuple[float, bool]:
    """Cociente real de la factura (con / sin), o ``1 + iva_configurado`` si falta uno.

    Devuelve también si el factor es supuesto. El cociente real refleja retenciones
    y facturas con tasas mezcladas; la constante no.
    """
    if facturado_sin_impuestos > 0 and facturado_con_impuestos > 0:
        return facturado_con_impuestos / facturado_sin_impuestos, False
    return 1.0 + iva_configurado, True


def topar_nota_de_credito(
    *,
    descuento_pendiente: float,
    facturado_neto: float,
    pagado_usd: float,
    teorico_de_referencia: float | None,
    facturado_sin_impuestos: float,
    facturado_con_impuestos: float,
    iva_configurado: float,
) -> NotaDeCreditoTopada:
    """Aplica los dos techos al descuento pendiente. Ver el módulo."""
    if facturado_neto <= TOLERANCIA:
        # No se sabe cuánto se facturó: no hay techo que aplicar. La sugerencia
        # sale entera, que es distinto de salir en cero.
        return NotaDeCreditoTopada(
            subtotal=descuento_pendiente,
            motivo=MOTIVO_SIN_FACTURADO,
            brecha_con_impuesto=None,
            factor_iva=None,
            iva_asumido=False,
        )

    brecha = round(facturado_neto - pagado_usd, 2)
    bajo_teorico = (
        teorico_de_referencia is not None
        and facturado_neto < float(teorico_de_referencia) - TOLERANCIA
    )
    if bajo_teorico:
        return NotaDeCreditoTopada(
            subtotal=0.0,
            motivo=MOTIVO_FACTURADO_BAJO_TEORICO,
            brecha_con_impuesto=brecha,
            factor_iva=None,
            iva_asumido=False,
        )
    if brecha <= TOLERANCIA:
        return NotaDeCreditoTopada(
            subtotal=0.0,
            motivo=MOTIVO_SIN_BRECHA,
            brecha_con_impuesto=brecha,
            factor_iva=None,
            iva_asumido=False,
        )

    factor, asumido = factor_de_impuesto(
        facturado_sin_impuestos, facturado_con_impuestos, iva_configurado=iva_configurado
    )
    tope = brecha / factor
    if descuento_pendiente <= tope:
        return NotaDeCreditoTopada(
            subtotal=descuento_pendiente,
            motivo=MOTIVO_CABE_EN_LA_BRECHA,
            brecha_con_impuesto=brecha,
            factor_iva=factor,
            iva_asumido=asumido,
        )
    return NotaDeCreditoTopada(
        subtotal=tope,
        motivo=MOTIVO_TOPADA_POR_BRECHA,
        brecha_con_impuesto=brecha,
        factor_iva=factor,
        iva_asumido=asumido,
    )
