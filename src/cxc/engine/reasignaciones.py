"""Cuál fue la reasignación MÁS RECIENTE de un pago, entre las que registró Odoo.

Cuadragésima pieza de la Fase 2.4, extraída de ``get_cobranza_pagos_unificado``.

``_resincronizar_vinculaciones_con_odoo`` corre en cada sync y, cuando Odoo
reconcilió un pago contra una orden distinta a la que decía la Vinculación
local, escribe una fila de auditoría con ``tipo_auditoria ==
"vinculacion_revinculada_por_odoo"``. Esa corrección ya existe y ya queda
auditada; lo que hace esta función es **surfacearla** en la vista unificada de
pagos, para que quien mira la tabla vea que ese pago se movió y cuándo.

Un mismo pago puede reasignarse más de una vez (movido, y luego movido de
nuevo). Antes de esta pieza el código se quedaba con la **última fila que
devolviera la consulta**, que no tiene orden garantizado -- un pago movido dos
veces podía mostrar el detalle del movimiento viejo si la consulta lo devolvía
después del nuevo. Acá se compara ``timestamp_audit`` explícitamente y gana el
más reciente, sin depender del orden de ``repo.all_auditoria()``.

**No cambia ningún monto**: la lógica se movió tal cual.
"""

from __future__ import annotations

from typing import Any


def pago_reasignado_mas_reciente(auditoria: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """De las filas de auditoría, la reasignación por Odoo más reciente por pago.

    Filtra por ``tipo_auditoria == "vinculacion_revinculada_por_odoo"`` y
    dedupea por ``pago_id`` quedándose con la de ``timestamp_audit`` mayor
    (comparación de string, formato ISO -- ordena igual que por fecha). Una
    fila sin ``pago_id`` se descarta: sin id no hay con qué dedupear.
    """
    reasignados: dict[str, dict[str, Any]] = {}
    for row in auditoria:
        if row.get("tipo_auditoria") != "vinculacion_revinculada_por_odoo":
            continue
        pid = str(row.get("pago_id", "")).strip()
        if not pid:
            continue
        previa = reasignados.get(pid)
        if previa is None or str(row.get("timestamp_audit") or "") >= str(
            previa.get("timestamp_audit") or ""
        ):
            reasignados[pid] = row
    return reasignados
