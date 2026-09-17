"""Cuánto falta cobrar de una orden, en sus cuatro referencias.

Primera pieza de la Fase 2.4 del plan de blindaje: sacar los caminos de dinero
de ``web/app.py``. Se eligió ésta para empezar por tres motivos que se refuerzan:

* es la **fuente única de verdad sobre cuánto falta cobrar** — el balance de
  comprobación lo dice así en su propio docstring, y toda partida que no cuadre
  significa que alguien dejó de usarla;
* es una función **pura**: entra un diccionario, sale un diccionario, sin
  repositorio, sin Odoo y sin caché. Se puede probar de a una sin levantar la
  aplicación, que es exactamente la razón por la que ``app.py`` está al 62 % y
  el motor entre 94 y 100 %;
* contiene una de las minas del inventario 1.1, y sacarla a la luz es el primer
  paso para poder discutirla (ver ``diagnostico_de_saldos``).

**No cambia ningún comportamiento.** La lógica es la que estaba en
``app.py:_saldos_4_columnas_item``, movida tal cual. Lo que se agrega es
``diagnostico_de_saldos``, que no calcula saldos sino que explica por qué
salieron así — y ésa es la parte nueva.

Las cuatro referencias, y por qué son cuatro:

``teorico_bs`` y ``teorico_usd``
    Lo que la orden debía según su teórico en cada una de las dos listas. Cuál
    de las dos manda depende de cómo termine pagando el cliente, y eso no se
    sabe hasta que paga -- por eso se llevan las dos.

``venta_real``
    Lo que Odoo dice que vale la orden, neto de los descuentos que gerencia
    aprobó a mano.

``factura_real``
    Lo mismo pero sobre lo facturado. Es ``None``, no cero, cuando la orden
    todavía no se facturó: no hay factura, así que no hay saldo de factura. La
    distinción importa y es la que el resto del módulo no hace.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Los campos del ítem de Ventas que alimentan cada referencia. Estar acá, con
# nombre, es lo que permite que ``diagnostico_de_saldos`` diga cuál faltaba.
CAMPO_TEORICO_VES = "ves_neta_teorica_iva"
CAMPO_TEORICO_USD = "usd_neta_teorica_iva"
CAMPO_VENTA_REAL = "venta_neta_real"
CAMPO_FACTURADO = "total_facturado_neto"
CAMPO_DESCUENTO_SISTEMA = "descuento_aplicado_sistema"
CAMPO_PAGADO_BCV = "pagado_teorico_bcv_incl_pendiente"
CAMPO_PAGADO_BINANCE = "pagado_teorico_binance_incl_pendiente"
CAMPO_PAGADO_REFERENCIA = "monto_pagado_factura_odoo_incl_pendiente"


def _num(item: dict[str, Any], campo: str) -> float:
    try:
        return float(item.get(campo) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def saldos_de_la_orden(item: dict[str, Any]) -> dict[str, float | None]:
    """Los 4 saldos pendientes de una orden, en tiempo real.

    Reusada por ``/api/ventas``, ``/api/reporte-cxc-cliente`` y
    ``/api/cobranza/pagos`` (el "Saldo Orden (CxC)" del modal de detalle de
    pago). Antes ``/api/cobranza/pagos`` mostraba un solo saldo mezclado; ahora
    son las mismas 4 referencias que el resto del sistema ya usa.

    Pedido explícito del usuario (agosto 2026, cliente CONSTRUCTORA GRANO
    AGREGADO / orden S00608): una Vinculación PENDIENTE de esta orden -- ya
    vinculada localmente, pero Odoo todavía no la reconcilió -- SÍ se resta de
    estos 4 saldos. Antes no restaba nada (solo contaba ``CONCILIADO``), así
    que un pago ya vinculado pero sin confirmar no aparecía ni como pagado ni
    como saldo a favor en ningún lado, y se mostraba el saldo completo sin
    tocar. De ahí los campos ``*_incl_pendiente``: es el mismo "beneficio de la
    duda" que ya usan ``sale_de_cxc``/``saldo_cxc`` en Ventas, y nunca gatea
    nada real (descuentos, salida de CxC confirmada) -- solo cambia lo que se
    MUESTRA acá.
    """
    desc_sistema = _num(item, CAMPO_DESCUENTO_SISTEMA)
    pagado_bcv = _num(item, CAMPO_PAGADO_BCV)
    pagado_binance = _num(item, CAMPO_PAGADO_BINANCE)
    pagado_ref = _num(item, CAMPO_PAGADO_REFERENCIA)

    saldo_teorico_bs = max(0.0, _num(item, CAMPO_TEORICO_VES) - pagado_bcv)
    saldo_teorico_usd = max(0.0, _num(item, CAMPO_TEORICO_USD) - pagado_binance)
    saldo_venta_real = max(0.0, _num(item, CAMPO_VENTA_REAL) - desc_sistema - pagado_ref)
    facturada = bool(item.get("facturada"))
    saldo_factura_real = (
        max(0.0, _num(item, CAMPO_FACTURADO) - desc_sistema - pagado_ref) if facturada else None
    )
    return {
        "teorico_bs": saldo_teorico_bs,
        "teorico_usd": saldo_teorico_usd,
        "venta_real": saldo_venta_real,
        "factura_real": saldo_factura_real,
    }


@dataclass(frozen=True)
class DiagnosticoSaldo:
    """Por qué los saldos de una orden salieron como salieron.

    No cambia ningún saldo: los explica. Existe porque ``saldos_de_la_orden``
    tiene dos comportamientos que un número solo no puede distinguir, y los dos
    están en el inventario 1.1 como mina de severidad alta:

    ``referencias_ausentes``
        Un teórico que **falta** entra al cálculo como ``0``, así que la orden
        sale con saldo cero -- o sea, **cobrada**. Un teórico ausente y un
        teórico genuinamente cero se ven idénticos, y no significan lo mismo:
        el primero quiere decir "el motor todavía no la calculó", el segundo
        "no hay nada que cobrar".

    ``recortes_a_cero``
        El ``max(0, …)`` tapa los negativos. Un saldo negativo es un sobrepago
        o un teórico mal calculado, y las dos cosas hay que verlas. Acá queda
        registrado cuánto se recortó y en qué referencia.

    ``evaluable`` es la pregunta que el llamador debería hacerse antes de
    mostrar el número: con referencias ausentes, el saldo no es un saldo.
    """

    so_id: str
    referencias_ausentes: tuple[str, ...]
    recortes_a_cero: dict[str, float]

    @property
    def evaluable(self) -> bool:
        return not self.referencias_ausentes

    @property
    def hay_sobrepago(self) -> bool:
        return any(v > 0.01 for v in self.recortes_a_cero.values())

    def __str__(self) -> str:
        if self.evaluable and not self.hay_sobrepago:
            return f"{self.so_id}: sin observaciones"
        partes = []
        if self.referencias_ausentes:
            partes.append("sin " + ", ".join(self.referencias_ausentes))
        for ref, monto in sorted(self.recortes_a_cero.items(), key=lambda x: -x[1]):
            if monto > 0.01:
                partes.append(f"{ref} recortado en {monto:,.2f}")
        return f"{self.so_id}: " + "; ".join(partes)


def diagnostico_de_saldos(item: dict[str, Any]) -> DiagnosticoSaldo:
    """Explica los saldos de una orden sin cambiarlos.

    Se separó del cálculo a propósito: cambiar lo que ``saldos_de_la_orden``
    devuelve mueve montos que hoy están en pantalla, y eso es una decisión del
    usuario (Fase 2.1 del plan). Esto se puede agregar hoy porque no toca
    ningún número -- solo permite preguntar.
    """
    ausentes = tuple(
        nombre
        for nombre, campo in (
            ("teórico VES", CAMPO_TEORICO_VES),
            ("teórico USD", CAMPO_TEORICO_USD),
            ("venta real", CAMPO_VENTA_REAL),
        )
        if item.get(campo) is None
    )

    desc_sistema = _num(item, CAMPO_DESCUENTO_SISTEMA)
    pagado_ref = _num(item, CAMPO_PAGADO_REFERENCIA)
    crudos = {
        "teorico_bs": _num(item, CAMPO_TEORICO_VES) - _num(item, CAMPO_PAGADO_BCV),
        "teorico_usd": _num(item, CAMPO_TEORICO_USD) - _num(item, CAMPO_PAGADO_BINANCE),
        "venta_real": _num(item, CAMPO_VENTA_REAL) - desc_sistema - pagado_ref,
    }
    if item.get("facturada"):
        crudos["factura_real"] = _num(item, CAMPO_FACTURADO) - desc_sistema - pagado_ref

    return DiagnosticoSaldo(
        so_id=str(item.get("so_id") or "?"),
        referencias_ausentes=ausentes,
        recortes_a_cero={ref: -v for ref, v in crudos.items() if v < 0},
    )


# --- las notas de crédito de Odoo, en dólares ------------------------------


def valor_usd_de_notas_de_credito(
    notas: list[dict[str, Any]],
    fecha_fallback: str,
    tasas_rows: list[dict[str, Any]],
    tasa_del_dia: Callable[[str, list[dict[str, Any]]], float],
) -> tuple[float, list[str]]:
    """Cuánto valen en dólares las notas de crédito reales de una orden.

    Novena pieza de la Fase 2.4. Vivía dentro de ``_get_reporte_saldos_sync``, en
    el medio de un bloque de 223 líneas, y **no tenía un solo test propio** aunque
    es lo que reduce la deuda de un cliente por una nota de crédito ya emitida.

    Cada nota trae su propia moneda y su propia fecha, y se convierte con la tasa
    **de su día**, no la de hoy ni la de la orden: una nota de crédito de marzo
    vale lo que valía en marzo. Cuando la nota no tiene fecha se usa la de la
    orden (``fecha_fallback``), que es lo más cercano disponible.

    ``tasa_del_dia`` entra por parámetro porque vive en ``web/app.py`` y depende
    de la política de tasas; acá solo se la aplica.

    Devuelve ``(monto en USD, nombres de las notas)``. Los nombres se devuelven
    porque la pantalla los muestra: una deuda reducida sin decir por qué documento
    es un número que nadie puede verificar.
    """
    monto_usd = 0.0
    nombres: list[str] = []
    for nc in notas:
        total = float(nc.get("amount_total") or 0)
        moneda_raw = nc.get("currency_id")
        moneda = (
            moneda_raw[1]
            if isinstance(moneda_raw, list | tuple) and len(moneda_raw) > 1
            else "USD"
        )
        fecha = str(nc.get("invoice_date") or fecha_fallback)[:10]
        tasa = tasa_del_dia(fecha, tasas_rows)
        if moneda == "VES":
            if tasa <= 0:
                # Sin tasa, esta nota NO se cuenta.
                #
                # Antes se sumaba el monto en bolívares COMO SI fuera dólares:
                # una nota de 1.362.751,66 Bs reducía la deuda en 1.362.751,66
                # USD sobre una orden de 1.860,48. Medido en la copia sin tasas
                # el 11-sep-2026, al convertir el default de 2019 en error duro:
                # la mina pasó de dar 37.335,66 (el monto a 36,50) a dar el
                # nominal entero. El error duro no la creó, la destapó.
                #
                # No contarla deja la deuda MÁS ALTA de lo que corresponde, que
                # es el lado seguro: se persigue un cobro que capaz ya está
                # acreditado, en vez de dar por saldada una orden que no lo
                # está. Y la nota queda listada igual, así que la pantalla dice
                # que existe aunque no pueda valuarla.
                nombres.append(str(nc.get("name", "")))
                continue
            monto_usd += total / tasa
        else:
            monto_usd += total
        nombres.append(str(nc.get("name", "")))
    return monto_usd, nombres


# --- el saldo deudor, que es el que consume el FIFO ------------------------


@dataclass(frozen=True)
class SaldosDeudores:
    """Los cuatro saldos de una orden, antes y después de descuentos."""

    deudor_bcv: float
    deudor_lista_usd: float
    con_descuento_bcv: float
    con_descuento_lista_usd: float
    # Por qué quedó en cero, cuando quedó en cero. Un cero con motivo es un dato;
    # un cero pelado es la trampa que este blindaje persigue.
    motivo_del_cero: str | None = None


def saldos_deudores(
    *,
    monto_total: float,
    monto_proyectado_usd: float,
    abono_bcv: float,
    abono_binance: float,
    descuentos_motor: float,
    notas_credito_usd: float,
    iva: float,
    lineas: list[dict[str, Any]] | None,
    descuento_no_otorgado: bool = False,
) -> SaldosDeudores:
    """Cuánto debe una orden. **Es el saldo que consume el FIFO.**

    Novena pieza de la Fase 2.4, y la que más pesaba: de este número depende si
    una orden sale de la cuenta por cobrar. Vivía en el medio de
    ``_get_reporte_saldos_sync`` y no tenía tests propios, con **cuatro trampas
    documentadas** que ahora quedan fijadas de a una:

    **1. El IVA se reaplica al descuento antes de restarlo.** Los descuentos del
    motor son sobre el subtotal (sin impuesto) y los saldos traen IVA. Restar
    directo subestima la deuda: un descuento de 100 reduce un total con IVA en
    116, no en 100. Las notas de crédito de Odoo **no** llevan este ajuste --
    ya son documentos reales con impuesto incluido.

    **2. La cuenta por cobrar nace con la entrega.** Criterio del usuario: «la
    orden, si no ha sido entregada, no es susceptible de cobro». Sin entrega el
    saldo es cero, porque si no el FIFO le asignaría un pago a mercancía que
    nunca salió del depósito.

    **3. Con la excepción de que si ya entró dinero, cuenta igual.** «Puede pasar
    que se registre primero el pago y luego la entrega». Por eso la guarda mira
    también ``abono_bcv``.

    **4. «No sé» no es «no se entregó».** Sin líneas cargadas no hay dato sobre
    la entrega, y tratar esa ausencia como cero dejaba en cero el saldo de
    cualquier orden cuyas líneas no se hubieran leído todavía -- el FIFO se
    quedaba sin nada que repartir. Lo detectaron los e2e 29 y 46. La guarda solo
    aplica cuando **realmente** se sabe qué se entregó.

    Y una quinta que no es trampa sino decisión: una orden marcada como «no se le
    otorgó el descuento» no ve su saldo reducido por ese descuento (caso TERA, ver
    ``schema.descuentos_no_otorgados``).
    """
    descuentos = 0.0 if descuento_no_otorgado else descuentos_motor
    deudor_bcv = max(0.0, monto_total - abono_bcv)
    deudor_usd = max(0.0, monto_proyectado_usd - abono_binance)
    motivo: str | None = None

    # Trampa 4: el campo crudo, y un vacío cuenta como cero SOLO si hay líneas.
    #
    # Los tres valores de "no sé" son ``None``, ``""`` y el **string** ``"None"``.
    # Con la columna del espejo en ``numeric`` solo el primero es alcanzable hoy;
    # los otros dos son resto de cuando el backend era Sheets y todo venía como
    # texto. Se conservan porque no cuestan nada y el día que vuelva a entrar un
    # texto, la guarda está.
    #
    # Y la conversión tiene que aguantar los mismos tres. En ``app.py`` las dos
    # mitades no coincidían: la guarda excluía ``"None"`` pero el ``float()``
    # corría antes y habría reventado con él. Al no ser alcanzable nunca mordió,
    # pero una defensa cuyas dos mitades se contradicen no es una defensa. Lo
    # encontró un test de esta extracción.
    def _entregada(ln: dict[str, Any]) -> float:
        valor = ln.get("cantidad_entregada")
        if valor is None or valor in ("", "None"):
            return 0.0
        try:
            return float(valor)
        except (TypeError, ValueError):
            return 0.0

    hay_dato = bool(lineas) and any(
        ln.get("cantidad_entregada") not in (None, "", "None") for ln in (lineas or [])
    )
    entregado = sum(_entregada(ln) for ln in (lineas or []))
    if hay_dato and entregado <= 0.005 and abono_bcv <= 0.005:
        deudor_bcv = 0.0
        deudor_usd = 0.0
        motivo = "sin entrega y sin abono: la cuenta por cobrar nace con la entrega"

    # Trampa 1: el IVA vuelve al descuento antes de restarlo. Las NC no.
    descuentos_con_iva = descuentos * (1 + iva)
    return SaldosDeudores(
        deudor_bcv=deudor_bcv,
        deudor_lista_usd=deudor_usd,
        con_descuento_bcv=max(0.0, deudor_bcv - descuentos_con_iva - notas_credito_usd),
        con_descuento_lista_usd=max(0.0, deudor_usd - descuentos_con_iva - notas_credito_usd),
        motivo_del_cero=motivo,
    )


# Descuento de linea a partir del cual la linea es un OBSEQUIO y no una venta.
#
# Regla de negocio que el usuario explico el 11-sep-2026, textual: "la unica
# excepcion es cuando aplica la promocion de primera compra en la que se obsequia
# producto, lo que se hacia era incluir el producto para que se descontara del
# inventario, pero se le ponia al producto precio 0 o dcto del 99% para que NO
# AFECTARA LA CXC".
#
# El corte es 99 y no 100 porque en los datos reales el obsequio se carga con
# 99,99 % (S00671, S00674 y S00679, las tres del producto 1033) y tambien con
# 100 % (S00336). Poner el corte en 100 dejaria afuera justo las tres que importan.
DESCUENTO_DE_OBSEQUIO = 99.0


def _num_linea(valor: Any, por_defecto: float = 0.0) -> float:
    """Un campo de linea como float. Lo ilegible vale el default, no revienta."""
    if valor is None or valor in ("", "None"):
        return por_defecto
    try:
        return float(valor)
    except (TypeError, ValueError):
        return por_defecto


# El alcance del descuento de linea al valuar lo entregado. Es LA DECISION, y la tomo
# el usuario el 11-sep-2026: "solo los obsequios reconocibles".
ALCANCE_NINGUNO = "ninguno"  # el calculo original: el descuento de linea no entra
ALCANCE_OBSEQUIOS = "obsequios"  # solo las lineas con >= 99 % (la forma del regalo)
ALCANCE_TODOS = "todos"  # todos los descuentos de linea, autorizados o no


def valor_entregado_y_retenido(
    lineas: list[dict[str, Any]],
    *,
    aplicar_descuento: bool | None = None,
    alcance: str = ALCANCE_NINGUNO,
) -> float:
    """Cuanta plata de mercancia salio y se quedo con el cliente.

    Es el umbral que decide si una orden genera cuenta por cobrar -- con cero no se
    persigue, porque no hay nada afuera -- y tambien la cifra que el reporte
    publica como ``subtotal``.

    ``alcance`` es LA DECISION. Tres valores:

    * ``ALCANCE_NINGUNO``: el calculo original, el descuento de linea no entra.
    * ``ALCANCE_OBSEQUIOS``: solo las lineas de obsequio (>= 99 % de descuento),
      que son las que el usuario autorizo: "se le ponia al producto precio 0 o dcto
      del 99 % para que NO afectara la cxc". Medido: -107,42 USD.
    * ``ALCANCE_TODOS``: todos los descuentos de linea. Medido: -8.719,06 USD en 204
      ordenes, de los cuales 8.611,64 son descuentos normales sin autorizar.

    **Decidido el 11-sep-2026: obsequios.** ``aplicar_descuento`` queda como alias
    (``True`` = todos, ``False`` = ninguno) para las llamadas y tests anteriores; si
    viene, manda sobre ``alcance``.

    El calculo original hace ``cantidad_entregada x precio_unitario`` y **no mira
    el descuento de linea**. Eso hace que un obsequio --producto a 35,81 con
    99,99 % de descuento, que es como el usuario carga los regalos "para que no
    afectara la cxc"-- cuente 35,81 de venta. El espejo ya trae el ``subtotal``
    correcto en 0,00, lo calcula Odoo, y este calculo lo ignora.

    Pero medido sobre la copia de produccion el efecto es mucho mas grande que los
    obsequios: **204 ordenes cambian y el total baja 8.719,06 USD (-1,10 %)**, de
    los cuales solo 107,42 son obsequios y 8.611,64 son descuentos de linea
    normales. Ninguna orden cruza el umbral, asi que ninguna sale del reporte.

    Los obsequios los autorizo el usuario explicitamente; los otros 8.611,64 no.
    Por eso la bandera existe y esta en False: aplicarla es una decision de el.

    No se usa ``ln["subtotal"]`` directamente porque esa columna corresponde a
    ``cantidad``, no a ``cantidad_entregada``: en una entrega parcial diria de mas.

    Dos comportamientos preservados del cuerpo original: ``cantidad_entregada``
    ausente cae a ``cantidad`` (hay ordenes cuyas lineas no la traen), y el piso en
    cero por linea, porque una entregada NEGATIVA --una devolucion que supera la
    linea, pasa en los datos reales-- no puede restarle valor a las otras lineas.
    """
    if aplicar_descuento is not None:
        alcance = ALCANCE_TODOS if aplicar_descuento else ALCANCE_NINGUNO
    if alcance not in (ALCANCE_NINGUNO, ALCANCE_OBSEQUIOS, ALCANCE_TODOS):
        raise ValueError(f"alcance desconocido: {alcance!r}")
    total = 0.0
    for ln in lineas:
        crudo = ln.get("cantidad_entregada")
        if crudo in (None, "", "None"):
            crudo = ln.get("cantidad", 0)
        cantidad = max(0.0, _num_linea(crudo))
        precio = _num_linea(ln.get("precio_unitario"))
        descuento = _num_linea(ln.get("descuento"))
        entra = alcance == ALCANCE_TODOS or (alcance == ALCANCE_OBSEQUIOS and es_obsequio(ln))
        # Un descuento fuera de 0-100 no sirve para inventar un factor: se ignora.
        if not (entra and 0.0 <= descuento <= 100.0):
            descuento = 0.0
        total += cantidad * precio * (1.0 - descuento / 100.0)
    return total


def diagnostico_de_obsequios(lineas: list[dict[str, Any]]) -> dict[str, Any]:
    """Las dos lecturas del valor entregado, y cuanto de la brecha es obsequio.

    Separa la parte que el usuario autorizo --el obsequio, que por regla de negocio
    NO debe afectar la cuenta por cobrar-- de la que no: los descuentos de linea
    normales, que son 8.611,64 de los 8.719,06 medidos y siguen siendo decision
    suya. Un instrumento que las sumara en un solo numero haria parecer autorizado
    lo que no lo esta.
    """
    sin = valor_entregado_y_retenido(lineas, aplicar_descuento=False)
    con = valor_entregado_y_retenido(lineas, aplicar_descuento=True)
    obsequios = [ln for ln in lineas if es_obsequio(ln)]
    brecha_obsequio = valor_entregado_y_retenido(
        obsequios, aplicar_descuento=False
    ) - valor_entregado_y_retenido(obsequios, aplicar_descuento=True)
    return {
        "sin_descuento": sin,
        "con_descuento": con,
        "brecha": sin - con,
        "brecha_de_obsequios": brecha_obsequio,
        "brecha_de_descuentos_normales": (sin - con) - brecha_obsequio,
        "lineas_de_obsequio": len(obsequios),
        "cruza_el_umbral": sin > 0 and con <= 0,
    }


def es_obsequio(linea: dict[str, Any]) -> bool:
    """True si la linea es un obsequio y no una venta.

    Existe para que el instrumento que cuenta precios en cero pueda separar "cero
    porque se regalo" de "cero porque no se pudo resolver el precio". Las dos se
    ven igual en el monto y son cosas distintas: la primera es correcta y la
    segunda es la mina 1.
    """
    return _num_linea(linea.get("descuento")) >= DESCUENTO_DE_OBSEQUIO
