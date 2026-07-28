import csv
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'src')

res = subprocess.run('railway variables --json', shell=True, capture_output=True)
try:
    data = json.loads(res.stdout.decode('utf-8', errors='ignore'))
    for k, v in data.items():
        if isinstance(v, str): os.environ[k] = v
except Exception: pass

from cxc.config import AppConfig
from cxc.sheets.gateway import GspreadGateway

config = AppConfig.from_env()
gw = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)

path = r'C:\Users\PC\Proyectos\CxC_Lubrikca\Info_Odoo\Actualziar Listas de Precio - Hoja 2.csv'

rows_to_insert = [
    ['codigo', 'producto_nombre', 'precio_usd', 'precio_bcv_euro', 'fecha_corte', 'notas']
]

with open(path, encoding='utf-8-sig') as f:
    reader = csv.reader(f)
    next(reader) # h0
    next(reader) # h1
    next(reader) # h2
    for r in reader:
        if not r or len(r) < 5: continue
        code = r[0].strip()
        name = r[1].strip()
        p_usd = r[3].strip().replace(',', '')
        p_eur = r[4].strip().replace(',', '')
        if code and (p_usd or p_eur):
            rows_to_insert.append([
                code,
                name,
                float(p_usd) if p_usd else 0.0,
                float(p_eur) if p_eur else 0.0,
                '2026-03-11',
                'Lista precio histórica referencia órdenes anteriores a 2026-03-12 o sin lista Odoo'
            ])

print(f'Total filas a guardar en Google Sheet: {len(rows_to_insert) - 1}')

# Check if worksheet exists, or create it
try:
    ws = gw._sh.worksheet('ListasPreciosHistoricas')
    ws.clear()
    print('Worksheet ListasPreciosHistoricas ya existía, limpiada.')
except Exception:
    ws = gw._sh.add_worksheet(title='ListasPreciosHistoricas', rows=len(rows_to_insert) + 20, cols=10)
    print('Worksheet ListasPreciosHistoricas creada exitosamente.')

ws.update(range_name='A1', values=rows_to_insert)
print('✅ Filas actualizadas con éxito en Google Sheet: ListasPreciosHistoricas!')
