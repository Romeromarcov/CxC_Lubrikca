"""Capturas `proveedor` y `factura_proveedor`: validación, escritor y servicio."""

from __future__ import annotations

from typing import Any

import pytest

from cxc.capturas.modelos import (
    CAMPOS_COMPLETABLES,
    ENVIADO,
    ERROR,
    INCOMPLETO,
    KINDS,
    PENDIENTE,
    CapturaInvalida,
    validar,
)
from cxc.capturas.servicio import FacturaDuplicada, NoCompletable
from cxc.odoo.escritura import NOTA_BORRADOR, EscritorOdoo, OdooEscrituraError
from tests.capturas_dobles import EspejoFalso, OdooFalso, partners
from tests.test_capturas_servicio import FUENTE, montar

# ── validación ───────────────────────────────────────────────────────────────


def _prov(**kw: Any) -> dict[str, Any]:
    base = {"nombre": "Lubricantes SA", "rif": "j-12345678-9", "telefono": "0414-1234567"}
    base.update(kw)
    return base


def _fact(**kw: Any) -> dict[str, Any]:
    base = {
        "proveedor": "Lubricantes SA",
        "rif": "J-123456789",
        "numero_factura": "F-001",
        "fecha_emision": "2026-11-10",
        "moneda": "USD",
        "monto_total": "116.00",
    }
    base.update(kw)
    return base


def test_los_kinds_nuevos_son_validos_y_la_factura_es_completable() -> None:
    assert {"proveedor", "factura_proveedor"} <= set(KINDS)
    assert "factura_proveedor" in CAMPOS_COMPLETABLES and "proveedor" not in CAMPOS_COMPLETABLES


def test_proveedor_valido_con_todos_los_opcionales() -> None:
    d = _prov(
        email="a@x.com",
        direccion="Av 1",
        dias_credito=30,
        notas="x",
        ubicacion={"latitud": 10.4, "longitud": -66.9, "precision_m": 8},
    )
    assert validar("proveedor", d) == []
    assert validar("proveedor", _prov(dias_credito="15")) == []


@pytest.mark.parametrize("campo", ["nombre", "rif", "telefono"])
def test_proveedor_exige_nombre_rif_y_telefono(campo: str) -> None:
    d = _prov()
    d.pop(campo)
    with pytest.raises(CapturaInvalida) as e:
        validar("proveedor", d)
    assert e.value.faltan == [campo]


@pytest.mark.parametrize(
    "cambio",
    [{"rif": "ABC"}, {"telefono": "123"}, {"dias_credito": "abc"}, {"dias_credito": -1}],
)
def test_proveedor_invalido_de_fondo(cambio: dict[str, Any]) -> None:
    with pytest.raises(CapturaInvalida):
        validar("proveedor", _prov(**cambio))


def test_factura_completa_y_faltantes_tolerables() -> None:
    assert validar("factura_proveedor", _fact()) == []
    assert validar("factura_proveedor", _fact(proveedor=None)) == []  # basta el RIF
    assert validar("factura_proveedor", _fact(rif=None)) == []  # o el nombre
    faltan = validar("factura_proveedor", {"monto_total": "5"})
    assert faltan == ["numero_factura", "fecha_emision", "moneda", "proveedor (nombre o RIF)"]
    assert validar("factura_proveedor", _fact(proveedor=None, rif=None)) == [
        "proveedor (nombre o RIF)"
    ]


@pytest.mark.parametrize(
    "cambio",
    [
        {"rif": "ABC"},
        {"monto_total": "0"},
        {"monto_total": "-5"},
        {"monto_total": "x"},
        {"iva": "-1"},
        {"base_imponible": "-1"},
        {"fecha_emision": "10/11/2026"},
        {"fecha_emision": "2026-13-45"},
        {"fecha_vencimiento": "mañana"},
        {"fecha_vencimiento": "2026-11-01"},  # anterior a la emisión
    ],
)
def test_factura_invalida_de_fondo(cambio: dict[str, Any]) -> None:
    with pytest.raises(CapturaInvalida):
        validar("factura_proveedor", _fact(**cambio))


# ── escritor ─────────────────────────────────────────────────────────────────


def test_crear_proveedor_en_borrador_con_rango_de_proveedor() -> None:
    odoo = OdooFalso()
    pid = EscritorOdoo(odoo).crear_proveedor(
        {
            "nombre": " Lubricantes SA ",
            "rif": "J-123456789",
            "telefono": "0414",
            "email": "p@x.com",
            "direccion": "Av 1",
            "dias_credito": 30,
            "notas": "contado",
            "lat": 10.4,
            "lon": -66.9,
        },
        "BOT-4",
    )
    (v,) = odoo.creados_de("res.partner")
    assert pid == 101 and v["name"] == "Lubricantes SA" and v["vat"] == "J-12345678-9"
    assert v["supplier_rank"] == 1 and v["customer_rank"] == 0 and v["ref"] == "BOT-4"
    assert v["street"] == "Av 1" and v["email"] == "p@x.com" and v["company_type"] == "company"
    assert v["comment"].startswith(NOTA_BORRADOR) and "Días de crédito: 30" in v["comment"]
    assert "contado" in v["comment"] and v["partner_latitude"] == 10.4


def test_proveedor_sin_gps_o_sin_campo_no_envia_coordenadas() -> None:
    odoo = OdooFalso(res_partner__fields_get={"name": {}})
    EscritorOdoo(odoo).crear_proveedor(
        {"nombre": "L", "rif": "V-1234567", "telefono": "0414", "lat": 1, "lon": 2}, "BOT-1"
    )
    (v,) = odoo.creados_de("res.partner")
    assert "partner_latitude" not in v and v["company_type"] == "person"


def test_proveedor_idempotente_por_marca_y_busquedas() -> None:
    odoo = OdooFalso(res_partner__search=[9])
    e = EscritorOdoo(odoo)
    assert e.crear_proveedor({"nombre": "L", "rif": "J-1", "telefono": "0414"}, "BOT-1") == 9
    assert odoo.creados == [] and e.partner_por_marca("BOT-1") == 9
    assert e.partners_por_nombre("100% Ñ_a") == [9]
    dominio = odoo.llamadas_a("res.partner", "search")[-1][0]
    assert dominio == [["name", "=ilike", "100\\% Ñ\\_a"]]  # sin filtrar por supplier_rank
    assert EscritorOdoo(OdooFalso()).partner_por_marca("BOT-1") is None


def _factura(escritor: EscritorOdoo, **kw: Any) -> int:
    args: dict[str, Any] = {
        "partner_id": 31,
        "numero": " F-001 ",
        "fecha_emision": "2026-11-10",
        "fecha_vencimiento": "2026-12-10",
        "moneda_id": 1,
        "monto": 100,
        "concepto": "Aceite",
        "marca": "BOT-8",
        "narration": "Factura proveedor F-001 · IVA 16",
    }
    args.update(kw)
    return escritor.crear_factura_proveedor(**args)


def test_factura_en_borrador_sin_inventar_impuestos() -> None:
    odoo = OdooFalso()
    assert _factura(EscritorOdoo(odoo)) == 101
    (v,) = odoo.creados_de("account.move")
    assert v["move_type"] == "in_invoice" and v["partner_id"] == 31 and v["ref"] == "F-001"
    assert v["invoice_date"] == "2026-11-10" and v["invoice_date_due"] == "2026-12-10"
    assert v["currency_id"] == 1 and v["narration"] == "BOT-8 · Factura proveedor F-001 · IVA 16"
    (linea,) = v["invoice_line_ids"]
    assert linea == (
        0,
        0,
        {"name": "Aceite", "quantity": 1.0, "price_unit": 100.0, "tax_ids": [(6, 0, [])]},
    )
    assert "state" not in v
    assert not [
        m for _, m, _, _ in odoo.llamadas if m in ("action_post", "write", "unlink")
    ]  # no publica


def test_factura_sin_vencimiento_ni_campo_de_impuestos() -> None:
    odoo = OdooFalso(account_move_line__fields_get={"name": {}})
    _factura(EscritorOdoo(odoo), fecha_vencimiento=None)
    (v,) = odoo.creados_de("account.move")
    assert "invoice_date_due" not in v and "tax_ids" not in v["invoice_line_ids"][0][2]


def test_factura_no_envia_vencimiento_si_odoo_no_tiene_ese_campo() -> None:
    campos = dict.fromkeys(
        [
            "move_type",
            "partner_id",
            "ref",
            "invoice_date",
            "currency_id",
            "invoice_line_ids",
            "narration",
        ],
        {},
    )
    odoo = OdooFalso(account_move__fields_get=campos)
    _factura(EscritorOdoo(odoo))
    assert "invoice_date_due" not in odoo.creados_de("account.move")[0]


def test_factura_verifica_los_campos_con_fields_get_y_no_crea_a_ciegas() -> None:
    odoo = OdooFalso(account_move__fields_get={"move_type": {}, "partner_id": {}})
    with pytest.raises(OdooEscrituraError) as e:
        _factura(EscritorOdoo(odoo))
    assert "narration" in str(e.value) and not e.value.transitorio and odoo.creados == []


def test_factura_idempotente_por_marca_delimitada_en_narration() -> None:
    odoo = OdooFalso(account_move__search=[555])
    assert _factura(EscritorOdoo(odoo)) == 555 and odoo.creados == []
    dominio = odoo.llamadas_a("account.move", "search")[0][0]
    # Delimitado con « · » para que BOT-1 nunca colisione con BOT-10 en el HTML de narration
    assert ["narration", "ilike", "BOT-8 · "] in dominio and [
        "move_type",
        "=",
        "in_invoice",
    ] in dominio


def test_buscar_factura_de_proveedor_por_ref_y_partner() -> None:
    odoo = OdooFalso(account_move__search=[77])
    assert EscritorOdoo(odoo).factura_proveedor_por_ref(31, " F-001 ") == 77
    dominio = odoo.llamadas_a("account.move", "search")[0][0]
    assert ["partner_id", "=", 31] in dominio and ["ref", "=", "F-001"] in dominio
    assert EscritorOdoo(OdooFalso()).factura_proveedor_por_ref(31, "X") is None


# ── servicio: proveedor ──────────────────────────────────────────────────────


def _buscar_partner(
    por_vat: list[int] | None = None,
    por_nombre: list[int] | None = None,
    supplier: list[int] | None = None,
    por_marca: list[int] | None = None,
) -> Any:
    def f(args: list[Any], kwargs: dict[str, Any]) -> list[int]:
        dominio = args[0]
        if any(isinstance(d, list) and d[0] == "ref" for d in dominio):
            return por_marca or []
        if dominio[0][0] == "vat":
            return por_vat or []
        if any(d[0] == "supplier_rank" for d in dominio if isinstance(d, list)):
            return supplier or []
        return por_nombre or []

    return f


def test_proveedor_nuevo_se_crea_en_borrador() -> None:
    svc, repo, odoo = montar()
    c, _ = svc.recibir("proveedor", _prov(email="p@x.com", dias_credito=30), FUENTE, "k1")
    assert c.estado == PENDIENTE and c.payload["rif"] == "J-123456789"
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.odoo_ref == "res.partner:101"
    assert out.resultado["ya_existia"] is False
    (v,) = odoo.creados_de("res.partner")
    assert v["supplier_rank"] == 1 and v["customer_rank"] == 0 and v["ref"] == f"BOT-{c.captura_id}"
    assert v["comment"].startswith(NOTA_BORRADOR)


def test_proveedor_existente_por_rif_no_se_duplica() -> None:
    svc, _, odoo = montar(OdooFalso(res_partner__search=_buscar_partner(por_vat=[42])))
    c, _ = svc.recibir("proveedor", _prov(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.odoo_ref == "res.partner:42"
    assert out.resultado["ya_existia"] is True and out.resultado["encontrado_por"] == "rif"
    assert "ya existía" in out.resultado["nota"] and odoo.creados == []


def test_proveedor_existente_por_nombre_exacto_no_se_duplica() -> None:
    svc, _, odoo = montar(OdooFalso(res_partner__search=_buscar_partner(por_nombre=[43])))
    c, _ = svc.recibir("proveedor", _prov(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.odoo_ref == "res.partner:43" and out.resultado["encontrado_por"] == "nombre"
    assert odoo.creados == []


@pytest.mark.parametrize(
    "busqueda,texto",
    [
        (_buscar_partner(por_vat=[1, 2]), "RIF J-123456789 está en 2"),
        (_buscar_partner(por_nombre=[1, 2, 3]), "3 contactos llamados"),
    ],
)
def test_proveedor_ambiguo_no_se_adivina(busqueda: Any, texto: str) -> None:
    svc, _, odoo = montar(OdooFalso(res_partner__search=busqueda))
    c, _ = svc.recibir("proveedor", _prov(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == INCOMPLETO and texto in out.error and odoo.creados == []


def test_proveedor_reintento_tras_crearlo_no_lo_marca_como_preexistente() -> None:
    svc, _, odoo = montar(
        OdooFalso(res_partner__search=_buscar_partner(por_marca=[77], por_vat=[77]))
    )
    c, _ = svc.recibir("proveedor", _prov(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.odoo_ref == "res.partner:77" and out.resultado["ya_existia"] is False
    assert odoo.creados == []


def test_proveedor_con_escritura_desactivada_no_toca_odoo() -> None:
    svc, _, odoo = montar(activa=False)
    c, _ = svc.recibir("proveedor", _prov(), FUENTE, "k1")
    assert svc.procesar(c.captura_id).estado == PENDIENTE and odoo.llamadas == []


# ── servicio: factura de proveedor ───────────────────────────────────────────


def _moves(por_marca: list[int] | None = None, por_ref: list[int] | None = None) -> Any:
    def f(args: list[Any], kwargs: dict[str, Any]) -> list[int]:
        dominio = args[0]
        if any(isinstance(d, list) and d[0] == "narration" for d in dominio):
            return por_marca or []
        return por_ref or []

    return f


def _odoo_factura(**kw: Any) -> OdooFalso:
    return OdooFalso(res_partner__search=partners(*kw.pop("partner", [31])), **kw)


def test_factura_completa_se_envia_en_borrador_con_la_nota_contable() -> None:
    svc, _, odoo = montar(_odoo_factura())
    c, _ = svc.recibir(
        "factura_proveedor",
        _fact(
            numero_control="00-123",
            iva="16.00",
            base_imponible="100.00",
            concepto="Aceite 20W50",
            fecha_vencimiento="2026-12-10",
            orden_ref="OC-9",
            notas="urgente",
        ),
        FUENTE,
        "k1",
    )
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.odoo_ref == "account.move:101"
    assert out.resultado["partner_id"] == 31 and out.resultado["proveedor_via"] == "rif"
    (v,) = odoo.creados_de("account.move")
    assert v["move_type"] == "in_invoice" and v["ref"] == "F-001" and v["currency_id"] == 1
    assert v["invoice_date"] == "2026-11-10" and v["invoice_date_due"] == "2026-12-10"
    linea = v["invoice_line_ids"][0][2]
    assert (
        linea["name"] == "Aceite 20W50"
        and linea["price_unit"] == 100.0
        and linea["tax_ids"] == [(6, 0, [])]
    )
    for pieza in (
        f"BOT-{c.captura_id}",
        "Control 00-123",
        "IVA 16.00",
        "Monto total 116.00 USD",
        "Orden OC-9",
        "urgente",
    ):
        assert pieza in v["narration"]


def test_factura_sin_base_usa_el_monto_total_y_concepto_por_defecto() -> None:
    svc, _, odoo = montar(_odoo_factura())
    c, _ = svc.recibir("factura_proveedor", _fact(), FUENTE, "k1")
    svc.procesar(c.captura_id)
    linea = odoo.creados_de("account.move")[0]["invoice_line_ids"][0][2]
    assert linea["price_unit"] == 116.0 and linea["name"] == "Factura proveedor F-001"


def test_factura_con_base_cero_usa_el_monto_total() -> None:
    svc, _, odoo = montar(_odoo_factura())
    c, _ = svc.recibir("factura_proveedor", _fact(base_imponible="0"), FUENTE, "k1")
    svc.procesar(c.captura_id)
    assert odoo.creados_de("account.move")[0]["invoice_line_ids"][0][2]["price_unit"] == 116.0


def test_factura_en_bolivares_usa_la_moneda_ves() -> None:
    svc, _, odoo = montar(_odoo_factura())
    c, _ = svc.recibir("factura_proveedor", _fact(moneda="Bs"), FUENTE, "k1")
    svc.procesar(c.captura_id)
    assert odoo.creados_de("account.move")[0]["currency_id"] == 2


def test_factura_con_moneda_desconocida_queda_incompleta() -> None:
    svc, _, odoo = montar(_odoo_factura())
    c, _ = svc.recibir("factura_proveedor", _fact(moneda="EUR"), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == INCOMPLETO and "moneda" in out.incompleto[0] and odoo.creados == []


def test_factura_identifica_al_proveedor_por_nombre_cuando_no_hay_rif() -> None:
    odoo = OdooFalso(
        res_partner__search=lambda a, k: [31]
        if any(isinstance(d, list) and d[0] == "supplier_rank" for d in a[0])
        else []
    )
    svc, _, odoo = montar(odoo)
    c, _ = svc.recibir("factura_proveedor", _fact(rif=None), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.resultado["proveedor_via"] == "nombre"


def test_factura_identifica_al_proveedor_aunque_en_odoo_solo_figure_como_cliente() -> None:
    """Un contacto con ese nombre que no tiene rango de proveedor se reutiliza (no se duplica)."""

    def busca(args: list[Any], kwargs: dict[str, Any]) -> list[int]:
        dominio = args[0]
        if any(isinstance(d, list) and d[0] == "supplier_rank" for d in dominio):
            return []
        return [] if any(isinstance(d, list) and d[0] == "ref" for d in dominio) else [55]

    svc, _, odoo = montar(OdooFalso(res_partner__search=busca))
    c, _ = svc.recibir("factura_proveedor", _fact(rif=None), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.resultado["proveedor_via"] == "nombre_contacto"
    assert odoo.creados_de("res.partner") == []
    assert odoo.creados_de("account.move")[0]["partner_id"] == 55


def test_factura_de_proveedor_inexistente_lo_crea_con_los_datos_del_documento() -> None:
    svc, _, odoo = montar(OdooFalso())
    c, _ = svc.recibir(
        "factura_proveedor",
        _fact(
            proveedor_direccion="Av. Industrial, Valencia",
            proveedor_telefono="0241-8001122",
            proveedor_email="ventas@lubri.com",
        ),
        FUENTE,
        "k1",
    )
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.resultado["proveedor_creado"] is True
    (p,) = odoo.creados_de("res.partner")
    assert p["name"] == "Lubricantes SA" and p["vat"] == "J-12345678-9"
    assert p["supplier_rank"] == 1 and p["customer_rank"] == 0 and p["company_type"] == "company"
    assert p["phone"] == "0241-8001122" and p["email"] == "ventas@lubri.com"
    assert p["street"] == "Av. Industrial, Valencia" and p["ref"] == f"BOT-{c.captura_id}"
    assert "proveedor creado desde un documento; revisar" in p["comment"]
    # la factura va al proveedor recién creado
    assert odoo.creados_de("account.move")[0]["partner_id"] == 101


def test_factura_de_proveedor_sin_rif_inexistente_se_crea_solo_con_el_nombre() -> None:
    svc, _, odoo = montar(OdooFalso())
    c, _ = svc.recibir("factura_proveedor", _fact(rif=None), FUENTE, "k1")
    assert svc.procesar(c.captura_id).estado == ENVIADO
    (p,) = odoo.creados_de("res.partner")
    assert p["name"] == "Lubricantes SA" and "vat" not in p and "phone" not in p
    assert "company_type" not in p


def test_factura_con_solo_el_rif_y_proveedor_inexistente_pide_el_nombre() -> None:
    svc, _, odoo = montar(OdooFalso())
    c, _ = svc.recibir("factura_proveedor", _fact(proveedor=None), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == INCOMPLETO and "indica el nombre" in out.error and odoo.creados == []


def test_el_reintento_tras_crear_el_proveedor_no_lo_duplica() -> None:
    """Si el proveedor se creó pero la factura falló, el reintento reutiliza ese partner."""
    svc, _, odoo = montar(OdooFalso(res_partner__search=lambda a, k: [77]))
    c, _ = svc.recibir("factura_proveedor", _fact(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and odoo.creados_de("res.partner") == []
    assert out.resultado["partner_id"] == 77


@pytest.mark.parametrize(
    "hallados,texto",
    [
        ([1, 2], "2 contactos"),
    ],
)
def test_factura_con_rif_ambiguo_no_se_adivina(hallados: list[int], texto: str) -> None:
    svc, _, odoo = montar(OdooFalso(res_partner__search=partners(*hallados)))
    c, _ = svc.recibir("factura_proveedor", _fact(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == INCOMPLETO and texto in out.error and odoo.creados == []


def test_factura_con_nombre_ambiguo_no_se_adivina() -> None:
    odoo = OdooFalso(
        res_partner__search=lambda a, k: [1, 2]
        if any(isinstance(d, list) and d[0] == "supplier_rank" for d in a[0])
        else []
    )
    svc, _, odoo = montar(odoo)
    c, _ = svc.recibir("factura_proveedor", _fact(rif=None), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == INCOMPLETO and "ambiguo" in out.error and odoo.creados == []
    # dos contactos con ese nombre que no son proveedores: tampoco se adivina ni se crea otro
    odoo2 = OdooFalso(
        res_partner__search=lambda a, k: []
        if any(isinstance(d, list) and d[0] in ("supplier_rank", "ref") for d in a[0])
        else [1, 2]
    )
    svc2, _, odoo2 = montar(odoo2)
    c2, _ = svc2.recibir("factura_proveedor", _fact(rif=None), FUENTE, "k2")
    assert svc2.procesar(c2.captura_id).estado == INCOMPLETO and odoo2.creados == []


def test_factura_que_ya_esta_en_odoo_es_un_rechazo_de_negocio() -> None:
    svc, _, odoo = montar(_odoo_factura(account_move__search=_moves(por_ref=[900])))
    c, _ = svc.recibir("factura_proveedor", _fact(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ERROR and out.resultado["transitorio"] is False
    assert (
        "F-001" in out.error
        and "account.move 900" in out.error
        and odoo.creados_de("account.move") == []
    )
    assert svc.reintentar_pendientes() == 0  # un duplicado no se reintenta solo


def test_reintento_tras_crear_la_factura_reutiliza_la_propia_sin_confundirla_con_un_duplicado() -> (
    None
):
    svc, _, odoo = montar(
        _odoo_factura(account_move__search=_moves(por_marca=[901], por_ref=[901]))
    )
    c, _ = svc.recibir("factura_proveedor", _fact(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.odoo_ref == "account.move:901" and odoo.creados == []


def test_factura_incompleta_no_se_envia_hasta_completarla() -> None:
    svc, repo, odoo = montar(_odoo_factura())
    c, _ = svc.recibir(
        "factura_proveedor", {"proveedor": "Lubricantes SA", "monto_total": "50"}, FUENTE, "k1"
    )
    assert c.estado == INCOMPLETO and "numero_factura" in c.incompleto
    assert svc.procesar(c.captura_id).estado == INCOMPLETO and odoo.llamadas == []
    parcial = svc.completar(
        c.captura_id, {"numero_factura": "F-7", "fecha_emision": "2026-11-10"}, "bot"
    )
    assert parcial.estado == INCOMPLETO and parcial.incompleto == ["moneda"]
    final = svc.completar(
        c.captura_id, {"moneda": "USD", "ignorado": "x", "rif": "j-12345678-9"}, "bot"
    )
    assert final.estado == ENVIADO and final.payload["rif"] == "J-123456789"
    assert "ignorado" not in final.payload and odoo.creados_de("account.move")
    eventos = [(e, d) for i, e, d, _a in repo.bitacora if i == c.captura_id and e == "completada"]
    assert eventos == [
        ("completada", "fecha_emision, numero_factura"),
        ("completada", "moneda, rif"),
    ]


def test_completar_una_factura_rechaza_valores_invalidos() -> None:
    svc, _, _ = montar(_odoo_factura())
    c, _ = svc.recibir("factura_proveedor", _fact(moneda=None), FUENTE, "k1")
    with pytest.raises(CapturaInvalida):
        svc.completar(c.captura_id, {"monto_total": "-3"})
    with pytest.raises(CapturaInvalida):
        svc.completar(c.captura_id, {"fecha_vencimiento": "2020-01-01"})


def test_un_proveedor_no_se_completa() -> None:
    svc, _, _ = montar()
    c, _ = svc.recibir("proveedor", _prov(), FUENTE, "k1")
    with pytest.raises(NoCompletable):
        svc.completar(c.captura_id, {"nombre": "Otro"})


# ── duplicados entre capturas ────────────────────────────────────────────────


def test_misma_factura_del_mismo_proveedor_se_rechaza() -> None:
    svc, repo, _ = montar(activa=False)
    svc.recibir("factura_proveedor", _fact(), FUENTE, "k1")
    with pytest.raises(FacturaDuplicada) as e:
        svc.recibir("factura_proveedor", _fact(), FUENTE, "k2")
    assert "F-001" in str(e.value) and "captura #1" in str(e.value) and len(repo.filas) == 1
    with pytest.raises(FacturaDuplicada):  # el número no distingue mayúsculas ni espacios
        svc.recibir("factura_proveedor", _fact(numero_factura=" f-001 "), FUENTE, "k3")


def test_duplicado_se_detecta_por_nombre_si_no_hay_rif() -> None:
    svc, _, _ = montar(activa=False)
    svc.recibir("factura_proveedor", _fact(rif=None), FUENTE, "k1")
    with pytest.raises(FacturaDuplicada):
        svc.recibir("factura_proveedor", _fact(rif=None, proveedor="lubricantes sa"), FUENTE, "k2")


def test_mismo_numero_de_otro_proveedor_no_es_duplicado() -> None:
    svc, repo, _ = montar(activa=False)
    svc.recibir("factura_proveedor", _fact(), FUENTE, "k1")
    svc.recibir("factura_proveedor", _fact(proveedor="Otro SA", rif="J-987654321"), FUENTE, "k2")
    svc.recibir("factura_proveedor", _fact(numero_factura="F-002"), FUENTE, "k3")
    assert len(repo.filas) == 3


def test_la_misma_clave_de_telegram_devuelve_lo_ya_registrado_en_vez_de_duplicado() -> None:
    svc, repo, _ = montar(activa=False)
    c1, creada1 = svc.recibir("factura_proveedor", _fact(), FUENTE, "k1")
    c2, creada2 = svc.recibir("factura_proveedor", _fact(), FUENTE, "k1")
    assert creada1 and not creada2 and c1.captura_id == c2.captura_id and len(repo.filas) == 1


def test_completar_con_un_numero_ya_capturado_es_duplicado_pero_el_propio_no() -> None:
    svc, _, _ = montar(activa=False)
    svc.recibir("factura_proveedor", _fact(), FUENTE, "k1")
    c2, _ = svc.recibir("factura_proveedor", _fact(numero_factura=None), FUENTE, "k2")
    assert c2.estado == INCOMPLETO
    with pytest.raises(FacturaDuplicada):
        svc.completar(c2.captura_id, {"numero_factura": "F-001"}, enviar=False)
    ok = svc.completar(c2.captura_id, {"numero_factura": "F-002"}, enviar=False)
    assert ok.estado == PENDIENTE
    again = svc.completar(
        c2.captura_id, {"numero_factura": "F-002"}, enviar=False
    )  # no choca consigo misma
    assert again.estado == PENDIENTE


def test_otros_tipos_no_pasan_por_la_deteccion_de_duplicados() -> None:
    svc, repo, _ = montar(activa=False)
    svc.recibir("proveedor", _prov(), FUENTE, "k1")
    svc.recibir(
        "proveedor", _prov(), FUENTE, "k2"
    )  # dos capturas del mismo proveedor: Odoo no lo duplica
    assert len(repo.filas) == 2


# ── pago a proveedor con factura_ref ─────────────────────────────────────────


def _pago_prov(**kw: Any) -> dict[str, Any]:
    d = {
        "proveedor": "Lubricantes SA",
        "monto": "116",
        "moneda": "USD",
        "referencia": "T1",
        "banco": "Banco de Venezuela",
        "factura_ref": "F-001",
    }
    d.update(kw)
    return d


def test_pago_a_proveedor_ubica_la_factura_solo_como_referencia() -> None:
    odoo = OdooFalso(res_partner__search=[31], account_move__search=[900])
    svc, _, odoo = montar(odoo)
    c, _ = svc.recibir("pago_proveedor", _pago_prov(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.resultado["factura_odoo_id"] == 900
    memo = odoo.creados_de("account.payment")[0]["memo"]
    assert "factura F-001 [account.move 900]" in memo
    dominio = odoo.llamadas_a("account.move", "search")[0][0]
    assert ["partner_id", "=", 31] in dominio and ["ref", "=", "F-001"] in dominio
    # no concilia: solo se creó el pago, ninguna escritura sobre la factura
    assert [m for m, me, _a, _k in odoo.llamadas if m == "account.move" and me != "search"] == []


def test_pago_a_proveedor_con_factura_que_no_se_encuentra_no_es_error() -> None:
    svc, _, odoo = montar(OdooFalso(res_partner__search=[31]))
    c, _ = svc.recibir("pago_proveedor", _pago_prov(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and "factura_odoo_id" not in out.resultado
    memo = odoo.creados_de("account.payment")[0]["memo"]
    assert "factura F-001" in memo and "account.move" not in memo


def test_pago_a_proveedor_sin_factura_ref_no_consulta_facturas() -> None:
    svc, _, odoo = montar(OdooFalso(res_partner__search=[31]))
    c, _ = svc.recibir("pago_proveedor", _pago_prov(factura_ref=None), FUENTE, "k1")
    svc.procesar(c.captura_id)
    assert odoo.llamadas_a("account.move", "search") == []


def test_pago_de_cliente_no_busca_facturas_de_proveedor() -> None:
    svc, _, odoo = montar(espejo=EspejoFalso(clientes={"Taller": ["55"]}))
    c, _ = svc.recibir(
        "pago",
        {
            "cliente": "Taller",
            "monto": "5",
            "moneda": "USD",
            "referencia": "R",
            "factura_ref": "F-9",
        },
        FUENTE,
        "k1",
    )
    svc.procesar(c.captura_id)
    assert odoo.llamadas_a("account.move", "search") == []
