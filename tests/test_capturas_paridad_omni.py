"""Paridad con Omni: lo que Omni rechaza con 422, CxC también (y lo que acepta, lo acepta).

La FUENTE DE VERDAD de las reglas de los bots es Omni
(``omni-erp/backend/apps/bots_ingest/services/handlers.py``). ``tests/fixtures/paridad_omni.json``
es una tabla de borradores inválidos que se verificó contra los handlers reales de Omni (los 45
devuelven 422 antes de tocar la base). Si cambia una regla en Omni hay que:

1. actualizar ``cxc.capturas.modelos`` (``validar`` y compañía),
2. agregar el caso al JSON y comprobarlo contra Omni (los handlers validan antes de consultar la
   base, así que se pueden llamar con ``Ctx(empresa=None, …)``),
3. correr esta suite.

Un rechazo de fondo es un 422 ``{error, faltan}`` y NO deja captura.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cxc.capturas.modelos import CapturaInvalida, validar
from cxc.web.app import app
from cxc.web.capturas_api import get_servicio
from tests.capturas_dobles import EspejoFalso, OdooFalso
from tests.test_capturas_api import BOT, FUENTE, client
from tests.test_capturas_servicio import montar

_DATOS = json.loads(
    Path(__file__).with_name("fixtures").joinpath("paridad_omni.json").read_text("utf-8")
)
INVALIDOS = [pytest.param(c["kind"], c["draft"], id=c["id"]) for c in _DATOS["invalidos"]]
VALIDOS = [pytest.param(c["kind"], c["draft"], id=c["id"]) for c in _DATOS["validos"]]


def test_la_tabla_cubre_todos_los_tipos() -> None:
    assert {c["kind"] for c in _DATOS["invalidos"]} == {
        "contacto",
        "pedido",
        "pago",
        "pago_proveedor",
        "proveedor",
        "factura_proveedor",
    }
    assert len(_DATOS["invalidos"]) >= 40


@pytest.mark.parametrize("kind,draft", INVALIDOS)
def test_el_modelo_rechaza_lo_que_rechaza_omni(kind: str, draft: dict[str, Any]) -> None:
    with pytest.raises(CapturaInvalida) as e:
        validar(kind, draft)
    assert e.value.error


@pytest.mark.parametrize("kind,draft", INVALIDOS)
def test_el_servicio_rechaza_y_no_deja_captura(kind: str, draft: dict[str, Any]) -> None:
    svc, repo, odoo = montar(espejo=EspejoFalso())
    with pytest.raises(CapturaInvalida):
        svc.recibir(kind, draft, FUENTE, "k-paridad")
    assert repo.filas == {} and odoo.llamadas == []


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("BOT_API_KEY", "clave-de-prueba-del-bot")
    svc, repo, odoo = montar(OdooFalso(), EspejoFalso())
    app.dependency_overrides[get_servicio] = lambda: svc
    yield repo, odoo
    app.dependency_overrides.pop(get_servicio, None)


@pytest.mark.parametrize("kind,draft", INVALIDOS)
def test_la_api_responde_422_con_error_y_faltan_y_no_guarda(
    api: Any, kind: str, draft: dict[str, Any]
) -> None:
    repo, odoo = api
    body = {"kind": kind, "draft": draft, "source": FUENTE, "idempotency_key": "k-paridad"}
    r = client.post("/api/bots/capturas", json=body, headers=BOT)
    assert r.status_code == 422, r.text
    assert isinstance(r.json()["error"], str) and isinstance(r.json()["faltan"], list)
    assert repo.filas == {} and odoo.llamadas == []


@pytest.mark.parametrize("kind,draft", VALIDOS)
def test_lo_valido_para_omni_es_valido_para_cxc(kind: str, draft: dict[str, Any]) -> None:
    validar(kind, draft)  # no lanza


# ── casos puntuales que motivaron la corrección (reporte de QA, B1) ──────────


def test_pedido_sin_rif_correo_direccion_ni_ubicacion_lista_todos_los_faltantes() -> None:
    with pytest.raises(CapturaInvalida) as e:
        validar("pedido", {"cliente": "T", "lineas": [{"producto": "A", "cantidad": "1"}]})
    assert e.value.faltan == ["rif", "email", "direccion_fiscal", "ubicacion"]


def test_entrega_igual_a_la_fiscal_o_direccion_de_entrega_bastan() -> None:
    base = next(c["draft"] for c in _DATOS["validos"] if c["id"] == "pedido")
    assert validar("pedido", base) == []
    sin_flag = {k: v for k, v in base.items() if k != "entrega_igual_fiscal"}
    assert validar("pedido", {**sin_flag, "direccion_entrega": "Galpón 3"}) == []
    with pytest.raises(CapturaInvalida) as e:
        validar("pedido", sin_flag)
    assert e.value.faltan == ["direccion_entrega"]


@pytest.mark.parametrize(
    "ub",
    [
        {"latitud": -90, "longitud": 180},
        {"latitud": 90, "longitud": -180},
        {"latitud": "10.5", "longitud": "-66.9"},
    ],
)
def test_los_limites_del_gps_son_validos(ub: dict[str, Any]) -> None:
    base = next(c["draft"] for c in _DATOS["validos"] if c["id"] == "contacto")
    assert validar("contacto", {**base, "ubicacion": ub}) == []


@pytest.mark.parametrize(
    "ub",
    [{"latitud": "NaN", "longitud": 0}, {"latitud": 0, "longitud": "Infinity"}, "10,20", [1, 2], 5],
)
def test_gps_con_valores_no_finitos_o_de_otro_tipo_se_rechaza(ub: Any) -> None:
    base = next(c["draft"] for c in _DATOS["validos"] if c["id"] == "contacto")
    with pytest.raises(CapturaInvalida):
        validar("contacto", {**base, "ubicacion": ub})


@pytest.mark.parametrize("dias", [0, 30, 365, "0", "90"])
def test_dias_de_credito_validos(dias: Any) -> None:
    base = next(c["draft"] for c in _DATOS["validos"] if c["id"] == "proveedor")
    assert validar("proveedor", {**base, "dias_credito": dias}) == []
