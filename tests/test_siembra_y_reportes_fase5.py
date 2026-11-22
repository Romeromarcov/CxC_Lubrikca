"""Pruebas Fase 5: Siembra Histórica Odoo + Correlativos + Reportes 100% sobre BD Propia."""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cxc.config import OdooConfig
from cxc.documentos.modelos import DocumentoComercial
from cxc.engine.saldos_propios import (
    calcular_saldos_propios,
    comparativo_bd_vs_odoo,
    estado_cuenta_cliente,
    exportar_saldos_csv,
)
from cxc.models import (
    BandejaFacturacion,
    Cliente,
    Entrega,
    EntregaLinea,
    EstadoBandeja,
    Factura,
    LineaFactura,
    LineaOrden,
    Moneda,
    OrdenVenta,
    Pago,
    Producto,
    SerieTasa,
    TipoTasa,
)
from cxc.odoo.client import OdooReader, OdooXmlRpcReader, map_pago
from cxc.operativos.modelos import (
    CONFIRMADO_CXC,
    ENVIADO_ODOO,
    PENDIENTE_APROBACION,
    DocumentoOperativo,
)
from cxc.permisos import efectivos
from cxc.repositories import InMemoryRepository
from cxc.sync.siembra_odoo import (
    SiembraOdooService,
    calcular_correlativos,
    extraer_correlativo_numerico,
    guardar_correlativos_sistema,
)
from cxc.web.app import app
from cxc.web.reportes_propios_api import get_extras_reportes_bd, get_siembra_service
from tests import builders as b

client = TestClient(app)

HOY = date(2026, 11, 19)
VENTAS_USER = {"email": "vendedor@lubrikca.com", "rol": "ventas", "permisos": efectivos("ventas")}
ADMIN_USER = {"email": "admin@lubrikca.com", "rol": "admin", "permisos": efectivos("admin")}


class _RepoSaldosConNoOtorgados(InMemoryRepository):
    """Extiende ``InMemoryRepository`` con ``all_descuentos_no_otorgados`` para tests."""

    def __init__(self) -> None:
        super().__init__()
        self._no_otorgados: dict[str, dict[str, str]] = {}

    def all_descuentos_no_otorgados(self) -> dict[str, dict[str, str]]:
        return dict(self._no_otorgados)


class _FakeReaderSiembra(OdooReader):
    """Doble de ``OdooReader`` que registra ``since`` recibido en todas las colecciones."""

    def __init__(self) -> None:
        self.since_calls: dict[str, list[datetime | None]] = {}
        self.clientes = [
            Cliente(cliente_id="101", nombre="TALLER NORTE C.A.", vendedor_email="v1@lubrikca.com"),
            Cliente(cliente_id="102", nombre="DIST. SUR S.R.L.", vendedor_email="v2@lubrikca.com"),
        ]
        self.ordenes = [
            b.orden("S00945", cliente_id="101", monto_total="1000.00", vendedor="v1@lubrikca.com"),
            b.orden("S00912", cliente_id="102", monto_total="500.00", vendedor="v2@lubrikca.com"),
        ]
        self.lineas = [
            b.linea("1001", so_id="S00945"),
            b.linea("1002", so_id="S00912"),
        ]
        self.pagos = [
            Pago(
                pago_id="9001",
                cliente_id="101",
                monto=Decimal("400.00"),
                moneda=Moneda.USD,
                metodo_pago="29",
                fecha_pago=datetime(2026, 11, 10, 9, 0),
                vendedor_email="v1@lubrikca.com",
                numero_odoo="PBAMI/2026/00042",
                referencia="ZELLE-REF-42",
            )
        ]
        self.facturas = [
            Factura(
                factura_id="5001",
                numero="INV/2026/00123",
                so_id="S00945",
                move_type="out_invoice",
                es_nota_debito=False,
                fecha=date(2026, 11, 5),
                moneda="USD",
                monto_total=Decimal("1000.00"),
                monto_sin_impuestos=Decimal("1000.00"),
                estado="posted",
                monto_total_signed_usd=Decimal("1000.00"),
            )
        ]
        self.entregas = [
            Entrega(
                entrega_id="6001",
                so_id="S00945",
                tipo="outgoing",
                fecha=date(2026, 11, 5),
                estado="done",
                es_devolucion=False,
            )
        ]
        self.catalogo = [
            Producto(
                producto_id="7001",
                codigo="EL-15W40",
                nombre="Aceite 15W40 (1x6)",
                marca="GLOBAL OIL",
                volumen=Decimal("12"),
                peso=Decimal("10"),
            )
        ]
        self.lineas_factura = [
            LineaFactura(
                linea_id="8001",
                factura_id="5001",
                nombre="Aceite 15W40",
                cantidad=Decimal("10"),
                precio_unitario=Decimal("100"),
                descuento=Decimal("0"),
                subtotal=Decimal("1000"),
                producto_id="7001",
                so_id_linea="S00945",
            )
        ]
        self.entregas_lineas = [
            EntregaLinea(linea_id="8501", entrega_id="6001", producto_id="7001"),
        ]
        self.pagos_prov = [
            {
                "pago_id": "9501",
                "numero_odoo": "PPROV/2026/00019",
                "partner_odoo_id": 601,
                "proveedor_nombre": "PROV PETROLERO",
                "monto": Decimal("250.00"),
                "moneda": "USD",
                "diario_id": 12,
                "diario_nombre": "Zelle Empresa",
                "referencia": "TR-991",
                "fecha_pago": date(2026, 11, 11),
                "estado_odoo": "posted",
                "reconciled": True,
            }
        ]
        self.compras_gastos = [
            {
                "move_id": "9601",
                "numero_odoo": "BILL/2026/00018",
                "move_type": "in_invoice",
                "tipo_cxc": "factura_proveedor",
                "partner_odoo_id": 601,
                "proveedor_nombre": "PROV PETROLERO",
                "rif": "J-301112229",
                "monto_total": Decimal("250.00"),
                "moneda": "USD",
                "diario_id": 10,
                "diario_nombre": "Compras",
                "referencia": "FAC-601",
                "fecha_documento": date(2026, 11, 9),
                "estado_odoo": "posted",
                "estado_pago": "paid",
            }
        ]

    def _rec(self, key: str, since: datetime | None, data: list[Any]) -> list[Any]:
        self.since_calls.setdefault(key, []).append(since)
        return list(data)

    def changed_clientes(self, since: datetime | None) -> list[Cliente]:
        return self._rec("clientes", since, self.clientes)

    def changed_ordenes(self, since: datetime | None) -> list[OrdenVenta]:
        return self._rec("ordenes", since, self.ordenes)

    def changed_lineas(self, since: datetime | None) -> list[LineaOrden]:
        return self._rec("lineas", since, self.lineas)

    def changed_pagos(self, since: datetime | None) -> list[Pago]:
        return self._rec("pagos", since, self.pagos)

    def lineas_vigentes_por_orden(self, so_ids: list[str]) -> dict[str, set[str]]:
        return {so: set() for so in so_ids}

    def changed_facturas(self, since: datetime | None) -> list[Factura]:
        return self._rec("facturas", since, self.facturas)

    def changed_entregas(self, since: datetime | None) -> list[Entrega]:
        return self._rec("entregas", since, self.entregas)

    def changed_catalogo(self, since: datetime | None) -> list[Producto]:
        return self._rec("catalogo", since, self.catalogo)

    def changed_lineas_factura(self, since: datetime | None) -> list[LineaFactura]:
        return self._rec("lineas_factura", since, self.lineas_factura)

    def changed_entregas_lineas(self, since: datetime | None) -> list[EntregaLinea]:
        return self._rec("entregas_lineas", since, self.entregas_lineas)

    def changed_pagos_proveedores(self, since: datetime | None) -> list[dict[str, Any]]:
        return self._rec("pagos_proveedores", since, self.pagos_prov)

    def changed_compras_gastos(self, since: datetime | None) -> list[dict[str, Any]]:
        return self._rec("compras_gastos", since, self.compras_gastos)


def _doc_comercial(
    doc_id: int,
    *,
    numero: str = "PED-00011",
    tipo: str = "pedido",
    estado: str = ENVIADO_ODOO,
    partner_odoo_id: int | None = 101,
    cliente_nombre: str = "TALLER NORTE C.A.",
    total_con_iva: str = "200.00",
    odoo_order_name: str = "",
    vendedor_email: str = "v1@lubrikca.com",
    fecha_entrega: date | None = date(2026, 11, 15),
) -> DocumentoComercial:
    ahora = datetime(2026, 11, 15, 10, 0)
    tot = Decimal(total_con_iva)
    return DocumentoComercial(
        documento_id=doc_id,
        numero=numero,
        tipo=tipo,
        estado=estado,
        origen="bot",
        captura_id=None,
        partner_odoo_id=partner_odoo_id,
        tipo_contacto="cliente",
        cliente_nombre=cliente_nombre,
        rif="J-123456789",
        email="cliente@example.com",
        telefono="04141234567",
        direccion="Av. Principal",
        plus_code="",
        latitud=None,
        longitud=None,
        es_primera_compra=False,
        moneda_lista="USD",
        vendedor_email=vendedor_email,
        fecha_entrega=fecha_entrega,
        condiciones_negociacion="Contado",
        observaciones="",
        subtotal=tot,
        total_litros=Decimal("10"),
        odoo_order_id=None,
        odoo_order_name=odoo_order_name,
        error_odoo="",
        creado_por="bot",
        creado_en=ahora,
        actualizado_en=ahora,
        total_con_iva=tot,
    )


def _doc_operativo(
    doc_id: int,
    *,
    numero: str = "PAG-00010",
    tipo: str = "pago",
    estado: str = CONFIRMADO_CXC,
    partner_odoo_id: int | None = 101,
    contraparte_nombre: str = "TALLER NORTE C.A.",
    monto: str = "100.00",
    moneda: str = "USD",
    metodo_pago: str = "Efectivo USD",
    fecha_documento: date | None = date(2026, 11, 16),
    odoo_model: str = "",
    odoo_res_id: int | None = None,
    es_zelle_tercero: bool = False,
    confirmado_cxc: bool = True,
    numero_recibo: str = "REC-20261116-001",
) -> DocumentoOperativo:
    ahora = datetime(2026, 11, 16, 10, 0)
    return DocumentoOperativo(
        documento_id=doc_id,
        numero=numero,
        token_preview=f"tok-{doc_id}",
        tipo=tipo,
        estado=estado,
        origen="bot",
        captura_id=None,
        partner_odoo_id=partner_odoo_id,
        contraparte_nombre=contraparte_nombre,
        rif="J-123456789",
        monto=Decimal(monto),
        base_imponible=None,
        iva=None,
        moneda=moneda,
        metodo_pago=metodo_pago,
        banco="",
        diario_id=None,
        diario_nombre="",
        cuenta_contable_id=None,
        cuenta_contable_codigo="",
        cuenta_contable_nombre="",
        referencia="REF-OP",
        numero_documento="",
        numero_control="",
        pedido_ref="",
        factura_ref="",
        orden_ref="",
        categoria="",
        concepto="",
        sin_factura=False,
        fecha_documento=fecha_documento,
        fecha_vencimiento=None,
        proveedor_telefono="",
        proveedor_email="",
        proveedor_direccion="",
        notas="",
        validacion={},
        faltantes=(),
        aprobado_por="",
        aprobado_en=None,
        rechazo_motivo="",
        odoo_model=odoo_model,
        odoo_res_id=odoo_res_id,
        odoo_ref="",
        error_odoo="",
        creado_por="bot",
        creado_en=ahora,
        actualizado_en=ahora,
        es_zelle_tercero=es_zelle_tercero,
        cuenta_tercero_id=5 if es_zelle_tercero else None,
        beneficiario_tercero="Proveedor Zelle SA" if es_zelle_tercero else "",
        confirmado_cxc=confirmado_cxc,
        confirmado_cxc_por="admin@lubrikca.com" if confirmado_cxc else "",
        confirmado_cxc_en=ahora if confirmado_cxc else None,
        numero_recibo=numero_recibo,
        recibo_emitido_en=ahora if numero_recibo else None,
    )


# ---------------------------------------------------------------------------
# 1. Correlativos Odoo y CxC
# ---------------------------------------------------------------------------
def test_extraer_y_calcular_correlativos_odoo_y_cxc() -> None:
    assert extraer_correlativo_numerico("S00945") == ("S", 945)
    assert extraer_correlativo_numerico("INV/2026/00123") == ("INV/2026/", 123)
    assert extraer_correlativo_numerico("PBAMI/2026/00042") == ("PBAMI/2026/", 42)
    assert extraer_correlativo_numerico("BILL/2026/00018") == ("BILL/2026/", 18)
    assert extraer_correlativo_numerico("COT-00005") == ("COT-", 5)
    assert extraer_correlativo_numerico("PED-00011") == ("PED-", 11)
    assert extraer_correlativo_numerico("REC-20261119-003") == ("REC-20261119-", 3)
    assert extraer_correlativo_numerico("GAS-00007") == ("GAS-", 7)
    assert extraer_correlativo_numerico("") is None
    assert extraer_correlativo_numerico("SIN_NUMEROS") is None

    res = calcular_correlativos(
        {
            ("ordenes_venta", "odoo"): ["S00912", "S00945", "S00900", "BORRADOR"],
            ("facturas", "odoo"): ["INV/2026/00100", "INV/2026/00123"],
            ("pagos_recibidos", "odoo"): ["PBAMI/2026/00009", "PBAMI/2026/00042"],
            ("compras_gastos", "odoo"): ["BILL/2026/00018"],
            ("cxc_cotizaciones", "cxc"): ["COT-00002", "COT-00005"],
            ("cxc_pedidos", "cxc"): ["PED-00011"],
            ("cxc_recibos", "cxc"): ["REC-20261119-001", "REC-20261119-003"],
            ("cxc_operativos", "cxc"): ["GAS-00007"],
        },
        ahora=datetime(2026, 11, 19, 12, 0),
    )
    por_clave = {(r["entidad"], r["prefijo"]): r for r in res}
    assert por_clave[("ordenes_venta", "S")]["ultimo_numero"] == 945
    assert por_clave[("ordenes_venta", "S")]["ejemplo_maximo"] == "S00945"
    assert por_clave[("ordenes_venta", "S")]["total_registros"] == 3
    assert por_clave[("facturas", "INV/2026/")]["ultimo_numero"] == 123
    assert por_clave[("pagos_recibidos", "PBAMI/2026/")]["ultimo_numero"] == 42
    assert por_clave[("compras_gastos", "BILL/2026/")]["ultimo_numero"] == 18
    assert por_clave[("cxc_cotizaciones", "COT-")]["ultimo_numero"] == 5
    assert por_clave[("cxc_pedidos", "PED-")]["ultimo_numero"] == 11
    assert por_clave[("cxc_recibos", "REC-20261119-")]["ultimo_numero"] == 3
    assert por_clave[("cxc_operativos", "GAS-")]["ultimo_numero"] == 7

    repo = InMemoryRepository()
    serializados = guardar_correlativos_sistema(repo, res)
    assert len(serializados) == len(res)
    assert repo.get_config("correlativos_sistema_json") is not None


# ---------------------------------------------------------------------------
# 2. Reader Odoo preserva numero_odoo y referencia
# ---------------------------------------------------------------------------
def test_odoo_changed_pagos_y_pagos_por_id_preservan_numero_y_referencia() -> None:
    p_fallback = map_pago(
        {
            "id": 77,
            "partner_id": [181, "ACME"],
            "amount": 100.0,
            "currency_id": [1, "USD"],
            "journal_id": [29, "Zelle"],
            "date": "2026-11-12",
            "name": False,
            "move_id": [550, "PBAMI/2026/00042"],
            "payment_reference": "REF-ZELLE-998",
        }
    )
    assert p_fallback.numero_odoo == "PBAMI/2026/00042"
    assert p_fallback.referencia == "REF-ZELLE-998"

    for campo_ref, valor_ref in (("memo", "MEMO-TR-01"), ("payment_reference", "PREF-02")):

        def _make_exec(ref_field: str = campo_ref, ref_val: str = valor_ref):
            def _exec(model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
                if model == "account.payment" and method == "fields_get":
                    return {"id": {}, "name": {}, ref_field: {}}
                if model == "account.payment" and method in ("search_read", "read"):
                    return [
                        {
                            "id": 88,
                            "name": "PAY/2026/0088",
                            "partner_id": [181, "ACME"],
                            "amount": 250.0,
                            "currency_id": [1, "USD"],
                            "journal_id": [29, "Zelle"],
                            "date": "2026-11-14",
                            "move_id": [900, "ASIENTO/900"],
                            ref_field: ref_val,
                        }
                    ]
                if model == "res.partner":
                    return [{"id": 181, "user_id": [13, "V1"]}]
                if model == "res.users":
                    return [{"id": 13, "name": "v1@lubrikca.com", "login": "v1@lubrikca.com"}]
                return []

            return _exec

        cfg = OdooConfig(url="http://x", db="d", username="u", password="p")
        reader = OdooXmlRpcReader(cfg, execute=_make_exec())
        p_changed = reader.changed_pagos(None)[0]
        assert p_changed.numero_odoo == "PAY/2026/0088"
        assert p_changed.referencia == valor_ref

        p_por_id = reader.pagos_por_id(["88"])[0]
        assert p_por_id.numero_odoo == "PAY/2026/0088"
        assert p_por_id.referencia == valor_ref


# ---------------------------------------------------------------------------
# 3. Siembra histórica completa + idempotencia + preservación de trabajo humano
# ---------------------------------------------------------------------------
def test_siembra_odoo_service_siembra_todo_e_idempotente() -> None:
    repo = InMemoryRepository()
    vinc_humana = b.vinculacion("V-HUM-1", so_id="S00945", pago_id="9001")
    repo.add_vinculacion(vinc_humana)
    serie_tasa = SerieTasa(
        timestamp=datetime(2026, 11, 10, 8, 0),
        tasa_bcv=Decimal("45.50"),
        tasa_binance=Decimal("52.00"),
        fuente="oficial",
    )
    repo.append_serie_tasa(serie_tasa)

    reader = _FakeReaderSiembra()
    srv = SiembraOdooService(repo, reader)

    assert SiembraOdooService.requiere_siembra_inicial(repo) is True
    res = srv.ejecutar(actor="admin@lubrikca.com")

    assert res["estado"] == "COMPLETADA"
    assert res["tipo"] == "completa"
    assert SiembraOdooService.requiere_siembra_inicial(repo) is False
    for k, calls in reader.since_calls.items():
        assert calls == [None], f"{k} debió consultarse con since=None"

    assert res["conteos_sembrados"]["clientes"] == 2
    assert res["conteos_sembrados"]["ordenes_venta"] == 2
    assert res["conteos_sembrados"]["pagos"] == 1
    assert res["conteos_sembrados"]["facturas"] == 1
    assert res["conteos_sembrados"]["pagos_proveedores_odoo"] == 1
    assert res["conteos_sembrados"]["compras_gastos_odoo"] == 1

    # Correlativos máximos conservados
    por_clave = {(c["entidad"], c["prefijo"]): c for c in res["correlativos"]}
    assert por_clave[("ordenes_venta", "S")]["ultimo_numero"] == 945
    assert por_clave[("facturas", "INV/2026/")]["ultimo_numero"] == 123
    assert por_clave[("pagos_recibidos", "PBAMI/2026/")]["ultimo_numero"] == 42
    assert por_clave[("pagos_proveedores", "PPROV/2026/")]["ultimo_numero"] == 19
    assert por_clave[("compras_gastos", "BILL/2026/")]["ultimo_numero"] == 18

    # Trabajo humano e inmutable intactos
    assert len(repo.all_vinculaciones()) == 1
    assert repo.all_vinculaciones()[0].vinc_id == "V-HUM-1"
    assert len(repo.all_serie_tasas()) == 1
    assert repo.all_serie_tasas()[0].tasa_bcv == Decimal("45.50")

    # 2da corrida idempotente
    res2 = srv.ejecutar(actor="admin@lubrikca.com")
    assert res2["estado"] == "COMPLETADA"
    assert len(repo.all_clientes()) == 2
    assert len(repo.all_ordenes()) == 2

    # Modo solo_correlativos no consulta a Odoo
    n_calls_before = sum(len(v) for v in reader.since_calls.values())
    res_corr = srv.ejecutar(actor="admin@lubrikca.com", solo_correlativos=True)
    assert res_corr["tipo"] == "solo_correlativos"
    assert sum(len(v) for v in reader.since_calls.values()) == n_calls_before

    estado = SiembraOdooService.estado_actual(repo)
    assert estado["requiere_siembra_inicial"] is False
    assert len(estado["correlativos"]) >= 5


# ---------------------------------------------------------------------------
# 4. Motor de saldos propios (BD CxC sin Odoo)
# ---------------------------------------------------------------------------
def test_motor_saldos_propios_formula_exacta_fifo_y_conciliacion() -> None:
    repo = _RepoSaldosConNoOtorgados()
    repo.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 10, 1, 9, 0),
            tasa_bcv=Decimal("50.00"),
            tasa_binance=Decimal("55.00"),
            fuente="test",
        )
    )
    repo.upsert_clientes(
        [
            Cliente(cliente_id="101", nombre="ALFA LUBRICANTES", vendedor_email="v1@lubrikca.com"),
            Cliente(cliente_id="102", nombre="BETA MOTORS", vendedor_email="v2@lubrikca.com"),
        ]
    )
    repo.upsert_ordenes(
        [
            # Alfa: orden vieja vencida hace 45 días (01-oct + 0 días, hoy 19-nov => 49 días)
            OrdenVenta(
                so_id="S00900",
                cliente_id="101",
                fecha=date(2026, 10, 1),
                fecha_entrega=date(2026, 10, 1),
                monto_total=Decimal("1000.00"),
                lista_precios="USD",
                vendedor_email="v1@lubrikca.com",
                es_primera_compra=False,
                estado_orden="sale",
                dias_credito=0,
            ),
            # Alfa: orden reciente aún vigente (vence 25-nov)
            OrdenVenta(
                so_id="S00901",
                cliente_id="101",
                fecha=date(2026, 11, 10),
                fecha_entrega=date(2026, 11, 10),
                monto_total=Decimal("500.00"),
                lista_precios="USD",
                vendedor_email="v1@lubrikca.com",
                es_primera_compra=False,
                estado_orden="sale",
                dias_credito=15,
            ),
            # Alfa: orden en descuentos_no_otorgados (su descuento en bandeja NO resta)
            OrdenVenta(
                so_id="S00902",
                cliente_id="101",
                fecha=date(2026, 11, 12),
                fecha_entrega=date(2026, 11, 12),
                monto_total=Decimal("100.00"),
                lista_precios="USD",
                vendedor_email="v1@lubrikca.com",
                es_primera_compra=False,
                estado_orden="sale",
                dias_credito=15,
            ),
            # Beta: orden 200 USD que quedará sobrepagada -> saldo a favor 50 USD
            OrdenVenta(
                so_id="S00903",
                cliente_id="102",
                fecha=date(2026, 11, 1),
                fecha_entrega=date(2026, 11, 1),
                monto_total=Decimal("200.00"),
                lista_precios="USD",
                vendedor_email="v2@lubrikca.com",
                es_primera_compra=False,
                estado_orden="sale",
                dias_credito=0,
            ),
        ]
    )

    # Descuentos: S00900 aprobado $50, S00902 marcado no_otorgado $40 (NO debe restar)
    repo.upsert_descuento_sistema_aprobado({"so_id": "S00900", "monto": "50.00", "activo": "TRUE"})
    repo._no_otorgados["S00902"] = {"so_id": "S00902", "motivo": "Excluido manual"}
    repo.upsert_bandeja(
        BandejaFacturacion(
            so_id="S00902",
            lista_aplicada="USD",
            precio_base_calculado=Decimal("100.00"),
            total_descuentos=Decimal("40.00"),
            estado=EstadoBandeja.APROBADO,
        )
    )

    # Facturas: 1 factura posted + 1 Nota de Crédito posted out_refund de $30 sobre S00900
    repo.upsert_facturas(
        [
            Factura(
                factura_id="F1",
                numero="INV/2026/00090",
                so_id="S00900",
                move_type="out_invoice",
                es_nota_debito=False,
                fecha=date(2026, 10, 1),
                moneda="USD",
                monto_total=Decimal("1000.00"),
                monto_sin_impuestos=Decimal("1000.00"),
                estado="posted",
                monto_total_signed_usd=Decimal("1000.00"),
            ),
            Factura(
                factura_id="NC1",
                numero="RINV/2026/00010",
                so_id="S00900",
                move_type="out_refund",
                es_nota_debito=False,
                fecha=date(2026, 10, 3),
                moneda="USD",
                monto_total=Decimal("30.00"),
                monto_sin_impuestos=Decimal("30.00"),
                estado="posted",
                monto_total_signed_usd=Decimal("-30.00"),
            ),
        ]
    )

    # Pagos Odoo:
    # - Pago 100 (USD $300 a Alfa)
    # - Pago 101 (VES 5000 @ BCV 50.00 = USD $100 a Alfa, convertido 100% localmente)
    # - Pago 102 (USD $250 a Beta -> Beta queda $50 a favor)
    repo.upsert_pagos(
        [
            Pago(
                pago_id="100",
                cliente_id="101",
                monto=Decimal("300.00"),
                moneda=Moneda.USD,
                metodo_pago="29",
                fecha_pago=datetime(2026, 10, 5, 10, 0),
                vendedor_email="v1@lubrikca.com",
                numero_odoo="PUSD/2026/00100",
                referencia="Z-100",
            ),
            Pago(
                pago_id="101",
                cliente_id="101",
                monto=Decimal("5000.00"),
                moneda=Moneda.VES,
                metodo_pago="30",
                fecha_pago=datetime(2026, 10, 6, 10, 0),
                vendedor_email="v1@lubrikca.com",
                numero_odoo="PBAMI/2026/00101",
                referencia="PM-101",
            ),
            Pago(
                pago_id="102",
                cliente_id="102",
                monto=Decimal("250.00"),
                moneda=Moneda.USD,
                metodo_pago="29",
                fecha_pago=datetime(2026, 11, 2, 10, 0),
                vendedor_email="v2@lubrikca.com",
                numero_odoo="PUSD/2026/00102",
                referencia="Z-102",
            ),
        ]
    )
    v_alfa = b.vinculacion(
        "V-ALFA-1",
        pago_id="100",
        so_id="S00900",
        monto_aplicado="300.00",
        moneda_abono=Moneda.USD,
        tipo_tasa_abono=TipoTasa.N_A,
    )
    v_alfa.equiv_usd_bcv = Decimal("300.00")
    repo.add_vinculacion(v_alfa)

    # Documentos comerciales CxC:
    # - S00901 YA existe en ordenes_venta => NO debe duplicarse
    # - PED-00011 ENVIADO_ODOO con odoo_order_name="S00999" aún fuera del espejo => SÍ suma +200.00
    docs_com = [
        _doc_comercial(
            1,
            numero="PED-00010",
            odoo_order_name="S00901",
            total_con_iva="500.00",
            partner_odoo_id=101,
        ),
        _doc_comercial(
            2,
            numero="PED-00011",
            odoo_order_name="S00999",
            total_con_iva="200.00",
            partner_odoo_id=101,
            fecha_entrega=date(2026, 11, 18),
        ),
    ]

    # Documentos operativos CxC:
    # - Zelle 3ro confirmado (odoo_model="account.move", MISMO odoo_res_id=100 que pago 100)
    # - Efectivo CxC confirmado $150
    # - Pago pendiente sin confirmar $999 -> NO debe restar
    docs_op = [
        _doc_operativo(
            10,
            numero="PAG-00010",
            estado=ENVIADO_ODOO,
            partner_odoo_id=101,
            monto="120.00",
            moneda="USD",
            metodo_pago="Zelle Tercero",
            odoo_model="account.move",
            odoo_res_id=100,
            es_zelle_tercero=True,
            confirmado_cxc=True,
        ),
        _doc_operativo(
            11,
            numero="PAG-00011",
            estado=CONFIRMADO_CXC,
            partner_odoo_id=101,
            monto="150.00",
            moneda="USD",
            metodo_pago="Efectivo USD",
            confirmado_cxc=True,
        ),
        _doc_operativo(
            12,
            numero="PAG-00012",
            estado=PENDIENTE_APROBACION,
            partner_odoo_id=101,
            monto="999.00",
            moneda="USD",
            metodo_pago="Efectivo USD",
            confirmado_cxc=False,
        ),
    ]

    rep = calcular_saldos_propios(
        repo,
        docs_comerciales=docs_com,
        docs_operativos=docs_op,
        hoy=HOY,
    )
    assert rep["fuente"] == "bd_propia_cxc"
    assert rep["solo_lectura"] is True

    por_cid = {c["cliente_id"]: c for c in rep["clientes"]}
    alfa = por_cid["101"]
    beta = por_cid["102"]

    # Alfa:
    # Ventas = 1000 + 500 + 100 + 200 (PED-00011) = 1800.00
    # Descuentos = 50 (aprobado) + 30 (NC) = 80.00 (S00902 no_otorgado ignorado)
    # Pagos = 300 (Odoo USD) + 100 (Odoo VES@50) + 120 (Zelle 3ro) + 150 (Efectivo CxC) = 670.00
    # Saldo Deudor = 1800 - 80 - 670 = 1050.00
    assert alfa["ventas_confirmadas_usd"] == 1800.00
    assert alfa["descuentos_sistema_usd"] == 50.00
    assert alfa["notas_credito_usd"] == 30.00
    assert alfa["descuentos_confirmados_usd"] == 80.00
    assert alfa["pagos_odoo_usd"] == 400.00
    assert alfa["pagos_zelle_tercero_usd"] == 120.00
    assert alfa["pagos_efectivo_cxc_usd"] == 150.00
    assert alfa["pagos_confirmados_usd"] == 670.00
    assert alfa["saldo_deudor_usd"] == 1050.00
    assert alfa["saldo_favor_usd"] == 0.0

    # FIFO mora Alfa: crédito total = 80 + 670 = 750.00 absorbe 750 de S00900 (1000),
    # dejando 250 vencidos 49d (31_60), PED-00011 vencido 1d (200 en 1_30) y S00901+S00902 (600)
    assert alfa["tramos"]["1_30"] == 200.00
    assert alfa["tramos"]["31_60"] == 250.00
    assert alfa["vencido_usd"] == 450.00
    assert alfa["vigente_usd"] == 600.00
    assert alfa["dias_mora_max"] == 49

    # Beta sobrepagado
    assert beta["saldo_deudor_usd"] == 0.0
    assert beta["saldo_favor_usd"] == 50.00

    # Filtros
    solo_v1 = calcular_saldos_propios(
        repo,
        docs_comerciales=docs_com,
        docs_operativos=docs_op,
        hoy=HOY,
        vendedor="v1@lubrikca.com",
        antiguedad="31_60",
        q="ALFA",
        solo_con_saldo=True,
    )
    assert len(solo_v1["clientes"]) == 1
    assert solo_v1["kpis_globales"]["clientes_con_saldo"] == 1

    # Estado de cuenta cronológico
    ec = estado_cuenta_cliente(
        repo, "101", docs_comerciales=docs_com, docs_operativos=docs_op, hoy=HOY
    )
    assert ec is not None
    assert ec["movimientos"][-1]["saldo_acumulado_usd"] == 1050.00

    # Comparativo y CSV
    comp = comparativo_bd_vs_odoo(repo, docs_comerciales=docs_com, docs_operativos=docs_op, hoy=HOY)
    assert comp["explicacion_diferencias"]["pagos_zelle_tercero_cxc_usd"] == 120.00
    assert comp["explicacion_diferencias"]["pagos_efectivo_cxc_usd"] == 150.00

    csv_bytes = exportar_saldos_csv(rep)
    assert csv_bytes.startswith(b"\xef\xbb\xbf")
    assert b"ALFA LUBRICANTES" in csv_bytes


# ---------------------------------------------------------------------------
# 5. Endpoints REST y autorización
# ---------------------------------------------------------------------------
def test_endpoints_reportes_propios_y_siembra_auth_y_flujo() -> None:
    repo = InMemoryRepository()
    repo.upsert_clientes(
        [
            Cliente(cliente_id="101", nombre="ALFA", vendedor_email="vendedor@lubrikca.com"),
            Cliente(cliente_id="102", nombre="BETA", vendedor_email="otro@lubrikca.com"),
        ]
    )
    repo.upsert_ordenes(
        [
            b.orden(
                "S00900",
                cliente_id="101",
                monto_total="300.00",
                vendedor="vendedor@lubrikca.com",
            ),
            b.orden(
                "S00901",
                cliente_id="102",
                monto_total="450.00",
                vendedor="otro@lubrikca.com",
            ),
        ]
    )
    reader = _FakeReaderSiembra()
    srv = SiembraOdooService(repo, reader)

    rutas_protegidas = [
        ("GET", "/api/reportes/saldos-propios"),
        ("GET", "/api/reportes/estado-cuenta/101"),
        ("GET", "/api/reportes/saldos-propios/csv"),
        ("GET", "/api/auditoria/comparativo-bd-odoo"),
        ("GET", "/api/admin/siembra-odoo/estado"),
        ("POST", "/api/admin/siembra-odoo"),
    ]
    usuario_actual: dict[str, Any] | None = None
    app.dependency_overrides[get_extras_reportes_bd] = lambda: ((), ())
    app.dependency_overrides[get_siembra_service] = lambda: srv
    try:
        with (
            patch("cxc.web.app.get_repo", return_value=repo),
            patch(
                "cxc.web.app.get_current_user_from_cookie",
                side_effect=lambda _c: dict(usuario_actual) if usuario_actual else None,
            ),
        ):
            # Defensa en profundidad: endpoint devuelve 401 sin usuario válido
            for method, path in rutas_protegidas:
                resp = client.request(method, path)
                assert resp.status_code == 401, f"{method} {path} debe exigir sesión (endpoint)"

            # Y el middleware real también bloquea con 401 sin necesidad de llegar al endpoint
            with patch("cxc.web.app.hay_sesion_valida", return_value=False):
                for method, path in rutas_protegidas:
                    resp = client.request(method, path)
                    assert resp.status_code == 401, f"{method} {path} debe exigir sesión (mw)"

            # Rol ventas puede consultar saldos y CSV, pero NO ejecutar siembra
            usuario_actual = dict(VENTAS_USER)
            r_saldos = client.get("/api/reportes/saldos-propios", cookies={"cxc_session": "ok"})
            assert r_saldos.status_code == 200
            assert r_saldos.json()["kpis"]["saldo_deudor_usd"] == 300.0

            r_csv = client.get("/api/reportes/saldos-propios/csv", cookies={"cxc_session": "ok"})
            assert r_csv.status_code == 200
            assert r_csv.content.startswith(b"\xef\xbb\xbf")

            r_forb = client.post(
                "/api/admin/siembra-odoo",
                json={"sincrono": True},
                cookies={"cxc_session": "ok"},
            )
            assert r_forb.status_code == 403

            # Rol admin puede todo
            usuario_actual = dict(ADMIN_USER)

            r_ec = client.get("/api/reportes/estado-cuenta/101", cookies={"cxc_session": "ok"})
            assert r_ec.status_code == 200
            assert r_ec.json()["resumen"]["saldo_deudor_usd"] == 300.0

            r_404 = client.get("/api/reportes/estado-cuenta/999999", cookies={"cxc_session": "ok"})
            assert r_404.status_code == 404

            r_422 = client.get(
                "/api/reportes/saldos-propios?hoy=fecha-invalida", cookies={"cxc_session": "ok"}
            )
            assert r_422.status_code == 422

            r_comp = client.get("/api/auditoria/comparativo-bd-odoo", cookies={"cxc_session": "ok"})
            assert r_comp.status_code == 200
            assert "explicacion_diferencias" in r_comp.json()

            r_sync = client.post(
                "/api/admin/siembra-odoo",
                json={"sincrono": True},
                cookies={"cxc_session": "ok"},
            )
            assert r_sync.status_code == 200
            assert r_sync.json()["resultado"]["estado"] == "COMPLETADA"

            r_est = client.get("/api/admin/siembra-odoo/estado", cookies={"cxc_session": "ok"})
            assert r_est.status_code == 200
            assert r_est.json()["requiere_siembra_inicial"] is False
            assert r_est.json()["puede_sembrar"] is True

            r_async = client.post(
                "/api/admin/siembra-odoo",
                json={"solo_correlativos": True},
                cookies={"cxc_session": "ok"},
            )
            assert r_async.status_code == 202
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 6. Prueba Postgres real (condicional si DATABASE_URL está definida, ej. CI)
# ---------------------------------------------------------------------------
def test_correlativos_siembra_y_pago_numero_sql_si_hay_postgres() -> None:
    from sqlalchemy import delete, select

    from cxc.db import schema
    from cxc.db.engine import make_engine
    from cxc.db.postgres_repository import PostgresRepository

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("Sin DATABASE_URL para probar correlativos_sistema / siembra_bitacora SQL.")

    eng = make_engine(url)
    schema.metadata.create_all(
        eng,
        tables=[
            schema.clientes,
            schema.pagos,
            schema.correlativos_sistema,
            schema.siembra_bitacora,
        ],
        checkfirst=True,
    )
    repo_sql = PostgresRepository(eng)
    reader = _FakeReaderSiembra()
    srv = SiembraOdooService(repo_sql, reader, engine=eng)
    siembra_id: int | None = None

    with eng.begin() as conn:
        conn.execute(delete(schema.pagos).where(schema.pagos.c.pago_id == "TEST-F5-PAGO"))
        conn.execute(delete(schema.clientes).where(schema.clientes.c.cliente_id == "TEST-F5-CLI"))
        conn.execute(
            delete(schema.correlativos_sistema).where(
                schema.correlativos_sistema.c.entidad == "test_f5_entidad"
            )
        )

    try:
        repo_sql.upsert_clientes(
            [Cliente(cliente_id="TEST-F5-CLI", nombre="CLI TEST F5", vendedor_email="v@test.com")]
        )
        repo_sql.upsert_pagos(
            [
                Pago(
                    pago_id="TEST-F5-PAGO",
                    cliente_id="TEST-F5-CLI",
                    monto=Decimal("77.50"),
                    moneda=Moneda.USD,
                    metodo_pago="29",
                    fecha_pago=datetime(2026, 11, 19, 10, 0),
                    vendedor_email="v@test.com",
                    numero_odoo="PUSD/TEST/00777",
                    referencia="REF-SQL-777",
                )
            ]
        )
        pagos_full = [r for r in repo_sql.all_pagos_full() if r["pago_id"] == "TEST-F5-PAGO"]
        assert len(pagos_full) == 1
        assert pagos_full[0]["numero_odoo"] == "PUSD/TEST/00777"
        assert pagos_full[0]["referencia"] == "REF-SQL-777"

        corrs = calcular_correlativos(
            {("test_f5_entidad", "odoo"): ["TF5-00012", "TF5-00099"]},
            ahora=datetime(2026, 11, 19, 11, 0),
        )
        guardar_correlativos_sistema(repo_sql, corrs, engine=eng)
        with eng.connect() as conn:
            row_corr = conn.execute(
                select(schema.correlativos_sistema).where(
                    schema.correlativos_sistema.c.entidad == "test_f5_entidad"
                )
            ).first()
            assert row_corr is not None
            assert int(row_corr.ultimo_numero) == 99

        res_corr = srv.ejecutar(actor="pytest_f5", solo_correlativos=True)
        siembra_id = res_corr.get("siembra_id")
        assert res_corr["estado"] == "COMPLETADA"
        assert siembra_id is not None
    finally:
        with eng.begin() as conn:
            conn.execute(delete(schema.pagos).where(schema.pagos.c.pago_id == "TEST-F5-PAGO"))
            conn.execute(
                delete(schema.clientes).where(schema.clientes.c.cliente_id == "TEST-F5-CLI")
            )
            conn.execute(
                delete(schema.correlativos_sistema).where(
                    schema.correlativos_sistema.c.entidad == "test_f5_entidad"
                )
            )
            if siembra_id is not None:
                conn.execute(
                    delete(schema.siembra_bitacora).where(
                        schema.siembra_bitacora.c.siembra_id == int(siembra_id)
                    )
                )
