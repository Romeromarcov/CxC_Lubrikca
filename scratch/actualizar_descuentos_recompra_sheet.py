import sys, os, subprocess, json
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'src')

res = subprocess.run('railway variables --json', shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    data = json.loads(res.stdout.decode('utf-8', errors='ignore'))
    for k, v in data.items():
        if isinstance(v, str): os.environ[k] = v
except Exception as e: pass

from cxc.config import AppConfig
from cxc.sheets.gateway import GspreadGateway

config = AppConfig.from_env()
gw = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)

# 2. DescuentosRecompra
data_recompra = [
    ['regla_id', 'marca', 'categoria', 'min_cajas', 'max_cajas', 'porcentaje', 'max_usos_mes', 'dias_ventana', 'vigencia_desde', 'vigencia_hasta', 'activo'],
    ['RECOMPRA_GLOBAL_2_4_CAJAS_3PCT', 'GLOBAL OIL', 'CAJA', '2', '4', '0.03', '1', '30', '2026-04-01', '2099-12-31', 'TRUE'],
    ['RECOMPRA_GLOBAL_5_MAS_CAJAS_5PCT', 'GLOBAL OIL', 'CAJA', '5', '9999', '0.05', '1', '30', '2026-04-01', '2099-12-31', 'TRUE']
]

ws = gw._sh.worksheet('DescuentosRecompra')
ws.clear()
ws.update(range_name='A1', values=data_recompra)

print('✅ Tabla DescuentosRecompra actualizada exitosamente con Marca (GLOBAL OIL), Categoría (CAJA), Tramos (2-4 -> 3% y 5+ -> 5%) y porcentajes decimales.')
