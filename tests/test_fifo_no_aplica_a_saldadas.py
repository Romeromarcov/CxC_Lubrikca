"""El FIFO no puede aplicar pagos a órdenes que ya salieron de CxC.

Bug encontrado en la auditoría exhaustiva de módulos (septiembre 2026), y
no era una sugerencia inocente: el daemon auto-vincula, así que eran
aplicaciones ya hechas.

El reparto decide el destino mirando ``_get_saldos_reales_por_so_sync``,
que mide el saldo deudor contra la referencia **BCV**. Pero el árbol de
CxC saca una orden cuando cubre CUALQUIERA de sus referencias, y la más
frecuente es el **Teórico Lista USD**, que es ~35 % menor. Una orden
pagada contra su teórico USD sigue mostrando saldo en BCV, y el FIFO la
ofrecía como destino.

Medido contra producción antes del arreglo: 19 sugerencias sobre órdenes
que Ventas da por pagadas, por **$5.891,49**. Después: 0.

Las cuatro invariantes de Cobranza, todas verificadas contra producción:

    sugiere aplicar a órdenes YA pagadas       0
    sugiere más que el saldo del pago          0
    sugiere aplicar a órdenes SIN ENTREGAR     0
    pares pago+orden repetidos                 0
"""

from __future__ import annotations


def _es_destino_valido(
    saldo_real: float | None,
    salio_de_cxc: bool,
    salidas_conocidas: bool = True,
) -> bool:
    """La regla, aislada tal como quedó en get_conciliaciones_sugerencias."""
    if saldo_real is None or saldo_real <= 0.05:
        return False
    return not (salidas_conocidas and salio_de_cxc)


def test_una_orden_saldada_contra_su_teorico_usd_no_es_destino() -> None:
    """El caso real: S00807 mostraba $2.380,79 de saldo BCV y el árbol ya
    la había sacado por el Teórico Lista USD."""
    assert _es_destino_valido(saldo_real=2380.79, salio_de_cxc=True) is False


def test_una_orden_que_sigue_debiendo_si_es_destino() -> None:
    assert _es_destino_valido(saldo_real=500.0, salio_de_cxc=False) is True


def test_sin_saldo_no_es_destino_aunque_siga_en_cxc() -> None:
    assert _es_destino_valido(saldo_real=0.0, salio_de_cxc=False) is False
    assert _es_destino_valido(saldo_real=None, salio_de_cxc=False) is False


def test_si_no_se_pudo_calcular_quien_salio_no_se_bloquea_el_reparto() -> None:
    """"No sé" no es "todas salieron": tratar el error como conjunto vacío
    dejaría al FIFO sin destinos y los pagos sin aplicar."""
    assert _es_destino_valido(saldo_real=500.0, salio_de_cxc=True, salidas_conocidas=False) is True


def test_el_umbral_es_de_cinco_centavos() -> None:
    assert _es_destino_valido(saldo_real=0.05, salio_de_cxc=False) is False
    assert _es_destino_valido(saldo_real=0.06, salio_de_cxc=False) is True
