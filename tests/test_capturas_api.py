"""API de capturas: contrato del bot, estados y permisos de la interfaz (servicio con dobles)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cxc.capturas.servicio import ServicioCapturas
from cxc.odoo.escritura import EscritorOdoo
from cxc.permisos import efectivos
from cxc.web.app import app
from cxc.web.capturas_api import get_servicio
from tests.capturas_dobles import EspejoFalso, OdooFalso, RepoMemoria, contacto_ok, pedido_ok

client = TestClient(app)
KEY = "clave-de-prueba-del-bot"
BOT = {"X-Bot-Key": KEY}
FUENTE = {
    "telegram_user_id": 7,
    "vendedor_email": "ana@x.com",
    "chat_id": -1,
    "message_id": 3,
    "auto": False,
    "incompleto": [],
}
ESPEJO = EspejoFalso(clientes={"Taller": ["55"]}, productos={"AC-20W50": ["900"]})

ADMIN = {"email": "admin@x.test", "rol": "admin", "permisos": []}
TESORERIA = {"email": "t@x.test", "rol": "tesoreria", "permisos": efectivos("tesoreria")}
AUDITOR = {"email": "a@x.test", "rol": "auditor", "permisos": efectivos("auditor")}
VENTAS = {"email": "v@x.test", "rol": "ventas", "permisos": efectivos("ventas")}


def _como(usuario: dict[str, Any] | None):
    return patch("cxc.web.app.get_current_user_from_cookie", return_value=usuario)


@pytest.fixture
def entorno(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[ServicioCapturas, RepoMemoria, OdooFalso]]:
    monkeypatch.setenv("BOT_API_KEY", KEY)
    odoo, repo = OdooFalso(), RepoMemoria()
    svc = ServicioCapturas(repo, ESPEJO, lambda: EscritorOdoo(odoo), diarios={"default": 14})
    app.dependency_overrides[get_servicio] = lambda: svc
    yield svc, repo, odoo
    app.dependency_overrides.pop(get_servicio, None)


def _pago(**kw: Any) -> dict[str, Any]:
    clave = kw.pop("k", "k1")
    d = {"cliente": "Taller", "monto": "100", "moneda": "USD", "referencia": "R1"}
    d.update(kw)
    return {"kind": "pago", "draft": d, "source": FUENTE, "idempotency_key": clave}


# ── autenticación del bot ────────────────────────────────────────────────────


def test_sin_clave_da_401(entorno: Any) -> None:
    assert client.post("/api/bots/capturas", json=_pago()).status_code == 401


def test_clave_incorrecta_da_401(entorno: Any) -> None:
    assert (
        client.post("/api/bots/capturas", json=_pago(), headers={"X-Bot-Key": "otra"}).status_code
        == 401
    )


def test_sin_bot_api_key_configurada_da_503_aunque_manden_clave(
    entorno: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BOT_API_KEY")
    r = client.post("/api/bots/capturas", json=_pago(), headers=BOT)
    assert r.status_code == 503
    assert (
        client.post("/api/bots/capturas/1/completar", json={"cambios": {}}, headers=BOT).status_code
        == 503
    )


def test_la_clave_no_se_compara_a_ciegas(entorno: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_API_KEY", "abc")
    for mala in ("abcd", "ab", "ABC", ""):
        assert (
            client.post("/api/bots/capturas", json=_pago(), headers={"X-Bot-Key": mala}).status_code
            == 401
        )


# ── contrato de POST /api/bots/capturas ──────────────────────────────────────


def test_crea_responde_201_con_el_contrato_y_envia_en_segundo_plano(entorno: Any) -> None:
    svc, repo, odoo = entorno
    r = client.post("/api/bots/capturas", json=_pago(), headers=BOT)
    assert r.status_code == 201
    body = r.json()
    assert set(body) >= {
        "id",
        "kind",
        "estado",
        "incompleto",
        "resultado",
        "odoo_ref",
        "error",
        "auto",
        "payload",
        "fecha_creacion",
        "fecha_actualizacion",
    }
    assert body["kind"] == "pago" and body["estado"] == "PENDIENTE"  # responde sin esperar a Odoo
    # El envío corrió en segundo plano (TestClient lo ejecuta al terminar la respuesta).
    assert repo.filas[body["id"]].estado == "ENVIADO" and odoo.creados_de("account.payment")


def test_misma_clave_responde_200_con_el_mismo_registro_y_no_duplica(entorno: Any) -> None:
    _, repo, odoo = entorno
    a = client.post("/api/bots/capturas", json=_pago(), headers=BOT)
    b = client.post("/api/bots/capturas", json=_pago(), headers=BOT)
    assert (a.status_code, b.status_code) == (201, 200) and a.json()["id"] == b.json()["id"]
    assert len(repo.filas) == 1 and len(odoo.creados_de("account.payment")) == 1


def test_pago_incompleto_responde_201_con_estado_incompleto(entorno: Any) -> None:
    _, _, odoo = entorno
    body = {
        "kind": "pago",
        "draft": {"cliente": "T", "monto": "5"},
        "source": {**FUENTE, "auto": True},
        "idempotency_key": "k9",
    }
    r = client.post("/api/bots/capturas", json=body, headers=BOT)
    assert r.status_code == 201 and r.json()["estado"] == "INCOMPLETO" and r.json()["auto"] is True
    assert "referencia" in r.json()["incompleto"] and odoo.llamadas == []


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "evento", "draft": {}, "source": FUENTE, "idempotency_key": "x"},
        {
            "kind": "pago",
            "draft": {"cliente": "T", "monto": "-4", "moneda": "USD"},
            "source": FUENTE,
            "idempotency_key": "x",
        },
        {"kind": "contacto", "draft": {"nombre": "P"}, "source": FUENTE, "idempotency_key": "x"},
        {
            "kind": "pedido",
            "draft": {"cliente": "T", "lineas": []},
            "source": FUENTE,
            "idempotency_key": "x",
        },
    ],
)
def test_invalido_de_fondo_da_422_con_error_y_faltan(entorno: Any, body: dict[str, Any]) -> None:
    _, repo, _ = entorno
    r = client.post("/api/bots/capturas", json=body, headers=BOT)
    assert r.status_code == 422 and "error" in r.json() and "faltan" in r.json()
    assert repo.filas == {}


def test_cuerpo_mal_formado_da_422(entorno: Any) -> None:
    assert client.post("/api/bots/capturas", json={"kind": "pago"}, headers=BOT).status_code == 422
    assert (
        client.post(
            "/api/bots/capturas", json={**_pago(), "idempotency_key": ""}, headers=BOT
        ).status_code
        == 422
    )


def test_contacto_pedido_y_pago_proveedor_se_aceptan(entorno: Any) -> None:
    for kind, draft in (
        ("contacto", contacto_ok()),
        ("pedido", pedido_ok(lineas=[{"producto": "AC-20W50", "cantidad": "1"}])),
        ("pago_proveedor", {"proveedor": "Lub", "monto": "9", "moneda": "USD"}),
    ):
        r = client.post(
            "/api/bots/capturas",
            json={"kind": kind, "draft": draft, "source": FUENTE, "idempotency_key": f"k-{kind}"},
            headers=BOT,
        )
        assert r.status_code == 201, (kind, r.text)


# ── completar ────────────────────────────────────────────────────────────────


def _incompleto(entorno: Any) -> int:
    body = {
        "kind": "pago",
        "draft": {"cliente": "Taller", "monto": "5", "moneda": "USD"},
        "source": FUENTE,
        "idempotency_key": "kc",
    }
    return int(client.post("/api/bots/capturas", json=body, headers=BOT).json()["id"])


def test_completar_un_pago_lo_reevalua_y_lo_envia(entorno: Any) -> None:
    _, repo, odoo = entorno
    cid = _incompleto(entorno)
    r = client.post(
        f"/api/bots/capturas/{cid}/completar", json={"cambios": {"referencia": "R77"}}, headers=BOT
    )
    assert r.status_code == 200 and r.json()["payload"]["referencia"] == "R77"
    assert r.json()["estado"] == "PENDIENTE"  # el envío va en segundo plano
    assert repo.filas[cid].estado == "ENVIADO" and odoo.creados_de("account.payment")


def test_completar_errores(entorno: Any) -> None:
    cid = _incompleto(entorno)
    assert (
        client.post(
            "/api/bots/capturas/999/completar", json={"cambios": {"a": 1}}, headers=BOT
        ).status_code
        == 404
    )
    r = client.post(
        f"/api/bots/capturas/{cid}/completar", json={"cambios": {"monto": "-1"}}, headers=BOT
    )
    assert r.status_code == 422 and r.json()["faltan"] == ["monto"]
    contacto = client.post(
        "/api/bots/capturas",
        json={
            "kind": "contacto",
            "draft": contacto_ok(),
            "source": FUENTE,
            "idempotency_key": "kcont",
        },
        headers=BOT,
    ).json()["id"]
    assert (
        client.post(
            f"/api/bots/capturas/{contacto}/completar",
            json={"cambios": {"nombre": "Q"}},
            headers=BOT,
        ).status_code
        == 422
    )
    client.post(
        f"/api/bots/capturas/{cid}/completar", json={"cambios": {"referencia": "R1"}}, headers=BOT
    )
    r = client.post(
        f"/api/bots/capturas/{cid}/completar", json={"cambios": {"referencia": "R2"}}, headers=BOT
    )
    assert r.status_code == 409 and "Odoo" in r.json()["error"]


# ── interfaz de CxC (sesión y permisos) ──────────────────────────────────────


def test_listar_exige_sesion_y_permiso(entorno: Any) -> None:
    with _como(None):
        assert client.get("/api/bots/capturas").status_code == 401
    with _como(VENTAS):
        assert client.get("/api/bots/capturas").status_code == 403
    for u in (TESORERIA, AUDITOR, ADMIN):
        with _como(u):
            assert client.get("/api/bots/capturas").status_code == 200


def test_listar_filtra_y_pagina(entorno: Any) -> None:
    client.post("/api/bots/capturas", json=_pago(k="a"), headers=BOT)
    client.post(
        "/api/bots/capturas",
        json={
            "kind": "contacto",
            "draft": contacto_ok(),
            "source": FUENTE,
            "idempotency_key": "b",
        },
        headers=BOT,
    )
    inc = {
        "kind": "pago",
        "draft": {"cliente": "T", "monto": "5"},
        "source": FUENTE,
        "idempotency_key": "c",
    }
    client.post("/api/bots/capturas", json=inc, headers=BOT)
    with _como(TESORERIA):
        todo = client.get("/api/bots/capturas").json()
        assert todo["total"] == 3 and [i["id"] for i in todo["items"]] == [3, 2, 1]
        assert client.get("/api/bots/capturas?estado=INCOMPLETO").json()["total"] == 1
        assert client.get("/api/bots/capturas?kind=contacto").json()["total"] == 1
        assert client.get("/api/bots/capturas?estado=ENVIADO&kind=pago").json()["total"] == 1
        pag = client.get("/api/bots/capturas?limit=1&offset=1").json()
        assert pag["total"] == 3 and len(pag["items"]) == 1
        assert client.get("/api/bots/capturas?estado=XX").status_code == 422
        assert client.get("/api/bots/capturas?kind=evento").status_code == 422


def test_detalle_con_bitacora(entorno: Any) -> None:
    cid = client.post("/api/bots/capturas", json=_pago(), headers=BOT).json()["id"]
    with _como(AUDITOR):
        r = client.get(f"/api/bots/capturas/{cid}")
        assert r.status_code == 200
        assert [e["evento"] for e in r.json()["eventos"]] == [
            "recibida",
            "intento_envio",
            "creada_en_odoo",
        ]
        assert client.get("/api/bots/capturas/999").status_code == 404
    with _como(VENTAS):
        assert client.get(f"/api/bots/capturas/{cid}").status_code == 403


def test_reintentar_exige_gestionar(entorno: Any) -> None:
    svc, repo, odoo = entorno
    body = {
        "kind": "pedido",
        "draft": pedido_ok(lineas=[{"producto": "Fantasma", "cantidad": "1"}]),
        "source": FUENTE,
        "idempotency_key": "kp",
    }
    cid = client.post("/api/bots/capturas", json=body, headers=BOT).json()["id"]
    assert repo.filas[cid].estado == "INCOMPLETO"
    with _como(AUDITOR):  # solo ve
        assert client.post(f"/api/bots/capturas/{cid}/reintentar").status_code == 403
    with _como(None):
        assert client.post(f"/api/bots/capturas/{cid}/reintentar").status_code == 401
    ESPEJO.prods["fantasma"] = ["901"]
    try:
        with _como(TESORERIA):
            r = client.post(f"/api/bots/capturas/{cid}/reintentar")
        assert r.status_code == 200 and r.json()["estado"] == "ENVIADO"
        assert ("reintento_manual", "t@x.test") in [
            (e[1], e[3]) for e in repo.bitacora if e[0] == cid
        ]
        with _como(TESORERIA):
            assert client.post(f"/api/bots/capturas/{cid}/reintentar").status_code == 409
            assert client.post("/api/bots/capturas/999/reintentar").status_code == 404
    finally:
        ESPEJO.prods.pop("fantasma", None)


# ── página HTML ──────────────────────────────────────────────────────────────


def test_pagina_capturas_segun_permisos() -> None:
    r_legado = client.get("/capturas", follow_redirects=False)
    assert r_legado.status_code == 302 and r_legado.headers["location"] == "/compras?tab=facturas"

    with _como(TESORERIA):
        assert client.get("/compras", follow_redirects=False).status_code == 200
    with _como(ADMIN):
        assert client.get("/compras", follow_redirects=False).status_code == 200
    with _como(VENTAS):
        r_v = client.get("/compras", follow_redirects=False)
        assert r_v.status_code in (302, 307) and r_v.headers["location"] == "/inicio"
    with _como(None):
        r = client.get("/compras", follow_redirects=False)
        assert r.status_code in (302, 307) and r.headers["location"] == "/login"
