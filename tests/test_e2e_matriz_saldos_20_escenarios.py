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
    EstadoVinculacion,
    Factura,
    OrdenVenta,
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


def test_pago_pendiente_no_conciliado_no_borra_descuento_pendiente_ni_saldo():
    """Combinación del gate CONCILIADO (Fase 0) con el patrón D: una

    Vinculación PENDIENTE (sugerencia FIFO sin confirmar) que cubriría
    exactamente el neto con descuento NO debe hacer que la orden se vea
    pagada -- ni en el saldo real ni en el estatus contra el teórico.
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
        assert item["estatus_pago_teorico_ves"] != "pagada"
        assert item["monto_pagado_factura_odoo"] == 0.0


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


def test_pendiente_facturada_muestra_pagada_pendiente_confirmar_odoo():
    """Nuevo estado intermedio (pedido del usuario en el artefacto de

    verificación): una orden YA FACTURADA con una Vinculación PENDIENTE
    (sin confirmar por Odoo) que cubre el teórico debe verse
    "pagada_pendiente_odoo" -- ni "sin_pago" (el dinero ya está vinculado)
    ni "pagada" (Odoo todavía no lo confirmó). No debe salir de CxC activa
    (eso sigue exigiendo CONCILIADO).
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
        assert item["estatus_pago_teorico_ves"] == "pagada_pendiente_odoo"
        assert item["estatus_pago_real_factura"] == "pagada_pendiente_odoo"


def test_pendiente_no_facturada_muestra_confirmada_temporal_app():
    """Espejo del test anterior para una orden SIN factura todavía: el

    mismo pago PENDIENTE se etiqueta "pagada_temporal_app" -- no hay
    ninguna factura en Odoo esperando reconciliación todavía, así que
    hablar de "pendiente confirmar en Odoo" sería engañoso. La orden
    tampoco sale de CxC activa.
    """
    from cxc.models import VentasTeorico

    mock_repo = MagicMock()
    mock_repo._g.read_rows.return_value = []
    mock_repo.all_ordenes.return_value = [
        OrdenVenta(
            so_id="SO_G1",
            cliente_id="CLI_G",
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
        VentasTeorico(so_id="SO_G1", teorico_ves=Decimal("100.00"), teorico_usd=Decimal("100.00")),
    ]
    mock_repo.all_vinculaciones.return_value = [
        _vinc("SO_G1", "116.00", EstadoVinculacion.PENDIENTE)
    ]

    def fake_execute(model, method, args, kwargs=None):
        if model == "sale.order":
            return [{"name": "SO_G1", "state": "sale", "amount_untaxed": 100.0}]
        return []

    with (
        patch("cxc.web.app.get_repo", return_value=mock_repo),
        patch("cxc.web.app._connect", return_value=fake_execute),
        patch("cxc.web.app.AppConfig.from_env", return_value=_fake_config()),
    ):
        res = client.get("/api/ventas")
        assert res.status_code == 200
        item = {it["so_id"]: it for it in res.json()["items"]}["SO_G1"]
        assert item["estatus_pago_teorico_ves"] == "pagada_temporal_app"
        assert item["estatus_pago_real_factura"] == "sin_factura"
