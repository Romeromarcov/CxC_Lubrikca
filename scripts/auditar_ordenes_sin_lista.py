#!/usr/bin/env python
"""Caso por caso: las ordenes SIN lista de precio, contra la lista historica.

**Por que existe.** El 11-sep-2026 aparecio que 37 de 914 ordenes confirmadas no
tienen ``pricelist_id``, y que Odoo las valua en EUR --una moneda *inactiva* en esa
base-- porque sin lista cae a otra moneda. La pregunta obvia era si eso costaba plata
por tipo de cambio. El usuario explico que no es eso:

    "se corrigieron los precios manualmente en cada caso, administracion aplico
    descuentos directamente en el precio muchas veces, entonces son un cumulo de
    cosas que sucedieron (...) hay que analizar y evaluar cada caso concreto"

Asi que no hay una cifra global que corregir: hay casos que evaluar. Esto no corrige
nada --Fase 1-- y no propone un monto. Arma la hoja de cada orden para que se pueda
mirar, con las cuatro preguntas que el usuario pidio cotejar:

  1. se aplico bien el precio de la lista historica
  2. si hubo descuento, de cuanto fue y por que via (en el precio o en el campo)
  3. si ese descuento esta dentro de lo permitido
  4. en que estado esta la orden: facturada, pagada, o refacturada a la lista
     siguiente

**La referencia, y como se verifica en vez de suponerse.** La lista que rige un
periodo no se declara aca: el script mide que lista usaron las ordenes del MISMO
periodo que si tienen ``pricelist_id``, y la usa como referencia. Medido para
feb-mar 2026: las 25 ordenes con lista usan la lista 3 ("USD") y sus precios
**textuales** (93,10 / 80,17 / 63,36 para tres productos verificados), asi que la
referencia queda calibrada por el dato y no por una constante en el codigo.

**Que NO hace, y por que.** No decide si un descuento estuvo bien otorgado: las seis
tablas de reglas de descuento estan VACIAS en el espejo de QA, asi que el tope contra
el que compara es un parametro (``--tope-descuento``), no una politica leida de la
configuracion. La columna dice "sobre el tope" y no "indebido".

Uso:
    python scripts/auditar_ordenes_sin_lista.py
    python scripts/auditar_ordenes_sin_lista.py --tope-descuento 3 --orden S00015
    python scripts/auditar_ordenes_sin_lista.py --csv salida.csv
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
TOLERANCIA_CENTAVOS = 0.02

# El tope de descuento contra el que se compara, en porciento. NO sale de la
# configuracion --las seis tablas de reglas estan vacias en QA-- asi que es un
# parametro con un default discutible: 4 % es el mayor que nombran las listas de
# precio de Odoo ("Industrial 4% USD/VES"). Cambiarlo con --tope-descuento.
TOPE_DESCUENTO_POR_DEFECTO = 4.0


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
    template: int
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
        que tambien hay que mirar.
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

    def diferencia_en_plata(self) -> float:
        """Cuanto menos se cobro que la lista, sobre la cantidad de la linea.

        En las mismas unidades que la lista (dolares), porque el price_unit de estas
        ordenes vive en el MISMO espacio numerico que la lista -- ver el docstring del
        modulo: las ordenes del periodo con lista usan los precios textuales.
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
        if abs(total) < TOLERANCIA_CENTAVOS:
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
    def razones(self) -> list[float]:
        """``price_unit / precio_de_lista`` por linea, que es la prueba de la conversion.

        Una conversion de moneda da la MISMA razon en todas las lineas de una orden.
        Si difieren, el precio se toco a mano. Medido en S00015: 0,683 / 0,683 / 0,773
        / 0,729 -- no fue conversion.
        """
        return [
            ln.precio_unitario / ln.precio_de_lista
            for ln in self.lineas
            if ln.precio_de_lista
        ]

    @property
    def razon_constante(self) -> bool:
        rs = self.razones
        return len(rs) > 1 and (max(rs) - min(rs)) < 0.001


def _vigencias(ejecutar: Any) -> dict[int, Any]:
    """``lista_id`` -> ``VigenciaEfectiva``, o sea el rango que cubren sus reglas.

    Usa la pieza 22 del motor (``engine.listas.vigencia_efectiva``), que ademas del
    rango trae el denominador: cuantas de las reglas declaran fecha. Importa porque
    una lista cuyas reglas no declaran fin cubre **cualquier** fecha posterior a su
    inicio, y eso la vuelve candidata para un periodo viejo sin que nadie lo decidiera.
    """
    listas = ejecutar(
        "product.pricelist",
        "search_read",
        [[["active", "in", [True, False]]]],
        {"fields": ["id", "name"], "context": {"active_test": False}, "order": "id asc"},
    )
    items = ejecutar(
        "product.pricelist.item",
        "search_read",
        [[["pricelist_id", "in", [x["id"] for x in listas]]]],
        {"fields": ["pricelist_id", "date_start", "date_end"], "context": {"active_test": False}},
    )
    por_lista: dict[int, list[dict]] = defaultdict(list)
    for i in items:
        pid = i["pricelist_id"]
        por_lista[pid[0] if isinstance(pid, list | tuple) else pid].append(i)
    return {x["id"]: vigencia_efectiva(por_lista.get(x["id"], [])) for x in listas}


def _cubre(vig: Any, fecha: str) -> bool:
    """La lista tiene reglas vigentes en esa fecha."""
    if vig.reglas == 0:
        return False
    if vig.desde and fecha < vig.desde:
        return False
    return not (vig.hasta and fecha > vig.hasta)


def _lista_de_referencia(
    ejecutar: Any, desde: str, hasta: str
) -> tuple[int | None, Counter, str]:
    """La lista que rige ese periodo: la mas usada **entre las que cubren la fecha**.

    **Las dos condiciones, y por que hacen falta las dos.** La primera version se
    quedaba con la mas usada, sin mirar vigencia, y para feb-mar 2026 eligio la lista
    10 ("Pago VES Sept 2026"), cuyas reglas arrancan el 2 de septiembre: no puede regir
    marzo. Esas ordenes de marzo quedaron apuntando a la lista 10 porque se
    reasignaron despues, y "la mas usada" leia esa reasignacion como si fuera la lista
    de la epoca.

    Al reves tampoco alcanza: varias listas cubren un mismo dia (la 3 y la 7 cubren
    feb-mar, con precios que difieren en un factor de 2/3 exacto), asi que la vigencia
    sola no desempata. El uso desempata entre las vigentes.

    Devuelve el id, el conteo completo --para que se vea el denominador de la eleccion
    y no solo el ganador-- y una nota que dice por que via se eligio.
    """
    ordenes = ejecutar(
        "sale.order",
        "search_read",
        [
            [
                ["state", "in", ["sale", "done"]],
                ["date_order", ">=", desde],
                ["date_order", "<=", hasta + " 23:59:59"],
            ]
        ],
        {"fields": ["pricelist_id"]},
    )
    # Mismo motivo que arriba: el "tiene lista" se decide leyendo, no con el dominio.
    conteo = Counter(o["pricelist_id"][0] for o in ordenes if o.get("pricelist_id"))
    vigs = _vigencias(ejecutar)
    vigentes = {pid for pid, v in vigs.items() if _cubre(v, desde) and _cubre(v, hasta)}

    usadas_y_vigentes = [(n, pid) for pid, n in conteo.items() if pid in vigentes]
    if usadas_y_vigentes:
        elegida = max(usadas_y_vigentes)[1]
        descartadas = sorted(set(conteo) - vigentes)
        nota = (
            f"lista {elegida}: la mas usada ENTRE LAS VIGENTES en {desde}..{hasta} "
            f"({conteo[elegida]} de {sum(conteo.values())} ordenes del periodo que "
            f"tienen lista)."
        )
        if descartadas:
            nota += (
                f"\n   Descartadas por vigencia aunque se usen en el periodo: "
                f"{descartadas} -- sus reglas no cubren esas fechas, asi que esas "
                f"ordenes fueron reasignadas despues."
            )
        return elegida, conteo, nota

    if vigentes:
        # Ninguna lista vigente se usa en el periodo: se elige la vigente con mas
        # reglas, y se dice que la eleccion salio de la vigencia y no del uso.
        elegida = max(vigentes, key=lambda pid: vigs[pid].reglas)
        nota = (
            f"lista {elegida}: NINGUNA lista vigente en {desde}..{hasta} se usa en "
            f"ordenes de ese periodo, asi que la referencia sale de la VIGENCIA "
            f"({vigs[elegida].reglas} reglas) y no del uso. Leer el reporte con eso en "
            f"cuenta."
        )
        return elegida, conteo, nota

    return None, conteo, f"ninguna lista tiene reglas vigentes en {desde}..{hasta}"


def _precios_de_lista(ejecutar: Any, lista_id: int, templates: list[int]) -> dict[int, float]:
    if not templates:
        return {}
    items = ejecutar(
        "product.pricelist.item",
        "search_read",
        [[["pricelist_id", "=", lista_id], ["product_tmpl_id", "in", templates]]],
        {
            "fields": ["product_tmpl_id", "fixed_price", "compute_price"],
            "context": {"active_test": False},
        },
    )
    salida = {}
    for i in items:
        # Solo las reglas de precio FIJO sirven como referencia directa. Una regla de
        # porcentaje o de formula necesita el precio base, que es otra pregunta.
        if i.get("compute_price") == "fixed" and i.get("product_tmpl_id"):
            salida[i["product_tmpl_id"][0]] = float(i["fixed_price"] or 0)
    return salida


def recolectar(
    ejecutar: Any, solo: str | None = None
) -> tuple[list[Caso], int | None, Counter, str]:
    # El filtro por ``pricelist_id`` va en Python y NO en el dominio de Odoo: probado
    # el 11-sep-2026, ``["pricelist_id", "=", False]`` devuelve cero aunque el campo se
    # LEA como False en las mismas ordenes. Un dominio que devuelve cero en silencio es
    # peor que uno que falla, asi que se trae todo y se filtra aca.
    todas = ejecutar(
        "sale.order",
        "search_read",
        [[["state", "in", ["sale", "done"]]]],
        {
            "fields": ["name", "date_order", "partner_id", "amount_total", "currency_id",
                       "invoice_ids", "pricelist_id"],
            "order": "date_order asc",
        },
    )
    ordenes = [o for o in todas if not o.get("pricelist_id")]
    # El periodo se calcula sobre TODAS las ordenes sin lista, no sobre la unica que
    # se pidio con --orden: un solo dia no alcanza para calibrar la referencia.
    fechas_todas = sorted(str(o["date_order"])[:10] for o in ordenes)
    if solo:
        ordenes = [o for o in ordenes if o["name"] == solo]
    if not ordenes:
        return [], None, Counter(), "no hay ordenes confirmadas sin lista de precio"

    lista_ref, conteo, nota = _lista_de_referencia(
        ejecutar, fechas_todas[0], fechas_todas[-1]
    )
    if lista_ref is None:
        return [], None, conteo, nota

    nombres = [o["name"] for o in ordenes]
    lineas = ejecutar(
        "sale.order.line",
        "search_read",
        [[["order_id.name", "in", nombres]]],
        {
            "fields": ["order_id", "product_template_id", "product_uom_qty", "price_unit",
                       "discount", "qty_delivered"],
        },
    )
    templates = sorted({ln["product_template_id"][0] for ln in lineas if ln["product_template_id"]})
    precios = _precios_de_lista(ejecutar, lista_ref, templates)

    inv_ids = [i for o in ordenes for i in (o["invoice_ids"] or [])]
    facturas = {}
    if inv_ids:
        for f in ejecutar(
            "account.move",
            "search_read",
            [[["id", "in", inv_ids]]],
            {
                "fields": ["name", "move_type", "state", "payment_state", "currency_id",
                           "amount_total", "invoice_date"],
            },
        ):
            facturas[f["id"]] = f

    por_orden: dict[str, list] = defaultdict(list)
    for ln in lineas:
        por_orden[ln["order_id"][1]].append(ln)

    casos = []
    for o in ordenes:
        caso = Caso(
            nombre=o["name"],
            fecha=str(o["date_order"])[:10],
            cliente=(o["partner_id"] or [0, "?"])[1],
            total_orden=float(o["amount_total"] or 0),
            moneda=(o["currency_id"] or [0, "?"])[1],
            facturas=[facturas[i] for i in (o["invoice_ids"] or []) if i in facturas],
        )
        for ln in por_orden.get(o["name"], []):
            tmpl = (ln["product_template_id"] or [0, "?"])
            caso.lineas.append(
                Linea(
                    producto=str(tmpl[1]),
                    template=int(tmpl[0] or 0),
                    cantidad=float(ln["product_uom_qty"] or 0),
                    precio_unitario=float(ln["price_unit"] or 0),
                    descuento_declarado=float(ln["discount"] or 0),
                    entregado=float(ln["qty_delivered"] or 0),
                    precio_de_lista=precios.get(int(tmpl[0] or 0)),
                )
            )
        casos.append(caso)
    return casos, lista_ref, conteo, nota


def imprimir(casos: list[Caso], nota: str, tope: float) -> None:
    print("=" * 96)
    print("ORDENES SIN LISTA DE PRECIO -- una hoja por caso")
    print("=" * 96)
    print(f"\nReferencia MEDIDA, no supuesta -- {nota}")
    print(
        f"Tope de descuento: {tope:.0f} % -- es un PARAMETRO, no una politica leida de "
        "la configuracion\n(las seis tablas de reglas estan vacias en el espejo de QA)."
    )

    for c in casos:
        print("\n" + "-" * 96)
        estado = []
        if c.refacturada:
            estado.append("REFACTURADA (tiene nota de credito)")
        if c.cobrada:
            estado.append("cobrada")
        print(
            f"{c.nombre}  {c.fecha}  {c.cliente[:40]}  total {c.total_orden:,.2f} {c.moneda}"
            + (f"  [{', '.join(estado)}]" if estado else "")
        )
        for f in c.facturas:
            tipo = "N/C" if f["move_type"] == "out_refund" else "FAC"
            print(
                f"    {tipo} {f['name']:<14} {f['invoice_date']}  "
                f"{f['amount_total']:>14,.2f} {(f['currency_id'] or [0,'?'])[1]}  "
                f"{f['state']}/{f['payment_state']}"
            )
        if not c.razones:
            print("    (ninguna linea tiene precio de referencia en la lista)")
        elif c.razon_constante:
            print(
                f"    razon price_unit/lista CONSTANTE ({c.razones[0]:.5f}): compatible con "
                "una conversion de moneda, no con edicion a mano"
            )
        print(
            f"    {'producto':<40} {'cant':>6} {'precio':>9} {'lista':>9} "
            f"{'dcto%':>6} {'dif USD':>9}  veredicto"
        )
        for ln in c.lineas:
            print(
                f"    {ln.producto[:40]:<40} {ln.cantidad:>6.1f} {ln.precio_unitario:>9.2f} "
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

    print("\n" + "=" * 96)
    print("RESUMEN")
    print("=" * 96)
    sin_ref = sum(1 for c in casos for ln in c.lineas if ln.sin_referencia)
    con_ref = sum(1 for c in casos for ln in c.lineas if not ln.sin_referencia)
    print(f"ordenes: {len(casos)}")
    print(f"lineas con referencia en la lista: {con_ref}   sin referencia: {sin_ref}")
    if sin_ref:
        print(
            "   -- las lineas sin referencia NO entran en ninguna cifra de abajo, y son "
            f"{sin_ref} de {con_ref + sin_ref}: ese es el denominador del resumen."
        )
    print(f"refacturadas (con nota de credito): {sum(1 for c in casos if c.refacturada)}")
    print(f"cobradas: {sum(1 for c in casos if c.cobrada)}")
    constantes = sum(1 for c in casos if c.razon_constante)
    print(f"con razon constante (compatible con conversion): {constantes}")
    dos_vias = sum(1 for c in casos for ln in c.lineas if ln.usa_las_dos_vias)
    print(f"lineas con descuento por LAS DOS vias a la vez: {dos_vias}")
    sobre = [
        (c.nombre, ln) for c in casos for ln in c.lineas
        if not ln.sin_referencia and ln.descuento_total > tope
    ]
    print(f"lineas con descuento sobre el tope de {tope:.0f} %: {len(sobre)}")
    print(f"\ndiferencia total contra la lista: {sum(c.diferencia_total for c in casos):,.2f} USD")
    print(
        "   Esa cifra NO es un monto a cobrar: parte puede ser un descuento bien\n"
        "   otorgado y parte una orden ya refacturada. Es el tamano de lo que hay que\n"
        "   evaluar caso por caso, que es lo que este reporte deja listo."
    )


def escribir_csv(casos: list[Caso], ruta: Path, tope: float) -> None:
    with ruta.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["orden", "fecha", "cliente", "refacturada", "cobrada", "producto", "cantidad",
             "entregado", "precio_unitario", "precio_de_lista", "descuento_en_el_precio_pct",
             "descuento_declarado_pct", "descuento_total_pct", "diferencia_usd", "veredicto"]
        )
        for c in casos:
            for ln in c.lineas:
                w.writerow([
                    c.nombre, c.fecha, c.cliente, c.refacturada, c.cobrada, ln.producto,
                    ln.cantidad, ln.entregado, round(ln.precio_unitario, 4),
                    round(ln.precio_de_lista or 0, 2), round(ln.descuento_en_el_precio, 2),
                    ln.descuento_declarado, round(ln.descuento_total, 2),
                    round(ln.diferencia_en_plata(), 2), ln.veredicto(tope),
                ])
    print(f"\nCSV escrito: {ruta}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--orden", help="una sola orden, por nombre (ej. S00015)")
    ap.add_argument(
        "--tope-descuento", type=float, default=TOPE_DESCUENTO_POR_DEFECTO,
        help="porcentaje sobre el cual un descuento se marca; es un parametro, no una politica",
    )
    ap.add_argument("--csv", help="ademas, escribir el detalle por linea a este archivo")
    args = ap.parse_args()

    ejecutar = _conectar()
    casos, lista_ref, _conteo, nota = recolectar(ejecutar, args.orden)
    # La referencia se chequea PRIMERO. Antes se miraba `not casos` antes que esto,
    # y las dos fallas --no hay ordenes, y no se pudo calibrar la referencia--
    # imprimian el mismo mensaje equivocado. Un diagnostico que confunde dos causas
    # distintas no es un diagnostico.
    if lista_ref is None:
        print(
            f"No se pudo calibrar la referencia: {nota}."
            "\nEl reporte NO se emite: una referencia supuesta es peor que ninguna."
        )
        return 1
    if not casos:
        print(f"Sin casos para mostrar ({nota}).")
        return 0

    imprimir(casos, nota, args.tope_descuento)
    if args.csv:
        escribir_csv(casos, Path(args.csv), args.tope_descuento)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
