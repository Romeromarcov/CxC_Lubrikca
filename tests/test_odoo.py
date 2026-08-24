"""Tests del adaptador Odoo: mapeo puro + enriquecimiento de relaciones.

Calibrado contra el Odoo 18 QA (ver docs/ODOO_MAPEO.md). El ``execute`` es un
fake que despacha por modelo/dominio; sin red.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from cxc.config import OdooConfig
from cxc.models import Moneda
from cxc.odoo.client import (
    OdooXmlRpcReader,
    map_cliente,
    map_factura,
    map_linea,
    map_orden,
    map_pago,
    parse_payment_term_days,
)


# --- map_* (puras, sobre dicts ya enriquecidos) -----------------------------
def test_map_cliente() -> None:
    c = map_cliente({"id": 181, "name": "ACME", "vendedor_email": "rep@x.com"})
    assert c.cliente_id == "181"
    assert c.vendedor_email == "rep@x.com"
    assert c.nombre == "ACME"


def test_map_cliente_subcontacto_sin_nombre_usa_contacto_principal() -> None:
    """Bug real S00768: el partner de la orden es un subcontacto (ej.

    dirección de entrega) con ``name=False`` en Odoo -- antes se guardaba
    literal ``str(False) == "False"``. Ahora resuelve al contacto principal
    vía ``commercial_partner_id`` (campo nativo de Odoo, mismo criterio que
    usa Odoo en contabilidad para agrupar subcontactos)."""
    c = map_cliente(
        {
            "id": 1563,
            "name": False,
            "commercial_partner_id": [1455, "Inversiones El Rey Jesucristo 41930 C.A"],
            "vendedor_email": "",
        }
    )
    assert c.cliente_id == "1563"
    assert c.nombre == "Inversiones El Rey Jesucristo 41930 C.A"
    assert c.nombre != "False"


def test_map_cliente_sin_nombre_ni_commercial_partner_queda_vacio() -> None:
    """Sin ninguna fuente de nombre disponible, queda vacío -- NUNCA

    ``str(False)``."""
    c = map_cliente({"id": 999, "name": False, "vendedor_email": ""})
    assert c.nombre == ""


def test_map_orden_usa_name_como_so_id() -> None:
    o = map_orden(
        {
            "id": 553,
            "name": "S00553",
            "partner_id": [181, "ACME"],
            "date_order": "2026-06-12 15:49:16",
            "fecha_entrega": "2026-06-13",
            "amount_total": "1650.44",
            "pricelist_id": [5, "Precio USD Pago VES"],
            "vendedor_email": "rep@x.com",
            "es_primera_compra": True,
            "invoice_status": "invoiced",
            "factura_id": "3835",
            "delivery_status": "full",
        }
    )
    assert o.so_id == "S00553"
    assert o.cliente_id == "181"
    assert o.fecha.isoformat() == "2026-06-11"
    assert o.entregada_completa is True
    assert o.fecha_entrega is not None and o.fecha_entrega.isoformat() == "2026-06-13"
    assert o.monto_total == Decimal("1650.44")
    assert o.lista_precios == "5"


def test_parse_payment_term_days() -> None:
    assert parse_payment_term_days("30 días") == 30
    assert parse_payment_term_days("21 dias") == 21
    assert parse_payment_term_days("Immediate Payment") == 0
    assert parse_payment_term_days("Contado") == 0
    assert parse_payment_term_days("") == 0
    assert parse_payment_term_days("Sin patron reconocible") == 0


def test_map_orden_dias_credito_desde_payment_term() -> None:
    o = map_orden(
        {
            "id": 553,
            "name": "S00553",
            "partner_id": [181, "ACME"],
            "date_order": "2026-06-12 15:49:16",
            "amount_total": "100.00",
            "pricelist_id": [5, "x"],
            "invoice_status": "no",
            "dias_credito": 30,
        }
    )
    assert o.dias_credito == 30


def test_map_orden_sin_dias_credito_default_cero() -> None:
    o = map_orden(
        {
            "id": 553,
            "name": "S00553",
            "partner_id": [181, "ACME"],
            "date_order": "2026-06-12 15:49:16",
            "amount_total": "100.00",
            "pricelist_id": [5, "x"],
            "invoice_status": "no",
        }
    )
    assert o.dias_credito == 0


def test_map_orden_override_fecha_historica_csv() -> None:
    from datetime import date

    o = map_orden(
        {
            "id": 4,
            "name": "S00004",
            "partner_id": [181, "ACME"],
            "date_order": "2026-07-20 10:00:00",
            "fecha_entrega": "2026-07-20",
            "amount_total": "232.98",
            "pricelist_id": [4, "USD"],
            "vendedor_email": "rep@x.com",
            "es_primera_compra": False,
            "invoice_status": "invoiced",
            "delivery_status": "full",
        }
    )
    assert o.so_id == "S00004"
    assert o.fecha == date(2026, 3, 9)
    assert o.es_primera_compra is False


def test_map_orden_no_facturada() -> None:
    o = map_orden(
        {
            "id": 552,
            "name": "S00552",
            "partner_id": [1, "X"],
            "date_order": "2026-06-11 20:11:51",
            "amount_total": "373.62",
            "pricelist_id": [5, "x"],
            "invoice_status": "no",
        }
    )
    assert o.facturada is False
    assert o.factura_id is None
    assert o.fecha_entrega is None


def test_map_orden_facturada_por_factura_id_aunque_invoice_status_diga_to_invoice() -> None:
    # Caso real S00817: Odoo reporta invoice_status="to invoice" aunque ya
    # existe una factura posted con pago aplicado, porque una Nota de
    # Crédito de corrección (creada vía "revertir factura") dispara el
    # mismo qty_invoiced/invoice_status que una reversión completa.
    o = map_orden(
        {
            "id": 817,
            "name": "S00817",
            "partner_id": [1, "X"],
            "date_order": "2026-06-11 20:11:51",
            "amount_total": "56704.73",
            "pricelist_id": [5, "x"],
            "invoice_status": "to invoice",
            "factura_id": 10131,
        }
    )
    assert o.facturada is True
    assert o.factura_id == "10131"


def test_map_orden_entrega_parcial_no_ancla_el_plazo() -> None:
    # Aunque haya una fecha de entrega, si no está completa no arranca el plazo.
    o = map_orden(
        {
            "id": 1,
            "name": "S1",
            "partner_id": [1, "X"],
            "date_order": "2026-06-01 10:00:00",
            "fecha_entrega": "2026-06-05",
            "amount_total": "100",
            "pricelist_id": [5, "x"],
            "invoice_status": "no",
            "delivery_status": "partial",
            "tiene_devolucion": True,
        }
    )
    assert o.estado_entrega == "partial"
    assert o.entregada_completa is False
    assert o.fecha_entrega is None  # el plazo de contado no arrancó
    assert o.tiene_devolucion is True


def test_map_linea_usa_nombre_de_so() -> None:
    ln = map_linea(
        {
            "id": 1463,
            "order_id": [553, "S00553"],
            "product_id": [906, "ELITE"],
            "marca": "Global Oil",
            "categoria": "Comercial",
            "product_uom_qty": "20",
            "price_unit": "71.13",
        }
    )
    assert ln.so_id == "S00553"
    assert ln.producto == "906"
    assert ln.marca == "Global Oil"
    assert ln.categoria == "Comercial"
    assert ln.cantidad == Decimal("20")


def test_map_pago_moneda_y_journal() -> None:
    p = map_pago(
        {
            "id": 729,
            "partner_id": [181, "X"],
            "amount": "1650.44",
            "currency_id": [1, "USD"],
            "journal_id": [29, "Efectivo USD"],
            "date": "2026-06-30",
            "vendedor_email": "rep@x.com",
        }
    )
    assert p.moneda == Moneda.USD
    assert p.metodo_pago == "29"
    assert p.fecha_pago == datetime(2026, 6, 30, 0, 0)
    assert p.monto == Decimal("1650.44")


def test_map_factura_usd_y_nota_credito() -> None:
    fact = map_factura(
        {
            "invoice_origin": "S00553",
            "amount_total_signed_usd": "1650.42",
            "move_type": "out_invoice",
        }
    )
    assert fact == ("S00553", Decimal("1650.42"), Decimal("0"))
    nc = map_factura(
        {
            "invoice_origin": "S00553",
            "amount_total_signed_usd": "-50.00",
            "move_type": "out_refund",
        }
    )
    assert nc == ("S00553", Decimal("0"), Decimal("50.00"))


# --- Reader con enriquecimiento (fake execute que despacha por modelo) ------
def _config() -> OdooConfig:
    return OdooConfig(url="http://x", db="d", username="u", password="p")


class FakeExecute:
    """Despacha por (modelo, método, dominio) devolviendo datos canónicos."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.calls: list[tuple[str, str, list[Any]]] = []

    def __call__(self, model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        self.calls.append((model, method, args))
        if model == "sale.order" and method == "search_read":
            domain = args[0]
            es_primeras = any("partner_id" in str(c) for c in domain)
            return self.data.get("ordenes_primeras" if es_primeras else "ordenes", [])
        if model == "stock.picking" and method == "search_read":
            domain = args[0]
            es_devolucion = any("return_id" in str(c) for c in domain)
            return self.data.get("devoluciones" if es_devolucion else "pickings", [])
        key = {
            ("res.partner", "search_read"): "partners",
            ("res.partner", "read"): "partners_read",
            ("res.users", "read"): "users",
            ("sale.order.line", "search_read"): "lineas",
            ("product.product", "read"): "productos",
            ("account.payment", "search_read"): "pagos",
            ("account.move", "search_read"): "facturas",
            ("account.move.line", "search_read"): "move_lines",
            ("account.account", "read"): "accounts",
        }.get((model, method))
        return self.data.get(key, [])


def test_changed_clientes_resuelve_login() -> None:
    fake = FakeExecute(
        {
            "partners": [{"id": 181, "name": "ACME", "user_id": [13, "TORO"]}],
            "users": [{"id": 13, "login": "ruta07@gmail.com"}],
        }
    )
    reader = OdooXmlRpcReader(_config(), execute=fake)
    clientes = reader.changed_clientes(None)
    assert clientes[0].vendedor_email == "ruta07@gmail.com"


def test_changed_ordenes_enriquece_todo() -> None:
    fake = FakeExecute(
        {
            "ordenes": [
                {
                    "id": 553,
                    "name": "S00553",
                    "partner_id": [181, "ACME"],
                    "date_order": "2026-06-12 15:49:16",
                    "amount_total": 1650.44,
                    "pricelist_id": [5, "Precio USD Pago VES"],
                    "user_id": [13, "TORO"],
                    "invoice_status": "invoiced",
                    "delivery_status": "full",
                }
            ],
            "users": [{"id": 13, "login": "ruta07@gmail.com"}],
            "pickings": [
                {"sale_id": [553, "S00553"], "date_done": "2026-06-13 10:00:00", "state": "done"}
            ],
            "ordenes_primeras": [
                {"name": "S00553", "partner_id": [181, "ACME"], "date_order": "2026-06-12 15:49:16"}
            ],
            "facturas": [{"id": 3835, "invoice_origin": "S00553"}],
        }
    )
    reader = OdooXmlRpcReader(_config(), execute=fake)
    o = reader.changed_ordenes(datetime(2026, 6, 1))[0]
    assert o.so_id == "S00553"
    assert o.vendedor_email == "ruta07@gmail.com"
    assert o.fecha_entrega is not None and o.fecha_entrega.isoformat() == "2026-06-13"
    assert o.es_primera_compra is True
    assert o.facturada is True
    assert o.factura_id == "3835"
    assert o.lista_precios == "5"
    assert o.dias_credito == 0  # sin payment_term_id en el fixture
    # El dominio delta de la primera consulta usa write_date con margen de 48h.
    primera = fake.calls[0]
    assert primera[2][0] == [["write_date", ">", "2026-05-30 00:00:00"]]


def test_changed_ordenes_calcula_dias_credito_del_payment_term() -> None:
    fake = FakeExecute(
        {
            "ordenes": [
                {
                    "id": 553,
                    "name": "S00553",
                    "partner_id": [181, "ACME"],
                    "date_order": "2026-06-12 15:49:16",
                    "amount_total": 1650.44,
                    "pricelist_id": [5, "x"],
                    "user_id": [13, "TORO"],
                    "invoice_status": "no",
                    "delivery_status": "",
                    "payment_term_id": [7, "30 días"],
                }
            ],
            "users": [{"id": 13, "login": "ruta07@gmail.com"}],
            "pickings": [],
            "ordenes_primeras": [],
            "facturas": [],
        }
    )
    reader = OdooXmlRpcReader(_config(), execute=fake)
    o = reader.changed_ordenes(None)[0]
    assert o.dias_credito == 30


def test_changed_lineas_resuelve_marca_y_categoria_raiz() -> None:
    fake = FakeExecute(
        {
            "lineas": [
                {
                    "id": 1463,
                    "order_id": [553, "S00553"],
                    "product_id": [906, "ELITE"],
                    "product_uom_qty": 20.0,
                    "price_unit": 71.13,
                    "qty_delivered": 18.0,
                }
            ],
            "productos": [
                {
                    "id": 906,
                    "brand_id": False,
                    "categ_id": [506, "Comercial / Elite / Sintetico / Gasolina"],
                }
            ],
        }
    )
    reader = OdooXmlRpcReader(_config(), execute=fake)
    ln = reader.changed_lineas(None)[0]
    assert ln.so_id == "S00553"
    assert ln.marca == ""  # brand_id vacío en QA
    assert ln.categoria == "Comercial"  # raíz del árbol
    assert ln.cantidad == Decimal("20")
    assert ln.cantidad_entregada == Decimal("18")  # neto de devoluciones


def test_lineas_vigentes_por_orden_agrupa_por_so_name() -> None:
    """Hallazgo real orden S00792 (agosto 2026): ``changed_lineas`` no puede

    detectar una línea BORRADA en Odoo (sin ``write_date`` de un registro
    inexistente). Este método sí -- devuelve el set de ids VIGENTES ahora
    mismo para reconciliar el espejo local."""
    fake = FakeExecute(
        {
            "lineas": [
                {"id": 1463, "order_id": [553, "S00553"]},
                {"id": 1464, "order_id": [553, "S00553"]},
                {"id": 1500, "order_id": [700, "S00700"]},
            ],
        }
    )
    reader = OdooXmlRpcReader(_config(), execute=fake)
    vigentes = reader.lineas_vigentes_por_orden(["S00553", "S00700", "S00999"])
    assert vigentes["S00553"] == {"1463", "1464"}
    assert vigentes["S00700"] == {"1500"}
    assert vigentes["S00999"] == set()  # sin líneas vigentes en Odoo -> vacío


def test_lineas_vigentes_por_orden_vacio_sin_llamar_odoo() -> None:
    fake = FakeExecute({})
    reader = OdooXmlRpcReader(_config(), execute=fake)
    assert reader.lineas_vigentes_por_orden([]) == {}
    assert fake.calls == []


def test_changed_pagos_resuelve_vendedor_y_journal() -> None:
    fake = FakeExecute(
        {
            "pagos": [
                {
                    "id": 729,
                    "partner_id": [181, "X"],
                    "amount": 1650.44,
                    "currency_id": [1, "USD"],
                    "journal_id": [29, "Efectivo USD"],
                    "date": "2026-06-30",
                }
            ],
            "partners_read": [{"id": 181, "user_id": [13, "TORO"]}],
            "users": [{"id": 13, "login": "ruta07@gmail.com"}],
        }
    )
    reader = OdooXmlRpcReader(_config(), execute=fake)
    p = reader.changed_pagos(None)[0]
    assert p.metodo_pago == "29"
    assert p.moneda == Moneda.USD
    assert p.vendedor_email == "ruta07@gmail.com"


def test_changed_pagos_resta_devolucion_embebida_en_el_mismo_asiento() -> None:
    """Caso real (agosto 2026, cliente Inversiones Sai 2006, C.A): el

    cliente pagó de más y la diferencia se le devolvió por banco, todo
    registrado en un asiento manual de 3 líneas (débito recibos
    pendientes, crédito AR parcial, crédito banco por la devolución) en
    vez del flujo normal "Registrar Pago" + conciliar. El monto que el
    sistema usa para FIFO/saldos debe ser el NETO (lo que de verdad se
    aplicó a Cuentas por Cobrar), no el bruto -- si no, queda un residuo
    fantasma sin ninguna orden a la que aplicarlo.
    """
    fake = FakeExecute(
        {
            "pagos": [
                {
                    "id": 1084,
                    "partner_id": [1482, "Inversiones Sai 2006, C.A"],
                    "amount": 4784.56,
                    "currency_id": [2, "VES"],
                    "journal_id": [30, "Banco Bancamiga 7806"],
                    "date": "2026-07-21",
                    "move_id": [5940, "PBAMI/2026/00294"],
                }
            ],
            "partners_read": [{"id": 1482, "user_id": False}],
            "users": [],
            "move_lines": [
                # Débito -- recibos pendientes (asset_current, nunca reembolso).
                {
                    "move_id": [5940, "x"],
                    "account_id": [409, "Recibos pendientes"],
                    "amount_currency": 4784.56,
                },
                # Crédito -- AR, la porción que sí se aplicó (asset_receivable, excluida).
                {
                    "move_id": [5940, "x"],
                    "account_id": [52, "AR"],
                    "amount_currency": -1566.02,
                },
                # Crédito -- banco real (asset_cash) = la devolución.
                {
                    "move_id": [5940, "x"],
                    "account_id": [391, "Banco Bancamiga"],
                    "amount_currency": -3218.54,
                },
            ],
            "accounts": [
                {"id": 409, "account_type": "asset_current"},
                {"id": 52, "account_type": "asset_receivable"},
                {"id": 391, "account_type": "asset_cash"},
            ],
        }
    )
    reader = OdooXmlRpcReader(_config(), execute=fake)
    p = reader.changed_pagos(None)[0]
    # 4784.56 - 3218.54 (reembolso real) = 1566.02 -- NUNCA el bruto.
    assert p.monto == Decimal("1566.02")


def test_changed_pagos_pago_normal_sin_reembolso_no_se_toca() -> None:
    """Un pago normal (2 líneas: débito banco, crédito AR -- sin ninguna

    línea extra de crédito a un banco) no debe verse afectado -- el monto
    sigue siendo el bruto original."""
    fake = FakeExecute(
        {
            "pagos": [
                {
                    "id": 900,
                    "partner_id": [10, "Cliente Normal"],
                    "amount": 500.0,
                    "currency_id": [1, "USD"],
                    "journal_id": [29, "Zelle"],
                    "date": "2026-07-01",
                    "move_id": [800, "PAY/900"],
                }
            ],
            "partners_read": [{"id": 10, "user_id": False}],
            "users": [],
            "move_lines": [
                {"move_id": [800, "x"], "account_id": [52, "AR"], "amount_currency": -500.0},
            ],
            "accounts": [
                {"id": 52, "account_type": "asset_receivable"},
            ],
        }
    )
    reader = OdooXmlRpcReader(_config(), execute=fake)
    p = reader.changed_pagos(None)[0]
    assert p.monto == Decimal("500.0")


def test_changed_pagos_reembolso_total_no_baja_de_cero() -> None:
    """Devolución del 100% (nada se aplicó a ninguna orden) -- el monto

    neto debe quedar en $0, nunca negativo, aunque por algún redondeo el
    reembolso registrado superara ligeramente el monto bruto."""
    fake = FakeExecute(
        {
            "pagos": [
                {
                    "id": 1200,
                    "partner_id": [20, "Cliente Reembolsado"],
                    "amount": 200.0,
                    "currency_id": [1, "USD"],
                    "journal_id": [29, "Zelle"],
                    "date": "2026-07-05",
                    "move_id": [900, "PAY/1200"],
                }
            ],
            "partners_read": [{"id": 20, "user_id": False}],
            "users": [],
            "move_lines": [
                {
                    "move_id": [900, "x"],
                    "account_id": [409, "Recibos pendientes"],
                    "amount_currency": 200.0,
                },
                {
                    "move_id": [900, "x"],
                    "account_id": [391, "Banco"],
                    "amount_currency": -200.5,
                },
            ],
            "accounts": [
                {"id": 409, "account_type": "asset_current"},
                {"id": 391, "account_type": "asset_cash"},
            ],
        }
    )
    reader = OdooXmlRpcReader(_config(), execute=fake)
    p = reader.changed_pagos(None)[0]
    assert p.monto == Decimal("0")


def test_changed_ordenes_vacio_no_falla() -> None:
    reader = OdooXmlRpcReader(_config(), execute=FakeExecute({}))
    assert reader.changed_ordenes(None) == []


def test_ordenes_con_devolucion_resuelve_parent_picking() -> None:
    fake = FakeExecute(
        {
            "ordenes": [
                {
                    "id": 6,
                    "name": "S00006",
                    "partner_id": [181, "UNIFRENOS"],
                    "date_order": "2026-02-26",
                    "amount_total": 83.42,
                    "pricelist_id": [1, "VES"],
                    "user_id": [13, "TORO"],
                    "invoice_status": "no",
                    "delivery_status": "full",
                    "state": "cancel",
                }
            ],
            "partners_read": [{"id": 181, "user_id": [13, "TORO"]}],
            "users": [{"id": 13, "login": "rep@x.com"}],
            "pickings": [
                {
                    "id": 95,
                    "sale_id": [6, "S00006"],
                    "date_done": "2026-03-16",
                    "scheduled_date": "2026-03-16",
                    "state": "done",
                    "picking_type_code": "outgoing",
                },
            ],
            "devoluciones": [
                {
                    "id": 95,
                    "sale_id": [6, "S00006"],
                    "date_done": "2026-03-16",
                    "scheduled_date": "2026-03-16",
                    "state": "done",
                    "picking_type_code": "outgoing",
                },
                {
                    "id": 311,
                    "sale_id": False,
                    "return_id": [95, "ALM/OUT/00004"],
                    "state": "done",
                    "picking_type_code": "incoming",
                    "origin": "Devolución de ALM/OUT/00004",
                },
            ],
        }
    )
    reader = OdooXmlRpcReader(_config(), execute=fake)
    res = reader.changed_ordenes(None)
    assert len(res) == 1
    assert res[0].so_id == "S00006"
    assert res[0].estado_orden == "cancel"
    assert res[0].tiene_devolucion is True
