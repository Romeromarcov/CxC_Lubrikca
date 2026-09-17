"""A qué vendedor se le atribuye un pago cuando el cliente cambió de vendedor.

Regla de negocio del usuario (auditoría de agosto 2026): manda el vendedor
de LA ORDEN. Si un cliente cambia de vendedor, los pagos de sus órdenes
viejas siguen siendo del vendedor que las vendió; solo cuando orden y ficha
coinciden -- o cuando no hay orden -- gobierna la ficha.

Antes era al revés (la ficha siempre ganaba), así que el vendedor nuevo se
quedaba retroactivamente con la atribución de las órdenes que no vendió.
Medido en producción: 83 de 927 órdenes con vendedor distinto al de la
ficha, con 16 Vinculaciones encima.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.models import Cliente, OrdenVenta
from cxc.web.app import resolve_vendedor_validado


def _mapas(vendedor_ficha: str, vendedor_orden: str):
    cliente = Cliente(cliente_id="C1", nombre="Cliente Uno", vendedor_email=vendedor_ficha)
    orden = OrdenVenta(
        so_id="SO1",
        cliente_id="C1",
        fecha=date(2026, 6, 1),
        fecha_entrega=None,
        monto_total=Decimal("100"),
        lista_precios="5",
        vendedor_email=vendedor_orden,
        es_primera_compra=False,
    )
    return {"C1": cliente}, {"SO1": orden}


def test_si_el_cliente_cambio_de_vendedor_el_pago_queda_con_quien_vendio() -> None:
    clientes, ordenes = _mapas(
        vendedor_ficha="nuevo@lubrikca.com", vendedor_orden="viejo@lubrikca.com"
    )
    vendedor, mismatch = resolve_vendedor_validado("C1", "SO1", clientes, ordenes)
    assert vendedor == "viejo@lubrikca.com"
    # La divergencia se sigue señalando para que un humano la revise.
    assert mismatch is True


def test_si_coinciden_se_usa_ese_vendedor() -> None:
    clientes, ordenes = _mapas(vendedor_ficha="ana@lubrikca.com", vendedor_orden="ana@lubrikca.com")
    vendedor, mismatch = resolve_vendedor_validado("C1", "SO1", clientes, ordenes)
    assert vendedor == "ana@lubrikca.com"
    assert mismatch is False


def test_pago_huerfano_sin_orden_usa_la_ficha() -> None:
    """Sin orden que lo ancle, la ficha del cliente es la única fuente."""
    clientes, ordenes = _mapas(
        vendedor_ficha="ana@lubrikca.com", vendedor_orden="beto@lubrikca.com"
    )
    vendedor, mismatch = resolve_vendedor_validado("C1", None, clientes, ordenes)
    assert vendedor == "ana@lubrikca.com"
    assert mismatch is False


def test_orden_sin_vendedor_cae_a_la_ficha() -> None:
    clientes, ordenes = _mapas(vendedor_ficha="ana@lubrikca.com", vendedor_orden="")
    vendedor, _ = resolve_vendedor_validado("C1", "SO1", clientes, ordenes)
    assert vendedor == "ana@lubrikca.com"


def test_sin_ninguno_de_los_dos() -> None:
    clientes, ordenes = _mapas(vendedor_ficha="", vendedor_orden="")
    vendedor, mismatch = resolve_vendedor_validado("C1", "SO1", clientes, ordenes)
    assert vendedor == "Sin Vendedor"
    assert mismatch is False
