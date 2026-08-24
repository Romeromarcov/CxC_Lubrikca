"""Matriz de escenarios de las Exclusiones Mutuas de Descuentos

(tabla ``exclusiones``, panel "Exclusiones Mutuas de Descuentos").

No son un descuento en sí: deciden qué componentes pueden apilarse. El bug
real que motivó estos tests (agosto 2026) es que las filas de producción
guardaban un vocabulario que el motor no reconoce ('promocion' en vez de
'primera_compra', 'recompra' en vez de 'recurrencia'), así que la fila
'promocion' <-> 'recompra' nunca excluyó nada.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.engine.discounts import calcular_factura, normalizar_componente_descuento
from cxc.models import ExclusionRegla

from . import builders as b
from .reglas_helpers import LISTA_VES, inputs, precios_ambas_listas, resolver

PROD = "1033"


def _inp(exclusiones=()):
    """Orden que dispara obsequio (100) y volumen (50) a la vez."""
    promo = b.promo_primera(
        producto=PROD, compra_minima="3", regalo_tipo="solo_uno", categorias_aplica="*"
    )
    vol = b.descuento_volumen(marca="*", categoria="*", litros_minimo="100", porcentaje="0.05")
    return inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista=LISTA_VES),
        lineas=[b.linea("L1", producto=PROD, categoria="CAJA", cantidad="10", precio="100")],
        promociones=[promo],
        descuentos_volumen=[vol],
        exclusiones=list(exclusiones),
        price_resolver=resolver(precios_ambas_listas(PROD), {PROD: "20"}),
    )


def test_sin_exclusiones_ambos_descuentos_se_apilan():
    bandeja = calcular_factura(_inp())

    assert bandeja.ncs_calculadas == Decimal("100.00")
    # 1000 de base, 5% de volumen.
    assert bandeja.total_descuentos == Decimal("50.00")
    assert bandeja.total_motor == Decimal("850.00")


def test_exclusion_anula_el_componente_de_menor_valor():
    """"Se aplica el de mayor valor": obsequio (100) gana sobre volumen (50)."""
    excl = ExclusionRegla(regla_tipo_a="primera_compra", regla_tipo_b="volumen", activo=True)
    bandeja = calcular_factura(_inp([excl]))

    assert bandeja.ncs_calculadas == Decimal("100.00")
    assert bandeja.total_descuentos == Decimal("0.00")
    assert bandeja.total_motor == Decimal("900.00")


def test_exclusion_tambien_se_refleja_en_los_teoricos():
    """El teórico debe cuadrar con el neto: 100 (obsequio), no 150."""
    excl = ExclusionRegla(regla_tipo_a="primera_compra", regla_tipo_b="volumen", activo=True)
    bandeja = calcular_factura(_inp([excl]))

    assert bandeja.descuentos_teorico_ves == Decimal("100.00")


def test_exclusion_inactiva_no_excluye_nada():
    excl = ExclusionRegla(regla_tipo_a="primera_compra", regla_tipo_b="volumen", activo=False)
    bandeja = calcular_factura(_inp([excl]))

    assert bandeja.ncs_calculadas == Decimal("100.00")
    assert bandeja.total_descuentos == Decimal("50.00")


def test_exclusion_entre_componentes_que_no_compiten_no_hace_nada():
    """Solo se anula un componente si AMBOS son > 0."""
    excl = ExclusionRegla(regla_tipo_a="contado", regla_tipo_b="bcv_completo", activo=True)
    bandeja = calcular_factura(_inp([excl]))

    assert bandeja.ncs_calculadas == Decimal("100.00")
    assert bandeja.total_descuentos == Decimal("50.00")


def test_alias_legacy_produce_el_mismo_resultado_que_el_canonico():
    """Regresión del bug: 'promocion' es el alias legacy de 'primera_compra'.

    Antes esta fila se ignoraba en silencio porque el nombre no era una
    clave válida del dict de componentes.
    """
    legacy = ExclusionRegla(regla_tipo_a="promocion", regla_tipo_b="volumen", activo=True)
    canonico = ExclusionRegla(regla_tipo_a="primera_compra", regla_tipo_b="volumen", activo=True)

    assert calcular_factura(_inp([legacy])).total_motor == calcular_factura(
        _inp([canonico])
    ).total_motor


def test_exclusion_con_tipo_desconocido_se_ignora_sin_romper():
    """Un nombre que no corresponde a ningún componente no debe tumbar el

    cálculo ni anular algo al azar.
    """
    excl = ExclusionRegla(regla_tipo_a="descuento_inventado", regla_tipo_b="volumen", activo=True)
    bandeja = calcular_factura(_inp([excl]))

    assert bandeja.ncs_calculadas == Decimal("100.00")
    assert bandeja.total_descuentos == Decimal("50.00")


def test_normalizacion_de_alias():
    assert normalizar_componente_descuento("promocion") == "primera_compra"
    assert normalizar_componente_descuento("recompra") == "recurrencia"
    assert normalizar_componente_descuento("  Recompra  ") == "recurrencia"
    assert normalizar_componente_descuento("pronto_pago") == "contado"
    # Ya canónico -- se devuelve tal cual.
    assert normalizar_componente_descuento("volumen") == "volumen"
    # Desconocido -- no se inventa un componente.
    assert normalizar_componente_descuento("otra_cosa") == "otra_cosa"
