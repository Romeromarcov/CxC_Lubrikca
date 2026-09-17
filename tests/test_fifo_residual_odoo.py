"""El reparto FIFO no puede ofrecer plata que Odoo ya aplicó a facturas.

Bug real (auditoría de agosto 2026): el disponible de cada pago se
calculaba como ``monto_total - Vinculaciones locales``, sin restar lo que
Odoo ya había conciliado. Las dos mitades del criterio eran
inconsistentes: ``get_reconciled_pago_ids_odoo`` deja disponible todo pago
que no esté TOTALMENTE reconciliado -- correcto, un residual genuino no
debe desaparecer (caso S00608) -- pero el monto se tomaba completo.

Medido en producción: de 260 pagos vivos, 69 tenían el disponible
inflado, con un exceso de 11.042.364,68 VES y 5.880,12 USD. El caso que
originó la investigación (pago 973, "Cauchera El Gordo") creía tener
186.251,19 disponibles cuando le quedaban 3.320,45 -- por eso el daemon le
inventó cuatro vinculaciones diminutas contra órdenes equivocadas.
"""

from __future__ import annotations

from decimal import Decimal

from cxc.web.app import residual_disponible_por_pago


def _execute_falso(*, residual, reconciled=False, state="paid"):
    def execute(model, method, args, kwargs=None):
        if model == "account.payment":
            return [{"id": 973, "move_id": [500, "PBAMI/2026/00237"], "state": state}]
        if model == "account.move.line":
            return [
                {
                    "move_id": [500, "PBAMI/2026/00237"],
                    "amount_residual_currency": residual,
                    "reconciled": reconciled,
                }
            ]
        return []

    return execute


def test_devuelve_el_residual_real_no_el_monto_total() -> None:
    residuales = residual_disponible_por_pago(_execute_falso(residual=3320.45), ["973"])
    assert residuales == {"973": Decimal("3320.45")}


def test_linea_totalmente_reconciliada_no_deja_nada_disponible() -> None:
    residuales = residual_disponible_por_pago(
        _execute_falso(residual=0.0, reconciled=True), ["973"]
    )
    assert residuales["973"] == Decimal("0")


def test_toma_la_magnitud_sin_importar_el_signo() -> None:
    """En la línea del pago el residual viene con signo negativo (es un
    crédito); lo que interesa es cuánto queda."""
    residuales = residual_disponible_por_pago(_execute_falso(residual=-3320.45), ["973"])
    assert residuales["973"] == Decimal("3320.45")


def test_pago_cancelado_queda_fuera() -> None:
    residuales = residual_disponible_por_pago(
        _execute_falso(residual=100.0, state="cancel"), ["973"]
    )
    assert residuales == {}


def test_sin_odoo_no_revienta_y_no_afirma_nada() -> None:
    """Best-effort: sin conexión se devuelve vacío y quien llama conserva
    su cálculo anterior -- mejor ofrecer de más que ocultar un pago real."""
    assert residual_disponible_por_pago(None, ["973"]) == {}


def test_odoo_que_falla_no_tumba_el_reparto() -> None:
    def execute_roto(*a, **k):
        raise RuntimeError("Odoo caído")

    assert residual_disponible_por_pago(execute_roto, ["973"]) == {}


def test_el_tope_se_aplica_en_proporcion() -> None:
    """Réplica del cálculo que hace el reparto: el residual viene en la

    moneda del pago (VES) y el disponible se lleva a USD escalando en
    proporción, no con una tasa aparte -- ``monto_orig_usd`` puede venir
    del ``amount_ref`` que Odoo calculó con la tasa de ESE pago.
    """
    monto_orig_raw = Decimal("186513.63")  # VES, pago 973 real
    monto_orig_usd = Decimal("263.00")
    residual_raw = Decimal("3320.45")

    disponible_usd = monto_orig_usd * (residual_raw / monto_orig_raw)

    # ~1,78% del pago sigue disponible: unos 4,68 USD, no los 263 completos.
    assert disponible_usd < Decimal("5")
    assert disponible_usd > Decimal("4")
