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


def _execute_falso(facturas=None, partials=None):
    facturas = _FACTURAS if facturas is None else facturas
    partials = _PARTIALS if partials is None else partials

    def execute(model, method, args, kwargs=None):
        if model == "account.payment":
            return _PAGOS
        if model == "account.move.line":
            if method == "search_read":
                return _LINEAS_PAGO
            return [f for f in _LINEAS_FACT if f["id"] in args[0]]
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
