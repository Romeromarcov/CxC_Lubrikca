import sys
import os
import json
import subprocess

sys.path.insert(0, 'src')

# Fetch GOOGLE_SERVICE_ACCOUNT_JSON from Railway CLI
res = subprocess.run('railway variables --json', shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    data = json.loads(res.stdout.decode('utf-8', errors='ignore'))
    os.environ['GOOGLE_SERVICE_ACCOUNT_JSON'] = data.get('GOOGLE_SERVICE_ACCOUNT_JSON', '')
    os.environ['GOOGLE_SHEETS_SPREADSHEET_ID'] = data.get('GOOGLE_SHEETS_SPREADSHEET_ID', '')
except Exception as e:
    print("Could not load from railway:", e)

from cxc.config import AppConfig
from cxc.sheets.gateway import GspreadGateway

config = AppConfig.from_env()
gw = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)

worksheets = gw._sh.worksheets()
print("=== PESTAÑAS ACTUALES EN GOOGLE SHEETS DE PRODUCCIÓN ===")
for ws in worksheets:
    print(f" - \"{ws.title}\" (Rows={ws.row_count}, Cols={ws.col_count})")
