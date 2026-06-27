import lib
import pandas as pd

d=lib.load(); ven=d['ventas'].copy(); am=d['am'].copy()
ven=ven[ven['Referencia de la orden'].notna()].copy()
ven['dif%']=pd.to_numeric(ven['Descuento x Diferencial %'],errors='coerce')
ven['pp%']=pd.to_numeric(ven['Dcto Pronto Pago %'],errors='coerce')
ven['mon']=ven['Moneda de pago'].astype(str)
maxd={'USD':0.35,'VES':0.15,'MIXTO':0.15}
def excede(r):
    m=maxd.get(r['mon'],None)
    if m is None or pd.isna(r['dif%']): return False
    return r['dif%']>m+1e-9
ven['excede_max']=ven.apply(excede,axis=1)
print("=== DESCUENTO DIFERENCIAL vs LIMITES (USD 0.35 / VES,MIXTO 0.15) ===")
print("Ordenes con descuento diferencial > máximo permitido:",ven['excede_max'].sum())
ex=ven[ven['excede_max']]
print(ex[['Referencia de la orden','mon','dif%','Total','Total C/Dcto','Validacion Descuento Diferencial']].head(15).to_string(index=False))
# Validacion column distribution
print("\nValidacion Descuento Diferencial (col existente):",ven['Validacion Descuento Diferencial'].value_counts(dropna=False).to_dict())
print("Validación por Diferencial:",ven['Validación por Diferencial'].value_counts(dropna=False).to_dict())
# Cross: does Total C/Dcto = Total*(1-dif%) ? check discount math
ven['esperado_dcto']=(ven['Total']*(1-ven['dif%'].fillna(0))).round(2)
ven['td']=pd.to_numeric(ven['Total C/Dcto'],errors='coerce')
ven['dmath']=(ven['td']-ven['esperado_dcto']).round(2)
badmath=ven[ven['dmath'].abs()>0.02]
print("\nOrdenes donde Total C/Dcto != Total*(1-dif%) (dif>0.02):",len(badmath))
print(badmath[['Referencia de la orden','mon','Total','dif%','pp%','esperado_dcto','td','dmath']].head(12).to_string(index=False))
# discount applied but mon = Sin Pago
sinpago_dcto=ven[(ven['mon']=='Sin Pago')&(ven['dif%'].fillna(0)>0)]
print("\nDescuento diferencial aplicado con 'Sin Pago':",len(sinpago_dcto))
# NEGATIVE / credit notes in account.move
am['tot']=pd.to_numeric(am['Total en moneda firmado'],errors='coerce')
print("\n=== account.move ===")
print("Estado en pago:",am['Estado en pago'].value_counts().to_dict())
print("Facturas con Total NEGATIVO (posible NC):",(am['tot']<0).sum(),"suma",am.loc[am['tot']<0,'tot'].sum().round(2))
print("Revertido:",(am['Estado en pago']=='Revertido').sum())
print(am[am['tot']<0][['Número','Fecha de la factura','Nombre del contacto a mostrar en la factura','tot','Estado en pago']].head(10).to_string(index=False))
ven.to_csv("E_ventas_dctos.csv",index=False)
ex.to_csv("E_descuento_excede.csv",index=False)
badmath.to_csv("E_descuento_math.csv",index=False)
