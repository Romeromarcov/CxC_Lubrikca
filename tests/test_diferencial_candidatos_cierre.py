"""Regla 3 de Diferencial Cambiario (candidatos a cierre de factura) --

función pura ``calcular_candidatos_cierre_diferencial``, sin mockear todo
el pipeline de ``get_ventas``."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.models import DescuentoDiferencialCambiario
from cxc.web.app import calcular_candidatos_cierre_diferencial

HOY = date(2026, 8, 12)


def _regla_max(porcentaje: str = "0.35", activo: bool = True) -> DescuentoDiferencialCambiario:
    return DescuentoDiferencialCambiario(
        regla_id="DIF_35_VES",
        nombre="35% Fijo VES a USD",
        tipo_diferencial="fijo_35_ves_usd",
        porcentaje_fijo=Decimal(porcentaje),
        vigencia_desde=date(2026, 1, 1),
        activo=activo,
    )


def _regla_candidatos(activo: bool = True) -> DescuentoDiferencialCambiario:
    return DescuentoDiferencialCambiario(
        regla_id="DIF_CANDIDATOS_CIERRE",
        nombre="Candidatos a Cierre de Factura",
        tipo_diferencial="candidato_cierre_factura",
        vigencia_desde=date(2026, 1, 1),
        activo=activo,
    )


def test_sin_reglas_configuradas_reporte_deshabilitado() -> None:
    res = calcular_candidatos_cierre_diferencial([], [], [], HOY)
    assert res["habilitado"] is False
    assert res["candidatos"] == []


def test_sin_regla_candidatos_activa_reporte_deshabilitado() -> None:
    # Solo la regla del 35% -- falta el interruptor de candidatos.
    res = calcular_candidatos_cierre_diferencial([_regla_max()], [], [], HOY)
    assert res["habilitado"] is False


def test_orden_supera_umbral_es_candidata() -> None:
    # Diferencial de hoy 12% -> umbral = 35% - 12% = 23% -> minimo pagado 77%.
    reglas = [_regla_max(), _regla_candidatos()]
    tasas = [{"timestamp": "2026-08-12T10:00:00", "diferencial_bcv_binance_pct": "12.0"}]
    items = [
        {
            "so_id": "S00001",
            "cliente_nombre": "Cliente Uno",
            "nacio_en_lista_usd": False,
            "ves_neta_teorica_iva": 1000.0,
            "pagado_teorico_bcv": 800.0,  # 80% >= 77%
        }
    ]
    res = calcular_candidatos_cierre_diferencial(reglas, tasas, items, HOY)
    assert res["habilitado"] is True
    assert res["diferencial_maximo_pct"] == 35.0
    assert res["diferencial_hoy_pct"] == 12.0
    assert res["umbral_pct_pagado"] == 77.0
    assert len(res["candidatos"]) == 1
    cand = res["candidatos"][0]
    assert cand["so_id"] == "S00001"
    assert cand["pct_pagado"] == 80.0
    # monto_candidato_maximo = 1000 * 0.23 = 230.00
    assert cand["monto_candidato_maximo"] == 230.0


def test_orden_bajo_umbral_no_es_candidata() -> None:
    reglas = [_regla_max(), _regla_candidatos()]
    tasas = [{"timestamp": "2026-08-12T10:00:00", "diferencial_bcv_binance_pct": "12.0"}]
    items = [
        {
            "so_id": "S00002",
            "cliente_nombre": "Cliente Dos",
            "nacio_en_lista_usd": False,
            "ves_neta_teorica_iva": 1000.0,
            "pagado_teorico_bcv": 500.0,  # 50% < 77%
        }
    ]
    res = calcular_candidatos_cierre_diferencial(reglas, tasas, items, HOY)
    assert res["candidatos"] == []


def test_orden_nacida_en_lista_usd_se_excluye() -> None:
    reglas = [_regla_max(), _regla_candidatos()]
    tasas = [{"timestamp": "2026-08-12T10:00:00", "diferencial_bcv_binance_pct": "0.0"}]
    items = [
        {
            "so_id": "S00003",
            "cliente_nombre": "Cliente Tres",
            "nacio_en_lista_usd": True,
            "ves_neta_teorica_iva": 1000.0,
            "pagado_teorico_bcv": 1000.0,
        }
    ]
    res = calcular_candidatos_cierre_diferencial(reglas, tasas, items, HOY)
    assert res["candidatos"] == []


def test_sin_diferencial_de_hoy_umbral_es_el_maximo_completo() -> None:
    # Sin filas en serie_tasas -> diferencial_hoy=0 -> umbral = diferencial_maximo completo.
    reglas = [_regla_max(porcentaje="0.35"), _regla_candidatos()]
    items = [
        {
            "so_id": "S00004",
            "cliente_nombre": "Cliente Cuatro",
            "nacio_en_lista_usd": False,
            "ves_neta_teorica_iva": 1000.0,
            "pagado_teorico_bcv": 660.0,  # 66% >= 65% (100%-35%)
        }
    ]
    res = calcular_candidatos_cierre_diferencial(reglas, [], items, HOY)
    assert res["umbral_pct_pagado"] == 65.0
    assert len(res["candidatos"]) == 1


def test_pagado_bcv_no_prorrateado_se_topa_al_100pct() -> None:
    """pagado_teorico_bcv hereda una limitación conocida: si un pago

    reconcilia facturas de VARIAS órdenes, se suma completo a cada una (no
    prorrateado) -- sin el cap, esto produciría %pagado absurdos como
    300-800%. El cap evita el numero sin sentido en el reporte."""
    reglas = [_regla_max(), _regla_candidatos()]
    tasas = [{"timestamp": "2026-08-12T10:00:00", "diferencial_bcv_binance_pct": "12.0"}]
    items = [
        {
            "so_id": "S00005",
            "cliente_nombre": "Cliente Cinco",
            "nacio_en_lista_usd": False,
            "ves_neta_teorica_iva": 1000.0,
            "pagado_teorico_bcv": 5000.0,  # pago compartido con otras ordenes del cliente
        }
    ]
    res = calcular_candidatos_cierre_diferencial(reglas, tasas, items, HOY)
    assert len(res["candidatos"]) == 1
    assert res["candidatos"][0]["pct_pagado"] == 100.0


def test_regla_max_vencida_reporte_deshabilitado() -> None:
    regla_vencida = DescuentoDiferencialCambiario(
        regla_id="DIF_35_VES",
        nombre="35% Fijo VES a USD",
        tipo_diferencial="fijo_35_ves_usd",
        porcentaje_fijo=Decimal("0.35"),
        vigencia_desde=date(2025, 1, 1),
        vigencia_hasta=date(2025, 12, 31),
        activo=True,
    )
    res = calcular_candidatos_cierre_diferencial(
        [regla_vencida, _regla_candidatos()], [], [], HOY
    )
    assert res["habilitado"] is False
