"""Las 63 combinaciones posibles entre reglas, clasificadas y fijadas.

El usuario pidió cubrir "dos casos por cada regla y dos por cada
combinación posible entre reglas". Al enumerarlas contra producción
resultó que no todas existen, y por dos razones distintas que conviene no
confundir:

  · **13 ocurren en producción** y se verificaron con 2 casos reales cada
    una, contrastando el detalle guardado contra los totales y la base.
    Ese barrido encontró el bug de ``ncs_calculadas`` -- ver
    ``test_ncs_tras_exclusiones.py``.
  · **32 involucran ``producto``**, que no tiene ninguna regla cargada:
    ese camino del motor nunca se ejerció con datos reales.
  · **12 son IMPOSIBLES por diseño**, no por falta de datos: las
    exclusiones activas (``volumen`` ↔ ``recurrencia`` y
    ``primera_compra`` ↔ ``recurrencia``) anulan el menor del par, así que
    Recompra no puede convivir con Volumen ni con Primera Compra.
  · **6 son alcanzables y todavía no ocurrieron.**

Este archivo fija las dos últimas categorías. Las imposibles importan más
de lo que parece: si mañana alguien desactiva una exclusión sin querer, el
motor empezaría a sumar dos descuentos que el negocio decidió que son
alternativos, y nada lo avisaría.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cxc.engine.discounts import _aplicar_exclusiones
from cxc.models import ExclusionRegla

# Las dos exclusiones activas en producción.
_EXCLUSIONES = [
    ExclusionRegla(regla_tipo_a="volumen", regla_tipo_b="recurrencia", activo=True),
    ExclusionRegla(regla_tipo_a="primera_compra", regla_tipo_b="recurrencia", activo=True),
]

_TODOS = ("primera_compra", "recurrencia", "contado", "volumen", "bcv_completo", "producto")


def _valores(*activos: str, monto: str = "100") -> dict[str, Decimal]:
    return {k: (Decimal(monto) if k in activos else Decimal("0")) for k in _TODOS}


# --- Las 12 imposibles -------------------------------------------------------

_IMPOSIBLES = [
    ("primera_compra", "recurrencia"),
    ("recurrencia", "volumen"),
    ("contado", "primera_compra", "recurrencia"),
    ("primera_compra", "recurrencia", "volumen"),
    ("bcv_completo", "primera_compra", "recurrencia"),
    ("contado", "recurrencia", "volumen"),
    ("bcv_completo", "recurrencia", "volumen"),
    ("contado", "primera_compra", "recurrencia", "volumen"),
    ("bcv_completo", "contado", "primera_compra", "recurrencia"),
    ("bcv_completo", "primera_compra", "recurrencia", "volumen"),
    ("bcv_completo", "contado", "recurrencia", "volumen"),
    ("bcv_completo", "contado", "primera_compra", "recurrencia", "volumen"),
]


@pytest.mark.parametrize("combo", _IMPOSIBLES, ids=[" + ".join(c) for c in _IMPOSIBLES])
def test_recompra_no_convive_con_volumen_ni_con_primera_compra(combo) -> None:
    """Ninguna de estas 12 puede darse: la exclusión anula uno de los dos."""
    resultado = _aplicar_exclusiones(_valores(*combo), _EXCLUSIONES)
    vivos = {k for k, v in resultado.items() if v > 0}
    assert not ("recurrencia" in vivos and "volumen" in vivos)
    assert not ("recurrencia" in vivos and "primera_compra" in vivos)


def test_la_exclusion_conserva_el_de_mayor_valor() -> None:
    """"Se aplica el de mayor valor", como dice el panel de Configuración --
    no siempre gana el mismo lado del par."""
    gana_recompra = _aplicar_exclusiones(
        {**_valores(), "recurrencia": Decimal("50"), "primera_compra": Decimal("10")},
        _EXCLUSIONES,
    )
    assert gana_recompra["recurrencia"] == Decimal("50")
    assert gana_recompra["primera_compra"] == Decimal("0")

    gana_primera = _aplicar_exclusiones(
        {**_valores(), "recurrencia": Decimal("10"), "primera_compra": Decimal("50")},
        _EXCLUSIONES,
    )
    assert gana_primera["primera_compra"] == Decimal("50")
    assert gana_primera["recurrencia"] == Decimal("0")


def test_un_empate_conserva_el_primero_del_par() -> None:
    """``va >= vb`` anula el segundo: con montos iguales gana ``regla_tipo_a``."""
    r = _aplicar_exclusiones(
        {**_valores(), "primera_compra": Decimal("30"), "recurrencia": Decimal("30")},
        _EXCLUSIONES,
    )
    assert r["primera_compra"] == Decimal("30")
    assert r["recurrencia"] == Decimal("0")


# --- Las 6 alcanzables que todavía no ocurrieron ----------------------------

_ALCANZABLES = [
    ("contado", "volumen"),
    ("contado", "recurrencia"),
    ("bcv_completo", "volumen"),
    ("bcv_completo", "recurrencia"),
    ("contado", "primera_compra", "volumen"),
    ("bcv_completo", "contado", "volumen"),
]


@pytest.mark.parametrize("combo", _ALCANZABLES, ids=[" + ".join(c) for c in _ALCANZABLES])
def test_las_combinaciones_alcanzables_se_suman_enteras(combo) -> None:
    """Ninguna exclusión las toca, así que los descuentos conviven y se
    suman. Si mañana se agrega una exclusión que las alcance, este test lo
    avisa antes de que aparezca en producción."""
    resultado = _aplicar_exclusiones(_valores(*combo), _EXCLUSIONES)
    assert {k for k, v in resultado.items() if v > 0} == set(combo)
    assert sum(resultado.values()) == Decimal("100") * len(combo)


def test_recompra_si_convive_con_contado_y_diferencial() -> None:
    """Es la única compañía que Recompra admite -- y de hecho ocurre:
    ``bcv_completo + contado + recurrencia`` tiene 2 casos reales."""
    r = _aplicar_exclusiones(_valores("bcv_completo", "contado", "recurrencia"), _EXCLUSIONES)
    assert {k for k, v in r.items() if v > 0} == {"bcv_completo", "contado", "recurrencia"}


# --- Producto: 32 combinaciones que el motor nunca ejerció ------------------


def test_producto_no_esta_excluido_de_ninguna_regla() -> None:
    """No hay reglas de producto cargadas en producción, así que las 32
    combinaciones que lo incluyen nunca se ejercieron. Cuando se carguen,
    ninguna exclusión las va a recortar -- se suman a lo demás."""
    for otra in ("primera_compra", "recurrencia", "contado", "volumen", "bcv_completo"):
        r = _aplicar_exclusiones(_valores("producto", otra), _EXCLUSIONES)
        assert r["producto"] == Decimal("100"), f"producto quedó anulado junto a {otra}"


def test_una_exclusion_inactiva_no_recorta_nada() -> None:
    inactivas = [
        ExclusionRegla(regla_tipo_a="volumen", regla_tipo_b="recurrencia", activo=False),
        ExclusionRegla(regla_tipo_a="primera_compra", regla_tipo_b="recurrencia", activo=False),
    ]
    r = _aplicar_exclusiones(_valores("primera_compra", "recurrencia", "volumen"), inactivas)
    assert {k for k, v in r.items() if v > 0} == {"primera_compra", "recurrencia", "volumen"}
