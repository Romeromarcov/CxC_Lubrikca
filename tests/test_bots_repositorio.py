"""Persistencia del panel de bots contra Postgres real (se salta sin DATABASE_URL).

Las tablas las crea ``alembic upgrade head`` (como en CI) para probar también el trigger que hace la
bitácora solo-append.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import Engine, insert, text
from sqlalchemy.exc import DBAPIError

from cxc.bots.modelos import UsuarioBot
from cxc.bots.repositorio import RepoBotsSQL
from cxc.db import schema
from cxc.db.engine import make_engine

_URL = os.environ.get("DATABASE_URL", "")
T0 = datetime(2026, 11, 14, 12, 0, 0)
_TABLAS = "bots_latidos, bots_usuarios, bots_registro"


def _limpiar(eng: Engine) -> None:
    with eng.begin() as c:
        c.execute(text("ALTER TABLE bots_bitacora DISABLE TRIGGER trg_bots_bitacora_inmutable"))
        c.execute(text(f"TRUNCATE {_TABLAS}, bots_bitacora RESTART IDENTITY"))
        c.execute(text("ALTER TABLE bots_bitacora ENABLE TRIGGER trg_bots_bitacora_inmutable"))
        c.execute(text("TRUNCATE capturas_bot_eventos, capturas_bot RESTART IDENTITY CASCADE"))


@pytest.fixture
def engine() -> Iterator[Engine]:
    if not _URL:
        pytest.skip("Sin DATABASE_URL: el panel de bots necesita Postgres.")
    eng = make_engine(_URL)
    schema.metadata.create_all(
        eng,
        tables=[
            schema.bots_registro,
            schema.bots_usuarios,
            schema.bots_latidos,
            schema.bots_bitacora,
            schema.capturas_bot,
            schema.capturas_bot_eventos,
        ],
        checkfirst=True,
    )
    with eng.begin() as c:  # el trigger lo crea la migración; aquí se garantiza para probarlo
        c.execute(
            text(
                """
                CREATE OR REPLACE FUNCTION bots_bitacora_inmutable() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'bots_bitacora es solo-append: no se modifica'; END;
                $$ LANGUAGE plpgsql
                """
            )
        )
        c.execute(text("DROP TRIGGER IF EXISTS trg_bots_bitacora_inmutable ON bots_bitacora"))
        c.execute(
            text(
                "CREATE TRIGGER trg_bots_bitacora_inmutable "
                "BEFORE UPDATE OR DELETE ON bots_bitacora "
                "FOR EACH ROW EXECUTE FUNCTION bots_bitacora_inmutable()"
            )
        )
    _limpiar(eng)
    yield eng
    _limpiar(eng)
    eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> RepoBotsSQL:
    return RepoBotsSQL(engine)


def test_sin_registro_no_hay_bot(repo: RepoBotsSQL) -> None:
    assert repo.obtener("pedidos") is None and repo.listar() == []


def test_el_latido_crea_el_registro_con_valores_por_defecto(repo: RepoBotsSQL) -> None:
    b = repo.registrar_latido(
        "pedidos", nombre="Ventas", version="1.0", estado={"omni": "ok"}, ahora=T0
    )
    assert (
        b.habilitado and b.ultimo_latido == T0 and b.version == "1.0" and b.estado == {"omni": "ok"}
    )
    assert b.parametros == {"auto_envio_min": 10, "gemini_model": "gemini-2.5-flash"}
    assert b.chats == {"respaldo": None, "grupo": None} and b.nombre == "Ventas"


def test_el_latido_no_pisa_la_configuracion_del_panel(repo: RepoBotsSQL) -> None:
    repo.registrar_latido("cobranza", nombre="Cobranzas", version="1", estado={}, ahora=T0)
    repo.guardar_config(
        "cobranza", {"habilitado": False, "mensaje": "Pausa"}, por="admin", ahora=T0
    )
    b = repo.registrar_latido(
        "cobranza",
        nombre="Cobranzas",
        version="2",
        estado={"omni": "ok"},
        ahora=T0 + timedelta(minutes=1),
    )
    assert b.habilitado is False and b.mensaje == "Pausa" and b.version == "2"


def test_guardar_config_devuelve_antes_y_despues_y_combina(repo: RepoBotsSQL) -> None:
    antes, despues = repo.guardar_config(
        "compras",
        {
            "parametros": {"auto_envio_min": 30},
            "chats": {"grupo": -100},
            "url_panel": "https://x.com",
        },
        por="ana",
        ahora=T0,
    )
    assert antes.parametros["auto_envio_min"] == 10 and despues.parametros["auto_envio_min"] == 30
    assert despues.parametros["gemini_model"] == "gemini-2.5-flash"  # lo no tocado se conserva
    assert despues.chats == {"respaldo": None, "grupo": -100} and despues.actualizado_por == "ana"
    _, d2 = repo.guardar_config("compras", {"chats": {"respaldo": -200}}, por="ana", ahora=T0)
    assert d2.chats == {"respaldo": -200, "grupo": -100}


def test_listar_devuelve_solo_los_registrados(repo: RepoBotsSQL) -> None:
    repo.registrar_latido("pedidos", nombre="V", version="", estado={}, ahora=T0)
    repo.registrar_latido("asana", nombre="A", version="", estado={}, ahora=T0)
    assert sorted(b.bot for b in repo.listar()) == ["asana", "pedidos"]


def test_contadores_diarios(repo: RepoBotsSQL) -> None:
    hoy = date.today()
    ahora = datetime.combine(hoy, datetime.min.time()) + timedelta(hours=10)
    repo.registrar_latido(
        "pedidos", nombre="V", version="", estado={"mensajes_hoy": 5, "errores_hoy": 1}, ahora=ahora
    )
    repo.registrar_latido(
        "pedidos", nombre="V", version="", estado={"mensajes_hoy": 9, "errores_hoy": 1}, ahora=ahora
    )
    repo.registrar_latido(
        "pedidos", nombre="V", version="", estado={"mensajes_hoy": 2, "errores_hoy": 0}, ahora=ahora
    )  # reinicio
    (h,) = repo.historial("pedidos")
    assert h == {"dia": hoy.isoformat(), "latidos": 3, "mensajes": 9, "errores": 1}


def test_los_latidos_viejos_se_podan(repo: RepoBotsSQL, engine: Engine) -> None:
    viejo = date.today() - timedelta(days=45)
    with engine.begin() as c:
        c.execute(
            insert(schema.bots_latidos).values(
                bot="pedidos", dia=viejo, latidos=1, mensajes=1, errores=0, ultimo=T0
            )
        )
    repo.registrar_latido("pedidos", nombre="V", version="", estado={}, ahora=datetime.now())
    with engine.connect() as c:
        dias = [r.dia for r in c.execute(text("SELECT dia FROM bots_latidos")).all()]
    assert viejo not in dias and len(dias) == 1


def test_usuarios_alta_actualizacion_y_baja(repo: RepoBotsSQL) -> None:
    antes, u = repo.guardar_usuario(
        UsuarioBot(5269337388, "m@x.com", "Marco", ["pedidos", "compras"]), por="admin", ahora=T0
    )
    assert (
        antes is None
        and u.telegram_id == 5269337388
        and u.bots == ["pedidos", "compras"]
        and u.creado_por == "admin"
    )
    antes2, u2 = repo.guardar_usuario(
        UsuarioBot(5269337388, "m@x.com", "Marco R.", ["pedidos"]),
        por="otro",
        ahora=T0 + timedelta(hours=1),
    )
    assert (
        antes2 is not None
        and antes2.nombre == "Marco"
        and u2.nombre == "Marco R."
        and u2.creado_por == "admin"
    )
    assert [x.telegram_id for x in repo.usuarios()] == [5269337388]
    assert repo.borrar_usuario(5269337388).nombre == "Marco R."  # type: ignore[union-attr]
    assert repo.borrar_usuario(5269337388) is None and repo.usuarios() == []


def test_telegram_id_grande_cabe(repo: RepoBotsSQL) -> None:
    _, u = repo.guardar_usuario(
        UsuarioBot(9_999_999_999_999, "", "Grande", ["eventos"]), por="a", ahora=T0
    )
    assert repo.usuarios()[0].telegram_id == 9_999_999_999_999 == u.telegram_id


def test_bitacora_guarda_antes_y_despues_y_filtra_por_entidad(repo: RepoBotsSQL) -> None:
    repo.bitacora_agregar(
        "bot:pedidos", "actualizar", {"habilitado": True}, {"habilitado": False}, "ana", T0
    )
    repo.bitacora_agregar("usuario:7", "alta", {}, {"telegram_id": 7}, "ana", T0)
    todo = repo.bitacora()
    assert [c["entidad"] for c in todo] == ["usuario:7", "bot:pedidos"]  # lo más reciente primero
    (solo,) = repo.bitacora("bot:pedidos")
    assert (
        solo["antes"] == {"habilitado": True}
        and solo["despues"] == {"habilitado": False}
        and solo["usuario"] == "ana"
    )


def test_la_bitacora_es_solo_append(repo: RepoBotsSQL, engine: Engine) -> None:
    repo.bitacora_agregar("bot:pedidos", "actualizar", {}, {}, "ana", T0)
    for sentencia in ("UPDATE bots_bitacora SET usuario = 'otro'", "DELETE FROM bots_bitacora"):
        with pytest.raises(DBAPIError, match="solo-append"), engine.begin() as c:
            c.execute(text(sentencia))
    assert len(repo.bitacora()) == 1


def _captura(
    engine: Engine, kind: str, estado: str, clave: str, vendedor: str = "ana@x.com"
) -> None:
    with engine.begin() as c:
        c.execute(
            insert(schema.capturas_bot).values(
                kind=kind,
                idempotency_key=clave,
                estado=estado,
                payload="{}",
                fuente=f'{{"vendedor_email": "{vendedor}"}}',
                auto=False,
                incompleto='["referencia"]',
                resultado="{}",
                odoo_ref="",
                error="",
                intentos=0,
                creada_en=T0,
                actualizada_en=T0,
            )
        )


def test_capturas_recientes_filtra_por_tipos_del_bot(repo: RepoBotsSQL, engine: Engine) -> None:
    _captura(engine, "pago", "ERROR", "a")
    _captura(engine, "pedido", "ENVIADO", "b")
    _captura(engine, "factura_proveedor", "INCOMPLETO", "c")
    _captura(engine, "pago_proveedor", "PENDIENTE", "d")
    assert [c["kind"] for c in repo.capturas_recientes(("pago",))] == ["pago"]
    assert sorted(
        c["kind"]
        for c in repo.capturas_recientes(("pago_proveedor", "factura_proveedor", "proveedor"))
    ) == [
        "factura_proveedor",
        "pago_proveedor",
    ]
    assert repo.capturas_recientes(()) == []
    c = repo.capturas_recientes(("pago",))[0]
    assert (
        c["vendedor"] == "ana@x.com"
        and c["incompleto"] == ["referencia"]
        and c["estado"] == "ERROR"
    )


def test_conteo_de_capturas_por_estado(repo: RepoBotsSQL, engine: Engine) -> None:
    for i, e in enumerate(["ERROR", "ERROR", "INCOMPLETO", "ENVIADO"]):
        _captura(engine, "pago", e, f"k{i}")
    assert repo.conteo_capturas() == {"ERROR": 2, "INCOMPLETO": 1, "ENVIADO": 1}
