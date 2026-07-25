import urllib.request, re, ssl, sys
from decimal import Decimal
sys.stdout.reconfigure(encoding='utf-8')

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

req = urllib.request.Request('https://www.bcv.org.ve/', headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
try:
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        html = resp.read().decode('utf-8', errors='ignore')
        
        m_usd = re.search(r'id=["\']dolar["\'].*?<strong[^>]*>\s*([\d\.,]+)\s*</strong>', html, flags=re.DOTALL | re.IGNORECASE)
        m_eur = re.search(r'id=["\']euro["\'].*?<strong[^>]*>\s*([\d\.,]+)\s*</strong>', html, flags=re.DOTALL | re.IGNORECASE)
        
        if m_usd and m_eur:
            u_str = m_usd.group(1).replace('.', '').replace(',', '.')
            e_str = m_eur.group(1).replace('.', '').replace(',', '.')
            print('Tasa BCV USD actual:', Decimal(u_str))
            print('Tasa BCV Euro actual:', Decimal(e_str))
except Exception as e:
    print('Error:', e)
