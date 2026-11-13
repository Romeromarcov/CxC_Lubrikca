"""Un parser por formato de estado de cuenta. Cada uno devuelve un ``EstadoCuenta`` canónico.

Los parsers reciben el contenido crudo (``bytes``), no una ruta: el archivo ya quedó
guardado tal cual en ``bancos_archivos`` antes de parsearlo, y se parsea desde ahí.

Agregar un banco = registrar una función en ``PARSERS``. Un conector por API (fase 4)
devolverá el mismo ``EstadoCuenta``, así que todo lo que viene después no cambia.
"""

from __future__ import annotations

import io
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

from .modelos import CERO, EstadoCuenta, MovimientoBancario
from .numeros import limpiar_ref, parse_fecha, parse_monto

Parser = Callable[[bytes, str], EstadoCuenta]


def _leer_hoja(contenido: bytes, nombre: str) -> tuple[list[list[Any]], int]:
    if nombre.lower().endswith(".xls"):
        import xlrd

        wb = xlrd.open_workbook(file_contents=contenido)
        s = wb.sheet_by_index(0)
        return [s.row_values(r) for r in range(s.nrows)], int(wb.datemode)
    import openpyxl

    ws = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True, read_only=True).worksheets[0]
    return [list(r) for r in ws.iter_rows(values_only=True)], 0


def _idx(header: list[Any], *nombres: str) -> int:
    norm = [str(h).strip().lower() for h in header]
    for n in nombres:
        if n.lower() in norm:
            return norm.index(n.lower())
    raise ValueError(f"No encuentro la columna {nombres} en el encabezado {header}")


def _vacia(fila: list[Any]) -> bool:
    return all(c in (None, "") for c in fila)


def _mov(
    banco: str,
    cuenta: str,
    moneda: str,
    fecha: date,
    ref: str,
    desc: Any,
    deb: Decimal | None,
    cre: Decimal | None,
    saldo: Decimal | None,
    orden: int,
    codigo: str = "",
    correlativo: str = "",
) -> MovimientoBancario:
    deb, cre = deb or CERO, cre or CERO
    if deb < 0 or cre < 0:
        raise ValueError(f"fila {orden}: débito/crédito negativo ({deb}/{cre})")
    return MovimientoBancario(
        banco=banco,
        cuenta=cuenta,
        moneda=moneda,
        fecha=fecha,
        referencia=ref,
        descripcion=re.sub(r"\s+", " ", str(desc or "")).strip(),
        debito=deb,
        credito=cre,
        saldo=saldo,
        orden=orden,
        codigo=codigo,
        correlativo=correlativo,
    )


def _derivar_saldo_inicial(est: EstadoCuenta) -> None:
    if est.saldo_inicial is not None or not est.movimientos:
        return
    m = est.movimientos[0]
    if m.saldo is not None:
        est.saldo_inicial = m.saldo - m.neto
        est.saldo_inicial_derivado = True


_FOOTER = re.compile(r"(?i)\s*cr[eé]ditos\s+total")


def _parse_deb_cred(
    contenido: bytes, nombre: str, banco: str, cuenta: str, moneda: str, *, con_codigo: bool = False
) -> EstadoCuenta:
    """Tabla con columnas Fecha / Referencia / Concepto / Débito / Crédito / Saldo."""
    filas, dm = _leer_hoja(contenido, nombre)
    try:
        h = next(
            i for i, f in enumerate(filas) if any(str(c).strip().lower() == "fecha" for c in f)
        )
    except StopIteration:
        raise ValueError(
            f"{nombre}: no encuentro la fila de encabezado (columna 'Fecha')"
        ) from None
    header = filas[h]
    i_f, i_r = _idx(header, "fecha"), _idx(header, "referencia")
    i_d = _idx(header, "concepto", "descripción", "descripcion")
    i_deb, i_cre = _idx(header, "débito", "debito"), _idx(header, "crédito", "credito")
    i_s = _idx(header, "saldo", "balance")
    i_c = _idx(header, "código", "codigo") if con_codigo else None

    est = EstadoCuenta(banco, cuenta, moneda, nombre)
    orden = 0
    for fila in filas[h + 1 :]:
        if _vacia(fila):
            continue
        texto = " ".join("" if c is None else str(c) for c in fila)
        if _FOOTER.match(str(fila[0] or "")):  # footer Bancamiga
            m_sf = re.search(r"Saldo Final:\s*([\d.,]+)", texto, re.I)
            est.avisos.append(f"footer del banco: {texto.strip()}")
            if m_sf:
                est.saldo_final = parse_monto(m_sf.group(1))
            continue
        concepto = str(fila[i_d] or "").strip().upper()
        saldo = parse_monto(fila[i_s])
        if concepto.startswith("SALDO INICIAL"):  # Venezuela: fila explícita
            if not est.movimientos:
                est.saldo_inicial = saldo
            else:
                # Varios estados pegados en un archivo: la fila del corte siguiente no es la
                # apertura del archivo. Si no coincide con el saldo previo, falta algo entre
                # ambos cortes (la validación fila a fila lo marca en el próximo movimiento).
                previo = est.movimientos[-1].saldo
                if saldo is not None and previo is not None and saldo != previo:
                    est.avisos.append(
                        f"SALDO INICIAL {saldo} a mitad de archivo no coincide con el "
                        f"saldo previo {previo}"
                    )
            continue
        if fila[i_f] in (None, ""):  # relleno con ceros al final del xlsx
            continue
        orden += 1
        est.movimientos.append(
            _mov(
                banco,
                cuenta,
                moneda,
                parse_fecha(fila[i_f], dm),
                limpiar_ref(fila[i_r]),
                fila[i_d],
                parse_monto(fila[i_deb]),
                parse_monto(fila[i_cre]),
                saldo,
                orden,
                str(fila[i_c] or "").strip() if i_c is not None else "",
            )
        )
    _derivar_saldo_inicial(est)
    return est


def parse_bdt(contenido: bytes, nombre: str) -> EstadoCuenta:
    return _parse_deb_cred(contenido, nombre, "BDT", "BDT", "VES", con_codigo=True)


def parse_bancamiga(contenido: bytes, nombre: str) -> EstadoCuenta:
    return _parse_deb_cred(contenido, nombre, "BANCAMIGA", "BANCAMIGA", "VES")


def parse_venezuela(contenido: bytes, nombre: str) -> EstadoCuenta:
    return _parse_deb_cred(contenido, nombre, "VENEZUELA", "VENEZUELA", "VES")


def parse_banesco(contenido: bytes, nombre: str) -> EstadoCuenta:
    return _parse_deb_cred(contenido, nombre, "BANESCO", "BANESCO", "VES")


def parse_provincial(contenido: bytes, nombre: str) -> EstadoCuenta:
    """Importe firmado, saldo como texto ``'  58,509.32'`` y orden DESCENDENTE.

    La columna 'Referencia' es un correlativo del reporte, no una referencia bancaria:
    va a ``correlativo`` (sirve para ordenar dentro del día) y la referencia queda vacía.
    """
    filas, dm = _leer_hoja(contenido, nombre)
    header = filas[0]
    i_f, i_r = _idx(header, "fecha"), _idx(header, "referencia")
    i_d = _idx(header, "descripción", "descripcion")
    i_i, i_s = _idx(header, "importe"), _idx(header, "saldo")
    est = EstadoCuenta("PROVINCIAL", "PROVINCIAL", "VES", nombre)
    crudo: list[tuple[date, int, Any, Decimal, Decimal | None]] = []
    for fila in filas[1:]:
        if _vacia(fila) or fila[i_f] in (None, ""):
            continue
        imp = parse_monto(fila[i_i])
        if imp is None:
            raise ValueError(f"{nombre}: fila sin importe: {fila}")
        crudo.append(
            (
                parse_fecha(fila[i_f], dm),
                int(float(fila[i_r])),
                fila[i_d],
                imp,
                parse_monto(fila[i_s]),
            )
        )
    crudo.sort(key=lambda t: (t[0], t[1]))  # cronológico ascendente
    for orden, (f, corr, desc, imp, saldo) in enumerate(crudo, 1):
        est.movimientos.append(
            _mov(
                "PROVINCIAL",
                "PROVINCIAL",
                "VES",
                f,
                "",
                desc,
                -imp if imp < 0 else CERO,
                imp if imp > 0 else CERO,
                saldo,
                orden,
                correlativo=str(corr),
            )
        )
    _derivar_saldo_inicial(est)
    return est


PARSERS: dict[str, Parser] = {
    "BDT": parse_bdt,
    "BANCAMIGA": parse_bancamiga,
    "VENEZUELA": parse_venezuela,
    "BANESCO": parse_banesco,
    "PROVINCIAL": parse_provincial,
}


def parsear(contenido: bytes, nombre: str, banco: str) -> EstadoCuenta:
    clave = banco.strip().upper()
    if clave not in PARSERS:
        raise KeyError(f"Banco sin parser: {banco}. Disponibles: {sorted(PARSERS)}")
    return PARSERS[clave](contenido, nombre)
