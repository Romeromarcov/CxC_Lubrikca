"""API de capturas: `proveedor` y `factura_proveedor` (contrato, 422/409, completar, permisos)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from cxc.capturas.servicio import ServicioCapturas
from cxc.odoo.escritura import EscritorOdoo
from cxc.web.app import app
from cxc.web.capturas_api import get_servicio
from tests.capturas_dobles import EspejoFalso, OdooFalso, RepoMemoria, partners
from tests.test_capturas_api import ADMIN, AUDITOR, BOT, FUENTE, TESORERIA, _como, client


def _montar(monkeypatch: pytest.MonkeyPatch, odoo: OdooFalso) -> tuple[RepoMemoria, OdooFalso]:
    monkeypatch.setenv("BOT_API_KEY", "clave-de-prueba-del-bot")
    repo = RepoMemoria()
    svc = ServicioCapturas(repo, EspejoFalso(), lambda: EscritorOdoo(odoo), diarios={"default": 14})
    app.dependency_overrides[get_servicio] = lambda: svc
    return repo, odoo


@pytest.fixture
def con_proveedor(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[RepoMemoria, OdooFalso]]:
    """Odoo donde el proveedor ya existe (partner 31)."""
    yield _montar(monkeypatch, OdooFalso(res_partner__search=[31]))
    app.dependency_overrides.pop(get_servicio, None)


@pytest.fixture
def sin_proveedor(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[RepoMemoria, OdooFalso]]:
    """Odoo vacío: el proveedor no existe."""
    yield _montar(monkeypatch, OdooFalso())
    app.dependency_overrides.pop(get_servicio, None)


def _cuerpo(kind: str, draft: dict[str, Any], clave: str = "k1") -> dict[str, Any]:
    return {"kind": kind, "draft": draft, "source": FUENTE, "idempotency_key": clave}


PROV = {
    "nombre": "Lubricantes SA",
    "rif": "j-12345678-9",
    "telefono": "0414-1234567",
    "email": "p@x.com",
    "direccion": "Av 1",
    "dias_credito": 30,
    "ubicacion": {"latitud": 10.4, "longitud": -66.9, "precision_m": 8},
    "notas": "n",
}
FACT = {
    "proveedor": "Lubricantes SA",
    "rif": "J-123456789",
    "numero_factura": "F-001",
    "numero_control": "00-1",
    "fecha_emision": "2026-11-10",
    "fecha_vencimiento": "2026-12-10",
    "moneda": "USD",
    "base_imponible": "100",
    "iva": "16",
    "monto_total": "116",
    "concepto": "Aceite",
    "orden_ref": "OC-9",
    "notas": "x",
}


# ── proveedor ────────────────────────────────────────────────────────────────


def test_proveedor_nuevo_201_y_se_crea_en_segundo_plano(sin_proveedor: Any) -> None:
    repo, odoo = sin_proveedor
    r = client.post("/api/bots/capturas", json=_cuerpo("proveedor", PROV), headers=BOT)
    assert (
        r.status_code == 201
        and r.json()["kind"] == "proveedor"
        and r.json()["estado"] == "PENDIENTE"
    )
    assert r.json()["payload"]["rif"] == "J-123456789"  # RIF normalizado
    cap = repo.filas[r.json()["id"]]
    assert cap.estado == "ENVIADO" and cap.odoo_ref == "res.partner:101"
    (v,) = odoo.creados_de("res.partner")
    assert v["supplier_rank"] == 1 and v["customer_rank"] == 0


def test_proveedor_que_ya_existe_no_se_duplica(monkeypatch: pytest.MonkeyPatch) -> None:
    def existe_por_rif(args: list[Any], kwargs: dict[str, Any]) -> list[int]:
        # el partner 31 existe (por RIF), pero ninguno lleva la marca de esta captura
        return [] if any(isinstance(d, list) and d[0] == "ref" for d in args[0]) else [31]

    repo, odoo = _montar(monkeypatch, OdooFalso(res_partner__search=existe_por_rif))
    try:
        r = client.post("/api/bots/capturas", json=_cuerpo("proveedor", PROV), headers=BOT)
    finally:
        app.dependency_overrides.pop(get_servicio, None)
    cap = repo.filas[r.json()["id"]]
    assert cap.estado == "ENVIADO" and cap.odoo_ref == "res.partner:31"
    assert cap.resultado["ya_existia"] is True and odoo.creados == []


@pytest.mark.parametrize("campo", ["nombre", "rif", "telefono"])
def test_proveedor_sin_requerido_da_422_con_faltan(sin_proveedor: Any, campo: str) -> None:
    draft = {k: v for k, v in PROV.items() if k != campo}
    r = client.post("/api/bots/capturas", json=_cuerpo("proveedor", draft), headers=BOT)
    assert r.status_code == 422 and r.json()["faltan"] == [campo] and "error" in r.json()


def test_proveedor_invalido_da_422(sin_proveedor: Any) -> None:
    for malo in ({"rif": "ABC"}, {"telefono": "1"}, {"dias_credito": "x"}):
        r = client.post(
            "/api/bots/capturas", json=_cuerpo("proveedor", {**PROV, **malo}), headers=BOT
        )
        assert r.status_code == 422


def test_los_kinds_nuevos_exigen_la_clave_del_bot(sin_proveedor: Any) -> None:
    for kind, draft in (("proveedor", PROV), ("factura_proveedor", FACT)):
        assert client.post("/api/bots/capturas", json=_cuerpo(kind, draft)).status_code == 401


# ── factura de proveedor ─────────────────────────────────────────────────────


def test_factura_completa_201_y_se_envia_en_borrador(con_proveedor: Any) -> None:
    repo, odoo = con_proveedor
    r = client.post("/api/bots/capturas", json=_cuerpo("factura_proveedor", FACT), headers=BOT)
    assert r.status_code == 201 and r.json()["estado"] == "PENDIENTE"
    cap = repo.filas[r.json()["id"]]
    assert cap.estado == "ENVIADO" and cap.odoo_ref == "account.move:101"
    (v,) = odoo.creados_de("account.move")
    assert (
        v["move_type"] == "in_invoice"
        and v["ref"] == "F-001"
        and v["invoice_date_due"] == "2026-12-10"
    )
    assert "Control 00-1" in v["narration"] and "IVA 16" in v["narration"]


def test_factura_incompleta_201_incompleto_y_sin_tocar_odoo(con_proveedor: Any) -> None:
    repo, odoo = con_proveedor
    r = client.post(
        "/api/bots/capturas",
        json=_cuerpo("factura_proveedor", {"proveedor": "L", "monto_total": "5"}),
        headers=BOT,
    )
    assert r.status_code == 201 and r.json()["estado"] == "INCOMPLETO"
    assert r.json()["incompleto"] == ["numero_factura", "fecha_emision", "moneda"]
    assert odoo.llamadas == []


def test_factura_invalida_de_fondo_da_422(con_proveedor: Any) -> None:
    for malo in ({"monto_total": "-1"}, {"fecha_emision": "ayer"}, {"rif": "X"}):
        r = client.post(
            "/api/bots/capturas", json=_cuerpo("factura_proveedor", {**FACT, **malo}), headers=BOT
        )
        assert r.status_code == 422 and "error" in r.json()


def test_factura_duplicada_da_409_y_no_se_registra(con_proveedor: Any) -> None:
    repo, _ = con_proveedor
    primera = client.post(
        "/api/bots/capturas", json=_cuerpo("factura_proveedor", FACT, "k1"), headers=BOT
    )
    r = client.post(
        "/api/bots/capturas", json=_cuerpo("factura_proveedor", FACT, "k2"), headers=BOT
    )
    assert primera.status_code == 201 and r.status_code == 409
    assert "F-001" in r.json()["error"] and f"#{primera.json()['id']}" in r.json()["error"]
    assert len(repo.filas) == 1


def test_el_reenvio_del_mismo_mensaje_sigue_siendo_200_no_409(con_proveedor: Any) -> None:
    a = client.post(
        "/api/bots/capturas", json=_cuerpo("factura_proveedor", FACT, "k1"), headers=BOT
    )
    b = client.post(
        "/api/bots/capturas", json=_cuerpo("factura_proveedor", FACT, "k1"), headers=BOT
    )
    assert (a.status_code, b.status_code) == (201, 200) and a.json()["id"] == b.json()["id"]


def test_factura_ya_en_odoo_queda_en_error_legible(monkeypatch: pytest.MonkeyPatch) -> None:
    def moves(args: list[Any], kwargs: dict[str, Any]) -> list[int]:
        return [] if any(isinstance(d, list) and d[0] == "narration" for d in args[0]) else [900]

    repo, odoo = _montar(
        monkeypatch, OdooFalso(res_partner__search=partners(31), account_move__search=moves)
    )
    try:
        r = client.post("/api/bots/capturas", json=_cuerpo("factura_proveedor", FACT), headers=BOT)
        cap = repo.filas[r.json()["id"]]
        assert cap.estado == "ERROR" and "ya existe en Odoo" in cap.error and odoo.creados == []
    finally:
        app.dependency_overrides.pop(get_servicio, None)


def test_proveedor_de_la_factura_inexistente_se_crea_desde_el_documento(
    sin_proveedor: Any,
) -> None:
    repo, odoo = sin_proveedor
    r = client.post("/api/bots/capturas", json=_cuerpo("factura_proveedor", FACT), headers=BOT)
    cap = repo.filas[r.json()["id"]]
    assert cap.estado == "ENVIADO" and cap.resultado["proveedor_creado"] is True
    assert len(odoo.creados_de("res.partner")) == 1 and len(odoo.creados_de("account.move")) == 1


# ── completar facturas ───────────────────────────────────────────────────────


def _incompleta(clave: str = "kf") -> int:
    draft = {k: v for k, v in FACT.items() if k not in ("numero_factura", "moneda")}
    return int(
        client.post(
            "/api/bots/capturas", json=_cuerpo("factura_proveedor", draft, clave), headers=BOT
        ).json()["id"]
    )


def test_completar_factura_por_el_bot(con_proveedor: Any) -> None:
    repo, odoo = con_proveedor
    cid = _incompleta()
    r = client.post(
        f"/api/bots/capturas/{cid}/completar",
        json={"cambios": {"numero_factura": "F-9", "moneda": "USD"}},
        headers=BOT,
    )
    assert r.status_code == 200 and r.json()["payload"]["numero_factura"] == "F-9"
    assert repo.filas[cid].estado == "ENVIADO" and odoo.creados_de("account.move")


def test_completar_factura_desde_la_pantalla_con_gestionar(con_proveedor: Any) -> None:
    repo, _ = con_proveedor
    cid = _incompleta()
    cambios = {"cambios": {"numero_factura": "F-9", "moneda": "USD"}}
    with _como(AUDITOR):
        assert client.post(f"/api/bots/capturas/{cid}/completar", json=cambios).status_code == 403
    with _como(TESORERIA):
        r = client.post(f"/api/bots/capturas/{cid}/completar", json=cambios)
    assert r.status_code == 200 and repo.filas[cid].estado == "ENVIADO"
    assert (cid, "completada", "moneda, numero_factura", "t@x.test") in repo.bitacora


def test_completar_con_un_numero_ya_capturado_da_409(con_proveedor: Any) -> None:
    client.post("/api/bots/capturas", json=_cuerpo("factura_proveedor", FACT, "k1"), headers=BOT)
    cid = _incompleta()
    r = client.post(
        f"/api/bots/capturas/{cid}/completar",
        json={"cambios": {"numero_factura": "F-001", "moneda": "USD"}},
        headers=BOT,
    )
    assert r.status_code == 409 and "F-001" in r.json()["error"]


def test_completar_factura_con_valor_invalido_da_422(con_proveedor: Any) -> None:
    cid = _incompleta()
    r = client.post(
        f"/api/bots/capturas/{cid}/completar", json={"cambios": {"monto_total": "-4"}}, headers=BOT
    )
    assert r.status_code == 422 and r.json()["faltan"] == ["monto_total"]


def test_un_proveedor_no_se_completa_por_api(sin_proveedor: Any) -> None:
    cid = client.post("/api/bots/capturas", json=_cuerpo("proveedor", PROV), headers=BOT).json()[
        "id"
    ]
    r = client.post(
        f"/api/bots/capturas/{cid}/completar", json={"cambios": {"nombre": "Otro"}}, headers=BOT
    )
    assert r.status_code == 422


# ── listado ──────────────────────────────────────────────────────────────────


def test_listar_filtra_por_los_kinds_nuevos(con_proveedor: Any) -> None:
    client.post("/api/bots/capturas", json=_cuerpo("proveedor", PROV, "a"), headers=BOT)
    client.post("/api/bots/capturas", json=_cuerpo("factura_proveedor", FACT, "b"), headers=BOT)
    with _como(ADMIN):
        assert client.get("/api/bots/capturas?kind=proveedor").json()["total"] == 1
        assert client.get("/api/bots/capturas?kind=factura_proveedor").json()["total"] == 1
        assert client.get("/api/bots/capturas?kind=nada").status_code == 422
