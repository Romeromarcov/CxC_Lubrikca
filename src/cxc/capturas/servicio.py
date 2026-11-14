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
    CAMPOS_PAGO,
    CONTACTO,
    ENVIADO,
    ERROR,
    INCOMPLETO,
    KINDS_COMPLETABLES,
    PAGO,
    PEDIDO,
    PENDIENTE,
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
    """Solo los pagos se completan desde el bot."""


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
        [
            "diario: no pude determinar el diario (indica el banco o configura CAPTURAS_DIARIOS)"
        ]
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
        faltan = validar(kind, draft)
        if not vacio(draft.get("rif")):
            draft = {**draft, "rif": normalizar_rif(str(draft["rif"]))}
        return self._repo.crear(
            kind=kind,
            idempotency_key=idempotency_key,
            payload=draft,
            fuente=source,
            auto=bool(source.get("auto")),
            estado=INCOMPLETO if faltan else PENDIENTE,
            incompleto=faltan,
        )

    def completar(
        self, captura_id: int, cambios: dict[str, Any], actor: str = "bot", enviar: bool = True
    ) -> Captura:
        """Fusiona correcciones de un pago y lo reevalúa; ``enviar=False`` no lo manda a Odoo."""
        c = self._existente(captura_id)
        if c.kind not in KINDS_COMPLETABLES:
            raise NoCompletable("Solo se pueden completar pagos.")
        if c.estado == ENVIADO:
            raise CapturaCerrada("Ya está en Odoo: edítala allá.")
        validos = {k: v for k, v in cambios.items() if k in CAMPOS_PAGO and not vacio(v)}
        payload = {**c.payload, **validos}
        faltan = validar(c.kind, payload)  # puede lanzar CapturaInvalida
        if not vacio(payload.get("rif")):
            payload["rif"] = normalizar_rif(str(payload["rif"]))
        c = self._repo.actualizar(
            captura_id,
            payload=payload,
            incompleto=faltan,
            estado=INCOMPLETO if faltan else PENDIENTE,
            error="",
        )
        self._repo.evento(captura_id, "completada", ", ".join(sorted(validos)), actor)
        return c if faltan or not enviar else self.procesar(captura_id)

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
                    [f"cliente: hay varios clientes llamados «{nombre}»; indica el RIF"]
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
        partes = [
            marca,
            f"ref {p['referencia']}" if not vacio(p.get("referencia")) else "",
            str(p.get("banco") or ""),
            str(p.get("metodo_pago") or ""),
            f"pedido {p['pedido_ref']}" if not vacio(p.get("pedido_ref")) else "",
            f"factura {p['factura_ref']}" if not vacio(p.get("factura_ref")) else "",
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


__all__ = [
    "CapturaCerrada",
    "CapturaInvalida",
    "NoCompletable",
    "NoEncontrada",
    "ServicioCapturas",
    "resolver_diario",
]
