"""Test de integración del EngineRunner: repositorio → motor → bandeja."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from decimal import Decimal

from cxc.config import EngineConfig
from cxc.engine.price_resolver import DictPriceResolver
from cxc.engine.runner import EngineRunner
from cxc.models import Moneda, TipoTasa
from cxc.repositories import InMemoryRepository

from . import builders as b

CFG = EngineConfig(
    cash_window_business_days=3,
    bcv_complete_formula="differential_over_binance",
)


def _seed() -> InMemoryRepository:
    repo = InMemoryRepository()
    repo.upsert_clientes([b.cliente("C1")])
    repo.upsert_ordenes([b.orden("SO1", cliente_id="C1", primera=False)])
    repo.upsert_lineas([b.linea("L1", so_id="SO1", marca="Sinoco", categoria="*", precio="100")])
    repo.add_metodo_pago(b.metodo("M1", moneda=Moneda.USD, es_contado=True))
    repo.upsert_pagos([b.pago("PG1", cliente_id="C1", monto="94", metodo_id="M1")])
    repo.add_vinculacion(
        b.vinculacion(
            "V1",
            pago_id="PG1",
            so_id="SO1",
            monto_aplicado="94",
            moneda_abono=Moneda.USD,
            tipo_tasa_abono=TipoTasa.N_A,
            hora=datetime(2026, 6, 5, 10, 0),
        )
    )
    repo.add_descuento(b.descuento("D1", marca="Sinoco", categoria="*", porcentaje="0.03"))
    repo.add_regla_recurrencia(b.regla_recompra("0.03"))
    return repo


def _seed_con_orden_anterior_pagada() -> InMemoryRepository:
    """Igual que ``_seed()`` pero con una orden anterior (SO0) del mismo

    cliente, totalmente pagada -- habilita la ventana de Recompra (días de
    crédito + gracia) para SO1. Fixture separada de ``_seed()`` porque otros
    tests de este archivo (``run_all``/``run_teoricos_pendientes``) asumen
    un único pedido en el repo."""
    repo = _seed()
    repo.upsert_ordenes(
        [
            b.orden(
                "SO0",
                cliente_id="C1",
                primera=False,
                fecha=date(2026, 5, 1),
                monto_total="500",
                dias_credito=30,
            )
        ]
    )
    repo.upsert_pagos([b.pago("PG0", cliente_id="C1", monto="500", metodo_id="M1")])
    repo.add_vinculacion(
        b.vinculacion(
            "V0",
            pago_id="PG0",
            so_id="SO0",
            monto_aplicado="500",
            moneda_abono=Moneda.USD,
            hora=datetime(2026, 5, 1, 10, 0),
        )
    )
    return repo


def test_runner_calcula_y_persiste_bandeja() -> None:
    repo = _seed()
    resolver = DictPriceResolver({("P1", "USD"): Decimal("100")})
    runner = EngineRunner(repo, resolver, CFG)

    resultados = runner.run_all(date(2026, 6, 8))
    assert len(resultados) == 1
    bandeja = repo.get_bandeja("SO1")
    assert bandeja is not None
    # SO1 no tiene orden anterior en este repo (_seed() solo siembra un
    # pedido) -- Recompra NO puede evaluarse (ver
    # test_runner_recompra_aplica_con_orden_anterior_pagada para el caso con
    # orden anterior). Antes (agosto 2026) este total incluía $3 extra de
    # "bcv_completo" que se auto-aplicaba SOLO por el spread entre las
    # tasas default del builder de vinculación (36/40), sin ninguna regla
    # de Diferencial Cambiario configurada aquí -- ese mecanismo se retiró
    # (ver bloque "(c) Diferencial Cambiario" en discounts.py); solo queda
    # el 3% de contado.
    assert bandeja.total_descuentos == Decimal("3.00")
    assert bandeja.total_motor == Decimal("97.00")
    assert bandeja.candidata_a_cierre is False
    origenes = {d.origen for d in bandeja.descuentos_detalle}
    assert origenes == {"contado"}

    # Los equivalentes quedaron congelados en la vinculación.
    vinc = repo.vinculaciones_de_orden("SO1")[0]
    assert vinc.equiv_usd_binance is not None


def test_runner_abono_conciliado_cuenta_aunque_metodo_pago_no_este_sembrado() -> None:
    """Bug real (agosto 2026, orden S00817/Michele Carfora Vigliotti): la
    tabla ``metodos_pago`` -- de referencia, sin panel propio en
    Configuración -- estaba completamente vacía en producción, y
    ``EngineRunner._abonos()`` descartaba TODO abono cuyo
    ``get_metodo_pago()`` no resolviera, dejando la orden sin contado pese a
    tener una Vinculación CONCILIADO real. Ahora un ``metodo_pago`` sin fila
    en la tabla usa un ``MetodoPago`` de reserva (a partir de los datos de
    la propia Vinculación) en vez de descartar el abono.
    """
    repo = _seed()
    # No se siembra ningún MetodoPago para "M1" -- simula la tabla vacía.
    repo._metodos.clear()  # type: ignore[attr-defined]

    resolver = DictPriceResolver({("P1", "USD"): Decimal("100")})
    runner = EngineRunner(repo, resolver, CFG)

    runner.run_all(date(2026, 6, 8))
    bandeja = repo.get_bandeja("SO1")
    assert bandeja is not None
    origenes = {d.origen for d in bandeja.descuentos_detalle}
    assert "contado" in origenes
    assert bandeja.total_descuentos == Decimal("3.00")


def test_runner_vinculacion_pendiente_si_activa_contado() -> None:
    """Decisión de negocio del usuario (auditoría de producción, agosto

    2026): una Vinculación PENDIENTE -- lo que la UI muestra como "en
    proceso de pago" -- SÍ activa Contado. El estado se creó justamente
    para que un pago ya vinculado se asuma como conciliado y la orden se
    pueda facturar CON el descuento; Odoo lo confirma después.

    Revierte la política "solo CONCILIADO" de la Fase 0, que producía un
    bloqueo real: una Vinculación solo llega a CONCILIADO cuando Odoo
    reconcilia el pago contra una FACTURA (o sea, con la orden ya
    facturada), así que el descuento nunca alcanzaba a aplicarse en la
    factura misma.
    """
    from cxc.models import EstadoVinculacion

    repo = _seed()
    # Sobreescribe la única Vinculación de SO1 a PENDIENTE.
    repo.add_vinculacion(
        dataclasses.replace(
            repo.vinculaciones_de_orden("SO1")[0], estado=EstadoVinculacion.PENDIENTE
        )
    )

    resolver = DictPriceResolver({("P1", "USD"): Decimal("100"), ("P1", "BCV"): Decimal("100")})
    runner = EngineRunner(repo, resolver, CFG)

    runner.run_all(date(2026, 6, 8))
    bandeja = repo.get_bandeja("SO1")
    assert bandeja is not None
    # Mismo resultado que con la Vinculación CONCILIADO (ver
    # test_runner_calcula_y_persiste_bandeja).
    assert bandeja.total_descuentos == Decimal("3.00")
    assert {d.origen for d in bandeja.descuentos_detalle} == {"contado"}


def test_runner_contado_retroactivo_tras_conciliar_despues_de_cerrar_ventana() -> None:
    """Fase 0: si la Vinculación se confirma (CONCILIADO) DESPUÉS de que la

    ventana de pago ya cerró en el calendario, el Contado igual debe
    aplicarse -- la ventana se evalúa contra la fecha REAL del abono
    (``hora_pago_confirmada``, que viene de la fecha real del pago), nunca
    contra la fecha en que se recalculó/confirmó. Pedido explícito del
    usuario: el descuento no se pierde por una confirmación tardía de Odoo.

    Pago por el NETO COMPLETO ($97, a diferencia de ``_seed()`` que usa
    $94 -- un pago parcial solo alcanza para el estado "proyectado", que
    SÍ se pierde si la ventana cierra sin liquidar del todo; acá se
    necesita "confirmado" -- liquidado en su totalidad -- para probar el
    caso real de retroactividad).
    """
    repo = _seed()
    repo.upsert_pagos([b.pago("PG1", cliente_id="C1", monto="97", metodo_id="M1")])
    repo.add_vinculacion(
        b.vinculacion(
            "V1",
            pago_id="PG1",
            so_id="SO1",
            monto_aplicado="97",
            moneda_abono=Moneda.USD,
            tipo_tasa_abono=TipoTasa.N_A,
            # Pagado el mismo día de la entrega (2026-06-05) -- bien
            # dentro de la ventana de 3 días hábiles.
            hora=datetime(2026, 6, 5, 10, 0),
        )
    )
    resolver = DictPriceResolver({("P1", "USD"): Decimal("100")})
    runner = EngineRunner(repo, resolver, CFG)

    # Se recalcula casi un mes después de que la ventana cerró -- solo
    # posible en la práctica si Odoo confirmó el pago recién ahí.
    runner.run_all(date(2026, 7, 1))
    bandeja = repo.get_bandeja("SO1")
    assert bandeja is not None
    origenes = {d.origen for d in bandeja.descuentos_detalle}
    assert "contado" in origenes
    assert bandeja.total_descuentos == Decimal("3.00")


def test_runner_recompra_aplica_con_orden_anterior_pagada() -> None:
    """Integración completa run_all -> build_inputs -> motor: con una orden

    anterior del cliente totalmente pagada y dentro de ventana, Recompra sí
    debe aparecer en descuentos_detalle."""
    repo = _seed_con_orden_anterior_pagada()
    resolver = DictPriceResolver({("P1", "USD"): Decimal("100")})
    runner = EngineRunner(repo, resolver, CFG)

    runner.run_all(date(2026, 6, 8))
    bandeja = repo.get_bandeja("SO1")
    assert bandeja is not None
    origenes = {d.origen for d in bandeja.descuentos_detalle}
    assert "recurrencia" in origenes


def test_build_inputs_encuentra_orden_anterior_del_cliente_y_sus_vincs() -> None:
    """build_inputs (no run_all) debe resolver la orden anterior del

    cliente (fecha más reciente antes de esta orden) y traer sus
    vinculaciones -- sin esto Recompra no puede evaluar si quedó pagada."""
    repo = _seed_con_orden_anterior_pagada()
    runner = EngineRunner(repo, DictPriceResolver({("P1", "USD"): Decimal("100")}), CFG)
    inp = runner.build_inputs("SO1", date(2026, 6, 8))
    assert inp is not None
    assert inp.orden_anterior_cliente is not None
    assert inp.orden_anterior_cliente.so_id == "SO0"
    assert len(inp.orden_anterior_cliente_vincs) == 1
    assert inp.orden_anterior_cliente_vincs[0].vinc_id == "V0"


def test_build_inputs_sin_orden_anterior_para_primer_pedido_del_cliente() -> None:
    repo = InMemoryRepository()
    repo.upsert_clientes([b.cliente("C_NUEVO")])
    repo.upsert_ordenes([b.orden("SO_UNICA", cliente_id="C_NUEVO", primera=True)])
    repo.upsert_lineas(
        [b.linea("L1", so_id="SO_UNICA", marca="Sinoco", categoria="*", precio="100")]
    )
    runner = EngineRunner(repo, DictPriceResolver({("P1", "USD"): Decimal("100")}), CFG)
    inp = runner.build_inputs("SO_UNICA", date(2026, 6, 8))
    assert inp is not None
    assert inp.orden_anterior_cliente is None
    assert inp.orden_anterior_cliente_vincs == []


def test_runner_omite_facturada_sin_ningun_abono() -> None:
    """Una orden facturada SIN ningún abono no puede haber ganado nada

    retroactivo -- se sigue saltando, que es lo que acota el trabajo del
    ciclo del daemon a las órdenes que sí pueden tener NC pendiente."""
    repo = _seed()
    orden = repo.get_orden("SO1")
    assert orden is not None
    orden.facturada = True
    repo.upsert_ordenes([orden])
    repo._vinculaciones.clear()  # type: ignore[attr-defined]

    runner = EngineRunner(repo, DictPriceResolver({("P1", "USD"): Decimal("100")}), CFG)
    assert runner.run_all(date(2026, 6, 8)) == []


def test_runner_calcula_facturada_con_abono_conciliado() -> None:
    """Bug real (auditoría de producción, agosto 2026): ``run_all`` saltaba

    TODA orden facturada, pero Contado/Diferencial exigen un abono
    CONCILIADO y una Vinculación solo llega a CONCILIADO cuando Odoo
    reconcilia el pago contra una FACTURA -- es decir, cuando la orden ya
    está facturada. El descuento se ganaba justo cuando la orden se volvía
    invisible, así que la Bandeja 2 (que lee la fila de bandeja para decir
    cuánto emitir en Nota de Crédito) mostraba 0% en todas las facturadas.
    """
    repo = _seed()
    orden = repo.get_orden("SO1")
    assert orden is not None
    orden.facturada = True
    repo.upsert_ordenes([orden])

    runner = EngineRunner(repo, DictPriceResolver({("P1", "USD"): Decimal("100")}), CFG)
    resultados = runner.run_all(date(2026, 6, 8))

    assert len(resultados) == 1
    bandeja = repo.get_bandeja("SO1")
    assert bandeja is not None
    # El descuento de contado se calcula igual que si no estuviera
    # facturada -- es exactamente el monto que debe emitirse como NC.
    assert bandeja.total_descuentos == Decimal("3.00")
    assert {d.origen for d in bandeja.descuentos_detalle} == {"contado"}


def test_run_orden_inexistente_devuelve_none() -> None:
    repo = _seed()
    runner = EngineRunner(repo, DictPriceResolver({("P1", "USD"): Decimal("100")}), CFG)
    assert runner.run_orden("NOPE", date(2026, 6, 8)) is None


def test_run_teoricos_pendientes_calcula_ordenes_facturadas() -> None:
    """A diferencia de run_all, run_teoricos_pendientes SÍ procesa órdenes

    ya facturadas -- el teórico es precisamente el punto de comparación
    que más se necesita ahí (Fase 10)."""
    repo = _seed()
    orden = repo.get_orden("SO1")
    assert orden is not None
    orden.facturada = True
    repo.upsert_ordenes([orden])

    resolver = DictPriceResolver({("P1", "USD"): Decimal("100"), ("P1", "BCV"): Decimal("90")})
    runner = EngineRunner(repo, resolver, CFG)

    procesadas = runner.run_teoricos_pendientes(date(2026, 6, 8))
    assert procesadas == 1

    teorico = repo.get_ventas_teorico("SO1")
    assert teorico is not None
    assert teorico.teorico_usd == Decimal("100.00")
    assert teorico.teorico_ves == Decimal("90.00")


def test_run_teoricos_pendientes_no_recalcula_si_ya_existe_sin_fallback() -> None:
    """Un teórico ya calculado SIN fallback es fijo -- no se recalcula en

    corridas posteriores aunque el resolver cambiaría el resultado."""
    repo = _seed()
    resolver = DictPriceResolver({("P1", "USD"): Decimal("100"), ("P1", "BCV"): Decimal("90")})
    runner = EngineRunner(repo, resolver, CFG)

    assert runner.run_teoricos_pendientes(date(2026, 6, 8)) == 1
    primero = repo.get_ventas_teorico("SO1")
    assert primero is not None
    assert primero.teorico_usd == Decimal("100.00")

    # El resolver "cambia de opinión" -- si se recalculara, daría 999.
    resolver.set_precio("P1", "USD", Decimal("999"))
    assert runner.run_teoricos_pendientes(date(2026, 6, 8)) == 0
    segundo = repo.get_ventas_teorico("SO1")
    assert segundo is not None
    assert segundo.teorico_usd == Decimal("100.00")  # sin cambios


def test_run_teoricos_pendientes_recalcula_si_estaba_marcado_fallback() -> None:
    """Un teórico marcado usa_fallback SÍ se re-verifica en la siguiente

    corrida -- si la lista ya se completó, el nuevo valor se guarda."""
    from cxc.engine.price_resolver import PriceResolver

    class _ResolverConFallback(PriceResolver):
        def __init__(self) -> None:
            self.resuelto_sin_fallback = False

        def precio(self, producto, lista, fecha=None):
            if lista == "BCV" and not self.resuelto_sin_fallback:
                return Decimal("100")  # fallback: usa el mismo que USD
            return Decimal("100") if lista == "USD" else Decimal("90")

        def volumen(self, producto):
            return Decimal("0")

        def fue_fallback(self, producto, lista):
            return lista == "BCV" and not self.resuelto_sin_fallback

    repo = _seed()
    resolver = _ResolverConFallback()
    runner = EngineRunner(repo, resolver, CFG)

    assert runner.run_teoricos_pendientes(date(2026, 6, 8)) == 1
    primero = repo.get_ventas_teorico("SO1")
    assert primero is not None
    assert primero.usa_fallback_ves is True
    assert primero.teorico_ves == Decimal("100.00")  # via fallback

    # La lista BCV "se completa" -- el resolver ya no necesita fallback.
    resolver.resuelto_sin_fallback = True
    assert runner.run_teoricos_pendientes(date(2026, 6, 8)) == 1  # se re-verificó
    segundo = repo.get_ventas_teorico("SO1")
    assert segundo is not None
    assert segundo.usa_fallback_ves is False
    assert segundo.teorico_ves == Decimal("90.00")  # valor real, ya no fallback

    # Ahora que quedó sin fallback, una tercera corrida NO lo vuelve a tocar.
    assert runner.run_teoricos_pendientes(date(2026, 6, 8)) == 0


def test_run_teoricos_pendientes_recalcula_si_cambiaron_las_lineas() -> None:
    """Hallazgo real (agosto 2026, orden S00792): si la orden se edita en

    Odoo DESPUÉS de calcular su teórico (cantidad/producto distinto), el
    teórico debe re-verificarse aunque no haya fallback de precio -- antes
    quedaba pegado a las líneas viejas para siempre."""
    repo = _seed()
    resolver = DictPriceResolver({("P1", "USD"): Decimal("100"), ("P1", "BCV"): Decimal("90")})
    runner = EngineRunner(repo, resolver, CFG)

    assert runner.run_teoricos_pendientes(date(2026, 6, 8)) == 1
    primero = repo.get_ventas_teorico("SO1")
    assert primero is not None
    assert primero.teorico_usd == Decimal("100.00")  # 1 x 100
    assert primero.lineas_fingerprint != ""

    # Sin cambios en las líneas -- no se recalcula (mismo caso que el test
    # de arriba, confirma que el fingerprint no dispara falsos positivos).
    assert runner.run_teoricos_pendientes(date(2026, 6, 8)) == 0

    # Odoo edita la orden: la cantidad de la línea cambia de 1 a 5.
    linea = repo.lineas_de_orden("SO1")[0]
    linea.cantidad = Decimal("5")
    repo.upsert_lineas([linea])

    assert runner.run_teoricos_pendientes(date(2026, 6, 8)) == 1  # se re-verificó
    segundo = repo.get_ventas_teorico("SO1")
    assert segundo is not None
    assert segundo.teorico_usd == Decimal("500.00")  # 5 x 100, ya no 1 x 100
    assert segundo.lineas_fingerprint != primero.lineas_fingerprint

    # Ya con la huella actualizada, una tercera corrida no lo vuelve a tocar.
    assert runner.run_teoricos_pendientes(date(2026, 6, 8)) == 0


def test_run_teoricos_pendientes_recalcula_orden_cancelada_entregada_sin_devolucion() -> None:
    """Corrección del usuario (artefacto de verificación, agosto 2026):

    cancelar una orden en Odoo DESPUÉS de que la mercancía ya salió del
    almacén (y sin ninguna devolución registrada) no revierte la venta
    real -- el teórico se sigue calculando igual que cualquier otra
    orden, no se salta como una cancelación normal.
    """
    repo = _seed()
    orden = repo.get_orden("SO1")
    assert orden is not None
    orden.estado_orden = "cancel"
    repo.upsert_ordenes([orden])
    repo.upsert_entregas([b.entrega("E1", so_id="SO1", tipo="outgoing", estado="done")])

    resolver = DictPriceResolver({("P1", "USD"): Decimal("100"), ("P1", "BCV"): Decimal("90")})
    runner = EngineRunner(repo, resolver, CFG)

    procesadas = runner.run_teoricos_pendientes(date(2026, 6, 8))
    assert procesadas == 1
    teorico = repo.get_ventas_teorico("SO1")
    assert teorico is not None
    assert teorico.teorico_usd == Decimal("100.00")


def test_run_teoricos_pendientes_salta_cancelada_con_devolucion() -> None:
    """Espejo del test anterior: si SÍ hay devolución registrada, la

    cancelación se comporta como siempre -- se salta, ningún teórico se
    calcula para una venta que efectivamente se revirtió.
    """
    repo = _seed()
    orden = repo.get_orden("SO1")
    assert orden is not None
    orden.estado_orden = "cancel"
    orden.tiene_devolucion = True
    repo.upsert_ordenes([orden])
    repo.upsert_entregas([b.entrega("E1", so_id="SO1", tipo="outgoing", estado="done")])

    resolver = DictPriceResolver({("P1", "USD"): Decimal("100"), ("P1", "BCV"): Decimal("90")})
    runner = EngineRunner(repo, resolver, CFG)

    procesadas = runner.run_teoricos_pendientes(date(2026, 6, 8))
    assert procesadas == 0
    assert repo.get_ventas_teorico("SO1") is None


def test_run_teoricos_pendientes_salta_cancelada_sin_entrega() -> None:
    """Una orden cancelada sin ninguna entrega "done" se sigue saltando --

    la excepción solo aplica cuando la mercancía realmente salió."""
    repo = _seed()
    orden = repo.get_orden("SO1")
    assert orden is not None
    orden.estado_orden = "cancel"
    repo.upsert_ordenes([orden])

    resolver = DictPriceResolver({("P1", "USD"): Decimal("100"), ("P1", "BCV"): Decimal("90")})
    runner = EngineRunner(repo, resolver, CFG)

    procesadas = runner.run_teoricos_pendientes(date(2026, 6, 8))
    assert procesadas == 0
    assert repo.get_ventas_teorico("SO1") is None
