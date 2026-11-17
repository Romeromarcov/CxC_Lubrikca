"""Persistencia SQLAlchemy Core de Documentos Comerciales y sus líneas."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import Engine, delete, func, insert, or_, select, update

from cxc.db.schema import documentos_comerciales, documentos_comerciales_lineas

from .modelos import COTIZACION, DocumentoComercial, LineaDocumento


class RepoDocumentos(Protocol):
    def crear(
        self, cabecera: dict[str, Any], lineas: Sequence[dict[str, Any]]
    ) -> DocumentoComercial: ...

    def obtener(self, documento_id: int) -> DocumentoComercial | None: ...

    def obtener_por_captura(self, captura_id: int) -> DocumentoComercial | None: ...

    def listar(
        self,
        *,
        tipo: str | None = None,
        estado: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DocumentoComercial], int, dict[str, int]]: ...

    def actualizar(
        self,
        documento_id: int,
        cabecera: dict[str, Any],
        lineas: Sequence[dict[str, Any]] | None = None,
    ) -> DocumentoComercial: ...


def _prefijo(tipo: str) -> str:
    return "COT" if tipo == COTIZACION else "PED"


def _a_linea(r: Any) -> LineaDocumento:
    return LineaDocumento(
        linea_id=int(r.linea_id),
        orden=int(r.orden),
        producto_id=str(r.producto_id or ""),
        codigo=str(r.codigo or ""),
        descripcion=str(r.descripcion or ""),
        presentacion=str(r.presentacion or ""),
        litros_unidad=Decimal(str(r.litros_unidad or "0")),
        cantidad=Decimal(str(r.cantidad or "0")),
        precio_unitario=Decimal(str(r.precio_unitario or "0")),
        descuento_pct=Decimal(str(r.descuento_pct or "0")),
        subtotal=Decimal(str(r.subtotal or "0")),
    )


def _a_documento(r: Any, lineas: Sequence[LineaDocumento]) -> DocumentoComercial:
    return DocumentoComercial(
        documento_id=int(r.documento_id),
        numero=str(r.numero),
        tipo=str(r.tipo),
        estado=str(r.estado),
        origen=str(r.origen),
        captura_id=int(r.captura_id) if r.captura_id is not None else None,
        partner_odoo_id=int(r.partner_odoo_id) if r.partner_odoo_id is not None else None,
        tipo_contacto=str(r.tipo_contacto or "prospecto"),
        cliente_nombre=str(r.cliente_nombre),
        rif=str(r.rif or ""),
        email=str(r.email or ""),
        telefono=str(r.telefono or ""),
        direccion=str(r.direccion or ""),
        plus_code=str(r.plus_code or ""),
        latitud=float(r.latitud) if r.latitud is not None else None,
        longitud=float(r.longitud) if r.longitud is not None else None,
        es_primera_compra=bool(r.es_primera_compra),
        moneda_lista=str(r.moneda_lista or "USD"),
        vendedor_email=str(r.vendedor_email or ""),
        fecha_entrega=r.fecha_entrega,
        condiciones_negociacion=str(r.condiciones_negociacion or ""),
        observaciones=str(r.observaciones or ""),
        subtotal=Decimal(str(r.subtotal or "0")),
        total_litros=Decimal(str(r.total_litros or "0")),
        odoo_order_id=int(r.odoo_order_id) if r.odoo_order_id is not None else None,
        odoo_order_name=str(r.odoo_order_name or ""),
        error_odoo=str(r.error_odoo or ""),
        creado_por=str(r.creado_por or "sistema"),
        creado_en=r.creado_en,
        actualizado_en=r.actualizado_en,
        lineas=tuple(lineas),
    )


class RepoDocumentosSQL:
    """Repositorio Postgres de ``documentos_comerciales``."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def crear(
        self, cabecera: dict[str, Any], lineas: Sequence[dict[str, Any]]
    ) -> DocumentoComercial:
        ahora = datetime.now()
        tipo = str(cabecera["tipo"])
        temporal = f"{_prefijo(tipo)}-TMP-{ahora.timestamp():.6f}"
        with self._engine.begin() as conn:
            fila = conn.execute(
                insert(documentos_comerciales)
                .values(
                    **cabecera,
                    numero=temporal,
                    creado_en=ahora,
                    actualizado_en=ahora,
                )
                .returning(*documentos_comerciales.c)
            ).one()
            doc_id = int(fila.documento_id)
            numero = f"{_prefijo(tipo)}-{doc_id:05d}"
            fila = conn.execute(
                update(documentos_comerciales)
                .where(documentos_comerciales.c.documento_id == doc_id)
                .values(numero=numero)
                .returning(*documentos_comerciales.c)
            ).one()
            filas_ln = self._insertar_lineas(conn, doc_id, lineas)
        return _a_documento(fila, filas_ln)

    def _insertar_lineas(
        self, conn: Any, documento_id: int, lineas: Sequence[dict[str, Any]]
    ) -> list[LineaDocumento]:
        salida: list[LineaDocumento] = []
        for idx, ln in enumerate(lineas, start=1):
            r = conn.execute(
                insert(documentos_comerciales_lineas)
                .values(
                    documento_id=documento_id,
                    orden=int(ln.get("orden") or idx),
                    producto_id=str(ln.get("producto_id") or ""),
                    codigo=str(ln.get("codigo") or ""),
                    descripcion=str(ln["descripcion"]),
                    presentacion=str(ln.get("presentacion") or ""),
                    litros_unidad=ln.get("litros_unidad") or Decimal("0"),
                    cantidad=ln["cantidad"],
                    precio_unitario=ln.get("precio_unitario") or Decimal("0"),
                    descuento_pct=ln.get("descuento_pct") or Decimal("0"),
                    subtotal=ln.get("subtotal") or Decimal("0"),
                )
                .returning(*documentos_comerciales_lineas.c)
            ).one()
            salida.append(_a_linea(r))
        return salida

    def obtener(self, documento_id: int) -> DocumentoComercial | None:
        with self._engine.connect() as conn:
            fila = conn.execute(
                select(documentos_comerciales).where(
                    documentos_comerciales.c.documento_id == documento_id
                )
            ).first()
            if fila is None:
                return None
            lns = conn.execute(
                select(documentos_comerciales_lineas)
                .where(documentos_comerciales_lineas.c.documento_id == documento_id)
                .order_by(
                    documentos_comerciales_lineas.c.orden,
                    documentos_comerciales_lineas.c.linea_id,
                )
            ).all()
        return _a_documento(fila, [_a_linea(x) for x in lns])

    def obtener_por_captura(self, captura_id: int) -> DocumentoComercial | None:
        with self._engine.connect() as conn:
            fila = conn.execute(
                select(documentos_comerciales)
                .where(documentos_comerciales.c.captura_id == captura_id)
                .order_by(documentos_comerciales.c.documento_id.desc())
            ).first()
            if fila is None:
                return None
            doc_id = int(fila.documento_id)
            lns = conn.execute(
                select(documentos_comerciales_lineas)
                .where(documentos_comerciales_lineas.c.documento_id == doc_id)
                .order_by(
                    documentos_comerciales_lineas.c.orden,
                    documentos_comerciales_lineas.c.linea_id,
                )
            ).all()
        return _a_documento(fila, [_a_linea(x) for x in lns])

    def listar(
        self,
        *,
        tipo: str | None = None,
        estado: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DocumentoComercial], int, dict[str, int]]:
        filtros = []
        if tipo:
            filtros.append(documentos_comerciales.c.tipo == tipo)
        if estado:
            filtros.append(documentos_comerciales.c.estado == estado)
        if q and q.strip():
            patron = f"%{q.strip().lower()}%"
            filtros.append(
                or_(
                    func.lower(documentos_comerciales.c.numero).like(patron),
                    func.lower(documentos_comerciales.c.cliente_nombre).like(patron),
                    func.lower(documentos_comerciales.c.rif).like(patron),
                    func.lower(documentos_comerciales.c.odoo_order_name).like(patron),
                )
            )
        with self._engine.connect() as conn:
            total = int(
                conn.execute(
                    select(func.count()).select_from(documentos_comerciales).where(*filtros)
                ).scalar_one()
            )
            filas = conn.execute(
                select(documentos_comerciales)
                .where(*filtros)
                .order_by(documentos_comerciales.c.documento_id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            ids = [int(f.documento_id) for f in filas]
            mapa_lineas: dict[int, list[LineaDocumento]] = {i: [] for i in ids}
            if ids:
                filas_ln = conn.execute(
                    select(documentos_comerciales_lineas)
                    .where(documentos_comerciales_lineas.c.documento_id.in_(ids))
                    .order_by(
                        documentos_comerciales_lineas.c.orden,
                        documentos_comerciales_lineas.c.linea_id,
                    )
                ).all()
                for fl in filas_ln:
                    mapa_lineas[int(fl.documento_id)].append(_a_linea(fl))

            # Resumen global sobre todos los documentos
            todos_res = conn.execute(
                select(
                    documentos_comerciales.c.tipo,
                    documentos_comerciales.c.estado,
                    func.count(),
                ).group_by(documentos_comerciales.c.tipo, documentos_comerciales.c.estado)
            ).all()

        resumen = {
            "total": 0,
            "cotizaciones_borrador": 0,
            "pedidos_borrador": 0,
            "enviados_odoo": 0,
            "con_error": 0,
        }
        for t, est, cnt in todos_res:
            n = int(cnt)
            resumen["total"] += n
            if est == "ENVIADO_ODOO":
                resumen["enviados_odoo"] += n
            elif est == "ERROR":
                resumen["con_error"] += n
            elif t == "cotizacion":
                resumen["cotizaciones_borrador"] += n
            else:
                resumen["pedidos_borrador"] += n

        docs = [_a_documento(f, mapa_lineas.get(int(f.documento_id), [])) for f in filas]
        return docs, total, resumen

    def actualizar(
        self,
        documento_id: int,
        cabecera: dict[str, Any],
        lineas: Sequence[dict[str, Any]] | None = None,
    ) -> DocumentoComercial:
        valores = {**cabecera, "actualizado_en": datetime.now()}
        if "tipo" in valores:
            valores["numero"] = f"{_prefijo(str(valores['tipo']))}-{documento_id:05d}"
        with self._engine.begin() as conn:
            fila = conn.execute(
                update(documentos_comerciales)
                .where(documentos_comerciales.c.documento_id == documento_id)
                .values(**valores)
                .returning(*documentos_comerciales.c)
            ).one()
            if lineas is not None:
                conn.execute(
                    delete(documentos_comerciales_lineas).where(
                        documentos_comerciales_lineas.c.documento_id == documento_id
                    )
                )
                filas_ln = self._insertar_lineas(conn, documento_id, lineas)
            else:
                lns = conn.execute(
                    select(documentos_comerciales_lineas)
                    .where(documentos_comerciales_lineas.c.documento_id == documento_id)
                    .order_by(
                        documentos_comerciales_lineas.c.orden,
                        documentos_comerciales_lineas.c.linea_id,
                    )
                ).all()
                filas_ln = [_a_linea(x) for x in lns]
        return _a_documento(fila, filas_ln)
