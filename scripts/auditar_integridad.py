#!/usr/bin/env python3
"""Integridad de las tablas del espejo (Fase 1.3 del plan de blindaje).

Un catalogo de preguntas que la base deberia contestar siempre con cero
filas. Cada una vive en ``CHEQUEOS`` con su SQL, su severidad y una
explicacion de por que importa -- si una empieza a dar filas, el texto dice
que se rompio sin tener que releer el SQL.

    python scripts/auditar_integridad.py                 # contra DATABASE_URL
    python scripts/auditar_integridad.py --env .env.qa   # contra el espejo de QA
    python scripts/auditar_integridad.py --detalle       # muestra las filas
    python scripts/auditar_integridad.py --json out.json

Salida distinta de cero si algun chequeo ALTA da filas: sirve de barrera en
la corrida diaria de la Fase 2.3.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Chequeo:
    nombre: str
    familia: str
    severidad: str  # ALTA | MEDIA | BAJA
    porque: str
    sql: str


# --- Huerfanos ---------------------------------------------------------------
# Una vinculacion que apunta a un pago o una orden que ya no existe es plata
# aplicada a la nada: sigue sumando en los reportes que agregan por
# vinculacion y no aparece en los que agregan por orden.
_HUERFANOS = [
    ("vinculaciones_sin_pago", "vinculaciones", "pago_id", "pagos", "pago_id", "ALTA"),
    ("vinculaciones_sin_orden", "vinculaciones", "so_id", "ordenes_venta", "so_id", "ALTA"),
    ("lineas_sin_orden", "lineas_orden", "so_id", "ordenes_venta", "so_id", "ALTA"),
    ("ventas_teoricos_sin_orden", "ventas_teoricos", "so_id", "ordenes_venta", "so_id", "ALTA"),
    ("descuentos_sin_orden", "descuento_aplicado", "so_id", "ordenes_venta", "so_id", "MEDIA"),
    ("bandeja_sin_orden", "bandeja_facturacion", "so_id", "ordenes_venta", "so_id", "MEDIA"),
    ("ordenes_sin_cliente", "ordenes_venta", "cliente_id", "clientes", "cliente_id", "MEDIA"),
    ("pagos_sin_cliente", "pagos", "cliente_id", "clientes", "cliente_id", "MEDIA"),
    ("lineas_entrega_sin_entrega", "lineas_entrega", "entrega_id", "entregas",
     "entrega_id", "MEDIA"),
    ("facturas_sin_orden", "facturas", "so_id", "ordenes_venta", "so_id", "MEDIA"),
    ("entregas_sin_orden", "entregas", "so_id", "ordenes_venta", "so_id", "MEDIA"),
]


def _chequeos_huerfanos() -> list[Chequeo]:
    salida = []
    for nombre, tabla, col, destino, col_destino, sev in _HUERFANOS:
        salida.append(
            Chequeo(
                nombre=nombre,
                familia="huerfanos",
                severidad=sev,
                porque=(
                    f"{tabla}.{col} apunta a {destino} que no existe. La fila sigue "
                    "contando en los reportes que agregan por esta tabla y desaparece "
                    "en los que agregan por la otra."
                ),
                sql=f"""
                    SELECT h."{col}" AS referencia_rota, count(*) AS filas
                    FROM "{tabla}" h
                    LEFT JOIN "{destino}" d ON d."{col_destino}" = h."{col}"
                    WHERE h."{col}" IS NOT NULL AND h."{col}" <> '' AND d."{col_destino}" IS NULL
                    GROUP BY 1 ORDER BY 2 DESC
                """,
            )
        )
    return salida


CHEQUEOS: list[Chequeo] = [
    *_chequeos_huerfanos(),
    # --- Duplicados por clave natural ------------------------------------
    Chequeo(
        "facturas_numero_duplicado",
        "duplicados",
        "ALTA",
        "Dos asientos del MISMO tipo con el mismo numero. Es la forma que toma "
        "'anulan una factura y emiten otra por lo mismo' cuando la anulada no "
        "cambia de numero: la orden se cuenta dos veces. Se agrupa tambien por "
        "move_type porque en Odoo cada tipo lleva su propia secuencia -- una "
        "factura, su nota de credito y su nota de debito comparten el numero "
        "00000002 y eso es normal, no un duplicado.",
        """
        SELECT numero, move_type, count(*) AS filas,
               string_agg(factura_id || ':' || estado, ', ') AS ids
        FROM facturas WHERE numero <> ''
        GROUP BY numero, move_type HAVING count(*) > 1
        ORDER BY 3 DESC
        """,
    ),
    Chequeo(
        "lineas_factura_fuera_del_universo",
        "espejos",
        "MEDIA",
        "Lineas del espejo cuyo asiento padre no es una factura de cliente. "
        "``changed_lineas_factura`` no filtra por ``move_type``, asi que trae "
        "TODA linea de account.move: facturas de proveedor, asientos de diario, "
        "movimientos de pago. El espejo hermano ``changed_entregas_lineas`` si "
        "filtra (y su comentario explica que sin el filtro traia 6124 filas en "
        "vez de 1729); a este nunca se le hizo lo mismo. Hoy el unico consumidor "
        "se salva porque cruza contra ``invoice_ids``, pero el proximo que "
        "agregue sin ese cruce va a sumar montos de proveedores a la cuenta por "
        "cobrar.",
        """
        SELECT count(*) AS lineas_huerfanas,
               (SELECT count(*) FROM lineas_factura) AS lineas_totales,
               round(100.0 * count(*) / nullif((SELECT count(*) FROM lineas_factura), 0), 1)
                   AS pct_ruido
        FROM lineas_factura lf
        LEFT JOIN facturas f ON f.factura_id = lf.factura_id
        WHERE f.factura_id IS NULL
        HAVING count(*) > 0
        """,
    ),
    Chequeo(
        "vinculacion_repetida_pago_orden",
        "duplicados",
        "ALTA",
        "El mismo pago aplicado dos veces a la misma orden. Duplica el abono y "
        "la orden queda saldada con la mitad del dinero.",
        """
        SELECT pago_id, so_id, count(*) AS filas, sum(monto_aplicado) AS total
        FROM vinculaciones GROUP BY pago_id, so_id HAVING count(*) > 1
        ORDER BY 3 DESC
        """,
    ),
    Chequeo(
        "pagos_gemelos",
        "duplicados",
        "MEDIA",
        "Mismo cliente, mismo monto, misma moneda, mismo dia. Puede ser "
        "legitimo (dos transferencias iguales) o el mismo pago registrado dos "
        "veces. Se lista para mirarlo, no para asumir el error.",
        """
        SELECT cliente_id, monto, moneda, fecha_pago::date AS dia,
               count(*) AS filas, string_agg(pago_id, ', ') AS ids
        FROM pagos GROUP BY 1,2,3,4 HAVING count(*) > 1 ORDER BY 5 DESC
        """,
    ),
    # --- Dinero imposible -------------------------------------------------
    Chequeo(
        "montos_negativos",
        "montos",
        "ALTA",
        "Un monto negativo donde el modelo no lo admite. Una orden o un pago en "
        "negativo se resta de los totales sin que ninguna pagina lo explique.",
        """
        SELECT 'ordenes_venta' AS tabla, so_id AS id, monto_total AS monto
        FROM ordenes_venta WHERE monto_total < 0
        UNION ALL SELECT 'pagos', pago_id, monto FROM pagos WHERE monto < 0
        UNION ALL SELECT 'vinculaciones', vinc_id, monto_aplicado
        FROM vinculaciones WHERE monto_aplicado < 0
        UNION ALL SELECT 'lineas_orden', linea_id, cantidad
        FROM lineas_orden WHERE cantidad < 0
        ORDER BY 3
        """,
    ),
    Chequeo(
        "equivalentes_usd_nulos",
        "montos",
        "ALTA",
        "Vinculacion sin equivalente en dolares congelado. El equivalente es lo "
        "que se compara contra el teorico USD; sin el, el abono no se puede "
        "medir y los reportes lo tratan como cero.",
        """
        SELECT vinc_id, pago_id, so_id, monto_aplicado, tipo_tasa_abono
        FROM vinculaciones WHERE equiv_usd_bcv IS NULL AND equiv_usd_binance IS NULL
        ORDER BY monto_aplicado DESC
        """,
    ),
    Chequeo(
        "equivalente_mayor_que_el_nominal",
        "montos",
        "ALTA",
        "Un equivalente en dolares mayor que el monto en bolivares del abono. "
        "Con la tasa por encima de 1 eso es imposible: delata una tasa "
        "congelada al reves o en la unidad equivocada.",
        """
        SELECT v.vinc_id, v.so_id, v.monto_aplicado, v.moneda_abono,
               v.equiv_usd_bcv, v.equiv_usd_binance, v.tasa_bcv_aplicada
        FROM vinculaciones v
        WHERE v.moneda_abono = 'VES'
          AND greatest(coalesce(v.equiv_usd_bcv, 0), coalesce(v.equiv_usd_binance, 0))
              > v.monto_aplicado
        ORDER BY 3 DESC
        """,
    ),
    Chequeo(
        "vinculaciones_congeladas_al_default_de_2019",
        "montos",
        "ALTA",
        "Vinculaciones cuya tasa congelada es 36,50 / 38,00: el default de 2019 de "
        "``get_rate_for_datetime``. No es una tasa, es la senal de que no habia "
        "ninguna y la funcion devolvio un numero igual. Y el dano es permanente, "
        "porque el equivalente se congela por diseño y no se recalcula: un espejo "
        "levantado de cero, una migracion, o una ventana en la que el scraper "
        "estuvo caido, y cada equivalente calculado en ese hueco queda mal para "
        "siempre. Medido en el espejo de QA: las 1.462 vinculaciones, el 100%.",
        """
        SELECT count(*) AS vinculaciones, min(hora_pago_confirmada) AS desde,
               max(hora_pago_confirmada) AS hasta, sum(monto_aplicado) AS monto
        FROM vinculaciones
        WHERE tasa_bcv_aplicada = 36.5 AND tasa_binance_aplicada = 38.0
        HAVING count(*) > 0
        """,
    ),
    Chequeo(
        "tasas_no_positivas",
        "montos",
        "ALTA",
        "Una tasa en cero o negativa congelada en una vinculacion. Divide o "
        "multiplica todo el equivalente de ese abono.",
        """
        SELECT vinc_id, so_id, tasa_bcv_aplicada, tasa_binance_aplicada
        FROM vinculaciones
        WHERE tasa_bcv_aplicada <= 0 OR tasa_binance_aplicada <= 0
        """,
    ),
    Chequeo(
        "sobreaplicacion_del_pago",
        "montos",
        "ALTA",
        "Las vinculaciones de un pago suman mas que el pago. Se esta aplicando "
        "plata que no entro.",
        """
        SELECT v.pago_id, p.monto AS pago, sum(v.monto_aplicado) AS aplicado,
               sum(v.monto_aplicado) - p.monto AS exceso
        FROM vinculaciones v JOIN pagos p ON p.pago_id = v.pago_id
        GROUP BY v.pago_id, p.monto
        HAVING sum(v.monto_aplicado) > p.monto + 0.01
        ORDER BY 4 DESC
        """,
    ),
    # --- Fechas -----------------------------------------------------------
    Chequeo(
        "fechas_fuera_de_rango",
        "fechas",
        "MEDIA",
        "Fechas anteriores al arranque del negocio o en el futuro. Una fecha "
        "mal cargada elige la lista de precios y la tasa equivocadas.",
        """
        SELECT 'ordenes_venta.fecha' AS campo, so_id AS id, fecha::text AS valor
        FROM ordenes_venta WHERE fecha < DATE '2019-01-01' OR fecha > CURRENT_DATE + 1
        UNION ALL SELECT 'ordenes_venta.fecha_entrega', so_id, fecha_entrega::text
        FROM ordenes_venta
        WHERE fecha_entrega IS NOT NULL
          AND (fecha_entrega < DATE '2019-01-01' OR fecha_entrega > CURRENT_DATE + 1)
        UNION ALL SELECT 'pagos.fecha_pago', pago_id, fecha_pago::text
        FROM pagos WHERE fecha_pago < TIMESTAMP '2019-01-01'
                      OR fecha_pago > CURRENT_DATE + 1
        UNION ALL SELECT 'facturas.fecha', factura_id, fecha::text
        FROM facturas WHERE fecha < DATE '2019-01-01' OR fecha > CURRENT_DATE + 1
        ORDER BY 1, 3
        """,
    ),
    Chequeo(
        "entrega_antes_de_la_orden",
        "fechas",
        "MEDIA",
        "La entrega es anterior a la orden. La CxC nace con la entrega, asi que "
        "una entrega previa a su propia orden rompe el orden causal del que "
        "cuelga la ventana de contado.",
        """
        SELECT so_id, fecha AS fecha_orden, fecha_entrega
        FROM ordenes_venta
        WHERE fecha_entrega IS NOT NULL AND fecha_entrega < fecha
        ORDER BY fecha DESC
        """,
    ),
    # --- Estados imposibles ------------------------------------------------
    Chequeo(
        "orden_cancelada_con_pagos",
        "estados",
        "ALTA",
        "Orden cancelada con abonos aplicados. Los pagos quedan huerfanos: la "
        "partida que compara contra Odoo excluye las canceladas, asi que la "
        "orden se va de los totales y el dinero con ella.",
        """
        SELECT o.so_id, o.estado_orden, o.monto_total,
               count(v.vinc_id) AS vinculaciones, sum(v.monto_aplicado) AS aplicado
        FROM ordenes_venta o JOIN vinculaciones v ON v.so_id = o.so_id
        WHERE o.estado_orden = 'cancel'
        GROUP BY 1,2,3 ORDER BY 5 DESC
        """,
    ),
    Chequeo(
        "orden_cancelada_con_entrega",
        "estados",
        "ALTA",
        "Orden cancelada que igual salio del deposito. La mercancia se entrego "
        "y la orden ya no se persigue. Nunca deberia irse callada.",
        """
        SELECT o.so_id, o.estado_orden, o.estado_entrega, o.monto_total, o.fecha_entrega
        FROM ordenes_venta o
        WHERE o.estado_orden = 'cancel'
          AND (o.entregada_completa OR o.estado_entrega IN ('full', 'partial'))
        ORDER BY o.monto_total DESC
        """,
    ),
    Chequeo(
        "entregada_sin_fecha_de_entrega",
        "estados",
        "BAJA",
        "Marcada como entregada completa pero sin fecha. La ventana de contado "
        "cuelga de esa fecha; ya hay un fallback a la fecha de la orden y este "
        "chequeo mide cuanto se usa.",
        """
        SELECT so_id, fecha, estado_entrega, monto_total
        FROM ordenes_venta WHERE entregada_completa AND fecha_entrega IS NULL
        ORDER BY monto_total DESC
        """,
    ),
    Chequeo(
        "facturada_sin_factura",
        "estados",
        "MEDIA",
        "Marcada como facturada pero sin ninguna factura en el espejo. O la "
        "bandera quedo vieja, o la factura se borro en Odoo.",
        """
        SELECT o.so_id, o.monto_total, o.monto_facturado, o.factura_id
        FROM ordenes_venta o
        WHERE o.facturada
          AND NOT EXISTS (SELECT 1 FROM facturas f WHERE f.so_id = o.so_id)
        ORDER BY o.monto_total DESC
        """,
    ),
    Chequeo(
        "orden_sin_lineas",
        "estados",
        "MEDIA",
        "Orden con monto y sin una sola linea. El teorico no se puede calcular: "
        "sale cero, y cero significa cobrada.",
        """
        SELECT o.so_id, o.monto_total, o.estado_orden
        FROM ordenes_venta o
        WHERE o.monto_total > 0
          AND NOT EXISTS (SELECT 1 FROM lineas_orden l WHERE l.so_id = o.so_id)
        ORDER BY o.monto_total DESC
        """,
    ),
    Chequeo(
        "devuelto_supera_lo_entregado",
        "estados",
        "ALTA",
        "Volvio mas mercancia de la que la linea dice haber enviado: "
        "``cantidad_entregada`` quedo NEGATIVA. Pasa cuando sacan el producto de "
        "la orden (cantidad a cero) despues de que la devolucion ya entro, asi "
        "que el deposito recibio unidades contra una linea que ya no reclama "
        "haber vendido nada. Medido en el Odoo de prueba: 2 casos, S00952 con -4 "
        "unidades y S00925 con -10. NO se prohibe con una restriccion de base a "
        "proposito -- es como Odoo lo representa, y prohibirlo tumbaria el sync "
        "(ver la migracion f1e2d3c4b5a6).",
        """
        SELECT l.so_id, l.linea_id, l.nombre, l.cantidad AS pedida,
               l.cantidad_entregada AS entregada, o.tiene_devolucion, o.monto_total
        FROM lineas_orden l JOIN ordenes_venta o ON o.so_id = l.so_id
        WHERE l.cantidad_entregada < 0
        ORDER BY l.cantidad_entregada
        """,
    ),
    Chequeo(
        "entregado_supera_lo_pedido",
        "estados",
        "ALTA",
        "Salio mas mercancia de la que la orden pide. Es el espejo del caso de "
        "nota de credito pendiente -- pasa cuando sacan un producto de una "
        "orden ya entregada sin hacer la devolucion -- y hoy no lo mira nadie.",
        """
        SELECT l.so_id, l.producto, l.cantidad AS pedida, l.cantidad_entregada AS entregada,
               l.cantidad_entregada - l.cantidad AS exceso
        FROM lineas_orden l
        WHERE l.cantidad_entregada > l.cantidad + 0.001
        ORDER BY 5 DESC
        """,
    ),
    Chequeo(
        "teorico_en_cero_sin_explicacion",
        "estados",
        "ALTA",
        "La orden tiene lineas, NO tiene devolucion, y su teorico calculado es "
        "cero. Un teorico en cero saca la orden de la cuenta por cobrar sin que "
        "nadie cobre nada. Se excluyen las devueltas por completo porque ahi el "
        "cero es correcto -- la mercancia volvio y no hay nada que cobrar; esas "
        "las mira ``devuelta_completa_con_factura_viva``. Separarlas importa: si "
        "no, el chequeo mezcla 'no pude calcular' con 'calcule cero bien', que es "
        "justo la distincion que este blindaje persigue.",
        """
        SELECT t.so_id, t.teorico_ves, t.teorico_usd, o.monto_total,
               t.usa_fallback_ves, t.usa_fallback_usd, o.tiene_devolucion,
               (SELECT count(*) FROM lineas_orden l WHERE l.so_id = t.so_id) AS lineas
        FROM ventas_teoricos t JOIN ordenes_venta o ON o.so_id = t.so_id
        WHERE t.teorico_ves <= 0 AND t.teorico_usd <= 0
          AND EXISTS (SELECT 1 FROM lineas_orden l WHERE l.so_id = t.so_id)
          AND NOT o.tiene_devolucion
        ORDER BY o.monto_total DESC
        """,
    ),
    Chequeo(
        "devuelta_completa_con_factura_viva",
        "estados",
        "ALTA",
        "Toda la mercancia volvio (cantidad_entregada en cero en cada linea, con "
        "devolucion marcada) y la factura sigue posteada. El teorico es cero, que "
        "es correcto, pero la factura le sigue pidiendo la plata al cliente: es "
        "una nota de credito pendiente. Medido en el Odoo de prueba: 4 ordenes "
        "asi, y solo UNA estaba en la bandeja de facturacion.",
        """
        SELECT o.so_id, o.monto_total,
               (SELECT coalesce(sum(f.monto_total_signed_usd), 0) FROM facturas f
                 WHERE f.so_id = o.so_id AND f.estado = 'posted') AS facturado_vivo,
               EXISTS (SELECT 1 FROM bandeja_facturacion b WHERE b.so_id = o.so_id)
                 AS en_bandeja
        FROM ordenes_venta o
        WHERE o.tiene_devolucion AND o.entregada_completa AND o.facturada
          AND EXISTS (SELECT 1 FROM lineas_orden l WHERE l.so_id = o.so_id)
          AND NOT EXISTS (
                SELECT 1 FROM lineas_orden l
                WHERE l.so_id = o.so_id AND l.cantidad_entregada > 0)
          AND (SELECT coalesce(sum(f.monto_total_signed_usd), 0) FROM facturas f
                WHERE f.so_id = o.so_id AND f.estado = 'posted') > 0.01
        ORDER BY 3 DESC
        """,
    ),
    Chequeo(
        "facturas_borrador_en_el_espejo",
        "espejos",
        "BAJA",
        "Facturas en borrador que el espejo trae a proposito (para reflejarlas en "
        "cuanto se crean). No son un defecto: son una trampa. Cualquier consumidor "
        "que sume ``monto_total_signed_usd`` sin filtrar por ``estado = 'posted'`` "
        "las cuenta como facturado real. Se reproduce facil -- me paso al medir el "
        "caso S00161, que parecia facturada al doble de su orden y era una posteada "
        "mas un borrador.",
        """
        SELECT count(*) AS borradores, sum(monto_total_signed_usd) AS monto_en_borrador
        FROM facturas WHERE estado = 'draft'
        HAVING count(*) > 0
        """,
    ),
    Chequeo(
        "teorico_por_formula",
        "estados",
        "ALTA",
        "Teoricos que se valoraron por la formula de fallback y no por el precio "
        "de la lista. Es la medicion que faltaba para decidir si el "
        "OdooPriceResolver sin calibrar para Odoo 18 es urgente.",
        """
        SELECT t.so_id, o.lista_precios, t.teorico_ves, t.teorico_usd,
               t.usa_fallback_ves, t.usa_fallback_usd
        FROM ventas_teoricos t JOIN ordenes_venta o ON o.so_id = t.so_id
        WHERE t.usa_fallback_ves OR t.usa_fallback_usd
        ORDER BY greatest(t.teorico_usd, t.teorico_ves) DESC
        """,
    ),
]


PREFIJO_DE_PRUEBA = "ZZ BLINDAJE"


def _excluir_datos_de_prueba(con) -> None:
    """Crea vistas temporales que tapan las tablas con su version sin pruebas.

    Se hace con vistas y no reescribiendo los 34 SQL: cada chequeo sigue
    diciendo ``FROM ordenes_venta`` y la vista decide que hay ahi. Son
    temporales, asi que viven lo que dura la conexion y no tocan la base.
    """
    import sqlalchemy as sa

    # El prefijo va inline y no como parametro: Postgres no puede inferir el
    # tipo de un parametro dentro de un CREATE VIEW. Es una constante del
    # modulo, no entrada de nadie.
    con.execute(
        sa.text(
            "CREATE TEMP VIEW clientes_reales AS "
            f"SELECT * FROM clientes WHERE nombre NOT LIKE '{PREFIJO_DE_PRUEBA}%'"
        )
    )
    con.execute(
        sa.text(
            "CREATE TEMP VIEW ordenes_venta AS SELECT o.* FROM public.ordenes_venta o "
            "JOIN clientes_reales c ON c.cliente_id = o.cliente_id"
        )
    )
    con.execute(
        sa.text(
            "CREATE TEMP VIEW clientes AS SELECT * FROM clientes_reales"
        )
    )
    con.execute(
        sa.text(
            "CREATE TEMP VIEW pagos AS SELECT p.* FROM public.pagos p "
            "JOIN clientes_reales c ON c.cliente_id = p.cliente_id"
        )
    )
    con.execute(
        sa.text(
            "CREATE TEMP VIEW facturas AS SELECT * FROM public.facturas "
            "WHERE numero NOT LIKE 'ZZPRU%' AND numero NOT LIKE 'NC-ZZPRU%'"
        )
    )


def cargar_env(ruta: Path | None) -> None:
    if ruta is None:
        ruta = RAIZ / ".env"
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, _, valor = linea.partition("=")
            os.environ[clave.strip()] = valor.strip()


def main() -> int:
    import sqlalchemy as sa

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=None, help="archivo de entorno a cargar")
    parser.add_argument("--detalle", action="store_true", help="imprime las filas encontradas")
    parser.add_argument("--max-filas", type=int, default=8, help="filas por chequeo en --detalle")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--familia", default=None)
    parser.add_argument(
        "--sin-pruebas",
        action="store_true",
        help="excluye los datos que deja el banco de escenarios (clientes ZZ BLINDAJE)",
    )
    args = parser.parse_args()

    cargar_env(args.env)
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        sys.exit("Falta DATABASE_URL.")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)

    chequeos = [c for c in CHEQUEOS if not args.familia or c.familia == args.familia]
    motor = sa.create_engine(url)
    resultados = []
    con_filas_altas = 0

    with motor.connect() as con:
        if args.sin_pruebas:
            # El banco de escenarios deja ordenes y facturas en el espejo de QA
            # -- Odoo no permite borrar una orden confirmada ni una factura
            # posteada -- y esas filas contaminan los conteos: la primera
            # medicion de ``orden_cancelada_con_entrega`` dio 16 y la segunda
            # 18, y las dos de mas eran mias. Con esta bandera los chequeos
            # miran solo los datos que vinieron de produccion.
            _excluir_datos_de_prueba(con)

        for chequeo in chequeos:
            try:
                filas = con.execute(sa.text(chequeo.sql)).mappings().all()
            except Exception as exc:  # noqa: BLE001 -- se reporta, no se traga
                resultados.append(
                    {
                        "nombre": chequeo.nombre,
                        "familia": chequeo.familia,
                        "severidad": chequeo.severidad,
                        "estado": "ERROR",
                        "filas": 0,
                        "error": str(exc)[:300],
                    }
                )
                continue
            n = len(filas)
            if n and chequeo.severidad == "ALTA":
                con_filas_altas += 1
            resultados.append(
                {
                    "nombre": chequeo.nombre,
                    "familia": chequeo.familia,
                    "severidad": chequeo.severidad,
                    "estado": "LIMPIO" if n == 0 else "HALLAZGOS",
                    "filas": n,
                    "porque": chequeo.porque,
                    "muestra": [
                        {k: (str(v) if v is not None else None) for k, v in f.items()}
                        for f in filas[: args.max_filas]
                    ],
                }
            )

    ancho = max(len(c.nombre) for c in chequeos) + 2
    print(f"{'chequeo':<{ancho}} {'sev':<6} {'estado':<10} {'filas':>7}")
    print("-" * (ancho + 26))
    for r in resultados:
        marca = "  " if r["estado"] == "LIMPIO" else "! "
        print(
            f"{marca}{r['nombre']:<{ancho - 2}} {r['severidad']:<6} "
            f"{r['estado']:<10} {r['filas']:>7}"
        )
        if args.detalle and r["estado"] == "HALLAZGOS":
            print(f"    {r['porque']}")
            for fila in r["muestra"]:
                print("      " + "  ".join(f"{k}={v}" for k, v in fila.items()))
            if r["filas"] > args.max_filas:
                print(f"      ... y {r['filas'] - args.max_filas} mas")
            print()

    print("-" * (ancho + 26))
    limpios = sum(1 for r in resultados if r["estado"] == "LIMPIO")
    print(f"{limpios}/{len(resultados)} limpios; {con_filas_altas} chequeo(s) ALTA con hallazgos")

    if args.json:
        args.json.write_text(
            json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Detalle en {args.json}")

    return 1 if con_filas_altas else 0


if __name__ == "__main__":
    sys.exit(main())
