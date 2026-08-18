"""_descuentos_lineas_desde_espejo (Fase 4/5 del plan de consolidación de

fuentes, agosto 2026) -- réplica, leyendo del espejo (LineaOrden/
LineaFactura), de _leer_descuentos_lineas_odoo. NO está conectada a
ningún endpoint todavía -- estos tests fijan que la reconstrucción es
correcta antes de conectarla.
"""

from __future__ import annotations

from cxc.repositories import InMemoryRepository
from cxc.web.app import _descuentos_lineas_desde_espejo

from . import builders as b


def test_descuento_porcentaje_en_linea_de_orden():
    repo = InMemoryRepository()
    repo.upsert_lineas([b.linea("L1", so_id="SO1", cantidad="2", precio="100", descuento="10")])
    desc_orden, desc_factura = _descuentos_lineas_desde_espejo(repo, {"SO1"}, [], {})
    assert desc_orden == {"SO1": 20.0}
    assert desc_factura == {}


def test_linea_descuento_separada_con_subtotal_negativo_en_orden():
    repo = InMemoryRepository()
    repo.upsert_lineas(
        [
            b.linea(
                "L1",
                so_id="SO1",
                cantidad="1",
                precio="0",
                descuento="0",
                nombre="Descuento",
                subtotal="-15",
            )
        ]
    )
    desc_orden, _ = _descuentos_lineas_desde_espejo(repo, {"SO1"}, [], {})
    assert desc_orden == {"SO1": 15.0}


def test_linea_negativa_sin_nombre_descuento_se_ignora():
    """Una línea con subtotal negativo por otra razón (ej. una devolución

    parcial) NO debe contarse como descuento -- mismo criterio que la
    consulta en vivo, que filtra por product_id.name ilike 'descuento'."""
    repo = InMemoryRepository()
    repo.upsert_lineas(
        [
            b.linea(
                "L1",
                so_id="SO1",
                cantidad="1",
                precio="0",
                descuento="0",
                nombre="Ajuste de inventario",
                subtotal="-15",
            )
        ]
    )
    desc_orden, _ = _descuentos_lineas_desde_espejo(repo, {"SO1"}, [], {})
    assert desc_orden == {}


def test_linea_sin_descuento_ni_subtotal_negativo_se_ignora():
    repo = InMemoryRepository()
    repo.upsert_lineas([b.linea("L1", so_id="SO1", cantidad="1", precio="100", descuento="0")])
    desc_orden, _ = _descuentos_lineas_desde_espejo(repo, {"SO1"}, [], {})
    assert desc_orden == {}


def test_lineas_fuera_del_conjunto_so_names_se_ignoran():
    repo = InMemoryRepository()
    repo.upsert_lineas(
        [b.linea("L1", so_id="SO_NO_PEDIDA", cantidad="1", precio="100", descuento="10")]
    )
    desc_orden, _ = _descuentos_lineas_desde_espejo(repo, {"SO1"}, [], {})
    assert desc_orden == {}


def test_descuento_porcentaje_en_linea_de_factura():
    repo = InMemoryRepository()
    repo.upsert_lineas_factura(
        [
            b.linea_factura(
                "LF1", factura_id="900", cantidad="2", precio_unitario="100", descuento="5"
            )
        ]
    )
    _, desc_factura = _descuentos_lineas_desde_espejo(repo, set(), [900], {900: "SO1"})
    assert desc_factura == {"SO1": 10.0}


def test_linea_descuento_separada_en_factura():
    repo = InMemoryRepository()
    repo.upsert_lineas_factura(
        [
            b.linea_factura(
                "LF1",
                factura_id="900",
                nombre="Descuento",
                cantidad="1",
                precio_unitario="0",
                descuento="0",
                subtotal="-30",
            )
        ]
    )
    _, desc_factura = _descuentos_lineas_desde_espejo(repo, set(), [900], {900: "SO1"})
    assert desc_factura == {"SO1": 30.0}


def test_descuento_factura_aplica_ratio_usd():
    """Bug real (S00010): account.move.line viene en la moneda de la

    factura (a veces VES) -- el ratio amount_total_signed_usd/amount_total
    convierte a USD equivalente."""
    repo = InMemoryRepository()
    repo.upsert_lineas_factura(
        [
            b.linea_factura(
                "LF1",
                factura_id="900",
                cantidad="1",
                precio_unitario="0",
                descuento="0",
                subtotal="-3600",
                nombre="Descuento",
            )
        ]
    )
    _, desc_factura = _descuentos_lineas_desde_espejo(
        repo, set(), [900], {900: "SO1"}, inv_usd_ratio_map={900: 0.1}
    )
    assert desc_factura == {"SO1": 360.0}


def test_descuento_factura_sin_ratio_usa_default_uno():
    repo = InMemoryRepository()
    repo.upsert_lineas_factura(
        [
            b.linea_factura(
                "LF1", factura_id="900", cantidad="1", precio_unitario="100", descuento="10"
            )
        ]
    )
    _, desc_factura = _descuentos_lineas_desde_espejo(repo, set(), [900], {900: "SO1"})
    assert desc_factura == {"SO1": 10.0}


def test_linea_factura_fuera_de_invoice_ids_se_ignora():
    repo = InMemoryRepository()
    repo.upsert_lineas_factura(
        [
            b.linea_factura(
                "LF1", factura_id="999", cantidad="1", precio_unitario="100", descuento="10"
            )
        ]
    )
    _, desc_factura = _descuentos_lineas_desde_espejo(repo, set(), [900], {900: "SO1"})
    assert desc_factura == {}


def test_linea_factura_id_no_numerico_no_rompe():
    repo = InMemoryRepository()
    repo.upsert_lineas_factura(
        [
            b.linea_factura(
                "LF1", factura_id="ABC", cantidad="1", precio_unitario="100", descuento="10"
            )
        ]
    )
    _, desc_factura = _descuentos_lineas_desde_espejo(repo, set(), [900], {900: "SO1"})
    assert desc_factura == {}


def test_multiples_lineas_de_la_misma_orden_se_suman():
    repo = InMemoryRepository()
    repo.upsert_lineas(
        [
            b.linea("L1", so_id="SO1", cantidad="1", precio="100", descuento="10"),
            b.linea("L2", so_id="SO1", cantidad="2", precio="50", descuento="20"),
        ]
    )
    desc_orden, _ = _descuentos_lineas_desde_espejo(repo, {"SO1"}, [], {})
    assert desc_orden == {"SO1": 10.0 + 20.0}
