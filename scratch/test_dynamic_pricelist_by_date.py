import sys
import os
import json
import subprocess
from datetime import datetime, date
from decimal import Decimal

sys.path.insert(0, 'src')

res = subprocess.run('railway variables --json', shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    data = json.loads(res.stdout.decode('utf-8', errors='ignore'))
    for k, v in data.items():
        if isinstance(v, str):
            os.environ[k] = v
except Exception as e:
    pass

from cxc.config import AppConfig
from cxc.odoo.client import _connect
from cxc.sheets.gateway import GspreadGateway
from cxc.sheets.repository import SheetsRepository

config = AppConfig.from_env()
execute = _connect(config.odoo)
gw = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
repo = SheetsRepository(gw)

def parse_ids(raw_str: str, default_id: int) -> list[int]:
    if not raw_str:
        return [default_id]
    if "," in raw_str:
        parts = [p.strip() for p in raw_str.split(",") if p.strip()]
    else:
        parts = [c for c in raw_str.strip() if c.isdigit()]
    res = [int(p) for p in parts if p.isdigit()]
    return res if res else [default_id]

meta_rows = repo._g.read_rows('_Meta')
meta_dict = {r.get('key'): r.get('value', '') for r in meta_rows if r.get('key')}

usd_ids = parse_ids(meta_dict.get('valid_pricelists_usd', '4'), 4)
ves_ids = parse_ids(meta_dict.get('valid_pricelists_ves', '5'), 5)

print("=== DECODIFICACION DE LISTAS SELECCIONADAS EN LA UI ===")
print("Candidate USD Pricelist IDs:", usd_ids)
print("Candidate VES Pricelist IDs:", ves_ids)

# Fetch rules for candidate pricelists
all_candidate_ids = list(set(usd_ids + ves_ids + [4, 5]))
all_rules = execute('product.pricelist.item', 'search_read', [[['pricelist_id', 'in', all_candidate_ids], ['compute_price', '=', 'fixed']]], {'fields': ['pricelist_id', 'product_tmpl_id', 'fixed_price', 'date_start', 'date_end']})

print(f"\n--- {len(all_rules)} REGLAS CARGADAS DESDE ODOO ---")

def resolve_effective_price(product_tmpl_id: int, order_date: date, candidate_ids: list[int]) -> Decimal | None:
    matched_items = []
    for r in all_rules:
        pl_id = r['pricelist_id'][0] if isinstance(r['pricelist_id'], list) else r['pricelist_id']
        if pl_id not in candidate_ids:
            continue
            
        pt_raw = r.get('product_tmpl_id')
        pt_id = pt_raw[0] if isinstance(pt_raw, list) else pt_raw
        if pt_id != product_tmpl_id:
            continue
            
        d_start_str = r.get('date_start')
        d_end_str = r.get('date_end')
        
        d_start = datetime.strptime(d_start_str[:10], '%Y-%m-%d').date() if d_start_str else None
        d_end = datetime.strptime(d_end_str[:10], '%Y-%m-%d').date() if d_end_str else None
        
        if d_start and order_date < d_start:
            continue
        if d_end and order_date > d_end:
            continue
            
        price = Decimal(str(r.get('fixed_price') or '0'))
        matched_items.append((d_start or date.min, price, pl_id))
        
    if matched_items:
        matched_items.sort(key=lambda x: x[0], reverse=True)
        return matched_items[0][1]
        
    return None

# Find a real template ID from all_rules
first_rule = all_rules[0]
sample_tmpl_id = first_rule['product_tmpl_id'][0]
sample_tmpl_name = first_rule['product_tmpl_id'][1]

print(f"\nProbar resolucion efectiva para {sample_tmpl_name} (Template ID: {sample_tmpl_id}):")
for test_date_str in ['2026-01-15', '2026-02-26', '2026-04-10', '2026-07-25']:
    dt = datetime.strptime(test_date_str, '%Y-%m-%d').date()
    usd_price = resolve_effective_price(sample_tmpl_id, dt, usd_ids)
    ves_price = resolve_effective_price(sample_tmpl_id, dt, ves_ids)
    print(f"  Fecha Orden: {test_date_str} -> Precio USD: ${usd_price} | Precio VES: ${ves_price}")
