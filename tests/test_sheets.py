"""Tests de la capa de persistencia Sheets (serde round-trip + repositorio)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cxc.models import (
    BandejaFacturacion,
    Cliente,
    Conciliacion,
    DescuentoAplicado,
    EstadoVinculacion,
    Moneda,
    ResultadoConciliacion,
    SerieTasa,
    TipoTasa,
)
from cxc.sheets import serde
from cxc.sheets.gateway import InMemorySheetGateway
from cxc.sheets.repository import SheetsRepository

from . import builders as b


# --- Round-trip de serialización por tabla ----------------------------------
def test_roundtrip_orden_con_opcionales() -> None:
    o = b.orden("SO9", fecha_entrega=None, primera=True)
    o.factura_id = "INV1"
    o.monto_facturado = Decimal("123.45")
    assert serde.orden_from_row(serde.orden_to_row(o)) == o


def test_sheets_repository_fechas_historicas_map() -> None:
    gw = InMemorySheetGateway()
    gw.append_row("FechasHistoricas", {"so_id": "S00004", "fecha_historica": "2026-03-09"})
    gw.append_row("FechasHistoricas", {"so_id": "S00007", "fecha_historica": "2026-02-26"})
    repo = SheetsRepository(gw)
    m = repo.fechas_historicas_map()
    assert m["4"] == "2026-03-09"
    assert m["S00004"] == "2026-03-09"
    assert m["7"] == "2026-02-26"


def test_roundtrip_vinculacion_con_equivalentes() -> None:
    v = b.vinculacion(moneda_abono=Moneda.VES, tipo_tasa_abono=TipoTasa.BCV)
    v.equiv_usd_bcv = Decimal("2.777778")
    v.equiv_usd_binance = Decimal("2.500000")
    v.estado = EstadoVinculacion.APROBADO
    v.timestamp_registro = datetime(2026, 6, 5, 10, 30)
    assert serde.vinculacion_from_row(serde.vinculacion_to_row(v)) == v


def test_roundtrip_serie_tasa() -> None:
    s = SerieTasa(
        timestamp=datetime(2026, 6, 27, 10, 0),
        tasa_bcv=Decimal("36.5"),
        tasa_binance=Decimal("40.0"),
        fuente="binance+bcv",
        es_heredada=True,
        capturada_ok=False,
    )
    assert serde.serie_from_row(serde.serie_to_row(s)) == s


def test_roundtrip_bandeja_con_detalle_json() -> None:
    bandeja = BandejaFacturacion(
        so_id="SO1",
        lista_aplicada="USD",
        precio_base_calculado=Decimal("100.00"),
        descuentos_detalle=[
            DescuentoAplicado("recurrencia", "recompra 0.03", Decimal("3.00")),
            DescuentoAplicado("contado", "contado", Decimal("3.00")),
        ],
        total_descuentos=Decimal("6.00"),
        total_motor=Decimal("94.00"),
        requiere_revision=True,
        candidata_a_cierre=True,
    )
    rt = serde.bandeja_from_row(serde.bandeja_to_row(bandeja))
    assert rt == bandeja
    assert len(rt.descuentos_detalle) == 2


def test_roundtrip_conciliacion() -> None:
    c = Conciliacion(
        so_id="SO1",
        total_motor=Decimal("94.00"),
        monto_odoo=Decimal("120.00"),
        ncs_odoo=Decimal("0.00"),
        diferencia=Decimal("-26.00"),
        resultado=ResultadoConciliacion.ROJO,
    )
    assert serde.conciliacion_from_row(serde.conciliacion_to_row(c)) == c


def test_roundtrip_descuento_y_regla_y_feriado_y_metodo() -> None:
    d = b.descuento(
        "D2",
        marca="Global Oil",
        categoria="Industrial",
        porcentaje="0.06",
        hasta=date(2026, 12, 31),
    )
    assert serde.descuento_from_row(serde.descuento_to_row(d)) == d
    r = b.regla_recompra("0.03")
    assert serde.regla_from_row(serde.regla_to_row(r)) == r
    f = b.feriado(date(2026, 5, 1), "Día del trabajador")
    assert serde.feriado_from_row(serde.feriado_to_row(f)) == f
    m = b.metodo("MZ", moneda=Moneda.VES, tipo_tasa=TipoTasa.BINANCE)
    assert serde.metodo_from_row(serde.metodo_to_row(m)) == m


def test_roundtrip_bcv_completo() -> None:
    d = b.regla_bcv_completo("0.10", desde=date(2026, 6, 27), hasta=date(2026, 6, 27))
    assert serde.bcv_completo_from_row(serde.bcv_completo_to_row(d)) == d


def test_roundtrip_promocion_primera_compra() -> None:
    p = b.promo_primera("LIGA", desde=date(2026, 6, 1), hasta=date(2026, 7, 31))
    assert serde.promocion_from_row(serde.promocion_to_row(p)) == p


def test_roundtrip_cliente_linea_pago() -> None:
    c = b.cliente("C5")
    assert serde.cliente_from_row(serde.cliente_to_row(c)) == c
    ln = b.linea("L5", marca="Sinoco", categoria="Industrial")
    assert serde.linea_from_row(serde.linea_to_row(ln)) == ln
    p = b.pago("PG5", moneda=Moneda.VES)
    assert serde.pago_from_row(serde.pago_to_row(p)) == p


def test_roundtrip_cliente_agente_retencion_iva() -> None:
    """Bug crítico: wh_iva_agent/wh_iva_rate se perdían en el sync porque

    cliente_to_row/cliente_from_row nunca los serializaban -- el sync
    incremental sobreescribía la hoja Clientes con esos campos vacíos en
    cada ciclo, dejando la Bandeja 3 (Pendiente Comprobante IVA) siempre
    vacía sin importar cuántos clientes fueran agentes de retención en Odoo.
    """
    c = Cliente(
        cliente_id="C_AGENTE",
        nombre="Cliente Agente Retención",
        vendedor_email="v@lubrikca.com",
        wh_iva_agent=True,
        wh_iva_rate=100.0,
    )
    row = serde.cliente_to_row(c)
    assert row["wh_iva_agent"] == "TRUE"
    assert row["wh_iva_rate"] == "100.0"
    assert serde.cliente_from_row(row) == c


def test_gspread_upsert_rows_preserva_columnas_no_mencionadas() -> None:
    """Bug crítico de pérdida de datos: un upsert por lote que solo conoce

    sus propios campos (ej. pago_to_row, que serializa 7 de los N campos
    reales de la hoja Pagos) NO debe borrar columnas de trabajo humano que
    otro flujo ya escribió (recibido, tasa_bcv, etc.) -- antes,
    GspreadGateway.upsert_rows sobreescribía cualquier columna del header
    ausente del dict con "" en cada ciclo del sync. También debe extender
    el header si la fila trae una columna nueva que aún no existe.
    """
    from unittest.mock import MagicMock

    from cxc.sheets.gateway import GspreadGateway

    gw = GspreadGateway.__new__(GspreadGateway)
    ws = MagicMock()
    ws.row_values.return_value = ["pago_id", "monto", "recibido"]
    ws.get_all_records.return_value = [{"pago_id": "P1", "monto": "100", "recibido": "TRUE"}]
    gw._ws = MagicMock(return_value=ws)  # type: ignore[method-assign]

    # Upsert parcial (simula el sync de Pagos): trae pago_id/monto
    # actualizados, pero NO menciona "recibido" -- y trae una columna
    # nueva ("vendedor_email") que el header aún no tiene.
    gw.upsert_rows(
        "Pagos", "pago_id", [{"pago_id": "P1", "monto": "150", "vendedor_email": "v@x.com"}]
    )

    ws.update.assert_any_call("A1", [["pago_id", "monto", "recibido", "vendedor_email"]])
    final_kwargs = ws.update.call_args_list[-1].kwargs
    assert final_kwargs["values"] == [["P1", "150", "TRUE", "v@x.com"]]


# --- SheetsRepository sobre gateway en memoria ------------------------------
def _repo() -> tuple[SheetsRepository, InMemorySheetGateway]:
    gw = InMemorySheetGateway()
    return SheetsRepository(gw), gw


def test_serie_tasas_es_append_only_y_trailing_fail() -> None:
    repo, _ = _repo()
    repo.append_serie_tasa(
        SerieTasa(datetime(2026, 6, 27, 9, 0), Decimal("36"), Decimal("40"), "ok")
    )
    repo.append_serie_tasa(
        SerieTasa(
            datetime(2026, 6, 27, 10, 0),
            Decimal("36"),
            Decimal("40"),
            "h",
            es_heredada=True,
            capturada_ok=False,
        )
    )
    repo.append_serie_tasa(
        SerieTasa(
            datetime(2026, 6, 27, 11, 0),
            Decimal("36"),
            Decimal("40"),
            "h",
            es_heredada=True,
            capturada_ok=False,
        )
    )
    assert repo.trailing_failed_captures() == 2
    last = repo.last_serie_tasa()
    assert last is not None and last.timestamp == datetime(2026, 6, 27, 11, 0)


def test_cursor_sync_roundtrip() -> None:
    repo, _ = _repo()
    assert repo.get_last_sync() is None
    repo.set_last_sync(datetime(2026, 6, 27, 10, 0))
    assert repo.get_last_sync() == datetime(2026, 6, 27, 10, 0)


def test_upsert_y_lecturas_de_espejo() -> None:
    repo, _ = _repo()
    repo.upsert_clientes([b.cliente("C1")])
    repo.upsert_ordenes([b.orden("SO1", cliente_id="C1")])
    repo.upsert_lineas([b.linea("L1", so_id="SO1"), b.linea("L2", so_id="SO2")])
    repo.upsert_pagos([b.pago("PG1")])

    assert repo.get_cliente("C1") is not None
    assert repo.get_orden("SO1") is not None
    assert len(repo.lineas_de_orden("SO1")) == 1
    assert repo.get_pago("PG1") is not None
    # Upsert reemplaza, no duplica.
    repo.upsert_ordenes([b.orden("SO1", cliente_id="C1", monto_total="2000")])
    assert repo.get_orden("SO1").monto_total == Decimal("2000")
    assert len(repo.all_ordenes()) == 1


def test_bandeja_y_conciliacion_persisten() -> None:
    repo, _ = _repo()
    repo.upsert_bandeja(
        BandejaFacturacion(
            so_id="SO1",
            lista_aplicada="USD",
            precio_base_calculado=Decimal("100"),
            total_motor=Decimal("94"),
        )
    )
    assert repo.get_bandeja("SO1") is not None
    assert len(repo.all_bandeja()) == 1
    repo.upsert_conciliacion(
        Conciliacion(
            so_id="SO1",
            total_motor=Decimal("94"),
            monto_odoo=Decimal("94"),
            ncs_odoo=Decimal("0"),
            diferencia=Decimal("0"),
            resultado=ResultadoConciliacion.VERDE,
        )
    )
    assert len(repo.all_conciliaciones()) == 1


def test_config_y_vinculaciones_se_leen() -> None:
    repo, gw = _repo()
    gw.seed("DescuentosMarcaCategoria", [serde.descuento_to_row(b.descuento("D1"))])
    gw.seed("ReglasRecurrencia", [serde.regla_to_row(b.regla_recompra())])
    gw.seed("DescuentoBCVCompleto", [serde.bcv_completo_to_row(b.regla_bcv_completo("0.10"))])
    gw.seed("PromocionPrimeraCompra", [serde.promocion_to_row(b.promo_primera("LIGA"))])
    gw.seed("Feriados", [serde.feriado_to_row(b.feriado(date(2026, 5, 1)))])
    gw.seed("MetodosPago", [serde.metodo_to_row(b.metodo("M1"))])
    gw.seed("Vinculaciones", [serde.vinculacion_to_row(b.vinculacion("V1", so_id="SO1"))])

    assert len(repo.descuentos_marca_categoria()) == 1
    assert len(repo.reglas_recurrencia()) == 1
    assert repo.descuento_bcv_completo()[0].porcentaje == Decimal("0.10")
    assert repo.promociones_primera_compra()[0].productos == "LIGA"
    assert len(repo.feriados()) == 1
    assert repo.get_metodo_pago("M1") is not None
    assert len(repo.vinculaciones_de_orden("SO1")) == 1
    assert len(repo.all_vinculaciones()) == 1
    v = repo.all_vinculaciones()[0]
    v.estado = EstadoVinculacion.APROBADO
    repo.update_vinculacion(v)
    assert repo.all_vinculaciones()[0].estado == EstadoVinculacion.APROBADO
