"""Aceptar una discrepancia la silencia solo mientras no cambie.

Dos pedidos del usuario al revisar la página de Auditoría (septiembre
2026):

  · Llamarlas **discrepancias**, no anomalías. Una anomalía suena a error
    del sistema; una discrepancia es lo que de verdad se muestra: dos
    fuentes que no coinciden, y alguien tiene que decidir cuál vale.
  · Al aceptarlas, que salgan de su bandeja y pasen a aceptadas, con
    trazabilidad de qué se aceptó y quién lo aceptó.

Y una idea suya que resultó ser la pieza clave: **una huella**. Sin ella,
aceptar una discrepancia la taparía PARA SIEMPRE, aunque después cambiaran
los datos que la originaron -- si se acepta una diferencia de 264,58 y
mañana esa orden pasa a diferir en 3.000, la aceptación vieja la seguiría
escondiendo. Es exactamente el "caso oculto" que no se quiere.

Con la huella el detector compara: si coincide, sigue aceptada; si cambió,
vuelve a aparecer como discrepancia nueva y hay que decidirla de nuevo.
Mismo patrón que ``VentasTeorico.lineas_fingerprint`` para los teóricos.
"""

from __future__ import annotations

from decimal import Decimal

from cxc.web.app import huella_discrepancia


def test_los_mismos_valores_dan_la_misma_huella() -> None:
    a = huella_discrepancia("saldo_deudor", "S00458", {"motor": 264.58, "odoo": 0.0})
    b = huella_discrepancia("saldo_deudor", "S00458", {"odoo": 0.0, "motor": 264.58})
    assert a == b, "el orden de las claves no debe cambiar la huella"


def test_un_cambio_real_reabre_la_discrepancia() -> None:
    """El caso que motivó la huella."""
    aceptada = huella_discrepancia("saldo_deudor", "S00458", {"motor": 264.58})
    hoy = huella_discrepancia("saldo_deudor", "S00458", {"motor": 3000.00})
    assert aceptada != hoy


def test_un_redondeo_no_la_reabre() -> None:
    """Sin el redondeo a dos decimales, la última cifra reabriría una
    discrepancia que en la práctica es la misma."""
    a = huella_discrepancia("saldo_deudor", "S00458", {"motor": 264.58})
    b = huella_discrepancia("saldo_deudor", "S00458", {"motor": 264.5849})
    assert a == b


def test_distingue_el_tipo_de_discrepancia() -> None:
    """La misma cifra en dos detectores distintos son dos discrepancias."""
    a = huella_discrepancia("saldo_deudor", "S00458", {"monto": 100.0})
    b = huella_discrepancia("tasa_implausible", "S00458", {"monto": 100.0})
    assert a != b


def test_distingue_la_orden() -> None:
    a = huella_discrepancia("saldo_deudor", "S00458", {"monto": 100.0})
    b = huella_discrepancia("saldo_deudor", "S00214", {"monto": 100.0})
    assert a != b


def test_decimal_y_float_dan_la_misma_huella() -> None:
    """El detector puede entregar cualquiera de los dos; la aceptación no
    debe depender de eso."""
    a = huella_discrepancia("x", "S1", {"m": Decimal("264.58")})
    b = huella_discrepancia("x", "S1", {"m": 264.58})
    assert a == b


def test_sin_valores_sigue_dando_una_huella_estable() -> None:
    """Un detector que todavía no reporte valores no debe romper la
    aceptación -- simplemente su huella depende solo de tipo y orden."""
    a = huella_discrepancia("x", "S1", {})
    assert a == huella_discrepancia("x", "S1", {})
    assert a != huella_discrepancia("x", "S2", {})
