"""Permisos por usuario: el rol da una base y el administrador la ajusta persona por persona.

Antes un usuario tenía exactamente lo que daba su rol (``ROLES_PERMISOS``). Pedido del
usuario (nov-2026): poder darle a una persona algo puntual --p. ej. que alguien de
tesorería suba estados de cuenta-- sin cambiarle el rol ni darle todo lo de otro rol.

Modelo:
  - El **rol** define la base (``BASE_POR_ROL``).
  - Cada usuario puede tener **ajustes**: permisos concedidos de más o quitados de la base
    (tabla ``usuarios_permisos``). Solo se guarda la diferencia con el rol, así que si
    cambia lo que trae un rol, cambia para todos los que no tengan ajuste en ese punto.
  - **Administrador** tiene todo, siempre: no se le puede quitar nada (evita dejar el
    sistema sin nadie que pueda administrar accesos).
  - El **Inicio** (dashboard) es de todos: es la página a la que se llega al entrar.
  - Cada cambio (rol, permisos, activo) queda en ``accesos_bitacora``, que es solo-append:
    quién cambió qué a quién, con el antes y el después.

Las páginas solo ocultan/permiten la pantalla; las acciones (``bancos.cargar`` etc.) las
exige además la API.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any, TypedDict

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from cxc.db.schema import accesos_bitacora, usuarios_permisos, usuarios_plataforma


class Permiso(TypedDict):
    clave: str
    grupo: str
    nombre: str
    descripcion: str


# Páginas, en el orden del menú. El primer permiso de la lista efectiva es la página a la
# que se redirige al entrar, por eso "dashboard" va primero y nunca se quita.
PAGINAS: tuple[tuple[str, str], ...] = (
    ("dashboard", "Inicio"),
    ("facturacion", "Facturación"),
    ("conciliaciones", "Conciliaciones"),
    ("cobranza", "Cobranza"),
    ("ventas", "Ventas"),
    ("reporte", "Reporte CxC"),
    ("auditoria", "Auditoría"),
    ("inventario", "Inventario"),
    ("configuracion", "Configuración"),
    ("bancos", "Bancos"),
    ("capturas", "Capturas Telegram"),
)
SIEMPRE = frozenset({"dashboard"})

# Las páginas "bancos" y "capturas" no se marcan a mano: se abren si la persona tiene cualquier
# permiso de su grupo. Así no puede quedar alguien con la página abierta y nada que ver en ella.
_PAGINA_DERIVADA = {"bancos": "bancos.", "capturas": "capturas."}

ACCIONES: tuple[Permiso, ...] = (
    {
        "clave": "bancos.cargar",
        "grupo": "Bancos",
        "nombre": "Cargar estados de cuenta",
        "descripcion": "Subir archivos del banco y ver la lista de archivos cargados.",
    },
    {
        "clave": "bancos.ver",
        "grupo": "Bancos",
        "nombre": "Ver saldos y movimientos",
        "descripcion": "Saldos consolidados, movimientos, la cola de pre-conciliación, su "
        "bitácora y las descargas (Excel y CSV para Odoo).",
    },
    {
        "clave": "bancos.conciliar",
        "grupo": "Bancos",
        "nombre": "Pre-conciliar",
        "descripcion": "Generar propuestas (lee Odoo) y aprobar, corregir, rechazar o reabrir.",
    },
    {
        "clave": "capturas.ver",
        "grupo": "Capturas Telegram",
        "nombre": "Ver capturas de Telegram",
        "descripcion": "Ver lo que llega por los bots (contactos, pedidos, pagos) y su estado.",
    },
    {
        "clave": "capturas.gestionar",
        "grupo": "Capturas Telegram",
        "nombre": "Gestionar capturas de Telegram",
        "descripcion": "Reintentar el envío a Odoo de una captura con error o incompleta.",
    },
    {
        "clave": "auditoria.decidir",
        "grupo": "Auditoría",
        "nombre": "Marcar filas de auditoría",
        "descripcion": "Cambiar el estado de una discrepancia (revisado, aprobado, rechazado).",
    },
    {
        "clave": "cobranza.recibo",
        "grupo": "Cobranza",
        "nombre": "Generar recibos",
        "descripcion": "Botón de generar recibo de cobro.",
    },
    {
        "clave": "sistema.recalcular",
        "grupo": "Sistema",
        "nombre": "Recalcular todo",
        "descripcion": "Forzar el recálculo completo de descuentos y conciliación.",
    },
)

CATALOGO: tuple[Permiso, ...] = (
    tuple(
        Permiso(
            clave=clave,
            grupo="Páginas",
            nombre=nombre,
            descripcion="Siempre visible: es la página de entrada."
            if clave in SIEMPRE
            else (
                f"Se abre sola con cualquier permiso de {nombre}."
                if clave in _PAGINA_DERIVADA
                else f"Ver la página {nombre}."
            ),
        )
        for clave, nombre in PAGINAS
    )
    + ACCIONES
)

CLAVES: frozenset[str] = frozenset(p["clave"] for p in CATALOGO)
# Lo que el panel deja marcar/desmarcar (lo demás es fijo o se deriva).
EDITABLES: frozenset[str] = CLAVES - SIEMPRE - frozenset(_PAGINA_DERIVADA)

ROL_ADMIN = "admin"
NOMBRES_ROLES: dict[str, str] = {
    "admin": "Administrador / Gerencia",
    "gerente_ventas": "Supervisor / Gerente de Ventas",
    "tesoreria": "Tesorería y Cobranza",
    "auditor": "Auditoría y Contabilidad",
    "ventas": "Ventas y Comercial",
}

# Páginas que se abren por sí solas (Bancos y Capturas se abren con sus acciones).
_PAGINAS_SIN_BANCOS = frozenset(c for c, _ in PAGINAS if c not in _PAGINA_DERIVADA)

# Base de cada rol: reproduce exactamente lo que cada rol podía hacer antes de que
# existieran los ajustes (páginas de ``ROLES_PERMISOS`` y los ``if rol in (...)`` de la API).
BASE_POR_ROL: dict[str, frozenset[str]] = {
    "admin": CLAVES,
    "gerente_ventas": _PAGINAS_SIN_BANCOS
    | {"cobranza.recibo", "sistema.recalcular", "capturas.ver", "capturas.gestionar"},
    "tesoreria": _PAGINAS_SIN_BANCOS | {"cobranza.recibo", "capturas.ver", "capturas.gestionar"},
    "auditor": _PAGINAS_SIN_BANCOS | {"bancos.ver", "auditoria.decidir", "capturas.ver"},
    "ventas": _PAGINAS_SIN_BANCOS,
}

_ORDEN = {p["clave"]: i for i, p in enumerate(CATALOGO)}


class AccesoInvalido(ValueError):
    """El cambio pedido no se puede aplicar (rol desconocido, permiso inexistente...)."""


def efectivos(rol: str, ajustes: Mapping[str, bool] | None = None) -> list[str]:
    """Permisos de una persona: base del rol, más lo concedido, menos lo quitado.

    Ordenados como el catálogo: primero las páginas en el orden del menú (el primero es
    la página de entrada) y después las acciones.
    """
    if rol == ROL_ADMIN:
        return sorted(CLAVES, key=_ORDEN.__getitem__)
    base = set(BASE_POR_ROL.get(rol, frozenset()))
    for clave, concedido in (ajustes or {}).items():
        if clave not in EDITABLES:
            continue
        if concedido:
            base.add(clave)
        else:
            base.discard(clave)
    base |= SIEMPRE
    for pagina, prefijo in _PAGINA_DERIVADA.items():
        if any(c.startswith(prefijo) for c in base):
            base.add(pagina)
        else:
            base.discard(pagina)
    return sorted(base, key=_ORDEN.__getitem__)


def ajustes_para(rol: str, deseados: Iterable[str]) -> dict[str, bool]:
    """La diferencia entre lo que se quiere y la base del rol: lo único que se guarda."""
    pedidos = set(deseados)
    desconocidos = pedidos - CLAVES
    if desconocidos:
        raise AccesoInvalido(f"Permisos inexistentes: {', '.join(sorted(desconocidos))}")
    if rol not in BASE_POR_ROL:
        raise AccesoInvalido(f"Rol '{rol}' no es válido.")
    if rol == ROL_ADMIN:
        return {}
    base = BASE_POR_ROL[rol] & EDITABLES
    pedidos &= EDITABLES
    return {c: True for c in pedidos - base} | {c: False for c in base - pedidos}


def tiene(usuario: Mapping[str, Any] | None, permiso: str) -> bool:
    """Si la sesión tiene un permiso. Administrador, todo."""
    if not usuario:
        return False
    if usuario.get("rol") == ROL_ADMIN:
        return True
    return permiso in (usuario.get("permisos") or [])


# ------------------------------------------------------------------- persistencia


def ajustes_de(engine: Engine, email: str) -> dict[str, bool]:
    with engine.connect() as c:
        filas = c.execute(
            select(usuarios_permisos.c.permiso, usuarios_permisos.c.concedido).where(
                usuarios_permisos.c.email == email.strip().lower()
            )
        ).all()
    return {f.permiso: bool(f.concedido) for f in filas}


def todos_los_ajustes(engine: Engine) -> dict[str, dict[str, bool]]:
    with engine.connect() as c:
        filas = c.execute(select(usuarios_permisos)).mappings().all()
    out: dict[str, dict[str, bool]] = {}
    for f in filas:
        out.setdefault(f["email"], {})[f["permiso"]] = bool(f["concedido"])
    return out


def cambiar_acceso(
    engine: Engine,
    email: str,
    *,
    por: str,
    rol: str | None = None,
    activo: bool | None = None,
    permisos: Iterable[str] | None = None,
    comentario: str = "",
) -> dict[str, Any]:
    """Aplica un cambio de acceso en una sola transacción y lo deja en la bitácora.

    ``permisos`` es la lista completa que la persona debe tener (lo que se ve marcado en
    el panel); se guarda solo su diferencia con el rol. Si no viene, se conservan los
    ajustes que ya tenía. Devuelve el estado nuevo.
    """
    email = email.strip().lower()
    with engine.begin() as c:
        fila = (
            c.execute(
                select(usuarios_plataforma.c.rol, usuarios_plataforma.c.activo)
                .where(usuarios_plataforma.c.email == email)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if fila is None:
            raise LookupError(email)
        ajustes_antes = {
            f.permiso: bool(f.concedido)
            for f in c.execute(
                select(usuarios_permisos.c.permiso, usuarios_permisos.c.concedido).where(
                    usuarios_permisos.c.email == email
                )
            ).all()
        }
        rol_antes = fila["rol"] or "ventas"
        rol_nuevo = rol if rol is not None else rol_antes
        if rol_nuevo not in BASE_POR_ROL:
            raise AccesoInvalido(f"Rol '{rol_nuevo}' no es válido.")
        activo_nuevo = bool(fila["activo"]) if activo is None else activo
        if permisos is None:
            ajustes_nuevos = {} if rol_nuevo == ROL_ADMIN else dict(ajustes_antes)
        else:
            ajustes_nuevos = ajustes_para(rol_nuevo, permisos)

        antes = {
            "rol": rol_antes,
            "activo": bool(fila["activo"]),
            "permisos": efectivos(rol_antes, ajustes_antes),
        }
        despues = {
            "rol": rol_nuevo,
            "activo": activo_nuevo,
            "permisos": efectivos(rol_nuevo, ajustes_nuevos),
        }
        if antes == despues and ajustes_antes == ajustes_nuevos:
            return despues | {"ajustes": ajustes_nuevos, "cambio": False}

        c.execute(
            update(usuarios_plataforma)
            .where(usuarios_plataforma.c.email == email)
            .values(rol=rol_nuevo, activo=activo_nuevo)
        )
        c.execute(delete(usuarios_permisos).where(usuarios_permisos.c.email == email))
        ahora = datetime.now()
        if ajustes_nuevos:
            c.execute(
                insert(usuarios_permisos),
                [
                    {
                        "email": email,
                        "permiso": k,
                        "concedido": v,
                        "actualizado_por": por,
                        "actualizado_en": ahora,
                    }
                    for k, v in sorted(ajustes_nuevos.items())
                ],
            )
        c.execute(
            insert(accesos_bitacora).values(
                email=email,
                antes=json.dumps(antes, ensure_ascii=False),
                despues=json.dumps(despues, ensure_ascii=False),
                comentario=comentario.strip(),
                usuario=por,
                cambiado_en=ahora,
            )
        )
    return despues | {"ajustes": ajustes_nuevos, "cambio": True}


def bitacora(engine: Engine, email: str | None = None, limite: int = 200) -> list[dict[str, Any]]:
    q = select(accesos_bitacora).order_by(accesos_bitacora.c.cambio_id.desc()).limit(limite)
    if email:
        q = q.where(accesos_bitacora.c.email == email.strip().lower())
    with engine.connect() as c:
        filas = c.execute(q).mappings().all()
    return [
        {
            "cambio_id": f["cambio_id"],
            "email": f["email"],
            "antes": json.loads(f["antes"]),
            "despues": json.loads(f["despues"]),
            "comentario": f["comentario"],
            "usuario": f["usuario"],
            "cambiado_en": f["cambiado_en"].isoformat(timespec="seconds"),
        }
        for f in filas
    ]
