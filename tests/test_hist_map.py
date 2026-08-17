"""_build_hist_map (agosto 2026) -- antes get_auditoria tenía su propia

copia idéntica de este bucle (el propio código lo documentaba como "bug
preexistente" de la Tarea 6). Fuente única ahora, usada por
_get_reporte_saldos_sync y get_auditoria.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

from cxc.web.app import _build_hist_map


def test_mapea_por_codigo_con_los_3_campos():
    repo = MagicMock()
    repo.all_listas_precios_historicas.return_value = [
        {
            "codigo": "P1",
            "producto_nombre": "Aceite X",
            "precio_usd": "10.5",
            "precio_bcv_euro": "13.2",
        },
    ]
    result = _build_hist_map(repo)
    assert result == {
        "P1": {"nombre": "Aceite X", "usd": Decimal("10.5"), "eur": Decimal("13.2")}
    }


def test_filas_sin_codigo_se_excluyen():
    repo = MagicMock()
    repo.all_listas_precios_historicas.return_value = [
        {"codigo": "", "producto_nombre": "Sin código", "precio_usd": "1", "precio_bcv_euro": "1"},
        {
            "codigo": "  ",
            "producto_nombre": "Solo espacios",
            "precio_usd": "1",
            "precio_bcv_euro": "1",
        },
    ]
    assert _build_hist_map(repo) == {}


def test_precio_invalido_no_rompe_el_resto():
    repo = MagicMock()
    repo.all_listas_precios_historicas.return_value = [
        {
            "codigo": "MALO",
            "producto_nombre": "X",
            "precio_usd": "no-es-numero",
            "precio_bcv_euro": "1",
        },
        {"codigo": "BUENO", "producto_nombre": "Y", "precio_usd": "5", "precio_bcv_euro": "6"},
    ]
    result = _build_hist_map(repo)
    assert "MALO" not in result  # la fila con precio inválido se descarta silenciosamente
    assert result["BUENO"]["usd"] == Decimal("5")


def test_sin_filas_da_dict_vacio():
    repo = MagicMock()
    repo.all_listas_precios_historicas.return_value = []
    assert _build_hist_map(repo) == {}
