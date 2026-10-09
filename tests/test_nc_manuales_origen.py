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


def _lector(facturas_odoo, conciliada_con=None, nc=None):
    """Odoo simulado. ``conciliada_con``: ids de factura contra las que la NC esta conciliada."""
    conciliada_con = conciliada_con or []
    # linea por cobrar de la NC = 900; las lineas de las facturas = 1000 + id
    parciales = {
        500 + i: {"debit_move_id": [1000 + fid], "credit_move_id": [900]}
        for i, fid in enumerate(conciliada_con)
    }

    def ejecutar(modelo, metodo, args, kwargs=None):
        if modelo == "account.move" and metodo == "search_read":
            dominio = args[0]
            if any(c[0] == "name" for c in dominio if isinstance(c, list)):
                nombres = next(c[2] for c in dominio if c[0] == "name")
                return [f for f in facturas_odoo if f["name"] in nombres]
            return [nc or NC_MANUAL]
        if modelo == "account.move.line" and metodo == "search_read":
            return [
                {
                    "id": 900,
                    "move_id": [17000, "NC"],
                    "matched_debit_ids": [],
                    "matched_credit_ids": list(parciales),
                }
            ]
        if modelo == "account.partial.reconcile" and metodo == "read":
            return [parciales[i] | {"id": i} for i in args[0]]
        if modelo == "account.move.line" and metodo == "read":
            return [{"id": i, "move_id": [i - 1000, "F"]} for i in args[0]]
        if modelo == "account.move" and metodo == "read":
            return [{"id": i, "move_type": "out_invoice", "state": "posted"} for i in args[0]]
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


def test_la_nc_manual_queda_atribuida_a_su_factura_por_la_referencia():
    (f,) = _lector([{"id": 11406, "name": "00000535"}]).changed_facturas(None)
    assert f.factura_origen_id == "11406"


def test_si_el_numero_identifica_varias_facturas_no_se_atribuye():
    lector = _lector([{"id": 1, "name": "00000535"}, {"id": 2, "name": "00000535"}])
    (f,) = lector.changed_facturas(None)
    assert f.factura_origen_id is None


def test_si_la_factura_no_existe_queda_sin_atribuir():
    (f,) = _lector([]).changed_facturas(None)
    assert f.factura_origen_id is None


def test_la_conciliacion_contable_atribuye_la_nc_aunque_el_ref_no_diga_nada():
    """NC 00000013 real: sin ref, conciliada contra la factura 00000515 (S00336)."""
    lector = _lector([], conciliada_con=[5515], nc=dict(NC_MANUAL, ref=False))
    (f,) = lector.changed_facturas(None)
    assert f.factura_origen_id == "5515"


def test_la_conciliacion_manda_sobre_el_ref_cuando_discrepan():
    lector = _lector([{"id": 11406, "name": "00000535"}], conciliada_con=[7777])
    (f,) = lector.changed_facturas(None)
    assert f.factura_origen_id == "7777"


def test_conciliada_contra_dos_facturas_no_se_atribuye_por_conciliacion():
    lector = _lector([], conciliada_con=[1, 2])
    (f,) = lector.changed_facturas(None)
    assert f.factura_origen_id is None
