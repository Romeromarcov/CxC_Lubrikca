#!/usr/bin/env python
"""Caso por caso: el precio de cada linea contra la lista que le corresponde.

**De donde sale.** Aparecio que 37 de 914 ordenes confirmadas no tienen
``pricelist_id``, y que Odoo las valua en EUR --una moneda *inactiva* en esa base--
porque sin lista cae a otra moneda. La pregunta obvia era si eso costaba plata por
tipo de cambio. El usuario explico que no es eso:

    "se corrigieron los precios manualmente en cada caso, administracion aplico
    descuentos directamente en el precio muchas veces, entonces son un cumulo de
    cosas que sucedieron (...) hay que analizar y evaluar cada caso concreto"

Asi que no hay una cifra global que corregir: hay casos que evaluar. Esto no corrige
nada --Fase 1-- y no propone un monto. Arma la hoja de cada orden con las cuatro
preguntas que el usuario pidio cotejar:

  1. se aplico bien el precio de la lista que regia
  2. si hubo descuento, de cuanto fue y por que via (bajando el precio, o en el campo
     ``discount``, o las dos a la vez)
  3. si ese descuento esta dentro de lo permitido
  4. en que estado esta la orden: facturada, cobrada, o refacturada

**Y el problema resulto ser mucho mas grande que las 37.** Medido el 11-sep-2026:

    ordenes confirmadas                                    914
      sin lista                                             37
      con lista QUE CUBRE su fecha                         800
      con lista que NO cubre su fecha (reasignadas)         77

De las 800 "limpias": 283 lineas por debajo de su lista, 62 por encima, y 248 ordenes
con al menos una linea desviada.

**Las tres poblaciones, y por que cada una necesita otra referencia.** Esta es la
parte que hace que el reporte valga algo:

  - **sin lista**: la referencia es la lista vigente en la fecha de la orden, medida
    por el uso de las demas ordenes de esa epoca.
  - **con lista que cubre su fecha**: su propia lista. Es el caso facil.
  - **con lista que NO cubre su fecha**: son ordenes de marzo apuntando a una lista
    cuyas reglas arrancan en septiembre, o sea que se **reasignaron despues**.
    Compararlas contra su lista actual daria basura --el precio se puso con la lista
    de su epoca-- asi que van con la referencia historica, y el reporte lo dice.

**La referencia historica se MIDE, no se declara.** Se toma la lista mas usada ENTRE
LAS VIGENTES en la fecha. Las dos condiciones hacen falta: sin la vigencia, para
feb-mar 2026 gana la lista 10 ("Pago VES Sept 2026"), cuyas reglas arrancan el 2 de
septiembre --justamente las ordenes reasignadas, leidas como si fueran la lista de la
epoca--; y sin el uso no se desempata entre la 3 y la 7, que cubren las mismas fechas
con precios que difieren en un factor de 2/3 exacto.

**Que NO hace, y por que.** No decide si un descuento estuvo bien otorgado: las seis
tablas de reglas de descuento estan VACIAS en el espejo de QA, asi que el tope contra
el que compara es un parametro (``--tope-descuento``), no una politica leida de la
configuracion. La columna dice "sobre el tope" y no "indebido".

Uso:
    python scripts/auditar_precios_contra_lista.py                  # las 37 sin lista
    python scripts/auditar_precios_contra_lista.py --todas          # las 914
    python scripts/auditar_precios_contra_lista.py --solo-desviadas --csv salida.csv
    python scripts/auditar_precios_contra_lista.py --orden S00015
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import xmlrpc.client
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from cxc.engine.listas import vigencia_efectiva  # noqa: E402

# Cuanto puede diferir un precio de su lista y seguir contando como "coincide". Es
# centavos: los precios de lista se guardan con dos decimales y el price_unit de la
# linea con quince, asi que la comparacion exacta fallaria por redondeo.
TOLERANCIA_PCT = 0.02

# El tope de descuento contra el que se compara, en porciento. NO sale de la
# configuracion --las seis tablas de reglas estan vacias en QA-- asi que es un
# parametro con un default discutible: 4 % es el mayor que nombran las listas de
# precio de Odoo ("Industrial 4% USD/VES"). Cambiarlo con --tope-descuento.
TOPE_DESCUENTO_POR_DEFECTO = 4.0

# Cuantos dias alrededor de la fecha de una orden se miran para saber que lista se
# usaba entonces. Veinte cubre el mes contable sin llegar al periodo siguiente.
VENTANA_DIAS = 20

SIN_LISTA = "sin lista"
LISTA_PROPIA = "lista propia"
REASIGNADA = "lista propia NO cubre la fecha"


def _conectar() -> Any:
    esperadas = ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD")
    faltan = [k for k in esperadas if not os.environ.get(k)]
    if faltan:
        sys.exit(f"Faltan variables de entorno: {faltan}. Corre con `. ./.env.qa` cargado.")
    url, db = os.environ["ODOO_URL"], os.environ["ODOO_DB"]
    usr, key = os.environ["ODOO_USERNAME"], os.environ["ODOO_PASSWORD"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, usr, key, {})
    if not uid:
        sys.exit("Odoo rechazo las credenciales.")
    modelos = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    def ejecutar(modelo: str, metodo: str, args: list, kwargs: dict | None = None) -> Any:
        return modelos.execute_kw(db, uid, key, modelo, metodo, args, kwargs or {})

    return ejecutar


@dataclass
class Linea:
    producto: str
    cantidad: float
    precio_unitario: float
    descuento_declarado: float
    entregado: float
    precio_de_lista: float | None

    @property
    def sin_referencia(self) -> bool:
        """El producto no tiene regla en la lista de referencia: no hay con que comparar."""
        return not self.precio_de_lista

    @property
    def descuento_en_el_precio(self) -> float:
        """Lo que se descontó bajando el precio, en porciento de la lista.

        Es la via que el usuario describio: "administracion aplico descuentos
        directamente en el precio". Negativo significa que se cobro MAS que la lista,
        que tambien hay que mirar -- medido, 62 lineas.
        """
        if self.sin_referencia:
            return 0.0
        assert self.precio_de_lista
        return (1 - self.precio_unitario / self.precio_de_lista) * 100

    @property
    def descuento_total(self) -> float:
        """Las dos vias juntas: el precio bajado Y el campo ``discount``.

        Importa que sea una sola cifra porque las dos vias se **acumulan**: un precio
        20 % abajo con un discount de 10 % deja al cliente pagando 72 % de la lista,
        no 80 % ni 70 %.
        """
        if self.sin_referencia:
            return 0.0
        assert self.precio_de_lista
        efectivo = self.precio_unitario * (1 - self.descuento_declarado / 100)
        return (1 - efectivo / self.precio_de_lista) * 100

    @property
    def usa_las_dos_vias(self) -> bool:
        """Precio bajado Y campo discount a la vez. Es lo que se puede contar doble."""
        return (
            not self.sin_referencia
            and abs(self.descuento_en_el_precio) > 0.1
            and self.descuento_declarado > 0
        )

    @property
    def desviada(self) -> bool:
        return not self.sin_referencia and abs(self.descuento_total) >= TOLERANCIA_PCT

    def diferencia_en_plata(self) -> float:
        """Cuanto menos se cobro que la lista, sobre la cantidad de la linea.

        Positivo = se cobro de menos; negativo = se cobro de mas. Se devuelve con
        signo a proposito: sumar los valores absolutos inflaria el total juntando
        dos cosas que se compensan.
        """
        if self.sin_referencia:
            return 0.0
        assert self.precio_de_lista
        efectivo = self.precio_unitario * (1 - self.descuento_declarado / 100)
        return (self.precio_de_lista - efectivo) * self.cantidad

    def veredicto(self, tope: float) -> str:
        if self.sin_referencia:
            return "SIN REFERENCIA"
        total = self.descuento_total
        if abs(total) < TOLERANCIA_PCT:
            return "coincide con la lista"
        if total < 0:
            return f"COBRADO POR ENCIMA de la lista ({-total:.1f} %)"
        if total > tope:
            return f"descuento {total:.1f} % SOBRE EL TOPE de {tope:.0f} %"
        return f"descuento {total:.1f} %, dentro del tope"


@dataclass
class Caso:
    nombre: str
    fecha: str
    cliente: str
    total_orden: float
    moneda: str
    lista_propia: str
    lista_referencia: int
    motivo: str
    lineas: list[Linea] = field(default_factory=list)
    facturas: list[dict] = field(default_factory=list)

    @property
    def refacturada(self) -> bool:
        """Tiene una nota de credito: se anulo y se volvio a facturar.

        Es la segunda mitad de lo que el usuario describio -- "otras se refacturaron
        llevandolas a la lista vigente inmediatamente posterior a ese periodo".
        """
        return any(f["move_type"] == "out_refund" for f in self.facturas)

    @property
    def cobrada(self) -> bool:
        pagadas = {"paid", "in_payment", "reversed"}
        facturas = [f for f in self.facturas if f["move_type"] == "out_invoice"]
        return bool(facturas) and all(f["payment_state"] in pagadas for f in facturas)

    @property
    def diferencia_total(self) -> float:
        return sum(ln.diferencia_en_plata() for ln in self.lineas)

    @property
    def tiene_desviadas(self) -> bool:
        return any(ln.desviada for ln in self.lineas)

    @property
    def razon_constante(self) -> bool:
        """Todas las lineas con la MISMA razon precio/lista: compatible con conversion.

        Una conversion de moneda da la misma razon en todas las lineas de una orden.
        Si difieren, el precio se toco a mano. Medido en S00015: 0,683 / 0,683 / 0,773
        / 0,729 -- no fue conversion.
        """
        rs = [
            ln.precio_unitario / ln.precio_de_lista
            for ln in self.lineas
            if ln.precio_de_lista
        ]
        return len(rs) > 1 and (max(rs) - min(rs)) < 0.001


def _precios_y_vigencias(ejecutar: Any) -> tuple[dict[tuple[int, int], float], dict[int, Any]]:
    """``(lista, producto) -> precio`` y ``lista -> VigenciaEfectiva``.

    Con reglas repetidas para el mismo producto gana **la de id mas alto**, que es el
    orden con que Odoo las recorre. Hay seis pares asi en las listas activas, dos de
    ellos con una regla en 0,00 -- ver ``engine/listas.reglas_duplicadas``, que los
    reporta. Aca se elige una para poder comparar, y la eleccion queda escrita.
    """
    items = ejecutar(
        "product.pricelist.item",
        "search_read",
        [[]],
        {
            "fields": ["id", "pricelist_id", "fixed_price", "date_start", "date_end",
                       "product_tmpl_id"],
            "context": {"active_test": False},
        },
    )
    por_lista: dict[int, list[dict]] = defaultdict(list)
    mejor: dict[tuple[int, int], tuple[int, float]] = {}
    for i in items:
        pid = i["pricelist_id"][0]
        por_lista[pid].append(i)
        crudo = i.get("product_tmpl_id")
        tmpl = crudo[0] if isinstance(crudo, list | tuple) and crudo else 0
        if not tmpl:
            continue
        clave = (pid, int(tmpl))
        if mejor.get(clave, (-1, 0.0))[0] < int(i["id"]):
            mejor[clave] = (int(i["id"]), float(i["fixed_price"] or 0.0))
    precios = {k: v[1] for k, v in mejor.items()}
    vigs = {pid: vigencia_efectiva(rs) for pid, rs in por_lista.items()}
    return precios, vigs


def _cubre(vig: Any, fecha: str) -> bool:
    if vig is None or vig.reglas == 0:
        return False
    if vig.desde and fecha < vig.desde:
        return False
    return not (vig.hasta and fecha > vig.hasta)


def _dias(a: str, b: str) -> int:
    from datetime import date

    ya, yb = (date(int(x[:4]), int(x[5:7]), int(x[8:10])) for x in (a, b))
    return abs((ya - yb).days)


def _referencia_historica(
    fecha: str, vigs: dict[int, Any], uso_por_fecha: list[tuple[str, int]]
) -> tuple[int | None, str]:
    """La lista que regia en ``fecha``: la mas usada ENTRE LAS VIGENTES, cerca de ahi.

    ``uso_por_fecha`` son pares ``(fecha, lista)`` de las ordenes que SI tienen una
    lista que cubre su propia fecha -- o sea, las que no estan reasignadas. Usar las
    reasignadas aca meteria justo el sesgo que esta funcion existe para evitar.
    """
    vigentes = {pid for pid, v in vigs.items() if _cubre(v, fecha)}
    if not vigentes:
        return None, f"ninguna lista tiene reglas vigentes en {fecha}"
    cerca = Counter(
        pid
        for f, pid in uso_por_fecha
        if pid in vigentes and _dias(f, fecha) <= VENTANA_DIAS
    )
    if cerca:
        elegida, n = cerca.most_common(1)[0]
        return elegida, (
            f"lista {elegida}, la mas usada entre las vigentes en {fecha} "
            f"(+/-{VENTANA_DIAS} dias): {n} de {sum(cerca.values())} ordenes"
        )
    elegida = max(vigentes, key=lambda pid: vigs[pid].reglas)
    return elegida, (
        f"lista {elegida}, elegida por VIGENCIA y no por uso ({vigs[elegida].reglas} "
        f"reglas): ninguna lista vigente en {fecha} se usa en ordenes de esa epoca"
    )


def recolectar(
    ejecutar: Any, *, todas: bool = False, solo: str | None = None
) -> tuple[list[Caso], list[str]]:
    """Las hojas de cada caso, y las notas de como se eligio cada referencia."""
    # El filtro por ``pricelist_id`` va en Python y NO en el dominio de Odoo: probado
    # el 11-sep-2026, ``["pricelist_id", "=", False]`` devuelve cero aunque el campo se
    # LEA como False en las mismas ordenes. Un dominio que devuelve cero en silencio es
    # peor que uno que falla, asi que se trae todo y se filtra aca.
    ordenes = ejecutar(
        "sale.order",
        "search_read",
        [[["state", "in", ["sale", "done"]]]],
        {
            "fields": ["name", "date_order", "partner_id", "amount_total", "currency_id",
                       "invoice_ids", "pricelist_id"],
            "order": "date_order asc",
        },
    )
    precios, vigs = _precios_y_vigencias(ejecutar)

    # El "uso limpio": ordenes cuya lista SI cubre su fecha. Es la base con que se
    # decide que lista regia cada epoca.
    uso_limpio = []
    for o in ordenes:
        if not o.get("pricelist_id"):
            continue
        f = str(o["date_order"])[:10]
        if _cubre(vigs.get(o["pricelist_id"][0]), f):
            uso_limpio.append((f, o["pricelist_id"][0]))

    elegidas = []
    for o in ordenes:
        fecha = str(o["date_order"])[:10]
        pid = o["pricelist_id"][0] if o.get("pricelist_id") else None
        if pid is not None and _cubre(vigs.get(pid), fecha):
            ref, motivo = pid, LISTA_PROPIA
        else:
            ref, nota = _referencia_historica(fecha, vigs, uso_limpio)
            motivo = f"{SIN_LISTA if pid is None else REASIGNADA} -- {nota}"
        if ref is None:
            continue
        if not todas and pid is not None:
            continue
        if solo and o["name"] != solo:
            continue
        elegidas.append((o, ref, motivo))

    if not elegidas:
        return [], []

    nombres = [o["name"] for o, _r, _m in elegidas]
    lineas = ejecutar(
        "sale.order.line",
        "search_read",
        [[["order_id.name", "in", nombres]]],
        {
            "fields": ["order_id", "product_template_id", "product_uom_qty", "price_unit",
                       "discount", "qty_delivered"],
        },
    )
    por_orden: dict[str, list] = defaultdict(list)
    for ln in lineas:
        por_orden[ln["order_id"][1]].append(ln)

    inv_ids = [i for o, _r, _m in elegidas for i in (o["invoice_ids"] or [])]
    facturas = {}
    for trozo in [inv_ids[i : i + 500] for i in range(0, len(inv_ids), 500)]:
        for f in ejecutar(
            "account.move",
            "search_read",
            [[["id", "in", trozo]]],
            {
                "fields": ["name", "move_type", "state", "payment_state", "currency_id",
                           "amount_total", "invoice_date"],
            },
        ):
            facturas[f["id"]] = f

    casos = []
    for o, ref, motivo in elegidas:
        caso = Caso(
            nombre=o["name"],
            fecha=str(o["date_order"])[:10],
            cliente=(o["partner_id"] or [0, "?"])[1],
            total_orden=float(o["amount_total"] or 0),
            moneda=(o["currency_id"] or [0, "?"])[1],
            lista_propia=(o["pricelist_id"][1] if o.get("pricelist_id") else "(ninguna)"),
            lista_referencia=ref,
            motivo=motivo,
            facturas=[facturas[i] for i in (o["invoice_ids"] or []) if i in facturas],
        )
        for ln in por_orden.get(o["name"], []):
            crudo = ln["product_template_id"] or [0, "?"]
            caso.lineas.append(
                Linea(
                    producto=str(crudo[1]),
                    cantidad=float(ln["product_uom_qty"] or 0),
                    precio_unitario=float(ln["price_unit"] or 0),
                    descuento_declarado=float(ln["discount"] or 0),
                    entregado=float(ln["qty_delivered"] or 0),
                    precio_de_lista=precios.get((ref, int(crudo[0] or 0))),
                )
            )
        casos.append(caso)
    return casos, sorted({m for _o, _r, m in elegidas if m != LISTA_PROPIA})


def imprimir(casos: list[Caso], tope: float, solo_desviadas: bool) -> None:
    print("=" * 100)
    print("PRECIO DE CADA LINEA CONTRA LA LISTA QUE LE CORRESPONDE")
    print("=" * 100)
    print(
        f"\nTope de descuento: {tope:.0f} % -- es un PARAMETRO, no una politica leida de "
        "la configuracion\n(las seis tablas de reglas de descuento estan vacias en el "
        "espejo de QA). La columna dice\n'sobre el tope', no 'indebido'."
    )

    mostrados = [c for c in casos if c.tiene_desviadas] if solo_desviadas else casos
    if solo_desviadas:
        print(
            f"\n--solo-desviadas: se muestran {len(mostrados)} de {len(casos)} ordenes, "
            "las que tienen al menos una linea que no coincide con su lista."
        )

    for c in mostrados:
        print("\n" + "-" * 100)
        estado = []
        if c.refacturada:
            estado.append("REFACTURADA (tiene N/C)")
        if c.cobrada:
            estado.append("cobrada")
        print(
            f"{c.nombre}  {c.fecha}  {c.cliente[:38]}  total {c.total_orden:,.2f} {c.moneda}"
            + (f"  [{', '.join(estado)}]" if estado else "")
        )
        print(f"    lista de la orden: {c.lista_propia}")
        print(f"    referencia usada:  {c.motivo}")
        if c.razon_constante:
            print("    razon precio/lista CONSTANTE: compatible con una conversion de moneda")
        print(
            f"    {'producto':<42} {'cant':>6} {'precio':>9} {'lista':>9} "
            f"{'dcto%':>6} {'dif USD':>9}  veredicto"
        )
        for ln in c.lineas:
            print(
                f"    {ln.producto[:42]:<42} {ln.cantidad:>6.1f} {ln.precio_unitario:>9.2f} "
                f"{(ln.precio_de_lista or 0):>9.2f} {ln.descuento_declarado:>6.1f} "
                f"{ln.diferencia_en_plata():>9.2f}  {ln.veredicto(tope)}"
            )
            if ln.usa_las_dos_vias:
                print(
                    f"        ^ LAS DOS VIAS: {ln.descuento_en_el_precio:.1f} % metido en el "
                    f"precio MAS {ln.descuento_declarado:.1f} % en el campo, "
                    f"que acumulan {ln.descuento_total:.1f} %"
                )
            if ln.entregado == 0 and ln.cantidad > 0:
                print("        ^ cantidad entregada CERO: puede ser una devolucion")
        print(f"    diferencia contra la lista: {c.diferencia_total:>12,.2f} USD")

    _resumen(casos, tope)


def _resumen(casos: list[Caso], tope: float) -> None:
    print("\n" + "=" * 100)
    print("RESUMEN")
    print("=" * 100)
    por_motivo = Counter(c.motivo.split(" -- ")[0] for c in casos)
    print(f"ordenes: {len(casos)}")
    for motivo, n in por_motivo.most_common():
        print(f"   {motivo:<34} {n:>5}")

    todas_lineas = [ln for c in casos for ln in c.lineas]
    sin_ref = [ln for ln in todas_lineas if ln.sin_referencia]
    con_ref = [ln for ln in todas_lineas if not ln.sin_referencia]
    print(f"\nlineas: {len(todas_lineas)}   con referencia: {len(con_ref)}   sin: {len(sin_ref)}")
    if sin_ref:
        print(
            f"   -- las {len(sin_ref)} sin referencia NO entran en ninguna cifra de abajo. "
            "Ese es el denominador."
        )
    debajo = [ln for ln in con_ref if ln.descuento_total >= TOLERANCIA_PCT]
    encima = [ln for ln in con_ref if ln.descuento_total <= -TOLERANCIA_PCT]
    print(f"   coinciden con la lista:        {len(con_ref) - len(debajo) - len(encima):>5}")
    print(f"   POR DEBAJO de la lista:        {len(debajo):>5}")
    print(f"   POR ENCIMA de la lista:        {len(encima):>5}")
    print(f"   sobre el tope de {tope:.0f} %:           "
          f"{sum(1 for ln in debajo if ln.descuento_total > tope):>5}")
    print(f"   con descuento por LAS DOS vias:{sum(1 for ln in con_ref if ln.usa_las_dos_vias):>5}")

    con_desvio = sum(1 for c in casos if c.tiene_desviadas)
    print(f"{chr(10)}ordenes con al menos una linea desviada: {con_desvio}")
    print(f"refacturadas (con N/C): {sum(1 for c in casos if c.refacturada)}")
    print(f"cobradas: {sum(1 for c in casos if c.cobrada)}")

    cobrado_de_menos = sum(ln.diferencia_en_plata() for ln in debajo)
    cobrado_de_mas = sum(ln.diferencia_en_plata() for ln in encima)
    print(f"\ncobrado de MENOS que la lista: {cobrado_de_menos:>14,.2f} USD")
    print(f"cobrado de MAS que la lista:   {cobrado_de_mas:>14,.2f} USD")
    print(f"neto:                          {cobrado_de_menos + cobrado_de_mas:>14,.2f} USD")
    print(
        "\n   El neto NO es un monto a cobrar, y las dos mitades van separadas justamente\n"
        "   para que no se lea asi: parte sera descuento bien otorgado, parte son ordenes\n"
        "   ya refacturadas, y lo cobrado de mas compensa en el neto cosas que no tienen\n"
        "   nada que ver entre si. Es el tamano de lo que hay que evaluar caso por caso."
    )


def escribir_csv(casos: list[Caso], ruta: Path, tope: float) -> None:
    with ruta.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["orden", "fecha", "cliente", "lista_de_la_orden", "lista_referencia", "motivo",
             "refacturada", "cobrada", "producto", "cantidad", "entregado", "precio_unitario",
             "precio_de_lista", "descuento_en_el_precio_pct", "descuento_declarado_pct",
             "descuento_total_pct", "diferencia_usd", "veredicto"]
        )
        for c in casos:
            for ln in c.lineas:
                w.writerow([
                    c.nombre, c.fecha, c.cliente, c.lista_propia, c.lista_referencia,
                    c.motivo.split(" -- ")[0], c.refacturada, c.cobrada, ln.producto,
                    ln.cantidad, ln.entregado, round(ln.precio_unitario, 4),
                    round(ln.precio_de_lista or 0, 2), round(ln.descuento_en_el_precio, 2),
                    ln.descuento_declarado, round(ln.descuento_total, 2),
                    round(ln.diferencia_en_plata(), 2), ln.veredicto(tope),
                ])
    print(f"\nCSV escrito: {ruta}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--todas", action="store_true", help="incluir tambien las ordenes CON lista")
    ap.add_argument("--orden", help="una sola orden, por nombre (ej. S00015)")
    ap.add_argument("--solo-desviadas", action="store_true", help="omitir las que coinciden")
    ap.add_argument(
        "--tope-descuento", type=float, default=TOPE_DESCUENTO_POR_DEFECTO,
        help="porcentaje sobre el cual un descuento se marca; es un parametro, no una politica",
    )
    ap.add_argument("--csv", help="ademas, escribir el detalle por linea a este archivo")
    args = ap.parse_args()

    ejecutar = _conectar()
    casos, notas = recolectar(ejecutar, todas=args.todas, solo=args.orden)
    if not casos:
        print(
            "Sin casos. Si esperabas ordenes con lista, agrega --todas; si pediste una "
            "orden por nombre, revisa que exista y este confirmada."
        )
        return 0

    imprimir(casos, args.tope_descuento, args.solo_desviadas)
    if notas:
        print(f"\nreferencias historicas usadas ({len(notas)} distintas):")
        for n in notas[:12]:
            print(f"   {n}")
    if args.csv:
        escribir_csv(casos, Path(args.csv), args.tope_descuento)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
