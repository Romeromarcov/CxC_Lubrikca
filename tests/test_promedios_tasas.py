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


# --- el rango del día, compartido por los dos endpoints de tasa -------------


def test_el_rango_sale_del_minimo_y_maximo_capturados() -> None:
    from cxc.engine.promedios_tasas import rango_binance_del_dia

    r = rango_binance_del_dia([_f(7, "900"), _f(11, "1000"), _f(17, "950")])
    assert (r.minimo, r.maximo, r.capturas) == (Decimal("900"), Decimal("1000"), 3)
    assert r.verificado
    assert r.acepta(Decimal("950"))
    assert not r.acepta(Decimal("899"))
    assert not r.acepta(Decimal("1001"))


def test_los_bordes_del_rango_se_aceptan() -> None:
    from cxc.engine.promedios_tasas import rango_binance_del_dia

    r = rango_binance_del_dia([_f(7, "900"), _f(17, "1000")])
    assert r.acepta(Decimal("900")) and r.acepta(Decimal("1000"))


def test_sin_capturas_ese_dia_la_guarda_NO_corre_y_lo_dice() -> None:
    """El hallazgo: la validación estaba apagada en el 100 % de los casos.

    Las 1.494 vinculaciones del espejo están en fechas sin capturas Binance, así
    que `acepta` devolvía True para cualquier tasa. Sigue devolviendo True —
    rechazar impediría corregir la tasa de un pago viejo, que es para lo que
    sirven esas pantallas— pero ahora `verificado` dice que no se comprobó.
    """
    from cxc.engine.promedios_tasas import rango_binance_del_dia

    r = rango_binance_del_dia([])
    assert not r.verificado
    assert r.capturas == 0
    assert r.acepta(Decimal("999999")), "acepta, pero avisando que no verificó"
    assert r.minimo is None and r.maximo is None


def test_una_captura_fallida_no_baja_el_minimo_a_cero() -> None:
    """Si el cero entrara al rango, aceptaría cualquier tasa por abajo."""
    from cxc.engine.promedios_tasas import rango_binance_del_dia

    r = rango_binance_del_dia([_f(7, "900"), _f(8, "0"), _f(17, "1000")])
    assert r.minimo == Decimal("900")
    assert not r.acepta(Decimal("1"))


def test_el_rango_lee_objetos_y_dicts() -> None:
    """Un endpoint le pasa filas de ``SerieTasas`` y el otro dicts."""
    from types import SimpleNamespace

    from cxc.engine.promedios_tasas import rango_binance_del_dia

    de_objetos = rango_binance_del_dia(
        [SimpleNamespace(tasa_binance=Decimal("900")), SimpleNamespace(tasa_binance=Decimal("950"))]
    )
    de_dicts = rango_binance_del_dia([{"tasa_binance": "900"}, {"tasa_binance": "950"}])
    assert (de_objetos.minimo, de_objetos.maximo) == (de_dicts.minimo, de_dicts.maximo)


def test_una_sola_captura_da_un_rango_de_un_punto() -> None:
    """Y entonces solo esa tasa exacta se acepta, que es lo correcto: es el único
    dato que hay de ese día."""
    from cxc.engine.promedios_tasas import rango_binance_del_dia

    r = rango_binance_del_dia([_f(10, "954.75")])
    assert r.verificado
    assert r.acepta(Decimal("954.75"))
    assert not r.acepta(Decimal("954.76"))


# --- el endpoint, que ahora llama a la pieza ---------------------------------


def test_el_endpoint_expone_los_dos_avisos() -> None:
    """Los dos hallazgos viajan en la respuesta, no solo en un docstring.

    `ventanas_solapadas` dice que mañana y tarde salen de las mismas capturas, y
    `filas_son_de_hoy` dice si el «promedio de hoy» es de hoy. Sin ellos, los dos
    defectos siguen ahí pero invisibles — que es justo la forma que este plan viene
    corrigiendo.

    Y hay una razón para probarlo por el endpoint y no solo por la pieza: extraer la
    lógica movió la cobertura al módulo nuevo sin bajar la del original. La barra de
    `app.py` bajó cuando el endpoint pasó a llamar a la pieza, medido: esa función
    fue de 54 de 75 líneas sin cubrir a ninguna en la lista.
    """
    from unittest.mock import MagicMock, patch

    from fastapi.testclient import TestClient

    import cxc.web.app as app

    repo = MagicMock()
    # Tres capturas a las 10 y ninguna entre las 6 y las 9: el caso del 11-sep.
    repo.all_serie_tasas.return_value = []
    filas = [
        {"timestamp": "2026-09-11 10:00:00", "tasa_binance": "954", "tasa_bcv": "832"},
        {"timestamp": "2026-09-11 10:15:00", "tasa_binance": "955", "tasa_bcv": "832"},
    ]

    async def _nada():
        return None

    with (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app._all_serie_tasas_rows", return_value=filas),
        patch("cxc.web.app.hay_sesion_valida", return_value=True),
        patch("cxc.web.app.run_scraper_in_background", _nada),
        patch("cxc.web.app.run_sync_in_background", _nada),
        patch("cxc.web.app.date") as fecha,
    ):
        fecha.today.return_value.isoformat.return_value = "2026-09-11"
        with TestClient(app.app) as cliente:
            r = cliente.get("/api/config/tasas-promedios")

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["ventanas_solapadas"] is True
    assert cuerpo["horas_compartidas"] == [10]
    assert cuerpo["filas_son_de_hoy"] is True
    assert cuerpo["tasa_binance_manana"] == cuerpo["tasa_binance_tarde"], (
        "el aviso existe porque los dos promedios salen de las mismas capturas"
    )
    assert cuerpo["capturas"] == {"manana": 2, "tarde": 2, "diario": 2}


# --- el endpoint de configuración de tasas, que tenía la cuenta inline -------


def test_get_config_tasas_usa_la_pieza_y_dice_si_el_diferencial_es_verificable() -> None:
    """`get_config_tasas` calculaba `diff_bs / tbin * 100` a mano.

    Es exactamente `diferencial_pct`, que ya existía — otro duplicado sin cablear. Y
    el cuerpo inline tenía un borde distinto: solo se protegía de `tbin == 0`, así que
    **con la BCV en cero mostraba 100 %**, o sea presentaba una captura fallida como
    un diferencial real (el scraper guarda cero cuando falla).

    Ninguna de las dos respuestas es correcta, y por eso la fila lleva ahora
    `diferencial_verificable`: un cero medido y un cero por no haber podido medir no
    son el mismo cero. Es la misma disciplina que `RangoDelDia.verificado`.

    Medido el 11-sep-2026: 0 de las 30 filas del espejo tienen la BCV en cero. Pero 30
    filas son dos días, así que eso no prueba que no pase — el test lo fija igual.
    """
    from unittest.mock import MagicMock, patch

    from fastapi.testclient import TestClient

    import cxc.web.app as app

    filas = [
        {"timestamp": "2026-09-11 10:00:00", "tasa_bcv": "700", "tasa_binance": "800",
         "fuente": "scraper"},
        # Captura de BCV fallida: el inline decía 100 %.
        {"timestamp": "2026-09-11 11:00:00", "tasa_bcv": "0", "tasa_binance": "800",
         "fuente": "scraper"},
        # Captura de Binance fallida: el inline ya devolvía 0 acá.
        {"timestamp": "2026-09-11 12:00:00", "tasa_bcv": "700", "tasa_binance": "0",
         "fuente": "scraper"},
    ]

    async def _nada():
        return None

    with (
        patch("cxc.web.app.get_repo", return_value=MagicMock()),
        patch("cxc.web.app._all_serie_tasas_rows", return_value=filas),
        patch("cxc.web.app.run_scraper_in_background", _nada),
        patch("cxc.web.app.run_sync_in_background", _nada),
        patch("cxc.web.app._aplicar_migraciones_pendientes"),
        TestClient(app.app) as cliente,
    ):
        r = cliente.get("/api/config/tasas")

    assert r.status_code == 200, r.text
    # La respuesta viene en orden inverso al de las filas.
    por_hora = {x["timestamp"][-8:]: x for x in r.json()}

    bien = por_hora["10:00:00"]
    assert bien["diferencia_pct"] == 12.5, "sobre Binance: (800-700)/800. Sobre BCV sería 14,29"
    assert bien["diferencia_bs"] == 100.0
    assert bien["diferencial_verificable"] is True

    sin_bcv = por_hora["11:00:00"]
    assert sin_bcv["diferencia_pct"] == 0.0, "antes decía 100 %, que era una captura fallida"
    assert sin_bcv["diferencial_verificable"] is False, "y ahora se sabe que no se midió"

    sin_binance = por_hora["12:00:00"]
    assert sin_binance["diferencia_pct"] == 0.0
    assert sin_binance["diferencial_verificable"] is False
