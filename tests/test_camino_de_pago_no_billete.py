"""La moneda que evalúa una regla es la del CAMINO, no la del billete.

Modelo del usuario (septiembre 2026), textual: "siempre hay dos caminos
posibles, ambos se fijan en USD. uno es pagando en VES a la tasa del BCV y
el otro es pagando en USD en cualquiera de sus formas o en VES a la tasa
Binance".

``pura_bcv`` ES ese camino, y sale de ``es_ruta_bcv_pura``, que mira el
TIPO DE TASA del abono (``tipo_tasa_abono``), no su moneda. Un pago en
bolívares registrado a tasa Binance pertenece al camino USD: le rinde
menos al cliente, que es justamente el resguardo que el usuario pidió
desde el principio -- una orden no puede salir por el teórico USD sin que
el pago haya sido en USD o su equivalente Binance.

Antes, ``moneda_pago`` se decidía SOLO por la moneda de los abonos, así
que una orden pagada íntegramente en bolívares quedaba marcada "VES" en
los DOS caminos y no podía matchear ninguna regla con
``monedas_aplicables=USD``, ni siquiera evaluándose contra la lista USD a
tasa Binance.

Alcance real, medido contra producción DESPUÉS de escribir el arreglo: de
las 216 órdenes pagadas solo en bolívares, las 216 van por ruta BCV y
ninguna por Binance. O sea que hoy el arreglo no mueve un peso -- cierra
un hueco latente. Y esas 216 sí reciben las reglas VES cuando califican:
14 de ellas tienen contado (12 por PP_AE86B6D6 al 20 %, 2 por
PP_DF33F50E al 15 %); las otras 202 no pagaron dentro de la ventana.
"""

from __future__ import annotations


def _moneda_pago(pura_bcv: bool, monedas_de_los_abonos: set[str]) -> str:
    """La regla, aislada tal como quedó en _calcular_componentes."""
    if pura_bcv and monedas_de_los_abonos == {"VES"}:
        return "VES"
    return "USD"


def test_bolivares_a_tasa_bcv_es_el_camino_ves() -> None:
    assert _moneda_pago(pura_bcv=True, monedas_de_los_abonos={"VES"}) == "VES"


def test_bolivares_a_tasa_binance_es_el_camino_usd() -> None:
    """El caso que el arreglo destraba: mismo billete, otro camino."""
    assert _moneda_pago(pura_bcv=False, monedas_de_los_abonos={"VES"}) == "USD"


def test_dolares_siempre_son_el_camino_usd() -> None:
    assert _moneda_pago(pura_bcv=False, monedas_de_los_abonos={"USD"}) == "USD"
    assert _moneda_pago(pura_bcv=True, monedas_de_los_abonos={"USD"}) == "USD"


def test_pago_mixto_cuenta_como_usd() -> None:
    """Regla previa del usuario: una orden con abonos en las dos monedas
    cuenta como USD, y los bolívares se valoran a Binance."""
    assert _moneda_pago(pura_bcv=True, monedas_de_los_abonos={"USD", "VES"}) == "USD"


def test_sin_abonos_se_evalua_como_usd() -> None:
    """Es el teórico: todavía no hay pago que clasifique."""
    assert _moneda_pago(pura_bcv=True, monedas_de_los_abonos=set()) == "USD"
    assert _moneda_pago(pura_bcv=False, monedas_de_los_abonos=set()) == "USD"
