"""Siembra histórica completa de Odoo → BD CxC preservando correlativos.

Trae el historial completo (``since=None``) desde Odoo hacia las tablas-espejo locales de CxC
sin tocar ninguna tabla de trabajo humano (``vinculaciones``, ``serie_tasas``,
``documentos_*``, ``descuentos_*``) y registra los correlativos máximos existentes de Odoo y
CxC en ``correlativos_sistema`` + bitácora en ``siembra_bitacora``.

Se puede ejecutar:
- Automáticamente una sola vez al arrancar cuando ``siembra_bitacora`` aún no tiene una corrida
  exitosa (``SIEMBRA_HISTORICA_AUTO=1``, default).
- Manualmente desde ``⚙️ Configuración → 🔄 Motor`` (``POST /api/admin/siembra-odoo``).
- Por CLI: ``python -m cxc.sync.siembra_odoo [--solo-correlativos]``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import re
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from ..config import AppConfig
from ..db.schema import (
    bandeja_facturacion,
    catalogo,
    clientes,
    compras_gastos_odoo,
    correlativos_sistema,
    documentos_comerciales,
    documentos_operativos,
    entregas,
    facturas,
    lineas_entrega,
    lineas_factura,
    lineas_orden,
    lotes_caja_chica,
    ordenes_venta,
    pagos,
    pagos_proveedores_odoo,
    siembra_bitacora,
)
from ..odoo.client import OdooReader, OdooXmlRpcReader
from ..repositories import Repository

logger = logging.getLogger("cxc.sync.siembra")

KEY_ULTIMA_SIEMBRA_JSON = "ultima_siembra_odoo_json"
KEY_CORRELATIVOS_JSON = "correlativos_sistema_json"

_RE_CORRELATIVO = re.compile(r"^(.*?)(\d+)$")


def extraer_correlativo_numerico(texto: str | None) -> tuple[str, int] | None:
    """Separa un número/código en ``(prefijo, entero_correlativo)``.

    Ejemplos: ``S00945`` → ``("S", 945)``, ``INV/2026/00123`` → ``("INV/2026/", 123)``,
    ``COT-00008`` → ``("COT-", 8)``, ``REC-20261119-003`` → ``("REC-20261119-", 3)``.
    """
    limpio = str(texto or "").strip()
    if not limpio:
        return None
    m = _RE_CORRELATIVO.match(limpio)
    if not m:
        return None
    prefijo = m.group(1) or "NUM"
    return prefijo, int(m.group(2))


def calcular_correlativos(
    colecciones: Mapping[tuple[str, str], Iterable[str]],
    *,
    ahora: datetime | None = None,
) -> list[dict[str, Any]]:
    """Calcula el mayor correlativo por ``(entidad, prefijo)`` a partir de colecciones."""
    ts = ahora or datetime.now()
    acumulado: dict[tuple[str, str], dict[str, Any]] = {}
    for (entidad, fuente), valores in colecciones.items():
        for raw in valores:
            parsed = extraer_correlativo_numerico(raw)
            if parsed is None:
                continue
            prefijo, numero = parsed
            clave = (entidad, prefijo)
            actual = acumulado.get(clave)
            if actual is None:
                acumulado[clave] = {
                    "entidad": entidad,
                    "prefijo": prefijo,
                    "ultimo_numero": numero,
                    "ejemplo_maximo": str(raw).strip(),
                    "fuente": fuente,
                    "total_registros": 1,
                    "actualizado_en": ts,
                }
            else:
                actual["total_registros"] += 1
                if numero > int(actual["ultimo_numero"]):
                    actual["ultimo_numero"] = numero
                    actual["ejemplo_maximo"] = str(raw).strip()

    salida = list(acumulado.values())
    salida.sort(key=lambda r: (r["fuente"], r["entidad"], r["prefijo"]))
    return salida


def _colecciones_desde_repo(repo: Repository) -> dict[tuple[str, str], list[str]]:
    cols: dict[tuple[str, str], list[str]] = {
        ("ordenes_venta", "odoo"): [str(o.so_id) for o in repo.all_ordenes() if o.so_id],
        ("pagos_recibidos", "odoo"): [
            str(p.get("numero_odoo") or "") for p in repo.all_pagos_full() if p.get("numero_odoo")
        ],
    }
    with contextlib.suppress(NotImplementedError):
        cols[("facturas", "odoo")] = [str(f.numero) for f in repo.all_facturas() if f.numero]
    with contextlib.suppress(NotImplementedError):
        cols[("pagos_proveedores", "odoo")] = [
            str(p.get("numero_odoo") or "")
            for p in repo.all_pagos_proveedores_odoo()
            if p.get("numero_odoo")
        ]
    with contextlib.suppress(NotImplementedError):
        cols[("compras_gastos", "odoo")] = [
            str(c.get("numero_odoo") or "")
            for c in repo.all_compras_gastos_odoo()
            if c.get("numero_odoo")
        ]
    return cols


def _colecciones_desde_sql(engine: Engine) -> dict[tuple[str, str], list[str]]:
    cols: dict[tuple[str, str], list[str]] = {}
    with engine.connect() as conn:
        cols[("ordenes_venta", "odoo")] = [
            str(r[0]) for r in conn.execute(select(ordenes_venta.c.so_id)).all() if r[0]
        ]
        cols[("facturas", "odoo")] = [
            str(r[0])
            for r in conn.execute(select(facturas.c.numero)).all()
            if r[0] and str(r[0]).strip()
        ]
        cols[("pagos_recibidos", "odoo")] = [
            str(r[0])
            for r in conn.execute(select(pagos.c.numero_odoo)).all()
            if r[0] and str(r[0]).strip()
        ]
        cols[("pagos_proveedores", "odoo")] = [
            str(r[0])
            for r in conn.execute(select(pagos_proveedores_odoo.c.numero_odoo)).all()
            if r[0] and str(r[0]).strip()
        ]
        cols[("compras_gastos", "odoo")] = [
            str(r[0])
            for r in conn.execute(select(compras_gastos_odoo.c.numero_odoo)).all()
            if r[0] and str(r[0]).strip()
        ]

        docs_com = conn.execute(
            select(documentos_comerciales.c.tipo, documentos_comerciales.c.numero)
        ).all()
        cols[("cxc_cotizaciones", "cxc")] = [str(n) for t, n in docs_com if t == "cotizacion" and n]
        cols[("cxc_pedidos", "cxc")] = [str(n) for t, n in docs_com if t == "pedido" and n]

        docs_op = conn.execute(
            select(documentos_operativos.c.numero, documentos_operativos.c.numero_recibo)
        ).all()
        cols[("cxc_operativos", "cxc")] = [str(n) for n, _r in docs_op if n]
        cols[("cxc_recibos", "cxc")] = [str(r) for _n, r in docs_op if r and str(r).strip()]

        cols[("cxc_lotes_caja_chica", "cxc")] = [
            str(r[0]) for r in conn.execute(select(lotes_caja_chica.c.codigo)).all() if r[0]
        ]
    return cols


def _serializar_correlativos(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "entidad": str(r["entidad"]),
            "prefijo": str(r["prefijo"]),
            "ultimo_numero": int(r["ultimo_numero"]),
            "ejemplo_maximo": str(r["ejemplo_maximo"]),
            "fuente": str(r["fuente"]),
            "total_registros": int(r["total_registros"]),
            "actualizado_en": (
                r["actualizado_en"].isoformat()
                if isinstance(r.get("actualizado_en"), datetime)
                else str(r.get("actualizado_en") or "")
            ),
        }
        for r in items
    ]


def guardar_correlativos_sistema(
    repo: Repository,
    items: list[dict[str, Any]],
    *,
    engine: Engine | None = None,
) -> list[dict[str, Any]]:
    """Persiste la foto de correlativos máximos en Postgres y/o ``app_settings``."""
    serializados = _serializar_correlativos(items)
    repo.set_config(KEY_CORRELATIVOS_JSON, json.dumps(serializados, ensure_ascii=False))
    if engine is None or not items:
        return serializados

    stmt = pg_insert(correlativos_sistema).values(items)
    stmt = stmt.on_conflict_do_update(
        index_elements=[correlativos_sistema.c.entidad, correlativos_sistema.c.prefijo],
        set_={
            "ultimo_numero": stmt.excluded.ultimo_numero,
            "ejemplo_maximo": stmt.excluded.ejemplo_maximo,
            "fuente": stmt.excluded.fuente,
            "total_registros": stmt.excluded.total_registros,
            "actualizado_en": stmt.excluded.actualizado_en,
        },
    )
    with engine.begin() as conn:
        conn.execute(stmt)
    return serializados


def conteos_tablas_bd(repo: Repository, *, engine: Engine | None = None) -> dict[str, int]:
    """Devuelve el total de filas actuales en las tablas espejo y CxC relevantes."""
    if engine is not None:
        tablas = (
            ("clientes", clientes),
            ("ordenes_venta", ordenes_venta),
            ("lineas_orden", lineas_orden),
            ("pagos", pagos),
            ("facturas", facturas),
            ("entregas", entregas),
            ("catalogo", catalogo),
            ("lineas_factura", lineas_factura),
            ("lineas_entrega", lineas_entrega),
            ("bandeja_facturacion", bandeja_facturacion),
            ("pagos_proveedores_odoo", pagos_proveedores_odoo),
            ("compras_gastos_odoo", compras_gastos_odoo),
            ("documentos_comerciales", documentos_comerciales),
            ("documentos_operativos", documentos_operativos),
        )
        out: dict[str, int] = {}
        with engine.connect() as conn:
            for nombre, tbl in tablas:
                try:
                    out[nombre] = int(
                        conn.execute(select(func.count()).select_from(tbl)).scalar_one()
                    )
                except SQLAlchemyError:
                    out[nombre] = 0
        return out

    conteos = {
        "clientes": len(repo.all_clientes()),
        "ordenes_venta": len(repo.all_ordenes()),
        "lineas_orden": len(repo.all_lineas()),
        "pagos": len(repo.all_pagos()),
    }
    for clave, fn in (
        ("facturas", repo.all_facturas),
        ("entregas", repo.all_entregas),
        ("catalogo", repo.all_catalogo),
        ("pagos_proveedores_odoo", repo.all_pagos_proveedores_odoo),
        ("compras_gastos_odoo", repo.all_compras_gastos_odoo),
    ):
        try:
            conteos[clave] = len(fn())
        except NotImplementedError:
            conteos[clave] = 0
    return conteos


class SiembraOdooService:
    """Orquesta la siembra histórica completa desde Odoo conservando correlativos."""

    def __init__(
        self,
        repo: Repository,
        reader: OdooReader,
        engine: Engine | None = None,
    ) -> None:
        self._repo = repo
        self._reader = reader
        self._engine = engine if engine is not None else getattr(repo, "_engine", None)

    @staticmethod
    def requiere_siembra_inicial(repo: Repository, engine: Engine | None = None) -> bool:
        """True si todavía nunca se completó una siembra histórica."""
        eng = engine if engine is not None else getattr(repo, "_engine", None)
        if eng is not None:
            try:
                with eng.connect() as conn:
                    cnt = conn.execute(
                        select(func.count())
                        .select_from(siembra_bitacora)
                        .where(
                            siembra_bitacora.c.estado.in_(["COMPLETADA", "COMPLETADA_CON_AVISOS"])
                        )
                    ).scalar_one()
                return int(cnt or 0) == 0
            except SQLAlchemyError:
                pass
        raw = repo.get_config(KEY_ULTIMA_SIEMBRA_JSON)
        return not (raw and raw.strip())

    def _crear_bitacora(self, tipo: str, actor: str, inicio: datetime) -> int | None:
        if self._engine is None:
            return None
        with self._engine.begin() as conn:
            sid = conn.execute(
                insert(siembra_bitacora)
                .values(
                    tipo=tipo,
                    estado="EN_PROGRESO",
                    actor=actor or "sistema",
                    iniciada_en=inicio,
                )
                .returning(siembra_bitacora.c.siembra_id)
            ).scalar_one()
        return int(sid)

    def _cerrar_bitacora(
        self,
        siembra_id: int | None,
        *,
        estado: str,
        fin: datetime,
        duracion: Decimal,
        conteos: dict[str, int],
        correlativos: list[dict[str, Any]],
        avisos: list[str],
        error: str = "",
    ) -> None:
        if siembra_id is None or self._engine is None:
            return
        with self._engine.begin() as conn:
            conn.execute(
                update(siembra_bitacora)
                .where(siembra_bitacora.c.siembra_id == siembra_id)
                .values(
                    estado=estado,
                    finalizada_en=fin,
                    duracion_seg=duracion,
                    conteos_json=json.dumps(conteos, ensure_ascii=False),
                    correlativos_json=json.dumps(correlativos, ensure_ascii=False),
                    avisos_json=json.dumps(avisos, ensure_ascii=False),
                    error=error,
                )
            )

    def _sembrar_paso(
        self,
        nombre: str,
        lector_fn: Callable[[], list[Any]],
        upsert_fn: Callable[[list[Any]], None],
        conteos: dict[str, int],
        avisos: list[str],
        *,
        obligatorio: bool,
    ) -> None:
        try:
            filas = lector_fn()
            if filas:
                upsert_fn(filas)
            conteos[nombre] = len(filas)
        except NotImplementedError:
            conteos[nombre] = 0
            logger.info("Siembra: backend actual no implementa espejo de %s; omitido.", nombre)
        except Exception as exc:
            if obligatorio:
                raise
            conteos[nombre] = 0
            aviso = f"{nombre}: {exc}"
            avisos.append(aviso)
            logger.warning("Siembra histórica (%s): %s", nombre, exc)

    def ejecutar(
        self,
        *,
        actor: str = "sistema",
        solo_correlativos: bool = False,
    ) -> dict[str, Any]:
        """Ejecuta siembra histórica completa desde Odoo (``since=None``) y correlativos."""
        inicio = datetime.now()
        t0 = time.monotonic()
        tipo = "solo_correlativos" if solo_correlativos else "completa"
        siembra_id = self._crear_bitacora(tipo, actor, inicio)
        conteos: dict[str, int] = {}
        avisos: list[str] = []

        try:
            if not solo_correlativos:
                # Orden estricto por claves foráneas en PostgresRepository
                self._sembrar_paso(
                    "clientes",
                    lambda: self._reader.changed_clientes(None),
                    self._repo.upsert_clientes,
                    conteos,
                    avisos,
                    obligatorio=True,
                )
                self._sembrar_paso(
                    "ordenes_venta",
                    lambda: self._reader.changed_ordenes(None),
                    self._repo.upsert_ordenes,
                    conteos,
                    avisos,
                    obligatorio=True,
                )
                self._sembrar_paso(
                    "lineas_orden",
                    lambda: self._reader.changed_lineas(None),
                    self._repo.upsert_lineas,
                    conteos,
                    avisos,
                    obligatorio=True,
                )
                self._sembrar_paso(
                    "pagos",
                    lambda: self._reader.changed_pagos(None),
                    self._repo.upsert_pagos,
                    conteos,
                    avisos,
                    obligatorio=True,
                )
                self._sembrar_paso(
                    "facturas",
                    lambda: self._reader.changed_facturas(None),
                    self._repo.upsert_facturas,
                    conteos,
                    avisos,
                    obligatorio=False,
                )
                self._sembrar_paso(
                    "entregas",
                    lambda: self._reader.changed_entregas(None),
                    self._repo.upsert_entregas,
                    conteos,
                    avisos,
                    obligatorio=False,
                )
                self._sembrar_paso(
                    "catalogo",
                    lambda: self._reader.changed_catalogo(None),
                    self._repo.upsert_catalogo,
                    conteos,
                    avisos,
                    obligatorio=False,
                )
                self._sembrar_paso(
                    "lineas_factura",
                    lambda: self._reader.changed_lineas_factura(None),
                    self._repo.upsert_lineas_factura,
                    conteos,
                    avisos,
                    obligatorio=False,
                )
                self._sembrar_paso(
                    "entregas_lineas",
                    lambda: self._reader.changed_entregas_lineas(None),
                    self._repo.upsert_entregas_lineas,
                    conteos,
                    avisos,
                    obligatorio=False,
                )
                if hasattr(self._reader, "changed_pagos_proveedores"):
                    self._sembrar_paso(
                        "pagos_proveedores_odoo",
                        lambda: self._reader.changed_pagos_proveedores(None),
                        self._repo.upsert_pagos_proveedores_odoo,
                        conteos,
                        avisos,
                        obligatorio=False,
                    )
                if hasattr(self._reader, "changed_compras_gastos"):
                    self._sembrar_paso(
                        "compras_gastos_odoo",
                        lambda: self._reader.changed_compras_gastos(None),
                        self._repo.upsert_compras_gastos_odoo,
                        conteos,
                        avisos,
                        obligatorio=False,
                    )
                self._repo.set_last_sync(inicio)

            colecciones = (
                _colecciones_desde_sql(self._engine)
                if self._engine is not None
                else _colecciones_desde_repo(self._repo)
            )
            correlativos_raw = calcular_correlativos(colecciones, ahora=datetime.now())
            correlativos = guardar_correlativos_sistema(
                self._repo, correlativos_raw, engine=self._engine
            )
            fin = datetime.now()
            duracion = Decimal(str(round(time.monotonic() - t0, 2)))
            estado = "COMPLETADA_CON_AVISOS" if avisos else "COMPLETADA"
            totales_bd = conteos_tablas_bd(self._repo, engine=self._engine)
            resumen = {
                "siembra_id": siembra_id,
                "tipo": tipo,
                "estado": estado,
                "actor": actor,
                "iniciada_en": inicio.isoformat(),
                "finalizada_en": fin.isoformat(),
                "duracion_seg": float(duracion),
                "conteos_sembrados": conteos,
                "conteos_bd": totales_bd,
                "correlativos_count": len(correlativos),
                "correlativos": correlativos,
                "avisos": avisos,
            }
            self._repo.set_config(KEY_ULTIMA_SIEMBRA_JSON, json.dumps(resumen, ensure_ascii=False))
            self._cerrar_bitacora(
                siembra_id,
                estado=estado,
                fin=fin,
                duracion=duracion,
                conteos=conteos,
                correlativos=correlativos,
                avisos=avisos,
            )
            logger.info(
                "Siembra histórica %s (%s) en %.2fs: sembrados=%s, correlativos=%s",
                estado,
                actor,
                float(duracion),
                conteos,
                len(correlativos),
            )
            return resumen
        except Exception as exc:
            fin = datetime.now()
            duracion = Decimal(str(round(time.monotonic() - t0, 2)))
            self._cerrar_bitacora(
                siembra_id,
                estado="ERROR",
                fin=fin,
                duracion=duracion,
                conteos=conteos,
                correlativos=[],
                avisos=avisos,
                error=str(exc),
            )
            raise

    @staticmethod
    def estado_actual(repo: Repository, *, engine: Engine | None = None) -> dict[str, Any]:
        """Consulta última siembra, correlativos guardados y conteos reales en BD."""
        eng = engine if engine is not None else getattr(repo, "_engine", None)
        ultima: dict[str, Any] | None = None
        historial: list[dict[str, Any]] = []
        corrs: list[dict[str, Any]] = []

        if eng is not None:
            try:
                with eng.connect() as conn:
                    rows_bit = conn.execute(
                        select(siembra_bitacora)
                        .order_by(siembra_bitacora.c.siembra_id.desc())
                        .limit(10)
                    ).all()
                    for r in rows_bit:
                        historial.append(
                            {
                                "siembra_id": int(r.siembra_id),
                                "tipo": str(r.tipo),
                                "estado": str(r.estado),
                                "actor": str(r.actor),
                                "iniciada_en": r.iniciada_en.isoformat() if r.iniciada_en else None,
                                "finalizada_en": (
                                    r.finalizada_en.isoformat() if r.finalizada_en else None
                                ),
                                "duracion_seg": (
                                    float(r.duracion_seg) if r.duracion_seg is not None else None
                                ),
                                "conteos_sembrados": json.loads(r.conteos_json or "{}"),
                                "avisos": json.loads(r.avisos_json or "[]"),
                                "error": str(r.error or ""),
                            }
                        )
                    rows_corr = conn.execute(
                        select(correlativos_sistema).order_by(
                            correlativos_sistema.c.fuente,
                            correlativos_sistema.c.entidad,
                            correlativos_sistema.c.prefijo,
                        )
                    ).all()
                    for c in rows_corr:
                        corrs.append(
                            {
                                "entidad": str(c.entidad),
                                "prefijo": str(c.prefijo),
                                "ultimo_numero": int(c.ultimo_numero),
                                "ejemplo_maximo": str(c.ejemplo_maximo),
                                "fuente": str(c.fuente),
                                "total_registros": int(c.total_registros),
                                "actualizado_en": (
                                    c.actualizado_en.isoformat() if c.actualizado_en else None
                                ),
                            }
                        )
            except SQLAlchemyError:
                pass

        if historial:
            ultima = historial[0]
        else:
            raw_u = repo.get_config(KEY_ULTIMA_SIEMBRA_JSON)
            if raw_u:
                try:
                    parsed = json.loads(raw_u)
                    if isinstance(parsed, dict):
                        ultima = parsed
                except Exception:
                    pass

        if not corrs:
            raw_c = repo.get_config(KEY_CORRELATIVOS_JSON)
            if raw_c:
                try:
                    parsed_c = json.loads(raw_c)
                    if isinstance(parsed_c, list):
                        corrs = parsed_c
                except Exception:
                    pass

        totales_bd = conteos_tablas_bd(repo, engine=eng)
        req = SiembraOdooService.requiere_siembra_inicial(repo, engine=eng)
        return {
            "requiere_siembra_inicial": req,
            "ultima_siembra": ultima,
            "historial": historial,
            "correlativos": corrs,
            "conteos_bd": totales_bd,
        }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Siembra histórica completa de Odoo en Postgres preservando correlativos."
    )
    parser.add_argument(
        "--solo-correlativos",
        action="store_true",
        help="Recalcula correlativos_sistema sobre la BD actual sin consultar Odoo.",
    )
    parser.add_argument("--actor", default="cli", help="Nombre de quien ejecuta la siembra.")
    args = parser.parse_args(argv)

    from ..web.app import get_repo

    config = AppConfig.from_env()
    repo = get_repo()
    reader = OdooXmlRpcReader(config.odoo)
    srv = SiembraOdooService(repo, reader, engine=getattr(repo, "_engine", None))
    resumen = srv.ejecutar(actor=args.actor, solo_correlativos=args.solo_correlativos)
    print(json.dumps(resumen, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(_main())
