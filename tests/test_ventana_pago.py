from datetime import date

from cxc.engine.discounts import ventana_pago_vigente


def test_vencimiento_vigente_dentro_de_la_ventana():
    # emision 2026-01-01, dias_credito=30 -> vence 2026-01-31, +3 dias gracia = 2026-02-03
    assert ventana_pago_vigente(
        "vencimiento",
        3,
        date(2026, 2, 3),
        fecha_emision=date(2026, 1, 1),
        fecha_entrega=None,
        dias_credito=30,
    )


def test_vencimiento_fuera_de_la_ventana():
    assert not ventana_pago_vigente(
        "vencimiento",
        3,
        date(2026, 2, 4),
        fecha_emision=date(2026, 1, 1),
        fecha_entrega=None,
        dias_credito=30,
    )


def test_vencimiento_usa_fecha_entrega_si_esta_disponible():
    # entrega 2026-01-10, dias_credito=30 -> vence 2026-02-09, +0 dias = limite inclusive
    assert ventana_pago_vigente(
        "vencimiento",
        0,
        date(2026, 2, 9),
        fecha_emision=date(2026, 1, 1),
        fecha_entrega=date(2026, 1, 10),
        dias_credito=30,
    )
    assert not ventana_pago_vigente(
        "vencimiento",
        0,
        date(2026, 2, 10),
        fecha_emision=date(2026, 1, 1),
        fecha_entrega=date(2026, 1, 10),
        dias_credito=30,
    )


def test_emision_vigente_hasta_n_dias_despues_de_emitida():
    assert ventana_pago_vigente(
        "emision",
        1,
        date(2026, 1, 2),
        fecha_emision=date(2026, 1, 1),
        fecha_entrega=None,
        dias_credito=30,
    )
    assert not ventana_pago_vigente(
        "emision",
        1,
        date(2026, 1, 3),
        fecha_emision=date(2026, 1, 1),
        fecha_entrega=None,
        dias_credito=30,
    )


def test_entrega_vigente_hasta_n_dias_despues_de_entregada():
    assert ventana_pago_vigente(
        "entrega",
        5,
        date(2026, 1, 15),
        fecha_emision=date(2026, 1, 1),
        fecha_entrega=date(2026, 1, 10),
        dias_credito=30,
    )
    assert not ventana_pago_vigente(
        "entrega",
        5,
        date(2026, 1, 16),
        fecha_emision=date(2026, 1, 1),
        fecha_entrega=date(2026, 1, 10),
        dias_credito=30,
    )


def test_entrega_cae_a_emision_si_no_hay_fecha_de_entrega():
    assert ventana_pago_vigente(
        "entrega",
        1,
        date(2026, 1, 2),
        fecha_emision=date(2026, 1, 1),
        fecha_entrega=None,
        dias_credito=30,
    )
    assert not ventana_pago_vigente(
        "entrega",
        1,
        date(2026, 1, 3),
        fecha_emision=date(2026, 1, 1),
        fecha_entrega=None,
        dias_credito=30,
    )


def test_no_aplica_o_desconocido_siempre_vigente():
    assert ventana_pago_vigente(
        "no_aplica",
        0,
        date(2099, 1, 1),
        fecha_emision=date(2026, 1, 1),
        fecha_entrega=None,
        dias_credito=30,
    )
    assert ventana_pago_vigente(
        "",
        0,
        date(2099, 1, 1),
        fecha_emision=date(2026, 1, 1),
        fecha_entrega=None,
        dias_credito=30,
    )
    assert ventana_pago_vigente(
        "tipo_invalido",
        0,
        date(2099, 1, 1),
        fecha_emision=date(2026, 1, 1),
        fecha_entrega=None,
        dias_credito=30,
    )
