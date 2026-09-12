"""Los ids de lista se leen por coma, no dígito por dígito.

``get_ui_pricelist_ids`` partía un valor sin comas carácter por carácter.
Fue inofensivo mientras todas las listas de Odoo tuvieron un dígito, y dejó
de serlo el 1 de septiembre de 2026, cuando la instancia estrenó las listas
10 a 19: configurar una sola lista nueva ("14") se leía como dos listas
viejas equivocadas ([1, 4]).

Importa también el ORDEN. ``_lista_ves_activa``/``_lista_usd_activa`` toman
el PRIMER id de cada lista para calcular el teórico, así que agregar una
lista nueva adelante cambiaría el teórico de todas las órdenes, incluidas
las viejas. Se agregan al final: eso corrige la clasificación de nacimiento
(``es_lista_usd_nacimiento``) sin mover ningún teórico.
"""

from __future__ import annotations

from types import SimpleNamespace

from cxc.engine.discounts import _lista_usd_activa, _lista_ves_activa
from cxc.web.app import get_ui_pricelist_ids


class _Repo:
    def __init__(self, usd: str, ves: str):
        self._cfg = {"valid_pricelists_usd": usd, "valid_pricelists_ves": ves}

    def all_config(self):
        return dict(self._cfg)


def test_un_id_de_dos_digitos_es_una_lista_no_dos() -> None:
    usd, ves = get_ui_pricelist_ids(_Repo("14", "15"))
    assert usd == [14]
    assert ves == [15]


def test_varios_ids_separados_por_coma() -> None:
    usd, ves = get_ui_pricelist_ids(_Repo("8,7,11,13,14,17,18", "5,3,4,10,12,15,16,19"))
    assert usd == [8, 7, 11, 13, 14, 17, 18]
    assert ves == [5, 3, 4, 10, 12, 15, 16, 19]


def test_tolera_espacios_y_entradas_vacias() -> None:
    usd, ves = get_ui_pricelist_ids(_Repo(" 8 , , 11 ", "5,"))
    assert usd == [8, 11]
    assert ves == [5]


def test_las_listas_de_un_digito_siguen_leyendose_igual() -> None:
    """Los valores que ya estaban en producción antes del arreglo."""
    usd, ves = get_ui_pricelist_ids(_Repo("8,7", "5,3,4"))
    assert usd == [8, 7]
    assert ves == [5, 3, 4]


def test_agregar_listas_al_final_no_cambia_el_teorico() -> None:
    """El teórico sale del PRIMER id de cada lista. Agregar las de
    septiembre 2026 al final deja intactos los 793 teóricos existentes."""

    def _inp(valid_ves, valid_usd):
        # Orden nacida en la lista 5, sin pareo configurado: el caso de las
        # 793 ordenes que ya tenian teorico antes de septiembre 2026.
        return SimpleNamespace(
            valid_ves=valid_ves,
            valid_usd=valid_usd,
            orden_es_historica=False,
            orden=SimpleNamespace(lista_precios="5"),
            pares_listas={},
        )

    antes = _inp(("5", "3", "4"), ("8", "7"))
    despues = _inp(
        ("5", "3", "4", "10", "12", "15", "16", "19"),
        ("8", "7", "11", "13", "14", "17", "18"),
    )
    assert _lista_ves_activa(antes) == _lista_ves_activa(despues) == "5"
    assert _lista_usd_activa(antes) == _lista_usd_activa(despues) == "8"


# --- la decisión del 11-sep-2026: el mapeo unificado manda ---------------------


def test_listas_configuradas_lee_del_MAPEO_y_no_de_las_claves() -> None:
    """Cinco sitios decidían con qué lista se valora un teórico leyendo de dos fuentes.

    Tres leían el mapeo unificado y dos las claves `valid_pricelists_*`, y las dos
    fuentes daban distinto —USD=11/BCV=10 contra USD=4/BCV=5, estas últimas archivadas—.
    El usuario eligió el mapeo. Este test fija que el lector nuevo sale de ahí y que las
    claves, aunque digan otra cosa, no cuentan.
    """
    from unittest.mock import patch

    from cxc.web.app import listas_configuradas

    repo = _Repo("4", "5")  # las claves dicen las archivadas
    with patch(
        "cxc.web.app.get_valid_pricelists_usd_and_ves", return_value=(["7", "8", "11"], ["3", "10"])
    ):
        usd, ves = listas_configuradas(repo)
    assert usd == [7, 8, 11], "del mapeo, no el [4] de las claves"
    assert ves == [3, 10]


def test_listas_configuradas_devuelve_enteros_y_saltea_lo_que_no_es_un_id() -> None:
    """`_get_pricelist_items_fixed` y los conjuntos de ids esperan enteros; el mapeo
    guarda texto, y un valor raro no puede reventar el reporte de saldos."""
    from unittest.mock import patch

    from cxc.web.app import listas_configuradas

    with patch(
        "cxc.web.app.get_valid_pricelists_usd_and_ves",
        return_value=([" 11 ", "", "x", "13"], ["10", None]),
    ):
        usd, ves = listas_configuradas(_Repo("", ""))
    assert usd == [11, 13]
    assert ves == [10]


def test_ninguna_pantalla_lee_ya_las_claves_directamente() -> None:
    """La guarda de la decisión: si alguien vuelve a leer `get_ui_pricelist_ids` desde un
    endpoint, dos pantallas pueden volver a valorar con listas distintas."""
    from pathlib import Path

    fuente = Path("src/cxc/web/app.py").read_text(encoding="utf-8")
    llamadas = [
        ln
        for ln in fuente.split("\n")
        if "get_ui_pricelist_ids(" in ln and "def get_ui_pricelist_ids" not in ln
    ]
    assert llamadas == [], f"las claves volvieron a leerse desde app.py: {llamadas}"
    llamadas_nuevas = [
        ln for ln in fuente.split("\n")
        if "listas_configuradas(repo)" in ln and "def listas_configuradas" not in ln
    ]
    assert len(llamadas_nuevas) == 4, "los cuatro sitios pasan por el mapeo"
