"""``metodos_pago`` se espeja desde los diarios de Odoo.

Corrección pedida por el usuario (auditoría de agosto 2026): "lo
importante es apoyarse de los journal de odoo, esa es la verdad". La tabla
estaba vacía en producción, así que cada abono caía en el ``MetodoPago``
de reserva de ``EngineRunner._abonos()``.
"""

from __future__ import annotations

from cxc.models import Moneda, TipoTasa
from cxc.repositories import InMemoryRepository
from cxc.web.app import sincronizar_metodos_pago

# Diarios reales de la instancia (bank/cash), con el mismo shape que
# devuelve Odoo: ``currency_id`` es un many2one [id, nombre] o False.
_JOURNALS = [
    {"id": 14, "name": "Bank", "type": "bank", "currency_id": [166, "VES"], "code": "BDV"},
    {"id": 15, "name": "Cash", "type": "cash", "currency_id": False, "code": "BS1"},
    {
        "id": 29,
        "name": "Efectivo moneda extragera",
        "type": "cash",
        "currency_id": [2, "USD"],
        "code": "USD1",
    },
    {"id": 35, "name": "Binance", "type": "bank", "currency_id": [2, "USD"], "code": "BNB"},
]


def _execute_falso(model, method, args, kwargs=None):
    if model == "account.journal":
        return _JOURNALS
    if model == "res.company":
        return [{"currency_id": [166, "VES"]}]
    return []


def test_espeja_los_diarios_con_su_moneda() -> None:
    repo = InMemoryRepository()
    assert sincronizar_metodos_pago(repo, _execute_falso) == 4

    bdv = repo.get_metodo_pago("14")
    assert bdv is not None
    assert bdv.nombre == "Bank"
    assert bdv.moneda == Moneda.VES
    assert bdv.es_contado is False

    usd = repo.get_metodo_pago("29")
    assert usd is not None
    assert usd.moneda == Moneda.USD
    # Diario de efectivo -> es_contado.
    assert usd.es_contado is True


def test_diario_sin_moneda_propia_usa_la_de_la_empresa() -> None:
    repo = InMemoryRepository()
    sincronizar_metodos_pago(repo, _execute_falso)
    cash = repo.get_metodo_pago("15")
    assert cash is not None
    assert cash.moneda == Moneda.VES


def test_tipo_tasa_se_deriva_del_diario() -> None:
    repo = InMemoryRepository()
    sincronizar_metodos_pago(repo, _execute_falso)
    assert repo.get_metodo_pago("35").tipo_tasa == TipoTasa.BINANCE  # type: ignore[union-attr]
    assert repo.get_metodo_pago("14").tipo_tasa == TipoTasa.BCV  # type: ignore[union-attr]
    assert repo.get_metodo_pago("29").tipo_tasa == TipoTasa.N_A  # type: ignore[union-attr]


def test_sin_conexion_a_odoo_no_falla() -> None:
    """Best-effort: sin Odoo se deja la tabla como esté, sin tumbar el ciclo."""
    repo = InMemoryRepository()
    assert sincronizar_metodos_pago(repo, None) == 0
