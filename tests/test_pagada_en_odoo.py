"""Las dos definiciones de «pagada en Odoo» (Fase 2.4, duodécima pieza).

`so_pagada_en_odoo` se calculaba en tres sitios de `app.py` y **uno usaba otra
regla**: el reporte de saldos y la auditoría preguntan por `payment_state`, las
sugerencias de conciliación suman `amount_residual_usd`. El nombre compartido lo
escondía.

Medido sobre la copia de producción: **66 de 796 órdenes con factura discrepan**,
y las tres causas apuntan a lados distintos sobre cuál regla es la correcta. Estos
tests fijan las dos lecturas y las tres causas; **no eligen ninguna**, porque
elegir mueve el universo de tres pantallas.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cxc.engine.pagada_en_odoo import (
    TOLERANCIA_RESIDUAL,
    diagnostico_de_pagada,
    pagada_por_estado,
    pagada_por_residual,
)


def _f(estado, residual="0"):
    return {"payment_state": estado, "amount_residual_usd": residual}


# --- la regla por estado ----------------------------------------------------


@pytest.mark.parametrize("estado", ["paid", "in_payment", "PAID", " In_Payment "])
def test_los_estados_pagados_cuentan_y_se_comparan_sin_formato(estado) -> None:
    assert pagada_por_estado([_f(estado)])


@pytest.mark.parametrize("estado", ["not_paid", "partial", "reversed", "", None])
def test_ningun_otro_estado_cuenta_como_pagado(estado) -> None:
    assert not pagada_por_estado([_f(estado)])


def test_tienen_que_estar_pagadas_TODAS() -> None:
    """Si queda una sin pagar, la orden sigue visible.

    Es la mitad que importa de esta regla: con ``any`` en vez de ``all``, una
    orden con dos facturas y una sola cobrada saldría de la cuenta por cobrar
    debiendo la otra.
    """
    assert pagada_por_estado([_f("paid"), _f("paid")])
    assert not pagada_por_estado([_f("paid"), _f("partial")])


def test_sin_facturas_la_regla_por_estado_dice_que_NO() -> None:
    """Una orden sin factura no está pagada: está sin facturar.

    Confundirlas la sacaría de la cuenta por cobrar sin que nadie haya cobrado.
    """
    assert not pagada_por_estado([])


# --- la regla por residual --------------------------------------------------


def test_el_residual_se_suma_entre_todas_las_facturas() -> None:
    assert pagada_por_residual([_f("partial", "0.02"), _f("partial", "0.02")])
    assert not pagada_por_residual([_f("partial", "0.04"), _f("partial", "0.04")])


def test_la_tolerancia_es_el_borde_y_esta_adentro() -> None:
    assert pagada_por_residual([_f("partial", str(TOLERANCIA_RESIDUAL))])
    assert not pagada_por_residual([_f("partial", "0.06")])


def test_sin_facturas_la_regla_por_residual_dice_que_SI() -> None:
    """La diferencia de fondo entre las dos, preservada a propósito.

    Una suma vacía es cero y cero cabe en la tolerancia, así que esta regla dice
    «pagada» sobre una orden sin facturar. Está fijado porque es la divergencia
    más silenciosa de las cuatro: no hay ningún estado raro que la delate.
    """
    assert pagada_por_residual([])
    assert not pagada_por_estado([]), "las dos difieren justo acá"


def test_un_residual_ilegible_vale_cero_y_no_revienta() -> None:
    """XML-RPC manda ``False`` por un campo vacío, no ``None``."""
    assert pagada_por_residual([{"payment_state": "partial", "amount_residual_usd": False}])
    assert pagada_por_residual([{"payment_state": "partial", "amount_residual_usd": "ilegible"}])


# --- el diagnóstico, y las tres causas medidas ------------------------------


def test_cuando_coinciden_lo_dice_sin_inventar_una_causa() -> None:
    d = diagnostico_de_pagada([_f("paid")])
    assert d.coinciden
    assert (d.por_estado, d.por_residual) == (True, True)
    assert d.causa == "coinciden"
    assert "dicen pagada" in d.nota


def test_causa_1_la_factura_anulada_medida_en_15_ordenes() -> None:
    """S00009 y compañía: ``['paid', 'reversed']`` con residual cero.

    Acá la regla del residual está **mal**: el cero viene de la nota de crédito
    que reversó la factura, no de un bolívar que entró. Es el mismo defecto del
    abono fantasma, en otro lugar.
    """
    d = diagnostico_de_pagada([_f("paid", "0"), _f("reversed", "0")])
    assert not d.coinciden
    assert (d.por_estado, d.por_residual) == (False, True)
    assert d.causa == "factura_anulada"
    assert "MAL" in d.nota


def test_causa_2_el_residual_de_centavos_medido_en_47_ordenes() -> None:
    """S00018, S00039 y compañía: ``partial`` con 0,01 a 0,03 de residual.

    Acá la razonable es la del residual: un centavo no es una deuda, y la de
    estado deja la orden en la cuenta por cobrar para siempre.
    """
    d = diagnostico_de_pagada([_f("partial", "0.03")])
    assert not d.coinciden
    assert (d.por_estado, d.por_residual) == (False, True)
    assert d.causa == "residual_de_centavos"
    assert "razonable" in d.nota


def test_causa_3_el_sobrepago_medido_en_4_ordenes_y_258_74_usd() -> None:
    """S00188, S00795, S00182 y S00061: ``partial`` con residual NEGATIVO.

    Odoo dice «parcial» y el residual es −116,69: se cobró más de lo facturado.
    **Ninguna de las dos reglas dice «hay saldo a favor»** — una lo llama pagado y
    la otra impago, y las dos se pierden la plata a devolver.
    """
    d = diagnostico_de_pagada([_f("partial", "-116.69")])
    assert not d.coinciden
    assert d.causa == "sobrepago"
    assert d.hay_sobrepago
    assert d.residual_total == Decimal("-116.69")
    assert "saldo a favor" in d.nota


def test_el_sobrepago_se_detecta_aunque_las_dos_reglas_coincidan() -> None:
    """Porque es un hallazgo aparte de la divergencia, no una consecuencia.

    Una orden con una factura ``paid`` y residual negativo tiene a las dos reglas
    de acuerdo en «pagada» — y sigue habiendo plata de más. Si ``hay_sobrepago``
    dependiera de que difieran, esos casos se perderían.
    """
    d = diagnostico_de_pagada([_f("paid", "-50.00")])
    assert d.coinciden
    assert d.hay_sobrepago


def test_sin_facturas_el_diagnostico_nombra_esa_causa_aparte() -> None:
    d = diagnostico_de_pagada([])
    assert not d.coinciden
    assert d.causa == "sin_facturas"
    assert "sin facturar" in d.nota


def test_una_divergencia_que_no_es_ninguna_de_las_tres_se_marca_para_mirar() -> None:
    """El instrumento no fuerza el caso raro dentro de una causa conocida.

    La única forma de que difieran sin caer en las tres causas es al revés: la
    regla por **estado** dice pagada y la del residual no — una factura ``paid``
    con residual por encima de la tolerancia. **Medido: cero órdenes en la copia
    de producción**, lo cual es coherente (Odoo no marca ``paid`` con residual).
    Pero el caso existe en el código, así que el diagnóstico dice «mirala a mano»
    en vez de meterlo en una causa que no es.

    Escribí primero este test con ``[paid 0, not_paid 0]`` esperando «otra», y el
    código tenía razón: residual cero **está** dentro de la tolerancia, así que esa
    combinación es la causa de centavos. Queda anotado porque es el tipo de error
    que hace creer que un clasificador está mal cuando el mal está en el ejemplo.
    """
    d = diagnostico_de_pagada([_f("paid", "10.00")])
    assert not d.coinciden
    assert (d.por_estado, d.por_residual) == (True, False)
    assert d.causa == "otra"
    assert "mirarla a mano" in d.nota


def test_residual_cero_con_una_factura_impaga_es_la_causa_de_centavos() -> None:
    """Lo que sí hace esa combinación, fijado aparte para que no se confunda."""
    d = diagnostico_de_pagada([_f("paid", "0"), _f("not_paid", "0")])
    assert not d.coinciden
    assert d.causa == "residual_de_centavos"


def test_el_diagnostico_guarda_los_estados_para_poder_ir_a_ver() -> None:
    d = diagnostico_de_pagada([_f("PAID", "0"), _f("Reversed", "0")])
    assert d.estados == ("paid", "reversed"), "normalizados, para comparar sin sorpresas"


def test_el_diagnostico_no_elige_ninguna_de_las_dos() -> None:
    """Es un instrumento, no una corrección.

    Importa que quede escrito: unificar mueve el universo de órdenes de tres
    pantallas, y la medición dice que **ninguna de las dos es correcta en los tres
    casos** — la respuesta buena es por estado, más una tolerancia de centavos,
    más una señal de sobrepago. Eso es una decisión del usuario.
    """
    facturas = [_f("reversed", "0")]
    d = diagnostico_de_pagada(facturas)
    # Las dos lecturas siguen disponibles y el módulo no privilegia ninguna.
    assert pagada_por_estado(facturas) is False
    assert pagada_por_residual(facturas) is True
    assert (d.por_estado, d.por_residual) == (False, True)
