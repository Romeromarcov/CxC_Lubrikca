"""Un cierre ya materializado deja de ser candidato a cierre.

Pedido del usuario al revisar las bandejas (septiembre 2026): "valida que
al generar la NC en Odoo salga de ambas bandejas".

La Bandeja 2 sí las suelta -- ``descuento_pendiente_aplicar`` resta la NC
emitida, y medido en producción 20 de 21 órdenes con NC salieron (la
restante quedaba por 0,64 de redondeo).

Este reporte NO. Su condición es ``pagado / teórico >= umbral``, y emitir
una Nota de Crédito no mueve ninguna de las dos cifras: el pago sigue
igual y el teórico sale de la lista de precios. La orden quedaba listada
para siempre.

Medido antes del arreglo: 8 órdenes con la NC ya emitida seguían
apareciendo como candidatas, entre ellas S00079 -- había pagado el 116 %
de su teórico y recibido 548,45 de Nota de Crédito, y el reporte la seguía
ofreciendo para que gerencia decidiera otorgarle un diferencial.

Son dos las vías por las que este cierre se concede, y las dos cuentan: la
NC en Odoo y el descuento de sistema aprobado desde la bandeja.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from cxc.web.app import calcular_candidatos_cierre_diferencial

_REGLAS = [
    SimpleNamespace(
        tipo_diferencial="fijo_35_ves_usd",
        porcentaje_fijo=Decimal("0.35"),
        activo=True,
        vigencia_desde=None,
        vigencia_hasta=None,
    ),
    SimpleNamespace(
        tipo_diferencial="candidato_cierre_factura",
        porcentaje_fijo=Decimal("0"),
        activo=True,
        vigencia_desde=None,
        vigencia_hasta=None,
    ),
]
# Diferencial de hoy 15,65 % -> umbral 19,35 % -> hace falta 80,65 % pagado.
_TASAS = [{"timestamp": "2026-09-07", "diferencial_bcv_binance_pct": "15.65"}]


def _item(so_id: str, **kw):
    base = {
        "so_id": so_id,
        "cliente_nombre": "Cliente",
        "nacio_en_lista_usd": False,
        "ves_neta_teorica_iva": 1000.0,
        "pagado_teorico_bcv": 900.0,  # 90 %, por encima del umbral
        "total_nc_aplicada": 0.0,
        "descuento_aplicado_sistema": 0.0,
    }
    base.update(kw)
    return base


def _ids(items):
    r = calcular_candidatos_cierre_diferencial(_REGLAS, _TASAS, items, date(2026, 9, 7))
    return {c["so_id"] for c in r["candidatos"]}


def test_sin_cierre_todavia_si_es_candidata() -> None:
    assert _ids([_item("S00001")]) == {"S00001"}


def test_con_la_nc_ya_emitida_deja_de_serlo() -> None:
    """El caso de las 8: S00079 pagó el 116 % y recibió 548,45 de NC."""
    assert _ids([_item("S00079", total_nc_aplicada=548.45)]) == set()


def test_con_el_descuento_de_sistema_aprobado_tampoco() -> None:
    """La otra vía por la que gerencia concede el cierre."""
    assert _ids([_item("S00002", descuento_aplicado_sistema=120.0)]) == set()


def test_una_nc_de_centavos_no_la_saca() -> None:
    """La tolerancia evita que un redondeo esconda una orden real."""
    assert _ids([_item("S00003", total_nc_aplicada=0.04)]) == {"S00003"}


def test_no_alcanzar_el_umbral_sigue_excluyendo() -> None:
    """El filtro nuevo no reemplaza al criterio original."""
    assert _ids([_item("S00004", pagado_teorico_bcv=500.0)]) == set()


def test_una_orden_nacida_en_lista_usd_sigue_fuera() -> None:
    assert _ids([_item("S00005", nacio_en_lista_usd=True)]) == set()
