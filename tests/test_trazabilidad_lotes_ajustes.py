"""Pruebas de trazabilidad, lotes de efectivo, conciliación y ajustes de descuento."""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select
from sqlalchemy.pool import StaticPool

from cxc.bancos import repositorio as bancos_repo
from cxc.db import schema
from cxc.engine.saldos_propios import calcular_saldos_propios
from cxc.models import (
    BandejaFacturacion,
    Cliente,
    DescuentoAplicado,
    EstadoBandeja,
    MetodoPago,
    Moneda,
    OrdenVenta,
    Pago,
    SerieTasa,
    TipoTasa,
)
from cxc.odoo.client import map_orden
from cxc.operativos.lotes_efectivo import es_pago_efectivo
from cxc.operativos.modelos import CONFIRMADO_CXC, ENVIADO_ODOO, PENDIENTE_APROBACION
from cxc.operativos.repositorio import RepoOperativosSQL
from cxc.operativos.servicio import ServicioOperativos
from cxc.operativos.terceros import RepoCuentasTercerosSQL
from cxc.permisos import efectivos
from cxc.repositories import InMemoryRepository
from cxc.web.app import app
from cxc.web.operativos_api import get_repo_terceros, get_servicio_operativos

client = TestClient(app)

ADMIN_USER = {
    "email": "admin@lubrikca.com",
    "nombre": "Admin Prueba",
    "rol": "admin",
    "permisos": efectivos("admin"),
}
TESO_USER = {
    "email": "tesoreria@lubrikca.com",
    "nombre": "María Caja",
    "rol": "tesoreria",
    "permisos": efectivos("tesoreria"),
}


def _sqlite_engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    schema.metadata.create_all(
        eng,
        tables=[
            schema.clientes,
            schema.ordenes_venta,
            schema.lineas_orden,
            schema.pagos,
            schema.metodos_pago,
            schema.serie_tasas,
            schema.tasas_historicas_auditoria,
            schema.bandeja_facturacion,
            schema.vinculaciones,
            schema.facturas,
            schema.bandeja_auditoria,
            schema.descuentos_sistema_aprobados,
            schema.descuentos_no_otorgados,
            schema.app_settings,
            schema.usuarios_plataforma,
            schema.lotes_caja_chica,
            schema.cuentas_terceros,
            schema.documentos_operativos,
            schema.documentos_comerciales,
            schema.documentos_comerciales_lineas,
            schema.lotes_recibo_efectivo,
            schema.orden_descuentos_ajustes,
            schema.bancos_archivos,
            schema.bancos_movimientos,
            schema.bancos_propuestas,
            schema.bancos_decisiones,
        ],
    )
    return eng


class _RepoHibridoSQL(InMemoryRepository):
    """InMemoryRepository backed by a real SQLite engine for SQL-specific services."""

    def __init__(self, engine: Any) -> None:
        super().__init__()
        self._engine = engine
        self._pagos_full: list[dict[str, str]] = []
        self._pagos_prov: list[dict[str, Any]] = []
        self._compras_odoo: list[dict[str, Any]] = []
        self._usuarios: list[dict[str, str]] = [
            {
                "email": "vend1@lubrikca.com",
                "nombre": "Carlos Vendedor",
                "user_id": "vend1@lubrikca.com",
            },
            {
                "email": "tesoreria@lubrikca.com",
                "nombre": "María Caja",
                "user_id": "tesoreria@lubrikca.com",
            },
        ]

    def all_usuarios_plataforma(self) -> list[dict[str, str]]:
        return list(self._usuarios)

    def all_pagos_full(self) -> list[dict[str, str]]:
        # Read live updated recibido state from SQL pagos table when available
        out: list[dict[str, str]] = []
        with self._engine.connect() as conn:
            rows = conn.execute(select(schema.pagos)).all()
        if rows:
            for r in rows:
                out.append(
                    {
                        "pago_id": str(r.pago_id),
                        "cliente_id": str(r.cliente_id),
                        "monto": str(r.monto),
                        "moneda": str(r.moneda.value if hasattr(r.moneda, "value") else r.moneda),
                        "metodo_pago": str(r.metodo_pago or ""),
                        "fecha_pago": r.fecha_pago.isoformat(),
                        "vendedor_email": str(r.vendedor_email or ""),
                        "recibido": "TRUE" if r.recibido else "FALSE",
                        "numero_recibido": str(r.numero_recibido or ""),
                        "fecha_recibido": (
                            r.fecha_recibido.isoformat()[:19] if r.fecha_recibido else ""
                        ),
                        "recibido_por": str(r.recibido_por or ""),
                        "numero_odoo": str(r.numero_odoo or ""),
                        "referencia": str(r.referencia or ""),
                    }
                )
            return out
        return list(self._pagos_full)

    def all_pagos_proveedores_odoo(self) -> list[dict[str, Any]]:
        return list(self._pagos_prov)

    def all_compras_gastos_odoo(self) -> list[dict[str, Any]]:
        return list(self._compras_odoo)


def _setup_datos_base(eng: Any) -> _RepoHibridoSQL:
    repo = _RepoHibridoSQL(eng)
    cli = Cliente(
        cliente_id="1001",
        nombre="Lubricantes del Centro C.A.",
        vendedor_email="vend1@lubrikca.com",
    )
    repo.upsert_clientes([cli])
    repo.add_metodo_pago(
        MetodoPago("11", "Efectivo USD", Moneda.USD, TipoTasa.BINANCE, es_contado=True)
    )
    repo.add_metodo_pago(
        MetodoPago("12", "Transferencia Banesco", Moneda.VES, TipoTasa.BCV, es_contado=True)
    )
    repo.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 11, 15, 9, 0),
            tasa_bcv=Decimal("50.00"),
            tasa_binance=Decimal("55.00"),
            tasa_bcv_euro=Decimal("54.00"),
            fuente="api",
            es_heredada=False,
            capturada_ok=True,
        )
    )
    ord_odoo = OrdenVenta(
        so_id="S00501",
        cliente_id="1001",
        fecha=date(2026, 11, 15),
        fecha_entrega=date(2026, 11, 15),
        monto_total=Decimal("1000.00"),
        lista_precios="1",
        vendedor_email="vend1@lubrikca.com",
        es_primera_compra=False,
        estado_orden="sale",
        facturada=True,
        odoo_order_id=9501,
    )
    ord_cxc = OrdenVenta(
        so_id="S00502",
        cliente_id="1001",
        fecha=date(2026, 11, 16),
        fecha_entrega=date(2026, 11, 16),
        monto_total=Decimal("400.00"),
        lista_precios="1",
        vendedor_email="vend1@lubrikca.com",
        es_primera_compra=False,
        estado_orden="sale",
        facturada=False,
        odoo_order_id=9502,
    )
    repo.upsert_ordenes([ord_odoo, ord_cxc])

    with eng.begin() as conn:
        conn.execute(
            insert(schema.clientes).values(
                cliente_id="1001",
                nombre="Lubricantes del Centro C.A.",
                vendedor_email="vend1@lubrikca.com",
            )
        )
        conn.execute(
            insert(schema.ordenes_venta).values(
                [
                    {
                        "so_id": "S00501",
                        "odoo_order_id": 9501,
                        "cliente_id": "1001",
                        "fecha": date(2026, 11, 15),
                        "fecha_entrega": date(2026, 11, 15),
                        "monto_total": Decimal("1000.00"),
                        "lista_precios": "1",
                        "vendedor_email": "vend1@lubrikca.com",
                        "es_primera_compra": False,
                        "facturada": True,
                        "estado_orden": "sale",
                    },
                    {
                        "so_id": "S00502",
                        "odoo_order_id": 9502,
                        "cliente_id": "1001",
                        "fecha": date(2026, 11, 16),
                        "fecha_entrega": date(2026, 11, 16),
                        "monto_total": Decimal("400.00"),
                        "lista_precios": "1",
                        "vendedor_email": "vend1@lubrikca.com",
                        "es_primera_compra": False,
                        "facturada": False,
                        "estado_orden": "sale",
                    },
                ]
            )
        )
        conn.execute(
            insert(schema.documentos_comerciales).values(
                numero="PED-2026-00050",
                token_preview="tok-ped-50",
                tipo="pedido",
                estado=ENVIADO_ODOO,
                origen="bot",
                partner_odoo_id=1001,
                cliente_nombre="Lubricantes del Centro C.A.",
                lista_precios_id=1,
                lista_precios_nombre="Pago VES Nov 2026",
                moneda_lista="USD",
                vendedor_email="vend1@lubrikca.com",
                es_primera_compra=False,
                auto_enviado=False,
                odoo_order_id=9502,
                odoo_order_name="S00502",
                creado_por="vend1@lubrikca.com",
                creado_en=datetime(2026, 11, 16, 10, 0),
                actualizado_en=datetime(2026, 11, 16, 10, 0),
            )
        )
    return repo


def _servicio_op_fixture(eng: Any, repo: _RepoHibridoSQL) -> ServicioOperativos:
    return ServicioOperativos(
        RepoOperativosSQL(eng),
        lambda: None,
        repo_cxc=repo,
    )


def test_map_orden_preserva_odoo_order_id() -> None:
    o = map_orden(
        {
            "id": 9182,
            "name": "S00999",
            "partner_id": [1001, "Cliente Demo"],
            "date_order": "2026-11-18 10:00:00",
            "amount_total": 250.0,
            "pricelist_id": [1, "VES Nov"],
            "state": "sale",
            "delivery_status": "full",
            "fecha_entrega": "2026-11-18",
            "invoice_status": "to invoice",
        }
    )
    assert o.odoo_order_id == 9182
    assert o.so_id == "S00999"


def test_trazabilidad_unificada_pagos_ventas_y_compras() -> None:
    eng = _sqlite_engine()
    repo = _setup_datos_base(eng)
    srv_op = _servicio_op_fixture(eng, repo)
    repo_ter = RepoCuentasTercerosSQL(eng)

    # 1 pago Odoo espejo + 1 pago CxC Bot enviado a Odoo + 1 gasto CxC + 1 factura prov Odoo
    with eng.begin() as conn:
        conn.execute(
            insert(schema.pagos).values(
                [
                    {
                        "pago_id": "7701",
                        "cliente_id": "1001",
                        "monto": Decimal("120.00"),
                        "moneda": Moneda.USD,
                        "metodo_pago": "11",
                        "fecha_pago": datetime(2026, 11, 18, 12, 0),
                        "vendedor_email": "vend1@lubrikca.com",
                        "recibido": False,
                        "numero_odoo": "PUSD/2026/0010",
                        "referencia": "",
                    }
                ]
            )
        )
    repo.upsert_pagos(
        [
            Pago(
                pago_id="7701",
                cliente_id="1001",
                monto=Decimal("120.00"),
                moneda=Moneda.USD,
                metodo_pago="11",
                fecha_pago=datetime(2026, 11, 18, 12, 0),
                vendedor_email="vend1@lubrikca.com",
                numero_odoo="PUSD/2026/0010",
            )
        ]
    )
    repo._compras_odoo = [
        {
            "move_id": "4401",
            "numero_odoo": "BILL/2026/0008",
            "move_type": "in_invoice",
            "tipo_cxc": "factura_proveedor",
            "partner_odoo_id": 601,
            "proveedor_nombre": "Proveedor Alfa",
            "rif": "J-30111222-3",
            "monto_total": 350.0,
            "moneda": "USD",
            "fecha_documento": "2026-11-18",
            "estado_odoo": "posted",
        }
    ]
    with eng.begin() as conn:
        conn.execute(
            insert(schema.documentos_operativos).values(
                numero="PAG-00010",
                token_preview="tok-op-10",
                tipo="pago",
                estado=ENVIADO_ODOO,
                origen="bot",
                partner_odoo_id=1001,
                contraparte_nombre="Lubricantes del Centro C.A.",
                monto=Decimal("80.00"),
                moneda="USD",
                metodo_pago="Efectivo USD",
                fecha_documento=date(2026, 11, 18),
                odoo_model="account.payment",
                odoo_res_id=7702,
                odoo_ref="PUSD/2026/0011",
                es_zelle_tercero=False,
                confirmado_cxc=False,
                sin_factura=False,
                creado_por="vend1@lubrikca.com",
                creado_en=datetime(2026, 11, 18, 13, 0),
                actualizado_en=datetime(2026, 11, 18, 13, 0),
            )
        )
        conn.execute(
            insert(schema.documentos_operativos).values(
                numero="GAS-00005",
                token_preview="tok-op-gas",
                tipo="gasto",
                estado=ENVIADO_ODOO,
                origen="web",
                contraparte_nombre="Estación Servicio",
                monto=Decimal("30.00"),
                moneda="USD",
                fecha_documento=date(2026, 11, 18),
                odoo_model="account.move",
                odoo_res_id=4402,
                odoo_ref="MISC/2026/0003",
                es_zelle_tercero=False,
                confirmado_cxc=False,
                sin_factura=True,
                creado_por="admin@lubrikca.com",
                creado_en=datetime(2026, 11, 18, 14, 0),
                actualizado_en=datetime(2026, 11, 18, 14, 0),
            )
        )

    app.dependency_overrides[get_servicio_operativos] = lambda: srv_op
    app.dependency_overrides[get_repo_terceros] = lambda: repo_ter
    try:
        with (
            patch("cxc.web.app.get_current_user_from_cookie", return_value=ADMIN_USER),
            patch("cxc.web.app.get_repo", return_value=repo),
        ):
            r_pagos = client.get("/api/pagos/unificados?direccion=recibidos")
            assert r_pagos.status_code == 200, r_pagos.text
            items_p = r_pagos.json()["items"]
            p_odoo = next(x for x in items_p if x.get("odoo_id") == 7701)
            assert p_odoo["origen_sistema"] == "odoo"
            assert p_odoo["numero_cxc"] is None
            assert p_odoo["odoo_numero"] == "PUSD/2026/0010"

            p_cxc = next(x for x in items_p if x.get("odoo_id") == 7702)
            assert p_cxc["origen_sistema"] == "cxc"
            assert p_cxc["origen"] == "bot"
            assert p_cxc["numero_cxc"] == "PAG-00010"
            assert p_cxc["odoo_numero"] == "PUSD/2026/0011"

            r_comp = client.get("/api/compras/unificadas")
            assert r_comp.status_code == 200, r_comp.text
            items_c = r_comp.json()["items"]
            g_cxc = next(x for x in items_c if x.get("numero_cxc") == "GAS-00005")
            assert g_cxc["odoo_id"] == 4402
            assert g_cxc["odoo_numero"] == "MISC/2026/0003"
            b_odoo = next(x for x in items_c if x.get("odoo_id") == 4401)
            assert b_odoo["origen_sistema"] == "odoo"
            assert b_odoo["numero_cxc"] is None
            assert b_odoo["odoo_numero"] == "BILL/2026/0008"

            r_v = client.get("/api/ventas/unificadas")
            assert r_v.status_code == 200, r_v.text
            items_v = r_v.json()["items"]
            v_od = next(x for x in items_v if x.get("odoo_numero") == "S00501")
            assert v_od["origen_sistema"] == "odoo"
            assert v_od["numero_cxc"] is None
            assert v_od["odoo_id"] == 9501
            assert v_od["puede_ajustar_descuentos"] is True

            v_cxc = next(x for x in items_v if x.get("odoo_numero") == "S00502")
            assert v_cxc["origen_sistema"] == "cxc"
            assert v_cxc["numero_cxc"] == "PED-2026-00050"
            assert v_cxc["odoo_id"] == 9502
            assert v_cxc["puede_ajustar_descuentos"] is False
    finally:
        app.dependency_overrides.clear()


def test_lotes_efectivo_por_vendedor_solo_efectivo_y_marcar_recibido() -> None:
    assert es_pago_efectivo("Efectivo USD") is True
    assert es_pago_efectivo("Transferencia Banesco") is False

    eng = _sqlite_engine()
    repo = _setup_datos_base(eng)
    srv_op = _servicio_op_fixture(eng, repo)
    repo_ter = RepoCuentasTercerosSQL(eng)

    with eng.begin() as conn:
        conn.execute(
            insert(schema.pagos).values(
                [
                    {
                        "pago_id": "8101",
                        "cliente_id": "1001",
                        "monto": Decimal("150.00"),
                        "moneda": Moneda.USD,
                        "metodo_pago": "11",  # Efectivo USD
                        "fecha_pago": datetime(2026, 11, 18, 9, 0),
                        "vendedor_email": "vend1@lubrikca.com",
                        "recibido": False,
                        "numero_odoo": "PUSD/2026/0081",
                        "referencia": "",
                    },
                    {
                        "pago_id": "8102",
                        "cliente_id": "1001",
                        "monto": Decimal("2500.00"),
                        "moneda": Moneda.VES,
                        "metodo_pago": "12",  # Transferencia Banesco (electrónico)
                        "fecha_pago": datetime(2026, 11, 18, 9, 30),
                        "vendedor_email": "vend1@lubrikca.com",
                        "recibido": False,
                        "numero_odoo": "PVES/2026/0082",
                        "referencia": "REF8822",
                    },
                ]
            )
        )
        conn.execute(
            insert(schema.documentos_operativos).values(
                numero="PAG-00020",
                token_preview="tok-cash-20",
                tipo="pago",
                estado=PENDIENTE_APROBACION,
                origen="bot",
                partner_odoo_id=1001,
                contraparte_nombre="Lubricantes del Centro C.A.",
                monto=Decimal("100.00"),
                moneda="USD",
                metodo_pago="Efectivo USD",
                referencia="",
                fecha_documento=date(2026, 11, 18),
                es_zelle_tercero=False,
                confirmado_cxc=False,
                sin_factura=False,
                creado_por="vend1@lubrikca.com",
                creado_en=datetime(2026, 11, 18, 10, 0),
                actualizado_en=datetime(2026, 11, 18, 10, 0),
            )
        )
        conn.execute(
            insert(schema.documentos_operativos).values(
                numero="PAG-00021",
                token_preview="tok-elec-21",
                tipo="pago",
                estado=PENDIENTE_APROBACION,
                origen="bot",
                partner_odoo_id=1001,
                contraparte_nombre="Lubricantes del Centro C.A.",
                monto=Decimal("5000.00"),
                moneda="VES",
                metodo_pago="Transferencia",
                referencia="998877",
                fecha_documento=date(2026, 11, 18),
                es_zelle_tercero=False,
                confirmado_cxc=False,
                sin_factura=False,
                creado_por="vend1@lubrikca.com",
                creado_en=datetime(2026, 11, 18, 10, 30),
                actualizado_en=datetime(2026, 11, 18, 10, 30),
            )
        )
    repo.upsert_pagos(
        [
            Pago(
                pago_id="8101",
                cliente_id="1001",
                monto=Decimal("150.00"),
                moneda=Moneda.USD,
                metodo_pago="11",
                fecha_pago=datetime(2026, 11, 18, 9, 0),
                vendedor_email="vend1@lubrikca.com",
            ),
            Pago(
                pago_id="8102",
                cliente_id="1001",
                monto=Decimal("2500.00"),
                moneda=Moneda.VES,
                metodo_pago="12",
                fecha_pago=datetime(2026, 11, 18, 9, 30),
                vendedor_email="vend1@lubrikca.com",
            ),
        ]
    )

    app.dependency_overrides[get_servicio_operativos] = lambda: srv_op
    app.dependency_overrides[get_repo_terceros] = lambda: repo_ter
    try:
        with (
            patch("cxc.web.app.get_current_user_from_cookie", return_value=TESO_USER),
            patch("cxc.web.app.get_repo", return_value=repo),
        ):
            r_pend = client.get("/api/pagos/efectivo/lotes-pendientes")
            assert r_pend.status_code == 200, r_pend.text
            pend = r_pend.json()
            assert pend["total_vendedores"] == 1
            assert pend["total_pagos"] == 2  # 8101 + PAG-00020 (electrónicos fuera)
            g = pend["grupos"][0]
            assert g["vendedor_email"] == "vend1@lubrikca.com"
            assert g["vendedor_nombre"] == "Carlos Vendedor"
            assert g["total_usd"] == 250.0

            # Rechazar pago electrónico en lote de efectivo (422)
            r_bad = client.post(
                "/api/pagos/efectivo/lotes",
                json={"vendedor_email": "vend1@lubrikca.com", "pago_ids": ["8102"]},
            )
            assert r_bad.status_code == 422

            # Rechazar pago electrónico en /api/cobranza/marcar-recibido (422)
            r_bad_cob = client.post(
                "/api/cobranza/marcar-recibido",
                json={"pago_ids": ["8102"], "solo_efectivo": True},
            )
            assert r_bad_cob.status_code == 422

            # Emitir recibo por lote con efectivo mixto (Odoo mirror + CxC doc)
            doc_cash = next(x for x in g["pagos"] if x.get("numero_cxc") == "PAG-00020")[
                "documento_id"
            ]
            r_ok = client.post(
                "/api/pagos/efectivo/lotes",
                json={
                    "vendedor_email": "vend1@lubrikca.com",
                    "pago_ids": ["8101"],
                    "documento_ids": [doc_cash],
                    "observaciones": "Sobre entregado al cierre",
                    "fecha_lote": "2026-11-18",
                },
            )
            assert r_ok.status_code == 201, r_ok.text
            lote = r_ok.json()["lote"]
            assert lote["numero_recibo"].startswith("REC-LOTE-20261118-001")
            assert lote["cantidad_pagos"] == 2
            assert lote["total_usd"] == 250.0
            assert lote["recibido_por"] == "María Caja"

            # Pendientes quedan vacíos y el lote aparece listado + imprimible
            r_pend2 = client.get("/api/pagos/efectivo/lotes-pendientes")
            assert r_pend2.json()["total_pagos"] == 0

            r_list = client.get("/api/pagos/efectivo/lotes")
            assert r_list.status_code == 200
            assert len(r_list.json()["items"]) == 1

            r_html = client.get(f"/recibo-lote/{lote['lote_id']}")
            assert r_html.status_code == 200
            assert "ORIGINAL" in r_html.text
            assert "COPIA" in r_html.text
            assert "PAG-00020" in r_html.text
            assert "PUSD/2026/0081" in r_html.text
    finally:
        app.dependency_overrides.clear()


def test_conciliacion_bancaria_confirma_pagos_electronicos_y_revierte() -> None:
    eng = _sqlite_engine()
    repo = _setup_datos_base(eng)
    srv_op = _servicio_op_fixture(eng, repo)
    repo_ter = RepoCuentasTercerosSQL(eng)

    with eng.begin() as conn:
        conn.execute(
            insert(schema.documentos_operativos).values(
                documento_id=501,
                numero="PAG-00501",
                token_preview="tok-501",
                tipo="pago",
                estado=ENVIADO_ODOO,
                origen="bot",
                partner_odoo_id=1001,
                contraparte_nombre="Lubricantes del Centro C.A.",
                monto=Decimal("5000.00"),
                moneda="VES",
                metodo_pago="Transferencia Banesco",
                referencia="BAN123456",
                fecha_documento=date(2026, 11, 15),
                odoo_model="account.payment",
                odoo_res_id=8801,
                odoo_ref="PVES/2026/08801",
                es_zelle_tercero=False,
                confirmado_cxc=False,
                sin_factura=False,
                conciliado_banco_huella="",
                creado_por="vend1@lubrikca.com",
                creado_en=datetime(2026, 11, 15, 10, 0),
                actualizado_en=datetime(2026, 11, 15, 10, 0),
            )
        )
        conn.execute(
            insert(schema.bancos_archivos).values(
                archivo_id=1,
                sha256="a" * 64,
                banco="banesco",
                cuenta="0134",
                moneda="VES",
                nombre_archivo="banesco_nov.xlsx",
                contenido=b"xlsx",
                cargado_en=datetime(2026, 11, 15, 16, 0),
                cargado_por="tesoreria@lubrikca.com",
                n_movimientos=1,
                n_nuevos=1,
                cuadra=True,
            )
        )
        conn.execute(
            insert(schema.bancos_movimientos).values(
                huella="HUELLA_ELEC_8801",
                unique_import_id="u-8801",
                banco="banesco",
                cuenta="0134",
                moneda="VES",
                fecha=date(2026, 11, 15),
                referencia="BAN123456",
                descripcion="TRF RECIBIDA LUBRICANTES CENTRO",
                debito=Decimal("0"),
                credito=Decimal("5000.00"),
                orden=1,
                categoria="transferencia",
                regla="R1",
                archivo_id=1,
                creado_en=datetime(2026, 11, 15, 16, 0),
            )
        )
        conn.execute(
            insert(schema.bancos_propuestas).values(
                huella="HUELLA_ELEC_8801",
                tipo="pago",
                regla="R1",
                motivo="Ref exacta BAN123456",
                pago_id=8801,
                pago_nombre="PVES/2026/08801",
                cuenta="",
                puntaje=100,
                candidatos='[{"id": 8801, "nombre": "PVES/2026/08801"}]',
                generada_en=datetime(2026, 11, 15, 16, 0),
                estado="pendiente",
            )
        )

    app.dependency_overrides[get_servicio_operativos] = lambda: srv_op
    app.dependency_overrides[get_repo_terceros] = lambda: repo_ter
    try:
        with (
            patch("cxc.web.app.get_current_user_from_cookie", return_value=TESO_USER),
            patch("cxc.web.app.get_repo", return_value=repo),
        ):
            # Antes de conciliar -> pendiente de conciliación bancaria
            r_before = client.get("/api/pagos/unificados?direccion=recibidos").json()["items"]
            item_before = next(x for x in r_before if x["numero_cxc"] == "PAG-00501")
            assert item_before["conciliado_banco"] is False
            assert item_before["estado_confirmacion_pago"] == "PENDIENTE_CONCILIACION"

            # Aprobar propuesta bancaria -> confirma automáticamente el pago electrónico
            bancos_repo.decidir(eng, "HUELLA_ELEC_8801", "aprobar", "tesoreria@lubrikca.com")

            r_after = client.get("/api/pagos/unificados?direccion=recibidos").json()["items"]
            item_after = next(x for x in r_after if x["numero_cxc"] == "PAG-00501")
            assert item_after["conciliado_banco"] is True
            assert item_after["confirmado_cxc"] is True
            assert item_after["estado_confirmacion_pago"] == "CONCILIADO_BANCO"
            assert item_after["conciliado_banco_info"]["banco"] == "banesco"

            # Impacto en saldos propios usando BD
            docs_op, _, _ = srv_op.listar(tipo="pago", limit=50)
            rep_saldos = calcular_saldos_propios(
                repo, docs_operativos=docs_op, hoy=date(2026, 11, 20)
            )
            cli_s = next(c for c in rep_saldos["clientes"] if c["cliente_id"] == "1001")
            assert cli_s["pagos_otros_cxc_usd"] == 100.0  # 5000 VES / 50.00 = $100

            # Reabrir conciliación -> revierte confirmación electrónica
            bancos_repo.decidir(
                eng,
                "HUELLA_ELEC_8801",
                "reabrir",
                "tesoreria@lubrikca.com",
                comentario="Revisión",
            )
            r_rev = client.get("/api/pagos/unificados?direccion=recibidos").json()["items"]
            item_rev = next(x for x in r_rev if x["numero_cxc"] == "PAG-00501")
            assert item_rev["conciliado_banco"] is False
            assert item_rev["confirmado_cxc"] is False
            assert item_rev["estado_confirmacion_pago"] == "PENDIENTE_CONCILIACION"

            # Vinculación manual R5 vía /api/compras/conciliacion-gastos/vincular también confirma
            doc_pend = srv_op.crear(
                {
                    "tipo": "pago",
                    "partner_odoo_id": 1001,
                    "contraparte_nombre": "Lubricantes del Centro C.A.",
                    "monto": 2500,
                    "moneda": "VES",
                    "metodo_pago": "Pago Móvil",
                    "referencia": "PM9988",
                    "fecha_documento": "2026-11-16",
                },
                actor="vend1@lubrikca.com",
            )
            r_vinc = client.post(
                "/api/compras/conciliacion-gastos/vincular",
                json={"documento_id": doc_pend.documento_id, "huella": "HUELLA_ELEC_8801"},
            )
            assert r_vinc.status_code == 200
            assert r_vinc.json()["documento"]["confirmado_cxc"] is True
            assert r_vinc.json()["documento"]["estado"] == CONFIRMADO_CXC
    finally:
        app.dependency_overrides.clear()


def test_ajustes_descuentos_odoo_individual_especial_y_toggle_limpieza() -> None:
    eng = _sqlite_engine()
    repo = _setup_datos_base(eng)
    bf = BandejaFacturacion(
        so_id="S00501",
        lista_aplicada="1",
        precio_base_calculado=Decimal("1000.00"),
        descuentos_detalle=[
            DescuentoAplicado(
                origen="Pronto Pago",
                descripcion="8% pronto pago contado",
                monto=Decimal("80.00"),
                regla_id="CONT_8",
                porcentaje=Decimal("0.08"),
                base=Decimal("1000.00"),
            ),
            DescuentoAplicado(
                origen="Volumen",
                descripcion="5% volumen caja",
                monto=Decimal("50.00"),
                regla_id="VOL_5",
                porcentaje=Decimal("0.05"),
                base=Decimal("1000.00"),
            ),
        ],
        total_descuentos=Decimal("130.00"),
        estado=EstadoBandeja.FACTURADO,
    )
    repo.upsert_bandeja(bf)

    with (
        patch("cxc.web.app.get_current_user_from_cookie", return_value=ADMIN_USER),
        patch("cxc.web.app.get_repo", return_value=repo),
    ):
        # CxC-born order cannot use Odoo cleanup discount adjustments (422)
        r_cxc_err = client.post(
            "/api/ordenes/S00502/ajustes-descuentos/excluir-motor",
            json={"descuento_origen": "Pronto Pago", "regla_id": "CONT_8", "motivo": "test"},
        )
        assert r_cxc_err.status_code == 422

        # Initial breakdown on S00501
        r0 = client.get("/api/ordenes/S00501/ajustes-descuentos")
        assert r0.status_code == 200, r0.text
        d0 = r0.json()
        assert d0["puede_editar"] is True
        assert d0["resumen"]["total_motor_original"] == 130.0
        assert d0["resumen"]["total_descuento_ajustado"] == 130.0

        # Exclude ONLY Pronto Pago (keeps Volumen $50!)
        r1 = client.post(
            "/api/ordenes/S00501/ajustes-descuentos/excluir-motor",
            json={
                "descuento_origen": "Pronto Pago",
                "regla_id": "CONT_8",
                "motivo": "Cliente pagó tarde, no aplica pronto pago",
            },
        )
        assert r1.status_code == 201, r1.text
        d1 = r1.json()
        assert d1["resumen"]["total_excluido"] == 80.0
        assert d1["resumen"]["total_motor_vigente"] == 50.0
        assert d1["resumen"]["total_descuento_ajustado"] == 50.0
        excl_id = d1["ajuste"]["ajuste_id"]

        # Add percentage special (10% of $1000 = $100) + fixed USD ($25)
        r2 = client.post(
            "/api/ordenes/S00501/ajustes-descuentos/especial",
            json={
                "tipo_valor": "porcentaje",
                "porcentaje": 10.0,
                "descripcion": "Acuerdo gerencial 10%",
                "motivo": "Decisión comercial Q4",
            },
        )
        assert r2.status_code == 201, r2.text
        r3 = client.post(
            "/api/ordenes/S00501/ajustes-descuentos/especial",
            json={
                "tipo_valor": "monto_fijo",
                "monto_usd": 25.0,
                "descripcion": "Bonificación transporte",
                "motivo": "Acuerdo especial",
            },
        )
        assert r3.status_code == 201, r3.text
        d3 = r3.json()
        assert d3["resumen"]["total_motor_vigente"] == 50.0
        assert d3["resumen"]["total_especial"] == 125.0
        assert d3["resumen"]["total_descuento_ajustado"] == 175.0

        # Saldos Propios reflects the exact adjusted total ($175.00 instead of $130.00)
        rep = calcular_saldos_propios(repo, hoy=date(2026, 11, 20))
        cli_s = next(c for c in rep["clientes"] if c["cliente_id"] == "1001")
        assert cli_s["descuentos_confirmados_usd"] == 175.0

        # Restore excluded Pronto Pago rule -> back to 130 motor + 125 special = 255
        r4 = client.delete(f"/api/ordenes/S00501/ajustes-descuentos/{excl_id}")
        assert r4.status_code == 200
        assert r4.json()["resumen"]["total_descuento_ajustado"] == 255.0

        # Close cleanup globally -> all mutating endpoints return 403
        r_off = client.post("/api/config/limpieza-descuentos-odoo", json={"activa": False})
        assert r_off.status_code == 200
        assert r_off.json()["activa"] is False

        r_chk = client.get("/api/ordenes/S00501/ajustes-descuentos")
        assert r_chk.json()["puede_editar"] is False

        r_frozen = client.post(
            "/api/ordenes/S00501/ajustes-descuentos/especial",
            json={"tipo_valor": "monto_fijo", "monto_usd": 10},
        )
        assert r_frozen.status_code == 403

        # Clean toggle back to True
        r_on = client.post("/api/config/limpieza-descuentos-odoo", json={"activa": True})
        assert r_on.status_code == 200


def test_postgres_real_alembic_y_orden_id_si_hay_database_url() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("Sin DATABASE_URL para probar Postgres real.")
    from sqlalchemy import delete

    from cxc.db.engine import make_engine
    from cxc.db.postgres_repository import PostgresRepository

    eng = make_engine(url)
    schema.metadata.create_all(
        eng,
        tables=[
            schema.clientes,
            schema.ordenes_venta,
            schema.lotes_recibo_efectivo,
            schema.orden_descuentos_ajustes,
        ],
        checkfirst=True,
    )
    repo_pg = PostgresRepository(eng)
    try:
        repo_pg.upsert_clientes(
            [Cliente(cliente_id="999901", nombre="Cliente PG Trace", vendedor_email="")]
        )
        repo_pg.upsert_ordenes(
            [
                OrdenVenta(
                    so_id="S-PG-9901",
                    cliente_id="999901",
                    fecha=date(2026, 11, 19),
                    fecha_entrega=None,
                    monto_total=Decimal("100.00"),
                    lista_precios="1",
                    vendedor_email="",
                    es_primera_compra=False,
                    odoo_order_id=99901,
                )
            ]
        )
        leido = repo_pg.get_orden("S-PG-9901")
        assert leido is not None
        assert leido.odoo_order_id == 99901
    finally:
        with eng.begin() as conn:
            conn.execute(
                delete(schema.ordenes_venta).where(schema.ordenes_venta.c.so_id == "S-PG-9901")
            )
            conn.execute(delete(schema.clientes).where(schema.clientes.c.cliente_id == "999901"))
