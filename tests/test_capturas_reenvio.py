"""Reenvío con la MISMA idempotency_key: la corrección reemplaza a la captura no enviada.

Reporte de QA (B1): antes CxC devolvía siempre la captura vieja y, con la escritura a Odoo
activada, se enviaba el borrador sin corregir.

Regla: ``PENDIENTE``/``INCOMPLETO`` con contenido distinto → se actualiza; mismo contenido →
idempotente; ``ENVIADO``/``ERROR`` → no se pisa.
"""

from __future__ import annotations

import xmlrpc.client
from typing import Any

import pytest

from cxc.capturas.modelos import ENVIADO, ERROR, INCOMPLETO, PENDIENTE, CapturaInvalida
from cxc.capturas.servicio import FacturaDuplicada
from cxc.web.app import app
from cxc.web.capturas_api import get_servicio
from tests.capturas_dobles import OdooFalso, contacto_ok, pedido_ok
from tests.test_capturas_api import BOT, FUENTE, client
from tests.test_capturas_servicio import ESP_TALLER, montar, pago

FUENTE_ANA = {**FUENTE, "vendedor_email": "ana@x.com"}


def _eventos(repo: Any, cid: int) -> list[tuple[str, str, str]]:
    return [(e, d, a) for i, e, d, a in repo.bitacora if i == cid]


# ── servicio ─────────────────────────────────────────────────────────────────


def test_la_correccion_con_la_misma_clave_actualiza_la_captura_pendiente() -> None:
    svc, repo, odoo = montar(espejo=ESP_TALLER, activa=False)
    vieja, creada = svc.recibir("pedido", pedido_ok(cliente="Tallr"), FUENTE_ANA, "k1")
    assert creada and vieja.estado == PENDIENTE
    nueva, creada2, actualizada = svc.recibir_detallado(
        "pedido", pedido_ok(cliente="Taller", email="correo@x.com"), FUENTE_ANA, "k1"
    )
    assert (creada2, actualizada) == (False, True) and nueva.captura_id == vieja.captura_id
    assert nueva.payload["cliente"] == "Taller" and nueva.payload["email"] == "correo@x.com"
    assert nueva.estado == PENDIENTE and len(repo.filas) == 1 and odoo.llamadas == []
    evento = _eventos(repo, vieja.captura_id)[-1]
    assert evento == ("actualizada_por_reenvio", "cliente, email", "ana@x.com")


def test_el_borrador_viejo_nunca_llega_a_odoo_si_se_corrigio_antes_de_enviarse() -> None:
    """Con la escritura apagada queda PENDIENTE; al corregir y activar, va lo corregido."""
    odoo = OdooFalso()
    svc, repo, _ = montar(odoo, ESP_TALLER, activa=False)
    c, _ = svc.recibir("pago", pago(referencia="MALA"), FUENTE_ANA, "k1")
    svc.recibir("pago", pago(referencia="BUENA"), FUENTE_ANA, "k1")
    from cxc.capturas.servicio import ServicioCapturas
    from cxc.odoo.escritura import EscritorOdoo

    activo = ServicioCapturas(repo, ESP_TALLER, lambda: EscritorOdoo(odoo), diarios={"default": 14})
    activo.procesar(c.captura_id)  # ahora sí se escribe
    memo = odoo.creados_de("account.payment")[0]["memo"]
    assert "BUENA" in memo and "MALA" not in memo


def test_mismo_contenido_es_idempotente_y_no_toca_la_bitacora() -> None:
    svc, repo, _ = montar(activa=False)
    c, _ = svc.recibir("contacto", contacto_ok(), FUENTE_ANA, "k1")
    antes = list(repo.bitacora)
    c2, creada, actualizada = svc.recibir_detallado("contacto", contacto_ok(), FUENTE_ANA, "k1")
    assert (creada, actualizada) == (False, False) and c2 == c and repo.bitacora == antes


def test_el_rif_en_otro_formato_pero_equivalente_no_cuenta_como_cambio() -> None:
    svc, repo, _ = montar(activa=False)
    svc.recibir("pedido", pedido_ok(rif="J-123456789"), FUENTE_ANA, "k1")
    _, _, actualizada = svc.recibir_detallado(
        "pedido", pedido_ok(rif="j-12345678-9"), FUENTE_ANA, "k1"
    )
    assert actualizada is False and not any(
        e[0] == "actualizada_por_reenvio" for e in _eventos(repo, 1)
    )


def test_una_captura_incompleta_se_completa_reenviando_el_borrador_corregido() -> None:
    svc, _, odoo = montar(espejo=ESP_TALLER, activa=False)
    c, _ = svc.recibir("pago", pago(referencia=None), FUENTE_ANA, "k1")
    assert c.estado == INCOMPLETO and "referencia" in c.incompleto
    nueva, _, actualizada = svc.recibir_detallado("pago", pago(referencia="R9"), FUENTE_ANA, "k1")
    assert (
        actualizada and nueva.estado == PENDIENTE and nueva.incompleto == [] and odoo.llamadas == []
    )


def test_el_reenvio_puede_volver_a_dejar_la_captura_incompleta() -> None:
    svc, _, _ = montar(activa=False)
    c, _ = svc.recibir("pago", pago(), FUENTE_ANA, "k1")
    assert c.estado == PENDIENTE
    nueva, _, actualizada = svc.recibir_detallado("pago", pago(referencia=None), FUENTE_ANA, "k1")
    assert actualizada and nueva.estado == INCOMPLETO and "referencia" in nueva.incompleto


def test_una_captura_enviada_no_se_pisa() -> None:
    svc, repo, odoo = montar(espejo=ESP_TALLER)
    c, _ = svc.recibir("pago", pago(referencia="R1"), FUENTE_ANA, "k1")
    enviada = svc.procesar(c.captura_id)
    assert enviada.estado == ENVIADO
    n = len(odoo.llamadas)
    otra, creada, actualizada = svc.recibir_detallado(
        "pago", pago(referencia="OTRA"), FUENTE_ANA, "k1"
    )
    assert (creada, actualizada) == (False, False)
    assert (
        otra.estado == ENVIADO
        and otra.payload["referencia"] == "R1"
        and otra.odoo_ref == enviada.odoo_ref
    )
    assert len(odoo.llamadas) == n and len(odoo.creados_de("account.payment")) == 1
    assert not any(e[0] == "actualizada_por_reenvio" for e in _eventos(repo, c.captura_id))


def test_una_captura_en_error_no_se_pisa() -> None:
    falla = OdooFalso(account_payment__create=xmlrpc.client.Fault(1, "x"))
    svc, _, _ = montar(falla, ESP_TALLER)
    c, _ = svc.recibir("pago", pago(referencia="R1"), FUENTE_ANA, "k1")
    assert svc.procesar(c.captura_id).estado == ERROR
    otra, _, actualizada = svc.recibir_detallado("pago", pago(referencia="OTRA"), FUENTE_ANA, "k1")
    assert not actualizada and otra.estado == ERROR and otra.payload["referencia"] == "R1"


def test_un_reenvio_invalido_se_rechaza_y_deja_intacta_la_captura() -> None:
    svc, repo, _ = montar(activa=False)
    c, _ = svc.recibir("pedido", pedido_ok(), FUENTE_ANA, "k1")
    with pytest.raises(CapturaInvalida):
        svc.recibir("pedido", pedido_ok(rif=None), FUENTE_ANA, "k1")
    assert (
        repo.filas[c.captura_id].payload == c.payload
        and repo.filas[c.captura_id].estado == PENDIENTE
    )


def test_la_misma_clave_con_otro_tipo_se_rechaza() -> None:
    svc, repo, _ = montar(activa=False)
    c, _ = svc.recibir("contacto", contacto_ok(), FUENTE_ANA, "k1")
    with pytest.raises(CapturaInvalida) as e:
        svc.recibir("pedido", pedido_ok(), FUENTE_ANA, "k1")
    assert "otro tipo" in e.value.error and repo.filas[c.captura_id].kind == "contacto"


def test_un_reenvio_ya_enviado_con_otro_tipo_devuelve_la_existente_sin_error() -> None:
    svc, _, _ = montar()
    c, _ = svc.recibir("contacto", contacto_ok(), FUENTE_ANA, "k1")
    svc.procesar(c.captura_id)
    otra, creada, actualizada = svc.recibir_detallado("pedido", pedido_ok(), FUENTE_ANA, "k1")
    assert not creada and not actualizada and otra.estado == ENVIADO


def test_corregir_el_numero_de_una_factura_no_puede_chocar_con_otra_ya_capturada() -> None:
    svc, repo, _ = montar(activa=False)
    base = {
        "proveedor": "Lubricantes SA",
        "rif": "J-123456789",
        "fecha_emision": "2026-11-10",
        "moneda": "USD",
        "monto_total": "50",
    }
    svc.recibir("factura_proveedor", {**base, "numero_factura": "F-1"}, FUENTE_ANA, "k1")
    b, _ = svc.recibir("factura_proveedor", {**base, "numero_factura": "F-2"}, FUENTE_ANA, "k2")
    with pytest.raises(FacturaDuplicada):
        svc.recibir("factura_proveedor", {**base, "numero_factura": "F-1"}, FUENTE_ANA, "k2")
    assert repo.filas[b.captura_id].payload["numero_factura"] == "F-2"
    ok, _, actualizada = svc.recibir_detallado(
        "factura_proveedor", {**base, "numero_factura": "F-3"}, FUENTE_ANA, "k2"
    )
    assert actualizada and ok.payload["numero_factura"] == "F-3"


def test_sin_vendedor_en_la_fuente_la_bitacora_registra_al_bot() -> None:
    svc, repo, _ = montar(activa=False)
    svc.recibir("contacto", contacto_ok(), {}, "k1")
    svc.recibir("contacto", contacto_ok(nombre="Otro"), {}, "k1")
    assert _eventos(repo, 1)[-1] == ("actualizada_por_reenvio", "nombre", "bot")


# ── API ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def entorno_api(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("BOT_API_KEY", "clave-de-prueba-del-bot")
    svc, repo, odoo = montar(OdooFalso(), ESP_TALLER)
    app.dependency_overrides[get_servicio] = lambda: svc
    yield repo, odoo
    app.dependency_overrides.pop(get_servicio, None)


def _post(kind: str, draft: dict[str, Any], clave: str = "k1") -> Any:
    body = {"kind": kind, "draft": draft, "source": FUENTE_ANA, "idempotency_key": clave}
    return client.post("/api/bots/capturas", json=body, headers=BOT)


def test_api_el_reenvio_corregido_responde_200_y_envia_lo_corregido_a_odoo(
    entorno_api: Any,
) -> None:
    repo, odoo = entorno_api
    # primer envío: el producto no existe → INCOMPLETO tras el intento en segundo plano
    r1 = _post("pedido", pedido_ok(lineas=[{"producto": "Fantasma", "cantidad": "1"}]))
    assert r1.status_code == 201 and repo.filas[r1.json()["id"]].estado == INCOMPLETO
    # el vendedor corrige el producto y reenvía con la MISMA clave
    r2 = _post("pedido", pedido_ok(lineas=[{"producto": "AC-20W50", "cantidad": "3"}]))
    assert r2.status_code == 200 and r2.json()["id"] == r1.json()["id"]
    assert r2.json()["payload"]["lineas"][0]["producto"] == "AC-20W50"
    cap = repo.filas[r1.json()["id"]]  # el envío en segundo plano ya corrió con lo corregido
    assert cap.estado == ENVIADO and cap.odoo_ref.startswith("sale.order:")
    linea = odoo.creados_de("sale.order")[0]["order_line"][0][2]
    assert linea["product_id"] == 900 and linea["product_uom_qty"] == 3.0
    assert len(repo.filas) == 1 and len(odoo.creados_de("sale.order")) == 1


def test_api_reenvio_identico_responde_200_sin_cambios_ni_nuevo_envio(entorno_api: Any) -> None:
    repo, odoo = entorno_api
    r1 = _post("contacto", contacto_ok())
    n = len(odoo.llamadas)
    r2 = _post("contacto", contacto_ok())
    assert (r1.status_code, r2.status_code) == (201, 200) and len(odoo.llamadas) == n
    assert not any(e[1] == "actualizada_por_reenvio" for e in repo.bitacora)


def test_api_reenvio_de_una_captura_enviada_responde_200_con_la_existente(entorno_api: Any) -> None:
    repo, odoo = entorno_api
    r1 = _post("contacto", contacto_ok())
    assert repo.filas[r1.json()["id"]].estado == ENVIADO
    r2 = _post("contacto", contacto_ok(nombre="Otro Nombre"))
    assert r2.status_code == 200 and r2.json()["payload"]["nombre"] == "Pedro"
    assert r2.json()["estado"] == "ENVIADO" and len(odoo.creados_de("res.partner")) == 1


def test_api_reenvio_invalido_da_422_y_no_modifica(entorno_api: Any) -> None:
    repo, _ = entorno_api
    r1 = _post("pedido", pedido_ok(lineas=[{"producto": "Fantasma", "cantidad": "1"}]))
    r2 = _post("pedido", pedido_ok(rif="ABC"))
    assert r2.status_code == 422 and "faltan" in r2.json()
    assert repo.filas[r1.json()["id"]].payload["rif"] == "J-123456789"


def test_api_reenvio_con_factura_duplicada_da_409(entorno_api: Any) -> None:
    base = {
        "proveedor": "Lubricantes SA",
        "rif": "J-123456789",
        "fecha_emision": "2026-11-10",
        "moneda": "USD",
        "monto_total": "50",
    }
    _post("factura_proveedor", {**base, "numero_factura": "F-1"}, "ka")
    _post("factura_proveedor", {**base, "numero_factura": "F-2"}, "kb")
    r = _post("factura_proveedor", {**base, "numero_factura": "F-1"}, "kb")
    assert r.status_code == 409 and "F-1" in r.json()["error"]


def test_api_la_captura_incompleta_corregida_con_la_misma_clave_se_envia(entorno_api: Any) -> None:
    repo, odoo = entorno_api
    r1 = _post("pago", pago(referencia=None))
    assert r1.json()["estado"] == "INCOMPLETO" and odoo.llamadas == []
    r2 = _post("pago", pago(referencia="R77"))
    assert r2.status_code == 200 and r2.json()["estado"] == "PENDIENTE"  # se envía en segundo plano
    assert repo.filas[r1.json()["id"]].estado == ENVIADO
    assert "R77" in odoo.creados_de("account.payment")[0]["memo"]
