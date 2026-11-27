"""Gestión de Cuentas Zelle de Terceros (Proveedores) para cobros sin pasar por bancos propios."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.exc import IntegrityError

from cxc.db.schema import cuentas_terceros

from .modelos import OperativoInvalido, OperativoNoEncontrado


@dataclass(frozen=True)
class CuentaTercero:
    cuenta_id: int
    alias: str
    titular: str
    correo_telefono: str
    proveedor_odoo_id: int
    proveedor_nombre: str
    diario_odoo_id: int | None
    diario_odoo_nombre: str
    cuenta_cxp_override_id: int | None
    cuenta_cxc_override_id: int | None
    moneda: str
    notas: str
    activo: bool
    creado_por: str
    creado_en: datetime
    actualizado_en: datetime

    def a_dict(self) -> dict[str, Any]:
        return {
            "id": self.cuenta_id,
            "cuenta_id": self.cuenta_id,
            "cuenta_tercero_id": self.cuenta_id,
            "alias": self.alias,
            "titular": self.titular,
            "correo_telefono": self.correo_telefono,
            "email": self.correo_telefono,
            "proveedor_odoo_id": self.proveedor_odoo_id,
            "proveedor_nombre": self.proveedor_nombre,
            "diario_odoo_id": self.diario_odoo_id,
            "diario_odoo_nombre": self.diario_odoo_nombre,
            "cuenta_cxp_override_id": self.cuenta_cxp_override_id,
            "cuenta_cxc_override_id": self.cuenta_cxc_override_id,
            "moneda": self.moneda,
            "notas": self.notas,
            "activo": self.activo,
            "activa": self.activo,
            "creado_por": self.creado_por,
            "creado_en": self.creado_en.isoformat(),
            "actualizado_en": self.actualizado_en.isoformat(),
        }


class RepoCuentasTerceros(Protocol):
    def listar(self, *, solo_activas: bool = False) -> list[CuentaTercero]: ...

    def obtener(self, cuenta_id: int) -> CuentaTercero | None: ...

    def crear(self, datos: dict[str, Any], *, actor: str = "") -> CuentaTercero: ...

    def actualizar(self, cuenta_id: int, cambios: dict[str, Any]) -> CuentaTercero: ...

    def desactivar(self, cuenta_id: int) -> CuentaTercero: ...


def _a_cuenta_tercero(r: Any) -> CuentaTercero:
    return CuentaTercero(
        cuenta_id=int(r.cuenta_id),
        alias=str(r.alias or ""),
        titular=str(r.titular or ""),
        correo_telefono=str(r.correo_telefono or ""),
        proveedor_odoo_id=int(r.proveedor_odoo_id),
        proveedor_nombre=str(r.proveedor_nombre or ""),
        diario_odoo_id=int(r.diario_odoo_id) if r.diario_odoo_id is not None else None,
        diario_odoo_nombre=str(r.diario_odoo_nombre or ""),
        cuenta_cxp_override_id=(
            int(r.cuenta_cxp_override_id) if r.cuenta_cxp_override_id is not None else None
        ),
        cuenta_cxc_override_id=(
            int(r.cuenta_cxc_override_id) if r.cuenta_cxc_override_id is not None else None
        ),
        moneda=str(r.moneda or "USD"),
        notas=str(r.notas or ""),
        activo=bool(r.activo),
        creado_por=str(r.creado_por or "sistema"),
        creado_en=r.creado_en,
        actualizado_en=r.actualizado_en,
    )


def _int_op(v: Any) -> int | None:
    if v in (None, "", False):
        return None
    s = str(v).strip()
    if s.isdigit():
        return int(s)
    raise OperativoInvalido(f"Identificador numérico inválido: {v!r}.")


def _limpiar_campos_cuenta(raw: dict[str, Any], *, es_creacion: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "alias" in raw or es_creacion:
        alias = str(raw.get("alias") or "").strip()
        if not alias:
            raise OperativoInvalido("El alias de la cuenta de tercero es obligatorio.", ["alias"])
        out["alias"] = alias

    if "proveedor_odoo_id" in raw or es_creacion:
        prov_id = _int_op(raw.get("proveedor_odoo_id"))
        if prov_id is None:
            raise OperativoInvalido(
                "Debes seleccionar el proveedor de Odoo asociado a esta cuenta.",
                ["proveedor_odoo_id"],
            )
        out["proveedor_odoo_id"] = prov_id

    for campo_txt in (
        "titular",
        "correo_telefono",
        "proveedor_nombre",
        "diario_odoo_nombre",
        "notas",
    ):
        if campo_txt in raw:
            out[campo_txt] = str(raw.get(campo_txt) or "").strip()
    for campo_id in ("diario_odoo_id", "cuenta_cxp_override_id", "cuenta_cxc_override_id"):
        if campo_id in raw:
            out[campo_id] = _int_op(raw.get(campo_id))
    if "moneda" in raw or es_creacion:
        mon = str(raw.get("moneda") or "USD").strip().upper() or "USD"
        out["moneda"] = mon[:10]
    if "activo" in raw:
        out["activo"] = bool(raw.get("activo"))
    return out


class RepoCuentasTercerosSQL:
    """Repositorio PostgreSQL para ``cuentas_terceros``."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def listar(self, *, solo_activas: bool = False) -> list[CuentaTercero]:
        stmt = select(cuentas_terceros)
        if solo_activas:
            stmt = stmt.where(cuentas_terceros.c.activo.is_(True))
        stmt = stmt.order_by(cuentas_terceros.c.activo.desc(), cuentas_terceros.c.alias.asc())
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [_a_cuenta_tercero(r) for r in rows]

    def obtener(self, cuenta_id: int) -> CuentaTercero | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(cuentas_terceros).where(cuentas_terceros.c.cuenta_id == cuenta_id)
            ).one_or_none()
        return _a_cuenta_tercero(row) if row is not None else None

    def crear(self, datos: dict[str, Any], *, actor: str = "") -> CuentaTercero:
        limpio = _limpiar_campos_cuenta(datos, es_creacion=True)
        ahora = datetime.now()
        vals = {
            "titular": "",
            "correo_telefono": "",
            "proveedor_nombre": "",
            "diario_odoo_nombre": "",
            "notas": "",
            "activo": True,
            **limpio,
            "creado_por": actor or "usuario",
            "creado_en": ahora,
            "actualizado_en": ahora,
        }
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    insert(cuentas_terceros).values(**vals).returning(cuentas_terceros)
                ).one()
        except IntegrityError as e:
            raise OperativoInvalido(
                f"Ya existe una cuenta de tercero con el alias «{vals['alias']}».",
                ["alias"],
            ) from e
        return _a_cuenta_tercero(row)

    def actualizar(self, cuenta_id: int, cambios: dict[str, Any]) -> CuentaTercero:
        actual = self.obtener(cuenta_id)
        if actual is None:
            raise OperativoNoEncontrado(f"Cuenta de tercero #{cuenta_id} no encontrada.")
        limpio = _limpiar_campos_cuenta(cambios, es_creacion=False)
        if not limpio:
            return actual
        limpio["actualizado_en"] = datetime.now()
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    update(cuentas_terceros)
                    .where(cuentas_terceros.c.cuenta_id == cuenta_id)
                    .values(**limpio)
                    .returning(cuentas_terceros)
                ).one()
        except IntegrityError as e:
            raise OperativoInvalido(
                "Ya existe otra cuenta de tercero con ese alias.",
                ["alias"],
            ) from e
        return _a_cuenta_tercero(row)

    def desactivar(self, cuenta_id: int) -> CuentaTercero:
        return self.actualizar(cuenta_id, {"activo": False})


def _norm_txt(texto: str) -> str:
    nfd = unicodedata.normalize("NFD", (texto or "").strip().lower())
    limpio = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return " ".join(limpio.split())


def detectar_cuenta_tercero(
    datos: dict[str, Any], cuentas: Sequence[CuentaTercero]
) -> CuentaTercero | None:
    """Detecta si el pago pertenece a una cuenta Zelle de terceros configurada.

    Prioriza ``cuenta_tercero_id`` explícito; si no viene, intenta casar
    ``beneficiario_tercero``, ``banco``, ``notas`` o ``metodo_pago`` contra
    ``correo_telefono``, ``titular`` o ``alias`` de las cuentas activas.
    """
    if not cuentas:
        return None
    cid_raw = datos.get("cuenta_tercero_id")
    if cid_raw not in (None, "", False) and str(cid_raw).isdigit():
        cid = int(str(cid_raw))
        return next((c for c in cuentas if c.cuenta_id == cid), None)

    activas = [c for c in cuentas if c.activo]
    if not activas:
        return None

    beneficiario_norm = _norm_txt(str(datos.get("beneficiario_tercero") or ""))
    pajar = _norm_txt(
        " ".join(
            str(datos.get(k) or "")
            for k in ("beneficiario_tercero", "banco", "notas", "metodo_pago")
        )
    )
    if not pajar:
        return None

    # 1. Coincidencia exacta por correo/teléfono o alias o titular >= 4 chars
    for c in activas:
        correo = _norm_txt(c.correo_telefono)
        if len(correo) >= 4 and correo in pajar:
            return c
    for c in activas:
        alias_n = _norm_txt(c.alias)
        if alias_n and (alias_n == beneficiario_norm or (len(alias_n) >= 4 and alias_n in pajar)):
            return c
    for c in activas:
        titular_n = _norm_txt(c.titular)
        if len(titular_n) >= 4 and titular_n in pajar:
            return c
    return None
