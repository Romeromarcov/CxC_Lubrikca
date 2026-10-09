"""Sincroniza las filas de la bandeja de auditoria con las discrepancias vigentes.

Hasta el 9-oct-2026 los generadores de la bandeja de descuentos y NC escribian una fila NUEVA por
orden, por dia y por tipo (``audit_id = {so}_{tipo}_{fecha}``) y no cerraban ninguna: 648 ordenes
llegaron a 17.635 filas pendientes de ``descuento_factura`` (44.653 filas y 12 MB en toda la
tabla), y la pestana las listaba todas. Esto deja el modelo en una fila abierta por (orden, tipo):

- Una discrepancia vigente SIN fila se escribe con un ``audit_id`` estable.
- Con fila abierta (``pendiente``) se refresca en el lugar, y solo si algo cambio (monto/detalle).
- Con una fila ya decidida por una persona (``revisado``, ``aprobado``, ``rechazado``) y el mismo
  monto, la decision se respeta y no se reabre; si el monto cambio, vuelve a abrirse.
- Una fila abierta cuya discrepancia ya no existe se CIERRA automaticamente (``resuelto``).
- Las filas abiertas repetidas de legado se cierran, dejando la mas reciente.

Funcion pura: no lee ni escribe el repositorio.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

ABIERTA = "pendiente"
ABIERTAS = frozenset({"pendiente", "pendiente_revision"})
RESUELTO = "resuelto"
_DECIDIDAS = frozenset({"revisado", "aprobado", "rechazado", "aplicado"})
TOLERANCIA_MONTO = 0.01


def _num(valor: Any) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def _clave_por_orden(fila: dict[str, Any]) -> tuple[str, str]:
    return (str(fila.get("so_id") or ""), str(fila.get("tipo_auditoria") or ""))


def clave_por_pago(fila: dict[str, Any]) -> tuple[str, str]:
    return (
        str(fila.get("pago_id") or fila.get("so_id") or ""),
        str(fila.get("tipo_auditoria") or ""),
    )


def sincronizar_filas(
    existentes: Iterable[dict[str, Any]],
    actuales: Iterable[dict[str, Any]],
    *,
    tipos: set[str],
    evaluadas: set[str] | None = None,
    clave: Callable[[dict[str, Any]], tuple[str, str]] = _clave_por_orden,
    id_estable: Callable[[dict[str, Any]], str] | None = None,
    puede_cerrar: Callable[[dict[str, Any]], bool] | None = None,
    comparar_detalle: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Devuelve ``(filas_a_escribir, audit_ids_a_cerrar)``.

    ``tipos``: los ``tipo_auditoria`` que este generador gobierna (no toca los demas).
    ``evaluadas``: ids (orden o pago, segun ``clave``) que el generador SI evaluo en esta
    corrida; una fila abierta solo se cierra si su id esta ahi (``None`` = se evaluo todo).
    ``id_estable``: arma el ``audit_id`` de una fila nueva (por defecto ``{id}_{tipo}``).
    ``puede_cerrar``: limita que filas abiertas puede cerrar este generador (cuando dos
    generadores comparten tipo y cada uno solo gobierna una parte).
    ``comparar_detalle``: ademas del monto, un texto distinto refresca la fila (para las filas
    sin monto, como «vinculacion_pendiente_revisar», donde lo que cambia es el detalle).
    """
    puede_cerrar = puede_cerrar or (lambda _f: True)
    id_estable = id_estable or (lambda f: "_".join(clave(f)))
    por_clave: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for fila in existentes:
        if str(fila.get("tipo_auditoria") or "") in tipos:
            por_clave.setdefault(clave(fila), []).append(fila)

    a_escribir: list[dict[str, Any]] = []
    a_cerrar: list[str] = []
    vistas: set[tuple[str, str]] = set()

    for nueva in actuales:
        k = clave(nueva)
        if k in vistas:
            continue
        vistas.add(k)
        previas = sorted(
            por_clave.get(k, []), key=lambda f: str(f.get("timestamp_audit") or ""), reverse=True
        )
        abiertas = [f for f in previas if f.get("estado") in ABIERTAS]
        for sobrante in abiertas[1:]:  # duplicados de legado: se queda la mas reciente
            a_cerrar.append(str(sobrante["audit_id"]))

        if abiertas:
            previa = abiertas[0]
            # Solo el MONTO decide si se refresca: dos generadores escriben el mismo tipo con
            # distinto nivel de detalle, y comparar el texto los haria pisarse en cada ciclo.
            cambio = (
                abs(_num(previa.get("diferencia_usd")) - _num(nueva.get("diferencia_usd")))
                > TOLERANCIA_MONTO
            ) or (
                comparar_detalle
                and str(previa.get("detalle_motor") or "") != str(nueva.get("detalle_motor") or "")
            )
            if cambio:
                a_escribir.append(
                    {
                        **nueva,
                        "audit_id": previa["audit_id"],
                        "estado": nueva.get("estado") or ABIERTA,
                    }
                )
            continue

        decidida = next((f for f in previas if f.get("estado") in _DECIDIDAS), None)
        if decidida is not None and (
            abs(_num(decidida.get("diferencia_usd")) - _num(nueva.get("diferencia_usd")))
            <= TOLERANCIA_MONTO
        ):
            continue  # la persona ya decidio sobre ESTE monto
        reutilizable = previas[0]["audit_id"] if previas else id_estable(nueva)
        a_escribir.append(
            {**nueva, "audit_id": reutilizable, "estado": nueva.get("estado") or ABIERTA}
        )

    for k, filas in por_clave.items():
        if k in vistas:
            continue
        if evaluadas is not None and k[0] not in evaluadas:
            continue
        a_cerrar.extend(
            str(f["audit_id"]) for f in filas if f.get("estado") in ABIERTAS and puede_cerrar(f)
        )
    return a_escribir, a_cerrar
