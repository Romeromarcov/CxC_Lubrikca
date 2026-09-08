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


# --- El filtro genérico que cubre los siete detectores ----------------------


def test_los_seis_detectores_mudos_ahora_se_pueden_aceptar() -> None:
    """Hasta hoy solo UN detector consultaba las aceptaciones. Los otros
    seis mostraban su hallazgo para siempre."""
    from cxc.web.app import separar_discrepancias_aceptadas

    item = {"so_id": "S00133", "diferencia": 264.58, "detalle": "texto"}
    pend, acep = separar_discrepancias_aceptadas([dict(item)], "tasa_implausible", {})
    assert len(pend) == 1 and not acep
    huella = pend[0]["huella"]

    aceptadas = {pend[0]["discrepancia_id"]: {"huella": huella, "aprobado_por": "Auditor"}}
    pend2, acep2 = separar_discrepancias_aceptadas([dict(item)], "tasa_implausible", aceptadas)
    assert not pend2
    assert acep2[0]["aceptada_por"] == "Auditor"


def test_si_el_monto_cambia_la_discrepancia_reabre() -> None:
    from cxc.web.app import separar_discrepancias_aceptadas

    base = {"so_id": "S00133", "diferencia": 264.58}
    pend, _ = separar_discrepancias_aceptadas([dict(base)], "tasa_implausible", {})
    aceptadas = {pend[0]["discrepancia_id"]: {"huella": pend[0]["huella"], "aprobado_por": "A"}}

    movido = {"so_id": "S00133", "diferencia": 3000.00}
    pend2, acep2 = separar_discrepancias_aceptadas([movido], "tasa_implausible", aceptadas)
    assert len(pend2) == 1 and not acep2
    assert pend2[0]["reabierta"] is True


def test_el_texto_no_entra_en_la_huella_pero_el_numero_si() -> None:
    """Reescribir la descripción no reabre; mover el monto sí."""
    from cxc.web.app import separar_discrepancias_aceptadas

    a, _ = separar_discrepancias_aceptadas([{"so_id": "S1", "monto": 10.0, "d": "uno"}], "t", {})
    b, _ = separar_discrepancias_aceptadas([{"so_id": "S1", "monto": 10.0, "d": "otro"}], "t", {})
    assert a[0]["huella"] == b[0]["huella"]


def test_cada_detector_tiene_su_propio_espacio_de_ids() -> None:
    """La misma orden con la misma cifra en dos detectores distintos son
    dos discrepancias: aceptar una no puede tapar la otra."""
    from cxc.web.app import separar_discrepancias_aceptadas

    a, _ = separar_discrepancias_aceptadas([{"so_id": "S1", "monto": 10.0}], "tasa", {})
    b, _ = separar_discrepancias_aceptadas([{"so_id": "S1", "monto": 10.0}], "devolucion", {})
    assert a[0]["discrepancia_id"] != b[0]["discrepancia_id"]
    assert a[0]["huella"] != b[0]["huella"]
