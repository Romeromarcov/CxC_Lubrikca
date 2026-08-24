"""``_detectar_devolucion_no_reflejada_en_cantidad`` -- por cada orden con

devolución registrada, compara cantidad pedida vs entregada por línea
(igual que la propia pantalla de la orden en Odoo).

Corrección (agosto 2026, caso SO 00146/cliente con dos líneas: una
100% entregada y otra con un faltante real de 4 unidades, revisado con
el usuario): una versión anterior de este chequeo usaba
``linea.subtotal == 0`` como señal de devolución -- resultó ser un falso
positivo real, ``subtotal`` (``price_subtotal``) está en 0 para el 81% de
TODAS las líneas del sistema por un hueco de backfill del sync, sin
relación con devoluciones. Ahora se compara ``cantidad`` (pedida) vs
``cantidad_entregada`` directamente.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.models import LineaOrden, OrdenVenta, Producto
from cxc.web.app import _detectar_devolucion_no_reflejada_en_cantidad


def _orden(so_id, tiene_devolucion=True) -> OrdenVenta:
    return OrdenVenta(
        so_id=so_id,
        cliente_id="C1",
        vendedor_email="v@lubrikca.com",
        fecha=date(2026, 3, 24),
        fecha_entrega=date(2026, 3, 25),
        monto_total=Decimal("1996.24"),
        lista_precios="3",
        es_primera_compra=False,
        facturada=False,
        tiene_devolucion=tiene_devolucion,
    )


def _linea(so_id, linea_id, producto, cantidad, cantidad_entregada, precio_unitario) -> LineaOrden:
    return LineaOrden(
        linea_id=linea_id,
        so_id=so_id,
        producto=producto,
        marca="",
        categoria="Comercial",
        cantidad=Decimal(cantidad),
        precio_unitario=Decimal(precio_unitario),
        cantidad_entregada=Decimal(cantidad_entregada),
    )


def test_detecta_faltante_real_pero_no_la_linea_totalmente_entregada() -> None:
    """Caso real S00146: línea 1025 (10 pedidas, 10 entregadas) NO se

    detecta -- línea 1027 (10 pedidas, 6 entregadas, faltante real de 4)
    SÍ se detecta."""
    orden = _orden("S00146")
    linea_ok = _linea("S00146", "465", "1025", "10", "10", "85.45")
    linea_faltante = _linea("S00146", "466", "1027", "10", "6", "86.64")
    catalogo = [
        Producto(
            producto_id="1027",
            codigo="0605",
            nombre="SINOCO SAE 20W-50 (Paila)",
            marca="Sinoco",
            volumen=Decimal("0"),
            peso=Decimal("0"),
        )
    ]

    resultado = _detectar_devolucion_no_reflejada_en_cantidad(
        [orden], [linea_ok, linea_faltante], catalogo
    )

    assert len(resultado) == 1
    assert resultado[0]["so_id"] == "S00146"
    assert resultado[0]["linea_id"] == "466"
    assert resultado[0]["cantidad_ordenada"] == 10.0
    assert resultado[0]["cantidad_entregada"] == 6.0
    assert resultado[0]["faltante"] == 4.0
    assert resultado[0]["producto_codigo"] == "0605"
    assert resultado[0]["producto_nombre"] == "SINOCO SAE 20W-50 (Paila)"
    assert resultado[0]["valor_potencial_afectado"] == 346.56  # 4 x 86.64


def test_no_detecta_si_orden_no_tiene_devolucion() -> None:
    orden = _orden("S00002", tiene_devolucion=False)
    linea = _linea("S00002", "L1", "1056", "18", "6", "93.10")

    assert _detectar_devolucion_no_reflejada_en_cantidad([orden], [linea]) == []


def test_no_detecta_si_cantidad_entregada_igual_a_la_pedida() -> None:
    orden = _orden("S00133")
    linea = _linea("S00133", "L1", "1056", "18", "18", "93.10")

    assert _detectar_devolucion_no_reflejada_en_cantidad([orden], [linea]) == []


def test_no_detecta_si_cantidad_entregada_supera_la_pedida() -> None:
    """Entrega de más -- otra cosa a revisar, pero no un faltante de

    devolución."""
    orden = _orden("S00006")
    linea = _linea("S00006", "L1", "1056", "10", "12", "93.10")

    assert _detectar_devolucion_no_reflejada_en_cantidad([orden], [linea]) == []


def test_sin_catalogo_usa_nombre_de_la_propia_linea() -> None:
    orden = _orden("S00007")
    linea = _linea("S00007", "L1", "1056", "10", "6", "93.10")
    linea.nombre = "Producto sin match en catálogo"

    resultado = _detectar_devolucion_no_reflejada_en_cantidad([orden], [linea], catalogo=None)

    assert len(resultado) == 1
    assert resultado[0]["producto_codigo"] is None
    assert resultado[0]["producto_nombre"] == "Producto sin match en catálogo"


def test_ignora_lineas_de_ajuste_descuento() -> None:
    """Una línea "Descuento" (precio_unitario negativo, no mercancía real)

    nunca representa un faltante de devolución, aunque su cantidad
    entregada sea 0 y la ordenada sea 1."""
    orden = _orden("S00013")
    linea = _linea("S00013", "L1", "999", "1", "0", "-110.7432")

    assert _detectar_devolucion_no_reflejada_en_cantidad([orden], [linea]) == []


def test_sin_ordenes_afectadas_no_falla() -> None:
    assert _detectar_devolucion_no_reflejada_en_cantidad([], []) == []
