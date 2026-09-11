"""La mina 9: un precio que sale de una regla vencida, ahora contado.

`_precio_fijo_en_lista` busca la regla de precio que cubre la fecha pedida y, si
ninguna la cubre, **devuelve la primera igual**. Nunca dice «no hay precio»
mientras exista alguna regla, aunque todas hayan vencido hace meses. Y la única
señal que dice «este teórico no es confiable» se enciende *solo* cuando devuelve
«no hay precio».

Por eso una lista con todas las reglas vencidas entrega precios de abril con la
misma cara que una lista al día — sin excepción, sin log, sin bandera. Fue la mina
más difícil de cazar de las nueve: el clasificador AST busca ``except`` vacíos y
ceros por defecto, y esto no es ninguno de los dos. Devuelve un dato real,
verdadero para otra fecha.

**El valor devuelto no cambia** (eso mueve montos y es decisión del usuario). Lo
que estos tests fijan es que deje de pasar en silencio.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from cxc.odoo.price import OdooPriceResolver


def _resolver(reglas):
    """Un resolver cuyo Odoo devuelve ``reglas`` para cualquier consulta de items."""

    def execute(modelo, metodo, args, kwargs=None):
        if modelo == "product.pricelist.item":
            return reglas
        return []

    return OdooPriceResolver(execute, {"USD": 4, "BCV": 5})


def _regla(precio, desde=None, hasta=None):
    return {
        "fixed_price": str(precio),
        "date_start": f"{desde} 00:00:00" if desde else False,
        "date_end": f"{hasta} 23:59:59" if hasta else False,
    }


def test_una_regla_vigente_no_se_reporta_como_vencida() -> None:
    r = _resolver([_regla("100", "2026-01-01", "2026-12-31")])
    assert r._precio_fijo_en_lista(5, 1, date(2026, 6, 1)) == Decimal("100")
    assert r.precios_de_regla_vencida() == []
    assert "Ningun precio" in r.resumen_de_reglas_vencidas()


def test_una_regla_vencida_devuelve_el_precio_y_queda_registrada() -> None:
    """El valor no cambia: sigue siendo el de la regla vieja."""
    r = _resolver([_regla("100", "2026-01-01", "2026-04-30")])
    assert r._precio_fijo_en_lista(5, 1, date(2026, 9, 1)) == Decimal("100")

    registradas = r.precios_de_regla_vencida()
    assert len(registradas) == 1
    v = registradas[0]
    assert v.producto == "1"
    assert v.lista == 5
    assert v.fecha_pedida == "2026-09-01"
    assert (v.desde, v.hasta) == ("2026-01-01", "2026-04-30")
    assert v.reglas_disponibles == 1


def test_el_registro_dice_de_cuanto_se_paso() -> None:
    """Por eso se guarda la vigencia y no solo un conteo.

    Una regla que venció ayer y una que venció en abril son decisiones distintas,
    y un contador no las distingue.
    """
    r = _resolver([_regla("100", "2026-01-01", "2026-04-30")])
    r._precio_fijo_en_lista(5, 1, date(2026, 9, 1))
    v = r.precios_de_regla_vencida()[0]
    assert date.fromisoformat(v.fecha_pedida) > date.fromisoformat(v.hasta)


def test_una_regla_que_todavia_no_empezo_tambien_cuenta() -> None:
    """«Vencida» acá es «no cubre la fecha», en los dos sentidos.

    Una regla que arranca el mes que viene devuelta para una orden de hoy es el
    mismo problema con el signo invertido: un precio verdadero para otra fecha.
    """
    r = _resolver([_regla("100", "2026-10-01")])
    assert r._precio_fijo_en_lista(5, 1, date(2026, 9, 1)) == Decimal("100")
    assert len(r.precios_de_regla_vencida()) == 1


def test_sin_fecha_pedida_no_hay_nada_que_incumplir() -> None:
    """Sin fecha el filtro de vigencia no corre, así que la regla siempre calza.

    Importa que no se reporte: un precio pedido sin fecha no está usando una regla
    vencida, está usando la regla que hay. Contarlo inflaría el hallazgo.
    """
    r = _resolver([_regla("100", "2026-01-01", "2026-04-30")])
    assert r._precio_fijo_en_lista(5, 1, None) == Decimal("100")
    assert r.precios_de_regla_vencida() == []


def test_sin_ninguna_regla_devuelve_none_y_no_se_registra() -> None:
    """El caso que SÍ enciende la señal vieja, y por eso no se duplica acá."""
    r = _resolver([])
    assert r._precio_fijo_en_lista(5, 1, date(2026, 9, 1)) is None
    assert r.precios_de_regla_vencida() == []


def test_con_varias_vencidas_se_elige_la_primera_y_se_dice_cuantas_habia() -> None:
    """``reglas_disponibles`` distingue «había una vieja» de «había ocho».

    Ocho reglas vencidas dicen que la lista entera quedó atrás; una sola puede ser
    un producto que se dejó de vender.
    """
    reglas = [
        _regla("100", "2026-01-01", "2026-02-28"),
        _regla("120", "2026-03-01", "2026-04-30"),
        _regla("140", "2026-05-01", "2026-06-30"),
    ]
    r = _resolver(reglas)
    assert r._precio_fijo_en_lista(5, 1, date(2026, 9, 1)) == Decimal("100")
    v = r.precios_de_regla_vencida()[0]
    assert v.reglas_disponibles == 3
    assert (v.desde, v.hasta) == ("2026-01-01", "2026-02-28")


def test_el_resumen_trae_el_denominador_y_un_ejemplo() -> None:
    """Un conteo sin ejemplo no se puede ir a mirar.

    Misma regla que el resto del blindaje: el instrumento dice sobre cuántos habló
    y da por dónde empezar, en vez de un número suelto.
    """
    r = _resolver([_regla("100", "2026-01-01", "2026-04-30")])
    r._precio_fijo_en_lista(5, 1, date(2026, 9, 1))
    r._precio_fijo_en_lista(5, 2, date(2026, 9, 2))
    resumen = r.resumen_de_reglas_vencidas()
    assert "2 precio(s)" in resumen
    assert "2 producto(s)" in resumen
    assert "[5]" in resumen
    assert "2026-09-01" in resumen


def test_el_registro_es_una_copia_y_no_se_puede_vaciar_desde_afuera() -> None:
    """Si el llamador pudiera mutar la lista, el conteo dejaría de ser confiable."""
    r = _resolver([_regla("100", "2026-01-01", "2026-04-30")])
    r._precio_fijo_en_lista(5, 1, date(2026, 9, 1))
    afuera = r.precios_de_regla_vencida()
    afuera.clear()
    assert len(r.precios_de_regla_vencida()) == 1


def test_una_regla_con_vigencia_abierta_cubre_cualquier_fecha_posterior() -> None:
    r = _resolver([_regla("100", "2026-01-01")])
    assert r._precio_fijo_en_lista(5, 1, date(2030, 1, 1)) == Decimal("100")
    assert r.precios_de_regla_vencida() == []


def test_una_regla_sin_ninguna_fecha_nunca_esta_vencida() -> None:
    """Es el caso más común en Odoo: reglas sin vigencia declarada."""
    r = _resolver([_regla("100")])
    assert r._precio_fijo_en_lista(5, 1, date(2026, 9, 1)) == Decimal("100")
    assert r.precios_de_regla_vencida() == []


@pytest.mark.parametrize("fecha", [date(2026, 4, 30), date(2026, 1, 1)])
def test_los_bordes_de_la_vigencia_estan_adentro(fecha) -> None:
    """El primer y el último día cuentan como vigentes.

    Si el borde quedara afuera, el instrumento reportaría como vencidas reglas que
    sí cubrían la fecha — y un hallazgo inflado es tan inútil como uno que falta.
    """
    r = _resolver([_regla("100", "2026-01-01", "2026-04-30")])
    assert r._precio_fijo_en_lista(5, 1, fecha) == Decimal("100")
    assert r.precios_de_regla_vencida() == []
