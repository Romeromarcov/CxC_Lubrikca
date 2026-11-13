"""Persistencia de la ingesta bancaria contra Postgres real (se salta sin DATABASE_URL)."""

from __future__ import annotations

import io
import os
from typing import Any

import openpyxl
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cxc.bancos import repositorio
from cxc.bancos.ingesta import preparar
from cxc.db import schema
from cxc.db.engine import make_engine

_URL = os.environ.get("DATABASE_URL", "")


@pytest.fixture
def engine():
    if not _URL:
        pytest.skip("Sin DATABASE_URL: la ingesta bancaria necesita Postgres.")
    eng = make_engine(_URL)
    schema.metadata.create_all(eng, tables=[schema.bancos_archivos, schema.bancos_movimientos])
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE bancos_movimientos, bancos_archivos RESTART IDENTITY CASCADE"))
    yield eng
    eng.dispose()


def _xlsx(filas: list[list[Any]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Fecha", "Referencia", "Concepto", "Débito", "Crédito", "Saldo"])
    for f in filas:
        ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


SEP_1 = ["01/09/2026", "1", "TRF CR INM CLIENTE A", "", "500,00", "1.500,00"]
SEP_2 = ["02/09/2026", "2", "COM MANTENIMIENTO DE CUENTA", "54,00", "", "1.446,00"]
SEP_3 = ["03/09/2026", "3", "TRF CR INM CLIENTE B", "", "54,00", "1.500,00"]


def test_guardar_es_idempotente_por_archivo(engine):
    prep = preparar(_xlsx([SEP_1, SEP_2]), "a.xlsx", "VENEZUELA")
    r1 = repositorio.guardar(engine, prep, "admin@x.test")
    r2 = repositorio.guardar(engine, prep, "admin@x.test")
    assert (r1.ya_cargado, r1.n_nuevos, r1.cuadra) == (False, 2, True)
    assert r2.ya_cargado and r2.archivo_id == r1.archivo_id
    assert len(repositorio.movimientos(engine)) == 2


def test_corte_solapado_solo_agrega_lo_nuevo(engine):
    repositorio.guardar(engine, preparar(_xlsx([SEP_1, SEP_2]), "a.xlsx", "VENEZUELA"), "u")
    r = repositorio.guardar(engine, preparar(_xlsx([SEP_2, SEP_3]), "b.xlsx", "VENEZUELA"), "u")
    assert (r.n_movimientos, r.n_nuevos) == (2, 1)
    movs = repositorio.movimientos(engine, banco="venezuela")
    assert [m.mov.referencia for m in movs] == ["1", "2", "3"]
    assert [m.categoria for m in movs][1] == "comision_bancaria"
    cortes = repositorio.cortes(engine)
    assert [c.saldo_final for c in cortes] == [1446, 1500]
    archivos = repositorio.archivos(engine)
    assert "contenido" not in archivos[0] and archivos[0]["nombre_archivo"] == "b.xlsx"


def test_filtros_de_movimientos(engine):
    repositorio.guardar(engine, preparar(_xlsx([SEP_1, SEP_2, SEP_3]), "a.xlsx", "BANESCO"), "u")
    from datetime import date

    assert len(repositorio.movimientos(engine, desde=date(2026, 9, 2))) == 2
    assert len(repositorio.movimientos(engine, hasta=date(2026, 9, 1))) == 1
    assert len(repositorio.movimientos(engine, categoria="comision_bancaria")) == 1
    assert repositorio.movimientos(engine, banco="BDT") == []


def test_archivo_crudo_es_inmutable_si_la_migracion_corrio(engine):
    with engine.connect() as conn:
        hay_trigger = conn.execute(
            text("SELECT 1 FROM pg_trigger WHERE tgname = 'trg_bancos_archivos_inmutable'")
        ).first()
    if not hay_trigger:
        pytest.skip("Tablas creadas sin alembic: el trigger vive en la migración.")
    repositorio.guardar(engine, preparar(_xlsx([SEP_1]), "a.xlsx", "VENEZUELA"), "u")
    with pytest.raises(DBAPIError, match="solo-append"), engine.begin() as conn:
        conn.execute(text("UPDATE bancos_archivos SET cargado_por = 'otro'"))
