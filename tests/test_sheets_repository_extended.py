"""Cobertura extendida de ``SheetsRepository`` -- métodos de configuración,

reglas de descuento, feriados, anomalías y tasas históricas que
``test_sheets.py`` no ejercía todavía. Usa ``InMemorySheetGateway`` (el
mismo doble de prueba que ya usa el resto de la suite de Sheets) para no
requerir credenciales de Google.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from cxc.models import (
    Condicion,
    DescuentoDiferencialCambiario,
    DescuentoProducto,
    DescuentoProntoPago,
    DescuentoRecompra,
    DescuentoVolumen,
    Feriado,
    Moneda,
    Pago,
    PromocionPrimeraCompra,
    TipoBeneficio,
    TipoFeriado,
)
from cxc.sheets.gateway import InMemorySheetGateway
from cxc.sheets.repository import SheetsRepository


def _repo() -> SheetsRepository:
    return SheetsRepository(InMemorySheetGateway())


# --- Configuración genérica (_Meta) ------------------------------------------


def test_config_get_set_all() -> None:
    repo = _repo()
    assert repo.get_config("no_existe") is None
    repo.set_config("cash_window_business_days", "5")
    assert repo.get_config("cash_window_business_days") == "5"

    # all_config() lee filas de la pestaña "_Meta" -- InMemorySheetGateway
    # guarda get_meta/set_meta en un dict aparte (solo GspreadGateway
    # persiste la meta como filas reales), así que se siembra la fila
    # directamente para ejercer la lectura de all_config().
    repo._g.seed("_Meta", [{"key": "cash_window_business_days", "value": "5"}])
    assert repo.all_config()["cash_window_business_days"] == "5"


def test_invalidate_cache_delega_al_gateway() -> None:
    repo = _repo()
    repo.invalidate_cache()
    repo.invalidate_cache("Pagos")


# --- Usuarios de la plataforma -----------------------------------------------


def test_usuarios_plataforma_upsert_y_lectura() -> None:
    repo = _repo()
    assert repo.get_usuario_plataforma("nadie@x.com") is None
    repo.upsert_usuario_plataforma(
        {"email": "vendedor@lubrikca.com", "rol": "ventas", "activo": "TRUE"}
    )
    u = repo.get_usuario_plataforma("VENDEDOR@LUBRIKCA.COM")
    assert u is not None
    assert u["rol"] == "ventas"
    assert len(repo.all_usuarios_plataforma()) == 1


# --- Espejo: get_*/all_* delegando a serde -----------------------------------


def test_get_cliente_get_orden_get_pago_no_encontrados() -> None:
    repo = _repo()
    assert repo.get_cliente("NOPE") is None
    assert repo.get_orden("NOPE") is None
    assert repo.get_pago("NOPE") is None
    assert repo.all_clientes() == []
    assert repo.all_ordenes() == []
    assert repo.all_lineas() == []


def test_all_pagos_full_y_marcar_recibido() -> None:
    repo = _repo()
    repo.upsert_pagos(
        [
            Pago(
                pago_id="PG1",
                cliente_id="C1",
                monto=Decimal("100"),
                moneda=Moneda.USD,
                metodo_pago="M1",
                fecha_pago=datetime(2026, 1, 1, 10, 0),
                vendedor_email="v1@lubrikca.com",
            )
        ]
    )
    rows = repo.all_pagos_full()
    assert len(rows) == 1
    actualizados = repo.marcar_pagos_recibido(
        ["PG1", "NO_EXISTE"], "REC-001", datetime(2026, 1, 2, 9, 0), "cobranza@lubrikca.com"
    )
    assert len(actualizados) == 1
    assert actualizados[0]["recibido"] == "TRUE"
    assert repo.all_pagos_full()[0]["recibido"] == "TRUE"


def test_get_metodo_pago_inexistente() -> None:
    assert _repo().get_metodo_pago("NO_EXISTE") is None


# --- Reglas de descuento: lectura + escritura --------------------------------


def test_descuentos_pronto_pago_append_y_lectura() -> None:
    repo = _repo()
    assert repo.descuentos_marca_categoria() == []
    repo.append_descuento_pronto_pago(
        DescuentoProntoPago(
            regla_id="PP_TEST",
            marca="Sinoco",
            porcentaje=Decimal("0.05"),
            vigencia_desde=date(2026, 1, 1),
        )
    )
    reglas = repo.descuentos_marca_categoria()
    assert len(reglas) == 1
    assert reglas[0].regla_id == "PP_TEST"
    assert repo.descuentos_pronto_pago() == reglas


def test_descuentos_volumen_append_y_lectura() -> None:
    repo = _repo()
    assert repo.descuentos_volumen() == []
    repo.append_descuento_volumen(
        DescuentoVolumen(
            regla_id="VOL_TEST",
            marca="Sinoco",
            litros_minimo=Decimal("100"),
            porcentaje=Decimal("0.05"),
            vigencia_desde=date(2026, 1, 1),
        )
    )
    reglas = repo.descuentos_volumen()
    assert len(reglas) == 1
    assert reglas[0].regla_id == "VOL_TEST"


def test_descuentos_recompra_append_y_lectura() -> None:
    repo = _repo()
    assert repo.descuentos_recompra() == []
    repo.append_descuento_recompra(
        DescuentoRecompra(
            regla_id="REC_TEST", porcentaje=Decimal("0.03"), vigencia_desde=date(2026, 1, 1)
        )
    )
    reglas = repo.descuentos_recompra()
    assert len(reglas) == 1
    assert reglas[0].regla_id == "REC_TEST"


def test_descuentos_producto_append_y_lectura() -> None:
    repo = _repo()
    assert repo.descuentos_producto() == []
    repo.append_descuento_producto(
        DescuentoProducto(
            regla_id="PROD_TEST",
            productos="P1",
            porcentaje=Decimal("0.05"),
            vigencia_desde=date(2026, 1, 1),
        )
    )
    reglas = repo.descuentos_producto()
    assert len(reglas) == 1
    assert reglas[0].regla_id == "PROD_TEST"


def test_descuentos_diferencial_cambiario_defaults_y_append() -> None:
    repo = _repo()
    # Sin filas persistidas, cae a los 3 defaults hardcodeados.
    defaults = repo.descuentos_diferencial_cambiario()
    assert len(defaults) == 3
    assert {d.regla_id for d in defaults} == {
        "DIF_35_VES",
        "DIF_EQUIPARAR",
        "DIF_CANDIDATOS_CIERRE",
    }

    repo.append_descuento_diferencial_cambiario(
        DescuentoDiferencialCambiario(
            regla_id="DIF_TEST", nombre="Diferencial de prueba", vigencia_desde=date(2026, 1, 1)
        )
    )
    reglas = repo.descuentos_diferencial_cambiario()
    assert len(reglas) == 1
    assert reglas[0].regla_id == "DIF_TEST"


def test_promociones_primera_compra_append_y_lectura() -> None:
    repo = _repo()
    assert repo.promociones_primera_compra() == []
    repo.append_promocion_primera_compra(
        PromocionPrimeraCompra(regla_id="PROMO_TEST", vigencia_desde=date(2026, 1, 1))
    )
    promos = repo.promociones_primera_compra()
    assert len(promos) == 1
    assert promos[0].regla_id == "PROMO_TEST"


def test_descuento_bcv_completo_lectura_vacia() -> None:
    assert _repo().descuento_bcv_completo() == []


def test_reglas_recurrencia_crear_y_actualizar_porcentaje() -> None:
    repo = _repo()
    assert repo.reglas_recurrencia() == []
    repo.set_regla_recurrencia_porcentaje(Condicion.RECOMPRA.value, Decimal("0.03"))
    reglas = repo.reglas_recurrencia()
    assert len(reglas) == 1
    assert reglas[0].valor == Decimal("0.03")
    assert reglas[0].tipo_beneficio == TipoBeneficio.PORCENTAJE

    repo.set_regla_recurrencia_porcentaje(Condicion.RECOMPRA.value, Decimal("0.04"))
    reglas = repo.reglas_recurrencia()
    assert len(reglas) == 1
    assert reglas[0].valor == Decimal("0.04")


# --- delete_regla / set_regla_activo -----------------------------------------


def test_delete_regla_y_set_regla_activo() -> None:
    repo = _repo()
    repo.append_descuento_volumen(
        DescuentoVolumen(
            regla_id="VOL_DEL", porcentaje=Decimal("0.02"), vigencia_desde=date(2026, 1, 1)
        )
    )
    assert repo.set_regla_activo("DescuentosVolumen", "NO_EXISTE", False) is False
    assert repo.set_regla_activo("DescuentosVolumen", "VOL_DEL", False) is True
    reglas = repo.descuentos_volumen()
    assert next(r for r in reglas if r.regla_id == "VOL_DEL").activo is False

    assert repo.delete_regla("DescuentosVolumen", "NO_EXISTE") is False
    assert repo.delete_regla("DescuentosVolumen", "VOL_DEL") is True
    assert all(r.regla_id != "VOL_DEL" for r in repo.descuentos_volumen())


# --- Feriados ----------------------------------------------------------------


def test_feriados_append_y_lectura() -> None:
    repo = _repo()
    assert repo.feriados() == []
    repo.append_feriado(
        Feriado(fecha=date(2026, 12, 25), descripcion="Navidad", tipo=TipoFeriado.NACIONAL)
    )
    feriados = repo.feriados()
    assert len(feriados) == 1
    assert feriados[0].descripcion == "Navidad"


# --- Anomalías / listas históricas / tasas históricas / pagos huérfanos -----


def test_anomalias_aceptadas_append_y_lectura() -> None:
    repo = _repo()
    assert repo.all_anomalias_aceptadas() == []
    repo.append_anomalia_aceptada({"anomalia_id": "ANOM_1", "motivo_aceptacion": "Revisado"})
    rows = repo.all_anomalias_aceptadas()
    assert len(rows) == 1
    assert rows[0]["anomalia_id"] == "ANOM_1"


def test_listas_precios_historicas_lectura_vacia() -> None:
    assert _repo().all_listas_precios_historicas() == []


def test_tasas_historicas_auditoria_replace() -> None:
    repo = _repo()
    assert repo.all_tasas_historicas_auditoria() == []
    repo.replace_tasas_historicas_auditoria([{"fecha": "2026-01-01", "tasa_bcv_usd": "36.0"}])
    rows = repo.all_tasas_historicas_auditoria()
    assert len(rows) == 1
    assert rows[0]["fecha"] == "2026-01-01"
    repo.replace_tasas_historicas_auditoria([])
    assert repo.all_tasas_historicas_auditoria() == []


def test_pagos_huerfanos_cerrados_upsert_y_lectura() -> None:
    repo = _repo()
    assert repo.all_pagos_huerfanos_cerrados() == []
    repo.upsert_pago_huerfano_cerrado(
        {
            "pago_id": "PG_HUERFANO",
            "motivo": "Cerrado a favor de la empresa",
            "cerrado_por": "admin@lubrikca.com",
            "timestamp_cierre": datetime(2026, 1, 1, 10, 0).isoformat(),
        }
    )
    rows = repo.all_pagos_huerfanos_cerrados()
    assert len(rows) == 1
    assert rows[0]["pago_id"] == "PG_HUERFANO"


# --- Vinculaciones ------------------------------------------------------------


def test_vinculaciones_de_orden_vacio() -> None:
    repo = _repo()
    assert repo.vinculaciones_de_orden("SO1") == []
    assert repo.all_vinculaciones() == []


def test_update_vinculaciones_lista_vacia_no_falla() -> None:
    _repo().update_vinculaciones([])


# --- Fechas históricas (mapa so_id -> fecha) ----------------------------------


def test_fechas_historicas_map_ignora_filas_incompletas() -> None:
    repo = _repo()
    repo._g.seed(
        "FechasHistoricas",
        [
            {"so_id": "SO0092", "fecha_historica": "2026-03-01"},
            {"so_id": "", "fecha_historica": "2026-03-02"},
            {"so_id": "SO0093", "fecha_historica": ""},
        ],
    )
    mapa = repo.fechas_historicas_map()
    assert mapa["92"] == "2026-03-01"
    assert mapa["SO0092"] == "2026-03-01"
    assert "SO0093" not in mapa
