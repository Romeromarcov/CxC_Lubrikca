"""Bug real (agosto 2026): la pricelist "primaria" se elegia como el

primer id de ``valid_pricelists_usd``/``_ves`` (texto de config), sin
importar si esa lista seguia activa en Odoo -- una lista ARCHIVADA con
reglas de precio viejas (ej. "7,8" con #7 archivada, 86 reglas stale que
ya no coinciden con las de #8) quedaba como primaria por puro accidente
de orden en el string. ``_primer_id_activo`` corrige esto sin requerir
que el usuario reordene la config: prefiere la primera activa.
"""

from __future__ import annotations

from cxc.web.app import _primer_id_activo


def _fake_execute(activos: set[int]):
    def _execute(model, method, args, kwargs=None):
        assert model == "product.pricelist"
        ids = args[0][0][2]
        return [{"id": i, "active": i in activos} for i in ids]

    return _execute


def test_prefiere_la_primera_lista_activa_aunque_no_sea_la_primera_del_config():
    execute = _fake_execute(activos={8})
    assert _primer_id_activo(execute, [7, 8]) == 8


def test_devuelve_la_primera_si_esa_ya_esta_activa():
    execute = _fake_execute(activos={7, 8})
    assert _primer_id_activo(execute, [7, 8]) == 7


def test_cae_a_la_primera_si_ninguna_esta_activa():
    execute = _fake_execute(activos=set())
    assert _primer_id_activo(execute, [7, 8]) == 7


def test_lista_vacia_devuelve_none():
    execute = _fake_execute(activos=set())
    assert _primer_id_activo(execute, []) is None


def test_si_la_consulta_a_odoo_falla_cae_a_la_primera_sin_reventar():
    def _execute_falla(model, method, args, kwargs=None):
        raise ConnectionError("Odoo caido")

    assert _primer_id_activo(_execute_falla, [7, 8]) == 7
