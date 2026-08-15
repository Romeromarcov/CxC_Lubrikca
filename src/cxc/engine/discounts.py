"""Motor de descuentos — lógica central (secciones 4.0–4.7).

Disparador neto-objetivo (no nominal), apilamiento aditivo, reselección de lista
por método (gana sobre lista especial), contado condicional a ventana de días
hábiles, BCV-completo, regla de mezcla → Binance y cierre híbrido.

El motor es una función PURA: recibe dataclasses, devuelve una
``BandejaFacturacion``. No conoce Sheets ni Odoo (eso lo cablea el runner).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, TypeVar

from ..config import EngineConfig
from ..decimal_utils import q2
from ..models import (
    BandejaFacturacion,
    Condicion,
    DescuentoAplicado,
    DescuentoDiferencialCambiario,
    DescuentoMarcaCategoria,
    DescuentoProducto,
    DescuentoRecompra,
    DescuentoVolumen,
    EstadoBandeja,
    ExclusionRegla,
    Feriado,
    LineaOrden,
    MetodoPago,
    Moneda,
    OrdenVenta,
    PromocionPrimeraCompra,
    ReglaRecurrencia,
    TipoBeneficio,
    TipoDescuento,
    Vinculacion,
)
from .business_days import fin_ventana_contado
from .effective_dating import (
    _match_categoria,
    _match_lista,
    _vigente,
    descuento_producto_vigente,
    descuento_vigente,
    regla_recurrencia_vigente,
)
from .equivalents import (
    congelar_en_vinculacion,
    es_ruta_bcv_pura,
    valor_pagado_bcv_usd,
    valor_pagado_binance_usd,
    valor_pagado_usd,
)
from .price_resolver import PriceResolver

# Epsilon para comparar "alcanzó el neto" sin que el redondeo niegue un cierre.
_EPS = Decimal("0.01")


@dataclass
class EngineInputs:
    orden: OrdenVenta
    lineas: list[LineaOrden]
    # Cada abono: la vinculación (con equivalentes congelados) + su método.
    abonos: list[tuple[Vinculacion, MetodoPago]]
    descuentos: list[DescuentoMarcaCategoria]
    descuentos_volumen: list[DescuentoVolumen]
    reglas_recurrencia: list[ReglaRecurrencia]
    promociones_primera_compra: list[PromocionPrimeraCompra]
    feriados_tabla: list[Feriado]
    price_resolver: PriceResolver
    engine_config: EngineConfig
    fecha_calculo: date
    all_ordenes: list[OrdenVenta] = field(default_factory=list)
    exclusiones: list[ExclusionRegla] = field(default_factory=list)
    descuentos_recompra: list[DescuentoRecompra] = field(default_factory=list)
    descuentos_diferencial: list[DescuentoDiferencialCambiario] = field(default_factory=list)
    descuentos_producto: list[DescuentoProducto] = field(default_factory=list)
    # Volumen "acumulado" (DescuentoVolumen.tipo_evaluacion == "acumulado"):
    # otras órdenes del MISMO cliente (con sus líneas) para sumar litros/
    # cajas dentro de la ventana de la regla (dias_evaluacion), además de
    # esta orden. Vacío = el umbral se evalúa solo con esta orden (lo que
    # ya hacía el motor antes de este cableo).
    historial_cliente_lineas: list[tuple[OrdenVenta, list[LineaOrden]]] = field(
        default_factory=list
    )
    # Tarea 3 (auditoria reglas por lista): listas de precio (ids de Odoo)
    # configuradas en Configuración como VES/USD -- vacías = el matching de
    # "LISTAS_VES"/"LISTAS_USD" en listas_aplicables cae al default
    # hardcodeado de effective_dating._match_lista (comportamiento previo).
    valid_ves: list[str] = field(default_factory=list)
    valid_usd: list[str] = field(default_factory=list)
    # Tarea 2 (Lista Histórica de Auditoría): si la orden cae en la
    # excepción histórica, el precio unitario de cada línea sale de este
    # mapa (codigo producto -> precio usd) en vez de la pricelist normal.
    orden_es_historica: bool = False
    historical_price_map: dict[str, dict[str, object]] = field(default_factory=dict)
    # Recompra (ventana = días de crédito reales de la orden anterior del
    # cliente + dias_gracia de la regla): la orden anterior más reciente
    # (misma cliente_id, fecha < la de esta orden) y SUS vinculaciones, para
    # poder verificar si quedó totalmente pagada. Calculado en
    # ``EngineRunner.build_inputs`` (necesita repo para las vinculaciones de
    # OTRA orden) -- ``discounts.py`` se queda puro, sin tocar el repo.
    orden_anterior_cliente: OrdenVenta | None = None
    orden_anterior_cliente_vincs: list[Vinculacion] = field(default_factory=list)
    # Diferencial Cambiario, regla "Equiparar" (agosto 2026): True si el
    # CLIENTE de esta orden tiene, en Odoo, algún pago sin aplicar/conciliar
    # contra ninguna factura ("pago huérfano" -- mismo concepto que ya usa
    # el reporte CxC por Cliente), sin importar si ese huérfano pertenece a
    # esta orden u otra del mismo cliente. Bloquea la regla "Equiparar"
    # mientras haya CUALQUIER huérfano abierto (uno ya cerrado manualmente
    # vía ``pagos_huerfanos_cerrados`` no cuenta). Requiere datos EN VIVO de
    # Odoo para calcularse correctamente -- ``discounts.py`` se queda puro,
    # sin tocar Odoo/el repo, así que el default conservador es False (no
    # se otorga el descuento) cuando el llamador no puede determinarlo (ej.
    # ``EngineRunner.build_inputs``, que no tiene la data de conciliación
    # con Odoo que sí tiene ``get_reporte_saldos``).
    cliente_tiene_pagos_huerfanos: bool = False

    @property
    def feriados(self) -> frozenset[date]:
        return frozenset(f.fecha for f in self.feriados_tabla)


@dataclass
class _Componentes:
    precio_base: Decimal
    pct_recompra: Decimal
    contado_proy: Decimal
    diferencial_cambiario: Decimal
    volumen: Decimal
    nc: Decimal
    detalle_recompra: DescuentoAplicado | None = None
    detalle_contado: DescuentoAplicado | None = None
    detalle_diferencial: DescuentoAplicado | None = None
    detalle_volumen: DescuentoAplicado | None = None
    detalle_nc: DescuentoAplicado | None = None
    flags: dict[str, bool] = field(default_factory=dict)
    # Regla de Pronto Pago (CONTADO) que más contribuyó al contado_proy --
    # su "Ventana de pago" decide si el contado se confirma (ver
    # calcular_factura). None si ninguna regla CONTADO matcheó.
    regla_contado_dominante: DescuentoMarcaCategoria | None = None
    producto: Decimal = Decimal("0")
    detalle_producto: DescuentoAplicado | None = None


_ReglaT = TypeVar("_ReglaT")


def _filtrar_por_pago_previo(reglas: list[_ReglaT], tiene_pago: bool) -> list[_ReglaT]:
    """Tarea 1: excluye reglas con ``requiere_pago_previo=True`` cuando la
    orden/factura aún no tiene ningún abono vinculado (``inp.abonos`` vacío).

    Reglas sin el atributo (compatibilidad hacia atrás) se tratan como
    ``False`` -- no requieren pago previo.
    """
    if tiene_pago:
        return reglas
    return [r for r in reglas if not getattr(r, "requiere_pago_previo", False)]


# "Ventana de pago" (reemplaza "Días de gracia" -- pedido explícito del
# usuario, agosto 2026): en vez de un único número de días de gracia
# siempre relativo a un mismo punto fijo, la regla ahora elige DESDE
# CUÁNDO se cuentan esos días.
VENTANA_PAGO_TIPOS = ("entrega", "emision", "vencimiento", "no_aplica")


def limite_ventana_pago(
    tipo: str,
    dias: int,
    *,
    fecha_emision: date,
    fecha_entrega: date | None,
    dias_credito: int,
) -> date | None:
    """Fecha límite (inclusive) de una "Ventana de pago", o ``None`` si el

    tipo es "no_aplica"/vacío/desconocido (sin restricción por esta ventana).
    Ver ``ventana_pago_vigente`` para la semántica de cada tipo.
    """
    if not tipo or tipo == "no_aplica" or tipo not in VENTANA_PAGO_TIPOS:
        return None
    if tipo == "emision":
        return fecha_emision + timedelta(days=dias)
    if tipo == "entrega":
        base = fecha_entrega or fecha_emision
        return base + timedelta(days=dias)
    # "vencimiento"
    base = fecha_entrega or fecha_emision
    return base + timedelta(days=dias_credito) + timedelta(days=dias)


def ventana_pago_vigente(
    tipo: str,
    dias: int,
    fecha_evaluacion: date,
    *,
    fecha_emision: date,
    fecha_entrega: date | None,
    dias_credito: int,
) -> bool:
    """True si, a ``fecha_evaluacion``, el descuento sigue dentro de su

    "Ventana de pago" configurada:

    - ``"vencimiento"``: vigente mientras ``fecha_evaluacion`` no supere el
      vencimiento (emisión/entrega + ``dias_credito``) MÁS ``dias`` de
      margen -- ej. vencimiento + 3 días.
    - ``"emision"``: vigente hasta ``dias`` días después de la emisión de
      la orden, sin importar el vencimiento -- ej. solo 1 día tras emitida.
    - ``"entrega"``: igual que "emision" pero contado desde la fecha de
      entrega (si no hay entrega registrada, cae a la emisión).
    - ``"no_aplica"`` (o vacío/desconocido): sin restricción de esta
      ventana -- siempre vigente por este criterio.
    """
    limite = limite_ventana_pago(
        tipo,
        dias,
        fecha_emision=fecha_emision,
        fecha_entrega=fecha_entrega,
        dias_credito=dias_credito,
    )
    if limite is None:
        return True
    return fecha_evaluacion <= limite


# Nombres lógicos usados SOLO como fallback cuando EngineInputs no trae
# valid_usd/valid_ves poblados (tests que construyen EngineInputs a mano
# con un DictPriceResolver keyeado por estos strings). En producción real
# el runner SIEMPRE puebla valid_usd/valid_ves desde Configuración
# (valid_pricelists_usd/ves en _Meta) -- causa raíz confirmada del bug de
# orden 771: existían ``ENGINE_LISTA_USD``/``ENGINE_LISTA_BCV`` (env vars)
# como fuente PARALELA e independiente de "cuál es la lista USD/VES
# activa", desincronizada de Configuración -- apuntaban a la pricelist 4
# (inactiva, con los mismos precios que la lista VES id 5 por coincidencia
# histórica) en vez de la 8 (la lista USD real activa). Se eliminaron esas
# variables por completo -- Configuración (``valid_pricelists_usd/ves``)
# es ahora la ÚNICA fuente de verdad de qué pricelist es USD/VES.
_LISTA_USD_FALLBACK = "USD"
_LISTA_VES_FALLBACK = "BCV"

# Lista USD vigente durante la ventana de la Lista Histórica de Auditoría
# (Odoo pricelist id 7, "Pago USD Marzo" -- archivada hoy, superada por la
# 8, pero con reglas de precio fijo reales para ese período). Aclaratoria
# del usuario (agosto 2026, auditoría S00020): la Lista Histórica de
# Auditoría SOLO sustituye la lista VES en ese período/esas órdenes -- el
# teórico USD (y el precio real si la orden termina pagándose por la ruta
# USD) debe seguir usando la lista USD que correspondía entonces, NO el
# mismo precio de la lista histórica. Bug real corregido: antes
# ``_precio_unitario_linea`` devolvía el precio histórico para AMBAS listas
# (VES y USD) sin distinguir, y ``_lista_usd_activa`` devolvía la lista USD
# de HOY (id 8) en vez de la vigente para esa fecha.
_LISTA_USD_HISTORICA = "7"


def _lista_usd_activa(inp: EngineInputs) -> str:
    """Id de pricelist USD vigente, según Configuración (``valid_usd``).

    Para órdenes de la ventana histórica (``orden_es_historica``), la
    lista USD vigente ENTONCES era la 7 ("Pago USD Marzo"), no la lista
    USD configurada hoy -- ver ``_LISTA_USD_HISTORICA``.
    """
    if inp.orden_es_historica:
        return _LISTA_USD_HISTORICA
    return inp.valid_usd[0] if inp.valid_usd else _LISTA_USD_FALLBACK


def _lista_ves_activa(inp: EngineInputs) -> str:
    """Id de pricelist VES vigente, según Configuración (``valid_ves``)."""
    return inp.valid_ves[0] if inp.valid_ves else _LISTA_VES_FALLBACK


def _determinar_lista(inp: EngineInputs, pura_bcv: bool) -> str:
    """Paso 1 (sección 4.2): la lista la define el método de pago.

    Gana sobre la lista especial de nacimiento. Sin abonos aún, se usa la lista
    de nacimiento como techo provisional.
    """
    if not inp.abonos:
        return inp.orden.lista_precios
    return _lista_ves_activa(inp) if pura_bcv else _lista_usd_activa(inp)


def _cantidad_efectiva(inp: EngineInputs, linea: LineaOrden) -> Decimal:
    """Cantidad a facturar por línea (sección 4.6 — devoluciones).

    Si la orden está entregada completa y tiene devolución, se usa la cantidad
    realmente entregada (``qty_delivered``, neta de la devolución). Eso resuelve
    la opción B (pedida − devuelta) y, a la vez, evita el doble descuento cuando
    la SO ya fue modificada para ajustar las cantidades: en ese caso
    ``cantidad_entregada`` ya coincide con la cantidad ajustada. En cualquier otro
    caso se usa la cantidad pedida (base provisional; Lubrikca factura antes de
    despachar, donde ``qty_delivered`` aún puede ser 0).
    """
    if inp.orden.entregada_completa and inp.orden.tiene_devolucion:
        return linea.cantidad_entregada
    return linea.cantidad


def _precio_unitario_linea(inp: EngineInputs, linea: LineaOrden, lista: str) -> Decimal:
    """Precio unitario de la línea -- Lista Histórica de Auditoría si la

    orden cae en esa excepción (Tarea 2) Y la lista solicitada es la VES
    (la Lista Histórica solo sustituye esa, no la USD -- ver
    ``_LISTA_USD_HISTORICA``); si no, la pricelist normal (o la 7 para el
    lado USD de una orden histórica, resuelta por ``_lista_usd_activa``).

    Bug real corregido (auditoría agosto 2026): esta rama VES leía
    ``hist_info["usd"]`` (columna ``precio_usd`` de
    ``listas_precios_historicas``) -- pero esa columna resultó ser el MISMO
    precio que ya tenía la línea real (la orden ya nació bajo la lista USD
    vigente entonces), no el precio VES/BCV-Euro histórico. La columna que
    SÍ representa ese precio es ``precio_bcv_euro`` (~25% más alta que la
    USD en los datos reales, consistente con la brecha BCV-Euro conocida de
    ese período) y nunca se leía -- por eso teórico VES y teórico USD
    salían casi idénticos para las órdenes de la ventana histórica."""
    if (
        inp.orden_es_historica
        and inp.historical_price_map
        and str(lista) != _LISTA_USD_HISTORICA
    ):
        code_key = str(linea.producto).strip()
        if code_key.isdigit():
            code_key = str(int(code_key))
        hist_info = inp.historical_price_map.get(code_key)
        if hist_info is not None:
            precio_ves = hist_info["eur"]
            assert isinstance(precio_ves, Decimal)
            if precio_ves > Decimal("0"):
                return precio_ves
    return inp.price_resolver.precio(linea.producto, lista, fecha=inp.orden.fecha)


def _precio_linea(inp: EngineInputs, linea: LineaOrden, lista: str) -> Decimal:
    return _precio_unitario_linea(inp, linea, lista) * _cantidad_efectiva(inp, linea)


def lineas_con_precio(inp: EngineInputs, lista: str) -> list[dict[str, Any]]:
    """Desglose por línea del precio teórico resuelto para ``lista`` (Fase 5,

    modal de detalle de orden en ``/api/ventas/{so_id}/detalle``). A
    diferencia de ``_calcular_componentes`` (que solo agrega totales), esto
    devuelve una fila por línea con su precio unitario, cantidad efectiva,
    subtotal y litros (unitario y total) para ESA lista específica -- reusa
    ``_precio_unitario_linea`` (misma resolución de precio, incluida la
    excepción de Lista Histórica de Auditoría) y la misma resolución de
    volumen que usa el motor para las reglas de Descuento por Volumen
    (``_calcular_componentes``, sección litros_por_mc) sin duplicar lógica.
    """
    filas: list[dict[str, Any]] = []
    for ln in inp.lineas:
        precio_unit = _precio_unitario_linea(inp, ln, lista)
        cantidad = _cantidad_efectiva(inp, ln)
        try:
            vol_unit = inp.price_resolver.volumen(ln.producto)
        except Exception:
            vol_unit = Decimal("0.0")
        filas.append(
            {
                "producto": ln.producto,
                "marca": ln.resolved_marca,
                "categoria": ln.categoria,
                "cantidad": cantidad,
                "precio_unitario": precio_unit,
                "subtotal": precio_unit * cantidad,
                "litros_unitario": vol_unit,
                "litros_total": vol_unit * cantidad,
            }
        )
    return filas


def _evaluar_promociones_producto(
    promos_activas: list[PromocionPrimeraCompra],
    inp: EngineInputs,
    lista: str,
    *,
    fallback_industrial: bool,
) -> tuple[Decimal, DescuentoAplicado | None]:
    """Obsequio de producto (conjunto/solo_uno) o, si ninguno matchea,

    descuento porcentual general -- misma lógica para "Primera Compra" y
    para promociones "Recurrente" (``solo_primera_compra=False``), ver
    ``_calcular_componentes``. ``fallback_industrial`` controla si, cuando
    NO hay ninguna promo configurada, se aplica el 2% por defecto solo a
    líneas Industrial (comportamiento histórico de "primera compra sin
    promos") -- las promos "recurrente" fuera de la primera compra NO
    deben inventar ese 2% de la nada, solo actúan si hay una regla
    explícitamente configurada.
    """
    nc = Decimal("0")
    detalle_nc: DescuentoAplicado | None = None

    prod_promos_califican = []
    for p in promos_activas:
        if p.tipo_beneficio == "producto":
            cats = [c.strip() for c in p.categorias_aplica.split(",") if c.strip()]
            units = sum(
                _cantidad_efectiva(inp, ln)
                for ln in inp.lineas
                if p.categorias_aplica == "*"
                or ln.categoria in cats
                or ln.categoria_madre in cats
                or ln.presentacion in cats
            )
            if units >= p.compra_minima:
                prod_promos_califican.append(p)

    if prod_promos_califican:
        best_promo = max(prod_promos_califican, key=lambda p: p.compra_minima)
        lista_prod = [x.strip() for x in best_promo.productos.split(",") if x.strip()]
        if best_promo.regalo_tipo == "conjunto":
            nc_acum = Decimal("0")
            for prod in lista_prod:
                matching_lines = [ln for ln in inp.lineas if ln.producto == prod]
                if matching_lines:
                    for ln in matching_lines:
                        if ln.descuento < Decimal("99.9"):
                            nc_acum += min(ln.cantidad, best_promo.valor) * ln.precio_unitario
            nc = nc_acum
            if nc > 0:
                detalle_nc = DescuentoAplicado(
                    origen="primera_compra",
                    descripcion=f"NC obsequio conjunto ({', '.join(lista_prod)})",
                    monto=q2(nc),
                )
        else:  # "solo_uno"
            gifted_in_lines = any(
                ln.descuento >= Decimal("99.9") for ln in inp.lineas if ln.producto in lista_prod
            )
            if not gifted_in_lines:
                matching_lines = [
                    ln
                    for ln in inp.lineas
                    if ln.producto in lista_prod and ln.descuento < Decimal("99.9")
                ]
                if matching_lines:
                    best_line = max(
                        matching_lines,
                        key=lambda ln: min(ln.cantidad, best_promo.valor) * ln.precio_unitario,
                    )
                    nc = min(best_line.cantidad, best_promo.valor) * best_line.precio_unitario
                    detalle_nc = DescuentoAplicado(
                        origen="primera_compra",
                        descripcion=f"NC obsequio ({best_line.producto})",
                        monto=q2(nc),
                    )
    else:
        pct_general = Decimal("0.0")
        if promos_activas:
            pcts = []
            for p in promos_activas:
                if p.tipo_beneficio == "porcentaje":
                    pcts.append(p.valor)
                else:
                    pcts.append(p.descuento_fallback)
            pct_general = max(pcts) if pcts else Decimal("0.02")
            if pct_general == 0:
                pct_general = Decimal("0.02")
        elif fallback_industrial:
            pct_general = Decimal("0.02")

        if promos_activas:
            nc = sum(_precio_linea(inp, ln, lista) for ln in inp.lineas) * pct_general
            if nc > 0:
                detalle_nc = DescuentoAplicado(
                    origen="primera_compra",
                    descripcion=f"Descuento primera compra {pct_general * 100:.2f}%",
                    monto=q2(nc),
                )
        elif fallback_industrial:
            nc = (
                sum(
                    _precio_linea(inp, ln, lista)
                    for ln in inp.lineas
                    if (ln.categoria or "").upper() == "INDUSTRIAL"
                )
                * pct_general
            )
            if nc > 0:
                detalle_nc = DescuentoAplicado(
                    origen="primera_compra",
                    descripcion=f"Descuento primera compra Industrial {pct_general * 100:.2f}%",
                    monto=q2(nc),
                )

    return nc, detalle_nc


def _calcular_componentes(inp: EngineInputs, lista: str, pura_bcv: bool) -> _Componentes:
    fecha_orden = inp.orden.fecha
    precio_base = sum((_precio_linea(inp, ln, lista) for ln in inp.lineas), Decimal("0"))

    # Tarea 1: reglas con requiere_pago_previo=True quedan excluidas si la
    # orden/factura no tiene ningún abono vinculado todavía.
    tiene_pago = bool(inp.abonos)
    descuentos_ok = _filtrar_por_pago_previo(inp.descuentos, tiene_pago)
    descuentos_volumen_ok = _filtrar_por_pago_previo(inp.descuentos_volumen, tiene_pago)
    descuentos_recompra_ok = _filtrar_por_pago_previo(inp.descuentos_recompra, tiene_pago)
    promociones_ok = _filtrar_por_pago_previo(inp.promociones_primera_compra, tiene_pago)
    reglas_recurrencia_ok = _filtrar_por_pago_previo(inp.reglas_recurrencia, tiene_pago)
    descuentos_producto_ok = _filtrar_por_pago_previo(inp.descuentos_producto, tiene_pago)
    # NOTA (aplica_a línea/subtotal): PromocionPrimeraCompra y
    # DescuentoDiferencialCambiario también tienen el campo `aplica_a` en
    # esquema (por consistencia), pero NO lo leen aquí -- no son cálculos por
    # línea hoy (primera compra ya opera sobre "todas las líneas"/"solo
    # Industrial" según otra lógica; diferencial cambiario se calcula por
    # abono, no por línea). Es una limitación real de estos 2 tipos de
    # descuento, no un olvido.

    # (a) Recurrencia — vigente a la fecha de la orden (sección 4.3a)
    pct_recompra = Decimal("0")
    nc = Decimal("0")
    promo_sin_precio = False
    detalle_recompra: DescuentoAplicado | None = None
    detalle_nc: DescuentoAplicado | None = None
    if inp.orden.es_primera_compra:
        promos_activas = [
            p
            for p in promociones_ok
            if _vigente(p.vigencia_desde, p.vigencia_hasta, p.activo, fecha_orden)
            and _match_lista(
                getattr(p, "listas_aplicables", "*"),
                lista,
                inp.valid_ves or None,
                inp.valid_usd or None,
            )
        ]
        nc, detalle_nc = _evaluar_promociones_producto(
            promos_activas, inp, lista, fallback_industrial=True
        )
    else:
        recompras_activas = [
            r
            for r in descuentos_recompra_ok
            if _vigente(r.vigencia_desde, r.vigencia_hasta, r.activo, fecha_orden)
            and _match_lista(
                getattr(r, "listas_aplicables", "*"),
                lista,
                inp.valid_ves or None,
                inp.valid_usd or None,
            )
        ]
        if not recompras_activas and reglas_recurrencia_ok:
            regla = regla_recurrencia_vigente(
                reglas_recurrencia_ok, condicion=Condicion.RECOMPRA, fecha=fecha_orden
            )
            if regla is not None and regla.tipo_beneficio == TipoBeneficio.PORCENTAJE:
                recompras_activas = [
                    DescuentoRecompra(
                        regla_id="REC_LEGACY",
                        marca="*",
                        categoria="*",
                        min_cajas=1,
                        max_cajas=9999,
                        porcentaje=regla.valor,
                        vigencia_desde=regla.vigencia_desde,
                        vigencia_hasta=regla.vigencia_hasta,
                        activo=regla.activo,
                    )
                ]

        if recompras_activas:
            # Ventana de recompra (reemplaza el criterio "primera orden del
            # mes"): aplica si la orden INMEDIATAMENTE anterior del cliente
            # está totalmente pagada, y esta orden llega dentro de (días de
            # crédito reales de esa orden anterior + dias_gracia de la
            # regla). ``orden_anterior_cliente``/``_vincs`` los calcula
            # ``EngineRunner.build_inputs`` (necesita repo para las
            # vinculaciones de OTRA orden). Sin orden anterior (primer
            # pedido del cliente) no hay recompra posible.
            orden_anterior = inp.orden_anterior_cliente
            pagada_completo = False
            if orden_anterior is not None:
                for v in inp.orden_anterior_cliente_vincs:
                    congelar_en_vinculacion(v)
                pagado_anterior = (
                    valor_pagado_usd(inp.orden_anterior_cliente_vincs)
                    if inp.orden_anterior_cliente_vincs
                    else Decimal("0")
                )
                pagada_completo = pagado_anterior >= orden_anterior.monto_total - _EPS

            if orden_anterior is not None and pagada_completo:
                recompra_monto = Decimal("0")
                # Reglas en modo "subtotal" se deduplican por regla_id: el %
                # se aplica UNA sola vez sobre precio_base, sin importar
                # cuántas líneas matcheen esa misma regla.
                reglas_recompra_subtotal: dict[str, Any] = {}
                for ln in inp.lineas:
                    cajas_linea = _cantidad_efectiva(inp, ln)
                    best_r = None
                    for r in recompras_activas:
                        marca_ok = (
                            r.marca == "*"
                            or r.marca.upper() in ln.resolved_marca.upper()
                            or ln.resolved_marca.upper() in r.marca.upper()
                        )
                        cat_ok = (
                            _match_categoria(r.categoria, ln.categoria)
                            or _match_categoria(r.categoria, ln.presentacion)
                            or _match_categoria(r.categoria, ln.categoria_madre)
                            or _match_categoria(r.categoria, getattr(ln, "subcategoria", ""))
                        )
                        ventana_ok = ventana_pago_vigente(
                            getattr(r, "ventana_pago_tipo", "vencimiento"),
                            getattr(r, "ventana_pago_dias", 3),
                            fecha_orden,
                            fecha_emision=orden_anterior.fecha,
                            fecha_entrega=None,
                            dias_credito=orden_anterior.dias_credito,
                        )
                        if (
                            marca_ok
                            and cat_ok
                            and ventana_ok
                            and r.min_cajas <= cajas_linea <= r.max_cajas
                            and (best_r is None or r.porcentaje > best_r.porcentaje)
                        ):
                            best_r = r
                    if best_r is not None:
                        if getattr(best_r, "aplica_a", "linea") == "subtotal":
                            existente = reglas_recompra_subtotal.get(best_r.regla_id)
                            if existente is None or best_r.porcentaje > existente.porcentaje:
                                reglas_recompra_subtotal[best_r.regla_id] = best_r
                        else:
                            recompra_monto += _precio_linea(inp, ln, lista) * best_r.porcentaje

                for regla_subtotal in reglas_recompra_subtotal.values():
                    recompra_monto += precio_base * regla_subtotal.porcentaje

                if recompra_monto > 0:
                    pct_recompra = recompra_monto
                    detalle_recompra = DescuentoAplicado(
                        origen="recurrencia",
                        descripcion="Recompra recurrencia",
                        monto=q2(recompra_monto),
                    )

        # Promociones "Recurrente" (solo_primera_compra=False, ej. 12+1)
        # aplican en CUALQUIER orden que cumpla la condición, no solo la
        # primera. Bug real corregido (auditoría agosto 2026): esta
        # evaluación vivía SOLO dentro del bloque "es_primera_compra" de
        # arriba (el chequeo `not solo_primera_compra or es_primera_compra`
        # era un no-op ahí, porque es_primera_compra ya era True) -- una
        # regla marcada "Recurrente" (ej. PROMO_12_MAS_1) nunca disparaba
        # fuera de la primerísima orden del cliente, aunque su propio
        # nombre/documentación dice que debe aplicar en cada compra que
        # cumpla la condición. Sin fallback industrial-2% aquí a propósito
        # (ese es específico del incentivo de primera compra sin promos
        # configuradas, no debe inventarse en órdenes recurrentes).
        promos_recurrentes = [
            p
            for p in promociones_ok
            if not getattr(p, "solo_primera_compra", False)
            and _vigente(p.vigencia_desde, p.vigencia_hasta, p.activo, fecha_orden)
            and _match_lista(
                getattr(p, "listas_aplicables", "*"),
                lista,
                inp.valid_ves or None,
                inp.valid_usd or None,
            )
        ]
        if promos_recurrentes:
            nc, detalle_nc = _evaluar_promociones_producto(
                promos_recurrentes, inp, lista, fallback_industrial=False
            )

    # (b) Contado por marca×categoría — proyección (sección 4.3b).
    # El método NO determina el contado: lo determina pagar el neto total dentro
    # del plazo (ventana de días hábiles desde la entrega completa). Solo se
    # requiere que haya abonos y un ancla de entrega.
    contado_evaluable = bool(inp.abonos) and inp.orden.fecha_entrega is not None
    contado_proy = Decimal("0")
    regla_contado_dominante: DescuentoMarcaCategoria | None = None
    if contado_evaluable:
        moneda_pago = "USD"
        if inp.abonos:
            monedas_usadas = {
                pago.moneda.value
                for _, pago in inp.abonos
                if hasattr(pago, "moneda") and pago.moneda
            }
            if "VES" in monedas_usadas:
                moneda_pago = "VES"

        reglas_contado_subtotal: dict[str, Any] = {}
        for ln in inp.lineas:
            d = descuento_vigente(
                descuentos_ok,
                marca=ln.resolved_marca,
                categoria=ln.categoria,
                tipo=TipoDescuento.CONTADO,
                fecha=fecha_orden,
                lista_precios=lista,
                producto=ln.producto,
                moneda_pago=moneda_pago,
                presentacion=ln.presentacion,
                subcategoria=getattr(ln, "subcategoria", ""),
                valid_ves=inp.valid_ves or None,
                valid_usd=inp.valid_usd or None,
            )
            if d is not None:
                if (
                    regla_contado_dominante is None
                    or d.porcentaje > regla_contado_dominante.porcentaje
                ):
                    regla_contado_dominante = d
                if getattr(d, "aplica_a", "linea") == "subtotal":
                    existente = reglas_contado_subtotal.get(d.regla_id)
                    if existente is None or d.porcentaje > existente.porcentaje:
                        reglas_contado_subtotal[d.regla_id] = d
                else:
                    contado_proy += _precio_linea(inp, ln, lista) * d.porcentaje

        for regla_subtotal in reglas_contado_subtotal.values():
            contado_proy += precio_base * regla_subtotal.porcentaje

    # (d) Descuento por Volumen (Litros o Unidades/Cajas) -- evaluado POR
    # REGLA, no agrupando primero por (marca, categoría raíz). Hallazgo de
    # auditoría (agosto 2026): el agrupado anterior por categoría raíz
    # significaba que una regla scoped a una subcategoría o presentación
    # específica (ej. "Elite", "1X6") NUNCA podía matchear ninguna línea,
    # porque el total de litros/cajas se calculaba antes de saber qué
    # regla se iba a evaluar. Ahora cada regla suma SOLO las líneas que
    # realmente le hacen match (marca + categoría/subcategoría/
    # presentación/categoría_madre, mismo criterio que Contado/Recompra).
    volumen_desc = Decimal("0.0")
    detalle_volumen: DescuentoAplicado | None = None
    detalles_vol = []

    def _match_marca_vol(regla_marca: str, marca_linea: str) -> bool:
        if not regla_marca or regla_marca == "*":
            return True
        if not marca_linea:
            return False
        return (
            regla_marca.upper() in marca_linea.upper()
            or marca_linea.upper() in regla_marca.upper()
        )

    def _match_categoria_vol(regla_categoria: str, ln: LineaOrden) -> bool:
        return (
            _match_categoria(regla_categoria, ln.categoria)
            or _match_categoria(regla_categoria, ln.presentacion)
            or _match_categoria(regla_categoria, ln.categoria_madre)
            or _match_categoria(regla_categoria, getattr(ln, "subcategoria", ""))
        )

    def _especificidad_vol(r: DescuentoVolumen) -> int:
        score = 0
        if r.marca != "*":
            score += 2
        if r.categoria != "*":
            score += 1
            # Bonus: una regla apuntando a una subcategoría/presentación
            # real (no solo la raíz Comercial/Industrial) es MÁS
            # específica -- gana sobre una regla que solo apunta a la raíz.
            if r.categoria.strip().upper() not in ("COMERCIAL", "INDUSTRIAL"):
                score += 1
        return score

    # Historial "acumulado" -- líneas crudas (sin agrupar) de otras
    # órdenes del cliente, cada una con la fecha de SU orden; cada regla
    # filtra por su propio marca/categoría (mismo criterio de arriba) y
    # por su propio dias_evaluacion más abajo.
    historial_lineas_crudas: list[tuple[date, LineaOrden]] = [
        (orden_hist.fecha, lh)
        for orden_hist, lineas_hist in inp.historial_cliente_lineas
        for lh in lineas_hist
    ]

    reglas_vol_vigentes = [
        r
        for r in descuentos_volumen_ok
        if _vigente(r.vigencia_desde, r.vigencia_hasta, r.activo, fecha_orden)
        and _match_lista(
            getattr(r, "listas_aplicables", "*"),
            lista,
            inp.valid_ves or None,
            inp.valid_usd or None,
        )
    ]

    candidatas_vol: list[dict[str, Any]] = []
    for r in reglas_vol_vigentes:
        matching_lines = [
            ln
            for ln in inp.lineas
            if _match_marca_vol(r.marca, ln.resolved_marca)
            and _match_categoria_vol(r.categoria, ln)
        ]
        if not matching_lines:
            continue

        total_litros = Decimal("0")
        total_cajas = Decimal("0")
        for ln in matching_lines:
            try:
                vol_unit = inp.price_resolver.volumen(ln.producto)
            except Exception:
                vol_unit = Decimal("0.0")
            qty = _cantidad_efectiva(inp, ln)
            total_litros += qty * vol_unit
            total_cajas += qty

        es_acumulado = str(getattr(r, "tipo_evaluacion", "orden") or "orden").lower() == "acumulado"
        litros_eval = total_litros
        cajas_eval = total_cajas
        if es_acumulado and historial_lineas_crudas:
            dias = int(getattr(r, "dias_evaluacion", 0) or 0)
            for fecha_h, lh in historial_lineas_crudas:
                if not (
                    _match_marca_vol(r.marca, lh.resolved_marca)
                    and _match_categoria_vol(r.categoria, lh)
                ):
                    continue
                if dias > 0 and (fecha_orden - fecha_h).days > dias:
                    continue
                try:
                    vol_unit_h = inp.price_resolver.volumen(lh.producto)
                except Exception:
                    vol_unit_h = Decimal("0.0")
                litros_eval += lh.cantidad * vol_unit_h
                cajas_eval += lh.cantidad

        unidad = str(r.unidad_medida or "").upper()
        is_liters_rule = (unidad == "LITROS") or (
            r.litros_minimo > 0 and (r.min_cantidad is None or r.min_cantidad == 0)
        )
        if is_liters_rule:
            if litros_eval < r.litros_minimo:
                continue
        else:
            val_eval = cajas_eval if cajas_eval > 0 else litros_eval
            thresh = r.min_cantidad if (r.min_cantidad and r.min_cantidad > 0) else r.litros_minimo
            if val_eval < thresh:
                continue
            if r.max_cantidad and r.max_cantidad < 999999 and val_eval > r.max_cantidad:
                continue

        if r.porcentaje <= 0:
            continue

        unidad_tag = "L" if unidad == "LITROS" else " Unid"
        min_tag = r.litros_minimo if unidad == "LITROS" else r.min_cantidad
        tag = f"{r.marca}/{r.categoria} (>{min_tag}{unidad_tag}): {r.porcentaje * 100}%"
        candidatas_vol.append(
            {
                "regla": r,
                "lineas": matching_lines,
                "especificidad": _especificidad_vol(r),
                "tag": tag,
            }
        )

    # Reglas "subtotal": una sola vez por regla_id (puede matchear varias
    # líneas vía comodines "*"), aplicada sobre precio_base COMPLETO.
    reglas_vol_subtotal: dict[str, tuple[Any, str]] = {}
    candidatas_linea = []
    for c in candidatas_vol:
        r = c["regla"]
        if getattr(r, "aplica_a", "linea") == "subtotal":
            existente = reglas_vol_subtotal.get(r.regla_id)
            if existente is None or r.porcentaje > existente[0].porcentaje:
                reglas_vol_subtotal[r.regla_id] = (r, c["tag"])
        else:
            candidatas_linea.append(c)

    for regla_subtotal, tag in reglas_vol_subtotal.values():
        volumen_desc += precio_base * regla_subtotal.porcentaje
        detalles_vol.append(tag)

    # Reglas "línea": la MÁS ESPECÍFICA gana las líneas que le hacen match;
    # una regla más general (ej. toda "Industrial") solo cobra sobre las
    # líneas que ninguna regla más específica ya reclamó -- evita
    # doble-conteo cuando una regla amplia y una scoped a subcategoría/
    # presentación matchean simultáneamente las mismas unidades.
    candidatas_linea.sort(key=lambda c: (c["especificidad"], c["regla"].porcentaje), reverse=True)
    lineas_reclamadas: set[str] = set()
    for c in candidatas_linea:
        lineas_libres = [ln for ln in c["lineas"] if ln.linea_id not in lineas_reclamadas]
        if not lineas_libres:
            continue
        subt_libre = sum((_precio_linea(inp, ln, lista) for ln in lineas_libres), Decimal("0"))
        if subt_libre <= 0:
            continue
        volumen_desc += subt_libre * c["regla"].porcentaje
        detalles_vol.append(c["tag"])
        lineas_reclamadas.update(ln.linea_id for ln in lineas_libres)

    if volumen_desc > 0:
        detalle_volumen = DescuentoAplicado(
            origen="volumen",
            descripcion="Dcto volumen " + ", ".join(detalles_vol),
            monto=q2(volumen_desc),
        )
        # Apply dynamic exclusions (e.g., volumen excludes recompra)
        exclusiones_activas = set()
        for ex in inp.exclusiones:
            if getattr(ex, "activo", True):
                ta = (ex.regla_tipo_a or "").lower().strip()
                tb = (ex.regla_tipo_b or "").lower().strip()
                exclusiones_activas.add((ta, tb))
                exclusiones_activas.add((tb, ta))
        if ("volumen", "recompra") in exclusiones_activas:
            pct_recompra = Decimal("0")
            detalle_recompra = None

    # (e) Descuento por Producto específico (SKU/código) -- independiente de
    # marca/categoría genérica, para promociones puntuales por producto.
    producto_desc = Decimal("0")
    detalle_producto: DescuentoAplicado | None = None
    if descuentos_producto_ok:
        moneda_pago_prod = "USD"
        if inp.abonos:
            monedas_usadas_prod = {
                pago.moneda.value
                for _, pago in inp.abonos
                if hasattr(pago, "moneda") and pago.moneda
            }
            if "VES" in monedas_usadas_prod:
                moneda_pago_prod = "VES"

        reglas_producto_subtotal: dict[str, Any] = {}
        detalles_prod = []
        for ln in inp.lineas:
            d_prod = descuento_producto_vigente(
                descuentos_producto_ok,
                marca=ln.resolved_marca,
                categoria=ln.categoria,
                producto=ln.producto,
                fecha=fecha_orden,
                lista_precios=lista,
                moneda_pago=moneda_pago_prod,
                valid_ves=inp.valid_ves or None,
                valid_usd=inp.valid_usd or None,
            )
            if d_prod is not None:
                if getattr(d_prod, "aplica_a", "linea") == "subtotal":
                    existente = reglas_producto_subtotal.get(d_prod.regla_id)
                    if existente is None or d_prod.porcentaje > existente.porcentaje:
                        reglas_producto_subtotal[d_prod.regla_id] = d_prod
                else:
                    producto_desc += _precio_linea(inp, ln, lista) * d_prod.porcentaje
                    detalles_prod.append(f"{ln.producto}: {d_prod.porcentaje * 100}%")

        for regla_subtotal in reglas_producto_subtotal.values():
            producto_desc += precio_base * regla_subtotal.porcentaje
            detalles_prod.append(f"{regla_subtotal.regla_id}: {regla_subtotal.porcentaje * 100}%")

        if producto_desc > 0:
            detalle_producto = DescuentoAplicado(
                origen="producto",
                descripcion="Dcto producto " + ", ".join(detalles_prod),
                monto=q2(producto_desc),
            )

    lista_usd_name = _lista_usd_activa(inp)
    try:
        precio_target_usd = sum(
            (_precio_linea(inp, ln, lista_usd_name) for ln in inp.lineas), Decimal("0")
        )
    except KeyError:
        precio_target_usd = precio_base

    # (c) Diferencial Cambiario (sección 4.3c) -- explicado por el usuario,
    # agosto 2026. Solo para órdenes NACIDAS en lista VES, y solo se suma al
    # teórico/lista VES (nunca al USD) -- por eso todo el bloque está detrás
    # de ``pura_bcv``, que en TODOS los call sites de ``_calcular_componentes``
    # es exactamente "estamos evaluando la lista VES de esta orden" (ver
    # ``_teoricos_por_lista``/``calcular_factura``: pura_bcv=True siempre
    # acompaña lista=lista_ves, nunca lista_usd).
    #
    # Reemplaza 3 mecanismos legacy (bcv_per_abono/nc_equiparar/bcv_cierre)
    # que no correspondían a ninguna de las 2 reglas reales de negocio:
    #   - Regla 1 (fijo): pago EXCLUSIVAMENTE en USD (moneda_abono=USD en
    #     TODOS los abonos) y orden pagada 100% según el teórico USD ->
    #     descuento fijo (``porcentaje_fijo`` de la regla vigente
    #     tipo_diferencial='fijo_35_ves_usd', ese mismo % es también el
    #     TOPE de la regla 2).
    #   - Regla 2 ("equiparar"): pago mixto (VES+USD) o solo VES valorado a
    #     tasa Binance, orden pagada 100% según el teórico USD, Y el
    #     cliente sin NINGÚN pago huérfano abierto en Odoo (ver
    #     ``EngineInputs.cliente_tiene_pagos_huerfanos``) -> NC variable =
    #     brecha entre el teórico VES y lo pagado a tasa BCV, topada por el
    #     % máximo de la regla 1.
    # Si ambas califican a la vez, se aplica la más favorable al cliente
    # (monto mayor) -- confirmado con el usuario.
    #
    # La "Regla 3" (candidatos a cierre de factura por diferencial del día)
    # NO es un descuento automático del motor -- es un reporte de
    # candidatos aparte (ver endpoint de candidatos de cierre) que gerencia
    # aprueba manualmente vía "Aprobar Descuento de Sistema".
    diferencial_cambiario = Decimal("0")
    detalle_diferencial: DescuentoAplicado | None = None

    listas_ves_validas = set(inp.valid_ves) if inp.valid_ves else {_lista_ves_activa(inp)}
    es_lista_ves_nativa = str(inp.orden.lista_precios) in listas_ves_validas

    if inp.abonos and pura_bcv and es_lista_ves_nativa:
        vincs = [v for v, _ in inp.abonos]

        diferenciales_ok = _filtrar_por_pago_previo(inp.descuentos_diferencial, tiene_pago)
        reglas_dif_vigentes = [
            r
            for r in diferenciales_ok
            if _vigente(r.vigencia_desde, r.vigencia_hasta, r.activo, fecha_orden)
            and _match_lista(
                getattr(r, "listas_aplicables", "*"),
                lista,
                inp.valid_ves or None,
                inp.valid_usd or None,
            )
        ]
        regla_max = next(
            (r for r in reglas_dif_vigentes if r.tipo_diferencial == "fijo_35_ves_usd"), None
        )
        regla_equiparar_activa = any(
            r.tipo_diferencial == "equiparar_binance" for r in reglas_dif_vigentes
        )

        if regla_max is not None:
            diferencial_maximo = regla_max.porcentaje_fijo
            todos_usd_puro = all(v.moneda_abono == Moneda.USD for v in vincs)

            # Regla 1: fijo, pago 100% USD, orden pagada según teórico USD.
            monto_fijo = Decimal("0")
            if todos_usd_puro:
                pagado_usd = valor_pagado_usd(vincs)
                if pagado_usd >= (precio_target_usd or Decimal("0")) - _EPS:
                    monto_fijo = precio_base * diferencial_maximo

            # Regla 2: "equiparar", pago mixto/Binance, sin huérfanos.
            monto_equiparar = Decimal("0")
            if (
                regla_equiparar_activa
                and not todos_usd_puro
                and not inp.cliente_tiene_pagos_huerfanos
            ):
                val_binance = valor_pagado_binance_usd(vincs)
                val_bcv = valor_pagado_bcv_usd(vincs)
                if (
                    val_binance >= (precio_target_usd or Decimal("0")) - _EPS
                    and precio_base > val_bcv
                ):
                    otros_desc_pre = nc + pct_recompra + contado_proy + volumen_desc
                    gap = max(Decimal("0"), precio_base - otros_desc_pre - val_bcv)
                    monto_equiparar = min(gap, precio_base * diferencial_maximo)

            diferencial_cambiario = max(monto_fijo, monto_equiparar)
            if diferencial_cambiario > 0:
                if monto_fijo >= monto_equiparar:
                    pct_str = f"{diferencial_maximo * 100:.1f}%"
                    desc_str = f"Diferencial Cambiario fijo ({pct_str}, pago 100% USD)"
                else:
                    monto_str = q2(diferencial_cambiario)
                    desc_str = f"Diferencial Cambiario - Equiparación (${monto_str})"
                detalle_diferencial = DescuentoAplicado(
                    origen="bcv_completo",
                    descripcion=desc_str,
                    monto=q2(diferencial_cambiario),
                )

    return _Componentes(
        precio_base=precio_base,
        pct_recompra=pct_recompra,
        contado_proy=contado_proy,
        diferencial_cambiario=diferencial_cambiario,
        volumen=volumen_desc,
        nc=nc,
        detalle_recompra=detalle_recompra,
        detalle_volumen=detalle_volumen,
        detalle_diferencial=detalle_diferencial,
        detalle_nc=detalle_nc,
        regla_contado_dominante=regla_contado_dominante,
        producto=producto_desc,
        detalle_producto=detalle_producto,
        flags={
            "contado_evaluable": contado_evaluable,
            "promo_sin_precio": promo_sin_precio,
        },
    )


def conceptos_descuento_teorico(
    inp: EngineInputs, lista: str, pura_bcv: bool
) -> list[dict[str, Any]]:
    """Desglose de conceptos que conforman ``descuentos_teorico_ves``/``_usd``

    (Fase 6, modal de detalle en ``/api/ventas/{so_id}/detalle``): qué
    reglas explican el descuento teórico de ESA lista específica. Mismos 3
    componentes que suma ``_teoricos_por_lista`` (recompra + contado +
    volumen) -- NO incluye BCV-completo/primera-compra, que no son
    específicos de una lista (se calculan por abono o de forma orden-wide,
    ver nota en ``_teoricos_por_lista``).
    """
    try:
        comp = _calcular_componentes(inp, lista, pura_bcv)
    except KeyError:
        return []
    conceptos: list[dict[str, Any]] = []
    if comp.detalle_recompra is not None and comp.pct_recompra > 0:
        conceptos.append(
            {"concepto": comp.detalle_recompra.descripcion, "monto": comp.detalle_recompra.monto}
        )
    if comp.contado_proy > 0:
        conceptos.append(
            {"concepto": "Contado por marca/categoría", "monto": q2(comp.contado_proy)}
        )
    if comp.detalle_volumen is not None and comp.volumen > 0:
        conceptos.append(
            {"concepto": comp.detalle_volumen.descripcion, "monto": comp.detalle_volumen.monto}
        )
    return conceptos


def _teoricos_por_lista(
    inp: EngineInputs, pura_bcv: bool, lista_ves_name: str, lista_usd_name: str
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Tarea 3a/3b/4a/4b: teóricos y descuentos correspondientes en cada

    lista vigente (VES y USD), reutilizando ``_calcular_componentes`` -- la
    misma función que calcula el neto real -- para no duplicar la lógica de
    descuentos fuera del motor. Devuelve
    ``(teorico_ves, teorico_usd, equivalente_usd, descuentos_ves, descuentos_usd)``.
    """
    try:
        comp_ves = _calcular_componentes(inp, lista_ves_name, pura_bcv=True)
    except KeyError:
        # Lista VES sin precio para algún producto en el resolver (ej. en
        # tests con catálogos parciales) -- no se puede derivar el teórico.
        comp_ves = _Componentes(
            precio_base=Decimal("0"),
            pct_recompra=Decimal("0"),
            contado_proy=Decimal("0"),
            diferencial_cambiario=Decimal("0"),
            volumen=Decimal("0"),
            nc=Decimal("0"),
        )
    try:
        comp_usd = _calcular_componentes(inp, lista_usd_name, pura_bcv=False)
    except KeyError:
        comp_usd = _Componentes(
            precio_base=Decimal("0"),
            pct_recompra=Decimal("0"),
            contado_proy=Decimal("0"),
            diferencial_cambiario=Decimal("0"),
            volumen=Decimal("0"),
            nc=Decimal("0"),
        )
    descuentos_ves = comp_ves.pct_recompra + comp_ves.contado_proy + comp_ves.volumen
    descuentos_usd = comp_usd.pct_recompra + comp_usd.contado_proy + comp_usd.volumen
    # 3a: "igual si nació en USD; teórico si nació en VES" -- en ambos casos
    # es el precio resuelto contra la lista USD vigente (mismo cálculo).
    equivalente_usd = comp_usd.precio_base
    return (
        q2(comp_ves.precio_base),
        q2(comp_usd.precio_base),
        q2(equivalente_usd),
        q2(descuentos_ves),
        q2(descuentos_usd),
    )


def calcular_teorico_orden_con_fallback(inp: EngineInputs) -> dict[str, Any]:
    """Teórico VES/USD de una orden + si usó precio de fallback (Fase 10,

    tabla ``ventas_teoricos`` -- punto de comparación FIJO fuera de
    BandejaFacturacion, ver docstring de esa tabla en ``db/schema.py``).

    Reusa ``_teoricos_por_lista`` (misma lógica que ``calcular_factura``,
    sin duplicarla) para los montos, y ``PriceResolver.fue_fallback`` --
    poblado por ``OdooPriceResolver.precio()`` durante ese mismo cálculo --
    para saber si ALGUNA línea de la orden no tenía precio fijo en la lista
    VES/USD específica (se resolvió con otra pricelist de respaldo o el
    precio de venta $ de la ficha). Esa señal (``usa_fallback_ves``/``_usd``)
    es la única razón para re-verificar un teórico ya guardado: si la lista
    se completa después con el precio faltante, el teórico cambiaría.
    """
    # Mismo freeze de equivalentes que calcular_factura -- _calcular_
    # componentes (via BCV-completo) exige v.equiv_usd_bcv ya congelado.
    for v, _ in inp.abonos:
        congelar_en_vinculacion(v)

    lista_ves = _lista_ves_activa(inp)
    lista_usd = _lista_usd_activa(inp)
    (
        teorico_ves,
        teorico_usd,
        _equivalente_usd,
        descuentos_ves,
        descuentos_usd,
    ) = _teoricos_por_lista(inp, pura_bcv=True, lista_ves_name=lista_ves, lista_usd_name=lista_usd)

    resolver = inp.price_resolver
    usa_fallback_ves = any(resolver.fue_fallback(ln.producto, lista_ves) for ln in inp.lineas)
    usa_fallback_usd = any(resolver.fue_fallback(ln.producto, lista_usd) for ln in inp.lineas)

    return {
        "teorico_ves": teorico_ves,
        "teorico_usd": teorico_usd,
        "descuentos_teorico_ves": descuentos_ves,
        "descuentos_teorico_usd": descuentos_usd,
        "lista_ves_id": lista_ves,
        "lista_usd_id": lista_usd,
        "usa_fallback_ves": usa_fallback_ves,
        "usa_fallback_usd": usa_fallback_usd,
    }


def calcular_factura(inp: EngineInputs) -> BandejaFacturacion:
    """Calcula la fila de BandejaFacturacion para una orden (cierre híbrido)."""
    cfg = inp.engine_config
    vincs = [v for v, _ in inp.abonos]
    for v in vincs:
        congelar_en_vinculacion(v)

    pura_bcv = es_ruta_bcv_pura(vincs)
    lista = _determinar_lista(inp, pura_bcv)
    comp = _calcular_componentes(inp, lista, pura_bcv)

    (
        teorico_ves,
        teorico_usd,
        equivalente_usd,
        descuentos_teorico_ves,
        descuentos_teorico_usd,
    ) = _teoricos_por_lista(inp, pura_bcv, _lista_ves_activa(inp), _lista_usd_activa(inp))

    contado_evaluable = comp.flags["contado_evaluable"]
    valor_pagado = valor_pagado_usd(vincs) if vincs else Decimal("0")

    # Ventana de contado (sección 4.6) sobre la fecha de entrega. Si la
    # regla de Pronto Pago que matcheó especifica su propia "Ventana de
    # pago" (ventana_pago_tipo != "no_aplica"), esa ventana por-regla
    # reemplaza la ventana global de días hábiles (cash_window_business_days)
    # -- así el campo configurado en la regla realmente decide si el
    # contado se confirma, en vez de quedar sin efecto.
    fin_ventana: date | None = None
    within_window = False
    if inp.orden.fecha_entrega is not None:
        regla_dominante = comp.regla_contado_dominante
        fin_ventana = None
        if regla_dominante is not None:
            fin_ventana = limite_ventana_pago(
                getattr(regla_dominante, "ventana_pago_tipo", "entrega"),
                getattr(regla_dominante, "ventana_pago_dias", 3),
                fecha_emision=inp.orden.fecha,
                fecha_entrega=inp.orden.fecha_entrega,
                dias_credito=inp.orden.dias_credito,
            )
        if fin_ventana is None:
            fin_ventana = fin_ventana_contado(
                inp.orden.fecha_entrega, cfg.cash_window_business_days, inp.feriados
            )
        fechas_abono = [v.hora_pago_confirmada.date() for v in vincs]
        if fechas_abono:
            within_window = max(fechas_abono) <= fin_ventana
    window_expired = fin_ventana is not None and inp.fecha_calculo > fin_ventana
    # Exclusiones en optimista
    val_opt = {
        "primera_compra": comp.nc,
        "recurrencia": comp.pct_recompra,
        "contado": comp.contado_proy,
        "volumen": comp.volumen,
        "bcv_completo": comp.diferencial_cambiario,
        "producto": comp.producto,
    }
    for exc in inp.exclusiones:
        if exc.activo:
            ta, tb = exc.regla_tipo_a, exc.regla_tipo_b
            if ta in val_opt and tb in val_opt:
                va, vb = val_opt[ta], val_opt[tb]
                if va > 0 and vb > 0:
                    if va >= vb:
                        val_opt[tb] = Decimal("0")
                    else:
                        val_opt[ta] = Decimal("0")

    descuentos_optimista = (
        val_opt["recurrencia"]
        + val_opt["contado"]
        + val_opt["bcv_completo"]
        + val_opt["volumen"]
        + val_opt["producto"]
    )
    neto_optimista = comp.precio_base - descuentos_optimista - val_opt["primera_compra"]
    liquidado_optimista = valor_pagado >= neto_optimista - _EPS

    # Decisión del contado condicional (sección 4.0b).
    contado_confirmado = False
    contado_denied = False
    if contado_evaluable:
        if liquidado_optimista and within_window:
            contado_confirmado = True
        elif (liquidado_optimista and not within_window) or (
            window_expired and not liquidado_optimista
        ):
            # Liquidó tarde, o venció la ventana sin liquidar → pasó a crédito.
            contado_denied = True
        # else: provisional dentro de ventana, sigue proyectando contado.
    contado_incluido = contado_evaluable and not contado_denied

    # Exclusiones en el cálculo final
    contado_aplicado_base = comp.contado_proy if contado_incluido else Decimal("0")
    valores = {
        "primera_compra": comp.nc,
        "recurrencia": comp.pct_recompra,
        "contado": contado_aplicado_base,
        "volumen": comp.volumen,
        "bcv_completo": comp.diferencial_cambiario,
        "producto": comp.producto,
    }
    for exc in inp.exclusiones:
        if exc.activo:
            ta, tb = exc.regla_tipo_a, exc.regla_tipo_b
            if ta in valores and tb in valores:
                va, vb = valores[ta], valores[tb]
                if va > 0 and vb > 0:
                    if va >= vb:
                        valores[tb] = Decimal("0")
                    else:
                        valores[ta] = Decimal("0")

    final_nc = valores["primera_compra"]
    final_recompra = valores["recurrencia"]
    final_contado = valores["contado"]
    final_volumen = valores["volumen"]
    final_diferencial = valores["bcv_completo"]
    final_producto = valores["producto"]

    # Apilamiento aditivo final (sección 4.1).
    detalle: list[DescuentoAplicado] = []
    if final_nc > 0 and comp.detalle_nc is not None:
        detalle.append(comp.detalle_nc)
    if final_recompra > 0 and comp.detalle_recompra is not None:
        detalle.append(comp.detalle_recompra)
    if final_contado > 0:
        detalle.append(
            DescuentoAplicado(
                origen="contado",
                descripcion=(
                    "contado por marca/categoría"
                    + (" (confirmado)" if contado_confirmado else " (proyectado)")
                ),
                monto=q2(final_contado),
            )
        )
    if final_diferencial > 0:
        if comp.detalle_diferencial is not None:
            detalle.append(comp.detalle_diferencial)
        else:
            detalle.append(
                DescuentoAplicado(
                    origen="bcv_completo",
                    descripcion="Diferencial Cambiario (por abono)",
                    monto=q2(final_diferencial),
                )
            )
    if final_volumen > 0 and comp.detalle_volumen is not None:
        detalle.append(comp.detalle_volumen)
    if final_producto > 0 and comp.detalle_producto is not None:
        detalle.append(comp.detalle_producto)

    # HALLAZGO (revisión de apilamiento, no corregido sin confirmar con
    # negocio primero): no hay piso/tope explícito aquí -- si varias reglas
    # en modo "subtotal" (o incluso "línea") se apilan sin una exclusión
    # configurada entre ellas, total_descuentos podría en teoría superar
    # precio_base y dejar `neto` negativo. Este riesgo ya existía antes de
    # aplica_a="subtotal" (con suficientes reglas "línea" apiladas también
    # se llega ahí); aplica_a solo lo hace más fácil de alcanzar porque cada
    # regla subtotal pesa sobre precio_base completo en vez de un subgrupo.
    # No se agrega un límite nuevo sin instrucción explícita porque
    # cambiaría montos ya validados en producción.
    total_descuentos = (
        final_recompra + final_contado + final_diferencial + final_volumen + final_producto
    )
    neto = comp.precio_base - total_descuentos - final_nc
    candidata = bool(vincs) and valor_pagado >= neto - _EPS

    requiere_revision = (
        any(v.es_tasa_heredada for v in vincs)
        or comp.diferencial_cambiario > 0
        or contado_denied
        or comp.flags["promo_sin_precio"]
        or inp.orden.tiene_devolucion  # devoluciones se revisan a mano
    )

    return BandejaFacturacion(
        so_id=inp.orden.so_id,
        lista_aplicada=lista,
        precio_base_calculado=q2(comp.precio_base),
        descuentos_detalle=detalle,
        total_descuentos=q2(total_descuentos),
        ncs_calculadas=q2(comp.nc),
        total_motor=q2(neto),
        requiere_revision=requiere_revision,
        candidata_a_cierre=candidata,
        estado=EstadoBandeja.CALCULADO,
        equivalente_lista_usd=equivalente_usd,
        teorico_lista_ves=teorico_ves,
        teorico_lista_usd=teorico_usd,
        descuentos_teorico_ves=descuentos_teorico_ves,
        descuentos_teorico_usd=descuentos_teorico_usd,
    )
