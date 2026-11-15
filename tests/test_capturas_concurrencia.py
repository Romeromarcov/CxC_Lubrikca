# ruff: noqa: F811
"""Concurrencia real (hilos + Postgres): los duplicados lógicos no se cuelan en paralelo.

Reproducción del hallazgo de QA: 6 envíos simultáneos de la MISMA factura con claves de
idempotencia DISTINTAS dejaban 5 capturas PENDIENTE en CxC. La comprobación «¿ya está
capturada?» y el alta eran pasos separados: todos veían «no hay duplicado» y todos insertaban.

Cada garantía se prueba dos veces:

- **control**: con el bloqueo anulado (y la ventana entre comprobar y escribir ensanchada con una
  barrera) los duplicados SÍ se cuelan → demuestra que la prueba detecta el fallo;
- **con el bloqueo real**: aun con la misma ventana ensanchada, solo entra una.

Se salta sin ``DATABASE_URL`` (como el resto de tests de repositorio).
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import Engine, text

from cxc.capturas import repositorio
from cxc.capturas.repositorio import BloqueoOcupado, RepoCapturasSQL, clave_factura
from cxc.capturas.servicio import FacturaDuplicada, ServicioCapturas
from cxc.odoo.escritura import EscritorOdoo
from tests.capturas_dobles import EspejoFalso, OdooFalso, RepoMemoria
from tests.test_capturas_repositorio import engine  # noqa: F401  (fixture)

FUENTE = {"vendedor_email": "ana@x.com"}


def _factura(numero: str = "F-001", **kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "proveedor": "Lubricantes SA",
        "rif": "J-123456789",
        "numero_factura": numero,
        "fecha_emision": "2026-11-10",
        "moneda": "USD",
        "monto_total": "116",
    }
    d.update(kw)
    return d


def _servicio(engine: Engine) -> ServicioCapturas:
    return ServicioCapturas(RepoCapturasSQL(engine), EspejoFalso(), lambda: None)


def _en_paralelo(n: int, tarea: Callable[[int], Any]) -> list[tuple[str, Any]]:
    """Lanza ``n`` hilos que arrancan a la vez y devuelve ``("ok", valor)`` o ``("error", exc)``."""
    salida: list[tuple[str, Any]] = [("error", None)] * n
    partida = threading.Barrier(n)

    def corre(i: int) -> None:
        partida.wait(timeout=20)
        try:
            salida[i] = ("ok", tarea(i))
        except Exception as e:
            salida[i] = ("error", e)

    hilos = [threading.Thread(target=corre, args=(i,)) for i in range(n)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=90)
        assert not h.is_alive(), "un hilo quedó colgado"
    return salida


def _errores(res: list[tuple[str, Any]]) -> list[Any]:
    return [v for tipo, v in res if tipo == "error"]


def _ensanchar_la_ventana(
    monkeypatch: pytest.MonkeyPatch, n: int, espera: float, sin_bloqueo: bool
) -> None:
    """Hace que los ``n`` envíos coincidan entre «comprobar» y «escribir».

    ``sin_bloqueo=True`` anula además el bloqueo asesor (el control del test). Con el bloqueo real
    solo un envío llega a la barrera a la vez, así que la barrera se rompe por tiempo y sigue.
    """
    barrera = threading.Barrier(n)

    def antes(conn: Any) -> None:
        with contextlib.suppress(threading.BrokenBarrierError):
            barrera.wait(timeout=espera)

    monkeypatch.setattr(repositorio, "_antes_de_escribir", antes)
    if sin_bloqueo:
        monkeypatch.setattr(repositorio, "_bloquear_xact", lambda conn, clave: None)


# ── alta de facturas ─────────────────────────────────────────────────────────


def test_control_sin_el_bloqueo_se_cuelan_las_facturas_duplicadas(
    engine: Engine,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduce el bug: 6 envíos simultáneos de la misma factura, claves distintas."""
    _ensanchar_la_ventana(monkeypatch, 6, espera=10, sin_bloqueo=True)
    svc = _servicio(engine)
    res = _en_paralelo(6, lambda i: svc.recibir("factura_proveedor", _factura(), FUENTE, f"k{i}"))
    assert _errores(res) == []  # nadie vio un duplicado...
    assert (
        len(svc._repo.facturas_con_numero("F-001")) == 6
    )  # ...y quedaron 6 capturas de la misma factura


def test_con_el_bloqueo_solo_entra_una_aun_con_la_ventana_ensanchada(
    engine: Engine,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensanchar_la_ventana(monkeypatch, 6, espera=1, sin_bloqueo=False)
    svc = _servicio(engine)
    res = _en_paralelo(6, lambda i: svc.recibir("factura_proveedor", _factura(), FUENTE, f"k{i}"))
    errores = _errores(res)
    assert len(errores) == 5 and all(isinstance(e, FacturaDuplicada) for e in errores)
    assert len([r for r in res if r[0] == "ok" and r[1][1] is True]) == 1  # una sola creada
    assert len(svc._repo.facturas_con_numero("F-001")) == 1
    assert "F-001" in str(errores[0]) and "ya fue capturada" in str(errores[0])


def test_carrera_natural_sin_ganchos_varias_rondas(engine: Engine) -> None:  # noqa: F811
    """Sin instrumentar nada: 8 hilos por ronda, 6 rondas, siempre exactamente una factura."""
    svc = _servicio(engine)
    for ronda in range(6):
        numero = f"R-{ronda}"
        res = _en_paralelo(
            8,
            lambda i, numero=numero, ronda=ronda: svc.recibir(
                "factura_proveedor", _factura(numero), FUENTE, f"r{ronda}-{i}"
            ),
        )
        assert all(isinstance(e, FacturaDuplicada) for e in _errores(res))
        assert len(_errores(res)) == 7, f"ronda {ronda}: se coló un duplicado"
        assert len(svc._repo.facturas_con_numero(numero)) == 1


def test_el_numero_se_compara_sin_mayusculas_ni_espacios_en_paralelo(engine: Engine) -> None:  # noqa: F811
    svc = _servicio(engine)
    variantes = ["F-9", " f-9 ", "F-9 ", "f-9"]
    res = _en_paralelo(
        4,
        lambda i: svc.recibir("factura_proveedor", _factura(variantes[i]), FUENTE, f"k{i}"),
    )
    assert len(_errores(res)) == 3 and len(svc._repo.facturas_con_numero("F-9")) == 1


def test_mismo_proveedor_por_nombre_cuando_falta_el_rif_tambien_se_serializa(
    engine: Engine,  # noqa: F811
) -> None:
    svc = _servicio(engine)
    sin_rif = {k: v for k, v in _factura().items() if k != "rif"}
    res = _en_paralelo(5, lambda i: svc.recibir("factura_proveedor", sin_rif, FUENTE, f"k{i}"))
    assert len(_errores(res)) == 4 and len(svc._repo.facturas_con_numero("F-001")) == 1


def test_facturas_distintas_en_paralelo_no_se_bloquean_entre_si(engine: Engine) -> None:  # noqa: F811
    """Sin falsos positivos: números distintos, o proveedores distintos con el mismo número."""
    svc = _servicio(engine)
    distintos = _en_paralelo(
        6, lambda i: svc.recibir("factura_proveedor", _factura(f"D-{i}"), FUENTE, f"d{i}")
    )
    assert _errores(distintos) == []
    otros_proveedores = _en_paralelo(
        6,
        lambda i: svc.recibir(
            "factura_proveedor",
            _factura("MISMO", rif=f"J-10000000{i}", proveedor=f"Proveedor {i}"),
            FUENTE,
            f"p{i}",
        ),
    )
    assert _errores(otros_proveedores) == []
    assert len(svc._repo.facturas_con_numero("MISMO")) == 6


def test_el_mismo_mensaje_reenviado_en_paralelo_es_idempotente_no_duplicado(
    engine: Engine,  # noqa: F811
) -> None:
    """Misma idempotency_key a la vez: una creada y el resto recibe la existente (no un 409)."""
    svc = _servicio(engine)
    res = _en_paralelo(
        6, lambda i: svc.recibir_detallado("factura_proveedor", _factura(), FUENTE, "misma")
    )
    assert _errores(res) == []
    assert len({r[1][0].captura_id for r in res}) == 1
    assert sum(1 for r in res if r[1][1]) == 1  # exactamente una ``creada``
    assert len(svc._repo.facturas_con_numero("F-001")) == 1


# ── corrección por reenvío / completar ───────────────────────────────────────


def _dos_facturas(svc: ServicioCapturas) -> None:
    svc.recibir("factura_proveedor", _factura("F-A"), FUENTE, "ka")
    svc.recibir("factura_proveedor", _factura("F-B"), FUENTE, "kb")


def test_control_dos_correcciones_simultaneas_al_mismo_numero_chocan_sin_bloqueo(
    engine: Engine,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _servicio(engine)
    _dos_facturas(svc)
    _ensanchar_la_ventana(monkeypatch, 2, espera=10, sin_bloqueo=True)
    claves = ["ka", "kb"]
    res = _en_paralelo(
        2,
        lambda i: svc.recibir_detallado(
            "factura_proveedor", _factura("F-NUEVA"), FUENTE, claves[i]
        ),
    )
    assert _errores(res) == []
    assert (
        len(svc._repo.facturas_con_numero("F-NUEVA")) == 2
    )  # las dos quedaron con el mismo número


def test_con_el_bloqueo_solo_una_correccion_gana(
    engine: Engine,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _servicio(engine)
    _dos_facturas(svc)
    _ensanchar_la_ventana(monkeypatch, 2, espera=1, sin_bloqueo=False)
    claves = ["ka", "kb"]
    res = _en_paralelo(
        2,
        lambda i: svc.recibir_detallado(
            "factura_proveedor", _factura("F-NUEVA"), FUENTE, claves[i]
        ),
    )
    errores = _errores(res)
    assert len(errores) == 1 and isinstance(errores[0], FacturaDuplicada)
    assert len(svc._repo.facturas_con_numero("F-NUEVA")) == 1
    # la perdedora conserva su número anterior (no quedó a medias)
    assert (
        len(svc._repo.facturas_con_numero("F-A")) + len(svc._repo.facturas_con_numero("F-B")) == 1
    )


def test_completar_en_paralelo_con_el_mismo_numero_no_duplica(engine: Engine) -> None:  # noqa: F811
    svc = _servicio(engine)
    base = {k: v for k, v in _factura().items() if k != "numero_factura"}
    ids = [svc.recibir("factura_proveedor", base, FUENTE, f"k{i}")[0].captura_id for i in range(4)]
    res = _en_paralelo(
        4,
        lambda i: svc.completar(
            ids[i], {"numero_factura": "COMPLETA-1"}, actor="bot", enviar=False
        ),
    )
    assert len(_errores(res)) == 3 and all(isinstance(e, FacturaDuplicada) for e in _errores(res))
    assert len(svc._repo.facturas_con_numero("COMPLETA-1")) == 1


# ── el bloqueo en sí ─────────────────────────────────────────────────────────


def _bloqueos_asesores(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")
            ).scalar_one()
        )


def test_si_el_bloqueo_esta_ocupado_se_rinde_con_bloqueo_ocupado_y_luego_funciona(
    engine: Engine,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repositorio, "ESPERA_BLOQUEO_MS", 300)
    repo = RepoCapturasSQL(engine)
    svc = ServicioCapturas(repo, EspejoFalso(), lambda: None)
    with repo.bloqueo("ajeno"):  # otro proceso tiene ocupado el bloqueo de esa factura
        pass
    llave = repositorio._clave_bloqueo(clave_factura("F-OCUPADA"))
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as otro:
        otro.execute(text("SELECT pg_advisory_lock(:k)"), {"k": llave})
        try:
            t0 = time.monotonic()
            with pytest.raises(BloqueoOcupado):
                svc.recibir("factura_proveedor", _factura("F-OCUPADA"), FUENTE, "k1")
            assert 0.2 < time.monotonic() - t0 < 5  # esperó lo configurado y no se colgó
            assert repo.obtener_por_clave("k1") is None  # no quedó nada a medias
        finally:
            otro.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": llave})
    capt, creada = svc.recibir("factura_proveedor", _factura("F-OCUPADA"), FUENTE, "k1")
    assert creada and capt.estado == "PENDIENTE"


def test_el_bloqueo_de_sesion_excluye_se_libera_y_no_se_filtra(engine: Engine) -> None:  # noqa: F811
    repo = RepoCapturasSQL(engine)
    base = _bloqueos_asesores(engine)
    dentro: list[str] = []
    with repo.bloqueo("clave-x"):
        assert _bloqueos_asesores(engine) == base + 1
        with pytest.raises(BloqueoOcupado), repo.bloqueo("clave-x", espera_segundos=0.3):
            dentro.append("no debería entrar")
        with repo.bloqueo("otra-clave"):  # claves distintas no se estorban
            pass
    assert dentro == [] and _bloqueos_asesores(engine) == base
    with repo.bloqueo("clave-x", espera_segundos=0.3):  # liberado: se puede volver a tomar
        pass


def test_el_bloqueo_se_libera_aunque_el_cuerpo_falle(engine: Engine) -> None:  # noqa: F811
    repo = RepoCapturasSQL(engine)
    base = _bloqueos_asesores(engine)
    with pytest.raises(RuntimeError), repo.bloqueo("clave-y"):
        raise RuntimeError("falló el envío")
    assert _bloqueos_asesores(engine) == base
    with repo.bloqueo("clave-y", espera_segundos=0.3):
        pass


def test_tras_las_operaciones_no_quedan_bloqueos_asesores(engine: Engine) -> None:  # noqa: F811
    svc = _servicio(engine)
    base = _bloqueos_asesores(engine)
    _en_paralelo(6, lambda i: svc.recibir("factura_proveedor", _factura(), FUENTE, f"k{i}"))
    assert _bloqueos_asesores(engine) == base  # los de transacción se sueltan solos al commit


# ── proveedor: creación en Odoo serializada por RIF ──────────────────────────


class OdooConPartners:
    """Odoo falso CON ESTADO y seguro entre hilos: ``create`` tarda y ensancha la carrera."""

    def __init__(self) -> None:
        self.partners: list[dict[str, Any]] = []
        self._candado = threading.Lock()

    def __call__(self, model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        if model != "res.partner":
            raise AssertionError(f"inesperado: {model}.{method}")
        if method == "fields_get":
            return {"name": {}, "partner_latitude": {}, "partner_longitude": {}}
        if method == "search":
            return [p["id"] for p in self._buscar(args[0])]
        if method == "create":
            time.sleep(0.15)  # lo que tarda Odoo: aquí se cuelan los duplicados sin bloqueo
            with self._candado:
                nuevo = {**args[0], "id": 1000 + len(self.partners)}
                self.partners.append(nuevo)
                return nuevo["id"]
        raise AssertionError(f"inesperado: {model}.{method}")

    def _buscar(self, dominio: list[Any]) -> list[dict[str, Any]]:
        with self._candado:
            vivos = list(self.partners)
        campo, operador, valor = dominio[0]
        if operador == "in":
            return [p for p in vivos if p.get(campo) in valor]
        if operador == "=":
            return [p for p in vivos if p.get(campo) == valor]
        return [p for p in vivos if str(p.get(campo, "")).lower() == str(valor).lower()]


def _proveedores_en_paralelo(engine: Engine, n: int) -> tuple[OdooConPartners, list[Any]]:
    odoo = OdooConPartners()
    repo = RepoCapturasSQL(engine)
    svc = ServicioCapturas(repo, EspejoFalso(), lambda: EscritorOdoo(odoo))
    prov = {"nombre": "Lubricantes SA", "rif": "J-123456789", "telefono": "0414-1234567"}
    ids = [svc.recibir("proveedor", prov, FUENTE, f"k{i}")[0].captura_id for i in range(n)]
    res = _en_paralelo(n, lambda i: svc.procesar(ids[i]))
    assert _errores(res) == []
    return odoo, [r[1] for r in res]


def test_control_sin_el_bloqueo_se_crean_varios_proveedores_en_odoo(
    engine: Engine,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def sin_bloqueo(self: Any, clave: str, espera_segundos: float = 30.0) -> Iterator[None]:
        yield

    monkeypatch.setattr(RepoCapturasSQL, "bloqueo", sin_bloqueo)
    odoo, _ = _proveedores_en_paralelo(engine, 5)
    assert len(odoo.partners) > 1  # el mismo RIF quedó repetido en Odoo


def test_con_el_bloqueo_se_crea_un_solo_proveedor_y_el_resto_ya_existia(
    engine: Engine,  # noqa: F811
) -> None:
    odoo, capturas = _proveedores_en_paralelo(engine, 5)
    assert len(odoo.partners) == 1
    assert all(c.estado == "ENVIADO" for c in capturas)
    assert sum(1 for c in capturas if c.resultado["ya_existia"] is False) == 1
    assert sum(1 for c in capturas if c.resultado["ya_existia"] is True) == 4
    assert {c.odoo_ref for c in capturas} == {f"res.partner:{odoo.partners[0]['id']}"}


def test_proveedores_con_rif_distinto_no_se_serializan_entre_si(engine: Engine) -> None:  # noqa: F811
    odoo = OdooConPartners()
    svc = ServicioCapturas(RepoCapturasSQL(engine), EspejoFalso(), lambda: EscritorOdoo(odoo))
    ids = [
        svc.recibir(
            "proveedor",
            {"nombre": f"Prov {i}", "rif": f"J-20000000{i}", "telefono": "0414-1234567"},
            FUENTE,
            f"k{i}",
        )[0].captura_id
        for i in range(4)
    ]
    inicio = time.monotonic()
    res = _en_paralelo(4, lambda i: svc.procesar(ids[i]))
    assert _errores(res) == [] and len(odoo.partners) == 4
    assert time.monotonic() - inicio < 0.15 * 4  # corrieron en paralelo, no uno tras otro


def test_si_el_bloqueo_del_proveedor_no_se_logra_queda_en_error_transitorio_para_reintento(
    engine: Engine,  # noqa: F811
) -> None:
    repo = RepoCapturasSQL(engine)
    odoo = OdooConPartners()

    class Impaciente(RepoCapturasSQL):
        def bloqueo(self, clave: str, espera_segundos: float = 30.0) -> Any:
            return super().bloqueo(clave, espera_segundos=0.2)

    svc = ServicioCapturas(Impaciente(engine), EspejoFalso(), lambda: EscritorOdoo(odoo))
    prov = {"nombre": "Lubricantes SA", "rif": "J-123456789", "telefono": "0414-1234567"}
    cid = svc.recibir("proveedor", prov, FUENTE, "k1")[0].captura_id
    with repo.bloqueo("proveedor:J-123456789"):  # otro envío del mismo RIF en curso
        ocupado = svc.procesar(cid)
    assert ocupado.estado == "ERROR" and ocupado.resultado["transitorio"] is True
    assert "se reintentará" in ocupado.error and odoo.partners == []
    assert svc.reintentar_pendientes() == 1  # liberado el bloqueo, el reintento automático lo envía
    assert repo.obtener(cid).estado == "ENVIADO"  # type: ignore[union-attr]


# ── API: bloqueo ocupado → 503 ───────────────────────────────────────────────


def test_la_api_responde_503_si_el_bloqueo_esta_ocupado(monkeypatch: pytest.MonkeyPatch) -> None:
    from cxc.web.app import app
    from cxc.web.capturas_api import get_servicio
    from tests.test_capturas_api import BOT, client
    from tests.test_capturas_api import FUENTE as FUENTE_API

    class Ocupado(RepoMemoria):
        def crear_factura(self, **datos: Any) -> Any:
            raise BloqueoOcupado("x")

        def actualizar_factura(self, captura_id: int, **datos: Any) -> Any:
            raise BloqueoOcupado("x")

    monkeypatch.setenv("BOT_API_KEY", "clave-de-prueba-del-bot")
    repo = Ocupado()
    svc = ServicioCapturas(repo, EspejoFalso(), lambda: EscritorOdoo(OdooFalso()))
    app.dependency_overrides[get_servicio] = lambda: svc
    try:
        cuerpo = {
            "kind": "factura_proveedor",
            "draft": _factura(),
            "source": FUENTE_API,
            "idempotency_key": "k1",
        }
        r = client.post("/api/bots/capturas", json=cuerpo, headers=BOT)
        assert r.status_code == 503 and "reintenta" in r.json()["error"]
        # completar una factura incompleta cuyo número choca con el bloqueo ocupado
        inc, _ = RepoMemoria.crear(
            repo,
            kind="factura_proveedor",
            idempotency_key="ki",
            payload={"proveedor": "L"},
            fuente={},
            auto=False,
            estado="INCOMPLETO",
            incompleto=["numero_factura"],
        )
        r2 = client.post(
            f"/api/bots/capturas/{inc.captura_id}/completar",
            json={
                "cambios": {
                    "numero_factura": "F-9",
                    "moneda": "USD",
                    "monto_total": "5",
                    "fecha_emision": "2026-11-10",
                }
            },
            headers=BOT,
        )
        assert r2.status_code == 503
    finally:
        app.dependency_overrides.pop(get_servicio, None)
