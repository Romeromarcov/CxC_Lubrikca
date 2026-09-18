"""Cuál fue la reasignación más reciente de un pago (Fase 2.4, pieza 40).

Cuadragésima pieza extraída de ``get_cobranza_pagos_unificado``: el dedup por
``pago_id`` vivía adentro de un ``try`` que solo existía para no tumbar la
vista unificada de pagos si ``repo.all_auditoria()`` fallaba -- ese ``try``
sigue en ``app.py``, lo que salió es el filtrado y dedup puros de adentro.
"""

from __future__ import annotations

from cxc.engine.reasignaciones import pago_reasignado_mas_reciente


def _fila(pago_id, timestamp, tipo="vinculacion_revinculada_por_odoo", **extra) -> dict:
    return {"pago_id": pago_id, "tipo_auditoria": tipo, "timestamp_audit": timestamp, **extra}


def test_sin_filas_no_hay_reasignados() -> None:
    assert pago_reasignado_mas_reciente([]) == {}


def test_una_fila_del_tipo_correcto_queda() -> None:
    fila = _fila("P1", "2026-09-01T10:00:00")
    assert pago_reasignado_mas_reciente([fila]) == {"P1": fila}


def test_filas_de_otro_tipo_de_auditoria_se_ignoran() -> None:
    otra = _fila("P1", "2026-09-01T10:00:00", tipo="pago_sin_tasa_para_su_fecha")
    assert pago_reasignado_mas_reciente([otra]) == {}


def test_un_pago_reasignado_dos_veces_se_queda_con_la_mas_reciente() -> None:
    """El bug real que esto arregla: antes se quedaba con la ÚLTIMA fila que
    devolviera la consulta, sin garantía de orden. Acá gana el timestamp
    mayor sin importar en qué orden llegan las filas."""
    vieja = _fila("P1", "2026-09-01T10:00:00", detalle_odoo="movido a S00100")
    nueva = _fila("P1", "2026-09-10T08:00:00", detalle_odoo="movido a S00200")
    assert pago_reasignado_mas_reciente([vieja, nueva])["P1"] == nueva
    assert pago_reasignado_mas_reciente([nueva, vieja])["P1"] == nueva


def test_pagos_distintos_no_se_mezclan() -> None:
    p1 = _fila("P1", "2026-09-01T10:00:00")
    p2 = _fila("P2", "2026-09-02T10:00:00")
    resultado = pago_reasignado_mas_reciente([p1, p2])
    assert resultado == {"P1": p1, "P2": p2}


def test_una_fila_sin_pago_id_se_descarta() -> None:
    fila = {"tipo_auditoria": "vinculacion_revinculada_por_odoo", "timestamp_audit": "2026-09-01"}
    assert pago_reasignado_mas_reciente([fila]) == {}


def test_un_pago_id_vacio_tambien_se_descarta() -> None:
    fila = _fila("", "2026-09-01T10:00:00")
    assert pago_reasignado_mas_reciente([fila]) == {}


def test_timestamps_iguales_conserva_la_ultima_procesada() -> None:
    """Empate: gana ``>=``, así que entre dos filas idénticas en el tiempo se
    queda con la que aparece después en la lista -- comportamiento estable y
    determinista, no un empate resuelto al azar."""
    a = _fila("P1", "2026-09-01T10:00:00", detalle_odoo="a")
    b = _fila("P1", "2026-09-01T10:00:00", detalle_odoo="b")
    assert pago_reasignado_mas_reciente([a, b])["P1"]["detalle_odoo"] == "b"


# --- la medición A/B -------------------------------------------------------

CASOS_AB = [
    [],
    [_fila("P1", "2026-09-01T10:00:00")],
    [_fila("P1", "2026-09-01T10:00:00", tipo="otro_tipo")],
    [
        _fila("P1", "2026-09-10", detalle_odoo="nueva"),
        _fila("P1", "2026-09-01", detalle_odoo="vieja"),
    ],
    [_fila("P1", "2026-09-01"), _fila("P2", "2026-09-02")],
    [{"tipo_auditoria": "vinculacion_revinculada_por_odoo", "timestamp_audit": "x"}],
    [_fila("", "2026-09-01")],
]


def test_el_calculo_original_reconstruido_da_lo_mismo() -> None:
    """El cuerpo tal como estaba en ``get_cobranza_pagos_unificado``, al lado
    del extraído, sobre todos los casos a la vez."""

    def original(auditoria):
        reasignados: dict[str, dict] = {}
        for row in auditoria:
            if row.get("tipo_auditoria") == "vinculacion_revinculada_por_odoo":
                pid = str(row.get("pago_id", "")).strip()
                if not pid:
                    continue
                previa = reasignados.get(pid)
                if previa is None or str(row.get("timestamp_audit") or "") >= str(
                    previa.get("timestamp_audit") or ""
                ):
                    reasignados[pid] = row
        return reasignados

    for caso in CASOS_AB:
        assert original(caso) == pago_reasignado_mas_reciente(caso)
