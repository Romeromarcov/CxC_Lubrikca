"""Una vinculación que viola la novena invariante no deja sin escribir a las demás.

Lo encontró el banco de escenarios el 12-sep-2026, la primera vez que corrió con la
invariante puesta (306f84f, del día anterior). Los diez pagos que ya están
sobreaplicados en Odoo -- los parciales corrompidos por la edición de fecha --
hacían que ``update_vinculaciones`` levantara ``InvarianteViolada`` sobre el lote
entero del ciclo, y el motor escribe TODAS sus vinculaciones en un solo lote:

    Error al recalcular todas las órdenes: 10 fila(s) violan una invariante de
    dinero y no se escribieron: pago 40 ... pago 829 ... pago 200 ...

«10 filas no se escribieron» era mentira por defecto: no se escribió ninguna. Y el
``except Exception`` del demonio lo imprimía a stderr cada cinco minutos, con lo
que la promoción PENDIENTE→CONCILIADO y todo recálculo de vinculaciones quedaban
congelados en cualquier base con esos diez pagos. Producción los tiene.

Dos arreglos, ninguno mueve montos:

1. La invariante dice exactamente «el exceso no crece» (``test_invariantes_repositorio``).
2. Los lotes del demonio van por ``update_vinculaciones_omitiendo_invalidas``: la
   fila que viola se omite, queda en la base como estaba, y va a la bandeja de
   auditoría como ``vinculacion_rechazada_por_invariante``. Las demás se escriben.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from cxc.db import schema as db_schema
from cxc.db.invariantes import InvarianteViolada
from cxc.db.postgres_repository import PostgresRepository
from cxc.models import Cliente, EstadoVinculacion, Moneda, OrdenVenta, Pago, Vinculacion

_DATABASE_URL = os.environ.get("DATABASE_URL")


def _vinc(vinc_id: str, monto: str, pago_id: str = "P1", so_id: str = "SO1") -> Vinculacion:
    return Vinculacion(
        vinc_id=vinc_id,
        pago_id=pago_id,
        so_id=so_id,
        monto_aplicado=Decimal(monto),
        hora_pago_confirmada=datetime(2026, 1, 1, 12, 0, 0),
        tasa_bcv_aplicada=Decimal("36.5"),
        tasa_binance_aplicada=Decimal("38.0"),
        es_tasa_heredada=False,
        estado=EstadoVinculacion.PENDIENTE,
    )


@pytest.fixture
def repo() -> PostgresRepository:
    if not _DATABASE_URL:
        pytest.skip("DATABASE_URL no configurado")
    from sqlalchemy import text

    r = PostgresRepository.from_url(_DATABASE_URL)
    db_schema.metadata.create_all(r._engine)
    tablas = ", ".join(tbl.name for tbl in db_schema.metadata.sorted_tables)
    with r._engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tablas} RESTART IDENTITY CASCADE"))
    r.upsert_clientes([Cliente(cliente_id="C1", nombre="Uno", vendedor_email="v@x.com")])
    r.upsert_ordenes(
        [
            OrdenVenta(
                so_id=so,
                cliente_id="C1",
                fecha=date(2026, 1, 1),
                fecha_entrega=None,
                monto_total=Decimal("1000"),
                lista_precios="4",
                vendedor_email="v@x.com",
                es_primera_compra=False,
            )
            for so in ("SO1", "SO2")
        ]
    )
    # P1 vale 134 (el pago 200 real); P2 vale 500 y está sano.
    r.upsert_pagos(
        [
            Pago(
                pago_id=pid,
                cliente_id="C1",
                monto=Decimal(monto),
                moneda=Moneda.USD,
                metodo_pago="M1",
                fecha_pago=datetime(2026, 1, 1, 12, 0, 0),
                vendedor_email="v@x.com",
            )
            for pid, monto in (("P1", "134"), ("P2", "500"))
        ]
    )
    return r


# --- el repositorio ------------------------------------------------------------


def test_la_version_estricta_sigue_tumbando_el_lote_entero(repo: PostgresRepository) -> None:
    """Es su contrato, y lo usan las rutas manuales: o entra todo o no entra nada."""
    with pytest.raises(InvarianteViolada):
        repo.update_vinculaciones([_vinc("V1", "318.27"), _vinc("V2", "100", pago_id="P2")])
    assert repo.all_vinculaciones() == [], "ni la sana se escribió: todo o nada"


def test_la_version_tolerante_escribe_las_sanas_y_devuelve_las_que_no(
    repo: PostgresRepository,
) -> None:
    rechazadas = repo.update_vinculaciones_omitiendo_invalidas(
        [_vinc("V1", "318.27"), _vinc("V2", "100", pago_id="P2")]
    )
    assert [v.vinc_id for v, _f in rechazadas] == ["V1"]
    assert rechazadas[0][1].restriccion == "ck_vinc_no_sobreaplica_el_pago"
    assert {v.vinc_id for v in repo.all_vinculaciones()} == {"V2"}


def test_reescribir_igual_la_fila_ya_sobreaplicada_no_se_rechaza(repo: PostgresRepository) -> None:
    """El caso de cada ciclo del demonio sobre los diez de producción.

    La fila entra por ``update_vinculacion`` (singular, sin la invariante de
    suma -- así llegaron las diez); después el lote la reescribe igual.
    """
    repo.update_vinculacion(_vinc("V1", "318.27"))
    assert repo.update_vinculaciones_omitiendo_invalidas([_vinc("V1", "318.27")]) == []
    repo.update_vinculaciones([_vinc("V1", "318.27")])  # y la estricta tampoco levanta


def test_agrandar_la_fila_ya_sobreaplicada_se_omite_y_la_base_conserva_la_anterior(
    repo: PostgresRepository,
) -> None:
    repo.update_vinculacion(_vinc("V1", "318.27"))
    rechazadas = repo.update_vinculaciones_omitiendo_invalidas([_vinc("V1", "715.04")])
    assert len(rechazadas) == 1
    (v,) = repo.all_vinculaciones()
    assert v.monto_aplicado == Decimal("318.270000"), "la base se quedó con la versión anterior"


def test_dos_filas_del_mismo_lote_se_miden_juntas(repo: PostgresRepository) -> None:
    """P2 vale 500: 300 + 250 no caben. La segunda es la que sobra."""
    rechazadas = repo.update_vinculaciones_omitiendo_invalidas(
        [_vinc("A", "300", pago_id="P2"), _vinc("B", "250", pago_id="P2", so_id="SO2")]
    )
    assert [v.vinc_id for v, _f in rechazadas] == ["B"]
    assert {v.vinc_id for v in repo.all_vinculaciones()} == {"A"}


def test_lote_vacio_no_hace_nada(repo: PostgresRepository) -> None:
    assert repo.update_vinculaciones_omitiendo_invalidas([]) == []


# --- los dos llamadores del demonio ----------------------------------------------


def test_el_motor_escribe_por_la_via_tolerante_y_guarda_lo_rechazado() -> None:
    from cxc.engine.runner import EngineRunner

    repo = MagicMock()
    repo.all_ordenes.return_value = []
    repo.all_vinculaciones.return_value = []
    repo.all_bandeja.return_value = []
    repo.all_lineas.return_value = []
    rechazo = (_vinc("V1", "715.04"), "motivo")
    repo.update_vinculaciones_omitiendo_invalidas.return_value = [rechazo]

    runner = EngineRunner(repo, MagicMock(), MagicMock())
    runner.run_all(date(2026, 9, 12))

    repo.update_vinculaciones_omitiendo_invalidas.assert_called_once()
    repo.update_vinculaciones.assert_not_called(), "la estricta tumbaría el ciclo"
    assert runner.vinculaciones_rechazadas == [rechazo]


def test_la_resincronizacion_con_odoo_no_se_cae_y_deja_la_rechazada_en_auditoria() -> None:
    """Una vinculación PENDIENTE que Odoo confirma se promueve a CONCILIADO; si
    esa escritura viola la invariante, el resync sigue y la bandeja lo dice.

    Se usa la rama más simple del resync (promoción sin recálculo) porque lo
    que se prueba es el manejo del rechazo, no el recálculo; el repo devuelve
    la fila como rechazada y eso basta.
    """
    import cxc.web.app as app

    v_local = _vinc("V1", "715.04", pago_id="200")
    repo = MagicMock()
    repo.all_vinculaciones.return_value = [v_local]
    repo.all_auditoria.return_value = []
    repo.all_serie_tasas.return_value = []
    falla = MagicMock()
    falla.__str__.return_value = "el pago vale 134 y las vinculaciones sumarían 715.04"
    repo.update_vinculaciones_omitiendo_invalidas.return_value = [(v_local, falla)]

    conciliado = {"pago_id": "200", "so_ids": ["SO1"], "monto": 715.04, "moneda": "USD"}
    with patch("cxc.web.app.get_live_pagos_conciliados", return_value=[conciliado]):
        app.invalidar_tasas()
        cambios = app._resincronizar_vinculaciones_con_odoo(repo, MagicMock())
    escrito = repo.update_vinculaciones_omitiendo_invalidas.call_args[0][0]
    assert escrito[0].estado == EstadoVinculacion.CONCILIADO, "la promoción se intentó"
    repo.update_vinculaciones.assert_not_called()

    rechazos = [c for c in cambios if c["tipo"] == "vinculacion_rechazada"]
    assert len(rechazos) == 1 and rechazos[0]["requiere_revision_manual"] is True
    filas = repo.append_auditoria_rows.call_args[0][0]
    tipos = {f["tipo_auditoria"] for f in filas}
    assert app.TIPO_AUDITORIA_VINCULACION_RECHAZADA in tipos
    fila = next(f for f in filas if f["tipo_auditoria"] == app.TIPO_AUDITORIA_VINCULACION_RECHAZADA)
    assert fila["estado"] == "pendiente_revision"
    assert "V1" in fila["detalle_odoo"] and "715.04" in fila["detalle_odoo"]


def test_recalculate_all_orders_registra_lo_que_el_motor_rechazo() -> None:
    """El ciclo del demonio: lo rechazado por el motor llega a la bandeja, y el
    ciclo NO se corta antes del reconciliador."""
    import cxc.web.app as app

    repo = MagicMock()
    repo.all_auditoria.return_value = []
    rechazo = (_vinc("V1", "715.04", pago_id="200"), "motivo")

    class _Runner:
        def __init__(self, *a, **k):
            self.vinculaciones_rechazadas = []

        def run_all(self, _fecha):
            self.vinculaciones_rechazadas = [rechazo]
            return []

        def run_teoricos_pendientes(self, *a, **k):
            return 0

    reconciler = MagicMock()
    with (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app._connect", return_value=MagicMock()),
        patch("cxc.web.app.AppConfig"),
        patch("cxc.web.app.EngineRunner", _Runner),
        patch("cxc.web.app._resolvedor_de_precios", return_value=MagicMock()),
        patch("cxc.web.app.get_valid_pricelists_usd_and_ves", return_value=([11], [10])),
        patch("cxc.web.app.sincronizar_metodos_pago"),
        patch("cxc.web.app.sincronizar_vigencia_listas", return_value=0),
        patch("cxc.web.app._auto_vincular_fifo_pendientes", return_value=0),
        patch("cxc.web.app._resincronizar_vinculaciones_con_odoo", return_value=[]),
        patch("cxc.web.app._detectar_vinculaciones_pendientes_a_revisar", return_value=[]),
        patch("cxc.web.app.OdooXmlRpcReader"),
        patch(
            "cxc.web.app._sincronizar_aplicaciones_conciliadas",
            return_value={"creadas": 0, "corregidas": 0, "sin_cambio": 0, "omitidas": 0},
        ),
        patch("cxc.web.app.OdooFacturasReader"),
        patch("cxc.web.app.Reconciler", return_value=reconciler),
    ):
        app.recalculate_all_orders()

    filas = repo.append_auditoria_rows.call_args[0][0]
    assert filas[0]["tipo_auditoria"] == app.TIPO_AUDITORIA_VINCULACION_RECHAZADA
    assert filas[0]["pago_id"] == "200"
    reconciler.run.assert_called_once(), "el ciclo siguió hasta el reconciliador"


# --- el segundo hallazgo de la misma corrida: un pago sin tasa abortaba el paso ----
#
# En el stderr del mismo escenario, dos líneas más:
#
#     Error sincronizando aplicaciones de Odoo: No hay tasa para 2026-09-05 ...
#     Error re-sincronizando Vinculaciones con Odoo: No hay tasa para 2026-04-15 ...
#
# Desde la Fase 2.1 ``get_rate_for_datetime`` levanta ``TasaNoDisponible`` en vez de
# inventar 36,5/38,0 -- decisión correcta -- pero ``TasaNoDisponible`` es un
# ``RuntimeError``, no un ``ValueError``, y las dos funciones lo llamaban fuera de todo
# ``try``. UN pago con fecha sin tasa abortaba el paso entero (todas las aplicaciones
# de Odoo, o todas las 1.494 vinculaciones del resync), cada cinco minutos, hasta que
# alguien cargara esa tasa. Ahora ese pago se salta, queda como estaba, y va a la
# bandeja como ``pago_sin_tasa_para_su_fecha``.


def _apl(pago, so, monto, fecha):
    from cxc.models import AplicacionConciliada

    return AplicacionConciliada(
        pago_id=pago,
        so_id=so,
        factura_id="F1",
        monto=Decimal(monto),
        moneda=Moneda.USD,
        fecha_pago=fecha,
    )


class _RepoSinTasaParaUnDia:
    """Serie sembrada solo para agosto; el pago de abril no tiene tasa."""

    def __init__(self):
        from tests import builders as b

        self.escritas: list[Vinculacion] = []
        self._serie = b.serie_tasas_sembrada(date(2026, 8, 1), date(2026, 8, 31))

    def all_vinculaciones(self):
        return []

    def all_pagos(self):
        from types import SimpleNamespace

        return [SimpleNamespace(pago_id="P_ABR"), SimpleNamespace(pago_id="P_AGO")]

    def update_vinculacion(self, v):
        self.escritas.append(v)

    def all_serie_tasas(self):
        return self._serie

    def all_tasas_historicas_auditoria(self):
        return []

    def all_listas_precios_historicas(self):
        return []

    def get_orden(self, so_id):
        return None


def test_un_pago_sin_tasa_para_su_fecha_no_aborta_las_aplicaciones_de_los_demas() -> None:
    import cxc.web.app as app

    repo = _RepoSinTasaParaUnDia()
    app.invalidar_tasas()
    apps = [
        _apl("P_ABR", "SO1", "100", date(2026, 4, 15)),  # sin tasa
        _apl("P_AGO", "SO2", "200", date(2026, 8, 10)),  # con tasa
    ]
    res = app._sincronizar_aplicaciones_conciliadas(repo, apps)

    assert [v.pago_id for v in repo.escritas] == ["P_AGO"], "el de agosto se escribió igual"
    assert res["creadas"] == 1
    assert res["sin_tasa"] == [{"pago_id": "P_ABR", "so_id": "SO1", "fecha": "2026-04-15"}]


def test_el_demonio_deja_el_pago_sin_tasa_en_la_bandeja() -> None:
    """Lo que devuelve ``sin_tasa`` no puede quedarse en un log: va a auditoría."""
    import cxc.web.app as app

    repo = MagicMock()
    repo.all_auditoria.return_value = []
    repo.all_pagos.return_value = []

    class _Runner:
        def __init__(self, *a, **k):
            self.vinculaciones_rechazadas = []

        def run_all(self, _fecha):
            return []

        def run_teoricos_pendientes(self, *a, **k):
            return 0

    lector = MagicMock()
    lector.aplicaciones_conciliadas.return_value = []
    with (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app._connect", return_value=MagicMock()),
        patch("cxc.web.app.AppConfig"),
        patch("cxc.web.app.EngineRunner", _Runner),
        patch("cxc.web.app._resolvedor_de_precios", return_value=MagicMock()),
        patch("cxc.web.app.get_valid_pricelists_usd_and_ves", return_value=([11], [10])),
        patch("cxc.web.app.sincronizar_metodos_pago"),
        patch("cxc.web.app.sincronizar_vigencia_listas", return_value=0),
        patch("cxc.web.app._auto_vincular_fifo_pendientes", return_value=0),
        patch("cxc.web.app._resincronizar_vinculaciones_con_odoo", return_value=[]),
        patch("cxc.web.app._detectar_vinculaciones_pendientes_a_revisar", return_value=[]),
        patch("cxc.web.app.OdooXmlRpcReader", return_value=lector),
        patch(
            "cxc.web.app._sincronizar_aplicaciones_conciliadas",
            return_value={
                "creadas": 0,
                "corregidas": 0,
                "sin_cambio": 0,
                "omitidas": 0,
                "sin_tasa": [{"pago_id": "P_ABR", "so_id": "SO1", "fecha": "2026-04-15"}],
            },
        ),
        patch("cxc.web.app.OdooFacturasReader"),
        patch("cxc.web.app.Reconciler"),
    ):
        app.recalculate_all_orders()

    filas = repo.append_auditoria_rows.call_args[0][0]
    assert filas[0]["tipo_auditoria"] == app.TIPO_AUDITORIA_PAGO_SIN_TASA
    assert filas[0]["pago_id"] == "P_ABR"
    assert filas[0]["estado"] == "pendiente_revision"
    assert "2026" not in filas[0]["detalle_odoo"] or "tasa" in filas[0]["detalle_odoo"]


def test_el_resync_no_se_cae_por_una_vinculacion_cuya_fecha_nueva_no_tiene_tasa() -> None:
    """Odoo movió la fecha del pago a un día sin tasa. Esa vinculación queda con
    sus equivalentes viejos (y su so_id/estado al día); las demás se procesan; y
    la bandeja dice cuál fue."""
    import cxc.web.app as app
    from cxc.rates import TasaNoDisponible

    v1 = _vinc("V1", "100", pago_id="P1")
    v1 = v1.__class__(**{**v1.__dict__, "estado": EstadoVinculacion.CONCILIADO})
    v2 = _vinc("V2", "50", pago_id="P2")
    repo = MagicMock()
    repo.all_vinculaciones.return_value = [v1, v2]
    repo.all_auditoria.return_value = []
    repo.all_serie_tasas.return_value = []
    repo.update_vinculaciones_omitiendo_invalidas.return_value = []
    conciliados = [
        # P1: misma orden, pero la FECHA cambió -> hay que recalcular -> sin tasa
        {
            "pago_id": "P1",
            "so_ids": ["SO1"],
            "monto": 100.0,
            "moneda": "USD",
            "fecha": "2026-04-15",
        },
        # P2: PENDIENTE que Odoo confirma -> promoción simple
        {"pago_id": "P2", "so_ids": ["SO1"], "monto": 50.0, "moneda": "USD"},
    ]
    with (
        patch("cxc.web.app.get_live_pagos_conciliados", return_value=conciliados),
        patch("cxc.web.app.get_rate_for_datetime", side_effect=TasaNoDisponible("sin tasa")),
    ):
        app.invalidar_tasas()
        cambios = app._resincronizar_vinculaciones_con_odoo(repo, MagicMock())

    escritas = repo.update_vinculaciones_omitiendo_invalidas.call_args[0][0]
    assert {v.vinc_id for v in escritas} == {"V2"}, "V1 no se tocó; V2 se promovió igual"
    sin_tasa = [c for c in cambios if c["tipo"] == "pago_sin_tasa"]
    assert [c["pago_id"] for c in sin_tasa] == ["P1"]
    filas = repo.append_auditoria_rows.call_args[0][0]
    assert any(f["tipo_auditoria"] == app.TIPO_AUDITORIA_PAGO_SIN_TASA for f in filas)


# --- la factura consolidada que tumbaba el ciclo entero (17-sep-2026) -------------
#
# Incidente real de producción: una factura que Odoo arma consolidando dos SO
# ("S00718, S00700" en invoice_origin) se escribía tal cual en Vinculacion.so_id,
# que tiene FK contra ordenes_venta. La violación abortaba el INSERT en lote --
# tanto el de _sincronizar_aplicaciones_conciliadas (el lote entero de cada ciclo)
# como el de _resincronizar_vinculaciones_con_odoo -- cada cinco minutos, hasta
# que se corrigió esto. Dos arreglos: la causa (so_ids_de_invoice_origin en
# odoo/client.py y get_live_pagos_conciliados) y la red (una fila que la base
# rechaza no corta el resto del lote).


def test_el_resync_no_escribe_nada_cuando_la_factura_consolida_dos_ordenes() -> None:
    """El caso real del pago 1866: había una Vinculación local sana (so_id

    "S00700", de un sync anterior a que Odoo consolidara otra orden en la
    misma factura). Con so_ids ya separado en {"S00700", "S00718"} (2
    elementos), la rama que corre es "discrepancia_multi_orden": audita, no
    intenta escribir ningún Vinculacion.so_id inventado con una coma adentro.
    """
    import cxc.web.app as app

    v_local = _vinc("V1", "43476.60", pago_id="1866", so_id="S00700")
    v_local = v_local.__class__(**{**v_local.__dict__, "estado": EstadoVinculacion.CONCILIADO})
    repo = MagicMock()
    repo.all_vinculaciones.return_value = [v_local]
    repo.all_auditoria.return_value = []
    repo.all_serie_tasas.return_value = []

    conciliado = {
        "pago_id": "1866",
        "so_ids": ["S00700", "S00718"],
        "monto": 43476.60,
        "moneda": "USD",
    }
    with patch("cxc.web.app.get_live_pagos_conciliados", return_value=[conciliado]):
        app.invalidar_tasas()
        cambios = app._resincronizar_vinculaciones_con_odoo(repo, MagicMock())

    repo.update_vinculaciones_omitiendo_invalidas.assert_not_called()
    repo.update_vinculaciones.assert_not_called()
    assert any(c["tipo"] == "discrepancia_multi_orden" and c["pago_id"] == "1866" for c in cambios)
    filas = repo.append_auditoria_rows.call_args[0][0]
    assert any(f["tipo_auditoria"] == "vinculacion_discrepancia_multi_orden" for f in filas)


def test_sincronizar_aplicaciones_una_fila_que_la_base_rechaza_no_corta_las_demas() -> None:
    """Antes: `repo.update_vinculacion(...)` sin try/except adentro del ``for`` --
    una excepción en la fila N (una FK, lo que sea) dejaba sin procesar TODAS las
    aplicaciones que venían después en la misma corrida, no solo esa. Ahora se
    salta esa fila, queda en ``errores``, y el resto se escribe."""
    from cxc.models import AplicacionConciliada
    from cxc.models import Moneda as _Moneda


    repo = MagicMock()
    repo.all_vinculaciones.return_value = []
    repo.all_pagos.return_value = [MagicMock(pago_id="1"), MagicMock(pago_id="2")]
    repo.all_serie_tasas.return_value = []
    repo.all_tasas_historicas_auditoria.return_value = [
        {
            "fecha": "2026-09-15",
            "tasa_bcv_usd": "800.0",
            "tasa_bcv_euro": "900.0",
            "tasa_binance_promedio_diario": "900.0",
        }
    ]
    # "1" viola algo en la base (simulado); "2" viene DESPUÉS en el orden
    # (sorted por (pago_id, so_id)) y tiene que escribirse igual.
    repo.update_vinculacion.side_effect = [
        Exception('insert or update on table "vinculaciones" violates foreign key constraint'),
        None,
    ]

    aplicaciones = [
        AplicacionConciliada(
            pago_id="1",
            so_id="S00718, S00700",
            factura_id="F1",
            monto=Decimal("100"),
            moneda=_Moneda.USD,
            fecha_pago=date(2026, 9, 15),
        ),
        AplicacionConciliada(
            pago_id="2",
            so_id="S00700",
            factura_id="F2",
            monto=Decimal("50"),
            moneda=_Moneda.USD,
            fecha_pago=date(2026, 9, 15),
        ),
    ]

    import cxc.web.app as app

    app.invalidar_tasas()
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = app._sincronizar_aplicaciones_conciliadas(repo, aplicaciones)

    assert repo.update_vinculacion.call_count == 2, "las DOS filas se intentaron, no se cortó"
    assert res["creadas"] == 1
    assert [e["pago_id"] for e in res["errores"]] == ["1"]
    assert "foreign key" in res["errores"][0]["motivo"]
