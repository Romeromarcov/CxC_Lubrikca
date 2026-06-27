"""Constructores de datos de prueba — mantienen los tests legibles."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cxc.models import (
    Cliente,
    Condicion,
    DescuentoBCVCompleto,
    DescuentoMarcaCategoria,
    Feriado,
    LineaOrden,
    MetodoPago,
    Moneda,
    OrdenVenta,
    Pago,
    ReglaRecurrencia,
    TipoBeneficio,
    TipoDescuento,
    TipoFeriado,
    TipoTasa,
    Vinculacion,
)


def cliente(cliente_id: str = "C1", vendedor: str = "rep@lubrikca.com") -> Cliente:
    return Cliente(cliente_id=cliente_id, nombre="Cliente " + cliente_id,
                   vendedor_email=vendedor)


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
) -> LineaOrden:
    return LineaOrden(
        linea_id=linea_id,
        so_id=so_id,
        producto=producto,
        marca=marca,
        categoria=categoria,
        cantidad=Decimal(cantidad),
        precio_unitario=Decimal(precio),
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
) -> DescuentoMarcaCategoria:
    return DescuentoMarcaCategoria(
        regla_id=regla_id,
        marca=marca,
        categoria=categoria,
        tipo_descuento=TipoDescuento.CONTADO,
        porcentaje=Decimal(porcentaje),
        vigencia_desde=desde,
        vigencia_hasta=hasta,
    )


def regla_bcv_completo(
    porcentaje: str = "0.15", desde: date = date(2026, 1, 1),
    hasta: date | None = None,
) -> DescuentoBCVCompleto:
    return DescuentoBCVCompleto(
        vigencia_desde=desde, porcentaje=Decimal(porcentaje),
        vigencia_hasta=hasta,
    )


def regla_recompra(
    valor: str = "0.03", desde: date = date(2026, 1, 1)
) -> ReglaRecurrencia:
    return ReglaRecurrencia(
        condicion=Condicion.RECOMPRA,
        tipo_beneficio=TipoBeneficio.PORCENTAJE,
        valor=Decimal(valor),
        vigencia_desde=desde,
    )


def regla_primera_compra(
    valor: str = "50", desde: date = date(2026, 1, 1)
) -> ReglaRecurrencia:
    return ReglaRecurrencia(
        condicion=Condicion.PRIMERA_COMPRA,
        tipo_beneficio=TipoBeneficio.NOTA_CREDITO,
        valor=Decimal(valor),
        vigencia_desde=desde,
    )


def feriado(fecha: date, descripcion: str = "Feriado") -> Feriado:
    return Feriado(fecha=fecha, descripcion=descripcion, tipo=TipoFeriado.NACIONAL)
