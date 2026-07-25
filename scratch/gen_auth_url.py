import subprocess
import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]
client_secret_file = 'secrets/client_secret_650349739109-7epvtitpu9at7ttu1cuji52710h34puc.apps.googleusercontent.com.json'

flow = InstalledAppFlow.from_client_secrets_file(
    client_secret_file,
    scopes=SCOPES,
    redirect_uri='http://localhost:8090/'
)

auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
print("AUTH_URL=" + auth_url)

try:
    subprocess.run(["powershell", "-Command", f'Start-Process "{auth_url}"'], check=True)
    print("Browser opened via PowerShell Start-Process!")
except Exception as e:
    print("Could not launch browser automatically:", e)
