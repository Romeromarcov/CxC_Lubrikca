"""El teórico se compara contra la lista con la que NACIÓ la orden.

Decisión del usuario (septiembre 2026): si la orden nació en "Industrial
3% VES", su teórico VES sale de esa lista y el USD de su par "Industrial
3% USD". El pareo se configura en ``pricelist_pares`` y es **simétrico** --
una orden nacida en la lista USD toma su teórico VES del par VES.

Esto invierte el diseño anterior, y vale dejar constancia de por qué:
hasta agosto de 2026 el teórico usaba una lista global (la primera VES y
la primera USD de Configuración) e ignoraba por completo la lista de la
orden. Aquel comportamiento se verificó contra producción y quedó fijado
en este mismo archivo. Se cambió a pedido del usuario porque comparar una
orden de septiembre contra precios de mayo no dice nada útil.

Consecuencia que el cambio arrastra, y que antes era inofensiva:
``fingerprint_lineas`` -- la huella que decide si hay que recalcular el
teórico -- no incluía ``lista_precios``. Daba igual mientras el teórico no
dependiera de la lista de la orden. Ahora sí depende, así que la lista
entra en la huella; sin eso, una orden que cambia de lista en Odoo sin
tocar sus líneas se quedaría con el teórico viejo. Ver
``test_huella_teorico.py``.

Dos reglas de caída, ambas decididas por el usuario:

  · **Sin par configurado** (o sin lista, como las 54 órdenes que
    perdieron su pricelist en Odoo): cae a la lista global, que es el
    comportamiento anterior. Así una lista nueva en Odoo nunca deja
    órdenes sin teórico solo por no estar pareada todavía.
  · **Listas viejas archivadas** (3, 4, 5, 7, 8, 9): no se aparean. Sus
    órdenes conservan el teórico contra 5/8, la referencia con la que se
    calcularon en su momento. El pareo aplica de las listas de septiembre
    en adelante.

La ventana histórica sigue ganando sobre todo lo demás: son órdenes
anteriores a que estas listas existieran.
"""

from __future__ import annotations

from datetime import date

from cxc.engine.discounts import _lista_usd_activa, _lista_ves_activa

from . import builders as b
from .reglas_helpers import inputs

# Pareo real cargado en producción: solo las listas de septiembre 2026.
_PARES = {
    "10": "11",
    "11": "10",  # Pago
    "12": "13",
    "13": "12",  # Foránea
    "15": "14",
    "14": "15",  # Industrial 3%
    "16": "17",
    "17": "16",  # Industrial 4%
    "19": "18",
    "18": "19",  # Maturín
}


def _inp(*, lista_orden: str, historica: bool = False, pares=None):
    inp = inputs(
        orden=b.orden(fecha=date(2026, 6, 1), lista=lista_orden),
        lineas=[b.linea("L1", producto="P1", marca="Sinoco", categoria="CAJA")],
        valid_ves=("5", "3", "4", "10", "12", "15", "16", "19", "9"),
        valid_usd=("8", "7", "11", "13", "14", "17", "18"),
    )
    inp.orden_es_historica = historica
    inp.pares_listas = _PARES if pares is None else pares
    return inp


# --- El caso que pidió el usuario -------------------------------------------


def test_una_orden_industrial_ves_usa_su_propia_lista_y_su_par_usd() -> None:
    """Nacida en "Industrial 3% VES" (15): teórico VES contra la 15,
    teórico USD contra su par "Industrial 3% USD" (14)."""
    inp = _inp(lista_orden="15")
    assert _lista_ves_activa(inp) == "15"
    assert _lista_usd_activa(inp) == "14"


def test_el_pareo_funciona_en_el_sentido_inverso() -> None:
    """Nacida en la lista USD (14): el teórico VES sale del par (15). El
    par es una pareja, no una dirección."""
    inp = _inp(lista_orden="14")
    assert _lista_usd_activa(inp) == "14"
    assert _lista_ves_activa(inp) == "15"


def test_cada_pareja_de_septiembre_se_resuelve_completa() -> None:
    for ves_id, usd_id in (("10", "11"), ("12", "13"), ("15", "14"), ("16", "17"), ("19", "18")):
        desde_ves = _inp(lista_orden=ves_id)
        assert (_lista_ves_activa(desde_ves), _lista_usd_activa(desde_ves)) == (ves_id, usd_id)
        desde_usd = _inp(lista_orden=usd_id)
        assert (_lista_ves_activa(desde_usd), _lista_usd_activa(desde_usd)) == (ves_id, usd_id)


# --- Las caídas -------------------------------------------------------------


def test_una_lista_vieja_sin_par_cae_a_la_lista_global() -> None:
    """Las archivadas (3, 4, 7, 9) no se aparean: conservan la referencia
    con la que se calcularon en su momento."""
    for lista_orden in ("3", "4", "5", "7", "8", "9"):
        inp = _inp(lista_orden=lista_orden)
        assert _lista_ves_activa(inp) == "5"
        assert _lista_usd_activa(inp) == "8"


def test_una_orden_sin_lista_cae_a_la_lista_global() -> None:
    """Las 54 órdenes que perdieron su pricelist en Odoo."""
    for vacio in ("", "0", "None", "False"):
        inp = _inp(lista_orden=vacio)
        assert _lista_ves_activa(inp) == "5"
        assert _lista_usd_activa(inp) == "8"


def test_una_lista_nueva_todavia_sin_configurar_no_deja_la_orden_sin_teorico() -> None:
    """Si Odoo estrena una lista y nadie la carga, la orden usa la global
    en vez de quedarse sin referencia."""
    inp = _inp(lista_orden="99")
    assert _lista_ves_activa(inp) == "5"
    assert _lista_usd_activa(inp) == "8"


def test_sin_ningun_pareo_configurado_vuelve_al_comportamiento_anterior() -> None:
    """El sistema tal como estaba antes de septiembre 2026."""
    for lista_orden in ("3", "5", "10", "15"):
        inp = _inp(lista_orden=lista_orden, pares={})
        assert _lista_ves_activa(inp) == "5"
        assert _lista_usd_activa(inp) == "8"


def test_un_par_que_apunta_a_una_lista_no_configurada_se_ignora() -> None:
    inp = _inp(lista_orden="15", pares={"15": "77", "77": "15"})
    assert _lista_ves_activa(inp) == "15"
    assert _lista_usd_activa(inp) == "8"


# --- La ventana histórica gana ----------------------------------------------


def test_la_ventana_historica_gana_sobre_el_pareo() -> None:
    """Son órdenes anteriores a que existieran estas listas."""
    inp = _inp(lista_orden="15", historica=True)
    assert _lista_usd_activa(inp) == "7"


def test_una_lista_configurada_pero_sin_par_no_manda_sobre_la_global() -> None:
    """La guarda que evita reescribir historia. Las listas 3, 4 y 9 figuran
    entre las VES configuradas, pero no están pareadas: sin esta condición
    una orden nacida en la 3 pasaría a usar la 3 como referencia solo por
    estar en la configuración, moviendo el teórico de 340 de las 793
    órdenes que el usuario decidió dejar quietas."""
    inp = _inp(lista_orden="3")
    assert "3" in inp.valid_ves
    assert "3" not in inp.pares_listas
    assert _lista_ves_activa(inp) == "5"
