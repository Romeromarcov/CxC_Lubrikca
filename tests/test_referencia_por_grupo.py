"""Qué lista de precios representa a cada grupo se configura, no se deduce.

En septiembre de 2026 varias listas quedaron vigentes a la vez dentro del
mismo grupo: "Industrial 3 %" e "Industrial 4 %" en industrial, y "Pago",
"Foránea" y "Maturín" en comercial. ``get_pricelist_vigente_por_grupo``
devuelve UNA por grupo, y sin una elección explícita quedaba la última que
devolviera el diccionario -- la de id más alto por accidente, no por
criterio.

El criterio con el que se eligieron las actuales es del usuario (la
industrial más cara; en comercial la de Caracas, que es la que no dice
Maturín ni Foránea), pero **ese criterio no vive en el código**: se aplicó
una vez sobre los datos reales y lo que quedó guardado son los ids. Fue un
pedido explícito -- "deja todo eso configurable para que en el futuro no
dependa solo de cruzar por nombres y no esté hardcodeado en el sistema".
Cuando Odoo estrene listas o cambien los nombres, se ajusta la
configuración y no hay ninguna regla de texto que tocar.
"""

from __future__ import annotations

import json

import pytest

from cxc.web.app import get_pricelist_vigente_por_grupo

_MAPEO = {
    "10": {"moneda": "ves", "categoria": "comercial", "vigente": True},
    "12": {"moneda": "ves", "categoria": "comercial", "vigente": True},
    "19": {"moneda": "ves", "categoria": "comercial", "vigente": True},
    "15": {"moneda": "ves", "categoria": "industrial", "vigente": True},
    "16": {"moneda": "ves", "categoria": "industrial", "vigente": True},
    "5": {"moneda": "ves", "categoria": "comercial", "vigente": False},
}


class _Repo:
    def __init__(self, referencias=None):
        self._cfg = {}
        if referencias is not None:
            self._cfg["pricelist_referencia_por_grupo"] = json.dumps(referencias)

    def get_config(self, k):
        return self._cfg.get(k)


@pytest.fixture(autouse=True)
def _mapeo_fijo(monkeypatch):
    import cxc.web.app as app

    monkeypatch.setattr(app, "get_pricelist_mapeo", lambda repo=None: dict(_MAPEO))


def test_manda_la_referencia_configurada() -> None:
    """Caracas (10) sobre Foránea (12) y Maturín (19), aunque 19 sea mayor."""
    r = get_pricelist_vigente_por_grupo(_Repo({"comercial_ves": "10", "industrial_ves": "16"}))
    assert r["comercial_ves"] == "10"
    assert r["industrial_ves"] == "16"


def test_un_grupo_sin_referencia_cae_a_cualquier_lista_vigente() -> None:
    """Respaldo para que un grupo sin configurar no quede sin nada."""
    r = get_pricelist_vigente_por_grupo(_Repo({"comercial_ves": "10"}))
    assert r["industrial_ves"] in ("15", "16")


def test_una_referencia_que_dejo_de_estar_vigente_se_ignora() -> None:
    """La 5 quedó archivada en Odoo: no puede seguir representando al grupo."""
    r = get_pricelist_vigente_por_grupo(_Repo({"comercial_ves": "5"}))
    assert r["comercial_ves"] in ("10", "12", "19")


def test_una_referencia_a_una_lista_inexistente_se_ignora() -> None:
    r = get_pricelist_vigente_por_grupo(_Repo({"comercial_ves": "999"}))
    assert r["comercial_ves"] in ("10", "12", "19")


def test_un_grupo_sin_ninguna_lista_vigente_queda_en_nada() -> None:
    assert get_pricelist_vigente_por_grupo(_Repo({}))["comercial_usd"] is None


def test_sin_configuracion_no_revienta() -> None:
    r = get_pricelist_vigente_por_grupo(_Repo(None))
    assert r["comercial_ves"] in ("10", "12", "19")
    assert get_pricelist_vigente_por_grupo(None)["comercial_ves"] in ("10", "12", "19")


def test_una_configuracion_corrupta_no_revienta() -> None:
    class _Roto:
        def get_config(self, k):
            return "{no es json"

    assert get_pricelist_vigente_por_grupo(_Roto())["comercial_ves"] in ("10", "12", "19")
