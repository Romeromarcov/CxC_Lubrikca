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

# Fetch all sale orders
orders = execute(
    'sale.order',
    'search_read',
    [],
    {'fields': ['id', 'name', 'state', 'partner_id', 'amount_total']}
)
so_map = {o['id']: o for o in orders}
so_name_map = {o['name']: o for o in orders}

# Fetch all pickings
pickings = execute(
    'stock.picking',
    'search_read',
    [],
    {'fields': ['id', 'name', 'sale_id', 'origin', 'state', 'picking_type_code', 'return_id']}
)

# Group pickings by sale_id or SO name origin
pickings_by_so = {}
for p in pickings:
    sid = p.get('sale_id')
    so_id = sid[0] if isinstance(sid, (list, tuple)) else None
    origin = str(p.get('origin') or '').strip()
    
    # Try matching by sale_id or origin (e.g. S00006 or Devolución de ALM/OUT/00004)
    target_so_name = None
    if so_id and so_id in so_map:
        target_so_name = so_map[so_id]['name']
    elif origin:
        for name in so_name_map:
            if name in origin:
                target_so_name = name
                break

    if target_so_name:
        pickings_by_so.setdefault(target_so_name, []).append(p)

def es_orden_cancelada_o_devuelta(so_name: str, state: str, p_list: list[dict]) -> tuple[bool, str]:
    """
    Retorna (debe_excluirse_de_cxc, razon)
    """
    # 1. Cotizaciones no confirmadas sin entregas
    done_out = [p for p in p_list if p.get('state') == 'done' and p.get('picking_type_code') == 'outgoing']
    done_in_returns = [p for p in p_list if p.get('state') == 'done' and (p.get('return_id') or p.get('picking_type_code') == 'incoming' or 'Devolución' in str(p.get('origin', '')))]

    if state in ['draft', 'sent']:
        if not done_out:
            return True, "Cotización no confirmada y sin despacho realizado"

    # 2. Orden cancelada
    if state == 'cancel':
        if len(done_in_returns) >= len(done_out) or not done_out or len(done_in_returns) > 0:
            return True, f"Orden cancelada con devolución procesada ({len(done_in_returns)} devoluciones en 'done')"

    # 3. Devuelta totalmente
    if done_out and len(done_in_returns) >= len(done_out):
        return True, f"Mercancía devuelta totalmente ({len(done_in_returns)} de {len(done_out)} despachos devueltos)"

    return False, "Orden activa"

print("=== PRUEBA DE EVALUACIÓN EN S00006 Y ÓRDENES SIMILARES ===")
for name in ['S00006', 'S00710', 'S00709', 'S00699']:
    o = so_name_map.get(name)
    if o:
        plist = pickings_by_so.get(name, [])
        excluir, razon = es_orden_cancelada_o_devuelta(name, o.get('state'), plist)
        print(f"SO: {name} | State: {o.get('state')} | Total: ${o.get('amount_total')} | Excluir de CxC: {excluir} | Razón: {razon}")
