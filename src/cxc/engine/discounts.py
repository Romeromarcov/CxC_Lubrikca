"""Motor de descuentos — lógica central (secciones 4.0–4.7).

Disparador neto-objetivo (no nominal), apilamiento aditivo, reselección de lista
por método (gana sobre lista especial), contado condicional a ventana de días
hábiles, BCV-completo, regla de mezcla → Binance y cierre híbrido.

El motor es una función PURA: recibe dataclasses, devuelve una
``BandejaFacturacion``. No conoce Sheets ni Odoo (eso lo cablea el runner).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..config import EngineConfig
from ..decimal_utils import q2
from ..models import (
    BandejaFacturacion,
    Condicion,
    DescuentoAplicado,
    DescuentoBCVCompleto,
    DescuentoMarcaCategoria,
    EstadoBandeja,
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
    descuento_vigente,
    promocion_primera_compra_vigente,
    regla_recurrencia_vigente,
    tasa_bcv_completo_vigente,
)
from .equivalents import (
    congelar_en_vinculacion,
    es_ruta_bcv_pura,
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
    reglas_recurrencia: list[ReglaRecurrencia]
    descuento_bcv_diario: list[DescuentoBCVCompleto]
    promociones_primera_compra: list[PromocionPrimeraCompra]
    feriados_tabla: list[Feriado]
    price_resolver: PriceResolver
    engine_config: EngineConfig
    fecha_calculo: date

    @property
    def feriados(self) -> frozenset[date]:
        return frozenset(f.fecha for f in self.feriados_tabla)


@dataclass
class _Componentes:
    precio_base: Decimal
    pct_recompra: Decimal
    contado_proy: Decimal
    bcv_completo: Decimal
    nc: Decimal
    detalle_recompra: DescuentoAplicado | None = None
    detalle_contado: DescuentoAplicado | None = None
    detalle_bcv: DescuentoAplicado | None = None
    detalle_nc: DescuentoAplicado | None = None
    flags: dict[str, bool] = field(default_factory=dict)


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
        diferencial = _diferencial_binance(
            v.tasa_bcv_aplicada, v.tasa_binance_aplicada
        )
        tasa_gerencia = tasa_bcv_completo_vigente(
            reglas_bcv, fecha=v.hora_pago_confirmada.date()
        )
        if tasa_gerencia is None:
            rate = Decimal("0")
        else:
            rate = max(Decimal("0"), min(tasa_gerencia, diferencial))
        base = v.equiv_usd_bcv
        assert base is not None  # congelado antes
        total += base * rate
    return total


def _determinar_lista(inp: EngineInputs, pura_bcv: bool) -> str:
    """Paso 1 (sección 4.2): la lista la define el método de pago.

    Gana sobre la lista especial de nacimiento. Sin abonos aún, se usa la lista
    de nacimiento como techo provisional.
    """
    cfg = inp.engine_config
    if not inp.abonos:
        return inp.orden.lista_precios
    return cfg.lista_bcv if pura_bcv else cfg.lista_usd


def _precio_linea(inp: EngineInputs, linea: LineaOrden, lista: str) -> Decimal:
    return inp.price_resolver.precio(linea.producto, lista) * linea.cantidad


def _calcular_componentes(inp: EngineInputs, lista: str, pura_bcv: bool) -> _Componentes:
    fecha_orden = inp.orden.fecha
    precio_base = sum(
        (_precio_linea(inp, ln, lista) for ln in inp.lineas), Decimal("0")
    )

    # (a) Recurrencia — vigente a la fecha de la orden (sección 4.3a)
    pct_recompra = Decimal("0")
    nc = Decimal("0")
    promo_sin_precio = False
    detalle_recompra: DescuentoAplicado | None = None
    detalle_nc: DescuentoAplicado | None = None
    if inp.orden.es_primera_compra:
        # NC = precio del producto-promo en la lista de NACIMIENTO de la orden.
        promo = promocion_primera_compra_vigente(
            inp.promociones_primera_compra, fecha=fecha_orden
        )
        if promo is not None:
            try:
                nc = inp.price_resolver.precio(
                    promo.producto, inp.orden.lista_precios
                )
                detalle_nc = DescuentoAplicado(
                    origen="primera_compra",
                    descripcion=f"NC producto promo {promo.producto}",
                    monto=q2(nc),
                )
            except KeyError:
                # Sin precio del producto-promo → no inventar NC; marcar revisión.
                promo_sin_precio = True
    else:
        regla = regla_recurrencia_vigente(
            inp.reglas_recurrencia, condicion=Condicion.RECOMPRA, fecha=fecha_orden
        )
        if regla is not None and regla.tipo_beneficio == TipoBeneficio.PORCENTAJE:
            pct_recompra = precio_base * regla.valor
            detalle_recompra = DescuentoAplicado(
                origen="recurrencia",
                descripcion=f"recompra {regla.valor}",
                monto=q2(pct_recompra),
            )

    # (b) Contado por marca×categoría — proyección (sección 4.3b).
    # El método NO determina el contado: lo determina pagar el neto total dentro
    # del plazo (ventana de días hábiles desde la entrega completa). Solo se
    # requiere que haya abonos y un ancla de entrega.
    contado_evaluable = bool(inp.abonos) and inp.orden.fecha_entrega is not None
    contado_proy = Decimal("0")
    if contado_evaluable:
        for ln in inp.lineas:
            d = descuento_vigente(
                inp.descuentos,
                marca=ln.marca,
                categoria=ln.categoria,
                tipo=TipoDescuento.CONTADO,
                fecha=fecha_orden,
            )
            if d is not None:
                contado_proy += _precio_linea(inp, ln, lista) * d.porcentaje

    # (c) BCV-completo (sección 4.3c) — solo si ruta BCV pura
    bcv_completo = Decimal("0")
    if pura_bcv:
        vincs = [v for v, _ in inp.abonos]
        bcv_completo = _bcv_completo_monto(
            vincs, inp.descuento_bcv_diario, inp.engine_config.bcv_complete_formula
        )

    return _Componentes(
        precio_base=precio_base,
        pct_recompra=pct_recompra,
        contado_proy=contado_proy,
        bcv_completo=bcv_completo,
        nc=nc,
        detalle_recompra=detalle_recompra,
        detalle_nc=detalle_nc,
        flags={
            "contado_evaluable": contado_evaluable,
            "promo_sin_precio": promo_sin_precio,
        },
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

    # Neto OPTIMISTA (asume contado) para decidir si liquidó dentro de ventana.
    descuentos_optimista = comp.pct_recompra + comp.contado_proy + comp.bcv_completo
    neto_optimista = comp.precio_base - descuentos_optimista - comp.nc
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

    # Apilamiento aditivo final (sección 4.1).
    detalle: list[DescuentoAplicado] = []
    if comp.detalle_nc is not None:
        # La NC se muestra en el desglose para auditoría; NO entra a
        # total_descuentos (va en su propio término ncs_calculadas).
        detalle.append(comp.detalle_nc)
    if comp.detalle_recompra is not None:
        detalle.append(comp.detalle_recompra)
    contado_aplicado = comp.contado_proy if contado_incluido else Decimal("0")
    if contado_incluido and comp.contado_proy > 0:
        detalle.append(
            DescuentoAplicado(
                origen="contado",
                descripcion=(
                    "contado por marca/categoría"
                    + (" (confirmado)" if contado_confirmado else " (proyectado)")
                ),
                monto=q2(comp.contado_proy),
            )
        )
    if comp.bcv_completo > 0:
        detalle.append(
            DescuentoAplicado(
                origen="bcv_completo",
                descripcion="BCV-completo (diferencial por abono)",
                monto=q2(comp.bcv_completo),
            )
        )

    total_descuentos = comp.pct_recompra + contado_aplicado + comp.bcv_completo
    neto = comp.precio_base - total_descuentos - comp.nc
    candidata = bool(vincs) and valor_pagado >= neto - _EPS

    requiere_revision = (
        any(v.es_tasa_heredada for v in vincs)
        or comp.bcv_completo > 0
        or contado_denied
        or comp.flags["promo_sin_precio"]
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
    )
