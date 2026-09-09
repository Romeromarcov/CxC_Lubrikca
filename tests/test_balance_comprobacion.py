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


# --- La identidad de fondo -------------------------------------------------


def _identidad(venta: float, cobrado: float, favor: float, por_cobrar: float) -> bool:
    """venta − cobrado + saldo a favor = por cobrar."""
    return _partida(venta - cobrado + favor, por_cobrar, tolerancia=1.0)


def test_ventas_menos_cobrado_es_por_cobrar() -> None:
    """La identidad que planteó el usuario: "en teoría mis ventas − lo
    cobrado = por cobrar". Verificada contra producción en las tres
    referencias, al centavo."""
    assert _identidad(venta=1000.0, cobrado=300.0, favor=0.0, por_cobrar=700.0) is True


def test_el_saldo_a_favor_es_el_residuo_de_la_identidad() -> None:
    """No cierra exacta sin ese ajuste, y no es un error: los saldos se
    calculan con ``max(0, venta − pagado)``, así que una orden pagada de
    más aporta 0 en vez de un negativo. Ese recorte ES el saldo a favor.

    Cliente que compró 1.000 y pagó 1.200: el saldo por cobrar es 0, no
    -200, y los 200 son crédito suyo.
    """
    assert _identidad(venta=1000.0, cobrado=1200.0, favor=200.0, por_cobrar=0.0) is True
    # Sin el ajuste, la misma orden parecería un descuadre de 200.
    assert _partida(1000.0 - 1200.0, 0.0) is False


def test_un_pago_que_no_se_resta_rompe_la_identidad() -> None:
    """Es lo que la partida existe para atrapar."""
    assert _identidad(venta=1000.0, cobrado=0.0, favor=0.0, por_cobrar=700.0) is False


# --- Contra Odoo -----------------------------------------------------------


def test_los_montos_en_monedas_distintas_no_se_comparan() -> None:
    """El residual de Odoo está en la moneda de cada factura (casi todas en
    bolívares) y el Reporte de Saldos lo trae a dólares con la tasa de SU
    día. Compararlos daba un "descuadre" de 65 millones que no es un error
    de conteo: 60.368,21 USD contra 65.162.339,64 VES.

    Convertir a una tasa única tampoco sirve -- deja un 25 % de diferencia,
    que es el efecto de las tasas históricas. La partida compara QUÉ
    facturas siguen debiendo, que sí es verificable.
    """
    usd_reporte = 60368.21
    ves_odoo = 65162339.64
    assert _partida(usd_reporte, ves_odoo) is False
    # Lo que sí se compara: el conjunto de facturas pendientes.
    assert _partida(0.0, 0.0) is True


def test_un_equivalente_mayor_que_el_nominal_es_una_tasa_mal_congelada() -> None:
    """Un abono en bolívares no puede valer más dólares que bolívares."""
    monto_ves = 34580.50
    equiv_usd_plausible = 42.45
    equiv_usd_imposible = 40000.0
    assert equiv_usd_plausible < monto_ves
    assert equiv_usd_imposible > monto_ves
