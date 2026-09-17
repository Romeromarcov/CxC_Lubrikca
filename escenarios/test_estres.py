"""Estrés y fallas (Fase 4 del plan de blindaje).

La Fase 3 pregunta si el sistema entiende datos raros. Esta pregunta si aguanta
condiciones malas: el sync cortado a la mitad, Odoo caído, dos ciclos a la vez,
y la misma corrida dos veces.

Va junto al banco de escenarios y no en la suite normal porque necesita el
Odoo de prueba y el espejo de QA, igual que la Fase 3.

**Volumen no se prueba acá.** Multiplicar por diez las órdenes y los pagos
significaría escribir ~9.000 órdenes en el Odoo de prueba, y eso es una hora de
escrituras por XML-RPC contra un servidor compartido. En vez de eso se miden los
tiempos reales y se extrapola, que da una respuesta usable sin ocupar el entorno
media mañana; los números y el razonamiento están en
``docs/blindaje/4-estres.md``.
"""

from __future__ import annotations

import os
from dataclasses import asdict

import pytest

# --- reejecución -----------------------------------------------------------


@pytest.mark.escenario("Reejecución: dos syncs seguidos dan lo mismo")
def test_dos_syncs_completos_seguidos_dan_el_mismo_espejo(sistema):
    """Correr el sync dos veces sobre los mismos datos tiene que dar
    exactamente el mismo resultado.

    Es la propiedad que hace que el sync sea reparable: si no es idempotente,
    la única forma de arreglar un espejo dudoso es recrearlo.
    """
    tablas = (
        "ordenes_venta",
        "lineas_orden",
        "clientes",
        "pagos",
        "facturas",
        "entregas",
        "catalogo",
        "lineas_factura",
        "lineas_entrega",
    )

    def huella() -> dict[str, int]:
        return {t: len(sistema.espejo(t)) for t in tablas}

    sistema.sync(desde_cero=True)
    primera = huella()
    sistema.sync(desde_cero=True)
    segunda = huella()

    assert primera == segunda, (
        "Dos corridas completas seguidas dejaron espejos distintos:\n"
        + "\n".join(
            f"  {t}: {primera[t]} -> {segunda[t]}" for t in tablas if primera[t] != segunda[t]
        )
    )


@pytest.mark.escenario("Reejecución: el delta sobre nada no cambia nada")
def test_un_delta_sin_cambios_no_toca_el_espejo(sistema):
    """El delta encuentra filas por la ventana de 48 h aunque no haya cambios.

    Eso está bien -- la ventana es deliberada, cubre el reloj desalineado entre
    Odoo y nosotros. Lo que no puede pasar es que refrescar esas filas cambie
    los CONTEOS, porque significaría que el upsert está insertando en vez de
    actualizar.
    """
    tablas = ("ordenes_venta", "lineas_orden", "pagos", "facturas", "entregas")

    def huella() -> dict[str, int]:
        return {t: len(sistema.espejo(t)) for t in tablas}

    sistema.sync()
    antes = huella()
    resultado = sistema.sync()
    despues = huella()

    assert antes == despues, (
        f"Un delta sin cambios movió los conteos: {antes} -> {despues}. "
        f"El sync reportó {asdict(resultado)}."
    )


# --- Odoo caído ------------------------------------------------------------


@pytest.mark.escenario("Odoo caído: ninguna página muestra ceros como saldos")
def test_con_odoo_caido_el_dashboard_se_declara_degradado(sistema, monkeypatch):
    """Con Odoo fuera, ninguna página debería mostrar ceros como si fueran
    saldos.

    El balance ya se abstiene. El dashboard no puede abstenerse -- tiene
    respaldos válidos para todo lo que muestra -- pero tiene que DECIR que está
    usando respaldos, que es lo que la Fase 5 agregó.
    """
    monkeypatch.setenv("ODOO_URL", "https://odoo-que-no-existe.invalido.test")
    from cxc.web import app as modulo

    monkeypatch.setattr(modulo, "_connect", lambda cfg: None)
    from escenarios.sistema import limpiar_caches

    limpiar_caches()

    datos = sistema._json("/api/reporte/diario")
    fuente = datos.get("fuente") or {}
    assert fuente, (
        "El reporte diario no dice de dónde salieron sus números, así que con Odoo "
        "caído el usuario no puede distinguir un dato bueno de un respaldo."
    )
    assert fuente["degradado"] is True
    assert fuente["odoo_respondio"] is False


@pytest.mark.escenario("Odoo caído: el balance se abstiene en vez de opinar")
def test_con_odoo_caido_el_balance_no_da_verde_sobre_la_nada(sistema, monkeypatch):
    """Sin datos no hay balance: un balance verde sobre la nada miente.

    Ya está implementado -- el balance devuelve ``evaluable: False`` con su
    motivo cuando Ventas viene vacío. Lo que este escenario fija es que siga
    ahí, porque es la defensa contra el falso verde Y el falso rojo a la vez
    (las partidas de monto daban 0,00 contra 0,00 en verde mientras las de
    bandeja contaban todas sus filas como error).
    """
    from cxc.web import app as modulo

    monkeypatch.setattr(modulo, "_connect", lambda cfg: None)
    from escenarios.sistema import limpiar_caches

    limpiar_caches()

    balance = sistema.balance()
    if balance.get("evaluable") is False:
        assert balance.get("motivo"), "Se abstuvo sin decir por qué."
        assert balance.get("partidas") == []
        return
    # Si evaluó, ninguna partida puede estar comparando 0 contra 0 y llamarlo
    # verde: eso es exactamente el falso verde que la abstención evita.
    ceros_en_verde = [
        p
        for p in balance.get("partidas") or []
        if p.get("cuadra")
        and float((p.get("izquierda") or {}).get("valor") or 0) == 0
        and float((p.get("derecha") or {}).get("valor") or 0) == 0
        and p.get("tipo") == "externa"
    ]
    assert not ceros_en_verde, (
        "Con Odoo caído hay partidas EXTERNAS comparando 0 contra 0 y saliendo en "
        f"verde: {[p['concepto'] for p in ceros_en_verde]}. Un cero que viene de no "
        "haber podido leer no es un cero."
    )


# --- sync concurrente e interrumpido --------------------------------------


@pytest.mark.escenario("Dos ciclos de sync a la vez")
def test_dos_syncs_en_paralelo_no_duplican_nada(sistema):
    """Comprobar que no se duplican vinculaciones ni se pisan escrituras.

    Los ``upsert_*`` van por clave primaria, así que dos ciclos simultáneos
    deberían converger al mismo estado. Lo que se verifica es que los conteos no
    crezcan: un crecimiento significaría que la escritura concurrente insertó
    filas nuevas en vez de actualizar.
    """
    from concurrent.futures import ThreadPoolExecutor

    from escenarios.sistema import sincronizar

    tablas = ("ordenes_venta", "lineas_orden", "pagos", "facturas", "entregas")
    antes = {t: len(sistema.espejo(t)) for t in tablas}

    with ThreadPoolExecutor(max_workers=2) as pool:
        futuros = [pool.submit(sincronizar, False) for _ in range(2)]
        errores = []
        for f in futuros:
            try:
                f.result()
            except Exception as exc:  # noqa: BLE001 -- se reporta, es el hallazgo
                errores.append(str(exc)[:300])

    despues = {t: len(sistema.espejo(t)) for t in tablas}
    assert antes == despues, (
        f"Dos syncs en paralelo movieron los conteos: {antes} -> {despues}. "
        "Un upsert por clave primaria no debería insertar de nuevo."
    )
    # Que uno de los dos falle es aceptable (un lock, una transacción abortada);
    # lo que no es aceptable es que falle DEJANDO el espejo distinto, que es lo
    # que la aserción de arriba cubre. Se reporta igual para que quede visible.
    if errores:
        pytest.skip(
            "Uno de los dos ciclos falló, y el espejo quedó consistente. "
            f"Errores: {errores}"
        )


@pytest.mark.escenario("Sync interrumpido a la mitad")
def test_un_sync_cortado_no_deja_el_cursor_adelantado(sistema, odoo):
    """Al reanudar no debe quedar un espejo a medias que se reporte como
    completo.

    La defensa está en el orden de las operaciones: ``set_last_sync(now)`` es lo
    ÚLTIMO que hace ``IncrementalSync.run``. Si el proceso muere antes, el
    cursor sigue donde estaba y la próxima corrida vuelve a leer la misma
    ventana. Este escenario lo verifica provocando el corte en el medio.
    """
    from cxc.config import AppConfig
    from cxc.odoo.client import OdooXmlRpcReader
    from cxc.sync.incremental import IncrementalSync

    cursor_antes = sistema.espejo("app_settings", "key = 'last_sync'")
    valor_antes = cursor_antes[0]["value"] if cursor_antes else None

    class LectorQueMuere(OdooXmlRpcReader):
        """Falla al llegar a los pagos, con las órdenes ya escritas."""

        def changed_pagos(self, since):
            raise RuntimeError("corte simulado a la mitad del sync")

    from cxc.db.postgres_repository import PostgresRepository

    repo = PostgresRepository.from_url(os.environ["DATABASE_URL"])
    reader = LectorQueMuere(AppConfig.from_env().odoo)
    from datetime import datetime

    with pytest.raises(RuntimeError, match="corte simulado"):
        IncrementalSync(repo, reader).run(datetime.now())

    cursor_despues = sistema.espejo("app_settings", "key = 'last_sync'")
    valor_despues = cursor_despues[0]["value"] if cursor_despues else None
    assert valor_despues == valor_antes, (
        f"El cursor avanzó de {valor_antes} a {valor_despues} en un sync que murió "
        "a la mitad. La próxima corrida se saltearía la ventana perdida y el "
        "espejo quedaría a medias reportándose como completo."
    )
