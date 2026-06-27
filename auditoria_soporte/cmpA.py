import lib
import pandas as pd

d=lib.load()
pg=d['pagos'].copy(); cb=d['cob'].copy()
# Pagos: exclude reversals? keep all but flag estado
pg['cli']=pg['Cliente/proveedor'].apply(lib.norm)
pg['fch']=pd.to_datetime(pg['Fecha']).dt.date
pg['usd']=pd.to_numeric(pg['Importe referencia'],errors='coerce').round(2)
pg['absusd']=pg['usd'].abs()
cb['cli']=cb['Cliente'].apply(lib.norm)
cb['fch']=pd.to_datetime(cb['Fecha']).dt.date
cb['usd']=pd.to_numeric(cb['Monto Unificado USD'],errors='coerce').round(2)
print("Pagos USD-ref sum:",pg['usd'].sum().round(2)," n=",len(pg))
print("Cobranza USD sum:",cb['usd'].sum().round(2)," n=",len(cb))
print("Pagos estados:",pg['Estado'].value_counts().to_dict())

# match by cli+fch+usd (tol .01) greedy
from collections import defaultdict

cb_idx=defaultdict(list)
for i,r in cb.iterrows(): cb_idx[(r['cli'],r['fch'])].append(i)
used=set(); matched=[]; pg_only=[]
for i,r in pg.iterrows():
    cand=cb_idx.get((r['cli'],r['fch']),[])
    hit=None
    for j in cand:
        if j in used: continue
        if pd.notna(r['usd']) and pd.notna(cb.at[j,'usd']) and abs(r['usd']-cb.at[j,'usd'])<=0.01:
            hit=j; break
    if hit is not None: used.add(hit); matched.append((i,hit))
    else: pg_only.append(i)
cb_only=[j for j in cb.index if j not in used]
print(f"\nMATCHED (cli+date+usd): {len(matched)}")
print(f"Pagos sin match en Cobranza: {len(pg_only)}  (USD {pg.loc[pg_only,'usd'].sum().round(2)})")
print(f"Cobranza sin match en Pagos: {len(cb_only)}  (USD {cb.loc[cb_only,'usd'].sum().round(2)})")

# Try looser: same cli+amount any date (timing diff)
pg_o=pg.loc[pg_only].copy(); cb_o=cb.loc[cb_only].copy()
cb_idx2=defaultdict(list)
for j,r in cb_o.iterrows(): cb_idx2[(r['cli'],round(r['usd'],2) if pd.notna(r['usd']) else None)].append(j)
used2=set(); timing=[]
for i,r in pg_o.iterrows():
    cand=cb_idx2.get((r['cli'],round(r['usd'],2) if pd.notna(r['usd']) else None),[])
    for j in cand:
        if j in used2: continue
        timing.append((i,j,(pd.to_datetime(pg.at[i,'fch'])-pd.to_datetime(cb.at[j,'fch'])).days)); used2.add(j); break
print(f"\nDe los no-match: {len(timing)} coinciden por cliente+monto pero DISTINTA fecha (timing diff)")
for i,j,dd in timing[:12]:
    print(f"  {str(pg.at[i,'Cliente/proveedor'])[:28]:28} usd={pg.at[i,'usd']} pagoFecha={pg.at[i,'fch']} cobFecha={cb.at[j,'fch']} dias={dd}")
print("... total timing:",len(timing))

# remaining truly unmatched
pg_un=[i for i in pg_only if i not in {t[0] for t in timing}]
cb_un=[j for j in cb_only if j not in used2]
print(f"\nPagos REALMENTE ausentes en Cobranza: {len(pg_un)} (USD {pg.loc[pg_un,'usd'].sum().round(2)})")
print(pg.loc[pg_un,['Cliente/proveedor','fch','usd','Estado','Método de pago']].head(15).to_string())
print(f"\nCobranza REALMENTE ausente en Pagos: {len(cb_un)} (USD {cb.loc[cb_un,'usd'].sum().round(2)})")
print(cb.loc[cb_un,['Cliente','fch','usd','Moneda','Nro de Orden','Validación Ingreso']].head(15).to_string())
pg.loc[pg_un].to_csv("A_pagos_sin_cobranza.csv",index=False)
cb.loc[cb_un].to_csv("A_cobranza_sin_pagos.csv",index=False)
