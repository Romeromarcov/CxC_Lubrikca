"""Ventana y mapa de la Lista Histórica de Auditoría (Tarea 2 -- Ventas).

Fuente única de verdad para la fecha de corte y el criterio de "orden
histórica" -- antes esto vivía duplicado en tres lugares distintos de
``web/app.py`` (con un off-by-one real entre copias). El motor central
(``engine/discounts.py`` -> ``BandejaFacturacion.precio_base_calculado``,
lo que alimenta ``/api/ventas``) necesita el mismo criterio que usan los
endpoints de reporte, o las órdenes sin lista/del período histórico
muestran teórico $0.00 en Ventas aunque el reporte de saldos sí las
resuelva bien.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

# Extremo superior verificado contra datos reales: S00092 (primera orden
# con lista de Odoo propia y consistente) está fechada 2026-03-13 -- por eso
# el corte es EXCLUSIVO ese día. Sin cota inferior no hay riesgo práctico
# hoy (no existen órdenes antes de 2026-02-25), pero se define para que
# coincida con el rango de negocio documentado.
HISTORICAL_PRICE_LIST_START = date(2026, 2, 20)
HISTORICAL_PRICE_LIST_END_EXCLUSIVE = date(2026, 3, 13)


def es_orden_historica(fecha: date | None, lista_id_str: str, enabled: bool = True) -> bool:
    """True si la orden debe usar la Lista Histórica de Auditoría en vez de
    su lista de Odoo asignada (o si nunca tuvo lista asignada -- ese caso es
    incondicional, no depende del toggle ni de la ventana)."""
    lista_id_str = (lista_id_str or "").strip()
    if not lista_id_str or lista_id_str in ("0", "None"):
        return True
    if not enabled or fecha is None:
        return False
    return HISTORICAL_PRICE_LIST_START <= fecha < HISTORICAL_PRICE_LIST_END_EXCLUSIVE


def cargar_mapa_historico(rows: Sequence[Mapping[str, str]]) -> dict[str, dict[str, object]]:
    """Convierte las filas crudas de ``listas_precios_historicas`` en un mapa

    ``producto_id_odoo -> {"nombre", "usd", "eur"}``.

    Bug real encontrado en auditoría (agosto 2026, ver
    ``scripts/cruzar_codigos_lista_historica.py``): ``LineaOrden.producto``
    (la clave que ``_precio_unitario_linea`` usa en tiempo de ejecución)
    es el ``product_id`` de Odoo, NO el "código" de la columna A del CSV
    origen de esta lista (numeración interna de Lubrikca, sin relación con
    Odoo). Antes el mapa se indexaba por ese "código" crudo -- el
    override histórico nunca hacía match contra una orden real. Ahora se
    indexa por ``producto_id_odoo`` (columna resuelta por el script de
    cruce, código<->default_code<->product_id), la misma clave que
    ``linea.producto``. Filas sin ``producto_id_odoo`` resuelto se
    excluyen (no hay forma de que hagan match jamás).
    """
    mapa: dict[str, dict[str, object]] = {}
    for r in rows:
        pid_odoo = str(r.get("producto_id_odoo", "") or "").strip()
        if not pid_odoo or not pid_odoo.isdigit():
            continue
        try:
            mapa[pid_odoo] = {
                "nombre": r.get("producto_nombre", ""),
                "usd": Decimal(str(r.get("precio_usd", "0") or "0")),
                "eur": Decimal(str(r.get("precio_bcv_euro", "0") or "0")),
            }
        except Exception:
            continue
    return mapa
