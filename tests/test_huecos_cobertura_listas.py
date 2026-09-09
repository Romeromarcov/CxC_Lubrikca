"""Ningún período puede quedarse sin lista de referencia.

Lo pidió el usuario (septiembre 2026): "revisa que no queden periodos
vacios, por ejemplo en USD no hay nada entre el 1 y el 6 de abril". Ese
hueco era real -- la lista 7 cerraba el 01-abr y la 8 abría el 06-abr --
y una orden de esos días se quedaba sin referencia USD. Se cerró
extendiendo cada lista hasta el día antes de que arranque la siguiente de
su grupo, y este detector queda en la pantalla de Configuración para que
no haya que buscarlos a mano.

Dos criterios que evitan avisos falsos:

  · solo se miran grupos que EXISTEN. No tiene sentido avisar que no había
    lista industrial en marzo si tampoco había órdenes industriales:
    verificado contra producción, 0 órdenes de lista industrial antes del
    11-ago.
  · una lista sin fecha de cierre cubre todo lo que sigue.

La Lista Histórica de Auditoría entra al mapeo con su período (20-feb a
12-mar) aunque no sea una pricelist de Odoo: cubre esos días para el
análisis, y el motor la saltea al resolver precios porque su id no es
numérico -- esas órdenes las atiende ``orden_es_historica``, que toma el
precio VES de ``precio_bcv_euro`` y el par USD de la lista 7.
"""

from __future__ import annotations

from cxc.web.app import huecos_de_cobertura


def _lista(moneda, categoria, desde, hasta=""):
    return {"moneda": moneda, "categoria": categoria, "desde": desde, "hasta": hasta}


def test_detecta_el_hueco_real_de_abril() -> None:
    """El que encontró el usuario: la 7 cerraba el 01-abr, la 8 abría el 06."""
    mapeo = {
        "7": _lista("usd", "comercial", "2026-02-26", "2026-04-01"),
        "8": _lista("usd", "comercial", "2026-04-06", "2026-08-31"),
    }
    huecos = huecos_de_cobertura(mapeo)
    assert len(huecos) == 1
    assert (huecos[0]["desde"], huecos[0]["hasta"]) == ("2026-04-02", "2026-04-05")
    assert huecos[0]["moneda"] == "USD"


def test_periodos_contiguos_no_son_hueco() -> None:
    """Cerrar el día antes de que arranque la siguiente es cobertura."""
    mapeo = {
        "7": _lista("usd", "comercial", "2026-02-26", "2026-04-05"),
        "8": _lista("usd", "comercial", "2026-04-06", ""),
    }
    assert huecos_de_cobertura(mapeo) == []


def test_una_lista_sin_cierre_cubre_lo_que_sigue() -> None:
    mapeo = {"8": _lista("usd", "comercial", "2026-04-06", "")}
    assert huecos_de_cobertura(mapeo) == []


def test_grupos_distintos_no_se_tapan_entre_si() -> None:
    """Una lista industrial no cubre el hueco de una comercial."""
    mapeo = {
        "7": _lista("usd", "comercial", "2026-02-26", "2026-04-01"),
        "8": _lista("usd", "comercial", "2026-04-06", ""),
        "14": _lista("usd", "industrial", "2026-04-01", ""),
    }
    assert len(huecos_de_cobertura(mapeo)) == 1


def test_no_avisa_por_grupos_que_no_existen() -> None:
    """Con una sola lista no hay entre-medio que reportar."""
    assert huecos_de_cobertura({"5": _lista("ves", "comercial", "2026-04-24", "2026-09-04")}) == []


def test_una_lista_sin_fecha_de_inicio_se_ignora() -> None:
    """A medio configurar no puede fabricar ni tapar un hueco."""
    mapeo = {
        "7": _lista("usd", "comercial", "2026-02-26", "2026-04-01"),
        "8": _lista("usd", "comercial", "2026-04-06", ""),
        "12": _lista("usd", "comercial", "", ""),
    }
    assert len(huecos_de_cobertura(mapeo)) == 1


def test_los_huecos_salen_ordenados_por_fecha() -> None:
    mapeo = {
        "3": _lista("ves", "comercial", "2026-03-03", "2026-05-01"),
        "5": _lista("ves", "comercial", "2026-06-01", ""),
        "7": _lista("usd", "comercial", "2026-02-26", "2026-04-01"),
        "8": _lista("usd", "comercial", "2026-04-06", ""),
    }
    huecos = huecos_de_cobertura(mapeo)
    assert [h["desde"] for h in huecos] == ["2026-04-02", "2026-05-02"]


def test_un_mapeo_vacio_no_revienta() -> None:
    assert huecos_de_cobertura({}) == []
