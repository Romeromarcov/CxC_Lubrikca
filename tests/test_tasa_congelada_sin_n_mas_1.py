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

    def __init__(self, fecha: date, serie: list[dict] | None = None) -> None:
        self._fecha = fecha
        self._serie = serie or []
        self.lecturas_de_serie = 0

    def get_orden(self, so_id: str):
        return SimpleNamespace(so_id=so_id, fecha=self._fecha)

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


def test_si_se_pasa_la_serie_no_se_lee_de_la_base() -> None:
    """El N+1 que este cambio saca del bucle."""
    repo = _Repo(FECHA_EN_VENTANA)
    tasa, variante = resolver_tasa_bcv_vinculacion(
        repo, "S00001", HORA_PAGO, DEFAULT_USD, serie_rows=_serie("40.00")
    )
    assert repo.lecturas_de_serie == 0, "leyó la serie teniéndola en la mano"
    assert (tasa, variante) == (Decimal("40.00"), "EUR")


def test_sin_serie_se_lee_fresco_como_antes() -> None:
    """El comportamiento viejo se preserva para los cuatro llamadores puntuales.

    Ahí la lectura fresca es lo correcto: se está fijando la tasa con la que una
    vinculación va a quedar congelada, y el caché de cinco minutos podría ocultar
    una tasa recién cargada.
    """
    repo = _Repo(FECHA_EN_VENTANA, _serie("41.00"))
    tasa, variante = resolver_tasa_bcv_vinculacion(repo, "S00001", HORA_PAGO, DEFAULT_USD)
    assert repo.lecturas_de_serie == 1
    assert (tasa, variante) == (Decimal("41.00"), "EUR")


def test_una_orden_fuera_de_la_ventana_no_llega_a_leer_nada() -> None:
    """La guarda de fecha va primero, y por eso el N+1 solo afectaba a 206 filas."""
    repo = _Repo(FECHA_FUERA, _serie("40.00"))
    tasa, variante = resolver_tasa_bcv_vinculacion(repo, "S00001", HORA_PAGO, DEFAULT_USD)
    assert repo.lecturas_de_serie == 0
    assert (tasa, variante) == (DEFAULT_USD, "USD")


def test_sin_tasa_euro_en_la_serie_cae_a_la_usd_y_lo_dice() -> None:
    """No se finge una tasa que no existe: la variante queda 'USD'."""
    repo = _Repo(FECHA_EN_VENTANA)
    tasa, variante = resolver_tasa_bcv_vinculacion(
        repo, "S00001", HORA_PAGO, DEFAULT_USD, serie_rows=_serie("")
    )
    assert (tasa, variante) == (DEFAULT_USD, "USD")


def test_una_serie_vacia_pasada_a_proposito_no_dispara_una_lectura() -> None:
    """``[]`` es «no hay tasas», distinto de ``None`` que es «leelas vos».

    Es la misma distinción que este blindaje persigue en todo lo demás, y acá
    importa: si ``[]`` disparara la lectura, el N+1 volvería por la puerta de
    atrás el día que un ciclo corra sin serie.
    """
    repo = _Repo(FECHA_EN_VENTANA, _serie("40.00"))
    tasa, variante = resolver_tasa_bcv_vinculacion(
        repo, "S00001", HORA_PAGO, DEFAULT_USD, serie_rows=[]
    )
    assert repo.lecturas_de_serie == 0
    assert (tasa, variante) == (DEFAULT_USD, "USD")
