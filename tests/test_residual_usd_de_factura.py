"""La tasa la pone Odoo; la nuestra es el respaldo.

Criterio del usuario (septiembre 2026): "la tasa nuestra del sistema para
el caso tanto de las facturas como los pagos, deberíamos usar la tasa de
Odoo, que es la que vale, y solo valida que coincida con el BCV de ese
día".

``amount_residual_usd`` es lo que Odoo mismo dice que le deben en
dólares -- la cifra contable de la empresa. Si el reporte discrepa de
ella, el que está mal es el reporte. Convertir por nuestra cuenta solo
agregaba una tasa más que podía divergir, y divergía: los 4.289,57 que
el balance de comprobación venía marcando en rojo.
"""

from __future__ import annotations

from cxc.web import app
from cxc.web.app import residual_usd_de_factura


def _serie(**por_dia: float) -> list[dict]:
    return [{"timestamp": f"{d} 12:00:00", "tasa_bcv": str(t)} for d, t in por_dia.items()]


def _inv(**campos):
    base = {
        "amount_residual": 1340030.18,
        "amount_residual_usd": None,
        "currency_id": [1, "VES"],
        "invoice_date": "2026-07-17",
    }
    base.update(campos)
    return base


def test_manda_la_cifra_de_odoo() -> None:
    """La factura 00000525, la que destapó el bug."""
    assert residual_usd_de_factura(_inv(amount_residual_usd=1829.45), "2026-07-17", []) == 1829.45


def test_la_cifra_de_odoo_gana_aunque_tengamos_tasa_propia() -> None:
    """Aunque nuestra serie tenga el día -- y aunque diera otro número."""
    serie = _serie(**{"2026-07-17": 820.10})
    inv = _inv(amount_residual_usd=1829.45)
    assert residual_usd_de_factura(inv, "2026-07-17", serie) == 1829.45


def test_sin_la_cifra_de_odoo_cae_a_nuestra_serie(monkeypatch) -> None:
    monkeypatch.setattr(app, "get_repo", lambda: None)
    serie = _serie(**{"2026-07-17": 732.4787})
    assert round(residual_usd_de_factura(_inv(), "2026-07-17", serie), 2) == 1829.45


def test_una_factura_en_dolares_no_se_convierte() -> None:
    inv = _inv(amount_residual=500.0, currency_id=False)
    assert residual_usd_de_factura(inv, "2026-07-17", []) == 500.0


def test_un_cero_de_odoo_es_un_dato_no_una_ausencia() -> None:
    """Factura saldada: Odoo dice 0,00 y eso es la respuesta, no un hueco
    que haya que rellenar convirtiendo el residual en bolívares."""
    inv = _inv(amount_residual=0.0, amount_residual_usd=0.0)
    assert residual_usd_de_factura(inv, "2026-07-17", _serie(**{"2026-07-17": 732.4787})) == 0.0


def test_sin_odoo_y_sin_tasa_no_se_inventa_una_conversion(monkeypatch) -> None:
    """"Sin datos" no es cero. Devolver 0 diría que no deben nada, que es
    justo lo contrario de la verdad; se deja el nominal para que el
    descuadre se vea."""
    monkeypatch.setattr(app, "get_repo", lambda: None)
    monkeypatch.setattr(app, "_tasas_historicas_cacheadas", lambda _r: [])
    assert residual_usd_de_factura(_inv(), "2026-07-17", []) != 0.0
