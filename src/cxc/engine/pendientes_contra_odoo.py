"""Vinculaciones PENDIENTE que Odoo ya contradijo.

Regla del usuario (30-sep-2026): **Odoo manda**. Una vinculación PENDIENTE es una
propuesta del sistema (auto-FIFO o manual) mientras Odoo no concilia el pago; cuando
Odoo concilia, lo que diga Odoo prevalece y la propuesta debe desaparecer. Al final
todas las vinculaciones terminan conciliadas por Odoo y solo quedan pendientes las que
legítimamente esperan.

Esta función decide cuáles PENDIENTE se retiran y cuáles quedan. Por pago:

- Las CONCILIADAS de Odoo cubren todo el pago (suma >= monto): TODAS sus pendientes
  sobran -- Odoo ya repartió el pago, cualquier propuesta que quedó apuntando a otra
  orden es un residuo (caso real: pago 2018, pendiente en S00470 y conciliado en S00790
  por el mismo monto).
- Las conciliadas cubren solo una parte, y las pendientes caben en lo que falta: son
  legítimas (el resto del pago todavía espera).
- Cualquier otro caso (pendientes que exceden lo que falta, o dos sugerencias sin
  confirmar que entre ambas reclaman más que el pago) NO se toca: no hay forma de saber
  cuál de las propuestas es la buena, y borrar una de ellas sería adivinar. Se devuelve
  aparte para que se vea.

Función pura: no lee ni escribe el repositorio.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..models import EstadoVinculacion, Pago, Vinculacion

TOLERANCIA = Decimal("0.01")


@dataclass(frozen=True)
class DepuracionPendientes:
    a_retirar: list[Vinculacion]  # pendientes de pagos que Odoo ya cubrió por completo
    ambiguas: list[Vinculacion]  # pendientes que sobran pero no se sabe cuáles


def clasificar_pendientes(
    vinculaciones: list[Vinculacion], pagos: list[Pago]
) -> DepuracionPendientes:
    monto_por_pago = {str(p.pago_id): p.monto for p in pagos}
    por_pago: dict[str, list[Vinculacion]] = {}
    for v in vinculaciones:
        por_pago.setdefault(str(v.pago_id), []).append(v)

    a_retirar: list[Vinculacion] = []
    ambiguas: list[Vinculacion] = []
    for pago_id, vincs in por_pago.items():
        pendientes = [v for v in vincs if v.estado == EstadoVinculacion.PENDIENTE]
        if not pendientes or pago_id not in monto_por_pago:
            continue
        monto = monto_por_pago[pago_id]
        conciliado = sum(
            (v.monto_aplicado for v in vincs if v.estado == EstadoVinculacion.CONCILIADO),
            Decimal("0"),
        )
        pendiente = sum((v.monto_aplicado for v in pendientes), Decimal("0"))
        if conciliado > 0 and conciliado >= monto - TOLERANCIA:
            a_retirar.extend(pendientes)
        elif conciliado > 0 and pendiente <= monto - conciliado + TOLERANCIA:
            continue  # parcial legitimo: el resto del pago sigue esperando
        elif conciliado > 0 or pendiente > monto + TOLERANCIA:
            ambiguas.extend(pendientes)
    return DepuracionPendientes(a_retirar=a_retirar, ambiguas=ambiguas)
