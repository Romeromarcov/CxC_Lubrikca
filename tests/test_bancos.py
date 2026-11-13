"""Ingesta bancaria, fase 1: parsers, huella, validación, saldos y salidas.

Los estados de cuenta reales no se versionan (son datos bancarios); los parsers se
prueban con libros sintéticos que reproducen la forma de cada banco.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal as D
from typing import Any

import openpyxl
import pytest

from cxc.bancos.clasificacion import clasificar
from cxc.bancos.exportar import COLUMNAS_ODOO, csv_odoo, libro_consolidado
from cxc.bancos.ingesta import MovimientoClasificado, preparar
from cxc.bancos.modelos import EstadoCuenta, MovimientoBancario
from cxc.bancos.numeros import limpiar_ref, parse_fecha, parse_monto
from cxc.bancos.parsers import parsear
from cxc.bancos.saldos import Corte, consolidar
from cxc.bancos.validacion import validar_estado


def _xlsx(filas: list[list[Any]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for f in filas:
        ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _m(orden, deb, cre, saldo, ref="1", desc="X", f=date(2026, 9, 1), banco="T"):
    return MovimientoBancario(
        banco, "C", "VES", f, ref, desc, D(deb), D(cre), None if saldo is None else D(saldo), orden
    )


# --------------------------------------------------------------------- números


@pytest.mark.parametrize(
    "raw,esperado",
    [
        ("1.002.945,86", D("1002945.86")),
        ("16.852,72", D("16852.72")),
        ("58,509.32", D("58509.32")),
        ("            58,509.32", D("58509.32")),
        ("69161775,62", D("69161775.62")),
        (810744.25, D("810744.25")),
        (5277.489999999991, D("5277.49")),
        (168200, D("168200.00")),
        ("", None),
        (None, None),
        (-5101.98, D("-5101.98")),
        ("0.00", D("0.00")),
        ("1.234", D("1234.00")),  # punto de miles, no 1,23
        ("12.50", D("12.50")),
        ("(54,00)", D("-54.00")),
    ],
)
def test_parse_monto(raw, esperado):
    assert parse_monto(raw) == esperado


def test_parse_monto_invalido():
    with pytest.raises(ValueError):
        parse_monto("abc")
    with pytest.raises(ValueError):
        parse_monto(True)


def test_fechas_y_refs():
    assert parse_fecha("09/09/2026") == date(2026, 9, 9)
    assert parse_fecha("2026-09-09") == date(2026, 9, 9)
    assert parse_fecha(46296.0) == date(2026, 10, 1)
    assert parse_fecha(datetime(2026, 9, 9, 10, 0)) == date(2026, 9, 9)
    with pytest.raises(ValueError):
        parse_fecha("32/13/2026")
    assert limpiar_ref("'19763674") == "19763674"
    assert limpiar_ref(640065766.0) == "640065766"
    assert limpiar_ref("") == ""


# ------------------------------------------------------------ validación/huella


def test_cadena_de_saldos_ok_y_hueco():
    e = EstadoCuenta(
        "T", "C", "VES", "a", [_m(1, "0", "100", "1100"), _m(2, "50", "0", "1050")], D("1000")
    )
    assert validar_estado(e).ok
    e.movimientos.append(_m(3, "10", "0", "900"))  # debería ser 1040: falta una fila
    r = validar_estado(e)
    assert not r.ok and "#3" in r.saltos[0]


def test_saldo_final_declarado_distinto_descuadra():
    e = EstadoCuenta("T", "C", "VES", "a", [_m(1, "0", "100", "1100")], D("1000"), D("999"))
    r = validar_estado(e)
    assert not r.ok and "saldo final declarado" in r.saltos[0]


def test_huella_estable_y_distinta_por_saldo():
    a, b = _m(1, "0", "5", "10"), _m(9, "0", "5", "10")
    assert a.huella == b.huella  # re-importar un corte solapado no duplica
    assert a.huella != _m(1, "0", "5", "15").huella
    assert a.unique_import_id == f"T-C-{a.huella}"


def test_huella_sin_saldo_distingue_movimientos_identicos():
    # Dos comisiones iguales el mismo día, sin saldo que las distinga: no son la misma.
    assert _m(1, "54", "0", None).huella != _m(2, "54", "0", None).huella


@pytest.mark.parametrize(
    "desc,deb,cre,cat",
    [
        ("COM MANTENIMIENTO DE CUENTA", "54", "0", "comision_bancaria"),
        ("TD POS J0507836142001", "0", "100", "cobro_pos"),
        ("TRF CR INM 0102 J507836142 DISTRIBUIDORA LUB", "0", "310000", "cobro_candidato"),
        ("PAGO A PROVEEDORES J40729429 SEPAC C.A.", "0", "1002945.86", "cobro_candidato"),
        ("PAGO A TERCEROS BDV J303253636 TV ZAMORA CA", "28987.88", "0", "pago_a_terceros"),
        ("ND Compra USD Intervencion Digital Bs", "813736.1", "0", "operacion_cambiaria"),
        ("INV.PLATCO PNCCOB 00002", "5101.98", "0", "operacion_pos"),
        ("NC Transf. Distinto cliente Internet", "0", "36616286.07", "cobro_candidato"),
        ("ND Credito Inmediato Dist Cliente", "540858.37", "0", "pago_a_terceros"),
        # Vistos en los estados reales: comisiones que caían en nómina o sin regla.
        ("Comisión servicio pago de nomina", "172.03", "0", "comision_bancaria"),
        ("Comisi�n servicio pago de nomina", "21.5", "0", "comision_bancaria"),
        ("Pago mixto nomina", "68814.02", "0", "nomina"),
        ("COMSERVMTTO CTA", "54", "0", "comision_bancaria"),
        ("COMENVIO AVCCE", "54", "0", "comision_bancaria"),
        ("MANT AFIL CANAL VIRT AGO26", "886.57", "0", "comision_bancaria"),
        ("SALDO INSUFICIENTE", "54", "0", "comision_bancaria"),
        ("ALGO RARO", "0", "10", "ingreso_por_revisar"),
        ("ALGO RARO", "10", "0", "por_revisar"),
    ],
)
def test_clasificacion(desc, deb, cre, cat):
    assert clasificar(_m(1, deb, cre, "0", desc=desc))[0] == cat


# --------------------------------------------------------------------- parsers


def _libro_deb_cred(con_inicial=True, footer=None, codigo=False) -> bytes:
    enc = ["Fecha", "Referencia", "Concepto", "Débito", "Crédito", "Saldo"]
    if codigo:
        enc.append("Código")
    filas: list[list[Any]] = [["ESTADO DE CUENTA"], [], enc]
    if con_inicial:
        filas.append(["", "", "SALDO INICIAL", "", "", "1.000,00"])
    filas += [
        ["01/09/2026", "'123", "TRF CR INM CLIENTE", "", "500,00", "1.500,00"],
        ["02/09/2026", 640065766.0, "COM MANTENIMIENTO DE CUENTA", "54,00", "", "1.446,00"],
        [None, None, None, 0, 0, 0],  # relleno al final del xlsx
    ]
    if footer:
        filas.append([footer])
    if codigo:
        for f in filas[3 + con_inicial :]:
            f.append("NC")
    return _xlsx(filas)


def test_parser_deb_cred_con_saldo_inicial_explicito():
    e = parsear(_libro_deb_cred(), "venezuela.xlsx", "venezuela")
    assert e.banco == "VENEZUELA" and e.saldo_inicial == D("1000.00")
    assert not e.saldo_inicial_derivado
    assert [m.referencia for m in e.movimientos] == ["123", "640065766"]
    assert e.movimientos[1].debito == D("54.00") and e.movimientos[0].credito == D("500.00")
    assert validar_estado(e).ok


def test_parser_deb_cred_deriva_saldo_inicial_y_lee_footer():
    footer = "Creditos Total: 500,00 Debito Total: 54,00 Saldo Final: 1.446,00"
    e = parsear(_libro_deb_cred(con_inicial=False, footer=footer), "bm.xlsx", "BANCAMIGA")
    assert e.saldo_inicial == D("1000.00") and e.saldo_inicial_derivado
    assert e.saldo_final == D("1446.00")
    assert validar_estado(e).ok


def _libro_dos_cortes(saldo_inicial_octubre: str) -> bytes:
    # Consolidado: el estado de septiembre y el de octubre pegados en una sola hoja.
    return _xlsx(
        [
            ["Nro.", "Fecha", "Referencia", "Concepto", "Débito", "Crédito", "Saldo"],
            [1, "29/09/2026", "11", "TRF CR INM CLIENTE", "", "500,00", "1.500,00"],
            [2, "30/09/2026", "12", "COM PAGO OTRAS CTAS", "54,00", "", "1.446,00"],
            [1, "01/10/2026", "", "SALDO INICIAL", "", "", saldo_inicial_octubre],
            [2, "01/10/2026", "13", "TRF CR INM CLIENTE", "", "100,00", "1.546,00"],
        ]
    )


def test_saldo_inicial_a_mitad_de_archivo_no_reemplaza_la_apertura():
    e = parsear(_libro_dos_cortes("1.446,00"), "bdv.xlsx", "VENEZUELA")
    assert e.saldo_inicial == D("1000.00") and e.saldo_inicial_derivado
    assert len(e.movimientos) == 3 and not e.avisos
    assert validar_estado(e).ok


def test_saldo_inicial_a_mitad_de_archivo_que_no_empalma_avisa():
    e = parsear(_libro_dos_cortes("1.400,00"), "bdv.xlsx", "VENEZUELA")
    assert e.saldo_inicial == D("1000.00")
    assert any("a mitad de archivo" in a for a in e.avisos)


def test_parser_bdt_lee_codigo():
    e = parsear(_libro_deb_cred(codigo=True), "bdt.xlsx", "bdt")
    assert {m.codigo for m in e.movimientos} == {"NC"}


def test_parser_sin_encabezado_falla_claro():
    with pytest.raises(ValueError, match="encabezado"):
        parsear(_xlsx([["nada"], ["que ver"]]), "x.xlsx", "banesco")


def test_banco_sin_parser():
    with pytest.raises(KeyError, match="Banco sin parser"):
        parsear(b"x", "x.xlsx", "BANCO INVENTADO")


def _libro_provincial(correlativo_base: int, filas_extra: bool = False) -> bytes:
    # Orden descendente, importe firmado, saldo como texto en formato inglés.
    filas: list[list[Any]] = [["Fecha", "Referencia", "Descripción", "Importe", "Saldo"]]
    datos = [
        ("15/08/2026", "COM MTTO POS", -3521.60, "  92,989.27"),
        ("13/08/2026", "TC POS J0507836142001", 96510.87, "  96,510.87"),
    ]
    if filas_extra:
        datos.insert(0, ("20/08/2026", "TD POS J0507836142001", 10.73, "  93,000.00"))
    for i, (f, d, imp, s) in enumerate(datos):
        filas.append([f, correlativo_base + len(datos) - i, d, imp, s])
    return _xlsx(filas)


def test_parser_provincial_ordena_y_no_usa_correlativo_como_referencia():
    e = parsear(_libro_provincial(32), "prov.xlsx", "provincial")
    assert [m.fecha.day for m in e.movimientos] == [13, 15]
    assert all(m.referencia == "" for m in e.movimientos)
    assert e.movimientos[0].correlativo == "33"
    assert e.saldo_inicial == D("0.00") and validar_estado(e).ok


def test_provincial_otro_rango_no_cambia_la_huella():
    """El correlativo se renumera al exportar otro rango: la huella no debe moverse."""
    a = parsear(_libro_provincial(32), "a.xlsx", "provincial")
    b = parsear(_libro_provincial(1, filas_extra=True), "b.xlsx", "provincial")
    assert {m.huella for m in a.movimientos} <= {m.huella for m in b.movimientos}


# ---------------------------------------------------------------------- ingesta


def test_preparar_valida_y_clasifica():
    prep = preparar(_libro_deb_cred(), "v.xlsx", "VENEZUELA")
    assert len(prep.sha256) == 64 and prep.validacion.ok
    assert [mc.categoria for mc in prep.movimientos] == ["cobro_candidato", "comision_bancaria"]
    assert prep.observaciones == ""


def test_preparar_marca_saldo_derivado_en_observaciones():
    prep = preparar(_libro_deb_cred(con_inicial=False), "v.xlsx", "VENEZUELA")
    assert "derivado" in prep.observaciones


@pytest.mark.parametrize(
    "tamano,msg", [(0, "vacío"), (10 * 1024 * 1024 + 1, "MB")], ids=["vacio", "enorme"]
)
def test_preparar_rechaza_vacio_o_enorme(tamano, msg):
    with pytest.raises(ValueError, match=msg):
        preparar(b"x" * tamano, "x.xlsx", "VENEZUELA")


def test_preparar_sin_movimientos():
    sin_movs = _xlsx([["Fecha", "Referencia", "Concepto", "Débito", "Crédito", "Saldo"]])
    with pytest.raises(ValueError, match="ningún movimiento"):
        preparar(sin_movs, "x.xlsx", "BANESCO")


# ----------------------------------------------------------------------- saldos


def _corte(i, banco, desde, hasta, ini, fin, moneda="VES", cuadra=True):
    return Corte(
        i,
        banco,
        banco,
        moneda,
        f"{banco}{i}.xlsx",
        desde,
        hasta,
        None if ini is None else D(ini),
        None if fin is None else D(fin),
        cuadra,
        datetime(2026, 11, 1, 0, i),
    )


def test_consolidar_convierte_con_la_tasa_de_la_fecha_de_corte():
    tasas = {date(2026, 9, 30): D("200"), date(2026, 10, 5): D("250")}
    cons = consolidar(
        [
            _corte(1, "BANCAMIGA", date(2026, 9, 1), date(2026, 9, 30), "0", "2000"),
            _corte(2, "BINANCE", date(2026, 10, 1), date(2026, 10, 5), "0", "10", moneda="USD"),
        ],
        tasas.get,
    )
    bm, bn = cons.cuentas
    assert (bm.saldo_usd, bm.saldo_ves, bm.tasa_bcv) == (D("10.00"), D("2000.00"), D("200"))
    assert (bn.saldo_usd, bn.saldo_ves) == (D("10.00"), D("2500.00"))
    assert cons.total_usd == D("20.00") and cons.total_ves == D("4500.00")
    assert cons.fechas_de_corte == [date(2026, 9, 30), date(2026, 10, 5)]


def test_consolidar_sin_tasa_no_inventa_conversion():
    cons = consolidar([_corte(1, "BDT", date(2026, 10, 1), date(2026, 10, 5), "0", "5")], {}.get)
    c = cons.cuentas[0]
    assert c.saldo_usd is None and c.saldo_ves == D("5.00")
    assert cons.cuentas_sin_tasa == ["BDT"] and cons.total_ves == 0


def test_consolidar_usa_el_ultimo_corte_y_detecta_huecos():
    cortes = [
        _corte(1, "BDV", date(2026, 8, 1), date(2026, 8, 31), "0", "100"),
        _corte(2, "BDV", date(2026, 9, 1), date(2026, 9, 30), "100", "300"),  # continuo
        _corte(3, "BDV", date(2026, 10, 2), date(2026, 10, 31), "350", "400", cuadra=False),
    ]
    c = consolidar(cortes, lambda _d: D("100")).cuentas[0]
    assert c.saldo == D("400") and c.fecha_corte == date(2026, 10, 31) and c.n_cortes == 3
    assert c.cortes_sin_cuadrar == 1
    assert "no cuadra" in c.alertas[0]
    assert len(c.alertas) == 2 and "hueco entre 2026-09-30 y 2026-10-02" in c.alertas[1]
    assert "cierra en 300.00 y" in c.alertas[1] and "(Δ=50.00)" in c.alertas[1]


def test_consolidar_cortes_solapados_no_alertan():
    cortes = [
        _corte(1, "BDV", date(2026, 9, 1), date(2026, 9, 30), "0", "100"),
        _corte(2, "BDV", date(2026, 9, 15), date(2026, 10, 15), "999", "150"),
    ]
    assert consolidar(cortes, lambda _d: D("1")).cuentas[0].alertas == []


# ---------------------------------------------------------------------- salidas


def _mc(banco="BANCAMIGA") -> MovimientoClasificado:
    return MovimientoClasificado(
        _m(1, "54.00", "0", "946", ref="77", desc="COM MTTO", banco=banco), "comision", "c"
    )


def test_csv_odoo_columnas_y_signo():
    filas = list(csv.reader(io.StringIO(csv_odoo([_mc()]))))
    assert filas[0] == COLUMNAS_ODOO
    uid, diario, fecha, ref_pago, ref, monto = filas[1]
    assert diario == "Banco Bancamiga 7806" and fecha == "2026-09-01"
    assert (ref_pago, ref, monto) == ("COM MTTO", "77", "-54.00")
    assert uid.startswith("BANCAMIGA-C-")


def test_csv_odoo_banco_sin_diario():
    with pytest.raises(KeyError, match="no tiene diario"):
        csv_odoo([_mc(banco="DESCONOCIDO")])


def test_libro_consolidado_tiene_las_tres_hojas():
    cons = consolidar(
        [_corte(1, "BANCAMIGA", date(2026, 9, 1), date(2026, 9, 30), "0", "946")], {}.get
    )
    wb = openpyxl.load_workbook(io.BytesIO(libro_consolidado([_mc()], cons)))
    assert wb.sheetnames == ["Saldos", "Movimientos", "Categorías"]
    assert wb["Saldos"]["A2"].value == "BANCAMIGA"
    assert "sin tasa: BANCAMIGA" in wb["Saldos"]["K4"].value
    assert wb["Movimientos"]["F2"].value == "COM MTTO"
    assert wb["Categorías"]["C2"].value == 1
