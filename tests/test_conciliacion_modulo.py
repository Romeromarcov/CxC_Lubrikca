"""Las dos referencias de un residual, en su propio módulo (Fase 2.4).

Sexta pieza extraída, y la primera del área que el plan nombra explícitamente.
Además de la medición A/B, estos tests fijan las dos cosas que el código anterior
resolvía con un truco o dejaba implícitas:

- que un pago en bolívares tiene **dos** referencias en dólares y las dos se
  muestran, porque la que aplique al vincular puede ajustarse antes de confirmar;
- que con tasa Binance en cero **no se divide ni se devuelve cero**, sino la
  referencia BCV. Devolver cero haría ver el saldo como inexistente, que es la
  clase de error que este blindaje persigue.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cxc.engine.conciliacion import campos_de_saldo, usd_bcv_a_binance

D = Decimal


# --- la reexpresión -------------------------------------------------------


def test_un_pago_en_dolares_no_cambia_de_valor() -> None:
    """No hay tasa que aplicar."""
    assert usd_bcv_a_binance(D("100"), "USD", D("827.74"), D("900")) == D("100")


def test_un_pago_en_bolivares_se_reexpresa_con_la_otra_tasa() -> None:
    """Ancla en bolívares y reconvierte: 100 USD a 800 son 80.000 Bs, que a
    1.000 son 80 USD."""
    assert usd_bcv_a_binance(D("100"), "VES", D("800"), D("1000")) == D("80")


@pytest.mark.parametrize("binance", [D("0"), D("-1"), D("-0.01")])
def test_con_tasa_binance_no_positiva_devuelve_la_referencia_bcv(binance) -> None:
    """Ni revienta ni devuelve cero.

    Dividir por cero reventaría. Devolver cero haría ver el saldo como
    inexistente, y un saldo que desaparece es peor que un saldo aproximado:
    devolver la referencia BCV es la única opción que no inventa un número ni
    oculta la deuda.
    """
    assert usd_bcv_a_binance(D("100"), "VES", D("800"), binance) == D("100")


def test_un_residual_en_cero_sigue_en_cero() -> None:
    assert usd_bcv_a_binance(D("0"), "VES", D("800"), D("1000")) == D("0")


def test_la_moneda_se_compara_exacta() -> None:
    """"ves" en minúscula no es "VES". Se fija el comportamiento actual, que es
    tratar cualquier cosa que no sea "VES" como moneda extranjera."""
    assert usd_bcv_a_binance(D("100"), "ves", D("800"), D("1000")) == D("100")


# --- los tres campos ------------------------------------------------------


def test_un_pago_en_bolivares_muestra_las_tres_referencias() -> None:
    campos = campos_de_saldo(D("100"), "VES", D("800"), D("1000"))
    assert campos == {
        "saldo_pago": 100.0,
        "saldo_pago_binance": 80.0,
        "saldo_pago_original": 80000.0,
    }


def test_un_pago_en_dolares_tiene_las_tres_iguales() -> None:
    """No hay dos referencias que mostrar, y las tres coinciden a propósito."""
    campos = campos_de_saldo(D("100"), "USD", D("800"), D("1000"))
    assert campos["saldo_pago"] == campos["saldo_pago_binance"] == 100.0
    assert campos["saldo_pago_original"] == 100.0


def test_los_tres_campos_estan_siempre() -> None:
    """La sugerencia los pinta sin preguntar: si falta uno, la pantalla rompe."""
    for moneda in ("VES", "USD"):
        campos = campos_de_saldo(D("0"), moneda, D("0"), D("0"))
        assert set(campos) == {"saldo_pago", "saldo_pago_binance", "saldo_pago_original"}


def test_el_original_de_un_pago_en_bolivares_usa_la_tasa_bcv() -> None:
    """Y no la de Binance: el monto original es un hecho, no una referencia."""
    campos = campos_de_saldo(D("50"), "VES", D("827.74"), D("1500"))
    assert campos["saldo_pago_original"] == pytest.approx(41387.0)


# --- la medición A/B ------------------------------------------------------

CASOS_AB = [
    (D("100"), "VES", D("800"), D("1000")),
    (D("100"), "USD", D("800"), D("1000")),
    (D("0"), "VES", D("800"), D("1000")),
    (D("100"), "VES", D("800"), D("0")),
    (D("100"), "VES", D("0"), D("1000")),
    (D("33.33"), "VES", D("827.74"), D("911.5")),
    (D("1"), "EUR", D("900"), D("1000")),
]


@pytest.mark.parametrize("restante,moneda,bcv,binance", CASOS_AB)
def test_el_nombre_viejo_da_exactamente_lo_mismo(restante, moneda, bcv, binance) -> None:
    """``usd_bcv_to_binance`` sigue existiendo en app.py y ahora delega."""
    from cxc.web.app import usd_bcv_to_binance

    assert usd_bcv_to_binance(restante, moneda, bcv, binance) == usd_bcv_a_binance(
        restante, moneda, bcv, binance
    )


@pytest.mark.parametrize("restante,moneda,bcv,binance", CASOS_AB)
def test_el_calculo_original_reconstruido_da_lo_mismo(restante, moneda, bcv, binance) -> None:
    """El cuerpo tal como estaba, al lado del extraído.

    Si difieren, la extracción cambió un monto que alguien ve al conciliar.
    """

    def original(usd_via_bcv, moneda_o, bcv_rate, binance_rate):
        if moneda_o == "VES" and binance_rate > Decimal("0"):
            return usd_via_bcv * bcv_rate / binance_rate
        return usd_via_bcv

    def campos_original(restante_usd_bcv, moneda_r, bcv_r, binance_r):
        return {
            "saldo_pago": float(restante_usd_bcv),
            "saldo_pago_binance": float(original(restante_usd_bcv, moneda_r, bcv_r, binance_r)),
            "saldo_pago_original": float(
                restante_usd_bcv * bcv_r if moneda_r == "VES" else restante_usd_bcv
            ),
        }

    assert campos_original(restante, moneda, bcv, binance) == campos_de_saldo(
        restante, moneda, bcv, binance
    )


def test_ya_no_hay_closure_que_amarrar() -> None:
    """El motivo de haber movido esto, hecho explícito.

    La versión anterior era una función definida adentro de un ``for``, y para no
    tomar los valores de la iteración siguiente los amarraba con argumentos por
    defecto. Un truco que hay que recordar es una trampa esperando. Acá los
    cuatro valores entran por parámetro, así que llamarla desde un loop con
    valores distintos da resultados distintos, sin depender de que nadie se
    acuerde de nada.
    """
    pagos = [
        (D("100"), "VES", D("800"), D("1000")),
        (D("200"), "VES", D("500"), D("1000")),
        (D("300"), "USD", D("900"), D("1100")),
    ]
    resultados = [campos_de_saldo(*p) for p in pagos]
    assert [r["saldo_pago_binance"] for r in resultados] == [80.0, 100.0, 300.0]
    assert len({r["saldo_pago_original"] for r in resultados}) == 3
