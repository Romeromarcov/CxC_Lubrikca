"""Quién hizo una acción de dinero, sin creerle al navegador.

Tres endpoints que cambian decisiones de dinero —aceptar una discrepancia, marcar un
descuento como no otorgado, aprobar un descuento del sistema— tomaban al actor del
**cuerpo del request**. Y el formulario lo pide así:

    quien = prompt("¿Quién lo marca?", "Dirección / Administración");

La persona **tipea** quién es, con un default que no identifica a nadie, mientras la
cookie de sesión ya lo sabe. En el mismo archivo, `patch_auditoria_estado` sí lo lee de
la sesión: la asimetría no era una decisión, era un olvido.

**Lo declarado no se descarta**, y ésa es la mitad que importa. Que alguien escriba
«Dirección / Administración» puede ser deliberado —actúa en nombre de ese rol— y
borrarlo perdería la intención. Lo que no puede pasar es que la identidad real no quede.
"""

from __future__ import annotations

import pytest

from cxc.auth import actor_de_la_accion, identidad_de_sesion

ANA = {"nombre": "Ana Pérez", "email": "ana@lubrikca.test", "rol": "admin"}


def test_con_sesion_y_un_declarado_generico_queda_la_identidad_sola() -> None:
    """El caso del 99 %: la persona apretó Enter sobre el default del `prompt`."""
    assert actor_de_la_accion(ANA, "Dirección / Administración") == "Ana Pérez"


def test_con_sesion_y_un_declarado_DISTINTO_quedan_los_dos() -> None:
    """Actuar en nombre de un rol es legítimo, y se conserva la intención."""
    assert (
        actor_de_la_accion(ANA, "Gerencia de Cobranzas")
        == "Ana Pérez en nombre de Gerencia de Cobranzas"
    )


def test_sin_sesion_se_conserva_lo_declarado_porque_es_todo_lo_que_hay() -> None:
    """No se puede inventar una identidad: lo declarado es el único dato."""
    assert actor_de_la_accion(None, "Gerencia") == "Gerencia"
    assert actor_de_la_accion({}, "Gerencia") == "Gerencia"


def test_sin_sesion_y_sin_declarado_NUNCA_queda_vacio() -> None:
    """Una fila de auditoría sin actor no sirve para auditar."""
    assert actor_de_la_accion(None, "") == "desconocido"
    assert actor_de_la_accion(None, "   ") == "desconocido"


@pytest.mark.parametrize(
    "declarado",
    [
        "",
        "   ",
        "Dirección / Administración",
        "direccion / administracion",
        "DIRECCIÓN / AUDITOR",
        "Dirección / Facturación",
        "desconocido",
    ],
)
def test_los_defaults_del_formulario_no_ensucian_la_identidad(declarado) -> None:
    """Son los tres defaults de los `prompt` más sus variantes sin tilde.

    Si no se filtraran, cada fila diría «Ana Pérez en nombre de Dirección /
    Administración» y el campo dejaría de servir para distinguir los casos en que
    alguien SÍ actuó en nombre de otro.
    """
    assert actor_de_la_accion(ANA, declarado) == "Ana Pérez"


def test_declarar_el_propio_nombre_no_se_duplica() -> None:
    assert actor_de_la_accion(ANA, "Ana Pérez") == "Ana Pérez"
    assert actor_de_la_accion(ANA, "ana pérez") == "Ana Pérez", "sin importar el case"


def test_sin_nombre_la_identidad_sale_del_email() -> None:
    """Un usuario recién registrado puede no tener nombre cargado."""
    assert identidad_de_sesion({"email": "c@lubrikca.test"}) == "c@lubrikca.test"
    assert actor_de_la_accion({"email": "c@lubrikca.test"}, "") == "c@lubrikca.test"


def test_identidad_de_sesion_sin_usuario_es_cadena_vacia() -> None:
    """Vacía y no «desconocido»: esta función informa, no decide el respaldo."""
    assert identidad_de_sesion(None) == ""
    assert identidad_de_sesion({}) == ""


# --- los tres endpoints ------------------------------------------------------


def _llamar(ruta, cuerpo, usuario, preparar=None):
    from contextlib import ExitStack
    from unittest.mock import MagicMock, patch

    from fastapi.testclient import TestClient

    import cxc.web.app as app

    async def _nada():
        return None

    repo = MagicMock()
    repo.all_descuentos_sistema_aprobados.return_value = {}
    if preparar is not None:
        preparar(repo)
    with ExitStack() as pila:
        for parche in (
            patch("cxc.web.app.get_repo", return_value=repo),
            patch("cxc.web.app.get_current_user_from_cookie", return_value=usuario),
            patch("cxc.web.app._detectar_sobre_descuento_vigente", return_value=None),
            patch("cxc.web.app.run_scraper_in_background", _nada),
            patch("cxc.web.app.run_sync_in_background", _nada),
            patch("cxc.web.app._aplicar_migraciones_pendientes"),
        ):
            pila.enter_context(parche)
        cliente = pila.enter_context(TestClient(app.app, raise_server_exceptions=False))
        r = cliente.post(ruta, json=cuerpo)
    return r, repo


def test_el_endpoint_de_descuento_no_otorgado_guarda_la_identidad_real() -> None:
    """Lo que se registraba antes era el texto del `prompt`, tal cual."""
    r, repo = _llamar(
        "/api/ventas/descuento-no-otorgado",
        {"so_id": "S00010", "motivo": "pagó completo", "marcado_por": "Cualquier Nombre"},
        ANA,
    )
    assert r.status_code == 200, r.text
    fila = repo.append_descuento_no_otorgado.call_args[0][0]
    assert fila["marcado_por"] == "Ana Pérez en nombre de Cualquier Nombre"
    assert fila["so_id"] == "S00010"


def test_desmarcar_no_necesita_actor_porque_borra_la_fila() -> None:
    r, repo = _llamar(
        "/api/ventas/descuento-no-otorgado",
        {"so_id": "S00010", "no_otorgado": False},
        ANA,
    )
    assert r.status_code == 200
    repo.delete_descuento_no_otorgado.assert_called_once_with("S00010")
    repo.append_descuento_no_otorgado.assert_not_called()


def test_aceptar_discrepancia_guarda_la_identidad_real() -> None:
    r, repo = _llamar(
        "/api/auditoria/aceptar-discrepancia",
        {
            "discrepancia_id": "D1",
            "so_id": "S00010",
            "tipo_discrepancia": "descuento",
            "aprobado_por": "Dirección / Auditor",
        },
        ANA,
    )
    assert r.status_code == 200, r.text
    fila = repo.append_discrepancia_aceptada.call_args[0][0]
    assert fila["aprobado_por"] == "Ana Pérez", "el default genérico no ensucia"


def test_aprobar_descuento_sistema_guarda_la_identidad_real() -> None:
    r, repo = _llamar(
        "/api/facturacion/aprobar-descuento-sistema",
        {"so_id": "S00010", "monto": 100.0, "aprobado_por": "Otro"},
        ANA,
    )
    assert r.status_code == 200, r.text
    fila = repo.upsert_descuento_sistema_aprobado.call_args[0][0]
    assert fila["aprobado_por"] == "Ana Pérez en nombre de Otro"


# --- los modelos de entrada: positivo y finito, o no entra ----------------------


@pytest.mark.parametrize(
    "ruta,cuerpo",
    [
        ("/api/vincular", {"pago_id": "P1", "so_id": "S00010", "monto_aplicado": 0}),
        ("/api/vincular", {"pago_id": "P1", "so_id": "S00010", "monto_aplicado": -5}),
        ("/api/vincular", {"pago_id": "P1", "so_id": "S00010", "monto_aplicado": "inf"}),
        ("/api/pago/P1/tasa-binance", {"tasa_binance": 0}),
        ("/api/pago/P1/tasa-binance", {"tasa_binance": -961.67}),
        ("/api/pago/P1/tasa-binance", {"tasa_binance": "nan"}),
    ],
)
def test_un_monto_o_tasa_no_positivo_da_422_antes_de_tocar_nada(ruta, cuerpo) -> None:
    """Invariante al escribir, en el modelo: `post_vincular` no validaba el monto en
    absoluto, y `TasaBinanceEditRequest` aceptaba cualquier float. Una tasa en cero es
    «no hay dato» para todo lo que la consume, así que no puede entrar por la puerta."""
    r, repo = _llamar(ruta, cuerpo, ANA)
    assert r.status_code == 422, r.text
    repo.append_vinculacion.assert_not_called()
    repo.upsert_pago_tasa_binance_override.assert_not_called()


def test_editar_la_tasa_binance_de_un_pago_pendiente_guarda_la_identidad_real() -> None:
    """`editado_por` venía del cuerpo, como los otros tres actores. Mismo arreglo."""
    from unittest.mock import patch

    from cxc.engine.promedios_tasas import RangoDelDia

    repo_extra = {}

    def _preparar(repo):
        from datetime import datetime
        from types import SimpleNamespace

        repo.get_pago.return_value = SimpleNamespace(
            pago_id="P1", fecha_pago=datetime(2026, 9, 11, 10, 0, 0)
        )
        repo_extra["repo"] = repo

    with patch(
        "cxc.web.app.rango_binance_del_dia",
        return_value=RangoDelDia(minimo=None, maximo=None, capturas=0),
    ):
        r, repo = _llamar(
            "/api/pago/P1/tasa-binance",
            {"tasa_binance": 961.67, "editado_por": "Dirección / Administración"},
            ANA,
            preparar=_preparar,
        )
    assert r.status_code == 200, r.text
    fila = repo.upsert_pago_tasa_binance_override.call_args[0][0]
    assert fila["editado_por"] == "Ana Pérez", "el default genérico no ensucia"
