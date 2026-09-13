"""El armado del balance, ahora ejercitable con diccionarios (Fase 2.4).

Cuarta pieza extraída, y la que el documento de la 2.4 tenía anotada como la de
mayor valor: 220 de las 843 líneas del endpoint eran puras y sólo se podían
correr levantando la aplicación entera con sus cuatro páginas.

El test de punta a punta (`test_balance_punta_a_punta.py`) sigue siendo la red que
verifica que las 24 partidas se emitan y se clasifiquen. Estos tests son lo otro:
los casos de a uno, incluidos los raros que armar por el endpoint costaría media
página de andamiaje —lista vacía, un campo con basura, un cliente con créditos
huérfanos— y que son justamente donde un balance miente.
"""

from __future__ import annotations

import pytest

from cxc.engine.balance import crear_partida, num, partidas_internas

# --- las dos piezas chicas -------------------------------------------------


@pytest.mark.parametrize(
    "valor,esperado",
    [
        (10, 10.0),
        (10.5, 10.5),
        ("10.5", 10.5),
        (None, 0.0),
        ("", 0.0),
        (0, 0.0),
        ("no es un numero", 0.0),
        ([], 0.0),
    ],
)
def test_num_no_revienta_con_lo_que_sea(valor, esperado) -> None:
    """Un balance que revienta no informa nada; uno que descuadra sí."""
    assert num({"k": valor}, "k") == esperado


def test_num_de_una_clave_que_no_existe_es_cero() -> None:
    assert num({}, "no_esta") == 0.0


def test_una_partida_cuadra_dentro_de_su_tolerancia() -> None:
    p = crear_partida("concepto", "izq", 100.0, "der", 100.4)
    assert p["cuadra"] is True
    assert p["diferencia"] == -0.4
    assert p["tipo"] == "interna"


def test_una_partida_descuadra_pasada_la_tolerancia() -> None:
    p = crear_partida("concepto", "izq", 100.0, "der", 100.6)
    assert p["cuadra"] is False


def test_la_diferencia_es_siempre_izquierda_menos_derecha() -> None:
    """Si esto se invierte, todo signo del balance miente."""
    p = crear_partida("c", "i", 10.0, "d", 4.0)
    assert p["diferencia"] == 6.0
    q = crear_partida("c", "i", 4.0, "d", 10.0)
    assert q["diferencia"] == -6.0


def test_crear_partida_devuelve_y_no_acumula() -> None:
    """Separar el armado del acumulado es lo que permite que la sección de Odoo
    siga en app.py usando la misma forma de partida."""
    a = crear_partida("uno", "i", 1.0, "d", 1.0)
    b = crear_partida("dos", "i", 2.0, "d", 2.0)
    assert a["concepto"] == "uno" and b["concepto"] == "dos"


# --- el armado completo ----------------------------------------------------


def _item(**kw):
    base = {
        "sale_de_cxc": False,
        "estado_cobro": "pendiente_cobro",
        "cliente_nombre": "CLIENTE UNO",
        "venta_real": 0.0,
        "venta_neta_real": 0.0,
        "venta_teorica_usd": 0.0,
        "descuento_aplicado_sistema": 0.0,
        "por_cobrar_real": 0.0,
        "por_cobrar_teorico_usd": 0.0,
        "saldo_a_favor": 0.0,
        "facturada": False,
    }
    base.update(kw)
    return base


VACIO = {"ordenes_por_facturar": [], "notas_credito_pendientes": []}


def test_sin_ordenes_igual_emite_todas_las_partidas() -> None:
    """Con todo en cero las partidas cuadran, y eso está bien acá.

    Distinto del caso «Ventas todavía calculando», que el endpoint corta antes
    de llegar hasta acá justamente para no emitir un verde sobre la nada.
    """
    partidas = partidas_internas({}, [], VACIO, {}, set())
    assert len(partidas) == 15
    assert all(p["tipo"] == "interna" for p in partidas)
    assert all(p["cuadra"] for p in partidas)


def test_una_orden_cobrada_que_el_reporte_sigue_listando_descuadra() -> None:
    """La partida 1, con el caso que existe para atrapar."""
    items = {"S001": _item(sale_de_cxc=True)}
    partidas = partidas_internas(items, [], VACIO, {"S001": {"so_id": "S001"}}, set())
    p = next(x for x in partidas if "sigue listando" in x["concepto"])
    assert p["cuadra"] is False
    assert p["derecha"]["valor"] == 1.0
    assert "Ventas da 1 por cobradas" in p["nota"]


def test_una_orden_por_cobrar_sin_fila_en_el_reporte_descuadra() -> None:
    items = {"S001": _item()}
    partidas = partidas_internas(items, [], VACIO, {}, set())
    p = next(x for x in partidas if "no lista" in x["concepto"])
    assert p["cuadra"] is False
    assert p["derecha"]["valor"] == 1.0


def test_el_saldo_minimo_cuenta_como_listada() -> None:
    """El Reporte lista aparte las de saldo mínimo; no son un descuadre."""
    items = {"S001": _item()}
    partidas = partidas_internas(items, [], VACIO, {}, {"S001"})
    p = next(x for x in partidas if "no lista" in x["concepto"])
    assert p["cuadra"] is True


def test_pendiente_de_entrega_no_cuenta_como_por_cobrar() -> None:
    items = {"S001": _item(estado_cobro="pendiente_entrega")}
    partidas = partidas_internas(items, [], VACIO, {}, set())
    p = next(x for x in partidas if "no lista" in x["concepto"])
    assert p["cuadra"] is True
    assert "Ventas cuenta 0 por cobrar" in p["nota"]


def test_los_creditos_huerfanos_no_se_comparan_contra_las_ordenes() -> None:
    """El falso descuadre de $23.673,56 que motivó separar documentos.

    El total por cliente está NETO de los pagos huérfanos, que son un crédito del
    cliente y no pertenecen a ninguna orden. Compararlo contra la suma por orden
    daba un rojo que no era un error.
    """
    items = {"S001": _item(por_cobrar_real=100.0, por_cobrar_teorico_usd=100.0)}
    clientes = [
        {
            "saldo_a_favor": 0.0,
            "saldos": {"venta_real": 60.0, "teorico_usd": 60.0},
            "documentos": [
                {"tipo": "orden", "saldos": {"venta_real": 100.0, "teorico_usd": 100.0}},
                {"tipo": "credito", "saldos": {"venta_real": -40.0, "teorico_usd": -40.0}},
            ],
        }
    ]
    partidas = partidas_internas(items, clientes, VACIO, {}, set())
    por_cobrar = [p for p in partidas if p["concepto"].startswith("Por cobrar")]
    assert len(por_cobrar) == 2
    assert all(p["cuadra"] for p in por_cobrar), "las órdenes contra las órdenes"
    cuadre = [p for p in partidas if p["concepto"].startswith("Cuadre interno")]
    assert all(p["cuadra"] for p in cuadre), "órdenes + créditos contra el total"


def test_la_bandeja_solo_se_juzga_sobre_ordenes_que_ventas_conoce() -> None:
    """El falso rojo de 3 y 129: una orden que Ventas no conoce no es un error
    de Facturación, es que no tenemos con qué opinar."""
    items = {"S001": _item(sale_de_cxc=True, facturada=True)}
    bandeja = {
        "ordenes_por_facturar": [{"so_id": "S001"}, {"so_id": "DESCONOCIDA"}],
        "notas_credito_pendientes": [{"so_id": "DESCONOCIDA"}],
    }
    partidas = partidas_internas(items, [], bandeja, {}, set())
    b1 = next(x for x in partidas if "Bandeja 1" in x["concepto"])
    b2 = next(x for x in partidas if "Bandeja 2" in x["concepto"])
    assert b1["cuadra"] and b2["cuadra"]


def test_la_bandeja_con_una_orden_no_cobrada_descuadra() -> None:
    items = {"S001": _item(sale_de_cxc=False)}
    bandeja = {"ordenes_por_facturar": [{"so_id": "S001"}], "notas_credito_pendientes": []}
    partidas = partidas_internas(items, [], bandeja, {}, set())
    b1 = next(x for x in partidas if "Bandeja 1" in x["concepto"])
    assert b1["cuadra"] is False
    assert "Solo se factura lo ya cobrado" in b1["nota"]


def test_el_saldo_a_favor_tiene_que_estar_en_las_dos_vistas() -> None:
    items = {"S001": _item(saldo_a_favor=250.0)}
    clientes = [{"saldo_a_favor": 0.0, "saldos": {}, "documentos": []}]
    partidas = partidas_internas(items, clientes, VACIO, {}, set())
    p = next(x for x in partidas if "Saldo a favor" in x["concepto"])
    assert p["cuadra"] is False
    assert p["izquierda"]["valor"] == 250.0
    assert p["derecha"]["valor"] == 0.0


def test_la_identidad_absorbe_el_saldo_a_favor_que_el_recorte_se_comio() -> None:
    """VENTA − COBRADO = POR COBRAR sólo cierra sumando lo que `max(0, …)` tapó.

    Es la razón de que la partida sume `favor`: el saldo nunca baja de cero, así
    que un sobrepago desaparece del lado derecho y sin ese ajuste la identidad
    no cierra nunca.
    """
    items = {
        "S001": _item(
            venta_real=100.0,
            pagado_real=150.0,
            por_cobrar_real=0.0,
            venta_neta_real=100.0,
        )
    }
    partidas = partidas_internas(items, [], VACIO, {}, set())
    identidad = [p for p in partidas if "= por cobrar" in p["concepto"]]
    assert identidad, "no se emitió la identidad de fondo"
    for p in identidad:
        assert "Saldo a favor" in p["nota"]


@pytest.mark.parametrize(
    "venta,pagado,desc",
    [
        (1000.0, 0.0, 0.0),
        (1000.0, 400.0, 0.0),
        (1000.0, 1000.0, 0.0),
        (1000.0, 1500.0, 0.0),  # sobrepago: el recorte a cero se activa
        (1000.0, 400.0, 250.0),
        (0.0, 300.0, 0.0),
        (0.0, 0.0, 0.0),
    ],
)
def test_la_identidad_cierra_siempre_y_eso_es_lo_que_verifica(venta, pagado, desc) -> None:
    """La identidad es un trinquete de consistencia, no una verificación.

    Vale escribirlo porque es fácil leerla al revés. `saldos_de_la_orden` calcula
    `max(0, venta_neta_real - descuento - pagado)` con **los mismos campos** que
    usa esta partida, y `favor` es exactamente `max(0, pagado - venta)`, o sea lo
    que el recorte a cero se comió. Sumado:

        Σ (v - p + max(0, p - v)) = Σ max(v - p, 0) = Σ saldo

    Cierra por álgebra, para cualquier entrada. **No puede detectar que los
    números estén mal**: si `venta_neta_real` viniera inflado, los dos lados se
    inflan igual y la partida sale verde.

    Lo que sí compra, y no es poco: el día que alguien cambie
    `saldos_de_la_orden` para usar otro campo, o toque el recorte a cero, esta
    partida se pone roja en el acto. Es un trinquete contra la deriva entre las
    dos definiciones, que es justo lo que el balance existe para atrapar.
    """
    items = {
        "S001": _item(
            cliente_nombre="CLIENTE A",
            venta_neta_real=venta,
            descuento_aplicado_sistema=desc,
            monto_pagado_factura_odoo_incl_pendiente=pagado,
        )
    }
    partidas = partidas_internas(items, [], VACIO, {}, set())
    identidad = [p for p in partidas if "= por cobrar" in p["concepto"]]
    arqueos = [p for p in partidas if p["concepto"].startswith("Arqueo por cliente")]
    assert identidad and arqueos
    for p in identidad + arqueos:
        assert p["cuadra"], f"{p['concepto']} descuadró: {p['izquierda']} vs {p['derecha']}"


def test_el_arqueo_dice_cuantos_clientes_miro() -> None:
    """Un «todos cuadran» sin decir sobre cuántos es la trampa de siempre."""
    items = {
        "S001": _item(cliente_nombre="CLIENTE A", venta_neta_real=1000.0),
        "S002": _item(cliente_nombre="CLIENTE B", venta_neta_real=10.0),
        "S003": _item(cliente_nombre="CLIENTE A", venta_neta_real=5.0),
    }
    partidas = partidas_internas(items, [], VACIO, {}, set())
    arqueo = next(p for p in partidas if p["concepto"].startswith("Arqueo por cliente"))
    assert "2 clientes arqueados" in arqueo["nota"]
    assert "Todos cuadran" in arqueo["nota"]


def test_un_campo_con_basura_descuadra_en_vez_de_reventar() -> None:
    """Lo que `num` compra, visto desde arriba."""
    items = {"S001": _item(por_cobrar_real="no es un numero", venta_real=None)}
    partidas = partidas_internas(items, [], VACIO, {}, set())
    assert len(partidas) == 15, "el balance siguió armándose"


def test_un_cliente_sin_documentos_no_rompe_el_cuadre() -> None:
    clientes = [{"saldo_a_favor": 0.0, "saldos": {"venta_real": 0.0, "teorico_usd": 0.0}}]
    partidas = partidas_internas({}, clientes, VACIO, {}, set())
    assert len(partidas) == 15


# --- el límite que la Fase 4 encontró --------------------------------------


def test_la_tolerancia_es_absoluta_y_no_escala_con_el_volumen() -> None:
    """El hallazgo de la Fase 4, fijado: el residuo crece y la tolerancia no.

    La partida «Saldo a favor de clientes» lleva `tolerancia=5.0` y su propia nota
    dice que «cubre el redondeo de cientos de filas». Medido multiplicando el
    espejo local por diez:

    | | filas | residuo | veredicto |
    |---|---:|---:|---|
    | 1× | 1.037 órdenes | 1,53 | verde |
    | 10× | 10.370 órdenes | **15,55** | **ROJA** |

    El residuo escala **lineal** con el volumen (10,2×) y la tolerancia es fija,
    así que a diez veces los datos una partida aritméticamente sana se pone roja.
    La dirección importa: es un **falso rojo**, y un instrumento que grita lobo a
    medida que el negocio crece deja de mirarse.

    No se cambió la tolerancia. Hacerla proporcional la volvería más permisiva a
    volumen alto, y eso podría tapar un descuadre real -- es un cambio de
    veredicto y es una decisión del usuario, igual que las dos partidas de tasa.
    Este test lo deja fijado y medido.
    """
    # Un cliente por orden y un centavo de residuo por orden: el residuo agregado
    # crece con la cantidad de filas, que es exactamente lo que pasa en la copia.
    def _con_residuo(n_ordenes: int, residuo_por_orden: float):
        items = {
            f"S{i:05d}": _item(cliente_nombre=f"CLIENTE {i}", saldo_a_favor=residuo_por_orden)
            for i in range(n_ordenes)
        }
        partidas = partidas_internas(items, [], VACIO, {}, set())
        return next(p for p in partidas if p["concepto"] == "Saldo a favor de clientes")

    chico = _con_residuo(100, 0.01)
    assert chico["cuadra"] is True, "con cien filas el residuo entra en la tolerancia"

    grande = _con_residuo(1000, 0.01)
    assert grande["cuadra"] is False, (
        "con mil filas el mismo residuo por fila supera la tolerancia fija de 5,0 "
        "-- eso es el hallazgo, no un fallo del test"
    )
    assert grande["diferencia"] == 10.0


# --- el margen de cada partida (decisión del 11-sep-2026) -------------------


def test_una_partida_expone_cuanta_tolerancia_esta_usando() -> None:
    """«Mantené la tolerancia al mínimo» resuelve un riesgo y deja el otro.

    No hacerla proporcional evita que una tolerancia generosa tape un descuadre
    real. Pero medido a diez veces los datos, el residuo de «Saldo a favor» pasa
    de 1,53 a 15,55 y la partida se pone roja con la tolerancia en 5,0 sin que
    nada esté mal.

    La salida es exponer el margen, no aflojar el límite.
    """
    from cxc.engine.balance import crear_partida

    p = crear_partida("X", "A", 101.53, "B", 100.0, tolerancia=5.0)
    assert p["cuadra"]
    assert p["tolerancia"] == 5.0
    assert p["margen_usado"] == 0.306
    assert not p["al_limite"]


def test_una_partida_al_limite_avisa_sin_cambiar_el_veredicto() -> None:
    """Sigue cuadrando: el aviso no es un rojo, es «mirala».

    Si `al_limite` cambiara `cuadra`, sería aflojar el límite en el otro sentido
    — poner en rojo algo que está dentro de su tolerancia.
    """
    from cxc.engine.balance import crear_partida

    p = crear_partida("X", "A", 104.0, "B", 100.0, tolerancia=5.0)
    assert p["cuadra"], "4,00 está dentro de 5,00"
    assert p["margen_usado"] == 0.8
    assert p["al_limite"]


def test_la_partida_que_a_10x_se_pondria_roja_hoy_ya_avisaria() -> None:
    """El caso medido, en los dos volúmenes.

    A 1× el residuo es 1,53 sobre una tolerancia de 5,0: 31 %, no avisa. A 10×
    es 15,55: cruza. Con el umbral en 0,6, una partida que hoy esté al 60 % ya
    avisa — y a diez veces los datos habría cruzado.
    """
    from cxc.engine.balance import UMBRAL_AL_LIMITE, crear_partida

    a_1x = crear_partida("Saldo a favor", "A", 1.53, "B", 0.0, tolerancia=5.0)
    a_10x = crear_partida("Saldo a favor", "A", 15.55, "B", 0.0, tolerancia=5.0)
    assert a_1x["cuadra"] and not a_10x["cuadra"]
    assert a_1x["margen_usado"] < UMBRAL_AL_LIMITE
    # Y el punto: al 60 % de hoy, diez veces eso ya no entra.
    al_60 = crear_partida("X", "A", 3.0, "B", 0.0, tolerancia=5.0)
    assert al_60["al_limite"]
    assert not crear_partida("X", "A", 30.0, "B", 0.0, tolerancia=5.0)["cuadra"]


def test_una_partida_que_no_cuadra_no_se_marca_al_limite() -> None:
    """`al_limite` es «cuadra, pero por poco». Una que no cuadra ya es un rojo."""
    from cxc.engine.balance import crear_partida

    p = crear_partida("X", "A", 200.0, "B", 100.0, tolerancia=5.0)
    assert not p["cuadra"]
    assert not p["al_limite"]


def test_con_tolerancia_en_cero_no_se_divide_por_cero() -> None:
    from cxc.engine.balance import crear_partida

    p = crear_partida("X", "A", 1.0, "B", 1.0, tolerancia=0.0)
    assert p["margen_usado"] is None
    assert not p["al_limite"]
