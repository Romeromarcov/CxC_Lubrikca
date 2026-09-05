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
