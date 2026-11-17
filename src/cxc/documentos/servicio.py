"""Servicio de Documentos Comerciales (Cotizaciones y Pedidos) integrados con el catálogo Odoo."""

from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from cxc.capturas.modelos import vacio, validar_gps
from cxc.odoo.escritura import EscritorOdoo, OdooEscrituraError

from .catalogo import ItemCatalogo, ProveedorCatalogo, resolver_producto_unico
from .modelos import (
    BORRADOR,
    COTIZACION,
    ENVIADO_ODOO,
    ERROR_ODOO,
    MONEDAS_LISTA,
    PEDIDO,
    TIPOS_DOCUMENTO,
    DocumentoComercial,
    DocumentoInvalido,
    calcular_subtotal_linea,
    decimal_limpio,
    normalizar_rif_opcional,
    parsear_fecha_opcional,
)
from .repositorio import RepoDocumentos

logger = logging.getLogger("cxc.documentos.servicio")

FabricaEscritor = Callable[[], EscritorOdoo | None]


class DocumentoNoEncontrado(LookupError):
    """El documento comercial no existe."""


class DocumentoYaEnviado(Exception):
    """El documento ya fue enviado a Odoo y no puede reescribirse desde aquí."""


def _extraer_gps(datos: dict[str, Any]) -> tuple[float | None, float | None, str]:
    ub = datos.get("ubicacion")
    lat = datos.get("latitud")
    lon = datos.get("longitud")
    plus = str(datos.get("plus_code") or "").strip()
    if isinstance(ub, dict):
        if lat in (None, "") and ub.get("latitud") not in (None, ""):
            lat = ub.get("latitud")
        if lon in (None, "") and ub.get("longitud") not in (None, ""):
            lon = ub.get("longitud")
        if not plus and ub.get("plus_code"):
            plus = str(ub["plus_code"]).strip()
    if lat not in (None, "") and lon not in (None, ""):
        validar_gps({"latitud": lat, "longitud": lon})
        f_lat, f_lon = float(str(lat)), float(str(lon))
        if f_lat == 0.0 and f_lon == 0.0:
            return None, None, ""
        return f_lat, f_lon, ("" if plus == "6FG22222+22" else plus)
    return None, None, ("" if plus == "6FG22222+22" else plus)


class ServicioDocumentos:
    """Gestiona Cotizaciones y Pedidos en CxC y su envío controlado a Odoo."""

    def __init__(
        self,
        repo: RepoDocumentos,
        catalogo: ProveedorCatalogo,
        fabrica_escritor: FabricaEscritor,
    ) -> None:
        self._repo = repo
        self._catalogo = catalogo
        self._fabrica = fabrica_escritor

    def catalogo_items(self, *, refresh: bool = False) -> list[ItemCatalogo]:
        return self._catalogo.listar(refresh=refresh)

    def obtener(self, documento_id: int) -> DocumentoComercial:
        doc = self._repo.obtener(documento_id)
        if doc is None:
            raise DocumentoNoEncontrado(str(documento_id))
        return doc

    def listar(
        self,
        *,
        tipo: str | None = None,
        estado: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DocumentoComercial], int, dict[str, int]]:
        return self._repo.listar(tipo=tipo, estado=estado, q=q, limit=limit, offset=offset)

    def _preparar_cabecera_y_lineas(
        self,
        datos: dict[str, Any],
        *,
        origen: str,
        captura_id: int | None,
        actor: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        tipo = str(datos.get("tipo") or COTIZACION).strip().lower()
        if tipo not in TIPOS_DOCUMENTO:
            raise DocumentoInvalido(f"Tipo de documento inválido: {tipo!r}.", ["tipo"])

        cliente = str(datos.get("cliente_nombre") or datos.get("cliente") or "").strip()
        if not cliente:
            raise DocumentoInvalido("Indica el nombre del cliente o prospecto.", ["cliente_nombre"])

        moneda_lista = str(datos.get("moneda_lista") or "USD").strip().upper()
        if moneda_lista not in MONEDAS_LISTA:
            moneda_lista = "USD"

        rif = normalizar_rif_opcional(datos.get("rif"))
        lat, lon, plus_code = _extraer_gps(datos)
        partner_id_raw = datos.get("partner_odoo_id")
        partner_odoo_id = (
            int(str(partner_id_raw))
            if partner_id_raw not in (None, "", False) and str(partner_id_raw).isdigit()
            else None
        )
        es_primera = datos.get("es_primera_compra") is not False
        tipo_contacto = (
            str(
                datos.get("tipo_contacto")
                or ("cliente" if (tipo == PEDIDO or not es_primera) else "prospecto")
            )
            .strip()
            .lower()
        )
        if tipo_contacto not in ("prospecto", "cliente"):
            tipo_contacto = "prospecto"

        lineas_in = datos.get("lineas")
        if not isinstance(lineas_in, list) or not lineas_in:
            raise DocumentoInvalido("Agrega al menos un producto al documento.", ["lineas"])

        catalogo_actual = self._catalogo.listar()
        lineas_limpias: list[dict[str, Any]] = []
        subtotal_doc = Decimal("0")
        litros_doc = Decimal("0")

        for idx, cruda in enumerate(lineas_in, start=1):
            if not isinstance(cruda, dict):
                raise DocumentoInvalido("Línea de documento inválida.", ["lineas"])
            pid_in = str(cruda.get("producto_id") or "").strip() or None
            desc_in = str(
                cruda.get("descripcion") or cruda.get("producto") or cruda.get("codigo") or ""
            ).strip()
            if not desc_in and not pid_in:
                raise DocumentoInvalido(f"La línea #{idx} no tiene producto.", ["lineas"])

            cantidad = decimal_limpio(cruda.get("cantidad"), "cantidad")
            if cantidad <= Decimal("0"):
                raise DocumentoInvalido(
                    f"La cantidad en la línea #{idx} debe ser mayor que cero.", ["lineas"]
                )
            descuento_pct = decimal_limpio(
                cruda.get("descuento_pct", 0), "descuento_pct", minimo=Decimal("0")
            )
            if descuento_pct > Decimal("100"):
                raise DocumentoInvalido("El descuento no puede superar 100%.", ["descuento_pct"])

            elegido, _ = resolver_producto_unico(
                catalogo_actual,
                producto_id=pid_in,
                texto=str(cruda.get("codigo") or desc_in),
            )

            tiene_precio_explicito = not vacio(cruda.get("precio_unitario"))
            precio_in = (
                decimal_limpio(cruda.get("precio_unitario"), "precio_unitario", minimo=Decimal("0"))
                if tiene_precio_explicito
                else None
            )

            if elegido is not None:
                producto_id = elegido.producto_id
                codigo = elegido.codigo
                descripcion = elegido.nombre
                presentacion = elegido.presentacion
                litros_u = elegido.litros
                precio_cat = elegido.precio_usd if moneda_lista == "USD" else elegido.precio_ves
                precio_u = (
                    precio_in
                    if (precio_in is not None and precio_in > Decimal("0"))
                    else precio_cat
                )
            else:
                producto_id = pid_in or ""
                codigo = str(cruda.get("codigo") or "").strip()
                descripcion = desc_in
                presentacion = str(cruda.get("presentacion") or "").strip()
                litros_u = decimal_limpio(cruda.get("litros_unidad", 0), "litros_unidad")
                precio_u = precio_in if precio_in is not None else Decimal("0")

            sub_ln = calcular_subtotal_linea(cantidad, precio_u, descuento_pct)
            subtotal_doc += sub_ln
            litros_doc += (litros_u * cantidad).quantize(Decimal("0.01"))

            lineas_limpias.append(
                {
                    "orden": idx,
                    "producto_id": producto_id,
                    "codigo": codigo,
                    "descripcion": descripcion,
                    "presentacion": presentacion,
                    "litros_unidad": litros_u,
                    "cantidad": cantidad,
                    "precio_unitario": precio_u,
                    "descuento_pct": descuento_pct,
                    "subtotal": sub_ln,
                }
            )

        cabecera: dict[str, Any] = {
            "tipo": tipo,
            "origen": origen,
            "captura_id": captura_id,
            "partner_odoo_id": partner_odoo_id,
            "tipo_contacto": tipo_contacto,
            "cliente_nombre": cliente,
            "rif": rif,
            "email": str(datos.get("email") or "").strip(),
            "telefono": str(datos.get("telefono") or "").strip(),
            "direccion": str(
                datos.get("direccion")
                or datos.get("direccion_fiscal")
                or datos.get("direccion_entrega")
                or ""
            ).strip(),
            "plus_code": plus_code,
            "latitud": lat,
            "longitud": lon,
            "es_primera_compra": es_primera,
            "moneda_lista": moneda_lista,
            "vendedor_email": str(datos.get("vendedor_email") or "").strip(),
            "fecha_entrega": parsear_fecha_opcional(datos.get("fecha_entrega")),
            "condiciones_negociacion": str(datos.get("condiciones_negociacion") or "").strip(),
            "observaciones": str(datos.get("observaciones") or datos.get("notas") or "").strip(),
            "subtotal": subtotal_doc,
            "total_litros": litros_doc,
            "creado_por": actor,
        }
        return cabecera, lineas_limpias

    def crear(
        self,
        datos: dict[str, Any],
        *,
        origen: str = "web",
        captura_id: int | None = None,
        actor: str = "sistema",
    ) -> DocumentoComercial:
        if captura_id is not None:
            previo = self._repo.obtener_por_captura(captura_id)
            if previo is not None:
                if previo.estado == ENVIADO_ODOO:
                    return previo
                cab, lns = self._preparar_cabecera_y_lineas(
                    datos, origen=origen, captura_id=captura_id, actor=actor
                )
                cab.pop("creado_por", None)
                cab["estado"] = BORRADOR
                cab["error_odoo"] = ""
                return self._repo.actualizar(previo.documento_id, cab, lns)
        cab, lns = self._preparar_cabecera_y_lineas(
            datos, origen=origen, captura_id=captura_id, actor=actor
        )
        cab["estado"] = BORRADOR
        return self._repo.crear(cab, lns)

    def editar(
        self, documento_id: int, datos: dict[str, Any], *, actor: str = "sistema"
    ) -> DocumentoComercial:
        existente = self.obtener(documento_id)
        if existente.estado == ENVIADO_ODOO:
            ref = existente.odoo_order_name or existente.odoo_order_id
            raise DocumentoYaEnviado(
                f"El documento {existente.numero} ya fue enviado a Odoo ({ref})."
            )
        combinado = {
            "tipo": datos.get("tipo", existente.tipo),
            "partner_odoo_id": datos.get("partner_odoo_id", existente.partner_odoo_id),
            "tipo_contacto": datos.get("tipo_contacto", existente.tipo_contacto),
            "cliente_nombre": datos.get("cliente_nombre", existente.cliente_nombre),
            "rif": datos.get("rif", existente.rif),
            "email": datos.get("email", existente.email),
            "telefono": datos.get("telefono", existente.telefono),
            "direccion": datos.get("direccion", existente.direccion),
            "plus_code": datos.get("plus_code", existente.plus_code),
            "latitud": datos.get("latitud", existente.latitud),
            "longitud": datos.get("longitud", existente.longitud),
            "ubicacion": datos.get("ubicacion"),
            "es_primera_compra": datos.get("es_primera_compra", existente.es_primera_compra),
            "moneda_lista": datos.get("moneda_lista", existente.moneda_lista),
            "vendedor_email": datos.get("vendedor_email", existente.vendedor_email),
            "fecha_entrega": datos.get("fecha_entrega", existente.fecha_entrega),
            "condiciones_negociacion": datos.get(
                "condiciones_negociacion", existente.condiciones_negociacion
            ),
            "observaciones": datos.get("observaciones", existente.observaciones),
            "lineas": datos.get(
                "lineas",
                [ln.a_dict() for ln in existente.lineas],
            ),
        }
        cab, lns = self._preparar_cabecera_y_lineas(
            combinado,
            origen=existente.origen,
            captura_id=existente.captura_id,
            actor=actor,
        )
        cab.pop("creado_por", None)
        cab["estado"] = BORRADOR
        cab["error_odoo"] = ""
        return self._repo.actualizar(documento_id, cab, lns)

    def registrar_envio_captura(
        self,
        documento_id: int,
        *,
        partner_odoo_id: int | None,
        odoo_order_id: int,
        odoo_order_name: str,
    ) -> DocumentoComercial:
        campos: dict[str, Any] = {
            "estado": ENVIADO_ODOO,
            "odoo_order_id": odoo_order_id,
            "odoo_order_name": odoo_order_name,
            "error_odoo": "",
            "tipo_contacto": "cliente",
        }
        if partner_odoo_id is not None:
            campos["partner_odoo_id"] = partner_odoo_id
        return self._repo.actualizar(documento_id, campos)

    def registrar_error_captura(self, documento_id: int, error: str) -> DocumentoComercial:
        return self._repo.actualizar(
            documento_id,
            {"estado": ERROR_ODOO, "error_odoo": error[:2000]},
        )

    def _resolver_partner_para_envio(
        self, doc: DocumentoComercial, odoo: EscritorOdoo, marca: str
    ) -> int:
        partner_id: int | None = doc.partner_odoo_id
        if partner_id is None and doc.rif:
            ids_rif = odoo.partners_por_rif(doc.rif)
            if len(ids_rif) > 1:
                raise DocumentoInvalido(
                    f"El RIF {doc.rif} aparece en {len(ids_rif)} contactos de Odoo; "
                    "selecciona el contacto exacto.",
                    ["rif"],
                )
            if ids_rif:
                partner_id = ids_rif[0]
        if partner_id is None and doc.cliente_nombre:
            ids_nom = odoo.partners_por_nombre(doc.cliente_nombre)
            if len(ids_nom) > 1:
                raise DocumentoInvalido(
                    f"Hay {len(ids_nom)} contactos llamados «{doc.cliente_nombre}» en Odoo; "
                    "indica su RIF o selecciónalo.",
                    ["cliente_nombre"],
                )
            if ids_nom:
                partner_id = ids_nom[0]

        if doc.tipo == COTIZACION:
            # Cotización: puede ir a un Prospecto sin exigir RIF ni GPS aún
            if partner_id is not None:
                odoo.actualizar_contacto(
                    partner_id,
                    {
                        "telefono": doc.telefono or None,
                        "email": doc.email or None,
                        "direccion": doc.direccion or None,
                        "rif": doc.rif or None,
                        "plus_code": doc.plus_code or None,
                        "lat": doc.latitud,
                        "lon": doc.longitud,
                    },
                    promover_cliente=False,
                )
                return partner_id
            return odoo.crear_contacto_borrador(
                {
                    "nombre": doc.cliente_nombre,
                    "telefono": doc.telefono or "",
                    "email": doc.email or None,
                    "direccion": doc.direccion or None,
                    "rif": doc.rif or None,
                    "plus_code": doc.plus_code or None,
                    "lat": doc.latitud,
                    "lon": doc.longitud,
                    "notas": f"Prospecto creado desde {doc.numero}",
                },
                marca,
            )

        # Pedido formal: si existe en Odoo, verificar/completar lo que le falte y promover a cliente
        if partner_id is not None:
            ficha = odoo.leer_partner(partner_id) or {}
            rif_ef = doc.rif or str(ficha.get("vat") or "").strip()
            if not rif_ef:
                raise DocumentoInvalido(
                    "Para enviar un Pedido a Odoo el contacto debe tener RIF/Cédula.", ["rif"]
                )
            odoo.actualizar_contacto(
                partner_id,
                {
                    "rif": doc.rif or None,
                    "email": doc.email or None,
                    "telefono": doc.telefono or None,
                    "direccion": doc.direccion or None,
                    "plus_code": doc.plus_code or None,
                    "lat": doc.latitud,
                    "lon": doc.longitud,
                },
                promover_cliente=True,
                marca=marca,
            )
            return partner_id

        # Cliente nuevo en Pedido: exige ficha completa + GPS en contacto principal
        faltan_nuevo: list[str] = []
        if not doc.rif:
            faltan_nuevo.append("rif")
        if not doc.email:
            faltan_nuevo.append("email")
        if not doc.direccion:
            faltan_nuevo.append("direccion")
        if doc.latitud is None or doc.longitud is None:
            faltan_nuevo.append("ubicacion")
        if faltan_nuevo:
            faltan_txt = ", ".join(faltan_nuevo)
            raise DocumentoInvalido(
                f"Cliente nuevo: faltan datos obligatorios para Odoo ({faltan_txt}).",
                faltan_nuevo,
            )
        return odoo.crear_cliente(
            {
                "nombre": doc.cliente_nombre,
                "rif": doc.rif,
                "email": doc.email,
                "telefono": doc.telefono or None,
                "direccion_fiscal": doc.direccion,
                "plus_code": doc.plus_code or None,
                "lat": doc.latitud,
                "lon": doc.longitud,
            },
            marca,
        )

    def enviar_a_odoo(self, documento_id: int, *, actor: str = "sistema") -> DocumentoComercial:
        """Envía la Cotización o Pedido desde CxC a Odoo como ``sale.order`` en borrador."""
        doc = self.obtener(documento_id)
        if doc.estado == ENVIADO_ODOO:
            ref = doc.odoo_order_name or doc.odoo_order_id
            raise DocumentoYaEnviado(
                f"El documento {doc.numero} ya fue enviado a Odoo ({ref})."
            )

        # Re-intentar resolver líneas pendientes contra el catálogo vigente antes de validar
        catalogo_actual = self._catalogo.listar()
        lineas_odoo: list[dict[str, Any]] = []
        sin_catalogo: list[str] = []
        for ln in doc.lineas:
            pid = ln.producto_id
            if not (pid and pid.isdigit()):
                elegido, _ = resolver_producto_unico(catalogo_actual, texto=ln.descripcion)
                if elegido is not None:
                    pid = elegido.producto_id
            if not (pid and pid.isdigit()):
                sin_catalogo.append(f"«{ln.descripcion}»")
                continue
            precio_ln = ln.precio_unitario if ln.precio_unitario > Decimal("0") else None
            lineas_odoo.append(
                {
                    "product_id": int(pid),
                    "cantidad": ln.cantidad,
                    "precio": precio_ln,
                    "descuento_pct": ln.descuento_pct if ln.descuento_pct > Decimal("0") else None,
                }
            )

        if sin_catalogo:
            raise DocumentoInvalido(
                "Selecciona un producto real del catálogo de Odoo para: " + ", ".join(sin_catalogo),
                ["lineas"],
            )

        escritor = self._fabrica()
        if escritor is None:
            raise DocumentoInvalido(
                "La escritura a Odoo está desactivada en este entorno (CAPTURAS_ODOO_ESCRITURA=0)."
            )

        marca = f"DOC-{doc.documento_id}"
        try:
            partner_id = self._resolver_partner_para_envio(doc, escritor, marca)
            etiqueta_doc = "Cotización" if doc.tipo == COTIZACION else "Pedido"
            notas_partes: list[str] = [
                f"{etiqueta_doc} CxC {doc.numero} (Lista ref: {doc.moneda_lista})"
            ]
            if doc.condiciones_negociacion:
                notas_partes.append(
                    f"Condiciones de negociación / descuentos: {doc.condiciones_negociacion}"
                )
            if doc.observaciones:
                notas_partes.append(doc.observaciones)
            nota_final = "\n".join(notas_partes)

            order_id = escritor.crear_pedido(
                partner_id=partner_id,
                lineas=lineas_odoo,
                marca=marca,
                nota=nota_final,
                vendedor_id=escritor.vendedor_id(doc.vendedor_email),
                shipping_id=None,
                fecha_entrega=doc.fecha_entrega.isoformat() if doc.fecha_entrega else None,
            )
            order_name = escritor.nombre_pedido(order_id)
        except DocumentoInvalido:
            raise
        except OdooEscrituraError as e:
            self._repo.actualizar(documento_id, {"estado": ERROR_ODOO, "error_odoo": str(e)[:2000]})
            raise

        logger.info(
            "Documento %s enviado a Odoo por %s -> sale.order %s (%s)",
            doc.numero,
            actor,
            order_id,
            order_name,
        )
        return self._repo.actualizar(
            documento_id,
            {
                "estado": ENVIADO_ODOO,
                "partner_odoo_id": partner_id,
                "tipo_contacto": "cliente" if doc.tipo == PEDIDO else doc.tipo_contacto,
                "odoo_order_id": order_id,
                "odoo_order_name": order_name,
                "error_odoo": "",
            },
        )
