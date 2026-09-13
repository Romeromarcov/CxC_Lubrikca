"""Una factura anulada no es una factura cobrada (Fase 2.4, décima pieza).

Estos tests no protegen una corrección: protegen un **diagnóstico**. El defecto
sigue vivo a propósito, porque arreglarlo devuelve 17 órdenes a la cuenta por
cobrar y eso mueve montos (regla de la Fase 1). Lo que se fija acá es que el
instrumento que mide el hueco no lo mida de menos, y los tres casos reales que la
copia de producción dio, con sus números exactos.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cxc.engine.reversadas import abono_implicito, diagnostico_de_reversadas

# --- la lectura de una factura sola -----------------------------------------


def test_una_factura_que_se_esta_cobrando_se_lee_igual_por_los_dos_caminos() -> None:
    lec = abono_implicito(1000, 300, "partial")
    assert lec.segun_la_resta == Decimal("700")
    assert lec.sin_anuladas == Decimal("700")
    assert not lec.anulada
    assert lec.fantasma == Decimal("0")


def test_una_factura_anulada_acredita_todo_su_total_por_la_resta() -> None:
    """El defecto, en su forma mínima: residual cero por anulación, no por cobro."""
    lec = abono_implicito(596091.85, 0.0, "reversed")
    assert lec.segun_la_resta == Decimal("596091.85")
    assert lec.sin_anuladas == Decimal("0")
    assert lec.anulada
    assert lec.fantasma == Decimal("596091.85")


@pytest.mark.parametrize("estado", ["not_paid", "partial", "paid", "in_payment", "", None])
def test_ningun_otro_estado_de_pago_se_considera_anulado(estado) -> None:
    """Importa que la lista sea corta: ``paid`` con residual 0 SÍ se cobró."""
    lec = abono_implicito(500, 0, estado)
    assert not lec.anulada
    assert lec.sin_anuladas == Decimal("500")


@pytest.mark.parametrize("estado", ["reversed", "REVERSED", " Reversed "])
def test_el_estado_se_compara_sin_mayusculas_ni_espacios(estado) -> None:
    assert abono_implicito(500, 0, estado).anulada


def test_un_residual_mayor_que_el_total_no_genera_un_abono_negativo() -> None:
    """Preservado del código original, y no es cosmético.

    Un abono negativo no dejaría la deuda quieta: la **aumentaría**, porque el
    saldo deudor resta el abono. Que el piso sea cero es la diferencia entre "esta
    factura no aporta nada" y "esta factura inventa deuda".
    """
    lec = abono_implicito(100, 250, "partial")
    assert lec.segun_la_resta == Decimal("0")
    assert lec.fantasma == Decimal("0")


@pytest.mark.parametrize("ausente", [None, False, ""])
def test_un_campo_ausente_de_xmlrpc_vale_cero_y_no_revienta(ausente) -> None:
    """XML-RPC manda ``False`` por un campo vacío, no ``None``.

    ``Decimal(str(False))`` es ``Decimal('False')``, que revienta. Con el valor
    equivocado y adentro de un bucle sobre todas las facturas, reventaría el
    reporte entero.
    """
    lec = abono_implicito(ausente, ausente, ausente)
    assert lec.segun_la_resta == Decimal("0")
    assert not lec.anulada


def test_un_total_ilegible_no_se_adivina() -> None:
    assert abono_implicito("mil", 0, "partial").segun_la_resta == Decimal("0")


# --- el diagnóstico por orden -----------------------------------------------


def test_sin_facturas_lo_dice_en_vez_de_devolver_un_cero_tranquilizador() -> None:
    d = diagnostico_de_reversadas([])
    assert d.abono_segun_la_resta == Decimal("0")
    assert not d.hay_fantasma
    assert "no tiene ninguna factura" in d.nota


def test_sin_anuladas_las_dos_lecturas_coinciden() -> None:
    d = diagnostico_de_reversadas(
        [
            {"name": "A", "amount_total": 1000, "amount_residual": 200, "payment_state": "partial"},
            {"name": "B", "amount_total": 500, "amount_residual": 0, "payment_state": "paid"},
        ]
    )
    assert d.abono_segun_la_resta == d.abono_sin_anuladas == Decimal("1300")
    assert not d.hay_fantasma
    assert d.numeros_vivos == ("A", "B")
    assert d.residual_vivo == Decimal("200")
    assert "coinciden" in d.nota


def test_el_caso_s00886_medido_queda_fijado() -> None:
    """El caso más limpio de la copia de producción: nadie pagó nada.

    00000677 anulada y 00000701 refacturada en ``not_paid`` con el residual
    completo. La resta acredita el total de la anulada, que es casi exactamente el
    total de la orden (756,91 USD), así que el saldo deudor da cero y la orden sale
    de la cuenta por cobrar.
    """
    d = diagnostico_de_reversadas(
        [
            {
                "name": "00000677",
                "amount_total": 581034.93,
                "amount_residual": 0.0,
                "payment_state": "reversed",
            },
            {
                "name": "00000701",
                "amount_total": 596091.85,
                "amount_residual": 596091.85,
                "payment_state": "not_paid",
            },
        ]
    )
    assert d.numeros_anulados == ("00000677",)
    assert d.numeros_vivos == ("00000701",)
    assert d.abono_segun_la_resta == Decimal("581034.93")
    assert d.abono_sin_anuladas == Decimal("0")
    assert d.fantasma == Decimal("581034.93")
    assert d.residual_vivo == Decimal("596091.85")
    assert d.hay_fantasma
    assert not d.sin_factura_viva, "S00886 SI fue refacturada; el problema es el abono"
    assert "encima de un residual vivo" in d.nota


def test_el_caso_s00573_medido_queda_fijado() -> None:
    """El ítem que el plan lista como «refacturar», ahora con diagnóstico.

    Única factura, anulada por la NC 00000014, y ninguna viva. 29 unidades
    entregadas y no devueltas: 1.860,48 USD de mercancía afuera sin ningún
    documento que la cobre.
    """
    d = diagnostico_de_reversadas(
        [
            {
                "name": "00000530",
                "amount_total": 1362751.66,
                "amount_residual": 0.0,
                "payment_state": "reversed",
            }
        ]
    )
    assert d.sin_factura_viva
    assert d.numeros_vivos == ()
    assert d.residual_vivo == Decimal("0")
    assert d.fantasma == Decimal("1362751.66")
    assert "HAY QUE REFACTURAR" in d.nota


def test_una_refacturacion_ya_cobrada_igual_deja_el_fantasma() -> None:
    """Que la segunda factura esté paga no borra el abono de la primera.

    Es el caso de 9 de las 16 refacturadas: el residual vivo es cero, así que no
    hay plata perdida, pero el abono sigue contando dos veces. Importa porque ese
    abono inflado alimenta los KPI de cobranza, no sólo la decisión de sacar la
    orden.
    """
    d = diagnostico_de_reversadas(
        [
            {
                "name": "vieja",
                "amount_total": 152884.08,
                "amount_residual": 0.0,
                "payment_state": "reversed",
            },
            {
                "name": "nueva",
                "amount_total": 152884.08,
                "amount_residual": 0.0,
                "payment_state": "paid",
            },
        ]
    )
    assert d.abono_segun_la_resta == Decimal("305768.16"), "cuenta el cobro dos veces"
    assert d.abono_sin_anuladas == Decimal("152884.08")
    assert d.residual_vivo == Decimal("0")
    assert d.hay_fantasma


def test_dos_anuladas_en_la_misma_orden_suman_las_dos() -> None:
    """S00817 tiene tres facturas, dos de ellas anuladas."""
    d = diagnostico_de_reversadas(
        [
            {"name": "a", "amount_total": 100, "amount_residual": 0, "payment_state": "reversed"},
            {"name": "b", "amount_total": 200, "amount_residual": 0, "payment_state": "reversed"},
            {"name": "c", "amount_total": 300, "amount_residual": 50, "payment_state": "partial"},
        ]
    )
    assert d.numeros_anulados == ("a", "b")
    assert d.fantasma == Decimal("300")
    assert d.abono_sin_anuladas == Decimal("250")
    assert d.residual_vivo == Decimal("50")


def test_el_residual_vivo_no_cuenta_el_de_las_anuladas() -> None:
    """Una anulada tiene residual 0, pero si Odoo diera otro no debe sumarse.

    El residual de un documento anulado no es cobrable por definición: incluirlo
    diría que se debe plata sobre una factura que ya no existe.
    """
    d = diagnostico_de_reversadas(
        [
            {"name": "a", "amount_total": 100, "amount_residual": 40, "payment_state": "reversed"},
            {"name": "b", "amount_total": 100, "amount_residual": 10, "payment_state": "partial"},
        ]
    )
    assert d.residual_vivo == Decimal("10")


def test_el_numero_se_lee_del_espejo_o_de_odoo() -> None:
    """``name`` en Odoo, ``numero`` en el espejo: los dos llegan a este diagnóstico."""
    d = diagnostico_de_reversadas(
        [
            {
                "numero": "00000530",
                "amount_total": 10,
                "amount_residual": 0,
                "payment_state": "reversed",
            }
        ]
    )
    assert d.numeros_anulados == ("00000530",)


def test_una_factura_sin_numero_no_deja_el_diagnostico_sin_decir_cual() -> None:
    d = diagnostico_de_reversadas(
        [{"amount_total": 10, "amount_residual": 0, "payment_state": "reversed"}]
    )
    assert d.numeros_anulados == ("?",)
