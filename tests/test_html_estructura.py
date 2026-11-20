"""Pruebas anti-regresión de estructura HTML y clases CSS globales del panel."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "cxc" / "web" / "static"

_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

_TABS_ESPERADOS = (
    "tab-inicio",
    "tab-auditoria",
    "tab-pagos",
    "tab-ventas",
    "tab-compras",
    "tab-reportes",
    "tab-facturacion",
    "tab-inventario",
    "tab-contactos",
    "tab-config",
)


class _HtmlAuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, int, str]] = []
        self.errors: list[str] = []
        self.ids: dict[str, int] = {}
        self.tab_ancestors: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k: (v or "") for k, v in attrs}
        line = self.getpos()[0]
        el_id = attr_map.get("id")
        if el_id:
            if el_id in self.ids:
                self.errors.append(f"ID duplicado '{el_id}' en líneas {self.ids[el_id]} y {line}")
            self.ids[el_id] = line
            if el_id.startswith("tab-"):
                self.tab_ancestors[el_id] = [t for t, _, _ in self.stack]
        if tag not in _VOID_TAGS:
            self.stack.append((tag, line, el_id or ""))

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        line = self.getpos()[0]
        if not self.stack:
            self.errors.append(f"Cierre extra </{tag}> en línea {line}")
            return
        if self.stack[-1][0] == tag:
            self.stack.pop()
            return
        for idx in range(len(self.stack) - 1, -1, -1):
            if self.stack[idx][0] == tag:
                self.errors.append(
                    f"Etiqueta mal cerrada </{tag}> en línea {line}; "
                    f"sin cerrar dentro: {self.stack[idx + 1 :]}"
                )
                self.stack = self.stack[:idx]
                return
        self.errors.append(f"Cierre huérfano </{tag}> en línea {line}; tope={self.stack[-1]}")


def test_index_html_anidamiento_y_ids_unicos() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    parser = _HtmlAuditParser()
    parser.feed(html)

    assert parser.errors == []
    assert parser.stack == []
    for tab_id in _TABS_ESPERADOS:
        assert tab_id in parser.tab_ancestors, f"Falta {tab_id} en index.html"
        assert parser.tab_ancestors[tab_id] == ["html", "body", "div", "div", "main"], (
            f"{tab_id} quedó fuera de <main class='main-content'>: "
            f"{parser.tab_ancestors[tab_id]}"
        )


def test_styles_css_define_clases_globales_de_botones_modales_y_layout() -> None:
    css = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    for selector in (
        ".btn-primary",
        ".btn-secondary",
        ".modal-backdrop",
        ".modal-card",
        ".badge",
        ".tab-panel",
        ".config-subpage-hidden",
        "overflow-x: clip;",
    ):
        assert selector in css, f"Falta {selector} en styles.css"
