"""Qué clientes tienen un pago huérfano, para la regla "Equiparar" del
Diferencial Cambiario (Fase 2.4, pieza 39).

Novena pieza extraída de ``_get_reporte_saldos_sync``: el bloque dedupeaba
por ``pago_id`` tomando el saldo máximo y filtraba por un umbral de 0,05,
adentro de un ``try`` que solo existía para no tumbar todo el reporte si
``_get_conciliaciones_sugerencias_sync`` fallaba -- ese ``try`` sigue en
``app.py``, lo que salió es el cálculo puro de adentro.
"""

from __future__ import annotations

import pytest

from cxc.engine.conciliacion import clientes_con_pagos_huerfanos


def _sug(pago_id, cliente_id, saldo_pago) -> dict:
    return {"pago_id": pago_id, "cliente_id": cliente_id, "saldo_pago": saldo_pago}


def test_sin_sugerencias_no_hay_clientes() -> None:
    assert clientes_con_pagos_huerfanos([]) == set()


def test_un_pago_por_encima_del_umbral_marca_a_su_cliente() -> None:
    sugerencias = [_sug("P1", "C1", 10.0)]
    assert clientes_con_pagos_huerfanos(sugerencias) == {"C1"}


def test_un_saldo_de_centavos_no_cuenta() -> None:
    """0,05 es el umbral compartido del módulo (``UMBRAL_CUBIERTO``): un
    residuo de centavos no es un pago huérfano que valga la pena avisar."""
    sugerencias = [_sug("P1", "C1", 0.05)]
    assert clientes_con_pagos_huerfanos(sugerencias) == set()


def test_un_saldo_justo_por_encima_del_umbral_si_cuenta() -> None:
    sugerencias = [_sug("P1", "C1", 0.06)]
    assert clientes_con_pagos_huerfanos(sugerencias) == {"C1"}


def test_el_mismo_pago_repetido_para_varias_ordenes_se_dedupea_por_pago_id() -> None:
    """Una fila por orden abierta a la que el pago podría aplicarse: no hay
    que contar el mismo pago varias veces ni sumar sus saldos."""
    sugerencias = [_sug("P1", "C1", 30.0), _sug("P1", "C1", 30.0), _sug("P1", "C1", 30.0)]
    assert clientes_con_pagos_huerfanos(sugerencias) == {"C1"}


def test_el_dedup_toma_el_saldo_maximo_no_el_primero_ni_el_ultimo() -> None:
    """El residual real sin aplicar es el máximo que se vio, no un promedio ni
    la primera fila que aparece."""
    sugerencias = [_sug("P1", "C1", 5.0), _sug("P1", "C1", 40.0), _sug("P1", "C1", 12.0)]
    assert clientes_con_pagos_huerfanos(sugerencias) == {"C1"}


def test_el_saldo_maximo_bajo_el_umbral_no_marca_aunque_haya_varias_filas() -> None:
    sugerencias = [_sug("P1", "C1", 0.01), _sug("P1", "C1", 0.04)]
    assert clientes_con_pagos_huerfanos(sugerencias) == set()


def test_pagos_de_distintos_clientes_no_se_mezclan() -> None:
    sugerencias = [_sug("P1", "C1", 10.0), _sug("P2", "C2", 20.0)]
    assert clientes_con_pagos_huerfanos(sugerencias) == {"C1", "C2"}


def test_un_pago_sin_pago_id_se_descarta() -> None:
    """Sin id no hay con qué dedupear -- no debería pasar con datos reales,
    pero el código anterior también lo descartaba en vez de reventar."""
    sugerencias = [{"pago_id": None, "cliente_id": "C1", "saldo_pago": 100.0}]
    assert clientes_con_pagos_huerfanos(sugerencias) == set()


def test_un_pago_id_vacio_tambien_se_descarta() -> None:
    sugerencias = [_sug("", "C1", 100.0)]
    assert clientes_con_pagos_huerfanos(sugerencias) == set()


def test_saldo_pago_ausente_se_trata_como_cero() -> None:
    sugerencias = [{"pago_id": "P1", "cliente_id": "C1"}]
    assert clientes_con_pagos_huerfanos(sugerencias) == set()


def test_cliente_id_vacio_no_se_agrega_al_resultado() -> None:
    sugerencias = [_sug("P1", "", 100.0)]
    assert clientes_con_pagos_huerfanos(sugerencias) == set()


# --- la medición A/B -------------------------------------------------------

CASOS_AB = [
    [],
    [_sug("P1", "C1", 10.0)],
    [_sug("P1", "C1", 0.05)],
    [_sug("P1", "C1", 30.0), _sug("P1", "C1", 30.0)],
    [_sug("P1", "C1", 5.0), _sug("P1", "C1", 40.0), _sug("P1", "C1", 12.0)],
    [_sug("P1", "C1", 10.0), _sug("P2", "C2", 20.0), _sug("P3", "C1", 0.02)],
    [{"pago_id": None, "cliente_id": "C1", "saldo_pago": 100.0}],
    [_sug("", "C1", 100.0)],
    [{"pago_id": "P1", "cliente_id": "C1"}],
    [_sug("P1", "", 100.0)],
]


@pytest.mark.parametrize("sugerencias", CASOS_AB)
def test_el_calculo_original_reconstruido_da_lo_mismo(sugerencias) -> None:
    """El cuerpo tal como estaba en ``_get_reporte_saldos_sync``, al lado del
    extraído. Si difieren, la extracción cambió qué clientes entran a la
    regla "Equiparar" del Diferencial Cambiario."""

    def original(sugerencias_o):
        _pago_saldo_max_h: dict[str, float] = {}
        _pago_cliente_h: dict[str, str] = {}
        for s in sugerencias_o:
            pid = s.get("pago_id")
            if not pid:
                continue
            saldo = float(s.get("saldo_pago") or 0.0)
            if saldo > _pago_saldo_max_h.get(pid, 0.0):
                _pago_saldo_max_h[pid] = saldo
                _pago_cliente_h[pid] = str(s.get("cliente_id") or "")
        clientes: set[str] = set()
        for pid, saldo in _pago_saldo_max_h.items():
            if saldo > 0.05 and _pago_cliente_h.get(pid):
                clientes.add(_pago_cliente_h[pid])
        return clientes

    assert original(sugerencias) == clientes_con_pagos_huerfanos(sugerencias)
