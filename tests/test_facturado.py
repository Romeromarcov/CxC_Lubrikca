"""Qué le queda facturado a una orden, y qué le falta (Fase 2.4, undécima pieza).

Las tres salidas de este bloque son las que usé para dictaminar el caso S00372
del plan, y no tenían una prueba propia. Los e2e las ejercitan a través del
endpoint, que prueba que la pantalla muestre algo coherente — no que la regla
sea la regla.

La medición A/B de esta pieza está al final: el cuerpo original transcrito, y la
comparación sobre una grilla de casos.
"""

from __future__ import annotations

import itertools

import pytest

from cxc.engine.facturado import CERO, facturado_de_orden

IVA = 0.16


# --- regla 1: las notas entran sobre el total con impuestos ------------------


def test_sin_notas_el_neto_es_el_bruto() -> None:
    f = facturado_de_orden(
        facturado_sin_impuestos=100.0,
        facturado_con_impuestos=116.0,
        nc_aplicada=0.0,
        nd_aplicada=0.0,
        iva_rate=IVA,
    )
    assert f.neto == 116.0
    assert f.tiene_factura
    assert f.iva_retenido_confirmado == 0.0


def test_la_nota_de_credito_resta_y_la_de_debito_suma() -> None:
    f = facturado_de_orden(
        facturado_sin_impuestos=100.0,
        facturado_con_impuestos=116.0,
        nc_aplicada=16.0,
        nd_aplicada=30.0,
        iva_rate=IVA,
    )
    assert f.neto == pytest.approx(130.0)
    assert (f.nc_aplicada, f.nd_aplicada) == (16.0, 30.0)


def test_una_nc_mayor_que_la_factura_deja_el_neto_negativo() -> None:
    """Preservado: acá NO hay piso en cero, y es deliberado.

    El piso existe sólo después de restar la retención. Un neto negativo por una
    NC excesiva es un dato real —la factura quedó en negativo— y recortarlo a
    cero escondería justo el caso que el plan quiere ver señalado. Ver la fila
    «Nota de crédito por más que la factura» de la Fase 3.
    """
    f = facturado_de_orden(
        facturado_sin_impuestos=100.0,
        facturado_con_impuestos=116.0,
        nc_aplicada=200.0,
        nd_aplicada=0.0,
        iva_rate=IVA,
    )
    assert f.neto == pytest.approx(-84.0)
    assert f.tiene_factura, "sigue teniendo factura aunque el neto sea negativo"


# --- regla 2: la retención baja el objetivo por el IVA COMPLETO --------------


def test_el_caso_s00372_medido_queda_fijado() -> None:
    """El caso real que hizo falta diagnosticar, con sus cifras de Odoo.

    Factura 00000049 por 644,12 USD con retención aplicada. El cliente pagó
    555,29 y retuvo el resto. 644,12 / 1,16 = 555,28: el objetivo baja hasta lo
    que efectivamente se cobra en efectivo, y los 88,84 restantes los cierra el
    comprobante de retención y no la cobranza.

    Sin esta regla la orden diría que debe 88,84 USD para siempre.
    """
    f = facturado_de_orden(
        facturado_sin_impuestos=555.29,
        facturado_con_impuestos=644.12,
        nc_aplicada=0.0,
        nd_aplicada=0.0,
        iva_rate=IVA,
        wh_iva_aplicado=True,
        facturada=True,
    )
    assert f.neto == pytest.approx(555.28, abs=0.01)
    assert f.iva_retenido_confirmado == pytest.approx(88.84, abs=0.01)
    assert f.tiene_factura


def test_la_retencion_no_asume_un_porcentaje_fijo() -> None:
    """Se resta el IVA completo, no «el 75 % que suele retenerse».

    El documento puede retener del 0 al 100 %. Asumir una fracción sería inventar
    un número; restar el IVA entero dice «en efectivo ya no se debe nada de
    impuesto», que es lo único que se sabe con certeza una vez confirmada.
    """
    f = facturado_de_orden(
        facturado_sin_impuestos=1000.0,
        facturado_con_impuestos=1160.0,
        nc_aplicada=0.0,
        nd_aplicada=0.0,
        iva_rate=IVA,
        wh_iva_aplicado=True,
    )
    assert f.neto == pytest.approx(1000.0)
    assert f.iva_retenido_confirmado == pytest.approx(160.0)


def test_sin_retencion_el_neto_no_se_toca() -> None:
    f = facturado_de_orden(
        facturado_sin_impuestos=1000.0,
        facturado_con_impuestos=1160.0,
        nc_aplicada=0.0,
        nd_aplicada=0.0,
        iva_rate=IVA,
        wh_iva_aplicado=False,
    )
    assert f.neto == 1160.0
    assert f.iva_retenido_confirmado == 0.0


def test_la_retencion_se_aplica_sobre_el_neto_de_notas_no_sobre_el_bruto() -> None:
    """Si la NC ya bajó la factura, el IVA que se descuenta es el de lo que queda."""
    f = facturado_de_orden(
        facturado_sin_impuestos=1000.0,
        facturado_con_impuestos=1160.0,
        nc_aplicada=580.0,
        nd_aplicada=0.0,
        iva_rate=IVA,
        wh_iva_aplicado=True,
    )
    assert f.iva_retenido_confirmado == pytest.approx(580.0 - 580.0 / 1.16)
    assert f.neto == pytest.approx(500.0)


def test_con_neto_en_cero_no_se_calcula_retencion() -> None:
    """Sin factura viva no hay IVA que retener, y dividir daría cero igual.

    Importa que la guarda exista igual: con un neto negativo por una NC excesiva,
    restar «el IVA» lo haría MENOS negativo, o sea maquillaría el caso que hay
    que ver.
    """
    f = facturado_de_orden(
        facturado_sin_impuestos=0.0,
        facturado_con_impuestos=116.0,
        nc_aplicada=200.0,
        nd_aplicada=0.0,
        iva_rate=IVA,
        wh_iva_aplicado=True,
    )
    assert f.iva_retenido_confirmado == 0.0
    assert f.neto == pytest.approx(-84.0), "el negativo se preserva, no se maquilla"


def test_el_neto_nunca_queda_negativo_por_culpa_de_la_retencion() -> None:
    """El piso en cero de la regla 2, que sí existe.

    Con un IVA mayor que 1 (configuración absurda pero posible) la resta daría
    negativo. Un neto negativo por retención no significa nada: la retención no
    puede hacer que el cliente tenga saldo a favor.
    """
    f = facturado_de_orden(
        facturado_sin_impuestos=10.0,
        facturado_con_impuestos=10.0,
        nc_aplicada=0.0,
        nd_aplicada=0.0,
        iva_rate=-2.0,
        wh_iva_aplicado=True,
    )
    assert f.neto == 0.0


# --- regla 3: cuándo falta la nota de crédito -------------------------------


def test_devolucion_en_orden_facturada_sin_nc_pide_la_nc() -> None:
    f = facturado_de_orden(
        facturado_sin_impuestos=555.29,
        facturado_con_impuestos=644.12,
        nc_aplicada=0.0,
        nd_aplicada=0.0,
        iva_rate=IVA,
        facturada=True,
        tiene_devolucion=True,
    )
    assert f.falta_nc_por_devolucion


def test_si_la_nc_ya_existe_no_se_pide_de_nuevo() -> None:
    f = facturado_de_orden(
        facturado_sin_impuestos=555.29,
        facturado_con_impuestos=644.12,
        nc_aplicada=644.12,
        nd_aplicada=0.0,
        iva_rate=IVA,
        facturada=True,
        tiene_devolucion=True,
    )
    assert not f.falta_nc_por_devolucion


def test_antes_de_facturar_la_devolucion_no_pide_nc() -> None:
    """Se corrige por las líneas de Odoo, nunca por una NC.

    Pedir una NC sobre una orden sin factura mandaría a alguien a emitir un
    documento contra nada.
    """
    f = facturado_de_orden(
        facturado_sin_impuestos=0.0,
        facturado_con_impuestos=0.0,
        nc_aplicada=0.0,
        nd_aplicada=0.0,
        iva_rate=IVA,
        facturada=False,
        tiene_devolucion=True,
    )
    assert not f.falta_nc_por_devolucion


def test_una_cancelada_con_mercancia_afuera_cuenta_igual_que_una_devolucion() -> None:
    """Mismo efecto: factura viva por mercancía que el cliente no se quedó."""
    f = facturado_de_orden(
        facturado_sin_impuestos=100.0,
        facturado_con_impuestos=116.0,
        nc_aplicada=0.0,
        nd_aplicada=0.0,
        iva_rate=IVA,
        facturada=True,
        tiene_devolucion=False,
        cancelada_sin_devolver=True,
    )
    assert f.falta_nc_por_devolucion


def test_sin_devolucion_ni_cancelacion_no_se_pide_nada() -> None:
    f = facturado_de_orden(
        facturado_sin_impuestos=100.0,
        facturado_con_impuestos=116.0,
        nc_aplicada=0.0,
        nd_aplicada=0.0,
        iva_rate=IVA,
        facturada=True,
    )
    assert not f.falta_nc_por_devolucion


def test_una_nc_por_debajo_del_umbral_cuenta_como_inexistente() -> None:
    """Medio centavo de NC no es una NC."""
    f = facturado_de_orden(
        facturado_sin_impuestos=100.0,
        facturado_con_impuestos=116.0,
        nc_aplicada=CERO / 2,
        nd_aplicada=0.0,
        iva_rate=IVA,
        facturada=True,
        tiene_devolucion=True,
    )
    assert f.falta_nc_por_devolucion


def test_el_veredicto_de_la_nc_no_depende_de_la_retencion() -> None:
    """El orden entre la regla 3 y la 2, fijado.

    Si el ajuste por retención corriera primero, este caso —NC que deja el neto
    en cero— podría cambiar de veredicto según cuánto IVA se hubiera retenido,
    que no tiene nada que ver con si el documento existe.
    """
    comun = {
        "facturado_sin_impuestos": 100.0,
        "facturado_con_impuestos": 116.0,
        "nc_aplicada": 0.0,
        "nd_aplicada": 0.0,
        "iva_rate": IVA,
        "facturada": True,
        "tiene_devolucion": True,
    }
    con = facturado_de_orden(**comun, wh_iva_aplicado=True)
    sin = facturado_de_orden(**comun, wh_iva_aplicado=False)
    assert con.falta_nc_por_devolucion == sin.falta_nc_por_devolucion is True
    assert con.neto != sin.neto, "el neto sí cambia; el veredicto no"


# --- regla 4: «tiene factura» mira el bruto ---------------------------------


def test_una_orden_acreditada_por_completo_sigue_teniendo_factura() -> None:
    """El caso donde más importa saber que estuvo facturada.

    Con el neto en cero, mirar el neto diría «nunca se facturó» — y entonces la
    regla 3 dejaría de pedir la NC justo en las órdenes que ya la tienen, y el
    resto del bucle trataría la orden como si nunca hubiera existido un
    documento.
    """
    f = facturado_de_orden(
        facturado_sin_impuestos=100.0,
        facturado_con_impuestos=116.0,
        nc_aplicada=116.0,
        nd_aplicada=0.0,
        iva_rate=IVA,
    )
    assert f.neto == 0.0
    assert f.tiene_factura


def test_sin_ninguna_factura_no_tiene_factura() -> None:
    f = facturado_de_orden(
        facturado_sin_impuestos=0.0,
        facturado_con_impuestos=0.0,
        nc_aplicada=0.0,
        nd_aplicada=0.0,
        iva_rate=IVA,
    )
    assert not f.tiene_factura


def test_un_bruto_por_debajo_del_umbral_no_es_una_factura() -> None:
    f = facturado_de_orden(
        facturado_sin_impuestos=0.0,
        facturado_con_impuestos=CERO / 2,
        nc_aplicada=0.0,
        nd_aplicada=0.0,
        iva_rate=IVA,
    )
    assert not f.tiene_factura


# --- entradas ilegibles -----------------------------------------------------


@pytest.mark.parametrize("ausente", [None, False, "", "no es un numero"])
def test_un_monto_ilegible_vale_cero_y_no_revienta(ausente) -> None:
    f = facturado_de_orden(
        facturado_sin_impuestos=ausente,
        facturado_con_impuestos=ausente,
        nc_aplicada=ausente,
        nd_aplicada=ausente,
        iva_rate=IVA,
    )
    assert f.neto == 0.0
    assert not f.tiene_factura


def test_los_montos_en_texto_se_leen() -> None:
    """El espejo devuelve numeric, que psycopg puede entregar como Decimal o str."""
    f = facturado_de_orden(
        facturado_sin_impuestos="100",
        facturado_con_impuestos="116.00",
        nc_aplicada="0",
        nd_aplicada="0",
        iva_rate=IVA,
    )
    assert f.neto == pytest.approx(116.0)


# --- la medición A/B --------------------------------------------------------


def _cuerpo_original(
    facturado_con_impuestos,
    nc,
    nd,
    iva_rate,
    wh,
    facturada,
    devolucion,
    cancelada_sin_devolver,
):
    """El bloque tal como estaba en ``app.py`` antes de la extracción.

    Transcripto sin tocar, incluidos los tres ``0.005`` literales y el orden de
    las dos reglas. Es la referencia contra la que se compara.
    """
    total_facturado_con_impuestos = facturado_con_impuestos
    total_nc_aplicada = nc
    total_nd_aplicada = nd
    total_facturado_neto = total_facturado_con_impuestos - total_nc_aplicada + total_nd_aplicada
    falta_nc_por_devolucion = (
        bool(facturada)
        and (bool(devolucion) or cancelada_sin_devolver)
        and total_nc_aplicada <= 0.005
    )
    iva_retenido_confirmado = 0.0
    if wh and total_facturado_neto > 0.005:
        iva_retenido_confirmado = total_facturado_neto - (total_facturado_neto / (1 + iva_rate))
        total_facturado_neto = max(0.0, total_facturado_neto - iva_retenido_confirmado)
    tiene_factura = total_facturado_con_impuestos > 0.005
    return (
        total_facturado_neto,
        iva_retenido_confirmado,
        tiene_factura,
        falta_nc_por_devolucion,
    )


GRILLA = list(
    itertools.product(
        [0.0, 0.004, 116.0, 644.12, 1160.0],  # facturado con impuestos
        [0.0, 0.004, 16.0, 644.12, 2000.0],  # nc
        [0.0, 30.0],  # nd
        [False, True],  # retención
        [False, True],  # facturada
        [False, True],  # devolución
        [False, True],  # cancelada sin devolver
    )
)


def test_el_modulo_da_exactamente_lo_mismo_que_el_cuerpo_original() -> None:
    """Los 800 casos de la grilla, en UN test.

    Deliberadamente no parametrizado: son una sola medición sobre 800 entradas,
    no 800 pruebas distintas. Parametrizarlo inflaría el conteo de la suite sin
    agregar una sola protección, y un número de tests inflado es justo la clase
    de cifra tranquilizadora que este trabajo viene corrigiendo.

    A cambio, se reportan **todas** las discrepancias y no sólo la primera: si el
    módulo se apartara del original, importa saber si es en un caso o en
    doscientos.
    """
    diferencias = []
    for bruto, nc, nd, wh, facturada, devolucion, cancelada in GRILLA:
        esperado = _cuerpo_original(bruto, nc, nd, IVA, wh, facturada, devolucion, cancelada)
        f = facturado_de_orden(
            facturado_sin_impuestos=0.0,
            facturado_con_impuestos=bruto,
            nc_aplicada=nc,
            nd_aplicada=nd,
            iva_rate=IVA,
            wh_iva_aplicado=wh,
            facturada=facturada,
            tiene_devolucion=devolucion,
            cancelada_sin_devolver=cancelada,
        )
        obtenido = (
            f.neto,
            f.iva_retenido_confirmado,
            f.tiene_factura,
            f.falta_nc_por_devolucion,
        )
        if obtenido != esperado:
            diferencias.append(
                (bruto, nc, nd, wh, facturada, devolucion, cancelada, esperado, obtenido)
            )
    assert not diferencias, (
        f"{len(diferencias)} de {len(GRILLA)} casos difieren del cuerpo original; "
        f"primeros tres: {diferencias[:3]}"
    )


def test_la_grilla_ejercita_las_cuatro_salidas() -> None:
    """Que la medición A/B no pase por no haber tocado nunca una rama.

    Sin esto, la parametrización podría dar 800 casos verdes sobre un solo
    camino y la comparación no probaría nada.
    """
    vistos = {"retencion": False, "falta_nc": False, "neto_negativo": False, "sin_factura": False}
    for bruto, nc, nd, wh, facturada, devolucion, cancelada in GRILLA:
        f = facturado_de_orden(
            facturado_sin_impuestos=0.0,
            facturado_con_impuestos=bruto,
            nc_aplicada=nc,
            nd_aplicada=nd,
            iva_rate=IVA,
            wh_iva_aplicado=wh,
            facturada=facturada,
            tiene_devolucion=devolucion,
            cancelada_sin_devolver=cancelada,
        )
        if f.iva_retenido_confirmado > 0:
            vistos["retencion"] = True
        if f.falta_nc_por_devolucion:
            vistos["falta_nc"] = True
        if f.neto < 0:
            vistos["neto_negativo"] = True
        if not f.tiene_factura:
            vistos["sin_factura"] = True
    assert all(vistos.values()), f"ramas nunca ejercitadas: {vistos}"
