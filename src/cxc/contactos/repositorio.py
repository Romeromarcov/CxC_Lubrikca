"""Persistencia SQLAlchemy Core y en memoria para el Maestro Único de Contactos."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import Engine, insert, or_, select, update

from cxc.contactos.modelos import ContactoMaestro, ContactoNoEncontrado
from cxc.db.schema import contactos_maestro


def ahora_utc() -> datetime:
    """Timestamp UTC sin tzinfo compatible con DateTime(timezone=False)."""
    return datetime.now(UTC).replace(tzinfo=None)


def _dec_opt(val: Any) -> Decimal | None:
    if val in (None, "", False):
        return None
    try:
        d = Decimal(str(val))
        return None if d == Decimal("0") else d
    except Exception:
        return None


def _cargar_json_lista(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except ValueError:
        return []


def _fila_a_contacto(r: Any) -> ContactoMaestro:
    return ContactoMaestro(
        contacto_id=int(r.contacto_id),
        odoo_partner_id=int(r.odoo_partner_id) if r.odoo_partner_id is not None else None,
        origen=str(r.origen or "cxc"),
        rol=str(r.rol or "prospecto"),
        nombre=str(r.nombre or ""),
        nombre_normalizado=str(r.nombre_normalizado or ""),
        rif=str(r.rif or ""),
        rif_normalizado=str(r.rif_normalizado or ""),
        telefono=str(r.telefono or ""),
        telefono_normalizado=str(r.telefono_normalizado or ""),
        email=str(r.email or ""),
        direccion=str(r.direccion or ""),
        ciudad=str(r.ciudad or ""),
        latitud=Decimal(str(r.latitud)) if r.latitud is not None else None,
        longitud=Decimal(str(r.longitud)) if r.longitud is not None else None,
        plus_code=str(r.plus_code or ""),
        vendedor_email=str(r.vendedor_email or ""),
        estado_validacion=str(r.estado_validacion or "PENDIENTE"),
        completitud_pct=int(r.completitud_pct or 0),
        campos_faltantes=[str(x) for x in _cargar_json_lista(r.campos_faltantes_json)],
        duplicado_de_id=int(r.duplicado_de_id) if r.duplicado_de_id is not None else None,
        posibles_duplicados=[
            x for x in _cargar_json_lista(r.posibles_duplicados_json) if isinstance(x, dict)
        ],
        es_borrador_telegram=bool(r.es_borrador_telegram),
        captura_id=int(r.captura_id) if r.captura_id is not None else None,
        creado_por=str(r.creado_por or ""),
        validado_por=str(r.validado_por or ""),
        notas=str(r.notas or ""),
        activo=bool(r.activo),
        creado_en=r.creado_en,
        actualizado_en=r.actualizado_en,
    )


def _contacto_a_valores(c: ContactoMaestro, *, incluir_creado_en: bool) -> dict[str, Any]:
    ts = ahora_utc()
    vals: dict[str, Any] = {
        "odoo_partner_id": c.odoo_partner_id,
        "origen": c.origen,
        "rol": c.rol,
        "nombre": c.nombre,
        "nombre_normalizado": c.nombre_normalizado,
        "rif": c.rif,
        "rif_normalizado": c.rif_normalizado,
        "telefono": c.telefono,
        "telefono_normalizado": c.telefono_normalizado,
        "email": c.email,
        "direccion": c.direccion,
        "ciudad": c.ciudad,
        "latitud": _dec_opt(c.latitud),
        "longitud": _dec_opt(c.longitud),
        "plus_code": c.plus_code,
        "vendedor_email": c.vendedor_email,
        "estado_validacion": c.estado_validacion,
        "completitud_pct": int(c.completitud_pct),
        "campos_faltantes_json": json.dumps(list(c.campos_faltantes), ensure_ascii=False),
        "duplicado_de_id": c.duplicado_de_id,
        "posibles_duplicados_json": json.dumps(list(c.posibles_duplicados), ensure_ascii=False),
        "es_borrador_telegram": bool(c.es_borrador_telegram),
        "captura_id": c.captura_id,
        "creado_por": c.creado_por or "sistema",
        "validado_por": c.validado_por or "",
        "notas": c.notas or "",
        "activo": bool(c.activo),
        "actualizado_en": c.actualizado_en or ts,
    }
    if incluir_creado_en:
        vals["creado_en"] = c.creado_en or ts
    return vals


def _calcular_resumen_lista(items: Iterable[ContactoMaestro]) -> dict[str, int]:
    total = activos = en_odoo = origen_bot = origen_cxc = 0
    prospectos = clientes = proveedores = ambos = 0
    incompletos = con_duplicados = pendientes_validacion = duplicados_resueltos = 0
    for c in items:
        total += 1
        if c.estado_validacion == "DUPLICADO":
            duplicados_resueltos += 1
        if not c.activo or c.estado_validacion in ("DUPLICADO", "DESCARTADO"):
            continue
        activos += 1
        if c.odoo_partner_id is not None:
            en_odoo += 1
        if c.origen == "bot":
            origen_bot += 1
        elif c.origen == "cxc":
            origen_cxc += 1
        if c.rol == "prospecto":
            prospectos += 1
        elif c.rol == "cliente":
            clientes += 1
        elif c.rol == "proveedor":
            proveedores += 1
        elif c.rol == "ambos":
            ambos += 1
            clientes += 1
            proveedores += 1
        if int(c.completitud_pct) < 80 or not c.listo_para_odoo:
            incompletos += 1
        if c.posibles_duplicados:
            con_duplicados += 1
        if c.estado_validacion == "PENDIENTE":
            pendientes_validacion += 1
    return {
        "total": total,
        "activos": activos,
        "en_odoo": en_odoo,
        "origen_bot": origen_bot,
        "origen_cxc": origen_cxc,
        "locales_sin_odoo": activos - en_odoo,
        "prospectos": prospectos,
        "clientes": clientes,
        "proveedores": proveedores,
        "ambos": ambos,
        "incompletos": incompletos,
        "con_duplicados": con_duplicados,
        "pendientes_validacion": pendientes_validacion,
        "duplicados_resueltos": duplicados_resueltos,
    }


def _cumple_filtros(
    c: ContactoMaestro,
    *,
    origen: str | None,
    rol: str | None,
    estado_validacion: str | None,
    solo_incompletos: bool,
    solo_duplicados: bool,
    q: str | None,
    incluir_inactivos: bool,
) -> bool:
    if not incluir_inactivos and (
        not c.activo or c.estado_validacion in ("DUPLICADO", "DESCARTADO")
    ):
        return False
    if origen and c.origen != origen:
        return False
    if rol:
        if rol == "cliente" and c.rol not in ("cliente", "ambos"):
            return False
        if rol == "proveedor" and c.rol not in ("proveedor", "ambos"):
            return False
        if rol in ("prospecto", "ambos") and c.rol != rol:
            return False
    if estado_validacion and c.estado_validacion != estado_validacion:
        return False
    if solo_incompletos and (int(c.completitud_pct) >= 80 and c.listo_para_odoo):
        return False
    if solo_duplicados and not c.posibles_duplicados:
        return False
    if q:
        termino = q.strip().lower()
        pajar = " ".join(
            [
                c.nombre.lower(),
                c.rif.lower(),
                c.telefono.lower(),
                c.email.lower(),
                c.ciudad.lower(),
                c.vendedor_email.lower(),
                str(c.odoo_partner_id or ""),
            ]
        )
        if termino not in pajar:
            return False
    return True


class RepoContactos(Protocol):
    def obtener(self, contacto_id: int) -> ContactoMaestro | None: ...

    def obtener_por_odoo_id(self, odoo_partner_id: int) -> ContactoMaestro | None: ...

    def obtener_por_captura_id(self, captura_id: int) -> ContactoMaestro | None: ...

    def listar_activos(self) -> list[ContactoMaestro]: ...

    def listar(
        self,
        *,
        origen: str | None = None,
        rol: str | None = None,
        estado_validacion: str | None = None,
        solo_incompletos: bool = False,
        solo_duplicados: bool = False,
        q: str | None = None,
        incluir_inactivos: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[ContactoMaestro], int, dict[str, int]]: ...

    def crear(self, contacto: ContactoMaestro) -> ContactoMaestro: ...

    def actualizar(self, contacto: ContactoMaestro) -> ContactoMaestro: ...

    def guardar_lote(
        self,
        nuevos: Sequence[ContactoMaestro],
        actualizados: Sequence[ContactoMaestro],
    ) -> tuple[int, int]: ...

    def resumen(self) -> dict[str, int]: ...


class RepoContactosSQL:
    """Implementación PostgreSQL/SQLAlchemy Core del repositorio de contactos."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def obtener(self, contacto_id: int) -> ContactoMaestro | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(contactos_maestro).where(contactos_maestro.c.contacto_id == contacto_id)
            ).first()
            return _fila_a_contacto(row) if row else None

    def obtener_por_odoo_id(self, odoo_partner_id: int) -> ContactoMaestro | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(contactos_maestro).where(
                    contactos_maestro.c.odoo_partner_id == odoo_partner_id
                )
            ).first()
            return _fila_a_contacto(row) if row else None

    def obtener_por_captura_id(self, captura_id: int) -> ContactoMaestro | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(contactos_maestro).where(contactos_maestro.c.captura_id == captura_id)
            ).first()
            return _fila_a_contacto(row) if row else None

    def listar_activos(self) -> list[ContactoMaestro]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(contactos_maestro)
                .where(
                    contactos_maestro.c.activo.is_(True),
                    contactos_maestro.c.estado_validacion.notin_(("DUPLICADO", "DESCARTADO")),
                )
                .order_by(contactos_maestro.c.contacto_id.desc())
            ).all()
            return [_fila_a_contacto(r) for r in rows]

    def _todos(self) -> list[ContactoMaestro]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(contactos_maestro).order_by(
                    contactos_maestro.c.actualizado_en.desc(),
                    contactos_maestro.c.contacto_id.desc(),
                )
            ).all()
            return [_fila_a_contacto(r) for r in rows]

    def resumen(self) -> dict[str, int]:
        return _calcular_resumen_lista(self._todos())

    def listar(
        self,
        *,
        origen: str | None = None,
        rol: str | None = None,
        estado_validacion: str | None = None,
        solo_incompletos: bool = False,
        solo_duplicados: bool = False,
        q: str | None = None,
        incluir_inactivos: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[ContactoMaestro], int, dict[str, int]]:
        todos = self._todos()
        resumen = _calcular_resumen_lista(todos)

        stmt = select(contactos_maestro)
        if not incluir_inactivos:
            stmt = stmt.where(
                contactos_maestro.c.activo.is_(True),
                contactos_maestro.c.estado_validacion.notin_(("DUPLICADO", "DESCARTADO")),
            )
        if origen:
            stmt = stmt.where(contactos_maestro.c.origen == origen)
        if rol:
            if rol == "cliente":
                stmt = stmt.where(contactos_maestro.c.rol.in_(("cliente", "ambos")))
            elif rol == "proveedor":
                stmt = stmt.where(contactos_maestro.c.rol.in_(("proveedor", "ambos")))
            else:
                stmt = stmt.where(contactos_maestro.c.rol == rol)
        if estado_validacion:
            stmt = stmt.where(contactos_maestro.c.estado_validacion == estado_validacion)
        if solo_incompletos:
            stmt = stmt.where(contactos_maestro.c.completitud_pct < 80)
        if solo_duplicados:
            stmt = stmt.where(contactos_maestro.c.posibles_duplicados_json != "[]")
        if q:
            patron = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(
                    contactos_maestro.c.nombre.ilike(patron),
                    contactos_maestro.c.rif.ilike(patron),
                    contactos_maestro.c.telefono.ilike(patron),
                    contactos_maestro.c.email.ilike(patron),
                    contactos_maestro.c.ciudad.ilike(patron),
                    contactos_maestro.c.vendedor_email.ilike(patron),
                )
            )

        with self._engine.connect() as conn:
            rows = conn.execute(
                stmt.order_by(
                    contactos_maestro.c.actualizado_en.desc(),
                    contactos_maestro.c.contacto_id.desc(),
                )
            ).all()
        filtrados = [_fila_a_contacto(r) for r in rows]
        # Refina filtro de solo_incompletos para incluir listo_para_odoo=False
        if solo_incompletos:
            filtrados = [
                c for c in filtrados if int(c.completitud_pct) < 80 or not c.listo_para_odoo
            ]
        # Prioriza contactos pendientes o con duplicados arriba cuando no hay orden específico
        filtrados.sort(
            key=lambda c: (
                0 if c.posibles_duplicados else (1 if c.estado_validacion == "PENDIENTE" else 2),
                -(c.contacto_id or 0),
            )
        )
        total = len(filtrados)
        lim = max(1, min(limit, 500))
        off = max(0, offset)
        return filtrados[off : off + lim], total, resumen

    def crear(self, contacto: ContactoMaestro) -> ContactoMaestro:
        vals = _contacto_a_valores(contacto, incluir_creado_en=True)
        with self._engine.begin() as conn:
            res = conn.execute(insert(contactos_maestro).values(**vals))
            nuevo_id = int(res.inserted_primary_key[0])
            row = conn.execute(
                select(contactos_maestro).where(contactos_maestro.c.contacto_id == nuevo_id)
            ).first()
            assert row is not None
            return _fila_a_contacto(row)

    def actualizar(self, contacto: ContactoMaestro) -> ContactoMaestro:
        if contacto.contacto_id is None:
            raise ContactoNoEncontrado("No se puede actualizar un contacto sin contacto_id")
        vals = _contacto_a_valores(contacto, incluir_creado_en=False)
        with self._engine.begin() as conn:
            res = conn.execute(
                update(contactos_maestro)
                .where(contactos_maestro.c.contacto_id == contacto.contacto_id)
                .values(**vals)
            )
            if res.rowcount == 0:
                raise ContactoNoEncontrado(f"Contacto #{contacto.contacto_id} no encontrado")
            row = conn.execute(
                select(contactos_maestro).where(
                    contactos_maestro.c.contacto_id == contacto.contacto_id
                )
            ).first()
            assert row is not None
            return _fila_a_contacto(row)

    def guardar_lote(
        self,
        nuevos: Sequence[ContactoMaestro],
        actualizados: Sequence[ContactoMaestro],
    ) -> tuple[int, int]:
        if not nuevos and not actualizados:
            return 0, 0
        creados = 0
        modificados = 0
        with self._engine.begin() as conn:
            if nuevos:
                filas = [_contacto_a_valores(c, incluir_creado_en=True) for c in nuevos]
                conn.execute(insert(contactos_maestro), filas)
                creados = len(filas)
            for c in actualizados:
                if c.contacto_id is None:
                    continue
                vals = _contacto_a_valores(c, incluir_creado_en=False)
                conn.execute(
                    update(contactos_maestro)
                    .where(contactos_maestro.c.contacto_id == c.contacto_id)
                    .values(**vals)
                )
                modificados += 1
        return creados, modificados


class RepoContactosMemoria:
    """Repositorio en memoria con la misma interfaz que ``RepoContactosSQL`` para tests."""

    def __init__(self) -> None:
        self._datos: dict[int, ContactoMaestro] = {}
        self._seq = 0

    def obtener(self, contacto_id: int) -> ContactoMaestro | None:
        return self._datos.get(contacto_id)

    def obtener_por_odoo_id(self, odoo_partner_id: int) -> ContactoMaestro | None:
        for c in self._datos.values():
            if c.odoo_partner_id == odoo_partner_id:
                return c
        return None

    def obtener_por_captura_id(self, captura_id: int) -> ContactoMaestro | None:
        for c in self._datos.values():
            if c.captura_id == captura_id:
                return c
        return None

    def listar_activos(self) -> list[ContactoMaestro]:
        return [
            c
            for c in self._datos.values()
            if c.activo and c.estado_validacion not in ("DUPLICADO", "DESCARTADO")
        ]

    def resumen(self) -> dict[str, int]:
        return _calcular_resumen_lista(self._datos.values())

    def listar(
        self,
        *,
        origen: str | None = None,
        rol: str | None = None,
        estado_validacion: str | None = None,
        solo_incompletos: bool = False,
        solo_duplicados: bool = False,
        q: str | None = None,
        incluir_inactivos: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[ContactoMaestro], int, dict[str, int]]:
        resumen = self.resumen()
        filtrados = [
            c
            for c in self._datos.values()
            if _cumple_filtros(
                c,
                origen=origen,
                rol=rol,
                estado_validacion=estado_validacion,
                solo_incompletos=solo_incompletos,
                solo_duplicados=solo_duplicados,
                q=q,
                incluir_inactivos=incluir_inactivos,
            )
        ]
        filtrados.sort(
            key=lambda c: (
                0 if c.posibles_duplicados else (1 if c.estado_validacion == "PENDIENTE" else 2),
                -(c.contacto_id or 0),
            )
        )
        total = len(filtrados)
        lim = max(1, min(limit, 500))
        off = max(0, offset)
        return filtrados[off : off + lim], total, resumen

    def crear(self, contacto: ContactoMaestro) -> ContactoMaestro:
        self._seq += 1
        ts = ahora_utc()
        contacto.contacto_id = self._seq
        contacto.creado_en = contacto.creado_en or ts
        contacto.actualizado_en = contacto.actualizado_en or ts
        self._datos[self._seq] = contacto
        return contacto

    def actualizar(self, contacto: ContactoMaestro) -> ContactoMaestro:
        if contacto.contacto_id is None or contacto.contacto_id not in self._datos:
            raise ContactoNoEncontrado(f"Contacto #{contacto.contacto_id} no encontrado")
        contacto.actualizado_en = ahora_utc()
        self._datos[contacto.contacto_id] = contacto
        return contacto

    def guardar_lote(
        self,
        nuevos: Sequence[ContactoMaestro],
        actualizados: Sequence[ContactoMaestro],
    ) -> tuple[int, int]:
        for c in nuevos:
            self.crear(c)
        for c in actualizados:
            self.actualizar(c)
        return len(nuevos), len(actualizados)
