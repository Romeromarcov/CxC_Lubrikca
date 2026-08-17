""""Días Vencido" en Ventas como fuente única (agosto 2026, pedido explícito

del usuario: agregar la columna en Ventas y que las demás páginas lean de
ahí en vez de recalcular). ``_fecha_y_dias_vencido`` reemplaza la antigua
``_dias_vencido_orden`` (usada solo por /api/reporte-cxc-cliente, que ahora
lee ``item["dias_vencido"]`` directo del resultado de Ventas).
"""

from __future__ import annotations

from datetime import date

from cxc.web.app import _fecha_y_dias_vencido


def test_vencida_usa_fecha_entrega_mas_dias_credito():
    item = {"fecha_entrega": "2026-06-01", "fecha": "2026-05-20", "dias_credito": 15}
    hoy = date(2026, 6, 20)
    vencimiento, dias = _fecha_y_dias_vencido(item, hoy)
    # 2026-06-01 + 15 dias = 2026-06-16; hoy 06-20 -> 4 dias vencido.
    assert vencimiento == date(2026, 6, 16)
    assert dias == 4


def test_sin_fecha_entrega_cae_a_fecha_de_la_orden():
    item = {"fecha_entrega": None, "fecha": "2026-06-01", "dias_credito": 0}
    hoy = date(2026, 6, 10)
    vencimiento, dias = _fecha_y_dias_vencido(item, hoy)
    assert vencimiento == date(2026, 6, 1)
    assert dias == 9


def test_vigente_no_vencida_da_cero_dias():
    item = {"fecha_entrega": "2026-06-15", "fecha": "2026-06-01", "dias_credito": 30}
    hoy = date(2026, 6, 20)
    vencimiento, dias = _fecha_y_dias_vencido(item, hoy)
    assert vencimiento == date(2026, 7, 15)
    assert dias == 0  # aun no vence, nunca negativo


def test_fecha_invalida_cae_a_hoy_como_base():
    item = {"fecha_entrega": "no-es-una-fecha", "fecha": None, "dias_credito": 5}
    hoy = date(2026, 6, 20)
    vencimiento, dias = _fecha_y_dias_vencido(item, hoy)
    assert vencimiento == date(2026, 6, 25)
    assert dias == 0
