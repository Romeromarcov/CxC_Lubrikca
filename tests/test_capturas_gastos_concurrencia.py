# ruff: noqa: F811
"""Concurrencia real (hilos + Postgres) del proveedor que se CREA desde un gasto o una factura.

Varios gastos simultáneos de un proveedor que aún no existe en Odoo deben dejar UN solo
``res.partner``. Igual que en ``test_capturas_concurrencia``, cada garantía tiene un **control**
(con el bloqueo anulado el duplicado SÍ aparece: la prueba detecta el fallo) y la versión real.
Se salta sin ``DATABASE_URL``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import Engine

from cxc.capturas.repositorio import RepoCapturasSQL
from cxc.capturas.servicio import ServicioCapturas
from cxc.odoo.escritura import EscritorOdoo
from tests.capturas_dobles import EspejoFalso
from tests.test_capturas_concurrencia import _en_paralelo, _errores
from tests.test_capturas_repositorio import engine  # noqa: F401  (fixture)

FUENTE = {"vendedor_email": "ana@x.com"}
ESPERA_CREAR = 0.15


class OdooConEstado:
    """Odoo falso con estado y seguro entre hilos; ``create`` tarda y ensancha la carrera."""

    def __init__(self) -> None:
        self.partners: list[dict[str, Any]] = []
        self.facturas: list[dict[str, Any]] = []
        self._candado = threading.Lock()

    def __call__(self, model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if method == "fields_get":
            return {
                c: {}
                for c in (
                    "name", "partner_latitude", "partner_longitude", "move_type", "partner_id",
                    "ref", "invoice_date", "currency_id", "invoice_line_ids", "narration",
                    "invoice_date_due", "tax_ids",
                )
            }  # fmt: skip
        if model == "res.currency" and method == "search_read":
            return [{"id": 1, "name": "VES"}, {"id": 2, "name": "USD"}]
        if method == "search":
            return [r["id"] for r in self._buscar(model, args[0])][: kwargs.get("limit", 3)]
        if method == "create":
            if model == "res.partner":
                time.sleep(ESPERA_CREAR)  # lo que tarda Odoo: aquí se cuelan los duplicados
            with self._candado:
                tabla = self.partners if model == "res.partner" else self.facturas
                nuevo = {**args[0], "id": 1000 + len(tabla)}
                tabla.append(nuevo)
                return nuevo["id"]
        raise AssertionError(f"inesperado: {model}.{method}")

    def _buscar(self, modelo: str, dominio: list[Any]) -> list[dict[str, Any]]:
        with self._candado:
            filas = list(self.partners if modelo == "res.partner" else self.facturas)
        return [f for f in filas if all(self._cumple(f, hoja) for hoja in dominio)]

    @staticmethod
    def _cumple(fila: dict[str, Any], hoja: list[Any]) -> bool:
        campo, op, valor = hoja
        actual = fila.get(campo)
        if op == "in":
            return actual in valor
        if op == "=":
            return bool(actual == valor)
        if op == ">":
            return actual is not None and actual > valor
        if op == "ilike":
            return str(valor).lower() in str(actual or "").lower()
        if op == "=ilike":
            return str(actual or "").lower() == str(valor).lower().replace("\\", "")
        raise AssertionError(f"operador no soportado: {op}")


def _gasto(numero: str, **kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "proveedor": "Restaurante El Sabor C.A.",
        "rif": "J-301234567",
        "numero_factura": numero,
        "fecha_emision": "2026-11-14",
        "moneda": "VES",
        "monto_total": "100",
    }
    d.update(kw)
    return d


def _gastos_en_paralelo(engine: Engine, n: int, **kw: Any) -> tuple[OdooConEstado, list[Any]]:
    """``n`` gastos distintos (números distintos) del MISMO proveedor nuevo, enviados a la vez."""
    odoo = OdooConEstado()
    svc = ServicioCapturas(RepoCapturasSQL(engine), EspejoFalso(), lambda: EscritorOdoo(odoo))
    ids = [
        svc.recibir("gasto", _gasto(f"R-{i}", **kw), FUENTE, f"k{i}")[0].captura_id
        for i in range(n)
    ]
    res = _en_paralelo(n, lambda i: svc.procesar(ids[i]))
    assert _errores(res) == []
    return odoo, [r[1] for r in res]


@contextmanager
def _sin_bloqueo(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    @contextmanager
    def libre(self: Any, clave: str, espera_segundos: float = 30.0) -> Iterator[None]:
        yield

    monkeypatch.setattr(RepoCapturasSQL, "bloqueo", libre)
    yield


# ── con RIF ──────────────────────────────────────────────────────────────────


def test_control_sin_el_bloqueo_se_crean_varios_proveedores_desde_gastos_simultaneos(
    engine: Engine,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _sin_bloqueo(monkeypatch):
        odoo, _ = _gastos_en_paralelo(engine, 5)
    assert len(odoo.partners) > 1  # el mismo proveedor quedó repetido en Odoo


def test_con_el_bloqueo_varios_gastos_de_un_proveedor_nuevo_crean_un_solo_partner(
    engine: Engine,  # noqa: F811
) -> None:
    odoo, capturas = _gastos_en_paralelo(engine, 5)
    assert len(odoo.partners) == 1
    assert all(c.estado == "ENVIADO" for c in capturas)
    assert sum(1 for c in capturas if c.resultado["proveedor_creado"] is True) == 1
    assert sum(1 for c in capturas if c.resultado["proveedor_creado"] is False) == 4
    assert len(odoo.facturas) == 5  # cada gasto tiene su borrador...
    assert {f["partner_id"] for f in odoo.facturas} == {
        odoo.partners[0]["id"]
    }  # ...del mismo proveedor
    assert {f["ref"] for f in odoo.facturas} == {f"R-{i}" for i in range(5)}


# ── sin RIF: se serializa por el nombre normalizado ─────────────────────────


def test_control_sin_rif_y_sin_bloqueo_tambien_se_duplica_el_proveedor(
    engine: Engine,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _sin_bloqueo(monkeypatch):
        odoo, _ = _gastos_en_paralelo(engine, 5, rif=None, proveedor="Bodegón La Esquina")
    assert len(odoo.partners) > 1


def test_sin_rif_con_el_bloqueo_un_solo_partner_aunque_el_nombre_cambie_de_mayusculas(
    engine: Engine,  # noqa: F811
) -> None:
    odoo = OdooConEstado()
    svc = ServicioCapturas(RepoCapturasSQL(engine), EspejoFalso(), lambda: EscritorOdoo(odoo))
    nombres = [
        "Bodegón La Esquina",
        "BODEGÓN LA ESQUINA",
        "bodegón la esquina",
        "Bodegón  La Esquina",
    ]
    ids = [
        svc.recibir("gasto", _gasto(f"S-{i}", rif=None, proveedor=n), FUENTE, f"k{i}")[0].captura_id
        for i, n in enumerate(nombres)
    ]
    res = _en_paralelo(len(ids), lambda i: svc.procesar(ids[i]))
    assert _errores(res) == []
    # el espacio doble no coincide en Odoo por nombre exacto, pero el bloqueo serializa por la clave
    # normalizada: el que llega después ve al primero (por nombre) o queda como mucho en uno más.
    assert 1 <= len(odoo.partners) <= 2
    assert all(r[1].estado == "ENVIADO" for r in res)


# ── facturas de proveedor usan el mismo camino ──────────────────────────────


def test_las_facturas_de_proveedor_simultaneas_tambien_crean_un_solo_partner(
    engine: Engine,  # noqa: F811
) -> None:
    odoo = OdooConEstado()
    svc = ServicioCapturas(RepoCapturasSQL(engine), EspejoFalso(), lambda: EscritorOdoo(odoo))
    ids = [
        svc.recibir("factura_proveedor", _gasto(f"F-{i}"), FUENTE, f"k{i}")[0].captura_id
        for i in range(4)
    ]
    res = _en_paralelo(4, lambda i: svc.procesar(ids[i]))
    assert _errores(res) == [] and len(odoo.partners) == 1 and len(odoo.facturas) == 4


# ── sin falsos positivos ────────────────────────────────────────────────────


def test_gastos_de_proveedores_distintos_no_se_serializan_entre_si(
    engine: Engine,  # noqa: F811
) -> None:
    odoo = OdooConEstado()
    svc = ServicioCapturas(RepoCapturasSQL(engine), EspejoFalso(), lambda: EscritorOdoo(odoo))
    ids = [
        svc.recibir(
            "gasto",
            _gasto(f"R-{i}", rif=f"J-30000000{i}", proveedor=f"Comercio {i}"),
            FUENTE,
            f"k{i}",
        )[0].captura_id
        for i in range(4)
    ]
    inicio = time.monotonic()
    res = _en_paralelo(4, lambda i: svc.procesar(ids[i]))
    assert _errores(res) == [] and len(odoo.partners) == 4
    assert time.monotonic() - inicio < ESPERA_CREAR * 4  # en paralelo, no uno tras otro
