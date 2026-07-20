"""
Script para poblar la pestaña 'FechasHistoricas' en Google Sheets con las 554 órdenes
del archivo CSV para consulta y edición directamente en Google Drive.
"""

import os
import sys
import csv
import logging
from cxc.config import AppConfig
from cxc.sheets.gateway import GspreadGateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cxc.upload_fechas_historicas")

csv_path = r"C:\Users\PC\Downloads\Cuentas x Cobrar Nuevo - Ventas (2).csv"

def main():
    if sys.version_info >= (3, 7):
        sys.stdout.reconfigure(encoding='utf-8')

    logger.info("Iniciando conexión a Google Sheets...")
    config = AppConfig.from_env()
    if os.environ.get("GOOGLE_TOKEN_JSON"):
        gw = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
    else:
        gw = GspreadGateway(config.sheets.spreadsheet_id, config.sheets.service_account_file)

    sh = gw._sh
    sheet_name = "FechasHistoricas"

    # Check if worksheet exists, if not create it
    try:
        ws = sh.worksheet(sheet_name)
        logger.info(f"Pestaña '{sheet_name}' encontrada en Google Sheets.")
    except Exception:
        logger.info(f"Pestaña '{sheet_name}' no existe. Creando nueva pestaña...")
        ws = sh.add_worksheet(title=sheet_name, rows=1000, cols=5)

    headers = ["so_id", "fecha_historica", "vencimiento_historico", "terminos_pago"]
    
    rows_to_insert = [headers]
    seen_ids = set()

    with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ref = row.get("Referencia de la orden", "").strip()
            num = row.get("Numero de Orden", "").strip()
            fecha_ord = row.get("Fecha de la orden", "").strip()
            fecha_col = row.get("Fecha", "").strip()
            vencimiento = row.get("Vencimiento", "").strip()
            terminos = row.get("Terminos de pago", "").strip()

            so_id = ref or f"S{num}"
            if not so_id or so_id in seen_ids:
                continue

            iso_date = ""
            if fecha_ord:
                iso_date = fecha_ord.split(" ")[0]
            elif fecha_col:
                parts = fecha_col.split("/")
                if len(parts) == 3:
                    day, month, year = parts
                    iso_date = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

            if iso_date:
                rows_to_insert.append([so_id, iso_date, vencimiento, terminos])
                seen_ids.add(so_id)

    logger.info(f"Subiendo {len(rows_to_insert)-1} registros de fechas históricas a la pestaña '{sheet_name}'...")
    ws.clear()
    ws.update("A1", rows_to_insert)
    logger.info("🎉 ¡Población de 'FechasHistoricas' en Google Sheets completada exitosamente!")

if __name__ == "__main__":
    main()
