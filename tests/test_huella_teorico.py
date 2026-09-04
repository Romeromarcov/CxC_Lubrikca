"""La lista de precios entra en la huella que dispara el recálculo.

``fingerprint_lineas`` decide si un teórico ya calculado sigue vigente.
Hasta agosto de 2026 cubría solo las líneas, y eso bastaba: el teórico
usaba una lista global e ignoraba por completo la lista de la orden, así
que un cambio de lista no podía alterarlo. Se verificó contra producción y
se dejó documentado justamente porque invitaba a un arreglo equivocado.

Desde septiembre de 2026 el teórico se calcula contra la lista de
NACIMIENTO (ver ``test_teorico_usa_listas_configuradas.py``), y esa
premisa se cayó: una orden que cambia de lista en Odoo sin tocar ninguna
línea tiene un teórico distinto y nada lo dispararía. Por eso la lista
entra ahora en la huella.
"""

from __future__ import annotations

from decimal import Decimal

from cxc.engine.runner import fingerprint_lineas
from cxc.models import LineaOrden


def _linea(linea_id="L1", producto="P1", cantidad="2", precio="10"):
    return LineaOrden(
        linea_id=linea_id,
        so_id="S00001",
        producto=producto,
        marca="Sinoco",
        categoria="CAJA",
        cantidad=Decimal(cantidad),
        precio_unitario=Decimal(precio),
    )


_LINEAS = [_linea("L1"), _linea("L2", producto="P2")]


def test_cambiar_de_lista_mueve_la_huella() -> None:
    """El caso que motivó el cambio: mismas líneas, otra lista."""
    assert fingerprint_lineas(_LINEAS, "15") != fingerprint_lineas(_LINEAS, "10")


def test_perder_la_lista_mueve_la_huella() -> None:
    """Le pasó de verdad a 54 órdenes: se borró la lista en Odoo."""
    assert fingerprint_lineas(_LINEAS, "15") != fingerprint_lineas(_LINEAS, "")


def test_la_misma_orden_da_siempre_la_misma_huella() -> None:
    assert fingerprint_lineas(_LINEAS, "15") == fingerprint_lineas(_LINEAS, "15")
    assert fingerprint_lineas(list(reversed(_LINEAS)), "15") == fingerprint_lineas(_LINEAS, "15")


def test_los_espacios_alrededor_de_la_lista_no_cuentan() -> None:
    assert fingerprint_lineas(_LINEAS, " 15 ") == fingerprint_lineas(_LINEAS, "15")


def test_editar_una_linea_sigue_moviendo_la_huella() -> None:
    """Lo que la huella ya cubría -- hallazgo original, orden S00792."""
    otras = [_linea("L1", cantidad="3"), _linea("L2", producto="P2")]
    assert fingerprint_lineas(otras, "15") != fingerprint_lineas(_LINEAS, "15")
    menos = [_linea("L1")]
    assert fingerprint_lineas(menos, "15") != fingerprint_lineas(_LINEAS, "15")


def test_la_lista_no_se_confunde_con_una_linea() -> None:
    """La marca ``__lista__`` va aparte: un producto llamado igual que un
    id de lista no puede colisionar con el campo de la lista."""
    con_lista = fingerprint_lineas([_linea("L1", producto="15")], "")
    sin_lista = fingerprint_lineas([_linea("L1", producto="")], "15")
    assert con_lista != sin_lista
