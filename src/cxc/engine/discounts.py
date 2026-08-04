"""Motor de descuentos — lógica central (secciones 4.0–4.7).

Disparador neto-objetivo (no nominal), apilamiento aditivo, reselección de lista
por método (gana sobre lista especial), contado condicional a ventana de días
hábiles, BCV-completo, regla de mezcla → Binance y cierre híbrido.

El motor es una función PURA: recibe dataclasses, devuelve una
``BandejaFacturacion``. No conoce Sheets ni Odoo (eso lo cablea el runner).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypeVar

from ..config import EngineConfig
from ..decimal_utils import q2
from ..models import (
    BandejaFacturacion,
    Condicion,
    DescuentoAplicado,
    DescuentoBCVCompleto,
    DescuentoDiferencialCambiario,
    DescuentoMarcaCategoria,
    DescuentoRecompra,
    DescuentoVolumen,
    EstadoBandeja,
    ExclusionRegla,
    Feriado,
    LineaOrden,
    MetodoPago,
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
    descuento_vigente,
    descuento_volumen_vigente,
    regla_recurrencia_vigente,
    tasa_bcv_completo_vigente,
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
    descuento_bcv_diario: list[DescuentoBCVCompleto]
    promociones_primera_compra: list[PromocionPrimeraCompra]
    feriados_tabla: list[Feriado]
    price_resolver: PriceResolver
    engine_config: EngineConfig
    fecha_calculo: date
    all_ordenes: list[OrdenVenta] = field(default_factory=list)
    exclusiones: list[ExclusionRegla] = field(default_factory=list)
    descuentos_recompra: list[DescuentoRecompra] = field(default_factory=list)
    descuentos_diferencial: list[DescuentoDiferencialCambiario] = field(default_factory=list)
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

    @property
    def feriados(self) -> frozenset[date]:
        return frozenset(f.fecha for f in self.feriados_tabla)


@dataclass
class _Componentes:
    precio_base: Decimal
    pct_recompra: Decimal
    contado_proy: Decimal
    bcv_completo: Decimal
    volumen: Decimal
    nc: Decimal
    detalle_recompra: DescuentoAplicado | None = None
    detalle_contado: DescuentoAplicado | None = None
    detalle_bcv: DescuentoAplicado | None = None
    detalle_volumen: DescuentoAplicado | None = None
    detalle_nc: DescuentoAplicado | None = None
    flags: dict[str, bool] = field(default_factory=dict)


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


def _diferencial_binance(tasa_bcv: Decimal, tasa_binance: Decimal) -> Decimal:
    """Default conservador del descuento BCV-completo: (binance − bcv)/binance."""
    return (tasa_binance - tasa_bcv) / tasa_binance


def _bcv_completo_monto(
    vinculaciones: list[Vinculacion],
    reglas_bcv: list[DescuentoBCVCompleto],
    formula: str,
) -> Decimal:
    """Descuento BCV-completo, calculado POR ABONO (sección 4.3c).

    La gerencia fija un porcentaje diario; el descuento aplicado por abono es
    ``min(porcentaje_gerencia, diferencial_real)`` y nunca menos de 0. Si no hay
    porcentaje configurado para la fecha del abono, no se otorga (conservador).
    La base es el equivalente USD a BCV ya congelado del abono.
    """
    if formula != "differential_over_binance":
        raise ValueError(
            f"Fórmula BCV-completo desconocida: {formula!r}. "
            "Configurar BCV_COMPLETE_FORMULA con un valor soportado."
        )
    total = Decimal("0")
    for v in vinculaciones:
        diferencial = _diferencial_binance(v.tasa_bcv_aplicada, v.tasa_binance_aplicada)
        tasa_gerencia = tasa_bcv_completo_vigente(reglas_bcv, fecha=v.hora_pago_confirmada.date())
        if tasa_gerencia is None:
            rate = Decimal("0")
        else:
            rate = max(Decimal("0"), min(tasa_gerencia, diferencial))
        base = v.equiv_usd_bcv
        assert base is not None  # congelado antes
        total += base * rate
    return total



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


def _lista_usd_activa(inp: EngineInputs) -> str:
    """Id de pricelist USD vigente, según Configuración (``valid_usd``)."""
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

    orden cae en esa excepción (Tarea 2), si no la pricelist normal."""
    if inp.orden_es_historica and inp.historical_price_map:
        code_key = str(linea.producto).strip()
        if code_key.isdigit():
            code_key = str(int(code_key))
        hist_info = inp.historical_price_map.get(code_key)
        if hist_info is not None:
            precio_usd = hist_info["usd"]
            assert isinstance(precio_usd, Decimal)
            if precio_usd > Decimal("0"):
                return precio_usd
    return inp.price_resolver.precio(linea.producto, lista, fecha=inp.orden.fecha)


def _precio_linea(inp: EngineInputs, linea: LineaOrden, lista: str) -> Decimal:
    return _precio_unitario_linea(inp, linea, lista) * _cantidad_efectiva(inp, linea)


def lineas_con_precio(inp: EngineInputs, lista: str) -> list[dict[str, Any]]:
    """Desglose por línea del precio teórico resuelto para ``lista`` (Fase 5,

    modal de detalle de orden en ``/api/ventas/{so_id}/detalle``). A
    diferencia de ``_calcular_componentes`` (que solo agrega totales), esto
    devuelve una fila por línea con su precio unitario, cantidad efectiva y
    subtotal para ESA lista específica -- reusa ``_precio_unitario_linea``
    (misma resolución de precio, incluida la excepción de Lista Histórica de
    Auditoría) sin duplicar la lógica.
    """
    filas: list[dict[str, Any]] = []
    for ln in inp.lineas:
        precio_unit = _precio_unitario_linea(inp, ln, lista)
        cantidad = _cantidad_efectiva(inp, ln)
        filas.append(
            {
                "producto": ln.producto,
                "marca": ln.resolved_marca,
                "categoria": ln.categoria,
                "cantidad": cantidad,
                "precio_unitario": precio_unit,
                "subtotal": precio_unit * cantidad,
            }
        )
    return filas


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
    descuento_bcv_diario_ok = _filtrar_por_pago_previo(inp.descuento_bcv_diario, tiene_pago)
    # NOTA (aplica_a línea/subtotal): PromocionPrimeraCompra, DescuentoBCVCompleto
    # y DescuentoDiferencialCambiario también tienen el campo `aplica_a` en
    # esquema (por consistencia), pero NO lo leen aquí -- no son cálculos por
    # línea hoy (primera compra ya opera sobre "todas las líneas"/"solo
    # Industrial" según otra lógica; BCV-completo/diferencial se calculan por
    # abono, no por línea). Es una limitación real de estos 3 tipos de
    # descuento, no un olvido.

    # (a) Recurrencia — vigente a la fecha de la orden (sección 4.3a)
    pct_recompra = Decimal("0")
    nc = Decimal("0")
    promo_sin_precio = False
    detalle_recompra: DescuentoAplicado | None = None
    detalle_nc: DescuentoAplicado | None = None
    if inp.orden.es_primera_compra:
        sum(
            _cantidad_efectiva(inp, ln)
            for ln in inp.lineas
            if ln.categoria_madre == "Comercial" or ln.presentacion == "CAJA"
        )
        promos_activas = [
            p
            for p in promociones_ok
            if _vigente(p.vigencia_desde, p.vigencia_hasta, p.activo, fecha_orden)
            and (not getattr(p, "solo_primera_compra", False) or inp.orden.es_primera_compra)
            and _match_lista(
                getattr(p, "listas_aplicables", "*"),
                lista,
                inp.valid_ves or None,
                inp.valid_usd or None,
            )
        ]

        # Calculate quantities for each promo based on its specific categorias_aplica
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
            # Gana la de mayor compra_minima
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
                    ln.descuento >= Decimal("99.9")
                    for ln in inp.lineas
                    if ln.producto in lista_prod
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
            # Fallback a porcentaje general / Industrial 2%
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
            else:
                pct_general = Decimal("0.02")

            if promos_activas:
                # Si hay promos configuradas, aplica el fallback de manera general a la orden
                nc = sum(_precio_linea(inp, ln, lista) for ln in inp.lineas) * pct_general
                if nc > 0:
                    detalle_nc = DescuentoAplicado(
                        origen="primera_compra",
                        descripcion=f"Descuento primera compra {pct_general * 100:.2f}%",
                        monto=q2(nc),
                    )
            else:
                # Si no hay promos, el 2% aplica únicamente a productos de la categoría Industrial
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
                        max_usos_mes=1,
                        dias_ventana=30,
                        vigencia_desde=regla.vigencia_desde,
                        vigencia_hasta=regla.vigencia_hasta,
                        activo=regla.activo,
                    )
                ]

        if recompras_activas:
            is_first_in_month = True
            current_year_month = (fecha_orden.year, fecha_orden.month)
            for o in inp.all_ordenes:
                if o.cliente_id == inp.orden.cliente_id and o.so_id != inp.orden.so_id:
                    o_ym = (o.fecha.year, o.fecha.month)
                    if o_ym == current_year_month and (
                        o.fecha < fecha_orden
                        or (o.fecha == fecha_orden and o.so_id < inp.orden.so_id)
                    ):
                        is_first_in_month = False
                        break

            if is_first_in_month:
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
                        )
                        if (
                            marca_ok
                            and cat_ok
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

                for best_r in reglas_recompra_subtotal.values():
                    recompra_monto += precio_base * best_r.porcentaje

                if recompra_monto > 0:
                    pct_recompra = recompra_monto
                    detalle_recompra = DescuentoAplicado(
                        origen="recurrencia",
                        descripcion="Recompra recurrencia",
                        monto=q2(recompra_monto),
                    )

    # (b) Contado por marca×categoría — proyección (sección 4.3b).
    # El método NO determina el contado: lo determina pagar el neto total dentro
    # del plazo (ventana de días hábiles desde la entrega completa). Solo se
    # requiere que haya abonos y un ancla de entrega.
    contado_evaluable = bool(inp.abonos) and inp.orden.fecha_entrega is not None
    contado_proy = Decimal("0")
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
                valid_ves=inp.valid_ves or None,
                valid_usd=inp.valid_usd or None,
            )
            if d is not None:
                if getattr(d, "aplica_a", "linea") == "subtotal":
                    existente = reglas_contado_subtotal.get(d.regla_id)
                    if existente is None or d.porcentaje > existente.porcentaje:
                        reglas_contado_subtotal[d.regla_id] = d
                else:
                    contado_proy += _precio_linea(inp, ln, lista) * d.porcentaje

        for d in reglas_contado_subtotal.values():
            contado_proy += precio_base * d.porcentaje

    # (d) Descuento por Volumen (Litros o Unidades/Cajas)
    litros_por_mc: dict[tuple[str, str], Decimal] = {}
    cajas_por_mc: dict[tuple[str, str], Decimal] = {}
    subtotal_por_mc: dict[tuple[str, str], Decimal] = {}
    for ln in inp.lineas:
        try:
            vol_unit = inp.price_resolver.volumen(ln.producto)
        except Exception:
            vol_unit = Decimal("0.0")
        qty = _cantidad_efectiva(inp, ln)
        k = (ln.resolved_marca, ln.categoria)
        litros_por_mc[k] = litros_por_mc.get(k, Decimal("0")) + (qty * vol_unit)
        cajas_por_mc[k] = cajas_por_mc.get(k, Decimal("0")) + qty
        subtotal_por_mc[k] = subtotal_por_mc.get(k, Decimal("0")) + _precio_linea(inp, ln, lista)

    volumen_desc = Decimal("0.0")
    detalle_volumen: DescuentoAplicado | None = None
    detalles_vol = []
    # Reglas en modo "subtotal" se deduplican por regla_id (una sola reglas
    # puede matchear varios grupos marca/categoria vía comodines "*"; el %
    # solo debe aplicarse UNA vez sobre precio_base, no una vez por grupo).
    reglas_vol_subtotal: dict[str, tuple[Any, str]] = {}

    for (marca, categoria), total_litros in litros_por_mc.items():
        total_cajas = cajas_por_mc.get((marca, categoria), Decimal("0"))
        regla_vol = descuento_volumen_vigente(
            descuentos_volumen_ok,
            marca=marca,
            categoria=categoria,
            litros=total_litros,
            cantidad_unidades=total_cajas,
            fecha=fecha_orden,
            lista_precios=lista,
            valid_ves=inp.valid_ves or None,
            valid_usd=inp.valid_usd or None,
        )
        if regla_vol is not None and regla_vol.porcentaje > 0:
            unidad_tag = "L" if str(regla_vol.unidad_medida).upper() == "LITROS" else " Unid"
            min_tag = (
                regla_vol.litros_minimo
                if str(regla_vol.unidad_medida).upper() == "LITROS"
                else regla_vol.min_cantidad
            )
            tag = f"{marca}/{categoria} (>{min_tag}{unidad_tag}): {regla_vol.porcentaje * 100}%"
            if getattr(regla_vol, "aplica_a", "linea") == "subtotal":
                existente = reglas_vol_subtotal.get(regla_vol.regla_id)
                if existente is None or regla_vol.porcentaje > existente[0].porcentaje:
                    reglas_vol_subtotal[regla_vol.regla_id] = (regla_vol, tag)
            else:
                subt = subtotal_por_mc[(marca, categoria)]
                volumen_desc += subt * regla_vol.porcentaje
                detalles_vol.append(tag)

    for regla_vol, tag in reglas_vol_subtotal.values():
        volumen_desc += precio_base * regla_vol.porcentaje
        detalles_vol.append(tag)

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

    lista_usd_name = _lista_usd_activa(inp)
    try:
        precio_target_usd = sum(
            (_precio_linea(inp, ln, lista_usd_name) for ln in inp.lineas), Decimal("0")
        )
    except KeyError:
        precio_target_usd = precio_base

    # (c) BCV-completo / Diferencial Cambiario / Equiparación Binance / USD Cash (sección 4.3c)
    bcv_completo = Decimal("0")
    detalle_bcv: DescuentoAplicado | None = None

    # Cualquier pricelist USD vigente (no solo la primaria/activa) cuenta --
    # una orden nacida en una lista USD histórica (ej. id 7, superada por la
    # 8 pero aún vigente para su fecha) sigue siendo "lista USD" a efectos
    # de esta regla.
    listas_usd_validas = set(inp.valid_usd) if inp.valid_usd else {lista_usd_name}
    es_lista_usd = str(inp.orden.lista_precios) in listas_usd_validas
    if inp.abonos and not es_lista_usd:
        vincs = [v for v, _ in inp.abonos]
        # 1. Per-abono fixed discount
        bcv_per_abono = Decimal("0")
        if pura_bcv and str(inp.orden.lista_precios) not in listas_usd_validas:
            bcv_per_abono = _bcv_completo_monto(
                vincs, descuento_bcv_diario_ok, inp.engine_config.bcv_complete_formula
            )

        # 2. Valuaciones duales: Abonos Binance vs Abonos BCV
        val_binance = valor_pagado_binance_usd(vincs)
        val_bcv = valor_pagado_bcv_usd(vincs)

        # 3. Equiparación Tasa Binance / USD Cash
        # Si los abonos en Binance / USD Cash cubren el 100% de la meta en Lista USD
        nc_equiparar = Decimal("0")
        if val_binance >= (precio_target_usd or Decimal("0")) - _EPS and precio_base > val_bcv:
            otros_desc_pre = nc + pct_recompra + contado_proy + volumen_desc
            nc_equiparar = max(Decimal("0"), precio_base - otros_desc_pre - val_bcv)

        # 4. Diferencial Brecha Cierre Variable (evaluado a la fecha del último pago)
        fechas_pago = [
            v.hora_pago_confirmada.date() for v, _ in inp.abonos if v.hora_pago_confirmada
        ]
        fecha_ultimo_pago = max(fechas_pago) if fechas_pago else fecha_orden

        ult_vinc = max(
            vincs, key=lambda v: v.hora_pago_confirmada if v.hora_pago_confirmada else datetime.min
        )
        tasa_bcv_ult = ult_vinc.tasa_bcv_aplicada
        tasa_binance_ult = ult_vinc.tasa_binance_aplicada
        brecha_pct = Decimal("0")
        if tasa_binance_ult > 0:
            brecha_pct = max(Decimal("0"), (tasa_binance_ult - tasa_bcv_ult) / tasa_binance_ult)

        otros_descuentos = nc + pct_recompra + contado_proy + volumen_desc
        unpaid_usd = precio_base - otros_descuentos - val_bcv
        max_brecha_usd = precio_base * brecha_pct

        bcv_cierre = Decimal("0")
        if brecha_pct > 0 and unpaid_usd > 0 and unpaid_usd <= max_brecha_usd + _EPS:
            # El cliente ha pagado >= 100% - brecha% de la orden
            bcv_cierre = min(unpaid_usd, max_brecha_usd)

        bcv_completo = max(bcv_per_abono, bcv_cierre, nc_equiparar)
        if bcv_completo > 0:
            if nc_equiparar >= bcv_cierre and nc_equiparar > bcv_per_abono:
                desc_str = f"Equiparación Binance / USD Cash (NC sugerida por ${q2(bcv_completo)})"
            else:
                brecha_fecha = fecha_ultimo_pago.isoformat()
                brecha_pct_str = f"{brecha_pct * 100:.1f}%"
                desc_str = f"Diferencial brecha cierre ({brecha_pct_str} brecha al {brecha_fecha})"
            detalle_bcv = DescuentoAplicado(
                origen="bcv_completo",
                descripcion=desc_str,
                monto=q2(bcv_completo),
            )

    return _Componentes(
        precio_base=precio_base,
        pct_recompra=pct_recompra,
        contado_proy=contado_proy,
        bcv_completo=bcv_completo,
        volumen=volumen_desc,
        nc=nc,
        detalle_recompra=detalle_recompra,
        detalle_volumen=detalle_volumen,
        detalle_bcv=detalle_bcv,
        detalle_nc=detalle_nc,
        flags={
            "contado_evaluable": contado_evaluable,
            "promo_sin_precio": promo_sin_precio,
        },
    )


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
            bcv_completo=Decimal("0"),
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
            bcv_completo=Decimal("0"),
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

    # Ventana de contado (sección 4.6) sobre la fecha de entrega.
    fin_ventana: date | None = None
    within_window = False
    if inp.orden.fecha_entrega is not None:
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
        "bcv_completo": comp.bcv_completo,
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
        val_opt["recurrencia"] + val_opt["contado"] + val_opt["bcv_completo"] + val_opt["volumen"]
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
        "bcv_completo": comp.bcv_completo,
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
    final_bcv = valores["bcv_completo"]

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
    if final_bcv > 0:
        if comp.detalle_bcv is not None:
            detalle.append(comp.detalle_bcv)
        else:
            detalle.append(
                DescuentoAplicado(
                    origen="bcv_completo",
                    descripcion="BCV-completo (diferencial por abono)",
                    monto=q2(final_bcv),
                )
            )
    if final_volumen > 0 and comp.detalle_volumen is not None:
        detalle.append(comp.detalle_volumen)

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
    total_descuentos = final_recompra + final_contado + final_bcv + final_volumen
    neto = comp.precio_base - total_descuentos - final_nc
    candidata = bool(vincs) and valor_pagado >= neto - _EPS

    requiere_revision = (
        any(v.es_tasa_heredada for v in vincs)
        or comp.bcv_completo > 0
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
