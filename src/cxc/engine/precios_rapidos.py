"""El resolver que evita miles de llamadas a Odoo, y la mina que esconde.

Quinta pieza de la Fase 2.4 del plan de blindaje. El documento la tenía anotada
como la condición para poder arreglar la **mina 1** del inventario de la
[1.1](../../../docs/blindaje/1.1-fallbacks-silenciosos.md): "precio 0 con Odoo
caído". Estaba definida como clase anidada dentro de
``_get_reporte_saldos_sync``, una función de 1.149 líneas, así que no había forma
de escribirle un test sin levantar la aplicación entera con Odoo enfrente.

**Qué hace.** El reporte de saldos recalcula el precio teórico de cada línea de
cada orden. Preguntarle a Odoo producto por producto son miles de llamadas XML-RPC
-- de ahí el nombre. En vez de eso arma un índice con los precios unitarios que
**ya están en el espejo local**, que es el precio al que la línea se vendió, y solo
cae al resolver real para lo que no encuentre.

**La mina.** Cuando no lo encuentra ni en el índice ni en el resolver de respaldo
--o cuando el resolver de respaldo existe pero falla, que es lo que pasa con Odoo
caído-- devuelve ``Decimal("0")``. Un precio cero no se distingue de un producto
regalado: la orden se valora en cero, su saldo teórico queda en cero, y **la orden
se ve como cobrada**. No hay excepción, no hay log.

**Este módulo no cambia ese comportamiento**, porque cambiarlo mueve montos en una
pantalla en uso y eso es una decisión del usuario (regla de la Fase 1). Lo que hace
es dos cosas que no mueven nada:

1. sacarlo a donde se le puedan escribir tests, que era el pedido del plan;
2. **contarlo**. ``ceros_silenciosos`` registra cada ``(producto, lista)`` que
   terminó en cero y por qué, así que el llamador puede decir "este reporte se armó
   con N precios que no pude resolver" en vez de mostrar los números como si todos
   fueran buenos.

**Una observación que salió de moverlo.** El índice se llena con cuatro claves por
producto: las listas ``"4"`` y ``"5"`` y sus nombres ``"Precio USD"`` y
``"Precio USD Pago VES"``. En la copia de producción **esas dos listas están
archivadas** desde el recambio de septiembre de 2026 (ver ``engine/listas.py``), así
que un precio pedido para las listas vigentes no acierta ninguna clave del índice y
se va al resolver de respaldo -- o al cero, si Odoo no contesta. O sea que el atajo
que existe para no llamar a Odoo dejó de aplicar justamente cuando las listas
cambiaron, en silencio. Está medido y anotado; corregir las claves cambia precios,
así que tampoco se toca acá.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from cxc.engine.price_resolver import PriceResolver

# Las claves con las que se indexa cada precio del espejo. Son cuatro y no dos
# porque los llamadores piden la lista tanto por id como por nombre lógico.
#
# Ver el docstring del módulo: en la copia de producción las listas 4 y 5 están
# ARCHIVADAS, así que este índice ya no acierta para las vigentes. Se deja tal
# cual porque cambiarlo mueve precios.
CLAVES_DE_LISTA = ("5", "Precio USD Pago VES", "4", "Precio USD")

CERO = Decimal("0")


@dataclass
class CeroSilencioso:
    """Una vez que ``precio()`` no pudo resolver y devolvió cero."""

    producto: str
    lista: str
    motivo: str


@dataclass
class ResolverRapidoDePrecios(PriceResolver):
    """Precios desde el espejo local, con el resolver real como respaldo.

    ``lines_map`` es ``{so_id: [LineaOrden, ...]}``: se recorre una vez al
    construir y de ahí sale el índice. ``fallback`` es el resolver que sí habla
    con Odoo, y puede ser ``None``.

    Cada cero que devuelve queda en ``ceros_silenciosos``. Esa lista es la
    diferencia entre "el reporte dice que esta orden está cobrada" y "el reporte
    no pudo averiguar el precio de esta orden y la muestra como cobrada".
    """

    lines_map: Any = None
    fallback: PriceResolver | None = None
    _precios: dict[tuple[str, str], Decimal] = field(default_factory=dict, init=False)
    ceros_silenciosos: list[CeroSilencioso] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        for lineas in (self.lines_map or {}).values():
            for linea in lineas:
                producto = getattr(linea, "producto", None)
                precio = getattr(linea, "precio_unitario", None)
                if producto and precio is not None:
                    clave = str(producto).strip()
                    for lista in CLAVES_DE_LISTA:
                        self._precios[(clave, lista)] = precio

    # --- la interfaz de PriceResolver ---------------------------------------

    def precio(self, producto: str, lista: str, fecha: date | None = None) -> Decimal:
        clave = str(producto).strip()
        directo = self._precios.get((clave, str(lista)))
        if directo is not None:
            return directo
        # La lista "5" como respaldo dentro del propio índice: es el
        # comportamiento original y se preserva tal cual.
        por_defecto = self._precios.get((clave, "5"))
        if por_defecto is not None:
            return por_defecto
        if self.fallback is not None:
            try:
                return self.fallback.precio(producto, lista, fecha)
            except Exception as exc:  # noqa: BLE001 -- se registra, no se traga
                self.ceros_silenciosos.append(
                    CeroSilencioso(clave, str(lista), f"el respaldo falló: {str(exc)[:120]}")
                )
                return CERO
        self.ceros_silenciosos.append(
            CeroSilencioso(
                clave,
                str(lista),
                "sin precio en el espejo y sin resolver de respaldo",
            )
        )
        return CERO

    def volumen(self, producto: str) -> Decimal:
        if self.fallback is not None:
            try:
                return self.fallback.volumen(producto)
            except Exception:  # noqa: BLE001
                # Un volumen en cero no saca una orden de la cuenta por cobrar:
                # solo apaga los descuentos por volumen, que es un efecto acotado
                # y en la dirección conservadora. No entra al conteo de ceros
                # silenciosos para que ese conteo signifique una sola cosa.
                return CERO
        return CERO

    # --- lo que el llamador necesita para no mentir -------------------------

    @property
    def productos_indexados(self) -> int:
        """Cuántos productos distintos tiene el índice.

        Si esto da cero, el resolver no está acelerando nada: cada precio va al
        respaldo, o al cero.
        """
        return len({p for p, _ in self._precios})

    @property
    def hubo_ceros(self) -> bool:
        return bool(self.ceros_silenciosos)

    def resumen_de_ceros(self, tope: int = 6) -> str:
        """Una línea para el log. Vacía si no hubo ceros."""
        if not self.ceros_silenciosos:
            return ""
        pares = {(c.producto, c.lista) for c in self.ceros_silenciosos}
        motivos = {c.motivo for c in self.ceros_silenciosos}
        muestra = ", ".join(f"{p}/{lista}" for p, lista in sorted(pares)[:tope])
        return (
            f"{len(self.ceros_silenciosos)} precio(s) se resolvieron en CERO sobre "
            f"{len(pares)} par(es) producto/lista distintos. Una orden valorada en "
            f"cero se ve como cobrada. Ejemplos: {muestra}. "
            f"Motivos: {'; '.join(sorted(motivos))[:240]}"
        )
