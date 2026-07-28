import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, timedelta

sys.stdout.reconfigure(encoding='utf-8')

path = r'C:\Users\PC\Proyectos\CxC_Lubrikca\Info_Odoo\Estrategias Global-Lubrikca. (1).xlsx'
excel_base = date(1899, 12, 30)

shared_strings = []
with zipfile.ZipFile(path) as z:
    if 'xl/sharedStrings.xml' in z.namelist():
        tree = ET.parse(z.open('xl/sharedStrings.xml'))
        for elem in tree.iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t'):
            shared_strings.append(elem.text)

def parse_val(v, t):
    if not v: return ''
    if t == 's': return shared_strings[int(v)]
    try:
        f = float(v)
        if f > 40000 and f < 60000:
            d = excel_base + timedelta(days=int(f))
            return d.strftime('%Y-%m-%d')
        return f
    except:
        return v

with zipfile.ZipFile(path) as z:
    tree = ET.parse(z.open('xl/worksheets/sheet1.xml'))
    root = tree.getroot()
    ns = {'ns': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

    rows = []
    for row in root.findall('.//ns:row', ns):
        r_num = int(row.get('r'))
        row_dict = {}
        for cell in row.findall('./ns:c', ns):
            col_letter = ''.join(c for c in cell.get('r') if c.isalpha())
            cell_type = cell.get('t')
            val_elem = cell.find('./ns:v', ns)
            val = val_elem.text if val_elem is not None else ''
            row_dict[col_letter] = parse_val(val, cell_type)
        rows.append((r_num, row_dict))

print('=== REGLAS Y ESTRATEGIAS EXTRAÍDAS DE EXCEL ===\n')
current_brand = 'GLOBAL OIL'
current_section = ''

for r_num, d in rows:
    col_a = str(d.get('A', '')).strip()
    col_b = d.get('B', '')
    col_c = str(d.get('C', '')).strip()
    col_d = d.get('D', '')
    col_e = d.get('E', '')

    if col_a in ['GLOBAL OIL', 'SINOCO']:
        current_brand = col_a
        print(f'\n================ MARCA: {current_brand} ================')
        continue

    if col_a in ['CLIENTE NUEVO', 'RECOMPRA', 'INCENTIVO PAGO INMEDIATO', 'VOLUMEN', 'FIDELIZACION', 'PRECIO ESPECIAL POR VOLUMEN']:
        current_section = col_a
        print(f'\n--- Categoria: {current_section} ---')
        continue

    if col_a or col_b or col_c or col_d or col_e:
        print(f'Fila {r_num:02d} | Regla: {col_a} | Beneficio: {col_b} | Condición: {col_c} | Inicio: {col_d} | Fin/Estado: {col_e}')
