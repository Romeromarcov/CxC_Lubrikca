"""Cada bandeja tiene que vivir DENTRO de la pestaña que le toca.

Bug real que el usuario reportó (septiembre 2026): tras mover "En Proceso
de Pago" de Auditoría a Cobranza, seguía apareciendo en Auditoría. El
bloque había quedado como hijo directo de ``<main>``, fuera de todo
``.tab-panel``. Como la visibilidad de las pestañas es puramente CSS
(``.tab-panel { display:none }`` / ``.tab-panel.active { display:block }``),
un panel que no está dentro de ninguna se muestra en TODAS las páginas.

La verificación que dejé pasar el bug miraba, sobre el texto del HTML,
qué ``<div id="tab-...">`` empezaba antes del bloque. Eso responde "en
qué parte del archivo está", no "de quién es hijo": un bloque colocado
justo después del cierre de una pestaña da la misma respuesta que uno
colocado justo antes. Este test parsea el HTML y sube por la cadena real
de ancestros, que es la única pregunta que importa.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

import pytest

_HTML = Path(__file__).resolve().parents[1] / "src" / "cxc" / "web" / "static" / "index.html"

_VOID = {
    "br", "img", "hr", "input", "meta", "link", "source",
    "col", "area", "base", "embed", "param", "track", "wbr",
}

# Cada bandeja y la pestaña a la que pertenece.
_ESPERADO = {
    "bandeja-en-proceso-de-pago-table-body": "tab-cobranza",
    "bandeja1-table-body": "tab-facturacion",
    "auditoria-ventas-alertas-body": "tab-auditoria",
    "discrepancias-aceptadas-table-body": "tab-auditoria",
    "auditoria-descuentos-body": "tab-auditoria",
    "discrepancias-facturas-table-body": "tab-auditoria",
    "pagos-residual-table-body": "tab-auditoria",
    "ventas-table-body": "tab-ventas",
}


class _Ancestros(HTMLParser):
    def __init__(self, buscados: set[str]) -> None:
        super().__init__(convert_charrefs=True)
        self._pila: list[tuple[str, str]] = []
        self._buscados = buscados
        self.pestana: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        atributos = dict(attrs)
        elem_id = atributos.get("id") or ""
        if elem_id in self._buscados:
            self.pestana[elem_id] = next(
                (i for _, i in reversed(self._pila) if i.startswith("tab-")),
                "FUERA DE TODA PESTAÑA",
            )
        if tag not in _VOID:
            self._pila.append((tag, elem_id))

    def handle_endtag(self, tag: str) -> None:
        for k in range(len(self._pila) - 1, -1, -1):
            if self._pila[k][0] == tag:
                del self._pila[k:]
                break


def _mapa() -> dict[str, str]:
    p = _Ancestros(set(_ESPERADO))
    p.feed(_HTML.read_text(encoding="utf-8"))
    return p.pestana


@pytest.mark.parametrize(("tabla", "pestana"), sorted(_ESPERADO.items()))
def test_cada_bandeja_esta_dentro_de_su_pestana(tabla: str, pestana: str) -> None:
    real = _mapa().get(tabla)
    assert real is not None, f"{tabla} ya no existe en index.html"
    assert real == pestana, (
        f"{tabla} está en {real} y debería estar en {pestana}. "
        "Un panel fuera de su .tab-panel se muestra en TODAS las páginas."
    )


def test_ninguna_bandeja_quedo_huerfana() -> None:
    """El caso exacto del bug: hijo suelto de <main>."""
    huerfanas = [t for t, p in _mapa().items() if not p.startswith("tab-")]
    assert not huerfanas, f"Bandejas fuera de toda pestaña: {huerfanas}"
