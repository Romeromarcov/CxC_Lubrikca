"""Persistencia de la ingesta bancaria (SQLAlchemy Core, mismo engine que el resto).

Fuera de ``Repository`` a propósito: esa interfaz es el contrato del espejo de Odoo
y del trabajo humano (con su doble en memoria para los tests). Los bancos son un
mundo aparte, de solo-append, y no necesitan pasar por ella.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Engine, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cxc.db.schema import bancos_archivos, bancos_movimientos

from .ingesta import ArchivoPreparado, MovimientoClasificado
from .modelos import MovimientoBancario
from .saldos import Corte


@dataclass(frozen=True)
class ResumenIngesta:
    archivo_id: int
    banco: str
    nombre: str
    ya_cargado: bool  # el mismo archivo (mismo sha256) ya estaba
    n_movimientos: int
    n_nuevos: int  # los demás ya venían de un corte anterior (solapado)
    cuadra: bool
    observaciones: str
    fecha_desde: date | None
    fecha_hasta: date | None


def guardar(engine: Engine, prep: ArchivoPreparado, usuario: str) -> ResumenIngesta:
    """Guarda el archivo crudo y sus movimientos nuevos, todo o nada.

    Idempotente en dos niveles: el mismo archivo (sha256) no se vuelve a guardar, y
    un movimiento ya conocido (huella) no se vuelve a insertar.
    """
    e, v = prep.estado, prep.validacion
    with engine.begin() as conn:
        previo = (
            conn.execute(select(bancos_archivos).where(bancos_archivos.c.sha256 == prep.sha256))
            .mappings()
            .first()
        )
        if previo is not None:
            return ResumenIngesta(
                previo["archivo_id"],
                previo["banco"],
                previo["nombre_archivo"],
                True,
                previo["n_movimientos"],
                0,
                previo["cuadra"],
                previo["observaciones"],
                previo["fecha_desde"],
                previo["fecha_hasta"],
            )
        ahora = datetime.now()
        huellas = [mc.mov.huella for mc in prep.movimientos]
        conocidas = set(
            conn.execute(
                select(bancos_movimientos.c.huella).where(bancos_movimientos.c.huella.in_(huellas))
            ).scalars()
        )
        nuevos: dict[str, MovimientoClasificado] = {}
        for mc in prep.movimientos:
            if mc.mov.huella not in conocidas:
                nuevos.setdefault(mc.mov.huella, mc)
        archivo_id = conn.execute(
            insert(bancos_archivos)
            .values(
                sha256=prep.sha256,
                banco=e.banco,
                cuenta=e.cuenta,
                moneda=e.moneda,
                nombre_archivo=prep.nombre,
                contenido=prep.contenido,
                cargado_en=ahora,
                cargado_por=usuario,
                n_movimientos=len(prep.movimientos),
                n_nuevos=len(nuevos),
                fecha_desde=e.movimientos[0].fecha,
                fecha_hasta=e.movimientos[-1].fecha,
                saldo_inicial=v.saldo_inicial,
                saldo_final=v.saldo_final_calculado,
                saldo_inicial_derivado=e.saldo_inicial_derivado,
                cuadra=v.ok,
                observaciones=prep.observaciones,
            )
            .returning(bancos_archivos.c.archivo_id)
        ).scalar_one()
        if nuevos:
            conn.execute(
                pg_insert(bancos_movimientos).on_conflict_do_nothing(index_elements=["huella"]),
                [_fila(mc, archivo_id, ahora) for mc in nuevos.values()],
            )
    return ResumenIngesta(
        archivo_id,
        e.banco,
        prep.nombre,
        False,
        len(prep.movimientos),
        len(nuevos),
        v.ok,
        prep.observaciones,
        e.movimientos[0].fecha,
        e.movimientos[-1].fecha,
    )


def _fila(mc: MovimientoClasificado, archivo_id: int, ahora: datetime) -> dict[str, Any]:
    m = mc.mov
    return {
        "huella": m.huella,
        "unique_import_id": m.unique_import_id,
        "banco": m.banco,
        "cuenta": m.cuenta,
        "moneda": m.moneda,
        "fecha": m.fecha,
        "referencia": m.referencia,
        "correlativo": m.correlativo,
        "descripcion": m.descripcion,
        "codigo": m.codigo,
        "debito": m.debito,
        "credito": m.credito,
        "saldo": m.saldo,
        "orden": m.orden,
        "categoria": mc.categoria,
        "regla": mc.regla,
        "archivo_id": archivo_id,
        "creado_en": ahora,
    }


def movimientos(
    engine: Engine,
    *,
    banco: str | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    categoria: str | None = None,
) -> list[MovimientoClasificado]:
    t = bancos_movimientos
    q = select(t)
    if banco:
        q = q.where(t.c.banco == banco.upper())
    if desde:
        q = q.where(t.c.fecha >= desde)
    if hasta:
        q = q.where(t.c.fecha <= hasta)
    if categoria:
        q = q.where(t.c.categoria == categoria)
    # Dentro del día, el orden del archivo que lo trajo primero.
    q = q.order_by(t.c.banco, t.c.cuenta, t.c.fecha, t.c.archivo_id, t.c.orden)
    with engine.connect() as conn:
        filas = conn.execute(q).mappings().all()
    return [
        MovimientoClasificado(
            MovimientoBancario(
                banco=r["banco"],
                cuenta=r["cuenta"],
                moneda=r["moneda"],
                fecha=r["fecha"],
                referencia=r["referencia"],
                descripcion=r["descripcion"],
                debito=r["debito"],
                credito=r["credito"],
                saldo=r["saldo"],
                orden=r["orden"],
                codigo=r["codigo"],
                correlativo=r["correlativo"],
            ),
            r["categoria"],
            r["regla"],
        )
        for r in filas
    ]


def cortes(engine: Engine) -> list[Corte]:
    t = bancos_archivos
    q = select(
        t.c.archivo_id,
        t.c.banco,
        t.c.cuenta,
        t.c.moneda,
        t.c.nombre_archivo,
        t.c.fecha_desde,
        t.c.fecha_hasta,
        t.c.saldo_inicial,
        t.c.saldo_final,
        t.c.cuadra,
        t.c.cargado_en,
    ).order_by(t.c.archivo_id)
    with engine.connect() as conn:
        return [Corte(**r) for r in conn.execute(q).mappings().all()]


def archivos(engine: Engine) -> list[dict[str, Any]]:
    """Listado de cargas (sin el contenido binario)."""
    t = bancos_archivos
    cols = [c for c in t.c if c.name != "contenido"]
    with engine.connect() as conn:
        return [
            dict(r) for r in conn.execute(select(*cols).order_by(t.c.archivo_id.desc())).mappings()
        ]
