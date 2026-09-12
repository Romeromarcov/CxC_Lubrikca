"""En qué tabla vive una regla, y qué pasa si el id está en dos (Fase 2.4, pieza 24).

`post_toggle_descuento` salió del barrido de cobertura con 22 de 41 líneas sin cubrir.
Prendía y apagaba una regla buscándola por id en todas las tablas y tocando la primera
que respondía, sin decir nunca si el id también estaba en otra.

**Lo que no se pudo medir.** Si hay ids repetidos entre tablas es una pregunta sobre
los datos, y la base de QA tiene **una sola regla** —`PRIMERA_COMPRA_COMERCIAL_2PCT`,
que la cargó este mismo plan—; las otras cinco tablas están vacías. Un cero sobre una
población de uno no es un cero verificado, y la base de producción no se puede
consultar. Así que estos tests fijan el comportamiento ante el choque sin afirmar que
el choque exista.
"""

from __future__ import annotations

import pytest

from cxc.engine.identidad_de_reglas import (
    ORDEN_DE_BUSQUEDA,
    aviso_de_ambiguedad,
    elegir_tabla_de_regla,
)

# --- la elección sin ambigüedad ---------------------------------------------


def test_gana_la_tabla_que_mando_el_front_cuando_tiene_el_id() -> None:
    e = elegir_tabla_de_regla(
        tabla_pedida="DescuentosVolumen", tablas_con_el_id=["DescuentosVolumen"]
    )
    assert e.elegida == "DescuentosVolumen"
    assert e.gano_la_pedida
    assert not e.ambigua


def test_si_el_front_se_equivoco_de_tabla_se_cae_al_orden_de_respaldo() -> None:
    """El front puede no saber en qué tabla vive una regla; el endpoint la encuentra."""
    e = elegir_tabla_de_regla(
        tabla_pedida="DescuentosVolumen", tablas_con_el_id=["PromocionPrimeraCompra"]
    )
    assert e.elegida == "PromocionPrimeraCompra"
    assert not e.gano_la_pedida
    assert not e.ambigua, "está en una sola tabla: no hay ambigüedad, hubo un front equivocado"


def test_un_id_que_no_esta_en_ninguna_tabla_no_elige_nada() -> None:
    """Y eso NO es un error: es la señal de seguir con las reglas por defecto.

    Las tres reglas de diferencial cambiario "por defecto" no están persistidas hasta
    que alguien las toca por primera vez, así que el endpoint tiene un segundo camino
    para ellas. Devolver `None` es lo que lo habilita.
    """
    e = elegir_tabla_de_regla(tabla_pedida="DescuentosVolumen", tablas_con_el_id=[])
    assert e.elegida is None
    assert not e.ambigua
    assert not e.gano_la_pedida
    assert e.descartadas == ()


# --- el alias, que sin él contaría doble ------------------------------------


def test_los_dos_alias_de_pronto_pago_NO_son_dos_tablas() -> None:
    """`DescuentosMarcaCategoria` y `DescuentosProntoPago` apuntan a la misma tabla.

    Sin esto, un id que vive solo en `descuentos_pronto_pago` se reportaría como
    ambiguo —un aviso falso, que es peor que ningún aviso porque entrena a ignorarlos.
    """
    e = elegir_tabla_de_regla(
        tabla_pedida="DescuentosMarcaCategoria",
        tablas_con_el_id=["DescuentosProntoPago", "DescuentosMarcaCategoria"],
    )
    assert not e.ambigua
    assert e.gano_la_pedida, "el alias pedido y la tabla encontrada son la misma"
    assert e.descartadas == ()


# --- el choque, que es el punto de la pieza ---------------------------------


def test_con_el_id_en_DOS_tablas_la_eleccion_es_por_ORDEN_y_lo_dice() -> None:
    """El defecto latente: hoy se toca una y se devuelve «listo».

    Prender o apagar una regla de descuento mueve plata en cada orden que la regla
    alcance. Si el id está en dos tablas, la que se toca sale del orden de búsqueda
    —no del dato— y eso tiene que viajar en la respuesta.
    """
    e = elegir_tabla_de_regla(
        tabla_pedida="",
        tablas_con_el_id=["DescuentosVolumen", "PromocionPrimeraCompra"],
    )
    assert e.ambigua
    assert e.elegida == "DescuentosVolumen", "va antes en ORDEN_DE_BUSQUEDA"
    assert e.descartadas == ("PromocionPrimeraCompra",)


def test_la_tabla_que_mando_el_front_le_gana_al_orden_incluso_en_el_choque() -> None:
    """Preserva el comportamiento de hoy: `[req.tabla, *conocidas]`.

    La tabla pedida iba primera en la lista de candidatas, así que ganaba. Sigue
    ganando; lo único que cambia es que ahora se avisa que había otra.
    """
    e = elegir_tabla_de_regla(
        tabla_pedida="PromocionPrimeraCompra",
        tablas_con_el_id=["DescuentosVolumen", "PromocionPrimeraCompra"],
    )
    assert e.elegida == "PromocionPrimeraCompra"
    assert e.gano_la_pedida
    assert e.ambigua
    assert e.descartadas == ("DescuentosVolumen",)


def test_el_aviso_nombra_las_dos_tablas_y_dice_de_donde_salio_la_eleccion() -> None:
    e = elegir_tabla_de_regla(
        tabla_pedida="",
        tablas_con_el_id=["DescuentosRecompra", "DescuentosProducto"],
    )
    aviso = aviso_de_ambiguedad(e, "REGLA_X")
    assert "DescuentosRecompra" in aviso and "DescuentosProducto" in aviso
    assert "NO porque" in aviso, "tiene que decir que la elección no salió del dato"
    assert "Sin tocar: DescuentosProducto" in aviso


def test_sin_ambiguedad_no_hay_aviso() -> None:
    """Cadena vacía, para que el llamador pregunte `if aviso:` sin conocer la regla."""
    e = elegir_tabla_de_regla(
        tabla_pedida="DescuentosVolumen", tablas_con_el_id=["DescuentosVolumen"]
    )
    assert aviso_de_ambiguedad(e, "REGLA_X") == ""


@pytest.mark.parametrize("tabla", ORDEN_DE_BUSQUEDA)
def test_cada_tabla_del_orden_se_puede_elegir(tabla: str) -> None:
    """Una tabla que el orden nombra pero que nunca gana sería una tabla inalcanzable."""
    e = elegir_tabla_de_regla(tabla_pedida="", tablas_con_el_id=[tabla])
    assert e.elegida == tabla


# --- el endpoint ------------------------------------------------------------


def _cliente_y_repo(tablas_con_el_id, exito=True):
    from unittest.mock import MagicMock, patch

    from fastapi.testclient import TestClient

    import cxc.web.app as app

    repo = MagicMock()
    repo.tablas_con_regla.return_value = tablas_con_el_id
    repo.set_regla_activo.return_value = exito
    repo.descuentos_diferencial_cambiario.return_value = []

    async def _nada():
        return None

    ctx = patch.multiple(
        "cxc.web.app",
        get_repo=MagicMock(return_value=repo),
        hay_sesion_valida=MagicMock(return_value=True),
        run_sync_in_background=_nada,
        run_scraper_in_background=_nada,
        _aplicar_migraciones_pendientes=MagicMock(),
    )
    return ctx, repo, TestClient(app.app)


def _toggle(tablas_con_el_id, exito=True, tabla="DescuentosVolumen"):
    ctx, repo, cliente = _cliente_y_repo(tablas_con_el_id, exito)
    with ctx, cliente as c:
        r = c.post(
            "/api/config/toggle-descuento",
            json={"regla_id": "REGLA_X", "tabla": tabla, "activo": False},
        )
    return r, repo


def test_el_endpoint_toca_la_tabla_elegida_y_ninguna_otra() -> None:
    """Antes probaba tabla por tabla con un UPDATE; ahora pregunta y toca una sola."""
    r, repo = _toggle(["DescuentosVolumen"])
    assert r.status_code == 200
    repo.set_regla_activo.assert_called_once_with("DescuentosVolumen", "REGLA_X", False)
    assert r.json()["tabla_ambigua"] is False
    assert "aviso" not in r.json()


def test_el_endpoint_avisa_cuando_el_id_esta_en_dos_tablas() -> None:
    """El aviso viaja en la respuesta, no solo en un log que nadie lee."""
    r, _repo = _toggle(
        ["DescuentosVolumen", "PromocionPrimeraCompra"], tabla="PromocionPrimeraCompra"
    )
    cuerpo = r.json()
    assert cuerpo["status"] == "success"
    assert cuerpo["tabla_ambigua"] is True
    assert set(cuerpo["tablas_con_el_id"]) == {"DescuentosVolumen", "PromocionPrimeraCompra"}
    assert "ATENCION" in cuerpo["aviso"]
    assert "PromocionPrimeraCompra" in cuerpo["message"], "se tocó la que pidió el front"


def test_un_id_inexistente_sin_regla_por_defecto_da_404() -> None:
    """Y no un 200 mentiroso: nada se prendió ni se apagó."""
    r, repo = _toggle([])
    assert r.status_code == 404
    repo.set_regla_activo.assert_not_called()


def test_una_regla_por_defecto_del_diferencial_se_persiste_al_tocarla_por_primera_vez() -> None:
    """Las tres reglas de diferencial cambiario «por defecto» no están en ninguna tabla
    hasta que alguien las prende o apaga desde el panel. Ese primer toque las escribe,
    con el `activo` que se pidió, y desde entonces viven como cualquier otra."""
    from unittest.mock import MagicMock, patch

    from fastapi.testclient import TestClient

    import cxc.web.app as app
    from cxc.models import DescuentoDiferencialCambiario

    por_defecto = DescuentoDiferencialCambiario(regla_id="DIF_DEFAULT_1", activo=True)
    repo = MagicMock()
    repo.tablas_con_regla.return_value = []  # no está en ninguna tabla
    repo.descuentos_diferencial_cambiario.return_value = [por_defecto]

    async def _nada():
        return None

    with (
        patch("cxc.web.app.get_repo", return_value=repo),
        patch("cxc.web.app.hay_sesion_valida", return_value=True),
        patch("cxc.web.app.run_sync_in_background", _nada),
        patch("cxc.web.app.run_scraper_in_background", _nada),
        patch("cxc.web.app._aplicar_migraciones_pendientes"),
        TestClient(app.app) as c,
    ):
        r = c.post(
            "/api/config/toggle-descuento",
            json={
                "regla_id": "DIF_DEFAULT_1",
                "tabla": "DescuentosDiferencialCambiario",
                "activo": False,
            },
        )
    assert r.status_code == 200, r.text
    assert "por defecto" in r.json()["message"]
    repo.set_regla_activo.assert_not_called(), "no había fila que actualizar"
    guardada = repo.append_descuento_diferencial_cambiario.call_args[0][0]
    assert guardada.regla_id == "DIF_DEFAULT_1"
    assert guardada.activo is False, "se persiste con el estado pedido, no con el default"
