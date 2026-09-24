"""Clasificación Comercial/Industrial (ítem 6, septiembre 2026): "≥2 de 4"

señales -- vendedor, cliente, lista de nacimiento y volumen de líneas
industriales -- deciden si una orden es Comercial o Industrial. Ninguna
señal manda sola.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from cxc.engine.clasificacion import (
    COMERCIAL,
    INDUSTRIAL,
    clasificar_comercial_industrial,
    litros_de_lineas_industriales,
)

_BASE = {
    "vendedor_industrial": False,
    "cliente_industrial": False,
    "lista_industrial": False,
    "litros_industriales": Decimal("0"),
}


def _clasificar(**overrides):
    kw = {**_BASE, **overrides}
    return clasificar_comercial_industrial(**kw)


def test_ninguna_senal_es_comercial():
    r = _clasificar()
    assert r.clasificacion == COMERCIAL
    assert r.criterios_cumplidos == 0


def test_una_sola_senal_sigue_siendo_comercial():
    r = _clasificar(vendedor_industrial=True)
    assert r.clasificacion == COMERCIAL
    assert r.criterios_cumplidos == 1


def test_dos_senales_ya_es_industrial():
    r = _clasificar(vendedor_industrial=True, cliente_industrial=True)
    assert r.clasificacion == INDUSTRIAL
    assert r.criterios_cumplidos == 2


def test_las_cuatro_senales_es_industrial():
    r = _clasificar(
        vendedor_industrial=True,
        cliente_industrial=True,
        lista_industrial=True,
        litros_industriales=Decimal("100"),
    )
    assert r.clasificacion == INDUSTRIAL
    assert r.criterios_cumplidos == 4


def test_volumen_exacto_en_el_umbral_cuenta():
    r = _clasificar(
        vendedor_industrial=True,
        litros_industriales=Decimal("18.92"),
    )
    assert r.volumen_industrial is True
    assert r.clasificacion == INDUSTRIAL


def test_volumen_justo_debajo_del_umbral_no_cuenta():
    r = _clasificar(
        vendedor_industrial=True,
        litros_industriales=Decimal("18.91"),
    )
    assert r.volumen_industrial is False
    assert r.clasificacion == COMERCIAL


def test_umbral_configurable():
    r = _clasificar(litros_industriales=Decimal("50"), umbral_litros=Decimal("100"))
    assert r.volumen_industrial is False
    r2 = _clasificar(litros_industriales=Decimal("50"), umbral_litros=Decimal("50"))
    assert r2.volumen_industrial is True


def _linea(producto: str, cantidad: str, categoria: str):
    return SimpleNamespace(producto=producto, cantidad=Decimal(cantidad), categoria_madre=categoria)


def test_litros_solo_suma_lineas_industriales():
    lineas = [
        _linea("P1", "10", "Industrial"),
        _linea("P2", "10", "Comercial"),
    ]
    volumenes = {"P1": 1.0, "P2": 5.0}
    total = litros_de_lineas_industriales(lineas, volumenes)
    assert total == Decimal("10")


def test_litros_producto_sin_volumen_en_catalogo_aporta_cero():
    lineas = [_linea("P_SIN_DATO", "10", "Industrial")]
    total = litros_de_lineas_industriales(lineas, {})
    assert total == Decimal("0")


def test_litros_sin_lineas_industriales_es_cero():
    lineas = [_linea("P1", "10", "Comercial")]
    total = litros_de_lineas_industriales(lineas, {"P1": 5.0})
    assert total == Decimal("0")


def test_categoria_madre_case_insensitive():
    lineas = [_linea("P1", "10", "industrial")]
    total = litros_de_lineas_industriales(lineas, {"P1": 2.0})
    assert total == Decimal("20")
