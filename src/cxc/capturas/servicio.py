"""Servicio de capturas: recibe lo de Telegram, lo identifica y lo manda a Odoo como borrador.

Flujo: ``recibir`` valida y guarda (idempotente) → ``procesar`` resuelve cliente/producto/diario/
moneda y escribe en Odoo. Con la escritura desactivada ``procesar`` no abre ninguna conexión: la
captura queda ``PENDIENTE``. Lo que no se puede identificar sin adivinar queda ``INCOMPLETO`` con
el motivo; los fallos de Odoo quedan ``ERROR`` (los de red, marcados transitorios, se reintentan).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

from cxc.bancos.diarios import DIARIOS
from cxc.odoo.escritura import NOTA_PROVEEDOR_DE_DOCUMENTO, EscritorOdoo, OdooEscrituraError

from .espejo import Espejo
from .modelos import (
    CAMPOS_COMPLETABLES,
    CONTACTO,
    CONTACTO_EDITAR,
    COTIZACION,
    ENVIADO,
    ERROR,
    FACTURA_PROVEEDOR,
    GASTO,
    INCOMPLETO,
    KINDS_COMPLETABLES,
    PAGO,
    PAGO_PROVEEDOR,
    PEDIDO,
    PENDIENTE,
    PROVEEDOR,
    Captura,
    CapturaInvalida,
    a_decimal,
    clase_moneda,
    es_efectivo,
    normalizar_nombre,
    normalizar_rif,
    vacio,
    validar,
)
from .repositorio import BloqueoOcupado, FacturaYaCapturada, RepoCapturas

logger = logging.getLogger("cxc.capturas")

FabricaEscritor = Callable[[], EscritorOdoo | None]


class NoEncontrada(LookupError):
    """No existe la captura."""


class CapturaCerrada(Exception):
    """La captura ya está en Odoo: se edita allá."""


class NoCompletable(Exception):
    """Solo los pagos, las facturas de proveedor y los gastos se completan después de recibidos."""


class FacturaDuplicada(Exception):
    """Misma (proveedor, número de factura) ya capturada: rechazo de negocio (409)."""


class _Incompleto(Exception):
    """Falta algo que no se puede adivinar; ``motivos`` se muestra a quien completa la captura."""

    def __init__(self, motivos: list[str]) -> None:
        super().__init__("; ".join(motivos))
        self.motivos = motivos


def _sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn"
    )


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _sin_acentos(texto)).strip("_")


def resolver_diario(payload: dict[str, Any], override: dict[str, int]) -> int:
    """``journal_id`` del pago, sin adivinar.

    1. Override ``CAPTURAS_DIARIOS`` (JSON): claves ``<banco>_<moneda>``, ``<banco>``,
       ``<método>_<moneda>``, ``<método>``, ``<moneda>``, ``default``.
    2. Mapa banco → diario de ``cxc.bancos.diarios`` (leído de Odoo producción), por el nombre o
       código del banco y la moneda.
    """
    moneda = clase_moneda(str(payload.get("moneda") or ""))
    banco = _slug(str(payload.get("banco") or ""))
    metodo = _slug(str(payload.get("metodo_pago") or ""))
    mon = moneda.lower()
    mapa = {k.lower(): v for k, v in override.items()}
    for clave in (f"{banco}_{mon}", banco, f"{metodo}_{mon}", metodo, mon, "default"):
        if clave and clave in mapa:
            return int(mapa[clave])
    if banco:
        texto = f"_{banco}_"
        for clave, d in DIARIOS.items():
            tokens = {_slug(clave.replace("_USD", "")), _slug(d["codigo"])} - {""}
            if d["moneda"] == moneda and any(f"_{t}_" in texto for t in tokens):
                if d["journal_id"] is None:
                    raise _Incompleto([f"diario: la cuenta {clave} no tiene diario en Odoo"])
                return int(d["journal_id"])
    raise _Incompleto(
        ["diario: no pude determinar el diario (indica el banco o configura CAPTURAS_DIARIOS)"]
    )


def _numero_factura(kind: str, draft: dict[str, Any]) -> str | None:
    """Número de una ``factura_proveedor`` o ``gasto`` (None si no es uno o aún no lo trae)."""
    if kind not in (FACTURA_PROVEEDOR, GASTO) or vacio(draft.get("numero_factura")):
        return None
    return str(draft["numero_factura"]).strip()


def _factura_duplicada(numero: str, e: FacturaYaCapturada) -> FacturaDuplicada:
    previa = e.existente
    return FacturaDuplicada(
        f"La factura {numero} de ese proveedor ya fue capturada "
        f"(captura #{previa.captura_id}, {previa.estado})."
    )


def _mismo_proveedor(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Mismo proveedor: igual RIF si ambos lo traen; si no, igual nombre (sin mayúsculas)."""
    if not vacio(a.get("rif")) and not vacio(b.get("rif")):
        return str(a["rif"]).upper() == str(b["rif"]).upper()
    na, nb = a.get("proveedor") or a.get("nombre"), b.get("proveedor") or b.get("nombre")
    return (
        not vacio(na) and not vacio(nb) and normalizar_nombre(str(na)) == normalizar_nombre(str(nb))
    )


def _fecha_iso(valor: Any) -> str | None:
    if vacio(valor):
        return None
    try:
        return date.fromisoformat(str(valor)[:10]).isoformat()
    except ValueError:
        return None


class ServicioCapturas:
    def __init__(
        self,
        repo: RepoCapturas,
        espejo: Espejo,
        fabrica_escritor: FabricaEscritor,
        *,
        diarios: dict[str, int] | None = None,
        max_intentos: int = 5,
    ) -> None:
        self._repo = repo
        self._espejo = espejo
        self._fabrica = fabrica_escritor
        self._diarios = diarios or {}
        self._max_intentos = max_intentos

    # ── entrada ──────────────────────────────────────────────────────────────

    def recibir(
        self, kind: str, draft: dict[str, Any], source: dict[str, Any], idempotency_key: str
    ) -> tuple[Captura, bool]:
        """Valida y registra. ``creada=False`` si la clave ya existía (mismo mensaje)."""
        captura, creada, _ = self.recibir_detallado(kind, draft, source, idempotency_key)
        return captura, creada

    def recibir_detallado(
        self, kind: str, draft: dict[str, Any], source: dict[str, Any], idempotency_key: str
    ) -> tuple[Captura, bool, bool]:
        """Como ``recibir`` y además dice si un reenvío CORRIGIÓ una captura aún no enviada.

        Devuelve ``(captura, creada, actualizada)``. Con la misma clave:

        - mismo contenido → la existente, sin cambios (idempotente);
        - contenido distinto y la captura sigue ``PENDIENTE``/``INCOMPLETO`` (no llegó a Odoo) →
          se actualiza el payload, se reevalúa el estado y se anota en la bitácora (así la
          corrección del vendedor reemplaza al borrador viejo y nunca se envía el viejo a Odoo);
        - ya ``ENVIADO`` o ``ERROR`` → no se pisa: se devuelve la existente.
        """
        existente = self._repo.obtener_por_clave(idempotency_key)
        if existente is not None:
            captura, actualizada = self._reenvio(existente, kind, draft, source)
            return captura, False, actualizada
        faltan = validar(kind, draft)
        draft = self._normalizar(draft)
        datos: dict[str, Any] = {
            "kind": kind,
            "idempotency_key": idempotency_key,
            "payload": draft,
            "fuente": source,
            "auto": bool(source.get("auto")),
            "estado": INCOMPLETO if faltan else PENDIENTE,
            "incompleto": faltan,
        }
        numero = _numero_factura(kind, draft)
        if numero is None:
            captura, creada = self._repo.crear(**datos)
        else:  # comprobar y crear en UNA transacción bloqueada por número (ver repositorio)
            try:
                captura, creada = self._repo.crear_factura(
                    **datos,
                    numero_factura=numero,
                    es_mismo_proveedor=lambda previo: _mismo_proveedor(previo, draft),
                )
            except FacturaYaCapturada as e:
                raise _factura_duplicada(numero, e) from e
        return captura, creada, False

    @staticmethod
    def _normalizar(draft: dict[str, Any]) -> dict[str, Any]:
        if not vacio(draft.get("rif")):
            return {**draft, "rif": normalizar_rif(str(draft["rif"]))}
        return draft

    def _reenvio(
        self, existente: Captura, kind: str, draft: dict[str, Any], source: dict[str, Any]
    ) -> tuple[Captura, bool]:
        if existente.estado not in (PENDIENTE, INCOMPLETO):
            return existente, False  # ya en Odoo o con error: no se pisa
        if kind != existente.kind:
            raise CapturaInvalida("Esa clave ya se usó con otro tipo de captura.")
        faltan = validar(kind, draft)  # un reenvío inválido se rechaza y deja intacta la captura
        nuevo = self._normalizar(draft)
        if nuevo == existente.payload:
            return existente, False
        cambiados = sorted(
            k
            for k in set(nuevo) | set(existente.payload)
            if nuevo.get(k) != existente.payload.get(k)
        )
        actualizada = self._guardar_cambios(existente.captura_id, kind, nuevo, faltan)
        self._repo.evento(
            existente.captura_id,
            "actualizada_por_reenvio",
            ", ".join(cambiados),
            str(source.get("vendedor_email") or "bot"),
        )
        return actualizada, True

    def completar(
        self, captura_id: int, cambios: dict[str, Any], actor: str = "bot", enviar: bool = True
    ) -> Captura:
        """Fusiona correcciones de un pago o factura y lo reevalúa; ``enviar=False`` no lo manda."""
        c = self._existente(captura_id)
        if c.kind not in KINDS_COMPLETABLES:
            raise NoCompletable("Solo se pueden completar pagos, facturas de proveedor y gastos.")
        if c.estado == ENVIADO:
            raise CapturaCerrada("Ya está en Odoo: edítala allá.")
        permitidos = CAMPOS_COMPLETABLES[c.kind]
        validos = {k: v for k, v in cambios.items() if k in permitidos and not vacio(v)}
        payload = {**c.payload, **validos}
        faltan = validar(c.kind, payload)  # puede lanzar CapturaInvalida
        if not vacio(payload.get("rif")):
            payload["rif"] = normalizar_rif(str(payload["rif"]))
        c = self._guardar_cambios(captura_id, c.kind, payload, faltan)
        self._repo.evento(captura_id, "completada", ", ".join(sorted(validos)), actor)
        return c if faltan or not enviar else self.procesar(captura_id)

    def _guardar_cambios(
        self, captura_id: int, kind: str, payload: dict[str, Any], faltan: list[str]
    ) -> Captura:
        """Guarda el payload corregido y reevalúa el estado.

        Una factura con número se guarda con la comprobación de duplicados DENTRO de la misma
        transacción bloqueada por número: dos capturas no pueden quedar con la misma factura.
        """
        campos: dict[str, Any] = {
            "payload": payload,
            "incompleto": faltan,
            "estado": INCOMPLETO if faltan else PENDIENTE,
            "error": "",
        }
        numero = _numero_factura(kind, payload)
        if numero is None:
            return self._repo.actualizar(captura_id, **campos)
        try:
            return self._repo.actualizar_factura(
                captura_id,
                numero_factura=numero,
                es_mismo_proveedor=lambda previo: _mismo_proveedor(previo, payload),
                **campos,
            )
        except FacturaYaCapturada as e:
            raise _factura_duplicada(numero, e) from e

    def reintentar(self, captura_id: int, actor: str = "sistema") -> Captura:
        c = self._existente(captura_id)
        if c.estado == ENVIADO:
            raise CapturaCerrada("Ya está en Odoo: no hay nada que reintentar.")
        self._repo.evento(captura_id, "reintento_manual", "", actor)
        return self.procesar(captura_id)

    def reintentar_pendientes(self) -> int:
        """Reintenta los ERROR transitorios y los PENDIENTE sin procesar. Devuelve cuántos tocó."""
        n = 0
        for c in self._repo.reintentables(max_intentos=self._max_intentos):
            try:
                self.procesar(c.captura_id)
                n += 1
            except Exception:
                logger.exception("Fallo reintentando la captura %s", c.captura_id)
        return n

    def listar(
        self, *, estado: str | None, kind: str | None, limit: int, offset: int
    ) -> tuple[list[Captura], int]:
        return self._repo.listar(estado=estado, kind=kind, limit=limit, offset=offset)

    def detalle(self, captura_id: int) -> tuple[Captura, list[dict[str, Any]]]:
        """La captura y su bitácora (recibida, intentos, creada en Odoo, errores...)."""
        return self._existente(captura_id), self._repo.eventos(captura_id)

    def _existente(self, captura_id: int) -> Captura:
        c = self._repo.obtener(captura_id)
        if c is None:
            raise NoEncontrada(str(captura_id))
        return c

    # ── envío a Odoo ─────────────────────────────────────────────────────────

    def procesar(self, captura_id: int) -> Captura:
        """Intenta crear el borrador en Odoo. Nunca lanza por fallos de Odoo: deja el estado."""
        c = self._existente(captura_id)
        if c.estado == ENVIADO:
            return c
        faltan = validar(c.kind, c.payload)
        if faltan:
            return self._repo.actualizar(captura_id, estado=INCOMPLETO, incompleto=faltan)
        if c.kind == COTIZACION or (c.kind == PEDIDO and c.payload.get("requiere_aprobacion_cxc")):
            # Las cotizaciones y los pedidos sujetos a aprobación quedan en CxC (Ventas) y solo
            # se envían a Odoo cuando una persona autorizada los aprueba en el panel de CxC.
            modo_cxc = "pendiente_aprobacion_cxc" if c.kind == PEDIDO else "documento_cxc"
            self._repo.evento(captura_id, "registrada_en_cxc", c.kind)
            return self._repo.actualizar(
                captura_id,
                estado=ENVIADO,
                odoo_ref="",
                incompleto=[],
                error="",
                resultado={**c.resultado, "modo": modo_cxc, "transitorio": False},
            )
        try:
            escritor = self._fabrica()
        except OdooEscrituraError as e:
            return self._fallo(c, e)
        if escritor is None:
            return c  # escritura desactivada: queda PENDIENTE y no se toca Odoo
        self._repo.actualizar(captura_id, sumar_intento=True)
        self._repo.evento(captura_id, "intento_envio", f"{c.kind}")
        try:
            ref, extra = self._enviar(c, escritor)
        except _Incompleto as e:
            self._repo.evento(captura_id, "incompleto", str(e))
            return self._repo.actualizar(
                captura_id,
                estado=INCOMPLETO,
                incompleto=e.motivos,
                error=str(e),
                resultado={**c.resultado, **{"transitorio": False}},
            )
        except OdooEscrituraError as e:
            return self._fallo(c, e)
        except Exception:
            logger.exception("Error inesperado enviando la captura %s", captura_id)
            return self._fallo(c, OdooEscrituraError("Error inesperado al enviar a Odoo."))
        self._repo.evento(captura_id, "creada_en_odoo", ref)
        return self._repo.actualizar(
            captura_id,
            estado=ENVIADO,
            odoo_ref=ref,
            incompleto=[],
            error="",
            resultado={**c.resultado, **extra, "transitorio": False},
        )

    def _fallo(self, c: Captura, e: OdooEscrituraError) -> Captura:
        self._repo.evento(c.captura_id, "error", str(e))
        return self._repo.actualizar(
            c.captura_id,
            estado=ERROR,
            error=str(e),
            resultado={**c.resultado, "transitorio": e.transitorio},
        )

    def _enviar(self, c: Captura, odoo: EscritorOdoo) -> tuple[str, dict[str, Any]]:
        marca = f"BOT-{c.captura_id}"
        if c.kind == CONTACTO:
            return self._contacto(c, odoo, marca)
        if c.kind == CONTACTO_EDITAR:
            return self._contacto_editar(c, odoo, marca)
        if c.kind == PEDIDO:
            return self._pedido(c, odoo, marca)
        if c.kind == PROVEEDOR:
            return self._proveedor_exclusivo(c, odoo, marca)
        if c.kind in (FACTURA_PROVEEDOR, GASTO):
            return self._documento(c, odoo, marca)
        return self._pago(c, odoo, marca)

    # ── contacto ─────────────────────────────────────────────────────────────

    def _contacto(self, c: Captura, odoo: EscritorOdoo, marca: str) -> tuple[str, dict[str, Any]]:
        p = c.payload
        ub = p.get("ubicacion") or {}
        pid = odoo.crear_contacto_borrador(
            {
                "nombre": p["nombre"],
                "telefono": p["telefono"],
                "email": p.get("email"),
                "direccion": p.get("direccion"),
                "rif": p.get("rif"),
                "notas": p.get("notas"),
                "plus_code": ub.get("plus_code") or p.get("plus_code"),
                "lat": ub.get("latitud"),
                "lon": ub.get("longitud"),
            },
            marca,
        )
        return f"res.partner:{pid}", {"partner_id": pid, "tipo_contacto": "prospecto"}

    def _contacto_editar(
        self, c: Captura, odoo: EscritorOdoo, marca: str
    ) -> tuple[str, dict[str, Any]]:
        p = c.payload
        pid_raw = p.get("partner_odoo_id")
        partner_id: int | None = (
            int(str(pid_raw))
            if pid_raw not in (None, "", False) and str(pid_raw).isdigit()
            else None
        )
        rif = str(p["rif"]).strip() if not vacio(p.get("rif")) else None
        nombre_ref = str(p.get("nombre_buscar") or p.get("nombre") or "").strip() or None
        if partner_id is None and rif:
            ids = odoo.partners_por_rif(rif)
            if len(ids) > 1:
                raise _Incompleto([f"contacto: el RIF {rif} está en {len(ids)} contactos de Odoo"])
            if ids:
                partner_id = ids[0]
        if partner_id is None and nombre_ref:
            ids_nom = odoo.partners_por_nombre(nombre_ref)
            if len(ids_nom) > 1:
                raise _Incompleto(
                    [f"contacto: hay {len(ids_nom)} contactos llamados «{nombre_ref}» en Odoo"]
                )
            if ids_nom:
                partner_id = ids_nom[0]
        if partner_id is None:
            raise _Incompleto(
                ["contacto: no encontré ese contacto en Odoo para editarlo (verifica nombre o RIF)"]
            )
        ub = p.get("ubicacion") or {}
        odoo.actualizar_contacto(
            partner_id,
            {
                "nombre": p.get("nombre") if p.get("nombre_buscar") else None,
                "telefono": p.get("telefono"),
                "email": p.get("email"),
                "direccion": p.get("direccion"),
                "rif": rif,
                "notas": p.get("notas"),
                "plus_code": ub.get("plus_code") or p.get("plus_code"),
                "lat": ub.get("latitud"),
                "lon": ub.get("longitud"),
            },
            promover_cliente=False,
            marca=marca,
        )
        return f"res.partner:{partner_id}", {"partner_id": partner_id, "editado": True}

    # ── pedido ───────────────────────────────────────────────────────────────

    def _partner_existente(
        self, odoo: EscritorOdoo, rif: str | None, nombre: str | None
    ) -> tuple[int | None, str]:
        """Partner por RIF y, si no, por nombre exacto (espejo o prospecto en Odoo)."""
        if rif:
            ids = odoo.partners_por_rif(rif)
            if len(ids) > 1:
                raise _Incompleto([f"cliente: el RIF {rif} está en {len(ids)} contactos de Odoo"])
            if ids:
                return ids[0], "rif"
        if nombre:
            ids_e = self._espejo.clientes_por_nombre(nombre)
            if len(ids_e) > 1:
                raise _Incompleto(
                    [f"cliente: hay varios clientes llamados «{nombre}»; vincúlalo en Odoo"]
                )
            if ids_e and ids_e[0].isdigit():
                return int(ids_e[0]), "nombre"
            # También busca entre contactos/prospectos aún sin orden confirmada en Odoo
            ids_p = odoo.partners_por_nombre(nombre)
            if len(ids_p) > 1:
                raise _Incompleto([f"cliente: hay varios contactos llamados «{nombre}» en Odoo"])
            if ids_p:
                return ids_p[0], "prospecto_odoo"
        return None, ""

    def _pedido(self, c: Captura, odoo: EscritorOdoo, marca: str) -> tuple[str, dict[str, Any]]:
        p = c.payload
        rif = str(p["rif"]) if not vacio(p.get("rif")) else None
        pid_raw = p.get("partner_odoo_id")
        partner: int | None
        if pid_raw not in (None, "", False) and str(pid_raw).isdigit():
            partner, via = int(str(pid_raw)), "odoo_id"
        else:
            partner, via = self._partner_existente(odoo, rif, str(p["cliente"]))

        lineas: list[dict[str, Any]] = []
        sin_resolver: list[str] = []
        for ln in p["lineas"]:
            pid_explicito = str(ln.get("producto_id") or "").strip()
            if pid_explicito and pid_explicito.isdigit():
                ids = [pid_explicito]
            else:
                ids = self._espejo.productos(str(ln.get("producto") or ""))
            if len(ids) != 1 or not ids[0].isdigit():
                motivo = "ambiguo" if len(ids) > 1 else "no encontrado"
                sin_resolver.append(f"producto «{ln.get('producto')}» {motivo} en el catálogo")
                continue
            lineas.append(
                {
                    "product_id": int(ids[0]),
                    "cantidad": a_decimal(ln["cantidad"], "Cantidad"),
                    "precio": ln.get("precio_unitario")
                    if not vacio(ln.get("precio_unitario"))
                    else None,
                    "descuento_pct": ln.get("descuento_pct")
                    if not vacio(ln.get("descuento_pct"))
                    else None,
                }
            )
        if sin_resolver:
            raise _Incompleto(sin_resolver)

        ub = p.get("ubicacion") or {}
        if partner is not None and via in ("odoo_id", "prospecto_odoo"):
            # Si era prospecto o trae datos nuevos para su ficha, los actualiza y promueve
            odoo.actualizar_contacto(
                partner,
                {
                    "rif": rif,
                    "email": p.get("email"),
                    "telefono": p.get("telefono"),
                    "direccion_fiscal": p.get("direccion_fiscal"),
                    "plus_code": ub.get("plus_code") or p.get("plus_code"),
                    "lat": ub.get("latitud"),
                    "lon": ub.get("longitud"),
                },
                promover_cliente=True,
                marca=marca,
            )
        elif partner is None:
            if not rif:
                raise _Incompleto(["cliente: no existe en Odoo; indica su RIF para crearlo"])
            partner = odoo.crear_cliente(
                {
                    "nombre": p["cliente"],
                    "rif": rif,
                    "email": p.get("email"),
                    "telefono": p.get("telefono"),
                    "direccion_fiscal": p.get("direccion_fiscal"),
                    "direccion_entrega": p.get("direccion_entrega"),
                    "plus_code": ub.get("plus_code") or p.get("plus_code"),
                    "lat": ub.get("latitud"),
                    "lon": ub.get("longitud"),
                },
                marca,
            )
            via = "creado"

        notas_pedido: list[str] = []
        if not vacio(p.get("condiciones_negociacion")):
            cond = str(p["condiciones_negociacion"]).strip()
            notas_pedido.append(f"Condiciones de negociación / descuentos: {cond}")
        if not vacio(p.get("observaciones")):
            notas_pedido.append(str(p["observaciones"]).strip())
        if (
            not p.get("entrega_igual_fiscal")
            and not vacio(p.get("direccion_entrega"))
            and p.get("direccion_entrega") != p.get("direccion_fiscal")
        ):
            notas_pedido.append(f"Referencia entrega: {str(p['direccion_entrega']).strip()}")
        nota_final = "\n".join(notas_pedido) if notas_pedido else None

        order = odoo.crear_pedido(
            partner_id=partner,
            lineas=lineas,
            marca=marca,
            nota=nota_final,
            vendedor_id=odoo.vendedor_id(str(c.fuente.get("vendedor_email") or "")),
            shipping_id=None,
            fecha_entrega=_fecha_iso(p.get("fecha_entrega")),
        )
        return f"sale.order:{order}", {
            "partner_id": partner,
            "cliente_via": via,
            "pedido_odoo_id": order,
            "odoo_name": odoo.nombre_pedido(order),
        }

    # ── pagos ────────────────────────────────────────────────────────────────

    def _partner_pago(self, c: Captura, odoo: EscritorOdoo) -> tuple[int, dict[str, Any]]:
        p = c.payload
        rif = str(p["rif"]) if not vacio(p.get("rif")) else None
        extra: dict[str, Any] = {}
        if c.kind == PAGO:
            if not vacio(p.get("pedido_ref")):
                cliente_id = self._espejo.cliente_de_orden(str(p["pedido_ref"]))
                if cliente_id and cliente_id.isdigit():
                    extra["pedido_ref_resuelto"] = str(p["pedido_ref"])
                    return int(cliente_id), extra
            partner, via = self._partner_existente(odoo, rif, p.get("cliente"))
            if partner is None:
                raise _Incompleto(
                    ["cliente: no lo encontré en Odoo (nombre exacto, RIF o pedido); complétalo"]
                )
            extra["cliente_via"] = via
            return partner, extra
        # pago a proveedor
        if rif:
            ids = odoo.partners_por_rif(rif)
        else:
            ids = odoo.proveedores_por_nombre(str(p["proveedor"]))
        if len(ids) != 1:
            donde = f"el RIF {rif}" if rif else f"«{p['proveedor']}»"
            raise _Incompleto(
                [
                    f"proveedor: {donde} "
                    + ("no existe en Odoo" if not ids else "es ambiguo en Odoo")
                ]
            )
        return ids[0], extra

    def _pago(self, c: Captura, odoo: EscritorOdoo, marca: str) -> tuple[str, dict[str, Any]]:
        p = c.payload
        partner, extra = self._partner_pago(c, odoo)
        moneda_id = odoo.moneda_id(str(p["moneda"]))
        if moneda_id is None:
            raise _Incompleto([f"moneda: «{p['moneda']}» no existe en Odoo"])
        diario = resolver_diario(p, self._diarios)
        factura_txt = f"factura {p['factura_ref']}" if not vacio(p.get("factura_ref")) else ""
        if c.kind == PAGO_PROVEEDOR and factura_txt:
            # Solo referencia para la nota: NO se concilia ni se exige que la factura exista.
            factura = odoo.factura_proveedor_por_ref(partner, str(p["factura_ref"]))
            if factura is not None:
                factura_txt += f" [account.move {factura}]"
                extra = {**extra, "factura_odoo_id": factura}
        partes = [
            marca,
            f"ref {p['referencia']}" if not vacio(p.get("referencia")) else "",
            str(p.get("banco") or ""),
            str(p.get("metodo_pago") or ""),
            f"pedido {p['pedido_ref']}" if not vacio(p.get("pedido_ref")) else "",
            factura_txt,
            str(p.get("notas") or ""),
        ]
        memo_limpio = (
            None
            if es_efectivo(p.get("metodo_pago"))
            else (str(p["referencia"]).strip() if not vacio(p.get("referencia")) else None)
        )
        pago = odoo.crear_pago(
            partner_id=partner,
            monto=Decimal(str(p["monto"])),
            journal_id=diario,
            moneda_id=moneda_id,
            es_cliente=c.kind == PAGO,
            marca=marca,
            memo=memo_limpio,
            nota_chatter=" · ".join(x for x in partes if x),
            fecha=_fecha_iso(p.get("fecha_pago")),
        )
        return f"account.payment:{pago}", {**extra, "partner_id": partner, "diario_id": diario}

    # ── proveedor y factura de proveedor ─────────────────────────────────────

    def _proveedor_exclusivo(
        self, c: Captura, odoo: EscritorOdoo, marca: str
    ) -> tuple[str, dict[str, Any]]:
        """Serializa por RIF: dos capturas del mismo proveedor no crean dos ``res.partner``.

        Con envíos simultáneos las dos buscarían «no existe» y las dos crearían. Con el bloqueo, la
        segunda espera a la primera, la encuentra en Odoo y queda ``ya_existia``. Si no se logra el
        bloqueo a tiempo es un error transitorio y se reintenta solo.
        """
        try:
            with self._repo.bloqueo(f"proveedor:{c.payload['rif']}"):
                return self._proveedor(c, odoo, marca)
        except BloqueoOcupado as e:
            raise OdooEscrituraError(
                "Hay otro envío del mismo proveedor en curso; se reintentará.", transitorio=True
            ) from e

    def _proveedor(self, c: Captura, odoo: EscritorOdoo, marca: str) -> tuple[str, dict[str, Any]]:
        """Crea el proveedor solo si no existe (por RIF o por nombre exacto): nunca duplica."""
        p = c.payload
        propio = odoo.partner_por_marca(marca)  # un reintento tras crearlo nosotros
        if propio is not None:
            return f"res.partner:{propio}", {"partner_id": propio, "ya_existia": False}
        rif, nombre = str(p["rif"]), str(p["nombre"])
        ids = odoo.partners_por_rif(rif)
        if len(ids) > 1:
            raise _Incompleto([f"proveedor: el RIF {rif} está en {len(ids)} contactos de Odoo"])
        via = "rif"
        if not ids:
            ids, via = odoo.partners_por_nombre(nombre), "nombre"
            if len(ids) > 1:
                raise _Incompleto(
                    [f"proveedor: hay {len(ids)} contactos llamados «{nombre}» en Odoo"]
                )
        if ids:
            return f"res.partner:{ids[0]}", {
                "partner_id": ids[0],
                "ya_existia": True,
                "encontrado_por": via,
                "nota": "El proveedor ya existía en Odoo: no se creó ni se modificó.",
            }
        ub = p.get("ubicacion") or {}
        pid = odoo.crear_proveedor(
            {
                "nombre": nombre,
                "rif": rif,
                "telefono": p["telefono"],
                "email": p.get("email"),
                "direccion": p.get("direccion"),
                "dias_credito": p.get("dias_credito"),
                "notas": p.get("notas"),
                "lat": ub.get("latitud"),
                "lon": ub.get("longitud"),
            },
            marca,
        )
        return f"res.partner:{pid}", {"partner_id": pid, "ya_existia": False}

    def _buscar_proveedor(
        self, odoo: EscritorOdoo, rif: str | None, nombre: str | None
    ) -> tuple[int | None, str]:
        """Proveedor ya existente en Odoo: por RIF y, si no, por nombre exacto.

        Ambiguo → ``_Incompleto``.

        Con nombre se busca primero entre los proveedores y luego entre cualquier contacto: un
        cliente que también nos vende no se duplica. Nunca modifica lo que encuentra.
        """
        if rif:
            ids = odoo.partners_por_rif(rif)
            if len(ids) > 1:
                raise _Incompleto([f"proveedor: el RIF {rif} está en {len(ids)} contactos de Odoo"])
            if ids:
                return ids[0], "rif"
        if nombre:
            for buscar, via in (
                (odoo.proveedores_por_nombre, "nombre"),
                (odoo.partners_por_nombre, "nombre_contacto"),
            ):
                ids = buscar(nombre)
                if len(ids) > 1:
                    raise _Incompleto(
                        [f"proveedor: «{nombre}» es ambiguo en Odoo ({len(ids)} coincidencias)"]
                    )
                if ids:
                    return ids[0], via
        return None, ""

    def _proveedor_del_documento(
        self, c: Captura, odoo: EscritorOdoo, marca: str
    ) -> tuple[int, dict[str, Any]]:
        """El proveedor de una factura o un gasto: lo busca y, si NO existe, lo CREA.

        Decisión del owner (nov-2026): el grupo de Compras es de compras menores del día a día; el
        bot lee el proveedor del propio documento y no se le exige darlo de alta aparte. Se
        serializa con el MISMO bloqueo que el kind ``proveedor`` (por RIF; sin RIF, por nombre
        normalizado): varios gastos simultáneos de un proveedor nuevo crean un solo ``res.partner``.
        """
        p = c.payload
        rif = str(p["rif"]) if not vacio(p.get("rif")) else None
        nombre = str(p["proveedor"]).strip() if not vacio(p.get("proveedor")) else None
        if rif is None and nombre is None:
            raise _Incompleto(["proveedor: indica su nombre o su RIF"])
        clave = f"proveedor:{rif}" if rif else f"proveedor:nombre:{normalizar_nombre(nombre or '')}"
        try:
            with self._repo.bloqueo(clave):
                return self._buscar_o_crear_proveedor(odoo, p, marca, rif, nombre)
        except BloqueoOcupado as e:
            raise OdooEscrituraError(
                "Hay otro envío del mismo proveedor en curso; se reintentará.", transitorio=True
            ) from e

    def _buscar_o_crear_proveedor(
        self,
        odoo: EscritorOdoo,
        p: dict[str, Any],
        marca: str,
        rif: str | None,
        nombre: str | None,
    ) -> tuple[int, dict[str, Any]]:
        propio = odoo.partner_por_marca(marca)  # un reintento tras crearlo nosotros
        if propio is not None:
            return propio, {"proveedor_via": "creado", "proveedor_creado": True}
        existente, via = self._buscar_proveedor(odoo, rif, nombre)
        if existente is not None:
            return existente, {"proveedor_via": via, "proveedor_creado": False}
        if nombre is None:  # solo traía el RIF y ese RIF no existe: sin nombre no se puede crear
            raise _Incompleto(
                [
                    f"proveedor: el RIF {rif} no existe en Odoo; "
                    "indica el nombre del proveedor para crearlo"
                ]
            )
        pid = odoo.crear_proveedor(
            {
                "nombre": nombre,
                "rif": rif,
                "telefono": p.get("proveedor_telefono"),
                "email": p.get("proveedor_email"),
                "direccion": p.get("proveedor_direccion"),
                "nota": NOTA_PROVEEDOR_DE_DOCUMENTO,
            },
            marca,
        )
        return pid, {"proveedor_via": "creado", "proveedor_creado": True}

    def _documento(self, c: Captura, odoo: EscritorOdoo, marca: str) -> tuple[str, dict[str, Any]]:
        """``account.move`` de proveedor en BORRADOR para una factura o un gasto pagado al contado.

        El IVA y el control van en la nota; no se inventan impuestos. Un gasto no genera pago ni
        cuenta por pagar: queda indicado en la nota para que contabilidad lo concilie.
        """
        p = c.payload
        es_gasto = c.kind == GASTO
        partner, extra = self._proveedor_del_documento(c, odoo, marca)
        numero = str(p["numero_factura"]).strip() if not vacio(p.get("numero_factura")) else None
        propia = odoo.factura_por_marca(marca)  # un reintento tras crearla nosotros
        if propia is not None:
            return f"account.move:{propia}", {"partner_id": partner, **extra}
        if numero and not extra["proveedor_creado"]:
            ajena = odoo.factura_proveedor_por_ref(partner, numero)
            if ajena is not None:
                raise OdooEscrituraError(
                    f"La factura {numero} ya existe en Odoo para ese proveedor "
                    f"(account.move {ajena})."
                )
        moneda_id = odoo.moneda_id(str(p["moneda"]))
        if moneda_id is None:
            raise _Incompleto([f"moneda: «{p['moneda']}» no existe en Odoo"])
        base = p.get("base_imponible")
        usa_base = not vacio(base) and Decimal(str(base)) > 0
        monto = Decimal(str(base if usa_base else p["monto_total"]))
        etiqueta = numero or f"S/N-{c.captura_id}"
        proveedor_txt = "proveedor"
        if not vacio(p.get("proveedor")):
            proveedor_txt = str(p["proveedor"]).strip()
        notas = [
            "Gasto de consumo pagado al contado (sin pago asociado: contabilidad lo concilia)"
            if es_gasto
            else f"Factura proveedor {etiqueta}",
            f"Documento {numero}" if es_gasto and numero else "",
            f"Categoría {p['categoria']}" if es_gasto and not vacio(p.get("categoria")) else "",
            f"Control {p['numero_control']}" if not vacio(p.get("numero_control")) else "",
            f"IVA {p['iva']}" if not vacio(p.get("iva")) else "",
            f"Monto total {p['monto_total']} {p['moneda']}",
            f"Orden {p['orden_ref']}" if not vacio(p.get("orden_ref")) else "",
            str(p["notas"]) if not vacio(p.get("notas")) else "",
        ]
        if not vacio(p.get("concepto")):
            concepto = str(p["concepto"])
        else:
            concepto = f"Compra en {proveedor_txt}" if es_gasto else f"Factura proveedor {etiqueta}"
        diario_id = odoo.diario_compras_ves_id(p.get("diario_id") or p.get("diario_codigo"))
        factura = odoo.crear_factura_proveedor(
            partner_id=partner,
            numero=etiqueta,
            fecha_emision=str(p["fecha_emision"]),
            fecha_vencimiento=None if es_gasto else _fecha_iso(p.get("fecha_vencimiento")),
            moneda_id=moneda_id,
            monto=monto,
            concepto=concepto,
            marca=marca,
            narration=" · ".join(x for x in notas if x),
            journal_id=diario_id,
        )
        res_doc: dict[str, Any] = {
            "partner_id": partner,
            **extra,
            "monto_linea": str(monto),
        }
        if diario_id is not None:
            res_doc["diario_id"] = diario_id
        return f"account.move:{factura}", res_doc


__all__ = [
    "CapturaCerrada",
    "BloqueoOcupado",
    "CapturaInvalida",
    "FacturaDuplicada",
    "NoCompletable",
    "NoEncontrada",
    "ServicioCapturas",
    "resolver_diario",
]
