"""``resolver_tasa_bcv_vinculacion``: del N+1 de la Fase 6 a la función trivial de hoy.

**11-sep-2026.** El plan listaba «`SerieTasas` se lee sin caché — quedan 24 lecturas
directas repartidas por `app.py`». De los 22 sitios no-definición, 21 seguían el
patrón que el plan aprueba (leen una vez, pasan las filas hacia abajo); uno no:
`resolver_tasa_bcv_vinculacion` armaba su propio objeto `Tasas` leyendo la serie
completa, y dos de sus seis llamadores la invocaban DENTRO de un bucle sobre pagos
-- 206 vinculaciones en la ventana histórica, 919 filas de serie cada una, por
ciclo. Se corrigió pasando la serie ya leída (`serie_rows`).

**12-sep-2026, un día después.** La decisión 5 del quiz («la vía euro es solo para
auditoría, no debe modificar los montos reales») fijó `is_historical_pricelist_
enabled` en `False` para siempre. Esa función gobernaba, a través de
`orden_en_periodo_historico` (retirada ese mismo día, sin llamador), la única rama
de `resolver_tasa_bcv_vinculacion` que llegaba a leer la serie o el histórico. Con
la rama inalcanzable, la función quedó en lo que es hoy: devuelve la tasa BCV-USD
que ya traía el llamador, con variante 'USD', siempre -- no toca `repo`, no lee
nada. El N+1 de ayer no se arregló dos veces: la segunda decisión hizo que la
primera corrección dejara de tener trabajo que hacer.

Estos tests son deliberadamente chicos para el tamaño actual de la función. Los
argumentos ``so_id``/``serie_rows``/``repo`` se pasan igual porque los cinco
llamadores no cambiaron -- lo que se fija es que la función los ignora.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

from cxc.web.app import resolver_tasa_bcv_vinculacion

HORA_PAGO = datetime(2026, 3, 1, 10, 0, 0)
DEFAULT_USD = Decimal("36.5")


def test_siempre_devuelve_la_tasa_default_con_variante_usd() -> None:
    repo = MagicMock()
    tasa, variante = resolver_tasa_bcv_vinculacion(repo, "S00001", HORA_PAGO, DEFAULT_USD)
    assert (tasa, variante) == (DEFAULT_USD, "USD")


def test_no_toca_el_repo_para_nada() -> None:
    """Ni ``get_orden``, ni ``all_serie_tasas``, ni ``all_tasas_historicas_auditoria``:

    la función no necesita saber nada de la orden ni de las tasas para responder.
    """
    repo = MagicMock()
    resolver_tasa_bcv_vinculacion(repo, "S00001", HORA_PAGO, DEFAULT_USD)
    repo.assert_not_called()
    assert repo.method_calls == []


def test_pasar_la_serie_no_cambia_nada() -> None:
    """``serie_rows`` sigue en la firma (los cinco llamadores la pasan); se ignora."""
    repo = MagicMock()
    tasa, variante = resolver_tasa_bcv_vinculacion(
        repo, "S00001", HORA_PAGO, DEFAULT_USD, serie_rows=[{"tasa_bcv_euro": "40.00"}]
    )
    assert (tasa, variante) == (DEFAULT_USD, "USD")
    assert repo.method_calls == []
