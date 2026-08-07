"""Constructores de datos de prueba — mantienen los tests legibles."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cxc.models import (
    Cliente,
    Condicion,
    DescuentoBCVCompleto,
    DescuentoMarcaCategoria,
    DescuentoRecompra,
    DescuentoVolumen,
    Feriado,
    LineaOrden,
    MetodoPago,
    Moneda,
    OrdenVenta,
    Pago,
    PromocionPrimeraCompra,
    ReglaRecurrencia,
    TipoBeneficio,
    TipoDescuento,
    TipoFeriado,
    TipoTasa,
    Vinculacion,
)


def cliente(cliente_id: str = "C1", vendedor: str = "rep@lubrikca.com") -> Cliente:
    return Cliente(cliente_id=cliente_id, nombre="Cliente " + cliente_id, vendedor_email=vendedor)


def orden(
    so_id: str = "SO1",
    *,
    cliente_id: str = "C1",
    fecha: date = date(2026, 6, 1),
    fecha_entrega: date | None = date(2026, 6, 5),
    lista: str = "BCV",
    primera: bool = False,
    monto_total: str = "1000",
    vendedor: str = "rep@lubrikca.com",
    facturada: bool = False,
    estado_orden: str = "sale",
    dias_credito: int = 0,
) -> OrdenVenta:
    return OrdenVenta(
        so_id=so_id,
        cliente_id=cliente_id,
        fecha=fecha,
        fecha_entrega=fecha_entrega,
        monto_total=Decimal(monto_total),
        lista_precios=lista,
        vendedor_email=vendedor,
        es_primera_compra=primera,
        facturada=facturada,
        estado_orden=estado_orden,
        dias_credito=dias_credito,
    )


def linea(
    linea_id: str = "L1",
    *,
    so_id: str = "SO1",
    producto: str = "P1",
    marca: str = "Sinoco",
    categoria: str = "*",
    cantidad: str = "1",
    precio: str = "100",
    descuento: str = "0",
) -> LineaOrden:
    return LineaOrden(
        linea_id=linea_id,
        so_id=so_id,
        producto=producto,
        marca=marca,
        categoria=categoria,
        cantidad=Decimal(cantidad),
        precio_unitario=Decimal(precio),
        descuento=Decimal(descuento),
    )


def metodo(
    metodo_id: str = "M1",
    *,
    moneda: Moneda = Moneda.USD,
    tipo_tasa: TipoTasa = TipoTasa.N_A,
    es_contado: bool = True,
) -> MetodoPago:
    return MetodoPago(
        metodo_id=metodo_id,
        nombre="Metodo " + metodo_id,
        moneda=moneda,
        tipo_tasa=tipo_tasa,
        es_contado=es_contado,
    )


def pago(
    pago_id: str = "PG1",
    *,
    cliente_id: str = "C1",
    monto: str = "100",
    moneda: Moneda = Moneda.USD,
    metodo_id: str = "M1",
    fecha: datetime = datetime(2026, 6, 5, 10, 0),
    vendedor: str = "rep@lubrikca.com",
) -> Pago:
    return Pago(
        pago_id=pago_id,
        cliente_id=cliente_id,
        monto=Decimal(monto),
        moneda=moneda,
        metodo_pago=metodo_id,
        fecha_pago=fecha,
        vendedor_email=vendedor,
    )


def vinculacion(
    vinc_id: str = "V1",
    *,
    pago_id: str = "PG1",
    so_id: str = "SO1",
    monto_aplicado: str = "100",
    hora: datetime = datetime(2026, 6, 5, 10, 0),
    tasa_bcv: str = "36.0",
    tasa_binance: str = "40.0",
    es_heredada: bool = False,
    moneda_abono: Moneda = Moneda.USD,
    tipo_tasa_abono: TipoTasa = TipoTasa.N_A,
) -> Vinculacion:
    return Vinculacion(
        vinc_id=vinc_id,
        pago_id=pago_id,
        so_id=so_id,
        monto_aplicado=Decimal(monto_aplicado),
        hora_pago_confirmada=hora,
        tasa_bcv_aplicada=Decimal(tasa_bcv),
        tasa_binance_aplicada=Decimal(tasa_binance),
        es_tasa_heredada=es_heredada,
        moneda_abono=moneda_abono,
        tipo_tasa_abono=tipo_tasa_abono,
    )


def descuento(
    regla_id: str = "D1",
    *,
    marca: str = "Sinoco",
    categoria: str = "*",
    porcentaje: str = "0.03",
    desde: date = date(2026, 1, 1),
    hasta: date | None = None,
    requiere_pago_previo: bool = True,
) -> DescuentoMarcaCategoria:
    return DescuentoMarcaCategoria(
        regla_id=regla_id,
        marca=marca,
        categoria=categoria,
        tipo_descuento=TipoDescuento.CONTADO,
        porcentaje=Decimal(porcentaje),
        vigencia_desde=desde,
        vigencia_hasta=hasta,
        requiere_pago_previo=requiere_pago_previo,
    )


def descuento_volumen(
    regla_id: str = "DV1",
    *,
    marca: str = "Sinoco",
    categoria: str = "*",
    litros_minimo: str = "100",
    porcentaje: str = "0.05",
    desde: date = date(2026, 1, 1),
    hasta: date | None = None,
    requiere_pago_previo: bool = False,
) -> DescuentoVolumen:
    return DescuentoVolumen(
        regla_id=regla_id,
        marca=marca,
        categoria=categoria,
        litros_minimo=Decimal(litros_minimo),
        porcentaje=Decimal(porcentaje),
        vigencia_desde=desde,
        vigencia_hasta=hasta,
        requiere_pago_previo=requiere_pago_previo,
    )


def regla_bcv_completo(
    porcentaje: str = "0.15",
    desde: date = date(2026, 1, 1),
    hasta: date | None = None,
    requiere_pago_previo: bool = True,
) -> DescuentoBCVCompleto:
    return DescuentoBCVCompleto(
        vigencia_desde=desde,
        porcentaje=Decimal(porcentaje),
        vigencia_hasta=hasta,
        requiere_pago_previo=requiere_pago_previo,
    )


def promo_primera(
    producto: str = "LIGA",
    desde: date = date(2026, 1, 1),
    hasta: date | None = None,
    tipo_beneficio: str = "producto",
    valor: str = "1",
    compra_minima: str = "0",
    regalo_tipo: str = "solo_uno",
    descuento_fallback: str = "0",
    categorias_aplica: str = "Comercial",
) -> PromocionPrimeraCompra:
    return PromocionPrimeraCompra(
        regla_id="TEST_PROMO",
        tipo_beneficio=tipo_beneficio,
        productos=producto,
        valor=Decimal(valor),
        compra_minima=Decimal(compra_minima),
        regalo_tipo=regalo_tipo,
        vigencia_desde=desde,
        vigencia_hasta=hasta,
        descuento_fallback=Decimal(descuento_fallback),
        categorias_aplica=categorias_aplica,
    )


def regla_recompra(valor: str = "0.03", desde: date = date(2026, 1, 1)) -> ReglaRecurrencia:
    return ReglaRecurrencia(
        condicion=Condicion.RECOMPRA,
        tipo_beneficio=TipoBeneficio.PORCENTAJE,
        valor=Decimal(valor),
        vigencia_desde=desde,
    )


def descuento_recompra(
    regla_id: str = "REC1",
    *,
    marca: str = "*",
    categoria: str = "*",
    min_cajas: int = 1,
    max_cajas: int = 9999,
    porcentaje: str = "0.03",
    vigencia_desde: date = date(2026, 1, 1),
    vigencia_hasta: date | None = None,
    activo: bool = True,
    aplica_a: str = "linea",
    dias_gracia: int = 3,
    ventana_pago_tipo: str = "vencimiento",
) -> DescuentoRecompra:
    return DescuentoRecompra(
        regla_id=regla_id,
        marca=marca,
        categoria=categoria,
        min_cajas=min_cajas,
        max_cajas=max_cajas,
        porcentaje=Decimal(porcentaje),
        vigencia_desde=vigencia_desde,
        vigencia_hasta=vigencia_hasta,
        activo=activo,
        aplica_a=aplica_a,
        ventana_pago_tipo=ventana_pago_tipo,
        ventana_pago_dias=dias_gracia,
    )


def regla_primera_compra(valor: str = "50", desde: date = date(2026, 1, 1)) -> ReglaRecurrencia:
    return ReglaRecurrencia(
        condicion=Condicion.PRIMERA_COMPRA,
        tipo_beneficio=TipoBeneficio.NOTA_CREDITO,
        valor=Decimal(valor),
        vigencia_desde=desde,
    )


def feriado(fecha: date, descripcion: str = "Feriado") -> Feriado:
    return Feriado(fecha=fecha, descripcion=descripcion, tipo=TipoFeriado.NACIONAL)
