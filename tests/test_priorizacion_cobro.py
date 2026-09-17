"""La urgencia de cobro sale de la Venta Real y del Teórico Lista USD.

Decisión del usuario (septiembre 2026). Antes ``saldo_priorizacion`` era el
máximo de las 4 columnas (Teórico BS, Teórico USD, Venta Real, Factura Neta
Real), y eso dejaba que el **Teórico Lista BS** fijara la urgencia de buena
parte de la cartera: es casi siempre el mayor de los cuatro -- verificado
contra producción, supera al Teórico USD en las 789 órdenes medibles.

El Teórico BS existe para auditar y para calcular descuentos, y en el árbol
de CxC es el último eslabón de la decisión. No es la cifra con la que se
sale a cobrar. La Factura Neta Real queda fuera por otra razón: es lo ya
facturado, no lo que falta cobrar de la orden.

Las dos referencias que quedan son exactamente las que la tarjeta muestra,
así que el tamaño y el color dejan de depender de un número que el usuario
no puede ver.
"""

from __future__ import annotations

from cxc.web.app import saldo_priorizacion_cliente


def _saldos(teorico_bs=0.0, teorico_usd=0.0, venta_real=0.0, factura_real=0.0):
    return {
        "teorico_bs": teorico_bs,
        "teorico_usd": teorico_usd,
        "venta_real": venta_real,
        "factura_real": factura_real,
    }


def test_el_teorico_bs_ya_no_fija_la_urgencia() -> None:
    """Caso real SJMG 2012 C.A: el Teórico BS (19.390,08) era el mayor de
    los cuatro y mandaba. Ahora manda la Venta Real (17.316,27)."""
    s = _saldos(
        teorico_bs=19390.08, teorico_usd=8572.66, venta_real=17316.27, factura_real=2059.14
    )
    assert saldo_priorizacion_cliente(s) == 17316.27


def test_la_factura_neta_real_tampoco_cuenta() -> None:
    s = _saldos(teorico_usd=100.0, venta_real=200.0, factura_real=9999.0)
    assert saldo_priorizacion_cliente(s) == 200.0


def test_manda_el_mayor_entre_las_dos_referencias_visibles() -> None:
    assert saldo_priorizacion_cliente(_saldos(teorico_usd=500.0, venta_real=200.0)) == 500.0
    assert saldo_priorizacion_cliente(_saldos(teorico_usd=200.0, venta_real=500.0)) == 500.0


def test_un_cliente_sin_saldo_no_pesa() -> None:
    assert saldo_priorizacion_cliente(_saldos()) == 0.0
    assert saldo_priorizacion_cliente({}) == 0.0


def test_un_cliente_al_que_solo_le_queda_el_teorico_bs_deja_de_priorizarse() -> None:
    """Su orden ya está cubierta contra la Venta Real y contra el Teórico
    USD: lo que resta contra el Teórico BS es materia de auditoría y
    descuentos, no una cobranza que perseguir."""
    assert saldo_priorizacion_cliente(_saldos(teorico_bs=5000.0)) == 0.0


def test_tolera_valores_nulos_o_ausentes() -> None:
    assert saldo_priorizacion_cliente({"venta_real": None, "teorico_usd": 42.0}) == 42.0
    assert saldo_priorizacion_cliente({"teorico_usd": 42.0}) == 42.0


def test_redondea_a_dos_decimales() -> None:
    assert saldo_priorizacion_cliente(_saldos(venta_real=10.005, teorico_usd=1.0)) == 10.01
