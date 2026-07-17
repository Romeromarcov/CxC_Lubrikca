import os
import sys
import json
import asyncio
from datetime import datetime, date
from decimal import Decimal
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

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

def get_repo() -> SheetsRepository:
    config = AppConfig.from_env()
    print(f"DEBUG: GOOGLE_SHEETS_SPREADSHEET_ID: length={len(config.sheets.spreadsheet_id)}, repr={repr(config.sheets.spreadsheet_id)}", file=sys.stderr)
    if os.environ.get("GOOGLE_TOKEN_JSON"):
        gateway = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
    else:
        gateway = GspreadGateway(
            config.sheets.spreadsheet_id, config.sheets.service_account_file
        )
    return SheetsRepository(gateway)

import traceback

async def run_sync_in_background():
    while True:
        try:
            print("FastAPI Daemon: Iniciando ciclo de sync incremental...")
            config = AppConfig.from_env()
            print(f"DEBUG SYNC: GOOGLE_SHEETS_SPREADSHEET_ID: length={len(config.sheets.spreadsheet_id)}, repr={repr(config.sheets.spreadsheet_id)}", file=sys.stderr)
            if os.environ.get("GOOGLE_TOKEN_JSON"):
                gateway = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
            else:
                gateway = GspreadGateway(
                    config.sheets.spreadsheet_id, config.sheets.service_account_file
                )
            repo = SheetsRepository(gateway)
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
            from cxc.scraper.bcv import BcvClient
            from cxc.scraper.binance import BinanceClient
            from cxc.scraper.rates_scraper import RatesScraper
            from cxc.alerts import build_alerter

            config = AppConfig.from_env()
            print(f"DEBUG SCRAPER: GOOGLE_SHEETS_SPREADSHEET_ID: length={len(config.sheets.spreadsheet_id)}, repr={repr(config.sheets.spreadsheet_id)}", file=sys.stderr)
            if os.environ.get("GOOGLE_TOKEN_JSON"):
                gateway = GspreadGateway.from_env_vars(config.sheets.spreadsheet_id)
            else:
                gateway = GspreadGateway(
                    config.sheets.spreadsheet_id, config.sheets.service_account_file
                )
            repo = SheetsRepository(gateway)
            scraper = RatesScraper(
                repo,
                BinanceClient(config.binance),
                BcvClient(config.bcv),
                build_alerter(config.alert),
                config.scraper_policy,
            )
            fila = scraper.run(datetime.now())
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
                
                pendientes.append({
                    "pago_id": pago_id,
                    "cliente_id": cliente_id,
                    "cliente_nombre": cliente_nombre,
                    "monto": float(saldo_pendiente),
                    "monto_original": float(monto_original),
                    "moneda": p.get("moneda", "USD"),
                    "fecha": p.get("fecha_pago", ""),
                    "metodo_pago": p.get("metodo_pago", "")
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
        
        resultados = []
        for b in bandeja:
            conc = concs.get(b.so_id)
            resultados.append({
                "so_id": b.so_id,
                "lista_aplicada": b.lista_aplicada,
                "precio_base": float(b.precio_base_calculado),
                "total_descuentos": float(b.total_descuentos),
                "total_motor": float(b.total_motor),
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
            
        reporte = []
        for o in ordenes:
            client_name = clientes_map.get(o.cliente_id, f"Cliente ID: {o.cliente_id}")
            pagado = linked_by_so.get(o.so_id, Decimal("0"))
            saldo = o.monto_total - pagado
            conc = concs.get(o.so_id)
            
            reporte.append({
                "so_id": o.so_id,
                "cliente_nombre": client_name,
                "fecha": o.fecha.isoformat(),
                "monto_total": float(o.monto_total),
                "monto_pagado": float(pagado),
                "saldo_deudor": float(saldo) if saldo > Decimal("0") else 0.0,
                "facturada": o.facturada,
                "candidata_a_cierre": saldo <= Decimal("0.05"),
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
            tasas.append({
                "timestamp": f.get("timestamp", ""),
                "tasa_bcv": float(parse_decimal_safe(f.get("tasa_bcv", "0"))),
                "tasa_binance": float(parse_decimal_safe(f.get("tasa_binance", "0"))),
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
        
        regla_id = f"REG_{uuid.uuid4().hex[:8].upper()}"
        rule = DescuentoMarcaCategoria(
            regla_id=regla_id,
            marca=req.marca,
            categoria=req.categoria,
            tipo_descuento=req.tipo_descuento,
            porcentaje=Decimal(str(req.porcentaje)),
            vigencia_desde=date.today(),
            vigencia_hasta=None,
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
