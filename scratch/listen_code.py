import http.server
import socketserver
import urllib.parse
import json
import subprocess
from google_auth_oauthlib.flow import InstalledAppFlow

PORT = 8090
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]
client_secret_file = 'secrets/client_secret_650349739109-7epvtitpu9at7ttu1cuji52710h34puc.apps.googleusercontent.com.json'

received_code = None

class OAuthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global received_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            received_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            html = "<h2>Autorizacion completada con exito! Puedes cerrar esta ventana.</h2>"
            self.wfile.write(html.encode("utf-8"))
        else:
            self.send_response(400)
            self.end_headers()

print(f"Servidor escuchando en http://localhost:{PORT} para recibir la redirección de Google...")
with socketserver.TCPServer(("", PORT), OAuthHandler) as httpd:
    httpd.handle_request()  # Handle 1 request then close

if received_code:
    print("=== CODIGO DE AUTORIZACIÓN RECIBIDO ===")
    flow = InstalledAppFlow.from_client_secrets_file(
        client_secret_file,
        scopes=SCOPES,
        redirect_uri=f'http://localhost:{PORT}/'
    )
    flow.fetch_token(code=received_code)
    creds = flow.credentials
    token_json = creds.to_json()

    print("=== NUEVO TOKEN GOOGLE_TOKEN_JSON GENERADO ===")
    print("Actualizando en Railway via CLI...")
    res = subprocess.run(['railway', 'variables', '--set', f'GOOGLE_TOKEN_JSON={token_json}'], capture_output=True, text=True)
    print("STDOUT:", res.stdout)
    print("STDERR:", res.stderr)
    print("¡VARIABLES DE RAILWAY ACTUALIZADAS EXITOSAMENTE!")
else:
    print("No se recibió código.")
