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

# 4. DescuentosVolumen
data_volumen = [
    ['regla_id', 'marca', 'categoria', 'litros_minimo', 'porcentaje', 'activo'],
    ['VOL_FIDELIDAD_GLOBAL_2500', 'GLOBAL OIL', '*', '2500', '5.0', 'TRUE'],
    ['VOL_FIDELIDAD_GLOBAL_5000', 'GLOBAL OIL', '*', '5000', '12.0', 'TRUE'],
    ['VOL_FIDELIDAD_SINOCO_5000', 'SINOCO', '*', '5000', '12.0', 'TRUE'],
    ['VOL_SINOCO_PAILAS_10_19', 'SINOCO', 'PAILA', '190', '4.52', 'TRUE'],
    ['VOL_SINOCO_PAILAS_20_99', 'SINOCO', 'PAILA', '380', '12.04', 'TRUE'],
    ['VOL_SINOCO_PAILAS_100', 'SINOCO', 'PAILA', '1900', '19.28', 'TRUE']
]

ws = gw._sh.worksheet('DescuentosVolumen')
ws.clear()
ws.update(range_name='A1', values=data_volumen)

print(f'✅ Tabla DescuentosVolumen actualizada con {len(data_volumen)-1} reglas incluyendo SINOCO 5000 Lts (12%).')
