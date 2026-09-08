"""Una nota de crédito nunca puede acreditar más que la brecha real.

Lo pidió el usuario (septiembre 2026): "necesito garantizar al 100% que un
descuento no se sugiera 2 veces, validar si se aplicó directo en el
precio, si fue en la línea, etc".

El caso que lo destapó lo trajo él mirando S00010 (TERA): el sistema
sugería una nota de crédito de $3.949,79 cuando la brecha entre lo
facturado y lo pagado era de **-$0,52** -- habían pagado la factura
entera, porque "a esa orden en especial se le aplicó el 35% directamente
en el precio" (``rebaja_en_precio`` $4.234,30). Emitir esa NC era regalar
el descuento por segunda vez.

La solución no es rastrear canal por canal, y de hecho no puede serlo: el
usuario aclaró que las listas viejas "se usaron para VES y USD
indistintamente en el pasado", así que ni siquiera el id de la lista dice
en qué moneda nació el precio.

El techo funciona sin importar POR QUÉ CANAL se dio el descuento, porque
todos los canales terminan moviendo la misma brecha:

  · dado en el PRECIO -> la factura salió más baja, el cliente la pagó
    completa, la brecha es cero -> no hay NC.
  · dado en la LÍNEA o por una NC previa -> bajó el facturado, la brecha
    lo refleja.
  · NO dado -> la factura quedó en bruto, el cliente pagó el monto con
    descuento, y la brecha es exactamente el descuento -> la NC sale
    completa.

Medido contra producción antes del techo: 92 de 215 órdenes sugerían más
que su brecha, por $22.393,84. Después: 0 órdenes, y la bandeja quedó en
152 con $6.887,27 a acreditar.
"""

from __future__ import annotations

_IVA = 1.16


def _nc_sugerida(
    descuento_motor: float, facturado: float, pagado: float, iva: float = _IVA
) -> float:
    """El techo tal como quedó en get_bandeja_facturacion."""
    brecha = round(facturado - pagado, 2)
    if brecha <= 0.05:
        return 0.0
    return round(min(descuento_motor, brecha / iva), 2)


def test_el_caso_tera_ya_no_sugiere_nada() -> None:
    """S00010: el 35% ya estaba en el precio y pagaron la factura entera."""
    assert _nc_sugerida(descuento_motor=3949.79, facturado=27780.56, pagado=27781.08) == 0.0


def test_si_no_se_dio_el_descuento_la_nc_sale_completa() -> None:
    """Factura en bruto de $116 (100 + IVA), pagó $85 -> se acredita el
    descuento entero."""
    assert _nc_sugerida(descuento_motor=15.0, facturado=116.0, pagado=98.6) == 15.0


def test_el_descuento_ya_dado_en_el_precio_no_se_sugiere_de_nuevo() -> None:
    """La factura ya salió con el descuento, el cliente la pagó completa."""
    assert _nc_sugerida(descuento_motor=15.0, facturado=98.6, pagado=98.6) == 0.0


def test_un_descuento_parcialmente_dado_se_topa_al_resto() -> None:
    """Se dieron 10 de los 15 en el precio: solo quedan 5 por acreditar."""
    assert _nc_sugerida(descuento_motor=15.0, facturado=104.4, pagado=98.6) == 5.0


def test_pagar_de_mas_no_genera_nota_de_credito() -> None:
    """Brecha negativa: ese excedente es saldo a favor, no una NC."""
    assert _nc_sugerida(descuento_motor=500.0, facturado=100.0, pagado=150.0) == 0.0


def test_la_brecha_se_compara_en_la_misma_unidad_que_el_descuento() -> None:
    """La brecha viene CON impuesto y el descuento se calcula sobre el
    subtotal. Sin dividir por el IVA, el techo dejaría pasar un 16 % de
    más en cada orden."""
    assert _nc_sugerida(descuento_motor=100.0, facturado=116.0, pagado=0.0) == 100.0
    assert _nc_sugerida(descuento_motor=100.0, facturado=116.0, pagado=0.0) != 116.0


def test_una_brecha_de_centavos_no_dispara_una_nota() -> None:
    assert _nc_sugerida(descuento_motor=50.0, facturado=100.04, pagado=100.0) == 0.0


def test_el_techo_nunca_agranda_el_descuento() -> None:
    """Si el motor calculó poco, la brecha grande no lo infla."""
    assert _nc_sugerida(descuento_motor=5.0, facturado=1000.0, pagado=0.0) == 5.0
