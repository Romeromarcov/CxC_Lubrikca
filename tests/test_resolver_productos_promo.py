"""``_resolver_productos_promo`` -- normaliza el campo ``productos`` de una

regla de obsequio/promoción a producto_id (``product.product``, el mismo
espacio que ``LineaOrden.producto`` que el motor de descuentos compara),
sin importar si el usuario seleccionó código de catálogo, producto_id
directo, o algo que no matchea nada (mejor esfuerzo).

Bug real (agosto 2026, orden S00679): las 2 reglas de obsequio activas en
producción tenían ``productos`` guardado como código de catálogo
("0761, 0561") o incluso un código con el cero inicial recortado ("881")
-- ninguno de los dos coincidía jamás con ``LineaOrden.producto``, así
que la regla nunca tuvo efecto.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from cxc.models import Producto
from cxc.web.app import _resolver_productos_promo


def _catalogo_mock(productos: list[Producto]) -> MagicMock:
    repo = MagicMock()
    repo.all_catalogo.return_value = productos
    return repo


def _producto(producto_id: str, codigo: str, nombre: str = "x") -> Producto:
    return Producto(
        producto_id=producto_id,
        codigo=codigo,
        nombre=nombre,
        marca="",
        volumen=Decimal("0"),
        peso=Decimal("0"),
    )


def test_resuelve_codigo_de_catalogo_a_producto_id() -> None:
    repo = _catalogo_mock([_producto("1033", "0761"), _producto("1022", "0561")])
    assert _resolver_productos_promo("0761, 0561", repo) == "1033,1022"


def test_producto_id_ya_valido_se_mantiene_tal_cual() -> None:
    repo = _catalogo_mock([_producto("1033", "0761")])
    assert _resolver_productos_promo("1033", repo) == "1033"


def test_resuelve_nombre_completo_a_producto_id() -> None:
    """Defensa adicional (agosto 2026): un navegador con app.js viejo en

    caché (el ``?v=`` de index.html no se había actualizado el mismo día
    del fix) siguió enviando el NOMBRE completo del producto al editar
    una regla ya existente -- se resuelve igual que el código."""
    repo = _catalogo_mock([_producto("1033", "0761", "LIGA PARA FRENOS DOT3 (1x12)")])
    assert _resolver_productos_promo("LIGA PARA FRENOS DOT3 (1x12)", repo) == "1033"


def test_token_sin_match_se_deja_tal_cual_mejor_esfuerzo() -> None:
    repo = _catalogo_mock([_producto("1033", "0761")])
    assert _resolver_productos_promo("881", repo) == "881"


def test_lista_vacia_da_string_vacio() -> None:
    repo = _catalogo_mock([])
    assert _resolver_productos_promo("", repo) == ""
