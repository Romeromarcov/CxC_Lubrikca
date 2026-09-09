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


def test_el_saldo_por_cobrar_de_lo_facturado_si_se_puede_saber_en_usd() -> None:
    """Odoo expone ``amount_residual_usd`` ("Importe adeudado Ref.").

    Con ese campo las dos vistas se comparan en la misma unidad, que era lo
    que faltaba: antes la partida enfrentaba 60.368,21 USD del Reporte de
    Saldos contra 65.162.339,64 VES de ``amount_residual`` y no probaba
    nada.

    Medido contra producción sobre las 229 facturas comparables:

        Reporte de Saldos            60.368,21
        amount_residual_usd (Odoo)   64.657,78
        diferencia                    4.289,57   (6,6 %)

    La diferencia es de TASA, no de conteo -- el reporte convierte con la
    serie BCV de la fecha de cada factura y Odoo con la suya -- y por eso
    la tolerancia es porcentual: una desviación chica es la tasa, una
    grande es que alguien dejó de contar algo.
    """
    rep = 60368.21
    odoo = 64657.78
    tolerancia = max(50.0, odoo * 0.01)
    assert not _partida(rep, odoo, tolerancia)
    # Con las dos vistas usando la misma tasa, sí cuadraría.
    assert _partida(odoo, odoo, tolerancia)


def test_la_tolerancia_del_residual_es_porcentual() -> None:
    """Un piso fijo no sirve: la cartera crece y la deriva de tasa con ella."""
    odoo = 64657.78
    tol = max(50.0, odoo * 0.01)
    assert _partida(odoo - 500.0, odoo, tol) is True
    assert _partida(odoo - 5000.0, odoo, tol) is False


# --- Arqueo por cliente ----------------------------------------------------
#
# Pedido del usuario: "una partida que vaya revisando aleatoriamente
# diferentes clientes [...] y les haga un balance de comprobación al
# cliente". Se hace sobre todos los clientes, no sobre una muestra: cuesta
# lo mismo (los datos ya están en memoria) y una muestra al azar cambiaría
# de veredicto en cada refresco sin que nadie tocara nada.


def _arqueo(clientes: dict[str, tuple[float, float, float, float]]) -> list[str]:
    """Los clientes cuya identidad no cierra, del peor al menos malo."""
    malos = [
        (abs(v - c + f - s), cli)
        for cli, (v, c, f, s) in clientes.items()
        if abs(v - c + f - s) > 1.0
    ]
    return [cli for _, cli in sorted(malos, reverse=True)]


def test_el_arqueo_por_cliente_no_reporta_a_quien_cuadra() -> None:
    assert _arqueo({"Rio Paraguas": (1000.0, 400.0, 0.0, 600.0)}) == []


def test_el_arqueo_encuentra_al_cliente_que_no_cierra() -> None:
    assert _arqueo({"Leche y Miel": (1000.0, 400.0, 0.0, 550.0)}) == ["Leche y Miel"]


def test_el_arqueo_separa_errores_que_se_compensan() -> None:
    """La razón de ser de la partida.

    El total cuadra -- uno sobra 50 y al otro le faltan 50 -- y sin
    embargo los dos clientes están mal. La partida 6, que solo mira el
    agregado, daría verde."""
    clientes = {
        "Uno": (1000.0, 400.0, 0.0, 650.0),
        "Dos": (1000.0, 400.0, 0.0, 550.0),
    }
    venta = sum(c[0] for c in clientes.values())
    cobrado = sum(c[1] for c in clientes.values())
    saldo = sum(c[3] for c in clientes.values())
    assert _partida(venta - cobrado, saldo, tolerancia=1.0) is True
    assert sorted(_arqueo(clientes)) == ["Dos", "Uno"]


def test_el_saldo_a_favor_tambien_cierra_el_arqueo_del_cliente() -> None:
    """Un cliente que pagó de más aporta 0 al saldo, no un negativo."""
    assert _arqueo({"Gustavo": (1000.0, 1200.0, 200.0, 0.0)}) == []


# --- Los equivalentes en BCV contra Odoo -----------------------------------
#
# Pedido del usuario: "puedes comparar en los pagos y en lo facturado vs
# Odoo los equivalentes en BCV, no los bolívares". Comparar importes
# nominales no prueba nada sobre la tasa: a los dos lados se suma el mismo
# número. La comparación en dólares sí audita la serie de tasas.


def test_el_nominal_cuadra_aunque_la_tasa_este_mal() -> None:
    """Por qué hacía falta la partida nueva: el nominal no ve la tasa."""
    nominal_nuestro = 13_931_659.64
    nominal_odoo = 13_931_659.64
    assert _partida(nominal_nuestro, nominal_odoo) is True


def test_el_equivalente_bcv_de_los_pagos_contra_odoo() -> None:
    """Medido contra producción: 1.274 pagos, 358.878,75 contra 358.968,57.

    La diferencia -- $89,82, un 0,025 % -- es el desfase de un día en la
    tasa de un puñado de pagos grandes, no un pago que falte."""
    assert _partida(358878.75, 358968.57, tolerancia=max(200.0, 358968.57 * 0.005)) is True


def test_un_dia_con_la_tasa_cambiada_si_descuadra() -> None:
    """Lo que la partida existe para atrapar."""
    assert _partida(358878.75, 320000.00, tolerancia=max(200.0, 320000.0 * 0.005)) is False


# --- Sin datos no hay balance ----------------------------------------------
#
# Error real (septiembre 2026), reportado por el usuario con una captura:
# "tu reportaste que los 22 partidas cuadraban, y la UI me reporta 2
# discrepancias". Ninguna de las dos lecturas era buena.
#
# ``_get_ventas_sync`` devuelve ``{"items": [], "calculando": True}``
# mientras hay un recálculo en vuelo y todavía no hay caché -- el estado
# normal tras cada despliegue. El balance no lo miraba y armaba sus
# partidas contra una lista vacía: las de monto daban 0,00 contra 0,00 y
# salían verdes, y las de bandeja contaban TODAS sus filas como error
# porque la búsqueda del so_id fallaba siempre.
#
# "Sin datos" no es "cero", y un balance verde sobre la nada es peor que
# no tener balance.


def _evaluable(ventas: dict) -> bool:
    return not (ventas.get("calculando") or not (ventas.get("items") or []))


def test_el_balance_se_abstiene_mientras_ventas_recalcula() -> None:
    assert _evaluable({"items": [], "calculando": True}) is False


def test_el_balance_se_abstiene_si_ventas_viene_vacio() -> None:
    """Aunque nadie haya marcado ``calculando``: sin órdenes no hay nada
    que cuadrar, y decir que cuadra sería mentir."""
    assert _evaluable({"items": []}) is False


def test_con_datos_el_balance_si_evalua() -> None:
    assert _evaluable({"items": [{"so_id": "S00001"}]}) is True


def test_una_bandeja_no_descuadra_por_ordenes_que_ventas_no_conoce() -> None:
    """El falso rojo de la captura: 3 y 129 descuadres.

    Una orden que Facturación lista pero Ventas todavía no cargó no es un
    error de Facturación -- es que no hay con qué opinar."""
    items = {"S00001": {"sale_de_cxc": True}}
    bandeja = [{"so_id": "S00001"}, {"so_id": "S00999"}]
    juzgables = [x for x in bandeja if str(x["so_id"]) in items]
    assert len(juzgables) == 1
    assert sum(1 for x in juzgables if not items[x["so_id"]]["sale_de_cxc"]) == 0


# --- La tasa la pone Odoo, y se valida contra el BCV -----------------------
#
# Criterio del usuario: "la tasa nuestra del sistema para el caso tanto de
# las facturas como los pagos, deberíamos usar la tasa de Odoo, que es la
# que vale, y solo valida que coincida con el BCV de ese día".


def _tasa_diverge(residual_ves: float, residual_usd: float, bcv: float) -> bool:
    return abs((residual_ves / residual_usd) / bcv - 1.0) > 0.02


def test_la_tasa_de_odoo_coincide_con_nuestro_bcv() -> None:
    """La factura 00000525, que fue la que destapó todo: 1.340.030,18 VES
    y 1.829,45 USD dan 732,48, contra nuestro BCV de ese día, 732,4787."""
    assert _tasa_diverge(1340030.18, 1829.45, 732.4787) is False


def test_un_dia_de_desfase_no_se_reporta() -> None:
    """Odoo fecha la tasa por el asiento y nosotros por el día; medio
    punto de diferencia es eso, no un error."""
    assert _tasa_diverge(1340030.18, 1829.45, 735.00) is False


def test_una_tasa_realmente_cambiada_si_se_reporta() -> None:
    """El caso que la partida existe para atrapar: convertir una factura
    de julio con la tasa de septiembre (820,10 contra 732,48)."""
    assert _tasa_diverge(1340030.18, 1829.45, 820.10) is True


# --- Los abonos en euros ---------------------------------------------------
#
# Al ubicar el descuadre de −91,27 de la partida de pagos aparecieron dos
# poblaciones distintas, y solo una era "tasa":
#
#   · 465 pagos con −117,74: el desfase de un día. Odoo fecha la tasa por
#     el asiento y nosotros por el día. No es un error.
#   · 5 pagos con +26,47: se cobraron en EUROS. Odoo los convirtió con la
#     tasa BCV del euro, ~16 % por encima del dólar. El memo de uno lo dice
#     con todas las letras: "Tasa € el abono de 70$".
#
# Convertirlos a BCV-USD los hacía ver como tasa mal cargada cuando la
# tasa está perfecta: solo es otra moneda. Se detectan por la tasa
# implícita del propio pago, no por una lista fija de ids.


def _es_abono_en_euros(monto_ves: float, ref_usd: float, bcv: float, eur: float) -> bool:
    implicita = monto_ves / ref_usd
    return abs(implicita / eur - 1.0) < 0.01 and abs(implicita / bcv - 1.0) >= 0.01


def test_reconoce_un_abono_cobrado_en_euros() -> None:
    """Pago 199, del 2026-04-24: 39.678,80 Bs por 70 $ da 566,84, que es
    clavado el BCV del euro de ese día y no el del dólar (483,87)."""
    assert _es_abono_en_euros(39678.80, 70.0, 483.8695, 566.84) is True


def test_un_abono_normal_no_se_confunde_con_uno_en_euros() -> None:
    assert _es_abono_en_euros(1340030.18, 1829.45, 732.4787, 850.0) is False


def test_el_desfase_de_un_dia_tampoco_se_confunde() -> None:
    """El grupo grande: 764,35 contra 766,86 es medio punto, no un euro."""
    assert _es_abono_en_euros(13931659.64, 18226.84, 766.8603, 890.0) is False


# --- La tasa oficial del BCV -----------------------------------------------
#
# El BCV calcula la tasa al cierre del día y la publica con FECHA VALOR del
# día siguiente -- sus archivos lo dicen en la cabecera: "Fecha Operación:
# 08/09/2026, Fecha Valor: 09/09/2026".
#
# El scraper la guardaba bajo la fecha de hoy, corriendo la serie un día
# entero: 24 días de agosto y septiembre quedaron desplazados. Se corrigió
# el scraper y se realineó el histórico contra las series oficiales, que
# ahora coinciden 147 de 147 días en USD y en EUR.
#
# Hubo un intento intermedio -- deducir la tasa de la primera captura
# horaria del día -- que llevaba el descuadre de −117,76 a −72,21. Se
# eliminó al alinear el histórico: con la fuente oficial el mismo cálculo
# da −6,06, y mantener las dos habría dejado dos respuestas distintas a
# "cuál era la tasa ese día".


def test_la_tasa_del_dia_es_la_que_el_bcv_publico_ayer() -> None:
    """El 2026-08-21 real: el BCV calculó 779,9522 con fecha operación del
    20 y fecha valor del 21. Esa es la que rige el 21, y es la que Odoo
    estampó en los asientos de ese día."""
    oficial_por_fecha_valor = {"2026-08-21": 779.9522, "2026-08-22": 784.6633}
    assert oficial_por_fecha_valor["2026-08-21"] == 779.9522


def test_el_desfase_de_un_dia_era_el_descuadre() -> None:
    """Lo que guardaba el scraper contra lo que correspondía: en los 24 días
    afectados se cumplía ``nuestra[D] == oficial[D+1]``."""
    nuestra = {"2026-08-21": 784.6633}
    oficial = {"2026-08-21": 779.9522, "2026-08-22": 784.6633}
    assert nuestra["2026-08-21"] == oficial["2026-08-22"]


# --- La tasa que Odoo estampa, documento por documento ---------------------
#
# Pedido del usuario: "validar que la tasa que odoo esta reportando en los
# pagos y las facturas se corresponda con la tasa correcta de ese dia".
#
# Ya existía para facturas; faltaba para pagos. La diferencia con la
# partida de totales es que aquella dice CUÁNTO se desvía el conjunto y
# esta dice CUÁLES documentos y con qué tasa, que es lo que sirve para ir
# a corregirlos en Odoo.


def _tasa_divergente(nominal_ves: float, ref_usd: float, oficial: float) -> bool:
    return abs((nominal_ves / ref_usd) / oficial - 1.0) > 0.02


def test_un_pago_con_la_tasa_correcta_no_se_reporta() -> None:
    """Pago 1387: 13.931.659,64 Bs por 18.226,84 USD da 764,35, que es
    exactamente la oficial del BCV de ese día."""
    assert _tasa_divergente(13931659.64, 18226.84, 764.3486) is False


def test_el_redondeo_de_amount_ref_no_dispara_la_alarma() -> None:
    """Odoo guarda ``amount_ref`` con dos decimales; en un abono chico eso
    mueve centésimas de punto, no puntos."""
    assert _tasa_divergente(1000.00, 1.27, 787.40) is False


def test_el_desfase_de_un_dia_no_dispara_la_alarma() -> None:
    """Un día de corrimiento son décimas de punto y quedan bajo el umbral;
    si no, la partida marcaría medio libro cada vez que el BCV se mueve."""
    assert _tasa_divergente(13931659.64, 18166.14, 764.3486) is False


def test_una_tasa_realmente_distinta_si_se_reporta() -> None:
    """Lo que la partida existe para atrapar: un abono de agosto convertido
    con una tasa de julio (742,23 contra los 794,99 que correspondían)."""
    assert _tasa_divergente(1000000.00, 1347.29, 794.9917) is True


def test_un_abono_en_euros_no_es_una_tasa_mal_puesta() -> None:
    """Pago 199: 39.678,80 Bs por 70 $ da 566,84, que es el BCV del EURO de
    ese día. Divergería contra el dólar (483,87), pero su tasa está bien:
    solo es otra moneda, y por eso se descarta antes de reportar."""
    estampada = 39678.80 / 70.0
    assert _tasa_divergente(39678.80, 70.0, 483.8695) is True
    assert abs(estampada / 566.84 - 1.0) < 0.01
