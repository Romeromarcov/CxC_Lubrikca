"""Servicio de Notificaciones Push Multicanal y Outbox Anti-Loop (Fase 4)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, and_, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from cxc.db.schema import notificaciones_push_outbox, web_push_suscripciones

from .repositorio import RepoApiV1SQL

logger = logging.getLogger("cxc.api_v1.notificaciones")

EXPO_PUSH_ENDPOINT = "https://exp.host/--/api/v2/push/send"
CANALES_PUSH = ("expo_push", "web_push", "email_alerta", "in_app")


def _row_dict(row: Any) -> dict[str, Any]:
    d = dict(row._mapping)
    for k in ("creado_en", "enviado_en", "leida_en", "actualizado_en"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].isoformat(timespec="seconds")
    if "datos_json" in d:
        try:
            d["datos"] = json.loads(d["datos_json"] or "{}")
        except ValueError:
            d["datos"] = {}
    return d


class RepoNotificacionesSQL:
    """Outbox persistente de notificaciones push/in-app con deduplicación estricta."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def encolar(
        self,
        *,
        dedupe_key: str,
        canal: str,
        sujeto_tipo: str,
        sujeto_id: str,
        evento_tipo: str,
        titulo: str,
        cuerpo: str,
        datos: dict[str, Any] | None = None,
        max_intentos: int = 3,
    ) -> dict[str, Any]:
        dk = dedupe_key.strip()[:160]
        canal_clean = canal if canal in CANALES_PUSH else "in_app"
        ahora = datetime.now()
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    insert(notificaciones_push_outbox)
                    .values(
                        dedupe_key=dk,
                        canal=canal_clean,
                        sujeto_tipo=sujeto_tipo[:32],
                        sujeto_id=sujeto_id.strip().lower()[:160],
                        evento_tipo=evento_tipo[:64],
                        titulo=titulo[:180],
                        cuerpo=cuerpo,
                        datos_json=json.dumps(datos or {}, ensure_ascii=False, default=str),
                        estado="ENVIADO" if canal_clean == "in_app" else "PENDIENTE",
                        intentos=0,
                        max_intentos=max(1, min(int(max_intentos), 10)),
                        ultimo_error="",
                        creado_en=ahora,
                        enviado_en=ahora if canal_clean == "in_app" else None,
                    )
                    .returning(notificaciones_push_outbox)
                ).one()
            d = _row_dict(row)
            d["duplicada"] = False
            return d
        except IntegrityError:
            with self._engine.connect() as conn:
                ex = conn.execute(
                    select(notificaciones_push_outbox).where(
                        notificaciones_push_outbox.c.dedupe_key == dk
                    )
                ).one()
            d = _row_dict(ex)
            d["duplicada"] = True
            return d

    def listar_para_sujeto(
        self,
        *,
        sujeto_tipo: str,
        sujeto_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        sid = sujeto_id.strip().lower()
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(notificaciones_push_outbox)
                .where(
                    and_(
                        notificaciones_push_outbox.c.sujeto_tipo == sujeto_tipo,
                        notificaciones_push_outbox.c.sujeto_id == sid,
                    )
                )
                .order_by(notificaciones_push_outbox.c.notificacion_id.desc())
                .limit(min(max(limit, 1), 200))
            ).all()
        return [_row_dict(r) for r in rows]

    def marcar_leida(
        self,
        notificacion_id: int,
        *,
        sujeto_tipo: str,
        sujeto_id: str,
    ) -> dict[str, Any] | None:
        ahora = datetime.now()
        with self._engine.begin() as conn:
            row = conn.execute(
                update(notificaciones_push_outbox)
                .where(
                    and_(
                        notificaciones_push_outbox.c.notificacion_id == int(notificacion_id),
                        notificaciones_push_outbox.c.sujeto_tipo == sujeto_tipo,
                        notificaciones_push_outbox.c.sujeto_id == sujeto_id.strip().lower(),
                    )
                )
                .values(leida_en=ahora)
                .returning(notificaciones_push_outbox)
            ).one_or_none()
        return _row_dict(row) if row is not None else None

    def listar_pendientes(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(notificaciones_push_outbox)
                .where(notificaciones_push_outbox.c.estado == "PENDIENTE")
                .order_by(notificaciones_push_outbox.c.notificacion_id.asc())
                .limit(min(max(limit, 1), 200))
            ).all()
        return [_row_dict(r) for r in rows]

    def marcar_enviada(self, notificacion_id: int) -> None:
        ahora = datetime.now()
        with self._engine.begin() as conn:
            conn.execute(
                update(notificaciones_push_outbox)
                .where(notificaciones_push_outbox.c.notificacion_id == int(notificacion_id))
                .values(
                    estado="ENVIADO",
                    intentos=notificaciones_push_outbox.c.intentos + 1,
                    ultimo_error="",
                    enviado_en=ahora,
                )
            )

    def marcar_fallo(
        self,
        notificacion_id: int,
        *,
        error: str,
        permanente: bool = False,
    ) -> str:
        with self._engine.begin() as conn:
            row = conn.execute(
                select(notificaciones_push_outbox).where(
                    notificaciones_push_outbox.c.notificacion_id == int(notificacion_id)
                )
            ).one_or_none()
            if row is None:
                return "INEXISTENTE"
            nuevos_int = int(row.intentos) + 1
            es_perm = permanente or nuevos_int >= int(row.max_intentos)
            nuevo_estado = "FALLIDO_PERMANENTE" if es_perm else "PENDIENTE"
            conn.execute(
                update(notificaciones_push_outbox)
                .where(notificaciones_push_outbox.c.notificacion_id == int(notificacion_id))
                .values(
                    estado=nuevo_estado,
                    intentos=nuevos_int,
                    ultimo_error=error[:500],
                )
            )
        return nuevo_estado

    def registrar_web_push(
        self,
        *,
        sujeto_tipo: str,
        sujeto_id: str,
        endpoint: str,
        p256dh: str = "",
        auth: str = "",
        user_agent: str = "",
    ) -> dict[str, Any]:
        ep = endpoint.strip()[:500]
        ahora = datetime.now()
        with self._engine.begin() as conn:
            ex = conn.execute(
                select(web_push_suscripciones).where(web_push_suscripciones.c.endpoint == ep)
            ).one_or_none()
            if ex is not None:
                row = conn.execute(
                    update(web_push_suscripciones)
                    .where(web_push_suscripciones.c.endpoint == ep)
                    .values(
                        sujeto_tipo=sujeto_tipo[:32],
                        sujeto_id=sujeto_id.strip().lower()[:160],
                        p256dh=p256dh[:255],
                        auth=auth[:255],
                        user_agent=user_agent[:200],
                        activa=True,
                        actualizado_en=ahora,
                    )
                    .returning(web_push_suscripciones)
                ).one()
            else:
                row = conn.execute(
                    insert(web_push_suscripciones)
                    .values(
                        sujeto_tipo=sujeto_tipo[:32],
                        sujeto_id=sujeto_id.strip().lower()[:160],
                        endpoint=ep,
                        p256dh=p256dh[:255],
                        auth=auth[:255],
                        user_agent=user_agent[:200],
                        activa=True,
                        creado_en=ahora,
                        actualizado_en=ahora,
                    )
                    .returning(web_push_suscripciones)
                ).one()
        return _row_dict(row)

    def desactivar_web_push(self, endpoint: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(web_push_suscripciones)
                .where(web_push_suscripciones.c.endpoint == endpoint.strip()[:500])
                .values(activa=False, actualizado_en=datetime.now())
            )

    def listar_web_push_activas(self, *, sujeto_tipo: str, sujeto_id: str) -> list[dict[str, Any]]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(web_push_suscripciones).where(
                    and_(
                        web_push_suscripciones.c.sujeto_tipo == sujeto_tipo,
                        web_push_suscripciones.c.sujeto_id == sujeto_id.strip().lower(),
                        web_push_suscripciones.c.activa.is_(True),
                    )
                )
            ).all()
        return [_row_dict(r) for r in rows]

    def resumen_salud(self) -> dict[str, Any]:
        with self._engine.connect() as conn:
            filas = conn.execute(
                select(
                    notificaciones_push_outbox.c.estado,
                    func.count(notificaciones_push_outbox.c.notificacion_id),
                ).group_by(notificaciones_push_outbox.c.estado)
            ).all()
            wp_activas = (
                conn.execute(
                    select(func.count(web_push_suscripciones.c.suscripcion_id)).where(
                        web_push_suscripciones.c.activa.is_(True)
                    )
                ).scalar_one()
                or 0
            )
        por_estado = {str(est): int(cnt) for est, cnt in filas}
        return {
            "por_estado": por_estado,
            "pendientes": por_estado.get("PENDIENTE", 0),
            "enviadas": por_estado.get("ENVIADO", 0),
            "fallidas_permanentes": por_estado.get("FALLIDO_PERMANENTE", 0),
            "web_push_suscripciones_activas": int(wp_activas),
        }


HttpPostFn = Callable[[str, dict[str, Any]], tuple[int, dict[str, Any]]]


def _default_http_post(url: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            txt = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(txt) if txt else {}
    except urllib.error.HTTPError as exc:
        txt = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(txt) if txt else {}
        except ValueError:
            parsed = {"error": txt}
        return exc.code, parsed if isinstance(parsed, dict) else {"error": txt}


class DespachadorPush:
    """Procesa ``notificaciones_push_outbox`` sin loops infinitos ni tokens muertos."""

    def __init__(
        self,
        repo_notifs: RepoNotificacionesSQL,
        repo_v1: RepoApiV1SQL,
        *,
        http_post: HttpPostFn | None = None,
    ) -> None:
        self._notifs = repo_notifs
        self._v1 = repo_v1
        self._post = http_post or _default_http_post

    def _enviar_expo(self, notif: dict[str, Any]) -> None:
        disps = [
            d
            for d in self._v1.listar_dispositivos_activos(
                sujeto_tipo=str(notif["sujeto_tipo"]),
                sujeto_id=str(notif["sujeto_id"]),
            )
            if str(d.get("push_token") or "").strip()
        ]
        if not disps:
            self._notifs.marcar_enviada(int(notif["notificacion_id"]))
            return

        payloads = [
            {
                "to": str(d["push_token"]),
                "title": notif["titulo"],
                "body": notif["cuerpo"],
                "data": notif.get("datos") or {},
            }
            for d in disps
        ]
        try:
            status, resp = self._post(EXPO_PUSH_ENDPOINT, {"messages": payloads})
        except Exception as exc:
            self._notifs.marcar_fallo(int(notif["notificacion_id"]), error=str(exc))
            return

        if status >= 500:
            self._notifs.marcar_fallo(
                int(notif["notificacion_id"]), error=f"Expo HTTP {status}"
            )
            return

        if status >= 400:
            self._notifs.marcar_fallo(
                int(notif["notificacion_id"]),
                error=f"Expo HTTP {status}: {resp}",
                permanente=True,
            )
            return

        tickets_raw = resp.get("data")
        tickets = tickets_raw if isinstance(tickets_raw, list) else []
        hubo_device_not_reg = False
        for idx, t in enumerate(tickets):
            if not isinstance(t, dict) or t.get("status") != "error":
                continue
            details_raw = t.get("details")
            details = details_raw if isinstance(details_raw, dict) else {}
            if details.get("error") == "DeviceNotRegistered" and idx < len(disps):
                self._v1.limpiar_push_token(str(disps[idx]["push_token"]))
                hubo_device_not_reg = True
        if hubo_device_not_reg and len(disps) == 1:
            self._notifs.marcar_fallo(
                int(notif["notificacion_id"]),
                error="DeviceNotRegistered: push_token revocado limpiado.",
                permanente=True,
            )
            return
        self._notifs.marcar_enviada(int(notif["notificacion_id"]))

    def despachar_pendientes(self, *, limit: int = 50) -> dict[str, int]:
        pendientes = self._notifs.listar_pendientes(limit=limit)
        enviadas = 0
        fallos = 0
        permanentes = 0
        for n in pendientes:
            nid = int(n["notificacion_id"])
            canal = str(n["canal"])
            if canal == "expo_push":
                before_err = n.get("estado")
                self._enviar_expo(n)
                # Re-leemos brevemente solo si hace falta contabilizar
                actuales = self._notifs.listar_para_sujeto(
                    sujeto_tipo=str(n["sujeto_tipo"]),
                    sujeto_id=str(n["sujeto_id"]),
                    limit=5,
                )
                act = next((x for x in actuales if int(x["notificacion_id"]) == nid), None)
                est_f = act["estado"] if act else before_err
                if est_f == "ENVIADO":
                    enviadas += 1
                elif est_f == "FALLIDO_PERMANENTE":
                    permanentes += 1
                else:
                    fallos += 1
            else:
                # web_push / in_app / email_alerta quedan trazados y entregados sin bloquear
                self._notifs.marcar_enviada(nid)
                enviadas += 1
        return {
            "procesadas": len(pendientes),
            "enviadas": enviadas,
            "reintentables": fallos,
            "permanentes": permanentes,
        }


def notificar_cambio_tasa_significativo(
    repo_notifs: RepoNotificacionesSQL,
    repo_v1: RepoApiV1SQL,
    *,
    tasa_anterior: float,
    tasa_nueva: float,
    tipo_tasa: str = "BCV",
    fecha_clave: str = "",
    umbral_pct: float = 1.5,
) -> int:
    """Encola alerta push a vendedores activos si la variación porcentual >= umbral_pct."""
    if tasa_anterior <= 0 or tasa_nueva <= 0:
        return 0
    var_pct = ((tasa_nueva - tasa_anterior) / tasa_anterior) * 100.0
    if abs(var_pct) < umbral_pct:
        return 0
    f_tag = fecha_clave or datetime.now().strftime("%Y%m%d%H")
    disps = repo_v1.listar_dispositivos_activos(sujeto_tipo="vendedor")
    vendedores = sorted({str(d["sujeto_id"]).strip().lower() for d in disps if d.get("sujeto_id")})
    creadas = 0
    for email in vendedores:
        res = repo_notifs.encolar(
            dedupe_key=f"tasa:{tipo_tasa.lower()}:{f_tag}:{email}",
            canal="expo_push",
            sujeto_tipo="vendedor",
            sujeto_id=email,
            evento_tipo="cambio_tasa",
            titulo=f"Cambio de tasa {tipo_tasa.upper()} ({var_pct:+.2f}%)",
            cuerpo=f"Tasa {tipo_tasa.upper()} pasó de {tasa_anterior:.2f} a {tasa_nueva:.2f}.",
            datos={"tipo_tasa": tipo_tasa, "anterior": tasa_anterior, "nueva": tasa_nueva},
        )
        if not res.get("duplicada"):
            creadas += 1
    return creadas
