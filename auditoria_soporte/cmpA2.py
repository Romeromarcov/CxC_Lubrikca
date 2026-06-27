from collections import defaultdict

import lib
import pandas as pd

d=lib.load(); pg=d['pagos'].copy(); cb=d['cob'].copy()
def cur_pg(m):
    m=str(m).lower()
    if 'extrajera' in m or 'usd' in m: return 'USD'
    return 'VES'
pg['cli']=pg['Cliente/proveedor'].apply(lib.norm)
pg['fch']=pd.to_datetime(pg['Fecha']).dt.date
pg['cur']=pg['Método de pago'].apply(cur_pg)
pg['nat']=pd.to_numeric(pg['Importe firmado'],errors='coerce').round(2)  # native amount
cb['cli']=cb['Cliente'].apply(lib.norm)
cb['fch']=pd.to_datetime(cb['Fecha']).dt.date
cb['cur']=cb['Moneda'].astype(str).str.upper().str.strip()
cb['nat']=pd.to_numeric(cb['Importe firmado'],errors='coerce').round(2)

# match native amount+cur+client, allow date window +-3 days
cb_idx=defaultdict(list)
for j,r in cb.iterrows(): cb_idx[(r['cli'],r['cur'],r['nat'])].append(j)
used=set(); m_exact=0; m_timing=[]; pg_only=[]
for i,r in pg.iterrows():
    cand=[j for j in cb_idx.get((r['cli'],r['cur'],r['nat']),[]) if j not in used]
    if not cand: pg_only.append(i); continue
    # prefer same date
    same=[j for j in cand if cb.at[j,'fch']==r['fch']]
    j=same[0] if same else cand[0]
    used.add(j)
    if cb.at[j,'fch']==r['fch']: m_exact+=1
    else: m_timing.append((i,j))
cb_only=[j for j in cb.index if j not in used]
print(f"Pagos n={len(pg)} Cobranza n={len(cb)}")
print(f"Match exacto (cli+cur+monto nativo+fecha): {m_exact}")
print(f"Match con diferencia de fecha (timing): {len(m_timing)}")
print(f"Pagos Odoo SIN registro en Cobranza: {len(pg_only)}  (USD ref {pd.to_numeric(pg.loc[pg_only,'Importe referencia'],errors='coerce').sum().round(2)})")
print(f"Cobranza SIN registro en Pagos Odoo: {len(cb_only)} (USD {pd.to_numeric(cb.loc[cb_only,'Monto Unificado USD'],errors='coerce').sum().round(2)})")
print("\nPagos sin cobranza por estado:",pg.loc[pg_only,'Estado'].value_counts().to_dict())
print("Pagos sin cobranza por moneda:",pg.loc[pg_only,'cur'].value_counts().to_dict())
print("\nCobranza sin pago por moneda:",cb.loc[cb_only,'cur'].value_counts().to_dict())
print("Cobranza sin pago - rango fechas:",cb.loc[cb_only,'fch'].min(),cb.loc[cb_only,'fch'].max())
# timing detail
print("\nEjemplos timing (mismo monto, distinta fecha):")
for i,j in m_timing[:10]:
    print(f"  {str(pg.at[i,'Cliente/proveedor'])[:26]:26} {pg.at[i,'cur']} {pg.at[i,'nat']:>10} pago={pg.at[i,'fch']} cob={cb.at[j,'fch']}")
pg.loc[pg_only].to_csv("A_pagos_sin_cobranza.csv",index=False)
cb.loc[cb_only].to_csv("A_cobranza_sin_pagos.csv",index=False)
# save matched-with-diff for currency/amount? already same amount.
print("\nSaved A_pagos_sin_cobranza.csv, A_cobranza_sin_pagos.csv")
