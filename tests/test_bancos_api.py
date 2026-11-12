"""Endpoints de bancos: permisos, errores de carga y salidas. La base va mockeada."""

from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal as D
from unittest.mock import MagicMock, patch

import openpyxl
import pytest
from fastapi.testclient import TestClient

from cxc.bancos.repositorio import ResumenIngesta
from cxc.bancos.saldos import Corte
from cxc.web.app import app

client = TestClient(app)

ADMIN = {"email": "admin@x.test", "rol": "admin", "permisos": []}
AUDITOR = {"email": "aud@x.test", "rol": "auditor", "permisos": []}
VENDEDOR = {"email": "v@x.test", "rol": "vendedor", "permisos": []}


def _como(usuario):
    return patch("cxc.web.app.get_current_user_from_cookie", return_value=usuario)


@pytest.fixture
def sin_base():
    with patch("cxc.web.app._engine_bancos", return_value=MagicMock()):
        yield


def _xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for f in [
        ["Fecha", "Referencia", "Concepto", "Débito", "Crédito", "Saldo"],
        ["01/09/2026", "1", "TRF CR INM CLIENTE", "", "500,00", "1.500,00"],
    ]:
        ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _subir(banco="VENEZUELA", contenido=None):
    return client.post(
        "/api/bancos/importar",
        data={"banco": banco},
        files={"archivo": ("edo.xlsx", contenido if contenido is not None else _xlsx())},
    )


@pytest.mark.parametrize("usuario,esperado", [(None, 401), (VENDEDOR, 403), (AUDITOR, 403)])
def test_importar_solo_admin(usuario, esperado, sin_base):
    with _como(usuario):
        assert _subir().status_code == esperado


@pytest.mark.parametrize("ruta", ["/api/bancos/saldos", "/api/bancos/movimientos"])
def test_lecturas_cerradas_a_vendedor(ruta, sin_base):
    with _como(VENDEDOR):
        assert client.get(ruta).status_code == 403


def test_importar_ok(sin_base):
    resumen = ResumenIngesta(
        7, "VENEZUELA", "edo.xlsx", False, 1, 1, True, "", date(2026, 9, 1), date(2026, 9, 1)
    )
    with _como(ADMIN), patch("cxc.web.app.bancos_repo.guardar", return_value=resumen) as g:
        r = _subir()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archivo_id"] == 7 and body["nuevos"] == 1 and body["desde"] == "2026-09-01"
    prep, usuario = g.call_args.args[1], g.call_args.args[2]
    assert prep.estado.banco == "VENEZUELA" and usuario == "admin@x.test"


@pytest.mark.parametrize(
    "banco,contenido,msg",
    [("INVENTADO", None, "Banco sin parser"), ("VENEZUELA", b"", "vacío")],
)
def test_importar_errores_de_archivo_son_422(banco, contenido, msg, sin_base):
    with _como(ADMIN):
        r = _subir(banco, contenido)
    assert r.status_code == 422 and msg in r.json()["detail"]


def test_saldos(sin_base):
    corte = Corte(
        1,
        "BANCAMIGA",
        "BANCAMIGA",
        "VES",
        "a.xlsx",
        date(2026, 9, 1),
        date(2026, 9, 30),
        D("0"),
        D("2000"),
        True,
        datetime(2026, 11, 1),
    )
    tasas = MagicMock()
    tasas.bcv_usd.return_value = D("200")
    with (
        _como(AUDITOR),
        patch("cxc.web.app.bancos_repo.cortes", return_value=[corte]),
        patch("cxc.web.app.tasas_vigentes", return_value=tasas),
    ):
        r = client.get("/api/bancos/saldos")
    assert r.status_code == 200
    body = r.json()
    assert body["total_usd"] == 10.0 and body["cuentas"][0]["fecha_corte"] == "2026-09-30"


def test_movimientos_fecha_invalida(sin_base):
    with _como(ADMIN):
        assert client.get("/api/bancos/movimientos?desde=30-09-2026").status_code == 422


def test_csv_odoo(sin_base):
    from cxc.bancos.ingesta import MovimientoClasificado
    from cxc.bancos.modelos import MovimientoBancario

    m = MovimientoBancario(
        "BDT", "BDT", "VES", date(2026, 10, 1), "9", "X", D("0"), D("5"), D("5"), 1
    )
    with (
        _como(ADMIN),
        patch(
            "cxc.web.app.bancos_repo.movimientos",
            return_value=[MovimientoClasificado(m, "c", "r")],
        ) as mov,
    ):
        r = client.get("/api/bancos/odoo-extracto.csv?banco=BDT&hasta=2026-10-31")
    assert r.status_code == 200
    assert "Banco Digital de los Trabajadores" in r.text and r.text.strip().endswith(",5.00")
    assert mov.call_args.kwargs["hasta"] == date(2026, 10, 31)
    assert 'filename="odoo_extracto_bdt.csv"' in r.headers["content-disposition"]


def test_pagina_bancos_solo_para_admin_y_auditor():
    from cxc.auth import ROLES_PERMISOS

    con_bancos = {rol for rol, paginas in ROLES_PERMISOS.items() if "bancos" in paginas}
    assert con_bancos == {"admin", "auditor"}


@pytest.mark.parametrize("usuario,esperado", [(AUDITOR, 200), (None, 307)])
def test_ruta_bancos_sirve_la_spa(usuario, esperado):
    usuario = None if usuario is None else {**usuario, "permisos": ["bancos"]}
    with _como(usuario):
        r = client.get("/bancos", follow_redirects=False)
    assert r.status_code == esperado
    if esperado == 200:
        assert 'id="tab-bancos"' in r.text
