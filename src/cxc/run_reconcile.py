"""Entrypoint: conciliación motor vs Odoo → semáforo. ``python -m cxc.run_reconcile``."""

from __future__ import annotations

import logging


def main() -> None:  # pragma: no cover - wiring de producción (red)
    from .config import AppConfig
    from .odoo.client import _connect
    from .reconciliation.reconcile import OdooFacturasReader, Reconciler
    from .sheets.gateway import GspreadGateway
    from .sheets.repository import SheetsRepository

    logging.basicConfig(level=logging.INFO)
    config = AppConfig.from_env()
    import os

    if os.environ.get("GOOGLE_TOKEN_JSON"):
        gateway = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
    else:
        gateway = GspreadGateway(
            config.sheets.spreadsheet_id, config.sheets.service_account_file
        )

    repo = SheetsRepository(gateway)
    facturas = OdooFacturasReader(_connect(config.odoo))
    resultados = Reconciler(repo, facturas, config.reconciliation).run()
    rojos = sum(1 for c in resultados if c.resultado.value == "rojo")
    logging.getLogger("cxc").info(
        "Conciliación: %s órdenes, %s rojas", len(resultados), rojos
    )


if __name__ == "__main__":  # pragma: no cover
    main()
