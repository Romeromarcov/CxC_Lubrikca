"""Catálogo de cuentas receptoras propias de Lubrikca y validador determinístico de destino."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.exc import IntegrityError

from cxc.bancos.diarios import DIARIOS
from cxc.capturas.modelos import normalizar_rif
from cxc.db.schema import cuentas_receptoras_lubrikca

from .modelos import OperativoInvalido, OperativoNoEncontrado

TIPOS_CANAL_CUENTA: tuple[str, ...] = (
    "banco",
    "pago_movil",
    "zelle",
    "binance",
    "efectivo",
    "otro",
)

# Catálogo oficial de códigos bancarios SUDEBAN (Venezuela) + aliases comunes
BANCOS_SUDEBAN: dict[str, str] = {
    "0102": "Banco de Venezuela",
    "0104": "Venezolano de Crédito",
    "0105": "Banco Mercantil",
    "0108": "Banco Provincial",
    "0114": "Bancaribe",
    "0115": "Banco Exterior",
    "0128": "Banco Caroní",
    "0134": "Banesco",
    "0137": "Banco Sofitasa",
    "0138": "Banco Plaza",
    "0146": "Bangente",
    "0151": "BFC Banco Fondo Común",
    "0156": "100% Banco",
    "0157": "DelSur",
    "0163": "Banco del Tesoro",
    "0166": "Banco Agrícola de Venezuela",
    "0168": "Bancrecer",
    "0169": "R4 Banco Microfinanciero",
    "0171": "Banco Activo",
    "0172": "Bancamiga",
    "0173": "Banco Internacional de Desarrollo",
    "0174": "Banplus",
    "0175": "Banco Digital de los Trabajadores",
    "0177": "Banfanb",
    "0178": "N58 Banco Digital",
    "0191": "Banco Nacional de Crédito",
}

_ALIASES_SUDEBAN: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("0102", ("banco de venezuela", "bdv", "venezuela")),
    ("0104", ("venezolano de credito", "banco venezolano de credito", "bvc", "venezolano credito")),
    ("0105", ("mercantil", "banco mercantil")),
    ("0108", ("provincial", "bbva", "bbva provincial", "banco provincial")),
    ("0114", ("bancaribe", "banco del caribe")),
    ("0115", ("exterior", "banco exterior")),
    ("0128", ("caroni", "banco caroni")),
    ("0134", ("banesco", "bban")),
    ("0137", ("sofitasa", "banco sofitasa")),
    ("0138", ("plaza", "banco plaza")),
    ("0146", ("bangente",)),
    ("0151", ("fondo comun", "bfc")),
    ("0156", ("100% banco", "100x100", "cien por ciento")),
    ("0157", ("delsur", "del sur")),
    ("0163", ("tesoro", "banco del tesoro")),
    ("0166", ("agricola de venezuela",)),
    ("0168", ("bancrecer",)),
    ("0169", ("mi banco", "mibanco", "r4 banco")),
    ("0171", ("banco activo",)),
    ("0172", ("bancamiga", "bami")),
    ("0173", ("internacional de desarrollo", "bid")),
    ("0174", ("banplus",)),
    ("0175", ("bdt", "bicentenario", "trabajadores", "digital de los trabajadores")),
    ("0177", ("banfanb", "fanb")),
    ("0178", ("n58",)),
    ("0191", ("bnc", "nacional de credito", "banco nacional de credito")),
)

_DIARIOS_META_SUDEBAN: dict[str, tuple[str, str]] = {
    "VENEZUELA": ("0102", "Banco de Venezuela"),
    "BANCAMIGA": ("0172", "Bancamiga"),
    "BDT": ("0175", "Banco Digital de los Trabajadores"),
    "BANESCO": ("0134", "Banesco"),
    "PROVINCIAL": ("0108", "Banco Provincial"),
    "BNC": ("0191", "Banco Nacional de Crédito"),
}


def _sin_acentos(texto: str) -> str:
    txt = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(ch for ch in txt if not unicodedata.combining(ch)).lower().strip()


def normalizar_cuenta_bancaria(valor: Any) -> str:
    """Deja únicamente los dígitos del número de cuenta."""
    return re.sub(r"\D+", "", str(valor or ""))


def codigo_sudeban_de_cuenta(cuenta: Any) -> str:
    """Extrae el código SUDEBAN de 4 dígitos si la cuenta empieza con un banco conocido."""
    norm = normalizar_cuenta_bancaria(cuenta)
    if len(norm) >= 4 and norm[:4] in BANCOS_SUDEBAN:
        return norm[:4]
    return ""


def codigo_sudeban_de_banco(valor: Any) -> str:
    """Resuelve el código SUDEBAN de 4 dígitos desde código o nombre del banco."""
    raw = str(valor or "").strip()
    if not raw:
        return ""
    for tok in re.findall(r"\b\d{4}\b", raw):
        tok_s = str(tok)
        if tok_s in BANCOS_SUDEBAN:
            return tok_s
    limpio = _sin_acentos(raw)
    if limpio in BANCOS_SUDEBAN:
        return limpio
    # Evitar falso positivo por subcadena en descripciones explícitas de otro canal
    for codigo, aliases in _ALIASES_SUDEBAN:
        if any(a in limpio for a in aliases):
            return codigo
    return ""


def normalizar_telefono_ve(valor: Any) -> str:
    """Normaliza teléfonos venezolanos a formato 04XXXXXXXX (11 dígitos) cuando aplica."""
    dig = re.sub(r"\D+", "", str(valor or ""))
    if not dig:
        return ""
    if dig.startswith("58") and len(dig) >= 12:
        dig = "0" + dig[2:]
    elif len(dig) == 10 and dig.startswith(("4", "2")):
        dig = "0" + dig
    return dig


def normalizar_rif_cedula(valor: Any) -> str:
    """Normaliza RIF o Cédula venezolana quitando guiones y espacios en mayúsculas."""
    raw = str(valor or "").strip().upper()
    if not raw:
        return ""
    try:
        return str(normalizar_rif(raw)).replace("-", "")
    except ValueError:
        return re.sub(r"[^A-Z0-9]+", "", raw)


def _nucleo_numerico_rif(rif_norm: str) -> str:
    return re.sub(r"^[VJEGP]+", "", rif_norm)


def _mismo_rif(rif_a: str, rif_b: str) -> bool:
    a = normalizar_rif_cedula(rif_a)
    b = normalizar_rif_cedula(rif_b)
    if not a or not b:
        return False
    if a == b:
        return True
    na = _nucleo_numerico_rif(a)
    nb = _nucleo_numerico_rif(b)
    # Si al menos uno no trajo letra de prefijo (cédula numérica pura en recibo), comparar dígitos
    if na and nb and (a.isdigit() or b.isdigit()):
        return na == nb
    return False


def normalizar_email(valor: Any) -> str:
    return str(valor or "").strip().lower()


@dataclass(frozen=True)
class CuentaReceptora:
    cuenta_id: int
    nombre: str
    tipo_canal: str
    banco_codigo_sudeban: str
    banco_nombre: str
    numero_cuenta: str
    numero_cuenta_norm: str
    titular_nombre: str
    titular_rif: str
    titular_rif_norm: str
    telefono: str
    telefono_norm: str
    email_zelle: str
    email_norm: str
    binance_id: str
    moneda: str
    diario_odoo_id: int | None
    diario_odoo_codigo: str
    diario_odoo_nombre: str
    activa: bool
    es_propia: bool
    notas: str
    creado_por: str
    creado_en: datetime
    actualizado_en: datetime

    def a_dict(self) -> dict[str, Any]:
        return {
            "id": self.cuenta_id,
            "cuenta_id": self.cuenta_id,
            "nombre": self.nombre,
            "tipo_canal": self.tipo_canal,
            "banco_codigo_sudeban": self.banco_codigo_sudeban,
            "banco_nombre": self.banco_nombre,
            "numero_cuenta": self.numero_cuenta,
            "titular_nombre": self.titular_nombre,
            "titular_rif": self.titular_rif,
            "telefono": self.telefono,
            "email_zelle": self.email_zelle,
            "binance_id": self.binance_id,
            "moneda": self.moneda,
            "diario_odoo_id": self.diario_odoo_id,
            "diario_odoo_codigo": self.diario_odoo_codigo,
            "diario_odoo_nombre": self.diario_odoo_nombre,
            "activa": self.activa,
            "es_propia": self.es_propia,
            "notas": self.notas,
            "creado_por": self.creado_por,
            "creado_en": self.creado_en.isoformat(),
            "actualizado_en": self.actualizado_en.isoformat(),
        }


def _a_cuenta_receptora(row: Any) -> CuentaReceptora:
    return CuentaReceptora(
        cuenta_id=int(row.cuenta_id),
        nombre=str(row.nombre or ""),
        tipo_canal=str(row.tipo_canal or "banco"),
        banco_codigo_sudeban=str(row.banco_codigo_sudeban or ""),
        banco_nombre=str(row.banco_nombre or ""),
        numero_cuenta=str(row.numero_cuenta or ""),
        numero_cuenta_norm=str(row.numero_cuenta_norm or ""),
        titular_nombre=str(row.titular_nombre or ""),
        titular_rif=str(row.titular_rif or ""),
        titular_rif_norm=str(row.titular_rif_norm or ""),
        telefono=str(row.telefono or ""),
        telefono_norm=str(row.telefono_norm or ""),
        email_zelle=str(row.email_zelle or ""),
        email_norm=str(row.email_norm or ""),
        binance_id=str(row.binance_id or ""),
        moneda=str(row.moneda or "VES"),
        diario_odoo_id=(int(row.diario_odoo_id) if row.diario_odoo_id is not None else None),
        diario_odoo_codigo=str(row.diario_odoo_codigo or ""),
        diario_odoo_nombre=str(row.diario_odoo_nombre or ""),
        activa=bool(row.activa),
        es_propia=bool(row.es_propia),
        notas=str(row.notas or ""),
        creado_por=str(row.creado_por or ""),
        creado_en=row.creado_en,
        actualizado_en=row.actualizado_en,
    )


def _limpiar_campos_cr(datos: dict[str, Any], *, es_creacion: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "nombre" in datos or es_creacion:
        nom = str(datos.get("nombre") or "").strip()
        if not nom:
            raise OperativoInvalido(
                "El nombre identificativo de la cuenta receptora es obligatorio.",
                ["nombre"],
            )
        out["nombre"] = nom[:120]

    if "tipo_canal" in datos or es_creacion:
        canal = str(datos.get("tipo_canal") or "banco").strip().lower()
        if canal not in TIPOS_CANAL_CUENTA:
            canal = "banco"
        out["tipo_canal"] = canal

    if "numero_cuenta" in datos:
        cta = str(datos.get("numero_cuenta") or "").strip()[:60]
        out["numero_cuenta"] = cta
        out["numero_cuenta_norm"] = normalizar_cuenta_bancaria(cta)

    if "banco_codigo_sudeban" in datos or "banco_nombre" in datos or "numero_cuenta" in datos:
        cod = str(datos.get("banco_codigo_sudeban") or "").strip()
        bnom = str(datos.get("banco_nombre") or "").strip()
        if not cod and "numero_cuenta" in out:
            cod = codigo_sudeban_de_cuenta(out["numero_cuenta"])
        if not cod and bnom:
            cod = codigo_sudeban_de_banco(bnom)
        if cod and not bnom and cod in BANCOS_SUDEBAN:
            bnom = BANCOS_SUDEBAN[cod]
        if "banco_codigo_sudeban" in datos or cod:
            out["banco_codigo_sudeban"] = cod[:8]
        if "banco_nombre" in datos or bnom:
            out["banco_nombre"] = bnom[:120]

    if "titular_nombre" in datos:
        out["titular_nombre"] = str(datos.get("titular_nombre") or "").strip()[:160]

    if "titular_rif" in datos:
        rif_val = str(datos.get("titular_rif") or "").strip()[:30]
        out["titular_rif"] = rif_val
        out["titular_rif_norm"] = normalizar_rif_cedula(rif_val)

    if "telefono" in datos:
        tel = str(datos.get("telefono") or "").strip()[:32]
        out["telefono"] = tel
        out["telefono_norm"] = normalizar_telefono_ve(tel)

    if "email_zelle" in datos:
        em = str(datos.get("email_zelle") or "").strip()[:160]
        out["email_zelle"] = em
        out["email_norm"] = normalizar_email(em)

    if "binance_id" in datos:
        out["binance_id"] = str(datos.get("binance_id") or "").strip()[:100]

    if "moneda" in datos or es_creacion:
        mon = str(datos.get("moneda") or "VES").strip().upper()[:8]
        out["moneda"] = mon if mon in ("USD", "VES", "EUR") else "VES"

    if "diario_odoo_id" in datos:
        raw_d = datos.get("diario_odoo_id")
        if raw_d in (None, "", False):
            out["diario_odoo_id"] = None
        elif str(raw_d).isdigit():
            out["diario_odoo_id"] = int(str(raw_d))
        else:
            raise OperativoInvalido("diario_odoo_id inválido.", ["diario_odoo_id"])

    if "diario_odoo_codigo" in datos:
        out["diario_odoo_codigo"] = str(datos.get("diario_odoo_codigo") or "").strip()[:32]
    if "diario_odoo_nombre" in datos:
        out["diario_odoo_nombre"] = str(datos.get("diario_odoo_nombre") or "").strip()[:160]
    if "notas" in datos:
        out["notas"] = str(datos.get("notas") or "").strip()
    if "activa" in datos:
        out["activa"] = bool(datos.get("activa"))
    if "es_propia" in datos:
        out["es_propia"] = bool(datos.get("es_propia"))

    return out


class RepoCuentasReceptorasSQL:
    """Repositorio SQL para ``cuentas_receptoras_lubrikca``."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def listar(
        self, *, solo_activas: bool = False, solo_propias: bool = False
    ) -> list[CuentaReceptora]:
        stmt = select(cuentas_receptoras_lubrikca)
        if solo_activas:
            stmt = stmt.where(cuentas_receptoras_lubrikca.c.activa.is_(True))
        if solo_propias:
            stmt = stmt.where(cuentas_receptoras_lubrikca.c.es_propia.is_(True))
        stmt = stmt.order_by(
            cuentas_receptoras_lubrikca.c.activa.desc(),
            cuentas_receptoras_lubrikca.c.nombre.asc(),
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [_a_cuenta_receptora(r) for r in rows]

    def obtener(self, cuenta_id: int) -> CuentaReceptora | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(cuentas_receptoras_lubrikca).where(
                    cuentas_receptoras_lubrikca.c.cuenta_id == cuenta_id
                )
            ).one_or_none()
        return _a_cuenta_receptora(row) if row is not None else None

    def crear(self, datos: dict[str, Any], *, actor: str = "") -> CuentaReceptora:
        limpio = _limpiar_campos_cr(datos, es_creacion=True)
        ahora = datetime.now()
        vals = {
            "banco_codigo_sudeban": "",
            "banco_nombre": "",
            "numero_cuenta": "",
            "numero_cuenta_norm": "",
            "titular_nombre": "",
            "titular_rif": "",
            "titular_rif_norm": "",
            "telefono": "",
            "telefono_norm": "",
            "email_zelle": "",
            "email_norm": "",
            "binance_id": "",
            "diario_odoo_id": None,
            "diario_odoo_codigo": "",
            "diario_odoo_nombre": "",
            "activa": True,
            "es_propia": True,
            "notas": "",
            **limpio,
            "creado_por": actor or "sistema",
            "creado_en": ahora,
            "actualizado_en": ahora,
        }
        try:
            with self._engine.begin() as conn:
                row = conn.execute(
                    insert(cuentas_receptoras_lubrikca)
                    .values(**vals)
                    .returning(cuentas_receptoras_lubrikca)
                ).one()
        except IntegrityError as e:
            raise OperativoInvalido(
                f"Ya existe una cuenta receptora con conflicto en {vals['nombre']}.",
                ["nombre"],
            ) from e
        return _a_cuenta_receptora(row)

    def actualizar(self, cuenta_id: int, cambios: dict[str, Any]) -> CuentaReceptora:
        actual = self.obtener(cuenta_id)
        if actual is None:
            raise OperativoNoEncontrado(f"Cuenta receptora #{cuenta_id} no encontrada.")
        limpio = _limpiar_campos_cr(cambios, es_creacion=False)
        if not limpio:
            return actual
        with self._engine.begin() as conn:
            row = conn.execute(
                update(cuentas_receptoras_lubrikca)
                .where(cuentas_receptoras_lubrikca.c.cuenta_id == cuenta_id)
                .values(**limpio, actualizado_en=datetime.now())
                .returning(cuentas_receptoras_lubrikca)
            ).one()
        return _a_cuenta_receptora(row)

    def desactivar(self, cuenta_id: int) -> CuentaReceptora:
        return self.actualizar(cuenta_id, {"activa": False})

    def sembrar_desde_diarios(
        self,
        diarios_extra: dict[str, int] | None = None,
        *,
        actor: str = "semilla",
    ) -> int:
        """Puebla cuentas propias iniciales desde ``DIARIOS`` y ``diarios_extra``."""
        existentes = self.listar()
        por_diario = {c.diario_odoo_id for c in existentes if c.diario_odoo_id is not None}
        por_nombre = {_sin_acentos(c.nombre) for c in existentes}
        creadas = 0

        for clave, d in DIARIOS.items():
            jid = d.get("journal_id")
            if jid is None or int(jid) in por_diario:
                continue
            cod_sud, b_nom = _DIARIOS_META_SUDEBAN.get(clave, ("", ""))
            canal = "binance" if clave == "BINANCE" else ("zelle" if "ZELLE" in clave else "banco")
            nombre = b_nom or str(d.get("nombre") or clave)
            if clave == "BINANCE":
                nombre = "Binance Lubrikca"
            elif clave == "ZELLE_FLUASA":
                nombre = "Zelle Fluasa"
            if _sin_acentos(nombre) in por_nombre:
                continue
            self.crear(
                {
                    "nombre": nombre,
                    "tipo_canal": canal,
                    "banco_codigo_sudeban": cod_sud,
                    "banco_nombre": b_nom or str(d.get("nombre") or ""),
                    "moneda": d.get("moneda") or "VES",
                    "diario_odoo_id": int(jid),
                    "diario_odoo_codigo": d.get("codigo") or "",
                    "diario_odoo_nombre": d.get("nombre") or "",
                    "activa": True,
                    "es_propia": True,
                },
                actor=actor,
            )
            por_diario.add(int(jid))
            por_nombre.add(_sin_acentos(nombre))
            creadas += 1

        for k, jid in sorted((diarios_extra or {}).items()):
            if int(jid) in por_diario:
                continue
            kl = _sin_acentos(k)
            canal = (
                "efectivo"
                if "efectivo" in kl or "caja" in kl
                else ("binance" if "binance" in kl else ("zelle" if "zelle" in kl else "banco"))
            )
            cod_sud = codigo_sudeban_de_banco(k)
            nombre = k.replace("_", " ").title()
            if _sin_acentos(nombre) in por_nombre:
                continue
            self.crear(
                {
                    "nombre": nombre,
                    "tipo_canal": canal,
                    "banco_codigo_sudeban": cod_sud,
                    "banco_nombre": BANCOS_SUDEBAN.get(cod_sud, nombre),
                    "moneda": "USD" if "usd" in kl or canal in ("binance", "zelle") else "VES",
                    "diario_odoo_id": int(jid),
                    "diario_odoo_codigo": str(k).upper()[:10],
                    "diario_odoo_nombre": nombre,
                    "activa": True,
                    "es_propia": True,
                },
                actor=actor,
            )
            por_diario.add(int(jid))
            creadas += 1

        return creadas

    def sincronizar_desde_odoo(
        self, escritor_odoo: Any, *, actor: str = "odoo_sync"
    ) -> dict[str, int]:
        """Sincroniza diarios ``bank``/``cash`` y números de cuenta desde Odoo."""
        if escritor_odoo is None:
            return {"creadas": 0, "actualizadas": 0}

        campos_j = escritor_odoo.campos("account.journal")
        fields = [
            f
            for f in ("id", "name", "code", "type", "currency_id", "bank_account_id", "bank_id")
            if f in campos_j
        ]
        diarios = escritor_odoo._call(
            "account.journal",
            "search_read",
            [[["type", "in", ["bank", "cash"]]]],
            {"fields": fields or ["id", "name", "code", "type"], "limit": 80},
        )
        existentes = {c.diario_odoo_id: c for c in self.listar() if c.diario_odoo_id is not None}
        creadas = 0
        actualizadas = 0
        for j in diarios or []:
            jid = int(j["id"])
            nombre_j = str(j.get("name") or f"Diario #{jid}")
            codigo_j = str(j.get("code") or "")
            j_type = str(j.get("type") or "bank")
            cur = j.get("currency_id")
            mon = str(cur[1]).upper() if isinstance(cur, list | tuple) and len(cur) > 1 else "VES"
            num_cta = ""
            ba = j.get("bank_account_id")
            if isinstance(ba, list | tuple) and ba and str(ba[0]).isdigit():
                try:
                    b_rows = escritor_odoo._call(
                        "res.partner.bank",
                        "read",
                        [[int(ba[0])]],
                        {"fields": ["id", "acc_number", "bank_name"]},
                    )
                    if b_rows:
                        num_cta = str(b_rows[0].get("acc_number") or "")
                except Exception:
                    num_cta = str(ba[1]) if len(ba) > 1 else ""

            cod_sud = codigo_sudeban_de_cuenta(num_cta) or codigo_sudeban_de_banco(nombre_j)
            jl = _sin_acentos(nombre_j)
            canal = (
                "efectivo"
                if j_type == "cash" or "efectivo" in jl or "caja" in jl
                else ("binance" if "binance" in jl else ("zelle" if "zelle" in jl else "banco"))
            )
            prev = existentes.get(jid)
            if prev is None:
                self.crear(
                    {
                        "nombre": nombre_j,
                        "tipo_canal": canal,
                        "banco_codigo_sudeban": cod_sud,
                        "banco_nombre": BANCOS_SUDEBAN.get(cod_sud, nombre_j),
                        "numero_cuenta": num_cta,
                        "moneda": mon if mon in ("USD", "VES", "EUR") else "VES",
                        "diario_odoo_id": jid,
                        "diario_odoo_codigo": codigo_j,
                        "diario_odoo_nombre": nombre_j,
                        "activa": True,
                        "es_propia": True,
                    },
                    actor=actor,
                )
                creadas += 1
            else:
                cambios: dict[str, Any] = {}
                if num_cta and not prev.numero_cuenta:
                    cambios["numero_cuenta"] = num_cta
                if cod_sud and not prev.banco_codigo_sudeban:
                    cambios["banco_codigo_sudeban"] = cod_sud
                if nombre_j and not prev.diario_odoo_nombre:
                    cambios["diario_odoo_nombre"] = nombre_j
                if cambios:
                    self.actualizar(prev.cuenta_id, cambios)
                    actualizadas += 1
        return {"creadas": creadas, "actualizadas": actualizadas}


def _canal_explicito(texto: str) -> str:
    txt = _sin_acentos(texto)
    if "binance" in txt or "usdt" in txt or "binance pay" in txt:
        return "binance"
    if "zelle" in txt:
        return "zelle"
    return ""


def _res_validado(cuenta: CuentaReceptora, detalle: str = "") -> dict[str, Any]:
    msg = (
        f"Cuenta receptora verificada en Lubrikca: {cuenta.nombre}"
        + (f" ({detalle})" if detalle else "")
        + "."
    )
    return {
        "estado": "VALIDADO_LUBRIKCA",
        "bloqueante": False,
        "mensaje": msg,
        "cuenta_id": cuenta.cuenta_id,
        "cuenta_nombre": cuenta.nombre,
        "diario_odoo_id": cuenta.diario_odoo_id,
        "diario_odoo_nombre": cuenta.diario_odoo_nombre or cuenta.nombre,
    }


def _res_no_lubrikca(motivo: str) -> dict[str, Any]:
    return {
        "estado": "DESTINO_NO_PERTENECE_LUBRIKCA",
        "bloqueante": True,
        "mensaje": motivo,
        "cuenta_id": None,
        "cuenta_nombre": None,
        "diario_odoo_id": None,
        "diario_odoo_nombre": None,
    }


def validar_destino_pago(
    datos: dict[str, Any],
    cuentas_propias: Sequence[CuentaReceptora],
    *,
    es_zelle_tercero: bool = False,
    es_efectivo_flag: bool = False,
) -> dict[str, Any]:
    """Valida que la cuenta/banco/RIF/teléfono destino pertenezca a Lubrikca.

    Nunca infiere la cuenta destino desde ``banco_origen``.
    """
    if es_zelle_tercero:
        return {
            "estado": "EXENTO_ZELLE_TERCERO",
            "bloqueante": False,
            "mensaje": "Pago a cuenta Zelle de tercero (asiento contable, sin banco propio).",
            "diario_odoo_id": None,
        }
    if es_efectivo_flag:
        return {
            "estado": "EXENTO_EFECTIVO",
            "bloqueante": False,
            "mensaje": "Cobro en efectivo.",
            "diario_odoo_id": None,
        }

    activas = [c for c in cuentas_propias if c.activa and c.es_propia]
    if not activas:
        return {
            "estado": "SIN_CATALOGO_PROPIO",
            "bloqueante": False,
            "mensaje": "No hay cuentas receptoras propias configuradas en Tesorería.",
            "diario_odoo_id": None,
        }

    banco_origen = str(datos.get("banco_origen") or "").strip()
    cod_origen = codigo_sudeban_de_banco(banco_origen) if banco_origen else ""

    cuenta_dest_norm = normalizar_cuenta_bancaria(datos.get("cuenta_destino"))
    rif_dest_raw = str(datos.get("rif_destino") or datos.get("rif_cedula_destino") or "").strip()
    rif_dest = normalizar_rif_cedula(rif_dest_raw)
    tel_dest = normalizar_telefono_ve(datos.get("telefono_destino"))
    email_dest = normalizar_email(datos.get("email_destino") or datos.get("correo_destino") or "")
    titular_dest = str(
        datos.get("titular_destino") or datos.get("beneficiario_destino") or ""
    ).strip()
    if not email_dest and "@" in titular_dest:
        email_dest = normalizar_email(titular_dest)

    banco_dest_explicit = str(datos.get("banco_destino") or "").strip()
    banco_dest = banco_dest_explicit
    if not any(
        [banco_dest_explicit, cuenta_dest_norm, rif_dest, tel_dest, email_dest, titular_dest]
    ):
        # Campo legado ``banco`` solo se evalúa como destino cuando NO coincide con ``banco_origen``
        banco_legado = str(datos.get("banco") or "").strip()
        if banco_legado:
            cod_leg = codigo_sudeban_de_banco(banco_legado)
            mismo_origen = bool(
                banco_origen
                and (
                    _sin_acentos(banco_legado) == _sin_acentos(banco_origen)
                    or (cod_origen and cod_leg == cod_origen)
                )
            )
            if not mismo_origen:
                banco_dest = banco_legado

    if not any([banco_dest, cuenta_dest_norm, rif_dest, tel_dest, email_dest, titular_dest]):
        return {
            "estado": "SIN_DATOS_DESTINO",
            "bloqueante": False,
            "mensaje": (
                "Sin cuenta o banco receptor identificado (se distingue origen de destino; "
                "completa el banco/cuenta receptora de Lubrikca antes de aprobar)."
            ),
            "diario_odoo_id": None,
        }

    cod_cuenta = codigo_sudeban_de_cuenta(cuenta_dest_norm)
    cod_banco = codigo_sudeban_de_banco(banco_dest)
    if cod_cuenta and cod_banco and cod_cuenta != cod_banco:
        nom_cta = BANCOS_SUDEBAN.get(cod_cuenta, cod_cuenta)
        return _res_no_lubrikca(
            f"La cuenta destino ({cod_cuenta} · {nom_cta}) no coincide con el banco destino "
            f"indicado ({banco_dest})."
        )
    cod_dest = cod_cuenta or cod_banco

    candidatos = list(activas)

    # 1. Filtro por número de cuenta destino (exacto 20 dígitos o sufijo últimos 4+)
    if cuenta_dest_norm:
        con_num = [c for c in candidatos if c.numero_cuenta_norm]
        exactos_cta: list[CuentaReceptora] = []
        for c in candidatos:
            if not c.numero_cuenta_norm:
                continue
            if (
                len(cuenta_dest_norm) >= 16
                and c.numero_cuenta_norm == cuenta_dest_norm
                or 4 <= len(cuenta_dest_norm) < 16
                and c.numero_cuenta_norm.endswith(cuenta_dest_norm)
            ):
                exactos_cta.append(c)
        if exactos_cta:
            candidatos = exactos_cta
        elif con_num:
            return _res_no_lubrikca(
                f"El número de cuenta destino ({datos.get('cuenta_destino') or cuenta_dest_norm}) "
                "no coincide con ninguna cuenta receptora de Lubrikca."
            )

    # 2. Filtro por código SUDEBAN / nombre de banco / canal digital
    canal_b = _canal_explicito(banco_dest)
    if cod_dest:
        m_banco = [c for c in candidatos if c.banco_codigo_sudeban == cod_dest]
        if not m_banco:
            b_nom = BANCOS_SUDEBAN.get(cod_dest, banco_dest or cod_dest)
            return _res_no_lubrikca(
                f"Banco destino «{b_nom}» ({cod_dest}) no pertenece a las cuentas "
                "receptoras configuradas de Lubrikca."
            )
        candidatos = m_banco
    elif canal_b:
        m_canal = [c for c in candidatos if c.tipo_canal == canal_b]
        if not m_canal:
            return _res_no_lubrikca(
                f"Canal destino «{banco_dest}» no tiene cuentas propias activas en Lubrikca."
            )
        candidatos = m_canal
    elif banco_dest:
        b_low = _sin_acentos(banco_dest)
        m_nom = [
            c
            for c in candidatos
            if b_low in _sin_acentos(c.nombre)
            or b_low in _sin_acentos(c.banco_nombre)
            or b_low in _sin_acentos(c.diario_odoo_nombre)
        ]
        if not m_nom:
            return _res_no_lubrikca(
                f"Banco/canal destino «{banco_dest}» no coincide con ninguna cuenta de Lubrikca."
            )
        candidatos = m_nom

    # 3. Filtro por RIF / cédula de titular
    if rif_dest:
        con_rif = [c for c in candidatos if c.titular_rif_norm]
        if con_rif:
            m_rif = [c for c in con_rif if _mismo_rif(rif_dest, c.titular_rif_norm)]
            if not m_rif:
                return _res_no_lubrikca(
                    f"El RIF/Cédula destino ({rif_dest_raw}) no corresponde al titular de Lubrikca."
                )
            candidatos = m_rif
        else:
            rifs_todos = [c for c in activas if c.titular_rif_norm]
            if rifs_todos and not any(_mismo_rif(rif_dest, c.titular_rif_norm) for c in rifs_todos):
                return _res_no_lubrikca(
                    f"El RIF/Cédula destino ({rif_dest_raw}) no pertenece a Lubrikca."
                )

    # 4. Filtro por teléfono Pago Móvil
    if tel_dest:
        con_tel = [c for c in candidatos if c.telefono_norm]
        if con_tel:
            m_tel = [c for c in con_tel if c.telefono_norm == tel_dest]
            if not m_tel:
                return _res_no_lubrikca(
                    f"El teléfono Pago Móvil destino ({datos.get('telefono_destino')}) "
                    "no pertenece a ninguna cuenta de Lubrikca."
                )
            candidatos = m_tel
        else:
            tels_todos = [c for c in activas if c.telefono_norm]
            if tels_todos and not any(c.telefono_norm == tel_dest for c in tels_todos):
                return _res_no_lubrikca(
                    f"El teléfono Pago Móvil destino ({datos.get('telefono_destino')}) "
                    "no está registrado como receptor de Lubrikca."
                )

    # 5. Filtro por correo Zelle / ID Binance
    if email_dest:
        con_em = [c for c in candidatos if c.email_norm or c.binance_id]
        if con_em:
            m_em = [
                c
                for c in con_em
                if c.email_norm == email_dest or normalizar_email(c.binance_id) == email_dest
            ]
            if not m_em:
                return _res_no_lubrikca(
                    f"El correo/ID destino ({email_dest}) no coincide con las cuentas de Lubrikca."
                )
            candidatos = m_em

    # 6. Si solo se indicó titular_destino sin otros datos, validar texto si hay titulares
    if (
        titular_dest
        and not email_dest
        and not any([banco_dest, cuenta_dest_norm, rif_dest, tel_dest])
    ):
        tit_norm = _sin_acentos(titular_dest)
        m_tit = [
            c
            for c in candidatos
            if (c.titular_nombre and tit_norm in _sin_acentos(c.titular_nombre))
            or tit_norm in _sin_acentos(c.nombre)
            or (c.binance_id and tit_norm == _sin_acentos(c.binance_id))
        ]
        if not m_tit and any(c.titular_nombre for c in candidatos):
            return _res_no_lubrikca(
                f"El titular/beneficiario destino «{titular_dest}» no coincide con Lubrikca."
            )
        if m_tit:
            candidatos = m_tit

    moneda = str(datos.get("moneda") or "").strip().upper()
    if moneda and len(candidatos) > 1:
        m_mon = [c for c in candidatos if c.moneda == moneda]
        if m_mon:
            candidatos = m_mon

    elegida = candidatos[0]
    detalle = elegida.diario_odoo_nombre or elegida.banco_nombre or elegida.moneda
    return _res_validado(elegida, detalle)
