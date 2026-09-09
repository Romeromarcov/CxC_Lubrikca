"""Contrasta ``TasasHistoricasAuditoria`` contra las series oficiales del BCV.

Uso:
  python scripts/verificar_tasas_contra_bcv.py                 # solo reporta
  python scripts/verificar_tasas_contra_bcv.py --aplicar       # corrige
  python scripts/verificar_tasas_contra_bcv.py --desde 2026-02-01
  python scripts/verificar_tasas_contra_bcv.py --database-url "postgresql://..."

POR QUE EXISTE

En septiembre de 2026 se descubrió que el scraper guardaba bajo la fecha
de hoy la tasa que el BCV publica con FECHA VALOR de mañana. Sus archivos
lo dicen en la cabecera de cada hoja:

    Fecha Operación: 08/09/2026     Fecha Valor: 09/09/2026

Eso corrió 24 días de la serie y produjo un descuadre contra Odoo. El
scraper ya está corregido y el histórico realineado, pero nada verificaba
que la alineación se mantuviera: si el BCV cambia el formato de sus
archivos, o el scraper se cae una semana, nos enteraríamos por un
descuadre en el balance de comprobación y no por una alerta.

Este script cierra ese flanco. Bajado y comparado, dice cuántos días
coinciden y cuáles no; con ``--aplicar`` los corrige. Es idempotente:
correrlo dos veces seguidas deja "0 días que NO" la segunda.

CADA CUANTO CORRERLO

Va como cron y NO como tarea del proceso web, a propósito. Meterle al
servidor una dependencia de un sitio externo -- que además necesita
``openssl`` para el certificado intermedio -- significa que un BCV lento o
un cambio de formato de sus archivos degrada la aplicación entera. Acá
como mucho falla el cron y el balance de comprobación sigue avisando por
su lado.

En Railway, "New > Cron Job" sobre este mismo repo, una vez al día después
de las 22:00 (cuando el scraper ya cerró el día):

    PYTHONPATH=src python scripts/verificar_tasas_contra_bcv.py --aplicar

Devuelve 0 si todo estaba alineado o si corrigió con ``--aplicar``, y 1 si
encontró diferencias y NO se le pasó ``--aplicar`` -- así, corriéndolo sin
esa bandera, sirve de chequeo que falla ruidoso.

LA FUENTE

https://www.bcv.org.ve/estadisticas/tipo-cambio-de-referencia-smc publica
un .xls por período con una hoja por día. De cada hoja se leen la fecha
valor y la cotización de venta (ASK) en Bs./M.E. de USD y EUR, que son las
que Odoo estampa en los asientos.

EL CERTIFICADO

El servidor del BCV no envía el certificado intermedio, así que la
verificación TLS falla de entrada. El certificado es legítimo (Sectigo,
``*.bcv.org.ve``); el script baja el intermedio desde la extensión AIA del
propio certificado del servidor y lo suma al almacén de confianza del
sistema. NO se desactiva la verificación: si el certificado fuera inválido
por cualquier otra razón, la descarga falla como corresponde.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from decimal import Decimal
from pathlib import Path

BASE = "https://www.bcv.org.ve"
PAGINA_SMC = f"{BASE}/estadisticas/tipo-cambio-de-referencia-smc"

# Cuánto puede diferir un día antes de contarlo como divergente. El BCV
# publica con cuatro decimales y nosotros guardamos texto; una diferencia
# por debajo de esto es representación, no desacuerdo.
TOLERANCIA = Decimal("0.0001")


def _contexto_tls_con_intermedio() -> ssl.SSLContext:
    """Confianza del sistema MÁS el intermedio que el BCV no envía."""
    ctx = ssl.create_default_context()
    pem = _descargar_intermedio()
    if pem:
        try:
            ctx.load_verify_locations(cafile=pem)
        except ssl.SSLError as e:
            print(f"  aviso: no se pudo cargar el intermedio ({e})", file=sys.stderr)
    return ctx


def _descargar_intermedio() -> str | None:
    """El intermedio de Sectigo, sacado de la AIA del cert del servidor."""
    try:
        salida = subprocess.run(
            ["openssl", "s_client", "-connect", "www.bcv.org.ve:443",
             "-servername", "www.bcv.org.ve"],
            input=b"", capture_output=True, timeout=60,
        ).stdout.decode("utf-8", "replace")
        cert = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", salida, re.S)
        if not cert:
            return None
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
            f.write(cert.group(0))
            leaf = f.name
        aia = subprocess.run(
            ["openssl", "x509", "-in", leaf, "-noout", "-text"],
            capture_output=True, timeout=60,
        ).stdout.decode("utf-8", "replace")
        url = re.search(r"CA Issuers - URI:(\S+)", aia)
        if not url:
            return None
        with urllib.request.urlopen(url.group(1), timeout=60) as r:
            der = r.read()
        with tempfile.NamedTemporaryFile("wb", suffix=".der", delete=False) as f:
            f.write(der)
            ruta_der = f.name
        destino = str(Path(tempfile.gettempdir()) / "bcv_intermedio.pem")
        subprocess.run(
            ["openssl", "x509", "-inform", "DER", "-in", ruta_der, "-out", destino],
            capture_output=True, timeout=60, check=True,
        )
        return destino
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  aviso: no se pudo obtener el intermedio del BCV ({e})", file=sys.stderr)
        return None


def _bajar(url: str, ctx: ssl.SSLContext) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "CxC-Lubrikca/1.0"})
    with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
        datos: bytes = r.read()
        return datos


def _iso(texto: str) -> str | None:
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", texto or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def serie_oficial(anio_corto: str, ctx: ssl.SSLContext) -> dict[str, tuple[Decimal, Decimal]]:
    """``{fecha valor: (usd, eur)}`` de todos los .xls del año indicado.

    Se indexa por FECHA VALOR, no por fecha de operación: la tasa que el
    BCV calcula el día D rige el D+1, y así es como la guarda el sistema
    (y como la estampa Odoo en los asientos).
    """
    import xlrd  # solo hace falta acá; queda fuera de las dependencias del server

    html = _bajar(PAGINA_SMC, ctx).decode("utf-8", "replace")
    archivos = sorted(set(re.findall(rf'href="([^"]+{anio_corto}_smc\.xls)"', html)))
    if not archivos:
        raise SystemExit(f"El BCV no lista archivos para el año {anio_corto}. ¿Cambió la página?")
    out: dict[str, tuple[Decimal, Decimal]] = {}
    for href in archivos:
        url = href if href.startswith("http") else f"{BASE}{href}"
        print(f"  bajando {url.rsplit('/', 1)[-1]}")
        with tempfile.NamedTemporaryFile("wb", suffix=".xls", delete=False) as f:
            f.write(_bajar(url, ctx))
            ruta = f.name
        for hoja in xlrd.open_workbook(ruta).sheets():
            fecha_valor = usd = eur = None
            for r in range(min(30, hoja.nrows)):
                celdas = [str(hoja.cell_value(r, c)) for c in range(hoja.ncols)]
                for celda in celdas:
                    if "Valor" in celda and "Fecha" in celda:
                        fecha_valor = _iso(celda.split(":")[-1])
                # La cotización de venta (ASK) en Bs./M.E. es la última columna.
                if len(celdas) > 6 and celdas[1].strip() in ("USD", "EUR"):
                    try:
                        valor = Decimal(celdas[6])
                    except (ArithmeticError, ValueError):
                        continue
                    if celdas[1].strip() == "USD":
                        usd = valor
                    else:
                        eur = valor
            if fecha_valor and usd and usd > 0:
                out[fecha_valor] = (usd, eur or Decimal("0"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aplicar", action="store_true", help="corrige; sin esto solo reporta")
    ap.add_argument("--desde", default="2026-02-01", help="primera fecha a revisar")
    ap.add_argument("--anio", default="26", help="sufijo de año de los archivos del BCV")
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = ap.parse_args()
    if not args.database_url:
        raise SystemExit("Falta --database-url o la variable DATABASE_URL.")

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from cxc.db.postgres_repository import PostgresRepository

    print("Bajando las series oficiales del BCV...")
    oficial = serie_oficial(args.anio, _contexto_tls_con_intermedio())
    print(f"  {len(oficial)} días oficiales, {min(oficial)} .. {max(oficial)}\n")

    repo = PostgresRepository.from_url(args.database_url)
    nuestras = {r["fecha"]: r for r in repo.all_tasas_historicas_auditoria()}

    def rige(f: str) -> tuple[Decimal, Decimal] | None:
        """La del día, o la última publicada antes -- la tasa del BCV rige
        hasta que se publica la siguiente (fines de semana y feriados)."""
        if f in oficial:
            return oficial[f]
        d = dt.date.fromisoformat(f)
        for atras in range(1, 8):
            previo = (d - dt.timedelta(days=atras)).isoformat()
            if previo in oficial:
                return oficial[previo]
        return None

    desde = dt.date.fromisoformat(args.desde)
    hasta = max(dt.date.fromisoformat(max(oficial)), dt.date.fromisoformat(max(nuestras)))
    coinciden, cambios = 0, []
    for i in range((hasta - desde).days + 1):
        f = (desde + dt.timedelta(days=i)).isoformat()
        esperado = rige(f)
        if not esperado:
            continue
        usd_of, eur_of = esperado
        fila = nuestras.get(f)
        if fila is None:
            cambios.append((f, "falta", Decimal("0"), usd_of, Decimal("0"), eur_of))
            continue
        usd_n = Decimal(str(fila.get("tasa_bcv_usd") or "0"))
        eur_n = Decimal(str(fila.get("tasa_bcv_euro") or "0"))
        mal_usd = usd_n <= 0 or abs(usd_n - usd_of) / usd_of > TOLERANCIA
        mal_eur = bool(eur_of) and (eur_n <= 0 or abs(eur_n - eur_of) / eur_of > TOLERANCIA)
        if mal_usd or mal_eur:
            cambios.append((f, "difiere", usd_n, usd_of, eur_n, eur_of))
        else:
            coinciden += 1

    print(f"días que coinciden con el BCV: {coinciden}")
    print(f"días que NO: {len(cambios)}")
    for f, tipo, un, uo, en, eo in cambios[:40]:
        print(
            f"   {f} {tipo:8} usd {un:>10,.4f} -> {uo:>10,.4f}"
            f"   eur {en:>10,.4f} -> {eo:>10,.4f}"
        )
    if len(cambios) > 40:
        print(f"   ... y {len(cambios) - 40} más")

    if not cambios:
        print("\nTodo alineado.")
        return 0
    if not args.aplicar:
        print("\n[SOLO REPORTE] Nada escrito. Volvé a correr con --aplicar para corregir.")
        return 1

    for f, _tipo, _un, uo, _en, eo in cambios:
        previa = nuestras.get(f, {})
        repo.upsert_tasa_historica_auditoria({
            "fecha": f,
            "tasa_bcv_usd": str(uo),
            "tasa_bcv_euro": str(eo) if eo else str(previa.get("tasa_bcv_euro") or ""),
            "tasa_binance_promedio_diario": str(previa.get("tasa_binance_promedio_diario") or ""),
            "diferencial_bcv_binance_pct": str(previa.get("diferencial_bcv_binance_pct") or ""),
            "fuente": "BCV oficial (tipo-cambio-de-referencia-smc, por fecha valor)",
            "notas": "Alineada con la serie oficial del BCV. "
                     + str(previa.get("notas") or "")[:120],
        })
    print(f"\nCorregidas {len(cambios)} filas.")
    print("Ojo: el caché de tasas de la app tarda hasta 5 minutos en verlo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
