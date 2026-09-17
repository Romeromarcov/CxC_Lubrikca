"""Las ocho partidas que comparan contra Odoo, probadas sin la red (Fase 2.4).

Son las que importan. Una partida interna compara dos vistas que salen de la misma
función, así que no puede detectar que el número esté mal: un error en la función de
origen se propaga igual a los dos lados y sale verde. Estas comparan contra Odoo, la
única fuente que no somos nosotros.

Antes de extraerlas solo se podían ejercitar a través del endpoint, con las cuatro
páginas sustituidas y un Odoo de mentira montado a mano — media página de andamiaje
por caso. Acá el `execute` son tres líneas.

Lo que estos tests fijan, y que el test de punta a punta no puede: que cada partida
**esté conectada**. Una partida externa que siempre cuadra porque nunca llegó a leer
de Odoo se ve idéntica a una que cuadra de verdad, y eso ya pasó dos veces en este
proyecto (las dos partidas de tasa que reportaban «cero divergencias» sin haber
comparado un documento).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from cxc.engine.balance import partidas_externas

# --- el Odoo de mentira ----------------------------------------------------


class _Odoo:
    """Devuelve lo que se le diga por modelo, y anota qué se le preguntó."""

    def __init__(self, respuestas: dict[str, object] | None = None) -> None:
        self.respuestas = respuestas or {}
        self.consultas: list[tuple[str, str]] = []

    def __call__(self, modelo, metodo, args, kwargs=None):
        self.consultas.append((modelo, metodo))
        valor = self.respuestas.get(modelo, [])
        return valor() if callable(valor) else valor


class _Tasas:
    """El objeto ``Tasas`` reducido a lo que las partidas le piden."""

    def __init__(self, usd=None, eur=None) -> None:
        self._usd, self._eur = usd, eur

    def bcv_usd(self, _fecha, arrastrar=True):
        return self._usd

    def bcv_eur(self, _fecha, arrastrar=True):
        return self._eur


class _Repo:
    def __init__(self, ordenes=(), facturas=(), pagos=(), vincs=()) -> None:
        self._ordenes, self._facturas = list(ordenes), list(facturas)
        self._pagos, self._vincs = list(pagos), list(vincs)

    def all_ordenes(self):
        return self._ordenes

    def all_facturas(self):
        return self._facturas

    def all_pagos(self):
        return self._pagos

    def all_vinculaciones(self):
        return self._vincs

    def all_clientes(self):
        return []

    def all_lineas(self):
        return []

    def all_entregas(self):
        return []

    def all_lineas_factura(self):
        return []


def _factura(factura_id="12345", monto="100"):
    return SimpleNamespace(
        factura_id=factura_id,
        so_id="S00001",
        numero="00000001",
        monto_total=Decimal(monto),
        estado="posted",
        fecha=date(2026, 3, 1),
        moneda="USD",
    )


def _pago(pago_id="777", monto="100"):
    return SimpleNamespace(
        pago_id=pago_id,
        cliente_id="C1",
        monto=Decimal(monto),
        moneda="USD",
        fecha_pago=date(2026, 3, 1),
        metodo_pago="1",
        numero_pago_odoo="PBAMI/2026/00001",
    )


def _orden(so_id="S00001", monto="100", estado="sale"):
    return SimpleNamespace(
        so_id=so_id,
        monto_total=Decimal(monto),
        estado_orden=estado,
        fecha=date(2026, 3, 1),
        cliente_id="C1",
        tiene_devolucion=False,
        entregada_completa=True,
        facturada=False,
        factura_id=None,
        monto_facturado=Decimal("0"),
    )


def _item(**kw):
    base = {
        "sale_de_cxc": False,
        "estado_cobro": "pendiente_cobro",
        "venta_real": 100.0,
        "por_cobrar_real": 100.0,
        "cliente_nombre": "CLIENTE UNO",
    }
    base.update(kw)
    return base


def _llamar(items=None, saldos=None, *, execute, repo=None, tasas=None, tasa=None):
    return partidas_externas(
        items if items is not None else {},
        saldos if saldos is not None else {},
        execute=execute,
        repo=repo if repo is not None else _Repo(),
        tasas=tasas if tasas is not None else _Tasas(),
        tasa_de_la_fecha=tasa if tasa is not None else (lambda _f: Decimal("0")),
    )


# --- el contrato de abstenerse ---------------------------------------------


def test_sin_conexion_no_emite_ninguna_partida() -> None:
    """El balance se queda con sus 15 internas, que es lo que ya hacía.

    Importa que sea una lista VACÍA y no partidas en cero: una partida externa en
    cero se lee como «comparé y coincide», y lo que pasó es que no se comparó.
    """
    assert _llamar(execute=None) == []


def test_con_conexion_pero_sin_datos_igual_emite_las_partidas() -> None:
    """Con Odoo respondiendo vacío las partidas salen, y eso es correcto:
    hay una comparación hecha, y su resultado es que los dos lados están en cero."""
    partidas = _llamar({"S00001": _item()}, execute=_Odoo())
    assert partidas, "con conexión tiene que haber comparado algo"
    assert all(p["tipo"] in {"externa", "invariante"} for p in partidas)


# --- que estén conectadas --------------------------------------------------


def test_los_tres_bloques_le_preguntan_a_odoo() -> None:
    """Si un bloque no consulta nada, su partida cuadra siempre y no sirve.

    Cada bloque necesita datos de SU clase en el espejo para llegar a preguntar:
    sin facturas no hay nada que comparar contra ``account.move``, y eso está
    bien. El test le da las tres clases para que los tres lleguen.
    """
    odoo = _Odoo()
    repo = _Repo(ordenes=[_orden()], facturas=[_factura()], pagos=[_pago()])
    _llamar({"S00001": _item()}, execute=odoo, repo=repo)
    modelos = {m for m, _ in odoo.consultas}
    assert "sale.order" in modelos, modelos
    assert "account.move" in modelos, modelos


def test_sin_facturas_en_el_espejo_no_se_consulta_odoo_por_ellas() -> None:
    """Y eso es correcto: no hay nada que comparar.

    Vale fijarlo porque es la diferencia entre «no comparé porque no había nada»
    y «no comparé porque me olvidé», que desde afuera se ven igual.
    """
    odoo = _Odoo()
    _llamar({"S00001": _item()}, execute=odoo, repo=_Repo(ordenes=[_orden()]))
    assert "account.move" not in {m for m, _ in odoo.consultas}


def test_una_orden_que_odoo_no_tiene_no_descuadra_pero_la_nota_lo_dice() -> None:
    """Escribí este test esperando un rojo y me equivoqué. El verde es correcto.

    La partida compara **montos**, y su nota lo declara: «se comparan solo las que
    existen en ambos lados». Comparar el monto de una orden que un lado no tiene
    no significa nada, así que excluirla de los dos lados es lo correcto — y con
    una sola orden ausente, los dos lados quedan en cero y cuadra.

    **La ausencia sí se detecta, en otro instrumento**:
    ``scripts/conciliar_espejo_odoo.py`` la reporta nominalmente («17 en el espejo
    y no en Odoo»), que es justo lo que el plan pedía de la Fase 1.4. Esta partida
    no es el lugar.

    Lo que este test exige es que la nota **diga el hueco**, porque un verde con
    «0 de 1» en la glosa es información y un verde pelado sería una mentira. Es la
    misma regla que se le aplicó a las dos partidas de tasa.
    """
    odoo = _Odoo({"sale.order": []})
    partidas = _llamar(
        {"S00001": _item()}, execute=odoo, repo=_Repo(ordenes=[_orden("S00001", "500")])
    )
    ordenes = [p for p in partidas if "rdenes" in p["concepto"]]
    assert ordenes, "no se emitió la partida de órdenes"
    assert ordenes[0]["cuadra"], "los dos lados quedan en cero: cuadra por construcción"
    assert "0 órdenes vivas en Odoo de 1" in ordenes[0]["nota"], ordenes[0]["nota"]


def test_cuando_odoo_confirma_el_monto_la_partida_cuadra() -> None:
    """La otra mitad: si cuadra siempre, tampoco sirve."""
    odoo = _Odoo({"sale.order": [{"name": "S00001", "amount_total": 500.0, "state": "sale"}]})
    partidas = _llamar(
        {"S00001": _item()}, execute=odoo, repo=_Repo(ordenes=[_orden("S00001", "500")])
    )
    ordenes = [p for p in partidas if "rdenes" in p["concepto"]]
    assert (
        ordenes and ordenes[0]["cuadra"]
    ), f"Odoo dijo lo mismo que el espejo y la partida descuadró: {ordenes and ordenes[0]}"


def test_una_cancelada_en_odoo_sale_de_los_dos_lados() -> None:
    """El sync mira una ventana de 48 h, así que el espejo puede decir `sale`
    donde Odoo ya dice `cancel`. La partida usa el estado en vivo y la excluye de
    **los dos** lados, así que cuadra — y la nota dice que comparó cero.

    Que quede fijado importa: si alguna vez la excluyera de un solo lado, esta
    partida se pondría roja en cada orden cancelada fuera de la ventana, que son
    muchas, y el balance se volvería inútil por ruido.
    """
    odoo = _Odoo({"sale.order": [{"name": "S00001", "amount_total": 500.0, "state": "cancel"}]})
    partidas = _llamar(
        {"S00001": _item()}, execute=odoo, repo=_Repo(ordenes=[_orden("S00001", "500")])
    )
    ordenes = [p for p in partidas if "rdenes" in p["concepto"]]
    assert ordenes
    assert ordenes[0]["cuadra"]
    assert ordenes[0]["izquierda"]["valor"] == 0.0
    assert ordenes[0]["derecha"]["valor"] == 0.0
    assert "0 órdenes vivas en Odoo de 1" in ordenes[0]["nota"]


# --- las clases, que no son decorativas ------------------------------------


def test_hay_una_invariante_y_es_una_sola() -> None:
    """La más fuerte de las 24: no compara dos vistas, verifica aritmética."""
    partidas = _llamar({"S00001": _item()}, execute=_Odoo())
    invariantes = [p for p in partidas if p["tipo"] == "invariante"]
    assert len(invariantes) == 1, [p["concepto"] for p in invariantes]


def test_las_demas_son_externas() -> None:
    partidas = _llamar({"S00001": _item()}, execute=_Odoo())
    externas = [p for p in partidas if p["tipo"] == "externa"]
    assert len(externas) == 8, [p["concepto"] for p in externas]


def test_ninguna_sale_como_interna() -> None:
    """Si una se cuela como interna, el balance dice que tiene más capacidad de
    detección de la que tiene -- y menos de la que reporta."""
    partidas = _llamar({"S00001": _item()}, execute=_Odoo())
    assert not [p for p in partidas if p["tipo"] == "interna"]


# --- que Odoo se caiga a mitad de camino no tumba el balance ---------------


def test_si_una_consulta_a_odoo_revienta_las_demas_partidas_salen() -> None:
    """Cada bloque tiene su propio try: una falla no puede llevarse el balance.

    Es lo contrario de tragar el error -- el bloque que falló no emite su partida,
    así que su ausencia es visible en el conteo.
    """

    def revienta():
        raise ConnectionError("Odoo cortó")

    odoo = _Odoo({"sale.order": revienta})
    partidas = _llamar({"S00001": _item()}, execute=odoo, repo=_Repo(ordenes=[_orden()]))
    assert partidas, "una falla en un bloque se llevó todas las partidas"


def test_todas_las_partidas_tienen_la_forma_completa() -> None:
    """La pantalla las pinta sin preguntar: si falta una clave, rompe.

    Y sirve para lo contrario también: este test es el que avisó que las tres
    claves nuevas del 11-sep-2026 (``tolerancia``, ``margen_usado``,
    ``al_limite``) llegaban a **todas** las partidas y no sólo a las internas.
    Que las 24 pasen por ``crear_partida`` es lo que lo garantiza.
    """
    partidas = _llamar({"S00001": _item()}, execute=_Odoo())
    for p in partidas:
        assert set(p) == {
            "concepto",
            "izquierda",
            "derecha",
            "diferencia",
            "cuadra",
            "tolerancia",
            "margen_usado",
            "al_limite",
            "nota",
            "tipo",
        }, p
        assert set(p["izquierda"]) == {"vista", "valor"}
        assert isinstance(p["cuadra"], bool)
        assert isinstance(p["al_limite"], bool)


@pytest.mark.parametrize("usd,eur", [(None, None), (Decimal("36.5"), None), (None, Decimal("40"))])
def test_sin_tasa_nuestra_las_partidas_de_tasa_no_mienten(usd, eur) -> None:
    """La trampa que este proyecto ya cazó dos veces.

    Las partidas que despejan la tasa implícita de un documento saltean el que no
    tiene tasa nuestra para esa fecha. Con la serie vacía salteaban todos y
    reportaban «cero divergencias», que se lee igual que «verifiqué y está bien».
    Ahora dicen cuántos compararon; este test fija que lo digan.
    """
    partidas = _llamar({"S00001": _item()}, execute=_Odoo(), tasas=_Tasas(usd, eur))
    de_tasa = [p for p in partidas if "tasa" in p["concepto"].lower()]
    for p in de_tasa:
        nota = p["nota"]
        assert (
            "Comparad" in nota or "NO SE COMPAR" in nota
        ), f"la partida «{p['concepto']}» no dice cuántos comparó: {nota!r}"


# --- las dos partidas de tasa, con un pago que SÍ se puede comparar ----------------
#
# El test de arriba fija que sin tasa nuestra las partidas digan «no se comparó». Éste
# es el otro lado: un abono en bolívares con tasa nuestra y `amount_ref` de Odoo, para
# que el cuerpo de la comparación corra. Sin esto, 56 líneas del módulo --justamente
# las que despejan la tasa estampada-- no las ejercitaba ningún test, que es el mismo
# hueco que este archivo existe para cerrar: una partida cuyo cuerpo nunca corre da
# cero, y cero se lee como «verificado».


def _pago_ves(pago_id="777", monto_ves="82774", fecha=date(2026, 3, 1)):
    p = _pago(pago_id, monto_ves)
    p.moneda = "VES"
    p.fecha_pago = __import__("datetime").datetime.combine(fecha, __import__("datetime").time())
    return p


def _odoo_con_pago(pago_id="777", amount_ref="100.00"):
    """Odoo tiene el pago vivo y le estampó un equivalente en dólares."""
    return _Odoo(
        {
            "account.payment": [
                {
                    "id": int(pago_id),
                    "amount": 82774.0,
                    "state": "posted",
                    "currency_id": [2, "VES"],
                    "amount_ref": float(amount_ref),
                }
            ]
        }
    )


def _partida(partidas, fragmento):
    (p,) = (p for p in partidas if fragmento in p["concepto"])
    return p


def test_un_pago_en_VES_con_tasa_nuestra_SE_COMPARA_y_cuadra_cuando_odoo_coincide() -> None:
    """82.774 Bs a tasa 827,74 = 100 USD, y Odoo estampó 100,00: cuadra, y lo dice."""
    partidas = _llamar(
        {"S00001": _item()},
        execute=_odoo_con_pago(amount_ref="100.00"),
        repo=_Repo(pagos=[_pago_ves()]),
        tasas=_Tasas(usd=Decimal("827.74")),
    )
    p = _partida(partidas, "en los pagos coincide con el BCV")
    assert "Comparados 1" in p["nota"], p["nota"]
    assert p["derecha"]["valor"] == 0.0, "cero desviados, y esta vez sí se miró"
    assert p["cuadra"] is True


def test_un_pago_cuya_tasa_estampada_se_desvia_mas_del_2pct_SALE_NOMBRADO() -> None:
    """Odoo estampó 90 USD por 82.774 Bs: tasa implícita 919,7 contra 827,74 oficial,
    +11 %. Es el caso que la partida existe para atrapar: un día con la tasa mal
    cargada en Odoo."""
    partidas = _llamar(
        {"S00001": _item()},
        execute=_odoo_con_pago(amount_ref="90.00"),
        repo=_Repo(pagos=[_pago_ves()]),
        tasas=_Tasas(usd=Decimal("827.74")),
    )
    p = _partida(partidas, "en los pagos coincide con el BCV")
    assert p["derecha"]["valor"] == 1.0
    assert "pago 777" in p["nota"]
    assert "919" in p["nota"] and "827" in p["nota"], "nombra las dos tasas"
    assert p["cuadra"] is False


def test_un_abono_cobrado_en_EUROS_no_cuenta_como_tasa_mal() -> None:
    """Su tasa implícita es la del euro, ~16 % arriba del dólar, y está bien.

    Sin esta excepción cada abono en euros aparecía como «tasa mal cargada» cuando la
    tasa está perfecta -- solo es otra moneda.
    """
    # 82.774 Bs / 963,21 (BCV euro) = 85,94 USD-equivalente; Odoo estampó eso.
    partidas = _llamar(
        {"S00001": _item()},
        execute=_odoo_con_pago(amount_ref="85.94"),
        repo=_Repo(pagos=[_pago_ves()]),
        tasas=_Tasas(usd=Decimal("827.74"), eur=Decimal("963.21")),
    )
    p = _partida(partidas, "en los pagos coincide con el BCV")
    assert p["derecha"]["valor"] == 0.0, "no es una desviación: es un abono en euros"
    assert "Comparados 1" in p["nota"]


def test_el_redondeo_de_amount_ref_a_dos_decimales_no_dispara_la_partida() -> None:
    """El 2 % de margen existe por esto: en un abono chico, dos decimales de
    `amount_ref` mueven centésimas de punto de la tasa despejada."""
    partidas = _llamar(
        {"S00001": _item()},
        execute=_Odoo(
            {
                "account.payment": [
                    {
                        "id": 777,
                        "amount": 8.0,
                        "state": "posted",
                        "currency_id": [2, "VES"],
                        "amount_ref": 0.01,
                    }
                ]
            }
        ),
        repo=_Repo(pagos=[_pago_ves(monto_ves="8")]),
        tasas=_Tasas(usd=Decimal("827.74")),
    )
    p = _partida(partidas, "en los pagos coincide con el BCV")
    # 8 / 0.01 = 800 contra 827,74: -3,4 %, fuera del 2 %. Es un caso real de redondeo
    # que la partida SÍ marca; queda fijado para que el margen no se agrande sin verlo.
    assert p["derecha"]["valor"] == 1.0


def test_la_partida_de_equivalentes_suma_con_NUESTRA_tasa_y_compara_contra_amount_ref() -> None:
    """La otra partida de tasa: no despeja tasas, suma los equivalentes en dólares de
    todos los pagos --los VES convertidos con nuestra serie, los USD tal cual-- y los
    compara contra la suma de `amount_ref` de Odoo."""
    odoo = _Odoo(
        {
            "account.payment": [
                {
                    "id": 777,
                    "amount": 82774.0,
                    "state": "posted",
                    "currency_id": [2, "VES"],
                    "amount_ref": 100.0,
                },
                {
                    "id": 778,
                    "amount": 50.0,
                    "state": "posted",
                    "currency_id": [1, "USD"],
                    "amount_ref": 50.0,
                },
            ]
        }
    )
    partidas = _llamar(
        {"S00001": _item()},
        execute=odoo,
        repo=_Repo(pagos=[_pago_ves(), _pago("778", "50")]),
        tasas=_Tasas(usd=Decimal("827.74")),
    )
    p = _partida(partidas, "equivalente BCV contra Odoo")
    assert abs(p["izquierda"]["valor"] - 150.0) < 0.01, "100 del VES convertido + 50 del USD"
    assert abs(p["derecha"]["valor"] - 150.0) < 0.01
    assert p["cuadra"] is True


# --- la partida de tasa de FACTURAS, con una factura que sí se puede comparar ------


def _factura_en_odoo(
    factura_id=12345, residual_ves=82774.0, residual_usd=100.0, fecha="2026-03-01"
):
    return {
        "id": factura_id,
        "name": "00000001",
        "state": "posted",
        "amount_total": residual_ves,
        "amount_residual": residual_ves,
        "amount_residual_usd": residual_usd,
        "invoice_date": fecha,
        "invoice_origin": "S00001",
        "move_type": "out_invoice",
    }


def _mundo_con_factura(residual_usd, tasas):
    """Una orden facturada, su factura en el espejo, y la misma factura viva en Odoo con
    su residual en las dos monedas. Es lo mínimo para que la partida compare una."""
    orden = _orden()
    orden.facturada = True
    orden.factura_id = "12345"
    saldos = {"S00001": {"so_id": "S00001", "factura_id": "12345", "saldo_factura_odoo": 100.0}}
    return _llamar(
        {"S00001": _item()},
        saldos,
        execute=_Odoo({"account.move": [_factura_en_odoo(residual_usd=residual_usd)]}),
        repo=_Repo(ordenes=[orden], facturas=[_factura()]),
        tasas=tasas,
    )


def test_una_factura_con_tasa_nuestra_SE_COMPARA_y_cuadra_cuando_odoo_coincide() -> None:
    """82.774 Bs de residual, 100 USD de residual en dólares: tasa implícita 827,74,
    igual a la oficial. Comparada 1, divergentes 0, y esta vez el cero se ganó."""
    partidas = _mundo_con_factura(residual_usd=100.0, tasas=_Tasas(usd=Decimal("827.74")))
    p = _partida(partidas, "La tasa de Odoo coincide con el BCV")
    assert "Comparadas 1" in p["nota"], p["nota"]
    assert p["derecha"]["valor"] == 0.0
    assert p["cuadra"] is True


def test_una_factura_cuya_tasa_implicita_se_desvia_SALE_NOMBRADA() -> None:
    """Residual USD de 90 por 82.774 Bs: 919,7 contra 827,74, +11 %. Es el escenario
    «cargan una tasa equivocada en Odoo» de la Fase 3, que antes pasaba en verde porque
    la partida no comparaba ni una."""
    partidas = _mundo_con_factura(residual_usd=90.0, tasas=_Tasas(usd=Decimal("827.74")))
    p = _partida(partidas, "La tasa de Odoo coincide con el BCV")
    assert p["derecha"]["valor"] == 1.0
    assert "00000001" in p["nota"] and "919" in p["nota"]
    assert p["cuadra"] is False


def test_una_factura_ya_cobrada_no_entra_a_la_comparacion() -> None:
    """Residual cero en las dos monedas: no hay tasa que despejar de un cero."""
    orden = _orden()
    orden.facturada = True
    orden.factura_id = "12345"
    partidas = _llamar(
        {"S00001": _item()},
        {"S00001": {"so_id": "S00001", "factura_id": "12345", "saldo_factura_odoo": 0.0}},
        execute=_Odoo({"account.move": [_factura_en_odoo(residual_ves=0.0, residual_usd=0.0)]}),
        repo=_Repo(ordenes=[orden], facturas=[_factura()]),
        tasas=_Tasas(usd=Decimal("827.74")),
    )
    p = _partida(partidas, "La tasa de Odoo coincide con el BCV")
    assert "Comparadas 0" in p["nota"] or "NO SE COMPAR" in p["nota"], p["nota"]


def test_una_factura_sin_fecha_legible_se_saltea_sin_reventar() -> None:
    orden = _orden()
    orden.facturada = True
    orden.factura_id = "12345"
    partidas = _llamar(
        {"S00001": _item()},
        {"S00001": {"so_id": "S00001", "factura_id": "12345", "saldo_factura_odoo": 100.0}},
        execute=_Odoo({"account.move": [_factura_en_odoo(fecha="")]}),
        repo=_Repo(ordenes=[orden], facturas=[_factura()]),
        tasas=_Tasas(usd=Decimal("827.74")),
    )
    p = _partida(partidas, "La tasa de Odoo coincide con el BCV")
    assert p["derecha"]["valor"] == 0.0
