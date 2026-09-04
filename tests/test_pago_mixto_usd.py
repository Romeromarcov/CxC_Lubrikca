"""Una orden pagada en dólares Y bolívares cuenta como USD.

Decisión de negocio del usuario (auditoría de septiembre 2026), en dos
partes:

  1. Si hay abonos en las dos monedas, la orden se evalúa como USD -- así
     recibe las reglas marcadas solo para dólares, incluido el diferencial
     cambiario del 35 %.
  2. Los abonos en bolívares se consideran por su equivalente **Binance**.
     Esa segunda parte YA la hacía el motor donde corresponde: el chequeo
     de "orden cumplida al 100%" contra la Lista USD compara con el total
     Binance. Se comprobó con el ejemplo real del propio usuario ($10 USD
     + 19.560,85 VES): Binance da 32,76 y así se declara cumplida, mientras
     el total BCV (36,35) es el que se usa para calcular la Nota de
     Crédito sugerida. Son dos valoraciones para dos propósitos distintos,
     y llevar Binance a las dos rompía el cálculo de la NC.

Antes bastaba un solo abono en bolívares para volver VES a la orden
entera. En producción eso afectaba a 19 órdenes de las 159 con pagos, y el
caso extremo -- S00952, con 1.624,41 en dólares contra 175,83 en
bolívares -- perdía las reglas en dólares por esos 175.

Solo una orden pagada ÍNTEGRAMENTE en bolívares se evalúa como VES.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from cxc.engine.equivalents import es_pago_mixto, valor_pagado_usd
from cxc.models import EstadoVinculacion, Moneda, TipoTasa, Vinculacion


def _vinc(vid, moneda, tipo_tasa, monto, eq_bcv, eq_binance):
    return Vinculacion(
        vinc_id=vid,
        pago_id="P" + vid,
        so_id="S00952",
        monto_aplicado=Decimal(monto),
        hora_pago_confirmada=datetime(2026, 8, 1, 12, 0),
        tasa_bcv_aplicada=Decimal("100"),
        tasa_binance_aplicada=Decimal("120"),
        es_tasa_heredada=False,
        equiv_usd_bcv=Decimal(eq_bcv),
        equiv_usd_binance=Decimal(eq_binance),
        moneda_abono=moneda,
        tipo_tasa_abono=tipo_tasa,
        estado=EstadoVinculacion.CONCILIADO,
    )


_USD = _vinc("V_USD", Moneda.USD, TipoTasa.N_A, "1624.41", "1624.41", "1624.41")
# Abono en bolívares con ruta BCV: sus dos equivalentes difieren.
_VES = _vinc("V_VES", Moneda.VES, TipoTasa.BCV, "17583.00", "175.83", "146.53")


def test_reconoce_el_pago_mixto() -> None:
    assert es_pago_mixto([_USD, _VES]) is True
    assert es_pago_mixto([_USD]) is False
    assert es_pago_mixto([_VES]) is False
    assert es_pago_mixto([]) is False


def test_cada_abono_se_valora_por_su_ruta_estampada() -> None:
    """``valor_pagado_usd`` NO cambia con el pago mixto: alimenta el cálculo

    de la Nota de Crédito, que el ejemplo real del usuario computa con el
    total BCV. La preferencia por Binance vive en el chequeo de "cumplida
    al 100%", que es otro camino.
    """
    assert valor_pagado_usd([_VES]) == Decimal("175.83")
    assert valor_pagado_usd([_USD, _VES]) == Decimal("1624.41") + Decimal("175.83")


def test_un_abono_en_dolares_solo_no_cambia_nada() -> None:
    assert valor_pagado_usd([_USD]) == Decimal("1624.41")


def test_la_moneda_de_evaluacion_de_una_orden_mixta_es_usd() -> None:
    """Réplica del criterio que aplica el motor: solo el pago íntegramente
    en bolívares se evalúa como VES."""

    def moneda_de(monedas: set[str]) -> str:
        return "VES" if monedas == {"VES"} else "USD"

    assert moneda_de({"USD", "VES"}) == "USD"
    assert moneda_de({"VES"}) == "VES"
    assert moneda_de({"USD"}) == "USD"
    assert moneda_de(set()) == "USD"
