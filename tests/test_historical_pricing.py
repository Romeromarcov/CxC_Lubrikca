"""cargar_mapa_historico -- crosswalk código->product_id de Odoo.

Bug real (auditoría agosto 2026): el mapa se indexaba por el "código"
crudo del CSV origen (numeración interna de Lubrikca, ej. "1", "7"...),
pero ``_precio_unitario_linea`` busca por ``linea.producto``, que es el
``product_id`` de Odoo (ej. "1059") -- nunca hacían match. Ahora el mapa
se indexa por ``producto_id_odoo`` (columna resuelta por
``scripts/cruzar_codigos_lista_historica.py``).
"""

from __future__ import annotations

from decimal import Decimal

from cxc.engine.historical_pricing import cargar_mapa_historico


def test_mapa_se_indexa_por_producto_id_odoo_no_por_codigo():
    rows = [
        {
            "codigo": "1",
            "producto_nombre": "ELITE API SP SAE 0W-20 (1x6)",
            "precio_usd": "42.24",
            "precio_bcv_euro": "52.59",
            "producto_id_odoo": "1059",
        }
    ]
    mapa = cargar_mapa_historico(rows)
    assert "1059" in mapa
    assert "1" not in mapa
    assert mapa["1059"]["usd"] == Decimal("42.24")


def test_filas_sin_producto_id_odoo_resuelto_se_excluyen():
    rows = [
        {
            "codigo": "999",
            "producto_nombre": "Producto sin match",
            "precio_usd": "10.00",
            "precio_bcv_euro": "12.00",
            "producto_id_odoo": "",
        }
    ]
    mapa = cargar_mapa_historico(rows)
    assert mapa == {}
