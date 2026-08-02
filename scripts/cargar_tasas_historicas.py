"""Siembra/actualiza ``TasasHistoricasAuditoria`` (tasas BCV/Binance diarias).

Uso:
  python scripts/cargar_tasas_historicas.py
  python scripts/cargar_tasas_historicas.py --start-date 2026-01-01
  python scripts/cargar_tasas_historicas.py --dry-run
  python scripts/cargar_tasas_historicas.py --database-url "postgresql://..."

Genera una fila por día calendario (desde ``--start-date``, default
2026-01-01, hasta hoy) con la tasa BCV oficial de Odoo (``res.currency.rate``,
``inverse_company_rate`` para USD/EUR) y el promedio de Binance capturado ese
día en ``SerieTasas`` -- si no hay captura real para un día, se estima con el
diferencial de mercado histórico (17.2%) sobre el BCV de ese día, dejando
constancia en la columna ``notas``.

Esta tabla es la fuente que usa ``cxc.web.app.get_binance_rate_for_date``
para convertir pagos VES a USD con la tasa Binance del día EXACTO del pago
(``SerieTasas`` solo cubre lo que el scraper horario capturó en vivo -- huecos
de días sin cron, downtime, o fechas anteriores a que el scraper existiera
quedan sin dato ahí). Reejecutable: reemplaza la tabla completa cada vez a
partir de las mismas fuentes autoritativas (Odoo + SerieTasas), no acumula
filas duplicadas.

Backend destino: ``--database-url`` (o la variable de entorno DATABASE_URL)
escribe en Postgres; si ninguna está presente, escribe en Google Sheets
(mismas credenciales que usa la app hoy).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cxc.config import AppConfig  # noqa: E402
from cxc.db.postgres_repository import PostgresRepository  # noqa: E402
from cxc.odoo.client import _connect  # noqa: E402
from cxc.repositories import Repository  # noqa: E402
from cxc.sheets.gateway import GspreadGateway  # noqa: E402
from cxc.sheets.repository import SheetsRepository  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cxc.cargar_tasas_historicas")

_DIFERENCIAL_MERCADO_DEFAULT = 1.172  # 17.2% -- mismo heurístico ya usado en producción.


def _build_repo(config: AppConfig, database_url: str | None) -> Repository:
    if database_url:
        return PostgresRepository.from_url(database_url)
    if (
        os.environ.get("GOOGLE_TOKEN_JSON")
        or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        or os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    ):
        gateway = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
    else:
        gateway = GspreadGateway(config.sheets.spreadsheet_id, config.sheets.service_account_file)
    return SheetsRepository(gateway)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(
        description="Siembra TasasHistoricasAuditoria desde Odoo + SerieTasas."
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default="2026-01-01",
        help="Primer día a generar (YYYY-MM-DD). Default: 2026-01-01.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calcula y muestra el resumen sin escribir nada.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Si se pasa (o está en DATABASE_URL), escribe en Postgres en vez de Sheets.",
    )
    args = parser.parse_args()
    start_d = date.fromisoformat(args.start_date)
    end_d = date.today()

    config = AppConfig.from_env()
    logger.info("Conectando a Odoo...")
    execute = _connect(config.odoo)
    database_url = args.database_url or os.environ.get("DATABASE_URL")
    logger.info("Conectando a %s...", "Postgres" if database_url else "Google Sheets")
    repo = _build_repo(config, database_url)

    rates_odoo = (
        execute(
            "res.currency.rate",
            "search_read",
            [[["name", ">=", start_d.isoformat()], ["currency_id", "in", [1, 125]]]],
            {"fields": ["name", "currency_id", "inverse_company_rate"], "order": "name asc"},
        )
        if execute
        else []
    )

    bcv_usd_map: dict[str, float] = {}
    bcv_eur_map: dict[str, float] = {}
    for r in rates_odoo:
        d_str = str(r.get("name", ""))[:10]
        curr = r.get("currency_id")
        c_id = curr[0] if isinstance(curr, list | tuple) else curr
        val = r.get("inverse_company_rate")
        if d_str and val:
            if c_id == 1:
                bcv_usd_map[d_str] = float(val)
            elif c_id == 125:
                bcv_eur_map[d_str] = float(val)

    binance_by_date: dict[str, list[float]] = {}
    for s in repo.all_serie_tasas():
        d_str = s.timestamp.date().isoformat()
        if s.tasa_binance and s.tasa_binance > 0:
            binance_by_date.setdefault(d_str, []).append(float(s.tasa_binance))
    binance_avg_captured = {d: sum(vals) / len(vals) for d, vals in binance_by_date.items()}

    table_rows: list[dict[str, str]] = []

    if not bcv_usd_map:
        logger.warning(
            "Sin tasas BCV de Odoo (res.currency.rate) para el rango -- "
            "verificar conexión a Odoo. No se generará ningún día."
        )
    else:
        curr_d = start_d
        primer_usd = bcv_usd_map.get(start_d.isoformat())
        last_usd = primer_usd if primer_usd is not None else next(iter(bcv_usd_map.values()))
        primer_eur = bcv_eur_map.get(start_d.isoformat())
        last_eur = primer_eur if primer_eur is not None else next(iter(bcv_eur_map.values()), 0.0)

        while curr_d <= end_d:
            d_str = curr_d.isoformat()
            usd = bcv_usd_map.get(d_str, last_usd)
            eur = bcv_eur_map.get(d_str, last_eur)

            # Los valores se escriben como str (no float/round nativo): el locale del
            # spreadsheet es es_ES (coma decimal), y escribir un NUMBER nativo hace que
            # el valor se lea luego con coma decimal y gspread lo "numericise" quitando
            # la coma como si fuera separador de miles (451,5072 -> 4515072).
            if d_str in binance_avg_captured:
                binance = round(binance_avg_captured[d_str], 4)
                notas = "Binance capturado (SerieTasas)"
            else:
                binance = round(usd * _DIFERENCIAL_MERCADO_DEFAULT, 4)
                notas = f"Binance estimado (diferencial {_DIFERENCIAL_MERCADO_DEFAULT})"

            diff_pct = round(((binance - usd) / binance) * 100, 2) if binance else 0.0

            table_rows.append(
                {
                    "fecha": d_str,
                    "tasa_bcv_usd": str(round(usd, 4)),
                    "tasa_bcv_euro": str(round(eur, 4)),
                    "tasa_binance_promedio_diario": str(binance),
                    "diferencial_bcv_binance_pct": f"{diff_pct}%",
                    "fuente": "Odoo res.currency.rate / SerieTasas",
                    "notas": notas,
                }
            )

            last_usd = usd
            last_eur = eur
            curr_d += timedelta(days=1)

    logger.info(
        "Días generados (%s a %s): %d", start_d.isoformat(), end_d.isoformat(), len(table_rows)
    )

    if args.dry_run:
        logger.info("DRY-RUN: no se escribió nada.")
        return

    repo.replace_tasas_historicas_auditoria(table_rows)
    logger.info("Listo: TasasHistoricasAuditoria actualizada (%d filas).", len(table_rows))


if __name__ == "__main__":
    main()
