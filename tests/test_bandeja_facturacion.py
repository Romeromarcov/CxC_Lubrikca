"""La Bandeja 1 muestra el teórico POR EL CUAL la orden quedó pagada.

Rediseño pedido por el usuario (septiembre 2026), después de no poder
explicar los montos de S00952 mirando la pantalla. Cuatro cifras, todas
subtotales netos sin IVA para que sean comparables entre sí:

  · el neto real de la orden en Odoo (``amount_untaxed``);
  · el estado: por cuál referencia salió de CxC y si está confirmada;
  · el teórico neto de ESA MISMA referencia;
  · el descuento que falta aplicar, ya neto de lo que la orden trae.

Sin botón de aprobar: la aprobación pasa a ser el acto de facturar en
Odoo, y ahí la orden desaparece de la bandeja.

Mostrar siempre el mismo teórico dejaría la columna sin relación con el
motivo por el que la orden salió de cobranza -- de ahí ``ReferenciaCxC``,
que nombra la referencia en vez de obligar a leer la prosa de ``motivo``.
"""

from __future__ import annotations

import pytest

from cxc.engine.cxc_routing import ReferenciaCxC, clasificar_estado_cxc
from cxc.web.app import _teorico_de_referencia

_ITEM = {"ves_neta_teorica": 1000.0, "usd_neta_teorica": 650.0}


def test_cada_teorico_se_muestra_cuando_es_el_que_se_cumplio() -> None:
    assert _teorico_de_referencia(_ITEM, ReferenciaCxC.TEORICO_USD) == 650.0
    assert _teorico_de_referencia(_ITEM, ReferenciaCxC.TEORICO_BS) == 1000.0


@pytest.mark.parametrize(
    "referencia",
    [ReferenciaCxC.VENTA_REAL, ReferenciaCxC.FACTURA_REAL, ReferenciaCxC.ODOO],
)
def test_sin_teorico_cumplido_no_se_muestra_ninguno(referencia: ReferenciaCxC) -> None:
    """Poner uno sugeriría que se cumplió, y en estas tres ramas no."""
    assert _teorico_de_referencia(_ITEM, referencia) is None


def test_una_orden_sin_teorico_calculado_no_inventa_un_monto() -> None:
    assert _teorico_de_referencia({}, ReferenciaCxC.TEORICO_USD) is None
    assert _teorico_de_referencia({"usd_neta_teorica": None}, ReferenciaCxC.TEORICO_USD) is None


# --- El árbol nombra la referencia, no hay que leerle el motivo ------------


def _clasificar(**kw):
    base = {
        "so_id": "S00952",
        "facturada": False,
        "teorico_bs_pagado": False,
        "teorico_usd_pagado": False,
        "factura_real_pagada": False,
    }
    base.update(kw)
    return clasificar_estado_cxc(**base)


def test_cada_rama_dice_por_cual_referencia_salio() -> None:
    assert _clasificar(teorico_usd_pagado=True).referencia == ReferenciaCxC.TEORICO_USD
    assert _clasificar(teorico_bs_pagado=True).referencia == ReferenciaCxC.TEORICO_BS
    assert _clasificar(venta_real_pagada=True).referencia == ReferenciaCxC.VENTA_REAL
    assert (
        _clasificar(facturada=True, factura_real_pagada=True).referencia
        == ReferenciaCxC.FACTURA_REAL
    )
    assert (
        _clasificar(facturada=True, factura_pagada_confirmada_odoo=True).referencia
        == ReferenciaCxC.ODOO
    )


def test_una_orden_que_sigue_en_cobranza_no_tiene_referencia() -> None:
    r = _clasificar()
    assert r.sale_de_cxc is False
    assert r.referencia == ReferenciaCxC.NINGUNA


def test_la_segunda_pasada_nombra_la_misma_referencia_pero_sin_confirmar() -> None:
    """"En proceso de pago": antes de facturar es la única señal posible,
    y la bandeja tiene que poder distinguirla de un pago confirmado."""
    r = _clasificar(teorico_usd_pagado_incl_pendiente=True)
    assert r.referencia == ReferenciaCxC.TEORICO_USD
    assert r.confirmado is False
    assert r.sale_de_cxc is True


# --- Subtotal pagado, IVA no ------------------------------------------------
#
# Pregunta del usuario (septiembre 2026): el árbol compara contra totales CON
# IVA, pero un cliente puede pagar el subtotal y esperar la factura para pagar
# el impuesto o para retenerlo. Hasta que no lo pague, lo debe -- así que debe
# seguir en CxC. Pero si no se factura, "puede darse el caso de que nunca lo
# pague porque no se ha generado la obligación legal de pagarlo".
#
# Es el único caso donde "sigue debiendo" y "hay que facturarla" no son la
# misma respuesta. Antes el árbol las trataba como una sola: sin salir de CxC
# no había destino de bandeja, y la orden quedaba trabada.
#
# Aplica a TODOS los clientes, no solo a los agentes de retención (decisión
# del usuario). Para el agente el IVA nunca será una cobranza -- lo entera al
# SENIAT -- y para el cliente normal es deuda real pero exigible solo después
# de facturar.


def test_subtotal_pagado_se_factura_pero_sigue_en_cobranza() -> None:
    r = _clasificar(subtotal_pagado=True)
    assert r.bandeja_destino is not None
    assert r.sale_de_cxc is False
    assert r.referencia == ReferenciaCxC.SUBTOTAL_SIN_IVA


def test_es_la_unica_rama_que_enruta_sin_salir_de_cxc() -> None:
    """Fija la excepción para que un cambio futuro no la borre por
    parecer una inconsistencia."""
    combinaciones = [
        {"teorico_usd_pagado": True},
        {"teorico_bs_pagado": True},
        {"venta_real_pagada": True},
        {"facturada": True, "factura_real_pagada": True},
        {"facturada": True, "factura_pagada_confirmada_odoo": True},
        {"teorico_usd_pagado_incl_pendiente": True},
    ]
    for kw in combinaciones:
        r = _clasificar(**kw)
        assert r.bandeja_destino is None or r.sale_de_cxc is True


def test_un_pago_completo_no_pasa_por_la_rama_del_subtotal() -> None:
    """Va al final del árbol: solo actúa si ninguna referencia completa
    alcanzó. Cubrir el total con IVA sale por su rama normal."""
    r = _clasificar(teorico_usd_pagado=True, subtotal_pagado=True)
    assert r.sale_de_cxc is True
    assert r.referencia == ReferenciaCxC.TEORICO_USD


def test_una_orden_ya_facturada_no_entra_por_esta_rama() -> None:
    """Facturada, el IVA ya tiene documento y lo recoge la bandeja de IVA
    pendiente, que sigue el rastro hasta ``account.move.wh_iva``."""
    r = _clasificar(facturada=True, subtotal_pagado=True)
    assert r.bandeja_destino is None
    assert r.referencia == ReferenciaCxC.NINGUNA


def test_distingue_el_subtotal_confirmado_del_que_esta_en_proceso() -> None:
    assert _clasificar(subtotal_pagado=True).confirmado is True
    assert _clasificar(subtotal_pagado_incl_pendiente=True).confirmado is False


def test_sin_teorico_cumplido_la_rama_del_subtotal_no_muestra_ninguno() -> None:
    assert _teorico_de_referencia(_ITEM, ReferenciaCxC.SUBTOTAL_SIN_IVA) is None
