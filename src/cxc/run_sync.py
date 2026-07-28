"""Entrypoint: sync incremental delta Odoo → Sheets. ``python -m cxc.run_sync``."""

from __future__ import annotations

import logging
from datetime import datetime


def main() -> None:  # pragma: no cover - wiring de producción (red)
    import os

    from .config import AppConfig
    from .odoo.client import OdooXmlRpcReader
    from .sheets.gateway import GspreadGateway
    from .sheets.repository import SheetsRepository
    from .sync.incremental import IncrementalSync

    logging.basicConfig(level=logging.INFO)
    config = AppConfig.from_env()

    if (
        os.environ.get("GOOGLE_TOKEN_JSON")
        or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        or os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    ):
        gateway = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
    else:
        gateway = GspreadGateway(config.sheets.spreadsheet_id, config.sheets.service_account_file)

    repo = SheetsRepository(gateway)
    sync = IncrementalSync(repo, OdooXmlRpcReader(config.odoo))
    result = sync.run(datetime.now())
    logging.getLogger("cxc").info("Sync delta: %s filas", result.total)


if __name__ == "__main__":  # pragma: no cover
    main()
