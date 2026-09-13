#!/usr/bin/env python
"""Que ordenes salen de la cuenta por cobrar porque una factura anulada cuenta como pago.

Instrumento del hallazgo que abrio el diagnostico de los dos items sueltos del
plan (S00573 "refacturar" y S00372 "revisar el caso"), 10-sep-2026.

**El defecto.** ``_pagos_odoo_por_orden`` pregunta primero por los
``account.payment`` reconciliados, que es el importe real cobrado. Cuando no hay
ninguno cae al fallback ``amount_total - amount_residual``. Esa resta es correcta
para una factura que se esta cobrando, pero **una factura anulada tiene residual
cero por definicion**: lo que la dejo en cero fue la nota de credito que la
reversa, no un bolivar que entro. El fallback lee la anulacion como cobro
completo, y como deduce la tasa dividiendo el total de la factura por el total de
la orden, el abono fantasma sale exactamente igual al monto de la orden.

No depende de ninguna tasa, ni de que la serie este cargada. Es la resta.

**Esto no corrige nada.** Aplicar la distincion devuelve ordenes a la cuenta por
cobrar, y eso mueve montos: decision del usuario (regla de la Fase 1). Lo que
hace es medir el hueco.

**La regla que este script se aplica a si mismo.** No basta con contar facturas
anuladas: la mayoria fueron refacturadas y su ciclo esta bien hecho. Lo que
importa se separa en tres, y cada seccion dice sobre cuantas hablo:

1. **Sin ninguna factura viva.** Mercancia entregada y ningun documento que la
   cobre. Hay que refacturar.
2. **Refacturada y con residual real sin cobrar.** El ciclo se hizo bien, pero
   el abono fantasma de la anulada tapa el residual de la nueva. Se descuenta el
   residual que es retencion de IVA, porque ese no se cobra en efectivo.
3. **Refacturada y ya cobrada.** No hay plata perdida, pero el abono cuenta dos
   veces y eso infla los KPI de cobranza.

Las ordenes de clientes ``ZZ BLINDAJE`` (el banco de pruebas) se excluyen y se
informa cuantas fueron.

Uso:
    python scripts/auditar_facturas_anuladas.py --env .env.qa
    python scripts/auditar_facturas_anuladas.py --env .env.qa --detalle
"""

from __future__ import annotations

import argparse
import os
import sys
import xmlrpc.client
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

# Los clientes que el banco de escenarios crea. No son ventas.
PREFIJO_PRUEBAS = "ZZ BLINDAJE"

# El IVA con el que se estima si un residual es retencion y no deuda. Un residual
# que coincide con el IVA de la factura lo cierra el comprobante de retencion, no
# la cobranza -- confirmado en S00372, donde el residual de 45.193,11 VES es
# exactamente el IVA que el cliente retuvo.
IVA = Decimal("0.16")

# Cuanto puede alejarse un residual del IVA estimado y seguir contando como
# retencion. Un 2% cubre el redondeo de Odoo por linea; el piso de 1 VES evita
# que una factura chica no tenga margen ninguno.
TOLERANCIA_IVA_PCT = Decimal("0.02")
TOLERANCIA_IVA_MIN = Decimal("1")


def _cargar_env(ruta: str) -> None:
    p = Path(ruta)
    if not p.is_absolute():
        p = RAIZ / ruta
    if not p.exists():
        sys.exit(f"No existe {p}")
    for linea in p.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, _, valor = linea.partition("=")
            os.environ[clave.strip()] = valor.strip()


def _conectar_odoo():
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    usuario = os.environ["ODOO_USERNAME"]
    clave = os.environ["ODOO_PASSWORD"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, usuario, clave, {})
    if not uid:
        sys.exit(f"Odoo rechazo las credenciales de {usuario} en {db}")
    objeto = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    def ejecutar(modelo, metodo, args, kwargs=None):
        return objeto.execute_kw(db, uid, clave, modelo, metodo, args, kwargs or {})

    return url, ejecutar


def _es_retencion(residual: Decimal, total: Decimal) -> bool:
    """True si el residual coincide con el IVA de la factura.

    Se compara contra el IVA **estimado sobre el total**, que es el mismo criterio
    que usa el reporte para bajar el saldo objetivo cuando ``wh_iva_aplicado``
    esta puesto. No se asume un porcentaje de retencion fijo: o coincide con el
    IVA completo o se cuenta como deuda.
    """
    if total <= 0:
        return False
    iva_estimado = total - total / (Decimal("1") + IVA)
    margen = max(TOLERANCIA_IVA_MIN, iva_estimado * TOLERANCIA_IVA_PCT)
    return abs(residual - iva_estimado) <= margen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default=".env")
    ap.add_argument("--detalle", action="store_true", help="lista cada orden")
    args = ap.parse_args()
    _cargar_env(args.env)

    import sqlalchemy as sa

    url, ejecutar = _conectar_odoo()
    print(f"Odoo: {url}  db={os.environ['ODOO_DB']}")

    anuladas = ejecutar(
        "account.move",
        "search_read",
        [
            [
                ["move_type", "=", "out_invoice"],
                ["state", "=", "posted"],
                ["payment_state", "=", "reversed"],
            ]
        ],
        {"fields": ["id", "name", "invoice_origin", "amount_total", "invoice_date"]},
    )
    print(f"\nFacturas out_invoice posted con payment_state='reversed': {len(anuladas)}")
    if not anuladas:
        print("Ninguna. Nada que medir -- el fallback no tiene por donde inventar un abono.")
        return 0

    # La ruta principal gana sobre el fallback: si hay un pago reconciliado, el
    # abono sale del pago real y la resta nunca corre.
    ids = [f["id"] for f in anuladas]
    pagos = ejecutar(
        "account.payment",
        "search_read",
        [[["reconciled_invoice_ids", "in", ids]]],
        {"fields": ["reconciled_invoice_ids"]},
    )
    con_pago = {i for p in pagos for i in p["reconciled_invoice_ids"]}
    print(f"  de esas, con account.payment reconciliado (no toman el fallback): {len(con_pago)}")

    motor = sa.create_engine(
        os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://")
    )
    sin_viva: list[tuple] = []
    con_residual: list[tuple] = []
    ya_cobradas: list[tuple] = []
    de_pruebas = 0
    sin_entrega = 0

    with motor.connect() as con:
        entregadas = {
            r[0]
            for r in con.execute(
                sa.text(
                    "SELECT so_id FROM lineas_orden GROUP BY so_id "
                    "HAVING coalesce(sum(cantidad_entregada),0) > 0"
                )
            )
        }
        ordenes: dict[str, tuple[Decimal, str]] = {}
        for fila in con.execute(
            sa.text(
                "SELECT o.so_id, o.monto_total, coalesce(c.nombre,'?') "
                "FROM ordenes_venta o LEFT JOIN clientes c ON c.cliente_id = o.cliente_id"
            )
        ):
            ordenes[fila[0]] = (Decimal(str(fila[1] or "0")), str(fila[2]))

        # Una orden puede tener mas de una anulada (S00817 tiene dos): se agrupa.
        candidatas: set[str] = set()
        for f in anuladas:
            so = f["invoice_origin"]
            if f["id"] in con_pago or not so or so not in ordenes:
                continue
            if PREFIJO_PRUEBAS in ordenes[so][1]:
                de_pruebas += 1
                continue
            if so not in entregadas:
                sin_entrega += 1
                continue
            candidatas.add(so)

        for so in sorted(candidatas):
            monto_orden, cliente = ordenes[so]
            vivas = ejecutar(
                "account.move",
                "search_read",
                [
                    [
                        ["invoice_origin", "=", so],
                        ["move_type", "=", "out_invoice"],
                        ["state", "=", "posted"],
                        ["payment_state", "!=", "reversed"],
                    ]
                ],
                {"fields": ["name", "payment_state", "amount_total", "amount_residual"]},
            )
            if not vivas:
                sin_viva.append((so, monto_orden, cliente))
                continue
            # El residual se pasa a dolares con la tasa que la propia factura
            # implica (su total sobre el total de la orden). Es la misma tasa que
            # usa el fallback, asi que el numero es comparable con el abono.
            deuda_usd = Decimal("0")
            retenido_usd = Decimal("0")
            detalle = []
            for v in vivas:
                residual = Decimal(str(v["amount_residual"] or "0"))
                total = Decimal(str(v["amount_total"] or "0"))
                if residual <= Decimal("0.005"):
                    continue
                tasa = total / monto_orden if monto_orden > 0 else Decimal("0")
                en_usd = residual / tasa if tasa > 0 else Decimal("0")
                if _es_retencion(residual, total):
                    retenido_usd += en_usd
                else:
                    deuda_usd += en_usd
                detalle.append((v["name"], v["payment_state"], residual, en_usd))
            if deuda_usd > Decimal("0.005"):
                con_residual.append((so, monto_orden, cliente, deuda_usd, retenido_usd, detalle))
            else:
                ya_cobradas.append((so, monto_orden, cliente, retenido_usd))

    total = len(sin_viva) + len(con_residual) + len(ya_cobradas)
    print(f"  de pruebas ({PREFIJO_PRUEBAS}), excluidas: {de_pruebas}")
    print(f"  sin mercancia entregada (no generan cuenta por cobrar): {sin_entrega}")
    print(f"\nOrdenes reales que toman el fallback con una anulada: {total}")
    if not total:
        print("Ninguna. El defecto existe en el codigo pero hoy no toca ninguna orden real.")
        return 0

    print(f"\n1. SIN NINGUNA FACTURA VIVA -- hay que refacturar: {len(sin_viva)} de {total}")
    suma = sum(m for _, m, _ in sin_viva)
    print(f"   mercancia entregada sin documento que la cobre: {suma:,.2f} USD")
    for so, m, cli in sorted(sin_viva, key=lambda x: -x[1]):
        print(f"     {so:9} {m:>10,.2f} USD  {cli[:40]}")

    print(f"\n2. REFACTURADA Y CON RESIDUAL REAL SIN COBRAR: {len(con_residual)} de {total}")
    suma_deuda = sum(d for _, _, _, d, _, _ in con_residual)
    suma_ret = sum(r for _, _, _, _, r, _ in con_residual)
    print(f"   deuda que el abono fantasma tapa: {suma_deuda:,.2f} USD")
    print(f"   (descontado aparte, residual que es retencion de IVA: {suma_ret:,.2f} USD)")
    for so, m, cli, deuda, _ret, detalle in sorted(con_residual, key=lambda x: -x[3]):
        print(f"     {so:9} orden {m:>10,.2f}  debe {deuda:>9,.2f} USD  {cli[:32]}")
        if args.detalle:
            for numero, estado, residual, en_usd in detalle:
                print(
                    f"        {numero} {estado:9} residual {residual:>12,.2f} "
                    f"= {en_usd:>8,.2f} USD"
                )

    print(
        f"\n3. REFACTURADA Y YA COBRADA -- el abono cuenta dos veces: {len(ya_cobradas)} de {total}"
    )
    print("   no hay plata perdida; infla los KPI de cobranza.")
    if args.detalle:
        for so, m, cli, _ret in sorted(ya_cobradas, key=lambda x: -x[1]):
            print(f"     {so:9} orden {m:>10,.2f} USD  {cli[:40]}")

    print(
        f"\nTOTAL de plata que el defecto esconde: {suma + suma_deuda:,.2f} USD "
        f"({suma:,.2f} sin facturar + {suma_deuda:,.2f} facturado y sin cobrar)"
    )
    print(
        "\nNada de esto se corrige aca: aplicar la distincion devuelve estas ordenes a la\n"
        "cuenta por cobrar, y eso mueve montos."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
