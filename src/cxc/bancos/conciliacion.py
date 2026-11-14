"""Pre-conciliación por reglas: para cada movimiento del banco, qué haría contabilidad.

Las reglas salen de medir contra lo que contabilidad ya concilió en Odoo (705
movimientos reales, ago-nov 2026): acertaron 640, se equivocaron en 8 y no
propusieron nada en 57 (docs/BANCOS.md, "Fase 2"):

- R1  comisión bancaria -> la cuenta de comisiones.
- R2  un pago de Odoo del MISMO diario, mismo monto exacto, sentido correcto (cobro
      con crédito, pago con débito), fecha a +-3 días. Si hay varios, desempatan: la
      misma fecha, los dígitos de la referencia del banco en el memo del pago y el
      RIF/cédula de la descripción igual al del partner. Gana el de mayor puntaje solo
      si supera al segundo; un empate queda "ambiguo" y lo decide una persona.
- R3  compra con tarjeta o nómina sin pago que la cierre -> la cuenta de pagos
      pendientes de ese banco.

Nada de esto escribe en Odoo: produce propuestas que una persona aprueba o corrige
en la cola de revisión. Odoo se lee con ``solo_lectura``, que rechaza cualquier
método que no sea de lectura antes de enviarlo.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .diarios import DIARIOS
from .ingesta import MovimientoClasificado

CUENTA_COMISIONES = "6.2.1.1.01.02 Bank Fees"
CATEGORIAS_COMISION = frozenset({"comision_bancaria"})
CATEGORIAS_PENDIENTES = frozenset({"compra_tarjeta", "nomina"})
VENTANA_DIAS = 3
CENT = Decimal("0.01")

# Tipos de propuesta
PAGO = "pago"
CUENTA = "cuenta"
AMBIGUO = "ambiguo"
SIN_PROPUESTA = "sin_propuesta"

# El verificador puede venir pegado (J507836142) o con guion (J-50783614-2).
_RIF = re.compile(r"\b([VJEGP])-?0*(\d{6,9})(?:-(\d))?\b")


def rifs(texto: str | None) -> frozenset[str]:
    """RIF/cédulas normalizados de un texto: 'J-0507836142' y 'J507836142' -> 'J507836142'."""
    return frozenset(
        f"{letra}{int(num)}{dv}" for letra, num, dv in _RIF.findall((texto or "").upper())
    )


def _digitos(ref: str) -> str:
    return re.sub(r"\D", "", ref or "").lstrip("0")


@dataclass(frozen=True)
class PagoCandidato:
    """Un ``account.payment`` publicado de Odoo, lo justo para emparejar."""

    pago_id: int
    nombre: str
    fecha: date
    monto: Decimal  # siempre positivo, como en Odoo
    entrante: bool  # payment_type == 'inbound'
    memo: str = ""
    partner: str = ""
    partner_rifs: frozenset[str] = frozenset()
    ya_conciliado: bool = False  # is_matched: ya está casado con una línea de banco


@dataclass
class Propuesta:
    huella: str
    tipo: str  # PAGO | CUENTA | AMBIGUO | SIN_PROPUESTA
    regla: str
    motivo: str
    pago_id: int | None = None
    pago_nombre: str = ""
    cuenta: str = ""
    puntaje: int = 0
    candidatos: list[int] = field(default_factory=list)


def _puntaje(m_fecha: date, m_ref: str, m_rifs: frozenset[str], p: PagoCandidato) -> int:
    dias = abs((p.fecha - m_fecha).days)
    s = 3 if dias == 0 else (2 if dias == 1 else 1)
    ref = _digitos(m_ref)
    if ref and any(
        len(x) >= 4 and ref.endswith(x) for x in re.sub(r"\D", " ", p.memo).split()
    ):
        s += 3
    if m_rifs & p.partner_rifs:
        s += 3
    return s


def _pendientes(banco: str) -> str:
    d = DIARIOS.get(banco)
    return (d["pagos_pendientes"] or "") if d else ""


def proponer(
    movs: Iterable[MovimientoClasificado], pagos: Iterable[PagoCandidato]
) -> list[Propuesta]:
    """Una propuesta por movimiento. Un pago se asigna a un solo movimiento (el primero
    en orden cronológico que lo gana); por eso los movimientos se recorren ordenados."""
    disponibles: dict[tuple[bool, Decimal], list[PagoCandidato]] = {}
    for p in pagos:
        disponibles.setdefault((p.entrante, p.monto.quantize(CENT)), []).append(p)
    usados: set[int] = set()
    out: list[Propuesta] = []
    for mc in sorted(movs, key=lambda x: (x.mov.fecha, x.mov.orden)):
        m = mc.mov
        if mc.categoria in CATEGORIAS_COMISION:
            out.append(
                Propuesta(
                    m.huella, CUENTA, "R1", f"comisión ({mc.regla})", cuenta=CUENTA_COMISIONES
                )
            )
            continue
        entrante = m.neto > 0
        ventana = timedelta(days=VENTANA_DIAS)
        cands = [
            p
            for p in disponibles.get((entrante, abs(m.neto).quantize(CENT)), [])
            if p.pago_id not in usados and abs(p.fecha - m.fecha) <= ventana
        ]
        m_rifs = rifs(m.descripcion)
        puntuados = sorted(
            ((_puntaje(m.fecha, m.referencia, m_rifs, p), p) for p in cands),
            key=lambda t: (-t[0], t[1].pago_id),
        )
        if puntuados and (len(puntuados) == 1 or puntuados[0][0] > puntuados[1][0]):
            s, p = puntuados[0]
            usados.add(p.pago_id)
            out.append(
                Propuesta(
                    m.huella,
                    PAGO,
                    "R2",
                    f"pago {p.nombre} de {len(puntuados)} candidato(s), puntaje {s}"
                    + (" (ya conciliado en Odoo)" if p.ya_conciliado else ""),
                    pago_id=p.pago_id,
                    pago_nombre=p.nombre,
                    puntaje=s,
                    candidatos=[q.pago_id for _, q in puntuados],
                )
            )
        elif puntuados:
            out.append(
                Propuesta(
                    m.huella,
                    AMBIGUO,
                    "R2",
                    f"{len(puntuados)} pagos empatan con puntaje {puntuados[0][0]}",
                    puntaje=puntuados[0][0],
                    candidatos=[q.pago_id for _, q in puntuados],
                )
            )
        elif mc.categoria in CATEGORIAS_PENDIENTES and _pendientes(m.banco):
            out.append(
                Propuesta(
                    m.huella,
                    CUENTA,
                    "R3",
                    f"{mc.categoria} sin pago que la cierre",
                    cuenta=_pendientes(m.banco),
                )
            )
        else:
            out.append(Propuesta(m.huella, SIN_PROPUESTA, "-", "sin pago candidato"))
    return out


# --------------------------------------------------------------------- Odoo (lectura)

Execute = Callable[..., Any]
METODOS_LECTURA = frozenset({"search_read", "read", "search_count", "search", "fields_get"})
ESTADOS_PAGO = ["in_process", "paid"]


def solo_lectura(execute: Execute) -> Execute:
    """Envuelve el ``execute`` de Odoo: un método que no sea de lectura no sale."""

    def _ex(model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None) -> Any:
        if method not in METODOS_LECTURA:
            raise PermissionError(f"bloqueado: {model}.{method} no es de lectura")
        return execute(model, method, args, kwargs or {})

    return _ex


def pagos_candidatos(
    execute: Execute, journal_id: int, desde: date, hasta: date
) -> list[PagoCandidato]:
    """Pagos publicados del diario entre ``desde - 3`` y ``hasta + 3`` días."""
    ex = solo_lectura(execute)
    ventana = timedelta(days=VENTANA_DIAS)
    recs = ex(
        "account.payment",
        "search_read",
        [
            [
                ["journal_id", "=", journal_id],
                ["date", ">=", (desde - ventana).isoformat()],
                ["date", "<=", (hasta + ventana).isoformat()],
                ["state", "in", ESTADOS_PAGO],
            ]
        ],
        {
            "fields": [
                "id", "name", "date", "amount", "payment_type", "memo", "partner_id", "is_matched"
            ]
        },
    )
    socios = sorted({r["partner_id"][0] for r in recs if r.get("partner_id")})
    vat: dict[int, frozenset[str]] = {}
    if socios:
        for s in ex("res.partner", "read", [socios], {"fields": ["vat"]}):
            # En Odoo el RIF viene como 'J507836142' o 'J-50783614-2': letra + dígitos.
            v = re.sub(r"[^A-Z0-9]", "", str(s.get("vat") or "").upper())
            vat[int(s["id"])] = rifs(v)
    out = []
    for r in recs:
        pid = r["partner_id"][0] if r.get("partner_id") else None
        out.append(
            PagoCandidato(
                pago_id=int(r["id"]),
                nombre=str(r.get("name") or ""),
                fecha=date.fromisoformat(str(r["date"])),
                monto=Decimal(str(r["amount"])).quantize(CENT),
                entrante=r.get("payment_type") == "inbound",
                memo=str(r.get("memo") or ""),
                partner=str(r["partner_id"][1]) if r.get("partner_id") else "",
                partner_rifs=vat.get(pid, frozenset()) if pid else frozenset(),
                ya_conciliado=bool(r.get("is_matched")),
            )
        )
    return out


def contactos_por_rif(execute: Execute, buscados: Iterable[str]) -> dict[str, str]:
    """RIF/cédula -> nombre del contacto en Odoo, solo cuando UN contacto lo tiene.

    Un RIF repetido en dos contactos no se asigna (sería adivinar), y el contacto de la
    propia empresa se descarta: su RIF aparece en las transferencias entre cuentas.
    """
    ex = solo_lectura(execute)
    lista = sorted(set(buscados))
    if not lista:
        return {}
    propios = {
        int(c["partner_id"][0])
        for c in ex("res.company", "search_read", [[]], {"fields": ["partner_id"]})
        if c.get("partner_id")
    }
    por_rif: dict[str, dict[int, str]] = {}
    for i in range(0, len(lista), 200):
        bloque = lista[i : i + 200]
        # En Odoo el RIF puede estar como 'J507836142' o 'J-50783614-2': se busca por
        # los dígitos sin el verificador y se confirma normalizando, como en los pagos.
        dominio: list[Any] = ["|"] * (len(bloque) - 1)
        dominio += [["vat", "ilike", r[1:-1]] for r in bloque]
        for s in ex(
            "res.partner", "search_read", [dominio], {"fields": ["id", "display_name", "vat"]}
        ):
            if int(s["id"]) in propios:
                continue
            v = re.sub(r"[^A-Z0-9]", "", str(s.get("vat") or "").upper())
            for r in rifs(v) & set(bloque):
                por_rif.setdefault(r, {})[int(s["id"])] = str(s.get("display_name") or "")
    return {r: next(iter(ids.values())) for r, ids in por_rif.items() if len(ids) == 1}
