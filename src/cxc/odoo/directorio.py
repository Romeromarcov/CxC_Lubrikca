"""Consulta de solo lectura a Odoo: directorio de contactos y plan de cuentas.

Provee dos capacidades para el ecosistema de CxC y los bots de Telegram:

1. **Directorio y clasificación de contactos (``res.partner``)**:
   Consulta los contactos activos en Odoo (excluyendo sub-direcciones de entrega o facturación)
   y los clasifica determinísticamente según su rol comercial en Odoo:
   - ``prospecto``: ``customer_rank == 0`` y ``supplier_rank == 0`` (incluye los contactos en
     borrador creados desde Telegram con marca ``BOT-`` o ``[Borrador Telegram]``).
   - ``cliente``: ``customer_rank > 0`` y ``supplier_rank == 0``.
   - ``proveedor``: ``supplier_rank > 0`` y ``customer_rank == 0``.
   - ``ambos``: ``customer_rank > 0`` y ``supplier_rank > 0`` (cliente y proveedor a la vez).

2. **Plan de cuentas (``account.account``) y propuesta de clasificación de compras/gastos**:
   Lee el plan contable activo de Odoo y propone qué cuenta contable usar para cada categoría
   de gasto menor (``alimentación``, ``combustible``, ``papelería``, ``mantenimiento``,
   ``transporte``, ``servicios``, ``otros``) y de ``factura_proveedor`` (compras de inventario,
   insumos o servicios facturados), además de sugerir la clasificación para cualquier concepto
   libre.

Nunca escribe en Odoo: usa únicamente la conexión de solo lectura (``OdooConfig.from_env()``).
"""

from __future__ import annotations

import logging
import unicodedata
import xmlrpc.client
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cxc.config import OdooConfig

logger = logging.getLogger("cxc.odoo.directorio")

ExecuteFn = Callable[[str, str, list[Any], dict[str, Any]], Any]

CLASIFICACIONES_CONTACTO = (
    "prospecto",
    "cliente",
    "proveedor",
    "ambos",
    "borrador_telegram",
)

ETIQUETAS_CLASIFICACION: dict[str, str] = {
    "prospecto": "Prospecto",
    "cliente": "Cliente",
    "proveedor": "Proveedor",
    "ambos": "Cliente y proveedor",
}

TIPOS_CUENTA_ODOO: dict[str, tuple[str, str]] = {
    "expense": ("Gasto operativo", "gastos"),
    "expense_direct_cost": ("Costo directo / Compras", "gastos"),
    "expense_depreciation": ("Depreciación", "gastos"),
    "liability_payable": ("Cuentas por pagar (Proveedores)", "pasivo"),
    "liability_current": ("Pasivo corriente", "pasivo"),
    "liability_non_current": ("Pasivo no corriente", "pasivo"),
    "liability_credit_card": ("Tarjeta de crédito", "pasivo"),
    "asset_receivable": ("Cuentas por cobrar (Clientes)", "activo"),
    "asset_cash": ("Banco y efectivo", "activo"),
    "asset_current": ("Activo corriente / Inventario", "activo"),
    "asset_prepayments": ("Anticipos / Prepagados", "activo"),
    "asset_fixed": ("Activo fijo / Propiedad y equipo", "activo"),
    "asset_non_current": ("Activo no corriente", "activo"),
    "income": ("Ingreso operativo", "ingreso"),
    "income_other": ("Otros ingresos", "ingreso"),
    "equity": ("Patrimonio", "otros"),
    "equity_unaffected": ("Resultado del ejercicio", "otros"),
    "off_balance": ("Cuentas de orden", "otros"),
}


class OdooDirectorioError(Exception):
    """Fallo al consultar el directorio o el plan de cuentas en Odoo."""


def _sin_acentos(texto: str) -> str:
    normalizado = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in normalizado if unicodedata.category(c) != "Mn")


def _m2o_id(value: Any) -> int | None:
    if isinstance(value, list | tuple) and value:
        try:
            return int(value[0])
        except (ValueError, TypeError):
            return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _m2o_nombre(value: Any) -> str:
    if isinstance(value, list | tuple) and len(value) > 1:
        return str(value[1] or "").strip()
    if value in (False, None):
        return ""
    return str(value).strip()


def _limpiar_str(value: Any) -> str:
    if value in (False, None):
        return ""
    return str(value).strip()


def conectar_lectura(cfg: OdooConfig, timeout: float = 30.0) -> ExecuteFn:  # pragma: no cover
    """Abre conexión XML-RPC de solo lectura con timeout."""
    base: type[xmlrpc.client.Transport] = (
        xmlrpc.client.SafeTransport if cfg.url.startswith("https") else xmlrpc.client.Transport
    )

    class _ConTimeout(base):  # type: ignore[valid-type, misc]
        def make_connection(self, host: Any) -> Any:
            conn = super().make_connection(host)
            conn.timeout = timeout
            return conn

    try:
        common = xmlrpc.client.ServerProxy(f"{cfg.url}/xmlrpc/2/common", transport=_ConTimeout())
        uid = common.authenticate(cfg.db, cfg.username, cfg.password, {})
    except (OSError, xmlrpc.client.ProtocolError) as e:
        raise OdooDirectorioError("No se pudo conectar con Odoo.") from e
    if not uid:
        raise OdooDirectorioError("Odoo rechazó la credencial de lectura.")
    models = xmlrpc.client.ServerProxy(f"{cfg.url}/xmlrpc/2/object", transport=_ConTimeout())

    def execute(model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        return models.execute_kw(cfg.db, uid, cfg.password, model, method, args, kwargs)

    return execute


# ── Contactos (res.partner) ──────────────────────────────────────────────────


@dataclass(frozen=True)
class ContactoOdoo:
    id: int
    nombre: str
    rif: str
    email: str
    telefono: str
    ciudad: str
    direccion: str
    es_empresa: bool
    customer_rank: int
    supplier_rank: int
    clasificacion: str
    clasificacion_etiqueta: str
    es_borrador_telegram: bool
    referencia: str
    vendedor: str
    notas: str
    plus_code: str = ""
    latitud: float | None = None
    longitud: float | None = None

    def a_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nombre": self.nombre,
            "rif": self.rif,
            "email": self.email,
            "telefono": self.telefono,
            "ciudad": self.ciudad,
            "direccion": self.direccion,
            "plus_code": self.plus_code,
            "latitud": self.latitud,
            "longitud": self.longitud,
            "es_empresa": self.es_empresa,
            "customer_rank": self.customer_rank,
            "supplier_rank": self.supplier_rank,
            "clasificacion": self.clasificacion,
            "clasificacion_etiqueta": self.clasificacion_etiqueta,
            "es_borrador_telegram": self.es_borrador_telegram,
            "referencia": self.referencia,
            "vendedor": self.vendedor,
            "notas": self.notas,
        }


def clasificar_contacto(customer_rank: int, supplier_rank: int) -> str:
    """Clasifica un ``res.partner`` según sus rangos de cliente y proveedor en Odoo."""
    es_cliente = customer_rank > 0
    es_proveedor = supplier_rank > 0
    if es_cliente and es_proveedor:
        return "ambos"
    if es_proveedor:
        return "proveedor"
    if es_cliente:
        return "cliente"
    return "prospecto"


def detectar_borrador_telegram(ref: str, comment: str) -> bool:
    """Indica si el contacto fue creado como borrador por los bots de Telegram."""
    ref_u = ref.upper()
    comment_u = comment.upper()
    return ref_u.startswith("BOT-") or "[BORRADOR TELEGRAM]" in comment_u


def es_subdireccion_entrega(registro: Mapping[str, Any]) -> bool:
    """Descarta direcciones hijas (entrega/factura) para no inflar la lista de prospectos."""
    parent_id = _m2o_id(registro.get("parent_id"))
    tipo = _limpiar_str(registro.get("type")).lower()
    return bool(parent_id is not None and tipo and tipo != "contact")


def mapear_contacto(registro: Mapping[str, Any]) -> ContactoOdoo:
    """Convierte un dict crudo de ``res.partner`` en ``ContactoOdoo``."""
    c_rank = max(0, int(registro.get("customer_rank") or 0))
    s_rank = max(0, int(registro.get("supplier_rank") or 0))
    clasificacion = clasificar_contacto(c_rank, s_rank)
    telefono = _limpiar_str(registro.get("phone")) or _limpiar_str(registro.get("mobile"))
    ref = _limpiar_str(registro.get("ref"))
    notas = _limpiar_str(registro.get("comment"))
    company_type = _limpiar_str(registro.get("company_type")).lower()
    es_empresa = bool(registro.get("is_company")) or company_type == "company"
    raw_lat = registro.get("partner_latitude")
    raw_lon = registro.get("partner_longitude")
    lat_val: float | None = None
    lon_val: float | None = None
    if raw_lat not in (None, False, "") and raw_lon not in (None, False, ""):
        try:
            f_lat, f_lon = float(str(raw_lat)), float(str(raw_lon))
            if not (f_lat == 0.0 and f_lon == 0.0):
                lat_val, lon_val = f_lat, f_lon
        except (TypeError, ValueError):
            pass
    return ContactoOdoo(
        id=int(registro["id"]),
        nombre=_limpiar_str(registro.get("name")) or f"Contacto #{registro['id']}",
        rif=_limpiar_str(registro.get("vat")),
        email=_limpiar_str(registro.get("email")),
        telefono=telefono,
        ciudad=_limpiar_str(registro.get("city")),
        direccion=_limpiar_str(registro.get("street")),
        es_empresa=es_empresa,
        customer_rank=c_rank,
        supplier_rank=s_rank,
        clasificacion=clasificacion,
        clasificacion_etiqueta=ETIQUETAS_CLASIFICACION[clasificacion],
        es_borrador_telegram=detectar_borrador_telegram(ref, notas),
        referencia=ref,
        vendedor=_m2o_nombre(registro.get("user_id")),
        notas=notas,
        plus_code=_limpiar_str(registro.get("street2")),
        latitud=lat_val,
        longitud=lon_val,
    )


def resumir_contactos(contactos: Sequence[ContactoOdoo]) -> dict[str, int]:
    """Totales por clasificación (y borradores de Telegram) sobre la lista completa."""
    prospectos = sum(1 for c in contactos if c.clasificacion == "prospecto")
    clientes = sum(1 for c in contactos if c.clasificacion == "cliente")
    proveedores = sum(1 for c in contactos if c.clasificacion == "proveedor")
    ambos = sum(1 for c in contactos if c.clasificacion == "ambos")
    borradores = sum(1 for c in contactos if c.es_borrador_telegram)
    return {
        "total": len(contactos),
        "prospectos": prospectos,
        "clientes": clientes,
        "proveedores": proveedores,
        "ambos": ambos,
        "clientes_total": clientes + ambos,
        "proveedores_total": proveedores + ambos,
        "borradores_telegram": borradores,
    }


def filtrar_contactos(
    contactos: Sequence[ContactoOdoo],
    *,
    clasificacion: str | None = None,
    q: str | None = None,
) -> list[ContactoOdoo]:
    """Filtra por clasificación y texto libre (nombre, RIF, correo, teléfono, vendedor, ref)."""
    resultado = list(contactos)
    if clasificacion:
        c = clasificacion.strip().lower()
        if c == "borrador_telegram":
            resultado = [x for x in resultado if x.es_borrador_telegram]
        elif c == "cliente":
            resultado = [x for x in resultado if x.clasificacion in ("cliente", "ambos")]
        elif c == "proveedor":
            resultado = [x for x in resultado if x.clasificacion in ("proveedor", "ambos")]
        elif c in ("prospecto", "ambos"):
            resultado = [x for x in resultado if x.clasificacion == c]

    if q and q.strip():
        buscado = _sin_acentos(q.strip())
        resultado = [
            x
            for x in resultado
            if buscado
            in _sin_acentos(
                f"{x.nombre} {x.rif} {x.email} {x.telefono} {x.ciudad} {x.referencia} {x.vendedor}"
            )
        ]
    return resultado


# ── Plan de cuentas (account.account) y clasificación de compras/gastos ──────


@dataclass(frozen=True)
class CuentaContable:
    id: int
    code: str
    name: str
    account_type: str
    tipo_nombre: str
    grupo: str
    reconcile: bool

    def a_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "account_type": self.account_type,
            "tipo_nombre": self.tipo_nombre,
            "grupo": self.grupo,
            "reconcile": self.reconcile,
            "etiqueta": f"{self.code} · {self.name}" if self.code else self.name,
        }


@dataclass(frozen=True)
class ReglaCategoriaGasto:
    categoria: str
    titulo: str
    kind_bot: str
    ejemplos: tuple[str, ...]
    palabras_clave: tuple[str, ...]
    tipos_preferidos: tuple[str, ...]
    prefijos_codigo: tuple[str, ...]
    criterio_contable: str


REGLAS_CLASIFICACION_GASTOS: tuple[ReglaCategoriaGasto, ...] = (
    ReglaCategoriaGasto(
        categoria="alimentación",
        titulo="Alimentación y refrigerios",
        kind_bot="gasto",
        ejemplos=(
            "Refrigerios de reunión",
            "Comidas de personal",
            "Agua potable / botellones",
            "Café, azúcar y artículos de cocina",
        ),
        palabras_clave=(
            "alimentacion",
            "refrigerio",
            "comida",
            "almuerzo",
            "cena",
            "desayuno",
            "agua potable",
            "botellon",
            "cafe",
            "azucar",
            "cocina",
            "atencion al personal",
            "beneficios al personal",
            "cestaticket",
        ),
        tipos_preferidos=("expense",),
        prefijos_codigo=("6", "5"),
        criterio_contable=(
            "Gasto operativo de administración/ventas (atención y refrigerios del personal). "
            "Registrar como gasto menor cuando es compra al contado."
        ),
    ),
    ReglaCategoriaGasto(
        categoria="combustible",
        titulo="Combustible y lubricantes de flota",
        kind_bot="gasto",
        ejemplos=(
            "Gasoil / Diésel para camiones",
            "Gasolina de vehículos operativos",
            "Lubricantes de consumo propio",
        ),
        palabras_clave=(
            "combustible",
            "gasoil",
            "diesel",
            "gasolina",
            "flota",
            "vehiculo",
            "carburante",
        ),
        tipos_preferidos=("expense", "expense_direct_cost"),
        prefijos_codigo=("6", "5"),
        criterio_contable=(
            "Gasto operativo de distribución/logística o costo de flota. "
            "Asociar placa o unidad en las notas del recibo."
        ),
    ),
    ReglaCategoriaGasto(
        categoria="papelería",
        titulo="Papelería, útiles e insumos de oficina",
        kind_bot="gasto",
        ejemplos=(
            "Resmas de papel, carpetas y bolígrafos",
            "Tóner, tinta e impresiones",
            "Accesorios menores de computación",
        ),
        palabras_clave=(
            "papeleria",
            "oficina",
            "utiles",
            "impresion",
            "toner",
            "tinta",
            "resma",
            "computacion",
            "suministros de oficina",
            "articulos de oficina",
        ),
        tipos_preferidos=("expense",),
        prefijos_codigo=("6", "5"),
        criterio_contable=(
            "Gasto administrativo por suministros de oficina y papelería de consumo inmediato."
        ),
    ),
    ReglaCategoriaGasto(
        categoria="mantenimiento",
        titulo="Mantenimiento, ferretería y limpieza",
        kind_bot="gasto",
        ejemplos=(
            "Mantenimiento de galpón y oficina",
            "Artículos de ferretería (brochas, bombillos, herramientas menores)",
            "Artículos de limpieza (jabón, desinfectante)",
            "Cisterna de agua",
        ),
        palabras_clave=(
            "mantenimiento",
            "reparacion",
            "ferreteria",
            "limpieza",
            "aseo",
            "galpon",
            "instalaciones",
            "cisterna",
            "repuesto",
            "conservacion",
        ),
        tipos_preferidos=("expense",),
        prefijos_codigo=("6", "5"),
        criterio_contable=(
            "Gasto operativo por conservación de instalaciones (oficina/galpón), "
            "insumos de limpieza y reparaciones menores que no capitalizan activo fijo."
        ),
    ),
    ReglaCategoriaGasto(
        categoria="transporte",
        titulo="Transporte, fletes, peajes y traslados",
        kind_bot="gasto",
        ejemplos=(
            "Traslado de personal",
            "Peajes y estacionamiento",
            "Fletes menores y envíos",
            "Viáticos de viaje",
        ),
        palabras_clave=(
            "transporte",
            "traslado",
            "flete",
            "peaje",
            "estacionamiento",
            "viatico",
            "viaje",
            "movilizacion",
            "pasaje",
            "encomienda",
        ),
        tipos_preferidos=("expense", "expense_direct_cost"),
        prefijos_codigo=("6", "5"),
        criterio_contable=("Gasto de movilización, peajes o fletes sobre ventas/compras menores."),
    ),
    ReglaCategoriaGasto(
        categoria="servicios",
        titulo="Servicios públicos, tecnológicos y honorarios",
        kind_bot="gasto",
        ejemplos=(
            "Electricidad y relleno sanitario (aseo urbano)",
            "Agua (Hidroven)",
            "Internet, telefonía y página web / hosting",
            "Honorarios profesionales (Inpsasel, asesorías) y comisiones bancarias",
        ),
        palabras_clave=(
            "servicio",
            "electricidad",
            "luz",
            "agua",
            "hidroven",
            "aseo urbano",
            "relleno",
            "internet",
            "telefono",
            "telecomunicacion",
            "pagina web",
            "hosting",
            "software",
            "honorario",
            "asesoria",
            "inpsasel",
            "bancaria",
            "comision",
        ),
        tipos_preferidos=("expense",),
        prefijos_codigo=("6", "5"),
        criterio_contable=(
            "Gasto por servicios públicos, telecomunicaciones, tecnología u honorarios "
            "profesionales. Si trae retención de IVA/ISLR o crédito a días, registrar como "
            "factura de proveedor."
        ),
    ),
    ReglaCategoriaGasto(
        categoria="otros",
        titulo="Otros gastos operativos, eventos y tributos",
        kind_bot="gasto",
        ejemplos=(
            "Eventos y activaciones comerciales (ej. Poliedro)",
            "Impuestos municipales, INCES y tasas",
            "Gastos generales diversos",
        ),
        palabras_clave=(
            "otros",
            "diversos",
            "generales",
            "varios",
            "evento",
            "publicidad",
            "mercadeo",
            "impuesto",
            "tributo",
            "municipal",
            "inces",
            "tasa",
        ),
        tipos_preferidos=("expense",),
        prefijos_codigo=("6", "5"),
        criterio_contable=(
            "Gastos diversos, eventos promocionales o contribuciones que no encajan en las "
            "categorías anteriores. Revisar en Odoo antes de publicar."
        ),
    ),
    ReglaCategoriaGasto(
        categoria="factura_proveedor",
        titulo="Compras de inventario y facturas de proveedor (CxP)",
        kind_bot="factura_proveedor",
        ejemplos=(
            "Compra de lubricantes, tambores y productos para la venta",
            "Facturas fiscales de proveedores a crédito o con retención",
            "Servicios mayores facturados con número de control",
        ),
        palabras_clave=(
            "compra",
            "mercancia",
            "inventario",
            "costo de venta",
            "costo directo",
            "lubricante",
            "producto",
            "materia prima",
            "insumo",
            "proveedor",
        ),
        tipos_preferidos=("expense_direct_cost", "asset_current", "expense"),
        prefijos_codigo=("5", "1", "6"),
        criterio_contable=(
            "Factura de proveedor (account.move in_invoice en borrador). "
            "Debita Costo de ventas / Inventario (según configuración del producto) "
            "con contrapartida en Cuentas por pagar (Proveedores)."
        ),
    ),
)


def mapear_cuenta(registro: Mapping[str, Any]) -> CuentaContable:
    """Convierte un registro crudo de ``account.account`` en ``CuentaContable``."""
    acc_type = _limpiar_str(registro.get("account_type")) or "other"
    tipo_nombre, grupo = TIPOS_CUENTA_ODOO.get(acc_type, (acc_type, "otros"))
    return CuentaContable(
        id=int(registro["id"]),
        code=_limpiar_str(registro.get("code")),
        name=_limpiar_str(registro.get("name")) or f"Cuenta #{registro['id']}",
        account_type=acc_type,
        tipo_nombre=tipo_nombre,
        grupo=grupo,
        reconcile=bool(registro.get("reconcile")),
    )


def _puntaje_cuenta_para_regla(cuenta: CuentaContable, regla: ReglaCategoriaGasto) -> int:
    """Puntúa qué tan bien calza una cuenta de Odoo con una regla de compra/gasto."""
    if cuenta.grupo not in ("gastos", "activo"):
        return -1
    if cuenta.account_type in ("asset_receivable", "asset_cash"):
        return -1

    puntaje = 0
    if cuenta.account_type in regla.tipos_preferidos:
        idx = regla.tipos_preferidos.index(cuenta.account_type)
        puntaje += 30 - (idx * 5)
    elif cuenta.grupo == "gastos":
        puntaje += 10

    for i, prefijo in enumerate(regla.prefijos_codigo):
        if cuenta.code.startswith(prefijo):
            puntaje += 8 - (i * 2)
            break

    nombre_norm = _sin_acentos(cuenta.name)
    for kw in regla.palabras_clave:
        kw_norm = _sin_acentos(kw)
        if kw_norm in nombre_norm:
            # Coincidencia exacta de palabra o frase suma más
            puntaje += 45 if len(kw_norm) >= 6 else 30

    return puntaje


def _cuenta_cxp_principal(cuentas: Sequence[CuentaContable]) -> CuentaContable | None:
    """Busca la cuenta principal de Cuentas por Pagar (Proveedores) en Odoo."""
    candidatas = [c for c in cuentas if c.account_type == "liability_payable"]
    if not candidatas:
        return None

    def _orden(c: CuentaContable) -> tuple[int, str]:
        nombre = _sin_acentos(c.name)
        prioridad = 0
        if "proveedor" in nombre or "pagar" in nombre or "comercial" in nombre:
            prioridad -= 10
        if "nacional" in nombre:
            prioridad -= 5
        return (prioridad, c.code)

    candidatas.sort(key=_orden)
    return candidatas[0]


def proponer_clasificacion_gastos(
    cuentas: Sequence[CuentaContable],
) -> list[dict[str, Any]]:
    """Genera la propuesta de clasificación de compras y gastos contra el plan de cuentas."""
    cuenta_cxp = _cuenta_cxp_principal(cuentas)
    propuestas: list[dict[str, Any]] = []

    for regla in REGLAS_CLASIFICACION_GASTOS:
        puntuadas: list[tuple[int, CuentaContable]] = []
        for cuenta in cuentas:
            pts = _puntaje_cuenta_para_regla(cuenta, regla)
            if pts > 0:
                puntuadas.append((pts, cuenta))

        puntuadas.sort(key=lambda par: (-par[0], par[1].code, par[1].name))
        sugerida = puntuadas[0][1] if puntuadas else None
        alternativas = [c for _, c in puntuadas[1:4]]

        propuestas.append(
            {
                "categoria": regla.categoria,
                "titulo": regla.titulo,
                "kind_bot": regla.kind_bot,
                "ejemplos": list(regla.ejemplos),
                "criterio_contable": regla.criterio_contable,
                "cuenta_sugerida": sugerida.a_dict() if sugerida else None,
                "cuenta_cxp_sugerida": cuenta_cxp.a_dict() if cuenta_cxp else None,
                "alternativas": [c.a_dict() for c in alternativas],
                "coincidencia_exacta": bool(puntuadas and puntuadas[0][0] >= 40),
            }
        )

    return propuestas


def sugerir_para_concepto(concepto: str, cuentas: Sequence[CuentaContable]) -> dict[str, Any]:
    """Dado el texto de una compra o gasto, propone su categoría del bot y la cuenta en Odoo."""
    texto_norm = _sin_acentos(concepto.strip())
    mejor_regla = REGLAS_CLASIFICACION_GASTOS[6]  # "otros" por defecto
    mejor_puntaje = 0
    palabras_detectadas: list[str] = []

    for regla in REGLAS_CLASIFICACION_GASTOS:
        coincidencias = [kw for kw in regla.palabras_clave if _sin_acentos(kw) in texto_norm]
        pts = sum(20 if len(kw) >= 6 else 12 for kw in coincidencias)
        if pts > mejor_puntaje:
            mejor_puntaje = pts
            mejor_regla = regla
            palabras_detectadas = coincidencias

    # Además busca si alguna cuenta del plan de cuentas coincide directamente por nombre
    propuestas = {p["categoria"]: p for p in proponer_clasificacion_gastos(cuentas)}
    propuesta_cat = propuestas[mejor_regla.categoria]
    cuenta_directa: CuentaContable | None = None
    if texto_norm:
        palabras_usuario = [
            p for p in texto_norm.replace(",", " ").replace(".", " ").split() if len(p) >= 4
        ]
        mejor_directa = 0
        for c in cuentas:
            if c.grupo not in ("gastos", "activo"):
                continue
            nombre_c = _sin_acentos(c.name)
            pts_c = sum(15 for p in palabras_usuario if p in nombre_c)
            if pts_c > mejor_directa:
                mejor_directa = pts_c
                cuenta_directa = c

    cuenta_final = (
        cuenta_directa.a_dict() if cuenta_directa is not None else propuesta_cat["cuenta_sugerida"]
    )

    return {
        "concepto": concepto.strip(),
        "categoria_sugerida": mejor_regla.categoria,
        "titulo_categoria": mejor_regla.titulo,
        "kind_bot": mejor_regla.kind_bot,
        "palabras_detectadas": palabras_detectadas,
        "criterio_contable": mejor_regla.criterio_contable,
        "cuenta_sugerida": cuenta_final,
        "cuenta_cxp_sugerida": propuesta_cat["cuenta_cxp_sugerida"],
        "alternativas": propuesta_cat["alternativas"],
    }


def resumir_plan_cuentas(cuentas: Sequence[CuentaContable]) -> dict[str, int]:
    """Totales del plan de cuentas agrupados por naturaleza contable."""
    return {
        "total": len(cuentas),
        "gastos": sum(1 for c in cuentas if c.account_type == "expense"),
        "costo_directo": sum(1 for c in cuentas if c.account_type == "expense_direct_cost"),
        "depreciacion": sum(1 for c in cuentas if c.account_type == "expense_depreciation"),
        "pasivo": sum(1 for c in cuentas if c.grupo == "pasivo"),
        "activo": sum(1 for c in cuentas if c.grupo == "activo"),
        "ingreso": sum(1 for c in cuentas if c.grupo == "ingreso"),
        "otros": sum(1 for c in cuentas if c.grupo == "otros"),
    }


def filtrar_plan_cuentas(
    cuentas: Sequence[CuentaContable],
    *,
    grupo: str | None = None,
    account_type: str | None = None,
    q: str | None = None,
) -> list[CuentaContable]:
    """Filtra las cuentas de Odoo por grupo, ``account_type`` o búsqueda por código/nombre."""
    resultado = list(cuentas)
    if grupo and grupo.strip():
        g = grupo.strip().lower()
        resultado = [c for c in resultado if c.grupo == g]
    if account_type and account_type.strip():
        t = account_type.strip().lower()
        resultado = [c for c in resultado if c.account_type.lower() == t]
    if q and q.strip():
        buscado = _sin_acentos(q.strip())
        resultado = [
            c
            for c in resultado
            if buscado in _sin_acentos(f"{c.code} {c.name} {c.tipo_nombre} {c.account_type}")
        ]
    return resultado


# ── Lector sobre XML-RPC (solo lectura) ──────────────────────────────────────


class LectorDirectorioOdoo:
    """Lee contactos y plan de cuentas de Odoo usando un ``execute`` inyectable."""

    CAMPOS_PARTNER_DESEADOS = (
        "id",
        "name",
        "vat",
        "email",
        "phone",
        "mobile",
        "street",
        "street2",
        "city",
        "partner_latitude",
        "partner_longitude",
        "is_company",
        "company_type",
        "customer_rank",
        "supplier_rank",
        "parent_id",
        "type",
        "ref",
        "comment",
        "user_id",
    )

    CAMPOS_CUENTA_DESEADOS = (
        "id",
        "code",
        "name",
        "account_type",
        "reconcile",
        "deprecated",
        "active",
    )

    def __init__(self, execute: ExecuteFn) -> None:
        self._execute = execute
        self._campos_cache: dict[str, set[str]] = {}

    def _call(
        self,
        model: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        try:
            return self._execute(model, method, args or [], kwargs or {})
        except OdooDirectorioError:
            raise
        except xmlrpc.client.Fault as e:
            logger.error("Odoo fault leyendo %s.%s: %s", model, method, e.faultString)
            raise OdooDirectorioError(f"Odoo rechazó la lectura de {model}.") from e
        except (TimeoutError, OSError, xmlrpc.client.ProtocolError) as e:
            logger.warning("Odoo no respondió leyendo %s.%s: %s", model, method, type(e).__name__)
            raise OdooDirectorioError("Odoo no respondió al consultar los datos.") from e

    def campos_disponibles(self, modelo: str) -> set[str]:
        if modelo not in self._campos_cache:
            info = self._call(modelo, "fields_get", [], {"attributes": ["string"]})
            self._campos_cache[modelo] = set(info or {})
        return self._campos_cache[modelo]

    def leer_contactos(self, limite: int = 3000) -> list[ContactoOdoo]:
        """Lee los contactos activos de ``res.partner`` y los clasifica."""
        disponibles = self.campos_disponibles("res.partner")
        campos = [c for c in self.CAMPOS_PARTNER_DESEADOS if c in disponibles]
        if "id" not in campos:
            campos.append("id")
        if "name" not in campos:
            campos.append("name")

        dominio: list[Any] = []
        if "active" in disponibles:
            dominio.append(["active", "=", True])

        registros = self._call(
            "res.partner",
            "search_read",
            [dominio],
            {"fields": campos, "limit": limite, "order": "name asc"},
        )
        contactos: list[ContactoOdoo] = []
        for r in registros or []:
            if es_subdireccion_entrega(r):
                continue
            contactos.append(mapear_contacto(r))
        return contactos

    def leer_plan_cuentas(self, limite: int = 2000) -> list[CuentaContable]:
        """Lee el plan de cuentas activo de ``account.account`` ordenado por código."""
        disponibles = self.campos_disponibles("account.account")
        campos = [c for c in self.CAMPOS_CUENTA_DESEADOS if c in disponibles]
        if "id" not in campos:
            campos.append("id")
        if "name" not in campos:
            campos.append("name")

        dominio: list[Any] = []
        if "deprecated" in disponibles:
            dominio.append(["deprecated", "=", False])
        if "active" in disponibles:
            dominio.append(["active", "=", True])

        orden = "code asc, name asc" if "code" in disponibles else "name asc"
        registros = self._call(
            "account.account",
            "search_read",
            [dominio],
            {"fields": campos, "limit": limite, "order": orden},
        )
        cuentas = [mapear_cuenta(r) for r in (registros or [])]
        cuentas.sort(key=lambda c: (c.code, c.name))
        return cuentas
