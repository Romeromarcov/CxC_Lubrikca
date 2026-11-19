"""Configuración contable y resolución de cuentas para gastos menores sin factura.

Permite generar en Odoo un asiento contable borrador (``account.move`` con
``move_type='entry'``): Débito cuenta de gasto (6.x) / Crédito cuenta de caja chica
o banco contrapartida, sin contaminar ``in_invoice`` ni el Libro de Compras SENIAT.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Sequence
from typing import Any

from cxc.odoo.directorio import CuentaContable, sugerir_para_concepto
from cxc.repositories import Repository

from .modelos import CATEGORIAS_GASTO, DocumentoOperativo

KEY_GASTOS_DIARIO_GENERAL = "gastos_diario_general_id"
KEY_GASTOS_CUENTA_CREDITO_METODO = "gastos_cuenta_credito_por_metodo_json"
KEY_GASTOS_CUENTA_DEBITO_CAT = "gastos_cuenta_debito_por_categoria_json"

METODOS_GASTO: tuple[tuple[str, str], ...] = (
    ("caja_chica", "Caja Chica"),
    ("efectivo_ves", "Efectivo VES"),
    ("efectivo_usd", "Efectivo USD"),
    ("pago_movil", "Pago Móvil"),
    ("transferencia", "Transferencia Bancaria"),
    ("zelle", "Zelle"),
    ("tarjeta", "Tarjeta de Débito/Crédito"),
    ("default", "Por defecto (otros métodos)"),
)


def _norm(txt: str) -> str:
    nfd = unicodedata.normalize("NFD", (txt or "").strip().lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _cargar_mapa(raw: str | None) -> dict[str, dict[str, Any]]:
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for k, v in data.items():
            if isinstance(v, dict) and v.get("account_id"):
                aid = int(str(v["account_id"]))
                out[str(k)] = {
                    "account_id": aid,
                    "codigo": str(v.get("codigo") or ""),
                    "nombre": str(v.get("nombre") or ""),
                }
            elif isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
                out[str(k)] = {"account_id": int(v), "codigo": "", "nombre": ""}
        return out
    except Exception:
        return {}


def cargar_config_gastos(repo_cxc: Repository | None) -> dict[str, Any]:
    if repo_cxc is None:
        return {
            "diario_general_id": None,
            "cuentas_credito_por_metodo": {},
            "cuentas_debito_por_categoria": {},
        }
    diario_raw = (repo_cxc.get_config(KEY_GASTOS_DIARIO_GENERAL) or "").strip()
    diario_id = int(diario_raw) if diario_raw.isdigit() else None
    return {
        "diario_general_id": diario_id,
        "cuentas_credito_por_metodo": _cargar_mapa(
            repo_cxc.get_config(KEY_GASTOS_CUENTA_CREDITO_METODO)
        ),
        "cuentas_debito_por_categoria": _cargar_mapa(
            repo_cxc.get_config(KEY_GASTOS_CUENTA_DEBITO_CAT)
        ),
    }


def _normalizar_mapa_entrada(raw: Any) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("El mapa contable debe ser un objeto JSON clave → cuenta.")
    out: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        clave = str(k).strip()
        if not clave or v in (None, "", {}):
            continue
        if isinstance(v, dict):
            aid_raw = v.get("account_id")
            if aid_raw in (None, ""):
                continue
            if not str(aid_raw).isdigit():
                raise ValueError(f"ID de cuenta contable inválido para «{clave}».")
            out[clave] = {
                "account_id": int(str(aid_raw)),
                "codigo": str(v.get("codigo") or ""),
                "nombre": str(v.get("nombre") or ""),
            }
        elif str(v).isdigit():
            out[clave] = {"account_id": int(str(v)), "codigo": "", "nombre": ""}
        else:
            raise ValueError(f"Valor inválido en mapa contable para «{clave}».")
    return out


def guardar_config_gastos(repo_cxc: Repository, datos: dict[str, Any]) -> dict[str, Any]:
    actual = cargar_config_gastos(repo_cxc)

    if "diario_general_id" in datos:
        dg_raw = datos.get("diario_general_id")
        if dg_raw in (None, "", 0, "0"):
            repo_cxc.set_config(KEY_GASTOS_DIARIO_GENERAL, "")
        elif str(dg_raw).isdigit():
            repo_cxc.set_config(KEY_GASTOS_DIARIO_GENERAL, str(int(str(dg_raw))))
        else:
            raise ValueError("diario_general_id debe ser un entero válido.")

    if "cuentas_credito_por_metodo" in datos:
        mapa_c = _normalizar_mapa_entrada(datos["cuentas_credito_por_metodo"])
        repo_cxc.set_config(
            KEY_GASTOS_CUENTA_CREDITO_METODO, json.dumps(mapa_c, ensure_ascii=False)
        )
    elif KEY_GASTOS_CUENTA_CREDITO_METODO not in repo_cxc.all_config():
        repo_cxc.set_config(
            KEY_GASTOS_CUENTA_CREDITO_METODO,
            json.dumps(actual["cuentas_credito_por_metodo"], ensure_ascii=False),
        )

    if "cuentas_debito_por_categoria" in datos:
        mapa_d = _normalizar_mapa_entrada(datos["cuentas_debito_por_categoria"])
        repo_cxc.set_config(KEY_GASTOS_CUENTA_DEBITO_CAT, json.dumps(mapa_d, ensure_ascii=False))
    elif KEY_GASTOS_CUENTA_DEBITO_CAT not in repo_cxc.all_config():
        repo_cxc.set_config(
            KEY_GASTOS_CUENTA_DEBITO_CAT,
            json.dumps(actual["cuentas_debito_por_categoria"], ensure_ascii=False),
        )

    return cargar_config_gastos(repo_cxc)


def clave_metodo_gasto(metodo_pago: str, moneda: str = "VES") -> str:
    mp = _norm(metodo_pago)
    mon = (moneda or "VES").strip().upper()
    if "caja" in mp:
        return "caja_chica"
    if "efectivo" in mp or "cash" in mp:
        return "efectivo_usd" if mon == "USD" else "efectivo_ves"
    if "movil" in mp or "pagomovil" in mp:
        return "pago_movil"
    if "zelle" in mp:
        return "zelle"
    if "tarjeta" in mp or "punto" in mp or "tdc" in mp or "tdd" in mp:
        return "tarjeta"
    if "transf" in mp or "deposito" in mp:
        return "transferencia"
    return "default"


def resolver_cuenta_debito_gasto(
    doc: DocumentoOperativo,
    cfg: dict[str, Any],
    cuentas_odoo: Sequence[CuentaContable],
) -> tuple[int | None, str]:
    """Devuelve ``(account_id, etiqueta)`` para la cuenta débito de gasto (6.x)."""
    if doc.cuenta_contable_id:
        etq = f"{doc.cuenta_contable_codigo} {doc.cuenta_contable_nombre}".strip() or (
            f"Cuenta #{doc.cuenta_contable_id}"
        )
        return int(doc.cuenta_contable_id), etq

    cat = (doc.categoria or "").strip()
    mapa_cat: dict[str, dict[str, Any]] = cfg.get("cuentas_debito_por_categoria") or {}
    if cat:
        for k, entry in mapa_cat.items():
            if _norm(k) == _norm(cat) and entry.get("account_id"):
                etq = f"{entry.get('codigo', '')} {entry.get('nombre', '')}".strip() or k
                return int(entry["account_id"]), etq

    if cuentas_odoo:
        concepto = " ".join(x for x in (doc.concepto, doc.categoria, doc.contraparte_nombre) if x)
        sug = sugerir_para_concepto(concepto or "gasto", cuentas_odoo).get("cuenta_sugerida")
        if isinstance(sug, dict) and sug.get("id"):
            return int(sug["id"]), f"{sug.get('code', '')} {sug.get('name', '')}".strip()

    return None, ""


def resolver_cuenta_credito_gasto(
    doc: DocumentoOperativo,
    cfg: dict[str, Any],
    cuentas_odoo: Sequence[CuentaContable],
) -> tuple[int | None, str]:
    """Devuelve ``(account_id, etiqueta)`` contrapartida (caja chica o banco)."""
    if doc.cuenta_contrapartida_id:
        cta_obj = next(
            (c for c in cuentas_odoo if c.id == int(doc.cuenta_contrapartida_id)),
            None,
        )
        etq = (
            f"{cta_obj.code} {cta_obj.name}".strip()
            if cta_obj
            else f"Cuenta #{doc.cuenta_contrapartida_id}"
        )
        return int(doc.cuenta_contrapartida_id), etq

    clave = clave_metodo_gasto(doc.metodo_pago, doc.moneda)
    mapa_met: dict[str, dict[str, Any]] = cfg.get("cuentas_credito_por_metodo") or {}
    for k in (clave, "default"):
        entry = mapa_met.get(k)
        if isinstance(entry, dict) and entry.get("account_id"):
            etq = f"{entry.get('codigo', '')} {entry.get('nombre', '')}".strip() or k
            return int(entry["account_id"]), etq

    # Heurística segura sobre el plan de cuentas de Odoo (asset_cash / caja / banco)
    if cuentas_odoo:
        cash = [
            c
            for c in cuentas_odoo
            if c.account_type == "asset_cash"
            or c.code.startswith(("1.1.1", "1101", "111"))
            or any(tok in _norm(c.name) for tok in ("caja", "efectivo", "banco"))
        ]
        if cash:
            es_cash = clave in ("caja_chica", "efectivo_ves", "efectivo_usd")
            cash.sort(
                key=lambda c: (
                    0
                    if (es_cash and any(t in _norm(c.name) for t in ("caja", "efectivo")))
                    else (1 if not es_cash and "banco" in _norm(c.name) else 2),
                    c.code,
                )
            )
            elegida = cash[0]
            return elegida.id, f"{elegida.code} {elegida.name}".strip()

    return None, ""


def categorias_y_metodos_catalogo() -> dict[str, Any]:
    return {
        "categorias": list(CATEGORIAS_GASTO),
        "metodos": [{"clave": k, "nombre": n} for k, n in METODOS_GASTO],
    }
