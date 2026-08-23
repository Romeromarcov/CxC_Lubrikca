"""``_vinc_usd_equiv`` -- bug real (reportado por el usuario, agosto 2026,

cliente CONSTRUCTORA GRANO AGREGADO/orden S00608): varios lugares de
``app.py`` (``_get_conciliaciones_sugerencias_sync``, ``get_resumen``,
historial de Vinculaciones, ``get_auditoria``) sumaban
``Vinculacion.monto_aplicado`` crudo como si siempre estuviera en USD --
pero está denominado en ``moneda_abono``: para un pago en VES es un número
en Bs (a veces millones), no en dólares. Cualquier pago VES con una
Vinculación ya existente quedaba con un "saldo vinculado" gigantesco,
sacándolo para siempre de las sugerencias FIFO/reportes de saldo real, sin
importar el residual genuino que quedara disponible en Odoo. La fuente de
verdad correcta son los equivalentes USD ya congelados
(``equiv_usd_bcv``/``equiv_usd_binance``, mismo criterio que
``engine.equivalents.valor_pagado_usd``), nunca ``monto_aplicado`` crudo.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from cxc.models import EstadoVinculacion, Moneda, TipoTasa, Vinculacion
from cxc.web.app import _vinc_usd_equiv


def _vinc(**overrides) -> Vinculacion:
    base = {
        "vinc_id": "V1",
        "pago_id": "P1",
        "so_id": "S00001",
        "monto_aplicado": Decimal("100"),
        "hora_pago_confirmada": datetime(2026, 7, 20, 12, 0, 0),
        "tasa_bcv_aplicada": Decimal("40.0"),
        "tasa_binance_aplicada": Decimal("45.0"),
        "es_tasa_heredada": False,
        "estado": EstadoVinculacion.PENDIENTE,
        "moneda_abono": Moneda.VES,
    }
    base.update(overrides)
    return Vinculacion(**base)


def test_pago_usd_usa_monto_aplicado_directo() -> None:
    v = _vinc(moneda_abono=Moneda.USD, monto_aplicado=Decimal("250"))
    assert _vinc_usd_equiv(v) == Decimal("250")


def test_pago_ves_usa_equiv_usd_bcv_no_monto_aplicado_crudo() -> None:
    # Caso real: 2.436.828,07 Bs aplicados, pero el equivalente real es
    # $3.378,17 -- sumar el monto crudo sería tratar Bs como si fueran $.
    v = _vinc(
        moneda_abono=Moneda.VES,
        monto_aplicado=Decimal("2436828.07"),
        equiv_usd_bcv=Decimal("3378.17"),
    )
    assert _vinc_usd_equiv(v) == Decimal("3378.17")


def test_pago_ves_ruta_binance_usa_equiv_usd_binance() -> None:
    v = _vinc(
        moneda_abono=Moneda.VES,
        monto_aplicado=Decimal("1000"),
        tipo_tasa_abono=TipoTasa.BINANCE,
        equiv_usd_bcv=Decimal("22.0"),
        equiv_usd_binance=Decimal("20.0"),
    )
    assert _vinc_usd_equiv(v) == Decimal("20.0")


def test_sin_equivalentes_congelados_cae_a_monto_aplicado_como_ultimo_recurso() -> None:
    """Vinculación vieja/malformada sin ningún equivalente -- no debe

    lanzar excepción en un endpoint vivo (a diferencia de
    ``valor_pagado_usd``, que sí exige el equivalente); se tolera el dato
    crudo como último recurso en vez de reventar la página.
    """
    v = _vinc(moneda_abono=Moneda.VES, monto_aplicado=Decimal("500"))
    assert _vinc_usd_equiv(v) == Decimal("500")
