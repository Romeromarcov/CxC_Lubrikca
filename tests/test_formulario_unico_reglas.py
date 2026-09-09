"""Un solo formulario crea las seis clases de regla.

Paso 4 del plan de unificación que aprobó el usuario (septiembre 2026).
Antes había seis formularios, uno por tabla, y cada uno ofrecía solo su
propio subconjunto de campos: no se podía armar un descuento por producto
con ventana de pago, aunque el motor lo soporta.

El inventario mostró que los 37 campos son en realidad 14 comunes más un
bloque de 3 a 7 por tipo. Este endpoint recibe el núcleo plano y despacha
a la tabla que corresponde según ``tipo_regla``.

Dos campos subieron al núcleo común a pedido del usuario:

  · la **ventana de pago**, que vivía en contado y en recompra -- "en
    recompra también tiene una ventana de pago". Cualquier regla puede
    condicionarse a que el pago haya entrado a tiempo.
  · la **frecuencia** (una sola vez por cliente / recurrente), que hoy
    existe solo en promociones como ``solo_primera_compra``. Esa
    limitación es la que hace que el motor etiquete con origen
    "primera_compra" tanto la primera compra real como las promos
    recurrentes, y por eso no se pueden distinguir en el desglose.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from cxc.web.app import app

_BASE = {
    "descripcion": "Regla de prueba",
    "marca": "GLOBAL OIL",
    "categoria": "CAJA",
    "vigencia_desde": "2026-09-01",
    "activo": True,
}


def _cliente_y_repo():
    repo = MagicMock()
    return TestClient(app), repo


@pytest.mark.parametrize(
    ("tipo", "metodo"),
    [
        ("contado", "append_descuento_pronto_pago"),
        ("volumen", "append_descuento_volumen"),
        ("recompra", "append_descuento_recompra"),
        ("producto", "append_descuento_producto"),
        ("promocion", "append_promocion_primera_compra"),
        ("diferencial", "append_descuento_diferencial_cambiario"),
        ("credito", "upsert_regla_dias_credito_volumen"),
    ],
)
def test_cada_tipo_va_a_su_tabla(tipo: str, metodo: str) -> None:
    client, repo = _cliente_y_repo()
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.post("/api/config/regla", json={**_BASE, "tipo_regla": tipo})
    assert res.status_code == 200, res.text
    assert getattr(repo, metodo).called, f"{tipo} no llegó a {metodo}"


def test_un_tipo_desconocido_se_rechaza() -> None:
    client, repo = _cliente_y_repo()
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.post("/api/config/regla", json={**_BASE, "tipo_regla": "inventado"})
    assert res.status_code == 400
    assert "inventado" in res.text


def test_sin_id_se_genera_uno() -> None:
    """Alta: el formulario deja el campo vacío."""
    client, repo = _cliente_y_repo()
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.post("/api/config/regla", json={**_BASE, "tipo_regla": "contado"})
    rid = res.json()["regla_id"]
    assert rid.startswith("CONT_") and len(rid) > 5


def test_con_id_se_respeta_para_editar() -> None:
    client, repo = _cliente_y_repo()
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.post(
            "/api/config/regla",
            json={**_BASE, "tipo_regla": "volumen", "regla_id": "VOL_SINOCO_PAILA_1"},
        )
    assert res.json()["regla_id"] == "VOL_SINOCO_PAILA_1"


def test_la_ventana_de_pago_llega_a_recompra() -> None:
    """El campo que el usuario señaló que faltaba en el plan."""
    client, repo = _cliente_y_repo()
    with patch("cxc.web.app.get_repo", return_value=repo):
        client.post(
            "/api/config/regla",
            json={
                **_BASE,
                "tipo_regla": "recompra",
                "ventana_pago_tipo": "vencimiento",
                "ventana_pago_dias": 3,
            },
        )
    regla = repo.append_descuento_recompra.call_args[0][0]
    assert regla.ventana_pago_tipo == "vencimiento"
    assert regla.ventana_pago_dias == 3


def test_la_frecuencia_llega_a_la_promocion() -> None:
    """"Una sola vez por cliente" vs recurrente."""
    client, repo = _cliente_y_repo()
    with patch("cxc.web.app.get_repo", return_value=repo):
        client.post(
            "/api/config/regla",
            json={**_BASE, "tipo_regla": "promocion", "solo_primera_compra": True},
        )
    assert repo.append_promocion_primera_compra.call_args[0][0].solo_primera_compra is True


def test_la_exclusion_llega_a_cualquier_tipo() -> None:
    """La prohibición explícita es del núcleo común, no de una tabla."""
    client, repo = _cliente_y_repo()
    with patch("cxc.web.app.get_repo", return_value=repo):
        client.post(
            "/api/config/regla",
            json={**_BASE, "tipo_regla": "volumen", "listas_excluidas": "LISTAS_USD"},
        )
    assert repo.append_descuento_volumen.call_args[0][0].listas_excluidas == "LISTAS_USD"


def test_una_fecha_invalida_no_revienta() -> None:
    """El formulario manda ISO, pero el endpoint no confía en eso."""
    client, repo = _cliente_y_repo()
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.post(
            "/api/config/regla",
            json={**_BASE, "tipo_regla": "contado", "vigencia_desde": "no-es-fecha"},
        )
    assert res.status_code == 200


def test_el_tramo_en_usd_se_rechaza_en_vez_de_comparar_dolares_contra_cajas() -> None:
    """Paso 0 del análisis de tramos en USD.

    El motor lee ``unidad_medida`` en un solo lugar y solo pregunta si dice
    LITROS; todo lo demás cae a la rama que compara contra un conteo de
    cajas. Con la unidad en USD y un tramo cargado, comparaba DÓLARES
    contra CAJAS en silencio. Hoy no muerde porque las 10 reglas con unidad
    USD tienen el tramo en 0-999999, pero el formulario único permite
    combinarlos.
    """
    client, repo = _cliente_y_repo()
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.post(
            "/api/config/regla",
            json={
                **_BASE,
                "tipo_regla": "volumen",
                "unidad_medida": "USD",
                "min_unidades": 2000,
                "max_unidades": 5000,
            },
        )
    assert res.status_code == 400
    assert "USD" in res.text
    assert not repo.append_descuento_volumen.called


def test_la_unidad_usd_sin_tramo_sigue_permitida() -> None:
    """Las 7 reglas de contado y las 3 de diferencial la usan así."""
    client, repo = _cliente_y_repo()
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = client.post(
            "/api/config/regla",
            json={**_BASE, "tipo_regla": "contado", "unidad_medida": "USD"},
        )
    assert res.status_code == 200


def test_el_tipo_calculo_del_diferencial_se_deriva() -> None:
    """Nunca se elige aparte: el motor solo lee tipo_diferencial, y un
    segundo selector para lo mismo era una trampa."""
    client, repo = _cliente_y_repo()
    with patch("cxc.web.app.get_repo", return_value=repo):
        client.post(
            "/api/config/regla",
            json={**_BASE, "tipo_regla": "diferencial", "tipo_diferencial": "fijo_35_ves_usd"},
        )
    assert repo.append_descuento_diferencial_cambiario.call_args[0][0].tipo_calculo == "fijo"


def test_el_descuento_por_producto_no_queda_afuera() -> None:
    """Era la única de las siete familias sin rama en el formulario único.
    La tabla y el motor la soportan desde siempre; hoy no hay reglas
    cargadas en producción, y por eso el hueco pasaba desapercibido."""
    client, repo = _cliente_y_repo()
    with patch("cxc.web.app.get_repo", return_value=repo):
        client.post(
            "/api/config/regla",
            json={**_BASE, "tipo_regla": "producto", "productos": "1033,1022"},
        )
    assert repo.append_descuento_producto.call_args[0][0].productos == "1033,1022"
