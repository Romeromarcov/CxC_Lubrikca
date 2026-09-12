"""De dónde sale el abono de una orden en el reporte de saldos (Fase 2.4, pieza 35).

La regla es «local manda si existe; si no, Odoo». La versión anterior tenía además una
rama «máximo entre las dos» que **nunca corría** -- doce líneas sin ejecutar en años de
tests, porque toda entrada local hacía ``continue`` antes. Estos tests fijan la regla
que sí regía, y el A/B del final prueba que la pieza da lo mismo que el bloque viejo
sobre lo que el bloque viejo podía alcanzar.
"""

from __future__ import annotations

from decimal import Decimal

from cxc.engine.abonos import AbonoDeOrden, fusionar_abonos


def _local(bcv="100", binance="95", ultimo="2026-07-01"):
    return {
        "abono_bcv": Decimal(bcv),
        "abono_binance": Decimal(binance),
        "ultimo_abono": ultimo,
        "tiene_vinc_manual": True,
    }


def _odoo(bcv="120", binance="120", ultimo="2026-07-05", sin_tasa=0):
    return {
        "abono_bcv": Decimal(bcv),
        "abono_binance": Decimal(binance),
        "ultimo_abono": ultimo,
        "pagos_sin_tasa": sin_tasa,
    }


def test_con_vinculacion_local_manda_la_local_y_odoo_queda_descartado() -> None:
    f = fusionar_abonos({"SO1": _local()}, {"SO1": _odoo(bcv="999")})
    a = f.por_orden["SO1"]
    assert a.abono_bcv == Decimal("100"), "no el máximo (999), no la suma: la local"
    assert a.abono_binance == Decimal("95")
    assert a.tiene_vinc_local and not a.desde_odoo
    assert f.odoo_descartado == ["SO1"], "y queda dicho que Odoo decía otra cosa"


def test_sin_vinculacion_local_entra_la_de_odoo_marcada() -> None:
    f = fusionar_abonos({}, {"SO2": _odoo(sin_tasa=1)})
    a = f.por_orden["SO2"]
    assert a.abono_bcv == Decimal("120") and a.desde_odoo and not a.tiene_vinc_local
    assert a.ultimo_abono == "2026-07-05"
    assert a.pagos_sin_tasa == 1, "los pagos que Odoo no pudo valorar viajan con el abono"
    assert f.odoo_descartado == []


def test_las_dos_poblaciones_conviven() -> None:
    f = fusionar_abonos({"A": _local()}, {"A": _odoo(), "B": _odoo(bcv="7")})
    assert set(f.por_orden) == {"A", "B"}
    assert f.por_orden["A"].abono_bcv == Decimal("100")
    assert f.por_orden["B"].abono_bcv == Decimal("7")
    assert f.odoo_descartado == ["A"]


def test_el_dict_de_salida_conserva_el_nombre_historico_del_campo() -> None:
    """Los consumidores del reporte leen ``tiene_vinc_manual``; el nombre miente
    (es «tiene vinculación local») pero cambiarlo es otra pieza."""
    d = AbonoDeOrden(tiene_vinc_local=True).como_dict()
    assert d["tiene_vinc_manual"] is True
    assert set(d) == {
        "abono_bcv",
        "abono_binance",
        "ultimo_abono",
        "desde_odoo",
        "tiene_vinc_manual",
        "pagos_sin_tasa",
    }


# --- A/B contra el bloque viejo --------------------------------------------------------


def _bloque_viejo(pagos_by_so: dict, pagos_odoo: dict) -> dict:
    """El bloque tal cual estaba en ``_get_reporte_saldos_sync``, incluida la rama
    del máximo que nunca corre."""
    pagos_by_so = {k: dict(v) for k, v in pagos_by_so.items()}
    for so_name, p_odoo in pagos_odoo.items():
        existing = pagos_by_so.get(so_name, {})
        if existing.get("tiene_vinc_manual", False):
            continue
        total_paid_bcv = p_odoo["abono_bcv"]
        total_paid_binance = p_odoo["abono_binance"]
        latest_inv_date = p_odoo["ultimo_abono"]
        if so_name not in pagos_by_so:
            pagos_by_so[so_name] = {
                "abono_bcv": total_paid_bcv,
                "abono_binance": total_paid_binance,
                "ultimo_abono": latest_inv_date,
                "desde_odoo": True,
                "tiene_vinc_manual": False,
            }
        else:  # pragma: no cover -- inalcanzable, y es el punto
            pagos_by_so[so_name]["abono_bcv"] = max(
                Decimal(str(pagos_by_so[so_name].get("abono_bcv", "0"))), total_paid_bcv
            )
    return pagos_by_so


def test_ab_la_pieza_da_lo_mismo_que_el_bloque_viejo() -> None:
    import random

    rng = random.Random(35)
    for _ in range(200):
        locales = {
            f"S{i}": _local(bcv=str(rng.randint(1, 500)), binance=str(rng.randint(1, 500)))
            for i in range(rng.randint(0, 6))
        }
        odoo = {
            f"S{i}": _odoo(bcv=str(rng.randint(1, 500)), binance=str(rng.randint(1, 500)))
            for i in rng.sample(range(10), rng.randint(0, 8))
        }
        viejo = _bloque_viejo(locales, odoo)
        nuevo = {so: a.como_dict() for so, a in fusionar_abonos(locales, odoo).por_orden.items()}
        assert set(viejo) == set(nuevo)
        for so in viejo:
            for campo in ("abono_bcv", "abono_binance", "ultimo_abono", "tiene_vinc_manual"):
                assert viejo[so][campo] == nuevo[so][campo], (so, campo)
            assert bool(viejo[so].get("desde_odoo", False)) == nuevo[so]["desde_odoo"], so
