"""En que tabla vive una regla, y que pasa si el id esta en dos.

Vigesimocuarta pieza de la Fase 2.4, salida de ``post_toggle_descuento`` (22 de 41
lineas sin cubrir). Ese endpoint prende y apaga una regla de descuento buscandola
**por id en todas las tablas de reglas**, y toca la PRIMERA que responde:

    candidate_names = [req.tabla, *_REGLA_TABLAS_CONOCIDAS]
    for tabla in dict.fromkeys(candidate_names):
        if repo.set_regla_activo(tabla, target_id_str, req.activo):
            return {...}

Si el mismo ``regla_id`` existiera en dos tablas, prende o apaga la de la primera
tabla del orden y devuelve "listo" sin decir que habia otra. Y prender o apagar una
regla de descuento mueve plata en cada orden que la regla alcance.

**Lo que NO se pudo medir, y hay que decirlo.** La pregunta obvia es si hay ids
repetidos entre tablas. Medido el 11-sep-2026 contra el espejo de QA: cero choques
**sobre una poblacion de una sola regla** --``PRIMERA_COMPRA_COMERCIAL_2PCT``, que la
cargo este mismo plan--; las otras cinco tablas estan vacias. Un cero sobre una
poblacion de uno no es un cero verificado, y la base de produccion no se puede
consultar desde aca. Asi que la existencia del choque queda sin medir.

Lo que esta pieza hace es que deje de ser invisible: dice cual tabla gana, cuales
otras tenian el id, y si la eleccion fue ambigua. **No cambia cual gana** --eso
movería montos y la Fase 1 dice que pasa por el visto bueno del usuario.
"""

from __future__ import annotations

from dataclasses import dataclass

# El orden en que ``post_toggle_descuento`` pregunta, despues de la tabla que mando
# el front. Es el que decide quien gana un empate, asi que vive escrito y con nombre.
ORDEN_DE_BUSQUEDA = (
    "DescuentosProntoPago",
    "DescuentosRecompra",
    "DescuentosVolumen",
    "PromocionPrimeraCompra",
    "DescuentosProducto",
    "DescuentosDiferencialCambiario",
)

# Dos alias que apuntan a la MISMA tabla fisica (``descuentos_pronto_pago``). Sin
# esto, un id en esa tabla se contaria como si estuviera en dos.
ALIAS_DE_LA_MISMA_TABLA = {
    "DescuentosMarcaCategoria": "DescuentosProntoPago",
}


def _canonica(tabla: str) -> str:
    return ALIAS_DE_LA_MISMA_TABLA.get(tabla, tabla)


@dataclass(frozen=True)
class EleccionDeTabla:
    """Que tabla se va a tocar, y con cuanta ambiguedad."""

    elegida: str | None
    candidatas: tuple[str, ...]
    tabla_pedida: str

    @property
    def ambigua(self) -> bool:
        """El id vive en mas de una tabla fisica: la eleccion es por orden, no por dato."""
        return len({_canonica(c) for c in self.candidatas}) > 1

    @property
    def gano_la_pedida(self) -> bool:
        """El front sabia donde vivia. Con False, la tabla salio del orden de respaldo."""
        return (
            self.elegida is not None
            and _canonica(self.elegida) == _canonica(self.tabla_pedida)
        )

    @property
    def descartadas(self) -> tuple[str, ...]:
        """Las otras tablas que tambien tenian el id, y que NO se van a tocar."""
        if self.elegida is None:
            return self.candidatas
        gana = _canonica(self.elegida)
        return tuple(c for c in self.candidatas if _canonica(c) != gana)


def elegir_tabla_de_regla(
    *, tabla_pedida: str, tablas_con_el_id: list[str] | tuple[str, ...]
) -> EleccionDeTabla:
    """Cual de las tablas que tienen el id se toca, reproduciendo el orden del endpoint.

    ``tablas_con_el_id`` son las tablas donde ese ``regla_id`` realmente existe. La
    tabla que mando el front va primero; si no esta entre las que tienen el id, se
    cae al orden de respaldo.

    Devuelve ``elegida=None`` cuando el id no esta en ninguna: eso no es un error de
    esta pieza, es la senal de que el endpoint tiene que seguir con el camino de las
    reglas por defecto del diferencial cambiario (las que nunca se persistieron).
    """
    presentes = {_canonica(t) for t in tablas_con_el_id}
    for candidata in (tabla_pedida, *ORDEN_DE_BUSQUEDA):
        if _canonica(candidata) in presentes:
            return EleccionDeTabla(
                elegida=candidata,
                candidatas=tuple(tablas_con_el_id),
                tabla_pedida=tabla_pedida,
            )
    return EleccionDeTabla(
        elegida=None, candidatas=tuple(tablas_con_el_id), tabla_pedida=tabla_pedida
    )


def aviso_de_ambiguedad(eleccion: EleccionDeTabla, regla_id: str) -> str:
    """El texto que viaja en la respuesta cuando la eleccion no salio del dato.

    Cadena vacia cuando no hay nada que avisar, para que el llamador pueda
    preguntarle ``if aviso:`` sin tener que conocer las reglas de la ambiguedad.
    """
    if not eleccion.ambigua:
        return ""
    return (
        f"ATENCION: el regla_id {regla_id!r} existe en mas de una tabla "
        f"({', '.join(eleccion.candidatas)}). Se toco "
        f"{eleccion.elegida!r} porque es la primera del orden de busqueda, NO porque "
        f"el dato lo indique. Sin tocar: {', '.join(eleccion.descartadas)}."
    )
