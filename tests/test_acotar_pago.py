"""Los dos repartos de un pago acotado (Fase 2.4, decimotercera pieza).

Existe para que una decisión del usuario se tome sobre los dos **resultados** y no
sobre la descripción de los dos métodos. La cota no se discute —un pago no puede
aplicar más de lo que vale— pero cómo se reparte sí, y las dos maneras mueven el
crédito de órdenes distintas.

Estos tests no eligen ninguna.
"""

from __future__ import annotations

from decimal import Decimal

from cxc.engine.acotar_pago import (
    Aplicacion,
    diagnostico_de_reparto,
    repartir_por_orden,
    repartir_proporcional,
)


def _a(so, monto):
    return Aplicacion(so, Decimal(str(monto)))


# --- reparto por orden ------------------------------------------------------


def test_por_orden_corta_al_agotar_el_pago() -> None:
    """El caso de 7 de los 10 pagos: el primer parcial ya es el pago entero."""
    r = repartir_por_orden([_a("A", "134"), _a("B", "581.04")], Decimal("134"))
    assert r == [Aplicacion("A", Decimal("134"))]


def test_por_orden_parte_la_aplicacion_que_no_entra_completa() -> None:
    """Caso del pago 845: la última entra a medias, no se descarta entera."""
    r = repartir_por_orden([_a("A", "570.43"), _a("B", "24.77")], Decimal("585"))
    assert r == [Aplicacion("A", Decimal("570.43")), Aplicacion("B", Decimal("14.57"))]


def test_por_orden_no_devuelve_aplicaciones_en_cero() -> None:
    """Lo que no se aplicó no existe como aplicación.

    Devolverlas en cero haría que una orden figure como «recibió crédito» en las
    pantallas que solo miran si hay fila.
    """
    r = repartir_por_orden([_a("A", "100"), _a("B", "50")], Decimal("100"))
    assert [x.so_id for x in r] == ["A"]


def test_por_orden_con_el_tope_en_cero_no_reparte_nada() -> None:
    assert repartir_por_orden([_a("A", "100")], Decimal("0")) == []


# --- reparto proporcional ---------------------------------------------------


def test_proporcional_reparte_segun_el_peso() -> None:
    r = repartir_proporcional([_a("A", "75"), _a("B", "25")], Decimal("100"))
    assert r == [Aplicacion("A", Decimal("75.00")), Aplicacion("B", Decimal("25.00"))]


def test_proporcional_cierra_exacto_contra_el_tope() -> None:
    """Tres partes iguales de un tercio: el último se lleva el resto.

    Redondear cada parte por separado deja un centavo suelto, y un centavo suelto
    en un camino de dinero es una divergencia que después alguien persigue.
    """
    r = repartir_proporcional([_a("A", "1"), _a("B", "1"), _a("C", "1")], Decimal("100"))
    assert sum((x.monto for x in r), Decimal("0")) == Decimal("100")


def test_proporcional_sin_aplicaciones_no_inventa_un_reparto() -> None:
    assert repartir_proporcional([], Decimal("100")) == []


def test_proporcional_con_montos_en_cero_no_divide_por_cero() -> None:
    assert repartir_proporcional([_a("A", "0"), _a("B", "0")], Decimal("100")) == []


# --- el diagnóstico ---------------------------------------------------------


def test_un_pago_que_no_esta_sobreaplicado_no_ofrece_una_eleccion() -> None:
    """Dos columnas iguales se leerían como si hubiera algo que decidir."""
    d = diagnostico_de_reparto("P1", [_a("A", "60"), _a("B", "40")], "100")
    assert not d.sobreaplicado
    assert d.por_orden == d.proporcional
    assert d.pierden_todo == ()
    assert "no hay nada que elegir" in d.nota


def test_el_redondeo_de_centavos_no_cuenta_como_sobreaplicado() -> None:
    d = diagnostico_de_reparto("P1", [_a("A", "100.04")], "100")
    assert not d.sobreaplicado


def test_el_caso_del_pago_200_medido_queda_fijado() -> None:
    """Vale 134,00 y tiene aplicados 715,04 en nueve parciales.

    Con ``POR_ORDEN`` el primer parcial —que es el pago completo— se lleva todo y
    las otras tres órdenes pierden el crédito entero. Con ``PROPORCIONAL`` las
    cuatro reciben una fracción.
    """
    aplic = [
        _a("S00279", "134"),
        _a("S00279", "3.79"),
        _a("S00617", "66.33"),
        _a("S00617", "66.89"),
        _a("S00617", "8.77"),
        _a("S00412", "76.79"),
        _a("S00412", "40.20"),
        _a("S00638", "128.01"),
        _a("S00638", "190.26"),
    ]
    d = diagnostico_de_reparto("200", aplic, "134")
    assert d.sobreaplicado
    assert d.aplicado_hoy == Decimal("715.04")
    assert d.exceso == Decimal("581.04")
    assert d.por_orden == {"S00279": Decimal("134")}
    assert set(d.pierden_todo) == {"S00617", "S00412", "S00638"}
    # Los dos repartos entregan exactamente el valor del pago, ni más ni menos.
    assert sum(d.por_orden.values()) == Decimal("134")
    assert sum(d.proporcional.values()) == Decimal("134")


def test_los_dos_repartos_siempre_suman_el_pago() -> None:
    """La cota es lo único que no se discute, así que se fija aparte.

    Si un reparto entregara de más seguiría acreditando plata que no entró, y si
    entregara de menos estaría inventando deuda. Los dos tienen que dar el pago.
    """
    aplic = [_a("A", "300"), _a("B", "200"), _a("C", "100")]
    d = diagnostico_de_reparto("P1", aplic, "250")
    assert sum(d.por_orden.values()) == Decimal("250")
    assert sum(d.proporcional.values()) == Decimal("250")


def test_varias_aplicaciones_a_la_misma_orden_se_suman() -> None:
    """El pago 200 tiene dos parciales contra S00279 y tres contra S00617."""
    d = diagnostico_de_reparto("P1", [_a("A", "60"), _a("A", "40")], "100")
    assert d.por_orden == {"A": Decimal("100")}


def test_sin_monto_de_pago_no_se_afirma_que_este_sobreaplicado() -> None:
    """«Sin datos no es cero». Con el tope ausente no hay con qué comparar."""
    for tope in (None, "", "0", "ilegible"):
        assert not diagnostico_de_reparto("P1", [_a("A", "999")], tope).sobreaplicado


def test_la_nota_dice_cuantas_ordenes_pierden_todo() -> None:
    """Es la cifra que separa las dos opciones en la práctica."""
    d = diagnostico_de_reparto("P1", [_a("A", "100"), _a("B", "50")], "100")
    assert d.pierden_todo == ("B",)
    assert "1 pierden todo" in d.nota or "1 orden" in d.nota
