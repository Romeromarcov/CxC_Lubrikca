"""El reparto de un pago entre las órdenes abiertas de su cliente (Fase 2.4).

Es la sugerencia que el usuario ve y acepta con un clic, así que es un camino de
dinero de los directos. Vivía dentro de una función de 458 líneas y no tenía un solo
test propio.

Lo que estos tests fijan es sobre todo **una semántica sutil que antes dependía de
dónde estaba un `continue`**: una sugerencia que no se ofrece no consume el pago ni
el saldo de la orden. Es coherente —lo que no se puede aceptar no puede haber
comprometido nada— pero era carga oculta, y al moverla quedó dicha.

Y la mutación compartida, que también es carga: el dict de la orden se comparte entre
los pagos del mismo cliente, y el reparto lo modifica para que un segundo pago no
vuelva a ofrecer lo que el primero ya cubrió. Sin eso, dos pagos sobre un mismo
cliente sugerirían el saldo completo cada uno — que es sobreaplicación, el error que
este blindaje ya midió en 1.333,85 USD.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cxc.engine.conciliacion import UMBRAL_CUBIERTO, repartir_pago_entre_ordenes

D = Decimal


def _orden(so_id: str, saldo: str) -> dict:
    return {"so_id": so_id, "saldo_pendiente": D(saldo)}


def _fila_simple(restante_antes, orden, a_aplicar):
    return {
        "so_id": orden["so_id"],
        "restante_antes": restante_antes,
        "monto_sugerido": a_aplicar,
    }


def _nunca(restante_antes, orden, a_aplicar):
    """Ninguna sugerencia es visible."""
    return None


# --- el reparto normal -----------------------------------------------------


def test_un_pago_que_cubre_una_sola_orden() -> None:
    ordenes = [_orden("S1", "100")]
    filas, sobra = repartir_pago_entre_ordenes(D("100"), ordenes, fila=_fila_simple)
    assert [f["so_id"] for f in filas] == ["S1"]
    assert filas[0]["monto_sugerido"] == D("100")
    assert sobra == D("0")
    assert ordenes[0]["saldo_pendiente"] == D("0")


def test_un_pago_que_cubre_varias_en_el_orden_recibido() -> None:
    """La política de orden es del llamador (hoy FIFO por fecha); esto la respeta."""
    ordenes = [_orden("S1", "60"), _orden("S2", "60"), _orden("S3", "60")]
    filas, sobra = repartir_pago_entre_ordenes(D("100"), ordenes, fila=_fila_simple)
    assert [(f["so_id"], f["monto_sugerido"]) for f in filas] == [
        ("S1", D("60")),
        ("S2", D("40")),
    ]
    assert sobra == D("0")
    assert [o["saldo_pendiente"] for o in ordenes] == [D("0"), D("20"), D("60")]


def test_cada_fila_ve_el_residual_de_ese_momento_no_el_total() -> None:
    """Si un pago cubre tres órdenes, cada fila muestra lo que le quedaba ahí."""
    ordenes = [_orden("S1", "30"), _orden("S2", "30"), _orden("S3", "30")]
    filas, _ = repartir_pago_entre_ordenes(D("100"), ordenes, fila=_fila_simple)
    assert [f["restante_antes"] for f in filas] == [D("100"), D("70"), D("40")]


def test_un_pago_mas_grande_que_todas_las_ordenes_deja_residual() -> None:
    ordenes = [_orden("S1", "10"), _orden("S2", "10")]
    filas, sobra = repartir_pago_entre_ordenes(D("100"), ordenes, fila=_fila_simple)
    assert len(filas) == 2
    assert sobra == D("80")


def test_sin_ordenes_abiertas_todo_queda_como_residual() -> None:
    filas, sobra = repartir_pago_entre_ordenes(D("100"), [], fila=_fila_simple)
    assert filas == []
    assert sobra == D("100")


# --- el umbral -------------------------------------------------------------


def test_una_orden_ya_cubierta_se_saltea() -> None:
    """Un centavo de residuo no es una deuda, y sugerir 0,003 es ruido."""
    ordenes = [_orden("S1", "0.04"), _orden("S2", "50")]
    filas, _ = repartir_pago_entre_ordenes(D("100"), ordenes, fila=_fila_simple)
    assert [f["so_id"] for f in filas] == ["S2"]
    assert ordenes[0]["saldo_pendiente"] == D("0.04"), "no se tocó la ya cubierta"


def test_un_pago_practicamente_agotado_corta_el_reparto() -> None:
    ordenes = [_orden("S1", "50"), _orden("S2", "50")]
    filas, sobra = repartir_pago_entre_ordenes(UMBRAL_CUBIERTO, ordenes, fila=_fila_simple)
    assert filas == []
    assert sobra == UMBRAL_CUBIERTO


@pytest.mark.parametrize("saldo", ["0.05", "0.04", "0"])
def test_el_umbral_es_inclusivo_de_los_dos_lados(saldo) -> None:
    """0,05 cuenta como cubierto. Fijarlo importa: un `<` donde va `<=` haría
    aparecer sugerencias de centavos en cada corrida."""
    ordenes = [_orden("S1", saldo)]
    filas, _ = repartir_pago_entre_ordenes(D("100"), ordenes, fila=_fila_simple)
    assert filas == []


# --- LA semántica sutil ----------------------------------------------------


def test_una_sugerencia_que_no_se_ofrece_no_consume_nada() -> None:
    """El comportamiento que antes dependía de dónde estaba un `continue`.

    Un vendedor no ve las sugerencias que no son suyas. Esas no se ofrecen, así que
    no pueden aceptarse, así que **no comprometen** ni el pago ni el saldo de la
    orden. La consecuencia visible: la orden siguiente recibe el pago íntegro.
    """
    ordenes = [_orden("S1", "100"), _orden("S2", "100")]

    def solo_la_segunda(restante_antes, orden, a_aplicar):
        if orden["so_id"] == "S1":
            return None
        return _fila_simple(restante_antes, orden, a_aplicar)

    filas, sobra = repartir_pago_entre_ordenes(D("100"), ordenes, fila=solo_la_segunda)
    assert [f["so_id"] for f in filas] == ["S2"]
    assert filas[0]["monto_sugerido"] == D("100"), (
        "la invisible no consumió, así que la visible recibe el pago entero"
    )
    assert ordenes[0]["saldo_pendiente"] == D("100"), "la invisible quedó intacta"
    assert ordenes[1]["saldo_pendiente"] == D("0")
    assert sobra == D("0")


def test_si_ninguna_se_ofrece_el_pago_queda_entero() -> None:
    """Para un vendedor que no ve ninguna de las órdenes del cliente, el pago
    aparece sin aplicar -- y eso es correcto desde su vista."""
    ordenes = [_orden("S1", "50"), _orden("S2", "50")]
    filas, sobra = repartir_pago_entre_ordenes(D("100"), ordenes, fila=_nunca)
    assert filas == []
    assert sobra == D("100")
    assert [o["saldo_pendiente"] for o in ordenes] == [D("50"), D("50")]


# --- la mutación compartida, que evita sobreaplicar ------------------------


def test_dos_pagos_sobre_el_mismo_cliente_no_ofrecen_el_saldo_dos_veces() -> None:
    """La razón de que el reparto mute el dict de la orden.

    Los pagos de un cliente se reparten contra **la misma lista de órdenes**. Si el
    saldo no bajara, el segundo pago volvería a ofrecer el saldo completo y aceptar
    las dos sugerencias sobreaplicaría la orden -- que es exactamente el error que
    este blindaje midió en 1.333,85 USD sobre 20 órdenes.
    """
    ordenes = [_orden("S1", "100")]

    primeras, sobra1 = repartir_pago_entre_ordenes(D("60"), ordenes, fila=_fila_simple)
    segundas, sobra2 = repartir_pago_entre_ordenes(D("60"), ordenes, fila=_fila_simple)

    assert primeras[0]["monto_sugerido"] == D("60")
    assert segundas[0]["monto_sugerido"] == D("40"), (
        "el segundo pago ofreció más de lo que quedaba: el saldo no se descontó"
    )
    total_sugerido = primeras[0]["monto_sugerido"] + segundas[0]["monto_sugerido"]
    assert total_sugerido == D("100"), "entre los dos pagos no se puede ofrecer más que el saldo"
    assert (sobra1, sobra2) == (D("0"), D("20"))


def test_el_tercer_pago_ya_no_encuentra_nada_que_ofrecer() -> None:
    ordenes = [_orden("S1", "100")]
    for _ in range(2):
        repartir_pago_entre_ordenes(D("60"), ordenes, fila=_fila_simple)
    filas, sobra = repartir_pago_entre_ordenes(D("60"), ordenes, fila=_fila_simple)
    assert filas == []
    assert sobra == D("60")


# --- la medición A/B ------------------------------------------------------

CASOS_AB = [
    (D("100"), ["100"], set()),
    (D("100"), ["60", "60"], set()),
    (D("100"), ["10", "10"], set()),
    (D("100"), ["0.04", "50"], set()),
    (D("0.05"), ["50"], set()),
    (D("100"), ["100", "100"], {"S1"}),
    (D("100"), ["50", "50"], {"S1", "S2"}),
    (D("100"), [], set()),
]


@pytest.mark.parametrize("pago,saldos,invisibles", CASOS_AB)
def test_el_bucle_original_reconstruido_da_lo_mismo(pago, saldos, invisibles) -> None:
    """El cuerpo tal como estaba en ``app.py``, al lado del extraído.

    Si difieren, la extracción cambió un monto que alguien acepta con un clic.
    """

    def original(restante, ordenes, visible):
        filas = []
        for o in ordenes:
            if restante <= Decimal("0.05"):
                break
            if o["saldo_pendiente"] <= Decimal("0.05"):
                continue
            monto_aplicar = min(restante, o["saldo_pendiente"])
            item = {"so_id": o["so_id"], "monto_sugerido": monto_aplicar}
            if not visible(o["so_id"]):
                continue
            filas.append(item)
            restante -= monto_aplicar
            o["saldo_pendiente"] -= monto_aplicar
        return filas, restante

    def fila(restante_antes, orden, a_aplicar):
        if orden["so_id"] in invisibles:
            return None
        return {"so_id": orden["so_id"], "monto_sugerido": a_aplicar}

    viejas = [
        {"so_id": f"S{i + 1}", "saldo_pendiente": D(s)} for i, s in enumerate(saldos)
    ]
    nuevas = [
        {"so_id": f"S{i + 1}", "saldo_pendiente": D(s)} for i, s in enumerate(saldos)
    ]

    f_viejo, r_viejo = original(pago, viejas, lambda so: so not in invisibles)
    f_nuevo, r_nuevo = repartir_pago_entre_ordenes(pago, nuevas, fila=fila)

    assert f_viejo == f_nuevo
    assert r_viejo == r_nuevo
    assert [o["saldo_pendiente"] for o in viejas] == [o["saldo_pendiente"] for o in nuevas]
