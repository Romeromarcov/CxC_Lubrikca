"""Motor de normalización, completitud y detección anti-duplicados de contactos."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from typing import Any

from cxc.contactos.modelos import ContactoMaestro

_SUFIJOS_SOCIETARIOS = (
    "compania anonima",
    "sociedad anonima",
    "c a",
    "ca",
    "s a",
    "sa",
    "s r l",
    "srl",
    "l l c",
    "llc",
)

_PESOS_PROSPECTO: tuple[tuple[str, str, int], ...] = (
    ("nombre", "Nombre / Razón social", 25),
    ("telefono", "Teléfono", 25),
    ("rif", "RIF / Cédula", 15),
    ("direccion", "Dirección", 15),
    ("ubicacion", "Ubicación GPS", 10),
    ("email", "Correo electrónico", 10),
)

_PESOS_COMERCIAL: tuple[tuple[str, str, int], ...] = (
    ("nombre", "Nombre / Razón social", 20),
    ("rif", "RIF / Cédula", 25),
    ("telefono", "Teléfono", 20),
    ("direccion", "Dirección", 15),
    ("email", "Correo electrónico", 10),
    ("ubicacion", "Ubicación GPS", 10),
)


def normalizar_rif_canonico(rif: str | None) -> str:
    """Normaliza RIF/Cédula a alfanumérico en mayúsculas sin guiones ni espacios."""
    if not rif:
        return ""
    limpio = re.sub(r"[^A-Za-z0-9]", "", str(rif).upper())
    return limpio if len(limpio) >= 5 else ""


def normalizar_telefono(tel: str | None) -> str:
    """Extrae dígitos relevantes (últimos 10 dígitos VE/US) para comparar teléfonos."""
    if not tel:
        return ""
    digitos = re.sub(r"\D", "", str(tel))
    if not digitos or len(digitos) < 7:
        return ""
    if digitos.startswith("0058") and len(digitos) >= 14:
        digitos = digitos[4:]
    elif digitos.startswith("58") and len(digitos) >= 12:
        digitos = digitos[2:]
    elif digitos.startswith("0") and len(digitos) >= 11:
        digitos = digitos[1:]
    if len(digitos) > 10:
        digitos = digitos[-10:]
    return digitos if len(digitos) >= 7 else ""


def normalizar_nombre(nombre: str | None) -> str:
    """Normaliza razón social quitando acentos, puntuación y sufijos societarios al final."""
    if not nombre:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(nombre))
    sin_acentos = "".join(ch for ch in nfkd if unicodedata.category(ch) != "Mn")
    limpio = re.sub(r"[^a-z0-9\s]", " ", sin_acentos.lower())
    limpio = re.sub(r"\s+", " ", limpio).strip()
    cambio = True
    while cambio and limpio:
        cambio = False
        for suf in _SUFIJOS_SOCIETARIOS:
            patron = f" {suf}"
            if limpio.endswith(patron):
                limpio = limpio[: -len(patron)].strip()
                cambio = True
                break
    return limpio


def normalizar_email(email: str | None) -> str:
    """Normaliza correo electrónico si tiene formato válido básico."""
    if not email:
        return ""
    limpio = str(email).strip().lower()
    if "@" not in limpio or "." not in limpio.split("@", 1)[1]:
        return ""
    return limpio


def _valor_campo(contacto: ContactoMaestro | Mapping[str, Any], clave: str) -> Any:
    if isinstance(contacto, Mapping):
        return contacto.get(clave)
    return getattr(contacto, clave, None)


def _tiene_ubicacion(contacto: ContactoMaestro | Mapping[str, Any]) -> bool:
    ub = _valor_campo(contacto, "ubicacion")
    if isinstance(ub, Mapping) and ub.get("latitud") is not None and ub.get("longitud") is not None:
        return True
    lat = _valor_campo(contacto, "latitud") or _valor_campo(contacto, "lat")
    lon = _valor_campo(contacto, "longitud") or _valor_campo(contacto, "lon")
    if lat in (None, "") or lon in (None, ""):
        return False
    try:
        return not (float(lat) == 0.0 and float(lon) == 0.0)
    except (TypeError, ValueError):
        return False


def evaluar_completitud(
    contacto: ContactoMaestro | Mapping[str, Any],
) -> tuple[int, list[str], bool]:
    """Calcula % de completitud, lista de campos faltantes y si está listo para Odoo."""
    rol = str(_valor_campo(contacto, "rol") or "prospecto").strip().lower()
    esquema = _PESOS_PROSPECTO if rol == "prospecto" else _PESOS_COMERCIAL

    presentes: dict[str, bool] = {
        "nombre": bool(str(_valor_campo(contacto, "nombre") or "").strip()),
        "rif": bool(str(_valor_campo(contacto, "rif") or "").strip()),
        "telefono": bool(str(_valor_campo(contacto, "telefono") or "").strip()),
        "direccion": bool(str(_valor_campo(contacto, "direccion") or "").strip()),
        "email": bool(str(_valor_campo(contacto, "email") or "").strip()),
        "ubicacion": _tiene_ubicacion(contacto),
    }

    pct = 0
    faltantes: list[str] = []
    for clave, etiqueta, peso in esquema:
        if presentes.get(clave):
            pct += peso
        else:
            faltantes.append(etiqueta)

    listo = (
        presentes["nombre"] and presentes["telefono"]
        if rol == "prospecto"
        else presentes["nombre"] and presentes["rif"] and presentes["telefono"]
    )
    return min(100, pct), faltantes, bool(listo)


def _normalizar_candidato(
    candidato: ContactoMaestro | Mapping[str, Any],
) -> tuple[str, str, str, str]:
    rif_n = normalizar_rif_canonico(str(_valor_campo(candidato, "rif") or ""))
    tel_n = normalizar_telefono(str(_valor_campo(candidato, "telefono") or ""))
    email_n = normalizar_email(str(_valor_campo(candidato, "email") or ""))
    nom_n = normalizar_nombre(str(_valor_campo(candidato, "nombre") or ""))
    return rif_n, tel_n, email_n, nom_n


def _similitud_par(
    rif_a: str,
    tel_a: str,
    email_a: str,
    nom_a: str,
    other: ContactoMaestro,
) -> dict[str, Any] | None:
    rif_b = other.rif_normalizado or normalizar_rif_canonico(other.rif)
    tel_b = other.telefono_normalizado or normalizar_telefono(other.telefono)
    email_b = normalizar_email(other.email)
    nom_b = other.nombre_normalizado or normalizar_nombre(other.nombre)

    score = 0
    bloqueante = False
    motivos: list[str] = []

    if rif_a and rif_b and len(rif_a) >= 6 and rif_a == rif_b:
        score = 100
        bloqueante = True
        motivos.append("RIF idéntico")

    if tel_a and tel_b and len(tel_a) >= 7 and tel_a == tel_b:
        score = max(score, 88)
        motivos.append("Teléfono idéntico")

    if email_a and email_b and email_a == email_b:
        score = max(score, 85)
        motivos.append("Email idéntico")

    if nom_a and nom_b and len(nom_a) >= 4 and len(nom_b) >= 4:
        if nom_a == nom_b:
            score = max(score, 92)
            motivos.append("Nombre idéntico")
        else:
            ratio = SequenceMatcher(None, nom_a, nom_b).ratio()
            if ratio >= 0.86:
                sim_score = int(round(ratio * 95))
                score = max(score, sim_score)
                motivos.append(f"Nombre muy similar ({int(round(ratio * 100))}%)")

    if not motivos:
        return None

    return {
        "contacto_id": other.contacto_id,
        "odoo_partner_id": other.odoo_partner_id,
        "nombre": other.nombre,
        "rif": other.rif,
        "telefono": other.telefono,
        "email": other.email,
        "direccion": other.direccion,
        "rol": other.rol,
        "origen": other.origen,
        "estado_validacion": other.estado_validacion,
        "score": score,
        "bloqueante": bloqueante,
        "nivel": "ALTA" if score >= 88 else "MEDIA",
        "motivos": motivos,
    }


def buscar_posibles_duplicados(
    candidato: ContactoMaestro | Mapping[str, Any],
    universo: Sequence[ContactoMaestro],
    *,
    excluir_id: int | None = None,
    excluir_odoo_id: int | None = None,
    limite: int = 5,
) -> list[dict[str, Any]]:
    """Busca contactos en ``universo`` que puedan ser duplicados de ``candidato``."""
    rif_a, tel_a, email_a, nom_a = _normalizar_candidato(candidato)
    if not any((rif_a, tel_a, email_a, nom_a)):
        return []

    hallados: list[dict[str, Any]] = []
    for c in universo:
        if not c.activo or c.estado_validacion in ("DUPLICADO", "DESCARTADO"):
            continue
        if excluir_id is not None and c.contacto_id == excluir_id:
            continue
        if (
            excluir_odoo_id is not None
            and c.odoo_partner_id is not None
            and c.odoo_partner_id == excluir_odoo_id
        ):
            continue
        sim = _similitud_par(rif_a, tel_a, email_a, nom_a, c)
        if sim is not None:
            hallados.append(sim)

    hallados.sort(key=lambda x: (-int(x["score"]), str(x["nombre"])))
    return hallados[: max(1, limite)]


def _elegir_maestro_sugerido(
    a: ContactoMaestro, b: ContactoMaestro
) -> tuple[ContactoMaestro, ContactoMaestro]:
    def _peso(c: ContactoMaestro) -> tuple[int, int, int]:
        tiene_odoo = 1 if c.odoo_partner_id is not None else 0
        id_inv = -(c.contacto_id or 999_999_999)
        return (tiene_odoo, int(c.completitud_pct), id_inv)

    if _peso(a) >= _peso(b):
        return a, b
    return b, a


def detectar_grupos_duplicados(
    contactos: Sequence[ContactoMaestro],
    *,
    max_pares: int = 200,
) -> list[dict[str, Any]]:
    """Detecta pares de posibles duplicados dentro del maestro usando bucketing O(N)."""
    activos = [
        c for c in contactos if c.activo and c.estado_validacion not in ("DUPLICADO", "DESCARTADO")
    ]
    buckets: dict[str, list[int]] = defaultdict(list)
    norm_por_idx: list[tuple[str, str, str, str]] = []

    for idx, c in enumerate(activos):
        rif_n = c.rif_normalizado or normalizar_rif_canonico(c.rif)
        tel_n = c.telefono_normalizado or normalizar_telefono(c.telefono)
        email_n = normalizar_email(c.email)
        nom_n = c.nombre_normalizado or normalizar_nombre(c.nombre)
        norm_por_idx.append((rif_n, tel_n, email_n, nom_n))

        if len(rif_n) >= 6:
            buckets[f"rif:{rif_n}"].append(idx)
        if len(tel_n) >= 7:
            buckets[f"tel:{tel_n}"].append(idx)
        if email_n:
            buckets[f"mail:{email_n}"].append(idx)
        if len(nom_n) >= 4:
            buckets[f"nom4:{nom_n[:4]}"].append(idx)
            primer_tok = nom_n.split(" ", 1)[0]
            if len(primer_tok) >= 4:
                buckets[f"tok:{primer_tok[:4]}"].append(idx)

    pares_vistos: set[tuple[int, int]] = set()
    resultados: list[dict[str, Any]] = []

    for clave_b, indices in buckets.items():
        if len(indices) < 2:
            continue
        # Evita explosión cuadrática en prefijos muy genéricos; RIF/Tel/Email no se capan
        if (clave_b.startswith("nom4:") or clave_b.startswith("tok:")) and len(indices) > 120:
            continue
        for pos_i in range(len(indices)):
            i = indices[pos_i]
            rif_a, tel_a, email_a, nom_a = norm_por_idx[i]
            for pos_j in range(pos_i + 1, len(indices)):
                j = indices[pos_j]
                par = (min(i, j), max(i, j))
                if par in pares_vistos:
                    continue
                pares_vistos.add(par)
                sim = _similitud_par(rif_a, tel_a, email_a, nom_a, activos[j])
                if sim is None:
                    continue
                maestro, duplicado = _elegir_maestro_sugerido(activos[i], activos[j])
                resultados.append(
                    {
                        "score": sim["score"],
                        "bloqueante": sim["bloqueante"],
                        "nivel": sim["nivel"],
                        "motivos": sim["motivos"],
                        "maestro_sugerido_id": maestro.contacto_id,
                        "duplicado_sugerido_id": duplicado.contacto_id,
                        "maestro": maestro.a_dict(),
                        "duplicado": duplicado.a_dict(),
                    }
                )

    resultados.sort(key=lambda r: (-int(r["score"]), not bool(r["bloqueante"])))
    return resultados[:max_pares]
