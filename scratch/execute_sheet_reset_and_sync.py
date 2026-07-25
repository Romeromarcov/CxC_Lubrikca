import sys
import os
import json
import subprocess
import logging
from datetime import datetime

sys.path.insert(0, 'src')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cxc.full_sync")

res = subprocess.run('railway variables --json', shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    data = json.loads(res.stdout.decode('utf-8', errors='ignore'))
    for k, v in data.items():
        if isinstance(v, str):
            os.environ[k] = v
except Exception as e:
    logger.error(f"Error cargando variables de Railway: {e}")

from cxc.config import AppConfig
from cxc.sheets.gateway import GspreadGateway
from cxc.sheets.repository import SheetsRepository
from cxc.odoo.client import OdooXmlRpcReader
from cxc.sync.incremental import IncrementalSync

def main():
    config = AppConfig.from_env()
    gw = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)

    # 1. Reset last_sync in _Meta so sync pulls everything from Odoo
    try:
        gw.set_meta("last_sync", "")
        logger.info("Reseteada la marca 'last_sync' en _Meta a vacío.")
    except Exception as e:
        logger.warning(f"No se pudo resetear last_sync: {e}")

    # 2. Run sync with since=None
    logger.info("=== SINCRONIZANDO DE FORMA COMPLETA DIRECTAMENTE DESDE ODOO PRODUCCIÓN ===")
    repo = SheetsRepository(gw)
    odoo_reader = OdooXmlRpcReader(config.odoo)
    
    sync = IncrementalSync(repo, odoo_reader)
    now = datetime.now()
    
    clientes = odoo_reader.changed_clientes(None)
    ordenes = odoo_reader.changed_ordenes(None)
    lineas = odoo_reader.changed_lineas(None)
    pagos = odoo_reader.changed_pagos(None)
    
    logger.info(f"Odoo Producción retornó: {len(clientes)} clientes, {len(ordenes)} órdenes, {len(lineas)} líneas de órdenes, {len(pagos)} pagos.")
    
    logger.info("Escribiendo datos frescos en Google Sheets...")
    repo.upsert_clientes(clientes)
    repo.upsert_ordenes(ordenes)
    repo.upsert_lineas(lineas)
    repo.upsert_pagos(pagos)
    
    repo.set_last_sync(now)
    
    logger.info(f"🎉 ¡SINCRONIZACIÓN COMPLETA CONCLUIDA CON ÉXITO!")
    logger.info(f"Total registros frescos cargados desde Odoo Producción: {len(clientes) + len(ordenes) + len(lineas) + len(pagos)}")

if __name__ == "__main__":
    main()
