"""Servicio de capturas: recepción, identificación sin adivinar y envío (escritor y Odoo falsos)."""

from __future__ import annotations

from typing import Any

import pytest

from cxc.capturas.modelos import (
    ENVIADO,
    ERROR,
    INCOMPLETO,
    PENDIENTE,
    CapturaInvalida,
)
from cxc.capturas.servicio import (
    CapturaCerrada,
    NoCompletable,
    NoEncontrada,
    ServicioCapturas,
    resolver_diario,
)
from cxc.odoo.escritura import EscritorOdoo, OdooEscrituraError
from tests.capturas_dobles import EspejoFalso, OdooFalso, RepoMemoria, contacto_ok, pedido_ok

FUENTE = {
    "telegram_user_id": 7,
    "vendedor_email": "ana@x.com",
    "chat_id": -1,
    "message_id": 3,
    "auto": False,
}


def montar(
    odoo: OdooFalso | None = None,
    espejo: EspejoFalso | None = None,
    diarios: dict[str, int] | None = None,
    activa: bool = True,
) -> tuple[ServicioCapturas, RepoMemoria, OdooFalso]:
    odoo = odoo or OdooFalso()
    repo = RepoMemoria()
    fabrica = (lambda: EscritorOdoo(odoo)) if activa else (lambda: None)
    return (
        ServicioCapturas(
            repo,
            espejo or EspejoFalso(),
            fabrica,
            diarios={"default": 14} if diarios is None else diarios,
        ),
        repo,
        odoo,
    )


def pago(**kw: Any) -> dict[str, Any]:
    base = {
        "cliente": "Taller",
        "monto": "100",
        "moneda": "USD",
        "referencia": "R1",
        "metodo_pago": "Transferencia",
        "banco": "Banco de Venezuela",
    }
    base.update(kw)
    return base


ESP_TALLER = EspejoFalso(
    clientes={"Taller": ["55"]},
    ordenes={"S00553": "66"},
    productos={"Aceite 20W50": ["900"], "AC-20W50": ["900"], "dup": ["1", "2"]},
)


# ── recepción ────────────────────────────────────────────────────────────────


def test_recibir_valida_guarda_y_es_idempotente() -> None:
    svc, repo, _ = montar()
    c1, creada1 = svc.recibir("pago", pago(), FUENTE, "k1")
    c2, creada2 = svc.recibir("pago", pago(), FUENTE, "k1")
    assert creada1 and not creada2 and c1.captura_id == c2.captura_id
    assert c1.estado == PENDIENTE and len(repo.filas) == 1


def test_recibir_normaliza_el_rif() -> None:
    svc, _, _ = montar()
    c, _ = svc.recibir("pago", pago(rif="j-12345678-9"), FUENTE, "k1")
    assert c.payload["rif"] == "J-123456789"


def test_pago_incompleto_se_registra_con_lo_que_falta() -> None:
    svc, _, odoo = montar()
    c, _ = svc.recibir("pago", {"cliente": "T", "monto": "5"}, {**FUENTE, "auto": True}, "k1")
    assert c.estado == INCOMPLETO and "referencia" in c.incompleto and c.auto
    assert svc.procesar(c.captura_id).estado == INCOMPLETO
    assert odoo.llamadas == []  # incompleto: no toca Odoo


def test_invalido_de_fondo_no_se_registra() -> None:
    svc, repo, _ = montar()
    with pytest.raises(CapturaInvalida):
        svc.recibir("pago", pago(monto="-1"), FUENTE, "k1")
    with pytest.raises(CapturaInvalida):
        svc.recibir("evento", {}, FUENTE, "k2")
    assert repo.filas == {}


# ── escritura desactivada ────────────────────────────────────────────────────


def test_con_la_escritura_desactivada_nunca_se_llama_a_odoo() -> None:
    svc, repo, odoo = montar(espejo=ESP_TALLER, activa=False)
    for kind, draft in (
        ("pago", pago()),
        ("contacto", contacto_ok()),
        ("pedido", pedido_ok(lineas=[{"producto": "AC-20W50", "cantidad": "1"}])),
    ):
        c, _ = svc.recibir(kind, draft, FUENTE, f"k-{kind}")
        out = svc.procesar(c.captura_id)
        assert out.estado == PENDIENTE and out.intentos == 0
    assert odoo.llamadas == [] and svc.reintentar_pendientes() == 3  # recorre pero no escribe
    assert odoo.llamadas == []


def test_si_no_se_puede_conectar_queda_en_error_transitorio() -> None:
    def fabrica() -> EscritorOdoo:
        raise OdooEscrituraError("No pude conectar con Odoo.", transitorio=True)

    svc = ServicioCapturas(RepoMemoria(), EspejoFalso(), fabrica)
    c, _ = svc.recibir("pago", pago(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ERROR and out.resultado["transitorio"] is True


# ── contacto ─────────────────────────────────────────────────────────────────


def test_contacto_se_envia_como_borrador() -> None:
    svc, repo, odoo = montar()
    c, _ = svc.recibir(
        "contacto",
        {
            "nombre": "Pedro",
            "telefono": "0414-1234567",
            "ubicacion": {"latitud": 10.4, "longitud": -66.9},
        },
        FUENTE,
        "k1",
    )
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.odoo_ref == "res.partner:101" and out.error == ""
    assert odoo.creados_de("res.partner")[0]["ref"] == f"BOT-{c.captura_id}"
    assert repo.eventos_de(c.captura_id) == ["recibida", "intento_envio", "creada_en_odoo"]


def test_enviado_no_se_reenvia() -> None:
    svc, _, odoo = montar()
    c, _ = svc.recibir("contacto", contacto_ok(), FUENTE, "k1")
    svc.procesar(c.captura_id)
    n = len(odoo.creados)
    assert svc.procesar(c.captura_id).estado == ENVIADO and len(odoo.creados) == n
    with pytest.raises(CapturaCerrada):
        svc.reintentar(c.captura_id)


# ── pedido ───────────────────────────────────────────────────────────────────


def _pedido(**kw: Any) -> dict[str, Any]:
    base = {
        "cliente": "Taller",
        "rif": "J-123456789",
        "email": "t@x.com",
        "direccion_fiscal": "Av 1",
        "entrega_igual_fiscal": True,
        "ubicacion": {"latitud": 10.4, "longitud": -66.9},
        "lineas": [{"producto": "AC-20W50", "cantidad": "2"}],
        "observaciones": "urgente",
    }
    base.update(kw)
    return base


def test_pedido_con_cliente_existente_por_rif() -> None:
    svc, _, odoo = montar(
        OdooFalso(res_partner__search=lambda a, k: [77] if a[0][0][0] == "vat" else []), ESP_TALLER
    )
    c, _ = svc.recibir("pedido", _pedido(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.odoo_ref == "sale.order:101"
    assert out.resultado["cliente_via"] == "rif" and out.resultado["odoo_name"] == "S00700"
    assert odoo.creados_de("res.partner") == []  # no crea clientes que ya existen
    v = odoo.creados_de("sale.order")[0]
    assert (
        v["partner_id"] == 77
        and v["user_id"] == 7
        and v["client_order_ref"] == f"BOT-{c.captura_id}"
    )
    assert "price_unit" not in v["order_line"][0][2] and v["order_line"][0][2]["product_id"] == 900


def test_pedido_con_cliente_nuevo_lo_crea_con_su_rif_y_entrega() -> None:
    svc, _, odoo = montar(
        OdooFalso(res_partner__search=lambda a, k: [44] if a[0][0][0] == "parent_id" else []),
        EspejoFalso(productos={"AC-20W50": ["900"]}),
    )
    c, _ = svc.recibir(
        "pedido",
        _pedido(cliente="Nuevo", entrega_igual_fiscal=False, direccion_entrega="Galpón 3"),
        FUENTE,
        "k1",
    )
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.resultado["cliente_via"] == "creado"
    padre, hijo = odoo.creados_de("res.partner")
    assert padre["vat"] == "J-12345678-9" and hijo["type"] == "delivery"
    assert odoo.creados_de("sale.order")[0]["partner_shipping_id"] == 44


def test_pedido_cliente_por_nombre_exacto_del_espejo_si_odoo_no_tiene_ese_rif() -> None:
    svc, _, odoo = montar(odoo=None, espejo=ESP_TALLER)
    c, _ = svc.recibir("pedido", _pedido(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert (
        out.resultado["cliente_via"] == "nombre"
        and odoo.creados_de("sale.order")[0]["partner_id"] == 55
    )


def test_pedido_rif_duplicado_en_odoo_no_se_adivina() -> None:
    svc, _, odoo = montar(OdooFalso(res_partner__search=[1, 2]), ESP_TALLER)
    c, _ = svc.recibir("pedido", _pedido(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == INCOMPLETO and "2 contactos" in out.error and odoo.creados == []


def test_pedido_con_varios_clientes_del_mismo_nombre_en_el_espejo_no_se_adivina() -> None:
    svc, _, odoo = montar(
        espejo=EspejoFalso(clientes={"Taller": ["1", "2"]}, productos={"AC-20W50": ["900"]})
    )
    c, _ = svc.recibir("pedido", _pedido(), FUENTE, "k1")  # el RIF no está en Odoo
    out = svc.procesar(c.captura_id)
    assert out.estado == INCOMPLETO and "varios clientes" in out.error and odoo.creados == []


def test_un_pedido_sin_rif_ni_otros_obligatorios_ya_no_llega_ni_a_registrarse() -> None:
    svc, repo, odoo = montar(espejo=EspejoFalso(productos={"AC-20W50": ["900"]}))
    with pytest.raises(CapturaInvalida) as e:
        svc.recibir("pedido", _pedido(cliente="Desconocido", rif=None), FUENTE, "k1")
    assert e.value.faltan == ["rif"] and repo.filas == {} and odoo.llamadas == []


def test_pedido_con_productos_sin_resolver_lista_todos_y_no_crea_nada() -> None:
    svc, _, odoo = montar(espejo=ESP_TALLER)
    c, _ = svc.recibir(
        "pedido",
        _pedido(
            lineas=[
                {"producto": "AC-20W50", "cantidad": "1"},
                {"producto": "Fantasma", "cantidad": "1"},
                {"producto": "dup", "cantidad": "1"},
            ]
        ),
        FUENTE,
        "k1",
    )
    out = svc.procesar(c.captura_id)
    assert out.estado == INCOMPLETO and len(out.incompleto) == 2
    assert "«Fantasma» no encontrado" in out.incompleto[0] and "«dup» ambiguo" in out.incompleto[1]
    assert odoo.creados == []


def test_pedido_respeta_el_precio_si_el_bot_lo_da() -> None:
    svc, _, odoo = montar(espejo=ESP_TALLER)
    c, _ = svc.recibir(
        "pedido",
        _pedido(lineas=[{"producto": "AC-20W50", "cantidad": "1", "precio_unitario": "12.5"}]),
        FUENTE,
        "k1",
    )
    svc.procesar(c.captura_id)
    assert odoo.creados_de("sale.order")[0]["order_line"][0][2]["price_unit"] == 12.5


def test_reintentar_un_pedido_incompleto_cuando_ya_se_sincronizo_el_catalogo() -> None:
    espejo = EspejoFalso(clientes={"Taller": ["55"]})
    svc, _, odoo = montar(espejo=espejo)
    c, _ = svc.recibir("pedido", _pedido(), FUENTE, "k1")
    assert svc.procesar(c.captura_id).estado == INCOMPLETO
    espejo.prods["ac-20w50"] = ["900"]  # llegó el producto al espejo
    out = svc.reintentar(c.captura_id, "ana@x.com")
    assert out.estado == ENVIADO and len(odoo.creados_de("sale.order")) == 1


# ── pagos ────────────────────────────────────────────────────────────────────


def test_pago_de_cliente_por_nombre_exacto_va_en_borrador() -> None:
    svc, _, odoo = montar(espejo=ESP_TALLER)
    c, _ = svc.recibir("pago", pago(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and out.odoo_ref == "account.payment:101"
    v = odoo.creados_de("account.payment")[0]
    assert v["partner_id"] == 55 and v["journal_id"] == 14 and v["currency_id"] == 1
    assert v["memo"].startswith(f"BOT-{c.captura_id}") and "ref R1" in v["memo"]
    assert odoo.creados_de("res.partner") == []  # jamás crea clientes para un pago


def test_pago_por_pedido_ref_usa_el_cliente_del_pedido() -> None:
    svc, _, odoo = montar(espejo=ESP_TALLER)
    c, _ = svc.recibir("pago", pago(cliente=None, pedido_ref="s00553"), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO and odoo.creados_de("account.payment")[0]["partner_id"] == 66
    assert out.resultado["pedido_ref_resuelto"] == "s00553"


def test_pago_por_rif() -> None:
    svc, _, odoo = montar(OdooFalso(res_partner__search=[88]))
    c, _ = svc.recibir("pago", pago(cliente=None, rif="j-12345678-9"), FUENTE, "k1")
    svc.procesar(c.captura_id)
    assert odoo.creados_de("account.payment")[0]["partner_id"] == 88


def test_pago_con_cliente_inexistente_queda_incompleto_y_no_crea_nada() -> None:
    svc, _, odoo = montar()
    c, _ = svc.recibir("pago", pago(cliente="Nadie"), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == INCOMPLETO and "cliente" in out.incompleto[0] and odoo.creados == []


def test_pago_con_rif_ambiguo_no_se_adivina() -> None:
    svc, _, odoo = montar(OdooFalso(res_partner__search=[1, 2]))
    c, _ = svc.recibir("pago", pago(cliente=None, rif="J-123456789"), FUENTE, "k1")
    assert svc.procesar(c.captura_id).estado == INCOMPLETO and odoo.creados == []


def test_pago_con_moneda_desconocida_en_odoo() -> None:
    svc, _, odoo = montar(espejo=ESP_TALLER)
    c, _ = svc.recibir("pago", pago(moneda="EUR"), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == INCOMPLETO and "moneda" in out.incompleto[0] and odoo.creados == []


def test_pago_en_bolivares_usa_la_moneda_ves() -> None:
    svc, _, odoo = montar(espejo=ESP_TALLER)
    c, _ = svc.recibir("pago", pago(moneda="Bs"), FUENTE, "k1")
    svc.procesar(c.captura_id)
    assert odoo.creados_de("account.payment")[0]["currency_id"] == 2


def test_pago_a_proveedor_por_nombre_y_por_rif() -> None:
    svc, _, odoo = montar(OdooFalso(res_partner__search=[31]))
    c, _ = svc.recibir(
        "pago_proveedor",
        {"proveedor": "Lubricantes SA", "monto": "900", "moneda": "USD"},
        FUENTE,
        "k1",
    )
    out = svc.procesar(c.captura_id)
    assert out.estado == ENVIADO
    v = odoo.creados_de("account.payment")[0]
    assert v["partner_id"] == 31 and v["payment_type"] == "outbound"
    dominio = odoo.llamadas_a("res.partner", "search")[0][0]
    assert ["supplier_rank", ">", 0] in dominio
    c2, _ = svc.recibir(
        "pago_proveedor",
        {"proveedor": "L", "rif": "J-5555555", "monto": "9", "moneda": "USD"},
        FUENTE,
        "k2",
    )
    assert svc.procesar(c2.captura_id).estado == ENVIADO
    assert odoo.llamadas_a("res.partner", "search")[-1][0][0][0] == "vat"


@pytest.mark.parametrize("encontrados,texto", [([], "no existe"), ([1, 2], "ambiguo")])
def test_proveedor_inexistente_o_ambiguo(encontrados: list[int], texto: str) -> None:
    svc, _, odoo = montar(OdooFalso(res_partner__search=encontrados))
    c, _ = svc.recibir(
        "pago_proveedor", {"proveedor": "L", "monto": "9", "moneda": "USD"}, FUENTE, "k1"
    )
    out = svc.procesar(c.captura_id)
    assert out.estado == INCOMPLETO and texto in out.error and odoo.creados == []


def test_pago_sin_diario_determinable_queda_incompleto() -> None:
    svc, _, odoo = montar(espejo=ESP_TALLER, diarios={})
    c, _ = svc.recibir("pago", pago(banco=None, metodo_pago=None), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == INCOMPLETO and "diario" in out.incompleto[0] and odoo.creados == []


# ── diarios ──────────────────────────────────────────────────────────────────


def test_diario_por_override_en_orden_de_especificidad() -> None:
    p = {"banco": "Bancamiga", "metodo_pago": "Pago móvil", "moneda": "Bs"}
    assert (
        resolver_diario(
            p, {"default": 1, "ves": 2, "pago_movil": 3, "bancamiga": 4, "bancamiga_ves": 5}
        )
        == 5
    )
    assert resolver_diario(p, {"default": 1, "ves": 2, "pago_movil": 3, "bancamiga": 4}) == 4
    assert resolver_diario(p, {"default": 1, "ves": 2, "pago_movil": 3}) == 3
    assert resolver_diario(p, {"default": 1, "ves": 2}) == 2
    assert resolver_diario(p, {"default": 1}) == 1


def test_diario_desde_el_mapa_de_bancos() -> None:
    assert resolver_diario({"banco": "Banco de Venezuela", "moneda": "VES"}, {}) == 14
    assert resolver_diario({"banco": "BDV", "moneda": "Bs"}, {}) == 14
    assert resolver_diario({"banco": "Bancamiga", "moneda": "VES"}, {}) == 30


def test_bancamiga_usd_no_tiene_diario_en_odoo() -> None:
    from cxc.capturas.servicio import _Incompleto

    with pytest.raises(_Incompleto) as e:
        resolver_diario({"banco": "Bancamiga", "moneda": "USD"}, {})
    assert "no tiene diario" in str(e.value)


def test_diario_banco_desconocido() -> None:
    from cxc.capturas.servicio import _Incompleto

    with pytest.raises(_Incompleto):
        resolver_diario({"banco": "Banco Inventado", "moneda": "VES"}, {})


# ── completar y reintentar ───────────────────────────────────────────────────


def test_completar_un_pago_incompleto_lo_envia() -> None:
    svc, _, odoo = montar(espejo=ESP_TALLER)
    c, _ = svc.recibir("pago", pago(referencia=None), FUENTE, "k1")
    assert c.estado == INCOMPLETO
    out = svc.completar(c.captura_id, {"referencia": "R99", "ignorado": "x", "banco": ""})
    assert (
        out.estado == ENVIADO
        and out.payload["referencia"] == "R99"
        and "ignorado" not in out.payload
    )
    assert odoo.creados_de("account.payment")[0]["memo"].count("R99") == 1


def test_completar_sin_enviar_deja_el_envio_al_llamador() -> None:
    svc, _, odoo = montar(espejo=ESP_TALLER)
    c, _ = svc.recibir("pago", pago(referencia=None), FUENTE, "k1")
    out = svc.completar(c.captura_id, {"referencia": "R99"}, enviar=False)
    assert out.estado == PENDIENTE and odoo.llamadas == []


def test_completar_que_sigue_incompleto_no_toca_odoo() -> None:
    svc, _, odoo = montar()
    c, _ = svc.recibir("pago", {"cliente": "T"}, FUENTE, "k1")
    out = svc.completar(c.captura_id, {"monto": "5"})
    assert out.estado == INCOMPLETO and "moneda" in out.incompleto and odoo.llamadas == []


def test_completar_rechaza_valores_invalidos_y_tipos_no_completables() -> None:
    svc, _, _ = montar()
    c, _ = svc.recibir("pago", pago(referencia=None), FUENTE, "k1")
    with pytest.raises(CapturaInvalida):
        svc.completar(c.captura_id, {"monto": "-3"})
    ct, _ = svc.recibir("contacto", contacto_ok(), FUENTE, "k2")
    with pytest.raises(NoCompletable):
        svc.completar(ct.captura_id, {"nombre": "Q"})
    with pytest.raises(NoEncontrada):
        svc.completar(999, {"monto": "1"})


def test_completar_un_pago_ya_enviado_es_conflicto() -> None:
    svc, _, _ = montar(espejo=ESP_TALLER)
    c, _ = svc.recibir("pago", pago(), FUENTE, "k1")
    svc.procesar(c.captura_id)
    with pytest.raises(CapturaCerrada):
        svc.completar(c.captura_id, {"referencia": "X"})


# ── errores y reintentos ─────────────────────────────────────────────────────


def test_error_permanente_de_odoo_queda_en_error_y_no_se_reintenta_solo() -> None:
    import xmlrpc.client

    svc, repo, odoo = montar(
        OdooFalso(account_payment__create=xmlrpc.client.Fault(1, "detalle interno")), ESP_TALLER
    )
    c, _ = svc.recibir("pago", pago(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ERROR and out.resultado["transitorio"] is False
    assert "detalle interno" not in out.error and out.intentos == 1
    assert svc.reintentar_pendientes() == 0  # permanente: solo a mano


def test_error_transitorio_se_reintenta_hasta_tener_exito() -> None:
    estado = {"cae": True}

    def crear(args: list[Any], kwargs: dict[str, Any]) -> Any:
        if estado["cae"]:
            raise ConnectionError("red")
        return 555

    svc, repo, _ = montar(OdooFalso(account_payment__create=crear), ESP_TALLER)
    c, _ = svc.recibir("pago", pago(), FUENTE, "k1")
    assert svc.procesar(c.captura_id).estado == ERROR
    assert svc.reintentar_pendientes() == 1  # sigue caído
    estado["cae"] = False
    assert svc.reintentar_pendientes() == 1
    out = repo.obtener(c.captura_id)
    assert out is not None and out.estado == ENVIADO and out.odoo_ref == "account.payment:555"


def test_los_reintentos_tienen_tope() -> None:
    def cae(args: list[Any], kwargs: dict[str, Any]) -> Any:
        raise ConnectionError("red")

    svc, repo, _ = montar(OdooFalso(account_payment__create=cae), ESP_TALLER)
    svc._max_intentos = 3
    c, _ = svc.recibir("pago", pago(), FUENTE, "k1")
    svc.procesar(c.captura_id)  # intento 1
    for _ in range(10):
        svc.reintentar_pendientes()
    assert repo.filas[c.captura_id].intentos == 3 and svc.reintentar_pendientes() == 0


def test_error_inesperado_no_filtra_detalles() -> None:
    def rompe(args: list[Any], kwargs: dict[str, Any]) -> Any:
        raise KeyError("campo_secreto")

    svc, _, _ = montar(OdooFalso(account_payment__create=rompe), ESP_TALLER)
    c, _ = svc.recibir("pago", pago(), FUENTE, "k1")
    out = svc.procesar(c.captura_id)
    assert out.estado == ERROR and "secreto" not in out.error


def test_reintento_manual_y_bitacora() -> None:
    svc, repo, _ = montar(espejo=ESP_TALLER, activa=False)
    c, _ = svc.recibir("pago", pago(), FUENTE, "k1")
    svc.reintentar(c.captura_id, "ana@x.com")
    assert (c.captura_id, "reintento_manual", "", "ana@x.com") in repo.bitacora
    with pytest.raises(NoEncontrada):
        svc.reintentar(404)
    capt, evs = svc.detalle(c.captura_id)
    assert capt.captura_id == c.captura_id and [e["evento"] for e in evs] == [
        "recibida",
        "reintento_manual",
    ]
    items, total = svc.listar(estado="PENDIENTE", kind="pago", limit=10, offset=0)
    assert total == 1 and items[0].captura_id == c.captura_id
