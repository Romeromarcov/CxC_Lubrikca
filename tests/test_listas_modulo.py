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


# --- los huecos de vigencia, y su denominador ------------------------------


def test_sin_vigencias_sembradas_dice_que_no_pudo_evaluar() -> None:
    """El hallazgo: cero huecos sobre cero listas evaluables no es «está bien».

    ``huecos_de_cobertura`` saltea toda lista sin ``desde``, así que en una base sin
    vigencias sembradas devuelve ``[]``. Un ``[]`` se lee como «no hay huecos»,
    cuando lo que pasó es que **no se pudo buscar ninguno**.

    Medido en la copia de prueba: las **16 listas del mapeo no tienen ni una
    vigencia declarada**, y el instrumento que el plan pide usar para verificar los
    períodos devolvía «ninguno» sin haber evaluado nada. Misma trampa que las dos
    partidas de tasa del balance, en otro lugar.
    """
    from cxc.engine.listas import diagnostico_de_huecos

    mapeo = {
        str(i): {"moneda": "ves", "categoria": "comercial", "desde": "", "hasta": ""}
        for i in range(16)
    }
    d = diagnostico_de_huecos(mapeo, [])
    assert d.listas_totales == 16
    assert d.listas_con_vigencia == 0
    assert d.evaluable is False
    assert "NO SE PUDO EVALUAR" in d.nota
    assert "16" in d.nota
    assert "no significa que" in d.nota


def test_con_vigencias_y_sin_huecos_lo_dice_con_el_denominador() -> None:
    from cxc.engine.listas import diagnostico_de_huecos

    mapeo = {
        "3": {
            "moneda": "ves",
            "categoria": "comercial",
            "desde": "2026-01-01",
            "hasta": "2026-06-30",
        },
        "4": {"moneda": "ves", "categoria": "comercial", "desde": "2026-07-01", "hasta": ""},
    }
    d = diagnostico_de_huecos(mapeo, [])
    assert d.evaluable is True
    assert (d.listas_con_vigencia, d.listas_totales) == (2, 2)
    assert d.grupos_evaluados == 1
    assert "Evaluadas 2 de 2" in d.nota
    assert "Sin huecos" in d.nota


def test_con_huecos_los_cuenta() -> None:
    from cxc.engine.listas import diagnostico_de_huecos

    mapeo = {
        "3": {
            "moneda": "usd",
            "categoria": "comercial",
            "desde": "2026-01-01",
            "hasta": "2026-04-01",
        },
        "8": {"moneda": "usd", "categoria": "comercial", "desde": "2026-04-06", "hasta": ""},
    }
    hueco = {
        "categoria": "comercial",
        "moneda": "USD",
        "desde": "2026-04-02",
        "hasta": "2026-04-05",
    }
    d = diagnostico_de_huecos(mapeo, [hueco])
    assert d.evaluable
    assert d.huecos == (hueco,)
    assert "1 hueco(s)" in d.nota


def test_una_lista_sin_moneda_o_sin_categoria_no_cuenta_como_evaluable() -> None:
    """El mismo criterio que usa ``huecos_de_cobertura`` para saltearla.

    Si el denominador contara listas que la función nunca mira, diría que evaluó
    más de lo que evaluó -- que es justo el error que este diagnóstico arregla.
    """
    from cxc.engine.listas import diagnostico_de_huecos

    mapeo = {
        "1": {"moneda": "", "categoria": "comercial", "desde": "2026-01-01"},
        "2": {"moneda": "ves", "categoria": "", "desde": "2026-01-01"},
        "3": {"moneda": "eur", "categoria": "comercial", "desde": "2026-01-01"},
        "4": {"moneda": "ves", "categoria": "comercial", "desde": "2026-01-01"},
    }
    d = diagnostico_de_huecos(mapeo, [])
    assert d.listas_totales == 4
    assert d.listas_con_vigencia == 1, "solo la 4 cumple los tres requisitos"


def test_un_mapeo_vacio_lo_dice_sin_confundirlo_con_sin_huecos() -> None:
    from cxc.engine.listas import diagnostico_de_huecos

    d = diagnostico_de_huecos({}, [])
    assert d.listas_totales == 0
    assert d.evaluable is False
    assert "No hay ninguna lista" in d.nota


# --- el mapa que consumen los cinco sitios (decisión del 11-sep-2026) -------


def test_el_mapa_primario_saltea_las_archivadas() -> None:
    """La guarda aplicada, con la configuración real de la copia de producción.

    ``valid_pricelists_ves`` empieza por 3, 4, 5 y 9 —las cuatro archivadas— y
    ``valid_pricelists_usd`` por 7 y 8, también archivadas. Antes de la decisión,
    el reporte de saldos y el detalle de una orden tomaban la 3 y la 7.
    """
    from cxc.engine.listas import mapa_de_listas_primarias

    usd = [7, 8, 11, 13, 14, 17, 18]
    ves = [3, 4, 5, 9, 10, 12, 15, 16, 19]
    activas = {10, 11, 12, 13, 14, 15, 16, 17, 18, 19}
    assert mapa_de_listas_primarias(usd, ves, activas) == {"USD": 11, "BCV": 10}


def test_sin_ninguna_activa_se_preserva_el_primero() -> None:
    """Mismo criterio que ``primera_activa``: sin respuesta mejor, la primera.

    Devolver el id por defecto acá diría «no hay lista» cuando sí la hay, solo
    que archivada — y valorar con el nombre lógico de respaldo es peor que
    valorar con una lista vieja que al menos existe.
    """
    from cxc.engine.listas import mapa_de_listas_primarias

    assert mapa_de_listas_primarias([7, 8], [3, 4], set()) == {"USD": 7, "BCV": 3}


def test_sin_listas_configuradas_caen_los_ids_por_defecto() -> None:
    """El caso de la copia de QA, y por qué sus teóricos no son comparables.

    Sin configuración de listas el mapa cae a los nombres lógicos 4 y 5. **Eso
    no es un dato**: es un respaldo, y significa que ningún teórico calculado en
    ese entorno se puede comparar con uno de un entorno configurado. Ver la nota
    de ``docs/blindaje/6-deuda-medida.md``.
    """
    from cxc.engine.listas import POR_DEFECTO_USD, POR_DEFECTO_VES, mapa_de_listas_primarias

    assert mapa_de_listas_primarias([], [], {10}) == {
        "USD": POR_DEFECTO_USD,
        "BCV": POR_DEFECTO_VES,
    }


def test_los_ids_no_numericos_se_descartan_sin_romper() -> None:
    """La configuración es texto libre editable desde la pantalla de ajustes."""
    from cxc.engine.listas import mapa_de_listas_primarias

    assert mapa_de_listas_primarias(["", "x", "11"], ["10", None], {10, 11}) == {
        "USD": 11,
        "BCV": 10,
    }


def test_la_nota_no_dice_que_las_paginas_discrepan_desde_que_la_guarda_esta() -> None:
    """La nota mintió durante un rato, y este test evita que vuelva a pasar.

    Decía «el reporte de saldos valora con la lista 7 y los otros tres caminos con
    la 11». Era verdad hasta el 11-sep-2026; al aplicar la guarda en los cinco
    sitios se volvió **falsa**, y la corrida diaria pasó a reportar en ALTA una
    condición ya resuelta. Un instrumento que grita lobo deja de mirarse.

    Lo que la divergencia significa ahora es otra cosa, más chica y cierta: la
    primera lista que la configuración ofrece está archivada y la guarda la
    saltea. Vale avisarlo —conviene reordenar la configuración— pero no como si
    dos pantallas dieran números distintos.

    **Y el reemplazo también mintió.** Decía «no hay divergencia entre páginas -- las
    cinco usan la guarda», y eso se volvió falso el mismo día al medir de dónde salen
    los ids: los cinco sitios aplican la guarda, pero leen de DOS fuentes de
    configuración distintas. La nota ya no afirma nada sobre la divergencia entre
    páginas, porque no tiene los datos para afirmarlo —`comparar_fuentes_de_lista`
    sí—. La lección, dos veces: una nota que afirma más de lo que su función puede
    ver envejece mal.
    """
    d = diagnostico_de_eleccion("BCV", [3, 10], {10})
    assert not d.coinciden
    assert "ARCHIVADA" in d.nota, "sigue diciendo cuál está archivada"
    assert "valora con la lista 3" not in d.nota, (
        "la nota no debe afirmar que una página valora con la lista archivada: "
        "desde que la guarda está aplicada, ninguna lo hace"
    )
    assert "No hay divergencia" not in d.nota, (
        "tampoco puede afirmar lo contrario: esta función solo ve UNA fuente de "
        "configuración, así que no sabe si las páginas coinciden entre sí"
    )
    assert "comparar_fuentes_de_lista" in d.nota, "y dice quién sí puede contestarlo"


# --- la vigencia efectiva y su denominador (Fase 2.4, pieza 22) --------------


def _regla(desde=None, hasta=None):
    """Una regla como las que Odoo devuelve: `False` cuando no declara la fecha."""
    return {"date_start": desde or False, "date_end": hasta or False}


def test_el_rango_sale_del_minimo_inicio_y_el_maximo_fin() -> None:
    from cxc.engine.listas import vigencia_efectiva

    v = vigencia_efectiva(
        [
            _regla("2026-04-02", "2026-09-02"),
            _regla("2026-02-23", "2026-04-01"),
        ]
    )
    assert (v.desde, v.hasta) == ("2026-02-23", "2026-09-02")
    assert v.reglas == 2
    assert v.todas_declaran_fin


def test_las_fechas_con_hora_se_recortan_al_dia() -> None:
    """Odoo devuelve `date_start` con hora en algunos campos datetime."""
    from cxc.engine.listas import vigencia_efectiva

    v = vigencia_efectiva([_regla("2026-04-02 00:00:00", "2026-09-02 23:59:59")])
    assert (v.desde, v.hasta) == ("2026-04-02", "2026-09-02")


def test_una_regla_sin_fechas_no_ensancha_ni_angosta_el_rango() -> None:
    """Pero sí cambia el denominador, que es lo que el endpoint no decía.

    Es el defecto que esta pieza expone: `min`/`max` saltean las reglas sin fecha
    en silencio, así que el rango se muestra como si fuera el de la lista entera.
    """
    from cxc.engine.listas import vigencia_efectiva

    v = vigencia_efectiva([_regla("2026-04-02", "2026-09-02"), _regla(), _regla()])
    assert (v.desde, v.hasta) == ("2026-04-02", "2026-09-02")
    assert v.reglas == 3
    assert (v.con_desde, v.con_hasta) == (1, 1)
    assert v.rango_parcial, "el rango sale de 1 de 3 reglas y hay que decirlo"
    assert not v.todas_declaran_fin


def test_la_lista_9_de_QA_no_declara_inicio_en_ninguna_de_sus_149_reglas() -> None:
    """Medido contra el Odoo de QA el 11-sep-2026.

    «Lista Industrial 3%» (id 9, archivada) tiene 149 reglas: 0 con `date_start` y
    148 con `date_end`. La pantalla mostraba `N/A..2026-09-02` sin decir que el
    `N/A` sale de 149 reglas que no lo declaran, no de una lista vacía.
    """
    from cxc.engine.listas import vigencia_efectiva

    reglas = [_regla(hasta="2026-09-02") for _ in range(148)] + [_regla()]
    v = vigencia_efectiva(reglas)
    assert v.desde is None
    assert v.hasta == "2026-09-02"
    assert (v.reglas, v.con_desde, v.con_hasta) == (149, 0, 148)
    assert not v.ninguna_declara_fin, "148 sí vencen"
    assert v.rango_parcial


def test_ninguna_regla_vence_es_la_forma_de_las_nueve_listas_activas() -> None:
    """Y por eso ninguna de ellas puede disparar la mina del precio vencido.

    Las listas 10 a 19 (las activas) tienen entre 151 y 186 reglas cada una y
    **ni una** declara `date_end`. Sus reglas cubren cualquier fecha posterior a su
    inicio, así que un precio de abril se sigue sirviendo en septiembre sin que
    nada lo marque como vencido.
    """
    from cxc.engine.listas import vigencia_efectiva

    v = vigencia_efectiva([_regla(desde="2026-09-02") for _ in range(154)])
    assert v.hasta is None
    assert v.ninguna_declara_fin
    assert v.con_desde == 154
    assert not v.rango_parcial, "no es un rango parcial: es que ninguna vence"


def test_una_lista_sin_reglas_no_declara_nada_de_las_dos_formas() -> None:
    """Cero reglas no es «ninguna vence»: es que no hay nada que vencer.

    Si `ninguna_declara_fin` fuera True acá, una lista vacía se leería igual que
    una de 154 reglas perpetuas.
    """
    from cxc.engine.listas import vigencia_efectiva

    v = vigencia_efectiva([])
    assert (v.desde, v.hasta) == (None, None)
    assert (v.reglas, v.con_desde, v.con_hasta) == (0, 0, 0)
    assert not v.ninguna_declara_fin
    assert not v.todas_declaran_fin
    assert not v.rango_parcial


def test_una_sola_regla_sin_fin_no_es_un_rango_parcial() -> None:
    """`rango_parcial` compara el rango contra sus hermanas, y acá no hay hermanas."""
    from cxc.engine.listas import vigencia_efectiva

    v = vigencia_efectiva([_regla(desde="2026-09-02")])
    assert not v.rango_parcial
    assert v.ninguna_declara_fin


# --- dos reglas para el mismo producto en la misma lista (pieza 25) ----------


def _item(id_, tmpl, precio, desde=None, hasta=None, nombre="SINOCO SAE 50 (Tambor)"):
    return {
        "id": id_,
        "product_tmpl_id": [tmpl, nombre],
        "fixed_price": precio,
        "date_start": desde or False,
        "date_end": hasta or False,
    }


def test_un_producto_con_una_sola_regla_no_es_noticia() -> None:
    from cxc.engine.listas import reglas_duplicadas

    assert reglas_duplicadas([_item(1, 1042, 1139.66), _item(2, 1034, 1021.55)]) == []


def test_dos_reglas_con_el_MISMO_precio_son_redundantes_pero_no_ambiguas() -> None:
    """Da igual cuál elija Odoo: el precio servido es el mismo.

    Es el caso de la lista 10 para [0877], medido: 106,10 en las dos reglas.
    """
    from cxc.engine.listas import reglas_duplicadas

    (d,) = reglas_duplicadas([_item(3817, 1056, 106.10), _item(3943, 1056, 106.10)])
    assert not d.precios_distintos
    assert not d.ambigua
    assert d.ids_de_regla == (3817, 3943)


def test_dos_precios_con_el_MISMO_rango_de_fechas_son_ambiguos() -> None:
    """El hallazgo: cuál se sirve depende del orden interno de Odoo, no del dato.

    Lista 10, [1042] SINOCO SAE 50 (Tambor): 1.236,07 contra 1.198,94, las dos desde
    el 2-sep-2026 y sin fin.
    """
    from cxc.engine.listas import reglas_duplicadas

    (d,) = reglas_duplicadas(
        [
            _item(3917, 1042, 1236.07, desde="2026-09-02"),
            _item(3945, 1042, 1198.94, desde="2026-09-02"),
        ]
    )
    assert d.precios_distintos
    assert not d.fechas_desempatan
    assert d.ambigua
    assert not d.tiene_precio_cero


def test_si_los_rangos_DIFIEREN_la_fecha_desempata_y_no_hay_ambiguedad() -> None:
    """Dos precios de épocas distintas es lo normal en una lista con historia.

    Marcar eso como ambiguo llenaría el reporte de ruido, y un aviso con ruido
    entrena a ignorar los avisos.
    """
    from cxc.engine.listas import reglas_duplicadas

    (d,) = reglas_duplicadas(
        [
            _item(1, 1042, 900.00, desde="2026-02-23", hasta="2026-04-01"),
            _item(2, 1042, 1198.94, desde="2026-04-02"),
        ]
    )
    assert d.precios_distintos
    assert d.fechas_desempatan
    assert not d.ambigua


def test_la_regla_en_CERO_es_la_que_tiene_consecuencia() -> None:
    """Listas 18 y 19, activas: 0,00 contra 1.139,66 y 1.753,32 para un tambor.

    Si Odoo elige la regla del cero, el producto sale gratis. Medido el 11-sep-2026:
    ese cero **no se sirvió nunca** —0 de 273 líneas de esos productos tienen precio
    cero, y ninguna orden confirmada usa esas dos listas—, así que es una trampa
    cargada y no una pérdida. Basta una orden para que dispare.
    """
    from cxc.engine.listas import reglas_duplicadas

    (d,) = reglas_duplicadas(
        [
            _item(3896, 1042, 0.0, desde="2026-09-02"),
            _item(3927, 1042, 1139.66, desde="2026-09-02"),
        ]
    )
    assert d.tiene_precio_cero
    assert d.ambigua, "y además es ambigua, que es lo que la vuelve peligrosa"


def test_tres_reglas_tambien_se_reportan() -> None:
    """No hay nada que limite el problema a dos."""
    from cxc.engine.listas import reglas_duplicadas

    (d,) = reglas_duplicadas(
        [_item(1, 1042, 100.0), _item(2, 1042, 200.0), _item(3, 1042, 300.0)]
    )
    assert len(d.precios) == 3
    assert d.ambigua


def test_una_regla_sin_producto_se_saltea_en_vez_de_agruparse_con_las_otras() -> None:
    """`product_tmpl_id` en False agruparía todas las reglas globales como un producto.

    Y entonces una lista con dos reglas globales se reportaría como un producto con
    precio ambiguo, que es un aviso falso.
    """
    from cxc.engine.listas import reglas_duplicadas

    reglas = [
        {"id": 1, "product_tmpl_id": False, "fixed_price": 10.0},
        {"id": 2, "product_tmpl_id": False, "fixed_price": 20.0},
    ]
    assert reglas_duplicadas(reglas) == []


# --- las DOS fuentes de configuración (Fase 2.4, pieza 26) -------------------


def test_dos_fuentes_que_coinciden_no_son_un_hallazgo() -> None:
    from cxc.engine.listas import comparar_fuentes_de_lista

    d = comparar_fuentes_de_lista(
        [("mapeo", [11, 13], [10, 12]), ("claves", [11], [10])], {10, 11, 12, 13}
    )
    assert d.coinciden
    assert not d.alguna_con_archivada
    assert "coinciden en USD=11 y BCV=10" in d.nota


def test_el_hallazgo_medido_las_fuentes_dan_listas_DISTINTAS() -> None:
    """Medido contra QA el 11-sep-2026, y es lo que este chequeo existe para ver.

    El mapeo unificado da USD=11 / BCV=10 (las dos activas) y las claves
    `valid_pricelists_*` dan USD=4 / BCV=5 (las dos archivadas, y la 5 con todas sus
    reglas vencidas el 2-sep). Cinco sitios del código deciden con qué lista se valora
    un teórico, y leen de esas dos fuentes.

    La salvedad va en el docstring del módulo: en QA esas claves no existen y caen al
    env, así que **ese número concreto no se traslada a producción**. Lo que sí es
    estructural es que hay dos fuentes y nada las sincroniza.
    """
    from cxc.engine.listas import comparar_fuentes_de_lista

    activos = {10, 11, 12, 13, 14, 15, 16, 17, 18, 19}
    d = comparar_fuentes_de_lista(
        [
            ("mapeo unificado", [7, 8, 11, 13], [3, 4, 5, 9, 10, 12]),
            ("claves valid_pricelists_*", [4], [5]),
        ],
        activos,
    )
    assert not d.coinciden
    assert "DIFIEREN" in d.nota
    assert "USD=11" in d.nota and "USD=4 (ARCHIVADA)" in d.nota
    assert "BCV=10" in d.nota and "BCV=5 (ARCHIVADA)" in d.nota
    assert "decision del usuario" not in d.nota, (
        "la nota no opina sobre qué hacer: esta función solo sabe que las fuentes "
        "difieren, no qué pantalla lee cada una. La versión anterior sí opinaba, y se "
        "volvió falsa el día en que el usuario decidió que el mapeo manda."
    )


def test_coinciden_pero_en_una_lista_ARCHIVADA_tambien_se_avisa() -> None:
    """Que las dos fuentes estén de acuerdo no las hace correctas.

    Si las dos apuntan a la misma lista archivada, no hay divergencia entre páginas y
    sin embargo se sirven precios de una lista que nadie mantiene. Son dos problemas
    distintos y el diagnóstico los separa.
    """
    from cxc.engine.listas import comparar_fuentes_de_lista

    d = comparar_fuentes_de_lista([("a", [4], [5]), ("b", [4], [5])], {10, 11})
    assert d.coinciden
    assert d.alguna_con_archivada
    assert "ARCHIVADA" in d.nota


def test_una_sola_fuente_no_tiene_con_que_comparar() -> None:
    """Y lo dice, en vez de devolver «coinciden», que se leería como verificado."""
    from cxc.engine.listas import comparar_fuentes_de_lista

    d = comparar_fuentes_de_lista([("unica", [11], [10])], {10, 11})
    assert d.coinciden, "trivialmente, porque hay una sola"
    assert "nada que comparar" in d.nota


def test_cada_fuente_reporta_sus_ids_y_cual_eligio() -> None:
    """Para poder ir a arreglar la configuración hay que ver de qué lista salió."""
    from cxc.engine.listas import comparar_fuentes_de_lista

    d = comparar_fuentes_de_lista(
        [("mapeo", ["7", "8", "11"], ["3", "10"])], {10, 11}
    )
    (f,) = d.fuentes
    assert f.ids_usd == (7, 8, 11)
    assert f.primaria_usd == 11
    assert f.usd_activa
    assert f.primaria_ves == 10


def test_una_fuente_vacia_cae_a_los_por_defecto_y_se_ve_que_estan_archivados() -> None:
    """Config vacía = ids 4 y 5 por defecto, que hoy están archivados.

    El `POR_DEFECTO_USD/VES` no es un dato: es un nombre lógico de respaldo. Que salga
    marcado como archivado es la señal de que la configuración falta.
    """
    from cxc.engine.listas import comparar_fuentes_de_lista

    d = comparar_fuentes_de_lista([("vacia", [], [])], {10, 11})
    (f,) = d.fuentes
    assert (f.primaria_usd, f.primaria_ves) == (4, 5)
    assert f.alguna_archivada
