"""El dashboard dice de dónde salió cada número (Fase 5 del blindaje).

Tres cosas que la revisión del dashboard encontró y que estos tests fijan:

* el equivalente en dólares de un pago **sin** ``amount_ref`` se calcula con
  nuestra serie, en vez de sumar cero -- antes un pago así aportaba su nominal
  completo al desglose por moneda y CERO al total, así que la tarjeta principal
  lo perdía y el desglose lo mostraba;
* los períodos de las tarjetas se anclan a ``fecha_hasta`` cuando el usuario
  filtró, no al día del servidor -- antes, con un filtro en un rango pasado, las
  tarjetas medían desde el inicio del mes actual y quedaban en cero mientras las
  tablas por día sí mostraban el rango;
* la respuesta lleva una bandera ``fuente`` que dice si los datos son de Odoo o
  de respaldo.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

# --- el equivalente cuando falta amount_ref --------------------------------


def test_un_pago_sin_amount_ref_no_vale_cero() -> None:
    from cxc.web.app import _eq_usd_por_serie

    filas = [
        {
            "timestamp": "2026-09-05 10:00:00",
            "tasa_bcv": "100.0",
            "tasa_binance": "120.0",
            "fuente": "test",
        }
    ]
    eq = _eq_usd_por_serie("2026-09-05", Decimal("5000"), filas)
    assert eq == Decimal("50"), (
        f"5.000 Bs a tasa 100 son 50 USD; dio {eq}. Sumar cero acá es lo que hacía "
        "desaparecer un pago de la tarjeta de cobranza."
    )


def test_sin_tasa_para_la_fecha_el_equivalente_es_cero_y_ahora_es_honesto() -> None:
    """El cero dejó de mentir, que era lo que este test esperaba.

    Antes asertaba ``eq > 0``: sin serie, ``get_rate_for_datetime`` caía al
    default de 2019 y 5.000 Bs se convertían a 136,99 USD a una tasa de 36,50
    que no tenía nada que ver con septiembre de 2026. El test fijaba ese
    comportamiento **a propósito y de forma transitoria**, hasta que la Fase 2.1
    se decidiera; su docstring lo decía.

    Se decidió el 11-sep-2026. Ahora la función devuelve cero, y ese cero
    significa exactamente «no había con qué convertir» — que es lo que el
    dashboard muestra anotado, en vez de un monto inventado.
    """
    from cxc.web.app import _eq_usd_por_serie

    eq = _eq_usd_por_serie("2026-09-05", Decimal("5000"), [])
    assert eq == Decimal("0"), (
        "sin tasa no se convierte; un 136,99 acá sería la tasa de 2019 "
        "disfrazada de equivalente de septiembre de 2026"
    )


def test_con_tasa_si_convierte_y_el_cero_no_se_come_un_monto_real() -> None:
    """El contraste, para que el cero de arriba no tape una regresión.

    Un cero devuelto siempre pasaría el test anterior. Éste comprueba que la
    función sigue convirtiendo cuando sí hay tasa — que es el caso que hacía
    desaparecer un pago de la tarjeta de cobranza cuando se sumaba cero.
    """
    from cxc.web.app import _eq_usd_por_serie

    filas = [{"timestamp": "2026-09-05 12:00:00", "tasa_bcv": "100"}]
    assert _eq_usd_por_serie("2026-09-05", Decimal("5000"), filas) == Decimal("50")


def test_una_fecha_ilegible_no_tumba_el_reporte() -> None:
    from cxc.web.app import _eq_usd_por_serie

    eq = _eq_usd_por_serie("no-es-fecha", Decimal("100"), [])
    assert eq >= 0


# --- los períodos de las tarjetas ------------------------------------------


def test_los_periodos_salen_de_la_fecha_que_se_les_da() -> None:
    from cxc.web.app import _periodo_bounds

    bounds = _periodo_bounds(date(2026, 5, 17))
    assert bounds["hoy"] == "2026-05-17"
    assert bounds["mes"] == "2026-05-01"
    assert bounds["trimestre"] == "2026-04-01", "mayo cae en el trimestre que arranca en abril"
    assert bounds["anio"] == "2026-01-01"


def test_el_trimestre_del_primer_mes_arranca_en_ese_mes() -> None:
    from cxc.web.app import _periodo_bounds

    trimestres = (
        (1, "2026-01-01"),
        (4, "2026-04-01"),
        (7, "2026-07-01"),
        (10, "2026-10-01"),
    )
    for mes, inicio in trimestres:
        bounds = _periodo_bounds(date(2026, mes, 15))
        assert bounds["trimestre"] == inicio, f"mes {mes}"


# --- la bandera de fuente ---------------------------------------------------


def test_la_respuesta_del_dashboard_declara_su_fuente(monkeypatch) -> None:
    """Sin Odoo, el reporte responde igual y se declara degradado.

    Es la diferencia con el balance: el balance se abstiene porque sin Ventas no
    tiene contra qué comparar; el dashboard sí puede dar números de respaldo, y
    lo que le faltaba era decir que lo son.
    """
    from cxc.web import app as modulo

    class RepoVacio:
        def all_ordenes(self):
            return []

        def all_catalogo(self):
            return []

        def all_pagos(self):
            return []

    monkeypatch.setattr(modulo, "get_repo", lambda: RepoVacio())
    monkeypatch.setattr(modulo, "_all_lineas_rows", lambda repo: [])
    monkeypatch.setattr(modulo, "_all_pagos_rows", lambda repo: [])
    monkeypatch.setattr(modulo, "_all_serie_tasas_rows", lambda repo: [])
    monkeypatch.setattr(modulo, "_entregas_desde_espejo", lambda repo, so: (set(), {}))
    # Sin conexión a Odoo.
    monkeypatch.setattr(modulo, "_connect", lambda cfg: None)

    datos = modulo._get_reporte_diario_sync()

    assert "fuente" in datos, "La respuesta no dice de dónde salieron los números."
    fuente = datos["fuente"]
    assert fuente["odoo_respondio"] is False
    assert fuente["cobranza"] == "espejo"
    assert fuente["degradado"] is True
    # Y sigue respondiendo, no se abstiene.
    assert datos["ventas_diarias"] == []
    assert "resumen" in datos
