"""Servicio del Maestro Único de Contactos (Odoo + Bots + CxC) y Anti-Duplicados."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import SQLAlchemyError

from cxc.contactos.modelos import (
    ESTADOS_VALIDACION,
    ORIGENES_CONTACTO,
    ROLES_CONTACTO,
    ContactoDuplicadoError,
    ContactoInvalido,
    ContactoMaestro,
    ContactoNoEncontrado,
)
from cxc.contactos.motor import (
    buscar_posibles_duplicados,
    detectar_grupos_duplicados,
    evaluar_completitud,
    normalizar_email,
    normalizar_nombre,
    normalizar_rif_canonico,
    normalizar_telefono,
)
from cxc.contactos.repositorio import RepoContactos
from cxc.db.schema import capturas_bot, documentos_comerciales, documentos_operativos

logger = logging.getLogger("cxc.contactos.servicio")


def _extraer_gps(datos: Mapping[str, Any]) -> tuple[Decimal | None, Decimal | None, str]:
    ub = datos.get("ubicacion")
    lat_raw = (
        ub.get("latitud") if isinstance(ub, Mapping) else datos.get("latitud", datos.get("lat"))
    )
    lon_raw = (
        ub.get("longitud") if isinstance(ub, Mapping) else datos.get("longitud", datos.get("lon"))
    )
    pc_raw = ub.get("plus_code") if isinstance(ub, Mapping) else datos.get("plus_code")
    lat: Decimal | None = None
    lon: Decimal | None = None
    if lat_raw not in (None, "", False) and lon_raw not in (None, "", False):
        try:
            d_lat = Decimal(str(lat_raw))
            d_lon = Decimal(str(lon_raw))
            if not (d_lat == Decimal("0") and d_lon == Decimal("0")):
                lat, lon = d_lat, d_lon
        except Exception:
            pass
    plus_code = str(pc_raw or "").strip()
    return lat, lon, plus_code


def _odoo_a_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return dict(item)
    if hasattr(item, "a_dict"):
        d = item.a_dict()
        if isinstance(d, dict):
            return d
    return {
        "id": getattr(item, "id", None),
        "nombre": getattr(item, "nombre", ""),
        "rif": getattr(item, "rif", ""),
        "email": getattr(item, "email", ""),
        "telefono": getattr(item, "telefono", ""),
        "ciudad": getattr(item, "ciudad", ""),
        "direccion": getattr(item, "direccion", ""),
        "clasificacion": getattr(item, "clasificacion", "prospecto"),
        "es_borrador_telegram": getattr(item, "es_borrador_telegram", False),
        "vendedor": getattr(item, "vendedor", ""),
        "notas": getattr(item, "notas", ""),
        "plus_code": getattr(item, "plus_code", ""),
        "latitud": getattr(item, "latitud", None),
        "longitud": getattr(item, "longitud", None),
    }


def _recalcular_normalizados_y_completitud(c: ContactoMaestro) -> None:
    c.nombre = c.nombre.strip()
    c.rif = c.rif.strip()
    c.telefono = c.telefono.strip()
    c.email = c.email.strip()
    c.direccion = c.direccion.strip()
    c.ciudad = c.ciudad.strip()
    c.nombre_normalizado = normalizar_nombre(c.nombre)
    c.rif_normalizado = normalizar_rif_canonico(c.rif)
    c.telefono_normalizado = normalizar_telefono(c.telefono)
    pct, faltantes, _ = evaluar_completitud(c)
    c.completitud_pct = pct
    c.campos_faltantes = faltantes


class ServicioContactos:
    """Orquesta sincronización Odoo + Bots, validación anti-duplicados, envío a Odoo y fusión."""

    def __init__(
        self,
        repo: RepoContactos,
        *,
        escritor_factory: Callable[[], Any] | None = None,
        engine: Engine | None = None,
    ) -> None:
        self.repo = repo
        self._escritor_factory = escritor_factory
        self._engine = engine

    def _universo_con_fallback(
        self, contactos_odoo_fallback: Sequence[Any] | None = None
    ) -> list[ContactoMaestro]:
        activos = self.repo.listar_activos()
        if activos or not contactos_odoo_fallback:
            return activos
        temporales: list[ContactoMaestro] = []
        for idx, raw in enumerate(contactos_odoo_fallback, start=1):
            d = _odoo_a_dict(raw)
            lat, lon, pc = _extraer_gps(d)
            c = ContactoMaestro(
                contacto_id=None,
                odoo_partner_id=int(d["id"]) if d.get("id") else None,
                origen="odoo",
                rol=str(d.get("clasificacion") or "prospecto"),
                nombre=str(d.get("nombre") or f"Contacto #{idx}"),
                rif=str(d.get("rif") or ""),
                telefono=str(d.get("telefono") or ""),
                email=str(d.get("email") or ""),
                direccion=str(d.get("direccion") or ""),
                ciudad=str(d.get("ciudad") or ""),
                latitud=lat,
                longitud=lon,
                plus_code=pc,
                estado_validacion="ENVIADO_ODOO",
            )
            _recalcular_normalizados_y_completitud(c)
            temporales.append(c)
        return temporales

    def verificar_duplicados(
        self,
        datos: ContactoMaestro | Mapping[str, Any],
        *,
        excluir_id: int | None = None,
        excluir_odoo_id: int | None = None,
        contactos_odoo_fallback: Sequence[Any] | None = None,
    ) -> dict[str, Any]:
        universo = self._universo_con_fallback(contactos_odoo_fallback)
        candidatos = buscar_posibles_duplicados(
            datos,
            universo,
            excluir_id=excluir_id,
            excluir_odoo_id=excluir_odoo_id,
        )
        pct, faltantes, listo = evaluar_completitud(datos)
        return {
            "duplicado_bloqueante": any(bool(c.get("bloqueante")) for c in candidatos),
            "candidatos": candidatos,
            "completitud": {
                "pct": pct,
                "campos_faltantes": faltantes,
                "listo_para_odoo": listo,
            },
        }

    def _recalcular_alertas_duplicados(self) -> int:
        activos = self.repo.listar_activos()
        pares = detectar_grupos_duplicados(activos, max_pares=300)
        alertas_por_id: dict[int, list[dict[str, Any]]] = defaultdict(list)

        for par in pares:
            m = par["maestro"]
            d = par["duplicado"]
            m_id = m.get("contacto_id")
            d_id = d.get("contacto_id")
            if isinstance(m_id, int) and isinstance(d_id, int):
                base_info = {
                    "score": par["score"],
                    "bloqueante": par["bloqueante"],
                    "nivel": par["nivel"],
                    "motivos": par["motivos"],
                }
                alertas_por_id[m_id].append(
                    {
                        **base_info,
                        "contacto_id": d_id,
                        "odoo_partner_id": d.get("odoo_partner_id"),
                        "nombre": d.get("nombre"),
                        "rif": d.get("rif"),
                        "origen": d.get("origen"),
                    }
                )
                alertas_por_id[d_id].append(
                    {
                        **base_info,
                        "contacto_id": m_id,
                        "odoo_partner_id": m.get("odoo_partner_id"),
                        "nombre": m.get("nombre"),
                        "rif": m.get("rif"),
                        "origen": m.get("origen"),
                    }
                )

        cambiados: list[ContactoMaestro] = []
        for c in activos:
            assert c.contacto_id is not None
            nuevas = alertas_por_id.get(c.contacto_id, [])[:5]
            if nuevas != c.posibles_duplicados:
                c.posibles_duplicados = nuevas
                cambiados.append(c)
        if cambiados:
            self.repo.guardar_lote([], cambiados)
        return len(pares)

    def sincronizar_desde_odoo(self, contactos_odoo: Sequence[Any]) -> dict[str, int]:
        """Sincroniza ``res.partner`` de Odoo hacia ``contactos_maestro`` preservando GPS local."""
        actuales = self.repo.listar_activos()
        por_odoo: dict[int, ContactoMaestro] = {
            c.odoo_partner_id: c for c in actuales if c.odoo_partner_id is not None
        }
        por_rif_sin_odoo: dict[str, ContactoMaestro] = {}
        for c in actuales:
            if c.odoo_partner_id is None and len(c.rif_normalizado) >= 6:
                por_rif_sin_odoo.setdefault(c.rif_normalizado, c)
        # Incluye inactivos duplicados/descartados por su odoo_partner_id para respetar unique key
        todos, _, _ = self.repo.listar(incluir_inactivos=True, limit=5000, offset=0)
        for c in todos:
            if c.odoo_partner_id is not None and c.odoo_partner_id not in por_odoo:
                por_odoo[c.odoo_partner_id] = c

        nuevos: list[ContactoMaestro] = []
        actualizados: list[ContactoMaestro] = []

        for raw in contactos_odoo:
            d = _odoo_a_dict(raw)
            pid = d.get("id")
            if not pid:
                continue
            partner_id = int(pid)
            rol = str(d.get("clasificacion") or "prospecto")
            if rol not in ROLES_CONTACTO:
                rol = "prospecto"
            es_bot = bool(d.get("es_borrador_telegram"))
            lat_o, lon_o, pc_o = _extraer_gps(d)

            existente = por_odoo.get(partner_id)
            if existente is None:
                rn_odoo = normalizar_rif_canonico(str(d.get("rif") or ""))
                if len(rn_odoo) >= 6 and rn_odoo in por_rif_sin_odoo:
                    existente = por_rif_sin_odoo.pop(rn_odoo)
                    por_odoo[partner_id] = existente

            if existente is None:
                c = ContactoMaestro(
                    contacto_id=None,
                    odoo_partner_id=partner_id,
                    origen="bot" if es_bot else "odoo",
                    rol=rol,
                    nombre=str(d.get("nombre") or f"Contacto #{partner_id}"),
                    rif=str(d.get("rif") or ""),
                    telefono=str(d.get("telefono") or ""),
                    email=str(d.get("email") or ""),
                    direccion=str(d.get("direccion") or ""),
                    ciudad=str(d.get("ciudad") or ""),
                    latitud=lat_o,
                    longitud=lon_o,
                    plus_code=pc_o,
                    vendedor_email=str(d.get("vendedor") or ""),
                    es_borrador_telegram=es_bot,
                    creado_por="odoo_sync",
                    notas=str(d.get("notas") or ""),
                    activo=True,
                )
                _recalcular_normalizados_y_completitud(c)
                c.estado_validacion = (
                    "PENDIENTE" if (es_bot and not c.listo_para_odoo) else "ENVIADO_ODOO"
                )
                nuevos.append(c)
            else:
                if not existente.activo or existente.estado_validacion in (
                    "DUPLICADO",
                    "DESCARTADO",
                ):
                    continue
                antes = (
                    existente.odoo_partner_id,
                    existente.estado_validacion,
                    existente.nombre,
                    existente.rif,
                    existente.telefono,
                    existente.email,
                    existente.ciudad,
                    existente.rol,
                    existente.vendedor_email,
                    existente.es_borrador_telegram,
                )
                if existente.odoo_partner_id is None:
                    existente.odoo_partner_id = partner_id
                    existente.estado_validacion = "ENVIADO_ODOO"
                existente.nombre = str(d.get("nombre") or existente.nombre)
                if d.get("rif"):
                    existente.rif = str(d["rif"])
                if d.get("telefono"):
                    existente.telefono = str(d["telefono"])
                if d.get("email"):
                    existente.email = str(d["email"])
                if d.get("ciudad"):
                    existente.ciudad = str(d["ciudad"])
                if d.get("direccion") and not existente.direccion:
                    existente.direccion = str(d["direccion"])
                if lat_o is not None and lon_o is not None and existente.latitud is None:
                    existente.latitud = lat_o
                    existente.longitud = lon_o
                if pc_o and not existente.plus_code:
                    existente.plus_code = pc_o
                if d.get("vendedor"):
                    existente.vendedor_email = str(d["vendedor"])
                existente.rol = rol
                existente.es_borrador_telegram = existente.es_borrador_telegram or es_bot
                if existente.estado_validacion == "PENDIENTE" and (
                    not es_bot or existente.odoo_partner_id is not None
                ):
                    existente.estado_validacion = "ENVIADO_ODOO"
                _recalcular_normalizados_y_completitud(existente)
                despues = (
                    existente.odoo_partner_id,
                    existente.estado_validacion,
                    existente.nombre,
                    existente.rif,
                    existente.telefono,
                    existente.email,
                    existente.ciudad,
                    existente.rol,
                    existente.vendedor_email,
                    existente.es_borrador_telegram,
                )
                if antes != despues:
                    actualizados.append(existente)

        creados, modificados = self.repo.guardar_lote(nuevos, actualizados)
        pares = self._recalcular_alertas_duplicados()
        return {"creados": creados, "actualizados": modificados, "pares_duplicados": pares}

    def absorber_locales(self) -> int:
        """Importa prospectos/proveedores de capturas y documentos locales aún no en el maestro."""
        if self._engine is None:
            return 0
        existentes = self.repo.listar_activos()
        por_cap = {c.captura_id for c in existentes if c.captura_id is not None}
        por_rif = {c.rif_normalizado for c in existentes if len(c.rif_normalizado) >= 6}
        por_nom = {c.nombre_normalizado for c in existentes if len(c.nombre_normalizado) >= 4}
        por_odoo = {c.odoo_partner_id for c in existentes if c.odoo_partner_id is not None}

        importados = 0
        candidatos: list[ContactoMaestro] = []

        def _ya_existe(cap_id: int | None, pid: int | None, rif: str, nom: str) -> bool:
            if cap_id is not None and cap_id in por_cap:
                return True
            if pid is not None and pid in por_odoo:
                return True
            rn = normalizar_rif_canonico(rif)
            if len(rn) >= 6 and rn in por_rif:
                return True
            nn = normalizar_nombre(nom)
            return bool(len(nn) >= 4 and nn in por_nom)

        def _marcar_visto(c: ContactoMaestro) -> None:
            if c.captura_id is not None:
                por_cap.add(c.captura_id)
            if c.odoo_partner_id is not None:
                por_odoo.add(c.odoo_partner_id)
            if len(c.rif_normalizado) >= 6:
                por_rif.add(c.rif_normalizado)
            if len(c.nombre_normalizado) >= 4:
                por_nom.add(c.nombre_normalizado)

        try:
            with self._engine.connect() as conn:
                rows_cap = conn.execute(
                    select(capturas_bot).where(capturas_bot.c.kind.in_(("contacto", "proveedor")))
                ).all()
                for r in rows_cap:
                    cap_id = int(r.captura_id)
                    p = json.loads(r.payload_json or "{}") if r.payload_json else {}
                    res = json.loads(r.resultado_json or "{}") if r.resultado_json else {}
                    fuente = json.loads(r.fuente_json or "{}") if r.fuente_json else {}
                    nom = str(
                        p.get("nombre") or p.get("cliente") or p.get("proveedor") or ""
                    ).strip()
                    rif = str(p.get("rif") or "").strip()
                    pid = res.get("partner_id") or p.get("partner_odoo_id")
                    pid_int = int(pid) if pid else None
                    if not nom or _ya_existe(cap_id, pid_int, rif, nom):
                        continue
                    lat, lon, pc = _extraer_gps(p)
                    c = ContactoMaestro(
                        contacto_id=None,
                        odoo_partner_id=pid_int,
                        origen="bot",
                        rol="proveedor" if r.kind == "proveedor" else "prospecto",
                        nombre=nom,
                        rif=rif,
                        telefono=str(p.get("telefono") or ""),
                        email=str(p.get("email") or ""),
                        direccion=str(p.get("direccion") or ""),
                        latitud=lat,
                        longitud=lon,
                        plus_code=pc,
                        vendedor_email=str(fuente.get("vendedor_email") or ""),
                        es_borrador_telegram=True,
                        captura_id=cap_id,
                        creado_por=str(fuente.get("vendedor_email") or "bot"),
                        notas=str(p.get("notas") or ""),
                        estado_validacion="ENVIADO_ODOO" if pid_int else "PENDIENTE",
                    )
                    _recalcular_normalizados_y_completitud(c)
                    _marcar_visto(c)
                    candidatos.append(c)

                rows_dc = conn.execute(select(documentos_comerciales)).all()
                for r in rows_dc:
                    nom = str(r.cliente_nombre or "").strip()
                    rif = str(r.rif or "").strip()
                    pid_int = int(r.partner_odoo_id) if r.partner_odoo_id else None
                    if not nom or _ya_existe(None, pid_int, rif, nom):
                        continue
                    c = ContactoMaestro(
                        contacto_id=None,
                        odoo_partner_id=pid_int,
                        origen=str(r.origen or "cxc")
                        if str(r.origen or "") in ORIGENES_CONTACTO
                        else "cxc",
                        rol=str(r.tipo_contacto or "prospecto")
                        if str(r.tipo_contacto or "") in ROLES_CONTACTO
                        else "prospecto",
                        nombre=nom,
                        rif=rif,
                        telefono=str(r.telefono or ""),
                        email=str(r.email or ""),
                        direccion=str(r.direccion or ""),
                        latitud=Decimal(str(r.latitud)) if r.latitud is not None else None,
                        longitud=Decimal(str(r.longitud)) if r.longitud is not None else None,
                        plus_code=str(r.plus_code or ""),
                        vendedor_email=str(r.vendedor_email or ""),
                        creado_por=str(r.creado_por or "cxc"),
                        estado_validacion="ENVIADO_ODOO" if pid_int else "PENDIENTE",
                    )
                    _recalcular_normalizados_y_completitud(c)
                    _marcar_visto(c)
                    candidatos.append(c)

                rows_op = conn.execute(select(documentos_operativos)).all()
                for r in rows_op:
                    nom = str(r.contraparte_nombre or "").strip()
                    rif = str(r.rif or "").strip()
                    pid_int = int(r.partner_odoo_id) if r.partner_odoo_id else None
                    if not nom or _ya_existe(None, pid_int, rif, nom):
                        continue
                    rol_op = (
                        "proveedor"
                        if str(r.tipo) in ("pago_proveedor", "factura_proveedor", "gasto")
                        else "cliente"
                    )
                    c = ContactoMaestro(
                        contacto_id=None,
                        odoo_partner_id=pid_int,
                        origen=str(r.origen or "cxc")
                        if str(r.origen or "") in ORIGENES_CONTACTO
                        else "cxc",
                        rol=rol_op,
                        nombre=nom,
                        rif=rif,
                        telefono=str(r.proveedor_telefono or ""),
                        email=str(r.proveedor_email or ""),
                        direccion=str(r.proveedor_direccion or ""),
                        vendedor_email=str(r.vendedor_email or ""),
                        creado_por=str(r.creado_por or "cxc"),
                        estado_validacion="ENVIADO_ODOO" if pid_int else "PENDIENTE",
                    )
                    _recalcular_normalizados_y_completitud(c)
                    _marcar_visto(c)
                    candidatos.append(c)
        except SQLAlchemyError as exc:
            logger.debug("absorber_locales omitido por esquema parcial: %s", exc)

        if candidatos:
            importados, _ = self.repo.guardar_lote(candidatos, [])
            self._recalcular_alertas_duplicados()
        return importados

    def crear(
        self,
        datos: Mapping[str, Any],
        *,
        origen: str = "cxc",
        actor: str = "sistema",
        forzar_duplicado: bool = False,
        captura_id: int | None = None,
    ) -> ContactoMaestro:
        nombre = str(datos.get("nombre") or "").strip()
        if not nombre:
            raise ContactoInvalido("El nombre o razón social del contacto es obligatorio.")
        rol = str(datos.get("rol") or "prospecto").strip().lower()
        if rol not in ROLES_CONTACTO:
            raise ContactoInvalido(f"rol debe ser uno de: {', '.join(ROLES_CONTACTO)}")
        ori = origen if origen in ORIGENES_CONTACTO else "cxc"

        chequeo = self.verificar_duplicados(datos)
        if chequeo["duplicado_bloqueante"] and not forzar_duplicado:
            raise ContactoDuplicadoError(
                "Ya existe un contacto registrado con este RIF en el Maestro Único.",
                duplicados=chequeo["candidatos"],
            )

        lat, lon, pc = _extraer_gps(datos)
        pid_raw = datos.get("odoo_partner_id") or datos.get("partner_odoo_id")
        pid = int(pid_raw) if pid_raw else None
        es_bot = bool(datos.get("es_borrador_telegram")) or ori == "bot"

        c = ContactoMaestro(
            contacto_id=None,
            odoo_partner_id=pid,
            origen=ori,
            rol=rol,
            nombre=nombre,
            rif=str(datos.get("rif") or "").strip(),
            telefono=str(datos.get("telefono") or "").strip(),
            email=normalizar_email(str(datos.get("email") or ""))
            or str(datos.get("email") or "").strip(),
            direccion=str(datos.get("direccion") or "").strip(),
            ciudad=str(datos.get("ciudad") or "").strip(),
            latitud=lat,
            longitud=lon,
            plus_code=pc,
            vendedor_email=str(datos.get("vendedor_email") or "").strip(),
            posibles_duplicados=list(chequeo["candidatos"]),
            es_borrador_telegram=es_bot,
            captura_id=captura_id,
            creado_por=actor,
            notas=str(datos.get("notas") or "").strip(),
            activo=True,
        )
        _recalcular_normalizados_y_completitud(c)
        if pid is not None:
            c.estado_validacion = "ENVIADO_ODOO"
        elif c.listo_para_odoo and not c.posibles_duplicados:
            c.estado_validacion = "VALIDADO"
            c.validado_por = actor
        else:
            c.estado_validacion = "PENDIENTE"

        creado = self.repo.crear(c)
        if creado.posibles_duplicados:
            self._recalcular_alertas_duplicados()
            reloaded = self.repo.obtener(creado.contacto_id or 0)
            if reloaded is not None:
                return reloaded
        return creado

    def registrar_desde_captura(
        self,
        kind: str,
        payload: Mapping[str, Any],
        resultado: Mapping[str, Any],
        fuente: Mapping[str, Any],
        captura_id: int,
    ) -> ContactoMaestro | None:
        if kind not in ("contacto", "contacto_editar", "proveedor"):
            return None
        pid_raw = resultado.get("partner_id") or payload.get("partner_odoo_id")
        pid = int(pid_raw) if pid_raw else None
        rif = str(payload.get("rif") or "").strip()
        nombre = str(payload.get("nombre") or payload.get("nombre_buscar") or "").strip()
        if not nombre and pid is None:
            return None

        existente = self.repo.obtener_por_captura_id(captura_id)
        if existente is None and pid is not None:
            existente = self.repo.obtener_por_odoo_id(pid)
        if existente is None and len(normalizar_rif_canonico(rif)) >= 6:
            rn = normalizar_rif_canonico(rif)
            for cand in self.repo.listar_activos():
                if cand.rif_normalizado == rn:
                    existente = cand
                    break

        lat, lon, pc = _extraer_gps(payload)
        actor = str(fuente.get("vendedor_email") or "bot")

        if existente is not None:
            cambios: dict[str, Any] = {}
            for k in ("nombre", "rif", "telefono", "email", "direccion", "ciudad", "notas"):
                val = str(payload.get(k) or "").strip()
                if val and (kind == "contacto_editar" or not getattr(existente, k)):
                    cambios[k] = val
            if lat is not None and lon is not None:
                cambios["latitud"] = lat
                cambios["longitud"] = lon
            if pc:
                cambios["plus_code"] = pc
            if pid is not None and existente.odoo_partner_id is None:
                existente.odoo_partner_id = pid
                existente.estado_validacion = "ENVIADO_ODOO"
                existente = self.repo.actualizar(existente)
                if not cambios:
                    self._recalcular_alertas_duplicados()
                    return self.repo.obtener(existente.contacto_id or 0) or existente
            return self.editar(
                existente.contacto_id or 0,
                cambios,
                actor=actor,
                forzar_duplicado=True,
            )

        rol = (
            "proveedor"
            if kind == "proveedor"
            else ("cliente" if payload.get("tipo_contacto") == "cliente" else "prospecto")
        )
        return self.crear(
            {
                "nombre": nombre or f"Contacto #{pid}",
                "rol": rol,
                "rif": rif,
                "telefono": payload.get("telefono") or "",
                "email": payload.get("email") or "",
                "direccion": payload.get("direccion") or "",
                "ciudad": payload.get("ciudad") or "",
                "ubicacion": payload.get("ubicacion"),
                "vendedor_email": str(fuente.get("vendedor_email") or ""),
                "notas": payload.get("notas") or "",
                "odoo_partner_id": pid,
                "es_borrador_telegram": True,
            },
            origen="bot",
            actor=actor,
            forzar_duplicado=True,
            captura_id=captura_id,
        )

    def editar(
        self,
        contacto_id: int,
        cambios: Mapping[str, Any],
        *,
        actor: str = "sistema",
        sincronizar_odoo: bool = False,
        forzar_duplicado: bool = False,
    ) -> ContactoMaestro:
        c = self.repo.obtener(contacto_id)
        if c is None:
            raise ContactoNoEncontrado(f"Contacto #{contacto_id} no encontrado")

        for campo in (
            "nombre",
            "rif",
            "telefono",
            "email",
            "direccion",
            "ciudad",
            "vendedor_email",
            "notas",
        ):
            if campo in cambios and cambios[campo] is not None:
                setattr(c, campo, str(cambios[campo]).strip())

        if "rol" in cambios and cambios["rol"] is not None:
            nuevo_rol = str(cambios["rol"]).strip().lower()
            if nuevo_rol not in ROLES_CONTACTO:
                raise ContactoInvalido(f"rol debe ser uno de: {', '.join(ROLES_CONTACTO)}")
            c.rol = nuevo_rol

        if (
            "ubicacion" in cambios
            or "latitud" in cambios
            or "lat" in cambios
            or "plus_code" in cambios
        ):
            lat, lon, pc = _extraer_gps(cambios)
            if lat is not None and lon is not None:
                c.latitud = lat
                c.longitud = lon
            if pc:
                c.plus_code = pc

        _recalcular_normalizados_y_completitud(c)
        chequeo = self.verificar_duplicados(
            c,
            excluir_id=c.contacto_id,
            excluir_odoo_id=c.odoo_partner_id,
        )
        if chequeo["duplicado_bloqueante"] and not forzar_duplicado:
            raise ContactoDuplicadoError(
                "El RIF ingresado ya pertenece a otro contacto activo.",
                duplicados=chequeo["candidatos"],
            )
        c.posibles_duplicados = list(chequeo["candidatos"])
        if c.estado_validacion not in ("ENVIADO_ODOO", "DUPLICADO", "DESCARTADO"):
            if c.listo_para_odoo and not c.posibles_duplicados:
                c.estado_validacion = "VALIDADO"
                c.validado_por = actor
            else:
                c.estado_validacion = "PENDIENTE"

        actualizado = self.repo.actualizar(c)
        self._recalcular_alertas_duplicados()

        if sincronizar_odoo and actualizado.odoo_partner_id is not None:
            actualizado = self.validar_y_enviar_a_odoo(contacto_id, actor=actor)
        return self.repo.obtener(contacto_id) or actualizado

    def validar_y_enviar_a_odoo(
        self, contacto_id: int, *, actor: str = "sistema"
    ) -> ContactoMaestro:
        c = self.repo.obtener(contacto_id)
        if c is None:
            raise ContactoNoEncontrado(f"Contacto #{contacto_id} no encontrado")
        _recalcular_normalizados_y_completitud(c)
        if not c.listo_para_odoo:
            raise ContactoInvalido(
                f"Faltan campos mínimos para enviar a Odoo ({', '.join(c.campos_faltantes)})."
            )

        chequeo = self.verificar_duplicados(
            c, excluir_id=c.contacto_id, excluir_odoo_id=c.odoo_partner_id
        )
        if chequeo["duplicado_bloqueante"]:
            raise ContactoDuplicadoError(
                "Existe un contacto duplicado con el mismo RIF; fusiónalo antes de enviar a Odoo.",
                duplicados=chequeo["candidatos"],
            )

        if self._escritor_factory is None:
            raise RuntimeError("Conexión de escritura a Odoo no configurada.")
        escritor = self._escritor_factory()
        if escritor is None:
            raise RuntimeError(
                "Escritura en Odoo deshabilitada (CAPTURAS_ODOO_ESCRITURA debe estar activa)."
            )

        if c.rif and hasattr(escritor, "partners_por_rif"):
            en_odoo = escritor.partners_por_rif(c.rif)
            for item_p in en_odoo:
                pid_existente = int(item_p["id"]) if isinstance(item_p, Mapping) else int(item_p)
                if pid_existente != (c.odoo_partner_id or -1):
                    info_p = (
                        escritor.leer_partner(pid_existente)
                        if hasattr(escritor, "leer_partner")
                        else None
                    )
                    nombre_p = str((info_p or {}).get("name") or "")
                    raise ContactoDuplicadoError(
                        f"El RIF {c.rif} ya existe en Odoo (#{pid_existente} {nombre_p}).".strip(),
                        duplicados=[
                            {
                                "odoo_partner_id": pid_existente,
                                "nombre": nombre_p,
                                "rif": str((info_p or {}).get("vat") or c.rif),
                                "bloqueante": True,
                                "score": 100,
                                "motivos": ["RIF ya existe en Odoo"],
                            }
                        ],
                    )

        datos_odoo: dict[str, Any] = {
            "nombre": c.nombre,
            "rif": c.rif or None,
            "telefono": c.telefono or None,
            "email": c.email or None,
            "direccion": c.direccion or None,
            "plus_code": c.plus_code or None,
            "notas": c.notas or None,
            "lat": float(c.latitud) if c.latitud is not None else None,
            "lon": float(c.longitud) if c.longitud is not None else None,
        }
        promover_cliente = c.rol in ("cliente", "ambos")
        promover_proveedor = c.rol in ("proveedor", "ambos")

        if c.odoo_partner_id is None:
            marca = f"[CxC contacto #{c.contacto_id}]"
            if c.rol == "proveedor" and hasattr(escritor, "crear_proveedor"):
                pid = int(escritor.crear_proveedor(datos_odoo, marca=marca))
            else:
                pid = int(escritor.crear_contacto_borrador(datos_odoo, marca=marca))
                if promover_cliente or promover_proveedor:
                    escritor.actualizar_contacto(
                        pid,
                        {},
                        promover_cliente=promover_cliente,
                        promover_proveedor=promover_proveedor,
                        marca=marca,
                    )
            c.odoo_partner_id = pid
        else:
            escritor.actualizar_contacto(
                c.odoo_partner_id,
                datos_odoo,
                promover_cliente=promover_cliente,
                promover_proveedor=promover_proveedor,
                marca=f"[CxC maestro #{c.contacto_id}]",
            )

        c.estado_validacion = "ENVIADO_ODOO"
        c.validado_por = actor
        c.es_borrador_telegram = False
        actualizado = self.repo.actualizar(c)
        self._recalcular_alertas_duplicados()
        return self.repo.obtener(contacto_id) or actualizado

    def fusionar(
        self,
        duplicado_id: int,
        maestro_id: int,
        *,
        actor: str = "sistema",
    ) -> dict[str, Any]:
        if duplicado_id == maestro_id:
            raise ContactoInvalido("El contacto duplicado y el maestro deben ser distintos.")
        dup = self.repo.obtener(duplicado_id)
        maestro = self.repo.obtener(maestro_id)
        if dup is None or maestro is None:
            raise ContactoNoEncontrado("Alguno de los contactos a fusionar no existe.")

        for campo in (
            "rif",
            "telefono",
            "email",
            "direccion",
            "ciudad",
            "plus_code",
            "vendedor_email",
        ):
            if not getattr(maestro, campo) and getattr(dup, campo):
                setattr(maestro, campo, getattr(dup, campo))
        if maestro.latitud is None and dup.latitud is not None:
            maestro.latitud = dup.latitud
            maestro.longitud = dup.longitud
        if dup.notas and dup.notas not in maestro.notas:
            maestro.notas = f"{maestro.notas}\n[Fusionado #{dup.contacto_id}]: {dup.notas}".strip()
        if maestro.rol == "prospecto" and dup.rol in ("cliente", "proveedor", "ambos"):
            maestro.rol = dup.rol
        if maestro.odoo_partner_id is None and dup.odoo_partner_id is not None:
            maestro.odoo_partner_id = dup.odoo_partner_id
            maestro.estado_validacion = "ENVIADO_ODOO"
            dup.odoo_partner_id = None

        _recalcular_normalizados_y_completitud(maestro)
        dup.estado_validacion = "DUPLICADO"
        dup.duplicado_de_id = maestro.contacto_id
        dup.activo = False
        dup.validado_por = actor
        dup.posibles_duplicados = []

        self.repo.actualizar(dup)
        maestro_act = self.repo.actualizar(maestro)

        # Reapunta documentos comerciales/operativos en BD si existe el engine
        reapuntados = 0
        if self._engine is not None and maestro_act.odoo_partner_id is not None:
            try:
                with self._engine.begin() as conn:
                    if dup.rif_normalizado:
                        r1 = conn.execute(
                            update(documentos_comerciales)
                            .where(
                                documentos_comerciales.c.partner_odoo_id.is_(None),
                                documentos_comerciales.c.rif == dup.rif,
                            )
                            .values(partner_odoo_id=maestro_act.odoo_partner_id)
                        )
                        reapuntados += r1.rowcount or 0
                        r2 = conn.execute(
                            update(documentos_operativos)
                            .where(
                                documentos_operativos.c.partner_odoo_id.is_(None),
                                documentos_operativos.c.rif == dup.rif,
                            )
                            .values(partner_odoo_id=maestro_act.odoo_partner_id)
                        )
                        reapuntados += r2.rowcount or 0
            except SQLAlchemyError as exc:
                logger.debug("Reapuntado al fusionar omitido: %s", exc)

        self._recalcular_alertas_duplicados()
        maestro_final = self.repo.obtener(maestro_id) or maestro_act
        return {
            "maestro": maestro_final.a_dict(),
            "duplicado": (self.repo.obtener(duplicado_id) or dup).a_dict(),
            "documentos_reapuntados": reapuntados,
        }

    def descartar(self, contacto_id: int, *, actor: str = "sistema") -> ContactoMaestro:
        c = self.repo.obtener(contacto_id)
        if c is None:
            raise ContactoNoEncontrado(f"Contacto #{contacto_id} no encontrado")
        c.estado_validacion = "DESCARTADO"
        c.activo = False
        c.validado_por = actor
        c.posibles_duplicados = []
        actualizado = self.repo.actualizar(c)
        self._recalcular_alertas_duplicados()
        return actualizado

    def cola_duplicados(self) -> list[dict[str, Any]]:
        return detectar_grupos_duplicados(self.repo.listar_activos())

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
        if estado_validacion and estado_validacion not in ESTADOS_VALIDACION:
            raise ContactoInvalido(f"estado_validacion inválido: {estado_validacion}")
        return self.repo.listar(
            origen=origen,
            rol=rol,
            estado_validacion=estado_validacion,
            solo_incompletos=solo_incompletos,
            solo_duplicados=solo_duplicados,
            q=q,
            incluir_inactivos=incluir_inactivos,
            limit=limit,
            offset=offset,
        )
