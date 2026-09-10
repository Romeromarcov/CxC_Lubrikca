"""Las invariantes de dinero, del lado del repositorio.

Segunda mitad de la Fase 2.2 del plan de blindaje. La primera fueron las ocho
restricciones ``CHECK`` de la migración ``f1e2d3c4b5a6``; el plan pedía las dos:

    "Restricciones en la base **más validación en el repositorio**."

**Qué agrega esto, dado que la base ya rechaza la escritura.** El mensaje. Hoy una
fila imposible sale como un ``IntegrityError`` de psycopg con el SQL crudo y el
nombre de la restricción, a veinte marcos de profundidad dentro del sync. Quien lo
lee sabe que algo se rompió pero no **qué fila** ni **con qué valores**, y en un
lote de mil vinculaciones eso es la diferencia entre arreglarlo y volver a correr
el sync a ver si pasa.

**Qué NO cambia, y es deliberado.** Una fila que viola una invariante sigue siendo
rechazada, y sigue abortando el lote entero. Podría haber elegido saltear la fila
mala y escribir el resto, y eso habría sido un cambio de comportamiento —
escrituras parciales donde antes no había ninguna— que necesita una decisión del
usuario. Fallar es exactamente lo que ya hace la base, así que **esto no mueve
nada**: solo dice mejor lo que ya pasaba.

Las reglas de acá son un espejo de las cláusulas SQL, y eso es una duplicación a
propósito. El test ``test_invariantes_al_escribir`` verifica que las dos versiones
coincidan sobre los mismos casos: si alguien cambia una y no la otra, falla.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# Postgres pone el nombre de la restricción en el mensaje, y los nombres se
# eligieron para que digan qué se rompió. Esto los traduce a una frase.
_POR_QUE: dict[str, str] = {
    "ck_vinc_monto_aplicado_no_negativo": (
        "una vinculación no puede aplicar un monto negativo: sería un cobro al revés"
    ),
    "ck_vinc_tasas_positivas": (
        "una tasa en cero o negativa hace que el equivalente en dólares no signifique "
        "nada, y queda congelado así para siempre"
    ),
    "ck_vinc_equivalente_no_supera_el_nominal": (
        "el equivalente en dólares de un abono en bolívares no puede superar el "
        "nominal en bolívares: implicaría una tasa menor que 1"
    ),
    "ck_teoricos_no_negativos": "un teórico negativo no es una venta",
    "ck_descuento_no_supera_el_teorico": (
        "un descuento mayor que el teórico dejaría la venta en negativo"
    ),
    "ck_pago_monto_no_negativo": "un pago negativo es una devolución, y va por otro lado",
    "ck_orden_monto_total_no_negativo": "una orden no puede valer menos que nada",
    "ck_linea_cantidad_no_negativa": (
        "una cantidad negativa en la línea es cómo Odoo representa una devolución "
        "que supera lo que quedó, y el espejo no debe guardarla así"
    ),
}

_NOMBRE_DE_RESTRICCION = re.compile(r'"(ck_[a-z0-9_]+)"')


class InvarianteViolada(ValueError):
    """Una fila que no se puede escribir, con el motivo dicho en castellano."""


@dataclass(frozen=True)
class Violacion:
    restriccion: str
    fila: str
    detalle: str

    def __str__(self) -> str:
        porque = _POR_QUE.get(self.restriccion, "")
        cola = f" -- {porque}" if porque else ""
        return f"{self.fila}: {self.detalle} [{self.restriccion}]{cola}"


def _dec(valor: Any) -> Decimal | None:
    if valor is None:
        return None
    try:
        return Decimal(str(valor))
    except Exception:  # noqa: BLE001 -- un valor ilegible se reporta aparte
        return None


def verificar_vinculacion(v: Any) -> list[Violacion]:
    """Las tres invariantes de ``vinculaciones``, con los valores en el mensaje."""
    fila = f"vinculación {getattr(v, 'vinc_id', '?')}"
    fallas: list[Violacion] = []

    monto = _dec(getattr(v, "monto_aplicado", None))
    if monto is not None and monto < 0:
        fallas.append(
            Violacion("ck_vinc_monto_aplicado_no_negativo", fila, f"monto_aplicado {monto}")
        )

    bcv = _dec(getattr(v, "tasa_bcv_aplicada", None))
    binance = _dec(getattr(v, "tasa_binance_aplicada", None))
    malas = [
        f"{nombre} {valor}"
        for nombre, valor in (("tasa_bcv_aplicada", bcv), ("tasa_binance_aplicada", binance))
        if valor is not None and valor <= 0
    ]
    if malas:
        fallas.append(Violacion("ck_vinc_tasas_positivas", fila, ", ".join(malas)))

    if str(getattr(v, "moneda_abono", "") or "") == "VES" and monto is not None:
        equivalentes = [
            _dec(getattr(v, "equiv_usd_bcv", None)) or Decimal(0),
            _dec(getattr(v, "equiv_usd_binance", None)) or Decimal(0),
        ]
        mayor = max(equivalentes)
        if mayor > monto:
            fallas.append(
                Violacion(
                    "ck_vinc_equivalente_no_supera_el_nominal",
                    fila,
                    f"equivalente USD {mayor} sobre un nominal en bolívares de {monto}",
                )
            )
    return fallas


def verificar_teorico(t: Any) -> list[Violacion]:
    """Las dos invariantes de ``ventas_teoricos``."""
    fila = f"teórico de {getattr(t, 'so_id', '?')}"
    fallas: list[Violacion] = []

    campos = {
        "teorico_ves": _dec(getattr(t, "teorico_ves", None)),
        "teorico_usd": _dec(getattr(t, "teorico_usd", None)),
        "descuentos_teorico_ves": _dec(getattr(t, "descuentos_teorico_ves", None)),
        "descuentos_teorico_usd": _dec(getattr(t, "descuentos_teorico_usd", None)),
    }
    negativos = [f"{n} {v}" for n, v in campos.items() if v is not None and v < 0]
    if negativos:
        fallas.append(Violacion("ck_teoricos_no_negativos", fila, ", ".join(negativos)))

    # El mismo margen de un centavo que la cláusula SQL, por la misma razón: los
    # dos lados redondean por separado.
    margen = Decimal("0.01")
    for moneda in ("ves", "usd"):
        teorico = campos[f"teorico_{moneda}"]
        descuento = campos[f"descuentos_teorico_{moneda}"]
        if teorico is not None and descuento is not None and descuento > teorico + margen:
            fallas.append(
                Violacion(
                    "ck_descuento_no_supera_el_teorico",
                    fila,
                    f"descuento {moneda.upper()} {descuento} sobre un teórico de {teorico}",
                )
            )
    return fallas


def exigir(fallas: Iterable[Violacion]) -> None:
    """Levanta ``InvarianteViolada`` si hay alguna, con todas en el mensaje.

    Todas y no la primera: si un lote trae cinco filas imposibles, verlas juntas
    dice si el problema es una fila o es el cálculo que las produjo.
    """
    lista = list(fallas)
    if not lista:
        return
    cuerpo = "\n  ".join(str(f) for f in lista)
    raise InvarianteViolada(
        f"{len(lista)} fila(s) violan una invariante de dinero y no se escribieron:\n"
        f"  {cuerpo}"
    )


def traducir_error_de_restriccion(exc: BaseException) -> str | None:
    """La frase legible de un error de Postgres, o ``None`` si no es uno de éstos.

    Existe para el camino que la validación de arriba no cubre: una escritura que
    no pasa por ``verificar_*`` y llega a la base. El nombre de la restricción
    viene en el mensaje de Postgres, así que se puede traducir sin adivinar.
    """
    m = _NOMBRE_DE_RESTRICCION.search(str(exc))
    if not m:
        return None
    nombre = m.group(1)
    porque = _POR_QUE.get(nombre)
    if porque is None:
        return f"La base rechazó la escritura por la restricción {nombre}."
    return f"La base rechazó la escritura: {porque} [{nombre}]."
