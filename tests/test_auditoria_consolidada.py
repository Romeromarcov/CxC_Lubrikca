"""Vista unica de Auditoria: cada hallazgo aparece UNA vez por orden, con todas sus fuentes."""

from __future__ import annotations

from cxc.engine.auditoria_consolidada import (
    consolidar_entregas,
    consolidar_ordenes,
    consolidar_pagos,
)


def _ordenes(**kw):
    base = {
        "discrepancias": [],
        "ventas_con_alerta": [],
        "bandeja": [],
        "nc_fuera_de_regla": [],
        "por_debajo": [],
        "saldos": [],
        "info_orden": {"S1": {"cliente": "Cliente 1", "vendedor": "V1", "fecha": "2026-10-01"}},
    }
    base.update(kw)
    return consolidar_ordenes(**base)


def _hallazgo(fila, codigo):
    return next(h for h in fila["hallazgos"] if h["codigo"] == codigo)


def test_el_mismo_hallazgo_de_varias_fuentes_aparece_una_sola_vez():
    (fila,) = _ordenes(
        discrepancias=[
            {
                "so_id": "S1",
                "tipo": "Precio Inferior a Lista",
                "diferencia_monto": 30,
                "detalle": "x",
            }
        ],
        ventas_con_alerta=[{"so_id": "S1", "diferencia": 50}],
        por_debajo=[
            {"so_id": "S1", "tipo": "orden_menor_que_lista", "diferencia_usd": 40, "detalle": "y"}
        ],
    )
    assert fila["n_hallazgos"] == 1
    h = _hallazgo(fila, "PRECIO")
    assert h["monto"] == 50  # el mayor, no la suma: es el mismo concepto
    assert sorted(h["fuentes"]) == ["discrepancias", "por_debajo", "ventas"]


def test_la_bandeja_de_nc_y_la_comparacion_con_el_motor_son_un_solo_hallazgo_nc():
    (fila,) = _ordenes(
        bandeja=[{"so_id": "S1", "tipo_auditoria": "nota_credito", "diferencia_usd": -5}],
        nc_fuera_de_regla=[
            {"so_id": "S1", "diferencia": 61.02, "nc_usd": 109.91, "motor_usd": 48.89}
        ],
    )
    assert fila["n_hallazgos"] == 1
    assert _hallazgo(fila, "NC")["monto"] == 61.02


def test_el_descuento_manual_y_la_bandeja_de_descuentos_son_un_solo_hallazgo():
    (fila,) = _ordenes(
        discrepancias=[
            {"so_id": "S1", "tipo": "Descuento Manual No Explicado", "diferencia_monto": 12}
        ],
        bandeja=[{"so_id": "S1", "tipo_auditoria": "descuento_orden", "diferencia_usd": -12}],
    )
    assert [h["codigo"] for h in fila["hallazgos"]] == ["DESCUENTO"]


def test_conceptos_distintos_de_una_orden_quedan_como_hallazgos_distintos():
    (fila,) = _ordenes(
        discrepancias=[
            {"so_id": "S1", "tipo": "Precio Inferior a Lista", "diferencia_monto": 30},
            {
                "so_id": "S1",
                "tipo": "Producto Entregado No Coincide con la Orden",
                "diferencia_monto": 5,
            },
        ],
        saldos=[{"so_id": "S1", "diferencia": 7, "causa_probable": "retenciones"}],
        por_debajo=[{"so_id": "S1", "tipo": "factura_menor_que_orden", "diferencia_usd": 20}],
    )
    assert {h["codigo"] for h in fila["hallazgos"]} == {"PRECIO", "ENTREGA", "SALDO", "FACTURA"}
    assert [h["codigo"] for h in fila["hallazgos"]][0] == "PRECIO"  # mayor monto primero


def test_las_ordenes_con_mas_hallazgos_van_primero():
    filas = _ordenes(
        discrepancias=[
            {"so_id": "S1", "tipo": "Precio Inferior a Lista", "diferencia_monto": 500},
            {"so_id": "S2", "tipo": "Precio Inferior a Lista", "diferencia_monto": 10},
        ],
        saldos=[{"so_id": "S2", "diferencia": 3}],
    )
    assert [f["so_id"] for f in filas] == ["S2", "S1"]


def test_el_cliente_y_el_vendedor_se_toman_de_la_primera_fuente_que_los_tenga():
    (fila,) = _ordenes(
        discrepancias=[
            {
                "so_id": "S1",
                "tipo": "Precio Inferior a Lista",
                "diferencia_monto": 1,
                "cliente_nombre": "Otro nombre",
                "vendedor": "Otro",
            }
        ],
    )
    assert (fila["cliente"], fila["vendedor"]) == ("Cliente 1", "V1")


def test_las_filas_aceptables_conservan_su_dato_original():
    row = {"so_id": "S1", "tipo": "Precio Inferior a Lista", "diferencia_monto": 1, "huella": "h"}
    (fila,) = _ordenes(discrepancias=[row])
    assert _hallazgo(fila, "PRECIO")["aceptar"] == [row]


def test_consolidar_pagos_junta_todos_los_tipos_en_una_lista():
    aud = {
        "pagos_con_residual_sin_aplicar": [
            {"pago_id": "1", "clase": "remanente", "residual_sin_aplicar_usd": 5, "moneda": "USD"}
        ],
        "ajustes_cambio_huerfanos": [{"move_name": "ACH/1", "so_id": "S1", "residual_ves": 100}],
        "vinculaciones_sobreaplicadas": [{"pago_id": "2", "exceso_usd": 3}],
    }
    filas = consolidar_pagos(aud, [{"pago_id": "9", "so_id": "S9", "detalle_motor": "Pago 9"}])
    assert sorted(f["tipo"] for f in filas) == [
        "Ajuste de cambio huérfano",
        "Propuesta sin confirmar",
        "Saldo a favor",
        "Vinculaciones sobreaplicadas",
    ]


def test_consolidar_entregas_junta_devoluciones_y_entregas_sin_fecha():
    aud = {
        "devolucion_no_reflejada_en_cantidad": [
            {"so_id": "S1", "producto_codigo": "1", "faltante": 2, "valor_potencial_afectado": 50}
        ],
        "entregadas_sin_fecha_de_entrega": [
            {"so_id": "S2", "detalle": "sin fecha", "monto_orden": 9}
        ],
    }
    assert [f["tipo"] for f in consolidar_entregas(aud)] == [
        "Entregada sin fecha",
        "Faltante de devolución por línea",
    ]
