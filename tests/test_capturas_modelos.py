"""Validación pura de las capturas de Telegram (sin red ni base)."""

from __future__ import annotations

import pytest

from cxc.capturas.modelos import (
    CapturaInvalida,
    a_decimal,
    clase_moneda,
    digitos,
    normalizar_rif,
    validar,
    variantes_rif,
)


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("j-12345678-9", "J-123456789"),
        ("J 123456789", "J-123456789"),
        ("  V-1234567 ", "V-1234567"),
        ("g200012345", "G-200012345"),
    ],
)
def test_normalizar_rif(entrada: str, esperado: str) -> None:
    assert normalizar_rif(entrada) == esperado


@pytest.mark.parametrize("malo", ["", "ABC", "J-", "12345", "X-123456"])
def test_rif_invalido(malo: str) -> None:
    with pytest.raises(CapturaInvalida) as e:
        normalizar_rif(malo)
    assert e.value.faltan == ["rif"]


def test_variantes_rif_cubren_los_formatos_de_odoo() -> None:
    v = variantes_rif("j-12345678-9")
    assert {"J-123456789", "J123456789", "J-12345678-9", "123456789"} <= set(v)


@pytest.mark.parametrize(
    "codigo,clase",
    [
        ("USD", "USD"),
        ("usd", "USD"),
        ("$", "USD"),
        ("VES", "VES"),
        ("Bs", "VES"),
        ("VEF", "VES"),
        ("VED", "VES"),
        ("Bs.", "VES"),
        ("EUR", "EUR"),
    ],
)
def test_clase_moneda_usd_exacto_y_familia_bolivar(codigo: str, clase: str) -> None:
    assert clase_moneda(codigo) == clase


def test_a_decimal() -> None:
    assert str(a_decimal("12.50", "Monto")) == "12.50"
    for malo in ("abc", "NaN", "Infinity", None):
        with pytest.raises(CapturaInvalida):
            a_decimal(malo, "Monto")


def test_digitos() -> None:
    assert digitos("0414-123 45 67") == "04141234567"


# ── contacto ─────────────────────────────────────────────────────────────────


def test_contacto_ok_y_faltantes() -> None:
    assert validar("contacto", {"nombre": "Pedro", "telefono": "0414-1234567"}) == []
    with pytest.raises(CapturaInvalida) as e:
        validar("contacto", {"nombre": "Pedro"})
    assert e.value.faltan == ["telefono"]
    with pytest.raises(CapturaInvalida):
        validar("contacto", {"nombre": "Pedro", "telefono": "123"})


# ── pedido ───────────────────────────────────────────────────────────────────


def _pedido(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "cliente": "Taller",
        "lineas": [{"producto": "Aceite", "cantidad": "2"}],
    }
    base.update(kw)
    return base


def test_pedido_valido() -> None:
    assert validar("pedido", _pedido(rif="j-12345678-9")) == []


@pytest.mark.parametrize(
    "draft",
    [
        _pedido(lineas=[]),
        _pedido(lineas="x"),
        _pedido(lineas=[{"cantidad": "1"}]),
        _pedido(lineas=[{"producto": "A", "cantidad": "0"}]),
        _pedido(lineas=[{"producto": "A", "cantidad": "x"}]),
        _pedido(lineas=[{"producto": "A", "cantidad": "1", "precio_unitario": "-1"}]),
        _pedido(rif="ABC"),
        {"lineas": [{"producto": "A", "cantidad": "1"}]},
    ],
)
def test_pedido_invalido(draft: dict[str, object]) -> None:
    with pytest.raises(CapturaInvalida):
        validar("pedido", draft)


# ── pagos ────────────────────────────────────────────────────────────────────


def test_pago_completo_y_cliente_por_cualquiera_de_las_tres_vias() -> None:
    base = {"monto": "10", "moneda": "USD", "referencia": "R1"}
    for via in ({"cliente": "A"}, {"rif": "J-1234567"}, {"pedido_ref": "S001"}):
        assert validar("pago", {**base, **via}) == []


def test_pago_incompleto_lista_lo_que_falta() -> None:
    faltan = validar("pago", {"monto": "10"})
    assert "moneda" in faltan and "referencia" in faltan
    assert any(f.startswith("cliente") for f in faltan)


@pytest.mark.parametrize("monto", ["0", "-5", "abc"])
def test_pago_monto_invalido_de_fondo(monto: str) -> None:
    with pytest.raises(CapturaInvalida):
        validar("pago", {"monto": monto, "moneda": "USD"})


def test_pago_proveedor() -> None:
    assert validar("pago_proveedor", {"proveedor": "L", "monto": "1", "moneda": "USD"}) == []
    assert validar("pago_proveedor", {"monto": "1"}) == ["proveedor", "moneda"]


def test_tipo_desconocido() -> None:
    with pytest.raises(CapturaInvalida):
        validar("evento", {})
