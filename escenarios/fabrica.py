"""La situación inicial de cada escenario, armada de cero.

Cada escenario construye su propia orden en vez de mutar una copiada de
producción. Cuesta unos segundos más y compra dos cosas:

* el banco es **reejecutable** -- correrlo dos veces da lo mismo, que es
  exactamente la propiedad que la Fase 4 le exige al sync;
* los hallazgos son inequívocos -- si algo sale mal, salió mal por lo que el
  escenario hizo, no por el estado previo de una orden que alguien tocó en
  marzo.

Los cuatro estados que las filas de la tabla necesitan como punto de partida:
pedida, entregada, facturada y pagada. Cada uno construye sobre el anterior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from escenarios.odoo_qa import OdooQA, OrdenCreada


@dataclass
class Situacion:
    """Lo que quedó armado, con todo lo que un escenario necesita señalar."""

    orden: OrdenCreada
    cliente_id: int
    lista_id: int
    facturas: list[int] = field(default_factory=list)
    pagos: list[int] = field(default_factory=list)
    entregas: list[str] = field(default_factory=list)

    @property
    def nombre(self) -> str:
        return self.orden.nombre

    @property
    def so(self) -> int:
        return self.orden.id


@dataclass
class Fabrica:
    odoo: OdooQA
    listas: dict[str, Any]
    etiqueta: str

    # Fecha fija para todas las órdenes del banco: si la fecha se moviera con
    # el reloj, la lista vigente y la tasa aplicable cambiarían de una corrida
    # a la otra y los escenarios dejarían de ser comparables.
    FECHA_ORDEN = "2026-09-01 10:00:00"

    def _cliente(self, sufijo: str = "") -> int:
        # Un cliente por escenario: así los saldos de uno no contaminan al
        # otro, que es lo que haría fallar el arqueo por cliente del balance
        # por un motivo ajeno al escenario.
        return self.odoo.cliente(f"{self.etiqueta}{sufijo}"[:60])

    # --- los cuatro estados ---------------------------------------------

    def pedida(
        self,
        *,
        moneda: str = "ves",
        cantidad: float = 2,
        productos: int = 1,
        fecha: str | datetime | None = None,
    ) -> Situacion:
        """Orden confirmada, sin entregar."""
        lista = self.listas[moneda]
        cliente = self._cliente()
        lineas = [
            (self.odoo.producto_con_precio(lista, salteando=i), cantidad)
            for i in range(productos)
        ]
        orden = self.odoo.crear_orden(
            cliente, lista, lineas, fecha=fecha or self.FECHA_ORDEN
        )
        self.odoo.confirmar(orden.id)
        return Situacion(orden=orden, cliente_id=cliente, lista_id=lista)

    def entregada(self, *, completa: bool = True, **kwargs: Any) -> Situacion:
        """Orden entregada. Es lo que hace nacer la cuenta por cobrar."""
        situacion = self.pedida(**kwargs)
        situacion.entregas = self.odoo.entregar(situacion.so, completa=completa)
        return situacion

    def facturada(self, **kwargs: Any) -> Situacion:
        """Orden entregada y facturada, por el diario SIN imprenta digital."""
        situacion = self.entregada(**kwargs)
        situacion.facturas = self.odoo.facturar(situacion.so)
        assert situacion.facturas, f"No se generó factura para {situacion.nombre}"
        return situacion

    def pagada(self, *, proporcion: float = 1.0, **kwargs: Any) -> Situacion:
        """Orden facturada y cobrada. ``proporcion`` permite un abono parcial."""
        situacion = self.facturada(**kwargs)
        total = self.total_factura(situacion.facturas[0])
        situacion.pagos = [
            self.odoo.pagar(situacion.facturas, monto=round(total * proporcion, 2))
        ]
        return situacion

    # --- lecturas de apoyo ----------------------------------------------

    def total_factura(self, factura_id: int) -> float:
        return float(
            self.odoo.ex("account.move", "read", [[factura_id]], {"fields": ["amount_total"]})[0][
                "amount_total"
            ]
        )

    def total_orden(self, so: int) -> float:
        return float(
            self.odoo.ex("sale.order", "read", [[so]], {"fields": ["amount_total"]})[0][
                "amount_total"
            ]
        )

    def estado_orden(self, so: int) -> str:
        return str(
            self.odoo.ex("sale.order", "read", [[so]], {"fields": ["state"]})[0]["state"]
        )
