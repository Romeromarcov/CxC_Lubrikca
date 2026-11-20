"""Servicio y lógica pura para ajustes por regla y descuentos especiales en Odoo existente."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Engine, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from cxc.db.schema import (
    app_settings,
    documentos_comerciales,
    orden_descuentos_ajustes,
    ordenes_venta,
)
from cxc.decimal_utils import q2

ACCION_EXCLUIR_MOTOR = "EXCLUIR_MOTOR"
ACCION_ESPECIAL = "ESPECIAL"
SETTING_LIMPIEZA_ACTIVA = "permitir_ajuste_descuentos_odoo_existentes"


class AjusteDescuentoError(ValueError):
    """Error de validación al gestionar ajustes de descuento."""


class AjusteDescuentoDeshabilitado(PermissionError):
    """Se intentó ajustar descuentos cuando el período de limpieza está cerrado."""


def _dec(val: Any) -> Decimal:
    if val is None or val == "":
        return Decimal("0")
    try:
        d = Decimal(str(val))
        return d if d.is_finite() else Decimal("0")
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _attr(obj: Any, campo: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(campo, default)
    return getattr(obj, campo, default)


def _normalizar_motor_item(item: Any) -> dict[str, Any]:
    origen = str(_attr(item, "origen", "") or "").strip()
    regla_id = str(_attr(item, "regla_id", "") or "").strip()
    descripcion = str(_attr(item, "descripcion", "") or origen or "Regla motor").strip()
    monto = q2(_dec(_attr(item, "monto", 0)))
    pct_raw = _attr(item, "porcentaje", None)
    pct_float = float(pct_raw) if pct_raw is not None else None
    base_raw = _attr(item, "base", None)
    base_float = float(q2(_dec(base_raw))) if base_raw is not None else None
    return {
        "origen": origen,
        "regla_id": regla_id,
        "descripcion": descripcion,
        "porcentaje": pct_float,
        "base": base_float,
        "monto_usd": float(monto),
    }


def _row_ajuste_a_dict(r: Any) -> dict[str, Any]:
    return {
        "ajuste_id": int(r.ajuste_id),
        "so_id": str(r.so_id),
        "accion": str(r.accion),
        "descuento_origen": str(r.descuento_origen or ""),
        "regla_id": str(r.regla_id or ""),
        "tipo_valor": str(r.tipo_valor or "porcentaje"),
        "porcentaje": float(r.porcentaje) if r.porcentaje is not None else None,
        "monto_usd": float(r.monto_usd) if r.monto_usd is not None else None,
        "descripcion": str(r.descripcion or ""),
        "motivo": str(r.motivo or ""),
        "activo": bool(r.activo),
        "creado_por": str(r.creado_por or ""),
        "creado_en": r.creado_en.isoformat()[:19] if r.creado_en else "",
        "anulado_por": str(r.anulado_por or ""),
        "anulado_en": r.anulado_en.isoformat()[:19] if r.anulado_en else "",
    }


def aplicar_ajustes_a_descuentos_orden(
    descuentos_motor: Sequence[Any],
    subtotal_base_usd: Any,
    ajustes_activos: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Combina los descuentos sugeridos por el motor con exclusiones y especiales activos."""
    base_dec = max(Decimal("0"), _dec(subtotal_base_usd))
    excl_exact: dict[tuple[str, str], dict[str, Any]] = {}
    excl_origen: dict[str, dict[str, Any]] = {}
    especiales_raw: list[dict[str, Any]] = []

    for aj in ajustes_activos:
        if not aj.get("activo", True):
            continue
        acc = str(aj.get("accion") or "").upper()
        if acc == ACCION_EXCLUIR_MOTOR:
            org = str(aj.get("descuento_origen") or "").strip().lower()
            rid = str(aj.get("regla_id") or "").strip().lower()
            if rid:
                excl_exact[(org, rid)] = aj
            elif org:
                excl_origen[org] = aj
        elif acc == ACCION_ESPECIAL:
            especiales_raw.append(aj)

    motor_items: list[dict[str, Any]] = []
    tot_motor_orig = Decimal("0")
    tot_excluido = Decimal("0")
    tot_motor_vigente = Decimal("0")

    for raw in descuentos_motor or ():
        it = _normalizar_motor_item(raw)
        monto_dec = q2(_dec(it["monto_usd"]))
        org_l = it["origen"].lower()
        rid_l = it["regla_id"].lower()
        aj_excl = excl_exact.get((org_l, rid_l)) or excl_origen.get(org_l)
        if aj_excl is None and not org_l and rid_l:
            aj_excl = excl_exact.get(("", rid_l))

        excluido = aj_excl is not None
        tot_motor_orig += monto_dec
        if excluido:
            tot_excluido += monto_dec
        else:
            tot_motor_vigente += monto_dec

        motor_items.append(
            {
                **it,
                "excluido": excluido,
                "ajuste_exclusion_id": aj_excl.get("ajuste_id") if aj_excl else None,
                "motivo_exclusion": str(aj_excl.get("motivo") or "") if aj_excl else "",
                "excluido_por": str(aj_excl.get("creado_por") or "") if aj_excl else "",
            }
        )

    especiales: list[dict[str, Any]] = []
    tot_especial = Decimal("0")
    for esp in especiales_raw:
        t_val = str(esp.get("tipo_valor") or "porcentaje").strip().lower()
        if t_val == "porcentaje":
            pct = _dec(esp.get("porcentaje"))
            monto_esp = q2(base_dec * (pct / Decimal("100")))
        else:
            monto_esp = q2(_dec(esp.get("monto_usd")))
        monto_esp = max(Decimal("0"), monto_esp)
        tot_especial += monto_esp
        especiales.append(
            {
                **esp,
                "monto_calculado_usd": float(monto_esp),
            }
        )

    tot_ajustado = max(Decimal("0"), q2(tot_motor_vigente + tot_especial))
    count_excl = sum(1 for m in motor_items if m["excluido"])
    return {
        "motor_items": motor_items,
        "especiales": especiales,
        "total_motor_original": float(q2(tot_motor_orig)),
        "total_motor_vigente": float(q2(tot_motor_vigente)),
        "total_excluido": float(q2(tot_excluido)),
        "total_especial": float(q2(tot_especial)),
        "total_descuento_ajustado": float(tot_ajustado),
        "tiene_ajustes": bool(count_excl > 0 or len(especiales) > 0),
        "count_excluidos": count_excl,
        "count_especiales": len(especiales),
    }


class ServicioAjustesDescuentos:
    """Gestiona exclusiones individuales del motor y descuentos comerciales especiales."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def limpieza_activa(self) -> bool:
        try:
            with self._engine.connect() as conn:
                if not sa.inspect(conn).has_table("app_settings"):
                    return True
                val = conn.execute(
                    select(app_settings.c.value).where(
                        app_settings.c.key == SETTING_LIMPIEZA_ACTIVA
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError:
            return True
        if val is None:
            return True
        return str(val).strip().lower() not in ("false", "0", "no", "off")

    def set_limpieza_activa(self, activa: bool) -> bool:
        valor = "true" if activa else "false"
        with self._engine.begin() as conn:
            dialect = conn.dialect.name
            if dialect == "postgresql":
                stmt = (
                    pg_insert(app_settings)
                    .values(key=SETTING_LIMPIEZA_ACTIVA, value=valor)
                    .on_conflict_do_update(index_elements=["key"], set_={"value": valor})
                )
                conn.execute(stmt)
            else:
                existe = conn.execute(
                    select(app_settings.c.key).where(app_settings.c.key == SETTING_LIMPIEZA_ACTIVA)
                ).scalar_one_or_none()
                if existe is None:
                    conn.execute(
                        insert(app_settings).values(key=SETTING_LIMPIEZA_ACTIVA, value=valor)
                    )
                else:
                    conn.execute(
                        update(app_settings)
                        .where(app_settings.c.key == SETTING_LIMPIEZA_ACTIVA)
                        .values(value=valor)
                    )
        return activa

    def es_orden_odoo_existente(self, so_id: str) -> bool:
        """Verifica que la orden exista y NO haya nacido en CxC."""
        so = str(so_id or "").strip()
        if not so:
            raise LookupError("Orden inválida.")
        with self._engine.connect() as conn:
            row_o = conn.execute(
                select(ordenes_venta.c.so_id).where(ordenes_venta.c.so_id == so)
            ).first()
            if row_o is None:
                raise LookupError(f"Orden {so} no encontrada en ordenes_venta.")
            if sa.inspect(conn).has_table("documentos_comerciales"):
                row_dc = conn.execute(
                    select(documentos_comerciales.c.documento_id).where(
                        documentos_comerciales.c.odoo_order_name == so
                    )
                ).first()
                if row_dc is not None:
                    raise AjusteDescuentoError(
                        f"La orden {so} nació en CxC/Bot; los ajustes temporales aplican "
                        "únicamente a órdenes existentes nacidas en Odoo."
                    )
        return True

    def listar_por_so(self, so_id: str, *, solo_activos: bool = False) -> list[dict[str, Any]]:
        so = str(so_id or "").strip()
        if not so:
            return []
        try:
            with self._engine.connect() as conn:
                if not sa.inspect(conn).has_table("orden_descuentos_ajustes"):
                    return []
                q = select(orden_descuentos_ajustes).where(orden_descuentos_ajustes.c.so_id == so)
                if solo_activos:
                    q = q.where(orden_descuentos_ajustes.c.activo.is_(True))
                q = q.order_by(orden_descuentos_ajustes.c.ajuste_id.desc())
                return [_row_ajuste_a_dict(r) for r in conn.execute(q).all()]
        except SQLAlchemyError:
            return []

    def listar_activos_por_so(self) -> dict[str, list[dict[str, Any]]]:
        por_so: dict[str, list[dict[str, Any]]] = {}
        try:
            with self._engine.connect() as conn:
                if not sa.inspect(conn).has_table("orden_descuentos_ajustes"):
                    return por_so
                rows = conn.execute(
                    select(orden_descuentos_ajustes)
                    .where(orden_descuentos_ajustes.c.activo.is_(True))
                    .order_by(orden_descuentos_ajustes.c.ajuste_id.asc())
                ).all()
                for r in rows:
                    d = _row_ajuste_a_dict(r)
                    por_so.setdefault(d["so_id"], []).append(d)
        except SQLAlchemyError:
            return por_so
        return por_so

    def _exigir_editable(self, so_id: str) -> None:
        if not self.limpieza_activa():
            raise AjusteDescuentoDeshabilitado(
                "El período temporal de limpieza de descuentos en órdenes Odoo está cerrado."
            )
        self.es_orden_odoo_existente(so_id)

    def excluir_descuento_motor(
        self,
        *,
        so_id: str,
        descuento_origen: str,
        regla_id: str = "",
        motivo: str = "",
        actor: str = "usuario",
    ) -> dict[str, Any]:
        so = str(so_id or "").strip()
        self._exigir_editable(so)
        org = str(descuento_origen or "").strip()
        rid = str(regla_id or "").strip()
        if not org and not rid:
            raise AjusteDescuentoError(
                "Indica descuento_origen o regla_id para excluir la regla individual."
            )

        ahora = datetime.now()
        with self._engine.begin() as conn:
            activos = conn.execute(
                select(orden_descuentos_ajustes).where(
                    orden_descuentos_ajustes.c.so_id == so,
                    orden_descuentos_ajustes.c.accion == ACCION_EXCLUIR_MOTOR,
                    orden_descuentos_ajustes.c.activo.is_(True),
                )
            ).all()
            for r in activos:
                if (
                    str(r.descuento_origen or "").strip().lower() == org.lower()
                    and str(r.regla_id or "").strip().lower() == rid.lower()
                ):
                    return _row_ajuste_a_dict(r)

            row = conn.execute(
                insert(orden_descuentos_ajustes)
                .values(
                    so_id=so,
                    accion=ACCION_EXCLUIR_MOTOR,
                    descuento_origen=org,
                    regla_id=rid,
                    tipo_valor="exclusion",
                    porcentaje=None,
                    monto_usd=None,
                    descripcion=f"Excluir motor: {org or rid}",
                    motivo=(motivo or "No otorgado en negociación comercial").strip(),
                    activo=True,
                    creado_por=actor.strip() or "usuario",
                    creado_en=ahora,
                    anulado_por="",
                    anulado_en=None,
                )
                .returning(orden_descuentos_ajustes)
            ).one()
        return _row_ajuste_a_dict(row)

    def agregar_descuento_especial(
        self,
        *,
        so_id: str,
        tipo_valor: str = "porcentaje",
        porcentaje: Any = None,
        monto_usd: Any = None,
        descripcion: str = "",
        motivo: str = "",
        actor: str = "usuario",
    ) -> dict[str, Any]:
        so = str(so_id or "").strip()
        self._exigir_editable(so)
        t_val = str(tipo_valor or "porcentaje").strip().lower()
        if t_val not in ("porcentaje", "monto_fijo"):
            raise AjusteDescuentoError("tipo_valor debe ser 'porcentaje' o 'monto_fijo'.")

        pct_dec: Decimal | None = None
        usd_dec: Decimal | None = None
        if t_val == "porcentaje":
            pct_dec = _dec(porcentaje)
            if pct_dec <= Decimal("0") or pct_dec > Decimal("100"):
                raise AjusteDescuentoError("El porcentaje especial debe ser > 0 y <= 100.")
        else:
            usd_dec = q2(_dec(monto_usd))
            if usd_dec <= Decimal("0"):
                raise AjusteDescuentoError("El monto fijo especial (USD) debe ser mayor a cero.")

        desc_txt = (descripcion or "").strip()
        if not desc_txt:
            desc_txt = (
                f"Descuento especial {float(pct_dec):.2f}%"
                if t_val == "porcentaje" and pct_dec is not None
                else f"Descuento especial ${float(usd_dec or 0):,.2f}"
            )

        ahora = datetime.now()
        with self._engine.begin() as conn:
            row = conn.execute(
                insert(orden_descuentos_ajustes)
                .values(
                    so_id=so,
                    accion=ACCION_ESPECIAL,
                    descuento_origen="especial",
                    regla_id="",
                    tipo_valor=t_val,
                    porcentaje=pct_dec,
                    monto_usd=usd_dec,
                    descripcion=desc_txt,
                    motivo=(motivo or "Negociación comercial especial").strip(),
                    activo=True,
                    creado_por=actor.strip() or "usuario",
                    creado_en=ahora,
                    anulado_por="",
                    anulado_en=None,
                )
                .returning(orden_descuentos_ajustes)
            ).one()
        return _row_ajuste_a_dict(row)

    def anular_ajuste(
        self, *, so_id: str, ajuste_id: int, actor: str = "usuario"
    ) -> dict[str, Any]:
        so = str(so_id or "").strip()
        self._exigir_editable(so)
        ahora = datetime.now()
        with self._engine.begin() as conn:
            row = conn.execute(
                update(orden_descuentos_ajustes)
                .where(
                    orden_descuentos_ajustes.c.ajuste_id == int(ajuste_id),
                    orden_descuentos_ajustes.c.so_id == so,
                    orden_descuentos_ajustes.c.activo.is_(True),
                )
                .values(activo=False, anulado_por=actor.strip() or "usuario", anulado_en=ahora)
                .returning(orden_descuentos_ajustes)
            ).one_or_none()
        if row is None:
            raise LookupError(f"Ajuste #{ajuste_id} activo no encontrado para {so}.")
        return _row_ajuste_a_dict(row)
