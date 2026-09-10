#!/usr/bin/env python3
"""Corrida diaria de vigilancia (Fase 2.3 del plan de blindaje).

POR QUE EXISTE

Hoy no hay una sola alerta. El balance de comprobacion se calcula cuando
alguien abre la pagina; si manana se descuadra en diez mil dolares, nadie se
entera hasta que alguien entra. Todo lo que la auditoria de septiembre 2026
encontro llevaba meses en un sistema que tambien cuadraba.

Esto invierte la relacion: el sistema avisa, en vez de esperar a que alguien
mire.

QUE EVALUA

Tres bloques, los tres reejecutables por separado:

1. **Integridad** -- los 34 chequeos de ``auditar_integridad``: huerfanos,
   duplicados por clave natural, dinero imposible, fechas absurdas, estados
   imposibles.
2. **Conciliacion** -- las 9 partidas de ``conciliar_espejo_odoo``: por cada
   tabla del espejo, cuantos registros y que suma hay de cada lado. Es lo
   unico que detecta una orden que Odoo borro y nosotros seguimos contando.
3. **Invariantes de dinero** -- las propiedades que tienen que ser ciertas por
   aritmetica, no por acuerdo entre dos vistas.

QUE NO EVALUA, Y POR QUE

El balance de comprobacion de la pagina de auditoria **no** se evalua aca. Su
endpoint exige sesion (cookie ``cxc_session``) y la API no tiene tokens, asi
que un cron no puede consultarlo sin la contrasena de un humano. Su sustancia
igual queda cubierta: sus 8 partidas externas comparan el espejo contra Odoo, y
el bloque 2 hace esa comparacion por tabla COMPLETA, que es mas amplio. Lo que
se pierde son las 9 partidas internas, que por construccion no pueden detectar
un numero mal calculado -- solo que dos paginas se contradigan.

Dar a la API un token de servicio para que el cron mire el balance de verdad es
trabajo aparte, y esta anotado como tal.

CADA CUANTO CORRERLO

Va como cron y no como tarea del proceso web, por el mismo motivo que el
verificador de tasas: una consulta lenta a Odoo no debe degradar la aplicacion.

En Railway, "New > Cron Job" sobre este repo, una vez al dia:

    PYTHONPATH=src python scripts/vigilancia_diaria.py --alertar

Uso:
  python scripts/vigilancia_diaria.py                   # reporta en consola
  python scripts/vigilancia_diaria.py --alertar         # + Telegram si hay credenciales
  python scripts/vigilancia_diaria.py --env .env.qa     # contra el espejo de QA
  python scripts/vigilancia_diaria.py --sin-odoo        # solo integridad e invariantes

Devuelve 0 si no hay nada de severidad ALTA, y 1 si hay -- asi sirve de
chequeo que falla ruidoso incluso sin canal de alertas.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ / "scripts"))

# Los catalogos se importan, no se copian: un chequeo que se agregue a
# ``auditar_integridad`` entra a la vigilancia diaria sin tocar este archivo.
from auditar_integridad import CHEQUEOS, cargar_env  # noqa: E402
from conciliar_espejo_odoo import PARTIDAS, conciliar  # noqa: E402


@dataclass
class Hallazgo:
    bloque: str
    nombre: str
    severidad: str
    detalle: str


@dataclass
class Informe:
    hallazgos: list[Hallazgo] = field(default_factory=list)
    evaluados: int = 0
    saltados: list[str] = field(default_factory=list)

    @property
    def altas(self) -> list[Hallazgo]:
        return [h for h in self.hallazgos if h.severidad == "ALTA"]

    @property
    def limpio(self) -> bool:
        return not self.hallazgos


# --- las invariantes de dinero ---------------------------------------------
#
# La diferencia con los chequeos de integridad es de naturaleza, no de grado:
# una invariante tiene que ser cierta POR ARITMETICA. No existe un par de
# numeros mal calculados que la haga pasar, que es justamente lo que le falta a
# una comparacion entre dos vistas de la misma funcion.
INVARIANTES: list[tuple[str, str, str]] = [
    (
        "el equivalente en dolares nunca supera el nominal en bolivares",
        "Con la tasa por encima de 1 es imposible. Delata una tasa congelada al "
        "reves o en la unidad equivocada.",
        """
        SELECT count(*) AS filas FROM vinculaciones
        WHERE moneda_abono = 'VES'
          AND greatest(coalesce(equiv_usd_bcv, 0), coalesce(equiv_usd_binance, 0))
              > monto_aplicado
        """,
    ),
    (
        "lo aplicado de un pago nunca supera el pago",
        "Se estaria aplicando plata que no entro.",
        """
        SELECT count(*) AS filas FROM (
            SELECT v.pago_id FROM vinculaciones v JOIN pagos p ON p.pago_id = v.pago_id
            GROUP BY v.pago_id, p.monto HAVING sum(v.monto_aplicado) > p.monto + 0.01
        ) x
        """,
    ),
    (
        "toda vinculacion tiene pago y orden existentes",
        "Una vinculacion huerfana es plata aplicada a la nada: sigue sumando en los "
        "reportes que agregan por vinculacion y desaparece en los que agregan por orden.",
        """
        SELECT count(*) AS filas FROM vinculaciones v
        WHERE NOT EXISTS (SELECT 1 FROM pagos p WHERE p.pago_id = v.pago_id)
           OR NOT EXISTS (SELECT 1 FROM ordenes_venta o WHERE o.so_id = v.so_id)
        """,
    ),
    (
        "ningun saldo teorico es negativo",
        "Un teorico negativo no existe: o el precio salio mal o las cantidades.",
        """
        SELECT count(*) AS filas FROM ventas_teoricos
        WHERE teorico_ves < 0 OR teorico_usd < 0
           OR descuentos_teorico_ves < 0 OR descuentos_teorico_usd < 0
        """,
    ),
    (
        "ningun descuento supera su propio teorico",
        "Un descuento mayor que la venta deja la orden en negativo.",
        """
        SELECT count(*) AS filas FROM ventas_teoricos
        WHERE descuentos_teorico_ves > teorico_ves + 0.01
           OR descuentos_teorico_usd > teorico_usd + 0.01
        """,
    ),
    (
        "toda orden con lineas tiene teorico distinto de cero",
        "Un teorico en cero saca la orden de la cuenta por cobrar sin que nadie cobre. "
        "Es la forma que toma 'sin datos no es cero'.",
        """
        SELECT count(*) AS filas FROM ventas_teoricos t
        WHERE t.teorico_ves <= 0 AND t.teorico_usd <= 0
          AND EXISTS (SELECT 1 FROM lineas_orden l WHERE l.so_id = t.so_id)
        """,
    ),
]


def _motor():
    import sqlalchemy as sa

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        sys.exit("Falta DATABASE_URL.")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return sa.create_engine(url)


def evaluar_integridad(con, informe: Informe) -> None:
    import sqlalchemy as sa

    for chequeo in CHEQUEOS:
        informe.evaluados += 1
        try:
            filas = con.execute(sa.text(chequeo.sql)).mappings().all()
        except Exception as exc:  # noqa: BLE001 -- se reporta, no se traga
            informe.hallazgos.append(
                Hallazgo("integridad", chequeo.nombre, "ALTA", f"el chequeo fallo: {exc}"[:200])
            )
            continue
        if filas:
            informe.hallazgos.append(
                Hallazgo(
                    "integridad",
                    chequeo.nombre,
                    chequeo.severidad,
                    f"{len(filas)} fila(s). {chequeo.porque.split('.')[0]}.",
                )
            )


def evaluar_invariantes(con, informe: Informe) -> None:
    import sqlalchemy as sa

    for nombre, porque, sql in INVARIANTES:
        informe.evaluados += 1
        try:
            filas = int(con.execute(sa.text(sql)).scalar() or 0)
        except Exception as exc:  # noqa: BLE001
            informe.hallazgos.append(
                Hallazgo("invariantes", nombre, "ALTA", f"no se pudo evaluar: {exc}"[:200])
            )
            continue
        if filas:
            informe.hallazgos.append(
                Hallazgo("invariantes", nombre, "ALTA", f"{filas} caso(s) la violan. {porque}")
            )


def evaluar_conciliacion(con, informe: Informe) -> None:
    from cxc.config import AppConfig
    from cxc.odoo.client import _connect

    ejecutar = _connect(AppConfig.from_env().odoo)
    if not ejecutar:
        informe.saltados.append("conciliacion contra Odoo (sin conexion)")
        return
    for partida in PARTIDAS:
        informe.evaluados += 1
        resultado = conciliar(partida, ejecutar, con, max_nominal=8)
        if resultado.get("estado") == "ERROR_ODOO":
            informe.hallazgos.append(
                Hallazgo(
                    "conciliacion",
                    partida.nombre,
                    "ALTA",
                    f"no se pudo leer de Odoo: {resultado.get('error', '')[:150]}",
                )
            )
            continue
        if resultado.get("estado") == "CUADRA":
            continue
        partes = []
        if resultado["faltan_en_el_espejo"]:
            faltan = ", ".join(resultado["nominal_faltan"][:6])
            partes.append(
                f"{resultado['faltan_en_el_espejo']} en Odoo y no en el espejo ({faltan})"
            )
        if resultado["sobran_en_el_espejo"]:
            sobran = ", ".join(resultado["nominal_sobran"][:6])
            partes.append(
                f"{resultado['sobran_en_el_espejo']} en el espejo y no en Odoo ({sobran})"
            )
        if resultado.get("montos_que_difieren"):
            partes.append(
                f"{resultado['montos_que_difieren']} monto(s) distintos, "
                f"dif total {resultado.get('dif_suma')}"
            )
        informe.hallazgos.append(
            Hallazgo("conciliacion", partida.nombre, "ALTA", "; ".join(partes))
        )


def texto_del_informe(informe: Informe, para_telegram: bool = False) -> str:
    if informe.limpio:
        cuerpo = f"CxC — vigilancia diaria: sin hallazgos ({informe.evaluados} chequeos)."
        if informe.saltados:
            cuerpo += " No evaluado: " + ", ".join(informe.saltados) + "."
        return cuerpo

    lineas = [
        f"CxC — vigilancia diaria: {len(informe.hallazgos)} hallazgo(s) "
        f"({len(informe.altas)} de severidad ALTA) sobre {informe.evaluados} chequeos."
    ]
    for bloque in ("invariantes", "conciliacion", "integridad"):
        del_bloque = [h for h in informe.hallazgos if h.bloque == bloque]
        if not del_bloque:
            continue
        lineas.append("")
        lineas.append(f"{bloque.upper()}")
        # Las ALTA primero: son las que hay que mirar hoy.
        for h in sorted(del_bloque, key=lambda x: {"ALTA": 0, "MEDIA": 1, "BAJA": 2}[x.severidad]):
            lineas.append(f"  [{h.severidad}] {h.nombre}: {h.detalle}")
    if informe.saltados:
        lineas.append("")
        lineas.append("No evaluado: " + ", ".join(informe.saltados))
    texto = "\n".join(lineas)
    # Telegram corta en 4096 caracteres y prefiere avisar de mas que de menos.
    if para_telegram and len(texto) > 3800:
        texto = texto[:3800] + "\n… (recortado; ver la salida completa del cron)"
    return texto


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=None)
    parser.add_argument("--alertar", action="store_true", help="manda el informe por Telegram")
    parser.add_argument(
        "--sin-odoo", action="store_true", help="salta la conciliacion contra Odoo"
    )
    args = parser.parse_args()

    cargar_env(args.env)
    informe = Informe()
    motor = _motor()
    with motor.connect() as con:
        evaluar_invariantes(con, informe)
        evaluar_integridad(con, informe)
        if args.sin_odoo:
            informe.saltados.append("conciliacion contra Odoo (--sin-odoo)")
        else:
            evaluar_conciliacion(con, informe)

    texto = texto_del_informe(informe)
    print(texto)

    if args.alertar:
        from cxc.alerts import build_alerter
        from cxc.config import AppConfig

        alertador = build_alerter(AppConfig.from_env().alerts)
        # Se avisa SIEMPRE, tambien cuando esta limpio. Un canal que solo habla
        # cuando hay problemas es indistinguible de un canal roto: el dia que el
        # cron se cae, el silencio se lee como "todo bien".
        alertador.send(texto_del_informe(informe, para_telegram=True))

    return 1 if informe.altas else 0


if __name__ == "__main__":
    sys.exit(main())
