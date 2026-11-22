"""Pruebas Fase 0 (Sprint 0B): integridad fiscal Odoo, locks, dual Binance, aging CxP/EUR y sync."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from cxc.bancos.conciliacion import egresos_desde_operativos, ingresos_desde_operativos
from cxc.db.schema import (
    documentos_comerciales,
    documentos_operativos,
    metadata,
    vinculaciones_ventas_op,
)
from cxc.documentos.modelos import ENVIADO_ODOO, DocumentoComercial, LineaDocumento
from cxc.documentos.sync_odoo import sincronizar_documentos_desde_odoo
from cxc.engine import saldos_cxp, saldos_propios
from cxc.models import (
    BandejaFacturacion,
    Cliente,
    EstadoBandeja,
    Factura,
    LineaOrden,
    OrdenVenta,
    Pago,
    Producto,
    SerieTasa,
)
from cxc.odoo.escritura import EscritorOdoo
from cxc.operativos.modelos import CONFIRMADO_CXC
from cxc.operativos.modelos import ENVIADO_ODOO as OP_ENVIADO
from cxc.operativos.repositorio import RepoOperativosSQL
from cxc.operativos.servicio import ServicioOperativos
from cxc.operativos.vinculaciones_docs import (
    VinculacionInvalida,
    _tasa_binance_cercana,
    vincular_pago_venta,
)
from tests.capturas_dobles import OdooFalso, RepoMemoria


def _sqlite_engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    metadata.create_all(eng)
    return eng


@dataclass
class _RepoCxcStub:
    clientes: list[Cliente] = field(default_factory=list)
    ordenes: list[OrdenVenta] = field(default_factory=list)
    facturas: list[Factura] = field(default_factory=list)
    bandejas: list[BandejaFacturacion] = field(default_factory=list)
    pagos: list[Pago] = field(default_factory=list)
    lineas_orden: dict[str, list[LineaOrden]] = field(default_factory=dict)
    series: list[SerieTasa] = field(default_factory=list)
    hist_tasas: list[dict[str, Any]] = field(default_factory=list)
    compras_gastos: list[dict[str, Any]] = field(default_factory=list)
    pagos_prov: list[dict[str, Any]] = field(default_factory=list)
    ajustes: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    vinculaciones_odoo: list[Any] = field(default_factory=list)
    vinculaciones_docs: list[Any] = field(default_factory=list)
    entregas: list[Any] = field(default_factory=list)

    def all_clientes(self) -> list[Cliente]:
        return list(self.clientes)

    def all_ordenes(self) -> list[OrdenVenta]:
        return list(self.ordenes)

    def get_orden(self, so_id: str) -> OrdenVenta | None:
        return next((o for o in self.ordenes if o.so_id == so_id), None)

    def lineas_de_orden(self, so_id: str) -> list[LineaOrden]:
        return list(self.lineas_orden.get(so_id, []))

    def all_facturas(self) -> list[Factura]:
        return list(self.facturas)

    def all_bandeja(self) -> list[BandejaFacturacion]:
        return list(self.bandejas)

    def all_bandejas(self) -> list[BandejaFacturacion]:
        return list(self.bandejas)

    def all_pagos(self) -> list[Pago]:
        return list(self.pagos)

    def all_descuentos_sistema_aprobados(self) -> list[dict[str, Any]]:
        return []

    def all_descuentos_no_otorgados(self) -> dict[str, Any]:
        return {}

    def get_last_sync(self) -> Any:
        return None

    def get_metodo_pago(self, _nombre: str) -> Any:
        return None

    def get_pago(self, pago_id: str) -> Pago | None:
        return next((p for p in self.pagos if p.pago_id == pago_id), None)

    def all_metodos_pago(self) -> list[Any]:
        return []

    def all_vinculaciones(self) -> list[Any]:
        return list(self.vinculaciones_odoo)

    def all_vinculaciones_ventas_op(self) -> list[Any]:
        return list(self.vinculaciones_docs)

    def all_entregas(self) -> list[Any]:
        return list(self.entregas)

    def all_serie_tasas(self) -> list[SerieTasa]:
        return list(self.series)

    def last_serie_tasa(self) -> SerieTasa | None:
        return self.series[-1] if self.series else None

    def all_tasas_historicas_auditoria(self) -> list[dict[str, Any]]:
        return list(self.hist_tasas)

    def all_compras_gastos_odoo(self) -> list[dict[str, Any]]:
        return list(self.compras_gastos)

    def all_pagos_proveedores_odoo(self) -> list[dict[str, Any]]:
        return list(self.pagos_prov)

    def all_orden_descuentos_ajustes(self) -> list[dict[str, Any]]:
        return []

    def all_mapeo_listas_ruta(self) -> dict[str, Any]:
        return {}

    def get_app_setting(self, _k: str) -> Any:
        return None


def test_odoo_impuesto_compra_y_verificacion_totales_ok_y_mismatch():
    odoo = OdooFalso(
        account_move__fields_get={
            "move_type": {},
            "partner_id": {},
            "journal_id": {},
            "ref": {},
            "invoice_date": {},
            "invoice_date_due": {},
            "currency_id": {},
            "invoice_line_ids": {},
            "narration": {},
            "message_ids": {},
            "l10n_ve_control_number": {},
        },
        account_move__read=lambda args, _kw: [
            {
                "id": int(args[0][0]),
                "amount_untaxed": 100.0,
                "amount_tax": 16.0,
                "amount_total": 116.0,
            }
        ],
    )
    esc = EscritorOdoo(odoo)
    tid1 = esc.buscar_impuesto_compra(Decimal("16"))
    tid2 = esc.buscar_impuesto_compra(Decimal("16"))
    assert tid1 == 51 and tid2 == 51
    assert len(odoo.llamadas_a("account.tax", "search_read")) == 1

    mid = esc.crear_factura_proveedor(
        partner_id=77,
        numero="F-100",
        fecha_emision="2026-11-20",
        fecha_vencimiento=None,
        moneda_id=1,
        monto=Decimal("100.00"),
        concepto="Lubricantes",
        marca="[CXC_TEST]",
        narration="Detalle",
        tax_ids=[51],
        numero_control="00-001234",
    )
    creado = odoo.creados_de("account.move")[0]
    assert creado["l10n_ve_control_number"] == "00-001234"
    linea = creado["invoice_line_ids"][0][2]
    assert linea["price_unit"] == 100.0
    assert linea["tax_ids"] == [(6, 0, [51])]

    ver_ok = esc.verificar_totales_factura(
        mid,
        esperado_subtotal=Decimal("100.00"),
        esperado_iva=Decimal("16.00"),
        esperado_total=Decimal("116.00"),
    )
    assert ver_ok["ok"] is True and ver_ok["diferencias"] == {}

    # Discrepancia post-creación -> alerta chatter
    odoo_diff = OdooFalso(
        account_move__read=[
            {"id": 9, "amount_untaxed": 100.0, "amount_tax": 0.0, "amount_total": 100.0}
        ]
    )
    esc_diff = EscritorOdoo(odoo_diff)
    ver_bad = esc_diff.verificar_totales_factura(
        9,
        esperado_subtotal=Decimal("100.00"),
        esperado_iva=Decimal("16.00"),
        esperado_total=Decimal("116.00"),
    )
    assert ver_bad["ok"] is False and "iva" in ver_bad["diferencias"]
    assert any("[ALERTA CXC]" in body for _, _, body in odoo_diff.chatter)


def test_servicio_operativos_sin_impuesto_compra_odoo_hace_fallback_a_total():
    eng = _sqlite_engine()
    repo_op = RepoOperativosSQL(eng)
    odoo = OdooFalso(
        res_partner__search=[88],
        account_tax__search_read=[],
        account_move__search=[],
        mail_message__search_read=[],
        account_move__read=[
            {"id": 101, "amount_untaxed": 116.0, "amount_tax": 0.0, "amount_total": 116.0}
        ],
    )
    serv = ServicioOperativos(
        repo_op,
        lambda: EscritorOdoo(odoo),
        repo_capturas=RepoMemoria(),
    )
    doc = serv.crear(
        {
            "tipo": "factura_proveedor",
            "partner_odoo_id": 88,
            "contraparte_nombre": "Proveedor Alpha",
            "rif": "J-301234567",
            "numero_documento": "FAC-88",
            "numero_control": "00-0088",
            "fecha_documento": "2026-11-20",
            "base_imponible": "100.00",
            "iva": "16.00",
            "monto": "116.00",
            "moneda": "VES",
            "concepto": "Insumos",
        },
        actor="admin",
    )
    enviado = serv.aprobar_y_enviar_a_odoo(doc.documento_id, actor="admin")
    assert enviado.estado == OP_ENVIADO
    creado = odoo.creados_de("account.move")[0]
    linea = creado["invoice_line_ids"][0][2]
    assert linea["price_unit"] == 116.0
    assert linea["tax_ids"] == [(6, 0, [])]
    assert any("tax_ids" in body for _, _, body in odoo.chatter)


def test_tasa_binance_cercana_nunca_toma_dia_futuro():
    repo = _RepoCxcStub(
        series=[
            SerieTasa(
                timestamp=datetime(2026, 11, 8, 10, 0), tasa_bcv=40, tasa_binance=50, fuente="s"
            ),
            SerieTasa(
                timestamp=datetime(2026, 11, 10, 14, 0), tasa_bcv=42, tasa_binance=52, fuente="s"
            ),
            SerieTasa(
                timestamp=datetime(2026, 11, 11, 9, 0), tasa_bcv=45, tasa_binance=65, fuente="s"
            ),
        ]
    )
    assert _tasa_binance_cercana(repo, date(2026, 11, 10), Decimal("42")) == Decimal("52")
    assert _tasa_binance_cercana(repo, date(2026, 11, 9), Decimal("41")) == Decimal("50")


def test_vincular_pago_venta_persiste_dual_bcv_y_binance_y_rechaza_otro_partner():
    eng = _sqlite_engine()
    ahora = datetime(2026, 11, 20, 12, 0)
    with eng.begin() as conn:
        conn.execute(
            documentos_comerciales.insert().values(
                documento_id=10,
                numero="PED-00010",
                tipo="pedido",
                estado="ENVIADO_ODOO",
                origen="web",
                partner_odoo_id=100,
                cliente_nombre="Cliente Uno",
                moneda_lista="USD",
                ruta_pago="binance",
                subtotal=Decimal("200.00"),
                subtotal_neto=Decimal("200.00"),
                monto_iva=Decimal("32.00"),
                total_con_iva=Decimal("232.00"),
                odoo_order_id=900,
                odoo_order_name="S00900",
                token_preview="tok-10",
                creado_en=ahora,
                actualizado_en=ahora,
            )
        )
        conn.execute(
            documentos_operativos.insert().values(
                documento_id=20,
                numero="PAG-00020",
                token_preview="tok-op-20",
                tipo="pago",
                estado=CONFIRMADO_CXC,
                origen="telegram",
                partner_odoo_id=100,
                contraparte_nombre="Cliente Uno",
                monto=Decimal("6000.00"),
                moneda="VES",
                metodo_pago="transferencia",
                fecha_documento=date(2026, 11, 20),
                creado_en=ahora,
                actualizado_en=ahora,
            )
        )
        conn.execute(
            documentos_operativos.insert().values(
                documento_id=21,
                numero="PAG-00021",
                token_preview="tok-op-21",
                tipo="pago",
                estado=CONFIRMADO_CXC,
                origen="telegram",
                partner_odoo_id=999,
                contraparte_nombre="Otro Cliente",
                monto=Decimal("100.00"),
                moneda="USD",
                metodo_pago="zelle",
                fecha_documento=date(2026, 11, 20),
                creado_en=ahora,
                actualizado_en=ahora,
            )
        )
    repo_cxc = _RepoCxcStub(
        clientes=[Cliente(cliente_id="100", nombre="Cliente Uno", vendedor_email="v@x.com")],
        series=[
            SerieTasa(
                timestamp=datetime(2026, 11, 20, 12, 0), tasa_bcv=40, tasa_binance=60, fuente="test"
            )
        ],
    )

    with pytest.raises(VinculacionInvalida):
        vincular_pago_venta(eng, repo_cxc, documento_comercial_id=10, pago_operativo_id=21)

    res = vincular_pago_venta(eng, repo_cxc, documento_comercial_id=10, pago_operativo_id=20)
    vinc = res["vinculacion"]
    assert vinc["tasa_bcv"] == 40.0
    assert vinc["tasa_binance"] == 60.0
    assert vinc["equiv_usd_bcv"] == 150.0
    assert vinc["equiv_usd_binance"] == 100.0
    with eng.connect() as conn:
        fila = conn.execute(select(vinculaciones_ventas_op)).first()
        assert fila is not None
        assert Decimal(str(fila.tasa_binance_aplicada)) == Decimal("60")
        assert Decimal(str(fila.equiv_usd_binance)) == Decimal("100.00")


def test_sync_odoo_pagina_mas_de_500_conserva_desc_y_sincroniza_dias_credito():
    docs: dict[int, DocumentoComercial] = {}
    ordenes: list[OrdenVenta] = []
    lineas_orden: dict[str, list[LineaOrden]] = {}
    for idx in range(1, 521):
        so = f"S{idx:05d}"
        dias = 21 if idx == 520 else 0
        sub = Decimal("200.00") if idx == 520 else Decimal("100.00")
        docs[idx] = DocumentoComercial(
            documento_id=idx,
            numero=f"PED-{idx:05d}",
            tipo="pedido",
            estado=ENVIADO_ODOO,
            origen="web",
            captura_id=None,
            partner_odoo_id=10,
            tipo_contacto="cliente",
            cliente_nombre="Cliente Test",
            rif="J-10",
            email="c@x.com",
            telefono="0414",
            direccion="Caracas",
            plus_code="",
            latitud=None,
            longitud=None,
            es_primera_compra=False,
            moneda_lista="USD",
            vendedor_email="v@x.com",
            fecha_entrega=None,
            condiciones_negociacion="",
            observaciones="",
            subtotal=Decimal("100.00"),
            total_litros=Decimal("12.00"),
            total_descuentos=Decimal("20.00"),
            subtotal_neto=Decimal("80.00"),
            monto_iva=Decimal("12.80"),
            total_con_iva=Decimal("92.80"),
            dias_credito=0,
            odoo_order_id=idx,
            odoo_order_name=so,
            error_odoo="",
            creado_por="v@x.com",
            creado_en=datetime(2026, 11, 20),
            actualizado_en=datetime(2026, 11, 20),
            lista_precios_id="21",
            lineas=[
                LineaDocumento(
                    linea_id=idx,
                    orden=0,
                    producto_id="1",
                    codigo="001",
                    descripcion="Aceite",
                    presentacion="Caja",
                    litros_unidad=Decimal("12"),
                    cantidad=Decimal("1"),
                    precio_unitario=Decimal("100.00"),
                    descuento_pct=Decimal("0"),
                    subtotal=Decimal("100.00"),
                )
            ],
        )
        ordenes.append(
            OrdenVenta(
                so_id=so,
                cliente_id="10",
                fecha=date(2026, 11, 20),
                fecha_entrega=None,
                monto_total=sub * Decimal("1.16"),
                lista_precios="21",
                vendedor_email="v@x.com",
                es_primera_compra=False,
                estado_orden="sale",
                dias_credito=dias,
            )
        )
        qty = Decimal("2") if idx == 520 else Decimal("1")
        lineas_orden[so] = [
            LineaOrden(
                linea_id=idx,
                so_id=so,
                producto="1",
                marca="ELITE",
                categoria="LUB",
                cantidad=qty,
                precio_unitario=Decimal("100.00"),
                nombre="Aceite",
                subtotal=sub,
            )
        ]

    class _RepoDocsPaginado:
        def listar(self, *, limit: int = 50, offset: int = 0, **_kw):
            todos = list(docs.values())
            return todos[offset : offset + limit], len(todos), {}

        def actualizar(self, doc_id: int, cambios: dict[str, Any], lineas=None):
            from dataclasses import replace

            kw = dict(cambios)
            if lineas is not None:
                kw["lineas"] = list(lineas)
            docs[doc_id] = replace(docs[doc_id], **kw)
            return docs[doc_id]

    repo_cxc = _RepoCxcStub(ordenes=ordenes, lineas_orden=lineas_orden)
    cat = [
        Producto(
            producto_id="1", codigo="001", nombre="Aceite", marca="ELITE", volumen=12.0, peso=10.0
        )
    ]
    conteos = sincronizar_documentos_desde_odoo(repo_cxc, _RepoDocsPaginado(), catalogo=cat)
    assert conteos["revisados"] == 520
    # Orden 520 cambió cantidades (sub=200) y dias_credito=21, aplicando desc_cxc antes del IVA
    d520 = docs[520]
    assert d520.dias_credito == 21
    assert d520.subtotal == Decimal("200.00")
    assert d520.subtotal_neto == Decimal("180.00")
    assert d520.monto_iva == Decimal("28.80")
    assert d520.total_con_iva == Decimal("208.80")


def test_escritura_buscar_factura_reconciliar_partner_y_promover_proveedor():
    odoo = OdooFalso(
        account_move__search_read=[
            {"id": 11, "name": "INV/011", "invoice_origin": "S00100", "payment_state": "not_paid"},
            {
                "id": 12,
                "name": "INV/012",
                "invoice_origin": "SO99, S0010",
                "payment_state": "partial",
            },
        ],
        res_partner__fields_get={
            "name": {},
            "vat": {},
            "phone": {},
            "street": {},
            "street2": {},
            "customer_rank": {},
            "supplier_rank": {},
            "partner_latitude": {},
            "partner_longitude": {},
            "message_ids": {},
        },
        res_partner__search=[],
        res_partner__read=[{"id": 50, "name": "Taller", "customer_rank": 1, "supplier_rank": 0}],
    )
    esc = EscritorOdoo(odoo)
    assert esc.buscar_factura_abierta_de_orden("S0010") == 12
    dom_fac = odoo.llamadas_a("account.move", "search_read")[0][0]
    assert ["payment_state", "not in", ["paid", "reversed"]] in dom_fac

    esc.actualizar_contacto(50, {}, promover_proveedor=True)
    assert odoo.escritos[-1] == ("res.partner", [50], {"supplier_rank": 1})

    esc.crear_proveedor({"nombre": "Prov GPS", "lat": 10.4806, "lon": -66.9036}, "[MARCA]")
    assert "+" in odoo.creados_de("res.partner")[0]["street2"]

    odoo_mismatch = OdooFalso(
        account_payment__read=[
            {"id": 5, "state": "posted", "move_id": [50, "P5"], "partner_id": [10, "A"]}
        ],
        account_move__read=lambda args, _kw: (
            [{"id": 50, "state": "posted", "name": "P5", "partner_id": [10, "A"]}]
            if args[0][0] == 50
            else [
                {
                    "id": 90,
                    "state": "posted",
                    "payment_state": "not_paid",
                    "move_type": "out_invoice",
                    "partner_id": [20, "B"],
                }
            ]
        ),
    )
    res_rec = EscritorOdoo(odoo_mismatch).reconciliar_pago_con_documento(
        pago_model="account.payment", pago_res_id=5, factura_move_id=90, es_cliente=True
    )
    assert res_rec["reconciliado"] is False
    assert "10" in res_rec["motivo"] and "20" in res_rec["motivo"]


def test_conciliacion_r4_r5_excluye_efectivo_y_ya_conciliados():
    from types import SimpleNamespace

    docs = [
        SimpleNamespace(
            documento_id=1,
            numero="GAS-1",
            tipo="gasto",
            estado=CONFIRMADO_CXC,
            monto=Decimal("50"),
            moneda="USD",
            metodo_pago="efectivo",
            conciliado_banco_huella="",
            fecha_documento=date(2026, 11, 20),
        ),
        SimpleNamespace(
            documento_id=2,
            numero="GAS-2",
            tipo="gasto",
            estado=CONFIRMADO_CXC,
            monto=Decimal("60"),
            moneda="VES",
            metodo_pago="transferencia",
            conciliado_banco_huella="h-dup",
            fecha_documento=date(2026, 11, 20),
        ),
        SimpleNamespace(
            documento_id=3,
            numero="PPR-3",
            tipo="pago_proveedor",
            estado=CONFIRMADO_CXC,
            monto=Decimal("70"),
            moneda="VES",
            metodo_pago="transferencia",
            conciliado_banco_huella="",
            fecha_documento=date(2026, 11, 20),
        ),
        SimpleNamespace(
            documento_id=4,
            numero="PAG-4",
            tipo="pago",
            estado=CONFIRMADO_CXC,
            monto=Decimal("80"),
            moneda="USD",
            metodo_pago="efectivo",
            conciliado_banco_huella="",
            es_zelle_tercero=False,
            fecha_documento=date(2026, 11, 20),
        ),
        SimpleNamespace(
            documento_id=5,
            numero="PAG-5",
            tipo="pago",
            estado=CONFIRMADO_CXC,
            monto=Decimal("90"),
            moneda="VES",
            metodo_pago="pago movil",
            conciliado_banco_huella="",
            es_zelle_tercero=False,
            fecha_documento=date(2026, 11, 20),
        ),
    ]
    assert [e.doc_id for e in egresos_desde_operativos(docs)] == [3]
    assert [i.doc_id for i in ingresos_desde_operativos(docs)] == [5]


def test_saldos_eur_nc_facturado_y_cxp_vencimiento():
    f = date(2026, 11, 10)
    usd_eur = saldos_propios._a_usd_local(
        Decimal("100"),
        "EUR",
        f,
        {f: Decimal("50")},
        Decimal("50"),
        eur_por_fecha={f: Decimal("55")},
        fallback_eur=Decimal("55"),
        usd_bcv_por_fecha={f: Decimal("50")},
        fallback_usd_bcv=Decimal("50"),
    )
    assert usd_eur == Decimal("110.00")

    repo = _RepoCxcStub(
        clientes=[Cliente(cliente_id="1", nombre="Cliente 1", vendedor_email="v@x.com")],
        ordenes=[
            OrdenVenta(
                so_id="S1",
                cliente_id="1",
                fecha=f,
                fecha_entrega=f,
                monto_total=Decimal("100.00"),
                lista_precios="USD",
                vendedor_email="v@x.com",
                es_primera_compra=False,
                estado_orden="sale",
                facturada=True,
                monto_facturado=Decimal("80.00"),
            )
        ],
        facturas=[
            Factura(
                factura_id="F1",
                numero="INV/1",
                so_id="S1",
                move_type="out_invoice",
                es_nota_debito=False,
                fecha=f,
                moneda="USD",
                monto_total=Decimal("100.00"),
                monto_sin_impuestos=Decimal("100.00"),
                estado="posted",
            ),
            Factura(
                factura_id="NC1",
                numero="RINV/1",
                so_id="S1",
                move_type="out_refund",
                es_nota_debito=False,
                fecha=f,
                moneda="USD",
                monto_total=Decimal("20.00"),
                monto_sin_impuestos=Decimal("20.00"),
                monto_total_signed_usd=Decimal("-20.00"),
                estado="posted",
            ),
        ],
        bandejas=[
            BandejaFacturacion(
                so_id="S1",
                lista_aplicada="USD",
                precio_base_calculado=Decimal("100.00"),
                total_descuentos=Decimal("20.00"),
                ncs_calculadas=Decimal("20.00"),
                total_motor=Decimal("80.00"),
                estado=EstadoBandeja.FACTURADO,
            )
        ],
        series=[
            SerieTasa(
                timestamp=datetime(2026, 11, 10, 12), tasa_bcv=50, tasa_binance=60, fuente="s"
            )
        ],
        compras_gastos=[
            {
                "move_id": "501",
                "numero_odoo": "BILL/1",
                "move_type": "in_invoice",
                "tipo_cxc": "factura_proveedor",
                "partner_odoo_id": 70,
                "proveedor_nombre": "Prov 70",
                "rif": "J-70",
                "monto_total": 100.0,
                "moneda": "USD",
                "fecha_documento": "2026-10-01",
                "fecha_vencimiento": "2026-11-15",
                "estado_odoo": "posted",
            }
        ],
    )
    res_cxc = saldos_propios.calcular_saldos_propios(repo, hoy=date(2026, 11, 20))
    # Deduplicó out_refund contra EstadoBandeja.FACTURADO: desc_confirmados=20 (no 40) y saldo=80
    cli = res_cxc["clientes"][0]
    assert cli["descuentos_confirmados_usd"] == 20.0
    assert cli["descuentos_sistema_usd"] == 0.0
    assert cli["notas_credito_usd"] == 20.0
    assert cli["saldo_deudor_usd"] == 80.0

    res_cxp = saldos_cxp.calcular_saldos_cxp(repo, hoy=date(2026, 11, 25))
    prov = res_cxp["proveedores"][0]
    assert prov["dias_mora_max"] == 10  # 2026-11-25 - fecha_vencimiento(2026-11-15)
