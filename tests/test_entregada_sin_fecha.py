"""Si hay mercancía entregada, tiene que haber fecha de entrega.

Instrucción del usuario (septiembre 2026): "si no tiene fecha de entrega
que valide si los productos fueron entregados, si sí, que utilice la fecha
de la orden. Aunque si fueron entregados obligatoriamente debe tener fecha
de entrega, hay que validar eso".

Dos cosas salieron de ahí.

1. El motor ya sabía caer a la fecha de la orden: ``limite_ventana_pago``
   usa ``base = fecha_entrega or fecha_emision``. El problema era la
   guarda que lo precedía -- ``if inp.orden.fecha_entrega is None or
   ventana_pago_vigente(...)`` -- que en vez de dejar actuar ese respaldo
   SALTEABA la ventana entera: una orden sin fecha de entrega conservaba
   el descuento de contado por tarde que pagara. Se quitó.

   Medido contra producción antes de tocarlo: 141 órdenes de 941 (15 %) no
   tienen fecha de entrega y ninguna recibe hoy descuento de contado, así
   que quitar la guarda no mueve un peso -- cierra un hueco latente.

2. La validación que pidió el usuario, como detector de auditoría. De esas
   141, solo 4 tienen mercancía efectivamente entregada ($44.427,64): esas
   son las que hay que corregir en Odoo. Una es S00010 de TERA.
"""

from __future__ import annotations

from types import SimpleNamespace

from cxc.web.app import _detectar_entregada_sin_fecha_de_entrega


def _orden(so_id, fecha_entrega=None, monto=100.0):
    return SimpleNamespace(
        so_id=so_id,
        fecha_entrega=fecha_entrega,
        fecha="2026-03-16",
        cliente_id="1",
        monto_total=monto,
    )


def _linea(so_id, entregada):
    return SimpleNamespace(so_id=so_id, cantidad_entregada=entregada)


def test_entregada_sin_fecha_se_reporta() -> None:
    """El caso real S00010 (TERA): 35 unidades entregadas, sin fecha."""
    r = _detectar_entregada_sin_fecha_de_entrega(
        [_orden("S00010", monto=27781.08)], {"S00010": [_linea("S00010", 35)]}
    )
    assert len(r) == 1
    assert r[0]["so_id"] == "S00010"
    assert r[0]["unidades_entregadas"] == 35.0
    assert r[0]["monto_orden"] == 27781.08


def test_con_fecha_de_entrega_no_es_hallazgo() -> None:
    r = _detectar_entregada_sin_fecha_de_entrega(
        [_orden("S1", fecha_entrega="2026-03-20")], {"S1": [_linea("S1", 10)]}
    )
    assert r == []


def test_sin_entregar_y_sin_fecha_es_normal() -> None:
    """137 de las 141 están así: la orden existe pero no se despachó."""
    r = _detectar_entregada_sin_fecha_de_entrega([_orden("S2")], {"S2": [_linea("S2", 0)]})
    assert r == []


def test_una_orden_sin_lineas_no_es_hallazgo() -> None:
    assert _detectar_entregada_sin_fecha_de_entrega([_orden("S3")], {}) == []


def test_suma_las_unidades_de_todas_las_lineas() -> None:
    r = _detectar_entregada_sin_fecha_de_entrega(
        [_orden("S4")], {"S4": [_linea("S4", 2), _linea("S4", 3.5)]}
    )
    assert r[0]["unidades_entregadas"] == 5.5


def test_una_cantidad_negativa_no_cuenta_como_entrega() -> None:
    """Una devolución total deja la cantidad efectiva en cero o menos."""
    r = _detectar_entregada_sin_fecha_de_entrega([_orden("S5")], {"S5": [_linea("S5", -4)]})
    assert r == []
