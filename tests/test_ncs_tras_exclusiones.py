"""``ncs_calculadas`` reporta la NC que queda DESPUÉS de las exclusiones.

Bug encontrado al verificar escenarios contra producción (septiembre 2026):
de 724 filas de bandeja, 7 tenían un descuento en el total que no aparecía
en ningún renglón del detalle -- 28,50 que la pantalla no podía justificar.

La causa: el detalle de primera compra se agrega usando ``final_nc``, el
valor ya pasado por ``_aplicar_exclusiones``, pero ``ncs_calculadas``
guardaba ``comp.nc``, el valor previo. Cuando una exclusión anulaba ese
descuento -- pasa cuando dispara Recompra, que lo excluye -- el renglón
desaparecía y el monto quedaba igual.

Las 7 tenían Recompra activa, que es exactamente la exclusión en juego.

``total_motor`` nunca estuvo mal: ``neto`` ya usaba ``final_nc``. Lo que
fallaba era la explicabilidad, que es la razón de ser de la bandeja.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.engine.discounts import calcular_factura
from cxc.models import ExclusionRegla

from . import builders as b
from .reglas_helpers import inputs, precios_ambas_listas, resolver


def _suma_detalle(res) -> Decimal:
    return sum((d.monto for d in res.descuentos_detalle), Decimal("0"))


def test_el_total_siempre_se_explica_con_el_detalle() -> None:
    """La invariante que se rompió: total_descuentos + ncs == suma del
    detalle. Sin ella la bandeja muestra una NC sin renglón que la
    sustente."""
    inp = inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista="5"),
        lineas=[b.linea("L1", producto="P1", marca="Sinoco", categoria="CAJA", cantidad="10")],
        valid_ves=("5",),
        valid_usd=("8",),
        price_resolver=resolver(precios_ambas_listas("P1")),
    )
    res = calcular_factura(inp)
    assert _suma_detalle(res) == res.total_descuentos + res.ncs_calculadas


def test_una_exclusion_que_anula_la_primera_compra_tambien_anula_su_monto() -> None:
    """Antes el renglón desaparecía y el monto sobrevivía."""
    inp = inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista="5"),
        lineas=[b.linea("L1", producto="P1", marca="Sinoco", categoria="CAJA", cantidad="10")],
        valid_ves=("5",),
        valid_usd=("8",),
        price_resolver=resolver(precios_ambas_listas("P1")),
    )
    inp.exclusiones = [
        ExclusionRegla(regla_tipo_a="promocion", regla_tipo_b="promocion", activo=True)
    ]
    res = calcular_factura(inp)
    origenes = {d.origen for d in res.descuentos_detalle}
    if "primera_compra" not in origenes:
        assert res.ncs_calculadas == Decimal("0")
    assert _suma_detalle(res) == res.total_descuentos + res.ncs_calculadas


def test_el_neto_no_cambia_por_este_arreglo() -> None:
    """``total_motor`` siempre estuvo bien -- ``neto`` ya usaba el valor
    posterior a las exclusiones. Se fija para que el arreglo no lo mueva."""
    inp = inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista="5"),
        lineas=[b.linea("L1", producto="P1", marca="Sinoco", categoria="CAJA", cantidad="10")],
        valid_ves=("5",),
        valid_usd=("8",),
        price_resolver=resolver(precios_ambas_listas("P1")),
    )
    res = calcular_factura(inp)
    assert res.total_motor == res.precio_base_calculado - _suma_detalle(res)
