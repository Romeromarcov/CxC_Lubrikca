"""El mismo pago cargado dos veces (Fase 2.4, decimoquinta pieza).

`_detectar_pagos_duplicados` no tenía ninguna prueba: la encontró el mismo barrido
que la pieza anterior, el que lista funciones de `app.py` cuyo nombre habla de
dinero y que ningún test nombra.

Un duplicado infla la cobranza y saca de la cuenta por cobrar una orden que no se
cobró, así que la clave de cinco campos importa: quitar cualquiera de los cinco
genera falsos positivos, y agregar uno de más los esconde.
"""

from __future__ import annotations

from decimal import Decimal

from cxc.engine.pagos_duplicados import (
    clave_de_pago,
    detectar_pagos_duplicados,
)


def _p(pid, cliente="C1", monto="100.00", moneda="VES", metodo="Transferencia", fecha="2026-07-31"):
    return {
        "pago_id": pid,
        "cliente_id": cliente,
        "monto": monto,
        "moneda": moneda,
        "metodo_pago": metodo,
        "fecha_pago": fecha,
    }


# --- la clave, campo por campo ----------------------------------------------


def test_dos_pagos_identicos_son_duplicados() -> None:
    """El caso real medido: «En ascenso 2011,c.a.», 72.287,74 VES, 31-jul, x2."""
    d = detectar_pagos_duplicados([_p("1270", monto="72287.74"), _p("1271", monto="72287.74")])
    assert d == {"1270": ["1271"], "1271": ["1270"]}


def test_cada_uno_lista_a_los_OTROS_y_no_a_si_mismo() -> None:
    """Verse a sí mismo haría que un grupo de dos pareciera de tres."""
    d = detectar_pagos_duplicados([_p("A"), _p("B"), _p("C")])
    assert d["A"] == ["B", "C"]
    assert "A" not in d["A"]


def test_dos_clientes_distintos_pueden_pagar_lo_mismo_el_mismo_dia() -> None:
    assert detectar_pagos_duplicados([_p("A", cliente="C1"), _p("B", cliente="C2")]) == {}


def test_el_mismo_monto_en_monedas_distintas_no_es_un_duplicado() -> None:
    """Y acá NO se convierte: un duplicado es el mismo documento, no dos montos
    equivalentes. Convertir haría que 100 USD y 3.650 VES parezcan el mismo pago."""
    assert detectar_pagos_duplicados([_p("A", moneda="USD"), _p("B", moneda="VES")]) == {}


def test_el_mismo_monto_por_metodos_distintos_no_es_un_duplicado() -> None:
    """Transferencia y efectivo el mismo día por el mismo monto es plausible."""
    assert (
        detectar_pagos_duplicados(
            [_p("A", metodo="Transferencia"), _p("B", metodo="Efectivo")]
        )
        == {}
    )


def test_fechas_distintas_no_son_duplicados() -> None:
    otra = _p("B", fecha="2026-08-01")
    assert detectar_pagos_duplicados([_p("A", fecha="2026-07-31"), otra]) == {}


def test_la_fecha_se_trunca_al_dia_asi_que_la_hora_no_separa() -> None:
    """Deliberado: el mismo pago cargado dos veces suele tener horas distintas."""
    d = detectar_pagos_duplicados(
        [_p("A", fecha="2026-07-31 09:14:00"), _p("B", fecha="2026-07-31 17:02:00")]
    )
    assert d["A"] == ["B"]


def test_la_moneda_se_compara_sin_formato() -> None:
    assert detectar_pagos_duplicados([_p("A", moneda="usd"), _p("B", moneda=" USD ")])["A"] == ["B"]


def test_el_monto_se_redondea_a_dos_decimales() -> None:
    """El mismo pago puede llegar con un tercer decimal distinto según por dónde
    entró, y eso no debería separarlo."""
    d = detectar_pagos_duplicados([_p("A", monto="100.004"), _p("B", monto="100.001")])
    assert d["A"] == ["B"]


def test_una_diferencia_de_un_centavo_SI_separa() -> None:
    """El redondeo es a dos decimales, no una tolerancia: un centavo es un monto
    distinto y agruparlos empezaría a inventar duplicados."""
    assert detectar_pagos_duplicados([_p("A", monto="100.00"), _p("B", monto="100.01")]) == {}


# --- bordes -----------------------------------------------------------------


def test_un_pago_sin_id_se_saltea() -> None:
    """Sin identificador no se puede señalar ni referenciar.

    Y agruparlos entre sí inventaría duplicados: dos filas sin id y sin más datos
    compartirían clave y se acusarían mutuamente.
    """
    d = detectar_pagos_duplicados([_p(""), _p(None), _p("   ")])
    assert d == {}


def test_un_monto_ilegible_no_revienta_y_agrupa_con_los_ceros() -> None:
    """Que dos pagos con el monto roto se señalen juntos es más útil que que pasen
    desapercibidos por separado."""
    d = detectar_pagos_duplicados([_p("A", monto="ilegible"), _p("B", monto=None)])
    assert d["A"] == ["B"]


def test_la_lista_vacia_no_devuelve_nada() -> None:
    assert detectar_pagos_duplicados([]) == {}


def test_un_pago_solo_no_es_duplicado_de_nadie() -> None:
    assert detectar_pagos_duplicados([_p("A")]) == {}


def test_fecha_pago_ausente_cae_a_fecha() -> None:
    """Las dos claves existen según de dónde venga la fila."""
    a = {"pago_id": "A", "cliente_id": "C1", "monto": "1", "moneda": "VES",
         "metodo_pago": "T", "fecha": "2026-07-31"}
    b = dict(a, pago_id="B")
    assert detectar_pagos_duplicados([a, b])["A"] == ["B"]


def test_la_clave_tiene_exactamente_los_cinco_campos() -> None:
    """Quitar uno genera falsos positivos; agregar uno esconde duplicados."""
    k = clave_de_pago(_p("A"))
    assert len(k) == 5
    assert k == ("C1", Decimal("100.00"), "VES", "Transferencia", "2026-07-31")


def test_tres_pagos_iguales_forman_un_grupo_de_tres() -> None:
    """Pasa en los datos: cinco grupos del banco de escenarios tienen tres."""
    d = detectar_pagos_duplicados([_p("A"), _p("B"), _p("C")])
    assert len(d) == 3
    assert all(len(v) == 2 for v in d.values())
