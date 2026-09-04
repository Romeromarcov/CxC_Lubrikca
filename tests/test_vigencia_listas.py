"""La vigencia de las listas de precio la manda Odoo, no un tilde local.

Decisión del usuario (septiembre 2026): "la vigencia está en Odoo". El
campo ``vigente`` del mapeo unificado era una marca manual que se ponía una
vez y quedaba a la deriva. Cuando Odoo archivó las listas viejas (3, 4, 5,
7, 8, 9) y estrenó las de septiembre (10 a 19), el mapeo siguió declarando
vigentes a la 5, la 8 y la 9 -- listas que ya nadie usa.

Alimenta solo la tabla comparativa de Inventario. El motor de descuentos no
mira este campo: resuelve precios contra las listas configuradas como
VES/USD sin importar su vigencia.
"""

from __future__ import annotations

from cxc.web.app import sincronizar_vigencia_listas


class _Repo:
    def __init__(self, mapeo):
        self.cfg = {}
        self._mapeo = mapeo

    def get_config(self, k):
        return self.cfg.get(k)

    def set_config(self, k, v):
        self.cfg[k] = v


def _execute(activas: dict[int, bool]):
    def execute(model, method, args, kwargs=None):
        if model == "product.pricelist":
            return [{"id": i, "active": a} for i, a in activas.items()]
        return []

    return execute


def _preparar(monkeypatch, mapeo):
    """El mapeo vive detrás de un caché de proceso y un archivo JSON."""
    import cxc.web.app as app

    guardado = {}
    monkeypatch.setattr(app, "get_pricelist_mapeo", lambda repo=None: dict(mapeo))
    monkeypatch.setattr(
        app, "set_pricelist_mapeo", lambda m, repo=None: guardado.update({"mapeo": m})
    )
    return guardado


_MAPEO = {
    "5": {"moneda": "ves", "categoria": "comercial", "vigente": True},
    "8": {"moneda": "usd", "categoria": "comercial", "vigente": True},
    "9": {"moneda": "ves", "categoria": "industrial", "vigente": True},
    "15": {"moneda": "ves", "categoria": "industrial", "vigente": False},
}


def test_una_lista_archivada_en_odoo_deja_de_estar_vigente(monkeypatch) -> None:
    """El caso real de septiembre 2026."""
    g = _preparar(monkeypatch, _MAPEO)
    n = sincronizar_vigencia_listas(
        _Repo(_MAPEO), _execute({5: False, 8: False, 9: False, 15: True})
    )
    assert n == 4
    m = g["mapeo"]
    assert [m[k]["vigente"] for k in ("5", "8", "9", "15")] == [False, False, False, True]


def test_no_toca_la_moneda_ni_la_categoria(monkeypatch) -> None:
    g = _preparar(monkeypatch, _MAPEO)
    sincronizar_vigencia_listas(_Repo(_MAPEO), _execute({5: False, 8: False, 9: False, 15: True}))
    assert g["mapeo"]["9"]["categoria"] == "industrial"
    assert g["mapeo"]["9"]["moneda"] == "ves"


def test_sin_cambios_no_reescribe_nada(monkeypatch) -> None:
    g = _preparar(monkeypatch, _MAPEO)
    assert sincronizar_vigencia_listas(_Repo(_MAPEO), _execute({5: True, 8: True, 9: True})) == 0
    assert g == {}


def test_una_lista_que_odoo_no_reporta_se_deja_como_esta(monkeypatch) -> None:
    g = _preparar(monkeypatch, _MAPEO)
    sincronizar_vigencia_listas(_Repo(_MAPEO), _execute({15: True}))
    assert g["mapeo"]["5"]["vigente"] is True


def test_sin_odoo_no_revienta(monkeypatch) -> None:
    _preparar(monkeypatch, _MAPEO)
    assert sincronizar_vigencia_listas(_Repo(_MAPEO), None) == 0
    assert sincronizar_vigencia_listas(_Repo(_MAPEO), _execute({})) == 0


def test_un_error_de_odoo_no_tumba_el_ciclo(monkeypatch) -> None:
    _preparar(monkeypatch, _MAPEO)

    def revienta(*a, **k):
        raise RuntimeError("Odoo caído")

    assert sincronizar_vigencia_listas(_Repo(_MAPEO), revienta) == 0
