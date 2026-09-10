"""Cuál lista de precios es la primaria, ahora en su propio módulo (Fase 2.4).

Tercera pieza extraída, con la misma medición A/B que las dos anteriores: el
nombre viejo (``app.py::_primer_id_activo``) y el nuevo tienen que dar
exactamente lo mismo sobre los mismos casos.

Y con algo más, que es la razón de haber elegido ésta: acá vive un defecto
medido. Tres de los cuatro sitios que arman un ``OdooPriceResolver`` pasan por la
guarda y uno no, y en la copia de producción eso son 789 órdenes valoradas
distinto según qué página las mire. Estos tests fijan la guarda y el diagnóstico
que esa decisión necesita.
"""

from __future__ import annotations

import pytest

from cxc.engine.listas import (
    DiagnosticoEleccion,
    diagnostico_de_eleccion,
    primera_activa,
    primero_crudo,
)

# (ids configurados, activos, esperado)
CASOS = [
    # El caso feliz: el primero está activo y gana.
    ([10, 11], {10, 11}, 10),
    # El caso del hallazgo: el primero está archivado, se salta.
    ([3, 4, 5, 10], {10, 12}, 10),
    ([7, 8, 11], {11, 13}, 11),
    # Ninguno activo: se preserva el comportamiento viejo, el primero.
    ([3, 7], set(), 3),
    ([3, 7], {99}, 3),
    # Uno solo, activo y archivado.
    ([10], {10}, 10),
    ([3], set(), 3),
    # Vacío: no hay respuesta.
    ([], {10}, None),
    ([], set(), None),
    # El orden de la configuración manda, no el numérico.
    ([19, 10, 12], {10, 12, 19}, 19),
    ([19, 10, 12], {10, 12}, 10),
]


@pytest.mark.parametrize("ids,activos,esperado", CASOS)
def test_la_primaria_es_la_esperada(ids, activos, esperado) -> None:
    assert primera_activa(ids, activos) == esperado


@pytest.mark.parametrize("ids,activos,_esperado", CASOS)
def test_el_nombre_viejo_da_exactamente_lo_mismo(ids, activos, _esperado) -> None:
    """La medición A/B de esta pieza.

    ``_primer_id_activo`` sigue existiendo y sigue siendo quien le pregunta a
    Odoo; lo que se movió es la decisión. Un ``execute`` de mentira devuelve los
    activos y las dos rutas tienen que coincidir.
    """
    from cxc.web.app import _primer_id_activo

    def execute(modelo, metodo, args, kwargs):
        assert modelo == "product.pricelist"
        pedidos = args[0][0][2]
        return [{"id": i, "active": i in activos} for i in pedidos]

    assert _primer_id_activo(execute, ids) == primera_activa(ids, activos)


def test_si_odoo_no_contesta_se_elige_el_primero_y_no_se_revienta() -> None:
    """Preservado a propósito: sin respuesta de Odoo no hay respuesta mejor.

    Devolver ``None`` acá dejaría la pantalla sin precio en vez de con un precio
    viejo, y eso es un cambio de montos disfrazado de corrección.
    """
    from cxc.web.app import _primer_id_activo

    def execute_roto(*_a, **_k):
        raise RuntimeError("Odoo no contesta")

    assert _primer_id_activo(execute_roto, [3, 10]) == 3
    assert _primer_id_activo(execute_roto, []) is None


def test_las_dos_elecciones_coinciden_cuando_el_primero_esta_activo() -> None:
    d = diagnostico_de_eleccion("BCV", [10, 12, 19], {10, 12, 19})
    assert d.coinciden
    assert d.con_guarda == d.sin_guarda == 10
    assert not d.elegida_esta_archivada
    assert "no cambia nada" in d.nota


def test_el_hallazgo_medido_queda_fijado() -> None:
    """La configuración exacta de la copia de producción, con sus dos elecciones.

    ``valid_pricelists_ves`` empieza por 3, 4, 5 y 9 —las cuatro archivadas— y la
    primera activa es la 10. Los otros tres caminos de la aplicación valoran con
    la 10; el reporte de saldos, con la 3. Medido: 789 órdenes difieren, −18,9 %
    en VES.
    """
    ves = [3, 4, 5, 9, 10, 12, 15, 16, 19]
    activas = {10, 12, 15, 16, 19, 11, 13, 14, 17, 18}
    d = diagnostico_de_eleccion("BCV", ves, activas)
    assert d.sin_guarda == 3, "el reporte de saldos toma el primero crudo"
    assert d.con_guarda == 10, "los otros tres caminos se saltan las archivadas"
    assert not d.coinciden
    assert d.elegida_esta_archivada
    assert "ARCHIVADA" in d.nota

    usd = [7, 8, 11, 13, 14, 17, 18]
    du = diagnostico_de_eleccion("USD", usd, activas)
    assert (du.sin_guarda, du.con_guarda) == (7, 11)
    assert du.elegida_esta_archivada


def test_el_diagnostico_no_elige_nada() -> None:
    """Es un instrumento, no una corrección: describe las dos opciones.

    Importa que quede escrito: si algún día este módulo empezara a *aplicar* la
    guarda en el cuarto sitio, movería el teórico de 789 órdenes, y eso es una
    decisión del usuario y no un efecto colateral de una extracción.
    """
    d = diagnostico_de_eleccion("BCV", [3, 10], {10})
    assert isinstance(d, DiagnosticoEleccion)
    assert d.con_guarda == 10 and d.sin_guarda == 3
    # Las dos siguen disponibles; el módulo no privilegia ninguna.
    assert primera_activa([3, 10], {10}) == 10
    assert primero_crudo([3, 10]) == 3


def test_sin_listas_configuradas_lo_dice_en_vez_de_inventar() -> None:
    d = diagnostico_de_eleccion("USD", [], {10})
    assert d.con_guarda is None and d.sin_guarda is None
    assert d.coinciden, "dos ausencias coinciden; no hay divergencia que reportar"
    assert not d.elegida_esta_archivada
    assert "no hay ninguna lista configurada" in d.nota


def test_los_activos_reportados_son_solo_los_configurados() -> None:
    """El diagnóstico no lista pricelists que no vienen en la configuración.

    Odoo puede tener veinte listas activas; sólo importan las que la
    configuración ofrece, porque son las únicas entre las que se elige.
    """
    d = diagnostico_de_eleccion("BCV", [3, 10], {10, 12, 19, 77})
    assert d.activos == (10,)
    assert d.ids_configurados == (3, 10)
