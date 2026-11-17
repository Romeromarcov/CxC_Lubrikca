"""Gastos (compras menores ya pagadas) y el proveedor que se busca o se CREA desde el documento.

Decisión del owner (nov-2026): el grupo de Compras es para compras menores del día a día. El bot
lee el proveedor del propio documento; si no existe en Odoo, CxC lo crea (solo borrador). Un gasto
no genera cuenta por pagar ni pago: va como factura de proveedor en borrador con la nota de que
se pagó al contado.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from cxc.bots.modelos import KINDS_POR_BOT
from cxc.capturas.modelos import (
    CAMPOS_COMPLETABLES,
    ENVIADO,
    ERROR,
    GASTO,
    INCOMPLETO,
    KINDS,
    PENDIENTE,
    CapturaInvalida,
    normalizar_nombre,
    validar,
)
from cxc.capturas.servicio import FacturaDuplicada, NoCompletable
from cxc.odoo.escritura import EscritorOdoo
from cxc.web.app import app
from cxc.web.capturas_api import get_servicio
from tests.capturas_dobles import EspejoFalso, OdooFalso, RepoMemoria, partners
from tests.test_capturas_api import BOT, client
from tests.test_capturas_servicio import FUENTE, montar


def _gasto(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "proveedor": "Restaurante El Sabor C.A.",
        "rif": "J-301234567",
        "numero_factura": "R-0045",
        "fecha_emision": "2026-11-14",
        "moneda": "VES",
        "monto_total": "1250.50",
    }
    base.update(kw)
    return base


# ── modelo ───────────────────────────────────────────────────────────────────


def test_gasto_es_un_kind_completable_y_de_compras() -> None:
    assert GASTO in KINDS and GASTO in CAMPOS_COMPLETABLES
    assert "gasto" in KINDS_POR_BOT["compras"]
    assert {"categoria", "proveedor_direccion", "proveedor_telefono", "proveedor_email"} <= (
        CAMPOS_COMPLETABLES[GASTO]
    )


def test_gasto_valido_sin_faltantes_y_con_faltantes_tolerables() -> None:
    assert validar(GASTO, _gasto()) == []
    # el número de factura y el RIF son opcionales en un gasto
    minimo = {
        "proveedor": "Bodegón",
        "fecha_emision": "2026-11-14",
        "moneda": "USD",
        "monto_total": "8",
    }
    assert validar(GASTO, minimo) == []
    assert validar(GASTO, {"fecha_emision": "2026-11-14"}) == ["proveedor", "moneda", "monto_total"]


@pytest.mark.parametrize(
    "cambio",
    [
        {"monto_total": "0"},
        {"monto_total": "-3"},
        {"monto_total": "abc"},
        {"monto_total": "NaN"},
        {"fecha_emision": "14/11/2026"},
        {"fecha_emision": "2026-13-45"},
        {"rif": "XYZ"},
        {"iva": "-1"},
    ],
)
def test_gasto_invalido_de_fondo_se_rechaza(cambio: dict[str, Any]) -> None:
    with pytest.raises(CapturaInvalida):
        validar(GASTO, _gasto(**cambio))


def test_un_gasto_no_valida_la_base_imponible_igual_que_omni() -> None:
    assert validar(GASTO, _gasto(base_imponible="-5")) == []  # Omni no la usa; paridad exacta


@pytest.mark.parametrize(
    "a,b",
    [
        ("Restaurante El Sabor", "RESTAURANTE  EL  SABOR "),
        ("Panadería Ñandú", "panaderia nandu"),
        ("  Café  del Centro", "CAFE DEL CENTRO"),
    ],
)
def test_nombres_de_proveedor_se_comparan_sin_tildes_mayusculas_ni_espacios_de_mas(
    a: str, b: str
) -> None:
    assert normalizar_nombre(a) == normalizar_nombre(b)


# ── servicio: proveedor existente / creado ───────────────────────────────────


def test_gasto_con_proveedor_existente_crea_la_factura_en_borrador_pagada_al_contado() -> None:
    svc, _, odoo = montar(OdooFalso(res_partner__search=partners(31)))
    c, _ = svc.recibir(
        GASTO,
        _gasto(
            iva="172.56",
            base_imponible="1077.94",
            numero_control="00-77",
            categoria="Alimentación",
            concepto="Almuerzo del equipo",
        ),
        FUENTE,
        "k1",
    )
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.odoo_ref == "account.move:101"
    assert out.resultado["proveedor_via"] == "rif" and out.resultado["proveedor_creado"] is False
    assert odoo.creados_de("res.partner") == []  # el proveedor ya existía: no se toca
    (v,) = odoo.creados_de("account.move")
    assert v["move_type"] == "in_invoice" and v["ref"] == "R-0045" and v["partner_id"] == 31
    assert v["currency_id"] == 2 and v["invoice_date"] == "2026-11-14"
    assert "invoice_date_due" not in v  # un gasto pagado no tiene vencimiento
    linea = v["invoice_line_ids"][0][2]
    assert linea["name"] == "Almuerzo del equipo" and linea["price_unit"] == 1077.94
    assert linea["tax_ids"] == [(6, 0, [])]  # no se inventan impuestos
    for pieza in (
        f"BOT-{c.captura_id}",
        "pagado al contado",
        "sin pago asociado",
        "Categoría Alimentación",
        "Documento R-0045",
        "Control 00-77",
        "IVA 172.56",
        "Monto total 1250.50 VES",
    ):
        assert pieza in v["narration"]
    assert odoo.creados_de("account.payment") == []  # NUNCA genera un pago


def test_gasto_con_proveedor_nuevo_lo_crea_con_los_datos_del_documento() -> None:
    svc, _, odoo = montar(OdooFalso())
    c, _ = svc.recibir(
        GASTO,
        _gasto(
            proveedor_direccion="Av. Bolívar, Valencia",
            proveedor_telefono="0241-5551234",
            proveedor_email="info@sabor.com",
        ),
        FUENTE,
        "k1",
    )
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.resultado["proveedor_creado"] is True
    (p,) = odoo.creados_de("res.partner")
    assert p["name"] == "Restaurante El Sabor C.A." and p["vat"] == "J-30123456-7"
    assert p["supplier_rank"] == 1 and p["customer_rank"] == 0 and p["company_type"] == "company"
    assert p["street"] == "Av. Bolívar, Valencia" and p["phone"] == "0241-5551234"
    assert p["email"] == "info@sabor.com" and p["ref"] == f"BOT-{c.captura_id}"
    assert "proveedor creado desde un documento; revisar" in p["comment"]
    assert "partner_latitude" not in p  # un proveedor de un recibo no trae GPS
    assert odoo.creados_de("account.move")[0]["partner_id"] == 101


def test_gasto_de_un_comercio_sin_rif_se_crea_solo_con_el_nombre() -> None:
    svc, _, odoo = montar(OdooFalso())
    c, _ = svc.recibir(
        GASTO, _gasto(rif=None, numero_factura=None, proveedor="Bodegón La Esquina"), FUENTE, "k1"
    )
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO
    (p,) = odoo.creados_de("res.partner")
    assert p["name"] == "Bodegón La Esquina" and "vat" not in p and "company_type" not in p
    (v,) = odoo.creados_de("account.move")
    assert v["ref"] == f"S/N-{c.captura_id}"  # sin número de documento
    assert v["invoice_line_ids"][0][2]["name"] == "Compra en Bodegón La Esquina"


def test_gasto_encuentra_al_proveedor_por_nombre_cuando_no_hay_rif() -> None:
    def busca(args: list[Any], kwargs: dict[str, Any]) -> list[int]:
        dominio = args[0]
        return [44] if any(isinstance(d, list) and d[0] == "supplier_rank" for d in dominio) else []

    svc, _, odoo = montar(OdooFalso(res_partner__search=busca))
    c, _ = svc.recibir(GASTO, _gasto(rif=None), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.resultado["proveedor_via"] == "nombre"
    assert (
        odoo.creados_de("res.partner") == []
        and odoo.creados_de("account.move")[0]["partner_id"] == 44
    )


def test_gasto_con_rif_ambiguo_no_adivina_ni_crea() -> None:
    svc, _, odoo = montar(OdooFalso(res_partner__search=partners(1, 2)))
    c, _ = svc.recibir(GASTO, _gasto(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == INCOMPLETO and "2 contactos" in out.error and odoo.creados == []


def test_gasto_con_nombre_ambiguo_no_adivina_ni_crea() -> None:
    svc, _, odoo = montar(
        OdooFalso(res_partner__search=lambda a, k: [] if _es_marca(a) else [1, 2])
    )
    c, _ = svc.recibir(GASTO, _gasto(rif=None), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == INCOMPLETO and "ambiguo" in out.error and odoo.creados == []


def _es_marca(args: list[Any]) -> bool:
    return any(isinstance(d, list) and d and d[0] == "ref" for d in args[0])


def test_gasto_con_solo_el_rif_inexistente_pide_el_nombre_en_vez_de_crear_a_ciegas() -> None:
    svc, _, odoo = montar(OdooFalso())
    c, _ = svc.recibir(
        GASTO,
        {"fecha_emision": "2026-11-14", "moneda": "USD", "monto_total": "5", "proveedor": "x"},
        FUENTE,
        "k0",
    )
    # proveedor es obligatorio en un gasto; con un RIF sin nombre una factura sí puede llegar:
    svc2, _, odoo2 = montar(OdooFalso())
    f, _ = svc2.recibir(
        "factura_proveedor",
        {
            "rif": "J-123456789",
            "numero_factura": "F-1",
            "fecha_emision": "2026-11-10",
            "moneda": "USD",
            "monto_total": "5",
        },
        FUENTE,
        "k1",
    )
    out = svc2.procesar(f.captura_id)
    assert out.estado == INCOMPLETO and "indica el nombre" in out.error and odoo2.creados == []
    assert c.estado == PENDIENTE and odoo.creados == []


def test_gasto_cuya_factura_ya_esta_en_odoo_para_ese_proveedor_es_un_rechazo() -> None:
    def moves(args: list[Any], kwargs: dict[str, Any]) -> list[int]:
        return [] if any(isinstance(d, list) and d[0] == "narration" for d in args[0]) else [900]

    svc, _, odoo = montar(OdooFalso(res_partner__search=partners(31), account_move__search=moves))
    c, _ = svc.recibir(GASTO, _gasto(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ERROR and "R-0045" in out.error and "account.move 900" in out.error
    assert odoo.creados_de("account.move") == [] and svc.reintentar_pendientes() == 0


def test_el_reintento_tras_crear_el_gasto_reutiliza_la_factura_propia() -> None:
    def moves(args: list[Any], kwargs: dict[str, Any]) -> list[int]:
        return [901]  # ya existe la factura con NUESTRA marca (y por ref)

    svc, _, odoo = montar(OdooFalso(res_partner__search=partners(31), account_move__search=moves))
    c, _ = svc.recibir(GASTO, _gasto(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.odoo_ref == "account.move:901" and odoo.creados == []


def test_gasto_con_moneda_desconocida_queda_incompleto_sin_crear_nada_de_mas() -> None:
    svc, _, odoo = montar(OdooFalso(res_partner__search=partners(31)))
    c, _ = svc.recibir(GASTO, _gasto(moneda="EUR"), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == INCOMPLETO and "moneda" in out.error and odoo.creados == []


def test_con_la_escritura_desactivada_el_gasto_queda_pendiente_y_no_toca_odoo() -> None:
    svc, _, odoo = montar(activa=False)
    c, _ = svc.recibir(GASTO, _gasto(), FUENTE, "k1")
    assert svc.procesar(c.captura_id).estado == PENDIENTE and odoo.llamadas == []


# ── duplicados lógicos: gasto y factura del mismo proveedor y número son el MISMO documento ──


def test_el_mismo_gasto_del_mismo_proveedor_se_rechaza_como_duplicado() -> None:
    svc, _, _ = montar(activa=False)
    svc.recibir(GASTO, _gasto(), FUENTE, "ka")
    with pytest.raises(FacturaDuplicada):
        svc.recibir(GASTO, _gasto(), FUENTE, "kb")


def test_un_gasto_y_una_factura_con_el_mismo_numero_y_proveedor_chocan() -> None:
    svc, _, _ = montar(activa=False)
    svc.recibir("factura_proveedor", _gasto(), FUENTE, "ka")
    with pytest.raises(FacturaDuplicada):
        svc.recibir(GASTO, _gasto(), FUENTE, "kb")


def test_el_proveedor_se_compara_sin_tildes_ni_mayusculas_cuando_no_hay_rif() -> None:
    svc, _, _ = montar(activa=False)
    svc.recibir(GASTO, _gasto(rif=None, proveedor="Panadería Ñandú"), FUENTE, "ka")
    with pytest.raises(FacturaDuplicada):
        svc.recibir(GASTO, _gasto(rif=None, proveedor="PANADERIA  NANDU"), FUENTE, "kb")


def test_mismo_numero_de_otro_proveedor_o_sin_numero_no_es_duplicado() -> None:
    svc, _, _ = montar(activa=False)
    svc.recibir(GASTO, _gasto(), FUENTE, "ka")
    svc.recibir(GASTO, _gasto(rif="J-999999999", proveedor="Otro"), FUENTE, "kb")
    # los recibos sin número se pueden repetir (dos almuerzos del mismo restaurante)
    svc.recibir(GASTO, _gasto(numero_factura=None), FUENTE, "kc")
    svc.recibir(GASTO, _gasto(numero_factura=None), FUENTE, "kd")


# ── completar y reenvío ──────────────────────────────────────────────────────


def test_gasto_incompleto_se_completa_y_se_envia() -> None:
    svc, repo, odoo = montar(OdooFalso(res_partner__search=partners(31)))
    c, _ = svc.recibir(GASTO, _gasto(monto_total=None, moneda=None), FUENTE, "k1")
    assert c.estado == INCOMPLETO and c.incompleto == ["moneda", "monto_total"]
    assert svc.procesar(c.captura_id).estado == INCOMPLETO and odoo.llamadas == []
    parcial = svc.completar(c.captura_id, {"moneda": "Bs", "ignorado": "x"}, "bot")
    assert parcial.estado == INCOMPLETO and parcial.incompleto == ["monto_total"]
    final = svc.completar(
        c.captura_id,
        {"monto_total": "99.90", "categoria": "Papelería", "proveedor_telefono": "0414-1112233"},
        "ana@x.com",
    )
    assert final.estado == ENVIADO and "ignorado" not in final.payload
    assert final.payload["categoria"] == "Papelería" and odoo.creados_de("account.move")
    assert ("completada", "categoria, monto_total, proveedor_telefono") in [
        (e, d) for i, e, d, _a in repo.bitacora if i == c.captura_id and e == "completada"
    ]


def test_completar_un_gasto_rechaza_valores_invalidos_y_no_los_guarda() -> None:
    svc, repo, _ = montar(activa=False)
    c, _ = svc.recibir(GASTO, _gasto(monto_total=None), FUENTE, "k1")
    with pytest.raises(CapturaInvalida):
        svc.completar(c.captura_id, {"monto_total": "-4"}, "bot")
    assert repo.filas[c.captura_id].payload["monto_total"] is None


def test_completar_con_un_numero_ya_capturado_es_duplicado() -> None:
    svc, _, _ = montar(activa=False)
    svc.recibir(GASTO, _gasto(numero_factura="R-1"), FUENTE, "ka")
    b, _ = svc.recibir(GASTO, _gasto(numero_factura=None), FUENTE, "kb")
    with pytest.raises(FacturaDuplicada):
        svc.completar(b.captura_id, {"numero_factura": "r-1"}, "bot")


def test_no_se_completan_los_contactos_aunque_ahora_haya_mas_tipos_completables() -> None:
    svc, _, _ = montar(activa=False)
    from tests.capturas_dobles import contacto_ok

    c, _ = svc.recibir("contacto", contacto_ok(), FUENTE, "k1")
    with pytest.raises(NoCompletable):
        svc.completar(c.captura_id, {"nombre": "X"}, "bot")


def test_el_reenvio_corregido_de_un_gasto_pendiente_reemplaza_al_borrador_viejo() -> None:
    svc, repo, odoo = montar(activa=False)
    c, _ = svc.recibir(GASTO, _gasto(monto_total="10"), FUENTE, "k1")
    c2, creada, actualizada = svc.recibir_detallado(GASTO, _gasto(monto_total="12"), FUENTE, "k1")
    assert creada is False and actualizada is True and c2.captura_id == c.captura_id
    assert repo.filas[c.captura_id].payload["monto_total"] == "12" and odoo.llamadas == []


# ── API ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[RepoMemoria, OdooFalso]]:
    monkeypatch.setenv("BOT_API_KEY", "clave-de-prueba-del-bot")
    odoo = OdooFalso()
    repo = RepoMemoria()
    from cxc.capturas.servicio import ServicioCapturas

    svc = ServicioCapturas(repo, EspejoFalso(), lambda: EscritorOdoo(odoo), diarios={"default": 14})
    app.dependency_overrides[get_servicio] = lambda: svc
    yield repo, odoo
    app.dependency_overrides.pop(get_servicio, None)


def _post(draft: dict[str, Any], clave: str = "k1") -> Any:
    body = {"kind": "gasto", "draft": draft, "source": FUENTE, "idempotency_key": clave}
    return client.post("/api/bots/capturas", json=body, headers=BOT)


def test_api_gasto_crea_el_proveedor_y_deja_la_captura_enviada(api: Any) -> None:
    repo, odoo = api
    r = _post(_gasto(proveedor_telefono="0241-5551234"))
    assert r.status_code == 201 and r.json()["kind"] == "gasto"
    cap = repo.filas[r.json()["id"]]
    assert cap.estado == ENVIADO and cap.resultado["proveedor_creado"] is True
    assert len(odoo.creados_de("res.partner")) == 1 and len(odoo.creados_de("account.move")) == 1


@pytest.mark.parametrize(
    "cambio", [{"monto_total": "0"}, {"fecha_emision": "ayer"}, {"rif": "ZZZ"}, {"iva": "-2"}]
)
def test_api_gasto_invalido_da_422_y_no_deja_captura(api: Any, cambio: dict[str, Any]) -> None:
    repo, odoo = api
    r = _post(_gasto(**cambio))
    assert r.status_code == 422 and isinstance(r.json()["faltan"], list)
    assert repo.filas == {} and odoo.llamadas == []


def test_api_gasto_duplicado_da_409(api: Any) -> None:
    _post(_gasto(), "ka")
    r = _post(_gasto(), "kb")
    assert r.status_code == 409 and "R-0045" in r.json()["error"]


def test_api_gasto_incompleto_y_completar_con_la_llave_del_bot(api: Any) -> None:
    repo, odoo = api
    r = _post(_gasto(monto_total=None))
    assert r.status_code == 201 and r.json()["estado"] == "INCOMPLETO"
    cid = r.json()["id"]
    r2 = client.post(
        f"/api/bots/capturas/{cid}/completar", json={"cambios": {"monto_total": "50"}}, headers=BOT
    )
    assert (
        r2.status_code == 200
        and repo.filas[cid].estado == ENVIADO
        and odoo.creados_de("account.move")
    )
