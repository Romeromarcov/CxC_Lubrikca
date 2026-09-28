"""``aplicaciones_conciliadas`` recorre la cadena de reconciliación de Odoo.

    account.payment → su asiento → su línea por cobrar
      → account.partial.reconcile (lado crédito)
      → línea por cobrar de la factura (lado débito)
      → account.move → invoice_origin → sale.order

Se prueba con un ``execute`` falso porque construir el lector real abre una
conexión XML-RPC.

Lo que fija este archivo, más allá del recorrido:

  · **El filtro que causó todo.** ``changed_pagos`` traía solo
    ``is_reconciled = False``, y por eso 886 pagos conciliados quedaban
    invisibles. Que no vuelva.
  · **El monto es el parcial en la moneda del PAGO**
    (``credit_amount_currency``), no el total del pago ni el de la
    factura. ``reconciled_invoice_ids`` -- lo que se usaba antes -- dice
    QUÉ facturas tocó un pago pero no CUÁNTO fue a cada una, así que un
    pago repartido entre dos órdenes se contaba completo en las dos.
  · **Los ajustes cambiarios quedan fuera.** También generan partials,
    pero no tienen ``account.payment`` detrás. Se filtran por
    construcción: la consulta arranca de líneas de pagos reales.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cxc.config import OdooConfig
from cxc.models import Moneda
from cxc.odoo.client import OdooXmlRpcReader

_CFG = OdooConfig(url="http://x", db="d", username="u", password="p")

# Pago 1304 (USD) y 890 (VES); el 1304 se reparte entre dos órdenes.
_PAGOS = [
    {"id": 1304, "move_id": [500, "P/1"], "currency_id": [2, "USD"], "date": "2026-08-06"},
    {"id": 890, "move_id": [501, "P/2"], "currency_id": [166, "VES"], "date": "2026-07-03"},
]
_LINEAS_PAGO = [
    {"id": 900, "move_id": [500, "P/1"]},
    {"id": 901, "move_id": [501, "P/2"]},
]
_PARTIALS = [
    {"credit_move_id": [900, "l"], "debit_move_id": [800, "f"], "credit_amount_currency": 10985.0},
    {"credit_move_id": [900, "l"], "debit_move_id": [801, "f"], "credit_amount_currency": 500.0},
    {"credit_move_id": [901, "l"], "debit_move_id": [800, "f"], "credit_amount_currency": 30000.0},
]
_LINEAS_FACT = [
    {"id": 800, "move_id": [10119, "F/1"]},
    {"id": 801, "move_id": [10120, "F/2"]},
]
_FACTURAS = [
    {
        "id": 10119,
        "invoice_origin": "S00584",
        "move_type": "out_invoice",
        "state": "posted",
    },
    {
        "id": 10120,
        "invoice_origin": "S00214",
        "move_type": "out_invoice",
        "state": "posted",
    },
]


def _execute_falso(facturas=None, partials=None, lineas_producto=None, lineas_venta=None):
    facturas = _FACTURAS if facturas is None else facturas
    partials = _PARTIALS if partials is None else partials
    lineas_producto = [] if lineas_producto is None else lineas_producto
    lineas_venta = [] if lineas_venta is None else lineas_venta

    def execute(model, method, args, kwargs=None):
        if model == "account.payment":
            return _PAGOS
        if model == "account.move.line":
            dominio = args[0] if args else []
            if method == "search_read":
                # Las líneas "por cobrar" del pago (account_type) vs. las
                # líneas de PRODUCTO de una factura (display_type) -- ver
                # _pesos_por_so_de_facturas, que busca estas últimas.
                if any(c[0] == "display_type" for c in dominio if isinstance(c, list)):
                    return lineas_producto
                return _LINEAS_PAGO
            return [f for f in _LINEAS_FACT if f["id"] in args[0]]
        if model == "sale.order.line":
            return [lv for lv in lineas_venta if lv["id"] in args[0]]
        if model == "account.partial.reconcile":
            return partials
        if model == "account.move":
            return [f for f in facturas if f["id"] in args[0]]
        return []

    return execute


def _leer(**kw):
    return OdooXmlRpcReader(_CFG, _execute_falso(**kw)).aplicaciones_conciliadas()


def test_resuelve_la_cadena_hasta_la_orden() -> None:
    apps = _leer()
    assert {(a.pago_id, a.so_id, a.monto) for a in apps} == {
        ("1304", "S00584", Decimal("10985.0")),
        ("1304", "S00214", Decimal("500.0")),
        ("890", "S00584", Decimal("30000.0")),
    }


def test_un_pago_repartido_conserva_cada_parcial_por_separado() -> None:
    """El pago 1304 va a dos órdenes: 10.985 a una y 500 a la otra --
    nunca 11.485 a cada una, que es lo que daba ``reconciled_invoice_ids``."""
    del_1304 = {a.so_id: a.monto for a in _leer() if a.pago_id == "1304"}
    assert del_1304 == {"S00584": Decimal("10985.0"), "S00214": Decimal("500.0")}


def test_la_moneda_y_la_fecha_salen_del_pago() -> None:
    por_pago = {a.pago_id: a for a in _leer()}
    assert por_pago["1304"].moneda == Moneda.USD
    assert por_pago["1304"].fecha_pago == date(2026, 8, 6)
    assert por_pago["890"].moneda == Moneda.VES
    assert por_pago["890"].fecha_pago == date(2026, 7, 3)


def test_una_nota_de_credito_no_cuenta_como_factura_de_la_orden() -> None:
    ajenas = [dict(f, move_type="out_refund") for f in _FACTURAS]
    assert _leer(facturas=ajenas) == []


def test_una_factura_en_borrador_se_ignora() -> None:
    borrador = [dict(f, state="draft") for f in _FACTURAS]
    assert _leer(facturas=borrador) == []


def test_un_parcial_en_cero_no_genera_aplicacion() -> None:
    """Odoo deja filas en 0,00 (se vio en la factura de S00105)."""
    ceros = [dict(p, credit_amount_currency=0.0) for p in _PARTIALS]
    assert _leer(partials=ceros) == []


def test_se_puede_filtrar_por_orden() -> None:
    reader = OdooXmlRpcReader(_CFG, _execute_falso())
    apps = reader.aplicaciones_conciliadas(so_names=["S00214"])
    assert [(a.pago_id, a.so_id) for a in apps] == [("1304", "S00214")]


def test_sin_pagos_ni_partials_devuelve_vacio() -> None:
    def vacio(model, method, args, kwargs=None):
        return []

    assert OdooXmlRpcReader(_CFG, vacio).aplicaciones_conciliadas() == []
    assert _leer(partials=[]) == []


def test_el_sync_de_pagos_ya_no_esconde_los_conciliados() -> None:
    """Guardián de la causa raíz: mientras el dominio llevó
    ``is_reconciled = False``, un pago desaparecía del espejo justo al
    terminar Odoo de reconciliarlo -- 886 pagos y 461 órdenes afectadas."""
    dominios: list[list] = []

    def execute(model, method, args, kwargs=None):
        if model == "account.payment" and method == "search_read":
            dominios.append(args[0])
        return []

    OdooXmlRpcReader(_CFG, execute).changed_pagos(None)
    assert dominios, "changed_pagos no consultó account.payment"
    for d in dominios:
        assert ["is_reconciled", "=", False] not in d


# --- la factura que consolida varias órdenes (17-sep-2026, resuelto con
# reparto por línea el 27-sep-2026) --------------------------------------------
#
# Bug real de producción: una factura con invoice_origin "S00718, S00700" (Odoo
# la arma consolidando dos SO) se leía como si "S00718, S00700" fuera el nombre
# de UNA orden. Ese string se escribía en Vinculacion.so_id, que tiene clave
# foránea contra ordenes_venta -- ninguna orden se llama así, y el INSERT
# violaba la restricción en cada ciclo del demonio. Se resolvió primero
# excluyendo estas facturas (no había forma de repartir el pago); ahora, con
# ``sale_line_ids`` por línea de factura, sí se puede: caso real reportado por
# el usuario (Corporacion JJP 2023, S00718 + S00700 en una sola factura).
_LINEAS_VENTA_MULTI = [
    {"id": 9001, "order_id": [718, "S00718"]},
    {"id": 9002, "order_id": [700, "S00700"]},
]
# 60% del subtotal es de S00718, 40% de S00700.
_LINEAS_PRODUCTO_MULTI = [
    {"move_id": [10119, "F/1"], "price_subtotal": 600.0, "sale_line_ids": [9001]},
    {"move_id": [10119, "F/1"], "price_subtotal": 400.0, "sale_line_ids": [9002]},
]


def test_una_factura_que_consolida_varias_ordenes_se_reparte_por_peso_de_linea() -> None:
    """La factura 10119 (originalmente S00584) pasa a consolidar S00718 (60%

    del subtotal) y S00700 (40%). Las dos aplicaciones que la tocan -- 1304
    (10.985,0) y 890 (30.000,0) -- se reparten en esa misma proporción, en vez
    de desaparecer. La otra factura del mismo pago 1304 (S00214, sin tocar)
    sigue entrando normal.
    """
    multi = [dict(f) for f in _FACTURAS]
    multi[0]["invoice_origin"] = "S00718, S00700"
    apps = _leer(
        facturas=multi,
        lineas_producto=_LINEAS_PRODUCTO_MULTI,
        lineas_venta=_LINEAS_VENTA_MULTI,
    )
    por_clave = {(a.pago_id, a.so_id): a.monto for a in apps}
    assert por_clave[("1304", "S00214")] == Decimal("500.0")
    assert por_clave[("1304", "S00718")] == Decimal("6591.000000")  # 10985 * 0.6
    assert por_clave[("1304", "S00700")] == Decimal("4394.000000")  # 10985 * 0.4
    assert por_clave[("890", "S00718")] == Decimal("18000.000000")  # 30000 * 0.6
    assert por_clave[("890", "S00700")] == Decimal("12000.000000")  # 30000 * 0.4
    assert len(apps) == 5


def test_factura_multi_orden_sin_lineas_resolubles_reparte_igualitario() -> None:
    """Si ninguna línea de la factura consolidada resuelve a una de sus

    órdenes (dato faltante en Odoo), se reparte en partes iguales entre las
    nombradas en invoice_origin -- mejor una aproximación visible que dejar
    la factura entera sin dueño para ninguna orden.
    """
    multi = [dict(f) for f in _FACTURAS]
    multi[0]["invoice_origin"] = "S00718, S00700"
    apps = _leer(facturas=multi)  # sin lineas_producto/lineas_venta -- vacías
    por_clave = {(a.pago_id, a.so_id): a.monto for a in apps}
    assert por_clave[("1304", "S00718")] == Decimal("5492.500000")
    assert por_clave[("1304", "S00700")] == Decimal("5492.500000")
    assert por_clave[("890", "S00718")] == Decimal("15000.000000")
    assert por_clave[("890", "S00700")] == Decimal("15000.000000")


def test_una_factura_de_una_sola_orden_no_se_toca() -> None:
    """Con una sola orden, invoice_origin no lleva coma y nada cambia."""
    apps = _leer()
    assert all("," not in a.so_id for a in apps)


def test_so_ids_de_invoice_origin() -> None:
    from cxc.odoo.client import so_ids_de_invoice_origin

    assert so_ids_de_invoice_origin("S00700") == ["S00700"]
    assert so_ids_de_invoice_origin("S00718, S00700") == ["S00718", "S00700"]
    assert so_ids_de_invoice_origin("S00718,S00700") == ["S00718", "S00700"], "sin espacio tambien"
    assert so_ids_de_invoice_origin("") == []
    assert so_ids_de_invoice_origin(None) == []  # type: ignore[arg-type]
    assert so_ids_de_invoice_origin("  S00700  ") == ["S00700"]
