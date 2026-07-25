import sys
import os
import json
import subprocess
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

config = AppConfig.from_env()
execute = _connect(config.odoo)

orders = execute('sale.order', 'search_read', [], {'fields': ['id', 'name', 'state', 'partner_id', 'amount_total']})
so_map = {o['id']: o for o in orders}

lines = execute('sale.order.line', 'search_read', [], {'fields': ['id', 'order_id', 'product_id', 'product_uom_qty', 'qty_delivered', 'price_unit', 'price_subtotal']})

lines_by_so = {}
for l in lines:
    oid = l['order_id'][0] if l.get('order_id') else None
    if oid:
        lines_by_so.setdefault(oid, []).append(l)

print("=== DETALLE DE ORDENES CANCELADAS CON ENTREGAS ACTIVAS ===")
for oid, o in so_map.items():
    so_lines = lines_by_so.get(oid, [])
    monto_entregado_neto = Decimal('0')
    for l in so_lines:
        qty_del = Decimal(str(l.get('qty_delivered') or '0'))
        price = Decimal(str(l.get('price_unit') or '0'))
        monto_entregado_neto += qty_del * price
        
    st = o.get('state')
    
    if st == 'cancel' and monto_entregado_neto > Decimal('0'):
        p_name = o.get('partner_id')[1] if o.get('partner_id') else 'N/A'
        print(f"SO: {o['name']} | Cliente: {p_name} | State: {st} | Monto Entregado Neto: ${monto_entregado_neto} | Total Orden: ${o.get('amount_total')}")
