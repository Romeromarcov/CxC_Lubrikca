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
    odoo = _Odoo(
        {"sale.order": [{"name": "S00001", "amount_total": 500.0, "state": "sale"}]}
    )
    partidas = _llamar(
        {"S00001": _item()}, execute=odoo, repo=_Repo(ordenes=[_orden("S00001", "500")])
    )
    ordenes = [p for p in partidas if "rdenes" in p["concepto"]]
    assert ordenes and ordenes[0]["cuadra"], (
        f"Odoo dijo lo mismo que el espejo y la partida descuadró: {ordenes and ordenes[0]}"
    )


def test_una_cancelada_en_odoo_sale_de_los_dos_lados() -> None:
    """El sync mira una ventana de 48 h, así que el espejo puede decir `sale`
    donde Odoo ya dice `cancel`. La partida usa el estado en vivo y la excluye de
    **los dos** lados, así que cuadra — y la nota dice que comparó cero.

    Que quede fijado importa: si alguna vez la excluyera de un solo lado, esta
    partida se pondría roja en cada orden cancelada fuera de la ventana, que son
    muchas, y el balance se volvería inútil por ruido.
    """
    odoo = _Odoo(
        {"sale.order": [{"name": "S00001", "amount_total": 500.0, "state": "cancel"}]}
    )
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
        assert "Comparad" in nota or "NO SE COMPAR" in nota, (
            f"la partida «{p['concepto']}» no dice cuántos comparó: {nota!r}"
        )
