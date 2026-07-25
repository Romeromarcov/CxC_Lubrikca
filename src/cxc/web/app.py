import os
import sys
import json
import asyncio
import logging
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Any
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
from cxc.engine.effective_dating import descuento_vigente
from cxc.reconciliation.reconcile import OdooFacturasReader, Reconciler
from cxc.models import Vinculacion, EstadoVinculacion, Moneda, TipoTasa, TipoDescuento

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

class PricelistMapRequest(BaseModel):
    valid_pricelists_usd: list[str]
    valid_pricelists_ves: list[str]

class VincularMasivoRequest(BaseModel):
    items: list[VinculacionRequest]

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

class ProductoPromoRequest(BaseModel):
    productos: str = "*"
    marca: str = "*"
    categoria: str = "*"
    compra_minima_cajas: int = 1
    porcentaje_descuento: float = 0.05
    monedas_aplicables: str = "*"
    listas_aplicables: str = "*"
    vigencia_desde: str = ""
    vigencia_hasta: str | None = None
    activo: bool = True


def get_ui_pricelist_ids(repo) -> tuple[list[int], list[int]]:
    try:
        rows = repo._g.read_rows("_Meta")
        meta = {r.get("key"): r.get("value", "") for r in rows if r.get("key")}
        
        def _parse(val_str: str, default_val: int) -> list[int]:
            if not val_str:
                return [default_val]
            if "," in val_str:
                parts = [p.strip() for p in val_str.split(",") if p.strip()]
            else:
                parts = [c for c in val_str.strip() if c.isdigit()]
            res = [int(p) for p in parts if p.isdigit()]
            return res if res else [default_val]
            
        usd_ids = _parse(meta.get("valid_pricelists_usd"), int(os.environ.get("ODOO_PRICELIST_USD", "4")))
        ves_ids = _parse(meta.get("valid_pricelists_ves"), int(os.environ.get("ODOO_PRICELIST_BCV", "5")))
        return usd_ids, ves_ids
    except Exception as e:
        logger.warning("Error reading pricelists from _Meta: %s", e)
        return [int(os.environ.get("ODOO_PRICELIST_USD", "4"))], [int(os.environ.get("ODOO_PRICELIST_BCV", "5"))]


def resolve_effective_pricelist_price(
    product_tmpl_id: int,
    order_date: date,
    candidate_pricelist_ids: list[int],
    pricelist_items: list[dict]
) -> Decimal | None:
    if not candidate_pricelist_ids or not pricelist_items:
        return None
    matched = []
    for r in pricelist_items:
        pl_id = r["pricelist_id"][0] if isinstance(r["pricelist_id"], (list, tuple)) else r["pricelist_id"]
        if candidate_pricelist_ids and pl_id not in candidate_pricelist_ids:
            continue
            
        pt_raw = r.get("product_tmpl_id")
        pt_id = pt_raw[0] if isinstance(pt_raw, (list, tuple)) else pt_raw
        if pt_id != product_tmpl_id:
            continue
            
        d_start_str = r.get("date_start")
        d_end_str = r.get("date_end")
        
        d_start = datetime.strptime(d_start_str[:10], "%Y-%m-%d").date() if d_start_str else None
        d_end = datetime.strptime(d_end_str[:10], "%Y-%m-%d").date() if d_end_str else None
        
        if d_start and order_date < d_start:
            continue
        if d_end and order_date > d_end:
            continue
            
        price = Decimal(str(r.get("fixed_price") or "0"))
        matched.append((d_start or date.min, price))
        
    if matched:
        matched.sort(key=lambda x: x[0], reverse=True)
        return matched[0][1]
        
    return None


def extract_product_tmpl_id(prod_raw: Any) -> int | None:
    if isinstance(prod_raw, (int, float)):
        return int(prod_raw)
    if isinstance(prod_raw, str):
        if prod_raw.startswith("["):
            try:
                import json
                parsed = json.loads(prod_raw.replace("'", '"'))
                return int(parsed[0])
            except Exception:
                import re
                m = re.search(r'\d+', prod_raw)
                if m:
                    return int(m.group())
        elif prod_raw.isdigit():
            return int(prod_raw)
    return None

class RecompraRequest(BaseModel):
    porcentaje: float = 0.05
    max_usos_mes: int = 2
    dias_ventana: int = 30
    min_cajas: int = 1
    max_cajas: int = 9999
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
    tipo_evaluacion: str = "orden"
    dias_evaluacion: int = 30
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

from fastapi import Cookie, Request, Response, Depends
from fastapi.responses import RedirectResponse
from cxc.auth import (
    ROLES_PERMISOS, NOMBRES_ROLES, hash_password, verificar_password,
    verificar_usuario_odoo_activo, obtener_usuarios_plataforma,
    buscar_usuario_plataforma, registrar_o_actualizar_usuario,
    autenticar_usuario, crear_session_token, verificar_session_token
)

SECRET_KEY = os.environ.get("SESSION_SECRET", "lubrikca_cxc_secret_key_2026")

class LoginRequest(BaseModel):
    email: str
    password: str

class RegisterPasswordRequest(BaseModel):
    email: str
    password: str

class ResetPasswordRequest(BaseModel):
    email: str
    password: str

class CambiarRolRequest(BaseModel):
    email: str
    nuevo_rol: str


def get_current_user_from_cookie(cxc_session: str | None = None) -> dict[str, Any] | None:
    if not cxc_session:
        return None
    email = verificar_session_token(cxc_session, SECRET_KEY)
    if not email:
        return None
    try:
        repo = get_repo()
        u_row = buscar_usuario_plataforma(repo, email)
        if not u_row or u_row.get("activo") == "FALSE":
            return None
        rol = u_row.get("rol", "ventas")
        return {
            "email": email,
            "nombre": u_row.get("nombre_odoo") or email,
            "rol": rol,
            "nombre_rol": NOMBRES_ROLES.get(rol, "Ventas"),
            "permisos": ROLES_PERMISOS.get(rol, ["reporte"])
        }
    except Exception as e:
        logger.warning("Error buscando usuario de sesión %s: %s", email, e)
        return None


# --- MULTI-PAGE & AUTH ROUTES ---

@app.get("/", response_class=HTMLResponse)
async def read_root(cxc_session: str | None = Cookie(default=None)):
    user = get_current_user_from_cookie(cxc_session)
    if not user:
        return RedirectResponse(url="/login")
    first_perm = user["permisos"][0] if user["permisos"] else "reporte"
    return RedirectResponse(url=f"/{first_perm}")

@app.get("/login", response_class=HTMLResponse)
async def serve_login(cxc_session: str | None = Cookie(default=None)):
    user = get_current_user_from_cookie(cxc_session)
    if user:
        first_perm = user["permisos"][0] if user["permisos"] else "reporte"
        return RedirectResponse(url=f"/{first_perm}")
    login_path = os.path.join(static_dir, "login.html")
    if not os.path.exists(login_path):
        return HTMLResponse("<html><body><h1>Error</h1><p>Archivo static/login.html no encontrado</p></body></html>")
    with open(login_path, 'r', encoding='utf-8') as f:
        return f.read()

@app.get("/logout")
async def handle_logout_get(response: Response):
    res = RedirectResponse(url="/login")
    res.delete_cookie(key="cxc_session")
    return res

@app.post("/api/auth/logout")
async def handle_logout_post():
    res = Response(content=json.dumps({"status": "success", "message": "Sesión cerrada"}), media_type="application/json")
    res.delete_cookie(key="cxc_session")
    return res

def render_page_or_login(page_name: str, cxc_session: str | None):
    user = get_current_user_from_cookie(cxc_session)
    if not user:
        return RedirectResponse(url="/login")
    if page_name not in user["permisos"] and user["rol"] != "admin":
        first_perm = user["permisos"][0] if user["permisos"] else "reporte"
        return RedirectResponse(url=f"/{first_perm}")
    
    index_path = os.path.join(static_dir, "index.html")
    if not os.path.exists(index_path):
        return HTMLResponse("<html><body><h1>Servidor Iniciado</h1><p>Frontend no encontrado</p></body></html>")
    with open(index_path, 'r', encoding='utf-8') as f:
        return f.read()

@app.get("/facturacion", response_class=HTMLResponse)
async def page_facturacion(cxc_session: str | None = Cookie(default=None)):
    return render_page_or_login("facturacion", cxc_session)

@app.get("/cobranza", response_class=HTMLResponse)
async def page_cobranza(cxc_session: str | None = Cookie(default=None)):
    return render_page_or_login("cobranza", cxc_session)

@app.get("/dashboard", response_class=HTMLResponse)
async def page_dashboard(cxc_session: str | None = Cookie(default=None)):
    return render_page_or_login("dashboard", cxc_session)

@app.get("/conciliaciones", response_class=HTMLResponse)
async def page_conciliaciones(cxc_session: str | None = Cookie(default=None)):
    return render_page_or_login("conciliaciones", cxc_session)

@app.get("/reporte", response_class=HTMLResponse)
async def page_reporte(cxc_session: str | None = Cookie(default=None)):
    return render_page_or_login("reporte", cxc_session)

@app.get("/auditoria", response_class=HTMLResponse)
async def page_auditoria(cxc_session: str | None = Cookie(default=None)):
    return render_page_or_login("auditoria", cxc_session)

@app.get("/configuracion", response_class=HTMLResponse)
async def page_configuracion(cxc_session: str | None = Cookie(default=None)):
    return render_page_or_login("configuracion", cxc_session)

# --- AUTH & ADMIN API ENDPOINTS ---

@app.post("/api/auth/login")
async def api_auth_login(req: LoginRequest):
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        repo = get_repo()
        
        user_info, err_msg = autenticar_usuario(execute, repo, req.email, req.password)
        if not user_info:
            raise HTTPException(status_code=401, detail=err_msg or "Credenciales inválidas")
        
        token = crear_session_token(req.email, SECRET_KEY)
        first_perm = user_info["permisos"][0] if user_info["permisos"] else "reporte"
        
        res = Response(
            content=json.dumps({"status": "success", "user": user_info, "redirect": f"/{first_perm}"}),
            media_type="application/json"
        )
        res.set_cookie(key="cxc_session", value=token, httponly=True, max_age=86400 * 7, samesite="lax")
        return res
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/register-password")
async def api_auth_register_password(req: RegisterPasswordRequest):
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        repo = get_repo()
        
        odoo_user = verificar_usuario_odoo_activo(execute, req.email)
        if not odoo_user:
            raise HTTPException(status_code=400, detail="El correo ingresado no pertenece a un usuario activo en Odoo ERP.")
        
        u_row = registrar_o_actualizar_usuario(
            repo,
            email=req.email,
            password=req.password,
            nombre_odoo=odoo_user.get("name") or "",
            activo=True
        )
        
        token = crear_session_token(req.email, SECRET_KEY)
        rol = u_row.get("rol", "ventas")
        permisos = ROLES_PERMISOS.get(rol, ["reporte"])
        first_perm = permisos[0] if permisos else "reporte"
        
        res = Response(
            content=json.dumps({"status": "success", "message": "Contraseña creada exitosamente", "redirect": f"/{first_perm}"}),
            media_type="application/json"
        )
        res.set_cookie(key="cxc_session", value=token, httponly=True, max_age=86400 * 7, samesite="lax")
        return res
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/reset-password")
async def api_auth_reset_password(req: ResetPasswordRequest):
    try:
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        repo = get_repo()
        
        odoo_user = verificar_usuario_odoo_activo(execute, req.email)
        if not odoo_user:
            raise HTTPException(status_code=400, detail="El correo ingresado no pertenece a un usuario activo en Odoo ERP.")
        
        registrar_o_actualizar_usuario(
            repo,
            email=req.email,
            password=req.password,
            nombre_odoo=odoo_user.get("name") or "",
            activo=True
        )
        return {"status": "success", "message": "Contraseña restablecida exitosamente."}
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/auth/me")
async def api_auth_me(cxc_session: str | None = Cookie(default=None)):
    user = get_current_user_from_cookie(cxc_session)
    if not user:
        raise HTTPException(status_code=401, detail="No autenticado")
    return user

@app.get("/api/admin/usuarios")
async def api_admin_list_usuarios(cxc_session: str | None = Cookie(default=None)):
    user = get_current_user_from_cookie(cxc_session)
    if not user or user["rol"] != "admin":
        raise HTTPException(status_code=403, detail="Acceso denegado: Se requiere rol Administrador")
    repo = get_repo()
    rows = obtener_usuarios_plataforma(repo)
    clean_rows = []
    for r in rows:
        clean_rows.append({
            "email": r.get("email"),
            "nombre_odoo": r.get("nombre_odoo"),
            "rol": r.get("rol", "ventas"),
            "nombre_rol": NOMBRES_ROLES.get(r.get("rol", "ventas"), "Ventas"),
            "activo": r.get("activo") == "TRUE",
            "fecha_registro": r.get("fecha_registro")
        })
    return clean_rows

@app.post("/api/admin/cambiar-rol")
async def api_admin_cambiar_rol(req: CambiarRolRequest, cxc_session: str | None = Cookie(default=None)):
    user = get_current_user_from_cookie(cxc_session)
    if not user or user["rol"] != "admin":
        raise HTTPException(status_code=403, detail="Acceso denegado: Se requiere rol Administrador")
    if req.nuevo_rol not in ROLES_PERMISOS:
        raise HTTPException(status_code=400, detail=f"Rol '{req.nuevo_rol}' no es válido.")
    repo = get_repo()
    u_row = buscar_usuario_plataforma(repo, req.email)
    if not u_row:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    u_row["rol"] = req.nuevo_rol
    repo._g.upsert_row("UsuariosPlataforma", "email", u_row)
    return {"status": "success", "message": f"Rol de {req.email} actualizado a {NOMBRES_ROLES.get(req.nuevo_rol)}."}

@app.get("/api/resumen")
async def get_resumen():
    try:
        repo = get_repo()
        # 1. Total por cobrar (Orders not invoiced)
        ordenes = repo.all_ordenes()
        total_por_cobrar = sum(o.monto_total for o in ordenes if not o.facturada)
        
        # 2. Pagos sin asignar (convert VES payments to USD at BCV rate)
        pagos = repo._g.read_rows("Pagos")
        vincs = repo.all_vinculaciones()
        linked_pago_ids = {v.pago_id for v in vincs}
        tasas_rows = repo._g.read_rows("SerieTasas")
        tasas_map = {r.get("fecha"): parse_decimal_safe(r.get("tasa_bcv", "0")) for r in tasas_rows}
        
        pagos_pendientes_monto = Decimal("0")
        for p in pagos:
            pid = str(p.get("pago_id", ""))
            if pid and pid not in linked_pago_ids:
                try:
                    monto = parse_decimal_safe(p.get("monto", "0"))
                    moneda = str(p.get("moneda", "USD")).upper().strip()
                    fecha_pago = str(p.get("fecha_pago", ""))[:10]
                    
                    if "VES" in moneda or "BS" in moneda or "BOLIVAR" in moneda:
                        tasa = parse_decimal_safe(p.get("tasa_bcv", "0"))
                        if tasa <= Decimal("0"):
                            tasa = tasas_map.get(fecha_pago, Decimal("0"))
                        if tasa > Decimal("0"):
                            monto_usd = monto / tasa
                        else:
                            monto_usd = Decimal("0")
                    else:
                        monto_usd = monto
                        
                    pagos_pendientes_monto += monto_usd
                except Exception:
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
        
        prices_usd = {}
        try:
            config = AppConfig.from_env()
            execute = _connect(config.odoo)
            if execute:
                lista_usd_id = int(os.environ.get("ODOO_PRICELIST_USD", "4"))
                rules = execute(
                    "product.pricelist.item",
                    "search_read",
                    [[["pricelist_id", "=", lista_usd_id], ["compute_price", "=", "fixed"]]],
                    {"fields": ["product_tmpl_id", "fixed_price"]}
                )
                for r in rules:
                    prod_tmpl_id = r.get("product_tmpl_id")
                    pt_id = prod_tmpl_id[0] if isinstance(prod_tmpl_id, list) else prod_tmpl_id
                    if pt_id:
                        prices_usd[pt_id] = float(r.get("fixed_price") or 0.0)
        except Exception as e_rules:
            logger.warning("Error leyendo precios de Odoo en get_bandeja: %s", e_rules)

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
        
        execute = None
        try:
            config = AppConfig.from_env()
            execute = _connect(config.odoo)
        except Exception as e_conn:
            logger.warning("No se pudo conectar a Odoo en get_reporte_saldos: %s", e_conn)

        # Query Odoo SOs for seller (user_id) & payment terms (payment_term_id)
        so_ids_names = [o.so_id for o in ordenes]
        so_odoo_data = {}
        if execute and so_ids_names:
            try:
                so_records = execute(
                    "sale.order",
                    "search_read",
                    [[["name", "in", so_ids_names]]],
                    {"fields": ["name", "user_id", "payment_term_id", "date_order", "state", "delivery_status", "invoice_status"]}
                )
                for s in so_records:
                    s_name = s.get("name")
                    u_info = s.get("user_id")
                    t_info = s.get("payment_term_id")
                    vendedor_name = u_info[1] if isinstance(u_info, (list, tuple)) and len(u_info) > 1 else "Sin Vendedor"
                    term_name = t_info[1] if isinstance(t_info, (list, tuple)) and len(t_info) > 1 else "Contado"
                    so_odoo_data[s_name] = {
                        "vendedor": vendedor_name,
                        "payment_term_name": term_name,
                        "date_order": s.get("date_order"),
                        "state": s.get("state"),
                        "delivery_status": s.get("delivery_status"),
                        "invoice_status": s.get("invoice_status")
                    }
            except Exception as e_so:
                logger.warning("Error consultando sale.order en Odoo: %s", e_so)

        # Query Odoo Pickings for Delivery Date and Returns (stock.picking done)
        picking_delivery_map = {}
        picking_return_set = set()
        if execute and so_ids_names:
            try:
                pickings = execute(
                    "stock.picking",
                    "search_read",
                    [[["state", "=", "done"]]],
                    {"fields": ["id", "origin", "sale_id", "date_done", "scheduled_date", "picking_type_code", "return_id"]}
                )
                p_by_id = {p["id"]: p for p in pickings}
                for p in pickings:
                    so_name = None
                    s_info = p.get("sale_id")
                    if isinstance(s_info, (list, tuple)) and len(s_info) > 1:
                        so_name = s_info[1]
                    elif p.get("origin"):
                        for name in so_ids_names:
                            if name and name in str(p["origin"]):
                                so_name = name
                                break
                    if not so_name and p.get("return_id"):
                        ret_parent_id = p["return_id"][0] if isinstance(p["return_id"], (list, tuple)) else None
                        if ret_parent_id and ret_parent_id in p_by_id:
                            parent_p = p_by_id[ret_parent_id]
                            ps_info = parent_p.get("sale_id")
                            if isinstance(ps_info, (list, tuple)) and len(ps_info) > 1:
                                so_name = ps_info[1]

                    p_code = str(p.get("picking_type_code") or "")
                    is_return = bool(p.get("return_id")) or (p_code == "incoming") or ("Devolución" in str(p.get("origin") or "")) or ("Return" in str(p.get("origin") or ""))

                    if so_name:
                        if is_return:
                            picking_return_set.add(so_name)
                        elif p_code == "outgoing":
                            dt_done = p.get("date_done") or p.get("scheduled_date")
                            if dt_done:
                                dt_str = str(dt_done).split(" ")[0]
                                if so_name not in picking_delivery_map or dt_str > picking_delivery_map[so_name]:
                                    picking_delivery_map[so_name] = dt_str
            except Exception as e_pic:
                logger.warning("Error consultando stock.picking en Odoo: %s", e_pic)

        # Compute payments per SO (BCV and Binance equivalents)
        pagos_by_so = {}
        for v in vincs:
            if v.so_id not in pagos_by_so:
                pagos_by_so[v.so_id] = {
                    "abono_bcv": Decimal("0"),
                    "abono_binance": Decimal("0"),
                    "ultimo_abono": None
                }
            
            # BCV equivalent
            eq_bcv = v.equiv_usd_bcv if v.equiv_usd_bcv is not None else v.monto_aplicado
            pagos_by_so[v.so_id]["abono_bcv"] += eq_bcv

            # Binance equivalent
            if v.equiv_usd_binance is not None:
                eq_binance = v.equiv_usd_binance
            else:
                eq_binance = v.monto_aplicado
            pagos_by_so[v.so_id]["abono_binance"] += eq_binance

            # Track latest payment date
            if v.hora_pago_confirmada:
                dt_pago_str = v.hora_pago_confirmada.strftime("%Y-%m-%d")
                curr_last = pagos_by_so[v.so_id]["ultimo_abono"]
                if not curr_last or dt_pago_str > curr_last:
                    pagos_by_so[v.so_id]["ultimo_abono"] = dt_pago_str

        # Load UI configured pricelist IDs (USD & VES) from _Meta
        usd_ids, ves_ids = get_ui_pricelist_ids(repo)
        rules_usd = execute(
            "product.pricelist.item",
            "search_read",
            [[["pricelist_id", "in", usd_ids], ["compute_price", "=", "fixed"]]],
            {"fields": ["pricelist_id", "product_tmpl_id", "fixed_price", "date_start", "date_end"]}
        ) if execute else []

        all_lines = repo._g.read_rows("LineasOrden")
        lines_by_so = {}
        for r in all_lines:
            so = r.get("so_id", "")
            if so:
                lines_by_so.setdefault(so, []).append(r)

        bandeja_rows = repo.all_bandeja()
        bandeja_map = {b.so_id: b for b in bandeja_rows}

        def parse_term_days(t_name: str) -> int:
            if not t_name:
                return 0
            t_low = t_name.lower().strip()
            if "immediate" in t_low or "contado" in t_low:
                return 0
            import re
            m = re.search(r'(\d+)\s*(dias|días|days|day|día)', t_low)
            if m:
                return int(m.group(1))
            return 0

        # Read discount rules for theoretical evaluation when order is not in BandejaFacturacion
        descuentos_mc = repo.descuentos_marca_categoria()

        # Read rates series to convert VES invoice residual to USD
        tasas_rows = repo._g.read_rows("SerieTasas")
        rates_map = {}
        for r in tasas_rows:
            ts = str(r.get("timestamp", ""))[:10]
            tbcv = r.get("tasa_bcv")
            if ts and tbcv:
                try:
                    rates_map[ts] = float(tbcv)
                except Exception:
                    pass
        last_bcv_val = list(rates_map.values())[-1] if rates_map else 742.23

        # Fetch posted invoices from Odoo in batch for all orders
        so_ids = [o.so_id for o in ordenes]
        invoices_by_so = {}
        try:
            config = AppConfig.from_env()
            execute = _connect(config.odoo)
            invoices = execute(
                "account.move",
                "search_read",
                [[["invoice_origin", "in", so_ids], ["state", "=", "posted"], ["move_type", "in", ["out_invoice", "out_refund"]]]],
                {"fields": ["id", "name", "invoice_origin", "amount_total", "amount_residual", "currency_id", "invoice_date"]}
            )
            for inv in invoices:
                so = str(inv.get("invoice_origin", "")).strip()
                if so:
                    invoices_by_so.setdefault(so, []).append(inv)
        except Exception as e:
            logger.warning("Error al consultar facturas Odoo en get_reporte_saldos: %s", e)

        # Read historical audit price lists from Google Sheets (ListasPreciosHistoricas)
        hist_rows = repo._g.read_rows("ListasPreciosHistoricas")
        hist_map = {}
        for r in hist_rows:
            code = str(r.get("codigo", "")).strip()
            if code:
                try:
                    hist_map[code] = {
                        "nombre": r.get("producto_nombre", ""),
                        "usd": Decimal(str(r.get("precio_usd", "0") or "0")),
                        "eur": Decimal(str(r.get("precio_bcv_euro", "0") or "0")),
                    }
                except Exception:
                    pass

        cutoff_historical = date(2026, 3, 12)
        reporte = []
        vendedores_set = set()

        def empty_kpi():
            return {"deudor_bcv": 0.0, "desc_bcv": 0.0, "desc_usd": 0.0, "factura_odoo": 0.0}

        kpi_total_general = empty_kpi()
        kpi_total_vencido = empty_kpi()
        kpi_vigentes = empty_kpi()
        kpi_1_30 = empty_kpi()
        kpi_31_60 = empty_kpi()
        kpi_61_90 = empty_kpi()
        kpi_mas_90 = empty_kpi()

        today_date = date.today()

        for o in ordenes:
            order_lines = lines_by_so.get(o.so_id, [])
            odoo_info = so_odoo_data.get(o.so_id, {})
            
            # Compute actual net delivered subtotal per product line (cantidad_entregada * precio_unitario)
            if order_lines:
                monto_entregado_neto_usd = sum(
                    max(Decimal("0"), Decimal(str(ln.get("cantidad_entregada") if ln.get("cantidad_entregada") not in (None, "", "None") else ln.get("cantidad", "0")))) * Decimal(str(ln.get("precio_unitario", "0")))
                    for ln in order_lines
                )
            else:
                st_fallback = odoo_info.get("state") or getattr(o, "estado_orden", "sale")
                monto_entregado_neto_usd = o.monto_total if st_fallback not in ["cancel", "draft"] else Decimal("0")

            # REGLA DE AUDITORÍA POR CANTIDADES ENTREGADAS Y DEVOLUCIONES (SIN HARDCODING):
            # Si el valor de mercancía efectivamente despachada y retenida por el cliente es 0
            # (sin despachar o devuelta a almacén 100%), no genera saldo deudor por cobrar.
            if monto_entregado_neto_usd <= Decimal("0"):
                continue

            client_name = clientes_map.get(o.cliente_id, f"Cliente ID: {o.cliente_id}")
            vendedor = odoo_info.get("vendedor", "Sin Vendedor")
            vendedores_set.add(vendedor)

            p_data = pagos_by_so.get(o.so_id, {"abono_bcv": Decimal("0"), "abono_binance": Decimal("0"), "ultimo_abono": None})
            abono_bcv = float(p_data["abono_bcv"])
            abono_binance = float(p_data["abono_binance"])
            fecha_ultimo_abono = p_data["ultimo_abono"]

            subtotal = monto_entregado_neto_usd
            
            lista_id_str = str(o.lista_precios or "").strip()
            is_historical = (not lista_id_str or lista_id_str in ("0", "None", "") or o.fecha < cutoff_historical)

            # Compute projected USD subtotal and total using UI candidate USD pricelists or Historical List
            total_proyectado_usd = Decimal("0.0")
            for ln in order_lines:
                qty = max(Decimal("0"), Decimal(str(ln.get("cantidad_entregada") if ln.get("cantidad_entregada") not in (None, "", "None") else ln.get("cantidad", "0"))))
                prod_raw = ln.get("producto", "")
                pt_id = extract_product_tmpl_id(prod_raw)
                
                if is_historical:
                    code_key = str(pt_id) if pt_id else prod_raw.strip()
                    hist_info = hist_map.get(code_key)
                    if hist_info and hist_info["usd"] > Decimal("0"):
                        price_usd = hist_info["usd"]
                    else:
                        price_usd = Decimal(str(ln.get("precio_unitario", "0")))
                else:
                    eff_price = resolve_effective_pricelist_price(pt_id, o.fecha, usd_ids, rules_usd) if pt_id else None
                    price_usd = eff_price if eff_price is not None else Decimal(str(ln.get("precio_unitario", "0")))
                
                total_proyectado_usd += qty * price_usd
                
            if is_historical:
                lista_name = "Lista Histórica Auditoría" if o.fecha < cutoff_historical else "Lista Histórica (Sin Lista)"
                monto_total_proyectado_usd = float(total_proyectado_usd) if total_proyectado_usd > Decimal("0") else float(o.monto_total)
            elif not lista_id_str or lista_id_str in ("0", "None"):
                lista_name = "Sin Lista (Odoo)"
                monto_total_proyectado_usd = float(o.monto_total)
            elif lista_id_str in [str(x) for x in usd_ids]:
                lista_name = f"Lista USD (#{lista_id_str})"
                monto_total_proyectado_usd = float(o.monto_total)
            elif lista_id_str in [str(x) for x in ves_ids]:
                lista_name = f"Precio VES (#{lista_id_str})"
                monto_total_proyectado_usd = float(total_proyectado_usd) if total_proyectado_usd > Decimal("0") else float(o.monto_total)
            else:
                lista_name = f"Lista #{lista_id_str}"
                monto_total_proyectado_usd = float(total_proyectado_usd) if total_proyectado_usd > Decimal("0") else float(o.monto_total)
            
            # Calculate Odoo Invoice residual balance for posted invoices (converted to USD if VES)
            inv_list = invoices_by_so.get(o.so_id, [])
            if inv_list:
                tot_res_usd = 0.0
                inv_names_list = []
                for inv in inv_list:
                    inv_names_list.append(str(inv.get("name", "")))
                    res_val = float(inv.get("amount_residual", 0.0))
                    curr = inv.get("currency_id")
                    c_name = curr[1] if isinstance(curr, (list, tuple)) and len(curr) > 1 else "USD"
                    inv_dt = str(inv.get("invoice_date") or o.fecha.isoformat())[:10]
                    rate = rates_map.get(inv_dt, last_bcv_val)
                    if c_name == "VES" and rate > 0:
                        tot_res_usd += res_val / rate
                    else:
                        tot_res_usd += res_val
                saldo_factura_odoo = max(0.0, float(tot_res_usd))
                factura_odoo_nombre = ", ".join(inv_names_list)
            else:
                saldo_factura_odoo = None
                factura_odoo_nombre = "Sin Factura"

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
                # Evaluate theoretical discounts for order o in memory using active rules
                desc_calc_monto = Decimal("0.0")
                descuentos_desglose = []
                
                for ln in order_lines:
                    cant = Decimal(str(ln.get("cantidad_entregada") if ln.get("cantidad_entregada") not in (None, "", "None") else ln.get("cantidad", "0")))
                    precio = Decimal(str(ln.get("precio_unitario", "0")))
                    subt_linea = cant * precio
                    
                    prod_raw = ln.get("producto", "")
                    marca = str(ln.get("marca") or "*")
                    cat = str(ln.get("categoria") or "*")
                    
                    r_desc = descuento_vigente(descuentos_mc, marca=marca, categoria=cat, tipo=TipoDescuento.CONTADO, fecha=o.fecha)
                    if r_desc and r_desc.porcentaje > Decimal("0"):
                        m_linea = subt_linea * (r_desc.porcentaje / Decimal("100"))
                        desc_calc_monto += m_linea
                        descuentos_desglose.append({
                            "origen": "descuento_marca_categoria",
                            "descripcion": f"Desc. {r_desc.marca}/{r_desc.categoria} ({r_desc.porcentaje}%)",
                            "monto": float(m_linea)
                        })

                total_descuentos_monto = float(desc_calc_monto)
                total_con_descuentos = float(o.monto_total) - total_descuentos_monto

            # Debt columns
            monto_orig = float(o.monto_total)
            saldo_deudor_bcv = max(0.0, monto_orig - abono_bcv)
            saldo_deudor_lista_usd = max(0.0, monto_total_proyectado_usd - abono_binance)
            saldo_con_descuento_bcv = max(0.0, saldo_deudor_bcv - total_descuentos_monto)
            saldo_con_descuento_lista_usd = max(0.0, saldo_deudor_lista_usd - total_descuentos_monto)

            # Dates & aging calculation
            fecha_delivery = picking_delivery_map.get(o.so_id)
            if not fecha_delivery:
                fecha_delivery = o.fecha.isoformat()
            
            term_name = odoo_info.get("payment_term_name") or "Contado"
            dias_credito = parse_term_days(term_name)
            
            try:
                dt_del = datetime.strptime(fecha_delivery[:10], "%Y-%m-%d").date()
            except Exception:
                dt_del = o.fecha
            dt_venc = dt_del + timedelta(days=dias_credito)
            fecha_vencimiento = dt_venc.isoformat()
            
            dias_vencido = 0
            if today_date > dt_venc:
                dias_vencido = (today_date - dt_venc).days

            # Accumulate Aging KPIs with 4 distinct sub-balances per bucket
            s_inv = saldo_factura_odoo if saldo_factura_odoo is not None else 0.0
            if saldo_deudor_bcv > 0.05 or saldo_con_descuento_lista_usd > 0.05 or s_inv > 0.05:
                # 1. Total General por Cobrar (Always accumulate)
                kpi_total_general["deudor_bcv"] += saldo_deudor_bcv
                kpi_total_general["desc_bcv"] += saldo_con_descuento_bcv
                kpi_total_general["desc_usd"] += saldo_con_descuento_lista_usd
                kpi_total_general["factura_odoo"] += s_inv

                if dias_vencido <= 0:
                    kpi_vigentes["deudor_bcv"] += saldo_deudor_bcv
                    kpi_vigentes["desc_bcv"] += saldo_con_descuento_bcv
                    kpi_vigentes["desc_usd"] += saldo_con_descuento_lista_usd
                    kpi_vigentes["factura_odoo"] += s_inv
                else:
                    # 2. Total Vencido General (All overdue orders)
                    kpi_total_vencido["deudor_bcv"] += saldo_deudor_bcv
                    kpi_total_vencido["desc_bcv"] += saldo_con_descuento_bcv
                    kpi_total_vencido["desc_usd"] += saldo_con_descuento_lista_usd
                    kpi_total_vencido["factura_odoo"] += s_inv

                    if 1 <= dias_vencido <= 30:
                        kpi_1_30["deudor_bcv"] += saldo_deudor_bcv
                        kpi_1_30["desc_bcv"] += saldo_con_descuento_bcv
                        kpi_1_30["desc_usd"] += saldo_con_descuento_lista_usd
                        kpi_1_30["factura_odoo"] += s_inv
                    elif 31 <= dias_vencido <= 60:
                        kpi_31_60["deudor_bcv"] += saldo_deudor_bcv
                        kpi_31_60["desc_bcv"] += saldo_con_descuento_bcv
                        kpi_31_60["desc_usd"] += saldo_con_descuento_lista_usd
                        kpi_31_60["factura_odoo"] += s_inv
                    elif 61 <= dias_vencido <= 90:
                        kpi_61_90["deudor_bcv"] += saldo_deudor_bcv
                        kpi_61_90["desc_bcv"] += saldo_con_descuento_bcv
                        kpi_61_90["desc_usd"] += saldo_con_descuento_lista_usd
                        kpi_61_90["factura_odoo"] += s_inv
                    else:
                        kpi_mas_90["deudor_bcv"] += saldo_deudor_bcv
                        kpi_mas_90["desc_bcv"] += saldo_con_descuento_bcv
                        kpi_mas_90["desc_usd"] += saldo_con_descuento_lista_usd
                        kpi_mas_90["factura_odoo"] += s_inv

            conc = concs.get(o.so_id)

            reporte.append({
                "so_id": o.so_id,
                "cliente_nombre": client_name,
                "vendedor": vendedor,
                "fecha": o.fecha.isoformat(),
                "fecha_entrega": fecha_delivery[:10],
                "terminos_pago": term_name,
                "dias_credito": dias_credito,
                "fecha_vencimiento": fecha_vencimiento,
                "dias_vencido": dias_vencido,
                "fecha_ultimo_abono": fecha_ultimo_abono,
                "lista_precios": lista_name,
                "lista_origen": lista_name,
                "subtotal": float(subtotal),
                "monto_total": monto_orig,
                "monto_odoo": monto_orig,
                "monto_total_proyectado_usd": monto_total_proyectado_usd,
                "abono_usd_bcv": abono_bcv,
                "abono_usd_binance": abono_binance,
                "monto_pagado": abono_bcv,
                "saldo_deudor": saldo_deudor_bcv,
                "saldo_deudor_bcv": saldo_deudor_bcv,
                "saldo_deudor_lista_usd": saldo_deudor_lista_usd,
                "total_con_descuentos": total_con_descuentos,
                "total_descuentos_monto": total_descuentos_monto,
                "saldo_deudor_con_descuentos": saldo_con_descuento_bcv,
                "saldo_con_descuento_bcv": saldo_con_descuento_bcv,
                "saldo_con_descuento_lista_usd": saldo_con_descuento_lista_usd,
                "saldo_factura_odoo": saldo_factura_odoo,
                "factura_odoo_nombre": factura_odoo_nombre,
                "descuentos_desglose": descuentos_desglose,
                "facturada": o.facturada,
                "candidata_a_cierre": saldo_deudor_bcv <= 0.05 or saldo_con_descuento_lista_usd <= 0.05,
                "reconciliacion": {
                    "resultado": conc.resultado.value if conc else "pendiente"
                } if conc else None
            })

        return {
            "kpis": {
                "total_general": kpi_total_general,
                "total_vencido": kpi_total_vencido,
                "vigentes": kpi_vigentes,
                "vencidas_1_30": kpi_1_30,
                "vencidas_31_60": kpi_31_60,
                "vencidas_61_90": kpi_61_90,
                "vencidas_mas_90": kpi_mas_90,
            },
            "vendedores": sorted(list(vendedores_set)),
            "items": reporte
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))
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
@app.get("/api/odoo/listas-precio")
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

@app.get("/api/config/listas-precio-mapeo")
async def get_config_listas_precio_mapeo():
    try:
        repo = get_repo()
        rows = repo._g.read_rows("_Meta")
        meta = {r.get("key"): r.get("value", "") for r in rows if r.get("key")}
        
        usd_str = meta.get("valid_pricelists_usd", "4")
        ves_str = meta.get("valid_pricelists_ves", "5")
        
        usd_list = [x.strip() for x in usd_str.split(",") if x.strip()]
        ves_list = [x.strip() for x in ves_str.split(",") if x.strip()]
        
        return {
            "valid_pricelists_usd": usd_list,
            "valid_pricelists_ves": ves_list
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/config/listas-precio-mapeo")
async def post_config_listas_precio_mapeo(req: PricelistMapRequest):
    try:
        repo = get_repo()
        usd_val = ",".join(req.valid_pricelists_usd) if req.valid_pricelists_usd else "4"
        ves_val = ",".join(req.valid_pricelists_ves) if req.valid_pricelists_ves else "5"
        
        repo._g.upsert_row("_Meta", "key", {"key": "valid_pricelists_usd", "value": usd_val})
        repo._g.upsert_row("_Meta", "key", {"key": "valid_pricelists_ves", "value": ves_val})
        
        return {"status": "success", "message": "Mapeo de listas de precios actualizado correctamente."}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/conciliaciones/sugerencias")
async def get_conciliaciones_sugerencias(cxc_session: str | None = Cookie(default=None)):
    try:
        repo = get_repo()
        user = get_current_user_from_cookie(cxc_session)
        
        pagos_rows = repo._g.read_rows("Pagos")
        vincs = repo.all_vinculaciones()
        ordenes = repo.all_ordenes()
        clientes_rows = repo._g.read_rows("Clientes")
        clientes_map = {str(r.get("cliente_id", "")): r.get("nombre", "") for r in clientes_rows}
        
        linked_pago = {}
        linked_so = {}
        for v in vincs:
            linked_pago[v.pago_id] = linked_pago.get(v.pago_id, Decimal("0")) + v.monto_aplicado
            linked_so[v.so_id] = linked_so.get(v.so_id, Decimal("0")) + v.monto_aplicado
            
        unallocated_pagos = []
        for p in pagos_rows:
            pid = str(p.get("pago_id", "")).strip()
            if not pid:
                continue
            monto_orig = parse_decimal_safe(p.get("monto", "0"))
            monto_vinculado = linked_pago.get(pid, Decimal("0"))
            saldo = monto_orig - monto_vinculado
            
            if saldo > Decimal("0.05"):
                vendedor = p.get("vendedor") or "Sin Vendedor"
                cliente_id = str(p.get("cliente_id", "")).strip()
                cliente_nombre = p.get("cliente_nombre") or clientes_map.get(cliente_id) or f"Cliente {cliente_id}"
                
                unallocated_pagos.append({
                    "pago_id": pid,
                    "fecha_pago": str(p.get("fecha_pago") or p.get("fecha") or "")[:10],
                    "cliente_id": cliente_id,
                    "cliente_nombre": cliente_nombre,
                    "monto_original": monto_orig,
                    "saldo_pendiente": saldo,
                    "moneda": p.get("moneda", "USD"),
                    "vendedor": vendedor
                })
                
        open_orders_by_client = {}
        for o in ordenes:
            if not o.facturada:
                pagado = linked_so.get(o.so_id, Decimal("0"))
                saldo = o.monto_total - pagado
                if saldo > Decimal("0.05"):
                    if o.cliente_id not in open_orders_by_client:
                        open_orders_by_client[o.cliente_id] = []
                    open_orders_by_client[o.cliente_id].append({
                        "so_id": o.so_id,
                        "fecha": o.fecha,
                        "monto_total": o.monto_total,
                        "saldo_pendiente": saldo,
                        "vendedor": o.vendedor_email
                    })
                    
        for cid in open_orders_by_client:
            open_orders_by_client[cid].sort(key=lambda x: x["fecha"])
            
        sugerencias = []
        for p in unallocated_pagos:
            cid = p["cliente_id"]
            client_orders = open_orders_by_client.get(cid, [])
            
            monto_pago_restante = p["saldo_pendiente"]
            for o in client_orders:
                if monto_pago_restante <= Decimal("0.05"):
                    break
                if o["saldo_pendiente"] <= Decimal("0.05"):
                    continue
                    
                monto_aplicar = min(monto_pago_restante, o["saldo_pendiente"])
                
                sug_id = f"SUG_{p['pago_id']}_{o['so_id']}"
                item = {
                    "sugerencia_id": sug_id,
                    "pago_id": p["pago_id"],
                    "pago_fecha": p["fecha_pago"],
                    "cliente_nombre": p["cliente_nombre"],
                    "monto_pago": float(p["monto_original"]),
                    "saldo_pago": float(p["saldo_pendiente"]),
                    "moneda_pago": p["moneda"],
                    "so_id": o["so_id"],
                    "so_fecha": o["fecha"].isoformat() if hasattr(o["fecha"], "isoformat") else str(o["fecha"]),
                    "so_monto_total": float(o["monto_total"]),
                    "so_saldo_pendiente": float(o["saldo_pendiente"]),
                    "monto_sugerido": float(monto_aplicar),
                    "vendedor": p["vendedor"] or o["vendedor"]
                }
                
                if user and user["rol"] == "ventas":
                    u_name = (user["nombre"] or user["email"]).strip().lower()
                    if item["vendedor"].strip().lower() != u_name and user["email"].strip().lower() not in item["vendedor"].lower():
                        continue
                        
                sugerencias.append(item)
                monto_pago_restante -= monto_aplicar
                o["saldo_pendiente"] -= monto_aplicar
                
        return sugerencias
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/vincular-masivo")
async def post_vincular_masivo(req: VincularMasivoRequest, background_tasks: BackgroundTasks):
    try:
        repo = get_repo()
        last_tasa = repo.last_serie_tasa()
        tasa_bcv = last_tasa.tasa_bcv if last_tasa else Decimal("36.5")
        tasa_binance = last_tasa.tasa_binance if last_tasa else Decimal("38.0")
        
        processed = 0
        so_ids_affected = set()
        
        for item in req.items:
            pago = repo.get_pago(item.pago_id)
            if not pago:
                continue
                
            monto_dec = Decimal(str(item.monto_aplicado))
            if monto_dec <= Decimal("0"):
                continue
                
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

            vinc_id = f"VINC_{item.pago_id}_{item.so_id}"
            vinc = Vinculacion(
                vinc_id=vinc_id,
                pago_id=item.pago_id,
                so_id=item.so_id,
                monto_aplicado=monto_dec,
                hora_pago_confirmada=datetime.combine(pago.fecha_pago, datetime.min.time()),
                tasa_bcv_aplicada=tasa_bcv,
                tasa_binance_aplicada=tasa_binance,
                es_tasa_heredada=False,
                equiv_usd_bcv=equiv_usd_bcv,
                equiv_usd_binance=equiv_usd_binance,
                equiv_ves_bcv=equiv_ves_bcv,
                equiv_ves_binance=equiv_ves_binance,
                confirmado_por="Aprobador Masivo FIFO",
                timestamp_registro=datetime.now(),
                estado=EstadoVinculacion.PENDIENTE,
                moneda_abono=Moneda(pago.moneda),
                tipo_tasa_abono=TipoTasa.BCV
            )
            
            repo.update_vinculacion(vinc)
            processed += 1
            so_ids_affected.add(item.so_id)
            
        for so_id in so_ids_affected:
            background_tasks.add_task(recalculate_all, so_id)
            
        return {
            "status": "success",
            "message": f"Se procesaron {processed} vinculaciones exitosamente.",
            "procesados": processed
        }
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
        current_year_month = datetime.now().strftime("%Y-%m")
        execute = None
        try:
            config = AppConfig.from_env()
            execute = _connect(config.odoo)
        except Exception as e_conn:
            logger.warning("No se pudo conectar a Odoo para clientes-auditoria, usando fallback Sheets: %s", e_conn)

        stats = {}
        partners_data = []

        if execute:
            partners = execute(
                "res.partner",
                "search_read",
                [[["customer_rank", ">", 0]]],
                {"fields": ["id", "name", "create_date"]}
            )
            orders = execute(
                "sale.order",
                "search_read",
                [[["state", "in", ["sale", "done"]]]],
                {"fields": ["id", "name", "partner_id", "date_order"]}
            )
            so_ids = [o["id"] for o in orders]
            lines = []
            if so_ids:
                try:
                    lines = execute(
                        "sale.order.line",
                        "search_read",
                        [[["order_id", "in", so_ids]]],
                        {"fields": ["order_id", "product_uom_qty", "qty_delivered", "product_id"]}
                    )
                except Exception:
                    pass

            product_ids = list({l["product_id"][0] for l in lines if isinstance(l.get("product_id"), list)})
            product_map = {}
            if product_ids:
                try:
                    prods = execute(
                        "product.product",
                        "search_read",
                        [[["id", "in", product_ids]]],
                        {"fields": ["id", "volume", "weight", "brand_id"]}
                    )
                    for p in prods:
                        b_info = p.get("brand_id")
                        b_name = b_info[1] if isinstance(b_info, list) else ""
                        vol = parse_decimal_safe(p.get("volume") or "0")
                        if vol == Decimal("0"):
                            vol = parse_decimal_safe(p.get("weight") or "1.0")
                        product_map[p["id"]] = {"brand": b_name, "volume": vol}
                except Exception:
                    pass

            so_partner_map = {}
            for o in orders:
                pid_info = o.get("partner_id")
                if isinstance(pid_info, list) and len(pid_info) > 0:
                    pid = str(pid_info[0])
                    so_partner_map[o["id"]] = pid
                    date_str = str(o.get("date_order") or "")
                    
                    s = stats.setdefault(pid, {
                        "count": 0,
                        "count_mes": 0,
                        "litros_global": Decimal("0"),
                        "litros_sinoco": Decimal("0"),
                        "last_date": ""
                    })
                    s["count"] += 1
                    if date_str and date_str.startswith(current_year_month):
                        s["count_mes"] += 1
                    if date_str and (not s["last_date"] or date_str > s["last_date"]):
                        s["last_date"] = date_str

            for l in lines:
                so_id = l["order_id"][0] if isinstance(l.get("order_id"), list) else None
                pid = so_partner_map.get(so_id)
                if pid and pid in stats:
                    p_info = l.get("product_id")
                    p_id = p_info[0] if isinstance(p_info, list) else None
                    if p_id in product_map:
                        brand = product_map[p_id]["brand"]
                        vol = product_map[p_id]["volume"]
                        qty = parse_decimal_safe(l.get("qty_delivered") or l.get("product_uom_qty") or "0")
                        total_l = qty * vol
                        if "GLOBAL" in brand.upper():
                            stats[pid]["litros_global"] += total_l
                        elif "SINOCO" in brand.upper():
                            stats[pid]["litros_sinoco"] += total_l

            for p in partners:
                partners_data.append({
                    "id": str(p["id"]),
                    "name": p["name"],
                    "create_date": str(p.get("create_date") or "").split(" ")[0] or "N/A"
                })

        else:
            repo = get_repo()
            clientes_rows = repo._g.read_rows("Clientes")
            ordenes = repo.all_ordenes()
            lineas = repo._g.read_rows("LineasOrden")

            for c in clientes_rows:
                partners_data.append({
                    "id": c.get("cliente_id", ""),
                    "name": c.get("nombre", ""),
                    "create_date": "N/A"
                })

            so_partner_map = {}
            for o in ordenes:
                pid = str(o.cliente_id)
                so_partner_map[o.so_id] = pid
                date_str = o.fecha.isoformat()
                
                s = stats.setdefault(pid, {
                    "count": 0,
                    "count_mes": 0,
                    "litros_global": Decimal("0"),
                    "litros_sinoco": Decimal("0"),
                    "last_date": ""
                })
                s["count"] += 1
                if date_str.startswith(current_year_month):
                    s["count_mes"] += 1
                if not s["last_date"] or date_str > s["last_date"]:
                    s["last_date"] = date_str

            for l in lineas:
                so_id = l.get("so_id")
                pid = so_partner_map.get(so_id)
                if pid and pid in stats:
                    brand = str(l.get("marca", "")).upper()
                    qty = parse_decimal_safe(l.get("cantidad_entregada") or l.get("cantidad") or "0")
                    if "GLOBAL" in brand:
                        stats[pid]["litros_global"] += qty
                    elif "SINOCO" in brand:
                        stats[pid]["litros_sinoco"] += qty

        resultado = []
        for p in partners_data:
            pid = p["id"]
            p_stats = stats.get(pid, {
                "count": 0,
                "count_mes": 0,
                "litros_global": Decimal("0"),
                "litros_sinoco": Decimal("0"),
                "last_date": "N/A"
            })
            resultado.append({
                "id": pid,
                "nombre": p["name"],
                "fecha_creacion": p["create_date"],
                "ventas_cantidad": p_stats["count"],
                "ventas_mes_actual": p_stats["count_mes"],
                "litros_global": float(p_stats["litros_global"]),
                "litros_sinoco": float(p_stats["litros_sinoco"]),
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
                "tipo_evaluacion": getattr(r, "tipo_evaluacion", "orden"),
                "dias_evaluacion": getattr(r, "dias_evaluacion", 30),
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

        regla_id = f"VOL_{uuid.uuid4().hex[:8].upper()}"
        rule = DescuentoVolumen(
            regla_id=regla_id,
            marca=req.marca,
            categoria=req.categoria,
            litros_minimo=Decimal(str(req.litros_minimo)),
            porcentaje=Decimal(str(req.porcentaje)),
            tipo_evaluacion=req.tipo_evaluacion,
            dias_evaluacion=req.dias_evaluacion,
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
                "min_cajas": getattr(r, "min_cajas", 1),
                "max_cajas": getattr(r, "max_cajas", 9999),
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
            min_cajas=req.min_cajas,
            max_cajas=req.max_cajas,
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
            "DescuentosDiferencialCambiario": ["DescuentosDiferencialCambiario", "DescuentoBCVCompleto"],
        }
        
        candidate_names = TABLE_CANDIDATES.get(req.tabla, [req.tabla, "DescuentosProntoPago", "DescuentosMarcaCategoria", "DescuentosRecompra", "DescuentosProducto", "DescuentosDiferencialCambiario"])
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
                            if hasattr(repo._g, "invalidate_cache"):
                                repo._g.invalidate_cache(w_name)
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

        # If not found in Sheet, handle Default Fallback Rules by persisting them into Google Sheets
        DEFAULT_RULES_SEED = {
            "RECOMPRA_DEFAULT": ("DescuentosRecompra", {
                "regla_id": "RECOMPRA_DEFAULT", "porcentaje": "0.05", "max_usos_mes": "2",
                "dias_ventana": "30", "vigencia_desde": date.today().isoformat(), "vigencia_hasta": "",
                "activo": "TRUE" if req.activo else "FALSE"
            }),
            "DIF_35_VES": ("DescuentosDiferencialCambiario", {
                "regla_id": "DIF_35_VES", "nombre": "35% Fijo VES a USD", "tipo_diferencial": "fijo_35_ves_usd",
                "tipo_calculo": "fijo", "porcentaje_fijo": "0.35", "monedas_aplicables": "*", "listas_aplicables": "*",
                "vigencia_desde": date.today().isoformat(), "vigencia_hasta": "",
                "activo": "TRUE" if req.activo else "FALSE"
            }),
            "DIF_EQUIPARAR": ("DescuentosDiferencialCambiario", {
                "regla_id": "DIF_EQUIPARAR", "nombre": "Equiparar Binance N/C", "tipo_diferencial": "equiparar_binance",
                "tipo_calculo": "variable", "porcentaje_fijo": "0", "monedas_aplicables": "*", "listas_aplicables": "*",
                "vigencia_desde": date.today().isoformat(), "vigencia_hasta": "",
                "activo": "TRUE" if req.activo else "FALSE"
            }),
            "DIF_BRECHA_CIERRE": ("DescuentosDiferencialCambiario", {
                "regla_id": "DIF_BRECHA_CIERRE", "nombre": "Brecha BCV vs Binance Cierre", "tipo_diferencial": "diferencial_bcv_binance",
                "tipo_calculo": "variable", "porcentaje_fijo": "0", "monedas_aplicables": "*", "listas_aplicables": "*",
                "vigencia_desde": date.today().isoformat(), "vigencia_hasta": "",
                "activo": "TRUE" if req.activo else "FALSE"
            }),
        }

        if target_id_str in DEFAULT_RULES_SEED:
            target_table, seed_row = DEFAULT_RULES_SEED[target_id_str]
            repo._g.append_row(target_table, seed_row)
            return {
                "status": "success",
                "message": f"Regla por defecto {target_id_str} registrada y actualizada a {'Activo' if req.activo else 'Inactivo'}."
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
        last_binance = Decimal("0")

        # Find most recent valid Binance & BCV rates from all rows
        for r in reversed(rows):
            try:
                tb_val = Decimal(str(r.get("tasa_binance", "0")))
                if tb_val > Decimal("0") and last_binance <= Decimal("0"):
                    last_binance = tb_val
                tbcv_val = Decimal(str(r.get("tasa_bcv", "0")))
                if tbcv_val > Decimal("0") and last_bcv <= Decimal("0"):
                    last_bcv = tbcv_val
                if last_binance > Decimal("0") and last_bcv > Decimal("0"):
                    break
            except Exception:
                pass

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
            "tasa_binance_vigente": float(last_binance),
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


@app.get("/api/tasas-historicas")
async def get_tasas_historicas():
    try:
        repo = get_repo()
        rows = repo._g.read_rows("TasasHistoricasAuditoria")
        return {"items": rows, "count": len(rows)}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/reglas-descuento")
async def get_reglas_descuento():
    try:
        repo = get_repo()
        primera = repo._g.read_rows("PromocionPrimeraCompra")
        recompra = repo._g.read_rows("DescuentosRecompra")
        pronto_pago = repo._g.read_rows("DescuentosProntoPago")
        volumen = repo._g.read_rows("DescuentosVolumen")
        producto = repo._g.read_rows("DescuentosProducto")
        diferencial = repo._g.read_rows("DescuentosDiferencialCambiario")
        
        return {
            "primera_compra": primera,
            "recompra": recompra,
            "pronto_pago": pronto_pago,
            "volumen": volumen,
            "producto": producto,
            "diferencial_cambiario": diferencial
        }
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

        # Load UI configured pricelists (USD & VES) from _Meta
        config = AppConfig.from_env()
        execute = _connect(config.odoo)
        usd_ids, ves_ids = get_ui_pricelist_ids(repo)
        all_candidate_ids = list(set(usd_ids + ves_ids))

        rules_all = execute(
            "product.pricelist.item",
            "search_read",
            [[["pricelist_id", "in", all_candidate_ids], ["compute_price", "=", "fixed"]]],
            {"fields": ["pricelist_id", "product_tmpl_id", "fixed_price", "date_start", "date_end"]}
        ) if execute else []

        # Load accepted anomalies from Google Sheets
        anomalias_aceptadas_rows = repo._g.read_rows("AnomaliasAceptadas")
        aceptadas_map = {r.get("anomalia_id"): r for r in anomalias_aceptadas_rows}
        lines_by_so = {}
        for r in lines_rows:
            so = r.get("so_id", "")
            if so:
                lines_by_so.setdefault(so, []).append(r)

        # Fetch posted invoices from Odoo in batch for audit comparison
        so_ids = [o.so_id for o in ordenes]
        invoices_by_so = {}
        try:
            invoices = execute(
                "account.move",
                "search_read",
                [[["invoice_origin", "in", so_ids], ["state", "=", "posted"], ["move_type", "in", ["out_invoice", "out_refund"]]]],
                {"fields": ["id", "name", "invoice_origin", "amount_total", "amount_residual", "currency_id", "invoice_date"]}
            ) if execute else []
            for inv in invoices:
                so = str(inv.get("invoice_origin", "")).strip()
                if so:
                    invoices_by_so.setdefault(so, []).append(inv)
        except Exception as e:
            logger.warning("Error al consultar facturas Odoo en get_auditoria: %s", e)

        # Read rates series to convert VES invoice residual to USD
        tasas_rows = repo._g.read_rows("SerieTasas")
        rates_map = {}
        for r in tasas_rows:
            ts = str(r.get("timestamp", ""))[:10]
            tbcv = r.get("tasa_bcv")
            if ts and tbcv:
                try:
                    rates_map[ts] = float(tbcv)
                except Exception:
                    pass
        last_bcv_val = list(rates_map.values())[-1] if rates_map else 742.23

        # Load payments map by SO for net debt comparison
        vincs = repo.all_vinculaciones()
        pagos_by_so = {}
        for v in vincs:
            if v.estado == EstadoVinculacion.CONCILIADO:
                pagos_by_so[v.so_id] = pagos_by_so.get(v.so_id, 0.0) + float(v.monto_aplicado)

        operaciones_conformes = []
        raw_discrepancias = []
        discrepancias_facturas_odoo = []

        for o in ordenes:
            c_name = clientes_map.get(o.cliente_id, f"Cliente ID: {o.cliente_id}")
            b = bandeja_map.get(o.so_id)
            so_lines = lines_by_so.get(o.so_id, [])

            has_discrepancy = False
            lista_id_str = str(o.lista_precios or "").strip()
            is_ves = lista_id_str in [str(x) for x in ves_ids]
            candidate_list_ids = ves_ids if is_ves else usd_ids
            pricelist_label = f"Lista VES (#{lista_id_str})" if is_ves else f"Lista USD (#{lista_id_str})"
            is_historical = (not lista_id_str or lista_id_str in ("0", "None", "") or o.fecha < cutoff_historical)

            # Check 1: Unit prices vs correct official pricelist or Historical List
            for ln in so_lines:
                qty = parse_decimal_safe(ln.get("cantidad", "0"))
                qty_delivered = parse_decimal_safe(ln.get("cantidad_entregada", ln.get("qty_delivered", "0")))
                price_order = parse_decimal_safe(ln.get("precio_unitario", "0"))

                # Skip returned/non-delivered/zero-qty lines (returns with price=0 or qty=0)
                if qty <= Decimal("0") or price_order <= Decimal("0") or qty_delivered <= Decimal("0"):
                    continue

                pt_id = extract_product_tmpl_id(ln.get("producto", ""))
                if is_historical:
                    code_key = str(pt_id) if pt_id else str(ln.get("producto", "")).strip()
                    hist_info = hist_map.get(code_key)
                    price_official = hist_info["usd"] if hist_info and hist_info["usd"] > Decimal("0") else None
                    cur_label = "Lista Histórica Auditoría (Pre-12-Mar)" if o.fecha < cutoff_historical else "Lista Histórica Auditoría (Sin Lista)"
                else:
                    price_official = resolve_effective_pricelist_price(pt_id, o.fecha, candidate_list_ids, rules_all) if pt_id else None
                    cur_label = pricelist_label

                if price_official is not None:
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
                            "detalle": f"Producto ID {pt_id}: Precio orden (${float(price_order):.2f}) < {cur_label} (${float(price_official):.2f}) [Entregado: {float(qty_delivered):.0f} und]",
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

            # Check 4: Discrepancia entre Saldo Deudor CxC vs Saldo Residual Factura Odoo
            inv_list = invoices_by_so.get(o.so_id, [])
            if inv_list:
                tot_res_usd = 0.0
                inv_names_list = []
                for inv in inv_list:
                    inv_names_list.append(str(inv.get("name", "")))
                    res_val = float(inv.get("amount_residual", 0.0))
                    curr = inv.get("currency_id")
                    c_name_inv = curr[1] if isinstance(curr, (list, tuple)) and len(curr) > 1 else "USD"
                    inv_dt = str(inv.get("invoice_date") or o.fecha.isoformat())[:10]
                    rate = rates_map.get(inv_dt, last_bcv_val)
                    if c_name_inv == "VES" and rate > 0:
                        tot_res_usd += res_val / rate
                    else:
                        tot_res_usd += res_val
                
                saldo_factura_odoo = max(0.0, float(tot_res_usd))
                factura_nombre = ", ".join(inv_names_list)
                abono_conc = pagos_by_so.get(o.so_id, 0.0)
                saldo_cxc = max(0.0, float(o.monto_total) - abono_conc)

                diff_cxc_inv = abs(saldo_cxc - saldo_factura_odoo)
                if diff_cxc_inv > 0.50:
                    discrepancias_facturas_odoo.append({
                        "so_id": o.so_id,
                        "factura_id": factura_nombre,
                        "cliente_nombre": c_name,
                        "vendedor": o.vendedor_email or "Sin Vendedor",
                        "fecha": o.fecha.isoformat(),
                        "saldo_cxc": round(saldo_cxc, 2),
                        "saldo_factura_odoo": round(saldo_factura_odoo, 2),
                        "diferencia": round(diff_cxc_inv, 2),
                        "causa_probable": "Abono / Pago registrado en CxC pero pendiente de aplicar en Odoo" if saldo_factura_odoo > saldo_cxc else "Diferencia por retenciones o ajustes en factura Odoo"
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
            "discrepancias_facturas_odoo": discrepancias_facturas_odoo,
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


class MarcarRecibidoRequest(BaseModel):
    pago_ids: list[str]
    recibido_por: str = "Administración"


@app.get("/api/cobranza")
async def get_cobranza_list(cxc_session: str | None = Cookie(default=None)):
    try:
        repo = get_repo()
        user = get_current_user_from_cookie(cxc_session)
        pagos = repo._g.read_rows("Pagos")
        vincs = repo.all_vinculaciones()
        vinc_by_pago = {v.pago_id: v for v in vincs}
        
        # Load clients and orders for vendor names
        ordenes = {o.so_id: o for o in repo.all_ordenes()}
        clientes_rows = repo._g.read_rows("Clientes")
        clientes_map = {r.get("cliente_id"): r.get("nombre", "") for r in clientes_rows}
        
        resultados = []
        for p in pagos:
            pid = str(p.get("pago_id", "")).strip()
            if not pid:
                continue
            
            moneda = p.get("moneda", "VES")
            monto = parse_decimal_safe(p.get("monto", "0"))
            fecha_str = str(p.get("fecha", ""))[:10]
            
            # Find rates for that date
            bcv_rate, binance_rate = get_rate_for_datetime(datetime.now())
            
            # Compute BCV and Binance equivalents
            eq_bcv = monto if moneda == "USD" else (monto / bcv_rate if bcv_rate > 0 else Decimal("0"))
            eq_binance = monto if moneda == "USD" else (monto / binance_rate if binance_rate > 0 else Decimal("0"))
            
            v = vinc_by_pago.get(pid)
            so_id = v.so_id if v else "-"
            orden_obj = ordenes.get(so_id)
            vendedor = p.get("vendedor") or (orden_obj.vendedor_email if orden_obj else "Sin Vendedor")
            cliente_name_from_order = clientes_map.get(orden_obj.cliente_id, "Sin Cliente") if orden_obj else "Sin Cliente"
            cliente = p.get("cliente_nombre") or cliente_name_from_order
            
            item = {
                "pago_id": pid,
                "fecha": fecha_str,
                "monto": float(monto),
                "moneda": moneda,
                "metodo_pago": p.get("metodo_pago") or p.get("forma_pago") or "Efectivo",
                "referencia": p.get("referencia") or p.get("banco") or "-",
                "vendedor": vendedor,
                "cliente_nombre": cliente,
                "so_id": so_id,
                "tasa_bcv": float(bcv_rate),
                "tasa_binance": float(binance_rate),
                "equivalente_bcv_usd": float(eq_bcv),
                "equivalente_binance_usd": float(eq_binance),
                "recibido": p.get("recibido") == "TRUE",
                "numero_recibido": p.get("numero_recibido") or "-",
                "fecha_recibido": p.get("fecha_recibido") or "-",
                "recibido_por": p.get("recibido_por") or "-"
            }
            
            # Filter if user is vendor
            if user and user["rol"] == "ventas":
                u_name = (user["nombre"] or user["email"]).strip().lower()
                if item["vendedor"].strip().lower() != u_name and user["email"].strip().lower() not in item["vendedor"].lower():
                    continue
                    
            resultados.append(item)
            
        return resultados
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cobranza/marcar-recibido")
async def post_marcar_recibido(req: MarcarRecibidoRequest, cxc_session: str | None = Cookie(default=None)):
    try:
        user = get_current_user_from_cookie(cxc_session)
        recibido_por = req.recibido_por or (user["nombre"] if user else "Administración")
        repo = get_repo()
        pagos_rows = repo._g.read_rows("Pagos")
        
        now = datetime.now()
        recibo_num = f"REC-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}"
        
        target_pago_ids = set(req.pago_ids)
        pagos_actualizados = []
        
        for r in pagos_rows:
            pid = str(r.get("pago_id", "")).strip()
            if pid in target_pago_ids:
                r["recibido"] = "TRUE"
                r["numero_recibido"] = recibo_num
                r["fecha_recibido"] = now.isoformat()[:19]
                r["recibido_por"] = recibido_por
                repo._g.upsert_row("Pagos", "pago_id", r)
                pagos_actualizados.append(r)
                
        return {
            "status": "success",
            "numero_recibido": recibo_num,
            "fecha_recibido": now.strftime("%Y-%m-%d %H:%M"),
            "recibido_por": recibido_por,
            "pagos": pagos_actualizados
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/reporte/diario")
async def get_reporte_diario():
    try:
        repo = get_repo()
        ordenes = repo.all_ordenes()
        lineas = repo._g.read_rows("LineasOrden")
        pagos = repo._g.read_rows("Pagos")
        
        prod_litros_map = {}
        try:
            config = AppConfig.from_env()
            execute = _connect(config.odoo)
            if execute:
                prods = execute("product.product", "search_read", [], {"fields": ["id", "default_code", "name", "volume", "weight"]})
                for p in prods:
                    pid = p.get("id")
                    vol = parse_decimal_safe(p.get("volume") or "0")
                    if vol == Decimal("0"):
                        vol = parse_decimal_safe(p.get("weight") or "1.0")
                    prod_litros_map[pid] = vol
        except Exception as e_p:
            logger.warning("Error leyendo litros de productos: %s", e_p)
            
        # 1. Ventas por Día (USD y Litros)
        ventas_por_dia = {}
        for o in ordenes:
            st = getattr(o, "estado_orden", "sale")
            if st in ["cancel", "draft", "sent"] and not (o.entregada_completa or bool(o.fecha_entrega)):
                continue
            fecha_key = o.fecha.isoformat()[:10]
            if fecha_key not in ventas_por_dia:
                ventas_por_dia[fecha_key] = {"fecha": fecha_key, "total_usd": Decimal("0"), "litros_totales": Decimal("0"), "ordenes_count": 0}
            ventas_por_dia[fecha_key]["total_usd"] += o.monto_total
            ventas_por_dia[fecha_key]["ordenes_count"] += 1

        # Sum line liters
        for l in lineas:
            so_id = l.get("so_id")
            prod_id = int(l.get("product_id") or 0)
            qty = parse_decimal_safe(l.get("cantidad_entregada") or l.get("cantidad_ordenada") or "0")
            l_per_unit = prod_litros_map.get(prod_id, Decimal("1.0"))
            total_l = qty * l_per_unit
            
            o_match = next((o for o in ordenes if o.so_id == so_id), None)
            if o_match:
                fk = o_match.fecha.isoformat()[:10]
                if fk in ventas_por_dia:
                    ventas_por_dia[fk]["litros_totales"] += total_l

        # 2. Cobranza por Día (Desglosada por Moneda y Método)
        cobranza_por_dia = {}
        for p in pagos:
            fecha_key = str(p.get("fecha", ""))[:10] or date.today().isoformat()
            if fecha_key not in cobranza_por_dia:
                cobranza_por_dia[fecha_key] = {
                    "fecha": fecha_key,
                    "total_eq_bcv": Decimal("0"),
                    "total_eq_binance": Decimal("0"),
                    "por_moneda": {},
                    "por_metodo": {}
                }
            monto = parse_decimal_safe(p.get("monto", "0"))
            moneda = p.get("moneda", "VES")
            metodo = p.get("metodo_pago") or p.get("forma_pago") or "Efectivo"
            
            bcv_rate, binance_rate = get_rate_for_datetime(datetime.now())
            eq_bcv = monto if moneda == "USD" else (monto / bcv_rate if bcv_rate > 0 else Decimal("0"))
            eq_binance = monto if moneda == "USD" else (monto / binance_rate if binance_rate > 0 else Decimal("0"))

            cobranza_por_dia[fecha_key]["total_eq_bcv"] += eq_bcv
            cobranza_por_dia[fecha_key]["total_eq_binance"] += eq_binance
            
            if moneda not in cobranza_por_dia[fecha_key]["por_moneda"]:
                cobranza_por_dia[fecha_key]["por_moneda"][moneda] = Decimal("0")
            cobranza_por_dia[fecha_key]["por_moneda"][moneda] += monto
            
            if metodo not in cobranza_por_dia[fecha_key]["por_metodo"]:
                cobranza_por_dia[fecha_key]["por_metodo"][metodo] = Decimal("0")
            cobranza_por_dia[fecha_key]["por_metodo"][metodo] += eq_bcv

        ventas_list = [
            {
                "fecha": k,
                "total_usd": float(v["total_usd"]),
                "litros_totales": float(v["litros_totales"]),
                "ordenes_count": v["ordenes_count"]
            } for k, v in sorted(ventas_por_dia.items(), reverse=True)
        ]
        
        cobranza_list = [
            {
                "fecha": k,
                "total_eq_bcv": float(v["total_eq_bcv"]),
                "total_eq_binance": float(v["total_eq_binance"]),
                "por_moneda": {m: float(val) for m, val in v["por_moneda"].items()},
                "por_metodo": {m: float(val) for m, val in v["por_metodo"].items()}
            } for k, v in sorted(cobranza_por_dia.items(), reverse=True)
        ]
        
        return {
            "ventas_diarias": ventas_list,
            "cobranza_diaria": cobranza_list
        }
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        raise HTTPException(status_code=500, detail=str(e))
