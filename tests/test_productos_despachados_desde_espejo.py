"""_productos_despachados_desde_espejo (Fase 5 del plan de consolidación

de fuentes, agosto 2026) -- réplica, leyendo del espejo (Entrega/
EntregaLinea), del Check 5 de get_auditoria ("productos realmente
despachados"). NO está conectada a ningún endpoint todavía -- estos
tests fijan que la reconstrucción es correcta antes de conectarla.
"""

from __future__ import annotations

from cxc.repositories import InMemoryRepository
from cxc.web.app import _productos_despachados_desde_espejo

from . import builders as b


def test_producto_despachado_en_picking_done_outgoing():
    repo = InMemoryRepository()
    repo.upsert_entregas(
        [b.entrega("E1", so_id="SO1", tipo="outgoing", estado="done")]
    )
    repo.upsert_entregas_lineas(
        [b.entrega_linea("L1", entrega_id="E1", producto_id="101")]
    )
    result = _productos_despachados_desde_espejo(repo, {"SO1"})
    assert result == {"SO1": {101}}


def test_picking_no_done_se_ignora():
    repo = InMemoryRepository()
    repo.upsert_entregas(
        [b.entrega("E1", so_id="SO1", tipo="outgoing", estado="assigned")]
    )
    repo.upsert_entregas_lineas(
        [b.entrega_linea("L1", entrega_id="E1", producto_id="101")]
    )
    result = _productos_despachados_desde_espejo(repo, {"SO1"})
    assert result == {}


def test_picking_no_outgoing_se_ignora():
    repo = InMemoryRepository()
    repo.upsert_entregas(
        [b.entrega("E1", so_id="SO1", tipo="internal", estado="done")]
    )
    repo.upsert_entregas_lineas(
        [b.entrega_linea("L1", entrega_id="E1", producto_id="101")]
    )
    result = _productos_despachados_desde_espejo(repo, {"SO1"})
    assert result == {}


def test_so_fuera_del_conjunto_pedido_se_ignora():
    repo = InMemoryRepository()
    repo.upsert_entregas(
        [b.entrega("E1", so_id="SO_NO_PEDIDA", tipo="outgoing", estado="done")]
    )
    repo.upsert_entregas_lineas(
        [b.entrega_linea("L1", entrega_id="E1", producto_id="101")]
    )
    result = _productos_despachados_desde_espejo(repo, {"SO1"})
    assert result == {}


def test_multiples_productos_del_mismo_picking_se_agregan_al_set():
    repo = InMemoryRepository()
    repo.upsert_entregas(
        [b.entrega("E1", so_id="SO1", tipo="outgoing", estado="done")]
    )
    repo.upsert_entregas_lineas(
        [
            b.entrega_linea("L1", entrega_id="E1", producto_id="101"),
            b.entrega_linea("L2", entrega_id="E1", producto_id="102"),
        ]
    )
    result = _productos_despachados_desde_espejo(repo, {"SO1"})
    assert result == {"SO1": {101, 102}}


def test_multiples_pickings_de_la_misma_orden_se_combinan():
    repo = InMemoryRepository()
    repo.upsert_entregas(
        [
            b.entrega("E1", so_id="SO1", tipo="outgoing", estado="done"),
            b.entrega("E2", so_id="SO1", tipo="outgoing", estado="done"),
        ]
    )
    repo.upsert_entregas_lineas(
        [
            b.entrega_linea("L1", entrega_id="E1", producto_id="101"),
            b.entrega_linea("L2", entrega_id="E2", producto_id="102"),
        ]
    )
    result = _productos_despachados_desde_espejo(repo, {"SO1"})
    assert result == {"SO1": {101, 102}}


def test_producto_id_no_numerico_no_rompe():
    repo = InMemoryRepository()
    repo.upsert_entregas(
        [b.entrega("E1", so_id="SO1", tipo="outgoing", estado="done")]
    )
    repo.upsert_entregas_lineas(
        [b.entrega_linea("L1", entrega_id="E1", producto_id="")]
    )
    result = _productos_despachados_desde_espejo(repo, {"SO1"})
    assert result == {}


def test_sin_entregas_devuelve_vacio():
    repo = InMemoryRepository()
    result = _productos_despachados_desde_espejo(repo, {"SO1"})
    assert result == {}
