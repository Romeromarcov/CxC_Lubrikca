"""Pruebas Fase 5B: Refinamientos de Inicio, Reportes CxC/CxP, Pagos, Ventas y Conciliación."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from cxc.bancos.conciliacion import (
    EgresoCxCCandidato,
    IngresoCxCCandidato,
    egresos_desde_operativos,
    ingresos_desde_operativos,
    proponer,
)
from cxc.bancos.ingesta import MovimientoClasificado
from cxc.bancos.modelos import MovimientoBancario
from cxc.documentos.modelos import COTIZACION, PENDIENTE_APROBACION
from cxc.documentos.servicio import ServicioDocumentos
from cxc.documentos.sync_odoo import orden_confirmada_en_odoo, sincronizar_documentos_desde_odoo
from cxc.engine.saldos_cxp import calcular_saldos_cxp, estado_cuenta_proveedor, exportar_cxp_csv
from cxc.engine.saldos_propios import calcular_saldos_propios
from cxc.models import Cliente, SerieTasa
from cxc.operativos.modelos import (
    CONFIRMADO_CXC,
    TIPO_GASTO,
    TIPO_PAGO,
    TIPO_PAGO_PROVEEDOR,
    DocumentoOperativo,
)
from cxc.operativos.modelos import (
    ENVIADO_ODOO as OP_ENVIADO_ODOO,
)
from cxc.operativos.servicio import ServicioOperativos
from cxc.operativos.terceros import CuentaTercero
from cxc.permisos import efectivos
from cxc.repositories import InMemoryRepository
from cxc.web.app import _get_tasas_promedios_sync, app
from cxc.web.documentos_api import get_servicio_documentos
from cxc.web.operativos_api import get_repo_terceros, get_servicio_operativos
from cxc.web.reportes_propios_api import get_cuentas_terceros_reportes_bd, get_extras_reportes_bd
from tests import builders as b
from tests.capturas_dobles import OdooFalso
from tests.test_compras_fase4 import RepoOpMem4
from tests.test_documentos_comerciales import (
    CATALOGO_PRUEBA,
    CatalogoMemoria,
    RepoDocumentosMemoria,
    _armar_repo_motor_prueba,
)

client = TestClient(app)

HOY = date(2026, 11, 19)
VENTAS_USER = {
    "email": "v1@lubrikca.com",
    "nombre": "Vendedor Uno",
    "rol": "ventas",
    "permisos": efectivos("ventas"),
}
ADMIN_USER = {
    "email": "admin@lubrikca.com",
    "nombre": "Admin Principal",
    "rol": "admin",
    "permisos": efectivos("admin"),
}
SIN_PERM_USER = {
    "email": "lector@lubrikca.com",
    "nombre": "Solo Auditor",
    "rol": "auditor",
    "permisos": {},
}


class _RepoFase5B(InMemoryRepository):
    """Repositorio en memoria extendido con espejo CxP, usuarios y listas."""

    def __init__(self) -> None:
        super().__init__()
        self._compras_gastos_list: list[dict[str, Any]] = []
        self._pagos_prov_list: list[dict[str, Any]] = []
        self._no_otorgados: dict[str, dict[str, str]] = {}
        self.upsert_usuario_plataforma(
            {
                "user_id": "u1",
                "email": "v1@lubrikca.com",
                "nombre": "Carlos Vendedor",
                "rol": "ventas",
            }
        )
        self.upsert_usuario_plataforma(
            {
                "user_id": "u2",
                "email": "admin@lubrikca.com",
                "nombre": "Ana Administradora",
                "rol": "admin",
            }
        )

    def all_compras_gastos_odoo(self) -> list[dict[str, Any]]:
        return list(self._compras_gastos_list)

    def all_pagos_proveedores_odoo(self) -> list[dict[str, Any]]:
        return list(self._pagos_prov_list)

    def all_descuentos_no_otorgados(self) -> dict[str, dict[str, str]]:
        return dict(self._no_otorgados)


class _RepoTercerosMem:
    def __init__(self, cuentas: list[CuentaTercero] | None = None) -> None:
        self._cuentas = list(cuentas or [])

    def listar(self, *, solo_activas: bool = True) -> list[CuentaTercero]:
        if solo_activas:
            return [c for c in self._cuentas if c.activo]
        return list(self._cuentas)

    def obtener(self, cuenta_id: int) -> CuentaTercero | None:
        return next((c for c in self._cuentas if c.cuenta_id == cuenta_id), None)


def _doc_op(
    doc_id: int,
    *,
    numero: str,
    tipo: str,
    estado: str,
    partner_odoo_id: int | None = None,
    contraparte_nombre: str = "Contraparte",
    rif: str = "J-111222333",
    monto: str = "100.00",
    moneda: str = "USD",
    metodo_pago: str = "transferencia",
    es_efectivo: bool = False,
    es_zelle_tercero: bool = False,
    cuenta_tercero_id: int | None = None,
    confirmado_cxc: bool = False,
    confirmado_cxc_por: str = "",
    aprobado_por: str = "",
    creado_por: str = "v1@lubrikca.com",
    referencia: str = "",
    odoo_model: str = "",
    odoo_res_id: int | None = None,
    fecha_documento: date = date(2026, 11, 15),
) -> DocumentoOperativo:
    return DocumentoOperativo(
        documento_id=doc_id,
        numero=numero,
        token_preview=f"tok-{doc_id}",
        tipo=tipo,
        estado=estado,
        origen="web",
        captura_id=None,
        partner_odoo_id=partner_odoo_id,
        contraparte_nombre=contraparte_nombre,
        rif=rif,
        monto=Decimal(monto),
        base_imponible=Decimal(monto),
        iva=Decimal("0"),
        moneda=moneda,
        metodo_pago="Efectivo USD" if es_efectivo else metodo_pago,
        banco="Bancamiga" if not es_efectivo else "",
        diario_id=10,
        diario_nombre="Efectivo" if es_efectivo else "Bancamiga",
        cuenta_contable_id=None,
        cuenta_contable_codigo="",
        cuenta_contable_nombre="",
        referencia=referencia,
        numero_documento=f"DOC-{doc_id}",
        numero_control="",
        pedido_ref="",
        factura_ref="",
        orden_ref="",
        categoria="",
        concepto=contraparte_nombre,
        sin_factura=(tipo == TIPO_GASTO),
        fecha_documento=fecha_documento,
        fecha_vencimiento=fecha_documento,
        proveedor_telefono="",
        proveedor_email="",
        proveedor_direccion="",
        notas="",
        validacion={},
        faltantes=[],
        aprobado_por=aprobado_por,
        aprobado_en=datetime(2026, 11, 15, 12, 0) if aprobado_por else None,
        rechazo_motivo="",
        odoo_model=odoo_model,
        odoo_res_id=odoo_res_id,
        odoo_ref=f"{odoo_model}:{odoo_res_id}" if odoo_res_id else "",
        error_odoo="",
        creado_por=creado_por,
        creado_en=datetime(2026, 11, 15, 10, 0),
        actualizado_en=datetime(2026, 11, 15, 12, 0),
        es_zelle_tercero=es_zelle_tercero,
        cuenta_tercero_id=cuenta_tercero_id,
        beneficiario_tercero="Tercero Test" if es_zelle_tercero else "",
        confirmado_cxc=confirmado_cxc,
        confirmado_cxc_por=confirmado_cxc_por,
        confirmado_cxc_en=datetime(2026, 11, 15, 11, 0) if confirmado_cxc else None,
        numero_recibo=f"REC-000{doc_id}" if confirmado_cxc else "",
        recibo_emitido_en=None,
        lote_caja_chica_id=None,
        cuenta_contrapartida_id=None,
        conciliado_banco_huella="",
    )


# ---------------------------------------------------------------------------
# 1. Tasas en Inicio + Markup estructural 5B
# ---------------------------------------------------------------------------
def test_tasas_promedios_incluye_euro_y_html_fase5b() -> None:
    repo = _RepoFase5B()
    repo.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 11, 19, 8, 30),
            tasa_bcv=Decimal("780.50"),
            tasa_bcv_euro=Decimal("842.15"),
            tasa_binance=Decimal("915.00"),
            fuente="test",
        )
    )
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = _get_tasas_promedios_sync()
    assert res["tasa_bcv_actual"] == 780.5
    assert res["tasa_bcv_euro"] == 842.15
    assert res["tasa_binance_vigente"] == 915.0
    assert res["ultima_actualizacion"].startswith("2026-11-19")

    html = Path("src/cxc/web/static/index.html").read_text(encoding="utf-8")
    assert 'id="inicio-tasas-card"' in html
    assert 'id="inicio-rate-bcv-euro"' in html
    assert 'data-rep-subtab="consolidado"' in html
    assert 'data-rep-subtab="cxc"' in html
    assert 'data-rep-subtab="cxp"' in html
    assert 'data-compras-subpage="conciliacion"' not in html
    assert 'id="bancos-sistema-cruce-card"' in html
    assert "abrirNuevoDocumento('pedido')" not in html
    assert 'id="doc-btn-nueva-cotizacion"' in html


# ---------------------------------------------------------------------------
# 2. Reportes CxC (clientes por vendedor) + Motor CxP y Endpoints CxP
# ---------------------------------------------------------------------------
def test_reporte_cxc_clientes_por_vendedor_y_motor_cxp() -> None:
    repo = _RepoFase5B()
    repo.upsert_clientes([Cliente(cliente_id="101", nombre="ALFA C.A.", vendedor_email="v1@x.com")])
    repo.upsert_ordenes(
        [b.orden("S00900", cliente_id="101", monto_total="450.00", vendedor="v1@x.com")]
    )

    rep_cxc = calcular_saldos_propios(repo, hoy=HOY)
    assert len(rep_cxc["vendedores"]) == 1
    assert len(rep_cxc["vendedores"][0]["clientes"]) == 1
    assert rep_cxc["vendedores"][0]["clientes"][0]["cliente_id"] == "101"

    # CxP: 2 facturas compra Odoo ($1000 + $200 vencida 40d), 1 NC Prov ($100),
    # 1 gasto sin factura entry ($75 — NO infla CxP), 1 pago prov Odoo ($300),
    # 1 pago prov CxC ($150), 1 pago Zelle 3ro confirmado al proveedor ($120)
    repo.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 11, 10, 9, 0),
            tasa_bcv=Decimal("50.00"),
            tasa_bcv_euro=Decimal("54.00"),
            tasa_binance=Decimal("60.00"),
            fuente="test",
        )
    )
    repo._compras_gastos_list = [
        {
            "move_id": 5001,
            "numero": "BILL/2026/001",
            "move_type": "in_invoice",
            "partner_odoo_id": 901,
            "proveedor_nombre": "DISTRIBUIDORA DELTA",
            "rif": "J-400010001",
            "fecha_documento": "2026-11-05",
            "fecha_vencimiento": "2026-11-18",
            "monto_total": 1000.0,
            "moneda": "USD",
            "estado_odoo": "posted",
            "asiento_contable": False,
        },
        {
            "move_id": 5002,
            "numero": "BILL/2026/002",
            "move_type": "in_invoice",
            "partner_odoo_id": 901,
            "proveedor_nombre": "DISTRIBUIDORA DELTA",
            "rif": "J-400010001",
            "fecha_documento": "2026-10-01",
            "fecha_vencimiento": "2026-10-10",
            "monto_total": 10000.0,  # 10,000 VES @ 50 = $200 USD
            "moneda": "VES",
            "estado_odoo": "posted",
            "asiento_contable": False,
        },
        {
            "move_id": 5003,
            "numero": "RBILL/2026/001",
            "move_type": "in_refund",
            "partner_odoo_id": 901,
            "proveedor_nombre": "DISTRIBUIDORA DELTA",
            "rif": "J-400010001",
            "fecha_documento": "2026-11-12",
            "monto_total": 100.0,
            "moneda": "USD",
            "estado_odoo": "posted",
            "asiento_contable": False,
        },
        {
            "move_id": 5004,
            "numero": "MISC/2026/001",
            "move_type": "entry",
            "partner_odoo_id": 901,
            "proveedor_nombre": "DISTRIBUIDORA DELTA",
            "rif": "J-400010001",
            "fecha_documento": "2026-11-14",
            "monto_total": 75.0,
            "moneda": "USD",
            "estado_odoo": "posted",
            "asiento_contable": True,
        },
    ]
    repo._pagos_prov_list = [
        {
            "pago_id": "701",
            "numero_odoo": "PBNK1/2026/00701",
            "partner_odoo_id": 901,
            "proveedor_nombre": "DISTRIBUIDORA DELTA",
            "rif": "J-400010001",
            "fecha_pago": "2026-11-13",
            "monto": 300.0,
            "moneda": "USD",
        }
    ]
    cuentas_3ro = [
        CuentaTercero(
            cuenta_id=7,
            alias="Zelle Delta",
            titular="Delta LLC",
            correo_telefono="zelle@delta.test",
            proveedor_odoo_id=901,
            proveedor_nombre="DISTRIBUIDORA DELTA",
            diario_odoo_id=None,
            diario_odoo_nombre="",
            cuenta_cxp_override_id=None,
            cuenta_cxc_override_id=None,
            moneda="USD",
            notas="",
            activo=True,
            creado_por="admin@lubrikca.com",
            creado_en=datetime(2026, 11, 10, 9, 0),
            actualizado_en=datetime(2026, 11, 10, 9, 0),
        )
    ]
    docs_op = [
        _doc_op(
            21,
            numero="PPR-00021",
            tipo=TIPO_PAGO_PROVEEDOR,
            estado=CONFIRMADO_CXC,
            partner_odoo_id=901,
            contraparte_nombre="DISTRIBUIDORA DELTA",
            monto="150.00",
            moneda="USD",
            confirmado_cxc=True,
        ),
        _doc_op(
            22,
            numero="PAG-00022",
            tipo=TIPO_PAGO,
            estado=CONFIRMADO_CXC,
            partner_odoo_id=101,
            contraparte_nombre="ALFA C.A.",
            monto="120.00",
            moneda="USD",
            es_zelle_tercero=True,
            cuenta_tercero_id=7,
            confirmado_cxc=True,
        ),
    ]

    rep_cxp = calcular_saldos_cxp(
        repo, docs_operativos=docs_op, cuentas_terceros=cuentas_3ro, hoy=HOY
    )
    assert rep_cxp["kpis"]["compras_confirmadas_usd"] == 1200.0
    assert rep_cxp["kpis"]["descuentos_confirmados_usd"] == 100.0
    assert rep_cxp["kpis"]["pagos_proveedor_usd"] == 450.0
    assert rep_cxp["kpis"]["pagos_zelle_tercero_usd"] == 120.0
    assert rep_cxp["kpis"]["pagos_confirmados_usd"] == 570.0
    # 1200 - 100 - 570 = 530.00 (entry $75 NO suma a deuda)
    assert rep_cxp["kpis"]["saldo_por_pagar_usd"] == 530.0
    assert rep_cxp["kpis"]["gastos_sin_factura_usd"] == 75.0

    ec = estado_cuenta_proveedor(
        repo, "901", docs_operativos=docs_op, cuentas_terceros=cuentas_3ro, hoy=HOY
    )
    assert ec is not None
    assert ec["resumen"]["saldo_por_pagar_usd"] == 530.0
    assert exportar_cxp_csv(rep_cxp).startswith(b"\xef\xbb\xbf")

    # Endpoints REST CxP
    usuario: dict[str, Any] | None = None
    app.dependency_overrides[get_extras_reportes_bd] = lambda: ((), docs_op)
    app.dependency_overrides[get_cuentas_terceros_reportes_bd] = lambda: cuentas_3ro
    try:
        with (
            patch("cxc.web.app.get_repo", return_value=repo),
            patch(
                "cxc.web.app.get_current_user_from_cookie",
                side_effect=lambda _c: dict(usuario) if usuario else None,
            ),
        ):
            assert client.get("/api/reportes/cxp-propios").status_code == 401
            with patch("cxc.web.app.hay_sesion_valida", return_value=False):
                assert client.get("/api/reportes/cxp-propios").status_code == 401

            usuario = dict(SIN_PERM_USER)
            assert (
                client.get("/api/reportes/cxp-propios", cookies={"cxc_session": "ok"}).status_code
                == 403
            )

            usuario = dict(VENTAS_USER)
            r = client.get("/api/reportes/cxp-propios", cookies={"cxc_session": "ok"})
            assert r.status_code == 200
            assert r.json()["kpis"]["saldo_por_pagar_usd"] == 530.0

            r_ec = client.get("/api/reportes/cxp/estado-cuenta/901", cookies={"cxc_session": "ok"})
            assert r_ec.status_code == 200
            assert r_ec.json()["resumen"]["saldo_por_pagar_usd"] == 530.0

            r_csv = client.get("/api/reportes/cxp-propios/csv", cookies={"cxc_session": "ok"})
            assert r_csv.status_code == 200
            assert b"DISTRIBUIDORA DELTA" in r_csv.content
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 3. Pagos Recibidos & Enviados Unificados: 3 tasas, enlace Odoo y nombres
# ---------------------------------------------------------------------------
def test_pagos_unificados_equivalentes_3_tasas_odoo_url_y_usuarios() -> None:
    repo = _RepoFase5B()
    repo.append_serie_tasa(
        SerieTasa(
            timestamp=datetime(2026, 11, 15, 10, 0),
            tasa_bcv=Decimal("50.00"),
            tasa_bcv_euro=Decimal("55.00"),
            tasa_binance=Decimal("60.00"),
            fuente="test",
        )
    )
    repo._pagos_prov_list = [
        {
            "pago_id": "8801",
            "numero_odoo": "PBNK/2026/08801",
            "partner_odoo_id": 901,
            "proveedor_nombre": "PROVEEDOR ALPHA",
            "rif": "J-999",
            "fecha_pago": "2026-11-15",
            "monto": 6000.0,
            "moneda": "VES",
            "diario_id": 7,
            "diario_nombre": "Bancamiga VES",
            "referencia": "TRF-8801",
        }
    ]
    repo_op = RepoOpMem4()
    repo_op.crear(
        {
            "tipo": TIPO_PAGO_PROVEEDOR,
            "token_preview": "t-ppr-1",
            "estado": OP_ENVIADO_ODOO,
            "partner_odoo_id": 901,
            "contraparte_nombre": "PROVEEDOR BETA",
            "monto": Decimal("3000.00"),
            "moneda": "VES",
            "fecha_documento": date(2026, 11, 15),
            "creado_por": "v1@lubrikca.com",
            "confirmado_cxc": True,
            "confirmado_cxc_por": "admin@lubrikca.com",
        }
    )
    # Adjuntar odoo_res_id al documento CxC
    repo_op.actualizar(
        1,
        {
            "aprobado_por": "admin@lubrikca.com",
            "odoo_model": "account.payment",
            "odoo_res_id": 8802,
            "odoo_ref": "account.payment:8802",
        },
    )
    repo_op.crear(
        {
            "tipo": TIPO_PAGO,
            "token_preview": "t-pag-1",
            "estado": CONFIRMADO_CXC,
            "partner_odoo_id": 101,
            "contraparte_nombre": "ALFA C.A.",
            "monto": Decimal("100.00"),
            "moneda": "USD",
            "fecha_documento": date(2026, 11, 15),
            "creado_por": "v1@lubrikca.com",
            "confirmado_cxc": True,
            "confirmado_cxc_por": "admin@lubrikca.com",
        }
    )

    svc = ServicioOperativos(
        repo_op,
        lambda: None,
        repo_cxc=repo,
        repo_terceros=_RepoTercerosMem(),
    )
    app.dependency_overrides[get_servicio_operativos] = lambda: svc
    app.dependency_overrides[get_repo_terceros] = lambda: _RepoTercerosMem()
    try:
        with (
            patch("cxc.web.app.get_repo", return_value=repo),
            patch("cxc.web.app.get_current_user_from_cookie", return_value=ADMIN_USER),
            patch("cxc.web.operativos_api._odoo_base_url", return_value="https://odoo.test"),
        ):
            r_env = client.get("/api/pagos/unificados?direccion=enviados")
            assert r_env.status_code == 200
            items_env = {x["contraparte_nombre"]: x for x in r_env.json()["items"]}

            od_it = items_env["PROVEEDOR ALPHA"]
            assert od_it["equivalentes"]["tasa_bcv"] == 50.0
            assert od_it["equivalentes"]["tasa_binance"] == 60.0
            assert od_it["equivalentes"]["tasa_bcv_eur"] == 55.0
            assert od_it["monto_bcv_usd"] == 120.0  # 6000 / 50
            assert od_it["monto_binance_usd"] == 100.0  # 6000 / 60
            assert od_it["monto_eur_usd"] == round(6000.0 / 55.0, 2)
            assert od_it["odoo_numero"] == "PBNK/2026/08801"
            assert od_it["odoo_pago_id"] == "8801"
            assert (
                od_it["odoo_url"]
                == "https://odoo.test/web#id=8801&model=account.payment&view_type=form"
            )

            cxc_it = items_env["PROVEEDOR BETA"]
            assert cxc_it["registrado_por_nombre"] == "Carlos Vendedor"
            assert cxc_it["aprobado_o_recibido_por_nombre"] == "Ana Administradora"
            assert cxc_it["monto_bcv_usd"] == 60.0
            assert cxc_it["monto_binance_usd"] == 50.0
            assert (
                cxc_it["odoo_url"]
                == "https://odoo.test/web#id=8802&model=account.payment&view_type=form"
            )

            r_rec = client.get("/api/pagos/unificados?direccion=recibidos&origen=cxc")
            assert r_rec.status_code == 200
            rec_it = r_rec.json()["items"][0]
            assert rec_it["registrado_por_nombre"] == "Carlos Vendedor"
            assert rec_it["aprobado_o_recibido_por_nombre"] == "Ana Administradora"
            assert rec_it["monto_bcv_usd"] == 100.0
            assert rec_it["monto_binance_usd"] == 100.0
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 4. Ventas: Sync Odoo -> CxC + Bloqueo al Confirmar + Subtotal / Total IVA
# ---------------------------------------------------------------------------
def test_ventas_sync_odoo_bloqueo_edicion_y_subtotal_con_iva() -> None:
    from cxc.odoo.escritura import EscritorOdoo

    repo_cxc = _armar_repo_motor_prueba()
    repo_docs = RepoDocumentosMemoria()
    odoo = OdooFalso(
        res_partner__read=[
            {"id": 88, "name": "Ronald Socorro", "vat": "V-13069226", "customer_rank": 1}
        ]
    )
    svc = ServicioDocumentos(
        repo_docs, CatalogoMemoria(CATALOGO_PRUEBA), lambda: EscritorOdoo(odoo), repo_cxc=repo_cxc
    )

    doc = svc.crear(
        {
            "tipo": COTIZACION,
            "partner_odoo_id": 88,
            "cliente_nombre": "Ronald Socorro",
            "lista_precios_id": "21",
            "lista_precios_nombre": "Pago VES Nov 2026",
            "lineas": [{"codigo": "0761", "cantidad": 2}],
        },
        actor="v1@lubrikca.com",
    )
    svc.registrar_envio_captura(
        doc.documento_id,
        partner_odoo_id=88,
        odoo_order_id=950,
        odoo_order_name="S00950",
    )

    # Simulamos que en Odoo se cambió cantidad de 2 -> 5 y lista de precios 21 -> 22, y sigue draft
    repo_cxc.upsert_ordenes(
        [
            b.orden(
                "S00950",
                cliente_id="88",
                fecha=date(2026, 11, 18),
                monto_total="139.20",
                estado_orden="draft",
                lista="22",
            )
        ]
    )
    repo_cxc.upsert_lineas(
        [
            b.linea(
                "501",
                so_id="S00950",
                producto="FORMULA 1 20W50",
                cantidad="5",
                precio="24.00",
                subtotal="120.00",
            )
        ]
    )

    res_sync1 = sincronizar_documentos_desde_odoo(
        repo_cxc, repo_docs, pricelist_mapeo={"22": "Especial Divisas"}
    )
    assert res_sync1["ajustados"] == 1
    doc_act = repo_docs.obtener(doc.documento_id)
    assert doc_act is not None
    assert doc_act.lista_precios_id == "22"
    assert doc_act.lista_precios_nombre == "Especial Divisas"
    assert doc_act.lineas[0].cantidad == Decimal("5")
    assert doc_act.subtotal == Decimal("120.00")
    assert doc_act.total_con_iva == Decimal("139.20")
    assert orden_confirmada_en_odoo(repo_cxc.get_orden("S00950"), doc_act) is False

    # Ahora la orden pasa a 'sale' (confirmada en Odoo) -> queda bloqueada contra edición
    # Más una orden nativa de Odoo (S00599) para probar el consolidado CxC + Odoo
    repo_cxc.upsert_ordenes(
        [
            b.orden(
                "S00950",
                cliente_id="88",
                fecha=date(2026, 11, 18),
                monto_total="139.20",
                estado_orden="sale",
                lista="22",
            ),
            b.orden(
                "S00599",
                cliente_id="88",
                fecha=date(2026, 11, 17),
                monto_total="116.00",
                estado_orden="sale",
                vendedor="v1@lubrikca.com",
                lista="21",
            ),
        ]
    )
    repo_cxc.upsert_lineas(
        [
            b.linea(
                "5991",
                so_id="S00599",
                producto="1001",
                cantidad="1",
                precio="100.00",
                subtotal="100.00",
            )
        ]
    )

    app.dependency_overrides[get_servicio_documentos] = lambda: svc
    try:
        with (
            patch("cxc.web.app.get_repo", return_value=repo_cxc),
            patch("cxc.web.app.get_current_user_from_cookie", return_value=VENTAS_USER),
        ):
            r_unif = client.get("/api/ventas/unificadas")
            assert r_unif.status_code == 200
            body = r_unif.json()
            assert "total_confirmados" in body["resumen"]
            assert "subtotal_confirmado_usd" in body["resumen"]
            assert "total_con_iva_confirmado_usd" in body["resumen"]

            items_map = {x["numero"]: x for x in body["items"]}
            it_cxc = items_map[doc.numero]
            assert it_cxc["confirmado_odoo"] is True
            assert it_cxc["solo_lectura"] is True
            assert it_cxc["ajustado_desde_odoo"] is True

            it_odoo = items_map["S00599"]
            assert it_odoo["subtotal"] == 100.0
            assert it_odoo["total_con_iva"] == 116.0
            assert it_odoo["confirmado_odoo"] is True

            # Editar pedido ya confirmado en Odoo debe ser bloqueado (409)
            r_edit = client.put(
                f"/api/documentos/{doc.documento_id}",
                json={"observaciones": "Intento de cambio tras confirmar"},
            )
            assert r_edit.status_code == 409

            r_post_sync = client.post("/api/ventas/sincronizar-odoo")
            assert r_post_sync.status_code == 200
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 5. Conciliación Bancaria Unificada: Regla R5 + /api/bancos/conciliacion/operativos-sistema
# ---------------------------------------------------------------------------
def test_conciliacion_regla_r5_ingresos_y_r4_egresos_unificados() -> None:
    mov_credito = MovimientoClasificado(
        MovimientoBancario(
            banco="BANCAMIGA_USD",
            cuenta="BANCAMIGA_USD",
            moneda="USD",
            fecha=date(2026, 11, 15),
            referencia="99887766",
            descripcion="ZELLE ALFA LUBRICANTES",
            debito=Decimal("0"),
            credito=Decimal("250.00"),
            saldo=Decimal("1500.00"),
            orden=1,
        ),
        categoria="cobro_candidato",
        regla="r",
    )
    mov_debito = MovimientoClasificado(
        MovimientoBancario(
            banco="BANCAMIGA_USD",
            cuenta="BANCAMIGA_USD",
            moneda="USD",
            fecha=date(2026, 11, 15),
            referencia="445566",
            descripcion="PAGO ESTACION SAN JUAN",
            debito=Decimal("80.00"),
            credito=Decimal("0"),
            saldo=Decimal("1420.00"),
            orden=2,
        ),
        categoria="egreso_sin_clasificar",
        regla="r",
    )

    doc_ing_banco = _doc_op(
        31,
        numero="PAG-00031",
        tipo=TIPO_PAGO,
        estado=CONFIRMADO_CXC,
        contraparte_nombre="ALFA LUBRICANTES",
        monto="250.00",
        moneda="USD",
        referencia="99887766",
        confirmado_cxc=True,
    )
    doc_ing_efectivo = _doc_op(
        32,
        numero="PAG-00032",
        tipo=TIPO_PAGO,
        estado=CONFIRMADO_CXC,
        contraparte_nombre="BETA",
        monto="50.00",
        moneda="USD",
        es_efectivo=True,
        confirmado_cxc=True,
    )
    doc_ing_z3 = _doc_op(
        33,
        numero="PAG-00033",
        tipo=TIPO_PAGO,
        estado=CONFIRMADO_CXC,
        contraparte_nombre="GAMMA",
        monto="70.00",
        moneda="USD",
        es_zelle_tercero=True,
        cuenta_tercero_id=7,
        confirmado_cxc=True,
    )
    doc_egr = _doc_op(
        34,
        numero="GAS-00034",
        tipo=TIPO_GASTO,
        estado=PENDIENTE_APROBACION,
        contraparte_nombre="ESTACION SAN JUAN",
        monto="80.00",
        moneda="USD",
        referencia="445566",
    )

    cands_ing: list[IngresoCxCCandidato] = ingresos_desde_operativos(
        [doc_ing_banco, doc_ing_efectivo, doc_ing_z3]
    )
    # Efectivo y Zelle 3ro NO entran a conciliación bancaria de cuenta propia
    assert [c.doc_id for c in cands_ing] == [31]

    cands_egr: list[EgresoCxCCandidato] = egresos_desde_operativos([doc_egr])
    props = proponer(
        [mov_credito, mov_debito],
        [],
        egresos_cxc=cands_egr,
        ingresos_cxc=cands_ing,
    )
    reglas = {p.huella: p for p in props}
    assert reglas[mov_credito.mov.huella].regla == "R5"
    assert reglas[mov_credito.mov.huella].candidatos == [31]
    assert reglas[mov_debito.mov.huella].regla == "R4"
    assert reglas[mov_debito.mov.huella].candidatos == [34]

    repo_op = RepoOpMem4()
    repo_op.crear(
        {
            "tipo": TIPO_PAGO,
            "token_preview": "t-p-31",
            "estado": CONFIRMADO_CXC,
            "contraparte_nombre": "ALFA LUBRICANTES",
            "monto": Decimal("250.00"),
            "moneda": "USD",
            "referencia": "99887766",
            "fecha_documento": date(2026, 11, 15),
        }
    )
    repo_op.crear(
        {
            "tipo": TIPO_GASTO,
            "token_preview": "t-g-34",
            "estado": PENDIENTE_APROBACION,
            "contraparte_nombre": "ESTACION SAN JUAN",
            "monto": Decimal("80.00"),
            "moneda": "USD",
            "referencia": "445566",
            "fecha_documento": date(2026, 11, 15),
        }
    )
    repo_b = _RepoFase5B()
    svc_op = ServicioOperativos(
        repo_op, lambda: None, repo_cxc=repo_b, repo_terceros=_RepoTercerosMem()
    )
    app.dependency_overrides[get_servicio_operativos] = lambda: svc_op
    try:
        with (
            patch("cxc.web.app.get_repo", return_value=repo_b),
            patch("cxc.web.app.get_current_user_from_cookie", return_value=ADMIN_USER),
        ):
            r_unif = client.get("/api/bancos/conciliacion/operativos-sistema")
            assert r_unif.status_code == 200
            d = r_unif.json()
            assert d["total_egresos"] == 1
            assert d["total_ingresos"] == 1
            assert "sugerencias_r4" in d and "sugerencias_r5" in d and "sugerencias_sistema" in d
    finally:
        app.dependency_overrides.clear()
