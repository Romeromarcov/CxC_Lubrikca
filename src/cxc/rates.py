"""Una sola respuesta a "¿cuál era la tasa ese día?".

Pedido del usuario (septiembre 2026): "lo mejor seria usar las tasas
correctas, unificar todo el tema de tasas para evitar en el futuro errores
y duplicidad, y mejora el mantenimiento y la escalabilidad del codigo".

Venía de una racha de bugs que eran todos el mismo bug con distinta ropa:

  · el Reporte de Saldos se armaba su propio mapa con SOLO ``SerieTasas``
    (40 días) y caía a la tasa de HOY para todo lo anterior -- 4.289,57 de
    descuadre contra Odoo;
  · la vía de pago en euros no se activaba nunca porque buscaba el euro
    solo en ``SerieTasas``, que arranca meses después de la ventana
    histórica;
  · el scraper guardaba bajo la fecha de hoy la tasa que el BCV publica
    con fecha valor de MAÑANA, corriendo 24 días de la serie.

El patrón común: cada consumidor elegía su fuente y su fallback por su
cuenta. Este módulo centraliza esa decisión para que haya una sola.

LAS DOS FUENTES, Y POR QUÉ EL ORDEN ES ESTE

``TasasHistoricasAuditoria`` es la oficial: una fila por día, indexada por
FECHA VALOR, alineada con las series que publica el BCV (verificado en
septiembre 2026: 147 de 147 días coinciden en USD y en EUR). Manda.

``SerieTasas`` la escribe el scraper cada hora. Es útil para ver el
intradía y para los días que el histórico todavía no tiene, pero NO es
autoritativa: el BCV calcula la tasa al cierre y la publica con fecha
valor del día siguiente, así que una captura de la noche ya trae la tasa
de mañana. Por eso va segunda y solo cuenta si es del mismo día.

Si ninguna fuente tiene el día se devuelve ``None``. Nunca un número
inventado: "sin dato" no es "cero" ni "la última que haya", y esa
confusión ya costó cuatro bugs distintos en este proyecto.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

# El euro nunca vale menos que 1,05 dólares BCV en la serie real -- el BCV
# devalúa más rápido que el euro en términos relativos. Una fila por debajo
# de ese piso es una tasa estancada, no una tasa real: pasó de verdad
# (agosto 2026), con ``res.currency.rate`` congelado casi un mes mientras
# el USD sí se movía, y el scraper repitiéndolo hora tras hora sin señal de
# error.
PISO_RATIO_EUR_USD = Decimal("1.05")

# Cuántos días hacia atrás se acepta arrastrar una tasa publicada. El BCV
# publica una tasa que rige hasta la siguiente -- los datos lo confirman:
# de los 60 fines de semana cargados, los 60 repiten la del viernes. Una
# semana cubre un feriado largo; más allá, lo que falta es la carga y no el
# feriado, y ahí es mejor decir "no sé" que arrastrar una tasa de otro mes.
DIAS_MAXIMOS_DE_ARRASTRE = 7

# Banda de plausibilidad de Binance contra el BCV del mismo día. Binance
# cotiza por encima del oficial, con un diferencial que en la serie real se
# mueve entre unos pocos puntos y ~50 %; nunca es la mitad del oficial ni
# el triple. La banda es deliberadamente ancha porque no está para detectar
# un diferencial raro sino un dato roto.
#
# Existe por un bug real (pago Odoo 29, marzo 2026): la hoja usaba locale
# es_ES y gspread "numericizó" 451,5072 quitando la coma como si fuera
# separador de miles, guardando 4515072. Un Binance ~10.000 veces la tasa
# BCV del mismo día producía montos de un centavo. Antes eso quedaba tapado
# porque se prefería SerieTasas; ahora que manda el histórico, el dato malo
# se rechaza donde sea que aparezca, que es donde corresponde.
BANDA_BINANCE_SOBRE_BCV = (Decimal("0.5"), Decimal("3"))


def _dec(valor: Any) -> Decimal:
    if valor is None or valor == "":
        return Decimal("0")
    try:
        return Decimal(str(valor).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _dia(valor: Any) -> str:
    return str(valor or "")[:10]


class Tasas:
    """Las tasas de un período, ya indexadas, listas para preguntar.

    Se construye una vez por request con las filas que el llamador ya
    tiene a mano; no consulta la base por su cuenta, justamente para que
    nadie la use dentro de un bucle sin darse cuenta (ese error ya se
    cometió: una lectura de la serie por cada abono).
    """

    __slots__ = ("_hist", "_serie_por_dia")

    def __init__(
        self,
        historicas: Sequence[dict[str, Any]] = (),
        serie: Sequence[dict[str, Any]] = (),
    ) -> None:
        self._hist: dict[str, dict[str, Decimal]] = {}
        for fila in historicas:
            f = _dia(fila.get("fecha"))
            if f:
                self._hist[f] = {
                    "usd": _dec(fila.get("tasa_bcv_usd")),
                    "eur": _dec(fila.get("tasa_bcv_euro")),
                    "binance": _dec(fila.get("tasa_binance_promedio_diario")),
                }
        # De SerieTasas se guarda la PRIMERA captura de cada día: es la que
        # estaba vigente al abrir, o sea la que el BCV publicó en el cierre
        # anterior y la que rige esa jornada. La última captura ya trae la
        # tasa de mañana -- ese fue el bug del scraper.
        primeras: dict[str, tuple[str, dict[str, Any]]] = {}
        for fila in serie:
            ts = str(fila.get("timestamp") or "")
            f = ts[:10]
            if not f or _dec(fila.get("tasa_bcv")) <= 0:
                continue
            previa = primeras.get(f)
            if previa is None or ts < previa[0]:
                primeras[f] = (ts, fila)
        self._serie_por_dia: dict[str, dict[str, Decimal]] = {
            f: {
                "usd": _dec(fila.get("tasa_bcv")),
                "eur": _dec(fila.get("tasa_bcv_euro")),
                "binance": _dec(fila.get("tasa_binance")),
            }
            for f, (_ts, fila) in primeras.items()
        }

    # --- lo que se pregunta -------------------------------------------

    def bcv_usd(self, fecha: date | datetime | str, *, arrastrar: bool = True) -> Decimal | None:
        """Tasa BCV-USD que regía ese día, o None.

        Con ``arrastrar=False`` solo responde por el día exacto. Existe
        para los llamadores viejos que dependen de esa semántica; el
        default es el arrastre porque es como rige la tasa de verdad.
        """
        return self._resolver(fecha, "usd", arrastrar=arrastrar)

    def bcv_eur(self, fecha: date | datetime | str, *, arrastrar: bool = True) -> Decimal | None:
        """Tasa BCV-EUR que regía ese día, o None.

        Aplica el piso de plausibilidad contra el USD del mismo día: una
        tasa euro por debajo de ``PISO_RATIO_EUR_USD`` está estancada.
        """
        return self._resolver(fecha, "eur", arrastrar=arrastrar)

    def binance(self, fecha: date | datetime | str) -> Decimal | None:
        """Promedio Binance de ese día, o None.

        No se arrastra de días anteriores: Binance se mueve todo el día y
        el promedio de ayer no describe hoy. El BCV sí se arrastra porque
        su tasa RIGE hasta que publica la siguiente; son cosas distintas y
        por eso no comparten el fallback.
        """
        f = self._normalizar(fecha)
        if not f:
            return None
        # El BCV del día sirve de vara para descartar un Binance roto. Se
        # resuelve una vez y contra ese se miden las dos fuentes: la fila
        # corrupta del caso real ni siquiera traía USD propio, así que
        # compararla solo contra su misma fila la dejaba pasar.
        referencia = self._resolver(f, "usd") or Decimal("0")
        bajo, alto = BANDA_BINANCE_SOBRE_BCV
        for fuente in (self._hist, self._serie_por_dia):
            fila = fuente.get(f)
            if not fila:
                continue
            v = fila.get("binance", Decimal("0"))
            if v <= 0:
                continue
            vara = fila.get("usd", Decimal("0")) or referencia
            if vara > 0 and not (bajo <= v / vara <= alto):
                continue
            return v
        return None

    def hay_dato(self, fecha: date | datetime | str) -> bool:
        f = self._normalizar(fecha)
        return bool(f) and (f in self._hist or f in self._serie_por_dia)

    # --- la política, en un solo lugar --------------------------------

    def _resolver(
        self, fecha: date | datetime | str, clave: str, *, arrastrar: bool = True
    ) -> Decimal | None:
        f = self._normalizar(fecha)
        if not f:
            return None
        # 1) el histórico oficial del día exacto
        v = self._del_dia(self._hist, f, clave)
        if v is not None:
            return v
        # 2) la serie horaria, pero solo del MISMO día -- una captura de
        #    otro día no describe este, y dejarla ganar en silencio ya
        #    causó un bug real (una N/C de marzo resuelta con una tasa de
        #    agosto).
        v = self._del_dia(self._serie_por_dia, f, clave)
        if v is not None:
            return v
        if not arrastrar:
            return None
        # 3) la última publicada antes, que es como rige la tasa del BCV
        base = date.fromisoformat(f)
        for atras in range(1, DIAS_MAXIMOS_DE_ARRASTRE + 1):
            previo = (base - timedelta(days=atras)).isoformat()
            v = self._del_dia(self._hist, previo, clave)
            if v is None:
                v = self._del_dia(self._serie_por_dia, previo, clave)
            if v is not None:
                return v
        return None

    def _del_dia(
        self, fuente: dict[str, dict[str, Decimal]], f: str, clave: str
    ) -> Decimal | None:
        fila = fuente.get(f)
        if not fila:
            return None
        v = fila.get(clave, Decimal("0"))
        if v <= 0:
            return None
        if clave == "eur":
            usd = fila.get("usd", Decimal("0"))
            if usd > 0 and v / usd < PISO_RATIO_EUR_USD:
                return None
        return v

    @staticmethod
    def _normalizar(fecha: date | datetime | str) -> str:
        if isinstance(fecha, datetime):
            return fecha.date().isoformat()
        if isinstance(fecha, date):
            return fecha.isoformat()
        f = _dia(fecha)
        try:
            date.fromisoformat(f)
        except (TypeError, ValueError):
            return ""
        return f
