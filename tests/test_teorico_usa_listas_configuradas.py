"""El teórico se calcula con las listas CONFIGURADAS, no con la de la orden.

Verificado durante la auditoría de septiembre 2026, y vale dejarlo fijado
porque invita a un arreglo equivocado: ``fingerprint_lineas`` -- la huella
que decide si hay que recalcular el teórico -- no incluye
``lista_precios``, así que a primera vista parece que una orden que cambia
de lista se quedaría con un teórico viejo.

No es así, y por eso no hay nada que corregir ahí: el teórico no depende de
la lista de la orden. ``_lista_ves_activa`` toma la VES configurada y
``_lista_usd_activa`` la USD configurada, salvo que la orden caiga en la
ventana histórica, donde usa la lista 7. La lista propia de la orden solo
influye en el neto (``_determinar_lista``), y ese camino se recalcula en
cada ciclo sin ninguna huella de por medio.

Comprobado contra producción: de 792 teóricos, 0 tienen huella desfasada y
0 usan una lista USD distinta a la que la regla daría hoy. Las 59 órdenes
en lista 7 son exactamente las 59 de la ventana histórica.
"""

from __future__ import annotations

from datetime import date

from cxc.engine.discounts import _lista_usd_activa, _lista_ves_activa

from . import builders as b
from .reglas_helpers import inputs


def _inp(*, lista_orden: str, historica: bool):
    inp = inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista=lista_orden),
        lineas=[b.linea("L1", producto="P1", marca="Sinoco", categoria="CAJA")],
        valid_ves=("5",),
        valid_usd=("8",),
    )
    inp.orden_es_historica = historica
    return inp


def test_la_lista_ves_sale_de_configuracion_no_de_la_orden() -> None:
    for lista_orden in ("3", "4", "5", "7"):
        assert _lista_ves_activa(_inp(lista_orden=lista_orden, historica=False)) == "5"


def test_la_lista_usd_sale_de_configuracion_no_de_la_orden() -> None:
    for lista_orden in ("3", "4", "5", "8"):
        assert _lista_usd_activa(_inp(lista_orden=lista_orden, historica=False)) == "8"


def test_una_orden_de_la_ventana_historica_usa_la_lista_usd_de_entonces() -> None:
    """Las 59 órdenes que en producción usan la lista 7 son exactamente las
    de la ventana histórica -- verificado, 59 de 59."""
    assert _lista_usd_activa(_inp(lista_orden="4", historica=True)) == "7"
    # Y la VES no cambia por ser histórica.
    assert _lista_ves_activa(_inp(lista_orden="4", historica=True)) == "5"
