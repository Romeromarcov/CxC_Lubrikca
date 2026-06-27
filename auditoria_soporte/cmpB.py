import lib
import pandas as pd

d=lib.load(); rep=d['rep'].copy(); st=d['stock'].copy()
rep['code']=rep['product_template_id'].apply(lib.code_of)
rep['cli']=rep['partner_id'].apply(lib.norm)
rep['qty']=pd.to_numeric(rep['product_uom_qty'],errors='coerce')
st['code']=st['Producto'].apply(lib.code_of)
st['cli']=st['Contacto'].apply(lib.norm)
st['qty']=pd.to_numeric(st['Cantidad'],errors='coerce')
print("Reporte ventas: lineas=%d, qty total=%.0f, productos=%d"%(len(rep),rep['qty'].sum(),rep['code'].nunique()))
print("Stock moves: lineas=%d, qty total=%.0f, productos=%d, refs=%d"%(len(st),st['qty'].sum(),st['code'].nunique(),st['Referencia'].nunique()))
print("Stock fechas:",pd.to_datetime(st['Fecha']).min(),"->",pd.to_datetime(st['Fecha']).max())
# aggregate by client+product
sv=rep.groupby(['cli','code'])['qty'].sum().rename('vendido')
mv=st.groupby(['cli','code'])['qty'].sum().rename('movido')
j=pd.concat([sv,mv],axis=1).reset_index()
j['vendido']=j['vendido'].fillna(0); j['movido']=j['movido'].fillna(0)
j['dif']=(j['vendido']-j['movido']).round(2)
sold_no_move=j[(j['vendido']>0)&(j['movido']==0)]
move_no_sold=j[(j['movido']>0)&(j['vendido']==0)]
qtydiff=j[(j['vendido']>0)&(j['movido']>0)&(j['dif'].abs()>0.001)]
print(f"\n(cliente+producto) Vendido sin salida de inventario: {len(sold_no_move)} pares, qty {sold_no_move['vendido'].sum():.0f}")
print(f"Salida de inventario sin venta: {len(move_no_sold)} pares, qty {move_no_sold['movido'].sum():.0f}")
print(f"Diferencia de cantidad (ambos>0): {len(qtydiff)} pares")
print("\nTop vendido sin salida:")
print(sold_no_move.sort_values('vendido',ascending=False).head(10).to_string(index=False))
print("\nTop salida sin venta:")
print(move_no_sold.sort_values('movido',ascending=False).head(10).to_string(index=False))
print("\nTop diferencia cantidad:")
print(qtydiff.reindex(qtydiff['dif'].abs().sort_values(ascending=False).index).head(10).to_string(index=False))
# totals by product (ignoring client) sanity
tot=pd.concat([rep.groupby('code')['qty'].sum().rename('vendido'),st.groupby('code')['qty'].sum().rename('movido')],axis=1).fillna(0)
tot['dif']=(tot['vendido']-tot['movido'])
print("\nA nivel producto (sin cliente): productos con dif !=0:",(tot['dif'].abs()>0.001).sum(),"de",len(tot))
j.to_csv("B_inv_recon.csv",index=False)
sold_no_move.to_csv("B_vendido_sin_salida.csv",index=False)
move_no_sold.to_csv("B_salida_sin_venta.csv",index=False)
