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


def _remanente(pagado: float, base_bruta: float, teorico: float | None, salio: bool) -> float:
    """La regla, aislada tal como quedó en get_ventas."""
    candidatos = [base_bruta]
    if salio and teorico is not None and teorico > _EPS:
        candidatos.append(teorico)
    validos = [v for v in candidatos if v > _EPS]
    referencia = min(validos) if validos else base_bruta
    return round(max(0.0, pagado - referencia), 2)


def test_el_ejemplo_del_usuario() -> None:
    """Orden 100, descuento 15%, debió pagar 85, pagó 90 -> sobran 5."""
    assert _remanente(pagado=90.0, base_bruta=100.0, teorico=85.0, salio=True) == 5.0


def test_el_caso_real_que_se_reportaba_en_cero() -> None:
    """Pagó $50,00 contra un teórico de $48,59."""
    assert _remanente(pagado=50.0, base_bruta=83.07, teorico=48.59, salio=True) == 1.41


def test_contra_el_bruto_el_remanente_desaparecia() -> None:
    """Lo que hacía el cálculo anterior: comparar contra la factura."""
    assert _remanente(pagado=50.0, base_bruta=83.07, teorico=None, salio=True) == 0.0


def test_una_orden_que_no_salio_de_cxc_no_tiene_nada_a_favor() -> None:
    """Cubre su teórico USD pero sigue debiendo por su nacimiento: debe,
    no tiene crédito. Sin esta guarda se acreditaban órdenes impagas."""
    assert _remanente(pagado=90.0, base_bruta=100.0, teorico=85.0, salio=False) == 0.0


def test_pagar_justo_no_deja_remanente() -> None:
    assert _remanente(pagado=85.0, base_bruta=100.0, teorico=85.0, salio=True) == 0.0


def test_pagar_de_menos_tampoco() -> None:
    assert _remanente(pagado=70.0, base_bruta=100.0, teorico=85.0, salio=True) == 0.0


def test_el_redondeo_de_centavos_se_captura_igual() -> None:
    """143 de los 378 casos reales son de $10 o menos."""
    assert _remanente(pagado=85.4, base_bruta=100.0, teorico=85.0, salio=True) == 0.4


def test_manda_la_referencia_mas_baja() -> None:
    """Si la factura ya quedó por debajo del teórico, gana la factura."""
    assert _remanente(pagado=90.0, base_bruta=80.0, teorico=85.0, salio=True) == 10.0
