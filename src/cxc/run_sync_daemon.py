import time
import logging
import sys

# Reconfigure stdout to use UTF-8
if sys.version_info >= (3, 7):
    sys.stdout.reconfigure(encoding='utf-8')

from cxc.run_sync import main

logger = logging.getLogger("cxc.daemon")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info("Iniciando Demonio de Sincronización CxC en la nube (intervalo: 5 minutos)...")
    while True:
        try:
            logger.info("Ejecutando ciclo de sincronización...")
            main()
            logger.info("Sincronización finalizada correctamente.")
        except Exception as e:
            logger.error(f"Error durante el ciclo de sincronización: {e}", exc_info=True)
        
        logger.info("Durmiendo por 5 minutos...")
        time.sleep(300)
