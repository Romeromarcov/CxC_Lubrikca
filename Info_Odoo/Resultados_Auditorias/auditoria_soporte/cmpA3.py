from collections import defaultdict

import lib
import pandas as pd

d=lib.load(); pg=d['pagos'].copy(); cb=d['cob'].copy()
cb=cb[cb['Importe firmado'].notna() & cb['Cliente'].notna()].copy()
print("Cobranza filas pobladas:",len(cb))
def cur_pg(m):
    m=str(m).lower(); return 'USD' if ('extrajera' in m or 'usd' in m) else 'VES'
pg['cli']=pg['Cliente/proveedor'].apply(lib.norm); pg['fch']=pd.to_datetime(pg['Fecha']).dt.date
pg['cur']=pg['Método de pago'].apply(cur_pg); pg['nat']=pd.to_numeric(pg['Importe firmado'],errors='coerce').round(2)
cb['cli']=cb['Cliente'].apply(lib.norm); cb['fch']=pd.to_datetime(cb['Fecha']).dt.date
cb['cur']=cb['Moneda'].astype(str).str.upper().str.strip(); cb['nat']=pd.to_numeric(cb['Importe firmado'],errors='coerce').round(2)
cb_idx=defaultdict(list)
for j,r in cb.iterrows(): cb_idx[(r['cli'],r['cur'],r['nat'])].append(j)
used=set(); m_exact=0; m_timing=[]; pg_only=[]
for i,r in pg.iterrows():
    cand=[j for j in cb_idx.get((r['cli'],r['cur'],r['nat']),[]) if j not in used]
    if not cand: pg_only.append(i); continue
    same=[j for j in cand if cb.at[j,'fch']==r['fch']]
    j=same[0] if same else cand[0]; used.add(j)
    if cb.at[j,'fch']==r['fch']: m_exact+=1
    else: m_timing.append((i,j))
cb_only=[j for j in cb.index if j not in used]
print(f"Match exacto: {m_exact} | timing: {len(m_timing)}")
print(f"Pagos Odoo SIN cobranza: {len(pg_only)} (USDref {pd.to_numeric(pg.loc[pg_only,'Importe referencia'],errors='coerce').sum().round(2)})")
print(f"Cobranza SIN pago Odoo: {len(cb_only)} (USD {pd.to_numeric(cb.loc[cb_only,'Monto Unificado USD'],errors='coerce').sum().round(2)})")
print(" pagos_only estado:",pg.loc[pg_only,'Estado'].value_counts().to_dict()," moneda:",pg.loc[pg_only,'cur'].value_counts().to_dict())
print(" cob_only moneda:",cb.loc[cb_only,'cur'].value_counts().to_dict())
fchs=[f for f in cb.loc[cb_only,'fch'] if isinstance(f,(pd.Timestamp,)) or hasattr(f,'year')]
print(" cob_only rango fechas:",min(fchs),max(fchs))
pg.loc[pg_only,['Cliente/proveedor','fch','cur','nat','Importe referencia','Estado','Método de pago']].to_csv("A_pagos_sin_cobranza.csv",index=False)
cb.loc[cb_only,['Cliente','fch','cur','nat','Monto Unificado USD','Nro de Orden','Metodo de Pago','Validación Ingreso']].to_csv("A_cobranza_sin_pagos.csv",index=False)
# duplicates within each
dpg=pg[pg.duplicated(['cli','fch','cur','nat'],keep=False)].sort_values(['cli','fch'])
dcb=cb[cb.duplicated(['cli','fch','cur','nat'],keep=False)].sort_values(['cli','fch'])
print(f"\nPosibles DUPLICADOS Pagos (mismo cli+fch+cur+monto): {len(dpg)} filas")
print(f"Posibles DUPLICADOS Cobranza: {len(dcb)} filas")
dpg.to_csv("A_dup_pagos.csv",index=False); dcb.to_csv("A_dup_cobranza.csv",index=False)
print(dcb[['Cliente','fch','cur','nat','Nro de Orden']].head(10).to_string())
