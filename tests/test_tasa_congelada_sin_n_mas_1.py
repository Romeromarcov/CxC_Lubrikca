"""La serie se lee una vez por ciclo, no una vez por fila (Fase 6).

El plan listaba «`SerieTasas` se lee sin caché — quedan 24 lecturas directas
repartidas por `app.py`. Las que pasan las filas por parámetro están bien; las
otras deberían ir por `tasas_vigentes()`».

Clasificados los 22 sitios (los otros dos son la definición): **21 son el patrón
que el plan aprueba** — leen una vez y pasan las filas hacia abajo, o son el camino
de escritura que necesita las filas crudas. Uno no lo era:
`resolver_tasa_bcv_vinculacion` armaba su propio objeto `Tasas` leyendo la serie
completa, y **dos de sus seis llamadores lo llaman dentro de un bucle sobre pagos**.

Medido en la copia de prueba: 206 vinculaciones caen en la ventana histórica, que
es la única rama que llega a esa lectura. En producción son 919 filas de serie por
cada una, por ciclo.

Y el argumento decisivo no fue el rendimiento: esos dos llamadores **ya** tienen la
serie leída antes del bucle y se la pasan a `get_rate_for_datetime` para la tasa del
día. La tasa USD salía de un snapshot y la EUR de una lectura fresca, **y las dos se
congelan juntas en la misma vinculación**. Pasar la misma serie hace el par
consistente.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from cxc.web.app import resolver_tasa_bcv_vinculacion

# Dentro de la ventana de la Lista Histórica (20-feb a 12-mar-2026 inclusive).
FECHA_EN_VENTANA = date(2026, 3, 1)
FECHA_FUERA = date(2026, 6, 1)
HORA_PAGO = datetime(2026, 3, 1, 10, 0, 0)
DEFAULT_USD = Decimal("36.5")


def _serie(tasa_eur: str) -> list[dict]:
    return [
        {
            "timestamp": "2026-03-01T22:00:00",
            "tasa_bcv": "36.50",
            "tasa_binance": "38.00",
            "tasa_bcv_euro": tasa_eur,
            "fuente": "test",
            "es_heredada": "FALSE",
            "capturada_ok": "TRUE",
        }
    ]


class _Repo:
    """Repo mínimo que cuenta cuántas veces se le pide la serie."""

    def __init__(self, fecha: date, serie: list[dict] | None = None, lista: str = "12") -> None:
        self._fecha = fecha
        self._serie = serie or []
        self.lecturas_de_serie = 0
        # La lista con la que nació la orden. Importa desde el 11-sep-2026: al
        # unificar las dos definiciones de "orden histórica", una orden SIN lista
        # es histórica sin importar la fecha. El default es una lista cualquiera
        # que no está entre las USD configuradas, para que los tests de la ventana
        # sigan midiendo la ventana y no ese caso.
        self._lista = lista

    def get_orden(self, so_id: str):
        return SimpleNamespace(so_id=so_id, fecha=self._fecha, lista_precios=self._lista)

    def all_serie_tasas(self):
        self.lecturas_de_serie += 1
        # ``_all_serie_tasas_rows`` pasa esto por ``serde.serie_to_row``, así que
        # acá van objetos, no filas. Se construyen desde el dict de arriba.
        from cxc.sheets import serde

        return [serde.serie_from_row(r) for r in self._serie]

    def all_tasas_historicas_auditoria(self):
        return []

    def get_config(self, clave, default=None):
        # El toggle de la lista histórica, encendido.
        return "true" if "histor" in clave else default


# Desde el 12-sep-2026 (quiz, pregunta 5) la vía euro no toca montos reales, y la
# tasa que se congela en una vinculación ES un monto real: `resolver_tasa_bcv_vinculacion`
# devuelve la BCV-USD del día para toda orden, sin leer la serie. Lo que este archivo
# fijaba sobre el N+1 sigue valiendo por otra razón: ya no hay lectura que repetir.


def _casos():
    return [
        ("en la ventana", _Repo(FECHA_EN_VENTANA, _serie("41.00"))),
        ("fuera de la ventana", _Repo(FECHA_FUERA, _serie("41.00"))),
        ("sin lista", _Repo(FECHA_FUERA, _serie("41.00"), lista="")),
    ]


def test_toda_orden_congela_la_bcv_usd_y_no_lee_la_serie() -> None:
    for nombre, repo in _casos():
        tasa, variante = resolver_tasa_bcv_vinculacion(repo, "S00001", HORA_PAGO, DEFAULT_USD)
        assert (tasa, variante) == (DEFAULT_USD, "USD"), nombre
        assert repo.lecturas_de_serie == 0, f"{nombre}: leyó la serie sin necesitarla"


def test_con_la_serie_en_la_mano_tampoco_cambia() -> None:
    repo = _Repo(FECHA_EN_VENTANA)
    tasa, variante = resolver_tasa_bcv_vinculacion(
        repo, "S00001", HORA_PAGO, DEFAULT_USD, serie_rows=_serie("40.00")
    )
    assert (tasa, variante) == (DEFAULT_USD, "USD")
    assert repo.lecturas_de_serie == 0
