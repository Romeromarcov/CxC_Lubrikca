"""El sistema refleja los pagos como los aplicó Odoo, no como los adivinó.

Causa raíz del problema más grande que tuvo el sistema: el sync de pagos
filtraba ``is_reconciled = False``, así que un pago desaparecía del espejo
justo cuando Odoo terminaba de reconciliarlo. Medido en producción
(septiembre 2026): 886 pagos conciliados invisibles -- 95.939,80 USD y
69.341.862,59 VES sobre 461 órdenes -- contra 352 pagos visibles. El motor
trabajaba con menos de un tercio de la cobranza.

El efecto no era dejar de cobrar: Odoo cobraba igual. Era que una orden
pagada al 100 % se evaluaba como si estuviera a medio pagar, y las reglas
que exigen pago previo no disparaban. El cliente perdía descuentos ganados.

Los dos escenarios de cardinalidad que pidió cubrir el usuario existen los
dos en producción, y se fijan acá:

  A) Un pago repartido entre VARIAS órdenes -- 126 casos. Exige el monto
     PARCIAL por orden; atribuirle el pago completo a cada una lo duplica.
  B) Una orden pagada con VARIOS pagos -- 266 casos. Exige una Vinculación
     por pago, sin que la última pise a las anteriores.

Y un tercero que apareció al medir: Odoo parte una misma aplicación en
varias filas de ``account.partial.reconcile`` cuando el pago se aplicó en
dos momentos o medió un ajuste cambiario (el pago 200 aparece dos veces
contra la factura de S00638). Esas se suman en una sola Vinculación.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from cxc.models import (
    AplicacionConciliada,
    EstadoVinculacion,
    Moneda,
    Vinculacion,
)
from cxc.web.app import (
    _sincronizar_aplicaciones_conciliadas,
    agrupar_aplicaciones,
)

_TODOS_LOS_PAGOS = {
    "513",
    "890",
    "1224",
    "1502",
    "364",
    "1262",
    "200",
    "1206",
    "777",
    "1304",
}


def _apl(pago, so, monto, moneda=Moneda.USD, factura="F1", fecha=date(2026, 8, 1)):
    return AplicacionConciliada(
        pago_id=pago,
        so_id=so,
        factura_id=factura,
        monto=Decimal(monto),
        moneda=moneda,
        fecha_pago=fecha,
    )


class _RepoFalso:
    """Lo mínimo que toca ``_sincronizar_aplicaciones_conciliadas``."""

    def __init__(self, vinculaciones=None, pagos_en_espejo=None):
        self.vincs = {v.vinc_id: v for v in (vinculaciones or [])}
        self.escritas: list[Vinculacion] = []
        # None = el espejo tiene todos los pagos que hagan falta.
        self._pagos = pagos_en_espejo

    def all_vinculaciones(self):
        return list(self.vincs.values())

    def all_pagos(self):
        if self._pagos is None:
            ids = {v.pago_id for v in self.vincs.values()} | _TODOS_LOS_PAGOS
            return [SimpleNamespace(pago_id=p) for p in ids]
        return [SimpleNamespace(pago_id=p) for p in self._pagos]

    def update_vinculacion(self, v):
        self.vincs[v.vinc_id] = v
        self.escritas.append(v)

    # Tasas: el helper de app.py las lee por estas dos vías.
    def all_serie_tasas(self):
        return []

    def all_tasas_historicas_auditoria(self):
        return []

    def all_listas_precios_historicas(self):
        return []

    def get_orden(self, so_id):
        return None


def _vinc_local(pago, so, monto, estado=EstadoVinculacion.PENDIENTE):
    return Vinculacion(
        vinc_id=f"VINC_{pago}_{so}",
        pago_id=pago,
        so_id=so,
        monto_aplicado=Decimal(monto),
        hora_pago_confirmada=datetime(2026, 8, 1),
        tasa_bcv_aplicada=Decimal("100"),
        tasa_binance_aplicada=Decimal("120"),
        es_tasa_heredada=False,
        equiv_usd_bcv=Decimal(monto),
        equiv_usd_binance=Decimal(monto),
        confirmado_por="Auto-FIFO (daemon)",
        estado=estado,
        moneda_abono=Moneda.USD,
    )


# --- Escenario A: un pago, varias órdenes ---------------------------------


def test_un_pago_repartido_entre_varias_ordenes_conserva_el_parcial() -> None:
    """Caso real: el pago 513 toca S00214 y S00427. Cada orden recibe SU
    parte, no el pago completo."""
    apps = [_apl("513", "S00214", "1782044.86"), _apl("513", "S00427", "500000.00")]
    assert agrupar_aplicaciones(apps) == {
        ("513", "S00214"): Decimal("1782044.86"),
        ("513", "S00427"): Decimal("500000.00"),
    }


def test_el_reparto_no_duplica_el_pago() -> None:
    apps = [_apl("513", "S00214", "600"), _apl("513", "S00427", "400")]
    repo = _RepoFalso()
    _sincronizar_aplicaciones_conciliadas(repo, apps)
    assert sum(v.monto_aplicado for v in repo.escritas) == Decimal("1000")


# --- Escenario B: una orden, varios pagos ---------------------------------


def test_una_orden_pagada_con_varios_pagos_conserva_todos() -> None:
    """Caso real S00105: cinco pagos distintos contra la misma factura."""
    apps = [
        _apl("890", "S00105", "30000", Moneda.VES),
        _apl("1224", "S00105", "30000", Moneda.VES),
        _apl("1502", "S00105", "29134.45", Moneda.VES),
        _apl("364", "S00105", "10846.31", Moneda.VES),
        _apl("1262", "S00105", "30.00", Moneda.USD),
    ]
    repo = _RepoFalso()
    res = _sincronizar_aplicaciones_conciliadas(repo, apps)
    assert res["creadas"] == 5
    assert len({v.vinc_id for v in repo.escritas}) == 5
    assert {v.pago_id for v in repo.escritas} == {"890", "1224", "1502", "364", "1262"}


def test_un_pago_en_dolares_junto_a_otros_en_bolivares_mantiene_su_moneda() -> None:
    apps = [
        _apl("890", "S00105", "30000", Moneda.VES),
        _apl("1262", "S00105", "30.00", Moneda.USD),
    ]
    repo = _RepoFalso()
    _sincronizar_aplicaciones_conciliadas(repo, apps)
    por_pago = {v.pago_id: v for v in repo.escritas}
    assert por_pago["890"].moneda_abono == Moneda.VES
    assert por_pago["1262"].moneda_abono == Moneda.USD


# --- Parciales repetidos del mismo par -------------------------------------


def test_varios_parciales_del_mismo_pago_a_la_misma_orden_se_suman() -> None:
    """Caso real S00638: el pago 200 aparece dos veces contra la misma
    factura (190,26 + 128,01). Si el segundo pisara al primero se
    perderían 190,26."""
    apps = [
        _apl("200", "S00638", "190.26"),
        _apl("200", "S00638", "128.01"),
    ]
    assert agrupar_aplicaciones(apps) == {("200", "S00638"): Decimal("318.27")}
    repo = _RepoFalso()
    _sincronizar_aplicaciones_conciliadas(repo, apps)
    assert len(repo.escritas) == 1
    assert repo.escritas[0].monto_aplicado == Decimal("318.27")


# --- Odoo manda sobre el monto ---------------------------------------------


def test_odoo_corrige_al_fifo_cuando_asigno_de_mas() -> None:
    """Caso real: el FIFO le dio a S00472 el pago 1206 completo
    (225.192,00) cuando Odoo solo le aplicó 54.161,83. En producción los
    38 conflictos van todos en esta dirección."""
    repo = _RepoFalso([_vinc_local("1206", "S00472", "225192.00")])
    res = _sincronizar_aplicaciones_conciliadas(repo, [_apl("1206", "S00472", "54161.83")])
    assert res == {"creadas": 0, "corregidas": 1, "sin_cambio": 0, "omitidas": 0}
    assert repo.escritas[0].monto_aplicado == Decimal("54161.83")


def test_lo_que_viene_de_odoo_queda_conciliado() -> None:
    """La reconciliación de Odoo ES la confirmación -- no espera a que otro
    paso la promueva desde PENDIENTE."""
    repo = _RepoFalso([_vinc_local("1206", "S00472", "225192.00")])
    _sincronizar_aplicaciones_conciliadas(repo, [_apl("1206", "S00472", "54161.83")])
    assert repo.escritas[0].estado == EstadoVinculacion.CONCILIADO
    assert repo.escritas[0].confirmado_por == "Odoo (reconciliación)"


def test_reusa_el_vinc_id_existente_en_vez_de_duplicar() -> None:
    previa = _vinc_local("1206", "S00472", "225192.00")
    previa.vinc_id = "VINC_1206_S00999"  # re-apuntada en su momento
    repo = _RepoFalso([previa])
    _sincronizar_aplicaciones_conciliadas(repo, [_apl("1206", "S00472", "54161.83")])
    assert repo.escritas[0].vinc_id == "VINC_1206_S00999"
    assert len(repo.all_vinculaciones()) == 1


# --- Donde Odoo no opina, no se toca ---------------------------------------


def test_una_vinculacion_que_odoo_no_reconcilio_queda_intacta() -> None:
    """Una orden sin facturar NO puede tener parciales -- no hay documento
    que reconciliar. Su Vinculación FIFO se conserva en PENDIENTE."""
    sin_facturar = _vinc_local("777", "S00900", "500")
    repo = _RepoFalso([sin_facturar])
    res = _sincronizar_aplicaciones_conciliadas(repo, [_apl("513", "S00214", "100")])
    assert res["creadas"] == 1
    assert repo.vincs["VINC_777_S00900"] is sin_facturar
    assert repo.vincs["VINC_777_S00900"].estado == EstadoVinculacion.PENDIENTE


def test_sin_aplicaciones_no_escribe_nada() -> None:
    repo = _RepoFalso([_vinc_local("777", "S00900", "500")])
    assert _sincronizar_aplicaciones_conciliadas(repo, []) == {
        "creadas": 0,
        "corregidas": 0,
        "sin_cambio": 0,
        "omitidas": 0,
    }
    assert repo.escritas == []


def test_una_ya_conciliada_con_el_mismo_monto_no_se_reescribe() -> None:
    """Evita que cada ciclo del daemon vuelva a congelar tasas sobre lo
    mismo."""
    ya = _vinc_local("513", "S00214", "100", estado=EstadoVinculacion.CONCILIADO)
    repo = _RepoFalso([ya])
    res = _sincronizar_aplicaciones_conciliadas(repo, [_apl("513", "S00214", "100")])
    assert res == {"creadas": 0, "corregidas": 0, "sin_cambio": 1, "omitidas": 0}
    assert repo.escritas == []


# --- La clave foránea contra `pagos` -----------------------------------------


def test_una_aplicacion_sin_su_pago_en_el_espejo_se_omite() -> None:
    """``vinculaciones.pago_id`` es clave foránea contra ``pagos``: escribir
    sobre un pago ausente revienta la transacción y se lleva el ciclo
    entero por delante. Pasó de verdad al desplegar -- el sync incremental
    solo mira ``write_date`` de las últimas 48 h, y estos pagos Odoo los
    reconcilió hace meses. El llamador los rescata con ``pagos_por_id``;
    esta guarda es para el resto."""
    repo = _RepoFalso(pagos_en_espejo=["513"])
    res = _sincronizar_aplicaciones_conciliadas(
        repo, [_apl("513", "S00214", "100"), _apl("999", "S00300", "50")]
    )
    assert res == {"creadas": 1, "corregidas": 0, "sin_cambio": 0, "omitidas": 1}
    assert [v.pago_id for v in repo.escritas] == ["513"]
