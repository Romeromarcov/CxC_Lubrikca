"""
Script para la Limpieza Segura de Google Sheets antes de Conectar con Odoo Producción.

Uso:
  # Modo simulación (Dry Run):
  python scripts/prepare_production_sheets.py --dry-run

  # Ejecución real de purga en producción:
  python scripts/prepare_production_sheets.py --confirm-reset-production
"""

import os
import sys
import argparse
import logging
from cxc.config import AppConfig
from cxc.sheets.gateway import GspreadGateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cxc.prepare_production")

# Tables containing business operational data that will be cleared
OPERATIONAL_TABLES_TO_CLEAR = [
    "OrdenesVenta",
    "LineasOrden",
    "Pagos",
    "Vinculaciones",
    "Conciliaciones",
    "CalculosOrden",
    "HistorialVinculaciones",
    "AnomaliasAceptadas",
    "SerieTasas"
]

# Tables containing platform users and business rules that MUST BE PRESERVED
PROTECTED_TABLES = [
    "UsuariosPlataforma",
    "Clientes",
    "_Meta",
    "DescuentosVolumen",
    "DescuentosMarcaCategoria",
    "DescuentosProntoPago",
    "DescuentosRecompra",
    "DescuentosProducto",
    "DescuentosDiferencialCambiario",
    "PromocionPrimeraCompra",
    "Exclusiones",
    "Feriados",
    "FechasHistoricas"
]

def main():
    if sys.version_info >= (3, 7):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="Pura datos de prueba en Google Sheets para iniciar en Odoo Producción.")
    parser.add_argument("--dry-run", action="store_true", help="Simula el proceso de limpieza sin modificar la hoja.")
    parser.add_argument("--confirm-reset-production", action="store_true", help="Confirmación explícita para borrar los datos operacionales.")
    args = parser.parse_args()

    if not args.dry_run and not args.confirm-reset-production:
        logger.error("Debe especificar --dry-run o --confirm-reset-production para ejecutar el script.")
        sys.exit(1)

    logger.info("Iniciando verificación de conexión con Google Sheets...")
    config = AppConfig.from_env()
    if os.environ.get("GOOGLE_TOKEN_JSON"):
        gw = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
    else:
        gw = GspreadGateway(config.sheets.spreadsheet_id, config.sheets.service_account_file)

    worksheets = gw._sh.worksheets()
    sheet_names = [ws.title for ws in worksheets]
    logger.info(f"Pestañas encontradas en Google Sheets: {sheet_names}")

    print("\n" + "="*60)
    print("RESUMEN DE ACCIÓN DE LIMPIEZA PARA ODOO PRODUCCIÓN:")
    print("="*60)
    print("🔒 Pestañas Protegidas (NO SE TOCAN):")
    for tbl in PROTECTED_TABLES:
        status = "PRESENTE" if tbl in sheet_names else "NO EXISTE"
        print(f"  - [OK] {tbl} ({status})")

    print("\n🧹 Pestañas Operacionales (SE PURGARÁN A 0 FILAS RESPETANDO ENCABEZADOS):")
    for tbl in OPERATIONAL_TABLES_TO_CLEAR:
        status = "PRESENTE" if tbl in sheet_names else "NO EXISTE"
        print(f"  - [VACIAS] {tbl} ({status})")
    print("="*60 + "\n")

    if args.dry_run:
        logger.info("Modo DRY-RUN completado. Ningún dato fue borrado de Google Sheets.")
        sys.exit(0)

    logger.warning("PROCESANDO PURGA REAL EN GOOGLE SHEETS PARA PRODUCCIÓN...")
    for tbl in OPERATIONAL_TABLES_TO_CLEAR:
        if tbl not in sheet_names:
            logger.info(f"Pestaña {tbl} no existe. Omitiendo...")
            continue

        try:
            ws = gw._sh.worksheet(tbl)
            headers = ws.row_values(1)
            # Clear worksheet content and re-insert headers
            ws.clear()
            if headers:
                ws.append_row(headers)
            logger.info(f"✅ Pestaña '{tbl}' purgada exitosamente. Conservados {len(headers)} encabezados.")
        except Exception as err:
            logger.error(f"❌ Error purgando pestaña {tbl}: {err}")

    logger.info("🎉 ¡PROCESO DE PREPARACIÓN DE GOOGLE SHEETS COMPLETADO EXITOSAMENTE!")

if __name__ == "__main__":
    main()
