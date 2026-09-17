"""Las reglas fijan a qué NO aplican, no solo a qué sí.

Criterio del usuario (septiembre 2026), textual: "cuando dice que aplica a
las listas VES, quiere decir que nunca debe aplicar a orden nacida con
lista USD, porque aplicaría dos veces el 35%, pero no que aplique a todas
las órdenes en lista VES, para eso el motor debe evaluar cada caso
concreto". Y: "para evitar problemas hacia el futuro podemos modificar los
formularios para incluir los tipos de lista a los que NO aplica ese
descuento, en lugar de establecerlo como que aplica a todas las órdenes de
una lista. Igualmente con las monedas".

La diferencia importa: decir "aplica a X" obliga a enumerar todo lo
permitido, y una lista nueva entra sin querer. Decir "nunca a Y" fija la
prohibición, que es lo que de verdad protege.

Lo destapó el caso S00010 (TERA): el motor le sugería el diferencial del
35 % sobre un precio que ya estaba en USD, o sea el 35 % dos veces. Y no
se podía arreglar clasificando la lista, porque el usuario aclaró que las
listas viejas "se usaron para VES y USD indistintamente en el pasado".

La migración deja ``listas_excluidas = 'LISTAS_USD'`` en las reglas de
diferencial cambiario.
"""

from __future__ import annotations

from cxc.engine.effective_dating import _excluida_por_lista, _excluida_por_moneda

_VES = ["3", "4", "5", "10", "15"]
_USD = ["7", "8", "11", "14"]


def _excluida(reglas: str, lista: str) -> bool:
    return _excluida_por_lista(reglas, lista, _VES, _USD)


def test_el_diferencial_nunca_toca_una_orden_nacida_en_lista_usd() -> None:
    """El caso TERA: el precio ya está en USD, el 35 % ya está dado."""
    assert _excluida("LISTAS_USD", "8") is True
    assert _excluida("LISTAS_USD", "11") is True


def test_pero_no_obliga_a_aplicarlo_a_todas_las_ves() -> None:
    """Excluir USD no es lo mismo que "aplica a todas las VES": la
    exclusión solo dice dónde NO, y el motor sigue evaluando cada caso."""
    assert _excluida("LISTAS_USD", "5") is False


def test_se_puede_excluir_una_lista_puntual_por_id() -> None:
    """Para las listas viejas que se usaron indistintamente."""
    assert _excluida("3,4", "3") is True
    assert _excluida("3,4", "5") is False


def test_sin_exclusion_configurada_no_excluye_nada() -> None:
    assert _excluida("", "8") is False
    assert _excluida("LISTAS_USD", "") is False


def test_una_lista_desconocida_no_cae_en_ningun_grupo() -> None:
    """Una lista nueva sin mapear no se excluye por accidente."""
    assert _excluida("LISTAS_USD", "99") is False
    assert _excluida("LISTAS_VES", "99") is False


def test_excluir_ves_es_simetrico() -> None:
    assert _excluida("LISTAS_VES", "5") is True
    assert _excluida("LISTAS_VES", "8") is False


def test_la_moneda_del_pago_tambien_se_puede_prohibir() -> None:
    """"Igualmente con las monedas"."""
    assert _excluida_por_moneda("USD", "USD") is True
    assert _excluida_por_moneda("USD", "VES") is False
    assert _excluida_por_moneda("", "USD") is False


def test_la_moneda_se_compara_sin_importar_mayusculas() -> None:
    assert _excluida_por_moneda("usd", "USD") is True
    assert _excluida_por_moneda("USD", "usd") is True
