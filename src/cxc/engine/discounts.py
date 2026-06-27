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
    DescuentoMarcaCategoria,
    EstadoBandeja,
    Feriado,
    LineaOrden,
    MetodoPago,
    OrdenVenta,
    ReglaRecurrencia,
    TipoBeneficio,
    TipoDescuento,
    Vinculacion,
)
from .business_days import fin_ventana_contado
from .effective_dating import descuento_vigente, regla_recurrencia_vigente
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
    vinculaciones: list[Vinculacion], formula: str
) -> Decimal:
    """Descuento BCV-completo, calculado POR ABONO (sección 4.3c).

    Cada abono usa la tasa Binance de su hora estampada. La base es el
    equivalente USD a BCV ya congelado del abono.
    """
    if formula != "differential_over_binance":
        raise ValueError(
            f"Fórmula BCV-completo desconocida: {formula!r}. "
            "Configurar BCV_COMPLETE_FORMULA con un valor soportado."
        )
    total = Decimal("0")
    for v in vinculaciones:
        rate = _diferencial_binance(v.tasa_bcv_aplicada, v.tasa_binance_aplicada)
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
    detalle_recompra: DescuentoAplicado | None = None
    detalle_nc: DescuentoAplicado | None = None
    condicion = (
        Condicion.PRIMERA_COMPRA
        if inp.orden.es_primera_compra
        else Condicion.RECOMPRA
    )
    regla = regla_recurrencia_vigente(
        inp.reglas_recurrencia, condicion=condicion, fecha=fecha_orden
    )
    if regla is not None:
        if regla.tipo_beneficio == TipoBeneficio.NOTA_CREDITO:
            nc = regla.valor
            detalle_nc = DescuentoAplicado(
                origen="recurrencia",
                descripcion=f"NC {condicion.value} (monto fijo)",
                monto=q2(nc),
            )
        else:  # porcentaje (recompra)
            pct_recompra = precio_base * regla.valor
            detalle_recompra = DescuentoAplicado(
                origen="recurrencia",
                descripcion=f"recompra {regla.valor}",
                monto=q2(pct_recompra),
            )

    # (b) Contado por marca×categoría — proyección (sección 4.3b)
    metodos = [m for _, m in inp.abonos]
    contado_metodo_ok = (
        bool(inp.abonos)
        and all(m.es_contado for m in metodos)
        and inp.orden.fecha_entrega is not None
    )
    contado_proy = Decimal("0")
    if contado_metodo_ok:
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
            vincs, inp.engine_config.bcv_complete_formula
        )

    return _Componentes(
        precio_base=precio_base,
        pct_recompra=pct_recompra,
        contado_proy=contado_proy,
        bcv_completo=bcv_completo,
        nc=nc,
        detalle_recompra=detalle_recompra,
        detalle_nc=detalle_nc,
        flags={"contado_metodo_ok": contado_metodo_ok},
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

    contado_metodo_ok = comp.flags["contado_metodo_ok"]
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
    if contado_metodo_ok:
        if liquidado_optimista and within_window:
            contado_confirmado = True
        elif (liquidado_optimista and not within_window) or (
            window_expired and not liquidado_optimista
        ):
            # Liquidó tarde, o venció la ventana sin liquidar → pasó a crédito.
            contado_denied = True
        # else: provisional dentro de ventana, sigue proyectando contado.
    contado_incluido = contado_metodo_ok and not contado_denied

    # Apilamiento aditivo final (sección 4.1).
    detalle: list[DescuentoAplicado] = []
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
