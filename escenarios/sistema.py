"""Lo que dice el sistema: sync, motor y los cuatro reportes.

La otra mitad del banco. ``odoo_qa`` escribe en Odoo; esto corre el sync y
pregunta a los mismos endpoints que ve el usuario, para que un escenario
mida lo que el usuario vería y no una función interna.

Se llama a los endpoints de verdad (por ``TestClient``) y no a los ``_sync``
que hay detrás, por una razón concreta: buena parte de lo que la Fase 1
encontró vive en el ensamblado -- el balance arma 24 partidas y ninguna de
sus 768 líneas se ejercita hoy de punta a punta. Un escenario que llamara al
helper interno se perdería justamente eso.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


def _repo():
    from cxc.db.postgres_repository import PostgresRepository

    return PostgresRepository.from_url(os.environ["DATABASE_URL"])


def sincronizar(desde_cero: bool = False) -> Any:
    """Una corrida del sync. ``desde_cero`` borra el cursor y lee todo.

    El delta es el modo interesante: es el que corre en producción cada 5
    minutos, y es el que NO puede ver una eliminación -- un registro borrado
    no deja ``write_date``. Varios escenarios dependen de esa diferencia.
    """
    import sqlalchemy as sa

    from cxc.config import AppConfig
    from cxc.odoo.client import OdooXmlRpcReader
    from cxc.sync.incremental import IncrementalSync

    if desde_cero:
        url = os.environ["DATABASE_URL"].replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
        with sa.create_engine(url).begin() as con:
            con.execute(sa.text("DELETE FROM app_settings WHERE key = 'last_sync'"))

    repo = _repo()
    reader = OdooXmlRpcReader(AppConfig.from_env().odoo)
    return IncrementalSync(repo, reader).run(datetime.now(), sync_catalogo=True)


def recalcular_teoricos(limite: int | None = None) -> int:
    """Corre el motor sobre las órdenes con teórico pendiente.

    Es lo que llena ``ventas_teoricos``, la tabla que congela el teórico. Los
    escenarios que preguntan "¿el teórico se movió cuando cambió la orden?"
    dependen de correr esto DESPUÉS del cambio.
    """
    from cxc.config import AppConfig
    from cxc.engine.runner import EngineRunner
    from cxc.odoo.client import _connect
    from cxc.odoo.price import OdooPriceResolver
    from cxc.web.app import (
        build_fallback_ficha_config,
        get_valid_pricelists_usd_and_ves,
    )

    config = AppConfig.from_env()
    repo = _repo()
    ejecutar = _connect(config.odoo)
    usd, ves = get_valid_pricelists_usd_and_ves(repo)
    ids_usd = [int(x) for x in usd if str(x).isdigit()]
    ids_ves = [int(x) for x in ves if str(x).isdigit()]
    resolver = OdooPriceResolver(
        ejecutar,
        {"USD": ids_usd[0] if ids_usd else 11, "BCV": ids_ves[0] if ids_ves else 10},
        [*ids_usd, *ids_ves],
        build_fallback_ficha_config(repo),
    )
    return int(EngineRunner(repo, resolver, config.engine).run_teoricos_pendientes(date.today()))


def limpiar_caches() -> None:
    """Vacía los cachés de los REPORTES, y solo esos.

    Sin esto un escenario mide lo que dijo el anterior.

    Lo que NO se toca a propósito es el caché compartido de precios y volumen
    de ``odoo/price.py``. Es catálogo: no cambia entre escenarios, y tirarlo
    obliga a cada corrida a releer los precios de Odoo producto por producto
    -- que es la razón de que el reporte de saldos tarde ~10 minutos con los
    cachés fríos. Un escenario que SÍ cambia un precio lo tira él mismo (ver
    ``olvidar_precios``).
    """
    from cxc.web import app as modulo

    for nombre in (
        "_REPORTE_SALDOS_CACHE",
        "_VENTAS_CACHE",
        "_COBRANZA_CACHE",
        "_REPORTE_DIARIO_CACHE",
    ):
        cache = getattr(modulo, nombre, None)
        if isinstance(cache, dict):
            cache["data"] = None
            cache["timestamp"] = 0.0


def olvidar_precios() -> None:
    """Tira el caché de precios de Odoo.

    Solo lo llaman los escenarios de catálogo, que son los únicos que cambian
    un precio: para el resto, releer el catálogo entero es puro costo.
    """
    from cxc.odoo import price as modulo_precio

    for nombre in ("_SHARED_PRICE_CACHE", "_SHARED_VOLUMEN_CACHE"):
        cache = getattr(modulo_precio, nombre, None)
        if isinstance(cache, dict):
            cache.clear()


@dataclass
class Sistema:
    """Los cuatro reportes, más el sync y el motor, en un solo objeto."""

    cliente: Any  # TestClient

    # --- lo que corre ---------------------------------------------------

    def sync(self, desde_cero: bool = False) -> Any:
        resultado = sincronizar(desde_cero=desde_cero)
        limpiar_caches()
        return resultado

    def espejo_todo(self, tablas: tuple[str, ...]) -> dict[str, int]:
        """Cuántas filas hay en cada tabla. La huella que compara la Fase 4."""
        return {t: len(self.espejo(t)) for t in tablas}

    def motor(self) -> int:
        procesadas = recalcular_teoricos()
        limpiar_caches()
        return procesadas

    def sync_y_motor(self) -> None:
        """El ciclo completo, que es como lo ve el usuario."""
        self.sync()
        self.motor()

    # --- lo que dice ----------------------------------------------------

    def _json(self, ruta: str) -> dict[str, Any]:
        respuesta = self.cliente.get(ruta)
        assert respuesta.status_code == 200, (
            f"{ruta} respondió {respuesta.status_code}: {respuesta.text[:400]}"
        )
        return dict(respuesta.json())

    def saldos(self) -> dict[str, Any]:
        return self._json("/api/reporte-saldos")

    def ventas(self) -> dict[str, Any]:
        return self._json("/api/ventas")

    def bandeja(self) -> dict[str, Any]:
        return self._json("/api/bandeja")

    def balance(self) -> dict[str, Any]:
        return self._json("/api/auditoria/balance-comprobacion")

    def auditoria(self) -> dict[str, Any]:
        return self._json("/api/auditoria")

    # --- atajos de lectura ----------------------------------------------

    def orden_en_saldos(self, nombre: str) -> dict[str, Any] | None:
        for item in self.saldos().get("items") or []:
            if str(item.get("so_id")) == nombre:
                return dict(item)
        return None

    def orden_en_ventas(self, nombre: str) -> dict[str, Any] | None:
        for item in self.ventas().get("items") or []:
            if str(item.get("so_id")) == nombre:
                return dict(item)
        return None

    def teorico(self, nombre: str) -> dict[str, Any] | None:
        """La fila congelada de ``ventas_teoricos``, leída de la base."""
        import sqlalchemy as sa

        url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
        with sa.create_engine(url).connect() as con:
            fila = (
                con.execute(
                    sa.text("SELECT * FROM ventas_teoricos WHERE so_id = :so"), {"so": nombre}
                )
                .mappings()
                .first()
            )
        return dict(fila) if fila else None

    def espejo(self, tabla: str, donde: str = "", **params: Any) -> list[dict[str, Any]]:
        """Filas del espejo, para preguntarle a la base y no al reporte."""
        import sqlalchemy as sa

        url = os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1)
        sql = f'SELECT * FROM "{tabla}"' + (f" WHERE {donde}" if donde else "")
        with sa.create_engine(url).connect() as con:
            return [dict(f) for f in con.execute(sa.text(sql), params).mappings().all()]

    def partidas_que_no_cuadran(self) -> list[dict[str, Any]]:
        balance = self.balance()
        if not balance.get("evaluable", True):
            return []
        return [p for p in balance.get("partidas") or [] if not p.get("cuadra")]
