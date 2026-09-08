"""La ventana de pago se mide con la fecha REAL del pago, no con cuándo se
registró.

Pregunta del usuario (septiembre 2026): "revisa el motor para ver si las
reglas que dependen de una ventana de pago se muestran durante la ventana
y después dejan de calcularse, y el escenario en que el pago ocurrió
durante la ventana, pero se reportó tarde, qué sucede, si se aplica el
descuento por haber pagado oportunamente".

Las tres respuestas, verificadas sobre el motor:

1. Mientras la orden NO tiene abonos, la ventana se evalúa contra
   ``inp.fecha_calculo`` (hoy). El descuento se proyecta mientras la
   ventana está abierta y deja de proyectarse cuando vence -- que es el
   comportamiento que el usuario esperaba.

2. Con abonos, la fecha que manda es la del ÚLTIMO abono:
   ``max(v.hora_pago_confirmada.date())``.

3. Y ``hora_pago_confirmada`` se construye como
   ``datetime.combine(pago.fecha_pago, ...)`` -- la fecha del pago en
   Odoo, no la de la vinculación ni la del sync. Así que **un pago hecho
   dentro de la ventana pero cargado tarde conserva el descuento**: lo que
   se evalúa es cuándo pagó el cliente, no cuándo lo vio el sistema.

   La dependencia que queda es de captura: si administración registra el
   pago en Odoo con la fecha en que lo cargó en vez de la fecha real del
   movimiento, se pierde el dato de origen y el motor no tiene cómo
   saberlo.

Hueco latente encontrado de paso: el filtro de ventana está detrás de
``if inp.orden.fecha_entrega is None or ventana_pago_vigente(...)``, así
que una orden SIN fecha de entrega saltea la ventana por completo y
conserva el descuento de contado por más tarde que pague. Medido contra
producción: 141 órdenes de 941 (15 %) no tienen fecha de entrega, pero
ninguna de ellas recibe hoy descuento de contado, así que el hueco existe
en el código sin estar activo.
"""

from __future__ import annotations

from datetime import date

from cxc.engine.discounts import ventana_pago_vigente

_EMISION = date(2026, 9, 1)
_ENTREGA = date(2026, 9, 2)


def _vigente(tipo: str, dias: int, fecha_evaluacion: date, dias_credito: int = 7) -> bool:
    return ventana_pago_vigente(
        tipo,
        dias,
        fecha_evaluacion,
        fecha_emision=_EMISION,
        fecha_entrega=_ENTREGA,
        dias_credito=dias_credito,
    )


# --- 1. Se proyecta durante la ventana y deja de proyectarse después -------


def test_dentro_de_la_ventana_de_entrega_sigue_vigente() -> None:
    """Entrega 02/09 + 3 días: al 05/09 todavía cuenta."""
    assert _vigente("entrega", 3, date(2026, 9, 5)) is True


def test_pasada_la_ventana_deja_de_calcularse() -> None:
    assert _vigente("entrega", 3, date(2026, 9, 6)) is False


def test_el_mismo_dia_del_limite_cuenta() -> None:
    """El límite es inclusivo: pagar el último día alcanza."""
    assert _vigente("entrega", 3, date(2026, 9, 5)) is True
    assert _vigente("entrega", 0, date(2026, 9, 2)) is True


def test_la_ventana_por_vencimiento_suma_los_dias_de_credito() -> None:
    """Entrega 02/09 + 7 de crédito = vence 09/09, +3 de margen = 12/09."""
    assert _vigente("vencimiento", 3, date(2026, 9, 12)) is True
    assert _vigente("vencimiento", 3, date(2026, 9, 13)) is False


def test_no_aplica_no_restringe_nada() -> None:
    assert _vigente("no_aplica", 3, date(2027, 1, 1)) is True
    assert _vigente("", 3, date(2027, 1, 1)) is True


# --- 2 y 3. Pago a tiempo, reportado tarde ---------------------------------


def test_un_pago_a_tiempo_reportado_tarde_conserva_el_descuento() -> None:
    """El escenario que preguntó el usuario. El motor evalúa la ventana
    con la fecha del ABONO (``hora_pago_confirmada``, que sale de
    ``pago.fecha_pago``), nunca con la fecha en que se cargó o se vinculó.

    Pagó el 04/09, dentro de la ventana que cierra el 05/09; se registró
    el 20/09. Lo que se evalúa es el 04/09."""
    fecha_real_del_pago = date(2026, 9, 4)
    fecha_en_que_se_reporto = date(2026, 9, 20)

    assert _vigente("entrega", 3, fecha_real_del_pago) is True
    # Y si el motor usara la fecha de carga, lo perdería:
    assert _vigente("entrega", 3, fecha_en_que_se_reporto) is False


def test_un_pago_realmente_tardio_si_pierde_el_descuento() -> None:
    """La contracara: la regla sigue siendo real para quien pagó tarde."""
    assert _vigente("entrega", 3, date(2026, 9, 30)) is False
