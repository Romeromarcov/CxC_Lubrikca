"""Tests de contrato de ``Repository`` -- ``InMemoryRepository`` y

``PostgresRepository`` deben cumplir exactamente la misma semántica (el
motor de descuentos, el sync incremental y el reconciliador corren sobre
cualquiera de las dos implementaciones sin cambios de código). Cada test
corre una vez por backend vía el fixture ``repo``.

Los tests contra Postgres necesitan una base real -- si ``DATABASE_URL`` no
está configurada (fuera de CI, sin Postgres local) se saltan en vez de
fallar; CI trae un servicio Postgres (ver .github/workflows/ci.yml) así que
ahí siempre corren contra una base real, no solo InMemoryRepository.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from cxc.db import schema as db_schema
from cxc.db.postgres_repository import PostgresRepository
from cxc.models import (
    BandejaFacturacion,
    Cliente,
    Conciliacion,
    DescuentoAplicado,
    EstadoBandeja,
    EstadoVinculacion,
    ExclusionRegla,
    LineaOrden,
    Moneda,
    OrdenVenta,
    Pago,
    ResultadoConciliacion,
    SerieTasa,
    Vinculacion,
)
from cxc.repositories import InMemoryRepository, Repository

_DATABASE_URL = os.environ.get("DATABASE_URL")


def _make_postgres_repo() -> PostgresRepository:
    assert _DATABASE_URL is not None
    repo = PostgresRepository.from_url(_DATABASE_URL)
    db_schema.metadata.create_all(repo._engine)
    table_names = ", ".join(tbl.name for tbl in db_schema.metadata.sorted_tables)
    with repo._engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
    return repo


@pytest.fixture(params=["memory", "postgres"])
def repo(request: pytest.FixtureRequest) -> Iterator[Repository]:
    if request.param == "postgres":
        if not _DATABASE_URL:
            pytest.skip("DATABASE_URL no configurado -- se salta el backend Postgres")
        yield _make_postgres_repo()
    else:
        yield InMemoryRepository()


def test_clientes_ordenes_lineas_pagos_upsert_y_lectura(repo: Repository) -> None:
    repo.upsert_clientes(
        [Cliente(cliente_id="C1", nombre="Cliente Uno", vendedor_email="v@x.com")]
    )
    assert repo.get_cliente("C1") is not None
    assert repo.get_cliente("C1").nombre == "Cliente Uno"  # type: ignore[union-attr]
    assert repo.get_cliente("NOPE") is None

    repo.upsert_ordenes(
        [
            OrdenVenta(
                so_id="SO1",
                cliente_id="C1",
                fecha=date(2026, 1, 1),
                fecha_entrega=None,
                monto_total=Decimal("100.50"),
                lista_precios="4",
                vendedor_email="v@x.com",
                es_primera_compra=True,
            )
        ]
    )
    orden = repo.get_orden("SO1")
    assert orden is not None
    assert orden.monto_total == Decimal("100.50")
    assert repo.all_ordenes() == [orden]

    # Upsert repetido con un cambio -- debe actualizar, no duplicar.
    repo.upsert_ordenes(
        [
            OrdenVenta(
                so_id="SO1",
                cliente_id="C1",
                fecha=date(2026, 1, 1),
                fecha_entrega=date(2026, 1, 5),
                monto_total=Decimal("100.50"),
                lista_precios="4",
                vendedor_email="v@x.com",
                es_primera_compra=True,
            )
        ]
    )
    assert len(repo.all_ordenes()) == 1
    assert repo.get_orden("SO1").fecha_entrega == date(2026, 1, 5)  # type: ignore[union-attr]

    repo.upsert_lineas(
        [
            LineaOrden(
                linea_id="L1",
                so_id="SO1",
                producto="P1",
                marca="X",
                categoria="Y",
                cantidad=Decimal("10"),
                precio_unitario=Decimal("5"),
            )
        ]
    )
    lineas = repo.lineas_de_orden("SO1")
    assert len(lineas) == 1
    assert lineas[0].cantidad == Decimal("10")
    assert repo.lineas_de_orden("SO_OTRA") == []

    repo.upsert_pagos(
        [
            Pago(
                pago_id="P1",
                cliente_id="C1",
                monto=Decimal("50"),
                moneda=Moneda.USD,
                metodo_pago="M1",
                fecha_pago=datetime(2026, 1, 1, 12, 0, 0),
                vendedor_email="v@x.com",
            )
        ]
    )
    pago = repo.get_pago("P1")
    assert pago is not None
    assert pago.monto == Decimal("50")
    assert pago.fecha_pago == datetime(2026, 1, 1, 12, 0, 0)


def test_vinculaciones_update_individual_y_lote(repo: Repository) -> None:
    _seed_cliente_orden_pago(repo)
    v1 = Vinculacion(
        vinc_id="V1",
        pago_id="P1",
        so_id="SO1",
        monto_aplicado=Decimal("30"),
        hora_pago_confirmada=datetime(2026, 1, 1, 12, 0, 0),
        tasa_bcv_aplicada=Decimal("36.5"),
        tasa_binance_aplicada=Decimal("38.0"),
        es_tasa_heredada=False,
        estado=EstadoVinculacion.PENDIENTE,
    )
    repo.update_vinculacion(v1)
    assert repo.vinculaciones_de_orden("SO1")[0].estado == EstadoVinculacion.PENDIENTE

    v1_conciliada = Vinculacion(
        vinc_id="V1",
        pago_id="P1",
        so_id="SO1",
        monto_aplicado=Decimal("30"),
        hora_pago_confirmada=datetime(2026, 1, 1, 12, 0, 0),
        tasa_bcv_aplicada=Decimal("36.5"),
        tasa_binance_aplicada=Decimal("38.0"),
        es_tasa_heredada=False,
        estado=EstadoVinculacion.CONCILIADO,
    )
    v2 = Vinculacion(
        vinc_id="V2",
        pago_id="P1",
        so_id="SO1",
        monto_aplicado=Decimal("20"),
        hora_pago_confirmada=datetime(2026, 1, 1, 12, 0, 0),
        tasa_bcv_aplicada=Decimal("36.5"),
        tasa_binance_aplicada=Decimal("38.0"),
        es_tasa_heredada=False,
        estado=EstadoVinculacion.CONCILIADO,
    )
    repo.update_vinculaciones([v1_conciliada, v2])
    vincs = {v.vinc_id: v for v in repo.all_vinculaciones()}
    assert len(vincs) == 2
    assert vincs["V1"].estado == EstadoVinculacion.CONCILIADO
    assert vincs["V2"].monto_aplicado == Decimal("20")

    # Lote vacío es un no-op seguro (no debe fallar ni borrar nada).
    repo.update_vinculaciones([])
    assert len(repo.all_vinculaciones()) == 2


def test_bandeja_reemplaza_descuentos_detalle_en_cada_upsert(repo: Repository) -> None:
    _seed_cliente_orden_pago(repo)
    bandeja = BandejaFacturacion(
        so_id="SO1",
        lista_aplicada="4",
        precio_base_calculado=Decimal("100"),
        descuentos_detalle=[
            DescuentoAplicado(origen="contado", descripcion="pronto pago", monto=Decimal("5")),
            DescuentoAplicado(origen="recurrencia", descripcion="recompra", monto=Decimal("3")),
        ],
        estado=EstadoBandeja.CALCULADO,
    )
    repo.upsert_bandeja(bandeja)
    leida = repo.get_bandeja("SO1")
    assert leida is not None
    assert len(leida.descuentos_detalle) == 2
    assert {d.monto for d in leida.descuentos_detalle} == {Decimal("5"), Decimal("3")}

    # Recalcular con un desglose distinto (típico: el motor corrió de nuevo)
    # debe REEMPLAZAR el detalle anterior, no acumularlo.
    bandeja_v2 = BandejaFacturacion(
        so_id="SO1",
        lista_aplicada="4",
        precio_base_calculado=Decimal("100"),
        descuentos_detalle=[
            DescuentoAplicado(origen="contado", descripcion="pronto pago", monto=Decimal("5")),
        ],
        estado=EstadoBandeja.CALCULADO,
    )
    repo.upsert_bandejas([bandeja_v2])
    leida_v2 = repo.get_bandeja("SO1")
    assert leida_v2 is not None
    assert len(leida_v2.descuentos_detalle) == 1
    assert repo.all_bandeja() == [leida_v2]

    assert repo.get_bandeja("NOPE") is None


def test_conciliacion_upsert_individual_y_lote(repo: Repository) -> None:
    _seed_cliente_orden_pago(repo)
    repo.upsert_conciliacion(
        Conciliacion(
            so_id="SO1",
            total_motor=Decimal("95"),
            monto_odoo=Decimal("95"),
            ncs_odoo=Decimal("0"),
            diferencia=Decimal("0"),
            resultado=ResultadoConciliacion.VERDE,
        )
    )
    concs = repo.all_conciliaciones()
    assert len(concs) == 1
    assert concs[0].resultado == ResultadoConciliacion.VERDE

    repo.upsert_conciliaciones(
        [
            Conciliacion(
                so_id="SO1",
                total_motor=Decimal("95"),
                monto_odoo=Decimal("90"),
                ncs_odoo=Decimal("0"),
                diferencia=Decimal("5"),
                resultado=ResultadoConciliacion.ROJO,
            )
        ]
    )
    concs2 = repo.all_conciliaciones()
    assert len(concs2) == 1
    assert concs2[0].resultado == ResultadoConciliacion.ROJO


def test_serie_tasas_append_only_orden_y_capturas_fallidas(repo: Repository) -> None:
    assert repo.last_serie_tasa() is None
    assert repo.trailing_failed_captures() == 0

    repo.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 1, 1, 9, 0, 0),
            tasa_bcv=Decimal("36.5"),
            tasa_binance=Decimal("38.0"),
            fuente="test",
            capturada_ok=True,
        )
    )
    repo.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 1, 1, 13, 0, 0),
            tasa_bcv=Decimal("36.6"),
            tasa_binance=Decimal("38.1"),
            fuente="test",
            capturada_ok=False,
        )
    )
    repo.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 1, 2, 9, 0, 0),
            tasa_bcv=Decimal("36.7"),
            tasa_binance=Decimal("38.2"),
            fuente="test",
            capturada_ok=False,
        )
    )

    ultima = repo.last_serie_tasa()
    assert ultima is not None
    assert ultima.tasa_bcv == Decimal("36.7")
    assert repo.trailing_failed_captures() == 2

    del_dia1 = repo.serie_tasas_del_dia(date(2026, 1, 1))
    assert len(del_dia1) == 2
    assert repo.serie_tasas_del_dia(date(2026, 1, 3)) == []


def test_cursor_de_sync(repo: Repository) -> None:
    assert repo.get_last_sync() is None
    cursor = datetime(2026, 1, 1, 10, 30, 0)
    repo.set_last_sync(cursor)
    assert repo.get_last_sync() == cursor
    # Se puede sobreescribir (el sync avanza el cursor en cada ciclo).
    cursor2 = datetime(2026, 1, 1, 11, 0, 0)
    repo.set_last_sync(cursor2)
    assert repo.get_last_sync() == cursor2


def test_exclusiones_par_no_ordenado(repo: Repository) -> None:
    repo.save_exclusion(
        ExclusionRegla(regla_tipo_a="primera_compra", regla_tipo_b="recurrencia", activo=True)
    )
    assert len(repo.exclusiones()) == 1

    # Mismo par, orden invertido -- debe actualizar la fila existente, no
    # crear una segunda (la regla de negocio trata el par como no ordenado).
    repo.save_exclusion(
        ExclusionRegla(regla_tipo_a="recurrencia", regla_tipo_b="primera_compra", activo=False)
    )
    excls = repo.exclusiones()
    assert len(excls) == 1
    assert excls[0].activo is False


def test_postgres_descuentos_diferencial_cambiario_defaults_cuando_vacio() -> None:
    # InMemoryRepository no replica este fallback (no es parte del contrato
    # abstracto, es un detalle que SheetsRepository y PostgresRepository
    # comparten -- ver test_discounts_new.py::test_repository_new_discounts
    # para la misma aserción contra SheetsRepository) -- por eso no está
    # parametrizado sobre el fixture ``repo``.
    if not _DATABASE_URL:
        pytest.skip("DATABASE_URL no configurado -- se salta el backend Postgres")
    repo = _make_postgres_repo()
    difs = repo.descuentos_diferencial_cambiario()
    assert len(difs) == 3
    assert {d.regla_id for d in difs} == {"DIF_35_VES", "DIF_EQUIPARAR", "DIF_BRECHA_CIERRE"}


def _seed_cliente_orden_pago(repo: Repository) -> None:
    repo.upsert_clientes([Cliente(cliente_id="C1", nombre="Cliente Uno", vendedor_email="v@x.com")])
    repo.upsert_ordenes(
        [
            OrdenVenta(
                so_id="SO1",
                cliente_id="C1",
                fecha=date(2026, 1, 1),
                fecha_entrega=None,
                monto_total=Decimal("100"),
                lista_precios="4",
                vendedor_email="v@x.com",
                es_primera_compra=True,
            )
        ]
    )
    repo.upsert_pagos(
        [
            Pago(
                pago_id="P1",
                cliente_id="C1",
                monto=Decimal("50"),
                moneda=Moneda.USD,
                metodo_pago="M1",
                fecha_pago=datetime(2026, 1, 1, 12, 0, 0),
                vendedor_email="v@x.com",
            )
        ]
    )


# --- Test específico de Postgres: el upsert de sync (upsert_pagos) no debe
# pisar una columna "humana" fuera del dataclass Pago (recibido, puesta por
# /api/cobranza/marcar-recibido) -- InMemoryRepository no modela esa columna
# en absoluto, así que este test solo tiene sentido contra Postgres real.
def test_postgres_upsert_pagos_no_pisa_columna_humana_recibido() -> None:
    if not _DATABASE_URL:
        pytest.skip("DATABASE_URL no configurado -- se salta el backend Postgres")
    repo = _make_postgres_repo()
    repo.upsert_clientes([Cliente(cliente_id="C1", nombre="Cliente Uno", vendedor_email="v@x.com")])
    repo.upsert_pagos(
        [
            Pago(
                pago_id="P1",
                cliente_id="C1",
                monto=Decimal("50"),
                moneda=Moneda.USD,
                metodo_pago="M1",
                fecha_pago=datetime(2026, 1, 1, 12, 0, 0),
                vendedor_email="v@x.com",
            )
        ]
    )
    # Simula lo que hace /api/cobranza/marcar-recibido -- una columna que el
    # dataclass Pago ni siquiera conoce.
    with repo._engine.begin() as conn:
        conn.execute(
            text("UPDATE pagos SET recibido = true, recibido_por = 'admin' WHERE pago_id = 'P1'")
        )

    # El sync vuelve a correr (mismo pago, monto actualizado) -- no debe
    # tocar recibido/recibido_por porque Pago no los conoce.
    repo.upsert_pagos(
        [
            Pago(
                pago_id="P1",
                cliente_id="C1",
                monto=Decimal("75"),
                moneda=Moneda.USD,
                metodo_pago="M1",
                fecha_pago=datetime(2026, 1, 1, 12, 0, 0),
                vendedor_email="v@x.com",
            )
        ]
    )
    with repo._engine.connect() as conn:
        row = conn.execute(
            text("SELECT monto, recibido, recibido_por FROM pagos WHERE pago_id = 'P1'")
        ).first()
    assert row is not None
    assert row.monto == Decimal("75.0000")
    assert row.recibido is True
    assert row.recibido_por == "admin"
