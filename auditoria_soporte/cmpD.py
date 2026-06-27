import lib
import pandas as pd

d=lib.load(); pls=lib.load_pricelists()
rep=d['rep'].copy(); ven=d['ventas'].copy()
rep['code']=rep['product_template_id'].apply(lib.code_of)
rep['so']=rep['Order'].apply(lib.sonum)
rep['fch']=pd.to_datetime(rep['date_order'])
rep['pu']=pd.to_numeric(rep['price_unit_usd'],errors='coerce').round(4)
# order -> moneda de pago
ven['so']=ven['Referencia de la orden'].apply(lib.sonum)
mp=ven.groupby('so')['Moneda de pago'].first()
rep['monpago']=rep['so'].map(mp)
# price lookups
feb=pls['feb23'].set_index('cod'); mar=pls['mar12'].set_index('cod'); jun=pls['jun15usd'].set_index('cod')
def usd_pay(code,fch):
    if fch>=pd.Timestamp('2026-06-15') and code in jun.index: return float(jun.at[code,'fixed']),'jun15USD'
    if fch>=pd.Timestamp('2026-03-12') and code in mar.index: return float(mar.at[code,'usd_pay']),'mar12'
    if code in feb.index: return float(feb.at[code,'usd_pay']),'feb23'
    return None,None
def ves_pay(code):
    if code in feb.index: return float(feb.at[code,'ves_pay']),'feb23'
    return None,None
rows=[]
for _,r in rep.iterrows():
    c=r['code']; mp_=str(r['monpago']); pu=r['pu']
    if mp_ in ('USD',): lista='USD'; exp,src=usd_pay(c,r['fch'])
    elif mp_ in ('VES',): lista='VES'; exp,src=ves_pay(c)
    elif mp_ in ('MIXTO',): lista='USD(mixto->Binance)'; exp,src=usd_pay(c,r['fch'])
    else: lista='INDETERMINADA'; exp,src=(None,None)
    # alt price (other list) for wrong-list check (feb era both known)
    altu,_=usd_pay(c,r['fch']); altv,_=ves_pay(c)
    flag=None
    if c is None: flag='codigo_invalido'
    elif lista=='INDETERMINADA': flag='lista_indeterminada'
    elif exp is None: flag='producto_sin_lista'
    elif pd.isna(pu): flag='precio_vacio'
    elif abs(pu-exp)<=0.01: flag='ok'
    elif pu<exp-0.01:
        flag='por_debajo'
        if altv is not None and abs(pu-altv)<=0.01 and lista.startswith('USD'): flag='lista_equivocada(cobró VES)'
    else:
        flag='por_encima'
        if altu is not None and abs(pu-altu)<=0.01 and lista=='VES': flag='lista_equivocada(cobró USD)'
    rows.append(dict(so=r['Order'],code=c,prod=str(r['product_template_id'])[:30],fch=r['fch'].date(),
                     monpago=mp_,lista=lista,pu=pu,esperado=None if exp is None else round(exp,2),src=src,
                     usd_pay=None if altu is None else round(altu,2),ves_pay=None if altv is None else round(altv,2),flag=flag))
D=pd.DataFrame(rows)
print("Lineas evaluadas:",len(D))
print("\nDistribución de flags:")
print(D['flag'].value_counts().to_string())
print("\nPor moneda de pago:",D['monpago'].value_counts().to_dict())
D.to_csv("D_precios.csv",index=False)
for f in ['por_debajo','por_encima','lista_equivocada(cobró VES)','lista_equivocada(cobró USD)','producto_sin_lista']:
    sub=D[D['flag']==f]
    if len(sub):
        print(f"\n--- {f}: {len(sub)} lineas ---")
        print(sub[['so','code','fch','monpago','pu','esperado','usd_pay','ves_pay','src']].head(8).to_string(index=False))
