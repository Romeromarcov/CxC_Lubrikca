"""Tests de contrato de ``Repository`` (parte 2) -- cubre los métodos de

configuración/reglas de descuento, feriados, exclusiones, usuarios,
anomalías, tasas históricas y pagos huérfanos que
``test_repository_contract.py`` no ejercía todavía. Mismo patrón: cada test
corre una vez por backend (``InMemoryRepository`` y, si hay
``DATABASE_URL``, ``PostgresRepository`` contra una base real).
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
    Cliente,
    Condicion,
    DescuentoDiferencialCambiario,
    DescuentoProducto,
    DescuentoProntoPago,
    DescuentoRecompra,
    DescuentoVolumen,
    ExclusionRegla,
    Feriado,
    Moneda,
    OrdenVenta,
    Pago,
    PromocionPrimeraCompra,
    TipoBeneficio,
    TipoFeriado,
    VentasTeorico,
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


# --- Configuración genérica (_Meta / app_settings) --------------------------


def test_config_get_set_all(repo: Repository) -> None:
    assert repo.get_config("no_existe") is None
    repo.set_config("cash_window_business_days", "5")
    assert repo.get_config("cash_window_business_days") == "5"
    assert repo.all_config()["cash_window_business_days"] == "5"


# --- Usuarios de la plataforma -----------------------------------------------


def test_usuarios_plataforma_upsert_y_lectura(repo: Repository) -> None:
    assert repo.get_usuario_plataforma("nadie@x.com") is None
    assert repo.all_usuarios_plataforma() == []
    repo.upsert_usuario_plataforma(
        {
            "email": "vendedor@lubrikca.com",
            "nombre_odoo": "Vendedor Uno",
            "password_hash": "hash123",
            "salt": "salt123",
            "rol": "ventas",
            "activo": "TRUE",
            "fecha_registro": "2026-01-01",
        }
    )
    u = repo.get_usuario_plataforma("vendedor@lubrikca.com")
    assert u is not None
    assert u["rol"] == "ventas"
    assert len(repo.all_usuarios_plataforma()) == 1
    # Case-insensitive lookup.
    assert repo.get_usuario_plataforma("VENDEDOR@LUBRIKCA.COM") is not None


# --- Reglas de descuento: lectura + escritura por tabla ---------------------


def test_descuentos_pronto_pago_append_y_lectura(repo: Repository) -> None:
    assert repo.descuentos_marca_categoria() == []
    regla = DescuentoProntoPago(
        regla_id="PP_TEST",
        marca="Sinoco",
        categoria="*",
        porcentaje=Decimal("0.05"),
        vigencia_desde=date(2026, 1, 1),
        requiere_pago_previo=True,
    )
    repo.append_descuento_pronto_pago(regla)
    reglas = repo.descuentos_marca_categoria()
    assert len(reglas) == 1
    assert reglas[0].regla_id == "PP_TEST"
    assert reglas[0].requiere_pago_previo is True


def test_descuentos_volumen_append_y_lectura(repo: Repository) -> None:
    assert repo.descuentos_volumen() == []
    regla = DescuentoVolumen(
        regla_id="VOL_TEST",
        marca="Sinoco",
        categoria="*",
        litros_minimo=Decimal("100"),
        porcentaje=Decimal("0.05"),
        vigencia_desde=date(2026, 1, 1),
        requiere_pago_previo=False,
    )
    repo.append_descuento_volumen(regla)
    reglas = repo.descuentos_volumen()
    assert len(reglas) == 1
    assert reglas[0].regla_id == "VOL_TEST"


def test_descuentos_recompra_append_y_lectura(repo: Repository) -> None:
    assert repo.descuentos_recompra() == []
    regla = DescuentoRecompra(
        regla_id="REC_TEST",
        marca="GLOBAL OIL",
        categoria="CAJA",
        porcentaje=Decimal("0.03"),
        vigencia_desde=date(2026, 1, 1),
    )
    repo.append_descuento_recompra(regla)
    reglas = repo.descuentos_recompra()
    assert len(reglas) == 1
    assert reglas[0].regla_id == "REC_TEST"


def test_descuentos_producto_append_y_lectura(repo: Repository) -> None:
    assert repo.descuentos_producto() == []
    regla = DescuentoProducto(
        regla_id="PROD_TEST",
        productos="P1,P2",
        marca="*",
        categoria="*",
        porcentaje=Decimal("0.05"),
        vigencia_desde=date(2026, 1, 1),
    )
    repo.append_descuento_producto(regla)
    reglas = repo.descuentos_producto()
    assert len(reglas) == 1
    assert reglas[0].regla_id == "PROD_TEST"


def test_descuentos_diferencial_cambiario_append_y_lectura(repo: Repository) -> None:
    regla = DescuentoDiferencialCambiario(
        regla_id="DIF_TEST",
        nombre="Diferencial de prueba",
        vigencia_desde=date(2026, 1, 1),
    )
    repo.append_descuento_diferencial_cambiario(regla)
    reglas = repo.descuentos_diferencial_cambiario()
    assert any(r.regla_id == "DIF_TEST" for r in reglas)


def test_promociones_primera_compra_append_y_lectura(repo: Repository) -> None:
    assert repo.promociones_primera_compra() == []
    promo = PromocionPrimeraCompra(
        regla_id="PROMO_TEST",
        vigencia_desde=date(2026, 1, 1),
    )
    repo.append_promocion_primera_compra(promo)
    promos = repo.promociones_primera_compra()
    assert len(promos) == 1
    assert promos[0].regla_id == "PROMO_TEST"


def test_descuento_bcv_completo_lectura_vacia(repo: Repository) -> None:
    assert repo.descuento_bcv_completo() == []


def test_reglas_recurrencia_crear_y_actualizar_porcentaje(repo: Repository) -> None:
    assert repo.reglas_recurrencia() == []
    repo.set_regla_recurrencia_porcentaje(Condicion.RECOMPRA.value, Decimal("0.03"))
    reglas = repo.reglas_recurrencia()
    assert len(reglas) == 1
    assert reglas[0].condicion == Condicion.RECOMPRA
    assert reglas[0].valor == Decimal("0.03")
    assert reglas[0].tipo_beneficio == TipoBeneficio.PORCENTAJE

    # Segunda llamada con la misma condición actualiza en vez de duplicar.
    repo.set_regla_recurrencia_porcentaje(Condicion.RECOMPRA.value, Decimal("0.04"))
    reglas = repo.reglas_recurrencia()
    assert len(reglas) == 1
    assert reglas[0].valor == Decimal("0.04")


# --- delete_regla / set_regla_activo (genéricos por tabla) ------------------


def test_delete_regla_tabla_desconocida_devuelve_false(repo: Repository) -> None:
    assert repo.delete_regla("TablaQueNoExiste", "X") is False


def test_set_regla_activo_tabla_desconocida_devuelve_false(repo: Repository) -> None:
    assert repo.set_regla_activo("TablaQueNoExiste", "X", False) is False


def test_delete_regla_y_set_regla_activo_sobre_volumen(repo: Repository) -> None:
    regla = DescuentoVolumen(
        regla_id="VOL_DEL",
        marca="Sinoco",
        categoria="*",
        litros_minimo=Decimal("50"),
        porcentaje=Decimal("0.02"),
        vigencia_desde=date(2026, 1, 1),
    )
    repo.append_descuento_volumen(regla)

    assert repo.set_regla_activo("DescuentosVolumen", "NO_EXISTE", False) is False
    assert repo.set_regla_activo("DescuentosVolumen", "VOL_DEL", False) is True
    reglas = repo.descuentos_volumen()
    assert next(r for r in reglas if r.regla_id == "VOL_DEL").activo is False

    assert repo.delete_regla("DescuentosVolumen", "NO_EXISTE") is False
    assert repo.delete_regla("DescuentosVolumen", "VOL_DEL") is True
    assert all(r.regla_id != "VOL_DEL" for r in repo.descuentos_volumen())


# --- Feriados ----------------------------------------------------------------


def test_feriados_append_y_reemplazo_por_fecha(repo: Repository) -> None:
    assert repo.feriados() == []
    repo.append_feriado(
        Feriado(fecha=date(2026, 12, 25), descripcion="Navidad", tipo=TipoFeriado.NACIONAL)
    )
    feriados = repo.feriados()
    assert len(feriados) == 1
    assert feriados[0].descripcion == "Navidad"

    # Volver a agregar en la misma fecha reemplaza, no duplica.
    repo.append_feriado(
        Feriado(
            fecha=date(2026, 12, 25), descripcion="Navidad (corregido)", tipo=TipoFeriado.NACIONAL
        )
    )
    feriados = repo.feriados()
    assert len(feriados) == 1
    assert feriados[0].descripcion == "Navidad (corregido)"


# --- Exclusiones mutuas -------------------------------------------------------


def test_exclusiones_guardar_y_actualizar_par_invertido(repo: Repository) -> None:
    assert repo.exclusiones() == []
    repo.save_exclusion(
        ExclusionRegla(regla_tipo_a="volumen", regla_tipo_b="recompra", activo=True)
    )
    exclusiones = repo.exclusiones()
    assert len(exclusiones) == 1

    # Guardar el par invertido (b, a) debe actualizar la fila existente, no
    # agregar una segunda -- ver ``save_exclusion`` (par no ordenado).
    repo.save_exclusion(
        ExclusionRegla(regla_tipo_a="recompra", regla_tipo_b="volumen", activo=False)
    )
    exclusiones = repo.exclusiones()
    assert len(exclusiones) == 1
    assert exclusiones[0].activo is False


# --- Anomalías aceptadas ------------------------------------------------------


def test_anomalias_aceptadas_append_y_lectura(repo: Repository) -> None:
    assert repo.all_anomalias_aceptadas() == []
    repo.append_anomalia_aceptada(
        {
            "anomalia_id": "ANOM_SO1_DESCUENTO_ORDEN_F1",
            "motivo_aceptacion": "Revisado y aceptado",
            "aprobado_por": "Dirección",
            "timestamp_aprobacion": "2026-01-01T00:00:00",
        }
    )
    rows = repo.all_anomalias_aceptadas()
    assert len(rows) == 1
    assert rows[0]["anomalia_id"] == "ANOM_SO1_DESCUENTO_ORDEN_F1"


# --- Tasas históricas de auditoría -------------------------------------------


def test_tasas_historicas_auditoria_replace(repo: Repository) -> None:
    assert repo.all_tasas_historicas_auditoria() == []
    filas = [
        {
            "fecha": "2026-01-01",
            "tasa_bcv_usd": "36.0",
            "tasa_bcv_euro": "39.0",
            "tasa_binance_promedio_diario": "38.0",
        }
    ]
    repo.replace_tasas_historicas_auditoria(filas)
    rows = repo.all_tasas_historicas_auditoria()
    assert len(rows) == 1
    assert rows[0]["fecha"] == "2026-01-01"

    # Un segundo "replace" reemplaza el contenido completo (no acumula).
    repo.replace_tasas_historicas_auditoria([])
    assert repo.all_tasas_historicas_auditoria() == []


# --- Pagos huérfanos cerrados -------------------------------------------------


def test_pagos_huerfanos_cerrados_upsert_y_lectura(repo: Repository) -> None:
    assert repo.all_pagos_huerfanos_cerrados() == []
    repo.upsert_pago_huerfano_cerrado(
        {
            "pago_id": "PG_HUERFANO",
            "motivo": "Cerrado a favor de la empresa",
            "cerrado_por": "admin@lubrikca.com",
            "timestamp_cierre": datetime(2026, 1, 1, 10, 0).isoformat(),
        }
    )
    rows = repo.all_pagos_huerfanos_cerrados()
    assert len(rows) == 1
    assert rows[0]["pago_id"] == "PG_HUERFANO"


# --- Descuentos de sistema aprobados (Bandeja 1 de Facturación) ---------------


def test_descuentos_sistema_aprobados_upsert_y_lectura(repo: Repository) -> None:
    # so_id tiene FK a ordenes_venta, que a su vez tiene FK a clientes --
    # se necesitan ambos padres primero.
    repo.upsert_clientes(
        [
            Cliente(
                cliente_id="CLI_DESC_SISTEMA",
                nombre="Cliente Desc Sistema",
                vendedor_email="ana@lubrikca.com",
            )
        ]
    )
    repo.upsert_ordenes(
        [
            OrdenVenta(
                so_id="SO_DESC_SISTEMA",
                cliente_id="CLI_DESC_SISTEMA",
                fecha=date(2026, 1, 1),
                fecha_entrega=None,
                monto_total=Decimal("100.00"),
                lista_precios="4",
                vendedor_email="ana@lubrikca.com",
                es_primera_compra=False,
            )
        ]
    )
    assert repo.all_descuentos_sistema_aprobados() == []
    repo.upsert_descuento_sistema_aprobado(
        {
            "so_id": "SO_DESC_SISTEMA",
            "monto": "10.00",
            "motivo": "Ajuste comercial",
            "aprobado_por": "admin@lubrikca.com",
            "timestamp_aprobacion": datetime(2026, 1, 1, 10, 0).isoformat(),
            "activo": "true",
        }
    )
    rows = repo.all_descuentos_sistema_aprobados()
    assert len(rows) == 1
    assert rows[0]["so_id"] == "SO_DESC_SISTEMA"
    # MONEY en Postgres devuelve "10.0000" (4 decimales); InMemory conserva
    # el string tal cual se guardo -- comparar via Decimal para ambos.
    assert Decimal(rows[0]["monto"]) == Decimal("10.00")
    assert rows[0]["activo"] == "true"

    # Upsert por PK natural (so_id) actualiza en vez de duplicar, y permite
    # "revocar" sin perder el registro (activo=false).
    repo.upsert_descuento_sistema_aprobado(
        {
            "so_id": "SO_DESC_SISTEMA",
            "monto": "10.00",
            "motivo": "Ajuste comercial",
            "aprobado_por": "admin@lubrikca.com",
            "timestamp_aprobacion": datetime(2026, 1, 1, 10, 0).isoformat(),
            "activo": "false",
        }
    )
    rows2 = repo.all_descuentos_sistema_aprobados()
    assert len(rows2) == 1
    assert rows2[0]["activo"] == "false"


def test_reglas_dias_credito_volumen_upsert_y_lectura(repo: Repository) -> None:
    assert repo.all_reglas_dias_credito_volumen() == []
    repo.upsert_regla_dias_credito_volumen(
        {
            "regla_id": "CREDITO_0_500L",
            "litros_minimo": "0",
            "litros_maximo": "500",
            "dias_credito_max": "21",
            "descripcion": "De 0 a 500 Lts: máximo 21 días de crédito.",
            "activo": "true",
        }
    )
    rows = repo.all_reglas_dias_credito_volumen()
    assert len(rows) == 1
    assert rows[0]["regla_id"] == "CREDITO_0_500L"
    assert int(rows[0]["dias_credito_max"]) == 21
    assert rows[0]["activo"] == "true"

    # Upsert por PK natural (regla_id) actualiza en vez de duplicar.
    repo.upsert_regla_dias_credito_volumen(
        {
            "regla_id": "CREDITO_0_500L",
            "litros_minimo": "0",
            "litros_maximo": "500",
            "dias_credito_max": "25",
            "descripcion": "Ajustado",
            "activo": "true",
        }
    )
    rows2 = repo.all_reglas_dias_credito_volumen()
    assert len(rows2) == 1
    assert int(rows2[0]["dias_credito_max"]) == 25


# --- Teóricos de Ventas (Fase 10) ---------------------------------------------


def test_ventas_teoricos_upsert_y_lectura(repo: Repository) -> None:
    repo.upsert_clientes(
        [Cliente(cliente_id="CLI_VT", nombre="Cliente VT", vendedor_email="ana@lubrikca.com")]
    )
    repo.upsert_ordenes(
        [
            OrdenVenta(
                so_id="SO_VT",
                cliente_id="CLI_VT",
                fecha=date(2026, 1, 1),
                fecha_entrega=None,
                monto_total=Decimal("100.00"),
                lista_precios="4",
                vendedor_email="ana@lubrikca.com",
                es_primera_compra=False,
            )
        ]
    )
    assert repo.all_ventas_teoricos() == []
    assert repo.get_ventas_teorico("SO_VT") is None

    repo.upsert_ventas_teorico(
        VentasTeorico(
            so_id="SO_VT",
            teorico_ves=Decimal("120.00"),
            teorico_usd=Decimal("100.00"),
            descuentos_teorico_ves=Decimal("6.00"),
            descuentos_teorico_usd=Decimal("5.00"),
            lista_ves_id="5",
            lista_usd_id="4",
            usa_fallback_ves=False,
            usa_fallback_usd=True,
            calculado_en=datetime(2026, 1, 2, 10, 0),
        )
    )
    fila = repo.get_ventas_teorico("SO_VT")
    assert fila is not None
    assert fila.teorico_ves == Decimal("120.00")
    assert fila.usa_fallback_ves is False
    assert fila.usa_fallback_usd is True
    assert len(repo.all_ventas_teoricos()) == 1

    # Upsert por PK natural (so_id) actualiza en vez de duplicar -- así se
    # re-verifica una orden marcada usa_fallback sin crear una fila nueva.
    repo.upsert_ventas_teorico(
        VentasTeorico(
            so_id="SO_VT",
            teorico_ves=Decimal("120.00"),
            teorico_usd=Decimal("100.00"),
            descuentos_teorico_ves=Decimal("6.00"),
            descuentos_teorico_usd=Decimal("5.00"),
            lista_ves_id="5",
            lista_usd_id="4",
            usa_fallback_ves=False,
            usa_fallback_usd=False,
            calculado_en=datetime(2026, 1, 3, 10, 0),
        )
    )
    filas = repo.all_ventas_teoricos()
    assert len(filas) == 1
    assert filas[0].usa_fallback_usd is False


# --- Listas de precios históricas (solo lectura) ------------------------------


def test_listas_precios_historicas_lectura_vacia(repo: Repository) -> None:
    assert repo.all_listas_precios_historicas() == []


# --- Pagos: lectura completa + marcar como recibido --------------------------


def test_all_pagos_full_y_marcar_recibido(repo: Repository) -> None:
    repo.upsert_clientes(
        [Cliente(cliente_id="C1", nombre="Cliente Uno", vendedor_email="v1@lubrikca.com")]
    )
    repo.upsert_pagos(
        [
            Pago(
                pago_id="PG1",
                cliente_id="C1",
                monto=Decimal("100"),
                moneda=Moneda.USD,
                metodo_pago="M1",
                fecha_pago=datetime(2026, 1, 1, 10, 0),
                vendedor_email="v1@lubrikca.com",
            )
        ]
    )
    rows = repo.all_pagos_full()
    assert len(rows) == 1
    assert rows[0]["pago_id"] == "PG1"
    assert rows[0]["recibido"] == "FALSE"

    actualizados = repo.marcar_pagos_recibido(
        ["PG1", "NO_EXISTE"], "REC-001", datetime(2026, 1, 2, 9, 0), "cobranza@lubrikca.com"
    )
    assert len(actualizados) == 1
    assert actualizados[0]["pago_id"] == "PG1"
    assert actualizados[0]["recibido"] == "TRUE"
    assert actualizados[0]["numero_recibido"] == "REC-001"

    rows = repo.all_pagos_full()
    assert rows[0]["recibido"] == "TRUE"


def test_get_metodo_pago_inexistente(repo: Repository) -> None:
    assert repo.get_metodo_pago("NO_EXISTE") is None


def test_invalidate_cache_no_falla(repo: Repository) -> None:
    # No-op en ambos backends (Postgres siempre lee en vivo) -- solo se
    # verifica que no lance.
    repo.invalidate_cache()
    repo.invalidate_cache("Pagos")
