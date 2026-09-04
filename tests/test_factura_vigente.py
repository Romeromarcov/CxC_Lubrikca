"""Cuál es la factura VIGENTE de una orden que se facturó más de una vez.

Bug real (auditoría de agosto 2026). Una orden puede tener varias facturas
cuando se anula la primera con una Nota de Crédito por el total y se vuelve
a facturar. ``_facturas_por_origen`` pedía solo ``state = posted`` -- y una
factura anulada SIGUE posteada, lo que cambia es su ``payment_state`` a
``reversed`` -- y luego colapsaba todas las coincidencias en un dict,
quedándose con una arbitraria.

Medido en producción: 16 de 718 órdenes apuntaban a una factura anulada,
entre ellas S00573 (1.362.751), S00479 (1.113.126) y S00294 (1.605.846).

Casos reales que lo destaparon:
  · Michele Carfora (S00817): tres facturas, dos anuladas. El espejo
    apuntaba a la primera; la vigente es la tercera, ya con el descuento.
  · Talleres Leo (S00886): dos facturas, la primera anulada.
"""

from __future__ import annotations

from cxc.odoo.client import OdooXmlRpcReader


def _reader(moves):
    """Reader con el ``_search_read`` sustituido por datos fijos."""
    r = OdooXmlRpcReader.__new__(OdooXmlRpcReader)
    r._search_read = lambda modelo, dominio, campos: moves  # type: ignore[method-assign]
    return r


def test_descarta_la_factura_anulada_y_toma_la_vigente() -> None:
    """El caso S00817: tres facturas, las dos primeras revertidas."""
    reader = _reader(
        [
            {"id": 10131, "invoice_origin": "S00817", "payment_state": "reversed"},
            {"id": 10532, "invoice_origin": "S00817", "payment_state": "reversed"},
            {"id": 10558, "invoice_origin": "S00817", "payment_state": "in_payment"},
        ]
    )
    assert reader._facturas_por_origen(["S00817"]) == {"S00817": "10558"}


def test_orden_sin_ninguna_factura_viva_no_aparece() -> None:
    """Si todas se anularon y no se volvió a facturar, la orden NO tiene
    factura vigente -- queda pendiente de facturar, que es lo correcto."""
    reader = _reader(
        [
            {"id": 5764, "invoice_origin": "S00573", "payment_state": "reversed"},
            {"id": 5900, "invoice_origin": "S00573", "payment_state": "reversed"},
        ]
    )
    assert reader._facturas_por_origen(["S00573"]) == {}


def test_entre_varias_vigentes_gana_la_mas_reciente() -> None:
    """Antes el dict se quedaba con una arbitraria según el orden en que
    Odoo devolviera las filas."""
    reader = _reader(
        [
            {"id": 700, "invoice_origin": "SO1", "payment_state": "not_paid"},
            {"id": 500, "invoice_origin": "SO1", "payment_state": "not_paid"},
            {"id": 600, "invoice_origin": "SO1", "payment_state": "not_paid"},
        ]
    )
    assert reader._facturas_por_origen(["SO1"]) == {"SO1": "700"}


def test_una_nc_parcial_no_anula_la_factura() -> None:
    """Distinción clave: una NC que solo descuenta parte del saldo deja la
    factura viva (``partial``/``not_paid``). Solo ``reversed`` la anula."""
    reader = _reader(
        [{"id": 4151, "invoice_origin": "S00555", "payment_state": "partial"}]
    )
    assert reader._facturas_por_origen(["S00555"]) == {"S00555": "4151"}


def test_varias_ordenes_no_se_pisan_entre_si() -> None:
    reader = _reader(
        [
            {"id": 10131, "invoice_origin": "S00817", "payment_state": "reversed"},
            {"id": 10558, "invoice_origin": "S00817", "payment_state": "in_payment"},
            {"id": 10534, "invoice_origin": "S00886", "payment_state": "reversed"},
            {"id": 10728, "invoice_origin": "S00886", "payment_state": "not_paid"},
        ]
    )
    assert reader._facturas_por_origen(["S00817", "S00886"]) == {
        "S00817": "10558",
        "S00886": "10728",
    }


def test_sin_ordenes_no_consulta_nada() -> None:
    """Corta antes de tocar Odoo -- el reader ni siquiera se conecta."""

    def _explota(*_a, **_k):
        raise AssertionError("no debería consultar Odoo sin órdenes")

    reader = _reader([])
    reader._search_read = _explota  # type: ignore[method-assign]
    assert reader._facturas_por_origen([]) == {}
