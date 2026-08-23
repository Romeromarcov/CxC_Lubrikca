"""Matriz de escenarios reales de una orden (Ventas y Cobranza como fuente
única, agosto 2026) -- verifica que los DOS fixes recién desplegados
(gate CONCILIADO en 5 sitios de app.py, commit 53b7531; fallback de precio
a ficha en vez de a otra lista, commit acd55b6) interactúan correctamente
con la matriz de 20 escenarios ya cubierta en fases anteriores (patrones
A-D de pago x VES/USD x facturada/no facturada, documento
"Auditoría del Ciclo CxC") y con las 3 combinaciones reales de estado de
descuento que puede tener una orden en un momento dado:

  1. Ya aplicado en Odoo (línea con ``discount`` o "Descuento" con
     ``price_subtotal`` negativo) -- ``descuento_aplicado_orden``/
     ``descuento_aplicado_factura``.
  2. Calculado por el motor pero AÚN NO aplicado en ningún lado --
     ``descuento_motor_total``/``descuento_pendiente_aplicar``.
  3. Ya aplicado por el sistema (aprobado a mano en Bandeja 1, ajusta solo
     el saldo interno de CxC, nunca viaja a Odoo) --
     ``descuento_aplicado_sistema``.

No repite los 20 escenarios base -- ya verdes en test_e2e_20..51 -- solo
prueba las combinaciones nuevas que solo existen ahora que el gate
CONCILIADO y el fallback a ficha están activos.
"""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from cxc.config import EngineConfig
from cxc.models import (
    BandejaFacturacion,
    Cliente,
    EstadoVinculacion,
    Factura,
    Moneda,
    OrdenVenta,
    Pago,
    Vinculacion,
)

from .test_e2e_production_readiness import client  # noqa: F401 (reutiliza el mismo TestClient)


def _fake_config():
    fake_config = MagicMock()
    fake_config.engine = EngineConfig(cash_window_business_days=3, bcv_complete_formula="full")
    return fake_config


def _vinc(so_id: str, monto: str, estado: EstadoVinculacion) -> Vinculacion:
    return Vinculacion(
        vinc_id=f"V_{so_id}",
        pago_id=f"P_{so_id}",
        so_id=so_id,
        monto_aplicado=Decimal(monto),
        hora_pago_confirmada=datetime(2026, 7, 15, 10, 0),
        tasa_bcv_aplicada=Decimal("60.0"),
        tasa_binance_aplicada=Decimal("63.0"),
        es_tasa_heredada=False,
        estado=estado,
    )


def test_escenario_a_descuento_ya_aplicado_en_odoo_conciliado_saldo_cero():
    """Patrón A/C (VES nativa): el descuento YA está aplicado en la factura

    de Odoo (línea "Descuento" con price_subtotal negativo) y el cliente
    pagó exactamente ese neto, con la Vinculación ya CONCILIADO. El saldo
    de las 4 columnas debe dar 0 -- no debe quedar "descuento pendiente"
    (ya está en Odoo) ni "saldo teórico" (el pago cubrió el neto).
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_A1",
            cliente_id="CLI_A",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 15),
            fecha_entrega=None,
            monto_total=Decimal("92.80"),  # $100 - 20% desc = $80 + 16% IVA
            lista_precios="5",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        ),
    ]
    mock_repo.all_bandeja.return_value = [
        BandejaFacturacion(
            so_id="SO_A1",
            lista_aplicada="5",
            precio_base_calculado=Decimal("100.00"),
            total_motor=Decimal("80.00"),
        ),
    ]
    mock_repo.all_vinculaciones.return_value = [
        _vinc("SO_A1", "92.80", EstadoVinculacion.CONCILIADO)
    ]
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="A1",
            numero="FAC/A1",
            so_id="SO_A1",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 15),
            moneda="USD",
            monto_total=Decimal("92.80"),
            monto_sin_impuestos=Decimal("80.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("92.80"),
            monto_sin_impuestos_signed_usd=Decimal("80.00"),
        ),
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_A1", "state": "sale", "amount_untaxed": 100.0}]
        if model == "account.move.line":
            # El 20% de descuento ya está aplicado en la factura real.
            return [
                {
                    "move_id": [900, "FAC/A1"],
                    "discount": 20.0,
                    "quantity": 1,
                    "price_unit": 100.0,
                    "price_subtotal": 80.0,
                }
            ]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = {it["so_id"]: it for it in res.json()["items"]}["SO_A1"]
        assert item["estatus_pago_real_factura"] == "pagada"
        assert item["total_facturado_neto"] == 92.80
        # Rediseño de Ventas (agosto 2026): campo consolidado "pagada" y
        # "saldo_cxc" -- CONCILIADO cubre el neto exacto, así que sale de
        # CxC confirmada y el saldo consolidado es 0.
        assert item["pagada"] is True
        assert item["saldo_cxc"] == 0.0


def test_escenario_c_descuento_motor_pendiente_no_se_confunde_con_pagado():
    """Patrón C: el motor calculó un descuento teórico (ves_desc_teorico)

    que TODAVÍA no está en Odoo (factura salió al monto lleno, sin
    descuento). El cliente ya pagó el neto CON ese descuento
    (Vinculación CONCILIADO). ``descuento_pendiente_aplicar`` debe ser >0
    (alimenta la bandeja "Pendiente por aprobar descuento/NC"), pero eso
    NO debe hacer que la orden se vea impaga -- el pago real ya cubrió lo
    que el cliente efectivamente debía.
    """
    from cxc.models import VentasTeorico

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_C1",
            cliente_id="CLI_C",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 15),
            fecha_entrega=None,
            monto_total=Decimal("116.00"),  # facturado al monto lleno, sin descuento
            lista_precios="5",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        ),
    ]
    mock_repo.all_bandeja.return_value = [
        BandejaFacturacion(
            so_id="SO_C1",
            lista_aplicada="5",
            precio_base_calculado=Decimal("100.00"),
            total_descuentos=Decimal("20.00"),  # motor exige 20% de descuento
            total_motor=Decimal("80.00"),
        ),
    ]
    mock_repo.all_ventas_teoricos.return_value = [
        VentasTeorico(
            so_id="SO_C1",
            teorico_ves=Decimal("100.00"),
            teorico_usd=Decimal("100.00"),
            descuentos_teorico_ves=Decimal("20.00"),
            descuentos_teorico_usd=Decimal("20.00"),
        ),
    ]
    # Pagó el neto con descuento + IVA: (100-20)*1.16 = 92.80, CONCILIADO.
    mock_repo.all_vinculaciones.return_value = [
        _vinc("SO_C1", "92.80", EstadoVinculacion.CONCILIADO)
    ]
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="C1",
            numero="FAC/C1",
            so_id="SO_C1",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 15),
            moneda="USD",
            monto_total=Decimal("116.00"),
            monto_sin_impuestos=Decimal("100.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("116.00"),
            monto_sin_impuestos_signed_usd=Decimal("100.00"),
        ),
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_C1", "state": "sale", "amount_untaxed": 100.0}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = {it["so_id"]: it for it in res.json()["items"]}["SO_C1"]
        # El motor exige $20 de descuento y Odoo no lo tiene -- pendiente.
        assert item["descuento_pendiente_aplicar"] > 0
        # Pero el teórico neto VES (con el descuento del motor) ya está
        # completamente cubierto por el pago CONCILIADO.
        assert item["estatus_pago_teorico_ves"] == "pagada"


def test_escenario_b_fallback_a_ficha_no_contamina_saldo_teorico_ves():
    """Caso real S00868 llevado a saldo: producto sin precio propio en su

    lista nativa USD -- el fallback (commit acd55b6) va a la ficha, NUNCA
    a otra lista configurada. Antes del fix, ``teorico_ves`` y
    ``teorico_usd`` coincidían por el bug -- ahora deben ser
    independientes, y cada columna de saldo debe reaccionar solo al pago
    de SU referencia (Binance para USD, BCV para VES), no a un valor
    prestado de la otra lista.
    """
    from cxc.models import VentasTeorico

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_B1",
            cliente_id="CLI_B",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 15),
            fecha_entrega=None,
            monto_total=Decimal("139.20"),
            lista_precios="8",  # nace en lista USD
            es_primera_compra=False,
            estado_orden="sale",
            facturada=False,
        ),
    ]
    mock_repo.all_bandeja.return_value = []
    # teorico_usd viene del fallback a ficha ($120), teorico_ves de una
    # lista con precio propio distinto ($150) -- deliberadamente NO
    # coinciden, a diferencia del bug pre-fix.
    mock_repo.all_ventas_teoricos.return_value = [
        VentasTeorico(
            so_id="SO_B1",
            teorico_ves=Decimal("150.00"),
            teorico_usd=Decimal("120.00"),
            descuentos_teorico_ves=Decimal("0"),
            descuentos_teorico_usd=Decimal("0"),
            usa_fallback_usd=True,
        ),
    ]
    # Paga por Binance exactamente el neto USD con IVA (120*1.16=139.20),
    # CONCILIADO -- la orden nació en lista USD, así que esta es su
    # referencia real de pago.
    mock_repo.all_vinculaciones.return_value = [
        _vinc("SO_B1", "139.20", EstadoVinculacion.CONCILIADO)
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_B1", "state": "sale", "amount_untaxed": 120.0}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = {it["so_id"]: it for it in res.json()["items"]}["SO_B1"]
        assert item["estatus_pago_real_orden"] == "pagada"
        # Las dos columnas teóricas siguen siendo $150 y $120 -- el
        # fallback a ficha no las hizo coincidir por accidente.
        assert item["ves_neta_teorica"] == 150.0
        assert item["usd_neta_teorica"] == 120.0


def test_pago_pendiente_no_conciliado_no_facturada_sale_a_facturacion_1():
    """Combinación del gate CONCILIADO (Fase 0) con el patrón D: una

    Vinculación PENDIENTE (sugerencia FIFO sin confirmar) que cubriría
    exactamente el neto con descuento se muestra "pagada" en el estatus de
    Ventas (colapso visual). Y desde el precedente de Odoo citado por el
    usuario ("en proceso de pago" ya saca la factura de CxC, distinguible
    de "pagado") también SALE de CxC activa -- con ``cxc_confirmado=False``.
    Corrección del 2026-08-22: como esta orden NO está facturada,
    CONCILIADO nunca podrá alcanzarse (no hay factura que Odoo reconcilie
    todavía) -- el destino debe ser Facturación 1 (acción real), no la
    lista pasiva "en proceso de pago", o esta orden nunca avanzaría. El
    saldo real (``monto_pagado_factura_odoo``, que solo cuenta CONCILIADO)
    sigue en $0 -- eso no cambia.
    """
    from cxc.models import VentasTeorico

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_D1",
            cliente_id="CLI_D",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 15),
            fecha_entrega=None,
            monto_total=Decimal("116.00"),
            lista_precios="5",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=False,
        ),
    ]
    mock_repo.all_bandeja.return_value = []
    mock_repo.all_ventas_teoricos.return_value = [
        VentasTeorico(
            so_id="SO_D1",
            teorico_ves=Decimal("100.00"),
            teorico_usd=Decimal("100.00"),
            descuentos_teorico_ves=Decimal("20.00"),
            descuentos_teorico_usd=Decimal("20.00"),
        ),
    ]
    # Sugerencia FIFO sin confirmar por Odoo -- cubriría el neto con
    # descuento, pero NO debe contar.
    mock_repo.all_vinculaciones.return_value = [
        _vinc("SO_D1", "92.80", EstadoVinculacion.PENDIENTE)
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_D1", "state": "sale", "amount_untaxed": 100.0}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = {it["so_id"]: it for it in res.json()["items"]}["SO_D1"]
        assert item["estatus_pago_teorico_ves"] == "pagada"
        assert item["monto_pagado_factura_odoo"] == 0.0
        assert item["sale_de_cxc"] is True
        assert item["cxc_confirmado"] is False
        assert item["bandeja_destino"] == "facturacion_1"


def test_retencion_iva_confirmada_mas_nc_reduce_saldo_factura_real():
    """Combina retención de IVA confirmada (espejo Factura.wh_iva_aplicado,

    Fase 3) con una nota de crédito posterior -- ambas deben restar del
    saldo real de la factura, encima de lo ya pagado (CONCILIADO).
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_E1",
            cliente_id="CLI_E",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 15),
            fecha_entrega=None,
            monto_total=Decimal("232.00"),
            lista_precios="5",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        ),
    ]
    mock_repo.all_bandeja.return_value = []
    # Factura $232 (con 16% IVA sobre $200), retención de IVA YA
    # confirmada en Odoo (espejo, no consulta en vivo).
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="E1",
            numero="FAC/E1",
            so_id="SO_E1",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 15),
            moneda="USD",
            monto_total=Decimal("232.00"),
            monto_sin_impuestos=Decimal("200.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("232.00"),
            monto_sin_impuestos_signed_usd=Decimal("200.00"),
            wh_iva_aplicado=True,
        ),
    ]
    # Pago parcial CONCILIADO de $100.
    mock_repo.all_vinculaciones.return_value = [
        _vinc("SO_E1", "100.00", EstadoVinculacion.CONCILIADO)
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_E1", "state": "sale", "amount_untaxed": 200.0}]
        if model == "account.move" and method == "search_read":
            # NC de $30.00 (con impuestos) contra la factura original.
            domain = args[0]
            filtros = str(domain)
            if "reversed_entry_id" in filtros:
                return [
                    {
                        "id": 55,
                        "reversed_entry_id": [900, "FAC/E1"],
                        "amount_total_signed_usd": -30.0,
                    }
                ]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = {it["so_id"]: it for it in res.json()["items"]}["SO_E1"]
        # IVA retenido reduce el neto: $232 - $32 (IVA de 232/1.16) = $200
        assert item["wh_iva_aplicado"] is True
        assert item["iva_retenido_confirmado"] > 0
        # El total facturado neto debe reflejar la NC restada también.
        assert item["total_facturado_neto"] < 232.0


def test_pendiente_se_muestra_pagada_en_ventas_y_sale_de_cxc_sin_confirmar():
    """Evolución del mismo hilo del artefacto de verificación (agosto 2026):

    primero un estado intermedio visible ("pagada pendiente de confirmar
    en Odoo"); después, colapsar a "pagada" en Ventas sin sacar la orden
    de CxC; y finalmente (citando el precedente de Odoo -- "en proceso de
    pago" ya saca la factura de CxC aunque falte la conciliación
    bancaria) la orden SÍ sale de CxC activa cuando una Vinculación
    PENDIENTE la cubre, pero con ``cxc_confirmado=False`` y
    ``bandeja_destino="en_proceso_de_pago"`` -- nunca confundida con un
    pago realmente CONCILIADO, y nunca destraba ningún descuento (eso
    sigue siendo exclusivo de CONCILIADO, ver ``_abonos`` en runner.py).
    """
    from cxc.models import VentasTeorico

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_F1",
            cliente_id="CLI_F",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 15),
            fecha_entrega=None,
            monto_total=Decimal("116.00"),
            lista_precios="5",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        ),
    ]
    mock_repo.all_bandeja.return_value = []
    mock_repo.all_ventas_teoricos.return_value = [
        VentasTeorico(so_id="SO_F1", teorico_ves=Decimal("100.00"), teorico_usd=Decimal("100.00")),
    ]
    mock_repo.all_vinculaciones.return_value = [
        _vinc("SO_F1", "116.00", EstadoVinculacion.PENDIENTE)
    ]
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="F1",
            numero="FAC/F1",
            so_id="SO_F1",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 15),
            moneda="USD",
            monto_total=Decimal("116.00"),
            monto_sin_impuestos=Decimal("100.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("116.00"),
            monto_sin_impuestos_signed_usd=Decimal("100.00"),
        ),
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_F1", "state": "sale", "amount_untaxed": 100.0}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = {it["so_id"]: it for it in res.json()["items"]}["SO_F1"]
        # Visual: se ve "pagada", sin texto intermedio.
        assert item["estatus_pago_teorico_ves"] == "pagada"
        assert item["estatus_pago_real_factura"] == "pagada"
        # Real: sale de CxC (precedente Odoo "en proceso de pago"), pero
        # marcada explícitamente como NO confirmada.
        assert item["sale_de_cxc"] is True
        assert item["cxc_confirmado"] is False
        assert item["bandeja_destino"] == "en_proceso_de_pago"
        # Rediseño de Ventas (agosto 2026): "beneficio de la duda" pedido
        # explícitamente por el usuario -- "en proceso de pago" también
        # debe verse como pagada=True y saldo_cxc=0 en el campo
        # consolidado, igual que un CONCILIADO real.
        assert item["pagada"] is True
        assert item["saldo_cxc"] == 0.0


def test_no_pagada_saldo_cxc_usa_teorico_nativo_de_la_orden():
    """Rediseño de Ventas (agosto 2026): orden VES nativa, facturada, SIN

    ningún pago vinculado -- "pagada" debe dar False (nunca "depende") y
    "saldo_cxc" debe mostrar el saldo contra el teórico VES neto de la
    orden (única referencia que, por definición, no se cumplió si la
    orden no salió de CxC por ninguna regla del árbol).
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_N1",
            cliente_id="CLI_N",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 15),
            fecha_entrega=None,
            monto_total=Decimal("92.80"),
            lista_precios="5",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        ),
    ]
    mock_repo.all_bandeja.return_value = [
        BandejaFacturacion(
            so_id="SO_N1",
            lista_aplicada="5",
            precio_base_calculado=Decimal("100.00"),
            total_motor=Decimal("80.00"),
        ),
    ]
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="N1",
            numero="FAC/N1",
            so_id="SO_N1",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 15),
            moneda="USD",
            monto_total=Decimal("92.80"),
            monto_sin_impuestos=Decimal("80.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("92.80"),
            monto_sin_impuestos_signed_usd=Decimal("80.00"),
        ),
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_N1", "state": "sale", "amount_untaxed": 100.0}]
        if model == "account.move.line":
            return [
                {
                    "move_id": [900, "FAC/N1"],
                    "discount": 20.0,
                    "quantity": 1,
                    "price_unit": 100.0,
                    "price_subtotal": 80.0,
                }
            ]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = {it["so_id"]: it for it in res.json()["items"]}["SO_N1"]
        assert item["pagada"] is False
        assert item["saldo_cxc"] == 92.80


def test_bandeja_facturacion_enruta_pago_pendiente_pero_marcado_no_confirmado():
    """Historia completa de este caso, en dos correcciones:

    1) ``get_bandeja_facturacion`` leía ``item["estatus_pago_teorico_ves"]``
       de ``/api/ventas``, que colapsa una Vinculación PENDIENTE a "pagada"
       -- eso habría hecho que Bandeja 1 mostrara "lista para facturar" una
       orden con solo un pago FIFO sin confirmar, el riesgo que la Fase 0
       existe para evitar. Se corrigió leyendo los campos
       ``*_confirmado``/``*_confirmada`` (solo CONCILIADO) en vez del texto.
    2) Corrección posterior (2026-08-22, precedente de Odoo "en proceso de
       pago"): para una orden SIN facturar, CONCILIADO nunca podrá
       alcanzarse (no existe factura que Odoo reconcilie) -- así que "en
       proceso de pago" SÍ debe enrutar a Bandeja 1 (o la orden nunca
       tendría ningún camino a facturarse), pero la entrada se marca
       ``cxc_confirmado=False`` para distinguirla de un pago realmente
       confirmado por Odoo.
    """
    from cxc.models import VentasTeorico

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_H1",
            cliente_id="CLI_H",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 15),
            fecha_entrega=None,
            monto_total=Decimal("116.00"),
            lista_precios="5",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=False,
        ),
    ]
    mock_repo.all_bandeja.return_value = []
    mock_repo.all_ventas_teoricos.return_value = [
        VentasTeorico(so_id="SO_H1", teorico_ves=Decimal("100.00"), teorico_usd=Decimal("100.00")),
    ]
    # Solo un pago PENDIENTE (FIFO sin confirmar) que cubre el objetivo --
    # /api/ventas lo muestra como "pagada" (colapso visual), pero NO debe
    # bastar para que Bandeja 1 lo enrute a facturar.
    mock_repo.all_vinculaciones.return_value = [
        _vinc("SO_H1", "116.00", EstadoVinculacion.PENDIENTE)
    ]
    mock_repo.all_conciliaciones.return_value = []
    mock_repo.all_lineas.return_value = []
    mock_repo.all_reglas_dias_credito_volumen.return_value = []
    mock_repo.all_descuentos_sistema_aprobados.return_value = []
    mock_repo.all_tasas_historicas_auditoria.return_value = []

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_H1", "state": "sale", "amount_untaxed": 100.0}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res_ventas = client.get("/api/ventas")
        assert res_ventas.status_code == 200
        item = {it["so_id"]: it for it in res_ventas.json()["items"]}["SO_H1"]
        # Confirma la premisa del test: el display SÍ dice "pagada".
        assert item["estatus_pago_teorico_ves"] == "pagada"
        # Pero el flag confirmado (solo CONCILIADO) sigue en False.
        assert item["teorico_bs_pagado_confirmado"] is False

        res_bandeja = client.get("/api/bandeja")
        assert res_bandeja.status_code == 200
        data = res_bandeja.json()
        por_facturar = {o["so_id"]: o for o in data["ordenes_por_facturar"]}
        assert "SO_H1" in por_facturar
        assert por_facturar["SO_H1"]["cxc_confirmado"] is False
        # Y nunca en la lista pasiva -- esa es solo para órdenes YA
        # facturadas esperando la conciliación bancaria.
        so_ids_en_proceso = {o["so_id"] for o in data["en_proceso_de_pago"]}
        assert "SO_H1" not in so_ids_en_proceso
        # Bug real (pedido explícito del usuario, agosto 2026, caso
        # "Inversiones La Bendición del Nazareno" SO 00133): antes de este
        # fix, `monto_pagado` solo sumaba Vinculaciones CONCILIADO -- para
        # una orden en Bandeja 1 (sin facturar, CONCILIADO estructuralmente
        # imposible) eso siempre daba $0, aunque la propia razón de estar
        # en esta bandeja es que un pago PENDIENTE ya cubrió el teórico.
        assert por_facturar["SO_H1"]["monto_pagado"] == 116.0
        assert por_facturar["SO_H1"]["saldo_pendiente"] == 0.0


def test_saldo_cxc_orden_sin_facturar_neta_pago_pendiente_sin_alcanzar_el_teorico():
    """Bug real (pedido explícito del usuario, agosto 2026, caso "Inversiones

    La Bendición del Nazareno" SO 00133): "las que aún no han sido
    facturadas deben netear el saldo pendiente" -- una orden SIN facturar
    con un pago PENDIENTE que cubre PARTE del teórico (no todo, así que
    sigue en CxC activa) debe mostrar el saldo YA restando esa parte, no el
    teórico completo congelado. Antes de este fix `saldo_cxc` solo restaba
    Vinculaciones CONCILIADO (estructuralmente imposible sin factura), así
    que se veía atascado en el teórico completo sin importar cuánto se
    hubiera abonado.
    """
    from cxc.models import VentasTeorico

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_H2",
            cliente_id="CLI_H2",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 15),
            fecha_entrega=None,
            monto_total=Decimal("200.00"),
            lista_precios="5",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=False,
        ),
    ]
    mock_repo.all_bandeja.return_value = []
    mock_repo.all_ventas_teoricos.return_value = [
        VentasTeorico(so_id="SO_H2", teorico_ves=Decimal("200.00"), teorico_usd=Decimal("200.00")),
    ]
    # Solo cubre 116 de 200 -- sigue en CxC activa, pero el saldo mostrado
    # debe reflejar los 116 ya abonados (116 pendiente, aún sin confirmar).
    mock_repo.all_vinculaciones.return_value = [
        _vinc("SO_H2", "116.00", EstadoVinculacion.PENDIENTE)
    ]
    mock_repo.all_conciliaciones.return_value = []
    mock_repo.all_lineas.return_value = []
    mock_repo.all_reglas_dias_credito_volumen.return_value = []
    mock_repo.all_descuentos_sistema_aprobados.return_value = []
    mock_repo.all_tasas_historicas_auditoria.return_value = []

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_H2", "state": "sale", "amount_untaxed": 200.0}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res_ventas = client.get("/api/ventas")
        assert res_ventas.status_code == 200
        item = {it["so_id"]: it for it in res_ventas.json()["items"]}["SO_H2"]
        assert item["pagada"] is False
        # Teórico neto CON IVA (200 * 1.16 = 232) menos lo pendiente ya
        # abonado (116) -- nunca los 232 completos sin netear nada.
        assert item["saldo_cxc"] == 116.0


def test_devolucion_facturada_sin_nc_marca_falta_nc_por_devolucion():
    """Pedido del usuario en el artefacto de verificación ("Implementalo"):

    una orden facturada con devolución registrada, pero SIN ninguna Nota
    de Crédito todavía en Odoo, debe decir explícitamente que falta
    crearla -- no solo "hay una devolución, revisar".
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_I1",
            cliente_id="CLI_I",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 15),
            fecha_entrega=None,
            monto_total=Decimal("116.00"),
            lista_precios="5",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
            tiene_devolucion=True,
        ),
    ]
    mock_repo.all_bandeja.return_value = []
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="I1",
            numero="FAC/I1",
            so_id="SO_I1",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 15),
            moneda="USD",
            monto_total=Decimal("116.00"),
            monto_sin_impuestos=Decimal("100.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("116.00"),
            monto_sin_impuestos_signed_usd=Decimal("100.00"),
        ),
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_I1", "state": "sale", "amount_untaxed": 100.0}]
        # Sin NC alguna en account.move -- reversed_entry_id vacío.
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = {it["so_id"]: it for it in res.json()["items"]}["SO_I1"]
        assert item["falta_nc_por_devolucion"] is True
        assert "Falta crear Nota de Crédito" in item["revisar_motivo"]


def test_devolucion_facturada_con_nc_no_marca_falta():
    """Espejo: si ya existe la NC en Odoo (total_nc_aplicada > 0), la

    devolución sigue apareciendo en "revisar" (informativo) pero YA NO se
    marca "falta crear NC" -- el lado financiero ya está corregido.
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_I2",
            cliente_id="CLI_I",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 15),
            fecha_entrega=None,
            monto_total=Decimal("116.00"),
            lista_precios="5",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
            tiene_devolucion=True,
        ),
    ]
    mock_repo.all_bandeja.return_value = []
    # NC/ND se leen del espejo Factura (Fase 2), no de account.move en
    # vivo -- una fila move_type="out_refund" con el mismo so_id basta.
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="I2",
            numero="FAC/I2",
            so_id="SO_I2",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 15),
            moneda="USD",
            monto_total=Decimal("116.00"),
            monto_sin_impuestos=Decimal("100.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("116.00"),
            monto_sin_impuestos_signed_usd=Decimal("100.00"),
        ),
        Factura(
            factura_id="I2NC",
            numero="NC/I2",
            so_id="SO_I2",
            move_type="out_refund",
            es_nota_debito=False,
            fecha=date(2026, 7, 16),
            moneda="USD",
            monto_total=Decimal("20.00"),
            monto_sin_impuestos=Decimal("17.24"),
            estado="posted",
            monto_total_signed_usd=Decimal("20.00"),
            monto_sin_impuestos_signed_usd=Decimal("17.24"),
        ),
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_I2", "state": "sale", "amount_untaxed": 100.0}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = {it["so_id"]: it for it in res.json()["items"]}["SO_I2"]
        assert item["falta_nc_por_devolucion"] is False
        assert "Devolución registrada" in item["revisar_motivo"]


def test_bandeja_lista_en_proceso_de_pago_para_orden_ya_facturada():
    """/api/bandeja expone la nueva lista "en_proceso_de_pago" (precedente

    de Odoo citado por el usuario) -- para una orden YA FACTURADA con
    solo un pago PENDIENTE que cubre el teórico, CONCILIADO sí es
    alcanzable (el resync automático de Odoo la promueve cuando
    reconcilie), así que no hace falta forzar ninguna acción: aparece en
    esta lista pasiva, visible pero SIN mezclarse con las bandejas de
    acción real (ordenes_por_facturar, auditoria_precios). Espejo de
    ``test_bandeja_facturacion_enruta_pago_pendiente_pero_marcado_no_confirmado``,
    que cubre el caso NO facturado (ese sí necesita ir a acción).
    """
    from cxc.models import VentasTeorico

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_J1",
            cliente_id="CLI_J",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 15),
            fecha_entrega=None,
            monto_total=Decimal("116.00"),
            lista_precios="5",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        ),
    ]
    mock_repo.all_bandeja.return_value = []
    mock_repo.all_ventas_teoricos.return_value = [
        VentasTeorico(so_id="SO_J1", teorico_ves=Decimal("100.00"), teorico_usd=Decimal("100.00")),
    ]
    mock_repo.all_vinculaciones.return_value = [
        _vinc("SO_J1", "116.00", EstadoVinculacion.PENDIENTE)
    ]
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="J1",
            numero="FAC/J1",
            so_id="SO_J1",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 15),
            moneda="USD",
            monto_total=Decimal("116.00"),
            monto_sin_impuestos=Decimal("100.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("116.00"),
            monto_sin_impuestos_signed_usd=Decimal("100.00"),
        ),
    ]
    mock_repo.all_conciliaciones.return_value = []
    mock_repo.all_lineas.return_value = []
    mock_repo.all_reglas_dias_credito_volumen.return_value = []
    mock_repo.all_descuentos_sistema_aprobados.return_value = []
    mock_repo.all_tasas_historicas_auditoria.return_value = []

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_J1", "state": "sale", "amount_untaxed": 100.0}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res_bandeja = client.get("/api/bandeja")
        assert res_bandeja.status_code == 200
        data = res_bandeja.json()

        so_ids_en_proceso = {o["so_id"] for o in data["en_proceso_de_pago"]}
        assert "SO_J1" in so_ids_en_proceso

        so_ids_en_facturar = {o["so_id"] for o in data["ordenes_por_facturar"]}
        so_ids_auditoria = {o["so_id"] for o in data["auditoria_precios"]}
        assert "SO_J1" not in so_ids_en_facturar
        assert "SO_J1" not in so_ids_auditoria


def test_diferencia_usa_ventas_teoricos_ya_corregido_no_el_snapshot_congelado():
    """Bug real reportado por el usuario (agosto 2026): después del fix del

    fallback de precios (commit 297bd4c), la columna "Diferencia" seguía
    mostrando el dato viejo para órdenes ya facturadas. Causa:
    BandejaFacturacion es un snapshot que run_all() nunca recalcula una
    vez facturada la orden -- si el precio se calculó mal ANTES del fix,
    quedaba congelado con el valor incorrecto para siempre. VentasTeorico
    sí se re-verifica cada ciclo mientras usa_fallback_ves/_usd siga
    marcado -- "Diferencia" debe usar esa fuente ya corregida, no el
    snapshot viejo de BandejaFacturacion.
    """
    from cxc.models import VentasTeorico

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_K1",
            cliente_id="CLI_K",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 15),
            fecha_entrega=None,
            monto_total=Decimal("116.00"),  # facturado exacto al valor ya corregido
            lista_precios="5",  # nace en lista VES
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        ),
    ]
    # Snapshot VIEJO -- BandejaFacturacion nunca se recalculó tras
    # facturar, quedó con el precio incorrecto de antes del fix ($0, el
    # bug real del fallback).
    mock_repo.all_bandeja.return_value = [
        BandejaFacturacion(
            so_id="SO_K1",
            lista_aplicada="5",
            precio_base_calculado=Decimal("0.00"),
            total_motor=Decimal("0.00"),
        ),
    ]
    # VentasTeorico SÍ se re-verificó (usa_fallback_ves) y ya tiene el
    # valor correcto: $100 -- coincide con lo realmente facturado.
    mock_repo.all_ventas_teoricos.return_value = [
        VentasTeorico(
            so_id="SO_K1",
            teorico_ves=Decimal("100.00"),
            teorico_usd=Decimal("100.00"),
            usa_fallback_ves=True,
        ),
    ]
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="K1",
            numero="FAC/K1",
            so_id="SO_K1",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 15),
            moneda="USD",
            monto_total=Decimal("116.00"),
            monto_sin_impuestos=Decimal("100.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("116.00"),
            monto_sin_impuestos_signed_usd=Decimal("100.00"),
        ),
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_K1", "state": "sale", "amount_untaxed": 100.0}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = {it["so_id"]: it for it in res.json()["items"]}["SO_K1"]
        # Si usara el snapshot viejo ($0), la diferencia sería -$116 (falsa
        # alarma enorme). Con VentasTeorico ya corregido ($100 + 16% IVA =
        # $116, exactamente lo facturado), la diferencia debe ser $0.
        assert item["diferencia"] == 0.0
        assert item["alerta"] is False


def test_reparto_cobranza_no_muestra_dos_saldos_distintos_para_la_misma_orden():
    """Bug real (reportado por el usuario, agosto 2026, pago 139/Devenalsa):

    la tabla "Reparto / Órdenes y Facturas" del modal de Detalle de Pago
    mostraba DOS valores distintos de "Saldo Factura (Odoo)" para la
    MISMA orden -- uno por la fila "pendiente" (sugerencia FIFO, usa el
    saldo neto del motor vía ``_saldos_orden_para_reparto``) y otro por la
    fila "vinculado" (Vinculación local, usaba ``orden.monto_total -
    total_vinculado_local`` -- una resta que SÍ cuenta Vinculaciones
    PENDIENTE, aunque el campo se llama "Saldo Factura (Odoo)" y Odoo no
    ha reconciliado nada todavía). No es un duplicado en la base de datos
    (una sola Vinculación real) -- es una inconsistencia de fórmula entre
    las dos ramas que hace parecer un error. Después del fix, ambas ramas
    deben usar la MISMA fuente -- y esa fuente ahora SÍ neta una
    Vinculación PENDIENTE de la orden (pedido explícito posterior del
    usuario, ver assert final).
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_DEVN",
            cliente_id="CLI_DEVN",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 15),
            fecha_entrega=None,
            monto_total=Decimal("92.80"),
            lista_precios="5",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        ),
    ]
    mock_repo.all_bandeja.return_value = [
        BandejaFacturacion(
            so_id="SO_DEVN",
            lista_aplicada="5",
            precio_base_calculado=Decimal("100.00"),
            total_motor=Decimal("80.00"),
        ),
    ]
    # PENDIENTE -- vinculada localmente, pero Odoo NO la ha conciliado
    # todavía (mismo estado que el pago real de Devenalsa).
    mock_repo.all_vinculaciones.return_value = [
        Vinculacion(
            vinc_id="VINC_P_DEVN_SO_DEVN",
            pago_id="P_DEVN",
            so_id="SO_DEVN",
            monto_aplicado=Decimal("16.07"),
            hora_pago_confirmada=datetime(2026, 7, 15, 10, 0),
            tasa_bcv_aplicada=Decimal("60.0"),
            tasa_binance_aplicada=Decimal("63.0"),
            es_tasa_heredada=False,
            estado=EstadoVinculacion.PENDIENTE,
            moneda_abono=Moneda.USD,
        )
    ]
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="F_DEVN",
            numero="FAC/DEVN",
            so_id="SO_DEVN",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 15),
            moneda="USD",
            monto_total=Decimal("92.80"),
            monto_sin_impuestos=Decimal("80.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("92.80"),
            monto_sin_impuestos_signed_usd=Decimal("80.00"),
        ),
    ]
    mock_repo.all_ventas_teoricos.return_value = []
    mock_repo.all_pagos.return_value = [
        Pago(
            pago_id="P_DEVN",
            cliente_id="CLI_DEVN",
            monto=Decimal("16.07"),
            moneda=Moneda.USD,
            metodo_pago="1",
            fecha_pago=datetime(2026, 7, 15),
            vendedor_email="v@lubrikca.com",
        )
    ]
    mock_repo.all_pagos_full.return_value = [
        {
            "pago_id": "P_DEVN",
            "cliente_id": "CLI_DEVN",
            "monto": "16.07",
            "moneda": "USD",
            "fecha_pago": "2026-07-15",
            "vendedor_email": "v@lubrikca.com",
        }
    ]
    mock_repo.all_serie_tasas.return_value = []
    mock_repo.all_tasas_historicas_auditoria.return_value = []
    mock_repo.all_pagos_huerfanos_cerrados.return_value = []
    mock_repo.all_clientes.return_value = [
        Cliente(cliente_id="CLI_DEVN", nombre="Devenalsa", vendedor_email="v@lubrikca.com"),
    ]
    mock_repo.all_auditoria.return_value = []
    mock_repo.all_anomalias_aceptadas.return_value = []
    mock_repo.all_pagos_tasa_binance_override.return_value = []
    mock_repo.all_lineas.return_value = []

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_DEVN", "state": "sale", "amount_untaxed": 100.0}]
        if model == "account.move.line":
            return [
                {
                    "move_id": [900, "FAC/DEVN"],
                    "discount": 20.0,
                    "quantity": 1,
                    "price_unit": 100.0,
                    "price_subtotal": 80.0,
                }
            ]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res = client.get("/api/cobranza/pagos")
        assert res.status_code == 200
        filas = [it for it in res.json() if it["so_id"] == "SO_DEVN"]
        # La Vinculación PENDIENTE cubre $16.07 de $92.80 -- si sobreviviera
        # cualquier sugerencia FIFO por el residuo, ambas filas deben
        # coincidir en "factura_saldo_odoo" (misma orden, MISMA fuente).
        saldos_odoo = {round(f["factura_saldo_odoo"], 2) for f in filas}
        assert len(saldos_odoo) == 1, f"Saldo Factura Odoo inconsistente entre filas: {filas}"
        # Pedido explícito del usuario (agosto 2026, cliente CONSTRUCTORA
        # GRANO AGREGADO/S00608): una Vinculación PENDIENTE de ESTA orden
        # SÍ debe restarse del saldo mostrado -- "beneficio de la duda",
        # mismo criterio que ya usa sale_de_cxc/saldo_cxc en Ventas. Antes
        # de ese pedido, este mismo test esperaba $92.80 sin tocar (ver
        # commit anterior) -- $92.80-$16.07=$76.73 es ahora el valor
        # correcto.
        assert round(next(iter(saldos_odoo)), 2) == 76.73


def test_reporte_cxc_cliente_neta_vinculacion_pendiente_de_la_orden():
    """Pedido explícito del usuario (agosto 2026, cliente CONSTRUCTORA

    GRANO AGREGADO/orden S00608): "los pagos no vinculados a la orden
    aún deberían... rebajarse teóricamente de la orden... que sería
    esa" -- una Vinculación PENDIENTE (vinculada localmente a ESTA
    orden, aún sin conciliar en Odoo) debe restarse de los 4 saldos del
    Reporte por Cliente, no solo mostrarse como saldo completo sin
    tocar. Mismo "beneficio de la duda" que sale_de_cxc/saldo_cxc en
    Ventas -- nunca gatea nada real (descuentos, salida de CxC
    confirmada), solo lo que se MUESTRA aquí.
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_GRANO",
            cliente_id="CLI_GRANO",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 6, 30),
            fecha_entrega=None,
            monto_total=Decimal("92.80"),
            lista_precios="5",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        ),
    ]
    mock_repo.all_bandeja.return_value = [
        BandejaFacturacion(
            so_id="SO_GRANO",
            lista_aplicada="5",
            precio_base_calculado=Decimal("100.00"),
            total_motor=Decimal("80.00"),
        ),
    ]
    # PENDIENTE -- vinculada localmente a SO_GRANO, Odoo aún no la
    # reconcilió (mismo patrón que S00608 en producción: cubre PARTE
    # del total, no todo).
    mock_repo.all_vinculaciones.return_value = [
        Vinculacion(
            vinc_id="VINC_P_GRANO_SO_GRANO",
            pago_id="P_GRANO",
            so_id="SO_GRANO",
            monto_aplicado=Decimal("16.07"),
            hora_pago_confirmada=datetime(2026, 6, 30, 10, 0),
            tasa_bcv_aplicada=Decimal("60.0"),
            tasa_binance_aplicada=Decimal("63.0"),
            es_tasa_heredada=False,
            estado=EstadoVinculacion.PENDIENTE,
            moneda_abono=Moneda.USD,
        )
    ]
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="F_GRANO",
            numero="FAC/GRANO",
            so_id="SO_GRANO",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 6, 30),
            moneda="USD",
            monto_total=Decimal("92.80"),
            monto_sin_impuestos=Decimal("80.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("92.80"),
            monto_sin_impuestos_signed_usd=Decimal("80.00"),
        ),
    ]
    mock_repo.all_ventas_teoricos.return_value = []
    mock_repo.all_pagos.return_value = []
    mock_repo.all_serie_tasas.return_value = []
    mock_repo.all_tasas_historicas_auditoria.return_value = []
    mock_repo.all_pagos_huerfanos_cerrados.return_value = []
    mock_repo.all_clientes.return_value = [
        Cliente(
            cliente_id="CLI_GRANO",
            nombre="Constructora Grano Agregado",
            vendedor_email="v@lubrikca.com",
        ),
    ]
    mock_repo.all_lineas.return_value = []

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_GRANO", "state": "sale", "amount_untaxed": 100.0}]
        if model == "account.move.line":
            return [
                {
                    "move_id": [900, "FAC/GRANO"],
                    "discount": 20.0,
                    "quantity": 1,
                    "price_unit": 100.0,
                    "price_subtotal": 80.0,
                }
            ]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res = client.get("/api/reporte-cxc-cliente")
        assert res.status_code == 200
        cliente = next(c for c in res.json()["clientes"] if c["cliente_id"] == "CLI_GRANO")
        # $92.80 - $16.07 (pendiente vinculado a ESTA orden) = $76.73 --
        # netea, no muestra el saldo completo sin tocar.
        assert round(cliente["saldos"]["venta_real"], 2) == 76.73
        assert round(cliente["saldos"]["factura_real"], 2) == 76.73


def test_odoo_confirma_pagada_directo_sin_vinculacion_local():
    """Pedido explícito del usuario (agosto 2026, auditoría de saldos de

    CxC -- 107 órdenes reales confirmadas en vivo): una orden facturada
    SIN ninguna Vinculación local (nuestra reconstrucción bottom-up
    nunca "ve" ningún pago) pero cuya factura Odoo YA marca
    payment_state='paid' EN VIVO debe salir de CxC igual -- "pagada"
    debe dar True y "saldo_cxc" debe dar $0, en vez de mostrar el saldo
    completo sin tocar solo porque nuestras Vinculaciones no cuadraron.
    """
    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_ODOO_PAID",
            cliente_id="CLI_OP",
            vendedor_email="v@lubrikca.com",
            fecha=date(2026, 7, 15),
            fecha_entrega=None,
            monto_total=Decimal("92.80"),
            lista_precios="5",
            es_primera_compra=False,
            estado_orden="sale",
            facturada=True,
        ),
    ]
    mock_repo.all_bandeja.return_value = [
        BandejaFacturacion(
            so_id="SO_ODOO_PAID",
            lista_aplicada="5",
            precio_base_calculado=Decimal("100.00"),
            total_motor=Decimal("80.00"),
        ),
    ]
    mock_repo.all_vinculaciones.return_value = []
    mock_repo.all_facturas.return_value = [
        Factura(
            factura_id="9001",
            numero="FAC/9001",
            so_id="SO_ODOO_PAID",
            move_type="out_invoice",
            es_nota_debito=False,
            fecha=date(2026, 7, 15),
            moneda="USD",
            monto_total=Decimal("92.80"),
            monto_sin_impuestos=Decimal("80.00"),
            estado="posted",
            monto_total_signed_usd=Decimal("92.80"),
            monto_sin_impuestos_signed_usd=Decimal("80.00"),
        ),
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_ODOO_PAID", "state": "sale", "amount_untaxed": 100.0}]
        if model == "account.move.line":
            return [
                {
                    "move_id": [900, "FAC/9001"],
                    "discount": 20.0,
                    "quantity": 1,
                    "price_unit": 100.0,
                    "price_subtotal": 80.0,
                }
            ]
        if model == "account.move" and method == "read":
            return [{"id": 9001, "move_type": "out_invoice", "payment_state": "paid"}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = {it["so_id"]: it for it in res.json()["items"]}["SO_ODOO_PAID"]
        assert item["pagada"] is True
        assert item["saldo_cxc"] == 0.0
        assert item["sale_de_cxc"] is True
        assert item["bandeja_destino"] == "facturacion_2"
