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
from cxc.rates import Tasas
from cxc.web.app import abono_cxc_en_euros

BCV_USD = Decimal("440.9657")
BCV_EUR = Decimal("510.49")
DIA = date(2026, 3, 12)


def _tasas(**por_dia: str) -> Tasas:
    """Un histórico con solo los días indicados, como el que resuelve la
    tasa euro en producción."""
    return Tasas(
        historicas=[
            {"fecha": f, "tasa_bcv_usd": str(BCV_USD), "tasa_bcv_euro": e}
            for f, e in por_dia.items()
        ]
    )


def test_el_abono_se_acredita_al_euro() -> None:
    """Pago 17 real: 16.606,59 Bs del 2026-03-12 sobre la orden S00020."""
    eq = abono_cxc_en_euros(Decimal("16606.59"), DIA, _tasas(**{"2026-03-12": "510.49"}))
    assert round(eq, 2) == Decimal("32.53")


def test_al_euro_se_acredita_menos_que_al_dolar() -> None:
    """El sentido económico y el signo del cambio: el euro está por encima
    del dólar, así que el mismo bolívar vale menos dólares y la cuenta por
    cobrar sube."""
    abono = Decimal("16606.59")
    al_euro = abono_cxc_en_euros(abono, DIA, _tasas(**{"2026-03-12": "510.49"}))
    assert al_euro < abono / BCV_USD


def test_sin_euro_en_ninguna_fuente_devuelve_none() -> None:
    """None es "no pude", no "cero". Quien llama sigue con el BCV-USD; si
    esto devolviera 0 se estaría dando por no cobrado un abono real."""
    assert abono_cxc_en_euros(Decimal("16606.59"), DIA, Tasas()) is None


def test_un_euro_en_cero_tampoco_es_una_tasa() -> None:
    assert abono_cxc_en_euros(Decimal("16606.59"), DIA, Tasas()) is None


def test_un_abono_en_cero_no_produce_equivalente() -> None:
    assert abono_cxc_en_euros(Decimal("0"), DIA, _tasas(**{"2026-03-12": "510.49"})) is None


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
    """Una orden con su lista, que es lo que el stub viejo no modelaba.

    Sin `lista_precios` el stub no podía expresar la regla del precio, y por eso los
    tests de abajo encodaban la regla VIEJA —solo la ventana de fechas— sin que se
    notara. Toda orden real tiene el campo, aunque sea vacío.
    """

    def __init__(self, so_id: str, fecha: date, lista: str = "4") -> None:
        self.so_id = so_id
        self.fecha = fecha
        self.lista_precios = lista


def test_desde_el_12_sep_ninguna_orden_es_historica_para_los_montos_reales() -> None:
    """Decisión del usuario (quiz, 12-sep-2026, pregunta 5): «Esa lista histórica es
    solo para fines de auditoría, igual que el equivalente de sus pagos a tasa euro.
    No debe modificar los montos reales».

    Hasta ese día este archivo fijaba qué órdenes entraban a la vía euro (la ventana,
    las sin lista, las de lista USD real que no). Todo eso sigue siendo cierto para la
    **auditoría**, que lee `lista_historica_habilitada_para_auditoria`; para los
    montos reales el conjunto es vacío, con el toggle en cualquier posición, y sin
    leer la configuración.
    """
    from cxc.web.app import so_ids_en_ventana_historica

    ordenes = [
        _Orden("S00020", date(2026, 3, 9)),  # en la ventana, lista no-USD
        _Orden("S00088", date(2026, 3, 13), lista=""),  # sin lista
        _Orden("S00100", date(2026, 6, 1)),  # fuera de la ventana
    ]
    for activo in (True, False):
        repo = _RepoConToggle(activo=activo)
        assert so_ids_en_ventana_historica(repo, ordenes) == set()
        assert repo.lecturas == 0, "los montos reales ya no leen el selector"


def test_el_selector_sigue_vivo_para_la_auditoria() -> None:
    from cxc.web.app import (
        is_historical_pricelist_enabled,
        lista_historica_habilitada_para_auditoria,
    )

    assert lista_historica_habilitada_para_auditoria(_RepoConToggle(activo=True)) is True
    assert lista_historica_habilitada_para_auditoria(_RepoConToggle(activo=False)) is False
    assert is_historical_pricelist_enabled(_RepoConToggle(activo=True)) is False


def test_una_orden_sin_fecha_no_rompe_el_conjunto() -> None:
    from cxc.web.app import so_ids_en_ventana_historica

    class _SinFecha:
        so_id = "S00001"
        fecha = None
        lista_precios = "4"

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


def test_el_valor_pagado_en_euros_usa_la_tasa_euro() -> None:
    from cxc.web.app import valor_pagado_bcv_usd_en_euros

    vincs = [_Vinc("16606.59", "37.66")]
    total = valor_pagado_bcv_usd_en_euros(vincs, _tasas(**{"2026-03-12": "510.49"}))
    assert round(total, 2) == Decimal("32.53")


def test_un_abono_en_dolares_no_se_toca() -> None:
    """La vía euro es para bolívares. Un abono en USD ya está en dólares."""
    from cxc.web.app import valor_pagado_bcv_usd_en_euros

    vincs = [_Vinc("70.00", "70.00", moneda=Moneda.USD)]
    total = valor_pagado_bcv_usd_en_euros(vincs, _tasas(**{"2026-03-12": "510.49"}))
    assert total == Decimal("70.00")


def test_sin_tasa_euro_se_conserva_el_equivalente_congelado() -> None:
    """No se pierde el abono por no tener la tasa: queda el BCV-USD."""
    from cxc.web.app import valor_pagado_bcv_usd_en_euros

    vincs = [_Vinc("16606.59", "37.66")]
    assert valor_pagado_bcv_usd_en_euros(vincs, Tasas()) == Decimal("37.66")


def test_la_funcion_del_motor_sigue_intacta() -> None:
    """``engine.equivalents.valor_pagado_bcv_usd`` suma lo congelado tal
    cual, sin saber de euros -- es la que alimenta la conciliación contra
    Odoo. La variante en euros vive en la capa web justamente para no
    contaminarla."""
    from cxc.engine.equivalents import valor_pagado_bcv_usd

    vincs = [_Vinc("16606.59", "37.66")]
    assert valor_pagado_bcv_usd(vincs) == Decimal("37.66")


# --- Los días que la serie no tiene ----------------------------------------
#
# A ``TasasHistoricasAuditoria`` le faltan 4 días de calendario
# (2026-08-14, 08-15, 08-31 y 09-07) y dos de ellos son fechas de abono de
# órdenes históricas: el pago 1624 de la orden S00058 y el 1714 de la
# S00078. Sin tasa euro esos abonos caían al BCV-USD y se acreditaban un
# ~16 % de más.
#
# La solución no es rellenar esos cuatro días a mano: es buscar el último
# día publicado antes, que además es cómo funciona la tasa. El BCV publica
# una tasa que rige hasta la siguiente, y los datos lo confirman -- de los
# 60 fines de semana cargados, los 60 repiten exactamente la del viernes.
# Buscar hacia atrás cubre también el hueco que aparezca mañana.


def test_un_dia_ausente_toma_la_ultima_tasa_publicada() -> None:
    """El pago 1624: 67.493,05 Bs el lunes 2026-08-31, día que no está en
    la tabla. Rige la del viernes 28, que es 922,6912."""
    eq = abono_cxc_en_euros(
        Decimal("67493.05"), date(2026, 8, 31), _tasas(**{"2026-08-28": "922.6912"})
    )
    assert round(eq, 2) == Decimal("73.15")


def test_el_dia_exacto_gana_sobre_el_anterior() -> None:
    eq = abono_cxc_en_euros(
        Decimal("67493.05"),
        date(2026, 8, 31),
        _tasas(**{"2026-08-31": "929.0908", "2026-08-28": "922.6912"}),
    )
    assert round(eq, 2) == round(Decimal("67493.05") / Decimal("929.0908"), 2)


def test_no_se_busca_indefinidamente_hacia_atras() -> None:
    """Si no hay tasa en una semana, lo que falta es la carga y no un
    feriado: mejor devolver None y quedarse en BCV-USD que acreditar con
    una tasa de hace un mes."""
    lejano = _tasas(**{"2026-07-01": "900.00"})
    assert abono_cxc_en_euros(Decimal("67493.05"), date(2026, 8, 31), lejano) is None


def test_el_borde_de_la_semana_todavia_cuenta() -> None:
    borde = _tasas(**{"2026-08-24": "922.6912"})
    assert abono_cxc_en_euros(Decimal("67493.05"), date(2026, 8, 31), borde) is not None
