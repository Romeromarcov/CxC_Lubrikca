import lib
import numpy as np
import pandas as pd

d=lib.load()
rep=d['rep'].copy(); so=d['so'].copy(); ven=d['ventas'].copy(); mo=d['montosodoo'].copy()
rep['so']=rep['Order'].apply(lib.sonum)
repg=rep.groupby('so').agg(rep_subtotal=('price_subtotal_ref','sum'),
        rep_lines=('Order','size'),partner=('partner_id','first'),fecha=('date_order','first')).reset_index()
so['so']=so['Referencia de la orden'].apply(lib.sonum)
soh=so.groupby('so').agg(so_total=('Total','sum'),so_cli=('Cliente','first'),so_estfac=('Estado de la factura','first')).reset_index()
ven['so']=ven['Referencia de la orden'].apply(lib.sonum)
vg=ven.groupby('so').agg(ven_total=('Total','sum'),ven_dcto=('Total C/Dcto','sum'),ven_cli=('Cliente','first')).reset_index()
mo.columns=['ref','monto_odoo']; mo['so']=mo['ref'].apply(lib.sonum)
mog=mo.groupby('so').agg(monto_odoo=('monto_odoo','sum')).reset_index()

m=repg.merge(soh,on='so',how='outer').merge(vg,on='so',how='outer').merge(mog,on='so',how='outer')
print("Total ordenes (union):",len(m))
print("Presencia: rep=%d so=%d ventas=%d montosOdoo=%d"%(m['rep_subtotal'].notna().sum(),m['so_total'].notna().sum(),m['ven_total'].notna().sum(),m['monto_odoo'].notna().sum()))

# Faltantes
in_so_not_ven=m[m['so_total'].notna() & m['ven_total'].isna()]
in_ven_not_so=m[m['ven_total'].notna() & m['so_total'].isna()]
in_rep_not_ven=m[m['rep_subtotal'].notna() & m['ven_total'].isna()]
print(f"\nOrdenes en sale.order/Reporte pero NO en hoja Ventas CxC: so={len(in_so_not_ven)} rep={len(in_rep_not_ven)}")
print(f"Ordenes en hoja Ventas pero NO en sale.order: {len(in_ven_not_so)}")

# Monto: compare ventas_total vs rep_subtotal vs so_total vs monto_odoo
def diff(a,b):
    return (a-b) if (pd.notna(a) and pd.notna(b)) else np.nan
m['d_ven_rep']=(m['ven_total']-m['rep_subtotal']).round(2)
m['d_ven_so']=(m['ven_total']-m['so_total']).round(2)
m['d_ven_odoo']=(m['ven_total']-m['monto_odoo']).round(2)
m['d_so_rep']=(m['so_total']-m['rep_subtotal']).round(2)
for c,lab in [('d_ven_so','Ventas.Total vs sale.order.Total'),('d_ven_rep','Ventas.Total vs Reporte subtotal'),('d_so_rep','sale.order vs Reporte subtotal'),('d_ven_odoo','Ventas.Total vs MontosOdoo')]:
    big=m[m[c].abs()>0.01]
    print(f"\n{lab}: {len(big)} con dif>0.01 (suma abs {m[c].abs().sum().round(2)})")
    print(big[['so',c]].head(6).to_string(index=False))
m.to_csv("C_ventas_recon.csv",index=False)
# specifically discrepancy montosOdoo vs Total (the pre-existing sheet)
big=m[(m['d_ven_odoo'].abs()>0.01)].sort_values('d_ven_odoo',key=abs,ascending=False)
print("\nTop |Ventas.Total - MontosOdoo|:")
print(big[['so','ven_total','monto_odoo','ven_dcto','d_ven_odoo']].head(12).to_string(index=False))
