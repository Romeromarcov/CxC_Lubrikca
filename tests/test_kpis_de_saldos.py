"""El encabezado del reporte de saldos y las filas que quedaron (Fase 2.4, pieza 29).

`get_reporte_saldos` filtra las órdenes que el árbol de CxC ya dio por cobradas, y el
comentario explica bien por qué: «19 órdenes por $8.278,15 (2,9 % de la cartera
reportada) aparecían como deuda estando cobradas, y un cobrador salía a perseguirlas».

**Y no toca `kpis`.** Así que después del filtro el encabezado —total general, vencido,
vigentes y los cuatro tramos de mora— sigue incluyendo las órdenes que las filas de
abajo ya no tienen. El encabezado y el detalle de la misma pantalla difieren exactamente
en el monto de lo filtrado. Lo introdujo un arreglo, que es el caso más fácil de no ver.

No se corrige el encabezado: bajar el total de la cartera reportada mueve un monto y eso
pasa por el visto bueno del usuario. Lo que hay acá es la segunda lectura y la
diferencia.
"""

from __future__ import annotations

import pytest

from cxc.engine.kpis_de_saldos import diferencia_de_kpis, kpis_de_filas


def _fila(dias, deudor=100.0, desc_bcv=90.0, desc_usd=70.0, factura=80.0, so_id="S1"):
    return {
        "so_id": so_id,
        "dias_vencido": dias,
        "saldo_deudor_bcv": deudor,
        "saldo_con_descuento_bcv": desc_bcv,
        "saldo_con_descuento_lista_usd": desc_usd,
        "saldo_factura_odoo": factura,
    }


# --- los tramos, tal como el cuerpo original los arma ------------------------


@pytest.mark.parametrize(
    "dias,tramo",
    [
        (-5, "vigentes"),
        (0, "vigentes"),
        (1, "vencidas_1_30"),
        (30, "vencidas_1_30"),
        (31, "vencidas_31_60"),
        (60, "vencidas_31_60"),
        (61, "vencidas_61_90"),
        (90, "vencidas_61_90"),
        (91, "vencidas_mas_90"),
        (400, "vencidas_mas_90"),
    ],
)
def test_cada_borde_de_tramo_cae_donde_el_original_lo_pone(dias, tramo) -> None:
    """Los bordes son la mitad del valor de esto: 30/31, 60/61, 90/91 y el 0."""
    k = kpis_de_filas([_fila(dias)])
    assert k[tramo]["deudor_bcv"] == 100.0
    otros = set(k) - {tramo, "total_general", "total_vencido"}
    assert all(k[o]["deudor_bcv"] == 0.0 for o in otros)


def test_vigentes_mas_vencido_es_igual_al_total_general() -> None:
    """La invariante que hace que el encabezado se pueda leer.

    Una fila cuenta en `total_general` y además en `vigentes` **o** en `total_vencido`,
    nunca en los dos.
    """
    k = kpis_de_filas([_fila(-1), _fila(15), _fila(45), _fila(200)])
    for campo in ("deudor_bcv", "desc_bcv", "desc_usd", "factura_odoo"):
        assert k["vigentes"][campo] + k["total_vencido"][campo] == k["total_general"][campo]


def test_el_vencido_total_es_la_suma_de_los_cuatro_tramos() -> None:
    k = kpis_de_filas([_fila(10), _fila(40), _fila(70), _fila(120)])
    suma = sum(
        k[t]["deudor_bcv"]
        for t in ("vencidas_1_30", "vencidas_31_60", "vencidas_61_90", "vencidas_mas_90")
    )
    assert suma == k["total_vencido"]["deudor_bcv"] == 400.0


# --- el umbral de cinco centavos ---------------------------------------------


def test_una_fila_de_centavos_no_entra_a_ningun_kpi() -> None:
    """Cinco centavos de residual no son una deuda. Mismo 0,05 del original."""
    k = kpis_de_filas([_fila(10, deudor=0.04, desc_bcv=0.04, desc_usd=0.04, factura=0.04)])
    assert k["total_general"]["deudor_bcv"] == 0.0


def test_el_desc_bcv_NO_alcanza_para_entrar_pero_SI_se_suma() -> None:
    """Rareza preservada del cuerpo original, y por eso está escrita.

    La condición de entrada mira el deudor BCV, el de lista USD y el de la factura —no
    el `desc_bcv`—. Pero una vez que la fila entra, sus cuatro sub-saldos se acumulan.
    Una fila con solo `desc_bcv` queda afuera; si algo más la mete, su `desc_bcv` suma.
    """
    solo_desc = kpis_de_filas([_fila(10, deudor=0.0, desc_bcv=500.0, desc_usd=0.0, factura=0.0)])
    assert solo_desc["total_general"]["desc_bcv"] == 0.0, "no entró"

    con_otro = kpis_de_filas([_fila(10, deudor=1.0, desc_bcv=500.0, desc_usd=0.0, factura=0.0)])
    assert con_otro["total_general"]["desc_bcv"] == 500.0, "entró, y su desc_bcv suma"


def test_un_saldo_ilegible_cuenta_como_cero_y_no_tumba_la_cuenta() -> None:
    fila = _fila(10)
    fila["saldo_deudor_bcv"] = None
    fila["saldo_factura_odoo"] = False
    fila["saldo_con_descuento_lista_usd"] = "no es un numero"
    k = kpis_de_filas([fila])
    assert k["total_general"]["deudor_bcv"] == 0.0
    assert k["total_general"]["desc_bcv"] == 0.0, "no entró: ninguno de los tres pasó el umbral"


def test_sin_filas_los_siete_kpi_estan_en_cero_y_existen() -> None:
    """Existir importa: un `kpis` sin la clave rompería el front, no lo mostraría vacío."""
    k = kpis_de_filas([])
    assert len(k) == 7
    vacio = {"deudor_bcv": 0.0, "desc_bcv": 0.0, "desc_usd": 0.0, "factura_odoo": 0.0}
    assert all(v == vacio for v in k.values())


# --- la diferencia, que es el hallazgo ---------------------------------------


def test_el_encabezado_dice_de_mas_exactamente_lo_que_se_filtro() -> None:
    """El caso real: el encabezado se calculó con la orden cobrada, las filas no."""
    quedan = [_fila(10, so_id="S1")]
    publicados = kpis_de_filas([_fila(10, so_id="S1"), _fila(10, so_id="S2_cobrada")])
    d = diferencia_de_kpis(publicados, quedan, filas_quitadas=1)
    assert not d.coinciden
    assert d.diferencias["total_general"]["deudor_bcv"] == 100.0
    assert d.diferencias["vencidas_1_30"]["desc_usd"] == 70.0
    assert "1 orden(es)" in d.nota
    assert "decision del usuario" in d.nota, "corregirlo mueve un monto"


def test_si_no_se_filtro_nada_coinciden_y_lo_dice() -> None:
    filas = [_fila(10), _fila(-2)]
    d = diferencia_de_kpis(kpis_de_filas(filas), filas, filas_quitadas=0)
    assert d.coinciden
    assert d.diferencias == {}
    assert "coinciden" in d.nota


def test_una_diferencia_de_un_centavo_no_se_reporta() -> None:
    """El redondeo de floats no es un hallazgo, y reportarlo sería ruido."""
    filas = [_fila(10)]
    publicados = kpis_de_filas(filas)
    publicados["total_general"]["deudor_bcv"] += 0.004
    d = diferencia_de_kpis(publicados, filas, filas_quitadas=0)
    assert d.coinciden


def test_un_kpis_publicado_vacio_no_revienta() -> None:
    """Si el reporte viene degradado, el diagnóstico informa en vez de fallar."""
    d = diferencia_de_kpis({}, [_fila(10)], filas_quitadas=1)
    assert not d.coinciden
    assert d.diferencias["total_general"]["deudor_bcv"] == -100.0, "negativo: dice de MENOS"


# --- el endpoint -------------------------------------------------------------


def test_el_endpoint_expone_la_diferencia_entre_encabezado_y_filas() -> None:
    """El aviso viaja en la respuesta, no solo en un log.

    Sin esto, el hueco vive entre dos partes de la misma pantalla y nadie lo mide.
    """
    from contextlib import ExitStack
    from unittest.mock import MagicMock, patch

    from fastapi.testclient import TestClient

    import cxc.web.app as app

    filas = [_fila(10, so_id="S1"), _fila(10, so_id="S2")]
    datos = {"kpis": kpis_de_filas(filas), "items": filas, "saldo_minimo_pendientes": []}

    async def _ventas(**kw):
        return {"items": [{"so_id": "S2", "sale_de_cxc": True}]}

    async def _nada():
        return None

    with ExitStack() as pila:
        for parche in (
            patch("cxc.web.app._get_reporte_saldos_sync", return_value=datos),
            patch("cxc.web.app.get_ventas", _ventas),
            patch("cxc.web.app.get_repo", return_value=MagicMock()),
            patch("cxc.web.app.hay_sesion_valida", return_value=True),
            patch("cxc.web.app.run_scraper_in_background", _nada),
            patch("cxc.web.app.run_sync_in_background", _nada),
            patch("cxc.web.app._aplicar_migraciones_pendientes"),
        ):
            pila.enter_context(parche)
        cliente = pila.enter_context(TestClient(app.app))
        r = cliente.get("/api/reporte-saldos")

    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert [f["so_id"] for f in cuerpo["items"]] == ["S1"], "S2 se filtró"
    assert cuerpo["kpis"]["total_general"]["deudor_bcv"] == 200.0, "el encabezado NO se tocó"
    assert cuerpo["kpis_sin_cobradas"]["total_general"]["deudor_bcv"] == 100.0
    assert cuerpo["kpis_coinciden"] is False
    assert cuerpo["kpis_diferencias"]["total_general"]["deudor_bcv"] == 100.0


# --- la medición A/B, que es la red para poder cablear la pieza ---------------


def _acumulacion_original(filas):
    """El cuerpo de `_get_reporte_saldos_sync` tal como estaba, copiado a propósito.

    **Por qué una copia en un test.** Nada ejercita la función real: los diez tests que
    la mencionan la mockean, y el de FIFO reimplementa su fórmula. O sea que la suite no
    es red para un cambio ahí, y armar el arnés de una función de 1.104 líneas con 29
    dependencias no es proporcionado.

    Entonces la red es una medición A/B: esta copia es el lado «antes». Si las dos dan lo
    mismo sobre las mismas filas, reemplazar el cuerpo por la pieza preserva el
    resultado. Y si alguien cambia la regla en un solo lado, este test falla.

    Verificado antes de copiarla: entre la acumulación y el `reporte.append` **no hay
    ningún `continue`**, así que las dos cuentas recorren exactamente las mismas órdenes.
    """

    def vacio():
        return {"deudor_bcv": 0.0, "desc_bcv": 0.0, "desc_usd": 0.0, "factura_odoo": 0.0}

    g, v, vig = vacio(), vacio(), vacio()
    b1, b2, b3, b4 = vacio(), vacio(), vacio(), vacio()

    for f in filas:
        saldo_deudor_bcv = float(f.get("saldo_deudor_bcv") or 0)
        saldo_con_descuento_bcv = float(f.get("saldo_con_descuento_bcv") or 0)
        saldo_con_descuento_lista_usd = float(f.get("saldo_con_descuento_lista_usd") or 0)
        saldo_factura_odoo = f.get("saldo_factura_odoo")
        dias_vencido = float(f.get("dias_vencido") or 0)

        s_inv = float(saldo_factura_odoo) if saldo_factura_odoo is not None else 0.0
        if saldo_deudor_bcv > 0.05 or saldo_con_descuento_lista_usd > 0.05 or s_inv > 0.05:
            g["deudor_bcv"] += saldo_deudor_bcv
            g["desc_bcv"] += saldo_con_descuento_bcv
            g["desc_usd"] += saldo_con_descuento_lista_usd
            g["factura_odoo"] += s_inv

            if dias_vencido <= 0:
                destino = [vig]
            else:
                destino = [v]
                if 1 <= dias_vencido <= 30:
                    destino.append(b1)
                elif 31 <= dias_vencido <= 60:
                    destino.append(b2)
                elif 61 <= dias_vencido <= 90:
                    destino.append(b3)
                else:
                    destino.append(b4)
            for d in destino:
                d["deudor_bcv"] += saldo_deudor_bcv
                d["desc_bcv"] += saldo_con_descuento_bcv
                d["desc_usd"] += saldo_con_descuento_lista_usd
                d["factura_odoo"] += s_inv

    return {
        "total_general": g,
        "total_vencido": v,
        "vigentes": vig,
        "vencidas_1_30": b1,
        "vencidas_31_60": b2,
        "vencidas_61_90": b3,
        "vencidas_mas_90": b4,
    }


def _universo_de_filas():
    """Filas que cubren cada borde y cada rareza que encontré en el cuerpo original."""
    filas = []
    for dias in (-400, -1, 0, 1, 15, 30, 31, 45, 60, 61, 75, 90, 91, 400):
        filas.append(_fila(dias, so_id=f"S{dias}"))
    # Bajo el umbral por los tres campos que la condición mira.
    filas.append(
        _fila(10, deudor=0.04, desc_bcv=0.04, desc_usd=0.04, factura=0.04, so_id="centavos")
    )
    # Solo `desc_bcv`: NO entra, aunque tenga monto.
    filas.append(
        _fila(10, deudor=0.0, desc_bcv=900.0, desc_usd=0.0, factura=0.0, so_id="solo_desc")
    )
    # Entra por uno solo de los tres, y arrastra su desc_bcv.
    filas.append(
        _fila(10, deudor=0.0, desc_bcv=900.0, desc_usd=0.0, factura=1.0, so_id="por_factura")
    )
    filas.append(_fila(40, deudor=1.0, desc_bcv=0.0, desc_usd=0.0, factura=0.0, so_id="por_deudor"))
    filas.append(_fila(70, deudor=0.0, desc_bcv=0.0, desc_usd=1.0, factura=0.0, so_id="por_usd"))
    # Saldo de factura ausente, que el original convierte en 0.0.
    sin_factura = _fila(10, so_id="sin_factura")
    sin_factura["saldo_factura_odoo"] = None
    filas.append(sin_factura)
    # Negativos: un sobrepago no entra por el umbral, pero si algo más la mete, resta.
    filas.append(
        _fila(10, deudor=-50.0, desc_bcv=-50.0, desc_usd=-50.0, factura=90.0, so_id="sobrepago")
    )
    return filas


def test_la_pieza_da_EXACTAMENTE_lo_mismo_que_el_cuerpo_original() -> None:
    """La medición A/B sobre las 20 filas que cubren todos los bordes.

    Es la prueba que hace seguro reemplazar la acumulación inline por la pieza. Sin
    esto sería un refactor sin red: nada ejercita la función real.
    """
    filas = _universo_de_filas()
    de_la_pieza = kpis_de_filas(filas)
    del_original = _acumulacion_original(filas)
    assert set(de_la_pieza) == set(del_original)
    for nombre in del_original:
        for campo in ("deudor_bcv", "desc_bcv", "desc_usd", "factura_odoo"):
            assert abs(de_la_pieza[nombre][campo] - del_original[nombre][campo]) < 1e-9, (
                f"{nombre}.{campo}: la pieza da {de_la_pieza[nombre][campo]} y el cuerpo "
                f"original {del_original[nombre][campo]}"
            )


def test_la_AB_no_es_trivial_las_filas_producen_numeros_distintos_de_cero() -> None:
    """Una A/B sobre dos ceros pasaría sin probar nada.

    El plan ya se comió esa trampa una vez: un instrumento que nunca corrió daba cero y
    se leía como un cero verificado.
    """
    k = kpis_de_filas(_universo_de_filas())
    assert k["total_general"]["deudor_bcv"] != 0.0
    for tramo in (
        "vigentes",
        "vencidas_1_30",
        "vencidas_31_60",
        "vencidas_61_90",
        "vencidas_mas_90",
    ):
        assert k[tramo]["deudor_bcv"] != 0.0, f"{tramo} quedó en cero: el universo no lo cubre"
