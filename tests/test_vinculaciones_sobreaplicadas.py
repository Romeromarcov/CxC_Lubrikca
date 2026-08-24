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


def _vinc(vinc_id, pago_id, so_id, monto_ves, equiv_usd) -> Vinculacion:
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
        estado=EstadoVinculacion.PENDIENTE,
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
