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

config = AppConfig.from_env()
execute = _connect(config.odoo)

orders = execute(
    'sale.order',
    'search_read',
    [[['state', 'in', ['cancel', 'draft', 'sale', 'done']]]],
    {'fields': ['name', 'partner_id', 'date_order', 'amount_total', 'state', 'invoice_status', 'delivery_status'], 'limit': 30}
)

print("=== MUESTRA DE ESTADOS DE ORDENES EN ODOO PRODUCCIÓN ===")
for o in orders:
    p_name = o.get('partner_id')[1] if o.get('partner_id') else 'N/A'
    print(f"SO: {o.get('name')} | Cliente: {p_name[:30]} | State: {o.get('state')} | DeliveryStatus: {o.get('delivery_status')} | InvoiceStatus: {o.get('invoice_status')} | Total: ${o.get('amount_total')}")
