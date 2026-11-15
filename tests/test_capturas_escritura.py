"""Escritor de Odoo (solo borradores) contra un Odoo falso: qué crea y qué nunca hace."""

from __future__ import annotations

import xmlrpc.client
from decimal import Decimal

import pytest

from cxc.odoo.escritura import (
    NOTA_BORRADOR,
    EscritorOdoo,
    EscrituraConfig,
    OdooEscrituraError,
)
from tests.capturas_dobles import OdooFalso

# ── interruptor y credencial ─────────────────────────────────────────────────


def _limpiar(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("CAPTURAS_ODOO_ESCRITURA", "ODOO_WRITE_USERNAME", "ODOO_WRITE_PASSWORD"):
        monkeypatch.delenv(v, raising=False)


def test_sin_interruptor_no_hay_escritura(monkeypatch: pytest.MonkeyPatch) -> None:
    _limpiar(monkeypatch)
    monkeypatch.setenv("ODOO_WRITE_USERNAME", "u")
    monkeypatch.setenv("ODOO_WRITE_PASSWORD", "p")
    assert EscrituraConfig.from_env() is None


def test_interruptor_sin_credencial_no_hay_escritura(monkeypatch: pytest.MonkeyPatch) -> None:
    _limpiar(monkeypatch)
    monkeypatch.setenv("CAPTURAS_ODOO_ESCRITURA", "1")
    assert EscrituraConfig.from_env() is None
    monkeypatch.setenv("ODOO_WRITE_USERNAME", "u")
    assert EscrituraConfig.from_env() is None  # falta la contraseña


def test_interruptor_apagado_explicito(monkeypatch: pytest.MonkeyPatch) -> None:
    _limpiar(monkeypatch)
    monkeypatch.setenv("CAPTURAS_ODOO_ESCRITURA", "0")
    monkeypatch.setenv("ODOO_WRITE_USERNAME", "u")
    monkeypatch.setenv("ODOO_WRITE_PASSWORD", "p")
    assert EscrituraConfig.from_env() is None


def test_activa_usa_la_url_y_base_de_lectura_pero_credencial_aparte(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limpiar(monkeypatch)
    monkeypatch.setenv("CAPTURAS_ODOO_ESCRITURA", "1")
    monkeypatch.setenv("ODOO_WRITE_USERNAME", "escritor@x.test")
    monkeypatch.setenv("ODOO_WRITE_PASSWORD", "clave-escritura")
    cfg = EscrituraConfig.from_env()
    assert cfg is not None
    assert cfg.url == "https://odoo.invalido.test" and cfg.db == "cxc_test"  # del entorno hermético
    assert cfg.username == "escritor@x.test"  # NO la de lectura (tests@cxc.test)


def test_sin_url_de_lectura_no_hay_escritura(monkeypatch: pytest.MonkeyPatch) -> None:
    _limpiar(monkeypatch)
    monkeypatch.setenv("CAPTURAS_ODOO_ESCRITURA", "1")
    monkeypatch.setenv("ODOO_WRITE_USERNAME", "u")
    monkeypatch.setenv("ODOO_WRITE_PASSWORD", "p")
    monkeypatch.delenv("ODOO_URL")
    assert EscrituraConfig.from_env() is None


# ── contacto borrador ────────────────────────────────────────────────────────


def test_contacto_se_crea_como_borrador_sin_rango_de_cliente() -> None:
    odoo = OdooFalso()
    pid = EscritorOdoo(odoo).crear_contacto_borrador(
        {
            "nombre": " Pedro ",
            "telefono": "0414-1234567",
            "email": "p@x.com",
            "notas": "zona norte",
            "lat": 10.48,
            "lon": -66.9,
        },
        "BOT-5",
    )
    assert pid == 101
    v = odoo.creados_de("res.partner")[0]
    assert v["name"] == "Pedro" and v["customer_rank"] == 0 and v["ref"] == "BOT-5"
    assert v["comment"].startswith(NOTA_BORRADOR) and "zona norte" in v["comment"]
    assert v["partner_latitude"] == 10.48 and v["partner_longitude"] == -66.9


def test_contacto_sin_campo_gps_en_odoo_se_crea_sin_gps() -> None:
    odoo = OdooFalso(res_partner__fields_get={"name": {}})
    EscritorOdoo(odoo).crear_contacto_borrador(
        {"nombre": "P", "telefono": "04141234567", "lat": 1, "lon": 2}, "BOT-1"
    )
    assert "partner_latitude" not in odoo.creados_de("res.partner")[0]


def test_contacto_sin_ubicacion_no_envia_gps() -> None:
    odoo = OdooFalso()
    EscritorOdoo(odoo).crear_contacto_borrador({"nombre": "P", "telefono": "04141234567"}, "BOT-1")
    assert "partner_latitude" not in odoo.creados_de("res.partner")[0]


def test_contacto_idempotente_por_marca() -> None:
    odoo = OdooFalso(res_partner__search=[55])
    assert (
        EscritorOdoo(odoo).crear_contacto_borrador(
            {"nombre": "P", "telefono": "04141234567"}, "BOT-1"
        )
        == 55
    )
    assert odoo.creados == []


# ── cliente nuevo desde un pedido ────────────────────────────────────────────


def test_cliente_nuevo_con_entrega_distinta_crea_el_hijo_de_entrega() -> None:
    odoo = OdooFalso()
    escritor = EscritorOdoo(odoo)
    pid = escritor.crear_cliente(
        {
            "nombre": "Taller",
            "rif": "J-123456789",
            "email": "t@x.com",
            "telefono": "0414",
            "direccion_fiscal": "Av 1",
            "direccion_entrega": "Galpón 3",
            "lat": 10.4,
            "lon": -66.9,
        },
        "BOT-2",
    )
    padre, hijo = odoo.creados_de("res.partner")
    assert pid == 101 and padre["vat"] == "J-123456789" and padre["customer_rank"] == 1
    assert (
        padre["company_type"] == "company" and "partner_latitude" not in padre
    )  # el GPS va a la entrega
    assert (
        hijo["parent_id"] == 101 and hijo["type"] == "delivery" and hijo["partner_latitude"] == 10.4
    )


def test_cliente_persona_natural_y_gps_en_el_fiscal_si_no_hay_entrega() -> None:
    odoo = OdooFalso()
    EscritorOdoo(odoo).crear_cliente(
        {"nombre": "Juan", "rif": "V-12345678", "lat": 1, "lon": 2}, "BOT-3"
    )
    (v,) = odoo.creados_de("res.partner")
    assert v["company_type"] == "person" and v["partner_latitude"] == 1.0


def test_cliente_idempotente() -> None:
    odoo = OdooFalso(res_partner__search=[9])
    assert EscritorOdoo(odoo).crear_cliente({"nombre": "T", "rif": "J-1"}, "BOT-2") == 9
    assert odoo.creados == []


def test_entrega_de() -> None:
    assert EscritorOdoo(OdooFalso(res_partner__search=[44])).entrega_de(3) == 44
    assert EscritorOdoo(OdooFalso()).entrega_de(3) is None


# ── pedido ───────────────────────────────────────────────────────────────────


def test_pedido_en_borrador_sin_precio_deja_que_odoo_aplique_la_lista() -> None:
    odoo = OdooFalso()
    oid = EscritorOdoo(odoo).crear_pedido(
        partner_id=5,
        lineas=[
            {"product_id": 9, "cantidad": Decimal("2"), "precio": None},
            {"product_id": 10, "cantidad": "1.5", "precio": "12.50"},
        ],
        marca="BOT-7",
        nota="urgente",
        vendedor_id=7,
        shipping_id=44,
        fecha_entrega="2026-11-20",
    )
    assert oid == 101
    v = odoo.creados_de("sale.order")[0]
    assert v["partner_id"] == 5 and v["client_order_ref"] == "BOT-7" and v["user_id"] == 7
    assert v["partner_shipping_id"] == 44 and v["commitment_date"] == "2026-11-20 12:00:00"
    assert v["order_line"][0] == (0, 0, {"product_id": 9, "product_uom_qty": 2.0})  # sin price_unit
    assert v["order_line"][1][2]["price_unit"] == 12.5
    assert "state" not in v  # nunca se confirma


def test_pedido_idempotente_por_marca() -> None:
    odoo = OdooFalso(sale_order__search=[321])
    assert EscritorOdoo(odoo).crear_pedido(partner_id=5, lineas=[], marca="BOT-7") == 321
    assert odoo.creados == []


def test_nombre_pedido() -> None:
    assert EscritorOdoo(OdooFalso()).nombre_pedido(5) == "S00700"
    assert EscritorOdoo(OdooFalso(sale_order__read=[])).nombre_pedido(5) == ""


# ── pago ─────────────────────────────────────────────────────────────────────


def _pago(escritor: EscritorOdoo, **kw: object) -> int:
    args = {
        "partner_id": 5,
        "monto": Decimal("100.5"),
        "journal_id": 14,
        "moneda_id": 1,
        "es_cliente": True,
        "marca": "BOT-9",
        "memo": "BOT-9 · ref R1",
    }
    args.update(kw)
    return escritor.crear_pago(**args)  # type: ignore[arg-type]


def test_pago_de_cliente_es_inbound_en_borrador() -> None:
    odoo = OdooFalso()
    _pago(EscritorOdoo(odoo), fecha="2026-11-12")
    v = odoo.creados_de("account.payment")[0]
    assert (v["payment_type"], v["partner_type"], v["amount"]) == ("inbound", "customer", 100.5)
    assert v["journal_id"] == 14 and v["currency_id"] == 1 and v["date"] == "2026-11-12"
    assert v["memo"] == "BOT-9 · ref R1"
    assert not any(
        m == "account.payment" and me in ("action_post", "write") for m, me, _a, _k in odoo.llamadas
    )


def test_pago_a_proveedor_es_outbound() -> None:
    odoo = OdooFalso()
    _pago(EscritorOdoo(odoo), es_cliente=False)
    v = odoo.creados_de("account.payment")[0]
    assert (v["payment_type"], v["partner_type"]) == ("outbound", "supplier")


@pytest.mark.parametrize(
    "campos,esperado",
    [
        ({"memo": {}, "ref": {}}, "memo"),
        ({"payment_reference": {}, "ref": {}}, "payment_reference"),
        ({"ref": {}}, "ref"),
    ],
)
def test_el_campo_del_memo_se_detecta_con_fields_get(
    campos: dict[str, object], esperado: str
) -> None:
    odoo = OdooFalso(account_payment__fields_get=campos)
    _pago(EscritorOdoo(odoo))
    assert odoo.creados_de("account.payment")[0][esperado] == "BOT-9 · ref R1"


def test_pago_sin_campo_de_memo_no_se_crea_a_ciegas() -> None:
    odoo = OdooFalso(account_payment__fields_get={"amount": {}})
    with pytest.raises(OdooEscrituraError) as e:
        _pago(EscritorOdoo(odoo))
    assert not e.value.transitorio and odoo.creados == []


def test_pago_idempotente_busca_la_marca_en_el_memo() -> None:
    odoo = OdooFalso(account_payment__search=[444])
    assert _pago(EscritorOdoo(odoo)) == 444
    assert odoo.creados == []
    dominio = odoo.llamadas_a("account.payment", "search")[0][0]
    assert dominio == [["memo", "ilike", "BOT-9"]]


def test_pago_sin_fecha_no_la_envia() -> None:
    odoo = OdooFalso()
    _pago(EscritorOdoo(odoo))
    assert "date" not in odoo.creados_de("account.payment")[0]


# ── resoluciones ─────────────────────────────────────────────────────────────


def test_moneda_usd_exacto_y_cualquier_bolivar() -> None:
    e = EscritorOdoo(OdooFalso())
    assert e.moneda_id("USD") == 1 and e.moneda_id("$") == 1
    assert e.moneda_id("Bs") == 2 and e.moneda_id("VEF") == 2
    assert e.moneda_id("EUR") is None


def test_vendedor_por_login_o_email_sin_adivinar() -> None:
    assert EscritorOdoo(OdooFalso()).vendedor_id("a@x.com") == 7
    assert EscritorOdoo(OdooFalso(res_users__search=[1, 2])).vendedor_id("a@x.com") is None
    assert EscritorOdoo(OdooFalso(res_users__search=[])).vendedor_id("a@x.com") is None
    assert EscritorOdoo(OdooFalso()).vendedor_id("") is None


def test_busqueda_por_igualdad_escapa_los_comodines() -> None:
    odoo = OdooFalso()
    EscritorOdoo(odoo).vendedor_id("juan_perez@x.com")
    EscritorOdoo(odoo).proveedores_por_nombre("100% Aceite_A")
    dominios = [
        a[0]
        for a in odoo.llamadas_a("res.users", "search") + odoo.llamadas_a("res.partner", "search")
    ]
    assert ["login", "=ilike", "juan\\_perez@x.com"] in dominios[0]
    assert ["name", "=ilike", "100\\% Aceite\\_A"] in dominios[1]


def test_partners_por_rif_busca_las_variantes() -> None:
    odoo = OdooFalso(res_partner__search=[3])
    assert EscritorOdoo(odoo).partners_por_rif("j-12345678-9") == [3]
    dominio = odoo.llamadas_a("res.partner", "search")[0][0]
    assert dominio[0][0] == "vat" and "J123456789" in dominio[0][2]


# ── errores: legibles y sin filtrar detalles ─────────────────────────────────


def test_fault_de_odoo_es_error_permanente_con_mensaje_generico(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fault = xmlrpc.client.Fault(2, "Traceback secreto: columna interna x_credito")
    odoo = OdooFalso(res_partner__create=fault)
    with pytest.raises(OdooEscrituraError) as e:
        EscritorOdoo(odoo).crear_contacto_borrador(
            {"nombre": "P", "telefono": "04141234567"}, "BOT-1"
        )
    assert not e.value.transitorio
    assert "secreto" not in str(e.value) and "x_credito" not in str(e.value)
    assert "secreto" in caplog.text  # el detalle técnico va al log


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("lento"),
        ConnectionError("caído"),
        xmlrpc.client.ProtocolError("u", 502, "bad", {}),
    ],
)
def test_fallos_de_red_son_transitorios(exc: Exception) -> None:
    odoo = OdooFalso(res_partner__search=exc)
    with pytest.raises(OdooEscrituraError) as e:
        EscritorOdoo(odoo).crear_contacto_borrador(
            {"nombre": "P", "telefono": "04141234567"}, "BOT-1"
        )
    assert e.value.transitorio
    assert "caído" not in str(e.value)


def test_fields_get_se_consulta_una_sola_vez_por_modelo() -> None:
    odoo = OdooFalso()
    e = EscritorOdoo(odoo)
    e.crear_contacto_borrador(
        {"nombre": "P", "telefono": "04141234567", "lat": 1, "lon": 2}, "BOT-1"
    )
    e.crear_contacto_borrador(
        {"nombre": "Q", "telefono": "04141234568", "lat": 1, "lon": 2}, "BOT-2"
    )
    assert len(odoo.llamadas_a("res.partner", "fields_get")) == 1
