"""Un teórico en cero significa "no pude calcular", nunca "nada que pagar".

Bug real reportado por el usuario (septiembre 2026): en la Bandeja 1 de
Facturación aparecían órdenes con 0 % de descuento y **sin un solo pago**
-- S00368 "Mini Market Las Mercedes" (2.128,48) y S00708 "Carlos Ruiz"
(1.041,64), entre 6 órdenes por 8.641,52 en total.

La cadena que lo producía:

  1. El motor no resuelve precio para ningún producto de la orden, así que
     el teórico Y el precio base quedan los dos en cero.
  2. ``_sin_datos_teorico`` exigía que la base fuera MAYOR que cero para
     declarar "sin datos". Con las dos en cero la condición no se cumplía y
     la orden se daba por "con datos".
  3. El objetivo de pago quedaba entonces en 0, y ``_estado_pago`` declara
     "pagada" cualquier objetivo de 0 (``target <= _EPS_PAGO``) -- lo cual
     es correcto para una factura cubierta por una NC, pero no para un
     objetivo que quedó en cero porque no se pudo calcular.
  4. El árbol de CxC la sacaba de cobranza y la enrutaba a facturar.

Una base en cero es la misma señal de "no pude calcular" que un teórico en
cero, así que deja de ser una condición extra.

El caso de S00708 muestra por qué pasa: sus dos líneas son un tambor sin
precio en ninguna lista (``usa_fallback`` en True) y una línea de descuento
("Descontar 3,00 %", precio -27,77), que no es un producto.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cxc.web.app import sin_datos_teorico


def _fila(**kw):
    return SimpleNamespace(**kw)


def test_teorico_y_base_en_cero_es_sin_datos() -> None:
    """El caso exacto de S00368 y S00708: el motor no resolvió nada."""
    assert sin_datos_teorico(_fila(), 0.0, 0.0) is True


def test_teorico_en_cero_con_base_calculada_sigue_siendo_sin_datos() -> None:
    """Lo que la guarda ya cubría antes del arreglo."""
    assert sin_datos_teorico(_fila(), 0.0, 500.0) is True


def test_sin_fila_de_teorico_es_sin_datos() -> None:
    assert sin_datos_teorico(None, 100.0, 100.0) is True


def test_teorico_ausente_es_sin_datos() -> None:
    assert sin_datos_teorico(_fila(), None, 100.0) is True


@pytest.mark.parametrize("base", [0.0, 500.0])
def test_un_teorico_real_tiene_datos_venga_o_no_la_base(base: float) -> None:
    """La base dejó de ser condición: lo que manda es el teórico."""
    assert sin_datos_teorico(_fila(), 2421.70, base) is False


def test_el_umbral_es_medio_centavo() -> None:
    assert sin_datos_teorico(_fila(), 0.005, 0.0) is True
    assert sin_datos_teorico(_fila(), 0.006, 0.0) is False
