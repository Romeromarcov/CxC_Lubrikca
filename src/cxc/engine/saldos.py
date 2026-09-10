"""Cuánto falta cobrar de una orden, en sus cuatro referencias.

Primera pieza de la Fase 2.4 del plan de blindaje: sacar los caminos de dinero
de ``web/app.py``. Se eligió ésta para empezar por tres motivos que se refuerzan:

* es la **fuente única de verdad sobre cuánto falta cobrar** — el balance de
  comprobación lo dice así en su propio docstring, y toda partida que no cuadre
  significa que alguien dejó de usarla;
* es una función **pura**: entra un diccionario, sale un diccionario, sin
  repositorio, sin Odoo y sin caché. Se puede probar de a una sin levantar la
  aplicación, que es exactamente la razón por la que ``app.py`` está al 62 % y
  el motor entre 94 y 100 %;
* contiene una de las minas del inventario 1.1, y sacarla a la luz es el primer
  paso para poder discutirla (ver ``diagnostico_de_saldos``).

**No cambia ningún comportamiento.** La lógica es la que estaba en
``app.py:_saldos_4_columnas_item``, movida tal cual. Lo que se agrega es
``diagnostico_de_saldos``, que no calcula saldos sino que explica por qué
salieron así — y ésa es la parte nueva.

Las cuatro referencias, y por qué son cuatro:

``teorico_bs`` y ``teorico_usd``
    Lo que la orden debía según su teórico en cada una de las dos listas. Cuál
    de las dos manda depende de cómo termine pagando el cliente, y eso no se
    sabe hasta que paga -- por eso se llevan las dos.

``venta_real``
    Lo que Odoo dice que vale la orden, neto de los descuentos que gerencia
    aprobó a mano.

``factura_real``
    Lo mismo pero sobre lo facturado. Es ``None``, no cero, cuando la orden
    todavía no se facturó: no hay factura, así que no hay saldo de factura. La
    distinción importa y es la que el resto del módulo no hace.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Los campos del ítem de Ventas que alimentan cada referencia. Estar acá, con
# nombre, es lo que permite que ``diagnostico_de_saldos`` diga cuál faltaba.
CAMPO_TEORICO_VES = "ves_neta_teorica_iva"
CAMPO_TEORICO_USD = "usd_neta_teorica_iva"
CAMPO_VENTA_REAL = "venta_neta_real"
CAMPO_FACTURADO = "total_facturado_neto"
CAMPO_DESCUENTO_SISTEMA = "descuento_aplicado_sistema"
CAMPO_PAGADO_BCV = "pagado_teorico_bcv_incl_pendiente"
CAMPO_PAGADO_BINANCE = "pagado_teorico_binance_incl_pendiente"
CAMPO_PAGADO_REFERENCIA = "monto_pagado_factura_odoo_incl_pendiente"


def _num(item: dict[str, Any], campo: str) -> float:
    try:
        return float(item.get(campo) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def saldos_de_la_orden(item: dict[str, Any]) -> dict[str, float | None]:
    """Los 4 saldos pendientes de una orden, en tiempo real.

    Reusada por ``/api/ventas``, ``/api/reporte-cxc-cliente`` y
    ``/api/cobranza/pagos`` (el "Saldo Orden (CxC)" del modal de detalle de
    pago). Antes ``/api/cobranza/pagos`` mostraba un solo saldo mezclado; ahora
    son las mismas 4 referencias que el resto del sistema ya usa.

    Pedido explícito del usuario (agosto 2026, cliente CONSTRUCTORA GRANO
    AGREGADO / orden S00608): una Vinculación PENDIENTE de esta orden -- ya
    vinculada localmente, pero Odoo todavía no la reconcilió -- SÍ se resta de
    estos 4 saldos. Antes no restaba nada (solo contaba ``CONCILIADO``), así
    que un pago ya vinculado pero sin confirmar no aparecía ni como pagado ni
    como saldo a favor en ningún lado, y se mostraba el saldo completo sin
    tocar. De ahí los campos ``*_incl_pendiente``: es el mismo "beneficio de la
    duda" que ya usan ``sale_de_cxc``/``saldo_cxc`` en Ventas, y nunca gatea
    nada real (descuentos, salida de CxC confirmada) -- solo cambia lo que se
    MUESTRA acá.
    """
    desc_sistema = _num(item, CAMPO_DESCUENTO_SISTEMA)
    pagado_bcv = _num(item, CAMPO_PAGADO_BCV)
    pagado_binance = _num(item, CAMPO_PAGADO_BINANCE)
    pagado_ref = _num(item, CAMPO_PAGADO_REFERENCIA)

    saldo_teorico_bs = max(0.0, _num(item, CAMPO_TEORICO_VES) - pagado_bcv)
    saldo_teorico_usd = max(0.0, _num(item, CAMPO_TEORICO_USD) - pagado_binance)
    saldo_venta_real = max(0.0, _num(item, CAMPO_VENTA_REAL) - desc_sistema - pagado_ref)
    facturada = bool(item.get("facturada"))
    saldo_factura_real = (
        max(0.0, _num(item, CAMPO_FACTURADO) - desc_sistema - pagado_ref) if facturada else None
    )
    return {
        "teorico_bs": saldo_teorico_bs,
        "teorico_usd": saldo_teorico_usd,
        "venta_real": saldo_venta_real,
        "factura_real": saldo_factura_real,
    }


@dataclass(frozen=True)
class DiagnosticoSaldo:
    """Por qué los saldos de una orden salieron como salieron.

    No cambia ningún saldo: los explica. Existe porque ``saldos_de_la_orden``
    tiene dos comportamientos que un número solo no puede distinguir, y los dos
    están en el inventario 1.1 como mina de severidad alta:

    ``referencias_ausentes``
        Un teórico que **falta** entra al cálculo como ``0``, así que la orden
        sale con saldo cero -- o sea, **cobrada**. Un teórico ausente y un
        teórico genuinamente cero se ven idénticos, y no significan lo mismo:
        el primero quiere decir "el motor todavía no la calculó", el segundo
        "no hay nada que cobrar".

    ``recortes_a_cero``
        El ``max(0, …)`` tapa los negativos. Un saldo negativo es un sobrepago
        o un teórico mal calculado, y las dos cosas hay que verlas. Acá queda
        registrado cuánto se recortó y en qué referencia.

    ``evaluable`` es la pregunta que el llamador debería hacerse antes de
    mostrar el número: con referencias ausentes, el saldo no es un saldo.
    """

    so_id: str
    referencias_ausentes: tuple[str, ...]
    recortes_a_cero: dict[str, float]

    @property
    def evaluable(self) -> bool:
        return not self.referencias_ausentes

    @property
    def hay_sobrepago(self) -> bool:
        return any(v > 0.01 for v in self.recortes_a_cero.values())

    def __str__(self) -> str:
        if self.evaluable and not self.hay_sobrepago:
            return f"{self.so_id}: sin observaciones"
        partes = []
        if self.referencias_ausentes:
            partes.append("sin " + ", ".join(self.referencias_ausentes))
        for ref, monto in sorted(self.recortes_a_cero.items(), key=lambda x: -x[1]):
            if monto > 0.01:
                partes.append(f"{ref} recortado en {monto:,.2f}")
        return f"{self.so_id}: " + "; ".join(partes)


def diagnostico_de_saldos(item: dict[str, Any]) -> DiagnosticoSaldo:
    """Explica los saldos de una orden sin cambiarlos.

    Se separó del cálculo a propósito: cambiar lo que ``saldos_de_la_orden``
    devuelve mueve montos que hoy están en pantalla, y eso es una decisión del
    usuario (Fase 2.1 del plan). Esto se puede agregar hoy porque no toca
    ningún número -- solo permite preguntar.
    """
    ausentes = tuple(
        nombre
        for nombre, campo in (
            ("teórico VES", CAMPO_TEORICO_VES),
            ("teórico USD", CAMPO_TEORICO_USD),
            ("venta real", CAMPO_VENTA_REAL),
        )
        if item.get(campo) is None
    )

    desc_sistema = _num(item, CAMPO_DESCUENTO_SISTEMA)
    pagado_ref = _num(item, CAMPO_PAGADO_REFERENCIA)
    crudos = {
        "teorico_bs": _num(item, CAMPO_TEORICO_VES) - _num(item, CAMPO_PAGADO_BCV),
        "teorico_usd": _num(item, CAMPO_TEORICO_USD) - _num(item, CAMPO_PAGADO_BINANCE),
        "venta_real": _num(item, CAMPO_VENTA_REAL) - desc_sistema - pagado_ref,
    }
    if item.get("facturada"):
        crudos["factura_real"] = _num(item, CAMPO_FACTURADO) - desc_sistema - pagado_ref

    return DiagnosticoSaldo(
        so_id=str(item.get("so_id") or "?"),
        referencias_ausentes=ausentes,
        recortes_a_cero={ref: -v for ref, v in crudos.items() if v < 0},
    )
