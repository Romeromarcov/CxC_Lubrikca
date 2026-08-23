"""``_vincular_masivo_sync`` -- bug real (reportado por el usuario, agosto

2026, cliente CONSTRUCTORA GRANO AGREGADO/pagos 998 y 1029, encontrado al
investigar por qué la orden S00427 se veía "menos pagada" justo después
de que el auto-FIFO creara sus Vinculaciones): antes de este fix, un
pago se vinculaba SIEMPRE con ``repo.last_serie_tasa()`` -- la tasa MÁS
RECIENTE del sistema, sin importar la fecha real del pago
(``pago.fecha_pago``). Bajo devaluación, vincular un pago viejo con la
tasa de HOY lo subvalúa en dólares. Caso real: pago del 2026-06-22
vinculado con la tasa del 2026-08-23 (784.66) en vez de la real de ese
día (612.43) -- 22% de diferencia.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from cxc.models import Moneda, Pago, SerieTasa
from cxc.web.app import _vincular_masivo_sync


def test_usa_la_tasa_del_dia_del_pago_no_la_mas_reciente() -> None:
    mock_repo = MagicMock()
    mock_repo.get_pago.return_value = Pago(
        pago_id="998",
        cliente_id="CLI_GRANO",
        monto=Decimal("1663301.20"),
        moneda=Moneda.VES,
        metodo_pago="Transferencia",
        fecha_pago=date(2026, 6, 22),
        vendedor_email="v@lubrikca.com",
    )
    # La tasa MÁS RECIENTE del sistema (mucho más alta, tasa de HOY) --
    # antes del fix, esta era la que se usaba SIEMPRE, sin importar la
    # fecha real del pago.
    mock_repo.last_serie_tasa.return_value = SerieTasa(
        timestamp=datetime(2026, 8, 23, 10, 0),
        tasa_bcv=Decimal("784.6633"),
        tasa_binance=Decimal("920.8731"),
        fuente="test",
    )
    # La tasa REAL del día del pago (2026-06-22) -- mucho más baja.
    mock_repo.all_serie_tasas.return_value = [
        SerieTasa(
            timestamp=datetime(2026, 6, 22, 12, 0),
            tasa_bcv=Decimal("612.4332"),
            tasa_binance=Decimal("717.7717"),
            fuente="test",
        ),
    ]
    mock_repo.get_orden.return_value = None  # fuera de la ventana histórica

    processed, so_ids = _vincular_masivo_sync(
        mock_repo, [("998", "S00427", 2141.06)], confirmado_por="test"
    )

    assert processed == 1
    assert so_ids == {"S00427"}
    vinc = mock_repo.update_vinculacion.call_args[0][0]
    # Debe congelar la tasa del DÍA DEL PAGO, no la más reciente.
    assert vinc.tasa_bcv_aplicada == Decimal("612.4332")
    assert vinc.tasa_binance_aplicada == Decimal("717.7717")


def test_cae_al_default_hardcodeado_si_no_hay_dato_para_esa_fecha() -> None:
    """Si ni SerieTasas ni TasasHistoricasAuditoria tienen nada para la

    fecha exacta del pago, ``get_rate_for_datetime`` ya trae su propio
    último recurso (36.5/38.0 hardcodeado) -- no hace falta ningún
    fallback adicional en ``_vincular_masivo_sync``."""
    mock_repo = MagicMock()
    mock_repo.get_pago.return_value = Pago(
        pago_id="1",
        cliente_id="CLI_X",
        monto=Decimal("1000.00"),
        moneda=Moneda.VES,
        metodo_pago="Transferencia",
        fecha_pago=date(2020, 1, 1),
        vendedor_email="v@lubrikca.com",
    )
    mock_repo.all_serie_tasas.return_value = []
    mock_repo.all_tasas_historicas_auditoria.return_value = []
    mock_repo.get_orden.return_value = None

    # get_rate_for_datetime consulta TasasHistoricasAuditoria vía el
    # get_repo() global (no el mock_repo pasado directo a la función) --
    # se parchea para que ambos apunten al mismo mock.
    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        processed, _ = _vincular_masivo_sync(
            mock_repo, [("1", "SO_X", 1.29)], confirmado_por="test"
        )
    assert processed == 1
    vinc = mock_repo.update_vinculacion.call_args[0][0]
    assert vinc.tasa_bcv_aplicada == Decimal("36.5")
    assert vinc.tasa_binance_aplicada == Decimal("38.0")
