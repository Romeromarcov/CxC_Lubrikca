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


# --- la novena: un pago no puede aplicar más de lo que vale ------------------


class _V:
    """Una vinculación mínima, que es todo lo que la invariante mira."""

    def __init__(self, vinc_id, pago_id, monto):
        self.vinc_id = vinc_id
        self.pago_id = pago_id
        self.monto_aplicado = monto


def test_una_vinculacion_que_cabe_en_el_pago_pasa() -> None:
    from cxc.db.invariantes import verificar_no_sobreaplica

    assert verificar_no_sobreaplica(_V("V2", "P1", "40"), [_V("V1", "P1", "60")], "100") == []


def test_una_vinculacion_que_excede_el_pago_se_rechaza() -> None:
    """El caso del pago 200: vale 134,00 y tiene aplicados 715,04."""
    from cxc.db.invariantes import verificar_no_sobreaplica

    fallas = verificar_no_sobreaplica(_V("V2", "200", "581.04"), [_V("V1", "200", "134")], "134")
    assert len(fallas) == 1
    assert fallas[0].restriccion == "ck_vinc_no_sobreaplica_el_pago"
    assert "134" in fallas[0].detalle and "715.04" in fallas[0].detalle


def test_el_borde_exacto_cabe() -> None:
    from cxc.db.invariantes import verificar_no_sobreaplica

    assert verificar_no_sobreaplica(_V("V2", "P1", "50"), [_V("V1", "P1", "50")], "100") == []


def test_el_redondeo_de_centavos_no_rechaza() -> None:
    """Misma tolerancia que el detector, para que no puedan discrepar."""
    from cxc.db.invariantes import TOLERANCIA_SOBREAPLICACION, verificar_no_sobreaplica

    assert (
        verificar_no_sobreaplica(
            _V("V2", "P1", str(TOLERANCIA_SOBREAPLICACION)), [_V("V1", "P1", "100")], "100"
        )
        == []
    )


def test_reescribir_la_misma_vinculacion_no_la_cuenta_dos_veces() -> None:
    """Un ``update`` trae la fila en las dos listas.

    Sin esta guarda, guardar una vinculación sin cambiarla se rechazaría a sí
    misma — y el sync reescribe filas todo el tiempo.
    """
    from cxc.db.invariantes import verificar_no_sobreaplica

    assert verificar_no_sobreaplica(_V("V1", "P1", "100"), [_V("V1", "P1", "100")], "100") == []


def test_reescribir_igual_una_fila_ya_sobreaplicada_pasa() -> None:
    """El caso del pago 200 tal como está en el espejo: UNA vinculación de 318,27
    sobre un pago de 134,00. El sync la reescribe en cada ciclo sin cambiarla.

    La primera versión de la invariante (11-sep a la mañana) rechazaba esto
    --cero hermanas, 318,27 «de esta»-- y como el motor escribe todo en un
    lote, tumbaba la escritura entera del ciclo. Lo encontró el banco de
    escenarios. La regla exacta es «el exceso no crece», y una reescritura
    idéntica no lo hace crecer.
    """
    from cxc.db.invariantes import verificar_no_sobreaplica

    ya = [_V("V1", "200", "318.27")]
    assert verificar_no_sobreaplica(_V("V1", "200", "318.27"), ya, "134") == []


def test_reescribir_igual_con_hermanas_que_no_exceden_solas_pasa() -> None:
    """El pago 40: 117,57 en hermanas + la de 90,89 reescrita igual, sobre 130."""
    from cxc.db.invariantes import verificar_no_sobreaplica

    ya = [_V("V1", "40", "117.57"), _V("V2", "40", "90.89")]
    assert verificar_no_sobreaplica(_V("V2", "40", "90.89"), ya, "130") == []


def test_bajar_una_fila_ya_sobreaplicada_pasa_aunque_siga_excedida() -> None:
    """Achicar el exceso nunca se rechaza: es la dirección de la corrección."""
    from cxc.db.invariantes import verificar_no_sobreaplica

    ya = [_V("V1", "200", "318.27")]
    assert verificar_no_sobreaplica(_V("V1", "200", "300"), ya, "134") == []


def test_agrandar_una_fila_ya_sobreaplicada_se_rechaza() -> None:
    """Lo que Odoo reporta para un parcial corrompido puede crecer con cada
    edición de fecha; la base no lo sigue hacia arriba."""
    from cxc.db.invariantes import verificar_no_sobreaplica

    fallas = verificar_no_sobreaplica(_V("V1", "200", "715.04"), [_V("V1", "200", "318.27")], "134")
    assert len(fallas) == 1
    assert "antes sumaban 318.27" in fallas[0].detalle


def test_una_fila_NUEVA_sobre_un_pago_ya_sobreaplicado_se_rechaza() -> None:
    """Cambio respecto del 11-sep a la mañana, y a propósito.

    La versión anterior dejaba pasar una fila nueva si las hermanas ya
    excedían solas, con el argumento de «no tocar lo existente». Pero una fila
    nueva de 10 sobre un pago que ya tiene 715,04 aplicados sobre 134,00 deja
    al pago con 725,04: el exceso creció, y eso es exactamente acreditarle al
    cliente plata que no entró. Lo existente no se toca; lo nuevo no entra.
    """
    from cxc.db.invariantes import verificar_no_sobreaplica

    ya = [_V("V1", "200", "715.04")]
    fallas = verificar_no_sobreaplica(_V("V2", "200", "10"), ya, "134")
    assert len(fallas) == 1
    assert "725.04" in fallas[0].detalle


def test_sin_monto_de_pago_confiable_no_se_afirma_nada() -> None:
    """«Sin datos no es cero», que es la regla de toda esta fase.

    Rechazar cuando falta el tope convertiría un dato ausente en un error.
    """
    from cxc.db.invariantes import verificar_no_sobreaplica

    for tope in (None, "", "0", "-5", "ilegible"):
        assert verificar_no_sobreaplica(_V("V1", "P1", "999"), [], tope) == []


def test_el_mensaje_dice_el_pago_y_los_dos_sumandos() -> None:
    """Para poder ir a mirarlo sin volver a calcular.

    Es la misma razón por la que las otras ocho invariantes traen sus valores:
    el error tiene que decir qué fila y con qué números, no sólo que algo falló.
    """
    from cxc.db.invariantes import verificar_no_sobreaplica

    f = verificar_no_sobreaplica(_V("V2", "P77", "30"), [_V("V1", "P77", "80")], "100")[0]
    assert "pago P77" in f.fila
    assert "80" in f.detalle and "30" in f.detalle and "100" in f.detalle
