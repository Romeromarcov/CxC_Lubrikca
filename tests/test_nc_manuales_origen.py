"""NC manuales: la factura origen se lee del campo ``ref`` (8-oct-2026)."""

from __future__ import annotations

from cxc.odoo.client import OdooXmlRpcReader, numeros_de_factura_en_referencia


def test_lee_el_numero_de_factura_de_la_referencia():
    ref = "00000535 |  Origen: S00571 CAP: 0.41$ CAMBI: 0.07$ TOTAL: 0.48$"
    assert numeros_de_factura_en_referencia(ref) == ["00000535"]


def test_descarta_el_numero_de_la_propia_nc():
    assert numeros_de_factura_en_referencia("NC 00000018 y 00000535", propio="00000018") == [
        "00000535"
    ]


def test_sin_numero_de_ocho_digitos_no_hay_candidata():
    assert numeros_de_factura_en_referencia("Origen: S00007 CAP: 35.00$") == []
    assert numeros_de_factura_en_referencia(False) == []
    assert numeros_de_factura_en_referencia("123456789") == []  # 9 digitos no es una factura


def _lector(facturas_odoo):
    def ejecutar(modelo, metodo, args, kwargs=None):
        if modelo == "account.move" and metodo == "search_read":
            dominio = args[0]
            if any(c[0] == "name" for c in dominio if isinstance(c, list)):
                nombres = next(c[2] for c in dominio if c[0] == "name")
                return [f for f in facturas_odoo if f["name"] in nombres]
            return [NC_MANUAL]
        return []

    return OdooXmlRpcReader(config=None, execute=ejecutar)


NC_MANUAL = {
    "id": 17000,
    "name": "00000018",
    "invoice_origin": False,
    "move_type": "out_refund",
    "invoice_date": "2026-07-22",
    "date": "2026-07-22",
    "currency_id": [166, "VES"],
    "amount_total": 100.0,
    "amount_untaxed": 100.0,
    "amount_total_signed_usd": -1.0,
    "amount_untaxed_signed_usd": -1.0,
    "state": "posted",
    "reversed_entry_id": False,
    "debit_origin_id": False,
    "ref": "00000535 |  Origen: S00571 CAP: 0.41$",
}


def test_la_nc_manual_queda_atribuida_a_su_factura():
    (f,) = _lector([{"id": 11406, "name": "00000535"}]).changed_facturas(None)
    assert f.factura_origen_id == "11406"


def test_si_el_numero_identifica_varias_facturas_no_se_atribuye():
    lector = _lector([{"id": 1, "name": "00000535"}, {"id": 2, "name": "00000535"}])
    (f,) = lector.changed_facturas(None)
    assert f.factura_origen_id is None


def test_si_la_factura_no_existe_queda_sin_atribuir():
    (f,) = _lector([]).changed_facturas(None)
    assert f.factura_origen_id is None
