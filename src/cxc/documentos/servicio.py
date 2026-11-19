"""Servicio de Documentos Comerciales (Cotizaciones y Pedidos) integrados con CxC y Odoo."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from cxc.capturas.modelos import vacio, validar_gps
from cxc.decimal_utils import q2
from cxc.odoo.escritura import EscritorOdoo, OdooEscrituraError
from cxc.repositories import Repository

from .catalogo import (
    LISTA_POR_DEFECTO_FALLBACK,
    ItemCatalogo,
    ListaComercialActiva,
    ProveedorCatalogo,
    resolver_producto_unico,
)
from .evaluador_descuentos import (
    IVA_VENEZUELA,
    evaluar_descuentos_cotizacion,
    obtener_catalogo_descuentos,
)
from .modelos import (
    BORRADOR,
    COTIZACION,
    ENVIADO_ODOO,
    ERROR_ODOO,
    MONEDAS_LISTA,
    PEDIDO,
    PENDIENTE_APROBACION,
    RECHAZADO,
    TIPOS_DOCUMENTO,
    DocumentoComercial,
    DocumentoInvalido,
    calcular_subtotal_linea,
    decimal_limpio,
    normalizar_rif_opcional,
    parsear_fecha_opcional,
)
from .politica_ventas import evaluar_politica_auto_envio
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


def _extraer_codigos_descuento(datos: dict[str, Any]) -> list[str]:
    crudo = datos.get("descuentos_codigos")
    if crudo is None:
        crudo = datos.get("descuentos")
    if not crudo or not isinstance(crudo, list | tuple):
        return []
    codigos: list[str] = []
    for item in crudo:
        if isinstance(item, str) and item.strip():
            codigos.append(item.strip())
        elif isinstance(item, dict):
            cod = str(item.get("codigo") or item.get("regla_id") or "").strip()
            if cod:
                codigos.append(cod)
    return list(dict.fromkeys(codigos))


def _construir_nota_odoo(doc: DocumentoComercial, *, actor: str, auto_enviado: bool = False) -> str:
    """Construye la nota detallada para el documento y el chatter de Odoo."""
    etiqueta_doc = "Cotización" if doc.tipo == COTIZACION else "Pedido"
    lista_lbl = doc.lista_precios_nombre or f"Lista {doc.moneda_lista}"
    cab_aut = (
        f"Auto-aprobado por política CxC (cumple todos los parámetros · {actor})"
        if auto_enviado
        else f"Autorizado / enviado desde CxC por: {actor}"
    )
    lineas_nota: list[str] = [
        f"{etiqueta_doc} CxC {doc.numero} · Lista comercial: {lista_lbl}",
        cab_aut,
        f"Subtotal precios normales de lista: ${doc.subtotal:.2f}",
    ]

    # Descuentos manuales heredados en líneas de CxC (si existieran) + descuentos de catálogo CxC
    desc_lineas_cxc = [ln for ln in doc.lineas if ln.descuento_pct > Decimal("0")]
    tiene_descuentos = bool(doc.descuentos_elegidos or desc_lineas_cxc)

    if tiene_descuentos:
        lineas_nota.append("")
        lineas_nota.append("DESCUENTOS OTORGADOS EN CxC (NO insertados en líneas de Odoo):")
        for d in doc.descuentos_elegidos:
            cod = str(d.get("codigo") or "")
            tit = str(d.get("titulo") or cod)
            es_obs = bool(d.get("es_obsequio"))
            cumple = bool(d.get("cumple", True))
            estado_m = "Cumple parámetros motor" if cumple else f"ALERTA MOTOR: {d.get('alerta')}"
            if es_obs:
                obs_det = str(d.get("obsequio_detalle") or "Obsequio promocional")
                lineas_nota.append(f"• [{cod}] {tit}: {obs_det} ({estado_m})")
            else:
                pct = float(d.get("porcentaje") or 0.0)
                mto = float(d.get("monto_estimado_usd") or 0.0)
                cond = str(d.get("condicion") or "").strip()
                extra_c = f" · {cond}" if cond else ""
                lineas_nota.append(
                    f"• [{cod}] {tit}: {pct:.2f}% (-${mto:.2f}){extra_c} [{estado_m}]"
                )
        for ln in desc_lineas_cxc:
            ahorro_ln = q2(ln.cantidad * ln.precio_unitario * (ln.descuento_pct / Decimal("100")))
            lineas_nota.append(
                f"• Descuento comercial en CxC sobre «{ln.descripcion}»: "
                f"{ln.descuento_pct:.2f}% (-${ahorro_ln:.2f})"
            )
        sub_neto = (
            doc.subtotal_neto
            if doc.subtotal_neto > Decimal("0") or doc.total_descuentos > Decimal("0")
            else doc.subtotal
        )
        iva_est = (
            doc.monto_iva if doc.total_con_iva > Decimal("0") else q2(sub_neto * IVA_VENEZUELA)
        )
        tot_est = doc.total_con_iva if doc.total_con_iva > Decimal("0") else q2(sub_neto + iva_est)
        lineas_nota.append(
            f"Resumen proyectado CxC: Total descuentos -${doc.total_descuentos:.2f} | "
            f"Base neta ${sub_neto:.2f} | IVA (16%) ${iva_est:.2f} | Total est. ${tot_est:.2f}"
        )
        lineas_nota.append(
            "Nota: Los descuentos quedan registrados en CxC sujetos a verificación de parámetros "
            "del motor al momento de cobro y facturación."
        )

    if doc.condiciones_negociacion:
        lineas_nota.append("")
        lineas_nota.append(f"Condiciones de negociación: {doc.condiciones_negociacion}")
    if doc.observaciones:
        lineas_nota.append(f"Observaciones: {doc.observaciones}")

    return "\n".join(lineas_nota)


class ServicioDocumentos:
    """Gestiona Cotizaciones, Previews, Aprobación de Pedidos en CxC y envío a Odoo."""

    def __init__(
        self,
        repo: RepoDocumentos,
        catalogo: ProveedorCatalogo,
        fabrica_escritor: FabricaEscritor,
        repo_cxc: Repository | None = None,
    ) -> None:
        self._repo = repo
        self._catalogo = catalogo
        self._fabrica = fabrica_escritor
        self._repo_cxc = repo_cxc

    def lista_comercial_defecto(self, *, refresh: bool = False) -> ListaComercialActiva:
        fn = getattr(self._catalogo, "lista_comercial_defecto", None)
        if callable(fn):
            res = fn(refresh=refresh)
            if isinstance(res, ListaComercialActiva):
                return res
        return LISTA_POR_DEFECTO_FALLBACK

    def catalogo_items(self, *, refresh: bool = False) -> list[ItemCatalogo]:
        return self._catalogo.listar(refresh=refresh)

    def catalogo_descuentos(self, *, lista_precios_id: str | None = None) -> list[dict[str, Any]]:
        lid = lista_precios_id or self.lista_comercial_defecto().lista_id
        return obtener_catalogo_descuentos(self._repo_cxc, lista_precios_id=lid)

    def evaluar_descuentos(self, datos: dict[str, Any]) -> dict[str, Any]:
        lista_def = self.lista_comercial_defecto()
        moneda_raw = str(datos.get("moneda_lista") or lista_def.moneda or "VES").strip().upper()
        moneda_lista = moneda_raw if moneda_raw in MONEDAS_LISTA else "VES"
        lista_id = str(
            datos.get("lista_precios_id")
            or (lista_def.lista_id if moneda_lista == "VES" else "USD")
        )
        lista_nom = str(
            datos.get("lista_precios_nombre")
            or (lista_def.nombre if moneda_lista == "VES" else "Lista Comercial USD")
        )

        # Enriquece las líneas contra el catálogo real para tener precio, marca, envase y litros
        catalogo_actual = self._catalogo.listar()
        lineas_enriq: list[dict[str, Any]] = []
        for cruda in datos.get("lineas") or []:
            if not isinstance(cruda, dict):
                continue
            pid_in = str(cruda.get("producto_id") or "").strip() or None
            desc_in = str(
                cruda.get("descripcion") or cruda.get("producto") or cruda.get("codigo") or ""
            ).strip()
            elegido, _ = resolver_producto_unico(
                catalogo_actual,
                producto_id=pid_in,
                texto=str(cruda.get("codigo") or desc_in),
            )
            cant = decimal_limpio(cruda.get("cantidad", 1), "cantidad")
            tiene_precio = not vacio(cruda.get("precio_unitario"))
            precio_in = (
                decimal_limpio(cruda.get("precio_unitario"), "precio_unitario", minimo=Decimal("0"))
                if tiene_precio
                else None
            )
            if elegido is not None:
                precio_cat = elegido.precio_usd if moneda_lista == "USD" else elegido.precio_ves
                precio_u = (
                    precio_in
                    if (precio_in is not None and precio_in > Decimal("0"))
                    else precio_cat
                )
                lineas_enriq.append(
                    {
                        "producto_id": elegido.producto_id,
                        "codigo": elegido.codigo,
                        "descripcion": elegido.nombre,
                        "marca": elegido.marca,
                        "presentacion": elegido.presentacion,
                        "litros_unidad": elegido.litros,
                        "cantidad": cant,
                        "precio_unitario": precio_u,
                    }
                )
            else:
                lineas_enriq.append(
                    {
                        "producto_id": pid_in or "",
                        "codigo": str(cruda.get("codigo") or ""),
                        "descripcion": desc_in,
                        "marca": str(cruda.get("marca") or ""),
                        "presentacion": str(cruda.get("presentacion") or ""),
                        "litros_unidad": decimal_limpio(
                            cruda.get("litros_unidad", 0), "litros_unidad"
                        ),
                        "cantidad": cant,
                        "precio_unitario": precio_in if precio_in is not None else Decimal("0"),
                    }
                )

        pid_partner = datos.get("partner_odoo_id")
        partner_int = (
            int(str(pid_partner))
            if pid_partner not in (None, "", False) and str(pid_partner).isdigit()
            else None
        )
        dias_cred = datos.get("dias_credito")
        dias_int = (
            int(str(dias_cred))
            if dias_cred not in (None, "", False) and str(dias_cred).isdigit()
            else None
        )
        return evaluar_descuentos_cotizacion(
            self._repo_cxc,
            lineas=lineas_enriq,
            descuentos_codigos=_extraer_codigos_descuento(datos),
            lista_precios_id=lista_id,
            lista_precios_nombre=lista_nom,
            moneda_lista=moneda_lista,
            partner_odoo_id=partner_int,
            rif=normalizar_rif_opcional(datos.get("rif")),
            cliente_nombre=str(datos.get("cliente_nombre") or datos.get("cliente") or "").strip(),
            es_primera_compra=(
                bool(datos["es_primera_compra"])
                if datos.get("es_primera_compra") is not None
                else None
            ),
            moneda_pago_esperada=datos.get("moneda_pago_esperada"),
            dias_credito_solicitados=dias_int,
        )

    def obtener(self, documento_id: int) -> DocumentoComercial:
        doc = self._repo.obtener(documento_id)
        if doc is None:
            raise DocumentoNoEncontrado(str(documento_id))
        return doc

    def obtener_por_token(self, token_preview: str) -> DocumentoComercial:
        doc = self._repo.obtener_por_token(token_preview)
        if doc is None:
            raise DocumentoNoEncontrado(token_preview)
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
        token_existente: str = "",
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        tipo = str(datos.get("tipo") or COTIZACION).strip().lower()
        if tipo not in TIPOS_DOCUMENTO:
            raise DocumentoInvalido(f"Tipo de documento inválido: {tipo!r}.", ["tipo"])

        cliente = str(datos.get("cliente_nombre") or datos.get("cliente") or "").strip()
        if not cliente:
            raise DocumentoInvalido("Indica el nombre del cliente o prospecto.", ["cliente_nombre"])

        lista_def = self.lista_comercial_defecto()
        moneda_raw = str(datos.get("moneda_lista") or "VES").strip().upper()
        moneda_lista = moneda_raw if moneda_raw in MONEDAS_LISTA else "VES"
        if moneda_lista == "VES":
            lista_id = str(datos.get("lista_precios_id") or lista_def.lista_id)
            lista_nombre = str(datos.get("lista_precios_nombre") or lista_def.nombre)
        else:
            lista_id = str(datos.get("lista_precios_id") or "USD")
            lista_nombre = str(datos.get("lista_precios_nombre") or "Pago USD Comercial")

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
        lineas_para_motor: list[dict[str, Any]] = []
        subtotal_doc = Decimal("0")
        subtotal_bruto_lista = Decimal("0")
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

            marca_ln = ""
            if elegido is not None:
                producto_id = elegido.producto_id
                codigo = elegido.codigo
                descripcion = elegido.nombre
                marca_ln = elegido.marca
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
                marca_ln = str(cruda.get("marca") or "").strip()
                presentacion = str(cruda.get("presentacion") or "").strip()
                litros_u = decimal_limpio(cruda.get("litros_unidad", 0), "litros_unidad")
                precio_u = precio_in if precio_in is not None else Decimal("0")

            sub_bruto = q2(cantidad * precio_u)
            sub_ln = calcular_subtotal_linea(cantidad, precio_u, descuento_pct)
            subtotal_bruto_lista += sub_bruto
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
            lineas_para_motor.append(
                {
                    "producto_id": producto_id,
                    "codigo": codigo,
                    "descripcion": descripcion,
                    "marca": marca_ln,
                    "presentacion": presentacion,
                    "litros_unidad": litros_u,
                    "cantidad": cantidad,
                    "precio_unitario": precio_u,
                }
            )

        codigos_desc = _extraer_codigos_descuento(datos)
        eval_res = evaluar_descuentos_cotizacion(
            self._repo_cxc,
            lineas=lineas_para_motor,
            descuentos_codigos=codigos_desc,
            lista_precios_id=lista_id,
            lista_precios_nombre=lista_nombre,
            moneda_lista=moneda_lista,
            partner_odoo_id=partner_odoo_id,
            rif=rif,
            cliente_nombre=cliente,
            es_primera_compra=es_primera,
            moneda_pago_esperada=datos.get("moneda_pago_esperada"),
        )

        # Descuento total = descuentos del catálogo CxC + cualquier descuento manual de línea en CxC
        ahorro_manual_lineas = q2(subtotal_bruto_lista - subtotal_doc)
        total_desc_cat = Decimal(str(eval_res["total_descuentos"]))
        total_descuentos = q2(total_desc_cat + ahorro_manual_lineas)
        if total_descuentos > subtotal_bruto_lista:
            total_descuentos = subtotal_bruto_lista

        # Subtotal refleja lista normal y subtotal_neto la base con descuento
        subtotal_mostrar = (
            subtotal_doc if ahorro_manual_lineas > Decimal("0") else subtotal_bruto_lista
        )
        subtotal_neto = q2(subtotal_bruto_lista - total_descuentos)
        monto_iva = q2(subtotal_neto * IVA_VENEZUELA)
        total_con_iva = q2(subtotal_neto + monto_iva)

        token_preview = (
            token_existente
            or str(datos.get("token_preview") or "").strip()
            or secrets.token_urlsafe(18)
        )

        cabecera: dict[str, Any] = {
            "token_preview": token_preview,
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
            "lista_precios_id": lista_id,
            "lista_precios_nombre": lista_nombre,
            "vendedor_email": str(datos.get("vendedor_email") or "").strip(),
            "fecha_entrega": parsear_fecha_opcional(datos.get("fecha_entrega")),
            "condiciones_negociacion": str(datos.get("condiciones_negociacion") or "").strip(),
            "observaciones": str(datos.get("observaciones") or datos.get("notas") or "").strip(),
            "descuentos_elegidos": eval_res["descuentos"],
            "validacion_motor": eval_res,
            "subtotal": subtotal_mostrar,
            "total_descuentos": total_descuentos,
            "subtotal_neto": subtotal_neto,
            "monto_iva": monto_iva,
            "total_con_iva": total_con_iva,
            "total_litros": litros_doc,
            "creado_por": actor,
        }
        return cabecera, lineas_limpias

    @staticmethod
    def _estado_inicial(tipo: str) -> str:
        return PENDIENTE_APROBACION if tipo == PEDIDO else BORRADOR

    def evaluar_politica(self, doc: DocumentoComercial) -> dict[str, Any]:
        """Evalúa la política de auto-envío y límites de crédito para un documento."""
        return evaluar_politica_auto_envio(
            doc,
            self._repo_cxc,
            engine=getattr(self._repo, "_engine", None),
        )

    def _aplicar_politica_y_auto_envio(
        self, doc: DocumentoComercial, *, actor: str
    ) -> DocumentoComercial:
        """Envía a Odoo directo si cumple todos los parámetros; si no, retiene con motivos."""
        if doc.tipo != PEDIDO or doc.estado == ENVIADO_ODOO:
            return doc

        eval_pol = self.evaluar_politica(doc)
        motivos = list(eval_pol.get("motivos_retencion") or ())

        if eval_pol.get("puede_auto_enviar"):
            escritor = self._fabrica()
            if escritor is None:
                motivos.append("Escritura a Odoo desactivada en este entorno.")
                eval_pol = {
                    **eval_pol,
                    "puede_auto_enviar": False,
                    "motivos_retencion": motivos,
                }
                return self._repo.actualizar(
                    doc.documento_id,
                    {
                        "estado": PENDIENTE_APROBACION,
                        "auto_enviado": False,
                        "motivos_retencion": motivos,
                        "evaluacion_politica": eval_pol,
                    },
                )
            self._repo.actualizar(
                doc.documento_id,
                {
                    "motivos_retencion": (),
                    "evaluacion_politica": eval_pol,
                },
            )
            try:
                return self.enviar_a_odoo(
                    doc.documento_id,
                    actor=actor,
                    registrar_aprobacion=True,
                    auto_enviado=True,
                )
            except (DocumentoInvalido, OdooEscrituraError) as exc:
                motivo_err = getattr(exc, "mensaje", None) or str(exc)
                motivos.append(motivo_err)
                eval_pol = {
                    **eval_pol,
                    "puede_auto_enviar": False,
                    "motivos_retencion": motivos,
                }
                return self._repo.actualizar(
                    doc.documento_id,
                    {
                        "estado": PENDIENTE_APROBACION,
                        "auto_enviado": False,
                        "motivos_retencion": motivos,
                        "evaluacion_politica": eval_pol,
                        "error_odoo": "",
                    },
                )

        return self._repo.actualizar(
            doc.documento_id,
            {
                "auto_enviado": False,
                "motivos_retencion": motivos,
                "evaluacion_politica": eval_pol,
            },
        )

    def crear(
        self,
        datos: dict[str, Any],
        *,
        origen: str = "web",
        captura_id: int | None = None,
        actor: str = "sistema",
        auto_enviar: bool = True,
    ) -> DocumentoComercial:
        if captura_id is not None:
            previo = self._repo.obtener_por_captura(captura_id)
            if previo is not None:
                if previo.estado == ENVIADO_ODOO:
                    return previo
                cab, lns = self._preparar_cabecera_y_lineas(
                    datos,
                    origen=origen,
                    captura_id=captura_id,
                    actor=actor,
                    token_existente=previo.token_preview,
                )
                cab.pop("creado_por", None)
                cab["estado"] = self._estado_inicial(cab["tipo"])
                cab["error_odoo"] = ""
                doc_upd = self._repo.actualizar(previo.documento_id, cab, lns)
                if cab["tipo"] == PEDIDO and auto_enviar:
                    return self._aplicar_politica_y_auto_envio(doc_upd, actor=actor)
                return doc_upd
        cab, lns = self._preparar_cabecera_y_lineas(
            datos, origen=origen, captura_id=captura_id, actor=actor
        )
        cab["estado"] = self._estado_inicial(cab["tipo"])
        doc = self._repo.crear(cab, lns)
        if cab["tipo"] == PEDIDO and auto_enviar:
            return self._aplicar_politica_y_auto_envio(doc, actor=actor)
        return doc

    def editar(
        self,
        documento_id: int,
        datos: dict[str, Any],
        *,
        actor: str = "sistema",
        auto_enviar: bool = True,
    ) -> DocumentoComercial:
        existente = self.obtener(documento_id)
        if existente.estado == ENVIADO_ODOO:
            ref = existente.odoo_order_name or existente.odoo_order_id
            raise DocumentoYaEnviado(
                f"El documento {existente.numero} ya fue enviado a Odoo ({ref})."
            )
        descuentos_previos = [
            d.get("codigo") for d in existente.descuentos_elegidos if d.get("codigo")
        ]
        tiene_desc_nuevo = "descuentos" in datos or "descuentos_codigos" in datos
        combinado: dict[str, Any] = {
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
            "lista_precios_id": datos.get("lista_precios_id", existente.lista_precios_id),
            "lista_precios_nombre": datos.get(
                "lista_precios_nombre", existente.lista_precios_nombre
            ),
            "vendedor_email": datos.get("vendedor_email", existente.vendedor_email),
            "fecha_entrega": datos.get("fecha_entrega", existente.fecha_entrega),
            "condiciones_negociacion": datos.get(
                "condiciones_negociacion", existente.condiciones_negociacion
            ),
            "observaciones": datos.get("observaciones", existente.observaciones),
            "descuentos_codigos": (
                _extraer_codigos_descuento(datos) if tiene_desc_nuevo else descuentos_previos
            ),
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
            token_existente=existente.token_preview,
        )
        cab.pop("creado_por", None)
        cab["estado"] = self._estado_inicial(cab["tipo"])
        cab["error_odoo"] = ""
        actualizado = self._repo.actualizar(documento_id, cab, lns)
        if (
            actualizado.tipo == PEDIDO
            and actualizado.estado == PENDIENTE_APROBACION
            and auto_enviar
        ):
            return self._aplicar_politica_y_auto_envio(actualizado, actor=actor)
        return actualizado

    def convertir_en_pedido(
        self,
        documento_id: int,
        datos_extra: dict[str, Any] | None = None,
        *,
        actor: str = "vendedor",
    ) -> DocumentoComercial:
        """Convierte una Cotización en Pedido y evalúa si auto-envía a Odoo o queda retenido."""
        existente = self.obtener(documento_id)
        if existente.estado == ENVIADO_ODOO:
            ref = existente.odoo_order_name or existente.odoo_order_id
            raise DocumentoYaEnviado(
                f"El documento {existente.numero} ya fue enviado a Odoo ({ref})."
            )
        extra = dict(datos_extra or {})
        if (
            existente.tipo == PEDIDO
            and existente.estado == PENDIENTE_APROBACION
            and not any(v not in (None, "") for v in extra.values())
        ):
            return existente

        cambios: dict[str, Any] = {"tipo": PEDIDO}
        for campo in (
            "rif",
            "email",
            "telefono",
            "direccion",
            "plus_code",
            "latitud",
            "longitud",
            "ubicacion",
            "fecha_entrega",
            "condiciones_negociacion",
            "observaciones",
            "partner_odoo_id",
            "es_primera_compra",
        ):
            if campo in extra and not vacio(extra[campo]):
                cambios[campo] = extra[campo]
        if "direccion_fiscal" in extra and not vacio(extra["direccion_fiscal"]):
            cambios["direccion"] = extra["direccion_fiscal"]
        if "descuentos_codigos" in extra or "descuentos" in extra:
            cambios["descuentos_codigos"] = _extraer_codigos_descuento(extra)

        actualizado = self.editar(documento_id, cambios, actor=actor, auto_enviar=False)
        if actualizado.estado != PENDIENTE_APROBACION:
            actualizado = self._repo.actualizar(
                documento_id, {"tipo": PEDIDO, "estado": PENDIENTE_APROBACION}
            )
        return self._aplicar_politica_y_auto_envio(actualizado, actor=actor)

    def convertir_por_token(
        self,
        token_preview: str,
        datos_extra: dict[str, Any] | None = None,
        *,
        actor: str = "preview_web",
    ) -> DocumentoComercial:
        doc = self.obtener_por_token(token_preview)
        return self.convertir_en_pedido(doc.documento_id, datos_extra, actor=actor)

    def rechazar_pedido(
        self, documento_id: int, *, motivo: str = "", actor: str = "sistema"
    ) -> DocumentoComercial:
        doc = self.obtener(documento_id)
        if doc.estado == ENVIADO_ODOO:
            ref = doc.odoo_order_name or doc.odoo_order_id
            raise DocumentoYaEnviado(f"El documento {doc.numero} ya fue enviado a Odoo ({ref}).")
        detalle = f"Rechazado por {actor}" + (f": {motivo.strip()}" if motivo.strip() else "")
        return self._repo.actualizar(
            documento_id,
            {"estado": RECHAZADO, "error_odoo": detalle[:2000]},
        )

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

    def aprobar_pedido(self, documento_id: int, *, actor: str) -> DocumentoComercial:
        """Autoriza un Pedido pendiente en CxC y lo envía a Odoo con precios normales + chatter."""
        doc = self.obtener(documento_id)
        if doc.tipo != PEDIDO:
            doc = self.convertir_en_pedido(documento_id, actor=actor)
        if doc.estado == ENVIADO_ODOO and doc.auto_enviado:
            return doc
        return self.enviar_a_odoo(doc.documento_id, actor=actor, registrar_aprobacion=True)

    def enviar_a_odoo(
        self,
        documento_id: int,
        *,
        actor: str = "sistema",
        registrar_aprobacion: bool = False,
        auto_enviado: bool = False,
    ) -> DocumentoComercial:
        """Envía a Odoo con precios normales de lista (sin discount en línea) + chatter."""
        doc = self.obtener(documento_id)
        if doc.estado == ENVIADO_ODOO:
            ref = doc.odoo_order_name or doc.odoo_order_id
            raise DocumentoYaEnviado(f"El documento {doc.numero} ya fue enviado a Odoo ({ref}).")

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
            # Regla de negocio: NUNCA se inserta discount en las líneas de sale.order en Odoo;
            # Odoo recibe siempre los precios normales de la lista comercial y el detalle completo
            # de los descuentos otorgados se registra como nota en el documento y en el chatter.
            lineas_odoo.append(
                {
                    "product_id": int(pid),
                    "cantidad": ln.cantidad,
                    "precio": precio_ln,
                    "descuento_pct": None,
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
        pricelist_odoo_id = (
            int(doc.lista_precios_id)
            if doc.lista_precios_id and doc.lista_precios_id.isdigit()
            else None
        )
        try:
            partner_id = self._resolver_partner_para_envio(doc, escritor, marca)
            nota_final = _construir_nota_odoo(doc, actor=actor, auto_enviado=auto_enviado)
            order_id = escritor.crear_pedido(
                partner_id=partner_id,
                lineas=lineas_odoo,
                marca=marca,
                nota=nota_final,
                vendedor_id=escritor.vendedor_id(doc.vendedor_email),
                shipping_id=None,
                fecha_entrega=doc.fecha_entrega.isoformat() if doc.fecha_entrega else None,
                pricelist_id=pricelist_odoo_id,
            )
            order_name = escritor.nombre_pedido(order_id)
        except DocumentoInvalido:
            raise
        except OdooEscrituraError as e:
            self._repo.actualizar(documento_id, {"estado": ERROR_ODOO, "error_odoo": str(e)[:2000]})
            raise

        logger.info(
            "Documento %s aprobado/enviado a Odoo por %s -> sale.order %s (%s)",
            doc.numero,
            actor,
            order_id,
            order_name,
        )
        campos_fin: dict[str, Any] = {
            "estado": ENVIADO_ODOO,
            "partner_odoo_id": partner_id,
            "tipo_contacto": "cliente" if doc.tipo == PEDIDO else doc.tipo_contacto,
            "odoo_order_id": order_id,
            "odoo_order_name": order_name,
            "error_odoo": "",
            "auto_enviado": auto_enviado,
        }
        if registrar_aprobacion or doc.tipo == PEDIDO:
            campos_fin["aprobado_por"] = f"auto_politica ({actor})" if auto_enviado else actor
            campos_fin["aprobado_en"] = datetime.now()
        return self._repo.actualizar(documento_id, campos_fin)
