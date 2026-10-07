"""Barrido de borrados del espejo (auditoría de septiembre 2026).

El delta por ``write_date`` no ve un registro borrado en Odoo; el barrido compara
los ids del espejo contra los que Odoo todavía tiene y borra la diferencia, con
salvaguardas: Odoo vacío no borra nada, un exceso sobre el tope no borra nada, y
un pago con Vinculaciones (trabajo humano) nunca se borra.
"""

from __future__ import annotations

from cxc.odoo.client import OdooReader
from cxc.repositories import InMemoryRepository
from cxc.sync.incremental import IncrementalSync


class _RepoBarrido(InMemoryRepository):
    def __init__(self, espejo: dict[str, set[str]], con_vinculaciones: set[str] | None = None):
        super().__init__()
        self.espejo = {k: set(v) for k, v in espejo.items()}
        self.con_vinculaciones = con_vinculaciones or set()
        self.borrados: dict[str, list[str]] = {}

    def ids_espejo(self, tabla: str) -> set[str]:
        return set(self.espejo.get(tabla, set()))

    def borrar_espejo(self, tabla: str, ids: list[str]) -> int:
        self.borrados.setdefault(tabla, []).extend(ids)
        self.espejo[tabla] -= set(ids)
        return len(ids)

    def pago_ids_con_vinculaciones(self, pago_ids: list[str]) -> set[str]:
        return {i for i in pago_ids if i in self.con_vinculaciones}


class _LectorBarrido(OdooReader):
    def __init__(self, vigentes: dict[str, set[str] | None]):
        self.vigentes = vigentes

    def changed_clientes(self, since):
        return []

    def changed_ordenes(self, since):
        return []

    def changed_lineas(self, since):
        return []

    def changed_pagos(self, since):
        return []

    def lineas_vigentes_por_orden(self, so_ids):
        return {}

    def ids_vigentes(self, tabla):
        return self.vigentes.get(tabla)


def _ids(n: int, prefijo: str = "") -> set[str]:
    return {f"{prefijo}{i}" for i in range(n)}


def test_borra_lo_que_odoo_ya_no_tiene():
    repo = _RepoBarrido({"facturas": {"1", "2", "3"}})
    lector = _LectorBarrido({"facturas": {"1", "2"}})
    r = IncrementalSync(repo, lector).barrer_borrados()
    assert r["borradas"] == {"facturas": 1}
    assert repo.espejo["facturas"] == {"1", "2"}


def test_lo_que_odoo_tiene_y_el_espejo_no_se_ignora():
    repo = _RepoBarrido({"facturas": {"1"}})
    lector = _LectorBarrido({"facturas": {"1", "2", "3"}})
    r = IncrementalSync(repo, lector).barrer_borrados()
    assert r["borradas"] == {}
    assert repo.espejo["facturas"] == {"1"}


def test_odoo_vacio_no_borra_nada():
    repo = _RepoBarrido({"facturas": {"1", "2"}})
    lector = _LectorBarrido({"facturas": set()})
    r = IncrementalSync(repo, lector).barrer_borrados()
    assert r["borradas"] == {}
    assert "facturas" in r["omitidas"]
    assert repo.espejo["facturas"] == {"1", "2"}


def test_lector_que_no_sabe_contestar_omite_la_tabla():
    repo = _RepoBarrido({"facturas": {"1", "2"}})
    lector = _LectorBarrido({})
    r = IncrementalSync(repo, lector).barrer_borrados()
    assert r["borradas"] == {}
    assert "facturas" in r["omitidas"]


def test_exceso_sobre_el_tope_no_borra_nada():
    en_espejo = _ids(2000)
    vigentes = _ids(800)  # sobran 1200 > max(500, 10% de 2000)
    repo = _RepoBarrido({"lineas_factura": en_espejo})
    lector = _LectorBarrido({"lineas_factura": vigentes})
    r = IncrementalSync(repo, lector).barrer_borrados()
    assert r["borradas"] == {}
    assert "tope" in r["omitidas"]["lineas_factura"]
    assert len(repo.espejo["lineas_factura"]) == 2000


def test_diferencia_dentro_del_tope_si_se_borra():
    en_espejo = _ids(2000)
    vigentes = _ids(1850)  # sobran 150, bajo el tope de 500
    repo = _RepoBarrido({"lineas_factura": en_espejo})
    lector = _LectorBarrido({"lineas_factura": vigentes})
    r = IncrementalSync(repo, lector).barrer_borrados()
    assert r["borradas"] == {"lineas_factura": 150}


def test_pago_con_vinculaciones_nunca_se_borra():
    repo = _RepoBarrido({"pagos": {"10", "11", "12"}}, con_vinculaciones={"11"})
    lector = _LectorBarrido({"pagos": {"10"}})
    r = IncrementalSync(repo, lector).barrer_borrados()
    assert r["pagos_bloqueados"] == ["11"]
    assert r["borradas"] == {"pagos": 1}
    assert repo.espejo["pagos"] == {"10", "11"}


def test_backend_sin_soporte_no_tumba_el_sync():
    repo = InMemoryRepository()  # no implementa ids_espejo
    lector = _LectorBarrido({"facturas": {"1"}})
    r = IncrementalSync(repo, lector).barrer_borrados()
    assert r["borradas"] == {}
    assert set(r["omitidas"]) == set(IncrementalSync.TABLAS_BARRIDO)


def _vinc(vinc_id, pago_id, estado):
    from . import builders as b

    return b.vinculacion(vinc_id, pago_id=pago_id, estado=estado)


def test_pago_borrado_en_odoo_con_solo_pendientes_se_retira_con_ellas():
    from cxc.models import EstadoVinculacion

    repo = _RepoBarrido({"pagos": {"10", "11"}}, con_vinculaciones={"11"})
    repo.update_vinculacion(_vinc("V1", "11", EstadoVinculacion.PENDIENTE))
    lector = _LectorBarrido({"pagos": {"10"}})
    r = IncrementalSync(repo, lector).barrer_borrados()
    assert r["pagos_bloqueados"] == []
    assert r["borradas"] == {"pagos": 1}
    assert repo.espejo["pagos"] == {"10"}
    assert repo.all_vinculaciones() == []


def test_pago_borrado_en_odoo_con_una_conciliada_se_conserva():
    from cxc.models import EstadoVinculacion

    repo = _RepoBarrido({"pagos": {"10", "11"}}, con_vinculaciones={"11"})
    repo.update_vinculacion(_vinc("V1", "11", EstadoVinculacion.PENDIENTE))
    repo.update_vinculacion(_vinc("V2", "11", EstadoVinculacion.CONCILIADO))
    lector = _LectorBarrido({"pagos": {"10"}})
    r = IncrementalSync(repo, lector).barrer_borrados()
    assert r["pagos_bloqueados"] == ["11"]
    assert repo.espejo["pagos"] == {"10", "11"}
    assert len(repo.all_vinculaciones()) == 2


def test_se_puede_acotar_el_barrido_a_una_tabla():
    repo = _RepoBarrido({"pagos": {"1", "2"}, "facturas": {"1", "2"}})
    lector = _LectorBarrido({"pagos": {"1"}, "facturas": {"1"}})
    r = IncrementalSync(repo, lector).barrer_borrados(("pagos",))
    assert r["borradas"] == {"pagos": 1}
    assert repo.espejo["facturas"] == {"1", "2"}
