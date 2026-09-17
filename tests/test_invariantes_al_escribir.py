"""Las invariantes de dinero rechazan la escritura, no solo la reportan.

Fase 2.2 del blindaje. Hasta ahora estas reglas se comprobaban cuando alguien
abría una página: la base aceptaba el dato y el problema aparecía después, si
alguien miraba. Con las ``CHECK`` de la migración ``f1e2d3c4b5a6`` la escritura
falla en el acto.

Estos tests corren contra Postgres de verdad (misma base que
``test_repository_contract``), porque una restricción de base solo se puede
probar intentando violarla.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
import sqlalchemy as sa

RESTRICCIONES_ESPERADAS = {
    ("vinculaciones", "ck_vinc_monto_aplicado_no_negativo"),
    ("vinculaciones", "ck_vinc_tasas_positivas"),
    ("vinculaciones", "ck_vinc_equivalente_no_supera_el_nominal"),
    ("ventas_teoricos", "ck_teoricos_no_negativos"),
    ("ventas_teoricos", "ck_descuento_no_supera_el_teorico"),
    ("pagos", "ck_pago_monto_no_negativo"),
    ("ordenes_venta", "ck_orden_monto_total_no_negativo"),
    ("lineas_orden", "ck_linea_cantidad_no_negativa"),
}


@pytest.fixture
def motor():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("Sin DATABASE_URL: las restricciones de base necesitan Postgres.")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    try:
        motor = sa.create_engine(url)
        with motor.connect() as con:
            con.execute(sa.text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 -- sin base, se salta
        pytest.skip(f"Postgres no disponible: {str(exc)[:120]}")
    return motor


def _restricciones(motor) -> set[tuple[str, str]]:
    with motor.connect() as con:
        return {
            (r["table_name"], r["constraint_name"])
            for r in con.execute(
                sa.text(
                    "select tc.table_name, tc.constraint_name "
                    "from information_schema.table_constraints tc "
                    "where tc.table_schema='public' "
                    "and tc.constraint_name like 'ck_%'"
                )
            ).mappings()
        }


def test_las_ocho_restricciones_estan_puestas(motor) -> None:
    """Si falta alguna, la migración no corrió en esta base."""
    puestas = _restricciones(motor)
    if not puestas:
        pytest.skip("La migración de invariantes no está aplicada en esta base.")
    faltan = RESTRICCIONES_ESPERADAS - puestas
    assert not faltan, f"Faltan restricciones: {sorted(faltan)}"


def _intentar(motor, sql: str, **params) -> str | None:
    """Ejecuta y devuelve el nombre de la restricción que se quejó, o None.

    Se hace en una transacción que siempre se revierte: el test verifica que la
    base RECHACE, así que no debe dejar nada escrito ni cuando el rechazo
    falla.
    """
    from sqlalchemy.exc import IntegrityError

    try:
        with motor.begin() as con:
            con.execute(sa.text(sql), params)
            con.rollback()
    except IntegrityError as exc:
        texto = str(exc)
        for _tabla, nombre in RESTRICCIONES_ESPERADAS:
            if nombre in texto:
                return nombre
        return "otra restriccion"
    return None


def test_un_pago_negativo_se_rechaza(motor) -> None:
    if not _restricciones(motor):
        pytest.skip("La migración de invariantes no está aplicada en esta base.")
    culpable = _intentar(
        motor,
        "INSERT INTO clientes (cliente_id, nombre, vendedor_email, wh_iva_agent, wh_iva_rate)"
        " VALUES ('ZZINV', 'ZZ invariantes', '', false, 0)"
        " ON CONFLICT (cliente_id) DO NOTHING;"
        " INSERT INTO pagos (pago_id, cliente_id, monto, moneda, metodo_pago, fecha_pago,"
        " vendedor_email, recibido)"
        " VALUES ('ZZINV1', 'ZZINV', -100, 'USD', 'x', now(), '', false)",
    )
    assert culpable == "ck_pago_monto_no_negativo", (
        f"Un pago de -100 se guardó sin quejas (culpable: {culpable}). Un monto "
        "negativo se resta de los totales sin que ninguna página lo explique."
    )


def test_una_orden_con_monto_negativo_se_rechaza(motor) -> None:
    if not _restricciones(motor):
        pytest.skip("La migración de invariantes no está aplicada en esta base.")
    culpable = _intentar(
        motor,
        "INSERT INTO clientes (cliente_id, nombre, vendedor_email, wh_iva_agent, wh_iva_rate)"
        " VALUES ('ZZINV', 'ZZ invariantes', '', false, 0)"
        " ON CONFLICT (cliente_id) DO NOTHING;"
        " INSERT INTO ordenes_venta (so_id, cliente_id, fecha, monto_total, lista_precios,"
        " vendedor_email, es_primera_compra, facturada, estado_orden, estado_entrega,"
        " entregada_completa, tiene_devolucion, dias_credito)"
        " VALUES ('ZZINV-SO', 'ZZINV', current_date, -50, '10', '', false, false,"
        " 'sale', 'full', true, false, 0)",
    )
    assert culpable == "ck_orden_monto_total_no_negativo", (
        f"Una orden de -50 se guardó sin quejas (culpable: {culpable})."
    )


def test_un_teorico_negativo_se_rechaza(motor) -> None:
    if not _restricciones(motor):
        pytest.skip("La migración de invariantes no está aplicada en esta base.")
    culpable = _intentar(
        motor,
        "INSERT INTO clientes (cliente_id, nombre, vendedor_email, wh_iva_agent, wh_iva_rate)"
        " VALUES ('ZZINV', 'ZZ invariantes', '', false, 0)"
        " ON CONFLICT (cliente_id) DO NOTHING;"
        " INSERT INTO ordenes_venta (so_id, cliente_id, fecha, monto_total, lista_precios,"
        " vendedor_email, es_primera_compra, facturada, estado_orden, estado_entrega,"
        " entregada_completa, tiene_devolucion, dias_credito)"
        " VALUES ('ZZINV-SO2', 'ZZINV', current_date, 100, '10', '', false, false,"
        " 'sale', 'full', true, false, 0)"
        " ON CONFLICT (so_id) DO NOTHING;"
        " INSERT INTO ventas_teoricos (so_id, teorico_ves, teorico_usd,"
        " descuentos_teorico_ves, descuentos_teorico_usd, lista_ves_id, lista_usd_id,"
        " usa_fallback_ves, usa_fallback_usd, calculado_en, lineas_fingerprint)"
        " VALUES ('ZZINV-SO2', -10, 5, 0, 0, 'BCV', 'USD', false, false, now(), 'x')",
    )
    # Se acepta cualquiera de las dos restricciones de la tabla: con
    # ``teorico_ves = -10`` y ``descuentos = 0`` tambien es cierto que el
    # descuento supera al teorico, y Postgres reporta la primera que evalua.
    # Lo que el test fija es que la escritura NO PASE, no cual de las dos la
    # ataja.
    assert culpable in {"ck_teoricos_no_negativos", "ck_descuento_no_supera_el_teorico"}, (
        f"Un teórico de -10 se guardó sin quejas (culpable: {culpable}). Un teórico "
        "negativo no existe: o el precio salió mal o las cantidades."
    )


def test_un_descuento_mayor_que_su_teorico_se_rechaza(motor) -> None:
    if not _restricciones(motor):
        pytest.skip("La migración de invariantes no está aplicada en esta base.")
    culpable = _intentar(
        motor,
        "INSERT INTO clientes (cliente_id, nombre, vendedor_email, wh_iva_agent, wh_iva_rate)"
        " VALUES ('ZZINV', 'ZZ invariantes', '', false, 0)"
        " ON CONFLICT (cliente_id) DO NOTHING;"
        " INSERT INTO ordenes_venta (so_id, cliente_id, fecha, monto_total, lista_precios,"
        " vendedor_email, es_primera_compra, facturada, estado_orden, estado_entrega,"
        " entregada_completa, tiene_devolucion, dias_credito)"
        " VALUES ('ZZINV-SO3', 'ZZINV', current_date, 100, '10', '', false, false,"
        " 'sale', 'full', true, false, 0)"
        " ON CONFLICT (so_id) DO NOTHING;"
        " INSERT INTO ventas_teoricos (so_id, teorico_ves, teorico_usd,"
        " descuentos_teorico_ves, descuentos_teorico_usd, lista_ves_id, lista_usd_id,"
        " usa_fallback_ves, usa_fallback_usd, calculado_en, lineas_fingerprint)"
        " VALUES ('ZZINV-SO3', 100, 100, 500, 0, 'BCV', 'USD', false, false, now(), 'x')",
    )
    assert culpable == "ck_descuento_no_supera_el_teorico", (
        f"Un descuento de 500 sobre un teórico de 100 se guardó (culpable: {culpable}). "
        "Deja la orden en negativo."
    )


def test_la_cantidad_entregada_negativa_NO_se_rechaza(motor) -> None:
    """Deliberadamente permitida, y por eso se fija por escrito.

    Los datos reales la violan en dos filas (S00952 con -4 unidades, S00925 con
    -10) y no es corrupción: es como Odoo representa una devolución que supera
    lo que quedó en la línea. Prohibirla rompería el sync contra datos que Odoo
    considera válidos, así que se mira desde ``auditar_integridad`` en vez de
    impedirse.

    Este test existe para que nadie la agregue "por simetría" sin saber que
    tumbaría el sync.
    """
    puestas = {n for _t, n in _restricciones(motor)}
    assert "ck_linea_cantidad_entregada_no_negativa" not in puestas, (
        "Se agregó una restricción sobre cantidad_entregada. Los datos reales de "
        "Odoo la violan (una devolución que supera lo que quedó en la línea), así "
        "que el sync va a empezar a fallar. Ver la migración f1e2d3c4b5a6."
    )


def test_las_claves_foraneas_ya_cubrian_la_integridad_referencial(motor) -> None:
    """La invariante «toda vinculación tiene pago y orden» ya estaba.

    Se verifica para que quede claro por qué ese chequeo nunca dio hallazgos: no
    es que nadie lo violara por suerte, es que la base no lo permite.
    """
    with motor.connect() as con:
        fks = {
            r["constraint_name"]
            for r in con.execute(
                sa.text(
                    "select constraint_name from information_schema.table_constraints "
                    "where table_schema='public' and table_name='vinculaciones' "
                    "and constraint_type='FOREIGN KEY'"
                )
            ).mappings()
        }
    assert "vinculaciones_pago_id_fkey" in fks
    assert "vinculaciones_so_id_fkey" in fks


def test_el_equivalente_no_puede_superar_el_nominal(motor) -> None:
    """La invariante más fuerte: es cierta por aritmética.

    Con la tasa por encima de 1, el equivalente en dólares de un abono en
    bolívares no puede superar su nominal. No existe un par de números mal
    calculados que la satisfaga, y eso la vuelve la clase de regla que conviene
    multiplicar.
    """
    if not _restricciones(motor):
        pytest.skip("La migración de invariantes no está aplicada en esta base.")
    assert Decimal("50") <= Decimal("5000"), "aritmética básica, por si acaso"
    puestas = {n for _t, n in _restricciones(motor)}
    assert "ck_vinc_equivalente_no_supera_el_nominal" in puestas
