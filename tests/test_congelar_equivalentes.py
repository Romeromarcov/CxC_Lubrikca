"""Los cuatro equivalentes de una vinculación, congelados con UNA sola precisión.

Estas ocho líneas estaban escritas **cuatro veces** —`post_vincular`,
`put_editar_vinculacion`, `_vincular_masivo_sync` y el resync del demonio— y las cuatro
**sin pasar por `q6`**, mientras las dos rutas que editan tasas sí usan
`equivalentes_bcv`/`equivalentes_binance`, que redondean a seis decimales.

O sea que el mismo equivalente **congelado** —el que por diseño no se recalcula nunca—
quedaba guardado con dos precisiones distintas según quién lo escribiera. Y el docstring
de `equivalentes_bcv` decía «ahora las dos rutas comparten esta función»: cierto de esas
dos, falso de las otras cuatro.

Lo encontré porque el barrido de cobertura marcó el mismo bloque en dos funciones; al
buscar el texto exacto aparecieron tres copias, y la cuarta era la variante del resync.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from cxc.models import Moneda
from cxc.web.app import _congelar_equivalentes, _moneda_de

TASA_BCV = Decimal("3")
TASA_BIN = Decimal("4")


# --- la precisión, que es el defecto ----------------------------------------


def test_un_pago_en_bolivares_se_congela_a_SEIS_decimales() -> None:
    """El caso del docstring de la pieza: 1.000 Bs a tasa 3.

    Sin `q6` quedaba `333.3333333333333333333333333`; con `q6`, `333.333333`. Los dos
    son «correctos» y ahí está el problema: el mismo concepto guardado de dos maneras,
    en un campo que no se vuelve a calcular nunca.
    """
    usd_bcv, usd_bin, ves_bcv, ves_bin = _congelar_equivalentes(
        Decimal("1000"), Moneda.VES, TASA_BCV, TASA_BIN
    )
    assert usd_bcv == Decimal("333.333333")
    assert str(usd_bcv) == "333.333333", "exactamente seis decimales, no una fracción larga"
    assert usd_bin == Decimal("250.000000")
    assert ves_bcv == Decimal("1000.000000")
    assert ves_bin == Decimal("1000.000000")


def test_un_pago_en_dolares_multiplica_en_vez_de_dividir() -> None:
    """El equivalente USD es el monto tal cual; el VES sale de la tasa."""
    usd_bcv, usd_bin, ves_bcv, ves_bin = _congelar_equivalentes(
        Decimal("100"), Moneda.USD, TASA_BCV, TASA_BIN
    )
    assert usd_bcv == usd_bin == Decimal("100.000000")
    assert ves_bcv == Decimal("300.000000")
    assert ves_bin == Decimal("400.000000")


def test_el_orden_de_los_cuatro_es_el_que_se_guarda() -> None:
    """`(usd_bcv, usd_binance, ves_bcv, ves_binance)`.

    No es alfabético ni agrupado por tasa: es el orden en que los cuatro cuerpos
    originales los asignaban, y equivocarlo intercambiaría el equivalente BCV con el
    Binance sin que nada falle.
    """
    cuatro = _congelar_equivalentes(Decimal("1000"), Moneda.VES, TASA_BCV, TASA_BIN)
    assert cuatro == (
        Decimal("333.333333"),
        Decimal("250.000000"),
        Decimal("1000.000000"),
        Decimal("1000.000000"),
    )


# --- la tasa que no sirve ----------------------------------------------------


@pytest.mark.parametrize("mala", [Decimal("0"), Decimal("-3")])
def test_una_tasa_BCV_no_positiva_LANZA(mala) -> None:
    """Deliberado: un equivalente calculado con una tasa en cero no significa nada, y
    queda congelado así para siempre."""
    with pytest.raises(ValueError, match="BCV"):
        _congelar_equivalentes(Decimal("1000"), Moneda.VES, mala, TASA_BIN)


@pytest.mark.parametrize("mala", [Decimal("0"), Decimal("-4")])
def test_una_tasa_Binance_no_positiva_tambien_LANZA(mala) -> None:
    with pytest.raises(ValueError):
        _congelar_equivalentes(Decimal("1000"), Moneda.VES, TASA_BCV, mala)


def test_un_pago_en_dolares_TAMPOCO_pasa_con_tasa_cero() -> None:
    """Aunque el equivalente USD no la necesite, el VES sí: guardar uno de los cuatro
    en cero dejaría la vinculación a medio congelar."""
    with pytest.raises(ValueError):
        _congelar_equivalentes(Decimal("100"), Moneda.USD, Decimal("0"), TASA_BIN)


# --- la moneda, que viaja como texto libre -----------------------------------


@pytest.mark.parametrize(
    "crudo,esperada",
    [("VES", Moneda.VES), ("ves", Moneda.VES), ("USD", Moneda.USD)],
)
def test_la_moneda_se_normaliza(crudo, esperada) -> None:
    assert _moneda_de(crudo) == esperada


@pytest.mark.parametrize("raro", ["", None, "EUR", "no es una moneda", "  "])
def test_una_moneda_que_no_se_reconoce_cae_a_USD(raro) -> None:
    """Es lo que hacían los cuatro cuerpos originales con su `== "USD"` / else.

    Preservado a propósito y no «mejorado»: cambiarlo movería el equivalente de
    cualquier vinculación cuya moneda no se lea, y eso es un cambio de montos.
    """
    assert _moneda_de(raro) == Moneda.USD


# --- la guarda de la deduplicación -------------------------------------------


def test_los_cuatro_sitios_delegan_en_el_helper() -> None:
    """Si alguien vuelve a escribir el bloque inline, falla.

    Cuatro copias de la misma cuenta, cada una capaz de guardar el equivalente con otra
    precisión. La guarda es más valiosa que de costumbre porque el campo es **congelado**:
    un error acá no se corrige en la próxima corrida.
    """
    fuente = Path("src/cxc/web/app.py").read_text(encoding="utf-8")
    assert fuente.count("_congelar_equivalentes(") == 5, (
        "una definición y cuatro llamadas: post_vincular, put_editar_vinculacion, "
        "_vincular_masivo_sync y el resync del demonio"
    )
    assert 'if pago.moneda == "USD":\n            equiv_usd_bcv = monto_dec' not in fuente, (
        "el bloque volvió a estar inline"
    )
    assert "equiv_usd_bcv = monto_nuevo / tasa_bcv" not in fuente, (
        "la variante del resync volvió a estar inline"
    )


def test_cada_sitio_maneja_el_ValueError_a_su_manera() -> None:
    """Y eso es el punto: el helper lanza, y qué hacer con eso es de cada llamador.

    - las dos rutas manuales devuelven **400**, porque «no hay tasa» se corrige cargando
      la tasa y no es un error del servidor;
    - la masiva hace `continue`, porque abortar el lote entero por una fila sería peor;
    - el resync devuelve la vinculación **sin cambio**, porque el `except Exception` de
      su llamador envuelve las 1.494 y una sola tasa mala detendría todo el ciclo.
    """
    fuente = Path("src/cxc/web/app.py").read_text(encoding="utf-8")
    assert fuente.count("raise HTTPException(status_code=400, detail=str(e_tasa)) from e_tasa") == 2
    assert "Vinculacion masiva: pago %s con orden %s se saltea, %s" in fuente
    assert "no se resincroniza: %s (BCV %s, Binance %s)" in fuente
