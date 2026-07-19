import os
import sys
import json
import asyncio
import logging
from datetime import datetime, date
from decimal import Decimal
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logger = logging.getLogger("cxc.web.app")

# Reconfigure stdout to use UTF-8
if sys.version_info >= (3, 7):
    sys.stdout.reconfigure(encoding='utf-8')

from cxc.config import AppConfig
from cxc.sheets.gateway import GspreadGateway
from cxc.sheets.repository import SheetsRepository
from cxc.odoo.client import OdooXmlRpcReader, _connect
from cxc.odoo.price import OdooPriceResolver
from cxc.sync.incremental import IncrementalSync
from cxc.engine.runner import EngineRunner
from cxc.reconciliation.reconcile import OdooFacturasReader, Reconciler
from cxc.models import Vinculacion, EstadoVinculacion, Moneda, TipoTasa

app = FastAPI(title="CxC Lubrikca Billing Dashboard")

# Serve static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

def parse_decimal_safe(val) -> Decimal:
    if not val:
        return Decimal("0")
    s = str(val).strip()
    s = s.replace("$", "").replace("€", "").replace("Bs.", "").replace("Bs", "").strip()
    if not s:
        return Decimal("0")
    # European/Spanish format check: comma for decimals
    if "," in s:
        if "." in s and s.find(".") < s.find(","):
            s = s.replace(".", "").replace(",", ".")
        elif "." not in s:
            s = s.replace(",", ".")
    try:
        return Decimal(s)
    except:
        return Decimal("0")

# Models for POST requests
class VinculacionRequest(BaseModel):
    pago_id: str
    so_id: str
    monto_aplicado: float

class TasaRequest(BaseModel):
    tasa_bcv: float
    tasa_binance: float

class FeriadoRequest(BaseModel):
    fecha: str
    descripcion: str

class DescuentoMarcaRequest(BaseModel):
    marca: str
    categoria: str
    tipo_descuento: str
    porcentaje: float
    vigencia_desde: str
    vigencia_hasta: str | None = None
    listas_aplicables: str = "*"

class MetaRequest(BaseModel):
    cash_window_business_days: int
    descuento_recompra: float

class PromocionRequest(BaseModel):
    tipo_beneficio: str = "producto"       # 'producto' | 'porcentaje'
    productos: str = ""                    # CSV de SKUs de regalo
    valor: float = 1.0                     # cantidad o pct (0.02 = 2%)
    compra_minima: float = 0.0             # unidades Comercial mínimas para el regalo
    descuento_fallback: float = 0.0        # pct si no alcanza compra_minima
    regalo_tipo: str = "solo_uno"          # 'solo_uno' | 'conjunto'
    categorias_aplica: str = "Comercial"   # CSV de categorías que califican
    vigencia_desde: str = ""
    vigencia_hasta: str | None = None
    activo: bool = True

class ExclusionRequest(BaseModel):
    regla_tipo_a: str
    regla_tipo_b: str
    activo: bool = True

class ProntoPagoRequest(BaseModel):
    marca: str = "*"
    categoria: str = "*"
    dias_gracia: int = 3
    porcentaje: float = 0.05
    monedas_aplicables: str = "*"
    listas_aplicables: str = "*"
    vigencia_desde: str = ""
    vigencia_hasta: str | None = None
    activo: bool = True

class RecompraRequest(BaseModel):
    porcentaje: float = 0.05
    max_usos_mes: int = 2
    dias_ventana: int = 30
    vigencia_desde: str = ""
    vigencia_hasta: str | None = None
    activo: bool = True

class ProductoPromoRequest(BaseModel):
    productos: str = "*"
    marca: str = "*"
    categoria: str = "*"
    porcentaje: float = 0.05
    monedas_aplicables: str = "*"
    listas_aplicables: str = "*"
    vigencia_desde: str = ""
    vigencia_hasta: str | None = None
    activo: bool = True

class DiferencialCambiarioRequest(BaseModel):
    nombre: str
    tipo_diferencial: str  # 'fijo_35_ves_usd' | 'equiparar_binance' | 'diferencial_bcv_binance'
    tipo_calculo: str      # 'fijo' | 'variable'
    porcentaje_fijo: float = 0.35
    monedas_aplicables: str = "*"
    listas_aplicables: str = "*"
    vigencia_desde: str = ""
    vigencia_hasta: str | None = None
    activo: bool = True

class ToggleDescuentoRequest(BaseModel):
    tabla: str
    regla_id: str
    activo: bool

class DescuentoVolumenRequest(BaseModel):
    marca: str
    categoria: str
    litros_minimo: float
    porcentaje: float
    vigencia_desde: str
    vigencia_hasta: str | None = None
    listas_aplicables: str = "*"

_repo_cache: SheetsRepository | None = None

def get_repo() -> SheetsRepository:
    global _repo_cache
    if _repo_cache is None:
        config = AppConfig.from_env()
        print(f"DEBUG: GOOGLE_SHEETS_SPREADSHEET_ID: length={len(config.sheets.spreadsheet_id)}, repr={repr(config.sheets.spreadsheet_id)}", file=sys.stderr)
        if os.environ.get("GOOGLE_TOKEN_JSON"):
            gateway = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
        else:
            gateway = GspreadGateway(
                config.sheets.spreadsheet_id, config.sheets.service_account_file
            )
        _repo_cache = SheetsRepository(gateway)
    return _repo_cache

def get_rate_for_datetime(dt: datetime, rows: list[dict] = None) -> tuple[Decimal, Decimal]:
    if rows is None:
        repo = get_repo()
        rows = repo._g.read_rows("SerieTasas")
    if not rows:
        return Decimal("36.5"), Decimal("38.0")
    
    closest_row = None
    min_diff = None
    
    for r in rows:
        ts_str = r.get("timestamp")
        if not ts_str:
            continue
        try:
            ts_str = ts_str.replace("T", " ")
            if "." in ts_str:
                ts_str = ts_str.split(".")[0]
            row_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        except:
            try:
                row_dt = datetime.strptime(ts_str[:16], "%Y-%m-%d %H:%M")
            except:
                continue
                
        diff = abs((dt - row_dt).total_seconds())
        if min_diff is None or diff < min_diff:
            min_diff = diff
            closest_row = r
            
    if closest_row:
        return parse_decimal_safe(closest_row.get("tasa_bcv")), parse_decimal_safe(closest_row.get("tasa_binance"))
        
    return Decimal("36.5"), Decimal("38.0")

import traceback

async def run_sync_in_background():
    while True:
        try:
            print("FastAPI Daemon: Iniciando ciclo de sync incremental...")
            config = AppConfig.from_env()
            repo = get_repo()
            reader = OdooXmlRpcReader(config.odoo)
            sync = IncrementalSync(repo, reader)
            result = sync.run(datetime.now())
            print(f"FastAPI Daemon: Sync completado. {result.total} filas actualizadas.")
        except Exception as e:
            print(f"Error en daemon de sincronización: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        await asyncio.sleep(300)

async def run_scraper_in_background():
    # Esperar 30 segundos tras el arranque inicial antes del primer scrape de tasas
    await asyncio.sleep(30)
    while True:
        try:
            print("FastAPI Daemon: Iniciando ciclo de scraping de tasas (BCV y Binance)...")
            from cxc.scraper.bcv import OdooBcvClient
            from cxc.scraper.binance import BinanceClient
            from cxc.scraper.rates_scraper import RatesScraper
            from cxc.alerts import build_alerter

            config = AppConfig.from_env()
            repo = get_repo()
            scraper = RatesScraper(
                repo,
                BinanceClient(config.binance),
                OdooBcvClient(config.odoo),
                build_alerter(config.alert),
                config.scraper_policy,
            )
            from datetime import timezone, timedelta
            now_utc = datetime.now(timezone.utc)
            now_caracas = (now_utc - timedelta(hours=4)).replace(tzinfo=None)
            fila = scraper.run(now_caracas)
            print(f"FastAPI Daemon: Tasas de cambio actualizadas. BCV: {fila.tasa_bcv}, Binance: {fila.tasa_binance}")
        except Exception as e:
            print(f"Error en daemon de scraping de tasas: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        # Repetir cada 1 hora (3600 segundos)
        await asyncio.sleep(3600)

@app.on_event("startup")
async def startup_event():
    # Start the synchronization and scraping daemon loops in the background
    asyncio.create_task(run_sync_in_background())
    asyncio.create_task(run_scraper_in_background())

@app.get("/", response_class=HTMLResponse)
async def read_index():
    index_path = os.path.join(static_dir, "index.html")
    if not os.path.exists(index_path):
        return HTMLResponse("<html><body><h1>Servidor Iniciado</h1><p>Frontend no encontrado en static/index.html</p></body></html>")
    with open(index_path, 'r', encoding='utf-8') as f:
        return f.read()

@app.get("/api/resumen")
async def get_resumen():
    try:
        repo = get_repo()
        # 1. Total por cobrar (Orders not invoiced)
        ordenes = repo.all_ordenes()
        total_por_cobrar = sum(o.monto_total for o in ordenes if not o.facturada)
        
        # 2. Pagos sin asignar (not in Vinculaciones)
        pagos = repo._g.read_rows("Pagos")
        vincs = repo.all_vinculaciones()
        linked_pago_ids = {v.pago_id for v in vincs}
        
        pagos_pendientes_monto = Decimal("0")
        for p in pagos:
            pid = str(p.get("pago_id", ""))
            if pid and pid not in linked_pago_ids:
                try:
                    # Let's count them in USD (if VES, convert or count original)
                    monto = parse_decimal_safe(p.get("monto", "0"))
                    pagos_pendientes_monto += monto
                except:
                    pass
                    
        # 3. Alertas rojas in Conciliación
        concs = repo.all_conciliaciones()
        alertas_rojas = sum(1 for c in concs if c.resultado.value == "rojo")
        
        return {
            "total_por_cobrar_usd": float(total_por_cobrar),
            "pagos_sin_asignar_usd": float(pagos_pendientes_monto),
            "alertas_reconciliacion": alertas_rojas
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/pagos-pendientes")
async def get_pagos_pendientes():
    try:
        repo = get_repo()
        # Read raw rows to preserve client names/emails
        pagos = repo._g.read_rows("Pagos")
        vincs = repo.all_vinculaciones()
        
        # Read SerieTasas once to avoid N+1 Sheets API quota errors
        tasas_rows = repo._g.read_rows("SerieTasas")
        
        # Load all clients once to avoid N+1 queries to Google Sheets API
        clientes_rows = repo._g.read_rows("Clientes")
        from cxc.sheets import serde
        clientes_map = {}
        for r in clientes_rows:
            cid = str(r.get("cliente_id", ""))
            if cid:
                try:
                    clientes_map[cid] = serde.cliente_from_row(r)
                except:
                    pass
        
        # Gather amounts linked per pago_id
        linked_amounts = {}
        for v in vincs:
            linked_amounts[v.pago_id] = linked_amounts.get(v.pago_id, Decimal("0")) + v.monto_aplicado
            
        pendientes = []
        for p in pagos:
            pago_id = str(p.get("pago_id", ""))
            if not pago_id:
                continue
            monto_original = parse_decimal_safe(p.get("monto", "0"))
            monto_vinculado = linked_amounts.get(pago_id, Decimal("0"))
            
            saldo_pendiente = monto_original - monto_vinculado
            if saldo_pendiente > Decimal("0.05"):
                # Retrieve client name from cache map
                cliente_id = str(p.get("cliente_id", ""))
                cliente = clientes_map.get(cliente_id)
                cliente_nombre = cliente.nombre if cliente else f"Cliente ID: {cliente_id}"
                
                moneda = p.get("moneda", "USD")
                fecha_str = p.get("fecha_pago", "")
                
                dt_pago = datetime.now()
                if fecha_str:
                    try:
                        if len(fecha_str) == 10:
                            dt_pago = datetime.strptime(f"{fecha_str} 12:00:00", "%Y-%m-%d %H:%M:%S")
                        else:
                            fs = fecha_str.replace("T", " ")
                            if "." in fs:
                                fs = fs.split(".")[0]
                            dt_pago = datetime.strptime(fs, "%Y-%m-%d %H:%M:%S")
                    except:
                        pass
                
                bcv, binance = get_rate_for_datetime(dt_pago, tasas_rows)
                
                if moneda == "VES":
                    equiv_usd_bcv = saldo_pendiente / bcv
                    equiv_usd_binance = saldo_pendiente / binance
                else:
                    equiv_usd_bcv = saldo_pendiente
                    equiv_usd_binance = saldo_pendiente
                
                pendientes.append({
                    "pago_id": pago_id,
                    "cliente_id": cliente_id,
                    "cliente_nombre": cliente_nombre,
                    "monto": float(saldo_pendiente),
                    "monto_original": float(monto_original),
                    "moneda": moneda,
                    "fecha": fecha_str,
                    "metodo_pago": p.get("metodo_pago", ""),
                    "equiv_usd_bcv": float(equiv_usd_bcv),
                    "equiv_usd_binance": float(equiv_usd_binance)
                })
        return pendientes
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/ordenes-pendientes/{cliente_id}")
async def get_ordenes_pendientes(cliente_id: str):
    try:
        repo = get_repo()
        ordenes = repo.all_ordenes()
        vincs = repo.all_vinculaciones()
        
        # Calculate sum of linked payment amounts per so_id
        linked_by_so = {}
        for v in vincs:
            # We assume linked amount is matching currency of the order (USD)
            # or in terms of the applied amount
            linked_by_so[v.so_id] = linked_by_so.get(v.so_id, Decimal("0")) + v.monto_aplicado
            
        # Filter outstanding orders for this client
        pendientes = []
        for o in ordenes:
            if o.cliente_id == cliente_id and not o.facturada:
                pagado = linked_by_so.get(o.so_id, Decimal("0"))
                saldo = o.monto_total - pagado
                
                # Show only orders that still have a outstanding balance (> $0.05)
                if saldo > Decimal("0.05"):
                    pendientes.append({
                        "so_id": o.so_id,
                        "fecha": o.fecha.isoformat(),
                        "monto_total": float(o.monto_total),
                        "saldo_pendiente": float(saldo),
                        "vendedor": o.vendedor_email
                    })
        return pendientes
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/vincular")
async def post_vincular(req: VinculacionRequest, background_tasks: BackgroundTasks):
    try:
        repo = get_repo()
        
        # Load Odoo connection to get exchange rates
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        
        # Fetch latest exchange rates from SerieTasas or Odoo
        last_tasa = repo.last_serie_tasa()
        tasa_bcv = last_tasa.tasa_bcv if last_tasa else Decimal("36.5")
        tasa_binance = last_tasa.tasa_binance if last_tasa else Decimal("38.0")
        
        # Fetch payment to get currency
        pago = repo.get_pago(req.pago_id)
        if not pago:
            raise HTTPException(status_code=404, detail="Pago no encontrado.")
            
        monto_dec = Decimal(str(req.monto_aplicado))
        
        # Calculate equivalents
        if pago.moneda == "USD":
            equiv_usd_bcv = monto_dec
            equiv_usd_binance = monto_dec
            equiv_ves_bcv = monto_dec * tasa_bcv
            equiv_ves_binance = monto_dec * tasa_binance
        else:
            equiv_usd_bcv = monto_dec / tasa_bcv
            equiv_usd_binance = monto_dec / tasa_binance
            equiv_ves_bcv = monto_dec
            equiv_ves_binance = monto_dec

        vinc_id = f"VINC_{req.pago_id}_{req.so_id}"
        vinc = Vinculacion(
            vinc_id=vinc_id,
            pago_id=req.pago_id,
            so_id=req.so_id,
            monto_aplicado=monto_dec,
            hora_pago_confirmada=datetime.combine(pago.fecha_pago, datetime.min.time()),
            tasa_bcv_aplicada=tasa_bcv,
            tasa_binance_aplicada=tasa_binance,
            es_tasa_heredada=False,
            equiv_usd_bcv=equiv_usd_bcv,
            equiv_usd_binance=equiv_usd_binance,
            equiv_ves_bcv=equiv_ves_bcv,
            equiv_ves_binance=equiv_ves_binance,
            confirmado_por="Panel de Control Web",
            timestamp_registro=datetime.now(),
            estado=EstadoVinculacion.PENDIENTE,
            moneda_abono=Moneda(pago.moneda),
            tipo_tasa_abono=TipoTasa.BCV
        )
        
        # Write vinculacion row
        repo.update_vinculacion(vinc)
        
        # Trigger background run of Engine and Reconciler to refresh totals in Sheets
        background_tasks.add_task(recalculate_all, req.so_id)
        
        return {"status": "success", "message": "Vinculación guardada. Recálculo en segundo plano iniciado."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/bandeja")
async def get_bandeja():
    try:
        repo = get_repo()
        bandeja = repo.all_bandeja()
        concs = {c.so_id: c for c in repo.all_conciliaciones()}
        
        # Load Odoo product prices for list 4 once
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        lista_usd_id = int(os.environ.get("ODOO_PRICELIST_USD", "4"))
        rules = execute(
            "product.pricelist.item",
            "search_read",
            [[["pricelist_id", "=", lista_usd_id], ["compute_price", "=", "fixed"]]],
            {"fields": ["product_tmpl_id", "fixed_price"]}
        )
        prices_usd = {}
        for r in rules:
            prod_tmpl_id = r.get("product_tmpl_id")
            pt_id = prod_tmpl_id[0] if isinstance(prod_tmpl_id, list) else prod_tmpl_id
            if pt_id:
                prices_usd[pt_id] = float(r.get("fixed_price") or 0.0)

        all_lines = repo._g.read_rows("LineasOrden")
        lines_by_so = {}
        for r in all_lines:
            so = r.get("so_id", "")
            if so:
                lines_by_so.setdefault(so, []).append(r)
                
        resultados = []
        for b in bandeja:
            conc = concs.get(b.so_id)
            
            # Find order to check its original pricelist
            ord_row = repo.get_orden(b.so_id)
            lista_precios_orig = ord_row.lista_precios if ord_row else "4"
            
            # Compute total proyectado USD under List 4
            order_lines = lines_by_so.get(b.so_id, [])
            total_proyectado_usd = Decimal("0.0")
            for ln in order_lines:
                qty = Decimal(str(ln.get("cantidad", "0")))
                prod_raw = ln.get("producto", "")
                pt_id = None
                if isinstance(prod_raw, str):
                    if prod_raw.startswith("["):
                        try:
                            import json
                            parsed = json.loads(prod_raw.replace("'", '"'))
                            pt_id = int(parsed[0])
                        except:
                            import re
                            match = re.search(r'\d+', prod_raw)
                            if match:
                                pt_id = int(match.group())
                    elif prod_raw.isdigit():
                        pt_id = int(prod_raw)
                elif isinstance(prod_raw, (int, float)):
                    pt_id = int(prod_raw)
                    
                price_usd = Decimal(str(prices_usd.get(pt_id))) if pt_id in prices_usd else Decimal(str(ln.get("precio_unitario", "0")))
                total_proyectado_usd += qty * price_usd
            
            pct = Decimal("0")
            if b.precio_base_calculado > 0:
                pct = b.total_descuentos / b.precio_base_calculado
                
            total_descuentos_proy = total_proyectado_usd * pct
            total_motor_proy = total_proyectado_usd - total_descuentos_proy
            
            lista_name = "Lista USD (#4)" if b.lista_aplicada == "4" else f"Precio VES (#{b.lista_aplicada})"
            
            resultados.append({
                "so_id": b.so_id,
                "lista_aplicada": lista_name,
                "precio_base": float(b.precio_base_calculado),
                "total_descuentos": float(b.total_descuentos),
                "total_motor": float(b.total_motor),
                "total_proyectado_usd": float(total_motor_proy) if lista_precios_orig != "4" else float(b.total_motor),
                "candidata_a_cierre": b.candidata_a_cierre,
                "estado": b.estado.value,
                "reconciliacion": {
                    "monto_odoo": float(conc.monto_odoo) if conc else 0.0,
                    "ncs_odoo": float(conc.ncs_odoo) if conc else 0.0,
                    "diferencia": float(conc.diferencia) if conc else 0.0,
                    "resultado": conc.resultado.value if conc else "pendiente"
                } if conc else None
            })
        return resultados
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

def recalculate_all(so_id: str):
    try:
        print(f"Recalculando orden {so_id}...")
        config = AppConfig.from_env()
        if os.environ.get("GOOGLE_TOKEN_JSON"):
            gateway = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
        else:
            gateway = GspreadGateway(
                config.sheets.spreadsheet_id, config.sheets.service_account_file
            )
        repo = SheetsRepository(gateway)
        execute = _connect(config.odoo)
        pricelist_ids = {
            config.engine.lista_usd: int(os.environ.get("ODOO_PRICELIST_USD", "4")),
            config.engine.lista_bcv: int(os.environ.get("ODOO_PRICELIST_BCV", "5")),
        }
        resolver = OdooPriceResolver(execute, pricelist_ids)
        runner = EngineRunner(repo, resolver, config.engine)
        
        # Calculate this SO
        runner.run_orden(so_id, date.today())
        
        # Run Reconciler to sync semaphores
        facturas = OdooFacturasReader(execute)
        Reconciler(repo, facturas, config.reconciliation).run()
        print(f"Recálculo de {so_id} completado con éxito.")
    except Exception as e:
        print(f"Error al recalcular {so_id}: {e}", file=sys.stderr)

@app.get("/api/reporte-saldos")
async def get_reporte_saldos():
    try:
        repo = get_repo()
        ordenes = repo.all_ordenes()
        vincs = repo.all_vinculaciones()
        concs = {c.so_id: c for c in repo.all_conciliaciones()}
        
        # Load clients once
        clientes_rows = repo._g.read_rows("Clientes")
        clientes_map = {r.get("cliente_id"): r.get("nombre", "") for r in clientes_rows}
        
        # Calculate sum of linked payment amounts per so_id
        linked_by_so = {}
        for v in vincs:
            linked_by_so[v.so_id] = linked_by_so.get(v.so_id, Decimal("0")) + v.monto_aplicado
            
        # Fetch product fixed prices for list 4 once
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        lista_usd_id = int(os.environ.get("ODOO_PRICELIST_USD", "4"))
        rules = execute(
            "product.pricelist.item",
            "search_read",
            [[["pricelist_id", "=", lista_usd_id], ["compute_price", "=", "fixed"]]],
            {"fields": ["product_tmpl_id", "fixed_price"]}
        )
        prices_usd = {}
        for r in rules:
            prod_tmpl_id = r.get("product_tmpl_id")
            pt_id = prod_tmpl_id[0] if isinstance(prod_tmpl_id, list) else prod_tmpl_id
            if pt_id:
                prices_usd[pt_id] = float(r.get("fixed_price") or 0.0)

        all_lines = repo._g.read_rows("LineasOrden")
        lines_by_so = {}
        for r in all_lines:
            so = r.get("so_id", "")
            if so:
                lines_by_so.setdefault(so, []).append(r)

        bandeja_rows = repo.all_bandeja()
        bandeja_map = {b.so_id: b for b in bandeja_rows}

        reporte = []
        for o in ordenes:
            client_name = clientes_map.get(o.cliente_id, f"Cliente ID: {o.cliente_id}")
            pagado = linked_by_so.get(o.so_id, Decimal("0"))
            saldo = o.monto_total - pagado
            conc = concs.get(o.so_id)
            
            # Compute actual subtotal from lines
            order_lines = lines_by_so.get(o.so_id, [])
            subtotal = sum(Decimal(str(ln.get("cantidad", "0"))) * Decimal(str(ln.get("precio_unitario", "0"))) for ln in order_lines)
            
            # Compute projected USD subtotal and total under List 4 (USD)
            total_proyectado_usd = Decimal("0.0")
            for ln in order_lines:
                qty = Decimal(str(ln.get("cantidad", "0")))
                prod_raw = ln.get("producto", "")
                pt_id = None
                if isinstance(prod_raw, str):
                    if prod_raw.startswith("["):
                        try:
                            import json
                            parsed = json.loads(prod_raw.replace("'", '"'))
                            pt_id = int(parsed[0])
                        except:
                            import re
                            match = re.search(r'\d+', prod_raw)
                            if match:
                                pt_id = int(match.group())
                    elif prod_raw.isdigit():
                        pt_id = int(prod_raw)
                elif isinstance(prod_raw, (int, float)):
                    pt_id = int(prod_raw)
                    
                price_usd = Decimal(str(prices_usd.get(pt_id))) if pt_id in prices_usd else Decimal(str(ln.get("precio_unitario", "0")))
                total_proyectado_usd += qty * price_usd
                
            lista_name = "Lista USD (#4)" if o.lista_precios == "4" else f"Precio VES (#{o.lista_precios})"
            monto_total_proyectado_usd = float(total_proyectado_usd) if o.lista_precios != "4" else float(o.monto_total)
            
            # Engine calculation data
            b = bandeja_map.get(o.so_id)
            if b:
                total_descuentos_monto = float(b.total_descuentos + b.ncs_calculadas)
                total_con_descuentos = float(b.total_motor)
                descuentos_desglose = []
                for d in b.descuentos_detalle:
                    descuentos_desglose.append({
                        "origen": d.origen,
                        "descripcion": d.descripcion,
                        "monto": float(d.monto)
                    })
                if b.ncs_calculadas > Decimal("0") and not any(d["origen"] == "primera_compra" for d in descuentos_desglose):
                    descuentos_desglose.append({
                        "origen": "primera_compra",
                        "descripcion": "Obsequio / Promo Primera Compra",
                        "monto": float(b.ncs_calculadas)
                    })
            else:
                total_descuentos_monto = 0.0
                total_con_descuentos = float(o.monto_total)
                descuentos_desglose = []

            saldo_deudor_con_descuentos = max(0.0, total_con_descuentos - float(pagado))

            reporte.append({
                "so_id": o.so_id,
                "cliente_nombre": client_name,
                "fecha": o.fecha.isoformat(),
                "lista_precios": lista_name,
                "subtotal": float(subtotal),
                "monto_total": float(o.monto_total),
                "monto_total_proyectado_usd": float(monto_total_proyectado_usd),
                "monto_pagado": float(pagado),
                "saldo_deudor": float(saldo) if saldo > Decimal("0") else 0.0,
                "total_con_descuentos": total_con_descuentos,
                "total_descuentos_monto": total_descuentos_monto,
                "saldo_deudor_con_descuentos": float(saldo_deudor_con_descuentos),
                "descuentos_desglose": descuentos_desglose,
                "facturada": o.facturada,
                "candidata_a_cierre": saldo <= Decimal("0.05") or saldo_deudor_con_descuentos <= 0.05,
                "reconciliacion": {
                    "resultado": conc.resultado.value if conc else "pendiente"
                } if conc else None
            })
        return reporte
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config/tasas")
async def get_config_tasas():
    try:
        repo = get_repo()
        # Read the last 15 raw rows from SerieTasas
        filas = repo._g.read_rows("SerieTasas")[-15:]
        tasas = []
        for f in reversed(filas):
            tbcv = float(parse_decimal_safe(f.get("tasa_bcv", "0")))
            tbin = float(parse_decimal_safe(f.get("tasa_binance", "0")))
            diff_bs = tbin - tbcv
            diff_pct = (diff_bs / tbin * 100) if tbin > 0 else 0.0
            tasas.append({
                "timestamp": f.get("timestamp", ""),
                "tasa_bcv": tbcv,
                "tasa_binance": tbin,
                "diferencia_bs": round(diff_bs, 2),
                "diferencia_pct": round(diff_pct, 2),
                "fuente": f.get("fuente", "")
            })
        return tasas
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/tasas")
async def post_config_tasas(req: TasaRequest):
    try:
        repo = get_repo()
        from cxc.models import SerieTasa
        tasa = SerieTasa(
            timestamp=datetime.now(),
            tasa_bcv=Decimal(str(req.tasa_bcv)),
            tasa_binance=Decimal(str(req.tasa_binance)),
            fuente="Carga Manual Web",
            es_heredada=False,
            capturada_ok=True
        )
        repo.append_serie_tasa(tasa)
        return {"status": "success", "message": "Tasa manual registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config/feriados")
async def get_config_feriados():
    try:
        repo = get_repo()
        feriados = repo.feriados()
        return [
            {
                "fecha": f.fecha.isoformat(),
                "descripcion": f.descripcion,
                "tipo": f.tipo.value
            } for f in feriados
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/feriados")
async def post_config_feriados(req: FeriadoRequest):
    try:
        repo = get_repo()
        from cxc.models import Feriado, TipoFeriado
        from cxc.sheets import serde, gateway as g
        feriado = Feriado(
            fecha=date.fromisoformat(req.fecha),
            descripcion=req.descripcion,
            tipo=TipoFeriado.NACIONAL
        )
        repo._g.append_row(g.T_FERIADOS, serde.feriado_to_row(feriado))
        return {"status": "success", "message": "Feriado registrado con éxito."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/mapa-vinculaciones")
async def get_mapa_vinculaciones():
    try:
        repo = get_repo()
        vincs = repo.all_vinculaciones()
        
        # Load clients once
        clientes_rows = repo._g.read_rows("Clientes")
        clientes_map = {r.get("cliente_id"): r.get("nombre", "") for r in clientes_rows}
        
        # Load all orders
        ordenes = {o.so_id: o for o in repo.all_ordenes()}
        
        # Fetch Odoo Invoices in batch
        so_ids = list(ordenes.keys())
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        invoices = execute(
            "account.move",
            "search_read",
            [[["invoice_origin", "in", so_ids], ["state", "=", "posted"], ["move_type", "in", ["out_invoice", "out_refund"]]]],
            {"fields": ["id", "name", "invoice_origin", "amount_total", "amount_untaxed", "amount_tax", "amount_residual", "payment_state", "move_type"]}
        )
        invoices_by_so = {}
        for inv in invoices:
            so = str(inv.get("invoice_origin", "")).strip()
            if so:
                invoices_by_so.setdefault(so, []).append(inv)
                
        resultado = []
        for v in vincs:
            o = ordenes.get(v.so_id)
            client_name = clientes_map.get(o.cliente_id, "Desconocido") if o else "Desconocido"
            
            # Fetch invoice details for this SO
            inv_name = "N/A"
            inv_total = 0.0
            inv_subtotal = 0.0
            inv_tax = 0.0
            inv_residual = 0.0
            
            if o and o.so_id in invoices_by_so:
                inv_list = invoices_by_so[o.so_id]
                inv_names = [inv.get("name", "") for inv in inv_list]
                inv_name = ", ".join(inv_names)
                
                inv_total = sum(float(inv.get("amount_total", 0.0)) for inv in inv_list)
                inv_subtotal = sum(float(inv.get("amount_untaxed", 0.0)) for inv in inv_list)
                inv_tax = sum(float(inv.get("amount_tax", 0.0)) for inv in inv_list)
                inv_residual = sum(float(inv.get("amount_residual", 0.0)) for inv in inv_list)
                
            resultado.append({
                "vinc_id": v.vinc_id,
                "pago_id": v.pago_id,
                "so_id": v.so_id,
                "cliente_nombre": client_name,
                "monto_aplicado": float(v.monto_aplicado),
                "moneda": v.moneda_abono.value,
                "fecha_pago": v.hora_pago_confirmada.date().isoformat(),
                "invoice_id": inv_name,
                "order_details": {
                    "total": float(o.monto_total) if o else 0.0,
                    "subtotal": float(o.monto_total) / 1.16 if o else 0.0,
                    "iva": float(o.monto_total) - (float(o.monto_total) / 1.16) if o else 0.0
                },
                "invoice_details": {
                    "total": inv_total,
                    "subtotal": inv_subtotal,
                    "iva": inv_tax,
                    "saldo_deudor": inv_residual,
                    "retencion_iva_est": inv_tax * 0.75
                }
            })
            
        return resultado
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config/descuentos-marca")
async def get_config_descuentos_marca():
    try:
        repo = get_repo()
        rules = repo.descuentos_marca_categoria()
        return [
            {
                "regla_id": r.regla_id,
                "marca": r.marca,
                "categoria": r.categoria,
                "tipo_descuento": r.tipo_descuento,
                "porcentaje": float(r.porcentaje),
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "listas_aplicables": r.listas_aplicables,
                "activo": r.activo
            } for r in rules
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/descuentos-marca")
async def post_config_descuentos_marca(req: DescuentoMarcaRequest):
    try:
        repo = get_repo()
        from cxc.models import DescuentoMarcaCategoria
        from cxc.sheets import serde, gateway as g
        import uuid
        
        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None
        
        # Check date overlap with active rules of same type, brand, category, list
        existing = repo.descuentos_marca_categoria()
        for r in existing:
            if r.activo and r.tipo_descuento == req.tipo_descuento:
                if r.marca == req.marca and r.categoria == req.categoria:
                    lists_overlap = (r.listas_aplicables == "*" or req.listas_aplicables == "*" or r.listas_aplicables == req.listas_aplicables)
                    if lists_overlap:
                        h1 = v_hasta if v_hasta is not None else date(9999, 12, 31)
                        h2 = r.vigencia_hasta if r.vigencia_hasta is not None else date(9999, 12, 31)
                        if max(v_desde, r.vigencia_desde) <= min(h1, h2):
                            raise HTTPException(
                                status_code=400,
                                detail=f"Conflicto: ya existe la regla activa {r.regla_id} ({r.vigencia_desde} a {r.vigencia_hasta or 'siempre'}) para esta marca/categoría/lista."
                            )

        regla_id = f"REG_{uuid.uuid4().hex[:8].upper()}"
        rule = DescuentoMarcaCategoria(
            regla_id=regla_id,
            marca=req.marca,
            categoria=req.categoria,
            tipo_descuento=req.tipo_descuento,
            porcentaje=Decimal(str(req.porcentaje)),
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            listas_aplicables=req.listas_aplicables,
            activo=True
        )
        repo._g.append_row(g.T_DESCUENTOS, serde.descuento_to_row(rule))
        return {"status": "success", "message": "Regla de descuento registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config/listas-precio")
async def get_config_listas_precio():
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        
        # Fetch all pricelists including archived
        pricelists = execute(
            "product.pricelist",
            "search_read",
            [[["active", "in", [True, False]]]],
            {"fields": ["id", "name", "currency_id", "active"]}
        )
        
        # Fetch items/rules for these pricelists
        list_ids = [pl["id"] for pl in pricelists]
        items = execute(
            "product.pricelist.item",
            "search_read",
            [[["pricelist_id", "in", list_ids]]],
            {"fields": ["pricelist_id", "fixed_price", "percent_price", "date_start", "date_end", "product_tmpl_id"]}
        )
        
        # Group items by pricelist ID
        items_by_list = {}
        for item in items:
            pl_id = item["pricelist_id"][0]
            items_by_list.setdefault(pl_id, []).append(item)
            
        resultado = []
        for pl in pricelists:
            pl_items = items_by_list.get(pl["id"], [])
            rules = []
            for item in pl_items:
                prod = item.get("product_tmpl_id")
                prod_name = prod[1] if isinstance(prod, list) and len(prod) > 1 else "Todos los productos"
                
                rules.append({
                    "producto": prod_name,
                    "precio_fijo": float(item.get("fixed_price") or 0.0),
                    "descuento_porcentaje": float(item.get("percent_price") or 0.0),
                    "fecha_inicio": item.get("date_start") or "N/A",
                    "fecha_fin": item.get("date_end") or "N/A"
                })
                
            resultado.append({
                "id": pl["id"],
                "name": pl["name"],
                "moneda": pl["currency_id"][1] if isinstance(pl["currency_id"], list) else "USD",
                "active": pl["active"],
                "reglas": rules
            })
            
        return resultado
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config/meta")
async def get_config_meta():
    try:
        repo = get_repo()
        rows = repo._g.read_rows("_Meta")
        meta = {}
        for r in rows:
            k = r.get("key")
            if k:
                meta[k] = r.get("value", "")
        # Defaults
        if "cash_window_business_days" not in meta:
            meta["cash_window_business_days"] = "3"
        if "descuento_recompra" not in meta:
            meta["descuento_recompra"] = "0.05"
        return meta
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/meta")
async def post_config_meta(req: MetaRequest):
    try:
        repo = get_repo()
        from cxc.sheets import gateway as g
        repo._g.upsert_row("_Meta", "key", {"key": "cash_window_business_days", "value": str(req.cash_window_business_days)})
        repo._g.upsert_row("_Meta", "key", {"key": "descuento_recompra", "value": str(req.descuento_recompra)})
        
        # Sync to ReglasRecurrencia sheet for RECOMPRA rule
        rules = repo._g.read_rows("ReglasRecurrencia")
        recompra_exists = False
        for r in rules:
            if r.get("condicion") == "recompra":
                recompra_exists = True
                r["porcentaje"] = str(req.descuento_recompra)
                repo._g.upsert_row("ReglasRecurrencia", "regla_id", r)
                break
        if not recompra_exists:
            repo._g.append_row("ReglasRecurrencia", {
                "regla_id": "REG_RECOMPRA",
                "condicion": "recompra",
                "porcentaje": str(req.descuento_recompra),
                "vigencia_desde": date.today().isoformat(),
                "vigencia_hasta": "",
                "activo": "TRUE"
            })
            
        return {"status": "success", "message": "Ajustes globales actualizados correctamente."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/odoo/marcas")
async def get_odoo_marcas():
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        brands = execute("product.brand", "search_read", [[]], {"fields": ["name"]})
        return [b["name"] for b in brands]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/odoo/categorias")
async def get_odoo_categorias():
    return ["Comercial", "Industrial"]

@app.get("/api/config/tasa-referencia")
async def get_tasa_referencia(fecha: str, hora: str):
    try:
        dt_str = f"{fecha} {hora}:00"
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        bcv, binance = get_rate_for_datetime(dt)
        return {
            "tasa_bcv": float(bcv),
            "tasa_binance": float(binance)
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/config/tasas/sync-odoo")
async def post_sync_odoo_rates():
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        
        # Fetch rates since 2026-01-01
        rates = execute(
            "res.currency.rate",
            "search_read",
            [[["name", ">=", "2026-01-01"], ["currency_id", "=", 1]]],
            {"fields": ["name", "inverse_company_rate"]}
        )
        
        repo = get_repo()
        existing_rows = repo._g.read_rows("SerieTasas")
        existing_dates = set()
        for r in existing_rows:
            ts = r.get("timestamp", "")
            if ts:
                existing_dates.add(ts.split(" ")[0].split("T")[0])
                
        from cxc.sheets import serde, gateway as g
        from cxc.models import SerieTasa
        
        rates.sort(key=lambda x: x["name"])
        added_count = 0
        for rate in rates:
            date_str = rate["name"]
            if date_str not in existing_dates:
                ts = datetime.combine(date.fromisoformat(date_str), datetime.min.time().replace(hour=8))
                val = rate.get("inverse_company_rate")
                bcv = Decimal(str(val)) if val else Decimal("1.0")
                binance = bcv * Decimal("1.05") 
                
                tasa = SerieTasa(
                    timestamp=ts,
                    tasa_bcv=bcv,
                    tasa_binance=binance,
                    fuente="Odoo Sync",
                    es_heredada=False,
                    capturada_ok=True
                )
                repo.append_serie_tasa(tasa)
                added_count += 1

        # Automated Holidays Detection Heuristics
        # Days where BCV didn't publish rates (except weekends and Mondays to avoid bank holidays)
        from datetime import timedelta
        start_date = date(2026, 1, 1)
        end_date = date.today() - timedelta(days=1)
        
        odoo_rate_dates = {r["name"] for r in rates}
        existing_feriados = {f.fecha for f in repo.feriados()}
        detected_feriados_count = 0
        
        current = start_date
        while current <= end_date:
            wday = current.weekday()
            if wday not in (0, 5, 6): # Tuesday to Friday
                date_str = current.isoformat()
                if date_str not in odoo_rate_dates and current not in existing_feriados:
                    from cxc.models import Feriado, TipoFeriado
                    feriado = Feriado(
                        fecha=current,
                        descripcion="Feriado detectado por BCV (sin tasa)",
                        tipo=TipoFeriado.NACIONAL
                    )
                    repo._g.append_row(g.T_FERIADOS, serde.feriado_to_row(feriado))
                    detected_feriados_count += 1
            current += timedelta(days=1)
                
        msg = f"Sincronizados {added_count} registros de tasas desde Odoo."
        if detected_feriados_count > 0:
            msg += f" Detectados y registrados {detected_feriados_count} nuevos feriados nacionales automáticos."
            
        return {"status": "success", "message": msg}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/odoo/productos")
async def get_odoo_productos():
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        
        # Fetch active products with product_volume (litros)
        prods = execute(
            "product.template",
            "search_read",
            [[["sale_ok", "=", True], ["active", "=", True]]],
            {"fields": ["id", "name", "default_code", "list_price", "product_volume"], "limit": 100}
        )
        
        lista_usd_id = int(os.environ.get("ODOO_PRICELIST_USD", "4"))
        lista_ves_id = int(os.environ.get("ODOO_PRICELIST_BCV", "5"))
        
        # Query rules directly from product.pricelist.item to get exact raw pricing entered
        rules = execute(
            "product.pricelist.item",
            "search_read",
            [[["pricelist_id", "in", [lista_usd_id, lista_ves_id]], ["compute_price", "=", "fixed"]]],
            {"fields": ["pricelist_id", "product_tmpl_id", "fixed_price"]}
        )
        
        prices_usd = {}
        prices_ves = {}
        for r in rules:
            pl_id = r.get("pricelist_id")
            p_id = pl_id[0] if isinstance(pl_id, list) else pl_id
            prod_tmpl_id = r.get("product_tmpl_id")
            pt_id = prod_tmpl_id[0] if isinstance(prod_tmpl_id, list) else prod_tmpl_id
            
            if pt_id:
                if p_id == lista_usd_id:
                    prices_usd[pt_id] = float(r.get("fixed_price") or 0.0)
                elif p_id == lista_ves_id:
                    prices_ves[pt_id] = float(r.get("fixed_price") or 0.0)
                    
        resultado = []
        for p in prods:
            pid = p["id"]
            resultado.append({
                "id": pid,
                "nombre": p["name"],
                "ref_interna": p.get("default_code") or "N/A",
                "precio_publico": float(p.get("list_price") or 0.0),
                "precio_usd": prices_usd.get(pid, float(p.get("list_price") or 0.0)),
                "precio_ves_usd": prices_ves.get(pid, 0.0),
                "litros": float(p.get("product_volume") or 0.0)
            })
        return resultado
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/odoo/clientes-auditoria")
async def get_odoo_clientes_auditoria():
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        
        # Fetch customers
        partners = execute(
            "res.partner",
            "search_read",
            [[["customer_rank", ">", 0]]],
            {"fields": ["id", "name", "create_date"]}
        )
        
        # Fetch sales count & last order date
        orders = execute(
            "sale.order",
            "search_read",
            [[["state", "in", ["sale", "done"]]]],
            {"fields": ["partner_id", "date_order"]}
        )
        
        stats = {}
        for o in orders:
            pid_info = o.get("partner_id")
            if isinstance(pid_info, list) and len(pid_info) > 0:
                pid = pid_info[0]
                date_str = o.get("date_order", "")
                
                s = stats.setdefault(pid, {"count": 0, "last_date": ""})
                s["count"] += 1
                if date_str and (not s["last_date"] or date_str > s["last_date"]):
                    s["last_date"] = date_str
                    
        resultado = []
        for p in partners:
            pid = p["id"]
            p_stats = stats.get(pid, {"count": 0, "last_date": "N/A"})
            resultado.append({
                "id": pid,
                "nombre": p["name"],
                "fecha_creacion": p.get("create_date", "N/A").split(" ")[0] if p.get("create_date") else "N/A",
                "ventas_cantidad": p_stats["count"],
                "fecha_ultima_venta": p_stats["last_date"].split(" ")[0] if p_stats["last_date"] != "N/A" else "N/A"
            })
        return resultado
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config/promociones")
async def get_config_promociones():
    try:
        repo = get_repo()
        promos = repo.promociones_primera_compra()
        return [
            {
                "regla_id": p.regla_id,
                "tipo_beneficio": p.tipo_beneficio,
                "productos": p.productos,
                "valor": str(p.valor),
                "compra_minima": str(p.compra_minima),
                "descuento_fallback": str(getattr(p, 'descuento_fallback', '0')),
                "regalo_tipo": p.regalo_tipo,
                "categorias_aplica": getattr(p, 'categorias_aplica', 'Comercial'),
                "vigencia_desde": p.vigencia_desde.isoformat(),
                "vigencia_hasta": p.vigencia_hasta.isoformat() if p.vigencia_hasta else None,
                "activo": p.activo
            } for p in promos
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/promociones")
async def post_config_promociones(req: PromocionRequest):
    try:
        repo = get_repo()
        from cxc.models import PromocionPrimeraCompra
        from cxc.sheets import serde, gateway as g

        v_desde = date.fromisoformat(req.vigencia_desde)
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        # Check date overlap with active first purchase promos
        existing = repo.promociones_primera_compra()
        for r in existing:
            if r.activo:
                h1 = v_hasta if v_hasta is not None else date(9999, 12, 31)
                h2 = r.vigencia_hasta if r.vigencia_hasta is not None else date(9999, 12, 31)
                if max(v_desde, r.vigencia_desde) <= min(h1, h2):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Conflicto: ya existe la promoción activa {r.regla_id} ({r.vigencia_desde} a {r.vigencia_hasta or 'siempre'})."
                    )

        regla_id = f"PROMO_{uuid.uuid4().hex[:8].upper()}"

        promo = PromocionPrimeraCompra(
            regla_id=regla_id,
            tipo_beneficio=req.tipo_beneficio,
            productos=req.productos,
            valor=Decimal(str(req.valor)),
            compra_minima=Decimal(str(req.compra_minima)),
            regalo_tipo=req.regalo_tipo,
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            descuento_fallback=Decimal(str(req.descuento_fallback)),
            categorias_aplica=req.categorias_aplica,
            activo=req.activo
        )
        row = serde.promocion_to_row(promo)
        repo._g.append_row(g.T_PROMO_PRIMERA, row)
        return {"status": "success", "message": "Promoción registrada correctamente."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config/exclusiones")
async def get_config_exclusiones():
    try:
        repo = get_repo()
        excls = repo.exclusiones()
        return [
            {
                "regla_tipo_a": e.regla_tipo_a,
                "regla_tipo_b": e.regla_tipo_b,
                "activo": e.activo
            } for e in excls
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/config/exclusiones")
async def post_config_exclusiones(req: ExclusionRequest):
    try:
        repo = get_repo()
        from cxc.models import ExclusionRegla
        rule = ExclusionRegla(
            regla_tipo_a=req.regla_tipo_a,
            regla_tipo_b=req.regla_tipo_b,
            activo=req.activo
        )
        repo.save_exclusion(rule)
        return {"status": "success", "message": "Exclusión registrada correctamente."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config/descuentos-volumen")
async def get_config_descuentos_volumen():
    try:
        repo = get_repo()
        rules = repo.descuentos_volumen()
        return [
            {
                "regla_id": r.regla_id,
                "marca": r.marca,
                "categoria": r.categoria,
                "litros_minimo": float(r.litros_minimo),
                "porcentaje": float(r.porcentaje),
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "listas_aplicables": r.listas_aplicables,
                "activo": r.activo
            } for r in rules
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/descuentos-volumen")
async def post_config_descuentos_volumen(req: DescuentoVolumenRequest):
    try:
        repo = get_repo()
        from cxc.models import DescuentoVolumen
        from cxc.sheets import serde
        import uuid
        
        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        # Check date overlap with active volume rules of same brand, category, list
        existing = repo.descuentos_volumen()
        for r in existing:
            if r.activo and r.marca == req.marca and r.categoria == req.categoria:
                lists_overlap = (r.listas_aplicables == "*" or req.listas_aplicables == "*" or r.listas_aplicables == req.listas_aplicables)
                if lists_overlap:
                    h1 = v_hasta if v_hasta is not None else date(9999, 12, 31)
                    h2 = r.vigencia_hasta if r.vigencia_hasta is not None else date(9999, 12, 31)
                    if max(v_desde, r.vigencia_desde) <= min(h1, h2):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Conflicto: ya existe la regla de volumen activa {r.regla_id} ({r.vigencia_desde} a {r.vigencia_hasta or 'siempre'}) para esta marca/categoría/lista."
                        )

        regla_id = f"VOL_{uuid.uuid4().hex[:8].upper()}"
        rule = DescuentoVolumen(
            regla_id=regla_id,
            marca=req.marca,
            categoria=req.categoria,
            litros_minimo=Decimal(str(req.litros_minimo)),
            porcentaje=Decimal(str(req.porcentaje)),
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            listas_aplicables=req.listas_aplicables,
            activo=True
        )
        repo._g.append_row("DescuentosVolumen", serde.desc_volumen_to_row(rule))
        return {"status": "success", "message": "Regla de descuento por volumen registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


# --- Pronto Pago Endpoints ---
@app.get("/api/config/descuentos-pronto-pago")
async def get_config_pronto_pago():
    try:
        repo = get_repo()
        rules = repo.descuentos_pronto_pago()
        return [
            {
                "regla_id": r.regla_id,
                "marca": r.marca,
                "categoria": r.categoria,
                "dias_gracia": r.dias_gracia,
                "porcentaje": float(r.porcentaje),
                "monedas_aplicables": r.monedas_aplicables,
                "listas_aplicables": r.listas_aplicables,
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "activo": r.activo
            } for r in rules
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/descuentos-pronto-pago")
async def post_config_pronto_pago(req: ProntoPagoRequest):
    try:
        repo = get_repo()
        from cxc.models import DescuentoProntoPago
        from cxc.sheets import serde
        import uuid

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        regla_id = f"PP_{uuid.uuid4().hex[:8].upper()}"
        rule = DescuentoProntoPago(
            regla_id=regla_id,
            marca=req.marca,
            categoria=req.categoria,
            dias_gracia=req.dias_gracia,
            porcentaje=Decimal(str(req.porcentaje)),
            monedas_aplicables=req.monedas_aplicables,
            listas_aplicables=req.listas_aplicables,
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            activo=req.activo
        )
        repo._g.append_row("DescuentosProntoPago", serde.pronto_pago_to_row(rule))
        return {"status": "success", "message": "Regla de descuento por pronto pago registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


# --- Recompra Endpoints ---
@app.get("/api/config/descuentos-recompra")
async def get_config_recompra():
    try:
        repo = get_repo()
        rules = repo.descuentos_recompra()
        return [
            {
                "regla_id": r.regla_id,
                "porcentaje": float(r.porcentaje),
                "max_usos_mes": r.max_usos_mes,
                "dias_ventana": r.dias_ventana,
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "activo": r.activo
            } for r in rules
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/descuentos-recompra")
async def post_config_recompra(req: RecompraRequest):
    try:
        repo = get_repo()
        from cxc.models import DescuentoRecompra
        from cxc.sheets import serde
        import uuid

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        regla_id = f"REC_{uuid.uuid4().hex[:8].upper()}"
        rule = DescuentoRecompra(
            regla_id=regla_id,
            porcentaje=Decimal(str(req.porcentaje)),
            max_usos_mes=req.max_usos_mes,
            dias_ventana=req.dias_ventana,
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            activo=req.activo
        )
        repo._g.append_row("DescuentosRecompra", serde.recompra_to_row(rule))
        return {"status": "success", "message": "Regla de descuento por recompra registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


# --- Producto Promo Endpoints ---
@app.get("/api/config/descuentos-producto")
async def get_config_producto():
    try:
        repo = get_repo()
        rules = repo.descuentos_producto()
        return [
            {
                "regla_id": r.regla_id,
                "productos": r.productos,
                "marca": r.marca,
                "categoria": r.categoria,
                "porcentaje": float(r.porcentaje),
                "monedas_aplicables": r.monedas_aplicables,
                "listas_aplicables": r.listas_aplicables,
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "activo": r.activo
            } for r in rules
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/descuentos-producto")
async def post_config_producto(req: ProductoPromoRequest):
    try:
        repo = get_repo()
        from cxc.models import DescuentoProducto
        from cxc.sheets import serde
        import uuid

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        regla_id = f"PROD_{uuid.uuid4().hex[:8].upper()}"
        rule = DescuentoProducto(
            regla_id=regla_id,
            productos=req.productos,
            marca=req.marca,
            categoria=req.categoria,
            porcentaje=Decimal(str(req.porcentaje)),
            monedas_aplicables=req.monedas_aplicables,
            listas_aplicables=req.listas_aplicables,
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            activo=req.activo
        )
        repo._g.append_row("DescuentosProducto", serde.producto_to_row(rule))
        return {"status": "success", "message": "Regla de descuento por producto registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


# --- Diferencial Cambiario Endpoints ---
@app.get("/api/config/descuentos-diferencial-cambiario")
async def get_config_diferencial():
    try:
        repo = get_repo()
        rules = repo.descuentos_diferencial_cambiario()
        return [
            {
                "regla_id": r.regla_id,
                "nombre": r.nombre,
                "tipo_diferencial": r.tipo_diferencial,
                "tipo_calculo": r.tipo_calculo,
                "porcentaje_fijo": float(r.porcentaje_fijo),
                "monedas_aplicables": r.monedas_aplicables,
                "listas_aplicables": r.listas_aplicables,
                "vigencia_desde": r.vigencia_desde.isoformat() if r.vigencia_desde else None,
                "vigencia_hasta": r.vigencia_hasta.isoformat() if r.vigencia_hasta else None,
                "activo": r.activo
            } for r in rules
        ]
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/descuentos-diferencial-cambiario")
async def post_config_diferencial(req: DiferencialCambiarioRequest):
    try:
        repo = get_repo()
        from cxc.models import DescuentoDiferencialCambiario
        from cxc.sheets import serde
        import uuid

        v_desde = date.fromisoformat(req.vigencia_desde) if req.vigencia_desde else date.today()
        v_hasta = date.fromisoformat(req.vigencia_hasta) if req.vigencia_hasta else None

        regla_id = f"DIF_{uuid.uuid4().hex[:8].upper()}"
        rule = DescuentoDiferencialCambiario(
            regla_id=regla_id,
            nombre=req.nombre,
            tipo_diferencial=req.tipo_diferencial,
            tipo_calculo=req.tipo_calculo,
            porcentaje_fijo=Decimal(str(req.porcentaje_fijo)),
            monedas_aplicables=req.monedas_aplicables,
            listas_aplicables=req.listas_aplicables,
            vigencia_desde=v_desde,
            vigencia_hasta=v_hasta,
            activo=req.activo
        )
        repo._g.append_row("DescuentosDiferencialCambiario", serde.diferencial_to_row(rule))
        return {"status": "success", "message": "Regla de diferencial cambiario registrada."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


# --- Toggle Rule Active Endpoint ---
@app.post("/api/config/toggle-descuento")
async def post_toggle_descuento(req: ToggleDescuentoRequest):
    try:
        repo = get_repo()
        
        TABLE_CANDIDATES = {
            "DescuentosProntoPago": ["DescuentosProntoPago", "DescuentosMarcaCategoria"],
            "DescuentosRecompra": ["DescuentosRecompra", "ReglasRecurrencia"],
            "DescuentosProducto": ["DescuentosProducto", "PromocionesPrimeraCompra"],
            "DescuentosDiferencialCambiario": ["DescuentosDiferencialCambiario", "DescuentosBCVCompleto"],
        }
        
        candidate_names = TABLE_CANDIDATES.get(req.tabla, [req.tabla, "DescuentosProntoPago", "DescuentosMarcaCategoria", "ReglasRecurrencia", "PromocionesPrimeraCompra"])
        target_id_str = str(req.regla_id).strip()

        # Handle GspreadGateway (Real Google Sheets)
        if hasattr(repo._g, "_sh"):
            for w_name in candidate_names:
                try:
                    ws = repo._g._ws(w_name)
                    values = ws.get_all_values()
                    if not values or len(values) < 2:
                        continue
                    
                    headers = [str(h).strip().lower() for h in values[0]]
                    activo_col_idx = None
                    for idx, h in enumerate(headers):
                        if h in ("activo", "active", "estado"):
                            activo_col_idx = idx + 1
                            break
                    
                    if not activo_col_idx:
                        continue
                    
                    for r_idx, row in enumerate(values[1:], start=2):
                        row_str_values = [str(val).strip() for val in row]
                        if target_id_str in row_str_values:
                            ws.update_cell(r_idx, activo_col_idx, "TRUE" if req.activo else "FALSE")
                            logger.info("Regla %s actualizada a %s en '%s', fila %d", target_id_str, req.activo, w_name, r_idx)
                            return {
                                "status": "success",
                                "message": f"Estado de la regla {target_id_str} actualizado a {'Activo' if req.activo else 'Inactivo'} en '{w_name}'."
                            }
                except Exception as inner_e:
                    logger.warning("Error buscando regla %s en '%s': %s", target_id_str, w_name, inner_e)
                    continue
        else:
            # InMemorySheetGateway fallback for tests
            for t_name in candidate_names:
                rows = repo._g.read_rows(t_name)
                for r in rows:
                    r_id = str(r.get("regla_id") or r.get("id") or "")
                    if r_id == target_id_str:
                        r["activo"] = "TRUE" if req.activo else "FALSE"
                        repo._g.upsert_row(t_name, "regla_id", r)
                        return {
                            "status": "success",
                            "message": f"Estado de la regla {target_id_str} actualizado a {'Activo' if req.activo else 'Inactivo'}."
                        }

        raise HTTPException(status_code=404, detail=f"Regla '{target_id_str}' no encontrada en Google Sheets.")
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


# --- Rate Averages Endpoint ---
@app.get("/api/config/tasas-promedios")
async def get_tasas_promedios():
    try:
        repo = get_repo()
        rows = repo._g.read_rows("SerieTasas")
        today_str = date.today().isoformat()
        
        rates_today = [r for r in rows if r.get("timestamp", "").startswith(today_str)]
        target_rows = rates_today if rates_today else rows[-24:]

        manana_near_9 = []
        manana_all = []
        tarde_near_13 = []
        tarde_all = []
        diario = []
        last_bcv = Decimal("0")

        for r in target_rows:
            try:
                tb = Decimal(str(r.get("tasa_binance", "0")))
                if tb > Decimal("0"):
                    diario.append(tb)
                    ts_str = str(r.get("timestamp", "00:00"))
                    time_part = ts_str.split("T")[-1].split(" ")[-1]
                    ts_hour = int(time_part.split(":")[0])
                    
                    if 6 <= ts_hour <= 9:
                        manana_near_9.append(tb)
                    elif 10 <= ts_hour <= 13:
                        tarde_near_13.append(tb)
                    
                    if ts_hour < 12:
                        manana_all.append(tb)
                    else:
                        tarde_all.append(tb)

                t_bcv = Decimal(str(r.get("tasa_bcv", "0")))
                if t_bcv > Decimal("0"):
                    last_bcv = t_bcv
            except:
                pass

        m_list = manana_near_9 if manana_near_9 else manana_all
        t_list = tarde_near_13 if tarde_near_13 else tarde_all

        avg_m = float(sum(m_list) / Decimal(len(m_list))) if m_list else None
        avg_t = float(sum(t_list) / Decimal(len(t_list))) if t_list else None
        avg_d = float(sum(diario) / Decimal(len(diario))) if diario else None
        
        diff_pct = 0.0
        if avg_d and last_bcv > Decimal("0"):
            diff_pct = float(((Decimal(str(avg_d)) - last_bcv) / Decimal(str(avg_d))) * 100)

        return {
            "fecha": today_str,
            "tasa_bcv_actual": float(last_bcv),
            "tasa_binance_manana": round(avg_m, 2) if avg_m else None,
            "tasa_binance_tarde": round(avg_t, 2) if avg_t else None,
            "tasa_binance_diario": round(avg_d, 2) if avg_d else None,
            "diferencial_bcv_binance_pct": round(diff_pct, 2)
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pagos-historial")
async def get_pagos_historial():
    try:
        repo = get_repo()
        vincs = repo.all_vinculaciones()
        pagos_rows = repo._g.read_rows("Pagos")
        pagos_map = {r.get("pago_id"): r for r in pagos_rows}
        clientes_rows = repo._g.read_rows("Clientes")
        clientes_map = {r.get("cliente_id"): r.get("nombre", "") for r in clientes_rows}
        ordenes_map = {o.so_id: o for o in repo.all_ordenes()}

        historial = []
        for v in vincs:
            p_data = pagos_map.get(v.pago_id, {})
            cid = p_data.get("cliente_id", "")
            c_name = clientes_map.get(cid, f"Cliente ID: {cid}")
            o = ordenes_map.get(v.so_id)
            factura_id = o.factura_id if o and o.factura_id else "N/A"

            historial.append({
                "pago_id": v.pago_id,
                "cliente_nombre": c_name,
                "fecha_pago": v.hora_pago_confirmada.strftime("%Y-%m-%d") if v.hora_pago_confirmada else "",
                "monto_aplicado": float(v.monto_aplicado),
                "moneda": v.moneda_abono.value if v.moneda_abono else "USD",
                "so_id": v.so_id,
                "factura_id": factura_id,
                "confirmado_por": v.confirmado_por or "Sistema",
                "estado": v.estado.value
            })
        return historial
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/auditoria")
async def get_auditoria():
    try:
        repo = get_repo()
        ordenes = repo.all_ordenes()
        lines_rows = repo._g.read_rows("LineasOrden")
        bandeja_rows = repo.all_bandeja()
        bandeja_map = {b.so_id: b for b in bandeja_rows}
        clientes_rows = repo._g.read_rows("Clientes")
        clientes_map = {r.get("cliente_id"): r.get("nombre", "") for r in clientes_rows}

        # Load pricelist 4 (USD) and pricelist 5 (VES) prices from Odoo
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        lista_usd_id = int(os.environ.get("ODOO_PRICELIST_USD", "4"))
        lista_ves_id = int(os.environ.get("ODOO_PRICELIST_BCV", "5"))

        rules_usd = execute(
            "product.pricelist.item",
            "search_read",
            [[["pricelist_id", "=", lista_usd_id], ["compute_price", "=", "fixed"]]],
            {"fields": ["product_tmpl_id", "fixed_price"]}
        )
        prices_usd = {}
        for r in rules_usd:
            pt = r.get("product_tmpl_id")
            pt_id = pt[0] if isinstance(pt, list) else pt
            if pt_id:
                prices_usd[pt_id] = float(r.get("fixed_price") or 0.0)

        rules_ves = execute(
            "product.pricelist.item",
            "search_read",
            [[["pricelist_id", "=", lista_ves_id], ["compute_price", "=", "fixed"]]],
            {"fields": ["product_tmpl_id", "fixed_price"]}
        )
        prices_ves = {}
        for r in rules_ves:
            pt = r.get("product_tmpl_id")
            pt_id = pt[0] if isinstance(pt, list) else pt
            if pt_id:
                prices_ves[pt_id] = float(r.get("fixed_price") or 0.0)

        # Load accepted anomalies from Google Sheets
        anomalias_aceptadas_rows = repo._g.read_rows("AnomaliasAceptadas")
        aceptadas_map = {r.get("anomalia_id"): r for r in anomalias_aceptadas_rows}

        lines_by_so = {}
        for r in lines_rows:
            so = r.get("so_id", "")
            if so:
                lines_by_so.setdefault(so, []).append(r)

        operaciones_conformes = []
        raw_discrepancias = []

        for o in ordenes:
            c_name = clientes_map.get(o.cliente_id, f"Cliente ID: {o.cliente_id}")
            b = bandeja_map.get(o.so_id)
            so_lines = lines_by_so.get(o.so_id, [])

            has_discrepancy = False
            is_ves = (o.lista_precios == "5") or (o.lista_precios != "4")
            official_prices_map = prices_ves if is_ves else prices_usd
            pricelist_label = "Lista VES (#5)" if is_ves else "Lista USD (#4)"

            # Check 1: Unit prices vs correct official pricelist (VES or USD)
            for ln in so_lines:
                qty = parse_decimal_safe(ln.get("cantidad", "0"))
                qty_delivered = parse_decimal_safe(ln.get("cantidad_entregada", ln.get("qty_delivered", "0")))
                price_order = parse_decimal_safe(ln.get("precio_unitario", "0"))

                # Skip returned/non-delivered/zero-qty lines (returns with price=0 or qty=0)
                if qty <= Decimal("0") or price_order <= Decimal("0") or qty_delivered <= Decimal("0"):
                    continue

                prod_raw = ln.get("producto", "")
                pt_id = None
                if isinstance(prod_raw, str):
                    if prod_raw.startswith("["):
                        try:
                            import json
                            parsed = json.loads(prod_raw.replace("'", '"'))
                            pt_id = int(parsed[0])
                        except:
                            import re
                            m = re.search(r'\d+', prod_raw)
                            if m:
                                pt_id = int(m.group())
                    elif prod_raw.isdigit():
                        pt_id = int(prod_raw)
                elif isinstance(prod_raw, (int, float)):
                    pt_id = int(prod_raw)

                if pt_id in official_prices_map:
                    price_official = Decimal(str(official_prices_map[pt_id]))
                    if price_order < price_official - Decimal("0.01"):
                        has_discrepancy = True
                        diff_unit = price_official - price_order
                        diff_monto = float(diff_unit * qty)
                        diff_pct = float((diff_unit / price_official) * 100) if price_official > 0 else 0.0
                        raw_discrepancias.append({
                            "so_id": o.so_id,
                            "factura_id": o.factura_id or "N/A",
                            "cliente_nombre": c_name,
                            "vendedor": o.vendedor_email or "N/A",
                            "tipo": "Precio Inferior a Lista",
                            "detalle": f"Producto ID {pt_id}: Precio orden (${float(price_order):.2f}) < {pricelist_label} (${float(price_official):.2f}) [Entregado: {float(qty_delivered):.0f} und]",
                            "esperado": float(price_official * qty),
                            "actual": float(price_order * qty),
                            "diferencia_monto": round(diff_monto, 2),
                            "diferencia_porcentaje": round(diff_pct, 2)
                        })

                # Check 2: Manual unapproved line discounts
                disc = parse_decimal_safe(ln.get("descuento", "0"))
                if disc > Decimal("0"):
                    if not b or (b and b.total_descuentos == Decimal("0") and b.ncs_calculadas == Decimal("0")):
                        has_discrepancy = True
                        disc_monto = float((price_order * qty) * (disc / Decimal("100")))
                        raw_discrepancias.append({
                            "so_id": o.so_id,
                            "factura_id": o.factura_id or "N/A",
                            "cliente_nombre": c_name,
                            "vendedor": o.vendedor_email or "N/A",
                            "tipo": "Descuento Manual No Explicado",
                            "detalle": f"Descuento manual del {float(disc):.1f}% en línea sin regla activa [Entregado: {float(qty_delivered):.0f} und]",
                            "esperado": float(price_order * qty),
                            "actual": float((price_order * qty) - Decimal(str(disc_monto))),
                            "diferencia_monto": round(disc_monto, 2),
                            "diferencia_porcentaje": float(disc)
                        })

            # Check 3: Sub-facturación / Sobre-facturación / Orden vs Factura
            if o.facturada and o.monto_facturado:
                net_expected = b.total_motor if b else o.monto_total
                diff_inv = o.monto_facturado - net_expected
                if abs(diff_inv) > Decimal("0.05"):
                    has_discrepancy = True
                    tipo_str = "Sobre-facturación" if diff_inv > Decimal("0") else "Sub-facturación"
                    pct_inv = float((abs(diff_inv) / net_expected) * 100) if net_expected > 0 else 0.0
                    raw_discrepancias.append({
                        "so_id": o.so_id,
                        "factura_id": o.factura_id or "N/A",
                        "cliente_nombre": c_name,
                        "vendedor": o.vendedor_email or "N/A",
                        "tipo": tipo_str,
                        "detalle": f"Factura Odoo (${float(o.monto_facturado):.2f}) no coincide con Neto Orden Esperado (${float(net_expected):.2f})",
                        "esperado": float(net_expected),
                        "actual": float(o.monto_facturado),
                        "diferencia_monto": round(float(abs(diff_inv)), 2),
                        "diferencia_porcentaje": round(pct_inv, 2)
                    })

            if not has_discrepancy and (o.facturada or (b and b.candidata_a_cierre)):
                neto = float(b.total_motor) if b else float(o.monto_total)
                desc_tot = float(b.total_descuentos + b.ncs_calculadas) if b else 0.0
                operaciones_conformes.append({
                    "so_id": o.so_id,
                    "factura_id": o.factura_id or "N/A",
                    "cliente_nombre": c_name,
                    "fecha": o.fecha.isoformat(),
                    "monto_original": float(o.monto_total),
                    "descuentos_aplicados": desc_tot,
                    "monto_neto_conciliado": neto,
                    "estado": "Conforme 100%"
                })

        # Separate into pending discrepancies and accepted anomalies
        discrepancias_pendientes = []
        anomalias_aceptadas = []

        for item in raw_discrepancias:
            tipo_clean = item["tipo"].replace(" ", "_").upper()
            anomalia_id = f"ANOM_{item['so_id']}_{tipo_clean}_{item['factura_id']}"
            item["anomalia_id"] = anomalia_id

            if anomalia_id in aceptadas_map:
                ac_rec = aceptadas_map[anomalia_id]
                item["motivo_aceptacion"] = ac_rec.get("motivo_aceptacion", "Revisado y Aceptado")
                item["aprobado_por"] = ac_rec.get("aprobado_por", "Dirección")
                item["timestamp_aprobacion"] = ac_rec.get("timestamp_aprobacion", "")
                anomalias_aceptadas.append(item)
            else:
                discrepancias_pendientes.append(item)

        return {
            "operaciones_conformes": operaciones_conformes,
            "discrepancias": discrepancias_pendientes,
            "anomalias_aceptadas": anomalias_aceptadas,
            "resumen_auditoria": {
                "total_conformes": len(operaciones_conformes),
                "total_discrepancias": len(discrepancias_pendientes),
                "total_aceptadas": len(anomalias_aceptadas),
                "monto_discrepancia_total": round(sum(d["diferencia_monto"] for d in discrepancias_pendientes), 2)
            }
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


class AceptarAnomaliaRequest(BaseModel):
    anomalia_id: str
    so_id: str
    factura_id: str = "N/A"
    tipo_anomalia: str
    motivo_aceptacion: str = "Revisado y Aceptado en Auditoría"
    aprobado_por: str = "Dirección / Auditor"


@app.post("/api/auditoria/aceptar-anomalia")
async def post_aceptar_anomalia(req: AceptarAnomaliaRequest):
    try:
        repo = get_repo()
        row = {
            "anomalia_id": req.anomalia_id,
            "so_id": req.so_id,
            "factura_id": req.factura_id,
            "tipo_anomalia": req.tipo_anomalia,
            "motivo_aceptacion": req.motivo_aceptacion,
            "aprobado_por": req.aprobado_por,
            "timestamp_aprobacion": datetime.now().isoformat()
        }
        repo._g.append_row("AnomaliasAceptadas", row)
        return {"status": "success", "message": "Anomalía aceptada y movida al historial de revisiones."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))
