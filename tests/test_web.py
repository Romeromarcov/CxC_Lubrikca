"""Pruebas de importación e inicialización de la app FastAPI."""


def test_app_import():
    from cxc.web.app import app

    assert app.title == "CxC Lubrikca Billing Dashboard"
