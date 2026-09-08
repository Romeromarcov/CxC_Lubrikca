"""Plata del cliente que entró pero no está asignada a ninguna orden.

Decisión del usuario (septiembre 2026), textual: "en cobranza debería
aparecer ese saldo como pendiente por aplicar, y el fifo lo asigna cuando
haya una orden contra la cual aplicarlo. Hasta tanto aparece como saldo a
favor en los reportes de cxc".

Salió de revisar la bandeja "Pagos con Residual sin Aplicar". Medido
contra producción: de 252 residuales negativos (créditos del cliente),
54 eran remanentes de pagos ya aplicados -- sobró plata después de cubrir
la orden. Casos reales: Antonio Terán con 169.679,32 Bs sobrantes de un
pago de 225.192,00 (75%), Comercial los de Faria con 57.332,49 de
206.726,40 (28%).

Ninguno de los 118 clientes con plata sin asignar aparecía marcado con
saldo a favor: ``saldo_a_favor`` se calcula POR ORDEN (nació del caso
S00372, devolución con un abono encima), así que responde "pagó más de lo
que esta orden vale" pero nunca "le sobró plata que no está asignada a
ninguna orden". Son dos preguntas distintas.

Por qué NO se resta de ``saldo_priorizacion``: el FIFO ya ofrece ese
mismo remanente en Cobranza (36 de los 54 estaban ya en sus sugerencias),
así que descontarlo del saldo a cobrar lo contaría dos veces -- una como
crédito y otra cuando se aplique. Se muestra para que cobranza sepa que
antes de perseguir al cliente hay plata suya sin asignar.
"""

from __future__ import annotations

from cxc.web.app import saldo_priorizacion_cliente


def _cliente(**kw):
    base = {
        "saldo_a_favor": 0.0,
        "saldo_pendiente_por_aplicar": 0.0,
        "saldos": {"teorico_bs": 0.0, "teorico_usd": 0.0, "venta_real": 0.0, "factura_real": 0.0},
    }
    base.update(kw)
    base["tiene_saldo_a_favor"] = (
        base["saldo_a_favor"] > 0.05 or base["saldo_pendiente_por_aplicar"] > 0.05
    )
    return base


def test_un_pago_sin_asignar_marca_al_cliente_con_saldo_a_favor() -> None:
    """El caso que no se veía: sin sobrepago en ninguna orden concreta."""
    c = _cliente(saldo_pendiente_por_aplicar=208.51)
    assert c["tiene_saldo_a_favor"] is True


def test_el_sobrepago_por_orden_sigue_marcando() -> None:
    """Lo que ya funcionaba -- caso S00372."""
    assert _cliente(saldo_a_favor=45.0)["tiene_saldo_a_favor"] is True


def test_las_dos_vias_conviven() -> None:
    c = _cliente(saldo_a_favor=45.0, saldo_pendiente_por_aplicar=208.51)
    assert c["tiene_saldo_a_favor"] is True
    assert round(c["saldo_a_favor"] + c["saldo_pendiente_por_aplicar"], 2) == 253.51


def test_un_cliente_sin_nada_a_favor_no_se_marca() -> None:
    assert _cliente()["tiene_saldo_a_favor"] is False


def test_centavos_no_son_saldo_a_favor() -> None:
    assert _cliente(saldo_pendiente_por_aplicar=0.04)["tiene_saldo_a_favor"] is False
    assert _cliente(saldo_pendiente_por_aplicar=0.06)["tiene_saldo_a_favor"] is True


def test_el_pendiente_por_aplicar_no_baja_la_urgencia_de_cobro() -> None:
    """La guarda contra el doble conteo: el FIFO ya ofrece ese remanente en
    Cobranza. Si además lo restáramos del saldo a cobrar, el mismo dinero
    contaría dos veces y el cliente dejaría de perseguirse por plata que
    todavía no se aplicó a nada."""
    c = _cliente(saldo_pendiente_por_aplicar=5000.0)
    c["saldos"]["venta_real"] = 17316.27
    assert saldo_priorizacion_cliente(c["saldos"]) == 17316.27
