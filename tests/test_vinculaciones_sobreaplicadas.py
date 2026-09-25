"""``_detectar_vinculaciones_sobreaplicadas`` -- bug real (reportado por el

usuario, agosto 2026, cliente CONSTRUCTORA GRANO AGREGADO): el pago 1267
tenía una Vinculación por su monto completo apuntando a S00608 Y otra
Vinculación por una fracción apuntando a S00799 -- juntas sumaban más de
lo que el pago realmente vale (residuo de una corrida de auto-FIFO
anterior al fix de ``_vinc_usd_equiv``). Este chequeo detecta cualquier
caso similar hacia adelante.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from cxc.models import EstadoVinculacion, Moneda, Vinculacion
from cxc.web.app import _detectar_vinculaciones_sobreaplicadas


def _vinc(
    vinc_id, pago_id, so_id, monto_ves, equiv_usd, estado=EstadoVinculacion.PENDIENTE
) -> Vinculacion:
    return Vinculacion(
        vinc_id=vinc_id,
        pago_id=pago_id,
        so_id=so_id,
        monto_aplicado=Decimal(monto_ves),
        hora_pago_confirmada=datetime(2026, 7, 28, 12, 0, 0),
        tasa_bcv_aplicada=Decimal("742.81"),
        tasa_binance_aplicada=Decimal("800.0"),
        es_tasa_heredada=False,
        equiv_usd_bcv=Decimal(equiv_usd),
        equiv_usd_binance=Decimal(equiv_usd),
        estado=estado,
        moneda_abono=Moneda.VES,
    )


def _tasas_row(fecha: str, bcv: str) -> dict:
    return {"timestamp": f"{fecha} 12:00:00", "tasa_bcv": bcv, "tasa_binance": "800.0"}


def test_detecta_dos_vinculaciones_que_suman_mas_que_el_pago() -> None:
    # Pago real: 2.509.340,15 Bs / 742.81 =~ $3.378,17. Dos Vinculaciones
    # reclaman, entre ambas, más de eso.
    vincs = [
        _vinc("V1", "1267", "S00608", "2509340.15", "3378.17"),
        _vinc("V2", "1267", "S00799", "1384.09", "1.86"),
    ]
    pagos_rows = [
        {"pago_id": "1267", "monto": "2509340.15", "moneda": "VES", "fecha_pago": "2026-07-28"}
    ]
    tasas_rows = [_tasas_row("2026-07-28", "742.81")]

    resultado = _detectar_vinculaciones_sobreaplicadas(vincs, pagos_rows, tasas_rows)

    assert len(resultado) == 1
    assert resultado[0]["pago_id"] == "1267"
    assert resultado[0]["exceso_usd"] > 0


def test_no_detecta_si_la_suma_calza_con_el_pago() -> None:
    vincs = [_vinc("V1", "998", "S00427", "1663301.20", "2239.20")]
    pagos_rows = [
        {"pago_id": "998", "monto": "1663301.20", "moneda": "VES", "fecha_pago": "2026-07-15"}
    ]
    tasas_rows = [_tasas_row("2026-07-15", "742.81")]

    assert _detectar_vinculaciones_sobreaplicadas(vincs, pagos_rows, tasas_rows) == []


def test_no_detecta_si_dos_vinculaciones_reparten_sin_exceder() -> None:
    # 2 vincs que juntas caben dentro del monto real del pago -- reparto
    # legítimo entre 2 órdenes, no sobreaplicación.
    vincs = [
        _vinc("V1", "500", "S0001", "50000.00", "67.32"),
        _vinc("V2", "500", "S0002", "50000.00", "67.32"),
    ]
    pagos_rows = [
        {"pago_id": "500", "monto": "150000.00", "moneda": "VES", "fecha_pago": "2026-07-15"}
    ]
    tasas_rows = [_tasas_row("2026-07-15", "742.81")]

    assert _detectar_vinculaciones_sobreaplicadas(vincs, pagos_rows, tasas_rows) == []


def test_ignora_pago_sin_fila_local() -> None:
    """Una Vinculación cuyo pago_id no aparece en pagos_rows (pago Odoo-

    directo, nunca sincronizado local) se omite -- no hay monto real
    contra qué comparar."""
    vincs = [_vinc("V1", "999", "S0001", "1000.00", "1.35")]
    assert _detectar_vinculaciones_sobreaplicadas(vincs, [], []) == []


def test_sin_vinculaciones_o_sin_pagos_no_falla() -> None:
    assert _detectar_vinculaciones_sobreaplicadas([], [], []) == []
    solo_vinc = [_vinc("V1", "1", "S1", "100", "1")]
    assert _detectar_vinculaciones_sobreaplicadas(solo_vinc, [], []) == []


# --- un pago con fecha sin tasa no tumba el chequeo -----------------------------
#
# Tercer hallazgo del banco de escenarios del 12-sep-2026: el escenario armó el bug
# del importe local y, al pedir /api/auditoria para verlo, recibió un 500 -- "No hay
# tasa para 2026-09-05". Este detector pedía la tasa para TODOS los pagos, incluso
# los de dólares (que no la necesitan), y desde la Fase 2.1 esa ausencia levanta.
# Un chequeo de auditoría lee, no congela: el pago sin tasa se anota y se sigue.


def test_un_pago_en_dolares_no_necesita_tasa_y_no_la_pide() -> None:
    vincs = [
        _vinc("V1", "P_USD", "S1", "100", "100"),
        _vinc("V2", "P_USD", "S2", "50", "50"),
    ]
    pagos_rows = [{"pago_id": "P_USD", "monto": "100", "moneda": "USD", "fecha_pago": "2030-01-01"}]
    sin_tasa: list[dict] = []
    resultado = _detectar_vinculaciones_sobreaplicadas(vincs, pagos_rows, [], sin_tasa=sin_tasa)
    assert [r["pago_id"] for r in resultado] == ["P_USD"], "sobreaplicado, sin tasa de por medio"
    assert sin_tasa == []


def test_un_pago_en_bolivares_sin_tasa_se_anota_y_los_demas_se_evaluan() -> None:
    vincs = [
        _vinc("V1", "P_SIN", "S1", "1000", "1.35"),
        _vinc("V2", "P_SIN", "S2", "1000", "1.35"),
        _vinc("V3", "1267", "S00608", "2509340.15", "3378.17"),
        _vinc("V4", "1267", "S00799", "1384.09", "1.86"),
    ]
    pagos_rows = [
        {"pago_id": "P_SIN", "monto": "1000", "moneda": "VES", "fecha_pago": "2030-01-01"},
        {"pago_id": "1267", "monto": "2509340.15", "moneda": "VES", "fecha_pago": "2026-07-28"},
    ]
    sin_tasa: list[dict] = []
    resultado = _detectar_vinculaciones_sobreaplicadas(
        vincs, pagos_rows, [_tasas_row("2026-07-28", "742.81")], sin_tasa=sin_tasa
    )
    assert [r["pago_id"] for r in resultado] == ["1267"], "el que sí tiene tasa se evalúa"
    assert sin_tasa == [{"fecha": "2030-01-01", "pago_id": "P_SIN", "chequeo": "sobreaplicadas"}]


def test_sin_acumulador_tampoco_levanta() -> None:
    """Los llamadores viejos (el script de cruce) no pasan ``sin_tasa``."""
    vincs = [_vinc("V1", "P_SIN", "S1", "1000", "1.35")]
    pagos_rows = [
        {"pago_id": "P_SIN", "monto": "1000", "moneda": "VES", "fecha_pago": "2030-01-01"}
    ]
    assert _detectar_vinculaciones_sobreaplicadas(vincs, pagos_rows, []) == []


# --- CONCILIADO manda sobre PENDIENTE (25-sep-2026) -----------------------------
#
# Falso positivo real reportado por el usuario: 32 de 34 filas de este chequeo
# en producción eran una Vinculación CONCILIADO (la real, la que Odoo reconoce)
# más una o dos PENDIENTE huérfanas -- residuo de una corrida de FIFO anterior a
# que el pago se conciliara contra OTRA orden. El usuario revisó asientos y
# contabilidad y, con razón, no encontró ninguna doble aplicación: Odoo mismo ya
# había descartado esas PENDIENTE.


def test_conciliado_mas_pendiente_huerfana_no_es_sobreaplicacion() -> None:
    """Caso real: pago 1954 ($55) -- CONCILIADO a S00965 por $55 (la real) más

    dos PENDIENTE huérfanas (S00891 $36.23, S00816 $18.77) que sumaban el
    "doble" sin que hubiera nada que buscar en Odoo.
    """
    vincs = [
        _vinc("V1", "1954", "S00965", "55.00", "55.00", estado=EstadoVinculacion.CONCILIADO),
        _vinc("V2", "1954", "S00891", "36.23", "36.23"),
        _vinc("V3", "1954", "S00816", "18.77", "18.77"),
    ]
    pagos_rows = [
        {"pago_id": "1954", "monto": "55.00", "moneda": "USD", "fecha_pago": "2026-07-28"}
    ]

    assert _detectar_vinculaciones_sobreaplicadas(vincs, pagos_rows, []) == []


def test_dos_conciliadas_que_exceden_el_pago_si_se_detectan() -> None:
    """Caso real: pago 640 ($105) -- DOS Vinculaciones ya CONCILIADO (S00408

    $110.42, S00220 $2.48) suman $112.90, de verdad más de lo que el pago
    vale. Esta sí es una sobreaplicación real -- CONCILIADO no perdona.
    """
    vincs = [
        _vinc("V1", "640", "S00408", "110.42", "110.42", estado=EstadoVinculacion.CONCILIADO),
        _vinc("V2", "640", "S00220", "2.48", "2.48", estado=EstadoVinculacion.CONCILIADO),
    ]
    pagos_rows = [
        {"pago_id": "640", "monto": "105.00", "moneda": "USD", "fecha_pago": "2026-07-28"}
    ]

    resultado = _detectar_vinculaciones_sobreaplicadas(vincs, pagos_rows, [])
    assert len(resultado) == 1
    assert resultado[0]["pago_id"] == "640"
    assert round(resultado[0]["exceso_usd"], 2) == 7.90
