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
    "ck_vinc_no_sobreaplica_el_pago": (
        "las vinculaciones de un pago no pueden sumar más que el pago: sería "
        "acreditarle al cliente plata que nunca entró"
    ),
    "ck_linea_cantidad_no_negativa": (
        "una cantidad negativa en la línea es cómo Odoo representa una devolución "
        "que supera lo que quedó, y el espejo no debe guardarla así"
    ),
}

# Cuanto puede exceder la suma y seguir siendo redondeo. Es la misma que usa
# ``_detectar_vinculaciones_sobreaplicadas``, para que el detector y la
# invariante no puedan discrepar sobre el mismo pago.
TOLERANCIA_SOBREAPLICACION = Decimal("0.05")

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


def verificar_no_sobreaplica(
    nueva: Any, ya_aplicadas: Iterable[Any], monto_del_pago: Any
) -> list[Violacion]:
    """Que las vinculaciones de un pago no sumen más que el pago.

    La novena invariante, y la única que **no puede** ser un ``CHECK`` de la base:
    no habla de una fila sino de la suma de varias, y Postgres no expresa eso sin
    un trigger. Vive acá, en el repositorio, que es donde pasa toda escritura.

    **Por qué hace falta.** Medido sobre la copia de producción el 11-sep-2026:
    diez pagos, todos en dólares, tienen vinculaciones que suman más que el pago --
    1.269,25 USD de exceso. El peor es el pago 200: vale 134,00 y tiene aplicados
    715,04 en nueve parciales. Ya existía un detector que lo **reporta**
    (``_detectar_vinculaciones_sobreaplicadas``) y ningún lugar que lo **rechace**.

    La dirección importa: un aplicado inflado hace que la orden se vea más pagada,
    así que sale de la cuenta por cobrar antes de estar cobrada, y además dispara
    las reglas que exigen pago previo.

    **La regla exacta es que el exceso no crezca.** ``ya_aplicadas`` son las
    vinculaciones que el pago ya tiene (en un ``update``, incluida la versión
    vieja de la misma fila). Se acepta la escritura si la suma nueva cabe en el
    pago, **o** si no supera la suma que ya había: reescribir una fila igual o
    menor pasa siempre, aunque el pago ya estuviera sobreaplicado. Lo que se
    rechaza es cualquier escritura que deje al pago con MÁS aplicado del que
    tenía y por encima de lo que vale -- una fila nueva sobre un pago ya
    sobreaplicado también, porque también lo agranda. Corregir los diez
    existentes mueve montos y es una decisión del usuario; impedir que crezcan
    no mueve ninguno.

    **Por qué esta versión y no la del 11-sep a la mañana.** La primera
    excusaba el caso «las hermanas ya exceden solas» y nada más. Con eso, la
    reescritura IDÉNTICA de la única vinculación del pago 200 (318,27 sobre
    134,00) se rechazaba: cero hermanas, 318,27 «de esta», por encima del tope.
    Y como el motor escribe todas sus vinculaciones en un solo lote, ese
    rechazo tumbaba la escritura entera de cada ciclo del demonio. Lo encontró
    el banco de escenarios la primera vez que corrió con la invariante puesta.

    ``monto_del_pago`` y los montos aplicados tienen que venir en **la misma
    moneda**. Suena obvio y es el error que cometí midiendo esto: comparar el
    aplicado en la moneda del pago contra la referencia en dólares daba 417 pagos
    y 82,9 millones de exceso, un número absurdo que delató la mezcla de unidades.
    """
    tope = _dec(monto_del_pago)
    monto_nuevo = _dec(getattr(nueva, "monto_aplicado", None))
    if tope is None or tope <= 0 or monto_nuevo is None:
        # Sin tope confiable no se afirma nada: rechazar acá convertiría un dato
        # ausente en un error, que es justo lo que este plan viene corrigiendo.
        return []

    vinc_id = str(getattr(nueva, "vinc_id", "") or "")
    ya = Decimal("0")  # las otras filas del pago
    antes = Decimal("0")  # todo lo que sumaba el pago antes de esta escritura
    for v in ya_aplicadas:
        parcial = _dec(getattr(v, "monto_aplicado", None))
        if parcial is None:
            continue
        antes += parcial
        # La propia fila no se cuenta dos veces: un update la trae en las dos
        # listas, y sin esta guarda una reescritura idéntica se rechazaría.
        if str(getattr(v, "vinc_id", "") or "") == vinc_id:
            continue
        ya += parcial

    suma = ya + monto_nuevo
    if suma <= tope + TOLERANCIA_SOBREAPLICACION:
        return []

    # El pago queda por encima de lo que vale, pero no con más de lo que ya
    # tenía: el exceso no lo trae esta escritura. Es el caso de cada reescritura
    # del sync sobre los diez que ya están mal. Se deja que el detector lo
    # reporte.
    if suma <= antes:
        return []

    pago = str(getattr(nueva, "pago_id", "?") or "?")
    return [
        Violacion(
            "ck_vinc_no_sobreaplica_el_pago",
            f"pago {pago}",
            f"el pago vale {tope} y las vinculaciones sumarían {suma} "
            f"({ya} ya aplicados + {monto_nuevo} de esta; antes sumaban {antes})",
        )
    ]


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
