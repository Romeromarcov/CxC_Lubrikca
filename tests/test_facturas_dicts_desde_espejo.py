"""_facturas_dicts_desde_espejo / _estado_pago_facturas_desde_odoo

(Fase 4 del plan de consolidación de fuentes, agosto 2026) -- reconstruye
por-factura, desde el espejo, el mismo shape de dict que
_get_reporte_saldos_sync/get_auditoria arman hoy consultando account.move
en vivo, con un overlay EN VIVO acotado solo para amount_residual/
payment_state (los únicos 2 campos genuinamente mutables). NO están
conectadas a ningún endpoint todavía -- estos tests solo fijan que la
reconstrucción es correcta antes de conectarlas.
"""

from __future__ import annotations

from cxc.repositories import InMemoryRepository
from cxc.web.app import _estado_pago_facturas_desde_odoo, _facturas_dicts_desde_espejo

from . import builders as b


def test_factura_normal_produce_dict_con_shape_esperado():
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura(
                "900",
                numero="FAC/900",
                so_id="SO1",
                move_type="out_invoice",
                estado="posted",
                moneda="USD",
                monto_total="500",
            )
        ]
    )
    result = _facturas_dicts_desde_espejo(repo, {"SO1"})
    assert len(result) == 1
    d = result[0]
    assert d["id"] == 900
    assert d["name"] == "FAC/900"
    assert d["invoice_origin"] == "SO1"
    assert d["amount_total"] == 500.0
    assert d["amount_residual"] is None
    assert d["currency_id"] == [0, "USD"]
    assert d["move_type"] == "out_invoice"
    assert d["payment_state"] == ""


def test_incluye_out_refund():
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [b.factura("900", so_id="SO1", move_type="out_refund", estado="posted")]
    )
    result = _facturas_dicts_desde_espejo(repo, {"SO1"})
    assert len(result) == 1
    assert result[0]["move_type"] == "out_refund"


def test_excluye_nota_debito():
    """Las N/D (move_type == "out_debit") no entran aquí -- mismo alcance

    que la consulta en vivo original (move_type in [out_invoice,
    out_refund])."""
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura(
                "900", so_id="SO1", move_type="out_debit", es_nota_debito=True, estado="posted"
            )
        ]
    )
    result = _facturas_dicts_desde_espejo(repo, {"SO1"})
    assert result == []


def test_excluye_no_posted():
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [b.factura("900", so_id="SO1", move_type="out_invoice", estado="draft")]
    )
    result = _facturas_dicts_desde_espejo(repo, {"SO1"})
    assert result == []


def test_resuelve_so_via_cadena_factura_origen():
    """Una N/C sin so_id propio se resuelve vía factura_origen_id, igual

    que _facturacion_por_so_desde_espejo."""
    repo = InMemoryRepository()
    repo.upsert_facturas(
        [
            b.factura("900", so_id="SO1", move_type="out_invoice", estado="posted"),
            b.factura(
                "901",
                so_id=None,
                move_type="out_refund",
                estado="posted",
                factura_origen_id="900",
            ),
        ]
    )
    result = _facturas_dicts_desde_espejo(repo, {"SO1"})
    assert len(result) == 2
    assert {d["invoice_origin"] for d in result} == {"SO1"}


def test_factura_id_no_numerico_produce_id_none_sin_romper():
    repo = InMemoryRepository()
    repo.upsert_facturas([b.factura("F1", so_id="SO1", move_type="out_invoice", estado="posted")])
    result = _facturas_dicts_desde_espejo(repo, {"SO1"})
    assert result[0]["id"] is None


def test_estado_pago_en_vivo_llena_payment_state_y_residual():
    def fake_execute(model, method, args, kwargs=None):
        assert model == "account.move"
        assert method == "read"
        assert args == [[900, 901]]
        return [
            {"id": 900, "payment_state": "paid", "amount_residual": 0.0},
            {"id": 901, "payment_state": "not_paid", "amount_residual": 200.0},
        ]

    result = _estado_pago_facturas_desde_odoo(fake_execute, [900, 901])
    assert result[900]["payment_state"] == "paid"
    assert result[901]["amount_residual"] == 200.0


def test_estado_pago_sin_execute_devuelve_vacio():
    assert _estado_pago_facturas_desde_odoo(None, [900]) == {}


def test_estado_pago_sin_ids_no_llama_a_odoo():
    def _debe_no_llamarse(*a, **k):
        raise AssertionError("no debería llamar a Odoo sin ids")

    assert _estado_pago_facturas_desde_odoo(_debe_no_llamarse, []) == {}


def test_estado_pago_error_no_propaga():
    def fake_execute(model, method, args, kwargs=None):
        raise RuntimeError("Odoo caído")

    assert _estado_pago_facturas_desde_odoo(fake_execute, [900]) == {}
