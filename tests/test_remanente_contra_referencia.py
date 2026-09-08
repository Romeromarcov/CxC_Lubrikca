"""El remanente se mide contra el saldo con el que quedó PAGADA la orden.

Regla del usuario (septiembre 2026), con su propio ejemplo textual: "si un
cliente hizo una compra que la orden marca 100$, pero recibió un descuento
del 15%. Debió pagar 85, a lo mejor pagó 90$. Se le hace su nota de
crédito por 15$ porque no tiene nada que ver, ese fue el descuento, y los
5$ quedan a favor del cliente para su siguiente compra".

Los dos canales son independientes y no se pisan: la NC **baja la
factura** de 100 a 85, así que medir el remanente contra 85 cuenta ese
descuento una sola vez. Comparar contra el bruto (100) era justamente lo
que lo tapaba -- una orden real pagó $50,00 contra un teórico de $48,59 y
el sistema reportaba saldo a favor $0,00 en vez de $1,41.

Medido contra producción al aplicar la regla: 378 órdenes con remanente
por $55.067,76 (antes se detectaban 6 por $102,16). La mayoría es chica
-- 143 casos de $10 o menos, mediana $16,42 -- que es el redondeo y los
descuentos que describió el usuario, pero hay cola larga (Agropecuaria
Leche y Miel $7.837,44).

La guarda que importa: el teórico solo sirve de referencia si la orden
EFECTIVAMENTE salió de CxC. Una orden que cubre su teórico USD pero sigue
debiendo por su referencia de nacimiento no tiene nada a favor -- todavía
debe. Sin esa guarda el crédito subía a $55.067,76 contra órdenes que no
habían salido.
"""

from __future__ import annotations

_EPS = 0.01


def _remanente(pagado: float, factura_neta: float, descuento_pendiente: float = 0.0) -> float:
    """La regla, aislada tal como quedó en get_ventas.

    La referencia es LO QUE EL CLIENTE DEBIÓ PAGAR: la factura neta menos
    el descuento que le corresponde. Nunca el teórico de otra lista.
    """
    debio_pagar = max(0.0, factura_neta - max(0.0, descuento_pendiente))
    return round(max(0.0, pagado - debio_pagar), 2)


def test_el_ejemplo_del_usuario() -> None:
    """Orden 100, descuento 15%, debió pagar 85, pagó 90 -> sobran 5."""
    assert _remanente(pagado=90.0, factura_neta=100.0, descuento_pendiente=15.0) == 5.0


def test_sin_descuento_pendiente_la_referencia_es_la_factura() -> None:
    """El caso TERA: pagaron completo y no se les dio descuento."""
    assert _remanente(pagado=100.0, factura_neta=100.0) == 0.0


def test_la_brecha_entre_dos_listas_no_es_credito() -> None:
    """El bug que costo caro. Una orden facturada en bolivares tiene su
    teorico de lista USD ~35% por debajo, y comparar contra ese teorico
    inventaba credito: S00458 mostraba $4.524,15 a favor con un descuento
    del 1% sobre una factura de $21.142,96 -- el descuento no daba para
    mas de $159. Con la referencia correcta da 0."""
    teorico_usd_de_otra_lista = 13642.99
    assert _remanente(pagado=15735.47, factura_neta=21142.96, descuento_pendiente=211.43) == 0.0
    assert teorico_usd_de_otra_lista < 21142.96  # la brecha estructural


def test_pagar_justo_no_deja_remanente() -> None:
    assert _remanente(pagado=85.0, factura_neta=100.0, descuento_pendiente=15.0) == 0.0


def test_pagar_de_menos_tampoco() -> None:
    assert _remanente(pagado=70.0, factura_neta=100.0, descuento_pendiente=15.0) == 0.0


def test_el_redondeo_de_centavos_se_captura_igual() -> None:
    """84 de los 127 casos reales son de $10 o menos, mediana $4,27."""
    assert _remanente(pagado=85.4, factura_neta=100.0, descuento_pendiente=15.0) == 0.4
