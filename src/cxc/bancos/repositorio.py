"""Persistencia de la ingesta bancaria (SQLAlchemy Core, mismo engine que el resto).

Fuera de ``Repository`` a propósito: esa interfaz es el contrato del espejo de Odoo
y del trabajo humano (con su doble en memoria para los tests). Los bancos son un
mundo aparte, de solo-append, y no necesitan pasar por ella.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cxc.db.schema import (
    bancos_archivos,
    bancos_decisiones,
    bancos_movimientos,
    bancos_propuestas,
)

from .conciliacion import CUENTA, PAGO, Propuesta
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


# ------------------------------------------------------------- fase 2: propuestas

PENDIENTE, APROBADA, RECHAZADA, CORREGIDA = "pendiente", "aprobada", "rechazada", "corregida"
ACCIONES = ("aprobar", "rechazar", "corregir", "reabrir")


class DecisionInvalida(ValueError):
    """La acción no aplica al estado actual de la propuesta (o le faltan datos)."""


def guardar_propuestas(
    engine: Engine, props: list[Propuesta], info_pagos: dict[int, dict[str, Any]] | None = None
) -> dict[str, int]:
    """Inserta o refresca propuestas. Las que una persona ya decidió no se tocan: las
    reglas pueden mejorar, pero no deshacen una decisión humana.

    info_pagos (id -> nombre, fecha, partner, memo...) se guarda junto a cada
    candidato, para que la cola de revisión lo muestre sin volver a leer Odoo.
    """
    info = info_pagos or {}
    ahora = datetime.now()
    nuevas = actualizadas = intactas = 0
    p = bancos_propuestas
    with engine.begin() as conn:
        estados: dict[str, str] = {
            str(h): str(e)
            for h, e in conn.execute(
                select(p.c.huella, p.c.estado).where(p.c.huella.in_([x.huella for x in props]))
            ).all()
        }
        for x in props:
            valores = {
                "tipo": x.tipo,
                "regla": x.regla,
                "motivo": x.motivo,
                "pago_id": x.pago_id,
                "pago_nombre": x.pago_nombre,
                "cuenta": x.cuenta,
                "puntaje": x.puntaje,
                "candidatos": json.dumps(
                    [info.get(c, {"id": c}) for c in x.candidatos], default=str
                ),
                "generada_en": ahora,
            }
            if x.huella not in estados:
                conn.execute(insert(p).values(huella=x.huella, **valores))
                nuevas += 1
            elif estados[x.huella] == PENDIENTE:
                conn.execute(update(p).where(p.c.huella == x.huella).values(**valores))
                actualizadas += 1
            else:
                intactas += 1
    return {"nuevas": nuevas, "actualizadas": actualizadas, "ya_decididas": intactas}


def propuestas(
    engine: Engine,
    *,
    banco: str | None = None,
    estado: str | None = None,
    tipo: str | None = None,
    desde: date | None = None,
    hasta: date | None = None,
) -> list[dict[str, Any]]:
    """Movimientos con su propuesta (los que no tienen propuesta no aparecen)."""
    m, p = bancos_movimientos, bancos_propuestas
    q = select(
        m.c.huella,
        m.c.banco,
        m.c.moneda,
        m.c.fecha,
        m.c.referencia,
        m.c.descripcion,
        (m.c.credito - m.c.debito).label("monto"),
        m.c.categoria,
        p.c.tipo,
        p.c.regla,
        p.c.motivo,
        p.c.pago_id,
        p.c.pago_nombre,
        p.c.cuenta,
        p.c.puntaje,
        p.c.candidatos,
        p.c.estado,
        p.c.decidida_por,
        p.c.decidida_en,
    ).join(p, p.c.huella == m.c.huella)
    if banco:
        q = q.where(m.c.banco == banco.upper())
    if estado:
        q = q.where(p.c.estado == estado)
    if tipo:
        q = q.where(p.c.tipo == tipo)
    if desde:
        q = q.where(m.c.fecha >= desde)
    if hasta:
        q = q.where(m.c.fecha <= hasta)
    q = q.order_by(m.c.fecha.desc(), m.c.banco, m.c.orden.desc())
    with engine.connect() as conn:
        out = []
        for r in conn.execute(q).mappings():
            d = dict(r)
            d["candidatos"] = json.loads(d["candidatos"] or "[]")
            out.append(d)
        return out


def decidir(
    engine: Engine,
    huella: str,
    accion: str,
    usuario: str,
    *,
    pago_id: int | None = None,
    cuenta: str | None = None,
    comentario: str = "",
) -> dict[str, Any]:
    """Aplica una decisión humana y la deja en la bitácora, en una sola transacción.

    - aprobar: acepta la propuesta tal cual (tiene que ser un pago o una cuenta).
    - rechazar: la propuesta está mal y no hay reemplazo (exige comentario).
    - corregir: reemplaza por otro pago u otra cuenta (uno solo).
    - reabrir: vuelve a pendiente una decisión ya tomada (exige comentario).
    """
    if accion not in ACCIONES:
        raise DecisionInvalida(f"acción desconocida: {accion}")
    comentario = comentario.strip()
    cuenta = (cuenta or "").strip()
    if accion in ("rechazar", "reabrir") and not comentario:
        raise DecisionInvalida(f"{accion} exige un comentario")
    if accion == "corregir" and (pago_id is None) == (not cuenta):
        raise DecisionInvalida("corregir exige un pago o una cuenta (uno solo)")
    p = bancos_propuestas
    with engine.begin() as conn:
        fila = (
            conn.execute(select(p).where(p.c.huella == huella).with_for_update())
            .mappings()
            .first()
        )
        if fila is None:
            raise KeyError(f"no hay propuesta para el movimiento {huella}")
        antes = {k: fila[k] for k in ("tipo", "pago_id", "pago_nombre", "cuenta", "estado")}
        cambios: dict[str, Any]
        if accion == "reabrir":
            if fila["estado"] == PENDIENTE:
                raise DecisionInvalida("la propuesta ya está pendiente")
            cambios = {"estado": PENDIENTE}
        elif fila["estado"] != PENDIENTE:
            raise DecisionInvalida(
                f"la propuesta ya está {fila['estado']}: reabrila antes de decidir de nuevo"
            )
        elif accion == "aprobar":
            if fila["tipo"] not in (PAGO, CUENTA):
                raise DecisionInvalida("no hay nada que aprobar: corregí con un pago o una cuenta")
            cambios = {"estado": APROBADA}
        elif accion == "rechazar":
            cambios = {"estado": RECHAZADA}
        elif pago_id is not None:
            nombre = next(
                (
                    str(c.get("nombre") or "")
                    for c in json.loads(fila["candidatos"] or "[]")
                    if c.get("id") == pago_id
                ),
                "",
            )
            cambios = {"estado": CORREGIDA, "tipo": PAGO, "pago_id": pago_id,
                       "pago_nombre": nombre, "cuenta": ""}
        else:
            cambios = {"estado": CORREGIDA, "tipo": CUENTA, "pago_id": None, "pago_nombre": "",
                       "cuenta": cuenta}
        ahora = datetime.now()
        reabre = accion == "reabrir"
        cambios["decidida_por"] = "" if reabre else usuario
        cambios["decidida_en"] = None if reabre else ahora
        conn.execute(update(p).where(p.c.huella == huella).values(**cambios))
        despues = antes | {k: v for k, v in cambios.items() if k in antes}
        conn.execute(
            insert(bancos_decisiones).values(
                huella=huella,
                accion=accion,
                antes=json.dumps(antes, default=str),
                despues=json.dumps(despues, default=str),
                comentario=comentario,
                usuario=usuario,
                decidida_en=ahora,
            )
        )
    return despues


def decisiones(
    engine: Engine, huella: str | None = None, limite: int = 500
) -> list[dict[str, Any]]:
    """La bitácora, de la más reciente a la más vieja."""
    d = bancos_decisiones
    q = select(d).order_by(d.c.decision_id.desc()).limit(limite)
    if huella:
        q = q.where(d.c.huella == huella)
    with engine.connect() as conn:
        out = []
        for r in conn.execute(q).mappings():
            x = dict(r)
            x["antes"], x["despues"] = json.loads(x["antes"]), json.loads(x["despues"])
            out.append(x)
        return out


def aprobadas(
    engine: Engine,
    *,
    banco: str | None = None,
    decididas_desde: date | None = None,
    decididas_hasta: date | None = None,
) -> list[dict[str, Any]]:
    """Lo aprobado o corregido, para aplicarlo en Odoo a mano mientras no hay fase 3.

    Igual que ``propuestas`` más el comentario de la última decisión. Los filtros de
    fecha son por cuándo se decidió (``decididas_hasta`` incluye todo ese día), para
    poder bajar "lo aprobado hoy" sin repetir lo de ayer.
    """
    filas = [
        f
        for f in propuestas(engine, banco=banco)
        if f["estado"] in (APROBADA, CORREGIDA)
        and (decididas_desde is None or f["decidida_en"].date() >= decididas_desde)
        and (decididas_hasta is None or f["decidida_en"].date() <= decididas_hasta)
    ]
    if not filas:
        return []
    d = bancos_decisiones
    ultima = (
        select(d.c.huella, d.c.comentario)
        .where(d.c.huella.in_([f["huella"] for f in filas]))
        .where(d.c.accion != "reabrir")
        .order_by(d.c.huella, d.c.decision_id.desc())
        .distinct(d.c.huella)
    )
    with engine.connect() as conn:
        comentarios = {r.huella: r.comentario for r in conn.execute(ultima)}
    for f in filas:
        f["comentario"] = comentarios.get(f["huella"], "")
    filas.sort(key=lambda f: (f["banco"], f["fecha"], f["huella"]))
    return filas
