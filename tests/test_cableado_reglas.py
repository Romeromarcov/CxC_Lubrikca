"""Todo campo de una regla tiene que leerlo el motor.

Lo pidió el usuario varias veces (septiembre 2026): "revises bien todas
las reglas/configuración y que esté todo correctamente cableado". Este
test congela esa auditoría: si alguien agrega un campo a una regla y no
lo cablea, o lo expone en Configuración sin que el motor lo lea, falla
acá en vez de descubrirse meses después con montos mal calculados.

Lo que encontró la auditoría cuando se escribió:

  · ``DescuentoDiferencialCambiario.tipo_calculo`` -- un SEGUNDO campo
    para lo mismo que ``tipo_diferencial``, que es el único que el motor
    lee. El formulario ofrecía los dos, así que se podía poner
    "Porcentaje Fijo" en una regla ``equiparar_binance`` y no pasaba
    nada. En producción los tres registros estaban consistentes
    (fijo↔fijo_35_ves_usd, variable↔equiparar_binance), así que era una
    trampa latente, no un cálculo malo. Se quitó del formulario y ahora
    se deriva al guardar.

  · ``DescuentoFidelizacion`` -- modelo muerto: sin tabla, sin método de
    repositorio y sin uso en el motor. Las reglas de fidelidad reales
    (FID_SINOCO_5000L, FID_GLOBAL_2500L) viven en ``descuentos_volumen``,
    que sí está cableado por completo.

Un primer intento de esta auditoría dio falsos positivos por escanear
solo ``discounts.py``: ``monedas_aplicables`` parecía muerto en cuatro
reglas y en realidad se lee en ``effective_dating.py``. Por eso el test
mira TODO el paquete del motor.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

import cxc.models as M

_MOTOR = Path(__file__).resolve().parents[1] / "src" / "cxc" / "engine"
_FUENTE = "".join(p.read_text(encoding="utf-8") for p in sorted(_MOTOR.glob("*.py")))

# Campos que son identidad o presentación, no comportamiento.
_NO_APLICA = {"regla_id", "descripcion", "activo", "nombre"}

# Campos DERIVADOS de otro: no los lee el motor a propósito, se completan
# al guardar a partir del campo que sí manda. Ver el comentario en el
# modelo -- cada entrada dice de quién se deriva.
_DERIVADOS = {("DescuentoDiferencialCambiario", "tipo_calculo")}  # <- tipo_diferencial

# Modelos que describen un RESULTADO del motor, no una configuración.
_SALIDAS = {"DescuentoAplicado"}


def _modelos_de_regla() -> list[str]:
    return [
        n
        for n in sorted(dir(M))
        if n.startswith(("Descuento", "Promocion", "Regla", "Exclusion"))
        and n not in _SALIDAS
        and dataclasses.is_dataclass(getattr(M, n))
    ]


@pytest.mark.parametrize("modelo", _modelos_de_regla())
def test_cada_campo_de_regla_lo_lee_el_motor(modelo: str) -> None:
    campos = [f.name for f in dataclasses.fields(getattr(M, modelo))]
    huerfanos = [
        c
        for c in campos
        if c not in _NO_APLICA
        and (modelo, c) not in _DERIVADOS
        and not re.search(rf"\.{re.escape(c)}\b|[\"']{re.escape(c)}[\"']", _FUENTE)
    ]
    assert not huerfanos, (
        f"{modelo}: el motor nunca lee {huerfanos}. "
        "Un campo configurable que nadie lee es una trampa: el usuario lo "
        "cambia y no pasa nada. Cablealo o sacalo del modelo."
    )


def test_la_auditoria_cubre_todas_las_reglas() -> None:
    """Guarda contra el falso verde: si el descubrimiento de modelos deja
    de encontrar reglas, los tests de arriba pasarían sin revisar nada."""
    modelos = _modelos_de_regla()
    assert len(modelos) >= 9, f"solo se encontraron {len(modelos)} modelos de regla"
    for esperado in ("DescuentoVolumen", "DescuentoProntoPago", "PromocionPrimeraCompra"):
        assert esperado in modelos


def test_el_fallback_de_primera_compra_tiene_id_propio() -> None:
    """El 2 % que aplica sin ninguna promoción configurada no es "sin regla".

    Encontrado en la auditoría exhaustiva: 28 componentes de descuento
    salían del desglose sin regla identificada, lo que se lee como "el
    motor regala un 2 % que nadie configuró". Son $1.077,32 en órdenes
    TODAS entre el 26-feb y el 26-mar -- anteriores al 01-abr, que es
    cuando arrancan las dos promociones reales. O sea que el respaldo
    histórico hizo exactamente lo suyo, pero el desglose no lo decía.
    """
    from cxc.engine.discounts import _REGLA_FALLBACK_INDUSTRIAL

    assert _REGLA_FALLBACK_INDUSTRIAL
    assert "FALLBACK" in _REGLA_FALLBACK_INDUSTRIAL
