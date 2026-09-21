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

import logging
from collections.abc import Callable
from datetime import date
from typing import Any

from cxc.engine.saldos import saldos_de_la_orden
from cxc.odoo.client import montos_reembolsados_en_pagos

logger = logging.getLogger(__name__)

# Cuánto puede diferir una partida y seguir cuadrando. La mayoría compara
# conteos, donde cualquier diferencia es real; las de monto usan 1.0 porque los
# dos lados redondean por separado en varios pasos.
TOLERANCIA_POR_DEFECTO = 0.5

# Desde qué fracción de su tolerancia una partida se marca "al límite".
# 0,6 y no 0,9 porque el residuo escala con el volumen: a diez veces los
# datos, una partida al 60 % de hoy ya cruzó. El aviso tiene que llegar
# mientras todavía se puede decidir, no el día que el balance amanece rojo.
UMBRAL_AL_LIMITE = 0.6


def _id_de_m2o(value: Any) -> str:
    """El id de un campo many2one de Odoo (``[id, "nombre"]`` o ``False``).

    Mismo criterio que ``cxc.odoo.client._m2o_id`` -- no se importa esa
    función porque es privada de ese módulo; esta es la versión local para
    los pocos usos que necesita el balance.
    """
    if isinstance(value, list | tuple) and value:
        return str(value[0])
    if value in (False, None):
        return ""
    return str(value)


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
    cuadra = abs(dif) <= tolerancia
    # Qué fracción de su tolerancia está usando la partida.
    #
    # Decisión del usuario del 11-sep-2026: «mantené la tolerancia al mínimo» --
    # o sea NO hacerla proporcional al volumen. Eso resuelve el riesgo de que una
    # tolerancia generosa tape un descuadre real, pero deja el otro: medido a diez
    # veces los datos, el residuo de «Saldo a favor» pasa de 1,53 a 15,55 y la
    # partida se pone roja con la tolerancia en 5,0, sin que nada esté mal.
    #
    # Un instrumento que grita lobo a medida que el negocio crece deja de mirarse.
    # Así que la tolerancia no se afloja y en cambio se expone cuánto margen queda:
    # una partida al 31 % hoy avisa mucho antes de cruzar, y el aviso llega cuando
    # todavía se puede decidir con calma en vez de el día que el balance amanece
    # rojo.
    margen = round(abs(dif) / tolerancia, 3) if tolerancia > 0 else None
    return {
        "concepto": concepto,
        "izquierda": {"vista": izq_nombre, "valor": round(izq, 2)},
        "derecha": {"vista": der_nombre, "valor": round(der, 2)},
        "diferencia": dif,
        "cuadra": cuadra,
        "tolerancia": round(tolerancia, 2),
        "margen_usado": margen,
        # "cuadra, pero por poco". No cambia el veredicto: una partida al 70 % de
        # su tolerancia sigue cuadrando, y esto sólo dice que conviene mirarla.
        "al_limite": bool(cuadra and margen is not None and margen >= UMBRAL_AL_LIMITE),
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


# --- las que comparan contra Odoo -----------------------------------------


def partidas_externas(
    items: dict[str, Any],
    saldos_items: dict[str, Any],
    *,
    execute: Any,
    repo: Any,
    tasas: Any,
    tasa_de_la_fecha: Callable[[Any], Any],
) -> list[dict[str, Any]]:
    """Las ocho partidas contra Odoo, y la invariante que necesita sus datos.

    Séptima pieza de la Fase 2.4, y la que completa el balance: con las 15
    internas ya en este módulo, al endpoint le queda el armado de la respuesta y
    nada más.

    **Por qué extraerlas si ya tenían un test de punta a punta.** Ese test las
    ejercita a través del endpoint, con las cuatro páginas sustituidas y un Odoo de
    mentira montado a mano: media página de andamiaje por caso. Acá se les puede
    pasar un ``execute`` de tres líneas y probar una partida sola.

    **Y son las que importan.** Una partida interna compara dos vistas que salen de
    la misma función, así que no puede detectar que el número esté mal: un error en
    la función de origen se propaga igual a los dos lados. Las ocho de acá comparan
    contra Odoo, la única fuente que no somos nosotros, así que son las que pueden
    decir «el número está mal» y no solo «las dos páginas no coinciden». La
    invariante es la novena y la más fuerte: verifica una propiedad que tiene que
    ser cierta por aritmética.

    **Todo entra por parámetro, y eso es el punto.** ``execute`` es la función de
    Odoo, y ``None`` cuando no hay conexión: entonces esto devuelve una lista vacía
    y el balance queda con sus 15 internas, que es exactamente lo que ya hacía.
    ``tasas`` es el objeto ``Tasas`` ya indexado — antes se pedía cinco veces con
    ``tasas_vigentes(repo)`` y ahora entra una sola. ``tasa_de_la_fecha`` es el
    respaldo para cuando ``bcv_usd`` no tiene dato de ese día.

    El cuerpo se movió tal cual, con tres renombres mecánicos y ninguna reescritura:
    la conexión y el repositorio dejaron de resolverse acá adentro, las cinco
    consultas de tasas pasaron a usar el objeto recibido, y la lectura de la serie
    salió porque su único consumidor ahora entra por ``tasa_de_la_fecha``.
    """
    partidas: list[dict[str, Any]] = []

    def externa(*args: Any, **kwargs: Any) -> None:
        # El tipo se FIJA, no se pasa como default: un llamador que mandara
        # ``tipo=`` acá estaría clasificando mal una partida, y con
        # ``crear_partida(..., tipo="externa")`` eso era un TypeError en
        # ejecución en vez de un error al escribirlo.
        kwargs["tipo"] = "externa"
        partidas.append(crear_partida(*args, **kwargs))

    def invariante(*args: Any, **kwargs: Any) -> None:
        kwargs["tipo"] = "invariante"
        partidas.append(crear_partida(*args, **kwargs))

    if execute:
        repo_bal = repo

        # 7a. Las órdenes: el espejo local contra sale.order.
        try:
            ordenes_repo = {o.so_id: o for o in repo_bal.all_ordenes()}
            nombres = sorted(so for so in items if so in ordenes_repo)
            odoo_ordenes = execute(
                "sale.order",
                "search_read",
                [[["name", "in", nombres]]],
                {"fields": ["name", "amount_total", "state"]},
            )
            vivas_o = [o for o in odoo_ordenes if o.get("state") != "cancel"]
            encontradas = {str(o["name"]) for o in vivas_o}
            externa(
                "Ventas reales (órdenes) contra Odoo",
                "espejo local",
                sum(
                    float(ordenes_repo[so].monto_total or 0.0)
                    for so in nombres
                    if so in encontradas
                ),
                "sale.order en Odoo",
                sum(float(o.get("amount_total") or 0.0) for o in vivas_o),
                f"{len(vivas_o)} órdenes vivas en Odoo de {len(nombres)} en el "
                "espejo. Se comparan solo las que existen en ambos lados.",
                tolerancia=5.0,
            )
        except Exception as e:
            logger.warning("No se pudo comparar las órdenes contra Odoo: %s", e)

        # 7b. La facturación: lo facturado y lo que Odoo da por cobrar.
        try:
            facturas = repo_bal.all_facturas()
            ids_fact = [int(f.factura_id) for f in facturas if str(f.factura_id).isdigit()]
            odoo_fact = (
                execute(
                    "account.move",
                    "read",
                    [ids_fact],
                    {
                        "fields": [
                            "amount_total",
                            "amount_residual",
                            # El saldo por cobrar YA en dólares, calculado
                            # por Odoo. Sin esto no había forma de
                            # comparar las dos vistas en la misma unidad.
                            "amount_residual_usd",
                            "state",
                            "move_type",
                        ]
                    },
                )
                if ids_fact
                else []
            )
            vivas_f = {
                int(m["id"]): m for m in odoo_fact if m.get("state") not in ("cancel", "draft")
            }
            externa(
                "Facturado contra Odoo",
                "espejo local",
                sum(
                    float(f.monto_total or 0.0)
                    for f in facturas
                    if str(f.factura_id).isdigit() and int(f.factura_id) in vivas_f
                ),
                "account.move en Odoo",
                sum(float(m.get("amount_total") or 0.0) for m in vivas_f.values()),
                f"{len(vivas_f)} facturas publicadas de {len(facturas)} en el espejo.",
                tolerancia=5.0,
            )
            # Lo que Odoo considera pendiente de cobro en sus facturas,
            # contra lo que el Reporte de Saldos muestra por ese mismo
            # concepto. Son las dos respuestas a "cuánto debe esa
            # factura" y no pueden diferir.
            # Odoo SÍ expone el saldo por cobrar en dólares:
            # ``amount_residual_usd`` ("Importe adeudado Ref."). Con eso
            # las dos vistas se comparan en la misma unidad, que era lo
            # que faltaba -- antes esta partida enfrentaba 60.368,21 USD
            # contra 65.162.339,64 VES y no probaba nada.
            #
            # Acá salieron 4.289,57 de descuadre, y el diagnóstico
            # fue al revés de lo que parecía: NO era que Odoo usara otra
            # tasa. El reporte convertía con un mapa armado solo con
            # ``SerieTasas`` (40 días) y caía a la tasa de HOY para todo
            # lo anterior -- ver ``tasa_bcv_de_dia``. Con la serie
            # completa las dos vistas coinciden. La tolerancia sigue
            # siendo porcentual porque un día de desfase entre la serie
            # de Odoo y la nuestra es normal y no significa nada.
            #
            # Se comparan solo las facturas de las órdenes que el
            # reporte lista; el universo de Odoo incluye facturas de
            # órdenes ya cobradas, que el reporte excluye a propósito.
            residual_odoo = sum(
                float(m.get("amount_residual") or 0.0) for m in vivas_f.values()
            )
            con_residual_odoo = {
                mid
                for mid, m in vivas_f.items()
                if abs(float(m.get("amount_residual") or 0.0)) > 0.05
            }
            con_saldo_reporte = {
                int(i["factura_id"])
                for i in saldos_items.values()
                if str(i.get("factura_id") or "").isdigit()
                and num(i, "saldo_factura_odoo") > 0.05
            }
            solo_reporte = con_saldo_reporte - con_residual_odoo
            # Las facturas de las órdenes que el reporte efectivamente
            # lista -- el universo comparable.
            ordenes_rep = {o.so_id: o for o in repo_bal.all_ordenes()}
            ids_comparables = {
                int(ordenes_rep[str(i["so_id"])].factura_id)
                for i in saldos_items.values()
                if str(i["so_id"]) in ordenes_rep
                and str(ordenes_rep[str(i["so_id"])].factura_id or "").isdigit()
            }
            odoo_usd = sum(
                float(m.get("amount_residual_usd") or 0.0)
                for mid, m in vivas_f.items()
                if mid in ids_comparables
            )
            rep_usd = sum(num(i, "saldo_factura_odoo") for i in saldos_items.values())
            externa(
                "Saldo por cobrar en USD de lo facturado",
                "Reporte de Saldos",
                rep_usd,
                "amount_residual_usd en Odoo",
                odoo_usd,
                f"Sobre {len(ids_comparables)} facturas de las órdenes que el "
                "reporte lista. Ambos lados convierten el residual con la "
                "tasa BCV de la fecha de cada factura; lo que quede es el "
                "desfase de un día entre la serie de Odoo y la nuestra.",
                tolerancia=max(50.0, odoo_usd * 0.01),
            )
            # Y la otra mitad del criterio: "solo valida que coincida
            # con el BCV de ese día".
            #
            # Ahora que los montos salen de Odoo, su tasa dejó de ser
            # una opinión más y pasó a ser la que mueve la cuenta por
            # cobrar. Esta partida es la que vigila que esa tasa sea la
            # del BCV: por cada factura en bolívares se despeja la tasa
            # implícita (residual en VES / residual en USD) y se compara
            # contra nuestro BCV del día de la factura.
            #
            # Un día de desfase es normal -- Odoo publica la tasa con la
            # fecha del asiento y nosotros con la del día -- así que se
            # cuentan solo las que se pasan del 2 %.
            divergentes: list[str] = []
            # Cuántas se pudieron comparar de verdad. Sin esto la partida
            # reportaba "0 divergentes" tanto cuando verificó y estaba todo
            # bien como cuando NO PUDO VERIFICAR NINGUNA -- y los dos se
            # leían igual, en verde. Pasa siempre que falte NUESTRA serie de
            # tasas para la fecha de la factura: el ``continue`` de abajo
            # saltea la factura en silencio. Reproducido corriendo el
            # escenario "Cargan una tasa equivocada en Odoo" contra un
            # espejo sin serie sembrada: se desvió la tasa un 50 % y la
            # partida siguió en verde porque no comparó ni una.
            comparadas = 0
            sin_tasa_nuestra = 0
            for mid, m in vivas_f.items():
                if mid not in ids_comparables:
                    continue
                ves = abs(float(m.get("amount_residual") or 0.0))
                usd = abs(float(m.get("amount_residual_usd") or 0.0))
                if ves <= 0.05 or usd <= 0.05:
                    continue
                # Contra la OFICIAL del BCV de esa fecha valor, no
                # contra ``tasa_bcv_de_dia``: esa prefiere la captura de
                # SerieTasas del mismo día, que es el intradía crudo y de
                # noche ya trae la tasa de mañana. El histórico está
                # alineado 147 de 147 días con lo que publica el BCV.
                try:
                    f_inv = date.fromisoformat(str(m.get("invoice_date") or "")[:10])
                except (TypeError, ValueError):
                    continue
                nuestra = float(tasas.bcv_usd(f_inv, arrastrar=False) or 0.0)
                if nuestra <= 0:
                    sin_tasa_nuestra += 1
                    continue
                comparadas += 1
                if abs((ves / usd) / nuestra - 1.0) > 0.02:
                    divergentes.append(
                        f"{m.get('name') or mid} ({ves / usd:,.2f} vs {nuestra:,.2f})"
                    )
            externa(
                "La tasa de Odoo coincide con el BCV del día",
                "esperado",
                0.0,
                "facturas con más de 2 % de desviación",
                float(len(divergentes)),
                "Los saldos en dólares se toman de Odoo, así que su tasa "
                "es la que manda; acá se verifica que sea la del BCV. Se "
                "despeja la tasa implícita de cada factura en bolívares "
                "(residual VES / residual USD) contra nuestro BCV de su "
                "fecha. "
                + (
                    f"Comparadas {comparadas}."
                    if comparadas
                    else "NO SE COMPARÓ NINGUNA: sin nuestra serie de tasas para "
                    "esas fechas, esta partida no puede opinar y el cero de la "
                    "derecha no significa que esté todo bien."
                )
                + (
                    f" {sin_tasa_nuestra} salteadas por falta de tasa nuestra."
                    if sin_tasa_nuestra
                    else ""
                )
                + (f" Divergen: {', '.join(divergentes[:5])}." if divergentes else ""),
            )
            externa(
                "Facturas por cobrar: el reporte contra Odoo",
                "esperado",
                0.0,
                "el reporte da por cobrar facturas que Odoo ya saldó",
                float(len(solo_reporte)),
                f"Odoo tiene {len(con_residual_odoo)} facturas con residual "
                f"({residual_odoo:,.2f} en su moneda, {odoo_usd:,.2f} USD en "
                f"las comparables); el reporte muestra {len(con_saldo_reporte)} "
                "con saldo.",
            )
        except Exception as e:
            logger.warning("No se pudo comparar la facturación contra Odoo: %s", e)

        # 7c. Los pagos: por diario, en su moneda y en su equivalente BCV.
        try:
            pagos = repo_bal.all_pagos()
            vincs = repo_bal.all_vinculaciones()
            por_diario: dict[str, dict[str, float]] = {}
            for p in pagos:
                d = str(getattr(p, "metodo_pago", "") or "sin diario")
                fila = por_diario.setdefault(d, {"VES": 0.0, "USD": 0.0})
                moneda = str(getattr(p, "moneda", "") or "USD").upper().replace("MONEDA.", "")
                fila[moneda if moneda in fila else "USD"] += float(
                    getattr(p, "monto", 0.0) or 0.0
                )
            total_ves = sum(f["VES"] for f in por_diario.values())
            total_usd = sum(f["USD"] for f in por_diario.values())
            ids_pago = [int(p.pago_id) for p in pagos if str(p.pago_id).isdigit()]
            odoo_pagos = (
                execute(
                    "account.payment",
                    "read",
                    [ids_pago],
                    # ``amount_ref`` es el equivalente en dólares que
                    # lleva Odoo: la unidad en la que el usuario pidió
                    # comparar los pagos. ``move_id`` es para detectar
                    # reembolsos embebidos -- ver ``reembolsos`` abajo.
                    {"fields": ["amount", "state", "currency_id", "amount_ref", "move_id"]},
                )
                if ids_pago
                else []
            )
            vivos = [p for p in odoo_pagos if p.get("state") != "cancel"]

            # Algunos pagos traen una devolución de sobrepago embebida en el
            # MISMO asiento (ver ``montos_reembolsados_en_pagos``): nuestro
            # espejo ya la resta al sincronizar (``changed_pagos``), pero acá
            # se está leyendo el ``amount``/``amount_ref`` CRUDOS de Odoo, sin
            # esa resta. Comparar neto (nuestro) contra bruto (Odoo, sin
            # corregir) hacía ver un pago con reembolso como "plata que
            # falta" o "tasa mal cargada" cuando la plata cuadra -- hallazgo
            # del 19-sep-2026, pago real de producción (id 1084): reembolso
            # de 2.372.712,49 VES en el mismo asiento, que explicaba solas
            # las tres partidas de pagos descuadradas.
            move_ids_vivos = [
                int(m) for p in vivos if (m := _id_de_m2o(p.get("move_id")))
            ]
            reembolsos = (
                montos_reembolsados_en_pagos(execute, move_ids_vivos)
                if move_ids_vivos
                else {}
            )

            def _neto(p: dict[str, Any]) -> tuple[float, float]:
                """(amount neto, amount_ref neto) de un pago de Odoo.

                El reembolso está en la moneda PROPIA del pago (misma unidad
                que ``amount``), así que se resta directo. ``amount_ref`` se
                reduce en la misma PROPORCIÓN -- no hay una tasa "correcta"
                más simple que no dependa de lo que se está auditando, y la
                proporción no lo hace: un pago sin reembolso queda intacto
                (proporción 1), y uno con reembolso reduce su equivalente
                exactamente en la parte que se devolvió.
                """
                bruto = float(p.get("amount") or 0.0)
                ref_bruto = float(p.get("amount_ref") or 0.0)
                move_id = _id_de_m2o(p.get("move_id"))
                reembolso = float(reembolsos.get(int(move_id), 0)) if move_id else 0.0
                if reembolso <= 0.0 or bruto <= 0.0:
                    return bruto, ref_bruto
                neto = max(0.0, bruto - reembolso)
                return neto, ref_bruto * (neto / bruto)

            netos_por_id = {int(p["id"]): _neto(p) for p in vivos}

            externa(
                "Pagos: importe en su moneda contra Odoo",
                "espejo local (VES + USD)",
                total_ves + total_usd,
                "account.payment en Odoo",
                sum(netos_por_id[int(p["id"])][0] for p in vivos),
                f"{len(vivos)} pagos vivos en Odoo de {len(pagos)} en el espejo, "
                f"repartidos en {len(por_diario)} diarios. VES {total_ves:,.2f} + "
                f"USD {total_usd:,.2f}."
                + (
                    f" {len(reembolsos)} pago(s) con un reembolso de sobrepago "
                    "embebido en el mismo asiento -- se compara neto contra "
                    "neto, igual que hace nuestro espejo al sincronizar."
                    if reembolsos
                    else ""
                ),
                tolerancia=5.0,
            )
            # El mismo universo de pagos, pero en dólares.
            #
            # Pedido del usuario: "puedes comparar en los pagos y en lo
            # facturado vs Odoo los equivalentes en BCV, no los
            # bolívares". La partida de arriba suma un pago en VES y uno
            # en USD como si fueran la misma unidad, y cuadra igual
            # aunque la tasa esté mal: a los dos lados se está sumando el
            # mismo nominal.
            #
            # Odoo lleva su propio equivalente en ``amount_ref``
            # ("Importe referencia"). Convertir cada pago en bolívares
            # con NUESTRA serie y comparar el total contra el suyo audita
            # la serie de tasas entera, día por día: si un día quedó con
            # la tasa cambiada, el descuadre aparece acá y en ningún otro
            # lado.
            vivos_por_id = {int(x["id"]): x for x in vivos}
            eq_nuestro = eq_odoo = 0.0
            en_euros = 0
            sin_intradia = 0
            for p in pagos:
                if not str(p.pago_id).isdigit():
                    continue
                m_pago = vivos_por_id.get(int(p.pago_id))
                if not m_pago:
                    continue
                moneda = str(getattr(p, "moneda", "") or "USD").upper().replace("MONEDA.", "")
                monto = float(getattr(p, "monto", 0.0) or 0.0)
                # ``monto`` (nuestro) ya viene NETO de reembolso desde el
                # sync (``changed_pagos``); ``amount_ref`` neto es el que
                # calculó ``_neto`` arriba, en la misma proporción.
                ref_p = netos_por_id.get(int(p.pago_id), (0.0, 0.0))[1]
                if moneda == "VES":
                    # La tasa oficial del BCV para esa fecha valor,
                    # leída del histórico -- que desde septiembre 2026
                    # está alineado al 100 % con las series que publica
                    # el BCV (147 de 147 días, en USD y en EUR).
                    #
                    # Se lee el histórico directo y no ``tasa_bcv_de_dia``
                    # a propósito: esa función prefiere la captura de
                    # ``SerieTasas`` del mismo día, que es el valor
                    # intradía crudo y de noche ya trae la tasa de
                    # MAÑANA. Para auditar contra Odoo lo que sirve es la
                    # oficial del día.
                    tasa_dec = tasas.bcv_usd(p.fecha_pago, arrastrar=False)
                    tasa_p = float(tasa_dec or 0.0)
                    sin_intradia += 0 if tasa_p > 0 else 1
                    if tasa_p <= 0:
                        tasa_p = float(
                            tasa_de_la_fecha(p.fecha_pago) or 0.0
                        )
                    # Un puñado de abonos se cobraron en EUROS y Odoo los
                    # convirtió con la tasa BCV del euro, ~16 % por
                    # encima del dólar. Convertirlos a BCV-USD los hacía
                    # aparecer como "tasa mal cargada" cuando la tasa
                    # está perfecta -- solo es otra moneda. Se detectan
                    # por la tasa implícita del propio pago, no por una
                    # lista fija, así que el que aparezca mañana también
                    # queda cubierto.
                    eur = tasas.bcv_eur(p.fecha_pago, arrastrar=False)
                    implicita = monto / ref_p if ref_p > 0 else 0.0
                    if (
                        eur
                        and implicita > 0
                        and tasa_p > 0
                        and abs(implicita / float(eur) - 1.0) < 0.01
                        and abs(implicita / tasa_p - 1.0) >= 0.01
                    ):
                        en_euros += 1
                        eq_nuestro += monto / float(eur)
                    else:
                        eq_nuestro += monto / tasa_p if tasa_p > 0 else 0.0
                else:
                    eq_nuestro += monto
                eq_odoo += ref_p
            # Y la misma validación que ya existe para las facturas,
            # ahora pago por pago.
            #
            # Pedido del usuario: "validar que la tasa que odoo esta
            # reportando en los pagos y las facturas se corresponda con
            # la tasa correcta de ese dia".
            #
            # La partida de abajo da un total y por lo tanto solo dice
            # CUÁNTO se desvía el conjunto; esta dice CUÁLES pagos y por
            # qué tasa, que es lo que sirve para ir a corregirlos en
            # Odoo. Se despeja la tasa que Odoo estampó (nominal en
            # bolívares / ``amount_ref``) y se compara contra la oficial
            # del BCV de esa fecha valor.
            #
            # El 2 % de margen deja pasar el redondeo de ``amount_ref``,
            # que Odoo guarda con dos decimales: en un abono chico eso
            # solo mueve centésimas de punto.
            tasa_mal: list[str] = []
            pagos_comparados = 0
            pagos_sin_tasa_nuestra = 0
            for p in pagos:
                if not str(p.pago_id).isdigit():
                    continue
                m_val = vivos_por_id.get(int(p.pago_id))
                if not m_val:
                    continue
                if str(getattr(p, "moneda", "") or "").upper().replace(
                    "MONEDA.", ""
                ) != "VES":
                    continue
                nominal = float(getattr(p, "monto", 0.0) or 0.0)
                # Neto contra neto -- ver el comentario de ``ref_p`` más
                # arriba. Con el ``amount_ref`` bruto, un pago con reembolso
                # embebido salía "con la tasa mal cargada" sin que la tasa
                # tuviera nada que ver.
                ref_val = netos_por_id.get(int(p.pago_id), (0.0, 0.0))[1]
                if nominal <= 0.0 or ref_val <= 0.0:
                    continue
                oficial = float(
                    tasas.bcv_usd(p.fecha_pago, arrastrar=False) or 0.0
                )
                if oficial <= 0:
                    # Sin NUESTRA tasa para esa fecha no hay contra qué
                    # comparar, y saltear en silencio hacía que la partida
                    # reportara "0 divergentes" -- indistinguible de
                    # "verifiqué y está todo bien". Ver la partida hermana
                    # de facturas, donde el mismo hueco quedó documentado.
                    pagos_sin_tasa_nuestra += 1
                    continue
                pagos_comparados += 1
                estampada = nominal / ref_val
                if abs(estampada / oficial - 1.0) <= 0.02:
                    continue
                # Los abonos cobrados en euros no son un error de tasa:
                # su tasa es la del euro y está bien. Ya se reconocen
                # arriba, así que acá solo se descartan.
                eur_dia = tasas.bcv_eur(p.fecha_pago, arrastrar=False)
                if eur_dia and abs(estampada / float(eur_dia) - 1.0) < 0.01:
                    continue
                tasa_mal.append(
                    f"pago {p.pago_id} del {p.fecha_pago.date().isoformat()} "
                    f"({estampada:,.2f} vs {oficial:,.2f})"
                )
            externa(
                "La tasa de Odoo en los pagos coincide con el BCV del día",
                "esperado",
                0.0,
                "pagos con más de 2 % de desviación",
                float(len(tasa_mal)),
                "Se despeja la tasa que Odoo estampó en cada abono en "
                "bolívares (nominal / amount_ref) y se compara contra la "
                "oficial del BCV de esa fecha valor. Los abonos cobrados "
                "en euros no cuentan: su tasa es la del euro y está bien. "
                + (
                    f"Comparados {pagos_comparados}."
                    if pagos_comparados
                    else "NO SE COMPARÓ NINGUNO: sin nuestra serie de tasas para "
                    "esas fechas, esta partida no puede opinar y el cero de la "
                    "derecha no significa que esté todo bien."
                )
                + (
                    f" {pagos_sin_tasa_nuestra} salteados por falta de tasa nuestra."
                    if pagos_sin_tasa_nuestra
                    else ""
                )
                + (f" Divergen: {'; '.join(tasa_mal[:5])}." if tasa_mal else ""),
            )

            externa(
                "Pagos: equivalente BCV contra Odoo",
                "nuestra serie de tasas",
                eq_nuestro,
                "amount_ref en Odoo",
                eq_odoo,
                f"Los {len(vivos_por_id)} pagos vivos llevados a dólares con "
                "la tasa BCV de su propia fecha. Un descuadre acá es una "
                "tasa mal cargada, no un pago que falte: los importes "
                "nominales ya cuadran en la partida anterior. Se compara "
                "con la tasa oficial del BCV de esa fecha valor: el BCV "
                "calcula la tasa al cierre y la publica con fecha valor "
                "del día siguiente, así que la que rige hoy es la que él "
                "publicó ayer."
                + (
                    f" Quedan {sin_intradia} pagos cuyo día no está en el "
                    "histórico oficial; esos van con la serie del scraper."
                    if sin_intradia
                    else ""
                )
                + (
                    f" {en_euros} abonos se cobraron en euros y se "
                    "convierten con la tasa BCV del euro."
                    if en_euros
                    else ""
                ),
                tolerancia=max(200.0, eq_odoo * 0.005),
            )

            # El equivalente BCV se congela por vinculación al momento de
            # aplicar; su suma tiene que dar lo mismo que convertir el
            # pago entero, o hay una vinculación con la tasa cambiada.
            eq_bcv = sum(float(getattr(v, "equiv_usd_bcv", 0.0) or 0.0) for v in vincs)
            aplicado = sum(float(getattr(v, "monto_aplicado", 0.0) or 0.0) for v in vincs)
            # Comparar la suma del equivalente contra la del nominal no
            # prueba nada: el nominal mezcla bolívares y dólares. Lo que
            # sí es una regla dura es que el equivalente en dólares de un
            # abono NUNCA puede superar su monto nominal en bolívares --
            # eso solo pasa con una tasa mal congelada.
            mal_congeladas = [
                v
                for v in vincs
                if str(getattr(v, "moneda_abono", "")).upper().endswith("VES")
                and float(getattr(v, "equiv_usd_bcv", 0.0) or 0.0)
                > float(getattr(v, "monto_aplicado", 0.0) or 0.0)
            ]
            invariante(
                "Pagos en bolívares: equivalente BCV plausible",
                "esperado",
                0.0,
                "vinculaciones con equivalente mayor que el nominal",
                float(len(mal_congeladas)),
                f"Suma de equivalentes: {eq_bcv:,.2f} USD sobre "
                f"{aplicado:,.2f} nominales (mezcla de monedas). Un "
                "equivalente en dólares por encima del monto en bolívares "
                "significa una tasa mal congelada.",
            )
        except Exception as e:
            logger.warning("No se pudieron comparar los pagos contra Odoo: %s", e)

    return partidas
