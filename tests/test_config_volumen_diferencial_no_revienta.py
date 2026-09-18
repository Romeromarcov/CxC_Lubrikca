"""GET /api/config/descuentos-volumen y /api/config/descuentos-diferencial-cambiario
dejan de reventar con AttributeError.

La migración de unificación de nombres (`litros_minimo` -> `min_unidades` en
``DescuentoVolumen``, `nombre` fusionado con `descripcion` en
``DescuentoDiferencialCambiario``) ya se había reflejado en
``get_todas_reglas_descuento`` -- de ahí salió ``engine/discounts.unidad_de_volumen``,
que existe justamente porque ``r.litros_minimo`` es un ``AttributeError``. Pero estos
dos endpoints hermanos, más viejos, nunca se actualizaron: accedían al atributo
crudo (`r.litros_minimo`, `r.nombre`) sin ningún ``getattr``, así que **cada
llamada** -- no solo una regla con datos raros -- terminaba en el ``except
Exception`` del endpoint devolviendo 500. Confirmado en los logs de producción
(18-sep-2026): las dos rutas tirando la misma traza en cada ciclo.

Estos endpoints alimentan las tablas de solo lectura "Reglas Vigentes" de los
paneles de Volumen y Diferencial Cambiario en la pantalla de Reglas de
Descuento -- con el 500, esas tablas se quedaban en "Cargando..." para
siempre.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cxc.models import DescuentoDiferencialCambiario, DescuentoVolumen
from cxc.web.app import app

client = TestClient(app)


def test_descuentos_volumen_no_revienta_con_una_regla_real() -> None:
    """Antes de esta pieza, esto daba 500 SIEMPRE -- ``r.litros_minimo`` ya no
    existe en el dataclass, así que no hacía falta ningún dato raro para
    reproducirlo."""
    regla = DescuentoVolumen(
        regla_id="VOL1",
        marca="GLOBAL OIL",
        categoria="CAJA",
        min_unidades=Decimal("10"),
        unidad_medida="CAJAS",
        porcentaje=Decimal("0.05"),
    )
    mock_repo = MagicMock()
    mock_repo.descuentos_volumen.return_value = [regla]

    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        res = client.get("/api/config/descuentos-volumen")

    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["min_unidades"] == 10.0
    assert data[0]["unidad_medida"] == "CAJAS"
    # Se conserva por compatibilidad, con el mismo valor que min_unidades --
    # ya no es el campo borrado.
    assert data[0]["litros_minimo"] == 10.0


def test_descuentos_volumen_con_unidad_medida_vacia_no_revienta() -> None:
    """El caso que ``unidad_de_volumen`` documenta: la columna es NOT NULL
    pero admite cadena vacía. Antes esto además adivinaba mal (pisaba con
    ``float(r.litros_minimo)``, que ya ni existe)."""
    regla = DescuentoVolumen(regla_id="VOL2", unidad_medida="")
    mock_repo = MagicMock()
    mock_repo.descuentos_volumen.return_value = [regla]

    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        res = client.get("/api/config/descuentos-volumen")

    assert res.status_code == 200
    data = res.json()
    assert data[0]["unidad_medida"] == "UNIDADES"


def test_descuentos_diferencial_cambiario_no_revienta() -> None:
    """Antes de esta pieza, esto daba 500 SIEMPRE -- ``r.nombre`` ya no
    existe en el dataclass (se fusionó con ``descripcion``)."""
    regla = DescuentoDiferencialCambiario(
        regla_id="DIF1",
        descripcion="35% Fijo VES a USD",
        porcentaje_fijo=Decimal("0.35"),
    )
    mock_repo = MagicMock()
    mock_repo.descuentos_diferencial_cambiario.return_value = [regla]

    with patch("cxc.web.app.get_repo", return_value=mock_repo):
        res = client.get("/api/config/descuentos-diferencial-cambiario")

    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    # ``nombre`` sale de ``descripcion`` -- es el mismo dato, fusionado.
    assert data[0]["nombre"] == "35% Fijo VES a USD"
    assert data[0]["descripcion"] == "35% Fijo VES a USD"
