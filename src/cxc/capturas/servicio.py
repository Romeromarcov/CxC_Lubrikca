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
from cxc.odoo.escritura import EscritorOdoo, OdooEscrituraError

from .espejo import Espejo
from .modelos import (
    CAMPOS_COMPLETABLES,
    CONTACTO,
    ENVIADO,
    ERROR,
    FACTURA_PROVEEDOR,
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
    normalizar_rif,
    vacio,
    validar,
)
from .repositorio import RepoCapturas

logger = logging.getLogger("cxc.capturas")

FabricaEscritor = Callable[[], EscritorOdoo | None]


class NoEncontrada(LookupError):
    """No existe la captura."""


class CapturaCerrada(Exception):
    """La captura ya está en Odoo: se edita allá."""


class NoCompletable(Exception):
    """Solo los pagos y las facturas de proveedor se completan después de recibidos."""


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


def _mismo_proveedor(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Mismo proveedor: igual RIF si ambos lo traen; si no, igual nombre (sin mayúsculas)."""
    if not vacio(a.get("rif")) and not vacio(b.get("rif")):
        return str(a["rif"]).upper() == str(b["rif"]).upper()
    na, nb = a.get("proveedor") or a.get("nombre"), b.get("proveedor") or b.get("nombre")
    return not vacio(na) and not vacio(nb) and str(na).strip().lower() == str(nb).strip().lower()


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
        self._rechazar_factura_duplicada(kind, draft, excluir=None)
        captura, creada = self._repo.crear(
            kind=kind,
            idempotency_key=idempotency_key,
            payload=draft,
            fuente=source,
            auto=bool(source.get("auto")),
            estado=INCOMPLETO if faltan else PENDIENTE,
            incompleto=faltan,
        )
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
        self._rechazar_factura_duplicada(kind, nuevo, excluir=existente.captura_id)
        cambiados = sorted(
            k
            for k in set(nuevo) | set(existente.payload)
            if nuevo.get(k) != existente.payload.get(k)
        )
        actualizada = self._repo.actualizar(
            existente.captura_id,
            payload=nuevo,
            incompleto=faltan,
            estado=INCOMPLETO if faltan else PENDIENTE,
            error="",
        )
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
            raise NoCompletable("Solo se pueden completar pagos y facturas de proveedor.")
        if c.estado == ENVIADO:
            raise CapturaCerrada("Ya está en Odoo: edítala allá.")
        permitidos = CAMPOS_COMPLETABLES[c.kind]
        validos = {k: v for k, v in cambios.items() if k in permitidos and not vacio(v)}
        payload = {**c.payload, **validos}
        faltan = validar(c.kind, payload)  # puede lanzar CapturaInvalida
        if not vacio(payload.get("rif")):
            payload["rif"] = normalizar_rif(str(payload["rif"]))
        self._rechazar_factura_duplicada(c.kind, payload, excluir=captura_id)
        c = self._repo.actualizar(
            captura_id,
            payload=payload,
            incompleto=faltan,
            estado=INCOMPLETO if faltan else PENDIENTE,
            error="",
        )
        self._repo.evento(captura_id, "completada", ", ".join(sorted(validos)), actor)
        return c if faltan or not enviar else self.procesar(captura_id)

    def _rechazar_factura_duplicada(
        self, kind: str, draft: dict[str, Any], excluir: int | None
    ) -> None:
        """Una factura (proveedor + número) se captura una sola vez."""
        if kind != FACTURA_PROVEEDOR or vacio(draft.get("numero_factura")):
            return
        for previa in self._repo.facturas_con_numero(str(draft["numero_factura"]).strip()):
            if previa.captura_id != excluir and _mismo_proveedor(previa.payload, draft):
                raise FacturaDuplicada(
                    f"La factura {str(draft['numero_factura']).strip()} de ese proveedor ya fue "
                    f"capturada (captura #{previa.captura_id}, {previa.estado})."
                )

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
        if c.kind == PEDIDO:
            return self._pedido(c, odoo, marca)
        if c.kind == PROVEEDOR:
            return self._proveedor(c, odoo, marca)
        if c.kind == FACTURA_PROVEEDOR:
            return self._factura(c, odoo, marca)
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
                "notas": p.get("notas"),
                "lat": ub.get("latitud"),
                "lon": ub.get("longitud"),
            },
            marca,
        )
        return f"res.partner:{pid}", {"partner_id": pid}

    # ── pedido ───────────────────────────────────────────────────────────────

    def _partner_existente(
        self, odoo: EscritorOdoo, rif: str | None, nombre: str | None
    ) -> tuple[int | None, str]:
        """Partner por RIF y, si no, por nombre exacto del espejo. Ambiguo → ``_Incompleto``."""
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
        return None, ""

    def _pedido(self, c: Captura, odoo: EscritorOdoo, marca: str) -> tuple[str, dict[str, Any]]:
        p = c.payload
        rif = str(p["rif"]) if not vacio(p.get("rif")) else None
        partner, via = self._partner_existente(odoo, rif, str(p["cliente"]))

        lineas: list[dict[str, Any]] = []
        sin_resolver: list[str] = []
        for ln in p["lineas"]:
            ids = self._espejo.productos(str(ln["producto"]))
            if len(ids) != 1 or not ids[0].isdigit():
                motivo = "ambiguo" if len(ids) > 1 else "no encontrado"
                sin_resolver.append(f"producto «{ln['producto']}» {motivo} en el catálogo")
                continue
            lineas.append(
                {
                    "product_id": int(ids[0]),
                    "cantidad": a_decimal(ln["cantidad"], "Cantidad"),
                    "precio": ln.get("precio_unitario")
                    if not vacio(ln.get("precio_unitario"))
                    else None,
                }
            )
        if sin_resolver:
            raise _Incompleto(sin_resolver)

        creado = False
        if partner is None:
            if not rif:
                raise _Incompleto(["cliente: no existe en Odoo; indica su RIF para crearlo"])
            ub = p.get("ubicacion") or {}
            partner = odoo.crear_cliente(
                {
                    "nombre": p["cliente"],
                    "rif": rif,
                    "email": p.get("email"),
                    "telefono": p.get("telefono"),
                    "direccion_fiscal": p.get("direccion_fiscal"),
                    "direccion_entrega": None
                    if p.get("entrega_igual_fiscal")
                    else p.get("direccion_entrega"),
                    "lat": ub.get("latitud"),
                    "lon": ub.get("longitud"),
                },
                marca,
            )
            creado, via = True, "creado"
        order = odoo.crear_pedido(
            partner_id=partner,
            lineas=lineas,
            marca=marca,
            nota=p.get("observaciones"),
            vendedor_id=odoo.vendedor_id(str(c.fuente.get("vendedor_email") or "")),
            shipping_id=odoo.entrega_de(partner) if creado else None,
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
        ]
        pago = odoo.crear_pago(
            partner_id=partner,
            monto=Decimal(str(p["monto"])),
            journal_id=diario,
            moneda_id=moneda_id,
            es_cliente=c.kind == PAGO,
            marca=marca,
            memo=" · ".join(x for x in partes if x),
            fecha=_fecha_iso(p.get("fecha_pago")),
        )
        return f"account.payment:{pago}", {**extra, "partner_id": partner, "diario_id": diario}

    # ── proveedor y factura de proveedor ─────────────────────────────────────

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

    def _proveedor_de_factura(self, odoo: EscritorOdoo, p: dict[str, Any]) -> tuple[int, str]:
        """El proveedor de una factura debe EXISTIR: no se crea desde una factura ni se adivina."""
        rif = str(p["rif"]) if not vacio(p.get("rif")) else None
        nombre = str(p["proveedor"]).strip() if not vacio(p.get("proveedor")) else None
        if rif:
            ids = odoo.partners_por_rif(rif)
            if len(ids) > 1:
                raise _Incompleto([f"proveedor: el RIF {rif} está en {len(ids)} contactos de Odoo"])
            if ids:
                return ids[0], "rif"
        if nombre:
            ids = odoo.proveedores_por_nombre(nombre)
            if len(ids) > 1:
                raise _Incompleto(
                    [f"proveedor: «{nombre}» es ambiguo en Odoo ({len(ids)} coincidencias)"]
                )
            if ids:
                return ids[0], "nombre"
            en_espejo = [i for i in self._espejo.clientes_por_nombre(nombre) if i.isdigit()]
            if len(en_espejo) > 1:
                raise _Incompleto(
                    [f"proveedor: «{nombre}» es ambiguo ({len(en_espejo)} coincidencias)"]
                )
            if en_espejo:
                return int(en_espejo[0]), "espejo"
        quien = f"el RIF {rif}" if rif else f"«{nombre}»"
        raise _Incompleto(
            [f"proveedor: {quien} no existe en Odoo: créalo (/proveedor) y reintenta"]
        )

    def _factura(self, c: Captura, odoo: EscritorOdoo, marca: str) -> tuple[str, dict[str, Any]]:
        """``account.move`` de proveedor en BORRADOR; el IVA y el control quedan en la nota."""
        p = c.payload
        partner, via = self._proveedor_de_factura(odoo, p)
        numero = str(p["numero_factura"]).strip()
        propia = odoo.factura_por_marca(marca)  # un reintento tras crearla nosotros
        if propia is not None:
            return f"account.move:{propia}", {"partner_id": partner, "proveedor_via": via}
        ajena = odoo.factura_proveedor_por_ref(partner, numero)
        if ajena is not None:
            raise OdooEscrituraError(
                f"La factura {numero} ya existe en Odoo para ese proveedor (account.move {ajena})."
            )
        moneda_id = odoo.moneda_id(str(p["moneda"]))
        if moneda_id is None:
            raise _Incompleto([f"moneda: «{p['moneda']}» no existe en Odoo"])
        base = p.get("base_imponible")
        usa_base = not vacio(base) and Decimal(str(base)) > 0
        monto = Decimal(str(base if usa_base else p["monto_total"]))
        notas = [
            f"Factura proveedor {numero}",
            f"Control {p['numero_control']}" if not vacio(p.get("numero_control")) else "",
            f"IVA {p['iva']}" if not vacio(p.get("iva")) else "",
            f"Monto total {p['monto_total']} {p['moneda']}",
            f"Orden {p['orden_ref']}" if not vacio(p.get("orden_ref")) else "",
            str(p["notas"]) if not vacio(p.get("notas")) else "",
        ]
        concepto = (
            str(p["concepto"]) if not vacio(p.get("concepto")) else f"Factura proveedor {numero}"
        )
        factura = odoo.crear_factura_proveedor(
            partner_id=partner,
            numero=numero,
            fecha_emision=str(p["fecha_emision"]),
            fecha_vencimiento=_fecha_iso(p.get("fecha_vencimiento")),
            moneda_id=moneda_id,
            monto=monto,
            concepto=concepto,
            marca=marca,
            narration=" · ".join(x for x in notas if x),
        )
        return f"account.move:{factura}", {
            "partner_id": partner,
            "proveedor_via": via,
            "monto_linea": str(monto),
        }


__all__ = [
    "CapturaCerrada",
    "CapturaInvalida",
    "FacturaDuplicada",
    "NoCompletable",
    "NoEncontrada",
    "ServicioCapturas",
    "resolver_diario",
]
