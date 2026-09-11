"""Una extracción sin cablear no reduce la deuda: la duplica.

Pasó el 11-sep-2026 con la pieza 16. Extraje la cuenta de los promedios de tasa a
`engine/promedios_tasas.py` con 31 tests y **no cambié el endpoint para que la
use**: `_get_tasas_promedios_sync` siguió con su copia inline, así que el proyecto
quedó con la misma lógica en dos lugares y una de ellas sin cobertura. El
instrumento de cobertura lo delató —esa función seguía con 54 de 75 líneas sin
cubrir después de la extracción— pero eso fue suerte de haber vuelto a medir.

Este test convierte esa suerte en una guarda: una función pública del motor que
nadie llama es sospechosa, y hay que declarar por qué.

**La excepción, que es la mitad del asunto.** Buena parte de las piezas de este plan
son **instrumentos**: dan las dos lecturas de un número para que el usuario decida
cuál aplicar, y por diseño **el sistema no las llama todavía** — aplicarlas movería
montos, y la regla de la Fase 1 dice que eso pasa por su visto bueno. Un test que
exigiera un llamador para todas empujaría a cablear cosas que no se deben cablear.

Así que la lista de abajo es explícita: cada nombre viene con la razón por la que no
tiene llamador. Si alguien extrae una pieza y se olvida de cablearla, el test falla
y lo obliga a elegir: cablearla, o anotarla acá diciendo por qué no.
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ENGINE = RAIZ / "src" / "cxc" / "engine"

# Funciones públicas del motor que a propósito NO tienen llamador en la aplicación.
# La clave es ``modulo.py::funcion`` y el valor es la razón.
SIN_LLAMADOR_A_PROPOSITO: dict[str, str] = {
    # --- instrumentos: las dos lecturas de un número, esperando una decisión ---
    "acotar_pago.py::repartir_por_orden": "reparto A de los pagos sobreaplicados; el usuario elige",
    "acotar_pago.py::repartir_proporcional": "reparto B de los mismos; el usuario elige",
    "acotar_pago.py::diagnostico_de_reparto": "compara los dos repartos para que se pueda elegir",
    "reversadas.py::abono_implicito": "las dos lecturas de una factura anulada",
    "reversadas.py::diagnostico_de_reversadas": "mide el hueco de las 17 órdenes; no lo corrige",
    "listas.py::primero_crudo": "la lectura vieja de la elección de lista, para poder compararla",
    "saldos.py::diagnostico_de_saldos": "expone la mina del saldo sin corregirla",
    "saldos.py::es_obsequio": "separa el cero deliberado del cero por no resolver el precio",
    "saldos.py::diagnostico_de_obsequios": "separa los 107,42 autorizados de los 8.611,64 que no",
    "pagada_en_odoo.py::pagada_por_residual": (
        "la regla del residual; la aplicación compara contra TOLERANCIA_RESIDUAL, "
        "que es la constante compartida -- envolver `x <= CONST` en una llamada sería peor"
    ),
    "promedios_tasas.py::diagnostico_de_promedios": (
        "compara las ventanas solapadas contra las disjuntas; el endpoint usa `promediar`"
    ),
    "pagos_duplicados.py::clave_de_pago": (
        "la usa `detectar_pagos_duplicados`, que sí está cableada"
    ),
    # --- anteriores a este plan, cada una con su motivo ---
    # El calendario de días hábiles lo consume ``discounts.py`` para la ventana de
    # contado; la aplicación nunca pregunta por un día hábil directamente.
    "business_days.py::es_dia_habil": "calendario interno del motor, lo usa discounts.py",
    "business_days.py::sumar_dias_habiles": "calendario interno del motor, lo usa discounts.py",
    "business_days.py::fin_ventana_contado": "calendario interno del motor, lo usa discounts.py",
    "discounts.py::limite_ventana_pago": (
        "helper de la ventana de contado, usado dentro de discounts"
    ),
    "discounts.py::listas_vigentes_en": "resuelve la lista vigente, usado dentro de discounts",
    "effective_dating.py::regla_recurrencia_vigente": (
        "elige la regla de recurrencia por fecha, y la llama el motor de descuentos"
    ),
    "equivalents.py::congelar_en_vinculacion": "camino del motor, no del endpoint",
    "equivalents.py::es_ruta_bcv_pura": (
        "clasifica la ruta de pago, y la consume el cálculo de equivalentes"
    ),
    "equivalents.py::es_pago_mixto": (
        "detecta el pago en dos monedas, y lo consume el cálculo de equivalentes"
    ),
    "historical_pricing.py::cargar_mapa_historico": "lo usa el runner del motor",
    "reportes_historicos.py::fetch_out_invoices_due_by": (
        "reporte histórico, invocado por su propio script"
    ),
    "reportes_historicos.py::build_point_in_time_state": (
        "arma el estado a una fecha de corte, para los reportes históricos"
    ),
    "reportes_historicos.py::paid_ratio_by_cutoff": (
        "razón de cobro por fecha de corte, para los reportes históricos"
    ),
    "reportes_historicos.py::vendedores_por_so": (
        "índice de vendedor por orden, para los reportes históricos"
    ),
    "reportes_historicos.py::vendedores_por_cliente": (
        "índice de vendedor por cliente, para los reportes históricos"
    ),
}


def _consumidores() -> str:
    """Todo el código que podría llamar al motor, menos el motor mismo."""
    partes = []
    for carpeta in ("src/cxc", "scripts", "escenarios"):
        for p in (RAIZ / carpeta).rglob("*.py"):
            if "engine" in p.parts or "__pycache__" in p.parts:
                continue
            partes.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(partes)


def _publicas_del_motor() -> list[tuple[str, str]]:
    salida = []
    for mod in sorted(ENGINE.glob("*.py")):
        if mod.name == "__init__.py":
            continue
        arbol = ast.parse(mod.read_text(encoding="utf-8"))
        for n in arbol.body:
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and not n.name.startswith("_"):
                salida.append((mod.name, n.name))
    return salida


def test_toda_pieza_del_motor_o_esta_cableada_o_dice_por_que_no() -> None:
    """El caso que este test habría atrapado: `promediar` extraída y sin cablear."""
    consumidores = _consumidores()
    sin_declarar = [
        f"{mod}::{fn}"
        for mod, fn in _publicas_del_motor()
        if fn not in consumidores and f"{mod}::{fn}" not in SIN_LLAMADOR_A_PROPOSITO
    ]
    assert not sin_declarar, (
        "Estas piezas del motor no tienen llamador y no están declaradas como "
        "instrumentos. O se cablean, o se agregan a SIN_LLAMADOR_A_PROPOSITO con la "
        f"razón: {sin_declarar}"
    )


def test_la_lista_de_excepciones_no_se_queda_con_nombres_que_ya_no_existen() -> None:
    """Una excepción que sobrevive a su función es permiso para cualquier cosa.

    Si alguien borra o renombra una pieza y la entrada queda, la próxima función con
    ese nombre hereda el permiso sin que nadie lo haya decidido.
    """
    reales = {f"{mod}::{fn}" for mod, fn in _publicas_del_motor()}
    fantasmas = sorted(set(SIN_LLAMADOR_A_PROPOSITO) - reales)
    assert not fantasmas, f"Entradas que ya no corresponden a ninguna función: {fantasmas}"


def test_cada_excepcion_trae_una_razon_de_verdad() -> None:
    """Una razón vacía o de dos palabras no es una razón."""
    flojas = [k for k, v in SIN_LLAMADOR_A_PROPOSITO.items() if len(v.split()) < 4]
    assert not flojas, f"Estas excepciones necesitan una razón escrita: {flojas}"
