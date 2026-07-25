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
from cxc.engine.effective_dating import descuento_vigente
from cxc.models import TipoDescuento

config = AppConfig.from_env()
gw = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
repo = SheetsRepository(gw)

ordenes = repo.all_ordenes()
all_lines = repo._g.read_rows("LineasOrden")
lines_by_so = {}
for r in all_lines:
    so = r.get("so_id", "")
    if so:
        lines_by_so.setdefault(so, []).append(r)

descuentos_marca = repo.descuentos_marca_categoria()
print(f"Total reglas DescuentosMarcaCategoria: {len(descuentos_marca)}")
for d in descuentos_marca:
    print(f"  Regla: {d.regla_id} | Marca: {d.marca} | Cat: {d.categoria} | Pct: {d.porcentaje}% | Desde: {d.vigencia_desde}")

orders_with_theoretical_desc = 0
total_theoretical_desc = Decimal("0")

for o in ordenes[:20]:
    order_lines = lines_by_so.get(o.so_id, [])
    desc_order = Decimal("0")
    details = []
    
    for ln in order_lines:
        cant = Decimal(str(ln.get("cantidad_entregada") if ln.get("cantidad_entregada") not in (None, "", "None") else ln.get("cantidad", "0")))
        precio = Decimal(str(ln.get("precio_unitario", "0")))
        subt = cant * precio
        
        marca = str(ln.get("marca") or "*")
        cat = str(ln.get("categoria") or "*")
        
        rule = descuento_vigente(descuentos_marca, marca=marca, categoria=cat, tipo=TipoDescuento.MARCA_CATEGORIA, fecha=o.fecha)
        if rule and rule.porcentaje > Decimal("0"):
            monto_d = subt * (rule.porcentaje / Decimal("100"))
            desc_order += monto_d
            details.append(f"{rule.marca}/{rule.categoria} ({rule.porcentaje}% = ${monto_d:.2f})")
            
    if desc_order > Decimal("0"):
        orders_with_theoretical_desc += 1
        total_theoretical_desc += desc_order
        print(f"SO {o.so_id} ({o.fecha}): Descuento Teórico = ${desc_order:.2f} -> {', '.join(details)}")

print(f"\nTotal órdenes con descuento teórico en muestra: {orders_with_theoretical_desc} | Total Descuentos Teóricos: ${total_theoretical_desc:.2f}")
