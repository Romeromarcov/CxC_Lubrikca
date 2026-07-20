"""
Script para extraer las fechas históricas de las órdenes desde el CSV proporcionado
y generar el archivo de mapeo 'src/cxc/data/fechas_historicas_ordenes.json'.
"""

import csv
import json
import re
import os
import sys

if sys.version_info >= (3, 7):
    sys.stdout.reconfigure(encoding='utf-8')

csv_path = r"C:\Users\PC\Downloads\Cuentas x Cobrar Nuevo - Ventas (2).csv"
out_json_path = os.path.join("src", "cxc", "data", "fechas_historicas_ordenes.json")

def normalize_so_key(ref_str: str) -> str:
    """Normaliza un SO ID para ignorar ceros a la izquierda y prefijos S/SO.
    Ej: 'S00004' -> '4', 'SO004' -> '4', '4' -> '4'
    """
    clean = ref_str.strip().upper()
    digits = re.sub(r"[^\d]", "", clean)
    if digits:
        return str(int(digits))
    return clean

fechas_map = {}
raw_map = {}

with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
    reader = csv.DictReader(f)
    for row in reader:
        ref = row.get("Referencia de la orden", "").strip()
        num = row.get("Numero de Orden", "").strip()
        fecha_ord = row.get("Fecha de la orden", "").strip()
        fecha_col = row.get("Fecha", "").strip()
        vencimiento = row.get("Vencimiento", "").strip()
        
        # Determine best ISO date string (YYYY-MM-DD)
        iso_date = None
        if fecha_ord:
            # Format: '2026-03-09 5:46:05' -> '2026-03-09'
            iso_date = fecha_ord.split(" ")[0]
        elif fecha_col:
            # Format: '9/3/2026' or '26/2/2026'
            parts = fecha_col.split("/")
            if len(parts) == 3:
                day, month, year = parts
                iso_date = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
                
        if iso_date and (ref or num):
            key = ref or f"SO{num}"
            raw_map[key] = iso_date
            
            norm_key = normalize_so_key(key)
            fechas_map[norm_key] = iso_date

os.makedirs(os.path.dirname(out_json_path), exist_ok=True)
with open(out_json_path, "w", encoding="utf-8") as f:
    json.dump({
        "fechas_por_numero": fechas_map,
        "fechas_por_ref_original": raw_map
    }, f, indent=2)

print(f"✅ Fechas históricas extraídas con éxito: {len(raw_map)} órdenes procesadas.")
print(f"💾 Guardado en: {out_json_path}")
