import subprocess

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]
client_secret_file = 'secrets/client_secret_650349739109-7epvtitpu9at7ttu1cuji52710h34puc.apps.googleusercontent.com.json'

print('=== ABRIENDO NAVEGADOR PARA RE-AUTORIZAR GOOGLE ===')
flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, scopes=SCOPES)
creds = flow.run_local_server(port=0)

token_json = creds.to_json()
print('=== NUEVO TOKEN GENERADO EXITOSAMENTE ===')

print('Ejecutando actualización de GOOGLE_TOKEN_JSON en Railway...')
res = subprocess.run(['railway', 'variables', '--set', f'GOOGLE_TOKEN_JSON={token_json}'], capture_output=True, text=True)
print('STDOUT:', res.stdout)
print('STDERR:', res.stderr)
