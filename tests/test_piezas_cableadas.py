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


# --- la guarda de la guarda --------------------------------------------------
#
# El test de arriba pasó con la pieza 21 cableada en código MUERTO. Solo chequea
# que el nombre de la pieza aparezca en algún archivo fuera de `engine/`, y
# `monto_de_descuento_de_linea` aparecía: sus dos llamadores estaban los dos dentro
# de `_leer_descuentos_lineas_odoo`, una función de 95 líneas que nadie invoca.
#
# O sea que la pieza 21 no redujo la duplicación: la movió a un lugar que no corre,
# y el camino vivo se quedó con su copia. Medido el 11-sep-2026.
#
# Lo que sigue cierra ese agujero por el otro lado: una función privada de `app.py`
# que nadie llama es código muerto, y hay que declararla.

APP = RAIZ / "src" / "cxc" / "web" / "app.py"

# Funciones privadas de `app.py` que a propósito no tienen llamador.
SIN_LLAMADOR_EN_APP: dict[str, str] = {
    # Las tres lecturas en vivo que el espejo reemplazó. Las tres preceden a este
    # plan y sus docstrings las posicionan como la referencia del parity check, así
    # que borrarlas es decisión del usuario, no de quien escribe este test.
    "_leer_descuentos_lineas_odoo": (
        "lectura en vivo superada por _descuentos_lineas_desde_espejo, y con la "
        "regla vieja (solo nombre de producto); borrarla es decisión del usuario"
    ),
    "_leer_notas_credito_odoo": (
        "lectura en vivo superada por _facturacion_por_so_desde_espejo; queda como "
        "referencia del parity check"
    ),
    "_leer_notas_debito_odoo": (
        "lectura en vivo superada por _facturacion_por_so_desde_espejo; queda como "
        "referencia del parity check"
    ),
}


def _privadas_de_app() -> list[str]:
    """Funciones de nivel de módulo de `app.py` con nombre privado.

    Solo las de nivel de módulo: una función anidada vive dentro de su madre y su
    alcance ya está acotado por ella.
    """
    arbol = ast.parse(APP.read_text(encoding="utf-8"))
    return [
        n.name
        for n in arbol.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name.startswith("_")
    ]


def _nombres_llamados_en_app() -> set[str]:
    """Todo nombre que aparece como `algo(...)` en `app.py`, más los referenciados.

    Se cuentan las referencias sueltas (pasar la función como argumento, meterla en
    un dict) además de las llamadas: usarla así también es usarla.
    """
    arbol = ast.parse(APP.read_text(encoding="utf-8"))
    usados: set[str] = set()
    for n in ast.walk(arbol):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            usados.add(n.func.id)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            usados.add(n.id)
        elif isinstance(n, ast.Attribute):
            usados.add(n.attr)
    return usados


def _usados_desde_afuera_de_app() -> set[str]:
    """Nombres que otro módulo IMPORTA de `cxc.web.app`, o llama como `app.algo(...)`.

    **Por qué con AST y no buscando el nombre en el texto**, que es lo que hacía la
    primera versión de esta guarda por el lado del motor. Las dos formas ingenuas
    fallan, cada una para un lado:

    - buscar `f"app.{fn}"` da FALSOS POSITIVOS: el 11-sep-2026 marcó
      `_primer_id_activo` como huérfano cuando lo usan cuatro archivos, porque lo
      importan con `from cxc.web.app import _primer_id_activo` y nunca escriben
      `app._primer_id_activo`.
    - buscar el nombre suelto da FALSOS NEGATIVOS, que es peor: el nombre de
      `_leer_descuentos_lineas_odoo` aparece en comentarios de `models.py` y de
      `odoo/client.py` diciendo que es "la que usa Ventas", así que una búsqueda
      textual la habría declarado viva. Y está muerta -- es el caso que esta guarda
      existe para atrapar.

    Un import es una declaración de uso; una mención en un comentario no lo es.
    """
    usados: set[str] = set()
    for carpeta in ("src/cxc", "scripts", "escenarios", "tests"):
        for archivo in (RAIZ / carpeta).rglob("*.py"):
            if "__pycache__" in archivo.parts or archivo.samefile(APP):
                continue
            try:
                arbol = ast.parse(archivo.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for n in ast.walk(arbol):
                if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("web.app"):
                    usados.update(a.name for a in n.names)
                elif isinstance(n, ast.Attribute):
                    # `app.algo` / `cxc.web.app.algo`, la otra forma de usarla.
                    usados.add(n.attr)
    return usados


def test_ninguna_funcion_privada_de_app_queda_sin_llamador_sin_declararlo() -> None:
    """El caso que este test habría atrapado: cablear una pieza en código muerto.

    No es lo mismo que el test de arriba. Ése pregunta "¿alguien llama a la pieza
    del motor?"; éste pregunta "¿alguien llama al que la llama?". Hacen falta los
    dos, porque la pieza 21 pasó el primero y falló el segundo.
    """
    definidas = _privadas_de_app()
    # Un nombre definido dos veces (un decorador, un overload) no es el caso de acá.
    usados = _nombres_llamados_en_app() | _usados_desde_afuera_de_app()
    huerfanas = sorted(
        fn
        for fn in definidas
        if definidas.count(fn) == 1 and fn not in usados and fn not in SIN_LLAMADOR_EN_APP
    )
    assert not huerfanas, (
        "Estas funciones privadas de app.py no tienen llamador. O se borran, o se "
        f"declaran en SIN_LLAMADOR_EN_APP con la razón: {huerfanas}"
    )


def test_la_declaracion_de_app_no_guarda_nombres_que_ya_no_existen() -> None:
    """Misma razón que su hermana del motor: una excepción huérfana es un permiso
    en blanco para la próxima función que se llame igual."""
    reales = set(_privadas_de_app())
    fantasmas = sorted(set(SIN_LLAMADOR_EN_APP) - reales)
    assert not fantasmas, f"Entradas que ya no corresponden a ninguna función: {fantasmas}"


def test_la_regla_del_descuento_de_linea_vive_en_UN_solo_lugar() -> None:
    """El camino vivo y la pieza del motor tienen que ser el mismo código.

    Antes de la pieza 23, `_descuentos_lineas_desde_espejo` tenía la regla inline y
    `descuento_de_linea` no existía. Este test fija que el camino vivo delegue: si
    alguien vuelve a escribir `disc_pct > 0` ahí, falla.
    """
    fuente = APP.read_text(encoding="utf-8")
    inicio = fuente.index("def _descuentos_lineas_desde_espejo")
    fin = fuente.index("def _productos_despachados_desde_espejo")
    cuerpo = fuente[inicio:fin]
    assert cuerpo.count("descuento_de_linea(") == 2, (
        "las dos mitades (orden y factura) tienen que llamar a la pieza del motor"
    )
    assert "disc_pct" not in cuerpo, "la regla volvió a estar inline en el camino vivo"
