"""``_detectar_devolucion_no_reflejada_en_cantidad`` -- bug real (reportado

por el usuario, agosto 2026, caso SO 00133/Inversiones La Bendición del
Nazareno, 12 órdenes reales corregidas): una devolución puede reflejarse
en Odoo poniendo el precio/``price_subtotal`` de la línea en $0 en vez de
bajar ``cantidad_entregada`` -- el motor de teóricos (``_cantidad_
efectiva``) ya respeta esta señal, pero nadie la vigila hacia adelante.
Este chequeo expone cualquier devolución futura mal reflejada.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.models import LineaOrden, OrdenVenta
from cxc.web.app import _detectar_devolucion_no_reflejada_en_cantidad


def _orden(so_id, tiene_devolucion=True, entregada_completa=True) -> OrdenVenta:
    return OrdenVenta(
        so_id=so_id,
        cliente_id="C1",
        vendedor_email="v@lubrikca.com",
        fecha=date(2026, 3, 24),
        fecha_entrega=date(2026, 3, 25),
        monto_total=Decimal("10843.49"),
        lista_precios="3",
        es_primera_compra=False,
        facturada=False,
        tiene_devolucion=tiene_devolucion,
        entregada_completa=entregada_completa,
    )


def _linea(so_id, subtotal, precio_unitario, cantidad_entregada) -> LineaOrden:
    return LineaOrden(
        linea_id=f"L_{so_id}",
        so_id=so_id,
        producto="1056",
        marca="",
        categoria="Comercial",
        cantidad=Decimal(cantidad_entregada),
        precio_unitario=Decimal(precio_unitario),
        cantidad_entregada=Decimal(cantidad_entregada),
        subtotal=Decimal(subtotal),
    )


def test_detecta_linea_con_precio_cero_y_cantidad_sin_bajar() -> None:
    orden = _orden("S00133")
    linea = _linea("S00133", "0", "93.10", "18")

    resultado = _detectar_devolucion_no_reflejada_en_cantidad([orden], [linea])

    assert len(resultado) == 1
    assert resultado[0]["so_id"] == "S00133"
    assert resultado[0]["valor_potencial_afectado"] == 1675.80


def test_no_detecta_si_subtotal_coincide_con_cantidad_precio() -> None:
    orden = _orden("S00001")
    linea = _linea("S00001", "1675.80", "93.10", "18")  # subtotal real, no cero

    assert _detectar_devolucion_no_reflejada_en_cantidad([orden], [linea]) == []


def test_no_detecta_si_orden_no_tiene_devolucion() -> None:
    orden = _orden("S00002", tiene_devolucion=False)
    linea = _linea("S00002", "0", "93.10", "18")

    assert _detectar_devolucion_no_reflejada_en_cantidad([orden], [linea]) == []


def test_no_detecta_si_orden_no_esta_entregada_completa() -> None:
    orden = _orden("S00003", entregada_completa=False)
    linea = _linea("S00003", "0", "93.10", "18")

    assert _detectar_devolucion_no_reflejada_en_cantidad([orden], [linea]) == []


def test_no_detecta_linea_legitimamente_gratis() -> None:
    """precio_unitario=0 desde el origen (producto promocional/gratis) no

    es una devolución mal reflejada -- nunca tuvo precio que cobrar."""
    orden = _orden("S00004")
    linea = _linea("S00004", "0", "0", "18")

    assert _detectar_devolucion_no_reflejada_en_cantidad([orden], [linea]) == []


def test_no_detecta_si_cantidad_entregada_ya_esta_en_cero() -> None:
    """Si Odoo SÍ bajó cantidad_entregada a 0 para la devolución, el

    comportamiento es correcto -- no hay nada que detectar."""
    orden = _orden("S00005")
    linea = _linea("S00005", "0", "93.10", "0")

    assert _detectar_devolucion_no_reflejada_en_cantidad([orden], [linea]) == []


def test_sin_ordenes_afectadas_no_falla() -> None:
    assert _detectar_devolucion_no_reflejada_en_cantidad([], []) == []
