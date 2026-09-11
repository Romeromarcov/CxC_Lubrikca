"""El rango de vigencia que la pantalla de listas muestra, y su denominador.

`get_config_listas_precio` salió del barrido de cobertura de la Fase 2.4 con 32 de
101 líneas sin cubrir. Calculaba el rango con `min(date_start)` / `max(date_end)`
sobre las reglas, **salteando las que no declaran fecha**, y mostraba el resultado
como el rango de la lista entera.

La hipótesis que fui a medir contra el Odoo de QA era otra: que hubiera listas con el
`hasta` saliendo de una minoría de sus reglas. **Eso midió cero.** Lo que la medición
sí mostró son dos formas distintas del mismo silencio, y son las que estos tests fijan.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient

import cxc.web.app as app


@contextmanager
def _sin_demonios():
    """Apaga el sync y el scraper del `startup`.

    Sin esto cada `TestClient` de este archivo dispara una request HTTPS real a
    www.bcv.org.ve -- diez en el archivo. Un test que sale a internet falla cuando
    se cae el BCV, que no tiene nada que ver con lo que se esta probando.
    """

    async def _nada():
        return None

    with (
        patch("cxc.web.app.run_sync_in_background", _nada),
        patch("cxc.web.app.run_scraper_in_background", _nada),
        patch("cxc.web.app._aplicar_migraciones_pendientes"),
    ):
        yield


def _execute_falso(listas, items):
    def _execute(modelo, _metodo, _args, _kwargs=None):
        if modelo == "product.pricelist":
            return listas
        if modelo == "product.pricelist.item":
            return items
        raise AssertionError(f"modelo inesperado: {modelo}")

    return _execute


def _pedir(listas, items):
    with (
        _sin_demonios(),
        patch("cxc.web.app._connect", return_value=_execute_falso(listas, items)),
        TestClient(app.app) as cliente,
    ):
        r = cliente.get("/api/config/listas-precio")
    assert r.status_code == 200, r.text
    return r.json()


def _lista(id_, nombre, activa=True):
    return {"id": id_, "name": nombre, "currency_id": [2, "USD"], "active": activa}


def _item(pl, desde=None, hasta=None):
    return {
        "pricelist_id": [pl, "x"],
        "fixed_price": 10.0,
        "percent_price": 0.0,
        "date_start": desde or False,
        "date_end": hasta or False,
        "product_tmpl_id": False,
    }


def test_la_respuesta_trae_el_rango_y_de_cuantas_reglas_sale() -> None:
    """Sin los contadores, un rango de 1 de 100 reglas se lee como el de las 100."""
    cuerpo = _pedir(
        [_lista(3, "USD", activa=False)],
        [_item(3, "2026-02-23", "2026-04-01"), _item(3), _item(3)],
    )
    (l3,) = cuerpo
    assert l3["fecha_desde"] == "2026-02-23"
    assert l3["fecha_hasta"] == "2026-04-01"
    assert l3["reglas_con_fecha_inicio"] == 1
    assert l3["reglas_con_fecha_fin"] == 1
    assert len(l3["reglas"]) == 3
    assert l3["rango_parcial"] is True


def test_la_forma_de_la_lista_9_de_QA_un_N_A_que_sale_de_149_reglas() -> None:
    """«Lista Industrial 3%»: 149 reglas, 0 declaran inicio, 148 declaran fin.

    El `fecha_desde: "N/A"` sigue siendo "N/A" —no cambio lo que la pantalla
    muestra— pero ahora la respuesta dice que sale de 0 de 149, que no es lo mismo
    que salir de una lista sin reglas.
    """
    items = [_item(9, hasta="2026-09-02") for _ in range(148)] + [_item(9)]
    (l9,) = _pedir([_lista(9, "Lista Industrial 3%", activa=False)], items)
    assert l9["fecha_desde"] == "N/A"
    assert l9["fecha_hasta"] == "2026-09-02"
    assert l9["reglas_con_fecha_inicio"] == 0
    assert len(l9["reglas"]) == 149


def test_las_listas_activas_no_tienen_una_sola_regla_con_fin_y_la_respuesta_lo_dice() -> None:
    """Medido: las nueve listas activas de QA, 151 a 186 reglas, 0 con `date_end`.

    Conecta con la mina del precio servido desde una regla vencida: una lista así
    **nunca** la dispara, porque sus reglas valen para cualquier fecha posterior a
    su inicio. El `N/A` del `hasta` no es un dato faltante, es "no vence nunca", y
    `ninguna_regla_vence` es lo que separa las dos lecturas.
    """
    items = [_item(10, desde="2026-09-02") for _ in range(154)]
    (l10,) = _pedir([_lista(10, "Pago VES Sept 2026")], items)
    assert l10["fecha_hasta"] == "N/A"
    assert l10["ninguna_regla_vence"] is True
    assert l10["rango_parcial"] is False


def test_una_lista_sin_reglas_no_dice_que_ninguna_vence() -> None:
    """Porque no hay ninguna. Dos ausencias distintas no pueden leerse igual."""
    (vacia,) = _pedir([_lista(20, "Recién creada")], [])
    assert vacia["fecha_desde"] == "N/A" and vacia["fecha_hasta"] == "N/A"
    assert vacia["ninguna_regla_vence"] is False
    assert vacia["reglas"] == []


def test_cada_lista_recibe_solo_sus_propias_reglas() -> None:
    """El agrupado por `pricelist_id` mezclaría los rangos si fallara."""
    cuerpo = _pedir(
        [_lista(3, "USD"), _lista(5, "Precio USD Pago VES")],
        [_item(3, "2026-02-23", "2026-04-01"), _item(5, "2026-04-02", "2026-09-02")],
    )
    por_id = {x["id"]: x for x in cuerpo}
    assert por_id[3]["fecha_hasta"] == "2026-04-01"
    assert por_id[5]["fecha_hasta"] == "2026-09-02"


def test_el_pricelist_id_escalar_tambien_agrupa() -> None:
    """Odoo devuelve `[id, nombre]` casi siempre, pero no en todas las rutas."""
    item = _item(3, "2026-04-02", "2026-09-02")
    item["pricelist_id"] = 3
    (l3,) = _pedir([_lista(3, "USD")], [item])
    assert len(l3["reglas"]) == 1
    assert l3["fecha_desde"] == "2026-04-02"


def test_una_regla_sin_producto_se_muestra_como_todos_los_productos() -> None:
    (l3,) = _pedir([_lista(3, "USD")], [_item(3)])
    assert l3["reglas"][0]["producto"] == "Todos los productos"
    assert l3["reglas"][0]["fecha_inicio"] == "N/A"


def test_el_nombre_del_producto_viaja_cuando_la_regla_lo_tiene() -> None:
    item = _item(3, "2026-04-02")
    item["product_tmpl_id"] = [99, "Aceite 20W50"]
    (l3,) = _pedir([_lista(3, "USD")], [item])
    assert l3["reglas"][0]["producto"] == "Aceite 20W50"


def test_la_respuesta_no_se_cachea() -> None:
    """La pantalla de listas se mira justo después de cambiar una regla en Odoo."""
    with (
        _sin_demonios(),
        patch("cxc.web.app._connect", return_value=_execute_falso([], [])),
        TestClient(app.app) as cliente,
    ):
        r = cliente.get("/api/config/listas-precio")
    assert "no-store" in r.headers["Cache-Control"]


def test_si_odoo_no_responde_el_endpoint_no_inventa_listas() -> None:
    """500, no una lista vacía: una pantalla de listas en blanco se lee como
    «no hay listas configuradas», que es lo contrario de la verdad."""

    def _explota(_cfg):
        raise OSError("odoo caído")

    with (
        _sin_demonios(),
        patch("cxc.web.app._connect", _explota),
        TestClient(app.app, raise_server_exceptions=False) as cliente,
    ):
        r = cliente.get("/api/config/listas-precio")
    assert r.status_code == 500


# --- dos reglas para el mismo producto (pieza 25) ----------------------------


def test_la_respuesta_avisa_del_producto_con_precio_ambiguo() -> None:
    """Medido el 11-sep-2026 en las listas ACTIVAS 10, 11, 18 y 19: seis pares así.

    Dos reglas con precios distintos y el mismo rango de fechas: cuál se sirve depende
    del orden interno de Odoo, no del dato. Antes las reglas salían en una lista plana
    de ciento cincuenta filas, así que las dos del mismo producto no se distinguían.
    """
    items = [
        _item(10, "2026-09-02"),
        _item(10, "2026-09-02"),
    ]
    items[0]["id"] = 3917
    items[0]["fixed_price"] = 1236.07
    items[0]["product_tmpl_id"] = [1042, "SINOCO SAE 50 (Tambor)"]
    items[1]["id"] = 3945
    items[1]["fixed_price"] = 1198.94
    items[1]["product_tmpl_id"] = [1042, "SINOCO SAE 50 (Tambor)"]

    (l10,) = _pedir([_lista(10, "Pago VES Sept 2026")], items)
    assert l10["productos_con_regla_repetida"] == 1
    assert l10["productos_con_precio_ambiguo"] == 1
    assert l10["productos_con_regla_en_cero"] == 0
    (aviso,) = l10["reglas_ambiguas"]
    assert aviso["producto"] == "SINOCO SAE 50 (Tambor)"
    assert sorted(aviso["precios"]) == [1198.94, 1236.07]
    assert aviso["ids_de_regla"] == [3917, 3945], "los ids, para poder ir a borrar una"
    assert aviso["alguna_en_cero"] is False


def test_la_respuesta_marca_aparte_la_regla_en_CERO() -> None:
    """Listas 18 y 19: 0,00 contra 1.139,66. Si Odoo elige el cero, sale gratis.

    Ese cero no se sirvió nunca —0 de 273 líneas y ninguna orden confirmada en esas
    listas— así que es una trampa cargada, no una pérdida. Por eso viaja como aviso y
    no como monto.
    """
    items = [_item(18, "2026-09-02"), _item(18, "2026-09-02")]
    for it, (rid, precio) in zip(items, [(3896, 0.0), (3927, 1139.66)], strict=True):
        it["id"] = rid
        it["fixed_price"] = precio
        it["product_tmpl_id"] = [1042, "SINOCO SAE 50 (Tambor)"]

    (l18,) = _pedir([_lista(18, "Maturin USD Sept 2026")], items)
    assert l18["productos_con_regla_en_cero"] == 1
    assert l18["reglas_ambiguas"][0]["alguna_en_cero"] is True


def test_una_lista_sin_reglas_repetidas_no_manda_avisos_vacios() -> None:
    (l3,) = _pedir([_lista(3, "USD")], [_item(3, "2026-02-23", "2026-04-01")])
    assert l3["productos_con_regla_repetida"] == 0
    assert l3["reglas_ambiguas"] == []
