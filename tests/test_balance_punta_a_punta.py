"""El balance de comprobación, armado de punta a punta.

Hasta la auditoría de septiembre de 2026 el balance tenía ~30 pruebas, y todas
ejercitaban sus **helpers**: la identidad del reporte por cliente, la tolerancia
del residual, el arqueo, la abstención cuando Ventas recalcula. Buenas pruebas,
y ninguna tocaba las 768 líneas que **ensamblan** las 24 partidas.

La diferencia práctica: estaba probado que cada regla del balance funciona, y no
estaba probado que el balance las use todas ni que las arme bien. Un ``partida()``
con los argumentos invertidos pasaba la suite entera.

Acá se llama al endpoint con las cuatro páginas de las que se alimenta ya
sustituidas por un juego de datos chico y coherente, y se verifica lo que solo se
puede ver de punta a punta: que emita las partidas que dice emitir, que cada una
compare lo que dice comparar, y que un descuadre puesto a propósito aparezca.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests import builders as b

# --- el juego de datos ------------------------------------------------------
#
# Tres órdenes que cubren los tres estados que el balance distingue: una
# cobrada, una por cobrar que calza, y una por cobrar con un pago parcial.
# Los montos están elegidos para que la identidad "venta − cobrado + saldo a
# favor = por cobrar" cierre exacta, así que cualquier descuadre que aparezca
# lo puso el código y no el fixture.


def _item_ventas(
    so_id: str,
    cliente: str,
    venta: float,
    pagado: float,
    *,
    cobrada: bool = False,
    facturado: float | None = None,
) -> dict[str, Any]:
    """Una fila de Ventas con las tres referencias en el mismo valor.

    Las tres (Venta Real, Teórico BS, Teórico USD) se dejan iguales a
    propósito: el balance las trata por separado y con el mismo número las
    tres partidas de identidad tienen que cuadrar las tres. Si una sola
    descuadra, el problema está en cómo el balance la arma.
    """
    return {
        "so_id": so_id,
        "cliente_nombre": cliente,
        "sale_de_cxc": cobrada,
        "estado_cobro": "cobrada" if cobrada else "por_cobrar",
        "facturada": True,
        "factura_id": f"F{so_id[-3:]}",
        "venta_neta_real": venta,
        "descuento_aplicado_sistema": 0.0,
        "monto_pagado_factura_odoo_incl_pendiente": pagado,
        "ves_neta_teorica_iva": venta,
        "pagado_teorico_bcv_incl_pendiente": pagado,
        "usd_neta_teorica_iva": venta,
        "pagado_teorico_binance_incl_pendiente": pagado,
        "total_facturado_neto": venta if facturado is None else facturado,
        "monto_total": venta,
    }


ORDENES = [
    _item_ventas("S00001", "Cliente Uno", venta=1000.0, pagado=1000.0, cobrada=True),
    _item_ventas("S00002", "Cliente Uno", venta=500.0, pagado=0.0),
    _item_ventas("S00003", "Cliente Dos", venta=800.0, pagado=300.0),
]


def _saldos_de(items: list[dict[str, Any]]) -> dict[str, Any]:
    """El Reporte de Saldos que corresponde a esas órdenes.

    Lista solo las que NO salieron de CxC: una orden cobrada que apareciera acá
    es justamente lo que la partida 1 existe para detectar.
    """
    return {
        "items": [dict(i) for i in items if not i["sale_de_cxc"]],
        "saldo_minimo_pendientes": [],
    }


def _cliente_de(items: list[dict[str, Any]]) -> dict[str, Any]:
    por_cliente: dict[str, dict[str, Any]] = {}
    for i in items:
        if i["sale_de_cxc"]:
            continue
        acc = por_cliente.setdefault(
            i["cliente_nombre"],
            {"cliente_nombre": i["cliente_nombre"], "documentos": [], "saldos": {}},
        )
        acc["documentos"].append(
            {
                "so_id": i["so_id"],
                "tipo": "orden",
                "venta_real": i["venta_neta_real"] - i["monto_pagado_factura_odoo_incl_pendiente"],
            }
        )
    return {"clientes": list(por_cliente.values())}


class _OdooFalso:
    """Un Odoo que confirma exactamente lo que dice el espejo.

    Las 8 partidas externas comparan contra él, así que con estos datos las 8
    tienen que cuadrar. Es la mitad del valor de este test: verifica que la
    comparación externa esté conectada, no solo que exista.
    """

    def __init__(self, items: list[dict[str, Any]], desviar: str | None = None) -> None:
        self._items = items
        self._desviar = desviar

    def __call__(self, modelo: str, metodo: str, args: Any, kwargs: Any = None) -> Any:
        if modelo == "sale.order":
            return [
                {
                    "id": n,
                    "name": i["so_id"],
                    "state": "sale",
                    "amount_total": (
                        i["monto_total"] * 2 if self._desviar == "ordenes" else i["monto_total"]
                    ),
                }
                for n, i in enumerate(self._items, start=1)
            ]
        if modelo == "account.move":
            return [
                {
                    "id": n,
                    "name": i["factura_id"],
                    "invoice_origin": i["so_id"],
                    "move_type": "out_invoice",
                    "state": "posted",
                    "amount_total": i["total_facturado_neto"],
                    "amount_total_signed_usd": i["total_facturado_neto"],
                    "amount_untaxed_signed_usd": i["total_facturado_neto"],
                    "amount_residual": (
                        i["venta_neta_real"] - i["monto_pagado_factura_odoo_incl_pendiente"]
                    ),
                    "amount_residual_usd": (
                        i["venta_neta_real"] - i["monto_pagado_factura_odoo_incl_pendiente"]
                    ),
                    "invoice_date": "2026-09-01",
                    "currency_id": [1, "USD"],
                }
                for n, i in enumerate(self._items, start=1)
            ]
        return []


@pytest.fixture
def balance(monkeypatch):
    """Llama al endpoint con las cuatro páginas sustituidas."""
    import asyncio

    from cxc.web import app as modulo

    def armar(items: list[dict[str, Any]], *, calculando: bool = False, desviar=None):
        async def ventas_falso(**_kw):
            return {"items": [] if calculando else items, "calculando": calculando}

        async def cliente_falso(**_kw):
            return _cliente_de(items)

        async def bandeja_falsa(**_kw):
            return {"ordenes_por_facturar": [], "notas_credito_pendientes": []}

        async def saldos_falso(**_kw):
            return _saldos_de(items)

        monkeypatch.setattr(modulo, "get_ventas", ventas_falso)
        monkeypatch.setattr(modulo, "get_reporte_cxc_cliente", cliente_falso)
        monkeypatch.setattr(modulo, "get_bandeja_facturacion", bandeja_falsa)
        monkeypatch.setattr(modulo, "get_reporte_saldos", saldos_falso)
        monkeypatch.setattr(modulo, "_connect", lambda cfg: _OdooFalso(items, desviar))
        monkeypatch.setattr(modulo, "_all_serie_tasas_rows", lambda repo: [])
        monkeypatch.setattr(modulo, "get_repo", lambda: _Espejo(items))
        return asyncio.run(modulo.get_balance_comprobacion())

    return armar


class _Espejo:
    """El espejo local que corresponde al mismo juego de datos.

    Las partidas externas comparan ESTO contra Odoo, no contra las filas de
    Ventas. Un repositorio vacio deja el lado izquierdo en cero y la partida
    descuadra por el motivo equivocado -- que es lo que paso la primera vez que
    corri este test, y es exactamente el falso negativo que la partida podria
    tener en produccion si el espejo se vaciara.
    """

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items

    def all_ordenes(self):
        # Se usan los builders de la suite, no ``OrdenVenta(...)`` a mano: los
        # modelos tienen campos obligatorios que a mano se olvidan, y el
        # balance traga la excepcion con un ``logger.warning``. La primera
        # version de este test construia el modelo incompleto, la partida
        # externa nunca se emitia, y el test "pasaba" verificando nada.
        return [
            b.orden(i["so_id"], monto_total=str(i["monto_total"]), facturada=True)
            for i in self._items
        ]

    def all_facturas(self):
        return [
            b.factura(
                str(n),
                so_id=i["so_id"],
                monto_total=str(i["total_facturado_neto"]),
                monto_total_signed_usd=str(i["total_facturado_neto"]),
            )
            for n, i in enumerate(self._items, start=1)
        ]

    def all_vinculaciones(self):
        return []

    def all_pagos(self):
        return []

    def all_serie_tasas(self):
        return []

    def all_tasas_historicas_auditoria(self):
        return []

    def get_config(self, key):
        return None


# --- lo que solo se ve de punta a punta -------------------------------------


def test_con_datos_el_balance_emite_sus_partidas(balance) -> None:
    datos = balance(ORDENES)
    assert datos.get("evaluable") is not False, datos.get("motivo")
    partidas = datos.get("partidas") or []
    assert len(partidas) >= 18, (
        f"El balance emitió {len(partidas)} partidas. Las 18 llamadas del código se "
        "convierten en ~24 en pantalla porque cuatro se emiten una vez por "
        "referencia; menos de 18 significa que alguna dejó de armarse."
    )


def test_toda_partida_emitida_trae_su_forma_completa(balance) -> None:
    """Una partida sin sus dos lados no se puede leer ni auditar."""
    for p in balance(ORDENES).get("partidas") or []:
        assert p.get("concepto"), p
        for lado in ("izquierda", "derecha"):
            assert lado in p, p
            assert "vista" in p[lado] and "valor" in p[lado], p
        assert "diferencia" in p and "cuadra" in p, p
        assert p.get("tipo") in {"interna", "externa", "invariante"}, p


def test_la_diferencia_es_siempre_izquierda_menos_derecha(balance) -> None:
    """El error que ninguna prueba de helper podía atrapar.

    ``partida()`` recibe los dos lados como posicionales. Invertirlos en un
    sitio de llamada no rompe nada visible: la partida sigue apareciendo, con la
    diferencia cambiada de signo. Esto lo fija.
    """
    for p in balance(ORDENES).get("partidas") or []:
        esperada = round(p["izquierda"]["valor"] - p["derecha"]["valor"], 2)
        assert p["diferencia"] == esperada, (
            f"«{p['concepto']}»: diferencia {p['diferencia']} contra "
            f"{p['izquierda']['valor']} − {p['derecha']['valor']} = {esperada}"
        )


def test_cuadra_es_coherente_con_la_diferencia(balance) -> None:
    """Una partida no puede decir que cuadra con una diferencia grande.

    La tolerancia varía por partida (0,5 por defecto, 1,0 en las de identidad),
    así que se verifica el lado que no admite discusión: una diferencia mayor a
    la tolerancia más grande NO puede estar en verde.
    """
    for p in balance(ORDENES).get("partidas") or []:
        if abs(p["diferencia"]) > 1.0:
            assert not p["cuadra"], (
                f"«{p['concepto']}» dice cuadrar con una diferencia de "
                f"{p['diferencia']}."
            )


def test_las_ocho_externas_cuadran_cuando_odoo_confirma(balance) -> None:
    """Con un Odoo que dice lo mismo que el espejo, las externas cuadran.

    Es la mitad del valor de este test: verifica que la comparación externa esté
    CONECTADA. Una partida externa que siempre cuadra porque no llegó a leer
    nada de Odoo se ve igual que una que cuadra de verdad.
    """
    partidas = balance(ORDENES).get("partidas") or []
    externas = [p for p in partidas if p.get("tipo") == "externa"]
    assert externas, "No se emitió ninguna partida externa."
    descuadradas = [p["concepto"] for p in externas if not p["cuadra"]]
    assert not descuadradas, (
        f"Odoo confirma exactamente lo que dice el espejo y aun así descuadran: "
        f"{descuadradas}"
    )


def test_un_descuadre_real_contra_odoo_se_reporta(balance) -> None:
    """Con Odoo diciendo el doble, la partida de órdenes tiene que morder.

    Es el complemento del test anterior: uno verifica que no haya falsos
    positivos, éste que no haya falsos negativos.
    """
    partidas = balance(ORDENES, desviar="ordenes").get("partidas") or []
    de_ordenes = [p for p in partidas if "Ventas reales" in p["concepto"]]
    assert de_ordenes, "La partida de ventas reales contra Odoo no se emitió."
    assert not de_ordenes[0]["cuadra"], (
        "Odoo reporta el doble del monto de cada orden y la partida sigue en "
        f"verde: {de_ordenes[0]}"
    )


def test_una_orden_cobrada_que_sigue_con_saldo_se_reporta(balance) -> None:
    """La partida 1, con el caso que existe para detectar."""
    items = [dict(i) for i in ORDENES]
    # La cobrada vuelve a aparecer en el Reporte de Saldos.
    items[0] = {**items[0], "sale_de_cxc": True}

    from cxc.web import app as modulo

    original = _saldos_de

    def saldos_con_la_cobrada(_items):
        salida = original(_items)
        salida["items"].append(dict(items[0]))
        return salida

    globals()["_saldos_de"] = saldos_con_la_cobrada
    try:
        partidas = balance(items).get("partidas") or []
    finally:
        globals()["_saldos_de"] = original
    assert modulo  # el import se usa para dejar claro contra qué se corre

    primera = [p for p in partidas if "cobradas" in p["concepto"]]
    assert primera, "La partida de órdenes cobradas no se emitió."
    assert not primera[0]["cuadra"], (
        "Una orden que salió de CxC sigue listada con saldo y la partida no lo "
        f"reporta: {primera[0]}"
    )


def test_el_conteo_del_resumen_coincide_con_las_partidas(balance) -> None:
    """``total``, ``cuadran`` y ``descuadres`` tienen que sumar.

    Son los tres números que la pantalla muestra arriba de todo, y salen de un
    recuento aparte: si se desincronizan de la lista, la cabecera miente sobre
    el detalle que tiene justo debajo.
    """
    datos = balance(ORDENES)
    partidas = datos.get("partidas") or []
    assert datos["total"] == len(partidas)
    assert datos["cuadran"] == sum(1 for p in partidas if p["cuadra"])
    assert datos["descuadres"] == sum(1 for p in partidas if not p["cuadra"])
    assert datos["cuadran"] + datos["descuadres"] == datos["total"]


def test_sin_datos_se_abstiene_y_no_emite_ninguna_partida(balance) -> None:
    """La defensa contra el falso verde Y el falso rojo a la vez."""
    datos = balance(ORDENES, calculando=True)
    assert datos["evaluable"] is False
    assert datos["partidas"] == []
    assert datos["motivo"]
