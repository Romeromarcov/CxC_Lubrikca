"""Pre-conciliación (fase 2): reglas puras, lectura de Odoo solo-lectura, persistencia
de propuestas y decisiones, y sus endpoints.

Las reglas se calibraron contra lo que contabilidad concilió en Odoo (ver
docs/BANCOS.md); acá se fija cada una con casos chicos y sintéticos.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal as D
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cxc.bancos import repositorio
from cxc.bancos.conciliacion import (
    AMBIGUO,
    CUENTA,
    CUENTA_COMISIONES,
    PAGO,
    SIN_PROPUESTA,
    PagoCandidato,
    contactos_por_rif,
    pagos_candidatos,
    proponer,
    rifs,
    solo_lectura,
)
from cxc.bancos.ingesta import MovimientoClasificado
from cxc.bancos.modelos import MovimientoBancario
from cxc.permisos import efectivos

HOY = date(2026, 10, 15)


def _mc(
    neto: str,
    *,
    cat: str = "cobro_candidato",
    f: date = HOY,
    ref: str = "000123456789",
    desc: str = "TRF CR INM",
    banco: str = "BANCAMIGA",
    orden: int = 1,
) -> MovimientoClasificado:
    n = D(neto)
    m = MovimientoBancario(
        banco=banco,
        cuenta=banco,
        moneda="VES",
        fecha=f,
        referencia=ref,
        descripcion=desc,
        debito=-n if n < 0 else D("0"),
        credito=n if n > 0 else D("0"),
        saldo=D("1000") + orden,
        orden=orden,
    )
    return MovimientoClasificado(m, cat, "r")


def _pago(
    pid: int,
    monto: str,
    *,
    f: date = HOY,
    entrante: bool = True,
    memo: str = "",
    rif: str = "",
    conciliado: bool = False,
) -> PagoCandidato:
    return PagoCandidato(
        pago_id=pid,
        nombre=f"PBAMI/2026/{pid:05d}",
        fecha=f,
        monto=D(monto),
        entrante=entrante,
        memo=memo,
        partner="Cliente",
        partner_rifs=rifs(rif),
        ya_conciliado=conciliado,
    )


# ------------------------------------------------------------------ reglas puras


def test_r1_comision_va_a_la_cuenta_de_comisiones():
    (p,) = proponer([_mc("-54", cat="comision_bancaria")], [_pago(1, "54", entrante=False)])
    assert (p.tipo, p.regla, p.cuenta) == (CUENTA, "R1", CUENTA_COMISIONES)


def test_r2_un_solo_pago_del_mismo_monto_y_sentido():
    (p,) = proponer([_mc("500")], [_pago(7, "500"), _pago(8, "500", entrante=False)])
    assert (p.tipo, p.pago_id, p.pago_nombre, p.candidatos) == (PAGO, 7, "PBAMI/2026/00007", [7])


def test_r2_fuera_de_la_ventana_no_es_candidato():
    lejos = _pago(7, "500", f=date(2026, 10, 19))
    (p,) = proponer([_mc("500")], [lejos])
    assert p.tipo == SIN_PROPUESTA


def test_r2_empate_queda_ambiguo_para_una_persona():
    (p,) = proponer([_mc("500")], [_pago(7, "500"), _pago(8, "500")])
    assert (p.tipo, p.pago_id, sorted(p.candidatos)) == (AMBIGUO, None, [7, 8])


@pytest.mark.parametrize(
    "pagos,gana",
    [
        # misma fecha gana a un día de diferencia
        ([_pago(7, "500", f=date(2026, 10, 14)), _pago(8, "500")], 8),
        # los últimos dígitos de la referencia del banco en el memo
        ([_pago(7, "500"), _pago(8, "500", memo="abono ref: 6789 S00375")], 8),
        # el RIF de la descripción igual al del cliente del pago
        ([_pago(7, "500", rif="J-50783614-2"), _pago(8, "500", rif="V12670859")], 8),
    ],
)
def test_r2_desempates(pagos, gana):
    (p,) = proponer([_mc("500", desc="TRANSF RECIBIDA BDV V12670859 YANIS")], pagos)
    assert (p.tipo, p.pago_id) == (PAGO, gana)


def test_r2_un_pago_no_se_usa_dos_veces():
    movs = [_mc("500", orden=1), _mc("500", orden=2, ref="999")]
    p1, p2 = proponer(movs, [_pago(7, "500")])
    assert (p1.tipo, p1.pago_id) == (PAGO, 7)
    assert p2.tipo == SIN_PROPUESTA


def test_r2_avisa_si_el_pago_ya_esta_conciliado_en_odoo():
    (p,) = proponer([_mc("500")], [_pago(7, "500", conciliado=True)])
    assert p.tipo == PAGO and "ya conciliado en Odoo" in p.motivo


def test_r3_compra_con_tarjeta_sin_pago_va_a_pagos_pendientes_del_banco():
    (p,) = proponer([_mc("-2255.06", cat="compra_tarjeta")], [])
    assert (p.tipo, p.regla, p.cuenta) == (
        CUENTA, "R3", "1.1.1.3.00.12 Pagos pendientes Bancamiga"
    )


def test_r3_pero_si_hay_pago_gana_el_pago():
    (p,) = proponer([_mc("-2255.06", cat="compra_tarjeta")], [_pago(9, "2255.06", entrante=False)])
    assert (p.tipo, p.pago_id) == (PAGO, 9)


def test_r3_binance_no_tiene_cuenta_de_pendientes():
    (p,) = proponer([_mc("-10", cat="compra_tarjeta", banco="BINANCE")], [])
    assert p.tipo == SIN_PROPUESTA


def test_rifs_normaliza():
    assert rifs("PAGO J-0507836142 y V12.670.859") == {"J507836142"}
    assert rifs("TRANSF RECIBIDA BDV V12670859 YANIS") == {"V12670859"}
    assert rifs("TRF J-50783614-2 / V-12670859-1234") == {"J507836142", "V12670859"}


# ------------------------------------------------------------ Odoo, solo lectura


def test_solo_lectura_bloquea_escrituras_antes_de_enviarlas():
    execute = MagicMock()
    ex = solo_lectura(execute)
    for metodo in ("create", "write", "unlink", "action_post", "reconcile"):
        with pytest.raises(PermissionError, match="no es de lectura"):
            ex("account.bank.statement.line", metodo, [[]])
    execute.assert_not_called()
    ex("account.payment", "search_read", [[]], {"fields": ["id"]})
    execute.assert_called_once()


def test_pagos_candidatos_lee_pagos_y_rif_del_cliente():
    llamadas: list[tuple[str, str]] = []

    def execute(model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        llamadas.append((model, method))
        if model == "account.payment":
            dominio = args[0]
            assert ["journal_id", "=", 30] in dominio
            assert ["date", ">=", "2026-09-28"] in dominio  # 3 días antes
            assert ["date", "<=", "2026-10-18"] in dominio  # 3 días después
            return [
                {
                    "id": 5, "name": "PBAMI/2026/00005", "date": "2026-10-01", "amount": 105737.8,
                    "payment_type": "inbound", "memo": "ref: 2262 pago completo S01114",
                    "partner_id": [77, "En ascenso 2011,c.a."], "is_matched": True,
                },
                {
                    "id": 6, "name": "PBAMI/2026/00006", "date": "2026-10-02", "amount": 50,
                    "payment_type": "outbound", "memo": False, "partner_id": False,
                    "is_matched": False,
                },
            ]
        assert (model, method) == ("res.partner", "read")
        return [{"id": 77, "vat": "J-50783614-2"}]

    pagos = pagos_candidatos(execute, 30, date(2026, 10, 1), date(2026, 10, 15))
    a, b = pagos
    assert (a.pago_id, a.monto, a.entrante, a.ya_conciliado) == (5, D("105737.80"), True, True)
    assert a.partner_rifs == {"J507836142"}
    assert (b.entrante, b.memo, b.partner, b.partner_rifs) == (False, "", "", frozenset())
    assert all(m in ("search_read", "read") for _, m in llamadas)


def test_contactos_por_rif_solo_si_un_contacto_lo_tiene():
    llamadas: list[tuple[str, str, Any]] = []

    def execute(model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        llamadas.append((model, method, args))
        if model == "res.company":
            return [{"id": 1, "partner_id": [1, "Lubrikca"]}]
        return [
            {"id": 1, "display_name": "Lubrikca", "vat": "J-50000000-1"},  # la propia empresa
            {"id": 77, "display_name": "En ascenso 2011,c.a.", "vat": "J-50783614-2"},
            {"id": 80, "display_name": "Yanis", "vat": "V12670859"},
            {"id": 81, "display_name": "Yanis (dup)", "vat": "V-12670859"},
            {"id": 90, "display_name": "Parecido", "vat": "J5078361429"},  # otro RIF
        ]

    r = contactos_por_rif(execute, {"J507836142", "V12670859", "J500000001"})
    assert r == {"J507836142": "En ascenso 2011,c.a."}
    dominio = llamadas[1][2][0]
    assert dominio[:2] == ["|", "|"] and ["vat", "ilike", "50783614"] in dominio
    assert all(m == "search_read" for _, m, _ in llamadas)
    assert contactos_por_rif(execute, []) == {} and len(llamadas) == 2


# ------------------------------------------------------------------- endpoints

client = TestClient(__import__("cxc.web.app", fromlist=["app"]).app)
ADMIN = {"email": "admin@x.test", "rol": "admin", "permisos": []}
AUDITOR = {"email": "aud@x.test", "rol": "auditor", "permisos": efectivos("auditor")}
VENDEDOR = {"email": "v@x.test", "rol": "vendedor", "permisos": []}


def _como(usuario):
    return patch("cxc.web.app.get_current_user_from_cookie", return_value=usuario)


@pytest.fixture
def sin_base():
    with patch("cxc.web.app._engine_bancos", return_value=MagicMock()):
        yield


@pytest.mark.parametrize("usuario,esperado", [(None, 401), (VENDEDOR, 403), (AUDITOR, 403)])
def test_generar_y_decidir_solo_admin(usuario, esperado, sin_base):
    with _como(usuario):
        assert client.post("/api/bancos/conciliacion/generar").status_code == esperado
        r = client.post("/api/bancos/conciliacion/abc/decidir", json={"accion": "aprobar"})
        assert r.status_code == esperado


def test_listar_lo_ve_el_auditor(sin_base):
    fila = {
        "huella": "h1", "banco": "BANCAMIGA", "fecha": date(2026, 10, 1), "monto": D("5.00"),
        "tipo": PAGO, "estado": "pendiente", "candidatos": [],
    }
    with _como(AUDITOR), patch("cxc.web.app.bancos_repo.propuestas", return_value=[fila]):
        r = client.get("/api/bancos/conciliacion?banco=BANCAMIGA&estado=pendiente")
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["fecha"] == "2026-10-01" and body["items"][0]["monto"] == 5.0
    assert body["resumen"] == {"estado": {"pendiente": 1}, "tipo": {PAGO: 1}}


def test_generar_lee_odoo_por_diario_y_guarda(sin_base):
    movs = [_mc("500", banco="BANCAMIGA"), _mc(
        "-10", banco="BANCAMIGA_USD", cat="compra_tarjeta",
        desc="Op. Internac. MasterDebit ME Sub - FACEBK",
    )]
    pagos = [_pago(7, "500")]
    with (
        _como(ADMIN),
        patch("cxc.web.app.bancos_repo.movimientos", return_value=movs),
        patch("cxc.web.app._connect", return_value=MagicMock()) as conn,
        patch("cxc.web.app.AppConfig.from_env", return_value=MagicMock()),
        patch(
            "cxc.web.app.bancos_conciliacion.pagos_candidatos", return_value=pagos
        ) as leer,
        patch(
            "cxc.web.app.bancos_repo.guardar_propuestas",
            return_value={"nuevas": 1, "actualizadas": 0, "ya_decididas": 0},
        ) as guardar,
    ):
        r = client.post("/api/bancos/conciliacion/generar")
    assert r.status_code == 200, r.text
    # Solo el banco con diario en Odoo pide pagos; BANCAMIGA_USD no tiene diario.
    assert leer.call_count == 1 and leer.call_args.args[1] == 30
    conn.assert_called_once()
    assert r.json()["por_tipo"] == {PAGO: 1, CUENTA: 1}
    # Los datos del pago viajan con la propuesta para mostrarlos sin volver a Odoo.
    props, info = guardar.call_args_list[0].args[1:]
    assert info[7]["nombre"] == "PBAMI/2026/00007" and props[0].pago_id == 7


def test_generar_sin_odoo_devuelve_502(sin_base):
    with (
        _como(ADMIN),
        patch("cxc.web.app.bancos_repo.movimientos", return_value=[_mc("500")]),
        patch("cxc.web.app.AppConfig.from_env", return_value=MagicMock()),
        patch("cxc.web.app._connect", side_effect=PermissionError("Autenticación Odoo fallida")),
    ):
        r = client.post("/api/bancos/conciliacion/generar")
    assert r.status_code == 502 and "Odoo" in r.json()["detail"]


@pytest.mark.parametrize(
    "error,codigo",
    [(KeyError("no hay propuesta para el movimiento x"), 404),
     (repositorio.DecisionInvalida("la propuesta ya está aprobada"), 409)],
)
def test_decidir_errores(error, codigo, sin_base):
    with _como(ADMIN), patch("cxc.web.app.bancos_repo.decidir", side_effect=error):
        r = client.post("/api/bancos/conciliacion/x/decidir", json={"accion": "aprobar"})
    assert r.status_code == codigo


def test_decidir_registra_al_usuario(sin_base):
    with (
        _como(ADMIN),
        patch("cxc.web.app.bancos_repo.decidir", return_value={"estado": "aprobada"}) as d,
    ):
        r = client.post(
            "/api/bancos/conciliacion/h1/decidir", json={"accion": "corregir", "pago_id": 9}
        )
    assert r.status_code == 200
    assert d.call_args.args[1:] == ("h1", "corregir", "admin@x.test")
    assert d.call_args.kwargs["pago_id"] == 9


# ------------------------------------------------------- persistencia (Postgres)

_URL = os.environ.get("DATABASE_URL", "")


@pytest.fixture
def engine():
    if not _URL:
        pytest.skip("Sin DATABASE_URL: la pre-conciliación necesita Postgres.")
    from cxc.db import schema
    from cxc.db.engine import make_engine

    eng = make_engine(_URL)
    schema.metadata.create_all(
        eng,
        tables=[
            schema.bancos_archivos,
            schema.bancos_movimientos,
            schema.bancos_propuestas,
            schema.bancos_decisiones,
        ],
    )
    with eng.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE bancos_decisiones, bancos_propuestas, bancos_movimientos, "
                "bancos_archivos RESTART IDENTITY CASCADE"
            )
        )
    yield eng
    eng.dispose()


def _cargar(engine) -> list[MovimientoClasificado]:
    import io

    import openpyxl

    from cxc.bancos.ingesta import preparar

    wb = openpyxl.Workbook()
    ws = wb.active
    for f in [
        ["Fecha", "Referencia", "Concepto", "Débito", "Crédito", "Saldo"],
        ["01/10/2026", "1", "TRF CR INM CLIENTE A", "", "500,00", "1.500,00"],
        ["01/10/2026", "2", "COM MANTENIMIENTO DE CUENTA", "54,00", "", "1.446,00"],
        ["02/10/2026", "3", "ALGO RARO", "6,00", "", "1.440,00"],
    ]:
        ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    prep = preparar(buf.getvalue(), "bm.xlsx", "BANCAMIGA")
    repositorio.guardar(engine, prep, "admin@x.test")
    return repositorio.movimientos(engine)


def _generar(engine, movs):
    props = proponer(movs, [_pago(7, "500", f=date(2026, 10, 1))])
    info = {7: {"id": 7, "nombre": "PBAMI/2026/00007", "partner": "Cliente A"}}
    return props, repositorio.guardar_propuestas(engine, props, info)


def test_propuestas_se_guardan_y_se_refrescan_sin_pisar_decisiones(engine):
    movs = _cargar(engine)
    props, r1 = _generar(engine, movs)
    assert r1 == {"nuevas": 3, "actualizadas": 0, "ya_decididas": 0}
    pago = next(p for p in props if p.tipo == PAGO)
    repositorio.decidir(engine, pago.huella, "aprobar", "admin@x.test")
    _, r2 = _generar(engine, movs)
    assert r2 == {"nuevas": 0, "actualizadas": 2, "ya_decididas": 1}
    filas = {f["huella"]: f for f in repositorio.propuestas(engine)}
    assert filas[pago.huella]["estado"] == "aprobada"
    assert filas[pago.huella]["decidida_por"] == "admin@x.test"
    assert filas[pago.huella]["candidatos"][0]["partner"] == "Cliente A"
    assert {f["tipo"] for f in repositorio.propuestas(engine, estado="pendiente")} == {
        CUENTA, SIN_PROPUESTA
    }


def test_flujo_de_decisiones_y_bitacora(engine):
    movs = _cargar(engine)
    props, _ = _generar(engine, movs)
    sin = next(p for p in props if p.tipo == SIN_PROPUESTA)
    pago = next(p for p in props if p.tipo == PAGO)

    with pytest.raises(repositorio.DecisionInvalida, match="nada que aprobar"):
        repositorio.decidir(engine, sin.huella, "aprobar", "u")
    with pytest.raises(repositorio.DecisionInvalida, match="comentario"):
        repositorio.decidir(engine, sin.huella, "rechazar", "u")
    with pytest.raises(repositorio.DecisionInvalida, match="uno solo"):
        repositorio.decidir(engine, sin.huella, "corregir", "u", pago_id=7, cuenta="X")

    r = repositorio.decidir(
        engine, sin.huella, "corregir", "u", cuenta="6.1.2.2.06.05 Articulos de Limpieza"
    )
    assert (r["estado"], r["tipo"], r["cuenta"]) == ("corregida", CUENTA, r["cuenta"])
    repositorio.decidir(engine, pago.huella, "aprobar", "u")
    with pytest.raises(repositorio.DecisionInvalida, match="reabrila"):
        repositorio.decidir(engine, pago.huella, "rechazar", "u", comentario="no")
    repositorio.decidir(engine, pago.huella, "reabrir", "jefe", comentario="revisar de nuevo")
    repositorio.decidir(engine, pago.huella, "corregir", "jefe", pago_id=7)
    fila = next(f for f in repositorio.propuestas(engine) if f["huella"] == pago.huella)
    assert (fila["estado"], fila["pago_nombre"], fila["decidida_por"]) == (
        "corregida", "PBAMI/2026/00007", "jefe"
    )
    with pytest.raises(KeyError):
        repositorio.decidir(engine, "no-existe", "aprobar", "u")

    bitacora = repositorio.decisiones(engine, huella=pago.huella)
    assert [b["accion"] for b in bitacora] == ["corregir", "reabrir", "aprobar"]
    assert bitacora[1]["antes"]["estado"] == "aprobada"
    assert bitacora[1]["despues"]["estado"] == "pendiente"
    assert bitacora[1]["comentario"] == "revisar de nuevo" and bitacora[1]["usuario"] == "jefe"
    assert len(repositorio.decisiones(engine)) == 4


def test_la_bitacora_es_solo_append(engine):
    movs = _cargar(engine)
    props, _ = _generar(engine, movs)
    repositorio.decidir(engine, props[0].huella, "rechazar", "u", comentario="mal")
    existe = False
    with engine.connect() as conn:
        existe = bool(
            conn.execute(
                text("SELECT 1 FROM pg_trigger WHERE tgname = 'trg_bancos_decisiones_inmutable'")
            ).first()
        )
    if not existe:
        pytest.skip("El trigger lo crea la migración (alembic upgrade head).")
    for sql in ("UPDATE bancos_decisiones SET usuario = 'otro'", "DELETE FROM bancos_decisiones"):
        with pytest.raises(DBAPIError, match="solo-append"), engine.begin() as conn:
            conn.execute(text(sql))


# ------------------------------------------------------------------- aprobadas a Excel


def test_aprobadas_trae_lo_decidido_con_su_ultimo_comentario(engine):
    movs = _cargar(engine)
    props, _ = _generar(engine, movs)
    pago = next(p for p in props if p.tipo == PAGO)
    cuenta = next(p for p in props if p.tipo == CUENTA)
    sin = next(p for p in props if p.tipo == SIN_PROPUESTA)
    repositorio.decidir(engine, pago.huella, "aprobar", "ana", comentario="ok")
    repositorio.decidir(engine, cuenta.huella, "rechazar", "ana", comentario="no va")
    repositorio.decidir(engine, sin.huella, "corregir", "ana", cuenta="6.1 Limpieza")
    repositorio.decidir(engine, sin.huella, "reabrir", "jefe", comentario="revisar")
    repositorio.decidir(
        engine, sin.huella, "corregir", "jefe", cuenta="6.2 Otra", comentario="era otra"
    )

    filas = repositorio.aprobadas(engine)
    assert {f["huella"]: (f["estado"], f["comentario"]) for f in filas} == {
        pago.huella: ("aprobada", "ok"),
        sin.huella: ("corregida", "era otra"),
    }
    hoy = date.today()
    assert len(repositorio.aprobadas(engine, decididas_desde=hoy, decididas_hasta=hoy)) == 2
    assert repositorio.aprobadas(engine, decididas_hasta=date(2000, 1, 1)) == []
    assert repositorio.aprobadas(engine, banco="BDT") == []


def _libro(filas):
    import io

    import openpyxl

    from cxc.bancos.exportar import libro_aprobadas

    return openpyxl.load_workbook(io.BytesIO(libro_aprobadas(filas)))


def test_libro_aprobadas():
    from datetime import datetime
    from decimal import Decimal

    base = {
        "banco": "BANCAMIGA", "fecha": date(2026, 11, 12), "referencia": "33577466",
        "descripcion": "NC Credito Inmediato", "moneda": "VES", "regla": "R2",
        "decidida_por": "ana@x.test", "decidida_en": datetime(2026, 11, 13, 10, 5, 7, 123),
        "comentario": "",
    }
    filas = [
        base | {"huella": "h1", "monto": Decimal("306118.02"), "tipo": "pago", "pago_id": 7,
                "pago_nombre": "PBAMI/2026/00973", "cuenta": "", "estado": "aprobada",
                "candidatos": [{"id": 7, "partner": "Cliente A", "fecha": "2026-11-12",
                                "ya_conciliado": True}]},
        base | {"huella": "h2", "monto": Decimal("-2435.85"), "tipo": "cuenta", "pago_id": None,
                "pago_nombre": "", "cuenta": "6.2.1.1.01.02 Bank Fees", "estado": "corregida",
                "candidatos": [], "regla": "R1", "comentario": "comisión"},
    ]
    filas.append(
        filas[0] | {"huella": "h3", "pago_id": 8, "pago_nombre": "PBAMI/2026/00974",
                    "candidatos": [{"id": 8, "partner": "Cliente B", "fecha": "2026-11-12"}]}
    )
    wb = _libro(filas)
    assert wb.sheetnames == ["Para aplicar", "Ya conciliadas en Odoo", "Resumen"]

    def tabla(nombre):
        ws = wb[nombre]
        enc = [c.value for c in ws[1]]
        return [dict(zip(enc, [c.value for c in r], strict=True)) for r in ws.iter_rows(min_row=2)]

    aplicar = tabla("Para aplicar")
    assert [f["Huella"] for f in aplicar] == ["h2", "h3"]
    cuenta, pago = aplicar
    assert (cuenta["Destino"], cuenta["Cuenta contable"], cuenta["Comentario"]) == (
        "Cuenta", "6.2.1.1.01.02 Bank Fees", "comisión"
    )
    assert cuenta["Diario Odoo"] == "Banco Bancamiga 7806"
    assert (pago["Destino"], pago["Pago Odoo"], pago["Cliente / proveedor"]) == (
        "Pago", "PBAMI/2026/00974", "Cliente B"
    )
    assert pago["Monto"] == 306118.02

    ya = tabla("Ya conciliadas en Odoo")
    assert [(f["Huella"], f["Pago Odoo"], f["Cliente / proveedor"]) for f in ya] == [
        ("h1", "PBAMI/2026/00973", "Cliente A")
    ]
    res = [[c.value for c in r] for r in wb["Resumen"].iter_rows(min_row=2)]
    assert res == [["BANCAMIGA", "Cuenta", 1, -2435.85, 0], ["BANCAMIGA", "Pago", 1, 306118.02, 1]]


def test_libro_aprobadas_vacio():
    assert _libro([])["Resumen"]["A2"].value.startswith("No hay propuestas")


def test_endpoint_aprobadas(sin_base):
    with _como(VENDEDOR):
        assert client.get("/api/bancos/conciliacion/aprobadas.xlsx").status_code == 403
    with _como(AUDITOR), patch("cxc.web.app.bancos_repo.aprobadas", return_value=[]) as ap:
        r = client.get(
            "/api/bancos/conciliacion/aprobadas.xlsx",
            params={"banco": 'BAN"CAMIGA', "desde": "2026-11-13", "hasta": "2026-11-14"},
        )
    assert r.status_code == 200
    assert ap.call_args.kwargs["decididas_desde"] == date(2026, 11, 13)
    assert ap.call_args.kwargs["decididas_hasta"] == date(2026, 11, 14)
    assert 'filename="aprobadas_para_odoo_bancamiga_' in r.headers["content-disposition"]
    with _como(AUDITOR):
        r = client.get("/api/bancos/conciliacion/aprobadas.xlsx", params={"desde": "ayer"})
    assert r.status_code == 422


def test_contactos_de_propuestas(engine):
    movs = _cargar(engine)
    props, _ = _generar(engine, movs)
    pago = next(p for p in props if p.tipo == PAGO)
    huellas = [mc.mov.huella for mc in movs]
    # Pendiente: vale el pago que proponen las reglas.
    assert repositorio.contactos_de_propuestas(engine, huellas) == {pago.huella: "Cliente A"}
    repositorio.decidir(engine, pago.huella, "rechazar", "ana", comentario="no es")
    assert repositorio.contactos_de_propuestas(engine, huellas) == {}
    assert repositorio.contactos_de_propuestas(engine, []) == {}


def _mov_csv(huella_desc: str) -> MovimientoClasificado:
    return MovimientoClasificado(
        MovimientoBancario(
            "BANCAMIGA", "7806", "VES", date(2026, 11, 12), "1", huella_desc,
            D("0"), D("10"), D("10"), 1,
        ),
        "transferencia",
        "r",
    )


def test_csv_odoo_contacto_de_la_cola_y_del_rif(sin_base):
    import csv
    import io

    a = _mov_csv("TRF RECIBIDA A")
    b = _mov_csv("TRF RECIBIDA J-50783614-2 EN ASCENSO")
    c = _mov_csv("TRF RECIBIDA V12670859 Y J507836142")  # dos RIF, dos contactos: vacío
    d = _mov_csv("COMPRA POS")
    por_rif = {"J507836142": "En ascenso 2011,c.a.", "V12670859": "Yanis"}
    with (
        _como(AUDITOR),
        patch("cxc.web.app.bancos_repo.movimientos", return_value=[a, b, c, d]),
        patch(
            "cxc.web.app.bancos_repo.contactos_de_propuestas",
            return_value={a.mov.huella: "Cliente A"},
        ),
        patch("cxc.web.app._connect", return_value=MagicMock()),
        patch(
            "cxc.web.app.bancos_conciliacion.contactos_por_rif", return_value=por_rif
        ) as cpr,
    ):
        r = client.get("/api/bancos/odoo-extracto.csv", params={"banco": "BANCAMIGA"})
    assert r.status_code == 200
    filas = list(csv.DictReader(io.StringIO(r.text)))
    assert [f["partner_id"] for f in filas] == ["Cliente A", "En ascenso 2011,c.a.", "", ""]
    assert cpr.call_args.args[1] == {"J507836142", "V12670859"}


def test_csv_odoo_sin_rifs_no_lee_odoo_y_si_odoo_falla_es_502(sin_base):
    con_rif = _mov_csv("TRF J507836142")
    with (
        _como(AUDITOR),
        patch("cxc.web.app.bancos_repo.movimientos", return_value=[_mov_csv("COMPRA POS")]),
        patch("cxc.web.app.bancos_repo.contactos_de_propuestas", return_value={}),
        patch("cxc.web.app._connect") as conn,
    ):
        assert client.get("/api/bancos/odoo-extracto.csv").status_code == 200
    conn.assert_not_called()
    with (
        _como(AUDITOR),
        patch("cxc.web.app.bancos_repo.movimientos", return_value=[con_rif]),
        patch("cxc.web.app.bancos_repo.contactos_de_propuestas", return_value={}),
        patch("cxc.web.app._connect", side_effect=PermissionError("Autenticación Odoo fallida")),
    ):
        r = client.get("/api/bancos/odoo-extracto.csv")
    assert r.status_code == 502 and "No pude leer Odoo" in r.json()["detail"]
