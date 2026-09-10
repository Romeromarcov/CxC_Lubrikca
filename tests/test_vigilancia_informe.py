"""El informe de la corrida diaria no puede tragarse un hallazgo.

Este test existe por un bug que escribí y encontré en el acto: `texto_del_informe`
iteraba una tupla fija de tres nombres de bloque, así que al agregar el bloque
`listas` sus hallazgos **se calculaban, se contaban en el encabezado, y no se
imprimían**. El canal de alertas decía «23 hallazgos» y mostraba 21.

Es exactamente la forma de error que este trabajo persigue —un número que se
computa y no se muestra— y en el peor lugar posible: el instrumento que avisa de
todos los demás. El arreglo fue hacer que el orden sea una preferencia y no un
filtro; esto lo fija para que el próximo bloque que alguien agregue no dependa de
que se acuerde de tocar dos lugares.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def vigilancia():
    """El script cargado como módulo, sin ejecutar su ``main``."""
    ruta = RAIZ / "scripts" / "vigilancia_diaria.py"
    spec = importlib.util.spec_from_file_location("vigilancia_diaria_bajo_prueba", ruta)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def test_todo_hallazgo_contado_aparece_en_el_texto(vigilancia) -> None:
    """La propiedad que importa: encabezado y cuerpo no pueden discrepar."""
    informe = vigilancia.Informe()
    informe.evaluados = 4
    informe.hallazgos = [
        vigilancia.Hallazgo("integridad", "algo_de_integridad", "ALTA", "detalle A"),
        vigilancia.Hallazgo("invariantes", "algo_invariante", "ALTA", "detalle B"),
        vigilancia.Hallazgo("listas", "eleccion_bcv", "ALTA", "detalle C"),
        vigilancia.Hallazgo("un_bloque_que_todavia_no_existe", "futuro", "MEDIA", "detalle D"),
    ]
    texto = vigilancia.texto_del_informe(informe)

    assert "4 hallazgo(s)" in texto
    for hallazgo in informe.hallazgos:
        assert hallazgo.nombre in texto, (
            f"{hallazgo.nombre} se contó en el encabezado y no se imprimió. "
            "Un hallazgo que no se muestra es peor que uno que no se calcula: "
            "el informe afirma haberlo mirado."
        )


def test_un_bloque_desconocido_va_al_final_y_no_al_tacho(vigilancia) -> None:
    informe = vigilancia.Informe()
    informe.evaluados = 2
    informe.hallazgos = [
        vigilancia.Hallazgo("bloque_nuevo", "recien_agregado", "ALTA", "d"),
        vigilancia.Hallazgo("invariantes", "conocido", "ALTA", "d"),
    ]
    texto = vigilancia.texto_del_informe(informe)
    assert "BLOQUE_NUEVO" in texto
    # Los conocidos primero, en el orden declarado; los nuevos detrás.
    assert texto.index("INVARIANTES") < texto.index("BLOQUE_NUEVO")


def test_el_orden_declarado_se_respeta(vigilancia) -> None:
    informe = vigilancia.Informe()
    informe.evaluados = 4
    informe.hallazgos = [
        vigilancia.Hallazgo(b, f"n_{b}", "ALTA", "d") for b in reversed(vigilancia.ORDEN_DE_BLOQUES)
    ]
    texto = vigilancia.texto_del_informe(informe)
    posiciones = [texto.index(b.upper()) for b in vigilancia.ORDEN_DE_BLOQUES]
    assert posiciones == sorted(posiciones), (
        "los bloques salieron en el orden en que llegaron los hallazgos, no en el declarado"
    )


def test_las_altas_van_primero_dentro_del_bloque(vigilancia) -> None:
    """Lo que hay que mirar hoy va arriba."""
    informe = vigilancia.Informe()
    informe.evaluados = 3
    informe.hallazgos = [
        vigilancia.Hallazgo("integridad", "baja_cosa", "BAJA", "d"),
        vigilancia.Hallazgo("integridad", "alta_cosa", "ALTA", "d"),
        vigilancia.Hallazgo("integridad", "media_cosa", "MEDIA", "d"),
    ]
    texto = vigilancia.texto_del_informe(informe)
    assert texto.index("alta_cosa") < texto.index("media_cosa") < texto.index("baja_cosa")


def test_limpio_dice_cuantos_miro_y_que_no_pudo_mirar(vigilancia) -> None:
    """Un informe limpio tiene que decir sobre qué base lo afirma."""
    informe = vigilancia.Informe()
    informe.evaluados = 51
    informe.saltados = ["conciliacion contra Odoo (sin conexion)"]
    texto = vigilancia.texto_del_informe(informe)
    assert "sin hallazgos" in texto
    assert "51 chequeos" in texto
    assert "No evaluado" in texto and "sin conexion" in texto


def test_el_recorte_para_telegram_avisa_que_recorto(vigilancia) -> None:
    informe = vigilancia.Informe()
    informe.evaluados = 200
    informe.hallazgos = [
        vigilancia.Hallazgo("integridad", f"hallazgo_{i}", "ALTA", "x" * 120) for i in range(80)
    ]
    texto = vigilancia.texto_del_informe(informe, para_telegram=True)
    assert len(texto) <= 3800 + 60
    assert "recortado" in texto, "un mensaje truncado sin avisar se lee como el informe completo"
