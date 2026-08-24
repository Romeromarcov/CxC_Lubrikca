"""``_detectar_vinculaciones_tasa_implicita_implausible`` -- forma general

del bug real de esta sesión (confundir Bs con USD, o congelar una tasa
equivocada): la tasa implícita de una Vinculación VES
(``monto_aplicado / equiv_usd_bcv``) debe parecerse a la tasa BCV real de
esa fecha. Si no, el equivalente USD ya congelado está mal, aunque no
haya sobreaplicación ni ningún otro chequeo lo detecte.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from cxc.models import EstadoVinculacion, Moneda, Vinculacion
from cxc.web.app import _detectar_vinculaciones_tasa_implicita_implausible


def _vinc(monto_ves, equiv_usd, moneda=Moneda.VES) -> Vinculacion:
    return Vinculacion(
        vinc_id="V1",
        pago_id="P1",
        so_id="S0001",
        monto_aplicado=Decimal(monto_ves),
        hora_pago_confirmada=datetime(2026, 7, 20, 12, 0, 0),
        tasa_bcv_aplicada=Decimal("721.35"),
        tasa_binance_aplicada=Decimal("780.0"),
        es_tasa_heredada=False,
        equiv_usd_bcv=Decimal(equiv_usd),
        estado=EstadoVinculacion.PENDIENTE,
        moneda_abono=moneda,
    )


def _tasas_row(fecha: str, bcv: str) -> dict:
    return {"timestamp": f"{fecha} 12:00:00", "tasa_bcv": bcv, "tasa_binance": "780.0"}


def test_detecta_tasa_implicita_muy_distinta_de_la_real() -> None:
    # monto_aplicado tratado como si equiv_usd_bcv ya fuera correcto, pero
    # la tasa implícita (2.436.828,07 / 3.378,17 =~ 721.35... vs real
    # simulada muy distinta) dispara el guard.
    v = _vinc("2436828.07", "3378.17")
    tasas_rows = [_tasas_row("2026-07-20", "300.0")]  # tasa real muy distinta

    resultado = _detectar_vinculaciones_tasa_implicita_implausible([v], tasas_rows)

    assert len(resultado) == 1
    assert resultado[0]["vinc_id"] == "V1"
    assert resultado[0]["diferencia_pct"] > 15.0


def test_no_detecta_si_la_tasa_implicita_calza_con_la_real() -> None:
    v = _vinc("2436828.07", "3378.17")  # implícita =~ 721.35
    tasas_rows = [_tasas_row("2026-07-20", "721.35")]

    assert _detectar_vinculaciones_tasa_implicita_implausible([v], tasas_rows) == []


def test_ignora_vinculaciones_en_usd() -> None:
    v = _vinc("100", "100", moneda=Moneda.USD)
    tasas_rows = [_tasas_row("2026-07-20", "1.0")]  # cualquier tasa disparatada

    assert _detectar_vinculaciones_tasa_implicita_implausible([v], tasas_rows) == []


def test_ignora_vinculaciones_sin_equiv_usd_bcv() -> None:
    v = _vinc("100", "0")
    v.equiv_usd_bcv = None
    tasas_rows = [_tasas_row("2026-07-20", "721.35")]

    assert _detectar_vinculaciones_tasa_implicita_implausible([v], tasas_rows) == []


def test_sin_vinculaciones_no_falla() -> None:
    assert _detectar_vinculaciones_tasa_implicita_implausible([], []) == []
