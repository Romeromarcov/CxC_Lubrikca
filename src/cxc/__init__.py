"""Backend del Sistema de Cobros y Conciliación de Lubrikca (CxC).

Piezas backend (ver Especificacion_Sistema_Cobros_Lubrikca.md):
    1. Scraper de tasas        -> cxc.scraper
    2. Sync incremental delta   -> cxc.sync
    4. Motor de descuentos      -> cxc.engine
    5. Capa de conciliación     -> cxc.reconciliation
    6. Auditoría hora vs banco  -> cxc.audit
"""

__version__ = "1.0.0"
