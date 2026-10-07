"""Odoo manda sobre las vinculaciones PENDIENTE (``engine/pendientes_contra_odoo``)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cxc.engine.pendientes_contra_odoo import clasificar_pendientes
from cxc.models import EstadoVinculacion, Moneda

from . import builders as b

PEND = EstadoVinculacion.PENDIENTE
CONC = EstadoVinculacion.CONCILIADO


def _v(vinc_id, so, monto, estado, pago="P1", hora=datetime(2026, 9, 3, 10, 0), **kw):
    v = b.vinculacion(vinc_id, pago_id=pago, so_id=so, monto_aplicado=monto, estado=estado, **kw)
    v.timestamp_registro = hora
    return v


def _ids(lista):
    return sorted(v.vinc_id for v in lista)


def test_si_odoo_cubrio_todo_el_pago_las_pendientes_sobran():
    """Pago 2018: conciliado en S2 por el monto completo, pendiente en S1 por lo mismo."""
    r = clasificar_pendientes(
        [_v("a", "S1", "100", PEND), _v("b", "S2", "100", CONC)], [b.pago("P1", monto="100")]
    )
    assert _ids(r.a_retirar) == ["a"]
    assert r.a_ajustar == []


def test_la_tolerancia_relativa_cuenta_como_cubierto():
    """Pago en VES: Odoo concilio 173.226,58 de 173.232,01 (0,003%) -> cubierto."""
    r = clasificar_pendientes(
        [_v("a", "S1", "173235.15", PEND), _v("b", "S2", "173226.58", CONC)],
        [b.pago("P1", monto="173232.01", moneda=Moneda.VES)],
    )
    assert _ids(r.a_retirar) == ["a"]


def test_redondeo_de_la_conversion_no_es_un_exceso():
    """Dos pendientes en VES que suman 0,57 Bs mas que el pago (pago 1090): se quedan."""
    r = clasificar_pendientes(
        [_v("a", "S1", "235.6931", PEND), _v("b", "S2", "173964.8798", PEND)],
        [b.pago("P1", monto="174200", moneda=Moneda.VES)],
    )
    assert r.a_retirar == []
    assert r.a_ajustar == []


def test_parcial_conciliado_con_pendiente_que_cabe_en_lo_que_falta_se_queda():
    r = clasificar_pendientes(
        [_v("a", "S1", "40", PEND), _v("b", "S2", "60", CONC)], [b.pago("P1", monto="100")]
    )
    assert r.a_retirar == []
    assert r.a_ajustar == []


def test_la_pendiente_que_excede_lo_que_dejo_odoo_se_achica():
    """FIFO asigno el pago completo (677) a S1 y Odoo ya habia conciliado 252,91."""
    r = clasificar_pendientes(
        [_v("a", "S1", "677", PEND), _v("b", "S2", "252.91", CONC)], [b.pago("P1", monto="677")]
    )
    assert r.a_retirar == []
    (ajustada,) = r.a_ajustar
    assert ajustada.vinc_id == "a"
    assert ajustada.monto_aplicado == Decimal("424.09")


def test_al_achicar_los_equivalentes_escalan_en_proporcion():
    v = _v("a", "S1", "200", PEND)
    v.equiv_usd_bcv = Decimal("200")
    v.equiv_ves_bcv = Decimal("7200")
    r = clasificar_pendientes(
        [v, _v("b", "S2", "100", CONC)], [b.pago("P1", monto="200")]
    )
    (ajustada,) = r.a_ajustar
    assert ajustada.monto_aplicado == Decimal("100")
    assert ajustada.equiv_usd_bcv == Decimal("100")
    assert ajustada.equiv_ves_bcv == Decimal("3600")


def test_el_reparto_sigue_el_orden_fifo_de_las_ordenes():
    """Dos sugerencias sin conciliar que reclaman mas que el pago: se queda entera la de
    la orden MAS ANTIGUA y la otra se achica a lo que sobra."""
    r = clasificar_pendientes(
        [_v("nueva", "S2", "60", PEND), _v("vieja", "S1", "60", PEND)],
        [b.pago("P1", monto="100")],
        fechas_orden={"S1": date(2026, 3, 1), "S2": date(2026, 6, 1)},
    )
    assert r.a_retirar == []
    (ajustada,) = r.a_ajustar
    assert ajustada.vinc_id == "nueva"
    assert ajustada.monto_aplicado == Decimal("40")


def test_las_que_ya_no_tienen_de_donde_se_retiran():
    r = clasificar_pendientes(
        [_v(x, s, "100", PEND) for x, s in (("a", "S1"), ("b", "S2"), ("c", "S3"))],
        [b.pago("P1", monto="100")],
        fechas_orden={"S1": date(2026, 1, 1), "S2": date(2026, 2, 1), "S3": date(2026, 3, 1)},
    )
    assert _ids(r.a_retirar) == ["b", "c"]
    assert r.a_ajustar == []


def test_pendiente_normal_sin_conciliar_no_se_toca():
    r = clasificar_pendientes([_v("a", "S1", "100", PEND)], [b.pago("P1", monto="100")])
    assert r.a_retirar == []
    assert r.a_ajustar == []


def test_solo_se_miran_las_pendientes_del_pago_que_odoo_cubrio():
    r = clasificar_pendientes(
        [
            _v("a", "S1", "100", PEND, pago="P1"),
            _v("b", "S2", "100", CONC, pago="P1"),
            _v("c", "S3", "100", PEND, pago="P2"),  # otro pago, sin conciliar
        ],
        [b.pago("P1", monto="100"), b.pago("P2", monto="100")],
    )
    assert _ids(r.a_retirar) == ["a"]


def test_un_pago_que_no_esta_en_el_espejo_no_se_evalua_aqui():
    """Lo resuelve el barrido de borrados, no esta funcion."""
    r = clasificar_pendientes([_v("a", "S1", "100", PEND)], [])
    assert r.a_retirar == []
    assert r.a_ajustar == []


# --- Conciliadas que Odoo no tiene / discrepancias resueltas ----------------------------

from cxc.engine.pendientes_contra_odoo import (  # noqa: E402
    conciliadas_que_odoo_no_tiene,
    discrepancias_resueltas,
)


def test_conciliada_local_que_odoo_ya_no_reparte_se_retira():
    """Pago 14: Odoo reparte S00046 y S01135; quedaban S00188 y S00170 de reparto viejo."""
    vs = [
        _v("a", "S00046", "129.96", CONC, pago="14"),
        _v("b", "S01135", "0.04", CONC, pago="14"),
        _v("c", "S00188", "0.04", CONC, pago="14"),
        _v("d", "S00170", "0.04", CONC, pago="14"),
    ]
    odoo = {("14", "S00046"): Decimal("129.96"), ("14", "S01135"): Decimal("0.04")}
    assert _ids(conciliadas_que_odoo_no_tiene(vs, odoo)) == ["c", "d"]


def test_si_odoo_no_reconcilia_el_pago_no_se_retira_nada_aqui():
    vs = [_v("a", "S1", "100", CONC, pago="P9")]
    assert conciliadas_que_odoo_no_tiene(vs, {("P1", "S2"): Decimal("5")}) == []


def test_las_pendientes_no_se_tocan_en_esta_regla():
    vs = [_v("a", "S1", "100", PEND, pago="P1")]
    assert conciliadas_que_odoo_no_tiene(vs, {("P1", "S2"): Decimal("100")}) == []


def test_discrepancia_resuelta_cuando_lo_local_es_el_reparto_de_odoo():
    vs = [_v("a", "S1", "80", CONC, pago="P1"), _v("b", "S2", "20", CONC, pago="P1")]
    odoo = {("P1", "S1"): Decimal("80"), ("P1", "S2"): Decimal("20")}
    assert discrepancias_resueltas(["P1"], vs, odoo) == ["P1"]


def test_discrepancia_abierta_si_un_monto_difiere():
    vs = [_v("a", "S1", "80", CONC, pago="P1"), _v("b", "S2", "20", CONC, pago="P1")]
    odoo = {("P1", "S1"): Decimal("80"), ("P1", "S2"): Decimal("25")}
    assert discrepancias_resueltas(["P1"], vs, odoo) == []


def test_discrepancia_abierta_si_falta_un_par():
    vs = [_v("a", "S1", "80", CONC, pago="P1")]
    odoo = {("P1", "S1"): Decimal("80"), ("P1", "S2"): Decimal("20")}
    assert discrepancias_resueltas(["P1"], vs, odoo) == []


def test_discrepancia_resuelta_si_odoo_ya_no_concilia_el_pago():
    vs = [_v("a", "S1", "80", PEND, pago="P1")]
    assert discrepancias_resueltas(["P1"], vs, {}) == ["P1"]


def test_alinear_con_el_reparto_de_odoo_retira_residuos_y_cierra_discrepancias():
    """De punta a punta con el repositorio en memoria: pago 14 (residuos viejos) y un pago
    cuya discrepancia ya coincide con Odoo."""
    from cxc.models import AplicacionConciliada
    from cxc.repositories import InMemoryRepository
    from cxc.web.app import _alinear_con_el_reparto_de_odoo

    repo = InMemoryRepository()
    for v in (
        _v("a", "S46", "129.96", CONC, pago="14"),
        _v("b", "S1135", "0.04", CONC, pago="14"),
        _v("c", "S188", "0.04", CONC, pago="14"),  # residuo
        _v("d", "S1", "80", CONC, pago="P2"),
        _v("e", "S2", "20", CONC, pago="P2"),
    ):
        repo.update_vinculacion(v)
    repo.append_auditoria_rows(
        [
            {
                "audit_id": f"A{p}",
                "so_id": "S1",
                "pago_id": p,
                "tipo_auditoria": "vinculacion_discrepancia_multi_orden",
                "estado": "pendiente_revision",
            }
            for p in ("14", "P2")
        ]
    )

    def app(pago, so, monto):
        return AplicacionConciliada(
            pago_id=pago,
            so_id=so,
            factura_id="F",
            monto=Decimal(monto),
            moneda=Moneda.USD,
            fecha_pago=date(2026, 9, 1),
        )

    apps = [
        app("14", "S46", "129.96"),
        app("14", "S1135", "0.04"),
        app("P2", "S1", "80"),
        app("P2", "S2", "20"),
    ]
    r = _alinear_con_el_reparto_de_odoo(repo, apps)
    assert r == {"retiradas": 1, "cerradas": 2}
    assert sorted(v.vinc_id for v in repo.all_vinculaciones()) == ["a", "b", "d", "e"]
    assert {x["audit_id"]: x["estado"] for x in repo.all_auditoria()} == {
        "A14": "revisado",
        "AP2": "revisado",
    }


def test_sin_aplicaciones_de_odoo_no_se_toca_nada():
    from cxc.repositories import InMemoryRepository
    from cxc.web.app import _alinear_con_el_reparto_de_odoo

    repo = InMemoryRepository()
    repo.update_vinculacion(_v("a", "S1", "100", CONC, pago="P1"))
    assert _alinear_con_el_reparto_de_odoo(repo, []) == {"retiradas": 0, "cerradas": 0}
    assert len(repo.all_vinculaciones()) == 1
