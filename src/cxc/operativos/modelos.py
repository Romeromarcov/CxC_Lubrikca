"""Modelos, estados y validaciones base para Documentos Operativos en CxC."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from cxc.capturas.modelos import (
    clase_moneda,
    es_efectivo,
    normalizar_rif,
    vacio,
)

TIPO_PAGO = "pago"
TIPO_FACTURA_PROVEEDOR = "factura_proveedor"
TIPO_PAGO_PROVEEDOR = "pago_proveedor"
TIPO_GASTO = "gasto"

TIPOS_OPERATIVOS: tuple[str, ...] = (
    TIPO_PAGO,
    TIPO_FACTURA_PROVEEDOR,
    TIPO_PAGO_PROVEEDOR,
    TIPO_GASTO,
)

TIPOS_COMPRAS: tuple[str, ...] = (
    TIPO_FACTURA_PROVEEDOR,
    TIPO_PAGO_PROVEEDOR,
    TIPO_GASTO,
)

PREFIJO_POR_TIPO: dict[str, str] = {
    TIPO_PAGO: "PAG",
    TIPO_FACTURA_PROVEEDOR: "FPR",
    TIPO_PAGO_PROVEEDOR: "PPR",
    TIPO_GASTO: "GAS",
}

ETIQUETA_POR_TIPO: dict[str, str] = {
    TIPO_PAGO: "Pago recibido de cliente",
    TIPO_FACTURA_PROVEEDOR: "Factura / recibo de proveedor",
    TIPO_PAGO_PROVEEDOR: "Pago a proveedor",
    TIPO_GASTO: "Gasto sin factura / compra menor",
}

BORRADOR = "BORRADOR"
INCOMPLETO = "INCOMPLETO"
PENDIENTE_APROBACION = "PENDIENTE_APROBACION"
CONFIRMADO_CXC = "CONFIRMADO_CXC"
ENVIADO_ODOO = "ENVIADO_ODOO"
RECHAZADO = "RECHAZADO"
ERROR_ODOO = "ERROR"

ESTADOS_OPERATIVOS: tuple[str, ...] = (
    BORRADOR,
    INCOMPLETO,
    PENDIENTE_APROBACION,
    CONFIRMADO_CXC,
    ENVIADO_ODOO,
    RECHAZADO,
    ERROR_ODOO,
)

CATEGORIAS_GASTO: tuple[str, ...] = (
    "alimentación",
    "combustible",
    "papelería",
    "mantenimiento",
    "transporte",
    "servicios",
    "otros",
)


class OperativoInvalido(ValueError):
    """Los datos del documento operativo tienen errores o faltantes."""

    def __init__(self, error: str, faltan: list[str] | None = None) -> None:
        super().__init__(error)
        self.error = error
        self.faltan = faltan or []


class OperativoNoEncontrado(LookupError):
    """Documento operativo inexistente."""


class OperativoYaEnviado(Exception):
    """El documento ya fue enviado a Odoo y no puede modificarse o reenviarse."""


class OperativoEstadoInvalido(Exception):
    """Transición de estado no permitida (ej. aprobar un documento rechazado)."""


def decimal_opcional(valor: Any, nombre: str, *, minimo: Decimal | None = None) -> Decimal | None:
    if vacio(valor):
        return None
    try:
        d = Decimal(str(valor).strip())
    except (InvalidOperation, ValueError) as e:
        raise OperativoInvalido(f"{nombre} inválido: {valor!r}.", [nombre.lower()]) from e
    if not d.is_finite():
        raise OperativoInvalido(f"{nombre} inválido: {valor!r}.", [nombre.lower()])
    if minimo is not None and d < minimo:
        raise OperativoInvalido(f"{nombre} no puede ser menor que {minimo}.", [nombre.lower()])
    return d


def fecha_opcional(valor: Any, nombre: str) -> date | None:
    if vacio(valor):
        return None
    try:
        return date.fromisoformat(str(valor).strip()[:10])
    except ValueError as e:
        raise OperativoInvalido(f"{nombre} inválida (usa AAAA-MM-DD).", [nombre.lower()]) from e


def rif_opcional(valor: Any) -> str:
    if vacio(valor):
        return ""
    try:
        return normalizar_rif(str(valor))
    except ValueError as e:
        raise OperativoInvalido(str(e), ["rif"]) from e


def extraer_campos_canonicos(tipo: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Mapea alias del bot y nombres canónicos del backend a un diccionario uniforme."""
    moneda_raw = str(raw.get("moneda") or "VES").strip()
    moneda = clase_moneda(moneda_raw) if moneda_raw else "VES"
    if moneda not in ("USD", "VES"):
        moneda = moneda_raw.upper()[:10] or "VES"

    contraparte = str(
        raw.get("contraparte_nombre")
        or (raw.get("cliente") if tipo == TIPO_PAGO else raw.get("proveedor"))
        or raw.get("nombre")
        or raw.get("cliente")
        or raw.get("proveedor")
        or ""
    ).strip()

    monto_raw = raw.get("monto") if not vacio(raw.get("monto")) else raw.get("monto_total")
    num_doc = str(raw.get("numero_documento") or raw.get("numero_factura") or "").strip()
    fecha_raw = raw.get("fecha_documento") or raw.get("fecha_pago") or raw.get("fecha_emision")
    metodo = str(raw.get("metodo_pago") or "").strip()
    sin_fac_raw = raw.get("sin_factura")
    sin_factura = (
        bool(sin_fac_raw) if sin_fac_raw is not None else (tipo == TIPO_GASTO and not num_doc)
    )

    diario_raw = raw.get("diario_id")
    diario_id = (
        int(str(diario_raw))
        if diario_raw not in (None, "", False) and str(diario_raw).isdigit()
        else None
    )
    partner_raw = raw.get("partner_odoo_id")
    partner_id = (
        int(str(partner_raw))
        if partner_raw not in (None, "", False) and str(partner_raw).isdigit()
        else None
    )
    cuenta_raw = raw.get("cuenta_contable_id")
    cuenta_id = (
        int(str(cuenta_raw))
        if cuenta_raw not in (None, "", False) and str(cuenta_raw).isdigit()
        else None
    )
    contra_raw = raw.get("cuenta_contrapartida_id")
    contrapartida_id = (
        int(str(contra_raw))
        if contra_raw not in (None, "", False) and str(contra_raw).isdigit()
        else None
    )
    lote_raw = raw.get("lote_caja_chica_id")
    lote_id = (
        int(str(lote_raw))
        if lote_raw not in (None, "", False) and str(lote_raw).isdigit()
        else None
    )
    tercero_raw = raw.get("cuenta_tercero_id")
    tercero_id = (
        int(str(tercero_raw))
        if tercero_raw not in (None, "", False) and str(tercero_raw).isdigit()
        else None
    )
    es_zt_raw = raw.get("es_zelle_tercero")
    es_zelle_tercero = bool(es_zt_raw) or (tercero_id is not None)

    return {
        "tipo": tipo,
        "partner_odoo_id": partner_id,
        "contraparte_nombre": contraparte,
        "rif": rif_opcional(raw.get("rif")),
        "monto": decimal_opcional(monto_raw, "monto"),
        "base_imponible": decimal_opcional(
            raw.get("base_imponible"), "base_imponible", minimo=Decimal("0")
        ),
        "iva": decimal_opcional(raw.get("iva"), "iva", minimo=Decimal("0")),
        "moneda": moneda if not vacio(raw.get("moneda")) else "",
        "metodo_pago": metodo,
        "banco": str(raw.get("banco") or "").strip(),
        "diario_id": diario_id,
        "diario_nombre": str(raw.get("diario_nombre") or raw.get("diario_codigo") or "").strip(),
        "cuenta_contable_id": cuenta_id,
        "cuenta_contable_codigo": str(raw.get("cuenta_contable_codigo") or "").strip(),
        "cuenta_contable_nombre": str(raw.get("cuenta_contable_nombre") or "").strip(),
        "referencia": str(raw.get("referencia") or "").strip(),
        "numero_documento": num_doc,
        "numero_control": str(raw.get("numero_control") or "").strip(),
        "pedido_ref": str(raw.get("pedido_ref") or "").strip(),
        "factura_ref": str(raw.get("factura_ref") or "").strip(),
        "orden_ref": str(raw.get("orden_ref") or "").strip(),
        "categoria": str(raw.get("categoria") or "").strip(),
        "concepto": str(raw.get("concepto") or "").strip(),
        "sin_factura": sin_factura,
        "fecha_documento": fecha_opcional(fecha_raw, "fecha_documento"),
        "fecha_vencimiento": fecha_opcional(raw.get("fecha_vencimiento"), "fecha_vencimiento"),
        "proveedor_telefono": str(raw.get("proveedor_telefono") or "").strip(),
        "proveedor_email": str(raw.get("proveedor_email") or "").strip(),
        "proveedor_direccion": str(raw.get("proveedor_direccion") or "").strip(),
        "es_zelle_tercero": es_zelle_tercero if tipo == TIPO_PAGO else False,
        "cuenta_tercero_id": tercero_id if tipo == TIPO_PAGO else None,
        "beneficiario_tercero": (
            str(raw.get("beneficiario_tercero") or "").strip() if tipo == TIPO_PAGO else ""
        ),
        "lote_caja_chica_id": lote_id,
        "cuenta_contrapartida_id": contrapartida_id,
        "conciliado_banco_huella": str(raw.get("conciliado_banco_huella") or "").strip(),
        "notas": str(raw.get("notas") or "").strip(),
    }


def validar_campos_operativo(tipo: str, datos: dict[str, Any]) -> list[str]:
    """Valida reglas de fondo (lanza ``OperativoInvalido``) y devuelve ``faltantes`` tolerables."""
    if tipo not in TIPOS_OPERATIVOS:
        raise OperativoInvalido(f"Tipo de documento operativo desconocido: {tipo!r}.")

    monto = datos.get("monto")
    if monto is not None and Decimal(str(monto)) <= Decimal("0"):
        raise OperativoInvalido("El monto debe ser mayor que cero.", ["monto"])

    fecha_doc = datos.get("fecha_documento")
    fecha_venc = datos.get("fecha_vencimiento")
    if fecha_doc and fecha_venc and fecha_venc < fecha_doc:
        raise OperativoInvalido(
            "La fecha de vencimiento es anterior a la de emisión.", ["fecha_vencimiento"]
        )

    faltantes: list[str] = []
    if tipo == TIPO_PAGO:
        if vacio(datos.get("monto")):
            faltantes.append("monto")
        if vacio(datos.get("moneda")):
            faltantes.append("moneda")
        es_zt = bool(datos.get("es_zelle_tercero"))
        if (es_zt or not es_efectivo(datos.get("metodo_pago"))) and vacio(datos.get("referencia")):
            faltantes.append("referencia")
        if es_zt and not datos.get("cuenta_tercero_id"):
            faltantes.append("cuenta_tercero (proveedor Zelle)")
        if (
            vacio(datos.get("contraparte_nombre"))
            and vacio(datos.get("rif"))
            and vacio(datos.get("pedido_ref"))
            and not datos.get("partner_odoo_id")
        ):
            faltantes.append("cliente (nombre, RIF o pedido)")
        return faltantes

    if tipo == TIPO_PAGO_PROVEEDOR:
        if (
            vacio(datos.get("contraparte_nombre"))
            and vacio(datos.get("rif"))
            and not datos.get("partner_odoo_id")
        ):
            faltantes.append("proveedor")
        if vacio(datos.get("monto")):
            faltantes.append("monto")
        if vacio(datos.get("moneda")):
            faltantes.append("moneda")
        if not es_efectivo(datos.get("metodo_pago")) and vacio(datos.get("referencia")):
            faltantes.append("referencia")
        return faltantes

    if tipo == TIPO_FACTURA_PROVEEDOR:
        if (
            vacio(datos.get("contraparte_nombre"))
            and vacio(datos.get("rif"))
            and not datos.get("partner_odoo_id")
        ):
            faltantes.append("proveedor (nombre o RIF)")
        if vacio(datos.get("numero_documento")):
            faltantes.append("numero_factura")
        if vacio(datos.get("fecha_documento")):
            faltantes.append("fecha_emision")
        if vacio(datos.get("moneda")):
            faltantes.append("moneda")
        if vacio(datos.get("monto")):
            faltantes.append("monto_total")
        return faltantes

    # Gasto sin factura / compra menor: numero_documento es 100% opcional
    if vacio(datos.get("contraparte_nombre")) and not datos.get("partner_odoo_id"):
        faltantes.append("proveedor")
    if vacio(datos.get("fecha_documento")):
        faltantes.append("fecha_emision")
    if vacio(datos.get("moneda")):
        faltantes.append("moneda")
    if vacio(datos.get("monto")):
        faltantes.append("monto_total")
    return faltantes


@dataclass(frozen=True)
class DocumentoOperativo:
    documento_id: int
    numero: str
    token_preview: str
    tipo: str
    estado: str
    origen: str
    captura_id: int | None
    partner_odoo_id: int | None
    contraparte_nombre: str
    rif: str
    monto: Decimal | None
    base_imponible: Decimal | None
    iva: Decimal | None
    moneda: str
    metodo_pago: str
    banco: str
    diario_id: int | None
    diario_nombre: str
    cuenta_contable_id: int | None
    cuenta_contable_codigo: str
    cuenta_contable_nombre: str
    referencia: str
    numero_documento: str
    numero_control: str
    pedido_ref: str
    factura_ref: str
    orden_ref: str
    categoria: str
    concepto: str
    sin_factura: bool
    fecha_documento: date | None
    fecha_vencimiento: date | None
    proveedor_telefono: str
    proveedor_email: str
    proveedor_direccion: str
    notas: str
    validacion: dict[str, Any]
    faltantes: tuple[str, ...]
    aprobado_por: str
    aprobado_en: datetime | None
    rechazo_motivo: str
    odoo_model: str
    odoo_res_id: int | None
    odoo_ref: str
    error_odoo: str
    creado_por: str
    creado_en: datetime
    actualizado_en: datetime
    es_zelle_tercero: bool = False
    cuenta_tercero_id: int | None = None
    beneficiario_tercero: str = ""
    confirmado_cxc: bool = False
    confirmado_cxc_por: str = ""
    confirmado_cxc_en: datetime | None = None
    numero_recibo: str = ""
    recibo_emitido_en: datetime | None = None
    lote_caja_chica_id: int | None = None
    cuenta_contrapartida_id: int | None = None
    conciliado_banco_huella: str = ""

    def a_dict(self) -> dict[str, Any]:
        efectivo = es_efectivo(self.metodo_pago) and not self.es_zelle_tercero
        alertas = list(self.validacion.get("alertas") or [])
        return {
            "id": self.documento_id,
            "documento_id": self.documento_id,
            "numero": self.numero,
            "token_preview": self.token_preview,
            "preview_path": f"/comprobante/{self.token_preview}" if self.token_preview else "",
            "recibo_path": f"/recibo/{self.token_preview}"
            if (self.token_preview and self.numero_recibo)
            else "",
            "tipo": self.tipo,
            "tipo_etiqueta": ETIQUETA_POR_TIPO.get(self.tipo, self.tipo),
            "estado": self.estado,
            "origen": self.origen,
            "origen_sistema": "cxc",
            "captura_id": self.captura_id,
            "partner_odoo_id": self.partner_odoo_id,
            "contraparte_nombre": self.contraparte_nombre,
            "rif": self.rif,
            "monto": float(self.monto) if self.monto is not None else None,
            "base_imponible": (
                float(self.base_imponible) if self.base_imponible is not None else None
            ),
            "iva": float(self.iva) if self.iva is not None else None,
            "moneda": self.moneda,
            "metodo_pago": self.metodo_pago,
            "es_efectivo": efectivo,
            "es_zelle_tercero": self.es_zelle_tercero,
            "cuenta_tercero_id": self.cuenta_tercero_id,
            "beneficiario_tercero": self.beneficiario_tercero,
            "banco": self.banco,
            "diario_id": self.diario_id,
            "diario_nombre": self.diario_nombre,
            "cuenta_contable_id": self.cuenta_contable_id,
            "cuenta_contable_codigo": self.cuenta_contable_codigo,
            "cuenta_contable_nombre": self.cuenta_contable_nombre,
            "referencia": self.referencia,
            "numero_documento": self.numero_documento,
            "numero_control": self.numero_control,
            "pedido_ref": self.pedido_ref,
            "factura_ref": self.factura_ref,
            "orden_ref": self.orden_ref,
            "categoria": self.categoria,
            "concepto": self.concepto,
            "sin_factura": self.sin_factura,
            "fecha_documento": (self.fecha_documento.isoformat() if self.fecha_documento else None),
            "fecha_vencimiento": (
                self.fecha_vencimiento.isoformat() if self.fecha_vencimiento else None
            ),
            "proveedor_telefono": self.proveedor_telefono,
            "proveedor_email": self.proveedor_email,
            "proveedor_direccion": self.proveedor_direccion,
            "notas": self.notas,
            "validacion": self.validacion,
            "alertas": alertas,
            "faltantes": list(self.faltantes),
            "confirmado_cxc": self.confirmado_cxc,
            "confirmado_cxc_por": self.confirmado_cxc_por,
            "confirmado_cxc_en": (
                self.confirmado_cxc_en.isoformat() if self.confirmado_cxc_en else None
            ),
            "numero_recibo": self.numero_recibo,
            "recibo_emitido_en": (
                self.recibo_emitido_en.isoformat() if self.recibo_emitido_en else None
            ),
            "lote_caja_chica_id": self.lote_caja_chica_id,
            "cuenta_contrapartida_id": self.cuenta_contrapartida_id,
            "conciliado_banco_huella": self.conciliado_banco_huella,
            "asiento_contable": (
                self.tipo == TIPO_GASTO and (self.sin_factura or not self.numero_documento.strip())
            ),
            "aprobado_por": self.aprobado_por,
            "aprobado_en": self.aprobado_en.isoformat() if self.aprobado_en else None,
            "rechazo_motivo": self.rechazo_motivo,
            "odoo_model": self.odoo_model,
            "odoo_res_id": self.odoo_res_id,
            "odoo_ref": self.odoo_ref,
            "error_odoo": self.error_odoo,
            "creado_por": self.creado_por,
            "creado_en": self.creado_en.isoformat(),
            "actualizado_en": self.actualizado_en.isoformat(),
            "puede_editar": self.estado != ENVIADO_ODOO,
            "puede_confirmar_cxc": self.tipo in (TIPO_PAGO, TIPO_PAGO_PROVEEDOR)
            and self.estado in (PENDIENTE_APROBACION, ERROR_ODOO)
            and not self.confirmado_cxc
            and not self.faltantes,
            "puede_aprobar": self.estado in (PENDIENTE_APROBACION, CONFIRMADO_CXC, ERROR_ODOO)
            and not self.faltantes,
            "puede_rechazar": self.estado not in (ENVIADO_ODOO, RECHAZADO),
        }
