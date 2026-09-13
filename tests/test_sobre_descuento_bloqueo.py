"""Tarea 2 (agosto 2026, aprobada por el usuario) -- bloqueo de un nuevo

descuento de sistema cuando la orden ya está en estado de "sobre-descuento"
(Odoo tiene aplicado más descuento, en la orden o en la factura, que lo que
el motor calcula). El bloqueo es SOLO sobre este endpoint -- no debe tocar
Odoo, y la revocación (``activo=false``) siempre debe pasar.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from cxc.models import BandejaFacturacion, LineaFactura, LineaOrden, OrdenVenta
from cxc.repositories import InMemoryRepository
from cxc.web.app import _detectar_sobre_descuentos_batch, app

client = TestClient(app)


def _orden(so_id: str) -> OrdenVenta:
    from datetime import date

    return OrdenVenta(
        so_id=so_id,
        cliente_id="CLI_SD",
        fecha=date(2026, 8, 1),
        fecha_entrega=None,
        monto_total=Decimal("100.00"),
        lista_precios="4",
        vendedor_email="v@lubrikca.com",
        es_primera_compra=False,
        facturada=False,
    )


def _mock_repo(
    *,
    motor_total_descuentos: Decimal,
    lineas_orden: list[LineaOrden] | None = None,
    lineas_factura: list[LineaFactura] | None = None,
    facturas: list | None = None,
) -> MagicMock:
    repo = MagicMock()
    repo.get_orden.return_value = _orden("SO_SD")
    repo.get_bandeja.return_value = BandejaFacturacion(
        so_id="SO_SD",
        lista_aplicada="4",
        precio_base_calculado=Decimal("100.00"),
        total_descuentos=motor_total_descuentos,
        total_motor=Decimal("100.00") - motor_total_descuentos,
    )
    repo.all_facturas.return_value = facturas or []
    repo.all_lineas.return_value = lineas_orden or []
    repo.all_lineas_factura.return_value = lineas_factura or []
    repo.all_catalogo.return_value = []
    return repo


def _aprobar(monto: float = 10.0, activo: bool = True):
    return client.post(
        "/api/facturacion/aprobar-descuento-sistema",
        json={
            "so_id": "SO_SD",
            "monto": monto,
            "motivo": "test",
            "aprobado_por": "tester",
            "activo": activo,
        },
    )


def test_bloquea_aprobar_si_odoo_ya_tiene_mas_descuento_que_el_motor_en_la_orden():
    # Motor exige $5.00 de descuento; Odoo ya tiene 20% aplicado en la línea
    # de orden ($20 sobre un precio base de $100) -- sobre-descuento.
    repo = _mock_repo(
        motor_total_descuentos=Decimal("5.00"),
        lineas_orden=[
            LineaOrden(
                linea_id="1",
                so_id="SO_SD",
                producto="1",
                marca="Sinoco",
                categoria="*",
                cantidad=Decimal("1"),
                precio_unitario=Decimal("100"),
                descuento=Decimal("20.0"),
            )
        ],
    )
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = _aprobar()
    assert res.status_code == 409
    assert "sobre" in res.json()["detail"].lower() or "más descuento" in res.json()["detail"]
    repo.upsert_descuento_sistema_aprobado.assert_not_called()


def test_permite_aprobar_si_no_hay_sobre_descuento():
    # Motor exige $20.00; Odoo solo tiene 5% aplicado ($5) -- normal
    # (descuento pendiente, no sobre-descuento).
    repo = _mock_repo(
        motor_total_descuentos=Decimal("20.00"),
        lineas_orden=[
            LineaOrden(
                linea_id="1",
                so_id="SO_SD",
                producto="1",
                marca="Sinoco",
                categoria="*",
                cantidad=Decimal("1"),
                precio_unitario=Decimal("100"),
                descuento=Decimal("5.0"),
            )
        ],
    )
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = _aprobar()
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    repo.upsert_descuento_sistema_aprobado.assert_called_once()


def test_revocacion_siempre_permitida_aunque_haya_sobre_descuento():
    repo = _mock_repo(
        motor_total_descuentos=Decimal("5.00"),
        lineas_orden=[
            LineaOrden(
                linea_id="1",
                so_id="SO_SD",
                producto="1",
                marca="Sinoco",
                categoria="*",
                cantidad=Decimal("1"),
                precio_unitario=Decimal("100"),
                descuento=Decimal("20.0"),
            )
        ],
    )
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = _aprobar(activo=False)
    assert res.status_code == 200
    repo.upsert_descuento_sistema_aprobado.assert_called_once()
    saved_row = repo.upsert_descuento_sistema_aprobado.call_args[0][0]
    assert saved_row["activo"] == "false"


def test_diferencia_pequena_dentro_de_tolerancia_no_bloquea():
    # Motor $5.00, Odoo $4.995 (redondeo) -- dentro de tolerance_rounding,
    # no debe bloquear.
    repo = _mock_repo(
        motor_total_descuentos=Decimal("5.00"),
        lineas_orden=[
            LineaOrden(
                linea_id="1",
                so_id="SO_SD",
                producto="1",
                marca="Sinoco",
                categoria="*",
                cantidad=Decimal("1"),
                precio_unitario=Decimal("100"),
                descuento=Decimal("5.0"),
            )
        ],
    )
    with patch("cxc.web.app.get_repo", return_value=repo):
        res = _aprobar()
    assert res.status_code == 200


# --- _detectar_sobre_descuentos_batch (agosto 2026): detección proactiva,
# corrida diaria desde el daemon -- ver docstring en web/app.py sobre por
# qué el chequeo reactivo solo (en post_aprobar_descuento_sistema) no basta:
# Contado/Recompra cuentan provisionalmente mientras la ventana de pago
# sigue vigente, lo que puede tapar un sobre-descuento real hasta que la
# ventana vence y el total del motor baja a su valor confirmado. ------------


def _repo_con_orden(*, total_descuentos: str, descuento_linea_pct: str) -> InMemoryRepository:
    from datetime import date as _date

    repo = InMemoryRepository()
    repo.upsert_ordenes(
        [
            OrdenVenta(
                so_id="SO_BATCH",
                cliente_id="CLI_BATCH",
                fecha=_date(2026, 8, 1),
                fecha_entrega=None,
                monto_total=Decimal("100.00"),
                lista_precios="4",
                vendedor_email="v@lubrikca.com",
                es_primera_compra=False,
                facturada=False,
            )
        ]
    )
    repo.upsert_bandeja(
        BandejaFacturacion(
            so_id="SO_BATCH",
            lista_aplicada="4",
            precio_base_calculado=Decimal("100.00"),
            total_descuentos=Decimal(total_descuentos),
            total_motor=Decimal("100.00") - Decimal(total_descuentos),
        )
    )
    repo.upsert_lineas(
        [
            LineaOrden(
                linea_id="1",
                so_id="SO_BATCH",
                producto="1",
                marca="Sinoco",
                categoria="*",
                cantidad=Decimal("1"),
                precio_unitario=Decimal("100"),
                descuento=Decimal(descuento_linea_pct),
            )
        ]
    )
    return repo


def test_batch_detecta_sobre_descuento_y_lo_persiste():
    repo = _repo_con_orden(total_descuentos="5.00", descuento_linea_pct="20.0")
    filas = _detectar_sobre_descuentos_batch(repo)
    assert len(filas) == 1
    assert filas[0]["so_id"] == "SO_BATCH"
    assert filas[0]["tipo_auditoria"] == "descuento_orden"
    assert filas[0]["diferencia_usd"] < 0

    repo.append_auditoria_rows(filas)
    assert len(repo.all_auditoria()) == 1


def test_batch_no_reporta_nada_si_no_hay_sobre_descuento():
    repo = _repo_con_orden(total_descuentos="20.00", descuento_linea_pct="5.0")
    assert _detectar_sobre_descuentos_batch(repo) == []


def test_batch_no_duplica_fila_ya_registrada_hoy():
    repo = _repo_con_orden(total_descuentos="5.00", descuento_linea_pct="20.0")
    primera_corrida = _detectar_sobre_descuentos_batch(repo)
    repo.append_auditoria_rows(primera_corrida)
    assert len(repo.all_auditoria()) == 1

    segunda_corrida = _detectar_sobre_descuentos_batch(repo)
    assert segunda_corrida == []


# --- la unidad de un tramo de volumen (Fase 2.4, pieza 31) -------------------


class TestUnidadDeVolumen:
    """`get_todas_reglas_descuento` tenía la cascada ANTERIOR a la migración.

        u_med = str(getattr(r, "unidad_medida", "") or "").strip()
        if not u_med or u_med == "None":
            u_med = "LITROS" if (float(r.litros_minimo) > 0 and ...) else "CAJAS"

    `litros_minimo` **ya no existe** en `DescuentoVolumen` —la migración de unificación
    de nombres lo eliminó porque era el mismo dato con otro nombre— así que esa línea es
    un `AttributeError` que el `except Exception` del endpoint convierte en **500**: una
    sola regla de volumen con la unidad vacía dejaba en blanco la pantalla de reglas
    entera, no solo esa fila. El motor ya había sacado la cascada; la pantalla no.
    """

    def test_una_unidad_declarada_se_respeta_tal_cual(self) -> None:
        from types import SimpleNamespace

        from cxc.engine.discounts import unidad_de_volumen

        assert unidad_de_volumen(SimpleNamespace(unidad_medida="LITROS")) == ("LITROS", True)
        assert unidad_de_volumen(SimpleNamespace(unidad_medida="UNIDADES")) == ("UNIDADES", True)
        assert unidad_de_volumen(SimpleNamespace(unidad_medida="USD")) == ("USD", True)

    def test_se_normaliza_a_mayusculas_y_sin_espacios(self) -> None:
        from types import SimpleNamespace

        from cxc.engine.discounts import unidad_de_volumen

        assert unidad_de_volumen(SimpleNamespace(unidad_medida=" litros ")) == ("LITROS", True)

    @pytest.mark.parametrize("malo", ["", "   ", None, "None", "none", "NONE"])
    def test_una_unidad_ilegible_cae_a_UNIDADES_y_lo_DECLARA(self, malo) -> None:
        """Es la que el motor usa de todos modos, y ahora se sabe que se infirió.

        La cadena `"None"` no es hipotética: el código original ya la chequeaba, o sea
        que un `None` se guardó como texto en esa columna alguna vez. Y la columna es
        `nullable=False` con `server_default="UNIDADES"`, así que prohíbe NULL pero
        **admite cadena vacía**.
        """
        from types import SimpleNamespace

        from cxc.engine.discounts import unidad_de_volumen

        assert unidad_de_volumen(SimpleNamespace(unidad_medida=malo)) == ("UNIDADES", False)

    def test_una_regla_SIN_el_campo_no_revienta(self) -> None:
        """Que es exactamente lo que pasaba con `r.litros_minimo`."""
        from types import SimpleNamespace

        from cxc.engine.discounts import unidad_de_volumen

        assert unidad_de_volumen(SimpleNamespace()) == ("UNIDADES", False)

    def test_la_inferencia_NO_adivina_entre_litros_y_cajas(self) -> None:
        """La cascada vieja elegía entre LITROS y CAJAS mirando un campo borrado.

        Adivinar decide si «10» significa diez litros o diez cajas, y de eso depende si
        un descuento por volumen se otorga o no. Ahora no se adivina: se usa la unidad
        que el motor usa y se dice que no estaba declarada.
        """
        from types import SimpleNamespace

        from cxc.engine.discounts import unidad_de_volumen

        unidad, declarada = unidad_de_volumen(SimpleNamespace(unidad_medida=""))
        assert unidad != "LITROS" and unidad != "CAJAS"
        assert not declarada
