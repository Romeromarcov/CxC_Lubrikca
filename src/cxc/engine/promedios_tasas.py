"""Los promedios de la tasa Binance, y las dos ventanas que se solapan.

Decimosexta pieza de la Fase 2.4, del mismo barrido que las tres anteriores:
funciones de ``app.py`` que hablan de dinero y que ninguna prueba nombra.
``_get_tasas_promedios_sync`` era una.

Calcula tres promedios de la tasa Binance --manana, tarde y diario-- y el
diferencial contra la BCV. Alimenta la pantalla de tasas y las columnas
``tasa_binance_*`` de la serie.

**Hallazgo 1: las ventanas se solapan, y hoy mismo muerde.**

    manana preferida    6 <= hora <= 9
    tarde preferida     10 <= hora <= 13
    manana de respaldo  hora < 12       <-- incluye las 10 y las 11
    tarde de respaldo   hora >= 12

Cuando NO hay captura entre las 6 y las 9, la manana cae a su respaldo, que llega
hasta las 11 -- y esas horas ya estan en la tarde preferida. Resultado: **la misma
captura entra como promedio de manana Y de tarde**.

Medido el 11-sep-2026 sobre la serie de la copia: de los dos dias con capturas, el
de hoy tiene sus tres capturas a las 10 y ninguna entre las 6 y las 9. Los dos
promedios que la pantalla muestra como distintos son el mismo numero.

**Hallazgo 2: sin capturas de hoy usa las ultimas 24 filas, de cualquier fecha.**

``rates_today if rates_today else rows[-24:]``, y nada dice que el "promedio de hoy"
pueda ser el de la semana pasada. Es la misma forma que este plan viene corrigiendo
en otros lugares: un numero que se presenta como de una fecha y sale de otra.

**Esto no corrige ninguno de los dos.** Cambiar las ventanas o el respaldo mueve las
cifras que la pantalla muestra y las que la serie guarda, asi que es decision del
usuario. Lo que hay aca son las dos lecturas --con ventanas solapadas y con ventanas
disjuntas-- y un diagnostico que dice si en este conjunto difieren.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

# Las ventanas preferidas, tal como estan en el cuerpo original.
MANANA_PREFERIDA = range(6, 10)  # 6, 7, 8, 9
TARDE_PREFERIDA = range(10, 14)  # 10, 11, 12, 13

# El corte de los respaldos. Es el que crea el solapamiento: la manana de respaldo
# llega hasta las 11 y la tarde preferida arranca en las 10.
HORA_DE_CORTE = 12

# Cuantas filas se miran cuando no hay ninguna de hoy.
FILAS_DE_RESPALDO = 24


def _dec(valor: Any) -> Decimal:
    if valor is None or valor is False or valor == "":
        return Decimal("0")
    try:
        return Decimal(str(valor))
    except (TypeError, ValueError, InvalidOperation):
        return Decimal("0")


def hora_de(fila: dict[str, Any]) -> int | None:
    """La hora de la captura, o ``None`` si el sello no se puede leer.

    El sello viene en dos formas segun de donde salga la fila (``...T10:00:00`` o
    ``... 10:00:00``), y el cuerpo original parte por las dos. ``None`` en vez de
    cero: una fila ilegible no es una fila de medianoche, y contarla como tal la
    metria en el promedio de la manana.
    """
    ts = str(fila.get("timestamp", "") or "")
    parte = ts.split("T")[-1].split(" ")[-1]
    try:
        return int(parte.split(":")[0])
    except (ValueError, IndexError):
        return None


def _capturas(filas: list[dict[str, Any]]) -> list[tuple[int, Decimal]]:
    """``(hora, tasa)`` de las capturas utilizables, en orden.

    Se descartan las de tasa cero o negativa: una captura fallida guarda cero, y
    promediarla arrastraria el promedio hacia abajo sin decirlo. Y las de sello
    ilegible, por la razon del docstring de ``hora_de``.
    """
    salida: list[tuple[int, Decimal]] = []
    for f in filas:
        tasa = _dec(f.get("tasa_binance"))
        hora = hora_de(f)
        if tasa > Decimal("0") and hora is not None:
            salida.append((hora, tasa))
    return salida


@dataclass(frozen=True)
class Promedios:
    """Los tres promedios, con el denominador de cada uno."""

    manana: Decimal | None
    tarde: Decimal | None
    diario: Decimal | None
    capturas_manana: int
    capturas_tarde: int
    capturas_diario: int
    # Las horas que quedaron en las DOS listas. Vacio = las ventanas no se solapan
    # en este conjunto. Ver el hallazgo 1 del docstring del modulo.
    horas_compartidas: tuple[int, ...]

    @property
    def ventanas_solapadas(self) -> bool:
        return bool(self.horas_compartidas)


def promediar(filas: list[dict[str, Any]], *, ventanas_disjuntas: bool = False) -> Promedios:
    """Los tres promedios de ``tasa_binance`` sobre ``filas``.

    ``ventanas_disjuntas=False`` reproduce el comportamiento original, solapamiento
    incluido. Con ``True``, la manana de respaldo corta donde arranca la tarde
    preferida en vez de en las 12, asi que ninguna captura entra en las dos.
    """
    caps = _capturas(filas)
    # El corte de la manana de respaldo: 12 en el original, 10 si se piden ventanas
    # disjuntas (donde arranca la tarde preferida).
    corte = TARDE_PREFERIDA.start if ventanas_disjuntas else HORA_DE_CORTE

    manana_pref = [(h, t) for h, t in caps if h in MANANA_PREFERIDA]
    tarde_pref = [(h, t) for h, t in caps if h in TARDE_PREFERIDA]
    manana_resp = [(h, t) for h, t in caps if h < corte]
    tarde_resp = [(h, t) for h, t in caps if h >= HORA_DE_CORTE]

    # La eleccion del original: la preferida si tiene algo, si no el respaldo.
    elegida_manana = manana_pref or manana_resp
    elegida_tarde = tarde_pref or tarde_resp

    def _prom(xs: list[tuple[int, Decimal]]) -> Decimal | None:
        return (sum((t for _, t in xs), Decimal("0")) / Decimal(len(xs))) if xs else None

    compartidas = sorted({h for h, _ in elegida_manana} & {h for h, _ in elegida_tarde})
    return Promedios(
        manana=_prom(elegida_manana),
        tarde=_prom(elegida_tarde),
        diario=_prom(caps),
        capturas_manana=len(elegida_manana),
        capturas_tarde=len(elegida_tarde),
        capturas_diario=len(caps),
        horas_compartidas=tuple(compartidas),
    )


def diferencial_pct(promedio_binance: Decimal | None, bcv: Decimal) -> Decimal:
    """El diferencial BCV-Binance en porcentaje.

    **La base es el promedio de Binance, no la BCV**, y el nombre del campo
    (``diferencial_bcv_binance_pct``) no lo dice. Queda escrito porque un
    diferencial cambia segun cual sea el denominador: con BCV 700 y Binance 800,
    sobre Binance es 12,5 % y sobre BCV es 14,3 %.

    Sin promedio o sin BCV devuelve cero, que es lo que hacia el cuerpo original.
    """
    if not promedio_binance or promedio_binance <= Decimal("0") or bcv <= Decimal("0"):
        return Decimal("0")
    return (promedio_binance - bcv) / promedio_binance * Decimal("100")


@dataclass(frozen=True)
class DiagnosticoPromedios:
    """Las dos lecturas de los mismos datos, y si difieren."""

    con_solapamiento: Promedios
    sin_solapamiento: Promedios
    nota: str

    @property
    def difieren(self) -> bool:
        return (
            self.con_solapamiento.manana != self.sin_solapamiento.manana
            or self.con_solapamiento.tarde != self.sin_solapamiento.tarde
        )


def diagnostico_de_promedios(filas: list[dict[str, Any]]) -> DiagnosticoPromedios:
    """Compara las ventanas solapadas contra las disjuntas sobre las mismas filas."""
    con = promediar(filas, ventanas_disjuntas=False)
    sin = promediar(filas, ventanas_disjuntas=True)
    if not _capturas(filas):
        nota = (
            "Sin capturas utilizables: no hay promedio que calcular, y eso NO es un "
            "promedio de cero."
        )
    elif con.ventanas_solapadas:
        nota = (
            f"Las ventanas se SOLAPAN en la(s) hora(s) {list(con.horas_compartidas)}: "
            f"no hay captura entre las {MANANA_PREFERIDA.start} y las "
            f"{MANANA_PREFERIDA.stop - 1}, asi que la manana cae a su respaldo "
            f"(hora < {HORA_DE_CORTE}) y comparte capturas con la tarde preferida. "
            f"Manana {con.manana} y tarde {con.tarde} salen de las mismas capturas."
        )
    else:
        nota = (
            f"Las ventanas no se solapan en este conjunto. Manana {con.manana} sobre "
            f"{con.capturas_manana} captura(s), tarde {con.tarde} sobre "
            f"{con.capturas_tarde}."
        )
    return DiagnosticoPromedios(con_solapamiento=con, sin_solapamiento=sin, nota=nota)
