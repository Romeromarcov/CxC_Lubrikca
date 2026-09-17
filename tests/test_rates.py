"""Una sola respuesta a "¿cuál era la tasa ese día?".

Pedido del usuario (septiembre 2026): "unificar todo el tema de tasas para
evitar en el futuro errores y duplicidad".

Los tests fijan la política de precedencia, que es lo que antes cada
consumidor decidía por su cuenta -- y de ahí salieron cuatro bugs que eran
el mismo bug con distinta ropa.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cxc.rates import Tasas


def _hist(**por_dia: tuple[str, str, str]) -> list[dict]:
    return [
        {
            "fecha": f,
            "tasa_bcv_usd": u,
            "tasa_bcv_euro": e,
            "tasa_binance_promedio_diario": b,
        }
        for f, (u, e, b) in por_dia.items()
    ]


def _serie(*capturas: tuple[str, str]) -> list[dict]:
    return [{"timestamp": t, "tasa_bcv": v} for t, v in capturas]


# --- la precedencia --------------------------------------------------------


def test_el_historico_oficial_manda_sobre_la_serie_horaria() -> None:
    """El histórico está alineado con lo que publica el BCV; la serie
    horaria es el intradía crudo y de noche ya trae la tasa de mañana."""
    t = Tasas(
        historicas=_hist(**{"2026-08-21": ("779.9522", "911.2182", "890.0")}),
        serie=_serie(("2026-08-21 23:00:00", "784.6633")),
    )
    assert t.bcv_usd(date(2026, 8, 21)) == Decimal("779.9522")


def test_sin_historico_sirve_la_serie_del_mismo_dia() -> None:
    t = Tasas(serie=_serie(("2026-08-21 06:00:00", "779.9522")))
    assert t.bcv_usd(date(2026, 8, 21)) == Decimal("779.9522")


def test_de_la_serie_se_toma_la_captura_de_la_apertura() -> None:
    """La de la noche ya es la tasa de mañana: ese fue el bug del scraper."""
    t = Tasas(
        serie=_serie(
            ("2026-08-21 06:00:00", "779.9522"),
            ("2026-08-21 12:00:00", "779.9522"),
            ("2026-08-21 23:00:00", "784.6633"),
        )
    )
    assert t.bcv_usd(date(2026, 8, 21)) == Decimal("779.9522")


def test_una_captura_de_otro_dia_no_resuelve_este() -> None:
    """Bug real de agosto 2026: una N/C de marzo se resolvía con la primera
    fila que el scraper llegó a escribir, meses después."""
    t = Tasas(serie=_serie(("2026-08-21 06:00:00", "779.9522")))
    assert t.bcv_usd(date(2026, 3, 12)) is None


# --- el arrastre -----------------------------------------------------------


def test_la_tasa_rige_hasta_que_se_publica_la_siguiente() -> None:
    """El lunes 2026-08-31 no está en la tabla; rige la del viernes 28."""
    t = Tasas(historicas=_hist(**{"2026-08-28": ("794.9917", "922.6912", "0")}))
    assert t.bcv_usd(date(2026, 8, 31)) == Decimal("794.9917")


def test_el_arrastre_tiene_tope() -> None:
    """Más de una semana sin tasa no es un feriado: falta la carga, y
    arrastrar una tasa de otro mes sería peor que decir "no sé"."""
    t = Tasas(historicas=_hist(**{"2026-07-01": ("742.23", "860.0", "0")}))
    assert t.bcv_usd(date(2026, 8, 31)) is None


def test_binance_no_se_arrastra() -> None:
    """Binance se mueve todo el día: el promedio de ayer no describe hoy.
    El BCV sí se arrastra porque su tasa RIGE hasta la siguiente."""
    t = Tasas(historicas=_hist(**{"2026-08-28": ("794.9917", "922.6912", "890.0")}))
    assert t.binance(date(2026, 8, 31)) is None
    assert t.bcv_usd(date(2026, 8, 31)) is not None


# --- el euro ---------------------------------------------------------------


def test_una_tasa_euro_estancada_no_cuenta() -> None:
    """Bug real de agosto 2026: ``res.currency.rate`` quedó congelado casi
    un mes mientras el USD sí se movía, y el scraper lo repetía hora tras
    hora sin señal de error. El euro nunca vale menos de 1,05 dólares BCV
    en la serie real."""
    t = Tasas(historicas=_hist(**{"2026-08-21": ("779.9522", "780.00", "0")}))
    assert t.bcv_eur(date(2026, 8, 21)) is None


def test_un_euro_plausible_si_cuenta() -> None:
    t = Tasas(historicas=_hist(**{"2026-08-21": ("779.9522", "911.2182", "0")}))
    assert t.bcv_eur(date(2026, 8, 21)) == Decimal("911.2182")


# --- sin dato no es cero ---------------------------------------------------


def test_sin_ninguna_fuente_devuelve_none() -> None:
    """La regla que este proyecto aprendió a los golpes: "sin datos" no es
    "cero" ni "la última que haya"."""
    t = Tasas()
    assert t.bcv_usd(date(2026, 8, 21)) is None
    assert t.bcv_eur(date(2026, 8, 21)) is None
    assert t.binance(date(2026, 8, 21)) is None


def test_una_fecha_ilegible_no_rompe() -> None:
    t = Tasas(historicas=_hist(**{"2026-08-21": ("779.9522", "911.2182", "0")}))
    assert t.bcv_usd("no es una fecha") is None
    assert t.bcv_usd("") is None


def test_acepta_date_datetime_y_texto() -> None:
    t = Tasas(historicas=_hist(**{"2026-08-21": ("779.9522", "911.2182", "0")}))
    esperado = Decimal("779.9522")
    assert t.bcv_usd(date(2026, 8, 21)) == esperado
    assert t.bcv_usd(datetime(2026, 8, 21, 14, 30)) == esperado
    assert t.bcv_usd("2026-08-21") == esperado
    assert t.bcv_usd("2026-08-21T14:30:00") == esperado


# --- Binance roto ----------------------------------------------------------
#
# Bug real (pago Odoo 29, marzo 2026): la hoja usaba locale es_ES y gspread
# "numericizó" 451,5072 quitando la coma como si fuera separador de miles,
# guardando 4515072. Un Binance ~10.000 veces la tasa BCV del mismo día
# producía montos de un centavo.
#
# Antes eso quedaba tapado por accidente, porque se prefería SerieTasas
# sobre el histórico. Al invertir la precedencia el dato malo afloró -- y
# el arreglo correcto no es volver al orden viejo sino rechazar el dato
# donde sea que aparezca.


def test_un_binance_disparatado_se_descarta() -> None:
    t = Tasas(
        historicas=_hist(**{"2026-03-18": ("451.5072", "0", "5216600")}),
        serie=_serie(("2026-03-18 12:00:00", "451.5072")),
    )
    assert t.binance(date(2026, 3, 18)) is None


def test_se_descarta_aunque_la_fila_corrupta_no_traiga_su_propio_usd() -> None:
    """El caso real: la fila del histórico solo tenía el Binance. Sin una
    vara externa pasaba el filtro."""
    t = Tasas(
        historicas=[{"fecha": "2026-03-18", "tasa_binance_promedio_diario": "5216600"}],
        serie=[
            {
                "timestamp": "2026-03-18 12:00:00",
                "tasa_bcv": "451.5072",
                "tasa_binance": "521.66",
            }
        ],
    )
    assert t.binance(date(2026, 3, 18)) == Decimal("521.66")


def test_un_diferencial_alto_pero_real_no_se_descarta() -> None:
    """La banda está para atrapar datos rotos, no diferenciales raros: en
    la serie real Binance llega a estar ~50 % sobre el oficial."""
    t = Tasas(historicas=_hist(**{"2026-08-05": ("755.1552", "880.0", "885.04")}))
    assert t.binance(date(2026, 8, 5)) == Decimal("885.04")
