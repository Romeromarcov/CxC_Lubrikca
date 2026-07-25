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

# 1. PromocionPrimeraCompra
data_primera = [
    ['producto', 'vigencia_desde', 'vigencia_hasta', 'activo'],
    ['GLOBAL OIL', '2026-01-01', '2099-12-31', 'TRUE']
]

# 2. DescuentosRecompra
data_recompra = [
    ['regla_id', 'porcentaje', 'max_usos_mes', 'dias_ventana', 'vigencia_desde', 'vigencia_hasta', 'activo'],
    ['RECOMPRA_GLOBAL_3PCT', '3.0', '1', '30', '2026-04-01', '2099-12-31', 'TRUE'],
    ['RECOMPRA_GLOBAL_5PCT', '5.0', '1', '30', '2026-04-01', '2099-12-31', 'TRUE']
]

# 3. DescuentosProntoPago
data_pronto_pago = [
    ['regla_id', 'marca', 'categoria', 'dias_gracia', 'porcentaje', 'monedas_aplicables', 'listas_aplicables', 'vigencia_desde', 'vigencia_hasta', 'activo'],
    ['PP_GLOBAL_ELITE_SS_10', 'GLOBAL OIL', 'ELITE,SS', '0', '10.0', 'USD,VES', '*', '2026-01-01', '2026-03-30', 'FALSE'],
    ['PP_GLOBAL_VISCOSIDADES_8', 'GLOBAL OIL', '*', '0', '8.0', 'USD,VES', '*', '2026-01-01', '2026-03-30', 'FALSE'],
    ['PP_GLOBAL_PAILAS_8', 'GLOBAL OIL', 'PAILA', '0', '8.0', 'USD,VES', '*', '2026-01-01', '2026-03-30', 'FALSE'],
    ['PP_GLOBAL_TAMBORES_6', 'GLOBAL OIL', 'TAMBOR', '0', '6.0', 'USD,VES', '*', '2026-01-01', '2026-03-30', 'FALSE'],
    ['PP_GLOBAL_CAJAS_8', 'GLOBAL OIL', 'CAJA', '0', '8.0', 'USD,VES', '*', '2026-03-01', '2099-12-31', 'TRUE'],
    ['PP_GLOBAL_PAILAS_6', 'GLOBAL OIL', 'PAILA', '0', '6.0', 'USD,VES', '*', '2026-03-01', '2099-12-31', 'TRUE'],
    ['PP_GLOBAL_TAMBORES_VIG_6', 'GLOBAL OIL', 'TAMBOR', '0', '6.0', 'USD,VES', '*', '2026-03-01', '2099-12-31', 'TRUE'],
    ['PP_SINOCO_CAJAS_3', 'SINOCO', 'CAJA', '0', '3.0', 'USD,VES', '*', '2026-03-01', '2099-12-31', 'TRUE']
]

# 4. DescuentosVolumen
data_volumen = [
    ['regla_id', 'marca', 'categoria', 'litros_minimo', 'porcentaje', 'activo'],
    ['VOL_FIDELIDAD_GLOBAL_2500', 'GLOBAL OIL', '*', '2500', '5.0', 'TRUE'],
    ['VOL_FIDELIDAD_GLOBAL_5000', 'GLOBAL OIL', '*', '5000', '12.0', 'TRUE'],
    ['VOL_SINOCO_PAILAS_10_19', 'SINOCO', 'PAILA', '190', '4.52', 'TRUE'],
    ['VOL_SINOCO_PAILAS_20_99', 'SINOCO', 'PAILA', '380', '12.04', 'TRUE'],
    ['VOL_SINOCO_PAILAS_100', 'SINOCO', 'PAILA', '1900', '19.28', 'TRUE']
]

# 5. DescuentosProducto
data_producto = [
    ['regla_id', 'productos', 'marca', 'categoria', 'porcentaje', 'monedas_aplicables', 'listas_aplicables', 'vigencia_desde', 'vigencia_hasta', 'activo'],
    ['PROMO_12_MAS_1', '*', 'GLOBAL OIL', 'CAJA', '8.33', 'USD,VES', '*', '2026-03-01', '2099-12-31', 'TRUE']
]

seed_map = {
    'PromocionPrimeraCompra': data_primera,
    'DescuentosRecompra': data_recompra,
    'DescuentosProntoPago': data_pronto_pago,
    'DescuentosVolumen': data_volumen,
    'DescuentosProducto': data_producto
}

for table_name, rows_data in seed_map.items():
    try:
        ws = gw._sh.worksheet(table_name)
        ws.clear()
    except Exception:
        ws = gw._sh.add_worksheet(title=table_name, rows=len(rows_data)+10, cols=12)
    ws.update(range_name='A1', values=rows_data)
    print(f'✅ Tabla {table_name:24s}: {len(rows_data)-1} reglas sembradas exitosamente.')

print('\n🎉 SIEMBRA MANUAL DE REGLAS COMPLETADA EN GOOGLE SHEETS.')
