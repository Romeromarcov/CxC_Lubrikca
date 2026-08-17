"""_entregas_desde_espejo (Fase 2 del plan de consolidación de fuentes,

agosto 2026) -- réplica, leyendo del espejo Entrega, de
get_live_entregas_info. NO está conectada a ningún endpoint todavía (ver
docstring de la función) -- estos tests solo fijan que replica la MISMA
semántica exacta que la función en vivo.
"""

from __future__ import annotations

from datetime import date

from cxc.repositories import InMemoryRepository
from cxc.web.app import _entregas_desde_espejo

from . import builders as b


def test_despacho_saliente_done_marca_entregada():
    repo = InMemoryRepository()
    repo.upsert_entregas(
        [b.entrega("E1", so_id="SO1", tipo="outgoing", estado="done", fecha=date(2026, 6, 5))]
    )
    delivered, fechas = _entregas_desde_espejo(repo, {"SO1"})
    assert delivered == {"SO1"}
    assert fechas == {"SO1": "2026-06-05"}


def test_devolucion_excluye_de_entregada_aunque_tenga_despacho_valido():
    repo = InMemoryRepository()
    repo.upsert_entregas(
        [
            b.entrega("E1", so_id="SO1", tipo="outgoing", estado="done", fecha=date(2026, 6, 5)),
            b.entrega(
                "E2",
                so_id="SO1",
                tipo="incoming",
                estado="done",
                es_devolucion=True,
                entrega_origen_id="E1",
            ),
        ]
    )
    delivered, fechas = _entregas_desde_espejo(repo, {"SO1"})
    assert delivered == set()
    # fecha_entrega_map SÍ se puebla igual, con o sin devolución posterior.
    assert fechas == {"SO1": "2026-06-05"}


def test_picking_no_done_se_ignora():
    repo = InMemoryRepository()
    repo.upsert_entregas(
        [b.entrega("E1", so_id="SO1", tipo="outgoing", estado="assigned", fecha=None)]
    )
    delivered, fechas = _entregas_desde_espejo(repo, {"SO1"})
    assert delivered == set()
    assert fechas == {}


def test_so_sin_pickings_no_aparece_en_ningun_resultado():
    repo = InMemoryRepository()
    repo.upsert_entregas([])
    delivered, fechas = _entregas_desde_espejo(repo, {"SO1"})
    assert delivered == set()
    assert fechas == {}


def test_varios_despachos_usa_la_fecha_mas_reciente():
    repo = InMemoryRepository()
    repo.upsert_entregas(
        [
            b.entrega("E1", so_id="SO1", tipo="outgoing", estado="done", fecha=date(2026, 6, 1)),
            b.entrega("E2", so_id="SO1", tipo="outgoing", estado="done", fecha=date(2026, 6, 10)),
        ]
    )
    delivered, fechas = _entregas_desde_espejo(repo, {"SO1"})
    assert delivered == {"SO1"}
    assert fechas == {"SO1": "2026-06-10"}


def test_pickings_fuera_del_conjunto_so_names_se_ignoran():
    repo = InMemoryRepository()
    repo.upsert_entregas(
        [b.entrega("E1", so_id="SO_NO_PEDIDA", tipo="outgoing", estado="done")]
    )
    delivered, fechas = _entregas_desde_espejo(repo, {"SO1"})
    assert delivered == set()
    assert fechas == {}


def test_entrega_interna_ni_entregada_ni_devuelta():
    """tipo == "internal" no es ni un despacho saliente válido ni una

    devolución -- no debe afectar ninguno de los dos conjuntos."""
    repo = InMemoryRepository()
    repo.upsert_entregas([b.entrega("E1", so_id="SO1", tipo="internal", estado="done")])
    delivered, fechas = _entregas_desde_espejo(repo, {"SO1"})
    assert delivered == set()
    assert fechas == {}
