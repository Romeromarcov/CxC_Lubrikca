"""Las invariantes de dinero, verificadas al ESCRIBIR y no al leer.

Fase 2.2 del plan de blindaje. Hoy estas reglas solo se comprueban cuando
alguien abre una pagina; a partir de aca la base se niega a guardar un dato
que las viole.

QUE SE AGREGA, Y POR QUE CADA UNA

Ocho ``CHECK``, ninguna arbitraria: cada una corresponde a un numero que,
si se guarda mal, sale despues como un saldo. Las referenciales ya estaban
cubiertas por claves foraneas (vinculaciones -> pagos y ordenes), que es
por que ese invariante nunca dio hallazgos.

- ``vinculaciones``: el monto aplicado no es negativo; las dos tasas
  congeladas son positivas (una tasa en cero divide o multiplica todo el
  equivalente de ese abono); y el equivalente en dolares de un abono en
  bolivares nunca supera su nominal -- con la tasa por encima de 1 eso es
  aritmeticamente imposible, y delata una tasa congelada al reves o en la
  unidad equivocada.
- ``ventas_teoricos``: ningun teorico ni descuento es negativo, y ningun
  descuento supera su propio teorico (un descuento mayor que la venta deja
  la orden en negativo).
- ``pagos``, ``ordenes_venta``, ``lineas_orden``: los montos y las
  cantidades pedidas no son negativos.

QUE NO SE AGREGA, Y POR QUE

**``lineas_orden.cantidad_entregada >= 0`` NO se agrega.** Los datos reales
la violan en dos filas (S00952 con -4 unidades y S00925 con -10), y no es
corrupcion: es como Odoo representa una devolucion que supera lo que quedo
en la linea. Convertirlo en restriccion romperia el sync contra datos que
Odoo considera validos. Se mira desde ``auditar_integridad`` en vez de
prohibirse.

**"Lo aplicado de un pago nunca supera el pago" tampoco.** Es la invariante
que hoy violan 10 pagos por 1.333,85 USD (ver docs/blindaje/2.3-alertas.md),
asi que la migracion fallaria; y ademas es una condicion entre filas, que un
CHECK no puede expresar. Vive en la corrida diaria hasta que se decida como
tratar las diferencias de cambio.

Las ocho que si entran se verificaron contra el espejo de QA -- copia de
produccion, 967 ordenes, 1.460 vinculaciones, 814 teoricos -- y las ocho dan
cero violaciones. Aun asi el ``upgrade`` las agrega con ``NOT VALID`` primero
y las valida despues: si alguna encontrara una fila vieja que la viola, la
migracion falla en la validacion y no a mitad de la escritura.

Revision ID: f1e2d3c4b5a6
Revises: 238aafc9bb95
"""

from __future__ import annotations

from alembic import op

revision = "f1e2d3c4b5a6"
down_revision = "238aafc9bb95"
branch_labels = None
depends_on = None


# (tabla, nombre, clausula). El nombre entra en el mensaje de error de
# Postgres, asi que dice que se rompio y no solo que algo se rompio.
RESTRICCIONES: list[tuple[str, str, str]] = [
    (
        "vinculaciones",
        "ck_vinc_monto_aplicado_no_negativo",
        "monto_aplicado >= 0",
    ),
    (
        "vinculaciones",
        "ck_vinc_tasas_positivas",
        "tasa_bcv_aplicada > 0 AND tasa_binance_aplicada > 0",
    ),
    (
        "vinculaciones",
        "ck_vinc_equivalente_no_supera_el_nominal",
        "moneda_abono <> 'VES' OR greatest("
        "coalesce(equiv_usd_bcv, 0), coalesce(equiv_usd_binance, 0)"
        ") <= monto_aplicado",
    ),
    (
        "ventas_teoricos",
        "ck_teoricos_no_negativos",
        "teorico_ves >= 0 AND teorico_usd >= 0 "
        "AND descuentos_teorico_ves >= 0 AND descuentos_teorico_usd >= 0",
    ),
    (
        "ventas_teoricos",
        "ck_descuento_no_supera_el_teorico",
        "descuentos_teorico_ves <= teorico_ves + 0.01 "
        "AND descuentos_teorico_usd <= teorico_usd + 0.01",
    ),
    ("pagos", "ck_pago_monto_no_negativo", "monto >= 0"),
    ("ordenes_venta", "ck_orden_monto_total_no_negativo", "monto_total >= 0"),
    ("lineas_orden", "ck_linea_cantidad_no_negativa", "cantidad >= 0"),
]


def upgrade() -> None:
    for tabla, nombre, clausula in RESTRICCIONES:
        # NOT VALID primero: la restriccion rige para toda escritura NUEVA
        # desde ya, sin bloquear la tabla para revisar el historico.
        op.execute(
            f'ALTER TABLE "{tabla}" ADD CONSTRAINT "{nombre}" '
            f"CHECK ({clausula}) NOT VALID"
        )
        # Y despues se valida el historico. Si alguna fila vieja la viola, la
        # migracion falla ACA, con la tabla y la clausula en el mensaje, en vez
        # de a mitad de la escritura.
        op.execute(f'ALTER TABLE "{tabla}" VALIDATE CONSTRAINT "{nombre}"')


def downgrade() -> None:
    for tabla, nombre, _clausula in reversed(RESTRICCIONES):
        op.execute(f'ALTER TABLE "{tabla}" DROP CONSTRAINT IF EXISTS "{nombre}"')
