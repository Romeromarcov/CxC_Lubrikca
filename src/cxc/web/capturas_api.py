"""API de las capturas de los bots de Telegram.

Dos audiencias:

- **El bot** (``POST /api/bots/capturas`` y ``…/{id}/completar``): se autentica con la cabecera
  ``X-Bot-Key`` (variable ``BOT_API_KEY``, comparación en tiempo constante). Sin la variable, 503.
  Las llaves de API normales de CxC siguen siendo SOLO GET: esta clave solo abre estas dos rutas.
- **La interfaz de CxC** (listar, ver, reintentar): sesión con ``capturas.ver`` o
  ``capturas.gestionar``. ``…/{id}/completar`` también acepta sesión con ``capturas.gestionar``
  (para que la pantalla complete pagos); ``POST /api/bots/capturas`` es SOLO del bot.

Los endpoints son síncronos (``def``) a propósito: FastAPI los corre en un hilo y los accesos a
Postgres/Odoo no bloquean el loop de eventos. El envío a Odoo corre en segundo plano.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from cxc.capturas.espejo import EspejoSQL
from cxc.capturas.modelos import ESTADOS, KINDS, PENDIENTE, CapturaInvalida
from cxc.capturas.repositorio import RepoCapturasSQL
from cxc.capturas.servicio import (
    BloqueoOcupado,
    CapturaCerrada,
    FacturaDuplicada,
    NoCompletable,
    NoEncontrada,
    ServicioCapturas,
)
from cxc.odoo.escritura import EscritorOdoo, EscrituraConfig, conectar
from cxc.permisos import tiene

logger = logging.getLogger("cxc.capturas.api")

router = APIRouter()

_RUTA_BOT_POST = re.compile(
    r"^/api/bots/(?:capturas(?:/\d+/completar)?|heartbeat|descuentos/evaluar|documentos/\d+/convertir-pedido|operativos/validar|contactos(?:/verificar)?)$"
)
_RUTA_BOT_GET = re.compile(
    r"^/api/bots/(?:catalogo|contactos|descuentos/catalogo|operativos/catalogos)$"
)
_KINDS_SINCRONOS = frozenset({"contacto", "contacto_editar", "cotizacion", "pedido"})
_KINDS_OPERATIVOS = frozenset({"pago", "pago_proveedor", "factura_proveedor", "gasto"})
PERMISOS_VER = ("capturas.ver", "capturas.gestionar")
INTERVALO_REINTENTO_SEGUNDOS = 120


def es_ruta_bot(metodo: str, ruta: str) -> bool:
    """Rutas que el middleware deja pasar con ``X-Bot-Key`` (el handler valida la clave):
    capturas, completar, latido del panel y consultas de catálogo/contactos para los bots."""
    if metodo == "POST":
        return _RUTA_BOT_POST.match(ruta) is not None
    if metodo == "GET":
        return _RUTA_BOT_GET.match(ruta) is not None
    return False


# ── dependencias ─────────────────────────────────────────────────────────────


def _engine() -> Engine:
    from cxc.web.app import get_repo  # import tardío: app incluye este router

    repo: Any = get_repo()  # PostgresRepository: ``_engine`` no está en el Protocol ``Repository``
    engine: Engine = repo._engine
    return engine


def _fabrica_escritor() -> EscritorOdoo | None:
    """Conexión de escritura SOLO con interruptor y credencial; si no, ``None`` (no se escribe)."""
    cfg = EscrituraConfig.from_env()
    if cfg is None:
        return None
    return EscritorOdoo(conectar(cfg))


def _diarios_env() -> dict[str, int]:
    raw = os.environ.get("CAPTURAS_DIARIOS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k): int(v) for k, v in data.items()}
    except (ValueError, TypeError, AttributeError):
        logger.error("CAPTURAS_DIARIOS no es un JSON {nombre: journal_id}; se ignora")
        return {}


def _max_intentos() -> int:
    try:
        return max(1, int(os.environ.get("CAPTURAS_MAX_INTENTOS", "5")))
    except ValueError:
        return 5


def get_servicio() -> ServicioCapturas:
    engine = _engine()
    return ServicioCapturas(
        RepoCapturasSQL(engine),
        EspejoSQL(engine),
        _fabrica_escritor,
        diarios=_diarios_env(),
        max_intentos=_max_intentos(),
    )


Servicio = Annotated[ServicioCapturas, Depends(get_servicio)]


def exigir_bot_key(x_bot_key: Annotated[str | None, Header()] = None) -> None:
    esperada = os.environ.get("BOT_API_KEY", "")
    if not esperada:
        raise HTTPException(status_code=503, detail="BOT_API_KEY no está configurada")
    if not x_bot_key or not hmac.compare_digest(x_bot_key.encode(), esperada.encode()):
        raise HTTPException(status_code=401, detail="Clave de bot inválida")


def _usuario(cxc_session: str | None, *permisos: str) -> dict[str, Any]:
    from cxc.web.app import get_current_user_from_cookie

    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    if not any(tiene(user, p) for p in permisos):
        raise HTTPException(status_code=403, detail="Sin permisos para capturas")
    return user


def _actor(user: dict[str, Any]) -> str:
    return str(user.get("email") or user.get("nombre") or "desconocido")


def bot_o_gestion(
    x_bot_key: Annotated[str | None, Header()] = None,
    cxc_session: Annotated[str | None, Cookie()] = None,
) -> str:
    """Quién completa: el bot (``X-Bot-Key``) o una persona con sesión y ``capturas.gestionar``.

    Si llega la cabecera ``X-Bot-Key`` se valida como clave de bot (503 sin ``BOT_API_KEY``, 401 si
    no coincide) y NO se cae a la sesión: una clave mala nunca se «perdona». Sin la cabecera se
    exige la sesión con permiso. Devuelve el actor para la bitácora.
    """
    if x_bot_key is not None:
        exigir_bot_key(x_bot_key)
        return "bot"
    return _actor(_usuario(cxc_session, "capturas.gestionar"))


# ── entrada del bot ──────────────────────────────────────────────────────────


class CapturaIn(BaseModel):
    kind: str
    draft: dict[str, Any]
    source: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=200)


class CompletarIn(BaseModel):
    cambios: dict[str, Any]


def _ocupado() -> JSONResponse:
    """Otro envío de lo mismo sigue en curso: el bot puede reintentar (es idempotente)."""
    return JSONResponse(
        status_code=503,
        content={
            "error": "Hay otro envío de la misma factura en curso; reintenta en unos segundos."
        },
    )


def _invalida(e: CapturaInvalida) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": e.error, "faltan": e.faltan})


def _sincronizar_documento_desde_captura(captura: Any) -> dict[str, Any]:
    """Crea/actualiza el Documento Comercial (COT/PED) en CxC cuando llega desde el bot."""
    if captura.kind not in ("cotizacion", "pedido"):
        return {}
    try:
        from cxc.web.app import app as _app
        from cxc.web.documentos_api import get_servicio_documentos

        fabrica_docs = _app.dependency_overrides.get(
            get_servicio_documentos, get_servicio_documentos
        )
        svc_docs = fabrica_docs()
        p = captura.payload
        actor = str(captura.fuente.get("vendedor_email") or "bot")
        ya_enviado_captura = (
            captura.kind == "pedido"
            and not p.get("requiere_aprobacion_cxc")
            and captura.estado == "ENVIADO"
            and bool(captura.resultado.get("pedido_odoo_id"))
        )
        doc = svc_docs.crear(
            {
                "tipo": captura.kind,
                "partner_odoo_id": captura.resultado.get("partner_id") or p.get("partner_odoo_id"),
                "tipo_contacto": "cliente"
                if (captura.kind == "pedido" or p.get("es_primera_compra") is False)
                else "prospecto",
                "cliente_nombre": p.get("cliente") or p.get("nombre") or "",
                "rif": p.get("rif") or "",
                "email": p.get("email") or "",
                "telefono": p.get("telefono") or "",
                "direccion": p.get("direccion_fiscal")
                or p.get("direccion")
                or p.get("direccion_entrega")
                or "",
                "ubicacion": p.get("ubicacion"),
                "es_primera_compra": p.get("es_primera_compra") is not False,
                "moneda_lista": p.get("moneda_lista") or "VES",
                "lista_precios_id": p.get("lista_precios_id"),
                "lista_precios_nombre": p.get("lista_precios_nombre"),
                "dias_credito": p.get("dias_credito") or 0,
                "moneda_pago_esperada": p.get("moneda_pago_esperada"),
                "descuentos_codigos": p.get("descuentos_codigos") or p.get("descuentos") or [],
                "descuentos_especiales": p.get("descuentos_especiales") or [],
                "vendedor_email": str(captura.fuente.get("vendedor_email") or ""),
                "fecha_entrega": p.get("fecha_entrega"),
                "condiciones_negociacion": p.get("condiciones_negociacion") or "",
                "observaciones": p.get("observaciones") or p.get("notas") or "",
                "lineas": p.get("lineas") or [],
            },
            origen="bot",
            captura_id=captura.captura_id,
            actor=actor,
            auto_enviar=not ya_enviado_captura,
        )
        if captura.kind == "pedido" and not p.get("requiere_aprobacion_cxc"):
            if captura.estado == "ENVIADO" and captura.resultado.get("pedido_odoo_id"):
                doc = svc_docs.registrar_envio_captura(
                    doc.documento_id,
                    partner_odoo_id=captura.resultado.get("partner_id"),
                    odoo_order_id=int(captura.resultado["pedido_odoo_id"]),
                    odoo_order_name=str(captura.resultado.get("odoo_name") or ""),
                )
            elif captura.estado in ("INCOMPLETO", "ERROR") and captura.error:
                doc = svc_docs.registrar_error_captura(doc.documento_id, captura.error)
        doc_dict = doc.a_dict(incluir_lineas=True)
        modo_res = (
            "auto_enviado_odoo"
            if (doc.auto_enviado and doc.estado == "ENVIADO_ODOO")
            else (
                "pendiente_aprobacion_cxc"
                if doc.estado == "PENDIENTE_APROBACION"
                else "documento_cxc"
            )
        )
        out_doc: dict[str, Any] = {
            "documento_id": doc.documento_id,
            "documento_numero": doc.numero,
            "documento_estado": doc.estado,
            "auto_enviado": doc.auto_enviado,
            "motivos_retencion": list(doc.motivos_retencion),
            "modo": modo_res,
            "token_preview": doc.token_preview,
            "preview_path": doc_dict["preview_path"],
            "lista_precios_nombre": doc.lista_precios_nombre,
            "subtotal": doc_dict["subtotal"],
            "total_descuentos": doc_dict["total_descuentos"],
            "subtotal_neto": doc_dict["subtotal_neto"],
            "monto_iva": doc_dict["monto_iva"],
            "total_con_iva": doc_dict["total_con_iva"],
            "total_litros": doc_dict["total_litros"],
            "descuentos": doc_dict["descuentos"],
            "alertas_motor": doc_dict["alertas_motor"],
            "lineas_calculadas": doc_dict["lineas"],
        }
        if doc.odoo_order_id:
            out_doc["odoo_order_id"] = doc.odoo_order_id
        if doc.odoo_order_name:
            out_doc["odoo_order_name"] = doc.odoo_order_name
            out_doc["odoo_name"] = doc.odoo_order_name
        return out_doc
    except Exception:
        # En tests unitarios aislados sin Postgres para documentos no interrumpe la captura.
        return {}


def _sincronizar_operativo_desde_captura(captura: Any) -> dict[str, Any]:
    """Crea/actualiza un Documento Operativo en CxC para flujo Preview + Aprobación."""
    if captura.kind not in _KINDS_OPERATIVOS or not captura.payload.get("requiere_aprobacion_cxc"):
        return {}
    try:
        from cxc.web.app import app as _app
        from cxc.web.operativos_api import get_servicio_operativos

        fabrica_ops = _app.dependency_overrides.get(
            get_servicio_operativos, get_servicio_operativos
        )
        svc_op = fabrica_ops()
        p = captura.payload
        actor = str(captura.fuente.get("vendedor_email") or "bot")
        doc = svc_op.crear(
            {"tipo": captura.kind, **p},
            origen="bot",
            captura_id=captura.captura_id,
            actor=actor,
        )
        d = doc.a_dict()
        return {
            "es_operativo": True,
            "documento_id": doc.documento_id,
            "documento_numero": doc.numero,
            "documento_tipo": doc.tipo,
            "documento_estado": doc.estado,
            "token_preview": doc.token_preview,
            "preview_path": d["preview_path"],
            "alertas": d["alertas"],
            "faltantes": d["faltantes"],
            "diario_nombre": doc.diario_nombre,
            "cuenta_sugerida": (
                f"{doc.cuenta_contable_codigo} · {doc.cuenta_contable_nombre}".strip(" ·")
                if (doc.cuenta_contable_codigo or doc.cuenta_contable_nombre)
                else ""
            ),
            "contraparte_resuelta": d["validacion"].get("contraparte_resuelta"),
        }
    except Exception:
        return {}


def _sincronizar_contacto_desde_captura(captura: Any) -> dict[str, Any]:
    """Registra o actualiza en ``contactos_maestro`` un contacto/proveedor enviado por bots."""
    if captura.kind not in ("contacto", "contacto_editar", "proveedor"):
        return {}
    try:
        from cxc.web.app import app as _app
        from cxc.web.contactos_api import get_servicio_contactos

        fabrica = _app.dependency_overrides.get(get_servicio_contactos, get_servicio_contactos)
        svc = fabrica()
        c = svc.registrar_desde_captura(
            captura.kind,
            captura.payload,
            captura.resultado,
            captura.fuente,
            captura.captura_id,
        )
        if c is None:
            return {}
        return {
            "contacto_maestro_id": c.contacto_id,
            "completitud_pct": c.completitud_pct,
            "posibles_duplicados": c.posibles_duplicados,
        }
    except Exception:
        return {}


def _procesar_y_sincronizar_en_segundo_plano(
    servicio: ServicioCapturas, captura_id: int
) -> None:
    """Procesa la captura en Odoo y actualiza ``contactos_maestro`` con el resultado."""
    procesada = servicio.procesar(captura_id)
    _sincronizar_contacto_desde_captura(procesada)


@router.post("/api/bots/capturas", dependencies=[Depends(exigir_bot_key)])
def recibir_captura(body: CapturaIn, tareas: BackgroundTasks, servicio: Servicio) -> JSONResponse:
    try:
        captura, creada, actualizada = servicio.recibir_detallado(
            body.kind, body.draft, body.source, body.idempotency_key
        )
    except FacturaDuplicada as e:
        return JSONResponse(status_code=409, content={"error": str(e)})  # rechazo de negocio
    except BloqueoOcupado:
        return _ocupado()
    except CapturaInvalida as e:
        return _invalida(e)
    if body.kind in _KINDS_SINCRONOS:
        if (creada or actualizada) and captura.estado == PENDIENTE:
            captura = servicio.procesar(captura.captura_id)
        extra_doc = _sincronizar_documento_desde_captura(captura)
        extra_contacto = _sincronizar_contacto_desde_captura(captura)
        cuerpo = captura.a_dict()
        extra_total = {**extra_doc, **extra_contacto}
        if extra_total:
            cuerpo["resultado"] = {**cuerpo.get("resultado", {}), **extra_total}
        return JSONResponse(status_code=201 if creada else 200, content=cuerpo)
    if body.kind in _KINDS_OPERATIVOS and body.draft.get("requiere_aprobacion_cxc"):
        if (creada or actualizada) and captura.estado == PENDIENTE:
            captura = servicio.procesar(captura.captura_id)
        extra_op = _sincronizar_operativo_desde_captura(captura)
        cuerpo = captura.a_dict()
        if extra_op:
            cuerpo["resultado"] = {**cuerpo.get("resultado", {}), **extra_op}
        return JSONResponse(status_code=201 if creada else 200, content=cuerpo)
    if (creada or actualizada) and captura.estado == PENDIENTE:
        # a Odoo en segundo plano, sincronizando luego el Maestro Único con el partner_id creado
        tareas.add_task(_procesar_y_sincronizar_en_segundo_plano, servicio, captura.captura_id)
    extra_contacto = _sincronizar_contacto_desde_captura(captura)
    cuerpo = captura.a_dict()
    if extra_contacto:
        cuerpo["resultado"] = {**cuerpo.get("resultado", {}), **extra_contacto}
    return JSONResponse(status_code=201 if creada else 200, content=cuerpo)


@router.post("/api/bots/capturas/{captura_id}/completar")
def completar_captura(
    captura_id: int,
    body: CompletarIn,
    tareas: BackgroundTasks,
    servicio: Servicio,
    actor: Annotated[str, Depends(bot_o_gestion)],
) -> JSONResponse:
    """Completa un pago o factura: lo llama el bot (``X-Bot-Key``) o la pantalla (gestionar)."""
    try:
        captura = servicio.completar(captura_id, body.cambios, actor=actor, enviar=False)
    except NoEncontrada as e:
        raise HTTPException(status_code=404, detail="Captura no encontrada") from e
    except NoCompletable as e:
        return JSONResponse(status_code=422, content={"error": str(e), "faltan": []})
    except (CapturaCerrada, FacturaDuplicada) as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    except BloqueoOcupado:
        return _ocupado()
    except CapturaInvalida as e:
        return _invalida(e)
    if captura.kind in _KINDS_OPERATIVOS and captura.payload.get("requiere_aprobacion_cxc"):
        if captura.estado == PENDIENTE:
            captura = servicio.procesar(captura.captura_id)
        extra_op = _sincronizar_operativo_desde_captura(captura)
        cuerpo = captura.a_dict()
        if extra_op:
            cuerpo["resultado"] = {**cuerpo.get("resultado", {}), **extra_op}
        return JSONResponse(content=cuerpo)
    if captura.estado == PENDIENTE:
        tareas.add_task(servicio.procesar, captura.captura_id)
    return JSONResponse(content=captura.a_dict())


# ── interfaz de CxC (sesión) ─────────────────────────────────────────────────


@router.get("/api/bots/capturas")
def listar_capturas(
    servicio: Servicio,
    estado: str | None = None,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
    cxc_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    _usuario(cxc_session, *PERMISOS_VER)
    if estado and estado not in ESTADOS:
        raise HTTPException(status_code=422, detail=f"estado debe ser uno de {', '.join(ESTADOS)}")
    if kind and kind not in KINDS:
        raise HTTPException(status_code=422, detail=f"kind debe ser uno de {', '.join(KINDS)}")
    items, total = servicio.listar(
        estado=estado, kind=kind, limit=max(1, min(limit, 200)), offset=max(0, offset)
    )
    return {"items": [c.a_dict() for c in items], "total": total}


@router.get("/api/bots/capturas/{captura_id}")
def ver_captura(
    captura_id: int, servicio: Servicio, cxc_session: str | None = Cookie(default=None)
) -> dict[str, Any]:
    _usuario(cxc_session, *PERMISOS_VER)
    try:
        c, eventos = servicio.detalle(captura_id)
    except NoEncontrada as e:
        raise HTTPException(status_code=404, detail="Captura no encontrada") from e
    return {**c.a_dict(), "eventos": eventos}


@router.post("/api/bots/capturas/{captura_id}/reintentar")
def reintentar_captura(
    captura_id: int,
    servicio: Servicio,
    cxc_session: str | None = Cookie(default=None),
) -> JSONResponse:
    user = _usuario(cxc_session, "capturas.gestionar")
    try:
        c = servicio.reintentar(captura_id, _actor(user))
    except NoEncontrada as e:
        raise HTTPException(status_code=404, detail="Captura no encontrada") from e
    except CapturaCerrada as e:
        return JSONResponse(status_code=409, content={"error": str(e)})
    return JSONResponse(content=c.a_dict())


# ── reintento periódico ──────────────────────────────────────────────────────


async def run_reintentos_capturas_en_background() -> None:
    """Cada ~2 min reintenta los ERROR transitorios y los PENDIENTE (con escritura activa)."""
    while True:
        await asyncio.sleep(INTERVALO_REINTENTO_SEGUNDOS)
        if EscrituraConfig.from_env() is None:
            continue
        try:
            n = await asyncio.to_thread(lambda: get_servicio().reintentar_pendientes())
            if n:
                logger.info("Capturas reintentadas: %d", n)
        except Exception:
            logger.exception("Falló el ciclo de reintento de capturas")


__all__ = [
    "es_ruta_bot",
    "get_servicio",
    "router",
    "run_reintentos_capturas_en_background",
]
