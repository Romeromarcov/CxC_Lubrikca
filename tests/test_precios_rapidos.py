"""La mina 1 del inventario 1.1, por fin ejercitable (Fase 2.4).

«Precio 0 con Odoo caído» estaba clasificada como mina de severidad ALTA desde la
Fase 1.1 y **no tenía un solo test**, porque el código vivía como clase anidada
dentro de una función de 1.149 líneas. El plan lo decía así: extraerla es la
condición para poder arreglarla.

Estos tests no la arreglan —cambiar lo que devuelve mueve montos en una pantalla en
uso, y eso es una decisión del usuario—. Lo que hacen es **fijar exactamente qué
hace hoy**, incluido el cero, para que la decisión se tome sobre un comportamiento
descrito y no sobre una lectura del código. Y verifican lo único que sí se agregó
sin mover nada: que cada cero quede contado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from cxc.engine.precios_rapidos import CLAVES_DE_LISTA, ResolverRapidoDePrecios


@dataclass
class _Linea:
    producto: str | None = None
    precio_unitario: Decimal | None = None


class _OdooCaido:
    """El resolver de respaldo cuando Odoo no contesta."""

    def precio(self, producto, lista, fecha=None):
        raise ConnectionError("Odoo no responde")

    def volumen(self, producto):
        raise ConnectionError("Odoo no responde")


class _OdooSano:
    def __init__(self, precio=Decimal("77.7"), volumen=Decimal("208")):
        self._p, self._v = precio, volumen
        self.consultas: list[tuple[str, str]] = []

    def precio(self, producto, lista, fecha=None):
        self.consultas.append((str(producto), str(lista)))
        return self._p

    def volumen(self, producto):
        return self._v


def _mapa(*pares):
    return {f"S{i:03d}": [_Linea(p, pu)] for i, (p, pu) in enumerate(pares, 1)}


# --- el índice -------------------------------------------------------------


def test_el_precio_del_espejo_gana_y_no_se_consulta_odoo() -> None:
    """La razón de existir: miles de llamadas XML-RPC que no se hacen."""
    odoo = _OdooSano()
    r = ResolverRapidoDePrecios(lines_map=_mapa(("1026", Decimal("601.72"))), fallback=odoo)
    assert r.precio("1026", "5") == Decimal("601.72")
    assert odoo.consultas == [], "consultó Odoo teniendo el precio en el espejo"


@pytest.mark.parametrize("lista", CLAVES_DE_LISTA)
def test_cada_clave_de_lista_encuentra_el_precio(lista) -> None:
    """Cuatro claves por producto: dos ids y dos nombres lógicos."""
    r = ResolverRapidoDePrecios(lines_map=_mapa(("1026", Decimal("100"))))
    assert r.precio("1026", lista) == Decimal("100")


def test_una_lista_desconocida_cae_a_la_5_del_indice() -> None:
    """Comportamiento original, preservado tal cual.

    Y acá se ve el problema que el docstring del módulo describe: se pide la lista
    10 --vigente-- y contesta con el precio indexado bajo la 5, que en la copia de
    producción está ARCHIVADA. No es un cero, así que nada lo denuncia.
    """
    r = ResolverRapidoDePrecios(lines_map=_mapa(("1026", Decimal("601.72"))))
    assert r.precio("1026", "10") == Decimal("601.72")
    assert not r.hubo_ceros, "no es un cero, y por eso no se cuenta"


def test_los_espacios_del_codigo_de_producto_no_importan() -> None:
    r = ResolverRapidoDePrecios(lines_map=_mapa(("  1026  ", Decimal("5"))))
    assert r.precio("1026", "5") == Decimal("5")
    assert r.precio(" 1026 ", "5") == Decimal("5")


def test_las_lineas_sin_producto_o_sin_precio_no_entran_al_indice() -> None:
    r = ResolverRapidoDePrecios(
        lines_map={
            "S001": [_Linea(None, Decimal("10")), _Linea("1026", None), _Linea("", Decimal("3"))]
        }
    )
    assert r.productos_indexados == 0


def test_un_precio_cero_en_el_espejo_es_un_dato_y_se_respeta() -> None:
    """Distinto del cero de la mina: acá el cero *es* el precio de la línea."""
    r = ResolverRapidoDePrecios(lines_map=_mapa(("1026", Decimal("0"))))
    assert r.precio("1026", "5") == Decimal("0")
    assert not r.hubo_ceros, "no es un cero inventado, es el precio que dice el espejo"


# --- la mina --------------------------------------------------------------


def test_con_odoo_caido_devuelve_cero_y_ahora_lo_cuenta() -> None:
    """LA MINA 1, descrita tal como es.

    Un precio cero valora la orden en cero, su saldo teórico queda en cero, y la
    orden **se ve como cobrada**. Antes esto pasaba sin excepción, sin log y sin
    rastro. El cero sigue ahí --cambiarlo mueve montos-- pero ya no es silencioso.
    """
    r = ResolverRapidoDePrecios(lines_map=_mapa(("1026", Decimal("100"))), fallback=_OdooCaido())
    assert r.precio("9999", "10") == Decimal("0"), "el comportamiento no cambió"
    assert r.hubo_ceros
    assert len(r.ceros_silenciosos) == 1
    cero = r.ceros_silenciosos[0]
    assert cero.producto == "9999" and cero.lista == "10"
    assert "el respaldo falló" in cero.motivo
    assert "Odoo no responde" in cero.motivo


def test_sin_resolver_de_respaldo_tambien_devuelve_cero_y_lo_cuenta() -> None:
    r = ResolverRapidoDePrecios(lines_map={}, fallback=None)
    assert r.precio("1026", "5") == Decimal("0")
    assert r.ceros_silenciosos[0].motivo == (
        "sin precio en el espejo y sin resolver de respaldo"
    )


def test_el_resumen_dice_cuantos_y_da_ejemplos() -> None:
    """Un contador sin ejemplos no sirve para ir a mirar."""
    r = ResolverRapidoDePrecios(lines_map={}, fallback=_OdooCaido())
    for prod in ("1026", "1082", "1655"):
        r.precio(prod, "10")
    resumen = r.resumen_de_ceros()
    assert "3 precio(s) se resolvieron en CERO" in resumen
    assert "se ve como cobrada" in resumen
    for prod in ("1026", "1082", "1655"):
        assert prod in resumen


def test_sin_ceros_el_resumen_esta_vacio() -> None:
    """No se avisa de lo que no pasó."""
    r = ResolverRapidoDePrecios(lines_map=_mapa(("1026", Decimal("10"))))
    assert r.precio("1026", "5") == Decimal("10")
    assert r.resumen_de_ceros() == ""


def test_el_mismo_par_pedido_dos_veces_se_cuenta_dos_veces() -> None:
    """Cuántas veces se resolvió en cero y sobre cuántos pares son dos números
    distintos, y el resumen los distingue."""
    r = ResolverRapidoDePrecios(lines_map={}, fallback=_OdooCaido())
    r.precio("1026", "10")
    r.precio("1026", "10")
    assert len(r.ceros_silenciosos) == 2
    assert "2 precio(s)" in r.resumen_de_ceros()
    assert "1 par(es)" in r.resumen_de_ceros()


# --- el volumen -----------------------------------------------------------


def test_el_volumen_sale_del_respaldo() -> None:
    r = ResolverRapidoDePrecios(lines_map={}, fallback=_OdooSano())
    assert r.volumen("1026") == Decimal("208")


def test_el_volumen_en_cero_no_entra_al_conteo_de_ceros() -> None:
    """Deliberado: un volumen cero apaga los descuentos por volumen, que es un
    efecto acotado y conservador. Si entrara al conteo, el conteo dejaría de
    significar «una orden se ve como cobrada»."""
    r = ResolverRapidoDePrecios(lines_map={}, fallback=_OdooCaido())
    assert r.volumen("1026") == Decimal("0")
    assert not r.hubo_ceros


def test_sin_respaldo_el_volumen_es_cero() -> None:
    r = ResolverRapidoDePrecios(lines_map={}, fallback=None)
    assert r.volumen("1026") == Decimal("0")


# --- la medición A/B ------------------------------------------------------

CASOS_AB = [
    # (mapa, producto, lista, con_respaldo)
    (_mapa(("1026", Decimal("601.72"))), "1026", "5", False),
    (_mapa(("1026", Decimal("601.72"))), "1026", "4", False),
    (_mapa(("1026", Decimal("601.72"))), "1026", "Precio USD", False),
    (_mapa(("1026", Decimal("601.72"))), "1026", "10", False),
    (_mapa(("1026", Decimal("601.72"))), "9999", "5", False),
    (_mapa(("1026", Decimal("601.72"))), "9999", "5", True),
    ({}, "1026", "5", False),
    ({}, "1026", "5", True),
]


@pytest.mark.parametrize("mapa,producto,lista,con_respaldo", CASOS_AB)
def test_da_lo_mismo_que_la_clase_anidada_original(mapa, producto, lista, con_respaldo) -> None:
    """La medición A/B: el original reconstruido al lado del extraído.

    El original ya no existe en ``app.py``, así que se reconstruye acá tal como
    estaba. Si los dos difieren en algún caso, la extracción cambió un precio --y
    un precio es lo que alguien ve en pantalla.
    """
    respaldo = _OdooSano() if con_respaldo else None

    class Original:
        def __init__(self, lines_map, fallback_resolver=None):
            self._prices = {}
            for lines in lines_map.values():
                for line in lines:
                    if line.producto and line.precio_unitario is not None:
                        p_str = str(line.producto).strip()
                        d_pu = line.precio_unitario
                        self._prices[(p_str, "5")] = d_pu
                        self._prices[(p_str, "Precio USD Pago VES")] = d_pu
                        self._prices[(p_str, "4")] = d_pu
                        self._prices[(p_str, "Precio USD")] = d_pu
            self._fallback = fallback_resolver

        def precio(self, producto: str, lista: str, fecha: date | None = None) -> Decimal:
            p_str = str(producto).strip()
            if (p_str, str(lista)) in self._prices:
                return self._prices[(p_str, str(lista))]
            if (p_str, "5") in self._prices:
                return self._prices[(p_str, "5")]
            if self._fallback:
                try:
                    return self._fallback.precio(producto, lista, fecha)
                except Exception:
                    pass
            return Decimal("0")

    viejo = Original(mapa, respaldo)
    nuevo = ResolverRapidoDePrecios(lines_map=mapa, fallback=respaldo)
    assert viejo.precio(producto, lista) == nuevo.precio(producto, lista)
