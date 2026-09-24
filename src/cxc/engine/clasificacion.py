"""Clasificación Comercial/Industrial de una orden (ítem 6, septiembre 2026).

Pedido del usuario: además de los flags reales de Odoo (categoría de
producto por línea), faltaba una forma de decidir si la ORDEN completa es
"Comercial" o "Industrial" combinando varias señales, ninguna de las
cuales por sí sola es suficiente:

- El vendedor que la tomó puede ser un vendedor "industrial".
- El cliente puede estar marcado como cliente "industrial" (dato que no
  existe en Odoo -- lo llena este sistema, ver ``clasificacion_clientes``).
- La lista de precios con la que nació la orden puede estar marcada como
  "industrial" (reusa el campo ``categoria`` que ya existe en el mapeo
  unificado de listas -- no hace falta una config nueva para esto).
- El volumen de las LÍNEAS cuyo producto es de categoría "Industrial" (dato
  real de Odoo, ``LineaOrden.categoria_madre``) puede superar un umbral en
  litros (por defecto 18,92 L -- el tamaño real de una paila en el
  catálogo).

Ninguna señal manda sola: la orden es "industrial" si CUALQUIERA DOS de
las cuatro se cumplen (decisión explícita del usuario), y "comercial" en
caso contrario -- incluyendo cuando falta información para evaluar alguna
señal (una señal ausente cuenta como "no cumplida", nunca como "sí").
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

COMERCIAL = "comercial"
INDUSTRIAL = "industrial"

UMBRAL_LITROS_INDUSTRIAL_DEFAULT = Decimal("18.92")


@dataclass(frozen=True)
class ClasificacionComercialIndustrial:
    clasificacion: str  # "comercial" | "industrial"
    criterios_cumplidos: int
    vendedor_industrial: bool
    cliente_industrial: bool
    lista_industrial: bool
    volumen_industrial: bool
    litros_industriales: Decimal
    umbral_litros: Decimal


def clasificar_comercial_industrial(
    *,
    vendedor_industrial: bool,
    cliente_industrial: bool,
    lista_industrial: bool,
    litros_industriales: Decimal,
    umbral_litros: Decimal = UMBRAL_LITROS_INDUSTRIAL_DEFAULT,
) -> ClasificacionComercialIndustrial:
    """Aplica la regla "≥2 de 4" sobre las cuatro señales ya evaluadas.

    Pura a propósito: quien llama resuelve cada señal (leer la tabla de
    vendedores, la de clasificación de clientes, el mapeo de listas, sumar
    litros de las líneas industriales) y le pasa el resultado ya
    calculado -- así esta función se prueba sin mockear repositorios ni
    Odoo, y se puede reusar donde haga falta (Ventas hoy, lo que venga
    después).
    """
    volumen_industrial = litros_industriales >= umbral_litros
    senales = (vendedor_industrial, cliente_industrial, lista_industrial, volumen_industrial)
    criterios_cumplidos = sum(1 for s in senales if s)
    clasificacion = INDUSTRIAL if criterios_cumplidos >= 2 else COMERCIAL
    return ClasificacionComercialIndustrial(
        clasificacion=clasificacion,
        criterios_cumplidos=criterios_cumplidos,
        vendedor_industrial=vendedor_industrial,
        cliente_industrial=cliente_industrial,
        lista_industrial=lista_industrial,
        volumen_industrial=volumen_industrial,
        litros_industriales=litros_industriales,
        umbral_litros=umbral_litros,
    )


def litros_de_lineas_industriales(
    lineas: list[Any], volumen_por_producto: dict[str, float]
) -> Decimal:
    """Suma de litros de las líneas cuya categoría de producto (dato real de

    Odoo, ``LineaOrden.categoria_madre``) es "Industrial". ``volumen_por_
    producto`` es ``{producto_id: litros por unidad}`` -- normalmente
    ``{p.producto_id: float(p.volumen) for p in repo.all_catalogo()}``, el
    mismo espejo que ya usa ``_litros_por_so_desde_espejo``.

    Un producto sin volumen en el catálogo aporta 0 -- "no sé cuánto mide"
    no puede sumar a favor de la clasificación industrial.
    """
    total = Decimal("0")
    for ln in lineas:
        if str(getattr(ln, "categoria_madre", "")).strip().lower() != "industrial":
            continue
        vol_unit = volumen_por_producto.get(str(ln.producto), 0.0)
        if not vol_unit:
            continue
        total += Decimal(str(ln.cantidad)) * Decimal(str(vol_unit))
    return total
