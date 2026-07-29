from decimal import Decimal

from cxc.odoo.price import OdooPriceResolver


def test_odoo_price_resolver_usa_regla_fija_cuando_existe():
    def fake_execute(model, method, args, kwargs=None):
        if model == "product.pricelist.item":
            return [{"fixed_price": "1271.88", "date_start": False, "date_end": False}]
        return []

    resolver = OdooPriceResolver(fake_execute, {"USD": 4})
    assert resolver.precio("923", "4") == Decimal("1271.88")


def test_odoo_price_resolver_fallback_usa_list_price_usd_no_list_price():
    """Si el producto no tiene regla de precio fijo en la pricelist de la

    orden, el fallback debe usar "Precio de venta $" (list_price_usd), NUNCA
    list_price -- ese campo está en VES (la moneda de la compañía en Odoo) y
    tratarlo como USD infla el precio ~800x. Verificado en vivo con las
    órdenes S00700/S00718: producto "GLOBAL MOTORGAS W SAE 40 (Tambor)" sin
    regla en la lista de la orden, list_price=1,457,052.51 (VES) vs
    list_price_usd=1,961.54 (USD, el valor correcto).
    """

    def fake_execute(model, method, args, kwargs=None):
        if model == "product.pricelist.item":
            return []
        if model == "product.product":
            return [{"list_price_usd": 1961.54, "list_price": 1457052.50817}]
        return []

    resolver = OdooPriceResolver(fake_execute, {"USD": 4})
    assert resolver.precio("1012", "4") == Decimal("1961.54")
