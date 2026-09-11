"""Los promedios de la tasa Binance y las ventanas que se solapan (Fase 2.4, 16).

`_get_tasas_promedios_sync` salió del barrido de funciones de dinero sin pruebas.
Tenía dos cosas que ninguna prueba miraba, y una está viva hoy.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cxc.engine.promedios_tasas import (
    diagnostico_de_promedios,
    diferencial_pct,
    hora_de,
    promediar,
)


def _f(hora, tasa="1000", dia="2026-09-11"):
    return {"timestamp": f"{dia} {hora:02d}:00:00", "tasa_binance": tasa}


# --- leer la hora del sello -------------------------------------------------


@pytest.mark.parametrize(
    "sello,esperado",
    [
        ("2026-09-11 10:00:00", 10),
        ("2026-09-11T10:00:00", 10),
        ("2026-09-11T07:30:00", 7),
        ("", None),
        ("sin hora", None),
    ],
)
def test_la_hora_se_lee_de_las_dos_formas_de_sello(sello, esperado) -> None:
    """El sello viene con T o con espacio según de dónde salga la fila."""
    assert hora_de({"timestamp": sello}) == esperado


def test_un_sello_ilegible_no_cuenta_como_medianoche() -> None:
    """Devolver 0 la metería en el promedio de la mañana.

    Es la misma trampa que este proyecto viene corrigiendo: un dato ausente
    convertido en un número que parece válido.
    """
    assert hora_de({"timestamp": "roto"}) is None
    p = promediar([{"timestamp": "roto", "tasa_binance": "999"}])
    assert p.capturas_diario == 0
    assert p.diario is None


# --- el solapamiento, que es el hallazgo -----------------------------------


def test_con_captura_de_manana_las_ventanas_no_se_solapan() -> None:
    """El caso del 10-sep: hay capturas 7-8, así que cada ventana usa las suyas."""
    p = promediar([_f(7, "900"), _f(8, "910"), _f(11, "1000"), _f(17, "1020")])
    assert p.manana == Decimal("905")
    assert p.tarde == Decimal("1000")
    assert not p.ventanas_solapadas


def test_SIN_captura_de_manana_la_misma_hora_entra_en_las_DOS_ventanas() -> None:
    """El caso del 11-sep-2026, medido y vivo.

    Las tres capturas del día son de las 10 y no hay ninguna entre las 6 y las 9.
    La mañana cae a su respaldo (hora < 12), que incluye las 10 — y las 10 ya están
    en la tarde preferida. La pantalla muestra dos promedios distintos que son el
    mismo número.
    """
    filas = [_f(10, "954"), _f(10, "955"), _f(10, "955.242")]
    p = promediar(filas)
    assert p.ventanas_solapadas
    assert p.horas_compartidas == (10,)
    assert p.manana == p.tarde, "los dos promedios salen de las mismas capturas"


def test_con_ventanas_disjuntas_la_manana_de_ese_dia_es_None() -> None:
    """Y ése es el dato verdadero: ese día no hubo captura de mañana.

    ``None`` no es cero ni es el promedio de la tarde: es «no se midió».
    """
    filas = [_f(10, "954"), _f(10, "955"), _f(10, "955.242")]
    p = promediar(filas, ventanas_disjuntas=True)
    assert p.manana is None
    assert p.tarde is not None
    assert not p.ventanas_solapadas


def test_la_hora_11_tambien_solapa() -> None:
    """El respaldo de la mañana llega hasta las 11 y la tarde preferida empieza en 10."""
    p = promediar([_f(11, "1000")])
    assert p.horas_compartidas == (11,)


def test_la_hora_12_no_solapa_porque_la_manana_de_respaldo_corta_ahi() -> None:
    p = promediar([_f(12, "1000")])
    assert not p.ventanas_solapadas
    assert p.manana is None, "12 no entra en la mañana por ninguna vía"


# --- las capturas que no se promedian --------------------------------------


def test_una_captura_fallida_no_arrastra_el_promedio() -> None:
    """Una captura que falló guarda cero, y promediarla bajaría el promedio.

    Con 1000 y un cero, el promedio sería 500 — la mitad de la tasa real, sin que
    nada lo diga.
    """
    p = promediar([_f(7, "1000"), _f(7, "0")])
    assert p.manana == Decimal("1000")
    assert p.capturas_manana == 1


@pytest.mark.parametrize("mala", ["0", "-100", None, "", "ilegible"])
def test_las_tasas_invalidas_se_descartan(mala) -> None:
    p = promediar([_f(7, "1000"), _f(7, mala)])
    assert p.capturas_diario == 1


def test_sin_capturas_el_promedio_es_None_y_no_cero() -> None:
    for p in (promediar([]), promediar([_f(7, "0")])):
        assert p.manana is None and p.tarde is None and p.diario is None


# --- el diferencial y su denominador ---------------------------------------


def test_el_diferencial_se_calcula_sobre_BINANCE_no_sobre_la_BCV() -> None:
    """El nombre del campo no lo dice, y cambia bastante según el denominador.

    Con BCV 700 y Binance 800: sobre Binance es 12,5 %, sobre BCV sería 14,29 %.
    """
    d = diferencial_pct(Decimal("800"), Decimal("700"))
    assert d == Decimal("12.5")


@pytest.mark.parametrize(
    "prom,bcv",
    [
        (None, Decimal("700")),
        (Decimal("0"), Decimal("700")),
        (Decimal("800"), Decimal("0")),
    ],
)
def test_sin_una_de_las_dos_tasas_el_diferencial_es_cero(prom, bcv) -> None:
    """Comportamiento preservado del cuerpo original."""
    assert diferencial_pct(prom, bcv) == Decimal("0")


# --- el diagnóstico ---------------------------------------------------------


def test_el_diagnostico_nombra_la_hora_que_se_comparte() -> None:
    d = diagnostico_de_promedios([_f(10, "954"), _f(10, "955")])
    assert d.difieren
    assert "SOLAPAN" in d.nota
    assert "[10]" in d.nota


def test_cuando_no_solapan_el_diagnostico_da_los_dos_denominadores() -> None:
    d = diagnostico_de_promedios([_f(7, "900"), _f(11, "1000"), _f(17, "1010")])
    assert not d.difieren
    assert "no se solapan" in d.nota
    assert "sobre 1 captura(s)" in d.nota


def test_sin_capturas_el_diagnostico_lo_dice_en_vez_de_mostrar_ceros() -> None:
    d = diagnostico_de_promedios([])
    assert "NO es un promedio de cero" in d.nota
    assert not d.difieren, "dos ausencias no difieren"
