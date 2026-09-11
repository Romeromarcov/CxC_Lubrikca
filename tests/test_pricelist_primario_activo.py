"""Bug real (agosto 2026): la pricelist "primaria" se elegia como el

primer id de ``valid_pricelists_usd``/``_ves`` (texto de config), sin
importar si esa lista seguia activa en Odoo -- una lista ARCHIVADA con
reglas de precio viejas (ej. "7,8" con #7 archivada, 86 reglas stale que
ya no coinciden con las de #8) quedaba como primaria por puro accidente
de orden en el string. ``_primer_id_activo`` corrige esto sin requerir
que el usuario reordene la config: prefiere la primera activa.
"""

from __future__ import annotations

from cxc.web.app import _primer_id_activo


def _fake_execute(activos: set[int]):
    def _execute(model, method, args, kwargs=None):
        assert model == "product.pricelist"
        ids = args[0][0][2]
        return [{"id": i, "active": i in activos} for i in ids]

    return _execute


def test_prefiere_la_primera_lista_activa_aunque_no_sea_la_primera_del_config():
    execute = _fake_execute(activos={8})
    assert _primer_id_activo(execute, [7, 8]) == 8


def test_devuelve_la_primera_si_esa_ya_esta_activa():
    execute = _fake_execute(activos={7, 8})
    assert _primer_id_activo(execute, [7, 8]) == 7


def test_cae_a_la_primera_si_ninguna_esta_activa():
    execute = _fake_execute(activos=set())
    assert _primer_id_activo(execute, [7, 8]) == 7


def test_lista_vacia_devuelve_none():
    execute = _fake_execute(activos=set())
    assert _primer_id_activo(execute, []) is None


def test_si_la_consulta_a_odoo_falla_cae_a_la_primera_sin_reventar():
    def _execute_falla(model, method, args, kwargs=None):
        raise ConnectionError("Odoo caido")

    assert _primer_id_activo(_execute_falla, [7, 8]) == 7


# --- el armado del resolvedor, que estaba copiado tres veces (Fase 2.4, 27) ---


def test_el_resolvedor_usa_la_primaria_ACTIVA_de_cada_moneda() -> None:
    """Ocho líneas idénticas en tres sitios: normalizar ids, elegir primaria, armar.

    Preserva el comportamiento de las tres copias: la 7 está archivada, así que la
    primaria USD es la 8.
    """
    from unittest.mock import MagicMock, patch

    from cxc.web.app import _resolvedor_de_precios

    def execute(modelo, metodo, args, kwargs=None):
        assert modelo == "product.pricelist"
        return [
            {"id": 7, "active": False},
            {"id": 8, "active": True},
            {"id": 5, "active": True},
        ]

    with patch("cxc.web.app.build_fallback_ficha_config", return_value=None):
        r = _resolvedor_de_precios(execute, MagicMock(), ["7", "8"], ["5"])
    assert r._pricelist_ids == {"USD": 8, "BCV": 5}


def test_el_respaldo_junta_las_listas_de_LAS_DOS_monedas() -> None:
    """Si la lista puntual no tiene item para un producto, se prueban las demás
    antes de asumir precio 0 -- y eso incluye las de la otra moneda, porque las dos
    están fijadas en dólares."""
    from unittest.mock import MagicMock, patch

    from cxc.web.app import _resolvedor_de_precios

    with patch("cxc.web.app.build_fallback_ficha_config", return_value=None):
        r = _resolvedor_de_precios(
            lambda *a, **k: [{"id": 8, "active": True}, {"id": 5, "active": True}],
            MagicMock(),
            ["8"],
            ["5"],
        )
    assert sorted(r._fallback_pricelist_ids) == [5, 8]


def test_sin_configuracion_cae_a_los_ids_por_defecto_4_y_5() -> None:
    """Que no son un dato: son un nombre lógico de respaldo.

    Y hoy las dos están ARCHIVADAS en Odoo, así que un teórico calculado así no es
    comparable con uno de un entorno configurado. Comportamiento preservado del
    `or 4` / `or 5` que tenían las tres copias.
    """
    from unittest.mock import MagicMock, patch

    from cxc.web.app import _resolvedor_de_precios

    with patch("cxc.web.app.build_fallback_ficha_config", return_value=None):
        r = _resolvedor_de_precios(lambda *a, **k: [], MagicMock(), [], [])
    assert r._pricelist_ids == {"USD": 4, "BCV": 5}


def test_si_Odoo_no_contesta_cual_esta_activa_se_usa_la_primera_configurada() -> None:
    """Preservado: `_activos_pricelist` devuelve vacío y `primera_activa` cae a ids[0].

    Devolver un precio viejo es peor que ninguno, pero dejar al llamador sin precio
    sería un cambio de montos y no una corrección -- por eso se preserva.
    """
    from unittest.mock import MagicMock, patch

    from cxc.web.app import _resolvedor_de_precios

    def explota(*a, **k):
        raise OSError("Odoo caido")

    with patch("cxc.web.app.build_fallback_ficha_config", return_value=None):
        r = _resolvedor_de_precios(explota, MagicMock(), ["7", "8"], ["3", "5"])
    assert r._pricelist_ids == {"USD": 7, "BCV": 3}


def test_los_tres_sitios_delegan_en_la_pieza_en_vez_de_copiarla() -> None:
    """La guarda de la deduplicación: si alguien vuelve a escribirla inline, falla.

    Las ocho líneas estaban tres veces palabra por palabra, y una de esas copias vive
    en `api_backfill_ventas_teoricos`, que **escribe** `ventas_teoricos`.
    """
    from pathlib import Path

    fuente = Path("src/cxc/web/app.py").read_text(encoding="utf-8")
    assert fuente.count("_resolvedor_de_precios(execute, repo,") == 3, (
        "los tres sitios que arman un OdooPriceResolver tienen que llamar a la pieza"
    )
    assert 'pricelist_ids = {"USD": primary_usd_id' not in fuente, (
        "el armado del mapa volvió a estar inline"
    )
    assert fuente.count("primary_usd_id = _primer_id_activo") == 0
