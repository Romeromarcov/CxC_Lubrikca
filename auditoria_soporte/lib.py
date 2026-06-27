import re
import unicodedata

import pandas as pd

BASE="/sessions/optimistic-eloquent-keller/mnt/CxC_Lubrikca/Info_Odoo"
LP=BASE+"/Listas de Precio"
TODAY=pd.Timestamp("2026-06-27")

def norm(s):
    if pd.isna(s): return ""
    s=str(s).strip().upper()
    s=''.join(c for c in unicodedata.normalize('NFKD',s) if not unicodedata.combining(c))
    s=re.sub(r'[.,]',' ',s); s=re.sub(r'\s+',' ',s).strip()
    s=re.sub(r'\b(C A|CA|S A|SA|RL|SRL)\b','',s).strip()
    return re.sub(r'\s+',' ',s).strip()

def code_of(s):
    if pd.isna(s): return None
    m=re.match(r'\s*\[(\d+)\]',str(s))
    if not m: return None
    return int(m.group(1))

def sonum(x):
    if pd.isna(x): return None
    m=re.search(r'(\d+)',str(x))
    return int(m.group(1)) if m else None

def load():
    d={}
    d['pagos']=pd.read_excel(BASE+"/Pagos (account.payment).xlsx")
    d['cob']=pd.read_excel(BASE+"/Cobranza.xlsx","Cobranza")
    d['rep']=pd.read_excel(BASE+"/Reporte_Ventas_2026-02-01_al_2026-06-27.xlsx","Reporte de Ventas")
    d['so']=pd.read_excel(BASE+"/Orden de venta (sale.order).xlsx")
    d['stock']=pd.read_excel(BASE+"/Movimientos de producto (línea de movimiento de stock) (stock.move.line).xlsx")
    d['am']=pd.read_excel(BASE+"/Asiento contable (account.move).xlsx")
    xls=pd.ExcelFile(BASE+"/Cuentas x Cobrar Nuevo.xlsx")
    d['ventas']=pd.read_excel(xls,"Ventas")
    d['lim']=pd.read_excel(xls,"Limites de Descuento")
    d['montosodoo']=pd.read_excel(xls,"Montos Odoo",header=None)
    d['discrep']=pd.read_excel(xls,"Reporte Discrepancias",header=None)
    return d

def load_pricelists():
    pls={}
    f=pd.read_excel(LP+"/Lista de Precio 23-02-26.xlsx","Table 1")
    f.columns=['cod','desc','pres','cont','usd_pay','ves_pay']
    f['cod']=f['cod'].apply(sonum)
    pls['feb23']=f.dropna(subset=['cod'])
    import openpyxl
    wb=openpyxl.load_workbook(LP+"/14 Precios Venta 12-3-26.xlsx",read_only=True,data_only=True)
    ws=wb['PRODUCT']; data=[]
    for i,r in enumerate(ws.iter_rows(values_only=True)):
        if i<9: continue
        if r[0] is None: continue
        data.append(r)
    wb.close()
    m=pd.DataFrame(data)
    mar=pd.DataFrame({'cod':m[0].apply(sonum),'desc':m[1],'usd_pay':pd.to_numeric(m[10],errors='coerce')})
    pls['mar12']=mar.dropna(subset=['cod'])
    j=pd.read_excel(LP+"/Lista de precios (product.pricelist) USD 15-6-26.xlsx","Sheet1")
    j['cod']=j['item_ids/product_tmpl_id'].apply(code_of)
    j['fixed']=pd.to_numeric(j['item_ids/fixed_price'],errors='coerce')
    pls['jun15usd']=j.dropna(subset=['cod'])[['cod','fixed']]
    return pls
