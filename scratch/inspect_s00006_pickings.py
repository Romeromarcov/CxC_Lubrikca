import sys
import os
import json
import subprocess

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

config = AppConfig.from_env()
execute = _connect(config.odoo)

so = execute('sale.order', 'search_read', [[['name', '=', 'S00006']]], {'fields': ['id', 'name', 'state', 'amount_total']})
print("Sale Order S00006:", so)

if so:
    so_id = so[0]['id']
    pickings = execute(
        'stock.picking',
        'search_read',
        [['|', ['sale_id', '=', so_id], ['origin', 'ilike', 'S00006']]],
        {'fields': ['id', 'name', 'origin', 'state', 'picking_type_code', 'return_id', 'location_id', 'location_dest_id']}
    )
    print(f"\n--- {len(pickings)} PICKINGS ENCONTRADOS PARA S00006 ---")
    for p in pickings:
        print(f"ID: {p.get('id')} | Name: {p.get('name')} | State: {p.get('state')} | TypeCode: {p.get('picking_type_code')} | Origin: {p.get('origin')} | ReturnId: {p.get('return_id')}")
