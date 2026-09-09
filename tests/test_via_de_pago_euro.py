"""La tasa euro es una vía de pago, y solo para la lista histórica.

Criterio del usuario (septiembre 2026): "el equivalente a tasa euro es vía
de pago, pero solo para las órdenes de la lista histórica, por ahora", y
aplica POR ORDEN -- toda la cobranza en bolívares de una orden histórica
se acredita al euro, no solo los abonos que Odoo convirtió así.

Y la aclaración que define dónde vive: "para esos casos mantén el
equivalente a BCV usd para conciliar contra odoo, solo usa la referencia
en euro para rebajar la cxc como se hace con la tasa binance; tú no
comparas el equivalente a binance contra odoo porque odoo no maneja esa
tasa".

Así que hay dos cifras y no una:

  · ``Vinculacion.equiv_usd_bcv`` sigue en BCV-USD y es la que se enfrenta
    a Odoo en el balance de comprobación. No se toca.
  · ``abono_bcv`` -- lo que rebaja la cuenta por cobrar -- toma el euro.
    Mismo trato que el equivalente Binance, que tampoco se compara contra
    Odoo porque Odoo no conoce esa tasa.

Se descubrió rastreando los 91,27 de descuadre de la partida de pagos: 5
abonos cuya tasa implícita en Odoo coincidía al 0,02 % con el BCV-Euro, y
uno con el memo "Tasa € el abono de 70$".
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.models import Moneda
from cxc.web import app
from cxc.web.app import abono_cxc_en_euros

BCV_USD = Decimal("440.9657")
BCV_EUR = Decimal("510.49")
DIA = date(2026, 3, 12)


def _preparar(monkeypatch, euro: Decimal | None) -> None:
    # SerieTasas arranca el 2026-07-25 en producción: para la ventana
    # histórica (20-feb a 12-mar) nunca tiene el euro. Por eso la vía
    # llevaba meses muerta.
    monkeypatch.setattr(app, "get_bcv_euro_rate_for_datetime", lambda _d, _r: None)
    monkeypatch.setattr(app, "get_eur_rate_for_date", lambda _f, _r: euro)


def test_el_abono_se_acredita_al_euro(monkeypatch) -> None:
    """Pago 17 real: 16.606,59 Bs del 2026-03-12 sobre la orden S00020."""
    _preparar(monkeypatch, BCV_EUR)
    assert round(abono_cxc_en_euros(Decimal("16606.59"), DIA, [], []), 2) == Decimal("32.53")


def test_al_euro_se_acredita_menos_que_al_dolar(monkeypatch) -> None:
    """El sentido económico y el signo del cambio: el euro está por encima
    del dólar, así que el mismo bolívar vale menos dólares y la cuenta por
    cobrar sube."""
    _preparar(monkeypatch, BCV_EUR)
    abono = Decimal("16606.59")
    al_euro = abono_cxc_en_euros(abono, DIA, [], [])
    assert al_euro < abono / BCV_USD


def test_sin_euro_en_ninguna_fuente_devuelve_none(monkeypatch) -> None:
    """None es "no pude", no "cero". Quien llama sigue con el BCV-USD; si
    esto devolviera 0 se estaría dando por no cobrado un abono real."""
    _preparar(monkeypatch, None)
    assert abono_cxc_en_euros(Decimal("16606.59"), DIA, [], []) is None


def test_un_euro_en_cero_tampoco_es_una_tasa(monkeypatch) -> None:
    _preparar(monkeypatch, Decimal("0"))
    assert abono_cxc_en_euros(Decimal("16606.59"), DIA, [], []) is None


def test_un_abono_en_cero_no_produce_equivalente(monkeypatch) -> None:
    _preparar(monkeypatch, BCV_EUR)
    assert abono_cxc_en_euros(Decimal("0"), DIA, [], []) is None


def test_el_equivalente_bcv_no_es_asunto_de_esta_funcion() -> None:
    """La frontera que puso el usuario, escrita como test.

    Esta función solo produce la cifra que rebaja la CxC. El equivalente
    que se concilia contra Odoo se calcula en otro lado y sigue siendo
    BCV-USD -- si algún día alguien enruta ``equiv_usd_bcv`` por acá, la
    partida "Pagos: equivalente BCV contra Odoo" se va a descuadrar por
    ~16 % en las órdenes históricas."""
    from cxc.models import Vinculacion

    assert "equiv_usd_eur" not in Vinculacion.__dataclass_fields__
    assert "equiv_usd_bcv" in Vinculacion.__dataclass_fields__


def test_la_ventana_historica_la_define_un_solo_modulo() -> None:
    from cxc.engine.historical_pricing import (
        HISTORICAL_PRICE_LIST_END_EXCLUSIVE,
        HISTORICAL_PRICE_LIST_START,
    )

    assert date(2026, 2, 20) == HISTORICAL_PRICE_LIST_START
    assert date(2026, 3, 13) == HISTORICAL_PRICE_LIST_END_EXCLUSIVE


# --- El conjunto de órdenes históricas -------------------------------------


class _RepoConToggle:
    def __init__(self, activo: bool = True) -> None:
        self._activo = activo
        self.lecturas = 0

    def get_config(self, _clave: str) -> str:
        self.lecturas += 1
        return "true" if self._activo else "false"


class _Orden:
    def __init__(self, so_id: str, fecha: date) -> None:
        self.so_id = so_id
        self.fecha = fecha


def test_solo_entran_las_ordenes_de_la_ventana() -> None:
    from cxc.web.app import so_ids_en_ventana_historica

    ordenes = [
        _Orden("S00020", date(2026, 3, 9)),  # dentro
        _Orden("S00074", date(2026, 3, 12)),  # dentro, último día
        _Orden("S00092", date(2026, 3, 13)),  # fuera: el corte es exclusivo
        _Orden("S00566", date(2026, 7, 17)),  # fuera
    ]
    assert so_ids_en_ventana_historica(_RepoConToggle(), ordenes) == {"S00020", "S00074"}


def test_el_toggle_apagado_desactiva_la_via_entera() -> None:
    from cxc.web.app import so_ids_en_ventana_historica

    ordenes = [_Orden("S00020", date(2026, 3, 9))]
    assert so_ids_en_ventana_historica(_RepoConToggle(activo=False), ordenes) == set()


def test_el_toggle_se_lee_una_sola_vez() -> None:
    """No es cosmético: ``orden_en_periodo_historico`` consulta la config en
    cada llamada, así que recorrer 953 órdenes con ella eran 953 lecturas a
    la base dentro del reporte de saldos."""
    from cxc.web.app import so_ids_en_ventana_historica

    repo = _RepoConToggle()
    so_ids_en_ventana_historica(repo, [_Orden(f"S{i:05d}", date(2026, 3, 9)) for i in range(200)])
    assert repo.lecturas == 1


def test_una_orden_sin_fecha_no_rompe_el_conjunto() -> None:
    from cxc.web.app import so_ids_en_ventana_historica

    class _SinFecha:
        so_id = "S00001"
        fecha = None

    assert so_ids_en_ventana_historica(_RepoConToggle(), [_SinFecha()]) == set()


# --- Los tres caminos de pago tienen que decir lo mismo ---------------------
#
# El sistema arma "cuánto se pagó" por tres vías distintas, y la regla del
# euro tiene que entrar en las tres o dos páginas dirían números
# diferentes para la misma orden:
#
#   · Ventas -> ``valor_pagado_bcv_usd`` sobre las Vinculaciones
#   · Reporte de Saldos -> su propio bucle sobre las Vinculaciones
#   · Reporte de Saldos sin Vinculaciones -> ``_pagos_odoo_por_orden``
#
# Medido: aplicarla solo en las dos últimas dejaba Ventas sin moverse ni un
# centavo, que fue como se descubrió que faltaba la primera.


class _Vinc:
    def __init__(self, monto: str, equiv_bcv: str, moneda: Moneda = Moneda.VES) -> None:
        from datetime import datetime

        self.monto_aplicado = Decimal(monto)
        self.equiv_usd_bcv = Decimal(equiv_bcv)
        self.moneda_abono = moneda
        self.hora_pago_confirmada = datetime(2026, 3, 12)


def test_el_valor_pagado_en_euros_usa_la_tasa_euro(monkeypatch) -> None:
    from cxc.web.app import valor_pagado_bcv_usd_en_euros

    _preparar(monkeypatch, BCV_EUR)
    vincs = [_Vinc("16606.59", "37.66")]
    assert round(valor_pagado_bcv_usd_en_euros(vincs, [], []), 2) == Decimal("32.53")


def test_un_abono_en_dolares_no_se_toca(monkeypatch) -> None:
    """La vía euro es para bolívares. Un abono en USD ya está en dólares."""
    from cxc.web.app import valor_pagado_bcv_usd_en_euros

    _preparar(monkeypatch, BCV_EUR)
    vincs = [_Vinc("70.00", "70.00", moneda=Moneda.USD)]
    assert valor_pagado_bcv_usd_en_euros(vincs, [], []) == Decimal("70.00")


def test_sin_tasa_euro_se_conserva_el_equivalente_congelado(monkeypatch) -> None:
    """No se pierde el abono por no tener la tasa: queda el BCV-USD."""
    from cxc.web.app import valor_pagado_bcv_usd_en_euros

    _preparar(monkeypatch, None)
    vincs = [_Vinc("16606.59", "37.66")]
    assert valor_pagado_bcv_usd_en_euros(vincs, [], []) == Decimal("37.66")


def test_la_funcion_del_motor_sigue_intacta() -> None:
    """``engine.equivalents.valor_pagado_bcv_usd`` suma lo congelado tal
    cual, sin saber de euros -- es la que alimenta la conciliación contra
    Odoo. La variante en euros vive en la capa web justamente para no
    contaminarla."""
    from cxc.engine.equivalents import valor_pagado_bcv_usd

    vincs = [_Vinc("16606.59", "37.66")]
    assert valor_pagado_bcv_usd(vincs) == Decimal("37.66")
