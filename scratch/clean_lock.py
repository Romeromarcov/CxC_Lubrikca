import glob
import subprocess

for f in glob.glob('Info_Odoo/~*'):
    print('Encontrado:', f)
    subprocess.run(['cmd', '/c', 'del', '/f', '/a', f], shell=True)
