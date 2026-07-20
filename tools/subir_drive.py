import os
import sys
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Reconfigure stdout to use UTF-8
if sys.version_info >= (3, 7):
    sys.stdout.reconfigure(encoding='utf-8')

# Define Scopes for Google Drive API
SCOPES = ['https://www.googleapis.com/auth/drive']

CLIENT_SECRETS_FILE = r"C:\Users\PC\Proyectos\CxC_Lubrikca\secrets\client_secret_650349739109-7epvtitpu9at7ttu1cuji52710h34puc.apps.googleusercontent.com.json"
TOKEN_FILE = r"C:\Users\PC\Proyectos\CxC_Lubrikca\secrets\token.json"
LOCAL_FILE = r"c:\Users\PC\Downloads\CxC Lubrikca Google\Cuentas_x_Cobrar_Lubrikca.xlsx"
FILE_NAME_ON_DRIVE = "Cuentas_x_Cobrar_Lubrikca"

def authenticate():
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            print(f"Error al cargar token existente: {e}")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Error al refrescar token: {e}")
                creds = None
        
        if not creds:
            if not os.path.exists(CLIENT_SECRETS_FILE):
                print(f"Error: No se encontró el archivo client_secrets en: {CLIENT_SECRETS_FILE}")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            # This will run a local server and open the browser for authentication
            creds = flow.run_local_server(port=0)
            
        # Save credentials for the next run
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
            
    return creds

def upload_file(creds):
    # Build Google Drive API service
    service = build('drive', 'v3', credentials=creds)
    
    if not os.path.exists(LOCAL_FILE):
        print(f"Error: El archivo local a subir no existe en: {LOCAL_FILE}")
        sys.exit(1)
        
    print(f"Buscando si ya existe un archivo '{FILE_NAME_ON_DRIVE}' en Google Drive...")
    query = f"name = '{FILE_NAME_ON_DRIVE}' and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
    results = service.files().list(q=query, spaces='drive', fields='files(id, name, webViewLink)').execute()
    items = results.get('files', [])
    
    media = MediaFileUpload(
        LOCAL_FILE, 
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        resumable=True
    )
    
    if items:
        file_id = items[0]['id']
        link = items[0]['webViewLink']
        print(f"Se encontró el archivo existente con ID: {file_id}. Actualizando contenido...")
        # Update file content (overwrite)
        file = service.files().update(
            fileId=file_id,
            media_body=media
        ).execute()
        print(f"¡Archivo actualizado con éxito!")
        print(f"Enlace de Google Sheets: {link}")
    else:
        print("No se encontró ningún archivo previo. Creando un archivo nuevo...")
        file_metadata = {
            'name': FILE_NAME_ON_DRIVE,
            'mimeType': 'application/vnd.google-apps.spreadsheet' # Google Sheets mimeType causes conversion
        }
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, name, webViewLink'
        ).execute()
        print(f"¡Archivo subido y convertido con éxito!")
        print(f"ID del archivo creado: {file.get('id')}")
        print(f"Enlace de Google Sheets: {file.get('webViewLink')}")

if __name__ == "__main__":
    print("Iniciando autenticación con Google...")
    creds = authenticate()
    print("Autenticación exitosa. Iniciando carga del archivo a Google Drive...")
    upload_file(creds)
