"""Los feriados alargan la ventana de contado TEÓRICA, no solo la real.

Bug real (auditoría de producción, agosto 2026): en el camino teórico (sin
abonos) se le pasaba ``inp.feriados_tabla`` -- la lista de objetos
``Feriado`` -- a ``fin_ventana_contado``, que espera el ``frozenset[date]``
de la property ``inp.feriados``. Como ``es_dia_habil`` evalúa ``d not in
feriados`` y un ``date`` nunca es igual a un ``Feriado``, los feriados se
ignoraban por completo: la ventana terminaba antes de tiempo y
``contado_proy`` se ponía en cero antes de vencer de verdad.

Estaba inerte en producción sólo porque la tabla ``feriados`` estaba
vacía; habría empezado a descontar de menos apenas se cargara uno.
"""

from __future__ import annotations

from datetime import date

from cxc.engine.business_days import fin_ventana_contado
from cxc.models import Feriado


def test_la_property_feriados_es_lo_que_espera_la_ventana() -> None:
    """El contrato que se violaba: la función compara contra fechas."""
    feriados_tabla = [Feriado(fecha=date(2026, 6, 3), descripcion="Feriado", tipo="nacional")]
    como_fechas = frozenset(f.fecha for f in feriados_tabla)

    # Miércoles 2026-06-03 feriado: 3 días hábiles desde el lunes 1 llegan
    # al viernes 5 en vez del jueves 4.
    assert fin_ventana_contado(date(2026, 6, 1), 3, como_fechas) == date(2026, 6, 5)

    # Pasar la LISTA de objetos (el bug) deja la ventana un día corta,
    # porque ningun ``date`` coincide con un ``Feriado``.
    assert fin_ventana_contado(date(2026, 6, 1), 3, feriados_tabla) == date(2026, 6, 4)  # type: ignore[arg-type]


def _bandeja_al(fecha_calculo: date, feriados=()):
    """Contado del 5% con entrega el lunes 2026-06-01 y ventana de 3 días
    hábiles, evaluado a ``fecha_calculo``."""
    from cxc.engine.discounts import calcular_factura

    from . import builders as b
    from .reglas_helpers import LISTA_VES, inputs, precios_ambas_listas, resolver

    prod = "1033"
    return calcular_factura(
        inputs(
            orden=b.orden(fecha=date(2026, 6, 1), fecha_entrega=date(2026, 6, 1), lista=LISTA_VES),
            lineas=[
                b.linea("L1", producto=prod, marca="Sinoco", categoria="CAJA",
                        cantidad="10", precio="100")
            ],
            descuentos=[b.descuento(marca="Sinoco", categoria="CAJA", porcentaje="0.05")],
            feriados=list(feriados),
            fecha_calculo=fecha_calculo,
            price_resolver=resolver(precios_ambas_listas(prod)),
        )
    )


def test_el_motor_teorico_respeta_los_feriados() -> None:
    """Extremo a extremo: con un feriado dentro de la ventana, el teórico

    de contado sigue vigente un día más.

    Entrega lunes 1, miércoles 3 feriado, ventana de 3 días hábiles. Sin
    contar el feriado la ventana cierra el jueves 4; contándolo, el viernes
    5. Evaluado el viernes 5, el descuento solo sigue proyectado si el
    feriado se tuvo en cuenta -- que es justo lo que el bug rompía.
    """
    from decimal import Decimal

    feriado = [Feriado(fecha=date(2026, 6, 3), descripcion="Feriado", tipo="nacional")]

    # Control: sin feriados la ventana ya venció el viernes 5.
    assert _bandeja_al(date(2026, 6, 5)).descuentos_teorico_ves == Decimal("0")
    # Con el feriado cargado, sigue vigente.
    assert _bandeja_al(date(2026, 6, 5), feriado).descuentos_teorico_ves > Decimal("0")
