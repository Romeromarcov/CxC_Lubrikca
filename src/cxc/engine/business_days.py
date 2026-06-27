"""Cálculo de días hábiles y ventana de contado (sección 4.6).

"Día hábil" salta sábados, domingos **y** los feriados de la tabla 3.8b. Un
cálculo que solo salte fines de semana falla en semanas con feriado decretado.
"""

from __future__ import annotations

from datetime import date, timedelta


def es_dia_habil(d: date, feriados: frozenset[date]) -> bool:
    return d.weekday() < 5 and d not in feriados


def sumar_dias_habiles(inicio: date, n: int, feriados: frozenset[date]) -> date:
    """Avanza ``n`` días hábiles a partir de ``inicio`` (sin contar ``inicio``).

    Ejemplo de la especificación: entrega viernes + 3 hábiles → miércoles
    (salta sábado y domingo).
    """
    if n < 0:
        raise ValueError("n debe ser >= 0")
    actual = inicio
    restantes = n
    while restantes > 0:
        actual += timedelta(days=1)
        if es_dia_habil(actual, feriados):
            restantes -= 1
    return actual


def fin_ventana_contado(
    fecha_entrega: date, dias_habiles: int, feriados: frozenset[date]
) -> date:
    """Último día de la ventana [entrega, entrega + N días hábiles]."""
    return sumar_dias_habiles(fecha_entrega, dias_habiles, feriados)
