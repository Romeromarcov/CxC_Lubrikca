import lib
import pandas as pd

d=lib.load(); TODAY=lib.TODAY
print("=== DUPLICADOS ===")
# account.move Número dup
am=d['am']; n=am['Número'].dropna()
print("account.move Número duplicados:",n[n.duplicated(keep=False)].nunique(),"valores; filas:",n.duplicated(keep=False).sum())
# sale.order ref dup
so=d['so']; r=so['Referencia de la orden'].dropna()
print("sale.order Referencia duplicada:",r.duplicated().sum())
# ventas ref dup
ven=d['ventas']; vr=ven['Referencia de la orden'].dropna()
print("CxC Ventas Referencia duplicada:",vr.duplicated().sum())
# cobranza order+amount+date dup
cb=d['cob']; cb2=cb[cb['Importe firmado'].notna()].copy()
cb2['k']=cb2['Cliente'].apply(lib.norm)+"|"+cb2['Fecha'].astype(str)+"|"+cb2['Importe firmado'].round(2).astype(str)+"|"+cb2['Moneda'].astype(str)
dupc=cb2[cb2['k'].duplicated(keep=False)]
print("Cobranza duplicados exactos (cli+fecha+monto+moneda):",len(dupc),"filas /",dupc['k'].nunique(),"grupos")

print("\n=== CAMPOS CRITICOS VACIOS ===")
for name,df,cols in [('pagos',d['pagos'],['Cliente/proveedor','Fecha','Importe firmado']),
                     ('cobranza_pobladas',cb2,['Cliente','Fecha','Importe firmado','Moneda']),
                     ('ventas',ven,['Referencia de la orden','Total','Fecha de la orden','Cliente']),
                     ('reporte',d['rep'],['Order','partner_id','price_unit_usd']),
                     ('account.move',am,['Número','Fecha de la factura','Total en moneda firmado'])]:
    for c in cols:
        miss=df[c].isna().sum()
        if miss: print(f"  {name}.{c}: {miss} vacíos")

print("\n=== FECHAS FUTURAS / ILOGICAS (hoy=2026-06-27) ===")
for name,df,c in [('pagos',d['pagos'],'Fecha'),('cobranza',cb2,'Fecha'),('ventas',ven,'Fecha de la orden'),
                  ('reporte',d['rep'],'date_order'),('sale.order',so,'Fecha de la orden'),
                  ('stock',d['stock'],'Fecha'),('account.move',am,'Fecha de la factura')]:
    s=pd.to_datetime(df[c],errors='coerce')
    fut=(s>TODAY).sum(); old=(s<pd.Timestamp('2026-01-01')).sum()
    if fut or old: print(f"  {name}.{c}: {fut} futuras, {old} antes de 2026; rango {s.min()} -> {s.max()}")
    else: print(f"  {name}.{c}: OK rango {s.min()} -> {s.max()}")

print("\n=== SALDO CxC: Total C/Dcto - Pagado = Saldo? ===")
v=ven.copy()
v['td']=pd.to_numeric(v['Total C/Dcto'],errors='coerce')
v['pag']=pd.to_numeric(v['Monto Pagado USD'],errors='coerce')
v['sal']=pd.to_numeric(v['Saldo Orden'],errors='coerce')
v['calc']=(v['td']-v['pag']).round(2)
v['difsaldo']=(v['sal']-v['calc']).round(2)
bad=v[v['difsaldo'].abs()>0.02]
print("Ordenes donde Saldo != Total C/Dcto - Pagado (>0.02):",len(bad))
print(bad[['Referencia de la orden','td','pag','sal','calc','difsaldo','Status de Pago']].head(12).to_string(index=False))
print("Saldos NEGATIVOS (sobrepago):",(v['sal']<-0.02).sum())
print(v[v['sal']<-0.02][['Referencia de la orden','td','pag','sal','Status de Pago']].head(8).to_string(index=False))
v.to_csv("G_saldos.csv",index=False); bad.to_csv("G_saldo_bad.csv",index=False); dupc.to_csv("G_dup_cobranza.csv",index=False)
