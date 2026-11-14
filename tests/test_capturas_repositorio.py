"""Persistencia de las capturas contra Postgres real (se salta sin DATABASE_URL).

Las tablas las crea la migración (``alembic upgrade head``, como en CI): así se prueba también el
trigger que hace la bitácora solo-append.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, delete, insert, text
from sqlalchemy.exc import DBAPIError

from cxc.capturas.espejo import EspejoSQL
from cxc.capturas.modelos import ENVIADO, ERROR, INCOMPLETO, PENDIENTE
from cxc.capturas.repositorio import RepoCapturasSQL
from cxc.db import schema
from cxc.db.engine import make_engine

_URL = os.environ.get("DATABASE_URL", "")


@pytest.fixture
def engine() -> Iterator[Engine]:
    if not _URL:
        pytest.skip("Sin DATABASE_URL: las capturas necesitan Postgres.")
    eng = make_engine(_URL)
    schema.metadata.create_all(
        eng, tables=[schema.capturas_bot, schema.capturas_bot_eventos], checkfirst=True
    )
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE capturas_bot_eventos, capturas_bot RESTART IDENTITY CASCADE"))
    yield eng
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE capturas_bot_eventos, capturas_bot RESTART IDENTITY CASCADE"))
    eng.dispose()


def _crear(repo: RepoCapturasSQL, clave: str = "k1", **kw: Any) -> Any:
    args: dict[str, Any] = {
        "kind": "pago",
        "idempotency_key": clave,
        "payload": {"monto": "5"},
        "fuente": {"vendedor_email": "ana@x.com"},
        "auto": False,
        "estado": PENDIENTE,
        "incompleto": [],
    }
    args.update(kw)
    return repo.crear(**args)


def test_crear_es_idempotente_por_clave(engine: Engine) -> None:
    repo = RepoCapturasSQL(engine)
    c1, creada1 = _crear(repo)
    c2, creada2 = _crear(repo, payload={"monto": "999"})
    assert creada1 and not creada2 and c1.captura_id == c2.captura_id
    assert c2.payload == {"monto": "5"}  # la repetición no pisa lo guardado
    assert [e["evento"] for e in repo.eventos(c1.captura_id)] == ["recibida"]


def test_ida_y_vuelta_de_json_y_campos(engine: Engine) -> None:
    repo = RepoCapturasSQL(engine)
    c, _ = _crear(
        repo,
        payload={"cliente": "Ñandú — «X»", "lineas": [{"producto": "A", "cantidad": "2"}]},
        fuente={"auto": True},
        auto=True,
        estado=INCOMPLETO,
        incompleto=["referencia"],
    )
    leida = repo.obtener(c.captura_id)
    assert leida is not None
    assert (
        leida.payload["cliente"] == "Ñandú — «X»"
        and leida.auto
        and leida.incompleto == ["referencia"]
    )
    assert (
        leida.estado == INCOMPLETO
        and leida.intentos == 0
        and leida.resultado == {}
        and leida.odoo_ref == ""
    )
    assert repo.obtener(9999) is None


def test_actualizar_y_contar_intentos(engine: Engine) -> None:
    repo = RepoCapturasSQL(engine)
    c, _ = _crear(repo)
    a = repo.actualizar(c.captura_id, sumar_intento=True)
    b = repo.actualizar(
        c.captura_id,
        estado=ENVIADO,
        odoo_ref="account.payment:5",
        incompleto=[],
        resultado={"partner_id": 3},
        error="",
        sumar_intento=True,
    )
    assert a.intentos == 1 and b.intentos == 2 and b.estado == ENVIADO
    assert b.odoo_ref == "account.payment:5" and b.resultado == {"partner_id": 3}
    assert b.actualizada_en >= c.actualizada_en
    c3 = repo.actualizar(c.captura_id, payload={"monto": "7"}, error="x" * 5000)
    assert c3.payload == {"monto": "7"} and len(c3.error) == 2000


def test_listar_filtra_ordena_y_pagina(engine: Engine) -> None:
    repo = RepoCapturasSQL(engine)
    for i in range(5):
        _crear(
            repo,
            f"k{i}",
            kind="contacto" if i % 2 else "pago",
            estado=ENVIADO if i == 4 else PENDIENTE,
        )
    todos, total = repo.listar(estado=None, kind=None, limit=10, offset=0)
    assert total == 5 and [c.captura_id for c in todos] == [5, 4, 3, 2, 1]
    assert repo.listar(estado=ENVIADO, kind=None, limit=10, offset=0)[1] == 1
    assert repo.listar(estado=None, kind="contacto", limit=10, offset=0)[1] == 2
    pagina, total = repo.listar(estado=None, kind=None, limit=2, offset=2)
    assert total == 5 and [c.captura_id for c in pagina] == [3, 2]


def test_reintentables_solo_error_transitorio_y_pendiente_con_intentos(engine: Engine) -> None:
    repo = RepoCapturasSQL(engine)
    pend, _ = _crear(repo, "p")
    trans, _ = _crear(repo, "t")
    perm, _ = _crear(repo, "m")
    agot, _ = _crear(repo, "a")
    inc, _ = _crear(repo, "i", estado=INCOMPLETO)
    repo.actualizar(
        trans.captura_id, estado=ERROR, resultado={"transitorio": True}, sumar_intento=True
    )
    repo.actualizar(
        perm.captura_id, estado=ERROR, resultado={"transitorio": False}, sumar_intento=True
    )
    for _ in range(5):
        repo.actualizar(
            agot.captura_id, estado=ERROR, resultado={"transitorio": True}, sumar_intento=True
        )
    ids = {c.captura_id for c in repo.reintentables(max_intentos=5)}
    assert ids == {pend.captura_id, trans.captura_id}
    assert inc.captura_id not in ids and perm.captura_id not in ids and agot.captura_id not in ids


def test_bitacora_es_solo_append(engine: Engine) -> None:
    repo = RepoCapturasSQL(engine)
    c, _ = _crear(repo)
    repo.evento(c.captura_id, "intento_envio", "pago", "sistema")
    repo.evento(c.captura_id, "creada_en_odoo", "account.payment:5", "sistema")
    ev = repo.eventos(c.captura_id)
    assert [e["evento"] for e in ev] == ["recibida", "intento_envio", "creada_en_odoo"]
    assert ev[0]["actor"] == "ana@x.com"
    with pytest.raises(DBAPIError), engine.begin() as conn:
        conn.execute(text("UPDATE capturas_bot_eventos SET detalle = 'x'"))
    with pytest.raises(DBAPIError), engine.begin() as conn:
        conn.execute(text("DELETE FROM capturas_bot_eventos"))


def test_idempotency_key_es_unica_en_la_base(engine: Engine) -> None:
    repo = RepoCapturasSQL(engine)
    _crear(repo)
    with pytest.raises(DBAPIError), engine.begin() as conn:
        conn.execute(
            insert(schema.capturas_bot).values(
                kind="pago",
                idempotency_key="k1",
                payload="{}",
                creada_en=text("now()"),
                actualizada_en=text("now()"),
            )
        )


# ── espejo ───────────────────────────────────────────────────────────────────


@pytest.fixture
def espejo(engine: Engine) -> Iterator[EspejoSQL]:
    schema.metadata.create_all(
        engine, tables=[schema.clientes, schema.ordenes_venta, schema.catalogo], checkfirst=True
    )
    from datetime import date
    from decimal import Decimal

    with engine.begin() as conn:
        conn.execute(
            insert(schema.clientes),
            [
                {"cliente_id": "9001", "nombre": "Taller El Rápido"},
                {"cliente_id": "9002", "nombre": "Duplicado"},
                {"cliente_id": "9003", "nombre": "duplicado"},
            ],
        )
        conn.execute(
            insert(schema.ordenes_venta).values(
                so_id="S99001",
                cliente_id="9001",
                fecha=date(2026, 11, 1),
                monto_total=Decimal("10"),
            )
        )
        conn.execute(
            insert(schema.catalogo),
            [
                {"producto_id": "8001", "codigo": "AC-20W50", "nombre": "Aceite 20W50 1L"},
                {"producto_id": "8002", "codigo": "", "nombre": "Filtro de aire"},
            ],
        )
    yield EspejoSQL(engine)
    with engine.begin() as conn:
        conn.execute(
            delete(schema.catalogo).where(schema.catalogo.c.producto_id.in_(["8001", "8002"]))
        )
        conn.execute(delete(schema.ordenes_venta).where(schema.ordenes_venta.c.so_id == "S99001"))
        conn.execute(
            delete(schema.clientes).where(
                schema.clientes.c.cliente_id.in_(["9001", "9002", "9003"])
            )
        )


def test_espejo_cliente_por_nombre_exacto_sin_mayusculas(espejo: EspejoSQL) -> None:
    assert espejo.clientes_por_nombre("  taller el rápido ") == ["9001"]
    assert sorted(espejo.clientes_por_nombre("DUPLICADO")) == ["9002", "9003"]
    assert espejo.clientes_por_nombre("Taller") == []  # no es búsqueda parcial


def test_espejo_cliente_de_una_orden(espejo: EspejoSQL) -> None:
    assert espejo.cliente_de_orden("s99001") == "9001"
    assert espejo.cliente_de_orden("S00000") is None


def test_espejo_producto_por_codigo_y_luego_por_nombre(espejo: EspejoSQL) -> None:
    assert espejo.productos("ac-20w50") == ["8001"]
    assert espejo.productos("Filtro de aire") == ["8002"]
    assert espejo.productos("Aceite") == []  # solo igualdad exacta: nunca adivina
