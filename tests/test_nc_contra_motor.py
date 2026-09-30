"""NC de descuento emitidas contra el motor (``engine/nc_contra_motor``)."""

from __future__ import annotations

from decimal import Decimal

from cxc.engine.nc_contra_motor import COINCIDE, NC_MAYOR, NC_MENOR, evaluar_ncs_contra_motor
from cxc.models import BandejaFacturacion

from . import builders as b

DESCUENTO = b.producto("1082", nombre="Descuento ")
ACEITE = b.producto("900", nombre="Aceite 15W40")


def _bandeja(so="S1", base="100", descuentos="35", ncs="0"):
    return BandejaFacturacion(
        so_id=so,
        lista_aplicada="5",
        precio_base_calculado=Decimal(base),
        total_descuentos=Decimal(descuentos),
        ncs_calculadas=Decimal(ncs),
    )


def _escenario(nc_usd, *, producto_nc="1082", so_factura="S1"):
    facturas = [
        b.factura("10", so_id=so_factura),
        b.factura(
            "11",
            numero="RINV/1",
            so_id=None,
            move_type="out_refund",
            factura_origen_id="10",
            monto_sin_impuestos=nc_usd,
            monto_sin_impuestos_signed_usd=f"-{nc_usd}",
        ),
    ]
    lineas = [
        b.linea_factura("LF1", factura_id="11", producto_id=producto_nc, subtotal=nc_usd)
    ]
    return facturas, lineas, [DESCUENTO, ACEITE]


def _evaluar(nc_usd, bandeja=None, **kw):
    facturas, lineas, catalogo = _escenario(nc_usd, **kw)
    return evaluar_ncs_contra_motor(facturas, lineas, catalogo, [bandeja or _bandeja()])


def test_nc_igual_al_motor_coincide():
    (h,) = _evaluar("35.00")
    assert h.veredicto == COINCIDE
    assert h.diferencia == Decimal("0.00")


def test_nc_mayor_que_el_motor_se_marca():
    (h,) = _evaluar("73.35")
    assert h.veredicto == NC_MAYOR
    assert h.pct_base == Decimal("0.7335")


def test_nc_menor_que_el_motor_se_marca():
    (h,) = _evaluar("20.79")
    assert h.veredicto == NC_MENOR


def test_diferencia_dentro_de_la_tolerancia_coincide():
    (h,) = _evaluar("36.50", _bandeja(base="100"))  # 1,50 <= max(1, 2% de 100)
    assert h.veredicto == COINCIDE


def test_el_obsequio_del_motor_cuenta_como_descuento_esperado():
    (h,) = _evaluar("135.00", _bandeja(base="500", descuentos="35", ncs="100"))
    assert h.veredicto == COINCIDE


def test_una_nc_de_producto_no_se_compara():
    assert _evaluar("100.00", producto_nc="900") == []


def test_factura_consolidada_no_se_atribuye_a_una_orden():
    assert _evaluar("35.00", so_factura="S1, S2") == []


def test_nc_sin_lineas_en_el_espejo_no_se_compara():
    facturas, _lineas, catalogo = _escenario("35.00")
    assert evaluar_ncs_contra_motor(facturas, [], catalogo, [_bandeja()]) == []


def test_dos_nc_de_la_misma_orden_se_suman():
    facturas, lineas, catalogo = _escenario("20.00")
    facturas.append(
        b.factura(
            "12",
            numero="RINV/2",
            so_id=None,
            move_type="out_refund",
            factura_origen_id="10",
            monto_sin_impuestos="15.00",
            monto_sin_impuestos_signed_usd="-15.00",
        )
    )
    lineas.append(b.linea_factura("LF2", factura_id="12", producto_id="1082", subtotal="15.00"))
    (h,) = evaluar_ncs_contra_motor(facturas, lineas, catalogo, [_bandeja()])
    assert h.nc_usd == Decimal("35.00")
    assert h.ncs == ["RINV/1", "RINV/2"]
    assert h.veredicto == COINCIDE


def test_el_endpoint_devuelve_resumen_y_filtra_solo_fuera(monkeypatch):
    import asyncio

    from cxc.repositories import InMemoryRepository
    from cxc.web import app as app_mod

    repo = InMemoryRepository()
    facturas, lineas, catalogo = _escenario("73.35")
    repo.upsert_facturas(facturas)
    repo.upsert_lineas_factura(lineas)
    repo.upsert_catalogo(catalogo)
    repo.upsert_bandejas([_bandeja()])
    monkeypatch.setattr(app_mod, "get_repo", lambda: repo)

    todo = asyncio.run(app_mod.get_nc_vs_motor())
    assert todo["resumen"] == {"nc_mayor_que_motor": 1}
    assert todo["items"][0]["so_id"] == "S1"

    solo = asyncio.run(app_mod.get_nc_vs_motor(solo_fuera=True))
    assert len(solo["items"]) == 1


def test_nc_de_descuento_con_linea_de_valuacion_de_inventario_si_se_compara():
    """Las NC reales traen ademas lineas con producto que no son de venta (valuacion
    de inventario): el descuento es la linea «Descuento», que explica todo el monto."""
    facturas, lineas, catalogo = _escenario("35.00")
    lineas.append(b.linea_factura("LF9", factura_id="11", producto_id="900", subtotal="80"))
    (h,) = evaluar_ncs_contra_motor(facturas, lineas, catalogo, [_bandeja()])
    assert h.veredicto == COINCIDE


def test_nc_con_producto_que_si_forma_parte_del_monto_no_es_descuento():
    facturas, lineas, catalogo = _escenario("35.00")
    facturas[1].monto_sin_impuestos = Decimal("115.00")  # 35 de descuento + 80 de producto
    lineas.append(b.linea_factura("LF9", factura_id="11", producto_id="900", subtotal="80"))
    assert evaluar_ncs_contra_motor(facturas, lineas, catalogo, [_bandeja()]) == []
