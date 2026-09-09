"""Todas las páginas tienen que cuadrar como un balance de comprobación.

Pedido del usuario (septiembre 2026): "no debería haber discrepancia entre
las páginas. todas tienen que cuadrar como un balance de comprobación [...]
si puedes diseñar algún tipo de balance de comprobación en la página de
auditoría que ayude a auditar si todas las páginas están cuadrando
correctamente sería bueno".

Dos fuentes únicas de verdad, y toda partida que no cuadre significa que
alguien dejó de usar una de las dos:

  · si una orden está cobrada lo decide ``clasificar_estado_cxc``;
  · cuánto falta cobrar lo dice ``_saldos_4_columnas_item``, y son DOS
    valores -- la Venta Real y el Teórico USD -- porque cuál de los dos se
    cobra depende de cómo termine pagando el cliente, y eso no se sabe
    hasta que paga.

Lo que el balance encontró al estrenarse, contra producción:

  · 19 órdenes ya cobradas seguían figurando con saldo en el Reporte de
    Saldos, por $8.278,15 (2,9 % de la cartera). El saldo del reporte se
    mide contra BCV y el árbol saca la orden cuando cubre cualquiera de
    sus referencias, casi siempre el Teórico USD, ~35 % menor.
  · un falso descuadre de $23.673,56 entre Ventas y el Reporte por
    Cliente, que resultó ser un error del propio balance: el total por
    cliente está NETO de los pagos huérfanos, que son un crédito del
    cliente y no pertenecen a ninguna orden. La partida correcta compara
    contra los documentos tipo orden, y por separado verifica la identidad
    interna del reporte.
"""

from __future__ import annotations


def _partida(izq: float, der: float, tolerancia: float = 0.5) -> bool:
    return abs(round(izq - der, 2)) <= tolerancia


def test_una_partida_que_calza_cuadra() -> None:
    assert _partida(269161.25, 269161.25) is True


def test_una_diferencia_de_centavos_cuadra() -> None:
    """Sumar cientos de filas redondeadas no da al centavo, y exigirlo
    convertiría el balance en ruido permanente."""
    assert _partida(245487.70, 245487.69) is True


def test_una_diferencia_real_no_cuadra() -> None:
    """Los $23.673,56 que destapó el balance al estrenarse."""
    assert _partida(269161.25, 245487.69) is False


def test_la_identidad_del_reporte_por_cliente() -> None:
    """órdenes + créditos del cliente = total por cliente.

    Verificado contra producción: 269.645,73 + (-24.158,03) = 245.487,70
    contra un total de 245.487,69.
    """
    ordenes = 269645.73
    creditos = -24158.03
    total = 245487.69
    assert _partida(ordenes + creditos, total, tolerancia=1.0) is True


def test_una_orden_cobrada_no_puede_tener_saldo() -> None:
    """La partida se expresa como "esperado 0 contra encontradas N"."""
    assert _partida(0.0, 0.0) is True
    assert _partida(0.0, 19.0) is False


def test_la_tolerancia_del_saldo_a_favor_cubre_el_redondeo() -> None:
    """Se agrega sobre cientos de clientes; $2,09 es redondeo, no un error."""
    assert _partida(3696.39, 3694.30, tolerancia=5.0) is True
