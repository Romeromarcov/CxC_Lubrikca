"""Los cuatro saldos, ahora en su propio módulo (Fase 2.4 del blindaje).

Dos cosas distintas se prueban acá:

1. Que la extracción **no cambió nada**. La lógica se movió de
   ``app.py:_saldos_4_columnas_item`` a ``engine/saldos.py`` tal cual, y el
   alias viejo sigue existiendo porque hay 13 sitios que lo llaman. La medición
   A/B que el plan pide para cada pieza extraída es ésta: los dos caminos tienen
   que dar el mismo diccionario para el mismo ítem.

2. Que ``diagnostico_de_saldos`` ve lo que el saldo solo no puede decir. Es la
   parte nueva, y no cambia ningún número: solo permite preguntar por qué salió
   así.
"""

from __future__ import annotations

import pytest

from cxc.engine.saldos import diagnostico_de_saldos, saldos_de_la_orden


def _item(**campos):
    base = {
        "so_id": "S00001",
        "ves_neta_teorica_iva": 1000.0,
        "usd_neta_teorica_iva": 800.0,
        "venta_neta_real": 900.0,
        "total_facturado_neto": 900.0,
        "descuento_aplicado_sistema": 0.0,
        "pagado_teorico_bcv_incl_pendiente": 0.0,
        "pagado_teorico_binance_incl_pendiente": 0.0,
        "monto_pagado_factura_odoo_incl_pendiente": 0.0,
        "facturada": True,
    }
    base.update(campos)
    return base


# --- la medición A/B de la extracción ---------------------------------------

CASOS = [
    ("sin pagos", _item()),
    ("con pago parcial", _item(pagado_teorico_bcv_incl_pendiente=400.0)),
    ("sobrepagada", _item(monto_pagado_factura_odoo_incl_pendiente=1500.0)),
    ("sin facturar", _item(facturada=False)),
    ("con descuento de gerencia", _item(descuento_aplicado_sistema=200.0)),
    ("teórico VES ausente", _item(ves_neta_teorica_iva=None)),
    ("todo en cero", _item(ves_neta_teorica_iva=0, usd_neta_teorica_iva=0, venta_neta_real=0)),
    ("con basura en un campo", _item(venta_neta_real="no es un número")),
]


@pytest.mark.parametrize("nombre,item", CASOS, ids=[c[0] for c in CASOS])
def test_el_alias_viejo_da_exactamente_lo_mismo(nombre, item) -> None:
    """La pieza extraída y el nombre viejo tienen que coincidir.

    Es la medición A/B de esta pieza: si difieren, la extracción cambió algo, y
    lo que cambió es un saldo que alguien ve en pantalla.
    """
    from cxc.web.app import _saldos_4_columnas_item

    assert _saldos_4_columnas_item(item) == saldos_de_la_orden(item), nombre


# --- lo que el saldo solo no puede decir ------------------------------------


def test_un_teorico_ausente_sale_como_saldo_cero() -> None:
    """El comportamiento actual, fijado para que el cambio sea deliberado.

    NO se arregla acá: cambiarlo mueve lo que se muestra para una orden no
    evaluable, y eso es la Fase 2.1 y una decisión del usuario. Lo que este test
    hace es dejar constancia de que hoy es así, para que el día que se cambie
    sea porque alguien lo decidió.
    """
    saldos = saldos_de_la_orden(_item(ves_neta_teorica_iva=None))
    assert saldos["teorico_bs"] == 0.0, (
        "Un teórico ausente da saldo cero, o sea que la orden se ve COBRADA."
    )


def test_el_diagnostico_si_distingue_ausente_de_cero() -> None:
    ausente = diagnostico_de_saldos(_item(ves_neta_teorica_iva=None))
    en_cero = diagnostico_de_saldos(_item(ves_neta_teorica_iva=0.0))

    assert saldos_de_la_orden(_item(ves_neta_teorica_iva=None))["teorico_bs"] == (
        saldos_de_la_orden(_item(ves_neta_teorica_iva=0.0))["teorico_bs"]
    ), "Los dos saldos son iguales -- por eso hace falta el diagnóstico."

    assert not ausente.evaluable
    assert "teórico VES" in ausente.referencias_ausentes
    assert en_cero.evaluable, "Un teórico genuinamente cero SÍ es evaluable."


def test_el_diagnostico_reporta_cuanto_se_recorto_a_cero() -> None:
    """El ``max(0, …)`` tapa el sobrepago; el diagnóstico lo devuelve."""
    item = _item(monto_pagado_factura_odoo_incl_pendiente=1500.0)
    saldos = saldos_de_la_orden(item)
    diag = diagnostico_de_saldos(item)

    assert saldos["venta_real"] == 0.0, "El recorte a cero es el comportamiento actual."
    assert diag.hay_sobrepago
    assert diag.recortes_a_cero["venta_real"] == pytest.approx(600.0), (
        "900 de venta contra 1.500 pagados son 600 de sobrepago que el saldo no muestra."
    )


def test_una_orden_sana_no_tiene_observaciones() -> None:
    diag = diagnostico_de_saldos(_item(pagado_teorico_bcv_incl_pendiente=400.0))
    assert diag.evaluable
    assert not diag.hay_sobrepago
    assert "sin observaciones" in str(diag)


def test_el_diagnostico_se_lee_sin_abrir_el_codigo() -> None:
    """El texto es lo que va a terminar en una alerta, así que tiene que decir algo."""
    diag = diagnostico_de_saldos(
        _item(ves_neta_teorica_iva=None, monto_pagado_factura_odoo_incl_pendiente=1500.0)
    )
    texto = str(diag)
    assert "S00001" in texto
    assert "teórico VES" in texto
    assert "600" in texto


def test_una_orden_sin_facturar_no_tiene_saldo_de_factura() -> None:
    """``None``, no cero: no hay factura, así que no hay saldo de factura.

    Es la distinción que el resto del módulo no hace, y la única del cálculo
    original que ya estaba bien.
    """
    assert saldos_de_la_orden(_item(facturada=False))["factura_real"] is None
    assert saldos_de_la_orden(_item(facturada=True))["factura_real"] == 900.0
