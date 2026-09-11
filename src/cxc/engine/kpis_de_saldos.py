"""Los KPI del reporte de saldos, recalculados desde las filas que quedan.

Vigesimonovena pieza de la Fase 2.4, salida de ``get_reporte_saldos`` (14 de 32 lineas
sin cubrir).

**El defecto, y esta vez lo introdujo un arreglo.** `get_reporte_saldos` filtra las
ordenes que el arbol de CxC ya dio por cobradas, con un comentario que explica bien por
que: "19 ordenes por $8.278,15 (2,9 % de la cartera reportada) aparecian como deuda
estando cobradas, y un cobrador salia a perseguirlas". El filtro saca esas filas de
``items`` y de ``saldo_minimo_pendientes``.

**Y no toca ``kpis``.** Asi que despues del filtro el encabezado --total general, total
vencido, vigentes, y los cuatro tramos de mora-- sigue incluyendo las ordenes que las
filas de abajo ya no tienen. El encabezado y el detalle de la misma pantalla difieren
exactamente en el monto de lo filtrado.

Esto NO cambia lo que la pantalla muestra hoy: calcula el segundo juego de KPI y la
diferencia, para que se vea. Corregir el encabezado baja el total de la cartera
reportada, o sea mueve un monto, y eso pasa por el visto bueno del usuario (regla de la
Fase 1).

Los tramos de mora, tal como los arma el cuerpo original:

    vigentes         dias_vencido <= 0
    vencido total    dias_vencido >= 1     (la suma de los cuatro de abajo)
      1 a 30         1 <= dias_vencido <= 30
      31 a 60        31 <= dias_vencido <= 60
      61 a 90        61 <= dias_vencido <= 90
      mas de 90      dias_vencido > 90
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Los cuatro sub-saldos que cada KPI acumula, y de que campo de la fila sale cada uno.
# El nombre del KPI no coincide con el de la fila en tres de los cuatro, que es parte
# de por que la cuenta se podia perder de vista.
CAMPOS = {
    "deudor_bcv": "saldo_deudor_bcv",
    "desc_bcv": "saldo_con_descuento_bcv",
    "desc_usd": "saldo_con_descuento_lista_usd",
    "factura_odoo": "saldo_factura_odoo",
}

# Una fila entra a los KPI solo si alguno de sus saldos supera este umbral. Es el mismo
# 0,05 del cuerpo original: cinco centavos de residual no son una deuda.
UMBRAL_DE_SALDO = 0.05

NOMBRES = (
    "total_general",
    "total_vencido",
    "vigentes",
    "vencidas_1_30",
    "vencidas_31_60",
    "vencidas_61_90",
    "vencidas_mas_90",
)


def _num(valor: Any) -> float:
    if valor is None or valor is False or valor == "":
        return 0.0
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def _vacio() -> dict[str, float]:
    return dict.fromkeys(CAMPOS, 0.0)


def _tramo(dias: float) -> str | None:
    """En que tramo de mora cae una fila, o ``None`` si esta vigente."""
    if dias <= 0:
        return None
    if dias <= 30:
        return "vencidas_1_30"
    if dias <= 60:
        return "vencidas_31_60"
    if dias <= 90:
        return "vencidas_61_90"
    return "vencidas_mas_90"


def kpis_de_filas(filas: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Los siete KPI acumulados sobre ``filas``, con la misma regla del cuerpo original.

    Una fila cuenta en ``total_general`` y ademas en ``vigentes`` **o** en
    ``total_vencido`` mas su tramo -- nunca en los dos lados, que es lo que hace que
    ``vigentes + total_vencido == total_general``.
    """
    kpis = {nombre: _vacio() for nombre in NOMBRES}
    for fila in filas:
        sub = {k: _num(fila.get(campo)) for k, campo in CAMPOS.items()}
        # El umbral mira los tres saldos que el cuerpo original mira: el deudor en BCV,
        # el de la lista USD y el de la factura de Odoo. El `desc_bcv` NO entra en la
        # condicion, igual que en el original.
        if not any(
            sub[k] > UMBRAL_DE_SALDO for k in ("deudor_bcv", "desc_usd", "factura_odoo")
        ):
            continue
        destinos = ["total_general"]
        tramo = _tramo(_num(fila.get("dias_vencido")))
        destinos.extend(["vigentes"] if tramo is None else ["total_vencido", tramo])
        for destino in destinos:
            for k, valor in sub.items():
                kpis[destino][k] += valor
    return kpis


@dataclass(frozen=True)
class DiferenciaDeKpis:
    """Lo que el encabezado dice de mas respecto de las filas que quedaron."""

    publicados: dict[str, dict[str, float]]
    recalculados: dict[str, dict[str, float]]
    filas_quitadas: int

    @property
    def diferencias(self) -> dict[str, dict[str, float]]:
        """``publicado - recalculado`` por KPI y sub-saldo, solo donde no es cero."""
        salida: dict[str, dict[str, float]] = {}
        for nombre in NOMBRES:
            pub = self.publicados.get(nombre) or {}
            rec = self.recalculados.get(nombre) or {}
            deltas = {
                k: round(_num(pub.get(k)) - _num(rec.get(k)), 2)
                for k in CAMPOS
                if abs(_num(pub.get(k)) - _num(rec.get(k))) > 0.01
            }
            if deltas:
                salida[nombre] = deltas
        return salida

    @property
    def coinciden(self) -> bool:
        return not self.diferencias

    @property
    def nota(self) -> str:
        if self.coinciden:
            return (
                "El encabezado y las filas coinciden"
                + (f" (se quitaron {self.filas_quitadas} fila(s))." if self.filas_quitadas else ".")
            )
        general = self.diferencias.get("total_general", {})
        return (
            f"El encabezado incluye {self.filas_quitadas} orden(es) que las filas ya no "
            f"tienen: el total general dice "
            f"{general.get('deudor_bcv', 0.0):,.2f} de mas en el saldo deudor BCV y "
            f"{general.get('desc_usd', 0.0):,.2f} en el de lista USD. Corregir el "
            f"encabezado baja el total de la cartera reportada, asi que es decision del "
            f"usuario."
        )


def diferencia_de_kpis(
    publicados: dict[str, Any], filas_que_quedan: list[dict[str, Any]], filas_quitadas: int
) -> DiferenciaDeKpis:
    """Compara los KPI publicados contra los que salen de las filas que sobrevivieron."""
    return DiferenciaDeKpis(
        publicados={k: dict(v) for k, v in (publicados or {}).items() if isinstance(v, dict)},
        recalculados=kpis_de_filas(filas_que_quedan),
        filas_quitadas=filas_quitadas,
    )
