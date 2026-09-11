"""El mismo pago cargado dos veces, y por qué la clave tiene cinco campos.

Decimoquinta pieza de la Fase 2.4, elegida por el mismo barrido que la anterior:
funciones de ``app.py`` cuyo nombre habla de dinero y que ninguna prueba nombra.
``_detectar_pagos_duplicados`` era una de ellas.

**Qué detecta.** Dos pagos con cliente, monto, moneda, método y fecha idénticos:
el mismo pago cargado dos veces en Odoo, o un banco que reporta la misma
transacción dos veces. Un duplicado infla la cobranza y saca de la cuenta por
cobrar una orden que no se cobró.

**Por qué compara contra TODO el universo y no sólo los pendientes.** El caso real
es que el pago «original» ya esté aplicado y el que entra de nuevo —todavía sin
asociar— sea el sospechoso. Comparar sólo entre pendientes no detectaría ese caso,
que es el más común de un duplicado real.

**Medido sobre la copia de producción (11-sep-2026): 19 grupos**, y acá está lo
que importa para usarlo: **13 son del banco de escenarios** (clientes
``ZZ BLINDAJE``, todos del 05-sep con montos idénticos) y **6 son de clientes
reales**:

| cliente | monto | fecha |
|---|---:|---|
| En ascenso 2011,c.a. | 40.187,53 VES | 19-ago |
| En ascenso 2011,c.a. | 72.287,74 VES | 31-jul |
| INVERSIONES MI LINDA YEMAIRE 2019 | 56.976,38 VES | 30-abr |
| AUTOPERIQUITOS LA CAMPIÑA C.A. | 14.757,63 VES | 23-jul |
| Pedro Castro | 100,00 USD | 22-jul |
| Angel ARMAS | 75,00 USD | 05-jun |

El detector **no puede distinguir** los del banco de los reales, así que muestra
19 y alguien tiene que filtrar a mano. No se filtra acá a propósito: una función
que descarta pagos por el nombre del cliente esconderá un duplicado real el día
que alguien llame a un cliente parecido. La separación es del llamador, que sabe
si está mirando un entorno de prueba.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

# Los cinco campos que tienen que coincidir. Cada uno está por una razón:
#
# * ``cliente_id`` -- dos clientes distintos pueden pagar lo mismo el mismo día.
# * ``monto`` -- redondeado a dos decimales, porque el mismo pago puede llegar con
#   un tercer decimal distinto según por dónde entró.
# * ``moneda`` -- 100 USD y 100 VES no son el mismo pago, y acá NO se convierte:
#   un duplicado es el mismo documento, no dos montos equivalentes.
# * ``metodo_pago`` -- el mismo monto por transferencia y en efectivo el mismo día
#   es plausible y no es un duplicado.
# * ``fecha`` -- truncada al día. Deliberado: el mismo pago cargado dos veces
#   suele tener horas distintas.
CAMPOS_DE_LA_CLAVE = ("cliente_id", "monto", "moneda", "metodo_pago", "fecha")


def _monto(valor: Any) -> Decimal:
    """El monto a dos decimales. Lo ilegible vale cero y agrupa con los ceros.

    Agrupar los ilegibles entre sí es preferible a darle a cada uno un valor
    propio: si entran dos pagos con el monto roto, que se señalen como sospechosos
    es más útil que que pasen desapercibidos por separado.
    """
    if valor is None or valor is False or valor == "":
        return Decimal("0.00")
    try:
        return Decimal(str(valor)).quantize(Decimal("0.01"))
    except (TypeError, ValueError, InvalidOperation):
        return Decimal("0.00")


def clave_de_pago(pago: dict[str, Any]) -> tuple[str, Decimal, str, str, str]:
    """Los cinco campos normalizados que definen «el mismo pago».

    ``fecha_pago`` con ``fecha`` como respaldo: las dos existen según de dónde
    venga la fila.
    """
    return (
        str(pago.get("cliente_id", "") or "").strip(),
        _monto(pago.get("monto")),
        str(pago.get("moneda", "") or "").upper().strip(),
        str(pago.get("metodo_pago", "") or "").strip(),
        str(pago.get("fecha_pago") or pago.get("fecha") or "")[:10],
    )


def detectar_pagos_duplicados(pagos: list[dict[str, Any]]) -> dict[str, list[str]]:
    """``pago_id`` -> los OTROS ``pago_id`` que comparten su clave.

    Cada miembro del grupo lista a los demás y **no a sí mismo**: la pantalla
    muestra «este pago es sospechoso, mirá estos otros», y verse a sí mismo en esa
    lista haría que un grupo de dos pareciera de tres.

    Un pago sin ``pago_id`` se saltea: sin identificador no se puede señalar ni
    referenciar, y agruparlo con los demás sin id inventaría duplicados.
    """
    grupos: dict[tuple[str, Decimal, str, str, str], list[str]] = {}
    for p in pagos:
        pid = str(p.get("pago_id", "") or "").strip()
        if not pid:
            continue
        grupos.setdefault(clave_de_pago(p), []).append(pid)

    duplicados: dict[str, list[str]] = {}
    for pids in grupos.values():
        if len(pids) > 1:
            for pid in pids:
                duplicados[pid] = [otro for otro in pids if otro != pid]
    return duplicados
