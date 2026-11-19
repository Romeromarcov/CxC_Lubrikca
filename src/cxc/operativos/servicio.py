"""Servicio de Documentos Operativos (Pagos, Facturas de Proveedor, Pagos a Proveedor y Gastos)."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from cxc.capturas.modelos import es_efectivo
from cxc.capturas.repositorio import RepoCapturas
from cxc.capturas.servicio import _Incompleto, resolver_diario
from cxc.odoo.directorio import (
    ContactoOdoo,
    CuentaContable,
    proponer_clasificacion_gastos,
)
from cxc.odoo.escritura import (
    NOTA_PROVEEDOR_DE_DOCUMENTO,
    EscritorOdoo,
    OdooEscrituraError,
)
from cxc.repositories import Repository

from .modelos import (
    BORRADOR,
    CATEGORIAS_GASTO,
    ENVIADO_ODOO,
    ERROR_ODOO,
    INCOMPLETO,
    PENDIENTE_APROBACION,
    RECHAZADO,
    TIPO_GASTO,
    TIPO_PAGO,
    TIPO_PAGO_PROVEEDOR,
    TIPOS_OPERATIVOS,
    DocumentoOperativo,
    OperativoEstadoInvalido,
    OperativoInvalido,
    OperativoNoEncontrado,
    OperativoYaEnviado,
    extraer_campos_canonicos,
)
from .repositorio import RepoOperativos
from .validador import listar_diarios_bancarios, validar_documento_operativo

logger = logging.getLogger("cxc.operativos.servicio")

FabricaEscritor = Callable[[], EscritorOdoo | None]
ProveedorContactos = Callable[[], Sequence[ContactoOdoo]]
ProveedorCuentas = Callable[[], Sequence[CuentaContable]]


def _sin_lista(fn: ProveedorContactos | None) -> Sequence[ContactoOdoo]:
    if fn is None:
        return ()
    try:
        return fn()
    except Exception as e:
        logger.warning("No se pudo consultar contactos de Odoo para validación: %s", e)
        return ()


def _sin_cuentas(fn: ProveedorCuentas | None) -> Sequence[CuentaContable]:
    if fn is None:
        return ()
    try:
        return fn()
    except Exception as e:
        logger.warning("No se pudo consultar plan de cuentas de Odoo para validación: %s", e)
        return ()


class ServicioOperativos:
    """Gestiona ciclo de vida, validación en vivo, Preview, aprobación CxC y envío a Odoo."""

    def __init__(
        self,
        repo: RepoOperativos,
        fabrica_escritor: FabricaEscritor,
        *,
        repo_cxc: Repository | None = None,
        obtener_contactos: ProveedorContactos | None = None,
        obtener_cuentas: ProveedorCuentas | None = None,
        diarios_extra: dict[str, int] | None = None,
        repo_capturas: RepoCapturas | None = None,
    ) -> None:
        self._repo = repo
        self._fabrica = fabrica_escritor
        self._repo_cxc = repo_cxc
        self._obtener_contactos = obtener_contactos
        self._obtener_cuentas = obtener_cuentas
        self._diarios_extra = diarios_extra or {}
        self._repo_capturas = repo_capturas

    def catalogos(self) -> dict[str, Any]:
        cuentas = list(_sin_cuentas(self._obtener_cuentas))
        cuentas_gasto = [
            c.a_dict()
            for c in cuentas
            if c.grupo in ("gastos", "activo")
            and c.account_type not in ("asset_receivable", "asset_cash")
        ]
        propuestas = proponer_clasificacion_gastos(cuentas) if cuentas else []
        diarios_compras: list[dict[str, Any]] = []
        try:
            escritor = self._fabrica()
            if escritor is not None:
                ves_id = escritor.diario_compras_ves_id()
                if ves_id is not None:
                    diarios_compras.append(
                        {
                            "id": ves_id,
                            "nombre": "Diario Compras VES (Por defecto)",
                            "defecto": True,
                        }
                    )
        except Exception:
            pass

        return {
            "diarios_bancarios": listar_diarios_bancarios(self._diarios_extra),
            "diarios_compras": diarios_compras,
            "categorias_gasto": list(CATEGORIAS_GASTO),
            "cuentas_contables": cuentas_gasto[:400],
            "propuestas_categorias": propuestas,
        }

    def validar_en_vivo(
        self, tipo: str, raw_datos: dict[str, Any], *, excluir_id: int | None = None
    ) -> dict[str, Any]:
        if tipo not in TIPOS_OPERATIVOS:
            raise OperativoInvalido(f"Tipo de documento operativo inválido: {tipo!r}.")
        contactos = _sin_lista(self._obtener_contactos)
        cuentas = _sin_cuentas(self._obtener_cuentas)
        datos, validacion = validar_documento_operativo(
            tipo,
            raw_datos,
            excluir_id=excluir_id,
            repo_operativos=self._repo,
            repo_cxc=self._repo_cxc,
            contactos_odoo=contactos,
            cuentas_odoo=cuentas,
            diarios_extra=self._diarios_extra,
        )
        return {
            "tipo": tipo,
            "datos_normalizados": {
                k: (
                    v.isoformat()
                    if hasattr(v, "isoformat")
                    else (float(v) if isinstance(v, Decimal) else v)
                )
                for k, v in datos.items()
            },
            **validacion,
        }

    def _aplicar_sugerencias(
        self,
        datos: dict[str, Any],
        validacion: dict[str, Any],
        *,
        sobreescribir_manual: bool = False,
    ) -> None:
        contraparte = validacion.get("contraparte_resuelta")
        if isinstance(contraparte, dict):
            if not datos.get("partner_odoo_id") and contraparte.get("id"):
                datos["partner_odoo_id"] = int(contraparte["id"])
            if not datos.get("contraparte_nombre") and contraparte.get("nombre"):
                datos["contraparte_nombre"] = str(contraparte["nombre"])
            if not datos.get("rif") and contraparte.get("rif"):
                datos["rif"] = str(contraparte["rif"])

        diario_sug = validacion.get("diario_sugerido")
        if (
            isinstance(diario_sug, dict)
            and diario_sug.get("id")
            and (sobreescribir_manual or not datos.get("diario_id"))
        ):
            datos["diario_id"] = int(diario_sug["id"])
            datos["diario_nombre"] = str(diario_sug.get("nombre") or "")

        cuenta_sug = validacion.get("cuenta_sugerida")
        if (
            isinstance(cuenta_sug, dict)
            and cuenta_sug.get("id")
            and not datos.get("cuenta_contable_id")
        ):
            datos["cuenta_contable_id"] = int(cuenta_sug["id"])
            datos["cuenta_contable_codigo"] = str(cuenta_sug.get("code") or "")
            datos["cuenta_contable_nombre"] = str(cuenta_sug.get("name") or "")

        if datos.get("tipo") == TIPO_GASTO and not datos.get("categoria"):
            cat_sug = str(validacion.get("categoria_sugerida") or "").strip()
            if cat_sug:
                datos["categoria"] = cat_sug

    def crear(
        self,
        raw_datos: dict[str, Any],
        *,
        origen: str = "web",
        captura_id: int | None = None,
        actor: str = "",
    ) -> DocumentoOperativo:
        if captura_id is not None:
            previo = self._repo.obtener_por_captura(captura_id)
            if previo is not None:
                if previo.estado == ENVIADO_ODOO:
                    return previo
                return self.editar(previo.documento_id, raw_datos, actor=actor or "bot")

        tipo = str(raw_datos.get("tipo") or raw_datos.get("kind") or "").strip()
        if tipo not in TIPOS_OPERATIVOS:
            raise OperativoInvalido(f"Tipo de documento operativo inválido: {tipo!r}.")

        contactos = _sin_lista(self._obtener_contactos)
        cuentas = _sin_cuentas(self._obtener_cuentas)
        datos, validacion = validar_documento_operativo(
            tipo,
            raw_datos,
            repo_operativos=self._repo,
            repo_cxc=self._repo_cxc,
            contactos_odoo=contactos,
            cuentas_odoo=cuentas,
            diarios_extra=self._diarios_extra,
        )
        self._aplicar_sugerencias(datos, validacion)

        faltantes = list(validacion.get("faltantes") or [])
        estado = INCOMPLETO if faltantes else PENDIENTE_APROBACION

        campos = {
            **datos,
            "token_preview": secrets.token_urlsafe(18),
            "estado": estado,
            "origen": origen,
            "captura_id": captura_id,
            "validacion": validacion,
            "faltantes": faltantes,
            "creado_por": actor or ("bot_telegram" if origen == "bot" else "usuario"),
        }
        return self._repo.crear(campos)

    def obtener(self, documento_id: int) -> DocumentoOperativo:
        doc = self._repo.obtener(documento_id)
        if doc is None:
            raise OperativoNoEncontrado(f"Documento operativo #{documento_id} no encontrado.")
        return doc

    def obtener_por_token(self, token_preview: str) -> DocumentoOperativo:
        doc = self._repo.obtener_por_token(token_preview)
        if doc is None:
            raise OperativoNoEncontrado("Comprobante no encontrado.")
        return doc

    def listar(
        self,
        *,
        tipo: str | None = None,
        grupo: str | None = None,
        estado: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DocumentoOperativo], int, dict[str, int]]:
        return self._repo.listar(
            tipo=tipo, grupo=grupo, estado=estado, q=q, limit=limit, offset=offset
        )

    def _merge_para_edicion(
        self, doc: DocumentoOperativo, cambios: dict[str, Any]
    ) -> dict[str, Any]:
        """Combina valores actuales con cambios explícitos del editor."""
        base: dict[str, Any] = {
            "tipo": doc.tipo,
            "partner_odoo_id": doc.partner_odoo_id,
            "contraparte_nombre": doc.contraparte_nombre,
            "rif": doc.rif,
            "monto": doc.monto,
            "base_imponible": doc.base_imponible,
            "iva": doc.iva,
            "moneda": doc.moneda,
            "metodo_pago": doc.metodo_pago,
            "banco": doc.banco,
            "diario_id": doc.diario_id,
            "diario_nombre": doc.diario_nombre,
            "cuenta_contable_id": doc.cuenta_contable_id,
            "cuenta_contable_codigo": doc.cuenta_contable_codigo,
            "cuenta_contable_nombre": doc.cuenta_contable_nombre,
            "referencia": doc.referencia,
            "numero_documento": doc.numero_documento,
            "numero_control": doc.numero_control,
            "pedido_ref": doc.pedido_ref,
            "factura_ref": doc.factura_ref,
            "orden_ref": doc.orden_ref,
            "categoria": doc.categoria,
            "concepto": doc.concepto,
            "sin_factura": doc.sin_factura,
            "fecha_documento": doc.fecha_documento.isoformat() if doc.fecha_documento else None,
            "fecha_vencimiento": (
                doc.fecha_vencimiento.isoformat() if doc.fecha_vencimiento else None
            ),
            "proveedor_telefono": doc.proveedor_telefono,
            "proveedor_email": doc.proveedor_email,
            "proveedor_direccion": doc.proveedor_direccion,
            "notas": doc.notas,
        }
        cambios_norm = dict(cambios)
        if "contraparte_nombre" not in cambios_norm:
            for alias in ("cliente", "proveedor", "nombre"):
                if alias in cambios_norm and cambios_norm[alias] not in (None, ""):
                    cambios_norm["contraparte_nombre"] = cambios_norm[alias]
                    break
        if "monto" not in cambios_norm and "monto_total" in cambios_norm:
            cambios_norm["monto"] = cambios_norm["monto_total"]
        if "numero_documento" not in cambios_norm and "numero_factura" in cambios_norm:
            cambios_norm["numero_documento"] = cambios_norm["numero_factura"]
        if "fecha_documento" not in cambios_norm:
            for alias_f in ("fecha_pago", "fecha_emision"):
                if alias_f in cambios_norm:
                    cambios_norm["fecha_documento"] = cambios_norm[alias_f]
                    break

        norm_cambios = extraer_campos_canonicos(doc.tipo, {**base, **cambios_norm})
        # Respetar borrado explícito de campos clave cuando vienen como "" o None en cambios
        for k in (
            "referencia",
            "numero_documento",
            "numero_control",
            "banco",
            "diario_id",
            "cuenta_contable_id",
            "partner_odoo_id",
            "pedido_ref",
            "factura_ref",
            "orden_ref",
            "notas",
        ):
            if k in cambios and cambios[k] in (None, ""):
                norm_cambios[k] = None if k.endswith("_id") else ""
        if "banco" in cambios and "diario_id" not in cambios:
            norm_cambios["diario_id"] = None
            norm_cambios["diario_nombre"] = ""
        if doc.tipo == TIPO_GASTO and "sin_factura" not in cambios:
            norm_cambios["sin_factura"] = not bool(norm_cambios.get("numero_documento"))
        elif cambios.get("sin_factura") is True and not cambios.get("numero_documento"):
            norm_cambios["numero_documento"] = ""
        return norm_cambios

    def editar(
        self, documento_id: int, cambios: dict[str, Any], *, actor: str = ""
    ) -> DocumentoOperativo:
        doc = self.obtener(documento_id)
        if doc.estado == ENVIADO_ODOO:
            raise OperativoYaEnviado(f"{doc.numero} ya fue enviado a Odoo ({doc.odoo_ref}).")
        fusionado = self._merge_para_edicion(doc, cambios)
        contactos = _sin_lista(self._obtener_contactos)
        cuentas = _sin_cuentas(self._obtener_cuentas)
        datos, validacion = validar_documento_operativo(
            doc.tipo,
            fusionado,
            excluir_id=doc.documento_id,
            repo_operativos=self._repo,
            repo_cxc=self._repo_cxc,
            contactos_odoo=contactos,
            cuentas_odoo=cuentas,
            diarios_extra=self._diarios_extra,
        )
        # Si cambió cuenta_contable_id manualmente, completar código y nombre desde plan de cuentas
        if datos.get("cuenta_contable_id") and cuentas:
            cta_obj = next((c for c in cuentas if c.id == int(datos["cuenta_contable_id"])), None)
            if cta_obj:
                datos["cuenta_contable_codigo"] = cta_obj.code
                datos["cuenta_contable_nombre"] = cta_obj.name
        self._aplicar_sugerencias(datos, validacion)

        faltantes = list(validacion.get("faltantes") or [])
        nuevo_estado = INCOMPLETO if faltantes else PENDIENTE_APROBACION
        return self._repo.actualizar(
            doc.documento_id,
            {
                **datos,
                "estado": nuevo_estado,
                "validacion": validacion,
                "faltantes": faltantes,
                "error_odoo": "",
            },
        )

    def confirmar_por_token(
        self, token_preview: str, cambios: dict[str, Any] | None = None, *, actor: str = "preview"
    ) -> DocumentoOperativo:
        """Completa/confirma datos desde el Preview público y deja en ``PENDIENTE_APROBACION``.

        Nunca escribe en Odoo: la autorización final pertenece solo al panel con sesión y permiso.
        """
        doc = self.obtener_por_token(token_preview)
        if doc.estado == ENVIADO_ODOO:
            raise OperativoYaEnviado(f"{doc.numero} ya fue enviado a Odoo ({doc.odoo_ref}).")
        if doc.estado == RECHAZADO:
            raise OperativoEstadoInvalido(f"{doc.numero} fue rechazado y no puede reactivarse.")
        actualizado = self.editar(doc.documento_id, cambios or {}, actor=actor)
        if actualizado.faltantes:
            raise OperativoInvalido(
                "Faltan datos obligatorios: " + ", ".join(actualizado.faltantes),
                list(actualizado.faltantes),
            )
        return actualizado

    def rechazar(
        self, documento_id: int, *, motivo: str = "", actor: str = ""
    ) -> DocumentoOperativo:
        doc = self.obtener(documento_id)
        if doc.estado == ENVIADO_ODOO:
            raise OperativoYaEnviado(
                f"{doc.numero} ya está en Odoo ({doc.odoo_ref}) y no puede rechazarse aquí."
            )
        return self._repo.actualizar(
            doc.documento_id,
            {
                "estado": RECHAZADO,
                "rechazo_motivo": motivo.strip(),
                "aprobado_por": actor,
                "aprobado_en": datetime.now(),
            },
        )

    def _verificar_partner_id(self, escritor: EscritorOdoo, pid: int | None) -> int | None:
        if pid is None:
            return None
        if escritor.leer_partner(pid) is None:
            return None
        return pid

    def _resolver_cliente_pago(self, doc: DocumentoOperativo, escritor: EscritorOdoo) -> int:
        pid = self._verificar_partner_id(escritor, doc.partner_odoo_id)
        if pid is not None:
            return pid
        if doc.rif:
            ids_r = escritor.partners_por_rif(doc.rif)
            if len(ids_r) == 1:
                return ids_r[0]
            if len(ids_r) > 1:
                raise OperativoInvalido(
                    f"El RIF {doc.rif} corresponde a varios contactos en Odoo; "
                    "selecciona el cliente en el editor.",
                    ["partner_odoo_id"],
                )
        if doc.pedido_ref and self._repo_cxc is not None:
            so = self._repo_cxc.get_orden(doc.pedido_ref.strip())
            if so is not None and str(so.cliente_id).isdigit():
                cid = int(str(so.cliente_id))
                if escritor.leer_partner(cid) is not None:
                    return cid
        if doc.contraparte_nombre:
            ids_n = escritor.partners_por_nombre(doc.contraparte_nombre)
            if len(ids_n) == 1:
                return ids_n[0]
            if len(ids_n) > 1:
                raise OperativoInvalido(
                    f"Hay {len(ids_n)} contactos llamados «{doc.contraparte_nombre}» "
                    "en Odoo; selecciona el correcto.",
                    ["partner_odoo_id"],
                )
        raise OperativoInvalido(
            "No pude identificar al cliente en Odoo. Edita el documento "
            "y selecciona el cliente exacto.",
            ["partner_odoo_id"],
        )

    def _resolver_proveedor_pago(self, doc: DocumentoOperativo, escritor: EscritorOdoo) -> int:
        pid = self._verificar_partner_id(escritor, doc.partner_odoo_id)
        if pid is not None:
            return pid
        if doc.rif:
            ids_r = escritor.partners_por_rif(doc.rif)
            if len(ids_r) == 1:
                return ids_r[0]
            if len(ids_r) > 1:
                raise OperativoInvalido(
                    f"El RIF {doc.rif} está duplicado en Odoo; "
                    "selecciona el proveedor en el editor.",
                    ["partner_odoo_id"],
                )
        if doc.contraparte_nombre:
            for buscador in (escritor.proveedores_por_nombre, escritor.partners_por_nombre):
                ids_n = buscador(doc.contraparte_nombre)
                if len(ids_n) == 1:
                    return ids_n[0]
                if len(ids_n) > 1:
                    raise OperativoInvalido(
                        f"«{doc.contraparte_nombre}» es ambiguo en Odoo; "
                        "selecciona el proveedor correcto.",
                        ["partner_odoo_id"],
                    )
        raise OperativoInvalido(
            "El proveedor no existe en Odoo. Selecciona un proveedor o créalo primero.",
            ["partner_odoo_id"],
        )

    def _resolver_o_crear_proveedor_doc(
        self, doc: DocumentoOperativo, escritor: EscritorOdoo, marca: str
    ) -> tuple[int, bool]:
        pid = self._verificar_partner_id(escritor, doc.partner_odoo_id)
        if pid is not None:
            return pid, False
        propio = escritor.partner_por_marca(marca)
        if propio is not None:
            return propio, True
        if doc.rif:
            ids_r = escritor.partners_por_rif(doc.rif)
            if len(ids_r) == 1:
                return ids_r[0], False
            if len(ids_r) > 1:
                raise OperativoInvalido(
                    f"El RIF {doc.rif} aparece en {len(ids_r)} contactos de Odoo; "
                    "selecciona el proveedor.",
                    ["partner_odoo_id"],
                )
        if doc.contraparte_nombre:
            for buscador in (escritor.proveedores_por_nombre, escritor.partners_por_nombre):
                ids_n = buscador(doc.contraparte_nombre)
                if len(ids_n) == 1:
                    return ids_n[0], False
                if len(ids_n) > 1:
                    raise OperativoInvalido(
                        f"Hay {len(ids_n)} contactos llamados «{doc.contraparte_nombre}»; "
                        "selecciona el proveedor.",
                        ["partner_odoo_id"],
                    )
        if not doc.contraparte_nombre:
            raise OperativoInvalido(
                "Indica el nombre del proveedor para poder crearlo en Odoo.",
                ["contraparte_nombre"],
            )
        nuevo_id = escritor.crear_proveedor(
            {
                "nombre": doc.contraparte_nombre,
                "rif": doc.rif or None,
                "telefono": doc.proveedor_telefono or None,
                "email": doc.proveedor_email or None,
                "direccion": doc.proveedor_direccion or None,
                "nota": NOTA_PROVEEDOR_DE_DOCUMENTO,
            },
            marca,
        )
        return nuevo_id, True

    def _enviar_pago(
        self, doc: DocumentoOperativo, escritor: EscritorOdoo, marca: str, *, actor: str
    ) -> tuple[str, int, str, int, int]:
        es_cliente = doc.tipo == TIPO_PAGO
        partner_id = (
            self._resolver_cliente_pago(doc, escritor)
            if es_cliente
            else self._resolver_proveedor_pago(doc, escritor)
        )
        moneda_id = escritor.moneda_id(doc.moneda)
        if moneda_id is None:
            raise OperativoInvalido(f"Moneda «{doc.moneda}» no existe en Odoo.", ["moneda"])

        journal_id = doc.diario_id
        if journal_id is None:
            try:
                journal_id = resolver_diario(
                    {"moneda": doc.moneda, "banco": doc.banco, "metodo_pago": doc.metodo_pago},
                    self._diarios_extra,
                )
            except _Incompleto as e:
                raise OperativoInvalido(
                    "Selecciona el diario bancario o caja antes de autorizar.", ["diario_id"]
                ) from e

        factura_txt = f"Factura {doc.factura_ref}" if doc.factura_ref else ""
        if not es_cliente and doc.factura_ref:
            fid = escritor.factura_proveedor_por_ref(partner_id, doc.factura_ref)
            if fid is not None:
                factura_txt += f" [account.move {fid}]"

        # Regla estricta Odoo: memo vacío si efectivo; SOLO referencia si transferencia/digital
        memo_limpio = (
            None
            if es_efectivo(doc.metodo_pago)
            else (doc.referencia.strip() if doc.referencia.strip() else None)
        )
        etq = "Pago recibido" if es_cliente else "Pago a proveedor"
        partes = [
            f"{marca} · {etq} autorizado en CxC por {actor}",
            f"Contraparte: {doc.contraparte_nombre}",
            f"Monto: {doc.monto} {doc.moneda}",
            f"Método: {doc.metodo_pago}" if doc.metodo_pago else "",
            f"Banco/Diario: {doc.diario_nombre or doc.banco}"
            if (doc.diario_nombre or doc.banco)
            else "",
            f"Ref: {doc.referencia}" if doc.referencia else "Sin referencia (efectivo)",
            f"Aplica a pedido: {doc.pedido_ref}" if (es_cliente and doc.pedido_ref) else "",
            factura_txt,
            f"Notas: {doc.notas}" if doc.notas else "",
        ]
        nota_chatter = "\n".join(p for p in partes if p)
        fecha_str = doc.fecha_documento.isoformat() if doc.fecha_documento else None
        assert doc.monto is not None
        pago_id = escritor.crear_pago(
            partner_id=partner_id,
            monto=doc.monto,
            journal_id=journal_id,
            moneda_id=moneda_id,
            es_cliente=es_cliente,
            marca=marca,
            memo=memo_limpio,
            nota_chatter=nota_chatter,
            fecha=fecha_str,
        )
        return "account.payment", pago_id, f"account.payment:{pago_id}", partner_id, journal_id

    def _enviar_documento_compra(
        self, doc: DocumentoOperativo, escritor: EscritorOdoo, marca: str, *, actor: str
    ) -> tuple[str, int, str, int, int | None]:
        es_gasto = doc.tipo == TIPO_GASTO
        partner_id, prov_creado = self._resolver_o_crear_proveedor_doc(doc, escritor, marca)
        numero = doc.numero_documento.strip()
        propio = escritor.factura_por_marca(marca)
        if propio is not None:
            return (
                "account.move",
                propio,
                f"account.move:{propio}",
                partner_id,
                doc.diario_id,
            )
        if numero and not prov_creado:
            ajena = escritor.factura_proveedor_por_ref(partner_id, numero)
            if ajena is not None:
                raise OperativoInvalido(
                    f"La factura {numero} ya existe en Odoo para ese proveedor "
                    f"(account.move {ajena}).",
                    ["numero_documento"],
                )

        moneda_id = escritor.moneda_id(doc.moneda)
        if moneda_id is None:
            raise OperativoInvalido(f"Moneda «{doc.moneda}» no existe en Odoo.", ["moneda"])

        # Siempre por defecto al diario de compras en VES, salvo diario_id explícito en CxC
        diario_id = doc.diario_id if doc.diario_id is not None else escritor.diario_compras_ves_id()
        usa_base = (
            not es_gasto and doc.base_imponible is not None and doc.base_imponible > Decimal("0")
        )
        monto_linea = doc.base_imponible if usa_base else doc.monto
        assert monto_linea is not None

        etiqueta_num = numero or f"S/N-{doc.numero}"
        concepto = doc.concepto.strip() or (
            f"Gasto menor en {doc.contraparte_nombre}"
            if es_gasto
            else f"Factura proveedor {etiqueta_num}"
        )

        titulo_doc = (
            "Gasto sin factura / compra menor al contado"
            if (es_gasto and not numero)
            else ("Gasto / compra menor" if es_gasto else f"Factura de proveedor {etiqueta_num}")
        )
        cta_txt = (
            f"{doc.cuenta_contable_codigo} {doc.cuenta_contable_nombre}".strip()
            if (doc.cuenta_contable_codigo or doc.cuenta_contable_nombre)
            else ""
        )
        detalles = [
            f"{titulo_doc}",
            f"Autorizado en CxC ({doc.numero}) por: {actor}",
            f"Proveedor: {doc.contraparte_nombre}" + (f" ({doc.rif})" if doc.rif else ""),
            f"Categoría: {doc.categoria}" if doc.categoria else "",
            f"Cuenta contable asignada: {cta_txt}" if cta_txt else "",
            f"Nº Control: {doc.numero_control}" if doc.numero_control else "",
            f"IVA: {doc.iva}" if doc.iva is not None else "",
            f"Monto total: {doc.monto} {doc.moneda}",
            f"Método/Ref pago gasto: {doc.metodo_pago} {doc.referencia}".strip()
            if (es_gasto and (doc.metodo_pago or doc.referencia))
            else "",
            f"Orden de compra: {doc.orden_ref}" if doc.orden_ref else "",
            f"Notas: {doc.notas}" if doc.notas else "",
        ]
        resumen_str = " · ".join(x for x in detalles if x)
        nota_chatter = "\n".join(x for x in [marca, *detalles] if x)
        fecha_str = (
            doc.fecha_documento.isoformat()
            if doc.fecha_documento
            else datetime.now().date().isoformat()
        )
        venc_str = (
            None
            if es_gasto
            else (doc.fecha_vencimiento.isoformat() if doc.fecha_vencimiento else None)
        )
        move_id = escritor.crear_factura_proveedor(
            partner_id=partner_id,
            numero=etiqueta_num,
            fecha_emision=fecha_str,
            fecha_vencimiento=venc_str,
            moneda_id=moneda_id,
            monto=monto_linea,
            concepto=concepto,
            marca=marca,
            narration=resumen_str,
            journal_id=diario_id,
            account_id=doc.cuenta_contable_id,
            nota_chatter=nota_chatter,
        )
        return "account.move", move_id, f"account.move:{move_id}", partner_id, diario_id

    def aprobar_y_enviar_a_odoo(self, documento_id: int, *, actor: str = "") -> DocumentoOperativo:
        doc = self.obtener(documento_id)
        if doc.estado == ENVIADO_ODOO:
            raise OperativoYaEnviado(f"{doc.numero} ya fue enviado a Odoo ({doc.odoo_ref}).")
        if doc.estado == RECHAZADO:
            raise OperativoEstadoInvalido(f"{doc.numero} está rechazado y no puede aprobarse.")
        if doc.faltantes:
            raise OperativoInvalido(
                "No se puede aprobar porque faltan campos obligatorios: "
                + ", ".join(doc.faltantes),
                list(doc.faltantes),
            )
        if doc.estado not in (PENDIENTE_APROBACION, ERROR_ODOO, BORRADOR):
            raise OperativoEstadoInvalido(f"Estado inválido para aprobar: {doc.estado}")

        escritor = self._fabrica()
        if escritor is None:
            raise OperativoInvalido(
                "La escritura en Odoo está desactivada (CAPTURAS_ODOO_ESCRITURA=0)."
            )

        marca = f"CXC-{doc.numero}"
        jid: int | None
        try:
            if doc.tipo in (TIPO_PAGO, TIPO_PAGO_PROVEEDOR):
                modelo, res_id, ref, pid, jid = self._enviar_pago(
                    doc, escritor, marca, actor=actor or "sistema"
                )
            else:
                modelo, res_id, ref, pid, jid = self._enviar_documento_compra(
                    doc, escritor, marca, actor=actor or "sistema"
                )
        except OperativoInvalido as e:
            self._repo.actualizar(doc.documento_id, {"error_odoo": str(e)})
            raise
        except OdooEscrituraError as e:
            self._repo.actualizar(doc.documento_id, {"estado": ERROR_ODOO, "error_odoo": str(e)})
            raise RuntimeError(str(e)) from e

        actualizado = self._repo.actualizar(
            doc.documento_id,
            {
                "estado": ENVIADO_ODOO,
                "partner_odoo_id": pid,
                "diario_id": jid,
                "aprobado_por": actor,
                "aprobado_en": datetime.now(),
                "odoo_model": modelo,
                "odoo_res_id": res_id,
                "odoo_ref": ref,
                "error_odoo": "",
            },
        )
        if doc.captura_id is not None and self._repo_capturas is not None:
            try:
                self._repo_capturas.actualizar(
                    doc.captura_id,
                    odoo_ref=ref,
                    resultado={
                        "modo": "aprobado_en_cxc",
                        "documento_id": actualizado.documento_id,
                        "documento_numero": actualizado.numero,
                        "odoo_ref": ref,
                    },
                )
                self._repo_capturas.evento(
                    doc.captura_id, "aprobada_y_enviada_odoo", ref, actor or "cxc"
                )
            except Exception as e:
                logger.warning(
                    "No se pudo actualizar captura #%s tras aprobar: %s", doc.captura_id, e
                )
        return actualizado
