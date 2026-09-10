"""Las invariantes del lado del repositorio (segunda mitad de la Fase 2.2).

El plan pedía «restricciones en la base **más validación en el repositorio**». Las
ocho `CHECK` ya estaban; esto es la otra mitad, y su valor es el **mensaje**: hoy
una fila imposible sale como un `IntegrityError` de psycopg a veinte marcos de
profundidad, y quien lo lee no sabe qué fila ni con qué valores.

Lo que **no** cambia, y estos tests lo fijan: una fila que viola una invariante
sigue siendo rechazada y sigue abortando el lote entero. Saltear la fila mala y
escribir el resto habría sido un cambio de comportamiento —escrituras parciales
donde antes no había ninguna— que necesita una decisión del usuario.

El test que importa más es el último grupo: verifica que la versión en Python y la
cláusula SQL de la migración **coincidan** sobre los mismos casos. Duplicar una
regla es aceptable si algo falla cuando se separan.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest

from cxc.db.invariantes import (
    InvarianteViolada,
    exigir,
    traducir_error_de_restriccion,
    verificar_teorico,
    verificar_vinculacion,
)

D = Decimal


@dataclass
class _Vinc:
    vinc_id: str = "V1"
    monto_aplicado: Decimal = D("100")
    tasa_bcv_aplicada: Decimal = D("36.5")
    tasa_binance_aplicada: Decimal = D("38.0")
    moneda_abono: str = "USD"
    equiv_usd_bcv: Decimal | None = None
    equiv_usd_binance: Decimal | None = None


@dataclass
class _Teorico:
    so_id: str = "S00001"
    teorico_ves: Decimal = D("100")
    teorico_usd: Decimal = D("100")
    descuentos_teorico_ves: Decimal = D("0")
    descuentos_teorico_usd: Decimal = D("0")


# --- vinculaciones ---------------------------------------------------------


def test_una_vinculacion_sana_no_tiene_violaciones() -> None:
    assert verificar_vinculacion(_Vinc()) == []


def test_un_monto_negativo_seria_un_cobro_al_reves() -> None:
    fallas = verificar_vinculacion(_Vinc(monto_aplicado=D("-1")))
    assert len(fallas) == 1
    assert fallas[0].restriccion == "ck_vinc_monto_aplicado_no_negativo"
    assert "V1" in str(fallas[0]) and "-1" in str(fallas[0])
    assert "cobro al revés" in str(fallas[0])


@pytest.mark.parametrize(
    "bcv,binance",
    [(D("0"), D("38")), (D("36.5"), D("0")), (D("-1"), D("-1"))],
)
def test_una_tasa_en_cero_deja_el_equivalente_sin_significado(bcv, binance) -> None:
    fallas = verificar_vinculacion(_Vinc(tasa_bcv_aplicada=bcv, tasa_binance_aplicada=binance))
    assert [f.restriccion for f in fallas] == ["ck_vinc_tasas_positivas"]


def test_el_equivalente_no_puede_superar_el_nominal_en_bolivares() -> None:
    """Implicaría una tasa menor que 1, que en esta economía no existe."""
    fallas = verificar_vinculacion(
        _Vinc(moneda_abono="VES", monto_aplicado=D("400"), equiv_usd_bcv=D("500"))
    )
    assert [f.restriccion for f in fallas] == ["ck_vinc_equivalente_no_supera_el_nominal"]
    assert "500" in str(fallas[0]) and "400" in str(fallas[0])


def test_en_dolares_el_equivalente_igual_al_nominal_es_normal() -> None:
    """La invariante es solo para abonos en bolívares.

    En dólares el equivalente ES el nominal, así que compararlos daría un falso
    positivo en cada fila.
    """
    sana = _Vinc(moneda_abono="USD", monto_aplicado=D("100"), equiv_usd_bcv=D("100"))
    assert verificar_vinculacion(sana) == []


def test_se_reportan_todas_las_violaciones_de_una_fila_no_la_primera() -> None:
    fallas = verificar_vinculacion(_Vinc(monto_aplicado=D("-5"), tasa_bcv_aplicada=D("0")))
    assert {f.restriccion for f in fallas} == {
        "ck_vinc_monto_aplicado_no_negativo",
        "ck_vinc_tasas_positivas",
    }


def test_un_equivalente_ausente_no_es_una_violacion() -> None:
    """``None`` es «todavía no se congeló», no «cero»."""
    assert verificar_vinculacion(_Vinc(moneda_abono="VES", monto_aplicado=D("100"))) == []


# --- teóricos --------------------------------------------------------------


def test_un_teorico_negativo_no_es_una_venta() -> None:
    """Rompe las DOS cláusulas, y el SQL también.

    Con el teórico en -1 y el descuento en 0, `0 <= -1 + 0.01` es falso, así que
    la cláusula del descuento se viola sola. No es un falso positivo: es que un
    teórico negativo deja de tener sentido de todas las formas a la vez, y
    reportar las dos es más informativo que reportar una.
    """
    fallas = verificar_teorico(_Teorico(teorico_usd=D("-1")))
    assert {f.restriccion for f in fallas} == {
        "ck_teoricos_no_negativos",
        "ck_descuento_no_supera_el_teorico",
    }
    assert all("S00001" in str(f) for f in fallas)


def test_un_descuento_mayor_que_el_teorico_deja_la_venta_en_negativo() -> None:
    fallas = verificar_teorico(_Teorico(teorico_usd=D("100"), descuentos_teorico_usd=D("150")))
    assert [f.restriccion for f in fallas] == ["ck_descuento_no_supera_el_teorico"]


def test_el_margen_de_un_centavo_del_sql_se_respeta() -> None:
    """La cláusula SQL usa ``+ 0.01`` porque los dos lados redondean por separado.

    Si la versión Python fuera más estricta, rechazaría filas que la base acepta.
    """
    justo = _Teorico(teorico_usd=D("100"), descuentos_teorico_usd=D("100.01"))
    assert verificar_teorico(justo) == []
    pasado = _Teorico(teorico_usd=D("100"), descuentos_teorico_usd=D("100.02"))
    assert verificar_teorico(pasado)


# --- el contrato de fallar -------------------------------------------------


def test_exigir_no_hace_nada_si_no_hay_violaciones() -> None:
    exigir([])
    exigir(verificar_vinculacion(_Vinc()))


def test_exigir_levanta_con_todas_las_filas_en_el_mensaje() -> None:
    """Ver varias filas juntas dice si el problema es una fila o el cálculo."""
    malas = (
        _Vinc(vinc_id="V1", monto_aplicado=D("-1")),
        _Vinc(vinc_id="V2", monto_aplicado=D("-2")),
    )
    fallas = [f for v in malas for f in verificar_vinculacion(v)]
    with pytest.raises(InvarianteViolada) as exc:
        exigir(fallas)
    mensaje = str(exc.value)
    assert "2 fila(s)" in mensaje
    assert "V1" in mensaje and "V2" in mensaje
    assert "no se escribieron" in mensaje


def test_el_error_de_postgres_se_traduce_a_castellano() -> None:
    """Para el camino que la validación no cubre: una escritura que llega a la base."""
    crudo = Exception(
        'new row for relation "vinculaciones" violates check constraint '
        '"ck_vinc_tasas_positivas"'
    )
    frase = traducir_error_de_restriccion(crudo)
    assert frase is not None
    assert "ck_vinc_tasas_positivas" in frase
    assert "no signifique nada" in frase


def test_un_error_que_no_es_de_restriccion_devuelve_none() -> None:
    assert traducir_error_de_restriccion(Exception("connection refused")) is None


# --- la duplicación, verificada -------------------------------------------


CASOS_CONTRA_EL_SQL = [
    # (fila, viola segun Python, que clausula SQL le corresponde)
    (_Vinc(monto_aplicado=D("0")), False, "monto_aplicado >= 0"),
    (_Vinc(monto_aplicado=D("-0.01")), True, "monto_aplicado >= 0"),
    (_Vinc(tasa_bcv_aplicada=D("0.01")), False, "tasa_bcv_aplicada > 0"),
    (_Vinc(tasa_bcv_aplicada=D("0")), True, "tasa_bcv_aplicada > 0"),
    (
        _Vinc(moneda_abono="VES", monto_aplicado=D("100"), equiv_usd_bcv=D("100")),
        False,
        "equivalente <= monto_aplicado",
    ),
    (
        _Vinc(moneda_abono="VES", monto_aplicado=D("100"), equiv_usd_bcv=D("100.01")),
        True,
        "equivalente <= monto_aplicado",
    ),
]


@pytest.mark.parametrize("fila,viola,clausula", CASOS_CONTRA_EL_SQL)
def test_la_version_python_coincide_con_la_clausula_sql(fila, viola, clausula) -> None:
    """La duplicación es aceptable si algo falla cuando las dos versiones se separan.

    Los bordes son los que importan: cero, un centavo por debajo, un centavo por
    encima. Un ``>=`` donde el SQL dice ``>`` se ve exactamente acá.
    """
    assert bool(verificar_vinculacion(fila)) is viola, clausula


def test_las_restricciones_de_la_migracion_estan_todas_traducidas() -> None:
    """Si alguien agrega un CHECK y no su frase, el error vuelve a ser ilegible."""
    import importlib.util
    from pathlib import Path

    from cxc.db.invariantes import _POR_QUE

    versiones = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    ruta = next(versiones.glob("*invariantes_de_dinero*.py"))
    spec = importlib.util.spec_from_file_location("migracion_invariantes", ruta)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)

    en_la_migracion = {nombre for _tabla, nombre, _clausula in modulo.RESTRICCIONES}
    sin_traducir = en_la_migracion - set(_POR_QUE)
    assert not sin_traducir, f"restricciones sin frase legible: {sorted(sin_traducir)}"
