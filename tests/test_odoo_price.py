from decimal import Decimal

import pytest

import cxc.odoo.price as price_module
from cxc.odoo.price import OdooPriceResolver


@pytest.fixture(autouse=True)
def _limpiar_cache_compartido():
    # El caché de precios ahora es COMPARTIDO a nivel de módulo (Fase 2,
    # agosto 2026 -- para que sobreviva entre instancias reales de
    # OdooPriceResolver en producción). Varios tests de este archivo
    # reusan el mismo (producto, lista) con precios distintos esperados
    # -- sin limpiar el caché entre tests, el primero que corra
    # contaminaría a los demás.
    price_module._SHARED_PRICE_CACHE.clear()
    price_module._SHARED_VOLUMEN_CACHE.clear()
    yield
    price_module._SHARED_PRICE_CACHE.clear()
    price_module._SHARED_VOLUMEN_CACHE.clear()


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


def test_odoo_price_resolver_fue_fallback_false_si_regla_fija_en_la_lista_pedida():
    def fake_execute(model, method, args, kwargs=None):
        if model == "product.pricelist.item":
            return [{"fixed_price": "100.0", "date_start": False, "date_end": False}]
        return []

    resolver = OdooPriceResolver(fake_execute, {"USD": 4})
    resolver.precio("923", "4")
    assert resolver.fue_fallback("923", "4") is False


def test_odoo_price_resolver_fue_fallback_true_si_uso_otra_pricelist_de_respaldo():
    def fake_execute(model, method, args, kwargs=None):
        if model == "product.pricelist.item":
            pl_id = next((c[2] for c in (args[0] if args else []) if c[0] == "pricelist_id"), None)
            if pl_id == 5:  # solo la pricelist de respaldo (5) tiene regla
                return [{"fixed_price": "90.0", "date_start": False, "date_end": False}]
            return []
        return []

    resolver = OdooPriceResolver(fake_execute, {"USD": 4}, fallback_pricelist_ids=[5])
    precio = resolver.precio("923", "4")
    assert precio == Decimal("90.0")
    assert resolver.fue_fallback("923", "4") is True


def test_odoo_price_resolver_fue_fallback_true_si_uso_precio_de_ficha():
    def fake_execute(model, method, args, kwargs=None):
        if model == "product.pricelist.item":
            return []
        if model == "product.product":
            return [{"list_price_usd": 50.0}]
        return []

    resolver = OdooPriceResolver(fake_execute, {"USD": 4})
    resolver.precio("1012", "4")
    assert resolver.fue_fallback("1012", "4") is True


def test_odoo_price_resolver_fue_fallback_default_false_sin_consultar_antes():
    resolver = OdooPriceResolver(lambda *a, **k: [], {"USD": 4})
    assert resolver.fue_fallback("999", "4") is False


def test_odoo_price_resolver_cache_compartido_sobrevive_entre_instancias():
    """Fase 2 (agosto 2026): el caché de precios es compartido a nivel de

    módulo -- una instancia NUEVA de OdooPriceResolver (una por ciclo de
    recálculo en producción) no debe volver a consultar Odoo para un
    producto/lista ya resuelto por una instancia anterior."""
    calls = []

    def fake_execute(model, method, args, kwargs=None):
        calls.append(model)
        if model == "product.pricelist.item":
            return [{"fixed_price": "500.00", "date_start": False, "date_end": False}]
        return []

    resolver1 = OdooPriceResolver(fake_execute, {"USD": 4})
    assert resolver1.precio("777", "4") == Decimal("500.00")
    assert len(calls) == 1

    # Instancia nueva (simula el siguiente ciclo de recálculo) -- NO debe
    # volver a consultar Odoo, debe leer del caché compartido.
    resolver2 = OdooPriceResolver(fake_execute, {"USD": 4})
    assert resolver2.precio("777", "4") == Decimal("500.00")
    assert len(calls) == 1  # sigue en 1 -- no hubo una segunda consulta


def test_odoo_price_resolver_fallback_no_se_cachea_compartido():
    """Un precio resuelto por fallback (sin regla fija en ninguna lista) NO

    se cachea compartido -- si alguien completa la pricelist en Odoo
    después, el próximo ciclo debe re-consultar y detectarlo, no seguir
    devolviendo el $0/fallback viejo hasta que expire el TTL de horas."""
    calls = []

    def fake_execute_sin_regla(model, method, args, kwargs=None):
        calls.append(model)
        if model == "product.product":
            return [{"list_price_usd": 10.0}]
        return []

    resolver1 = OdooPriceResolver(fake_execute_sin_regla, {"USD": 4})
    assert resolver1.precio("888", "4") == Decimal("10.0")
    assert resolver1.fue_fallback("888", "4") is True

    # Ahora Odoo SÍ tiene una regla fija (alguien la completó) -- una
    # instancia nueva debe verla, no quedar pegada al fallback viejo.
    def fake_execute_con_regla(model, method, args, kwargs=None):
        if model == "product.pricelist.item":
            return [{"fixed_price": "999.00", "date_start": False, "date_end": False}]
        return []

    resolver2 = OdooPriceResolver(fake_execute_con_regla, {"USD": 4})
    assert resolver2.precio("888", "4") == Decimal("999.00")
    assert resolver2.fue_fallback("888", "4") is False


def test_odoo_price_resolver_volumen_cache_compartido_sobrevive_entre_instancias():
    calls = []

    def fake_execute(model, method, args, kwargs=None):
        calls.append(model)
        if model == "product.template":
            return [{"product_volume": "20.5"}]
        return []

    resolver1 = OdooPriceResolver(fake_execute, {"USD": 4})
    assert resolver1.volumen("555") == Decimal("20.5")
    assert len(calls) == 1

    resolver2 = OdooPriceResolver(fake_execute, {"USD": 4})
    assert resolver2.volumen("555") == Decimal("20.5")
    assert len(calls) == 1


def test_fallback_ficha_config_ves_comercial_usa_ficha_tal_cual():
    from cxc.odoo.price import FallbackFichaConfig

    cfg = FallbackFichaConfig(
        moneda_por_lista={5: "ves"},
        categoria_por_lista={5: "comercial"},
        diferencial_fijo_pct=Decimal("0.35"),
        ajuste_industrial_pct=Decimal("0.04"),
    )
    assert cfg.precio_fallback(5, Decimal("100.00")) == Decimal("100.00")


def test_fallback_ficha_config_usd_comercial_resta_el_diferencial_vigente():
    from cxc.odoo.price import FallbackFichaConfig

    cfg = FallbackFichaConfig(
        moneda_por_lista={8: "usd"},
        categoria_por_lista={8: "comercial"},
        diferencial_fijo_pct=Decimal("0.35"),
        ajuste_industrial_pct=Decimal("0.04"),
    )
    # ficha $100 - 35% = $65.
    assert cfg.precio_fallback(8, Decimal("100.00")) == Decimal("65.00")


def test_fallback_ficha_config_ves_industrial_divide_por_el_ajuste():
    from cxc.odoo.price import FallbackFichaConfig

    cfg = FallbackFichaConfig(
        moneda_por_lista={9: "ves"},
        categoria_por_lista={9: "industrial"},
        diferencial_fijo_pct=Decimal("0.35"),
        ajuste_industrial_pct=Decimal("0.04"),
    )
    # ficha $100 / 0.96 = $104.1666...
    resultado = cfg.precio_fallback(9, Decimal("100.00"))
    assert resultado == Decimal("100.00") / Decimal("0.96")


def test_fallback_ficha_config_usd_industrial_aplica_ambos_ajustes():
    from cxc.odoo.price import FallbackFichaConfig

    cfg = FallbackFichaConfig(
        moneda_por_lista={10: "usd"},
        categoria_por_lista={10: "industrial"},
        diferencial_fijo_pct=Decimal("0.35"),
        ajuste_industrial_pct=Decimal("0.04"),
    )
    # (ficha $100 - 35%) / 0.96 = $65 / 0.96.
    resultado = cfg.precio_fallback(10, Decimal("100.00"))
    assert resultado == Decimal("65.00") / Decimal("0.96")


def test_odoo_price_resolver_con_fallback_ficha_no_cae_a_otra_pricelist():
    """Caso real S00868/producto 1655 (Sinoco Hidráulico 68 Paila): la

    Lista #5 VES no tenía precio propio, y el resolver caía silenciosamente
    al precio de la Lista #8 USD ($56.03), dando Teórico VES == Teórico USD
    por coincidencia. Con ``FallbackFichaConfig``, el fallback debe ir
    DIRECTO a la ficha (nunca a la Lista #8), aunque esa otra lista sí
    tenga regla propia para el producto.
    """
    from cxc.odoo.price import FallbackFichaConfig

    def fake_execute(model, method, args, kwargs=None):
        if model == "product.pricelist.item":
            pricelist_id = args[0][0][1]
            if pricelist_id == 8:
                # La Lista #8 SÍ tiene precio -- no debe usarse para la
                # Lista #5, que es la que se está pidiendo.
                return [{"fixed_price": "56.03", "date_start": False, "date_end": False}]
            return []
        if model == "product.product":
            return [{"list_price_usd": 120.0}]
        return []

    cfg = FallbackFichaConfig(
        moneda_por_lista={5: "ves", 8: "usd"},
        categoria_por_lista={5: "comercial", 8: "comercial"},
        diferencial_fijo_pct=Decimal("0.35"),
        ajuste_industrial_pct=Decimal("0.04"),
    )
    resolver = OdooPriceResolver(fake_execute, {"USD": 8, "BCV": 5}, [8, 5], cfg)
    precio_ves = resolver.precio("1655", "5")
    # Ficha $120, lista VES comercial -> tal cual, NUNCA $56.03 de la Lista 8.
    assert precio_ves == Decimal("120.0")
    assert resolver.fue_fallback("1655", "5") is True
