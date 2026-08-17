"""_facturacion_por_so_desde_espejo (Fase 2 del plan de consolidación de

fuentes, agosto 2026) -- réplica, leyendo del espejo Factura, de la
agregación por-SO que ``_get_ventas_sync`` arma hoy con 3 llamadas en vivo
a Odoo. NO está conectada a ningún endpoint todavía (ver docstring de la
función) -- estos tests solo fijan que la lógica de agregación es
correcta y coincide con el comportamiento documentado de las 3 funciones
en vivo que replica, para que conectarla más adelante sea de bajo riesgo.
"""

from __future__ import annotations

from cxc.repositories import InMemoryRepository
from cxc.web.app import _facturacion_por_so_desde_espejo

from . import builders as b


def test_factura_normal_suma_a_facturado():
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura(
                "F1",
                so_id="SO1",
                move_type="out_invoice",
                estado="posted",
                monto_total_signed_usd="1160",
                monto_sin_impuestos_signed_usd="1000",
            )
        ]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert result["facturado_con_imp"] == {"SO1": 1160.0}
    assert result["facturado_antes_imp"] == {"SO1": 1000.0}
    assert result["nc_con_imp"] == {}
    assert result["nd_con_imp"] == {}


def test_nota_credito_suma_a_nc_no_a_facturado():
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura(
                "F1",
                so_id="SO1",
                move_type="out_refund",
                estado="posted",
                monto_total_signed_usd="100",
            )
        ]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert result["nc_con_imp"] == {"SO1": 100.0}
    assert result["facturado_con_imp"] == {}


def test_nota_debito_suma_a_nd_no_a_facturado():
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura(
                "F1",
                so_id="SO1",
                move_type="out_debit",
                es_nota_debito=True,
                estado="posted",
                monto_total_signed_usd="50",
            )
        ]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert result["nd_con_imp"] == {"SO1": 50.0}
    assert result["facturado_con_imp"] == {}


def test_nota_debito_sin_so_id_propio_se_resuelve_via_factura_origen():
    """Replica el comportamiento real de _leer_notas_debito_odoo: una N/D

    no siempre trae invoice_origin poblado, se resuelve por
    debit_origin_id -> so_id de la factura original."""
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura("F1", so_id="SO1", move_type="out_invoice", estado="posted"),
            b.factura(
                "F2",
                so_id=None,
                move_type="out_debit",
                es_nota_debito=True,
                estado="posted",
                monto_total_signed_usd="50",
                factura_origen_id="F1",
            ),
        ]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert result["nd_con_imp"] == {"SO1": 50.0}


def test_nota_credito_sin_so_id_propio_se_resuelve_via_factura_origen():
    """Replica _leer_notas_credito_odoo: N/C creada por el asistente normal

    de Odoo deja invoice_origin vacío, se resuelve por reversed_entry_id."""
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura("F1", so_id="SO1", move_type="out_invoice", estado="posted"),
            b.factura(
                "F2",
                so_id=None,
                move_type="out_refund",
                estado="posted",
                monto_total_signed_usd="100",
                factura_origen_id="F1",
            ),
        ]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert result["nc_con_imp"] == {"SO1": 100.0}


def test_facturas_no_posted_se_ignoran():
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura(
                "F1",
                so_id="SO1",
                move_type="out_invoice",
                estado="draft",
                monto_total_signed_usd="1160",
            )
        ]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert result["facturado_con_imp"] == {}


def test_facturas_fuera_del_conjunto_so_names_se_ignoran():
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura(
                "F1",
                so_id="SO_NO_PEDIDA",
                move_type="out_invoice",
                estado="posted",
                monto_total_signed_usd="1160",
            )
        ]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert result["facturado_con_imp"] == {}


def test_cadena_de_factura_origen_rota_no_rompe():
    """factura_origen_id apuntando a un id que no existe en el espejo no

    debe explotar -- simplemente no se puede resolver el so_id."""
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura(
                "F2",
                so_id=None,
                move_type="out_refund",
                estado="posted",
                monto_total_signed_usd="100",
                factura_origen_id="NO_EXISTE",
            )
        ]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert result["nc_con_imp"] == {}


def test_multiples_facturas_de_la_misma_orden_se_suman():
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura(
                "F1",
                so_id="SO1",
                move_type="out_invoice",
                estado="posted",
                monto_total_signed_usd="500",
                monto_sin_impuestos_signed_usd="431",
            ),
            b.factura(
                "F2",
                so_id="SO1",
                move_type="out_invoice",
                estado="posted",
                monto_total_signed_usd="300",
                monto_sin_impuestos_signed_usd="259",
            ),
        ]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert result["facturado_con_imp"] == {"SO1": 800.0}
    assert result["facturado_antes_imp"] == {"SO1": 690.0}
