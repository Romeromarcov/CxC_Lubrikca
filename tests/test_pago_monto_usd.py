"""Convertir un pago a dólares, y el docstring que prometía lo que no cumplía.

`pago_monto_usd` salió del barrido de funciones de dinero sin pruebas. Su docstring
decía «nunca trata un monto en VES como si ya fuera USD» y la función hacía
exactamente eso cuando la tasa llegaba en cero: caía al `return monto_raw` final.

Medido: 513 pagos en VES por 120.848.004,12 Bs. Contados así serían 120,8 millones
de dólares en vez de unos 164.980 — **732 veces**.

Ninguno de los cinco llamadores podía alcanzarlo, porque los cinco toman la tasa de
`get_rate_for_datetime`, que levanta. Pero una garantía escrita que no se cumple es
peor que no escribirla: el próximo que lea el docstring le va a pasar una tasa sin
comprobarla.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cxc.rates import TasaNoDisponible
from cxc.web.app import pago_monto_usd


def test_un_pago_en_dolares_se_devuelve_tal_cual() -> None:
    assert pago_monto_usd(Decimal("100"), "USD", Decimal("732.5")) == Decimal("100")


def test_un_pago_en_dolares_no_necesita_tasa() -> None:
    """Y por lo tanto una tasa en cero no lo afecta: no hay nada que convertir."""
    assert pago_monto_usd(Decimal("100"), "USD", Decimal("0")) == Decimal("100")


def test_un_pago_en_bolivares_se_divide_por_la_tasa() -> None:
    assert pago_monto_usd(Decimal("73250"), "VES", Decimal("732.5")) == Decimal("100")


@pytest.mark.parametrize("tasa", ["0", "0.00", "-1", "-732.5"])
def test_sin_tasa_valida_un_pago_en_bolivares_LEVANTA(tasa) -> None:
    """Lo que antes devolvía el nominal, y es el corazón del hallazgo.

    Con 120.848.004,12 Bs de pagos en VES, devolver el nominal los contaría como
    120,8 millones de dólares. Levantar es la misma decisión que se tomó para la
    tasa: no inventar un número donde no hay dato.
    """
    with pytest.raises(TasaNoDisponible):
        pago_monto_usd(Decimal("73250"), "VES", Decimal(tasa))


def test_el_error_dice_el_monto_y_la_tasa() -> None:
    """Para poder ver qué pago fue sin volver a buscarlo."""
    with pytest.raises(TasaNoDisponible, match="73250"):
        pago_monto_usd(Decimal("73250"), "VES", Decimal("0"))


def test_un_monto_en_cero_en_bolivares_tambien_levanta_sin_tasa() -> None:
    """Podría argumentarse que 0 Bs son 0 USD con cualquier tasa.

    Pero devolverlo silenciaría el hecho de que la tasa falta, y el siguiente pago
    del mismo lote —que no es cero— fallaría igual. Mejor que falle el primero.
    """
    with pytest.raises(TasaNoDisponible):
        pago_monto_usd(Decimal("0"), "VES", Decimal("0"))


def test_una_moneda_desconocida_se_trata_como_dolares() -> None:
    """Comportamiento preservado: sólo ``VES`` dispara la conversión.

    Queda fijado porque es la mitad que NO cambió, y si alguien agregara una
    tercera moneda sin tocar esta función, sus montos entrarían como dólares sin
    convertirse — el mismo error, con otro nombre.
    """
    assert pago_monto_usd(Decimal("100"), "EUR", Decimal("732.5")) == Decimal("100")
    assert pago_monto_usd(Decimal("100"), "", Decimal("732.5")) == Decimal("100")
