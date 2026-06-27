"""Entrypoint: scraper de tasas horario. Uso: ``python -m cxc.run_scraper``.

Pensado para correr cada hora en Railway (cron/scheduler).
"""

from __future__ import annotations

import logging
from datetime import datetime


def main() -> None:  # pragma: no cover - wiring de producción (red)
    from .alerts import build_alerter
    from .config import AppConfig
    from .scraper.bcv import BcvClient
    from .scraper.binance import BinanceClient
    from .scraper.rates_scraper import RatesScraper
    from .sheets.gateway import GspreadGateway
    from .sheets.repository import SheetsRepository

    logging.basicConfig(level=logging.INFO)
    config = AppConfig.from_env()
    repo = SheetsRepository(
        GspreadGateway(
            config.sheets.spreadsheet_id, config.sheets.service_account_file
        )
    )
    scraper = RatesScraper(
        repo,
        BinanceClient(config.binance),
        BcvClient(config.bcv),
        build_alerter(config.alert),
        config.scraper_policy,
    )
    fila = scraper.run(datetime.now())
    logging.getLogger("cxc").info(
        "SerieTasas += %s (bcv=%s binance=%s heredada=%s)",
        fila.timestamp, fila.tasa_bcv, fila.tasa_binance, fila.es_heredada,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
