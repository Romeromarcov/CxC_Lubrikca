"""El saldo que consume el FIFO, y las cuatro trampas que lo rodean (Fase 2.4).

De este número depende si una orden sale de la cuenta por cobrar. Vivía en el medio
de un bloque de 223 líneas dentro de `_get_reporte_saldos_sync` y **no tenía un solo
test propio**, con cuatro subtilezas documentadas en comentarios — cada una con su
historia de bug real.

Esos comentarios eran buena documentación y mala protección: explicaban la trampa sin
impedir que alguien la volviera a pisar. Cada uno es ahora un test.
"""

from __future__ import annotations

import pytest

from cxc.engine.saldos import (
    SaldosDeudores,
    saldos_deudores,
    valor_usd_de_notas_de_credito,
)

IVA = 0.16


def _linea(entregada=1.0):
    return {"cantidad": 1.0, "cantidad_entregada": entregada}


def _saldos(**kw):
    base = {
        "monto_total": 116.0,
        "monto_proyectado_usd": 116.0,
        "abono_bcv": 0.0,
        "abono_binance": 0.0,
        "descuentos_motor": 0.0,
        "notas_credito_usd": 0.0,
        "iva": IVA,
        "lineas": [_linea()],
    }
    base.update(kw)
    return saldos_deudores(**base)


# --- lo básico -------------------------------------------------------------


def test_sin_abonos_ni_descuentos_se_debe_todo() -> None:
    s = _saldos()
    assert s.deudor_bcv == 116.0
    assert s.con_descuento_bcv == 116.0
    assert s.motivo_del_cero is None


def test_el_abono_baja_la_deuda() -> None:
    s = _saldos(abono_bcv=40.0, abono_binance=30.0)
    assert s.deudor_bcv == 76.0
    assert s.deudor_lista_usd == 86.0


def test_un_sobrepago_no_deja_la_deuda_negativa() -> None:
    """El `max(0, …)` de siempre. Lo que tapa es saldo a favor, no deuda."""
    s = _saldos(abono_bcv=500.0, abono_binance=500.0)
    assert s.deudor_bcv == 0.0
    assert s.deudor_lista_usd == 0.0


# --- TRAMPA 1: el IVA vuelve al descuento antes de restarlo ---------------


def test_un_descuento_de_100_baja_la_deuda_en_116_no_en_100() -> None:
    """La trampa con la que se pierde plata sin que nada avise.

    Los descuentos del motor son sobre el **subtotal** (sin impuesto) y los saldos
    traen IVA. Restarlos directo **subestima** la deuda, y este saldo es el que
    consume el FIFO: una orden se daría por saldada con menos plata de la que hay
    que cobrar.
    """
    s = _saldos(monto_total=1160.0, monto_proyectado_usd=1160.0, descuentos_motor=100.0)
    assert s.deudor_bcv == 1160.0
    assert s.con_descuento_bcv == pytest.approx(1160.0 - 116.0)
    assert s.con_descuento_bcv != pytest.approx(
        1160.0 - 100.0
    ), "si el IVA no se reaplicara, el saldo quedaría 16 dólares por encima"


def test_las_notas_de_credito_de_odoo_NO_llevan_el_ajuste_de_iva() -> None:
    """Y eso es correcto: ya son documentos reales con impuesto incluido.

    Es la mitad que se olvida. Si se les reaplicara el IVA, una nota de crédito de
    116 reduciría la deuda en 134,56 — plata que el cliente no dejó de deber.
    """
    s = _saldos(monto_total=1160.0, monto_proyectado_usd=1160.0, notas_credito_usd=116.0)
    assert s.con_descuento_bcv == pytest.approx(1160.0 - 116.0)


def test_un_descuento_y_una_nota_juntos_usan_cada_uno_su_regla() -> None:
    s = _saldos(
        monto_total=1160.0,
        monto_proyectado_usd=1160.0,
        descuentos_motor=100.0,
        notas_credito_usd=50.0,
    )
    assert s.con_descuento_bcv == pytest.approx(1160.0 - 116.0 - 50.0)


# --- TRAMPA 2 y 3: la CxC nace con la entrega, salvo que ya entró dinero --


def test_sin_entrega_y_sin_abono_no_hay_nada_que_cobrar() -> None:
    """«La orden, si no ha sido entregada, no es susceptible de cobro».

    Sin esta guarda el FIFO le asignaría un pago a mercancía que nunca salió del
    depósito.
    """
    s = _saldos(lineas=[_linea(entregada=0.0)])
    assert s.deudor_bcv == 0.0
    assert s.deudor_lista_usd == 0.0
    assert s.motivo_del_cero is not None
    assert "nace con la entrega" in s.motivo_del_cero


def test_si_ya_entro_dinero_la_orden_cuenta_aunque_no_se_haya_entregado() -> None:
    """La excepción que el usuario señaló: «puede pasar el caso de que se registra
    primero el pago y luego la entrega o la misma orden»."""
    s = _saldos(lineas=[_linea(entregada=0.0)], abono_bcv=10.0)
    assert s.deudor_bcv == 106.0
    assert s.motivo_del_cero is None


def test_un_abono_de_menos_de_medio_centavo_no_cuenta_como_dinero_entrado() -> None:
    """El umbral es 0,005: un residuo de redondeo no reabre una orden."""
    s = _saldos(lineas=[_linea(entregada=0.0)], abono_bcv=0.004)
    assert s.deudor_bcv == 0.0
    assert s.motivo_del_cero is not None


def test_una_entrega_parcial_minima_ya_hace_la_orden_cobrable() -> None:
    s = _saldos(lineas=[_linea(entregada=0.01)])
    assert s.deudor_bcv == 116.0
    assert s.motivo_del_cero is None


# --- TRAMPA 4: «no sé» no es «no se entregó» ------------------------------


def test_sin_lineas_cargadas_la_deuda_NO_se_pone_en_cero() -> None:
    """La trampa que detectaron los e2e 29 y 46.

    Sin líneas no hay dato sobre la entrega. Tratar esa ausencia como cero dejaba
    en cero el saldo de cualquier orden cuyas líneas no se hubieran leído todavía,
    y **el FIFO se quedaba sin nada que repartir**.
    """
    for sin_datos in ([], None):
        s = _saldos(lineas=sin_datos)
        assert s.deudor_bcv == 116.0, f"con lineas={sin_datos!r} se puso en cero"
        assert s.motivo_del_cero is None


@pytest.mark.parametrize("valor", [None, "", "None"])
def test_una_linea_con_el_campo_vacio_tampoco_es_dato_de_entrega(valor) -> None:
    """Los tres valores de «no sé», y uno de ellos destapó algo.

    Con la columna del espejo en ``numeric`` solo ``None`` es alcanzable hoy; ``""``
    y el string ``"None"`` son resto de cuando el backend era Sheets.

    Pero este test, con el string ``"None"``, hizo reventar la versión recién
    extraída: **la guarda excluía ese valor y la conversión a ``float`` no**, y la
    conversión corría primero. Era así en ``app.py`` desde siempre; al no ser
    alcanzable nunca mordió. Una defensa cuyas dos mitades se contradicen no es
    una defensa, así que ahora coinciden.
    """
    s = _saldos(lineas=[{"cantidad": 1.0, "cantidad_entregada": valor}])
    assert s.deudor_bcv == 116.0
    assert s.motivo_del_cero is None


def test_una_linea_con_cero_explicito_SI_es_dato_de_entrega() -> None:
    """Y acá está la diferencia que importa: un cero puesto por el sync es un
    dato («no se entregó»), un vacío es la ausencia de dato («no sé»)."""
    s = _saldos(lineas=[{"cantidad": 1.0, "cantidad_entregada": 0.0}])
    assert s.deudor_bcv == 0.0
    assert s.motivo_del_cero is not None


def test_con_una_linea_vacia_y_otra_con_dato_manda_el_dato() -> None:
    s = _saldos(
        lineas=[
            {"cantidad": 1.0, "cantidad_entregada": None},
            {"cantidad": 1.0, "cantidad_entregada": 0.0},
        ]
    )
    assert s.deudor_bcv == 0.0, "hay al menos un dato de entrega, y dice cero"


# --- la quinta, que es decisión y no trampa: el caso TERA ----------------


def test_una_orden_con_el_descuento_no_otorgado_no_ve_su_deuda_reducida() -> None:
    """El caso TERA. Si se le calculó un descuento que no se le dio, la deuda es
    la completa -- y este saldo es el que consume el FIFO, así que sin la guarda
    la orden se daría por saldada con menos plata de la que hay que cobrar."""
    con = _saldos(monto_total=1160.0, monto_proyectado_usd=1160.0, descuentos_motor=100.0)
    sin = _saldos(
        monto_total=1160.0,
        monto_proyectado_usd=1160.0,
        descuentos_motor=100.0,
        descuento_no_otorgado=True,
    )
    assert con.con_descuento_bcv == pytest.approx(1044.0)
    assert sin.con_descuento_bcv == 1160.0, "no otorgado: la deuda no baja"


# --- las notas de crédito, en dólares ------------------------------------


def _tasa(fecha, _rows):
    return {"2026-03-01": 40.0, "2026-06-01": 100.0}.get(fecha, 0.0)


def test_una_nota_en_dolares_vale_su_monto() -> None:
    monto, nombres = valor_usd_de_notas_de_credito(
        [
            {
                "amount_total": 50.0,
                "currency_id": [2, "USD"],
                "invoice_date": "2026-03-01",
                "name": "NC-1",
            }
        ],
        "2026-06-01",
        [],
        _tasa,
    )
    assert monto == 50.0
    assert nombres == ["NC-1"]


def test_una_nota_en_bolivares_usa_la_tasa_de_SU_dia() -> None:
    """Una nota de marzo vale lo que valía en marzo, no lo de hoy.

    Con 40 en marzo y 100 en junio, 4.000 Bs de marzo son 100 USD; a la tasa de
    junio serían 40. Elegir mal la fecha cambia el monto 2,5 veces.
    """
    monto, _ = valor_usd_de_notas_de_credito(
        [{"amount_total": 4000.0, "currency_id": [1, "VES"], "invoice_date": "2026-03-01"}],
        "2026-06-01",
        [],
        _tasa,
    )
    assert monto == pytest.approx(100.0)


def test_sin_fecha_en_la_nota_se_usa_la_de_la_orden() -> None:
    monto, _ = valor_usd_de_notas_de_credito(
        [{"amount_total": 4000.0, "currency_id": [1, "VES"]}],
        "2026-06-01",
        [],
        _tasa,
    )
    assert monto == pytest.approx(40.0), "cayó a la fecha de la orden, junio"


def test_sin_tasa_para_esa_fecha_la_nota_no_se_cuenta() -> None:
    """La mina, desactivada el 11-sep-2026.

    Este test asertaba ``40000.0``: con tasa en cero, 40.000 Bs reducían la deuda
    en 40.000 **dólares**. Estaba preservado a propósito, con nombre, para que
    cambiarlo fuera una decisión y no un descubrimiento.

    La decisión llegó por otro lado. Al convertir el default de 2019 en error
    duro, ``tasa_bcv_de_dia`` pasó a devolver cero donde antes devolvía 36,50, y
    la mina se agrandó en vez de desaparecer: medido en la copia sin tasas, la
    nota de S00573 pasó de valuarse en 37.335,66 a valuarse en 1.362.751,66 sobre
    una orden de 1.860,48. El error duro no la creó, la destapó.

    Ahora la nota no se cuenta. Eso deja la deuda **más alta** de lo que
    corresponde, que es el lado seguro: se persigue un cobro que quizá ya está
    acreditado, en vez de dar por saldada una orden que no lo está.
    """
    monto, nombres = valor_usd_de_notas_de_credito(
        [
            {
                "amount_total": 40000.0,
                "currency_id": [1, "VES"],
                "invoice_date": "2099-01-01",
                "name": "NC-SIN-TASA",
            }
        ],
        "2099-01-01",
        [],
        _tasa,
    )
    assert monto == 0.0
    assert nombres == ["NC-SIN-TASA"], "la nota se lista aunque no se pueda valuar"


def test_una_nota_en_dolares_sin_tasa_si_se_cuenta() -> None:
    """El contraste: sin tasa solo se pierde la conversión, no el documento.

    Una nota emitida en dólares no necesita tasa ninguna, así que excluirla
    sería confundir «no puedo convertir» con «no puedo contar».
    """
    monto, _ = valor_usd_de_notas_de_credito(
        [{"amount_total": 250.0, "currency_id": [2, "USD"], "invoice_date": "2099-01-01"}],
        "2099-01-01",
        [],
        _tasa,
    )
    assert monto == 250.0


def test_varias_notas_se_suman_y_se_nombran_todas() -> None:
    monto, nombres = valor_usd_de_notas_de_credito(
        [
            {
                "amount_total": 10.0,
                "currency_id": [2, "USD"],
                "invoice_date": "2026-03-01",
                "name": "NC-1",
            },
            {
                "amount_total": 4000.0,
                "currency_id": [1, "VES"],
                "invoice_date": "2026-03-01",
                "name": "NC-2",
            },
        ],
        "2026-06-01",
        [],
        _tasa,
    )
    assert monto == pytest.approx(110.0)
    assert nombres == ["NC-1", "NC-2"]


def test_sin_notas_no_hay_monto_ni_nombres() -> None:
    assert valor_usd_de_notas_de_credito([], "2026-06-01", [], _tasa) == (0.0, [])


def test_una_moneda_ilegible_se_trata_como_dolares() -> None:
    """Preservado: `currency_id` puede venir `False` de Odoo."""
    monto, _ = valor_usd_de_notas_de_credito(
        [{"amount_total": 33.0, "currency_id": False, "invoice_date": "2026-03-01"}],
        "2026-06-01",
        [],
        _tasa,
    )
    assert monto == 33.0


# --- la medición A/B ------------------------------------------------------

CASOS_AB = [
    {
        "monto_total": 116.0,
        "monto_proyectado_usd": 116.0,
        "abono_bcv": 0.0,
        "abono_binance": 0.0,
        "descuentos_motor": 0.0,
        "notas_credito_usd": 0.0,
        "lineas": [_linea()],
    },
    {
        "monto_total": 1160.0,
        "monto_proyectado_usd": 1100.0,
        "abono_bcv": 100.0,
        "abono_binance": 50.0,
        "descuentos_motor": 100.0,
        "notas_credito_usd": 20.0,
        "lineas": [_linea()],
    },
    {
        "monto_total": 116.0,
        "monto_proyectado_usd": 116.0,
        "abono_bcv": 0.0,
        "abono_binance": 0.0,
        "descuentos_motor": 0.0,
        "notas_credito_usd": 0.0,
        "lineas": [_linea(0.0)],
    },
    {
        "monto_total": 116.0,
        "monto_proyectado_usd": 116.0,
        "abono_bcv": 5.0,
        "abono_binance": 0.0,
        "descuentos_motor": 0.0,
        "notas_credito_usd": 0.0,
        "lineas": [_linea(0.0)],
    },
    {
        "monto_total": 116.0,
        "monto_proyectado_usd": 116.0,
        "abono_bcv": 0.0,
        "abono_binance": 0.0,
        "descuentos_motor": 0.0,
        "notas_credito_usd": 0.0,
        "lineas": [],
    },
    {
        "monto_total": 100.0,
        "monto_proyectado_usd": 100.0,
        "abono_bcv": 200.0,
        "abono_binance": 200.0,
        "descuentos_motor": 50.0,
        "notas_credito_usd": 10.0,
        "lineas": [_linea()],
    },
]


@pytest.mark.parametrize("caso", CASOS_AB)
def test_el_calculo_original_reconstruido_da_lo_mismo(caso) -> None:
    """El cuerpo tal como estaba en ``app.py``, al lado del extraído.

    Si difieren, la extracción cambió el saldo que consume el FIFO — o sea, cuándo
    una orden sale de la cuenta por cobrar.
    """

    def original(
        monto_orig,
        monto_total_proyectado_usd,
        abono_bcv,
        abono_binance,
        total_descuentos_monto,
        ncs_odoo_monto_usd,
        order_lines,
        iva,
    ):
        saldo_deudor_bcv = max(0.0, monto_orig - abono_bcv)
        saldo_deudor_lista_usd = max(0.0, monto_total_proyectado_usd - abono_binance)
        entregado_crudo = sum(
            float(ln.get("cantidad_entregada") or 0) for ln in (order_lines or [])
        )
        hay_dato_de_entrega = bool(order_lines) and any(
            ln.get("cantidad_entregada") not in (None, "", "None") for ln in order_lines
        )
        if hay_dato_de_entrega and entregado_crudo <= 0.005 and abono_bcv <= 0.005:
            saldo_deudor_bcv = 0.0
            saldo_deudor_lista_usd = 0.0
        descuentos_motor_con_iva = total_descuentos_monto * (1 + iva)
        return (
            saldo_deudor_bcv,
            saldo_deudor_lista_usd,
            max(0.0, saldo_deudor_bcv - descuentos_motor_con_iva - ncs_odoo_monto_usd),
            max(0.0, saldo_deudor_lista_usd - descuentos_motor_con_iva - ncs_odoo_monto_usd),
        )

    viejo = original(
        caso["monto_total"],
        caso["monto_proyectado_usd"],
        caso["abono_bcv"],
        caso["abono_binance"],
        caso["descuentos_motor"],
        caso["notas_credito_usd"],
        caso["lineas"],
        IVA,
    )
    nuevo = saldos_deudores(iva=IVA, **caso)
    assert isinstance(nuevo, SaldosDeudores)
    assert viejo == (
        nuevo.deudor_bcv,
        nuevo.deudor_lista_usd,
        nuevo.con_descuento_bcv,
        nuevo.con_descuento_lista_usd,
    )


# --- el valor entregado, y la regla del obsequio (11-sep-2026) ---------------


def _ln(entregada=None, cantidad="0", precio="0", descuento="0"):
    return {
        "cantidad_entregada": entregada,
        "cantidad": cantidad,
        "precio_unitario": precio,
        "descuento": descuento,
    }


def test_el_valor_entregado_no_aplica_el_descuento_por_defecto() -> None:
    """La bandera viene en False: el comportamiento no cambia solo.

    Aplicarla mueve 204 órdenes y 8.719,06 USD sobre la copia de producción, de
    los cuales solo 107,42 son obsequios. Los otros no están autorizados.
    """
    from cxc.engine.saldos import valor_entregado_y_retenido

    lineas = [_ln(entregada="1", precio="35.81", descuento="99.99")]
    assert valor_entregado_y_retenido(lineas) == pytest.approx(35.81)


def test_con_la_bandera_puesta_el_obsequio_deja_de_contar_como_venta() -> None:
    """El caso real: S00671, S00674 y S00679, producto 1033.

    Regla del usuario: el producto regalado se carga con 99,99 % de descuento
    «para que no afectara la cxc». Con la bandera, la línea aporta 0,0036 en vez
    de 35,81.
    """
    from cxc.engine.saldos import valor_entregado_y_retenido

    lineas = [_ln(entregada="1", precio="35.81", descuento="99.99")]
    assert valor_entregado_y_retenido(lineas, aplicar_descuento=True) == pytest.approx(
        0.003581, abs=1e-6
    )


def test_la_entregada_ausente_cae_a_la_cantidad() -> None:
    """Comportamiento original, preservado: hay líneas sin la entregada."""
    from cxc.engine.saldos import valor_entregado_y_retenido

    for ausente in (None, "", "None"):
        assert valor_entregado_y_retenido(
            [_ln(entregada=ausente, cantidad="2", precio="10")]
        ) == pytest.approx(20.0)


def test_una_entregada_negativa_no_le_resta_valor_a_las_otras_lineas() -> None:
    """Pasa en los datos reales: S00925 y S00952 tienen −10 y −4.

    Es una devolución que supera la línea. Sin el piso por línea, restaría del
    total de la orden y haría desaparecer mercancía que sí está afuera.
    """
    from cxc.engine.saldos import valor_entregado_y_retenido

    lineas = [_ln(entregada="-10", precio="50"), _ln(entregada="2", precio="50")]
    assert valor_entregado_y_retenido(lineas) == pytest.approx(100.0)


def test_un_descuento_fuera_de_rango_no_inventa_un_factor() -> None:
    """Con 150 % el factor daría negativo, y con −20 % inflaría la venta."""
    from cxc.engine.saldos import valor_entregado_y_retenido

    for malo in ("150", "-20", "ilegible"):
        assert valor_entregado_y_retenido(
            [_ln(entregada="1", precio="10", descuento=malo)], aplicar_descuento=True
        ) == pytest.approx(10.0)


def test_el_diagnostico_separa_el_obsequio_de_los_descuentos_normales() -> None:
    """La separación es el punto: una suma sola haría parecer todo autorizado.

    El usuario autorizó que el obsequio no afecte la CxC. No autorizó los
    descuentos de línea normales, que son 8.611,64 de los 8.719,06 medidos.
    """
    from cxc.engine.saldos import diagnostico_de_obsequios

    lineas = [
        _ln(entregada="1", precio="35.81", descuento="99.99"),  # obsequio
        _ln(entregada="1", precio="100", descuento="10"),  # descuento normal
    ]
    d = diagnostico_de_obsequios(lineas)
    assert d["lineas_de_obsequio"] == 1
    assert d["brecha_de_obsequios"] == pytest.approx(35.81 - 0.003581, abs=1e-4)
    assert d["brecha_de_descuentos_normales"] == pytest.approx(10.0, abs=1e-4)
    assert d["brecha"] == pytest.approx(
        d["brecha_de_obsequios"] + d["brecha_de_descuentos_normales"], abs=1e-6
    )
    assert not d["cruza_el_umbral"]


def test_el_diagnostico_avisa_si_la_orden_quedaria_sin_nada_que_cobrar() -> None:
    """Una orden cuyas únicas líneas entregadas son obsequios sale del reporte.

    Medido: **cero** órdenes en la copia de producción están en ese caso, así que
    aplicar la regla no saca a ninguna. Pero el aviso existe porque el día que
    haya una, su desaparición no debería ser una sorpresa.
    """
    from cxc.engine.saldos import diagnostico_de_obsequios

    d = diagnostico_de_obsequios([_ln(entregada="1", precio="35.81", descuento="100")])
    assert d["cruza_el_umbral"]


def test_el_100_por_ciento_tambien_es_obsequio() -> None:
    """S00336 se carga con 100 %, no con 99,99: el corte tiene que cubrir las dos."""
    from cxc.engine.saldos import es_obsequio

    assert es_obsequio({"descuento": "100"})
    assert es_obsequio({"descuento": "99.99"})
    assert es_obsequio({"descuento": "99"})
    assert not es_obsequio({"descuento": "98.9"})
    assert not es_obsequio({"descuento": None})
