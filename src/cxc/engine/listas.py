"""Cuál de las listas de precio configuradas es la primaria.

Tercera pieza de la Fase 2.4 del plan de blindaje. Se eligió ésta porque es
donde vive un defecto medido, y porque la decisión que hay que tomar sobre ese
defecto necesita un instrumento para tomarse.

**El contrato, escrito por el propio código.** ``get_valid_pricelists_usd_and_ves``
devuelve a propósito TODAS las listas de una moneda, vigentes o no, y su docstring
dice por qué: la vigencia "solo afecta qué se muestra en Inventario, no qué usa el
motor para resolver precios (ver ``_primer_id_activo``/``OdooPriceResolver``)". O
sea que **elegir bien la primaria es responsabilidad de quien la elige**, y el
mecanismo designado para eso es la guarda.

**El defecto.** De los cuatro sitios de ``web/app.py`` que arman un
``OdooPriceResolver``, tres pasan por esa guarda (líneas 2623, 3573, 3709) y uno
no: ``_get_reporte_saldos_sync`` toma ``ves_ids[0]``/``usd_ids[0]`` crudo. Medido
en la copia de producción con ``scripts/auditar_listas_de_precio.py``: eso es la
lista 3/7 —archivadas, y sin **una sola** regla de precio vigente desde abril de
2026— contra la 10/11, activas y al día. **789 órdenes se valoran distinto según
qué página las mire**: −18,9 % en VES y −16,8 % en USD, con un desvío bruto de
194.532,51 y 115.805,93 respectivamente. El reporte de saldos **subvalúa** el
teórico, así que la subfacturación es justo lo que no se ve.

**Por qué nadie lo notó en cinco meses.** ``_precio_fijo_en_lista``
(``odoo/price.py``) devuelve ``rules[0]`` cuando ninguna regla calza por fecha, así
que nunca devuelve "no hay precio" mientras exista alguna regla -- y la marca
``usa_fallback`` se enciende solo cuando devuelve eso. Una lista con todas las
reglas vencidas entrega precios de abril con la misma cara que una al día. Es la
mina 9 del inventario de la 1.1.

**Este módulo no arregla el defecto**, porque arreglarlo mueve montos en una
pantalla que ya se usa y ésa es una decisión del usuario (regla de la Fase 1). Lo
que hace es sacar la decisión —que es pura: una lista de ids y un conjunto de
activos— de un archivo de diecisiete mil líneas, y agregar
``diagnostico_de_eleccion``, que **no elige: compara las dos elecciones posibles**
y dice si difieren. Ese es el instrumento que la decisión necesita, y no cambia
ningún número.
"""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Any


def primera_activa(ids: list[int], activos: AbstractSet[int]) -> int | None:
    """El primer id de ``ids`` que sigue activo, preservando el orden dado.

    ``None`` si ``ids`` viene vacío. Si NINGUNO está activo, devuelve el primero
    de todos: es el comportamiento histórico de ``_primer_id_activo`` y se
    preserva a propósito -- sin ninguna lista activa no hay respuesta mejor, y
    devolver ``None`` ahí dejaría al llamador sin precio en vez de con un precio
    viejo, que es un cambio de montos y no una corrección.

    Es la mitad **pura** de ``web/app.py::_primer_id_activo``: ese sigue siendo
    quien le pregunta a Odoo cuáles están activas, porque eso es E/S.
    """
    if not ids:
        return None
    for pid in ids:
        if pid in activos:
            return pid
    return ids[0]


def primero_crudo(ids: list[int]) -> int | None:
    """El primer id, sin mirar si está activo.

    Existe para que el diagnóstico pueda comparar contra lo que hace hoy
    ``_get_reporte_saldos_sync``, y para que ese comportamiento tenga un nombre
    en vez de ser un ``[0]`` suelto en medio de mil líneas.
    """
    return ids[0] if ids else None


@dataclass(frozen=True)
class DiagnosticoEleccion:
    """Las dos elecciones posibles para una moneda, y si difieren.

    No decide nada: describe. ``con_guarda`` es lo que eligen tres de los cuatro
    caminos de la aplicación; ``sin_guarda`` es lo que elige el cuarto.
    """

    moneda: str
    ids_configurados: tuple[int, ...]
    activos: tuple[int, ...]
    con_guarda: int | None
    sin_guarda: int | None

    @property
    def coinciden(self) -> bool:
        return self.con_guarda == self.sin_guarda

    @property
    def elegida_esta_archivada(self) -> bool:
        """True si el camino sin guarda eligió una lista que Odoo tiene archivada.

        Es la condición exacta que produjo el hallazgo: no que la lista no exista
        ni que falle, sino que esté archivada y siga entregando precios.
        """
        return self.sin_guarda is not None and self.sin_guarda not in self.activos

    @property
    def nota(self) -> str:
        if not self.ids_configurados:
            return f"{self.moneda}: no hay ninguna lista configurada."
        if self.coinciden:
            return (
                f"{self.moneda}: las dos elecciones dan la lista {self.con_guarda}. "
                "La guarda no cambia nada en esta configuración."
            )
        detalle = " (ARCHIVADA en Odoo)" if self.elegida_esta_archivada else ""
        # Desde el 11-sep-2026 la guarda está aplicada en los cinco sitios, así
        # que esta divergencia YA NO es "una página valora distinto que otra":
        # todas usan ``primera_activa``. Lo que queda es una configuración
        # incómoda -- la primera lista que la configuración ofrece está archivada
        # y la guarda la saltea en silencio.
        #
        # El texto viejo decía "el reporte de saldos valora con la lista 7 y los
        # otros tres caminos con la 11", y al aplicar la guarda se volvió FALSO:
        # la corrida diaria reportaba en ALTA una condición ya resuelta. Un
        # instrumento que grita lobo deja de mirarse, que es justo lo que este
        # plan viene corrigiendo en otros lugares.
        return (
            f"{self.moneda}: la primera lista que la configuración ofrece es la "
            f"{self.sin_guarda}{detalle}, y la guarda la saltea para valorar con la "
            f"{self.con_guarda}. No hay divergencia entre páginas -- las cinco usan "
            "la guarda. Conviene reordenar la configuración para que la primera sea "
            "la que de verdad se usa."
        )


def diagnostico_de_eleccion(
    moneda: str, ids: list[int], activos: Iterable[int]
) -> DiagnosticoEleccion:
    """Compara las dos elecciones posibles sin tomar ninguna.

    ``ids`` en el orden en que los da la configuración -- el orden es el dato,
    porque las dos elecciones se diferencian justamente en cuánto lo respetan.
    """
    conjunto = frozenset(activos)
    return DiagnosticoEleccion(
        moneda=moneda,
        ids_configurados=tuple(ids),
        activos=tuple(sorted(conjunto & set(ids))),
        con_guarda=primera_activa(ids, conjunto),
        sin_guarda=primero_crudo(ids),
    )


# --- los huecos de vigencia, y cuántas listas se pudieron mirar ------------


@dataclass(frozen=True)
class DiagnosticoHuecos:
    """Los tramos sin lista de referencia, **y sobre cuántas listas se buscó**.

    La segunda mitad es el punto. ``huecos_de_cobertura`` saltea toda lista sin
    ``desde`` declarado, así que en una base sin vigencias sembradas devuelve una
    lista vacía -- y una lista vacía se lee como «no hay huecos», cuando lo que
    pasó es que **no se pudo buscar ninguno**.

    Medido en la copia de prueba: las **16 listas del mapeo no tienen ni una
    vigencia declarada**, así que el instrumento que el plan pide usar para
    verificar los períodos devolvía «ninguno» sin haber evaluado nada. Es la misma
    trampa que tenían las dos partidas de tasa del balance, en otro lugar.

    No cambia ningún monto: agrega el denominador que faltaba.
    """

    huecos: tuple[dict[str, str], ...]
    listas_totales: int
    listas_con_vigencia: int
    grupos_evaluados: int

    @property
    def evaluable(self) -> bool:
        """False si no había con qué buscar un hueco."""
        return self.listas_con_vigencia > 0

    @property
    def nota(self) -> str:
        if not self.listas_totales:
            return "No hay ninguna lista en el mapeo."
        if not self.evaluable:
            return (
                f"NO SE PUDO EVALUAR: ninguna de las {self.listas_totales} listas del "
                "mapeo tiene vigencia declarada (campo «desde»), así que no hay tramos "
                "entre los que pueda haber un hueco. Cero huecos acá no significa que "
                "la cobertura esté bien."
            )
        base = (
            f"Evaluadas {self.listas_con_vigencia} de {self.listas_totales} listas "
            f"con vigencia declarada, en {self.grupos_evaluados} grupo(s)."
        )
        if not self.huecos:
            return base + " Sin huecos."
        return base + f" {len(self.huecos)} hueco(s)."


def diagnostico_de_huecos(
    mapeo: dict[str, dict[str, Any]] | None,
    huecos: list[dict[str, str]],
) -> DiagnosticoHuecos:
    """Envuelve el resultado de ``huecos_de_cobertura`` con su denominador.

    Recibe los huecos ya calculados en vez de recalcularlos: la política de qué
    es un hueco vive en un solo lugar, y esto solo la describe.
    """
    total = len(mapeo or {})
    con_vigencia = 0
    grupos: set[tuple[str, str]] = set()
    for info in (mapeo or {}).values():
        if not str(info.get("desde") or ""):
            continue
        moneda = str(info.get("moneda") or "").lower()
        categoria = str(info.get("categoria") or "").lower()
        if moneda not in ("ves", "usd") or not categoria:
            continue
        con_vigencia += 1
        grupos.add((categoria, moneda))
    return DiagnosticoHuecos(
        huecos=tuple(huecos),
        listas_totales=total,
        listas_con_vigencia=con_vigencia,
        grupos_evaluados=len(grupos),
    )


# Los ids que el motor usa cuando la configuración no ofrece ninguna lista. Son
# los nombres lógicos históricos ("USD" -> 4, "BCV" -> 5) y estaban escritos como
# literales en los cuatro sitios que arman el mapa.
POR_DEFECTO_USD = 4
POR_DEFECTO_VES = 5


def mapa_de_listas_primarias(
    ids_usd: list[Any],
    ids_ves: list[Any],
    activos: set[int],
    *,
    por_defecto_usd: int = POR_DEFECTO_USD,
    por_defecto_ves: int = POR_DEFECTO_VES,
) -> dict[str, int]:
    """El ``pricelist_ids_map`` que consume ``OdooPriceResolver``.

    Existe para que los cuatro sitios que lo arman no puedan volver a
    divergir. Era exactamente esa divergencia —tres pasaban por la guarda de
    archivadas y el cuarto tomaba el primer id crudo— la que hacía que 789
    órdenes se valoraran distinto según qué página las mirara.

    Cuando la configuración no ofrece ninguna lista se cae a los ids por
    defecto, que es lo que hacía el código original. **Eso no es un dato**: es
    un nombre lógico de respaldo, y significa que los teóricos calculados así no
    son comparables con los de un entorno configurado.
    """
    primaria_usd = primera_activa([int(x) for x in ids_usd if str(x).isdigit()], activos)
    primaria_ves = primera_activa([int(x) for x in ids_ves if str(x).isdigit()], activos)
    return {
        "USD": primaria_usd if primaria_usd is not None else por_defecto_usd,
        "BCV": primaria_ves if primaria_ves is not None else por_defecto_ves,
    }


@dataclass(frozen=True)
class VigenciaEfectiva:
    """El rango de vigencia de una lista, CON el denominador de donde sale.

    Vigesimosegunda pieza de la Fase 2.4. ``get_config_listas_precio`` calculaba
    ``min(date_start)`` y ``max(date_end)`` sobre las reglas y mostraba eso como el
    rango de la lista, **salteando en silencio las reglas sin fecha**. Una lista con
    99 reglas sin fecha de fin y una que vencio en abril se veia vencida en abril.

    Los tres contadores estan para que el rango no se lea sin su denominador, que es
    la misma disciplina que el resto de este plan: un ``hasta`` que sale de 1 de 100
    reglas no significa lo mismo que uno que sale de 100 de 100.
    """

    desde: str | None
    hasta: str | None
    reglas: int
    con_desde: int
    con_hasta: int

    @property
    def todas_declaran_fin(self) -> bool:
        """Todas las reglas tienen fecha de fin, asi que ``hasta`` es el fin real."""
        return self.reglas > 0 and self.con_hasta == self.reglas

    @property
    def ninguna_declara_fin(self) -> bool:
        """Ninguna vence: el ``hasta`` es ``None`` y las reglas valen para siempre.

        Importa para la mina 9 -- la del precio que sale de una regla vencida. Una
        lista asi NUNCA la dispara, porque sus reglas cubren cualquier fecha
        posterior a su inicio. Y eso hace que un precio viejo se vea al dia.
        """
        return self.reglas > 0 and self.con_hasta == 0

    @property
    def rango_parcial(self) -> bool:
        """El rango sale de una minoria de las reglas, asi que dice menos de lo que parece."""
        return self.reglas > 1 and 0 < self.con_hasta < self.reglas


def vigencia_efectiva(reglas: list[dict[str, Any]]) -> VigenciaEfectiva:
    """El rango que abarcan las fechas de las reglas de una lista.

    ``reglas`` son dicts como los que Odoo devuelve para
    ``product.pricelist.item``: ``date_start`` y ``date_end``, que pueden venir en
    ``False`` cuando la regla no los declara.
    """
    desde = [str(r.get("date_start"))[:10] for r in reglas if r.get("date_start")]
    hasta = [str(r.get("date_end"))[:10] for r in reglas if r.get("date_end")]
    return VigenciaEfectiva(
        desde=min(desde) if desde else None,
        hasta=max(hasta) if hasta else None,
        reglas=len(reglas),
        con_desde=len(desde),
        con_hasta=len(hasta),
    )


@dataclass(frozen=True)
class ReglaDuplicada:
    """Un producto con MAS DE UNA regla de precio en la misma lista."""

    producto_id: int
    producto: str
    precios: tuple[float, ...]
    ids_de_regla: tuple[int, ...]
    # Los rangos de fecha de cada regla, como ``(desde, hasta)`` con ``None`` cuando
    # la regla no los declara.
    rangos: tuple[tuple[str | None, str | None], ...]

    @property
    def precios_distintos(self) -> bool:
        """Dos reglas con el MISMO precio son redundantes; con precios distintos, ambiguas."""
        return len({round(p, 2) for p in self.precios}) > 1

    @property
    def fechas_desempatan(self) -> bool:
        """Los rangos son distintos, asi que la fecha decide cual regla aplica.

        Con rangos distintos no hay ambiguedad de verdad: son precios de epocas
        distintas y eso es lo normal en una lista con historia.
        """
        return len(set(self.rangos)) > 1

    @property
    def ambigua(self) -> bool:
        """Precios distintos y mismo rango: cual se sirve depende del orden interno de Odoo."""
        return self.precios_distintos and not self.fechas_desempatan

    @property
    def tiene_precio_cero(self) -> bool:
        """Alguna de las reglas dice cero.

        Es la que tiene consecuencia: si Odoo elige esa, el producto sale **gratis**.
        Medido el 11-sep-2026 en las listas 18 y 19 (activas) para el tambor de
        SINOCO SAE 50: 0,00 contra 1.139,66 y 1.753,32. Ese cero NO se sirvio nunca
        --0 de 273 lineas de esos productos tienen precio cero, y ninguna orden
        confirmada usa esas dos listas-- asi que es una trampa cargada y no una
        perdida. Basta una orden para que dispare.
        """
        return any(round(p, 2) == 0.0 for p in self.precios)


def reglas_duplicadas(reglas: list[dict[str, Any]]) -> list[ReglaDuplicada]:
    """Los productos que tienen mas de una regla en esta lista.

    Vigesimoquinta pieza de la Fase 2.4, hermana de ``vigencia_efectiva`` y del mismo
    endpoint. ``get_config_listas_precio`` mostraba las reglas en una lista plana, asi
    que dos reglas para el mismo producto se veian como dos filas cualesquiera entre
    ciento cincuenta.

    ``reglas`` son dicts de ``product.pricelist.item`` con ``product_tmpl_id``,
    ``fixed_price``, ``date_start`` y ``date_end``. Solo salen los productos con dos o
    mas; un producto con una sola regla no es noticia.
    """
    por_producto: dict[int, list[dict[str, Any]]] = {}
    for r in reglas:
        crudo = r.get("product_tmpl_id")
        pid = crudo[0] if isinstance(crudo, list | tuple) and crudo else crudo
        if not pid:
            continue
        por_producto.setdefault(int(pid), []).append(r)

    salida = []
    for pid, grupo in sorted(por_producto.items()):
        if len(grupo) < 2:
            continue
        primero = grupo[0].get("product_tmpl_id")
        nombre = (
            str(primero[1])
            if isinstance(primero, list | tuple) and len(primero) > 1
            else str(pid)
        )
        salida.append(
            ReglaDuplicada(
                producto_id=pid,
                producto=nombre,
                precios=tuple(float(r.get("fixed_price") or 0.0) for r in grupo),
                ids_de_regla=tuple(int(r.get("id") or 0) for r in grupo),
                rangos=tuple(
                    (
                        str(r["date_start"])[:10] if r.get("date_start") else None,
                        str(r["date_end"])[:10] if r.get("date_end") else None,
                    )
                    for r in grupo
                ),
            )
        )
    return salida
