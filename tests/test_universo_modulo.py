"""Qué órdenes entran a la CxC, ahora en su propio módulo (Fase 2.4).

Segunda pieza extraída, con la misma medición A/B que la primera: el nombre
viejo y el nuevo tienen que dar exactamente lo mismo.

Y con algo más, que es la razón de haberla elegido: esta función es **la
definición del universo**. De ella dependen las dos primeras partidas del
balance, el reporte de saldos, la bandeja, el dashboard y el reporte diario. Que
las seis páginas cuenten las mismas órdenes es lo que hace que el balance
signifique algo, y esa garantía descansa entera en que las seis llamen a la
misma función. Estos tests la fijan.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from cxc.engine.universo import ESTADOS_ORDEN_EXCLUIDOS, orden_excluida


@dataclass
class _Orden:
    estado_orden: str = "sale"


CASOS = [
    # (estado local, estado en vivo, entrega válida, se excluye)
    ("sale", None, False, False),
    ("done", None, False, False),
    ("cancel", None, False, True),
    ("cancelled", None, False, True),
    ("draft", None, False, True),
    ("sent", None, False, True),
    # La excepción de negocio: cancelada CON entrega no devuelta sigue siendo venta.
    ("cancel", None, True, False),
    ("cancelled", None, True, False),
    # Una cotización no se salva por tener entrega: no puede tenerla.
    ("draft", None, True, True),
    ("sent", None, True, True),
    # El estado en vivo gana sobre el del espejo, en las dos direcciones.
    ("sale", "cancel", False, True),
    ("cancel", "sale", False, False),
    # Mayúsculas y espacios no cambian el veredicto.
    ("  CANCEL  ", None, False, True),
    ("sale", "  Draft ", False, True),
    # Sin estado, se trata como viva: no excluir por no saber.
    ("", None, False, False),
]


@pytest.mark.parametrize("local,vivo,entrega,esperado", CASOS)
def test_el_veredicto_es_el_esperado(local, vivo, entrega, esperado) -> None:
    assert orden_excluida(_Orden(local), live_state=vivo, entrega_valida=entrega) is esperado


@pytest.mark.parametrize("local,vivo,entrega,_esperado", CASOS)
def test_el_alias_viejo_da_exactamente_lo_mismo(local, vivo, entrega, _esperado) -> None:
    """La medición A/B de esta pieza."""
    from cxc.web.app import orden_excluida as desde_app

    orden = _Orden(local)
    assert desde_app(orden, live_state=vivo, entrega_valida=entrega) == orden_excluida(
        orden, live_state=vivo, entrega_valida=entrega
    )


def test_el_estado_en_vivo_gana_porque_el_espejo_se_queda_viejo() -> None:
    """El caso real que motivó consultar Odoo en vivo.

    S00162, por 161.679,06 USD, figuraba como ``sale`` en el espejo y estaba
    ``cancel`` en Odoo: el sync mira una ventana de 48 horas por ``write_date``,
    así que una orden cancelada fuera de esa ventana se queda con el estado
    viejo para siempre. Inflaba «Ventas del Año» en ese monto.
    """
    orden_vieja_en_el_espejo = _Orden("sale")
    assert orden_excluida(orden_vieja_en_el_espejo) is False, "sin el vivo, cuenta como venta"
    assert orden_excluida(orden_vieja_en_el_espejo, live_state="cancel") is True


def test_una_cancelada_con_entrega_sigue_siendo_una_venta() -> None:
    """La excepción de negocio, que no es hipotética.

    Medido en el Odoo de prueba: **16 órdenes canceladas con entrega completa,
    por 11.995,68 USD**. Odoo permite cancelar una orden después del despacho, y
    eso no deshace la entrega. Si la excepción no existiera, esas 16 saldrían de
    los totales llevándose la mercancía.
    """
    assert orden_excluida(_Orden("cancel"), entrega_valida=True) is False
    assert orden_excluida(_Orden("cancel"), entrega_valida=False) is True


def test_los_estados_excluidos_son_los_cuatro_conocidos() -> None:
    """Si esta lista crece, crece el universo que TODAS las páginas dejan de ver.

    No es un test de trivia: agregar un estado acá saca órdenes de seis páginas
    a la vez, y el balance no lo notaría porque sus dos primeras partidas usan
    esta misma función de los dos lados.
    """
    assert frozenset({"cancel", "cancelled", "draft", "sent"}) == ESTADOS_ORDEN_EXCLUIDOS
