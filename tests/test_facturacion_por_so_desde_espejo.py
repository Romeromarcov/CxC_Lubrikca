"""_facturacion_por_so_desde_espejo (Fase 2 del plan de consolidación de

fuentes, agosto 2026) -- réplica, leyendo del espejo Factura, de la
agregación por-SO que ``_get_ventas_sync`` arma hoy con 3 llamadas en vivo
a Odoo. Validada con parity check completo contra 819 órdenes reales
(0 diffs) y conectada a ``_get_ventas_sync`` -- estos tests fijan que la
lógica de agregación es correcta y coincide con el comportamiento
documentado de las 3 funciones en vivo que replica.
"""

from __future__ import annotations

import pytest

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


# --- invoice_ids_all / inv_id_to_so / inv_usd_ratio_map ----------------------
# Alimentan _leer_descuentos_lineas_odoo y _pagos_bcv_binance_por_orden, que
# se quedan en vivo -- deben replicar el alcance EXACTO (más angosto) de la
# consulta principal en vivo: solo out_invoice/out_refund posted con so_id
# PROPIO, sin resolver vía cadena factura_origen_id.


def test_invoice_ids_all_ignora_factura_id_no_numerico_sin_romper():
    """En producción factura_id siempre es el id numérico de account.move

    (Odoo), pero un factura_id no numérico (ej. datos de prueba) no debe
    tumbar el endpoint -- se omite de invoice_ids_all/inv_id_to_so, la
    agregación monetaria (que no usa el id, solo el so resuelto) sigue
    funcionando igual."""
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [b.factura("F1", so_id="SO1", move_type="out_invoice", estado="posted")]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert result["invoice_ids_all"] == []
    assert result["inv_id_to_so"] == {}
    assert result["facturado_con_imp"] != {}


def test_invoice_ids_all_incluye_out_invoice_y_out_refund_con_so_id_propio():
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura("1", so_id="SO1", move_type="out_invoice", estado="posted"),
            b.factura("2", so_id="SO1", move_type="out_refund", estado="posted"),
        ]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert set(result["invoice_ids_all"]) == {1, 2}
    assert result["inv_id_to_so"] == {1: "SO1", 2: "SO1"}


def test_invoice_ids_all_excluye_nota_debito():
    """Las N/D reales tienen move_type == "out_debit" -- nunca entraban a

    invoice_ids_all en vivo tampoco (_leer_notas_debito_odoo es una
    consulta aparte que nunca alimentaba ese dict)."""
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura(
                "1",
                so_id="SO1",
                move_type="out_debit",
                es_nota_debito=True,
                estado="posted",
            )
        ]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert result["invoice_ids_all"] == []
    assert result["inv_id_to_so"] == {}


def test_invoice_ids_all_excluye_nota_credito_sin_so_id_propio():
    """Una N/C resuelta vía reversed_entry_id (sin invoice_origin propio)

    tampoco entraba a invoice_ids_all en vivo -- solo la agregación
    monetaria usa la resolución por cadena, no este dict."""
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura("1", so_id="SO1", move_type="out_invoice", estado="posted"),
            b.factura(
                "2",
                so_id=None,
                move_type="out_refund",
                estado="posted",
                factura_origen_id="1",
            ),
        ]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert set(result["invoice_ids_all"]) == {1}


def test_inv_usd_ratio_map_usd_default_uno():
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura(
                "1",
                so_id="SO1",
                move_type="out_invoice",
                estado="posted",
                monto_total="1000",
                monto_total_signed_usd="1000",
            )
        ]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert result["inv_usd_ratio_map"][1] == 1.0


def test_inv_usd_ratio_map_ves_calcula_ratio():
    """Bug real (S00010 y similares): amount_total en VES no puede

    usarse tal cual como si fuera USD -- el ratio
    amount_total_signed_usd/amount_total convierte cualquier consumidor
    downstream que reciba montos en VES a USD equivalente."""
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura(
                "1",
                so_id="SO1",
                move_type="out_invoice",
                estado="posted",
                moneda="VES",
                monto_total="3600",
                monto_total_signed_usd="100",
            )
        ]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert result["inv_usd_ratio_map"][1] == pytest.approx(100 / 3600)


def test_inv_usd_ratio_map_monto_total_cero_usa_default_uno():
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura(
                "1",
                so_id="SO1",
                move_type="out_invoice",
                estado="posted",
                monto_total="0",
                monto_total_signed_usd="0",
            )
        ]
    )
    result = _facturacion_por_so_desde_espejo(repo, {"SO1"})
    assert result["inv_usd_ratio_map"][1] == 1.0
