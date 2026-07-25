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

print("=== INSPECCIONANDO LINEAS Y MOVES DE S00006 EN ODOO PRODUCCIÓN ===")

so = execute('sale.order', 'search_read', [[['name', '=', 'S00006']]], {'fields': ['id', 'name', 'state', 'amount_total']})
so_id = so[0]['id']

solines = execute('sale.order.line', 'search_read', [[['order_id', '=', so_id]]], {'fields': ['id', 'name', 'product_id', 'product_uom_qty', 'qty_delivered', 'price_unit', 'price_subtotal']})
print("\n--- LINEAS DE LA ORDEN S00006 ---")
for l in solines:
    p_name = l.get('product_id')[1] if l.get('product_id') else 'N/A'
    print(f"Line ID: {l.get('id')} | Prod: {p_name} | QtyOrdered: {l.get('product_uom_qty')} | QtyDelivered: {l.get('qty_delivered')} | PriceUnit: ${l.get('price_unit')} | Subtotal: ${l.get('price_subtotal')}")

moves = execute('stock.move', 'search_read', [['|', ['sale_line_id', 'in', [l['id'] for l in solines]], ['origin', 'ilike', 'S00006']]], {'fields': ['id', 'name', 'product_id', 'product_uom_qty', 'quantity', 'state', 'picking_code', 'location_id', 'location_dest_id', 'sale_line_id']})
print(f"\n--- {len(moves)} STOCK MOVES DE LA ORDEN S00006 ---")
for m in moves:
    p_name = m.get('product_id')[1] if m.get('product_id') else 'N/A'
    l_from = m.get('location_id')[1] if m.get('location_id') else 'N/A'
    l_to = m.get('location_dest_id')[1] if m.get('location_dest_id') else 'N/A'
    qty_done = m.get('quantity') if m.get('quantity') is not None else m.get('product_uom_qty')
    print(f"Move ID: {m.get('id')} | Prod: {p_name} | State: {m.get('state')} | Qty: {qty_done} | Code: {m.get('picking_code')} | SaleLine: {m.get('sale_line_id')} | Loc: {l_from} -> {l_to}")
