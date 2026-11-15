"""Facturas de proveedor y búsqueda por clave contra Postgres real (sin DATABASE_URL se salta)."""

from __future__ import annotations

from sqlalchemy import Engine

from cxc.capturas.repositorio import RepoCapturasSQL
from tests.test_capturas_repositorio import _crear, engine  # noqa: F401  (fixture)


def test_obtener_por_clave(engine: Engine) -> None:  # noqa: F811
    repo = RepoCapturasSQL(engine)
    c, _ = _crear(repo, "clave-x")
    assert repo.obtener_por_clave("clave-x") is not None
    assert repo.obtener_por_clave("clave-x").captura_id == c.captura_id  # type: ignore[union-attr]
    assert repo.obtener_por_clave("no-existe") is None


def test_facturas_con_numero_sin_distinguir_mayusculas_y_solo_de_ese_tipo(engine: Engine) -> None:  # noqa: F811
    repo = RepoCapturasSQL(engine)
    f1, _ = _crear(
        repo, "a", kind="factura_proveedor", payload={"numero_factura": "F-001", "rif": "J-1"}
    )
    f2, _ = _crear(
        repo, "b", kind="factura_proveedor", payload={"numero_factura": "f-001", "rif": "J-2"}
    )
    _crear(repo, "c", kind="factura_proveedor", payload={"numero_factura": "F-002"})
    _crear(repo, "d", kind="pago", payload={"numero_factura": "F-001"})  # otro tipo: no cuenta
    _crear(repo, "e", kind="factura_proveedor", payload={"proveedor": "sin número"})
    ids = [c.captura_id for c in repo.facturas_con_numero("F-001")]
    assert ids == [f1.captura_id, f2.captura_id]
    assert repo.facturas_con_numero("F-999") == []


def test_facturas_con_numero_tolera_caracteres_especiales(engine: Engine) -> None:  # noqa: F811
    repo = RepoCapturasSQL(engine)
    f, _ = _crear(repo, "a", kind="factura_proveedor", payload={"numero_factura": "A/12'%_ñ"})
    assert [c.captura_id for c in repo.facturas_con_numero("a/12'%_Ñ")] == [f.captura_id]


def test_reenvio_con_la_misma_clave_contra_postgres_real(engine: Engine) -> None:  # noqa: F811
    """Servicio + repositorio SQL: corrige lo pendiente y no pisa lo enviado."""
    from cxc.capturas.servicio import ServicioCapturas
    from tests.capturas_dobles import EspejoFalso, contacto_ok

    repo = RepoCapturasSQL(engine)
    svc = ServicioCapturas(repo, EspejoFalso(), lambda: None)
    fuente = {"vendedor_email": "ana@x.com"}
    c, creada = svc.recibir("contacto", contacto_ok(), fuente, "k-real")
    assert creada and c.estado == "PENDIENTE"

    c2, creada2, actualizada = svc.recibir_detallado(
        "contacto",
        contacto_ok(nombre="Pedro Pérez", ubicacion={"latitud": 1.5, "longitud": 2.5}),
        fuente,
        "k-real",
    )
    assert (creada2, actualizada) == (False, True) and c2.captura_id == c.captura_id
    leido = repo.obtener(c.captura_id)
    assert leido is not None and leido.payload["nombre"] == "Pedro Pérez"
    assert leido.payload["ubicacion"] == {"latitud": 1.5, "longitud": 2.5}
    eventos = repo.eventos(c.captura_id)
    assert [e["evento"] for e in eventos] == ["recibida", "actualizada_por_reenvio"]
    assert eventos[-1]["detalle"] == "nombre, ubicacion" and eventos[-1]["actor"] == "ana@x.com"

    _, _, otra_vez = svc.recibir_detallado("contacto", c2.payload, fuente, "k-real")
    assert otra_vez is False  # mismo contenido: idempotente

    repo.actualizar(c.captura_id, estado="ENVIADO", odoo_ref="res.partner:9")
    c3, _, pisada = svc.recibir_detallado("contacto", contacto_ok(nombre="Otro"), fuente, "k-real")
    assert pisada is False and c3.payload["nombre"] == "Pedro Pérez" and c3.estado == "ENVIADO"
    assert len(repo.listar(estado=None, kind=None, limit=10, offset=0)[0]) == 1
