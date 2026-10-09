"""Una fila abierta por (orden, tipo): refresco en el lugar, respeto de las decisiones y cierre."""

from __future__ import annotations

from cxc.engine.auditoria_filas import clave_por_pago, sincronizar_filas

TIPOS = {"descuento_orden", "descuento_factura"}


def fila(
    so="S1",
    tipo="descuento_orden",
    dif=-10.0,
    estado="pendiente",
    audit_id=None,
    ts="2026-10-09",
    **kw,
):
    return {
        "audit_id": audit_id or f"{so}_{tipo}_{ts}",
        "so_id": so,
        "tipo_auditoria": tipo,
        "diferencia_usd": dif,
        "detalle_motor": "d",
        "detalle_odoo": "o",
        "estado": estado,
        "timestamp_audit": ts,
        **kw,
    }


def _sync(existentes, actuales, **kw):
    return sincronizar_filas(existentes, actuales, tipos=TIPOS, **kw)


def test_una_discrepancia_nueva_se_escribe_con_id_estable_sin_fecha():
    escribir, cerrar = _sync([], [fila()])
    assert [f["audit_id"] for f in escribir] == ["S1_descuento_orden"]
    assert cerrar == []


def test_con_fila_abierta_igual_no_se_reescribe_cada_dia():
    escribir, cerrar = _sync([fila(audit_id="X")], [fila(ts="2026-10-10")])
    assert escribir == [] and cerrar == []


def test_con_fila_abierta_y_monto_distinto_se_refresca_en_el_mismo_id():
    escribir, _ = _sync([fila(audit_id="X", dif=-10)], [fila(dif=-25)])
    assert [(f["audit_id"], f["diferencia_usd"]) for f in escribir] == [("X", -25)]


def test_la_decision_de_una_persona_se_respeta_si_el_monto_no_cambio():
    escribir, cerrar = _sync([fila(audit_id="X", estado="revisado")], [fila()])
    assert escribir == [] and cerrar == []


def test_la_decision_se_reabre_si_el_monto_cambio():
    escribir, _ = _sync([fila(audit_id="X", estado="aprobado", dif=-10)], [fila(dif=-40)])
    assert [(f["audit_id"], f["estado"]) for f in escribir] == [("X", "pendiente")]


def test_una_fila_abierta_sin_discrepancia_vigente_se_cierra():
    escribir, cerrar = _sync([fila(audit_id="X")], [])
    assert escribir == [] and cerrar == ["X"]


def test_solo_se_cierran_las_ordenes_evaluadas():
    existentes = [fila(so="S1", audit_id="A"), fila(so="S2", audit_id="B")]
    _, cerrar = _sync(existentes, [], evaluadas={"S1"})
    assert cerrar == ["A"]


def test_no_toca_los_tipos_que_no_gobierna():
    _, cerrar = _sync([fila(tipo="otro_tipo", audit_id="Z")], [])
    assert cerrar == []


def test_los_duplicados_de_legado_se_cierran_y_queda_la_mas_reciente():
    existentes = [
        fila(audit_id="viejo", ts="2026-10-07"),
        fila(audit_id="medio", ts="2026-10-08"),
        fila(audit_id="nuevo", ts="2026-10-09"),
    ]
    escribir, cerrar = _sync(existentes, [fila(ts="2026-10-10")])
    assert escribir == []
    assert sorted(cerrar) == ["medio", "viejo"]


def test_una_fila_resuelta_que_la_discrepancia_reabre_reutiliza_su_id():
    escribir, _ = _sync([fila(audit_id="X", estado="resuelto")], [fila()])
    assert [(f["audit_id"], f["estado"]) for f in escribir] == [("X", "pendiente")]


def test_la_clave_por_pago_separa_los_pagos_de_una_misma_orden():
    tipos = {"vinculacion_pendiente_revisar"}
    existentes = [
        fila(so="S1", tipo="vinculacion_pendiente_revisar", audit_id="A", pago_id="P1"),
    ]
    actuales = [
        fila(so="S1", tipo="vinculacion_pendiente_revisar", pago_id="P1"),
        fila(so="S1", tipo="vinculacion_pendiente_revisar", pago_id="P2"),
    ]
    escribir, cerrar = sincronizar_filas(existentes, actuales, tipos=tipos, clave=clave_por_pago)
    assert [f["pago_id"] for f in escribir] == ["P2"]
    assert cerrar == []


def test_cambiar_solo_el_texto_no_reescribe_la_fila():
    previa = fila(audit_id="X")
    nueva = fila(detalle_motor="texto distinto", detalle_odoo="otro")
    nueva["detalle_motor"] = "texto distinto"
    escribir, cerrar = _sync([previa], [nueva])
    assert escribir == [] and cerrar == []


def test_puede_cerrar_limita_lo_que_un_generador_cierra():
    existentes = [fila(so="S1", dif=-5, audit_id="neg"), fila(so="S2", dif=7, audit_id="pos")]
    _, cerrar = _sync(existentes, [], puede_cerrar=lambda f: f["diferencia_usd"] < 0)
    assert cerrar == ["neg"]
