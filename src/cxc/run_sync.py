"""Entrypoint: sync incremental delta Odoo → Sheets. ``python -m cxc.run_sync``."""

from __future__ import annotations

import logging
from datetime import datetime


def main() -> None:  # pragma: no cover - wiring de producción (red)
    from .config import AppConfig
    from .odoo.client import OdooXmlRpcReader
    from .sheets.gateway import GspreadGateway
    from .sheets.repository import SheetsRepository
    from .sync.incremental import IncrementalSync

    logging.basicConfig(level=logging.INFO)
    config = AppConfig.from_env()
    repo = SheetsRepository(
        GspreadGateway(
            config.sheets.spreadsheet_id, config.sheets.service_account_file
        )
    )
    sync = IncrementalSync(repo, OdooXmlRpcReader(config.odoo))
    result = sync.run(datetime.now())
    logging.getLogger("cxc").info("Sync delta: %s filas", result.total)


if __name__ == "__main__":  # pragma: no cover
    main()
