"""El teórico se compara contra las listas que regían cuando nació la orden.

Regla del usuario (septiembre 2026), textual: "para todos los periodos hay
al menos una lista para pagos en VES y otra lista para pagos en USD [...]
lo que debes hacer es tener un mapeo [...] de cuales son las listas que
aplican para calcular los teóricos VES y USD respectivamente según su
periodo en la que estuvieron vigentes. Y calcular los teóricos en función
de la fecha en la que se generó la orden (la fecha inicial porque a veces
hay órdenes que se tienen que modificar, pero deben mantener su lista
original)".

Antes, una orden sin par configurado caía a ``valid_ves[0]`` /
``valid_usd[0]`` -- la PRIMERA lista de la configuración, que es una de
hoy. Comparar una orden de marzo contra precios de septiembre no dice
nada. Medido contra producción: 289 de las 917 órdenes sin par cambian de
referencia con la vigencia cargada.

Los períodos reales, derivados del uso de las órdenes:

    lista  7  usd  26-feb .. 01-abr        lista  3  ves  03-mar .. 29-jun
    lista  8  usd  06-abr .. 31-ago        lista  4  ves  27-mar .. 03-jul
    lista 11  usd  desde 02-sep            lista  5  ves  24-abr .. 04-sep
    lista 14  usd  desde 04-sep            lista  9  ves  11-ago .. 01-sep
                                           lista 15  ves  desde 04-sep

Son editables desde Configuración -> Mapeo de listas, con sus columnas
"Vigente desde" y "Hasta".
"""

from __future__ import annotations

from datetime import date

from cxc.engine.discounts import listas_vigentes_en

# El mapeo real de producción, recortado a lo que estas pruebas necesitan.
_VIG = {
    "3": {"moneda": "ves", "categoria": "comercial", "desde": "2026-03-03", "hasta": "2026-06-29"},
    "4": {"moneda": "ves", "categoria": "comercial", "desde": "2026-03-27", "hasta": "2026-07-03"},
    "5": {"moneda": "ves", "categoria": "comercial", "desde": "2026-04-24", "hasta": "2026-09-04"},
    "7": {"moneda": "usd", "categoria": "comercial", "desde": "2026-02-26", "hasta": "2026-04-01"},
    "8": {"moneda": "usd", "categoria": "comercial", "desde": "2026-04-06", "hasta": "2026-08-31"},
    "9": {"moneda": "ves", "categoria": "industrial", "desde": "2026-08-11", "hasta": "2026-09-01"},
    "15": {"moneda": "ves", "categoria": "industrial", "desde": "2026-09-04", "hasta": ""},
    "14": {"moneda": "usd", "categoria": "industrial", "desde": "2026-09-04", "hasta": ""},
    # Sin fecha de inicio: a medio configurar.
    "12": {"moneda": "ves", "categoria": "comercial", "desde": "", "hasta": ""},
}


def test_una_orden_de_mayo_usa_las_listas_de_mayo() -> None:
    assert listas_vigentes_en(date(2026, 5, 1), _VIG, "comercial") == ("5", "8")


def test_una_orden_de_marzo_no_usa_las_listas_de_hoy() -> None:
    """La lista USD de marzo era la 7, no la 8 ni la 11."""
    assert listas_vigentes_en(date(2026, 3, 15), _VIG, "comercial")[1] == "7"


def test_entre_dos_listas_de_la_misma_moneda_gana_la_que_arranco_despues() -> None:
    """Los períodos se solapan: una lista nueva convive un tiempo con la
    anterior. En mayo conviven la 3, la 4 y la 5; manda la 5."""
    assert listas_vigentes_en(date(2026, 5, 1), _VIG, "comercial")[0] == "5"
    assert listas_vigentes_en(date(2026, 4, 1), _VIG, "comercial")[0] == "4"


def test_la_categoria_separa_grupos_que_conviven() -> None:
    """En agosto la 9 (industrial, arrancó el 11-ago) le ganaba a la 5
    (comercial) solo por ser más nueva. No son períodos sucesivos, son
    grupos distintos."""
    assert listas_vigentes_en(date(2026, 8, 15), _VIG, "comercial")[0] == "5"
    assert listas_vigentes_en(date(2026, 8, 15), _VIG, "industrial")[0] == "9"


def test_una_lista_sin_fecha_de_inicio_no_se_lleva_toda_la_historia() -> None:
    """Con ``desde`` vacío el rango sería abierto hacia atrás, y una lista a
    medio configurar ganaría fechas anteriores a su existencia -- pasó de
    verdad al cargar la vigencia: las listas de septiembre, que aún no
    tenían órdenes, capturaban marzo."""
    assert listas_vigentes_en(date(2026, 3, 1), _VIG, "comercial")[0] != "12"


def test_antes_de_que_existiera_ninguna_lista_no_se_inventa_una() -> None:
    """``None`` deja que el llamador aplique su respaldo, en vez de que
    esta función invente una referencia."""
    assert listas_vigentes_en(date(2026, 1, 1), _VIG, "comercial") == (None, None)


def test_una_lista_sin_fecha_de_cierre_sigue_vigente() -> None:
    assert listas_vigentes_en(date(2027, 6, 1), _VIG, "industrial") == ("15", "14")


def test_sin_categoria_no_filtra_por_grupo() -> None:
    """Cuando no se sabe el grupo de la orden, la búsqueda no se acota."""
    assert listas_vigentes_en(date(2026, 8, 15), _VIG)[0] == "9"


def test_sin_vigencias_configuradas_no_resuelve_nada() -> None:
    assert listas_vigentes_en(date(2026, 5, 1), {}) == (None, None)
