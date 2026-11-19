"""Persistencia SQLAlchemy Core de Documentos Operativos en CxC."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import Engine, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from cxc.capturas.modelos import normalizar_nombre
from cxc.db.schema import documentos_operativos

from .modelos import (
    BORRADOR,
    ENVIADO_ODOO,
    ERROR_ODOO,
    INCOMPLETO,
    PENDIENTE_APROBACION,
    PREFIJO_POR_TIPO,
    RECHAZADO,
    TIPOS_COMPRAS,
    DocumentoOperativo,
)


class RepoOperativos(Protocol):
    def crear(self, campos: dict[str, Any]) -> DocumentoOperativo: ...

    def obtener(self, documento_id: int) -> DocumentoOperativo | None: ...

    def obtener_por_token(self, token_preview: str) -> DocumentoOperativo | None: ...

    def obtener_por_captura(self, captura_id: int) -> DocumentoOperativo | None: ...

    def actualizar(self, documento_id: int, campos: dict[str, Any]) -> DocumentoOperativo: ...

    def listar(
        self,
        *,
        tipo: str | None = None,
        grupo: str | None = None,
        estado: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DocumentoOperativo], int, dict[str, int]]: ...

    def buscar_duplicados_referencia(
        self, tipo: str, referencia: str, *, excluir_id: int | None = None
    ) -> list[DocumentoOperativo]: ...

    def buscar_duplicados_factura(
        self,
        proveedor: str,
        rif: str,
        numero_documento: str,
        *,
        excluir_id: int | None = None,
    ) -> list[DocumentoOperativo]: ...


def _cargar_json_dict(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _cargar_json_list(raw: Any) -> tuple[str, ...]:
    if not raw:
        return ()
    if isinstance(raw, list | tuple):
        return tuple(str(x) for x in raw)
    try:
        parsed = json.loads(str(raw))
        return tuple(str(x) for x in parsed) if isinstance(parsed, list) else ()
    except Exception:
        return ()


def _a_doc(r: Any) -> DocumentoOperativo:
    return DocumentoOperativo(
        documento_id=int(r.documento_id),
        numero=str(r.numero),
        token_preview=str(r.token_preview or ""),
        tipo=str(r.tipo),
        estado=str(r.estado),
        origen=str(r.origen or "web"),
        captura_id=int(r.captura_id) if r.captura_id is not None else None,
        partner_odoo_id=int(r.partner_odoo_id) if r.partner_odoo_id is not None else None,
        contraparte_nombre=str(r.contraparte_nombre or ""),
        rif=str(r.rif or ""),
        monto=Decimal(str(r.monto)) if r.monto is not None else None,
        base_imponible=Decimal(str(r.base_imponible)) if r.base_imponible is not None else None,
        iva=Decimal(str(r.iva)) if r.iva is not None else None,
        moneda=str(r.moneda or ""),
        metodo_pago=str(r.metodo_pago or ""),
        banco=str(r.banco or ""),
        diario_id=int(r.diario_id) if r.diario_id is not None else None,
        diario_nombre=str(r.diario_nombre or ""),
        cuenta_contable_id=(
            int(r.cuenta_contable_id) if r.cuenta_contable_id is not None else None
        ),
        cuenta_contable_codigo=str(r.cuenta_contable_codigo or ""),
        cuenta_contable_nombre=str(r.cuenta_contable_nombre or ""),
        referencia=str(r.referencia or ""),
        numero_documento=str(r.numero_documento or ""),
        numero_control=str(r.numero_control or ""),
        pedido_ref=str(r.pedido_ref or ""),
        factura_ref=str(r.factura_ref or ""),
        orden_ref=str(r.orden_ref or ""),
        categoria=str(r.categoria or ""),
        concepto=str(r.concepto or ""),
        sin_factura=bool(r.sin_factura),
        fecha_documento=r.fecha_documento,
        fecha_vencimiento=r.fecha_vencimiento,
        proveedor_telefono=str(r.proveedor_telefono or ""),
        proveedor_email=str(r.proveedor_email or ""),
        proveedor_direccion=str(r.proveedor_direccion or ""),
        notas=str(r.notas or ""),
        validacion=_cargar_json_dict(r.validacion_json),
        faltantes=_cargar_json_list(r.faltantes_json),
        aprobado_por=str(r.aprobado_por or ""),
        aprobado_en=r.aprobado_en,
        rechazo_motivo=str(r.rechazo_motivo or ""),
        odoo_model=str(r.odoo_model or ""),
        odoo_res_id=int(r.odoo_res_id) if r.odoo_res_id is not None else None,
        odoo_ref=str(r.odoo_ref or ""),
        error_odoo=str(r.error_odoo or ""),
        creado_por=str(r.creado_por or "sistema"),
        creado_en=r.creado_en,
        actualizado_en=r.actualizado_en,
    )


def _serializar(campos: dict[str, Any]) -> dict[str, Any]:
    out = dict(campos)
    if "validacion" in out:
        out["validacion_json"] = json.dumps(out.pop("validacion") or {}, ensure_ascii=False)
    if "faltantes" in out:
        out["faltantes_json"] = json.dumps(list(out.pop("faltantes") or []), ensure_ascii=False)
    return out


class RepoOperativosSQL:
    """Repositorio PostgreSQL / SQLAlchemy para ``documentos_operativos``."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def _siguiente_numero(self, conn: Any, tipo: str) -> str:
        pref = PREFIJO_POR_TIPO[tipo]
        ultimo = conn.execute(
            select(func.max(documentos_operativos.c.numero)).where(
                documentos_operativos.c.numero.like(f"{pref}-%")
            )
        ).scalar_one_or_none()
        n = 1
        if ultimo and "-" in str(ultimo):
            parte = str(ultimo).split("-", 1)[1]
            if parte.isdigit():
                n = int(parte) + 1
        return f"{pref}-{n:05d}"

    def crear(self, campos: dict[str, Any]) -> DocumentoOperativo:
        ahora = datetime.now()
        tipo = str(campos["tipo"])
        base = {
            **_serializar(campos),
            "creado_en": ahora,
            "actualizado_en": ahora,
        }
        for intento in range(8):
            try:
                with self._engine.begin() as conn:
                    if intento == 0 or "numero" not in base:
                        base["numero"] = self._siguiente_numero(conn, tipo)
                    else:
                        pref = PREFIJO_POR_TIPO[tipo]
                        parte = str(base["numero"]).split("-", 1)[1]
                        base["numero"] = f"{pref}-{int(parte) + 1:05d}"
                    row = conn.execute(
                        insert(documentos_operativos)
                        .values(**base)
                        .returning(documentos_operativos)
                    ).one()
                    return _a_doc(row)
            except IntegrityError:
                if intento == 7:
                    raise
        raise RuntimeError("No se pudo asignar consecutivo operativo.")  # pragma: no cover

    def obtener(self, documento_id: int) -> DocumentoOperativo | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(documentos_operativos).where(
                    documentos_operativos.c.documento_id == documento_id
                )
            ).one_or_none()
            return _a_doc(row) if row is not None else None

    def obtener_por_token(self, token_preview: str) -> DocumentoOperativo | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(documentos_operativos).where(
                    documentos_operativos.c.token_preview == token_preview.strip()
                )
            ).one_or_none()
            return _a_doc(row) if row is not None else None

    def obtener_por_captura(self, captura_id: int) -> DocumentoOperativo | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(documentos_operativos).where(
                    documentos_operativos.c.captura_id == captura_id
                )
            ).one_or_none()
            return _a_doc(row) if row is not None else None

    def actualizar(self, documento_id: int, campos: dict[str, Any]) -> DocumentoOperativo:
        upd = {**_serializar(campos), "actualizado_en": datetime.now()}
        with self._engine.begin() as conn:
            row = conn.execute(
                update(documentos_operativos)
                .where(documentos_operativos.c.documento_id == documento_id)
                .values(**upd)
                .returning(documentos_operativos)
            ).one()
            return _a_doc(row)

    def _conds_grupo(self, tipo: str | None, grupo: str | None) -> list[Any]:
        conds: list[Any] = []
        if tipo:
            conds.append(documentos_operativos.c.tipo == tipo)
        elif grupo == "cobranza":
            conds.append(documentos_operativos.c.tipo == "pago")
        elif grupo == "pagos_enviados":
            conds.append(documentos_operativos.c.tipo == "pago_proveedor")
        elif grupo == "compras_solo":
            conds.append(documentos_operativos.c.tipo.in_(("factura_proveedor", "gasto")))
        elif grupo == "compras":
            conds.append(documentos_operativos.c.tipo.in_(TIPOS_COMPRAS))
        return conds

    def listar(
        self,
        *,
        tipo: str | None = None,
        grupo: str | None = None,
        estado: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DocumentoOperativo], int, dict[str, int]]:
        conds = self._conds_grupo(tipo, grupo)
        if estado:
            conds.append(documentos_operativos.c.estado == estado)
        if q and q.strip():
            patron = f"%{q.strip()}%"
            conds.append(
                or_(
                    documentos_operativos.c.numero.ilike(patron),
                    documentos_operativos.c.contraparte_nombre.ilike(patron),
                    documentos_operativos.c.rif.ilike(patron),
                    documentos_operativos.c.referencia.ilike(patron),
                    documentos_operativos.c.numero_documento.ilike(patron),
                    documentos_operativos.c.concepto.ilike(patron),
                    documentos_operativos.c.odoo_ref.ilike(patron),
                )
            )
        with self._engine.connect() as conn:
            stmt = select(documentos_operativos)
            if conds:
                stmt = stmt.where(*conds)
            rows = conn.execute(
                stmt.order_by(documentos_operativos.c.documento_id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            count_stmt = select(func.count()).select_from(documentos_operativos)
            if conds:
                count_stmt = count_stmt.where(*conds)
            total = int(conn.execute(count_stmt).scalar_one())

            resumen_conds = self._conds_grupo(tipo, grupo)
            resumen_stmt = select(
                documentos_operativos.c.estado,
                func.count().label("n"),
            ).group_by(documentos_operativos.c.estado)
            if resumen_conds:
                resumen_stmt = resumen_stmt.where(*resumen_conds)
            por_estado = {str(r.estado): int(r.n) for r in conn.execute(resumen_stmt).all()}

        resumen = {
            "total": sum(por_estado.values()),
            "pendientes_aprobacion": por_estado.get(PENDIENTE_APROBACION, 0),
            "incompletos": por_estado.get(INCOMPLETO, 0) + por_estado.get(BORRADOR, 0),
            "enviados_odoo": por_estado.get(ENVIADO_ODOO, 0),
            "rechazados": por_estado.get(RECHAZADO, 0),
            "errores": por_estado.get(ERROR_ODOO, 0),
        }
        return [_a_doc(r) for r in rows], total, resumen

    def buscar_duplicados_referencia(
        self, tipo: str, referencia: str, *, excluir_id: int | None = None
    ) -> list[DocumentoOperativo]:
        ref_clean = referencia.strip().lower()
        if not ref_clean:
            return []
        with self._engine.connect() as conn:
            stmt = select(documentos_operativos).where(
                documentos_operativos.c.tipo == tipo,
                func.lower(func.trim(documentos_operativos.c.referencia)) == ref_clean,
                documentos_operativos.c.estado != RECHAZADO,
            )
            if excluir_id is not None:
                stmt = stmt.where(documentos_operativos.c.documento_id != excluir_id)
            rows = conn.execute(stmt.limit(5)).all()
        return [_a_doc(r) for r in rows]

    def buscar_duplicados_factura(
        self,
        proveedor: str,
        rif: str,
        numero_documento: str,
        *,
        excluir_id: int | None = None,
    ) -> list[DocumentoOperativo]:
        num_clean = numero_documento.strip().lower()
        if not num_clean:
            return []
        with self._engine.connect() as conn:
            stmt = select(documentos_operativos).where(
                documentos_operativos.c.tipo.in_(("factura_proveedor", "gasto")),
                func.lower(func.trim(documentos_operativos.c.numero_documento)) == num_clean,
                documentos_operativos.c.estado != RECHAZADO,
            )
            if excluir_id is not None:
                stmt = stmt.where(documentos_operativos.c.documento_id != excluir_id)
            candidatos = [_a_doc(r) for r in conn.execute(stmt.limit(20)).all()]

        rif_u = rif.strip().upper()
        prov_n = normalizar_nombre(proveedor)
        iguales: list[DocumentoOperativo] = []
        for c in candidatos:
            if (rif_u and c.rif and c.rif.strip().upper() == rif_u) or (
                prov_n
                and c.contraparte_nombre
                and normalizar_nombre(c.contraparte_nombre) == prov_n
            ):
                iguales.append(c)
        return iguales[:5]
