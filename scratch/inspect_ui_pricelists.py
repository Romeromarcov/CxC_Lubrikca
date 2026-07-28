import json
import os
import subprocess
import sys

sys.path.insert(0, 'src')

res = subprocess.run('railway variables --json', shell=True, capture_output=True)
try:
    data = json.loads(res.stdout.decode('utf-8', errors='ignore'))
    for k, v in data.items():
        if isinstance(v, str):
            os.environ[k] = v
except Exception:
    pass

from cxc.config import AppConfig
from cxc.odoo.client import _connect
from cxc.sheets.gateway import GspreadGateway
from cxc.sheets.repository import SheetsRepository

config = AppConfig.from_env()
execute = _connect(config.odoo)
gw = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
repo = SheetsRepository(gw)

meta_rows = repo._g.read_rows('_Meta')
meta_dict = {r.get('key'): r.get('value', '') for r in meta_rows if r.get('key')}
print("=== META SELECCIONADA DESDE LA UI ===")
print("valid_pricelists_usd:", meta_dict.get('valid_pricelists_usd'))
print("valid_pricelists_ves:", meta_dict.get('valid_pricelists_ves'))

pricelists = execute('product.pricelist', 'search_read', [], {'fields': ['id', 'name', 'currency_id', 'active']})
print("\n--- LISTAS DISPONIBLES EN ODOO ---")
for pl in pricelists:
    print(f"ID: {pl['id']} | Name: {pl['name']} | Currency: {pl.get('currency_id')} | Active: {pl.get('active')}")

items = execute('product.pricelist.item', 'search_read', [], {'fields': ['id', 'pricelist_id', 'product_tmpl_id', 'fixed_price', 'date_start', 'date_end']})
print(f"\n--- TOTAL REGLAS DE LISTAS EN ODOO: {len(items)} ---")
for it in items[:15]:
    pl_name = it['pricelist_id'][1] if isinstance(it['pricelist_id'], list) else it['pricelist_id']
    prod_name = it.get('product_tmpl_id')[1] if isinstance(it.get('product_tmpl_id'), list) else 'Todos'
    print(f"Pricelist: {pl_name} (ID: {it['pricelist_id'][0]}) | Prod: {prod_name} | Price: ${it.get('fixed_price')} | Start: {it.get('date_start')} | End: {it.get('date_end')}")
