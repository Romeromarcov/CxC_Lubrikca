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

    processed, so_ids, _omitidos = _vincular_masivo_sync(
        mock_repo, [("998", "S00427", 2141.06)], confirmado_por="test"
    )

    assert processed == 1
    assert so_ids == {"S00427"}
    vinc = mock_repo.update_vinculacion.call_args[0][0]
    # Debe congelar la tasa del DÍA DEL PAGO, no la más reciente.
    assert vinc.tasa_bcv_aplicada == Decimal("612.4332")
    assert vinc.tasa_binance_aplicada == Decimal("717.7717")


def test_sin_tasa_no_se_escribe_la_vinculacion() -> None:
    """Era «cae al default hardcodeado»; ahora es «no se escribe nada».

    Reescrito el 11-sep-2026 por decisión del usuario. Este test asertaba que la
    vinculación quedaba guardada con ``36,5 / 38,0`` — o sea, era la
    especificación del defecto: **1.463 vinculaciones del espejo de prueba
    tienen exactamente ese valor congelado**, acreditando 2.260.174,60 USD donde
    correspondían unos 99.664.

    Este es un camino de ESCRITURA, y el equivalente que se calcula acá se
    congela y por diseño no se vuelve a revisar. No escribir es más barato que
    una cifra mala que nadie va a corregir.

    **Corregido el 12-sep-2026: se saltea la fila, no el lote.** La versión del
    11-sep decía «falla todo el lote, deliberado — un lote a medias deja al
    usuario sin saber qué se aplicó y qué no». Dos cosas estaban mal en eso: el
    lote ya era a medias (cada fila se escribe al pasar, así que las anteriores
    a la que fallaba quedaban escritas y la excepción no lo decía), y el
    Auto-FIFO del demonio corre por esta misma función, así que UN pago sin
    tasa dejaba muerto ese paso en todos los ciclos. Ahora la fila sin tasa se
    saltea, nada se escribe para ella, y vuelve en ``omitidos`` con el motivo
    -- que es lo que le dice al usuario qué se aplicó y qué no.
    """
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
        processed, so_ids, omitidos = _vincular_masivo_sync(
            mock_repo, [("1", "SO_X", 1.29)], confirmado_por="test"
        )

    assert processed == 0 and so_ids == set()
    assert [(o["pago_id"], o["so_id"]) for o in omitidos] == [("1", "SO_X")]
    assert "2020-01-01" in omitidos[0]["motivo"], "el motivo nombra la fecha sin tasa"
    mock_repo.update_vinculacion.assert_not_called()
    mock_repo.add_vinculacion.assert_not_called()


def test_el_endpoint_masivo_dice_parcial_y_lista_lo_que_no_vinculo() -> None:
    """«Se procesaron N» sin decir cuántos faltaron era la mitad de la verdad."""
    from fastapi.testclient import TestClient

    import cxc.web.app as app

    async def _nada():
        return None

    repo = MagicMock()
    with (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app.hay_sesion_valida", return_value=True),
        patch("cxc.web.app.run_scraper_in_background", _nada),
        patch("cxc.web.app.run_sync_in_background", _nada),
        patch("cxc.web.app._aplicar_migraciones_pendientes"),
        patch(
            "cxc.web.app._vincular_masivo_sync",
            return_value=(1, {"S1"}, [{"pago_id": "9", "so_id": "S2", "motivo": "sin tasa"}]),
        ),
        TestClient(app.app) as c,
    ):
        r = c.post(
            "/api/vincular-masivo",
            json={
                "items": [
                    {"pago_id": "1", "so_id": "S1", "monto_aplicado": 10},
                    {"pago_id": "9", "so_id": "S2", "monto_aplicado": 10},
                ]
            },
        )
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["status"] == "parcial"
    assert cuerpo["procesados"] == 1
    assert cuerpo["omitidos"] == [{"pago_id": "9", "so_id": "S2", "motivo": "sin tasa"}]
    assert "1 NO se vincularon" in cuerpo["message"] and "pago 9" in cuerpo["message"]
