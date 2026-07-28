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

# PromocionPrimeraCompra
data_primera = [
    ['regla_id', 'tipo_beneficio', 'productos', 'valor', 'compra_minima', 'regalo_tipo', 'vigencia_desde', 'vigencia_hasta', 'descuento_fallback', 'categorias_aplica', 'activo'],
    ['PROMO_PRIMERA_COMPRA_GLOBAL', 'producto', 'GLOBAL OIL (Liga de frenos DOT 3 1x12 / Elevador de octanaje)', '1', '3', 'solo_uno', '2026-01-01', '2099-12-31', '0.02', 'Comercial', 'TRUE']
]

ws = gw._sh.worksheet('PromocionPrimeraCompra')
ws.clear()
ws.update(range_name='A1', values=data_primera)

print('✅ Tabla PromocionPrimeraCompra actualizada exitosamente con obsequio (3 cajas) y Fallback de 2%!')
