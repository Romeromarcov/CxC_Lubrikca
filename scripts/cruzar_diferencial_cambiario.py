#!/usr/bin/env python
"""Los pagos sobreaplicados y los sobrepagos, cruzados con el bug de Odoo al editar fechas.

**Por que existe.** El usuario, al responder el quiz de decisiones (11-sep-2026):

    "Estoy seguro con el caso de la orden 0061 que es por un error del sistema que
    genera un diferencial cuando se tiene que editar la fecha de pago por algun error
    humano, hay una bandeja de auditoria que ya reconoce esas discrepancias, y eso
    afecta esos pagos. Cruza esa informacion con auditoria porque eso puede estar
    ocurriendo con otros casos, que no generan el saldo negativo porque aun no se ha
    cubierto todo el saldo de la orden. Y no se si a lo mejor eso tambien tenga que ver
    con el caso 1"

El caso 1 son los diez pagos "sobreaplicados" (1.269,25 USD de exceso), y la decision
pendiente era como repartir ese exceso. **Antes de repartir nada habia que saber si es
exceso.** Mirando el peor --pago 200, vale 134 USD y tiene 715,04 aplicados-- la firma
es inconfundible:

  - nueve parciales, todos contra facturas de cliente reales, que suman 217.134,51 Bs
    contra una linea de pago de 96.660,31 Bs (2,25x);
  - tasas implicitas de 4,1 a 709,7 **dentro del mismo pago** (el parcial 1084 dice que
    272,49 Bs son 66,33 USD);
  - parciales creados el 16-jul y el 21-ago, pago fechado el 24-abr, `write_date` del
    31-ago, y `is_reconciled = False` a pesar de tener cinco veces su valor aplicado.

Eso no es dinero sobreaplicado: son parciales corrompidos por ediciones de fecha/tasa
sobre un pago ya reconciliado --el bug de Odoo que `_detectar_pagos_con_importe_local_
desincronizado` y `_detectar_ajustes_cambio_huerfanos` ya persiguen por otros sintomas.
Repartir ese "exceso" por orden seria corregir lo que no es.

**Que mide.** Para cada pago sobreaplicado y para cada pago de las cuatro ordenes con
sobrepago, cuatro senales de la misma firma:

  1. `write_date` del pago POSTERIOR a la creacion de sus parciales (editado despues de
     reconciliar);
  2. dispersion de la tasa implicita entre sus parciales (max/min);
  3. lo detecta `_detectar_pagos_con_importe_local_desincronizado`;
  4. tiene un Ajuste Cambio huerfano (`_detectar_ajustes_cambio_huerfanos`).

Y despues barre TODOS los pagos con mas de un parcial, porque el usuario tiene razon en
que el sintoma visible (saldo negativo, exceso) solo aparece cuando la orden ya se cubrio:
un pago corrompido sobre una orden a medio pagar no dispara ninguna alarma.

No corrige nada. Los parciales viven en Odoo y deshacerlos es reconciliar de nuevo, que
son las manos del usuario.

Uso:
    python scripts/cruzar_diferencial_cambiario.py
    python scripts/cruzar_diferencial_cambiario.py --csv salida.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import xmlrpc.client
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from cxc.web.app import (  # noqa: E402
    _detectar_ajustes_cambio_huerfanos,
    _detectar_pagos_con_importe_local_desincronizado,
)

# Las cuatro ordenes con residual negativo, medidas el 11-sep-2026.
ORDENES_CON_SOBREPAGO = ("S00188", "S00795", "S00182", "S00061")

# Una tasa implicita cuyo maximo supera al minimo por mas de esto, dentro del mismo pago,
# no es una tasa: es la firma de parciales calculados en momentos distintos.
DISPERSION_SOSPECHOSA = 1.5


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
class Parcial:
    id: int
    amount_ves: float
    amount_currency: float
    creado: str
    factura: str
    so_id: str

    @property
    def tasa_implicita(self) -> float | None:
        return self.amount_ves / self.amount_currency if self.amount_currency else None


@dataclass
class Pago:
    id: int
    nombre: str
    monto: float
    moneda: str
    fecha: str
    write_date: str
    reconciliado: bool
    parciales: list[Parcial] = field(default_factory=list)
    importe_local_desincronizado: dict | None = None
    ajuste_huerfano: bool = False

    @property
    def aplicado(self) -> float:
        return sum(p.amount_currency for p in self.parciales)

    @property
    def exceso(self) -> float:
        return self.aplicado - self.monto

    @property
    def sobreaplicado(self) -> bool:
        return self.exceso > 0.05

    @property
    def editado_despues_de_reconciliar(self) -> bool:
        if not self.parciales:
            return False
        return self.write_date[:10] > max(p.creado[:10] for p in self.parciales)

    @property
    def dispersion_de_tasa(self) -> float | None:
        tasas = [
            p.tasa_implicita for p in self.parciales if p.tasa_implicita and p.tasa_implicita > 0
        ]
        if len(tasas) < 2:
            return None
        return max(tasas) / min(tasas)

    @property
    def senales(self) -> list[str]:
        s = []
        if self.editado_despues_de_reconciliar:
            s.append("editado tras reconciliar")
        d = self.dispersion_de_tasa
        if d is not None and d > DISPERSION_SOSPECHOSA:
            s.append(f"tasa implicita x{d:.1f}")
        if self.importe_local_desincronizado:
            s.append(
                "importe local desinc. "
                f"{self.importe_local_desincronizado['diferencia_ves']:+,.2f} Bs"
            )
        if self.ajuste_huerfano:
            s.append("ajuste cambio huerfano")
        if self.parciales and not self.reconciliado and self.sobreaplicado:
            s.append("sobreaplicado y SIN reconciliar")
        return s

    @property
    def ordenes(self) -> set[str]:
        return {p.so_id for p in self.parciales if p.so_id}


def recolectar(ejecutar: Any) -> list[Pago]:
    pagos_crudos = ejecutar(
        "account.payment",
        "search_read",
        [[["payment_type", "=", "inbound"], ["state", "!=", "cancel"]]],
        {
            "fields": [
                "id",
                "name",
                "amount",
                "currency_id",
                "date",
                "write_date",
                "move_id",
                "is_reconciled",
            ],
        },
    )
    por_move = {p["move_id"][0]: p for p in pagos_crudos if p.get("move_id")}
    lineas = ejecutar(
        "account.move.line",
        "search_read",
        [[["move_id", "in", list(por_move)], ["account_type", "=", "asset_receivable"]]],
        {"fields": ["id", "move_id"]},
    )
    linea_a_pago = {
        ln["id"]: por_move[ln["move_id"][0]] for ln in lineas if ln["move_id"][0] in por_move
    }
    partials = ejecutar(
        "account.partial.reconcile",
        "search_read",
        [[["credit_move_id", "in", list(linea_a_pago)]]],
        {
            "fields": [
                "id",
                "credit_move_id",
                "debit_move_id",
                "amount",
                "credit_amount_currency",
                "create_date",
            ]
        },
    )
    deb_ids = sorted({pr["debit_move_id"][0] for pr in partials})
    linea_a_factura: dict[int, int] = {}
    for trozo in [deb_ids[i : i + 500] for i in range(0, len(deb_ids), 500)]:
        for ln in ejecutar("account.move.line", "read", [trozo], {"fields": ["id", "move_id"]}):
            if ln.get("move_id"):
                linea_a_factura[ln["id"]] = ln["move_id"][0]
    fact_ids = sorted(set(linea_a_factura.values()))
    facturas: dict[int, dict] = {}
    for trozo in [fact_ids[i : i + 500] for i in range(0, len(fact_ids), 500)]:
        for f in ejecutar(
            "account.move",
            "read",
            [trozo],
            {"fields": ["id", "name", "invoice_origin", "move_type"]},
        ):
            facturas[f["id"]] = f

    pagos: dict[int, Pago] = {}
    for p in pagos_crudos:
        pagos[p["id"]] = Pago(
            id=p["id"],
            nombre=p["name"],
            monto=float(p["amount"] or 0),
            moneda=(p["currency_id"] or [0, "?"])[1],
            fecha=str(p["date"]),
            write_date=str(p["write_date"]),
            reconciliado=bool(p.get("is_reconciled")),
        )
    for pr in partials:
        pago = linea_a_pago.get(pr["credit_move_id"][0])
        if not pago:
            continue
        fid = linea_a_factura.get(pr["debit_move_id"][0])
        f = facturas.get(fid, {}) if fid else {}
        pagos[pago["id"]].parciales.append(
            Parcial(
                id=pr["id"],
                amount_ves=float(pr["amount"] or 0),
                amount_currency=float(pr["credit_amount_currency"] or 0),
                creado=str(pr["create_date"]),
                factura=str(f.get("name") or fid or ""),
                so_id=str(f.get("invoice_origin") or "").strip(),
            )
        )

    # Los dos detectores de la app, sobre todos.
    desinc = {
        d["pago_id"]: d
        for d in _detectar_pagos_con_importe_local_desincronizado(
            ejecutar, [p.id for p in pagos.values()]
        )
    }
    for p in pagos.values():
        p.importe_local_desincronizado = desinc.get(str(p.id))
    # El detector de ajustes huerfanos contesta por FACTURA (y su orden), no por pago:
    # un Ajuste Cambio posteado cuya linea de CxC sigue sin reconciliar. Se marca cada
    # pago cuyas ordenes tengan uno.
    fact_por_nombre = {
        str(f["name"]): str(f.get("invoice_origin") or "").strip()
        for f in facturas.values()
        if f.get("name")
    }
    huerfanos = _detectar_ajustes_cambio_huerfanos(ejecutar, fact_por_nombre)
    ordenes_con_huerfano = {str(h.get("so_id") or "") for h in huerfanos} - {""}
    for p in pagos.values():
        if p.ordenes & ordenes_con_huerfano:
            p.ajuste_huerfano = True
    return sorted(pagos.values(), key=lambda p: p.id)


def imprimir(pagos: list[Pago]) -> None:
    def fila(p: Pago) -> str:
        return (
            f"  {p.id:>5} {p.nombre:<18} {p.monto:>10,.2f} {p.moneda:<4} "
            f"aplicado={p.aplicado:>10,.2f} "
            f"exceso={p.exceso:>+9,.2f}  {len(p.parciales):>2} parc  "
            f"{'; '.join(p.senales) or '-'}"
        )

    print("=" * 110)
    print("CASO 1: LOS PAGOS SOBREAPLICADOS, Y QUE SON DE VERDAD")
    print("=" * 110)
    sobre = [p for p in pagos if p.sobreaplicado]
    print(f"pagos sobreaplicados (aplicado > monto + 0,05): {len(sobre)}")
    print(f"exceso total: {sum(p.exceso for p in sobre):,.2f}\n")
    for p in sobre:
        print(fila(p))
        for pc in sorted(p.parciales, key=lambda x: x.creado):
            t = f"{pc.tasa_implicita:8.1f}" if pc.tasa_implicita else "     -  "
            print(
                f"           parcial {pc.id:>5}  {pc.amount_ves:>12,.2f} Bs = "
                f"{pc.amount_currency:>9,.2f} "
                f"{p.moneda}  tasa {t}  creado {pc.creado[:10]}  {pc.factura} -> {pc.so_id}"
            )
    con_firma = [
        p
        for p in sobre
        if p.editado_despues_de_reconciliar or (p.dispersion_de_tasa or 0) > DISPERSION_SOSPECHOSA
    ]
    print(
        f"\n  con la firma del bug (editado tras reconciliar, o tasas dispersas): "
        f"{len(con_firma)} de {len(sobre)}"
    )
    print(
        f"  exceso de esos: {sum(p.exceso for p in con_firma):,.2f} "
        f"de {sum(p.exceso for p in sobre):,.2f}"
    )

    print("\n" + "=" * 110)
    print("CASO 3: LOS PAGOS DE LAS CUATRO ORDENES CON SOBREPAGO")
    print("=" * 110)
    for so in ORDENES_CON_SOBREPAGO:
        de_la_orden = [p for p in pagos if so in p.ordenes]
        print(f"\n{so}: {len(de_la_orden)} pago(s)")
        for p in de_la_orden:
            print(fila(p))

    print("\n" + "=" * 110)
    print("EL BARRIDO: TODOS LOS PAGOS CON LA FIRMA, tengan o no saldo negativo")
    print("=" * 110)
    firmados = [
        p
        for p in pagos
        if p.parciales
        and (
            p.editado_despues_de_reconciliar
            or (p.dispersion_de_tasa or 0) > DISPERSION_SOSPECHOSA
            or p.importe_local_desincronizado
            or p.ajuste_huerfano
        )
    ]
    print(
        f"pagos con al menos una senal: {len(firmados)} "
        f"de {len([p for p in pagos if p.parciales])} con parciales"
    )
    por_senal: dict[str, int] = defaultdict(int)
    for p in firmados:
        for s in p.senales:
            por_senal[s.split(" x")[0].split(" +")[0].split(" -")[0]] += 1
    for s, n in sorted(por_senal.items(), key=lambda x: -x[1]):
        print(f"   {n:>4}  {s}")
    ordenes_tocadas = set().union(*(p.ordenes for p in firmados)) if firmados else set()
    print(f"ordenes tocadas por esos pagos: {len(ordenes_tocadas)}")
    silenciosos = [p for p in firmados if not p.sobreaplicado]
    print(
        f"\n  de los {len(firmados)}, SIN exceso visible (la orden no esta cubierta, o el "
        f"parcial corrompido no llega a superar el pago): {len(silenciosos)}"
    )
    print("  -- estos son los que el usuario dijo que no disparan ninguna alarma.")


def escribir_csv(pagos: list[Pago], ruta: Path) -> None:
    with ruta.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "pago_id",
                "nombre",
                "monto",
                "moneda",
                "fecha",
                "write_date",
                "reconciliado",
                "parciales",
                "aplicado",
                "exceso",
                "sobreaplicado",
                "editado_tras_reconciliar",
                "dispersion_tasa",
                "importe_local_desinc_ves",
                "ajuste_huerfano",
                "ordenes",
            ]
        )
        for p in pagos:
            if not p.parciales:
                continue
            w.writerow(
                [
                    p.id,
                    p.nombre,
                    p.monto,
                    p.moneda,
                    p.fecha,
                    p.write_date,
                    p.reconciliado,
                    len(p.parciales),
                    round(p.aplicado, 2),
                    round(p.exceso, 2),
                    p.sobreaplicado,
                    p.editado_despues_de_reconciliar,
                    round(p.dispersion_de_tasa, 2) if p.dispersion_de_tasa else "",
                    (p.importe_local_desincronizado or {}).get("diferencia_ves", ""),
                    p.ajuste_huerfano,
                    " ".join(sorted(p.ordenes)),
                ]
            )
    print(f"\nCSV escrito: {ruta}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv")
    args = ap.parse_args()
    pagos = recolectar(_conectar())
    imprimir(pagos)
    if args.csv:
        escribir_csv(pagos, Path(args.csv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
