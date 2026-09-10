"""El armado del balance de comprobación: las partidas que no necesitan Odoo.

Cuarta pieza de la Fase 2.4 del plan de blindaje, y la que el documento tenía
anotada como la de mayor valor: el endpoint del balance son 843 líneas, de las
cuales estas 220 son **puras** -- entran cuatro payloads y sale una lista de
partidas, sin repositorio, sin Odoo y sin caché.

**Qué se movió y qué no.** Las partidas 1 a 6b, que comparan vistas nuestras
entre sí. La sección 7 -- las ocho partidas que comparan contra Odoo -- se queda
en ``web/app.py``, porque es E/S y no se puede probar sin la red. El corte es
exactamente ese: de un lado lo que se puede ejercitar con diccionarios, del otro
lo que necesita un servidor.

**El cuerpo se movió tal cual**, sin retipearlo: se extrajo, se le sacó la
indentación y se renombró la única llamada que usaba el alias
``_saldos_4_columnas_item`` por su nombre real ``saldos_de_la_orden``. Eso importa
en una pieza de este tamaño -- un refactor de un camino de dinero que además
reescribe el cuerpo mezcla dos cosas, y si algo sale mal no se sabe cuál fue.

**La red que lo hizo posible.** ``tests/test_balance_punta_a_punta.py`` llama al
endpoint con las cuatro páginas sustituidas y verifica que emita las 24 partidas
con su clasificación. Sin ese test esta extracción no se podía hacer con
confianza, y por eso se hizo primero.

Sobre por qué el balance importa, y qué prueba cada clase de partida, ver el
docstring de ``get_balance_comprobacion`` y ``docs/blindaje/1.2-1.5-auditoria.md``.
"""

from __future__ import annotations

from typing import Any

from cxc.engine.saldos import saldos_de_la_orden

# Cuánto puede diferir una partida y seguir cuadrando. La mayoría compara
# conteos, donde cualquier diferencia es real; las de monto usan 1.0 porque los
# dos lados redondean por separado en varios pasos.
TOLERANCIA_POR_DEFECTO = 0.5


def num(d: Any, k: str) -> float:
    """El valor numérico de una clave, o 0.0 si no se puede leer.

    Un balance no puede reventar porque una página mandó ``None`` o un texto en
    un campo de monto: lo que tiene que hacer es seguir y que la partida
    descuadre, que es información. Es distinto de "sin datos es cero" -- acá el
    dato existe y está mal formado, y el descuadre resultante lo denuncia.
    """
    try:
        return float(d.get(k) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def crear_partida(
    concepto: str,
    izq_nombre: str,
    izq: float,
    der_nombre: str,
    der: float,
    nota: str = "",
    tolerancia: float = TOLERANCIA_POR_DEFECTO,
    tipo: str = "interna",
) -> dict[str, Any]:
    """Una partida ya armada. No la agrega a ninguna lista: la devuelve.

    Separar el armado del acumulado es lo que permite que la sección de Odoo
    siga viviendo en ``app.py`` y use exactamente la misma forma de partida que
    ésta -- si el diccionario se armara en dos lugares, tarde o temprano
    diferirían en un campo.
    """
    dif = round(izq - der, 2)
    return {
        "concepto": concepto,
        "izquierda": {"vista": izq_nombre, "valor": round(izq, 2)},
        "derecha": {"vista": der_nombre, "valor": round(der, 2)},
        "diferencia": dif,
        "cuadra": abs(dif) <= tolerancia,
        "nota": nota,
        "tipo": tipo,
    }


def partidas_internas(
    items: dict[str, Any],
    clientes: list[dict[str, Any]],
    bandeja: dict[str, Any],
    saldos_items: dict[str, Any],
    saldos_min: set[str],
) -> list[dict[str, Any]]:
    """Las partidas del balance que comparan vistas nuestras entre sí.

    **Todas salen con ``tipo="interna"``, y eso es una limitación, no un
    detalle.** Una partida interna compara dos vistas que salen de la misma
    función, así que detecta que dos páginas se contradigan --y ya atrapó bugs--
    pero NO puede detectar que el número esté mal: un error en la función de
    origen se propaga igual a los dos lados y la partida sale verde. Las que sí
    pueden detectarlo son las ocho externas y la invariante, y las nueve
    necesitan Odoo, así que viven en ``web/app.py``. La clasificación es de la
    Fase 1.5 y está para que un verde acá no se lea como un verde de allá.

    ``saldos_min`` son los so_id que el Reporte de Saldos lista aparte, por
    saldo mínimo: cuentan como listados aunque no estén en ``saldos_items``.
    """
    partidas: list[dict[str, Any]] = []

    def partida(*args: Any, **kwargs: Any) -> None:
        partidas.append(crear_partida(*args, **kwargs))

    # 1. Órdenes cobradas: el árbol decide, y las tres vistas lo acatan.
    pagadas_ventas = sum(1 for i in items.values() if i.get("sale_de_cxc"))
    pagadas_no_en_saldos = sum(
        1
        for so, i in items.items()
        if i.get("sale_de_cxc") and so in saldos_items
    )
    partida(
        "Órdenes cobradas que el Reporte de Saldos sigue listando",
        "Ventas: cobradas",
        0.0,
        "de esas, con saldo en el Reporte",
        float(pagadas_no_en_saldos),
        "Una orden que salió de CxC no debería aparecer con saldo. "
        f"Ventas da {pagadas_ventas} por cobradas.",
    )

    # 2. Órdenes por cobrar: Ventas vs el Reporte (sus dos listas).
    por_cobrar_ventas = {
        so
        for so, i in items.items()
        if not i.get("sale_de_cxc") and i.get("estado_cobro") != "pendiente_entrega"
    }
    faltantes = por_cobrar_ventas - set(saldos_items) - saldos_min
    partida(
        "Órdenes por cobrar que el Reporte de Saldos no lista",
        "Ventas: por cobrar",
        0.0,
        "sin fila en el Reporte",
        float(len(faltantes)),
        f"Ventas cuenta {len(por_cobrar_ventas)} por cobrar.",
    )

    # 3. El monto por cobrar sale de la misma función en las dos vistas.
    #
    # La comparación se hace contra los DOCUMENTOS tipo orden del
    # reporte, no contra su total por cliente: ese total está NETO de
    # los pagos huérfanos, que son un crédito del cliente y no
    # pertenecen a ninguna orden. Compararlo contra la suma por orden
    # daba un falso descuadre de $23.673,56 -- que es justamente lo que
    # suman esos créditos.
    docs_orden: dict[str, float] = {"venta_real": 0.0, "teorico_usd": 0.0}
    docs_credito: dict[str, float] = {"venta_real": 0.0, "teorico_usd": 0.0}
    for c in clientes:
        for d in c.get("documentos") or []:
            destino = docs_orden if d.get("tipo") == "orden" else docs_credito
            for k in destino:
                valor = (d.get("saldos") or {}).get(k)
                if valor is not None:
                    destino[k] += float(valor)

    for clave, etiqueta in (("venta_real", "Venta Real"), ("teorico_usd", "Teórico USD")):
        campo = "por_cobrar_real" if clave == "venta_real" else "por_cobrar_teorico_usd"
        partida(
            f"Por cobrar — {etiqueta}",
            "Ventas (suma por orden)",
            sum(num(i, campo) for so, i in items.items() if so in por_cobrar_ventas),
            "Reporte por Cliente (órdenes)",
            docs_orden[clave],
            "Las dos salen de _saldos_4_columnas_item; deben ser el mismo número.",
            tolerancia=1.0,
        )
        partida(
            f"Cuadre interno del Reporte por Cliente — {etiqueta}",
            "órdenes + créditos del cliente",
            docs_orden[clave] + docs_credito[clave],
            "total por cliente",
            sum(num(c["saldos"], clave) for c in clientes),
            "El total está neto de los pagos huérfanos, que no pertenecen "
            "a ninguna orden.",
            tolerancia=1.0,
        )

    # 4. Facturación no puede contradecir a Ventas.
    b1 = bandeja.get("ordenes_por_facturar") or []
    b2 = bandeja.get("notas_credito_pendientes") or []
    # Solo se juzgan las órdenes que Ventas efectivamente conoce. Una
    # que no esté en ``items`` no es un error de Facturación: es que no
    # tenemos con qué opinar, y contarla como descuadre fue justo lo
    # que produjo el falso rojo de 3 y 129.
    b1 = [x for x in b1 if str(x["so_id"]) in items]
    b2 = [x for x in b2 if str(x["so_id"]) in items]
    partida(
        "Bandeja 1 con órdenes que Ventas no da por cobradas",
        "esperado",
        0.0,
        "encontradas",
        float(sum(1 for x in b1 if not items.get(str(x["so_id"]), {}).get("sale_de_cxc"))),
        "Solo se factura lo ya cobrado.",
    )
    partida(
        "Bandeja 2 con órdenes que Ventas no da por facturadas",
        "esperado",
        0.0,
        "encontradas",
        float(sum(1 for x in b2 if not items.get(str(x["so_id"]), {}).get("facturada"))),
        "La nota de crédito exige factura.",
    )

    # 5. El crédito del cliente no puede vivir solo en una vista.
    favor_ventas = sum(num(i, "saldo_a_favor") for i in items.values())
    favor_cliente = sum(num(c, "saldo_a_favor") for c in clientes)
    partida(
        "Saldo a favor de clientes",
        "Ventas",
        favor_ventas,
        "Reporte por Cliente",
        favor_cliente,
        "El reporte por cliente agrega lo que Ventas calcula por orden. "
        "La tolerancia cubre el redondeo de cientos de filas.",
        tolerancia=5.0,
    )

    # 6. La identidad de fondo: VENTA - COBRADO = POR COBRAR.
    #
    # Planteada por el usuario: "en teoría mis ventas - lo cobrado = por
    # cobrar". Se verifica por cada referencia, que es como el sistema
    # mide la venta.
    #
    # No cierra exacta a propósito, y el residuo tiene nombre: los
    # saldos se calculan con ``max(0, venta - pagado)``, así que una
    # orden pagada de más aporta 0 en vez de un negativo. Ese recorte es
    # el saldo a favor del cliente. La identidad completa es:
    #
    #     venta - cobrado + saldo a favor = por cobrar
    #
    # Si no cuadra ni con ese ajuste, hay un pago que no se está
    # restando o una venta que no se está contando.
    referencias = (
        (
            "Venta Real",
            "venta_neta_real",
            "monto_pagado_factura_odoo_incl_pendiente",
            "venta_real",
        ),
        (
            "Teórico BS",
            "ves_neta_teorica_iva",
            "pagado_teorico_bcv_incl_pendiente",
            "teorico_bs",
        ),
        (
            "Teórico USD",
            "usd_neta_teorica_iva",
            "pagado_teorico_binance_incl_pendiente",
            "teorico_usd",
        ),
    )
    for etiqueta, campo_venta, campo_pago, campo_saldo in referencias:
        venta = cobrado = saldo = favor = 0.0
        for so, i in items.items():
            if so not in por_cobrar_ventas:
                continue
            v = num(i, campo_venta)
            if campo_venta == "venta_neta_real":
                v -= num(i, "descuento_aplicado_sistema")
            p = num(i, campo_pago)
            venta += v
            cobrado += p
            saldo += float(saldos_de_la_orden(i)[campo_saldo] or 0.0)
            # Lo que el recorte a cero se comió en esta orden.
            favor += max(0.0, p - v)
        partida(
            f"Ventas − cobrado = por cobrar — {etiqueta}",
            "venta − cobrado + saldo a favor",
            venta - cobrado + favor,
            "por cobrar",
            saldo,
            f"Venta {venta:,.2f} − cobrado {cobrado:,.2f}. Saldo a favor "
            f"absorbido: {favor:,.2f} — sin ese ajuste la identidad no "
            "cierra, porque el saldo nunca baja de cero.",
            tolerancia=1.0,
        )

    # 6b. La misma identidad, pero cliente por cliente.
    #
    # Pedido del usuario: "una partida que vaya revisando aleatoriamente
    # diferentes clientes [...] y les haga un balance de comprobación al
    # cliente". Se hace sobre TODOS los clientes en vez de una muestra al
    # azar: los datos ya están en memoria, así que no cuesta más, y una
    # muestra distinta en cada refresco haría que la partida cambiara de
    # veredicto sin que nadie hubiera tocado nada.
    #
    # No es redundante con la partida 6. Un total puede cuadrar con
    # errores que se compensan -- un cliente de más contra otro de
    # menos -- y esta es la partida que los separa. Por eso lo que
    # reporta es la CANTIDAD de clientes descuadrados, no un monto.
    for etiqueta, campo_venta, campo_pago, campo_saldo in referencias:
        por_cliente: dict[str, list[float]] = {}
        for so, i in items.items():
            if so not in por_cobrar_ventas:
                continue
            cli = str(i.get("cliente_nombre") or "sin cliente")
            v = num(i, campo_venta)
            if campo_venta == "venta_neta_real":
                v -= num(i, "descuento_aplicado_sistema")
            p = num(i, campo_pago)
            acc = por_cliente.setdefault(cli, [0.0, 0.0, 0.0, 0.0])
            acc[0] += v
            acc[1] += p
            acc[2] += float(saldos_de_la_orden(i)[campo_saldo] or 0.0)
            acc[3] += max(0.0, p - v)
        descuadrados = sorted(
            (
                (abs(a[0] - a[1] + a[3] - a[2]), cli)
                for cli, a in por_cliente.items()
                if abs(a[0] - a[1] + a[3] - a[2]) > 1.0
            ),
            reverse=True,
        )
        peores = "; ".join(f"{c} ({d:,.2f})" for d, c in descuadrados[:3])
        partida(
            f"Arqueo por cliente — {etiqueta}",
            "esperado",
            0.0,
            "clientes descuadrados",
            float(len(descuadrados)),
            f"{len(por_cliente)} clientes arqueados con la misma identidad "
            "de la partida anterior."
            + (f" Los peores: {peores}." if peores else " Todos cuadran."),
        )

    return partidas
