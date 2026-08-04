"""Entrypoint: motor de descuentos → BandejaFacturacion. ``python -m cxc.run_engine``.

El mapeo de nombre lógico de lista (USD/BCV) → id de pricelist de Odoo se toma de
las env vars ``ODOO_PRICELIST_USD`` y ``ODOO_PRICELIST_BCV`` (ver SETUP.md).
"""

from __future__ import annotations

import logging
import os
from datetime import date


def main() -> None:  # pragma: no cover - wiring de producción (red)
    from .config import AppConfig
    from .engine.runner import EngineRunner
    from .odoo.client import OdooXmlRpcReader, _connect
    from .odoo.price import OdooPriceResolver
    from .sheets.gateway import GspreadGateway
    from .sheets.repository import SheetsRepository

    logging.basicConfig(level=logging.INFO)
    config = AppConfig.from_env()

    if os.environ.get("GOOGLE_TOKEN_JSON"):
        gateway = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
    else:
        gateway = GspreadGateway(config.sheets.spreadsheet_id, config.sheets.service_account_file)

    repo = SheetsRepository(gateway)
    execute = _connect(config.odoo)
    # Mantener viva la referencia al reader no es necesario; usamos execute directo.
    _ = OdooXmlRpcReader
    usd_id = int(os.environ["ODOO_PRICELIST_USD"])
    ves_id = int(os.environ["ODOO_PRICELIST_BCV"])
    # "USD"/"BCV": mismos nombres lógicos de fallback que usa el motor
    # (engine/discounts.py) cuando EngineInputs no trae valid_usd/valid_ves
    # poblados -- este entrypoint standalone no los puebla.
    pricelist_ids = {"USD": usd_id, "BCV": ves_id}
    resolver = OdooPriceResolver(execute, pricelist_ids, fallback_pricelist_ids=[usd_id, ves_id])
    runner = EngineRunner(repo, resolver, config.engine)
    resultados = runner.run_all(date.today())
    logging.getLogger("cxc").info("Motor: %s órdenes calculadas", len(resultados))


if __name__ == "__main__":  # pragma: no cover
    main()
