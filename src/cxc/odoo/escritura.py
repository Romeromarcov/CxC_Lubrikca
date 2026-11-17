"""Escritura a Odoo de lo capturado por Telegram — SOLO BORRADORES.

Es la única parte de CxC que escribe en Odoo (decisión del owner, nov-2026; ver README). Reglas:

- **Credencial aparte**: ``ODOO_WRITE_USERNAME`` / ``ODOO_WRITE_PASSWORD`` (misma URL y base que la
  lectura). Debe ser un usuario de Odoo con permisos MÍNIMOS: crear en ``res.partner``,
  ``sale.order`` y ``account.payment``, y nada más. La credencial de lectura no se toca.
- **Interruptor**: ``CAPTURAS_ODOO_ESCRITURA=1``. Sin interruptor o sin credencial, ``from_env``
  devuelve ``None`` y NUNCA se abre una conexión a Odoo.
- **Solo borradores**: contacto marcado «[Borrador Telegram]», ``sale.order`` sin confirmar y
  ``account.payment`` sin publicar. Una persona los confirma dentro de Odoo.
- **Idempotente**: cada creación lleva la marca ``BOT-<id>`` y se busca antes de crear, así un
  reintento nunca duplica.
- **Sin filtrar detalles**: los errores técnicos de Odoo van al log; hacia arriba sale un mensaje
  legible (``OdooEscrituraError``) y si conviene reintentar (``transitorio``).
"""

from __future__ import annotations

import logging
import os
import unicodedata
import xmlrpc.client
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from cxc.capturas.modelos import clase_moneda, normalizar_rif, variantes_rif
from cxc.config import OdooConfig

logger = logging.getLogger("cxc.odoo.escritura")

ExecuteFn = Callable[[str, str, list[Any], dict[str, Any]], Any]

NOTA_BORRADOR = "[Borrador Telegram] pendiente de completar"
NOTA_PROVEEDOR_DE_DOCUMENTO = "[Borrador Telegram] proveedor creado desde un documento; revisar"
SALTO = "\n"
TIMEOUT_SEGUNDOS = 30.0


class OdooEscrituraError(Exception):
    """Fallo al escribir en Odoo. ``transitorio``: conviene reintentar (red caída, timeout)."""

    def __init__(self, mensaje: str, transitorio: bool = False) -> None:
        super().__init__(mensaje)
        self.transitorio = transitorio


@dataclass(frozen=True)
class EscrituraConfig:
    url: str
    db: str
    username: str
    password: str

    @classmethod
    def from_env(cls) -> EscrituraConfig | None:
        """``None`` si la escritura no está activada o falta la credencial (nunca escribe)."""
        if os.environ.get("CAPTURAS_ODOO_ESCRITURA", "").strip().lower() not in (
            "1",
            "true",
            "yes",
            "si",
            "sí",
        ):
            return None
        user = os.environ.get("ODOO_WRITE_USERNAME", "")
        password = os.environ.get("ODOO_WRITE_PASSWORD", "")
        if not user or not password:
            logger.warning("CAPTURAS_ODOO_ESCRITURA activa pero falta ODOO_WRITE_USERNAME/PASSWORD")
            return None
        try:
            lectura = OdooConfig.from_env()
        except KeyError:
            return None
        return cls(url=lectura.url, db=lectura.db, username=user, password=password)


def _transporte(url: str, timeout: float) -> xmlrpc.client.Transport:  # pragma: no cover - red
    base: type[xmlrpc.client.Transport] = (
        xmlrpc.client.SafeTransport if url.startswith("https") else xmlrpc.client.Transport
    )

    class _ConTimeout(base):  # type: ignore[valid-type, misc]
        def make_connection(self, host: Any) -> Any:
            conn = super().make_connection(host)
            conn.timeout = timeout
            return conn

    return _ConTimeout()


def conectar(
    cfg: EscrituraConfig, timeout: float = TIMEOUT_SEGUNDOS
) -> ExecuteFn:  # pragma: no cover
    """Conexión XML-RPC con timeout (red externa; los tests usan dobles de ``EscritorOdoo``)."""
    try:
        common = xmlrpc.client.ServerProxy(
            f"{cfg.url}/xmlrpc/2/common", transport=_transporte(cfg.url, timeout)
        )
        uid = common.authenticate(cfg.db, cfg.username, cfg.password, {})
    except (OSError, xmlrpc.client.ProtocolError) as e:
        raise OdooEscrituraError("No pude conectar con Odoo.", transitorio=True) from e
    if not uid:
        raise OdooEscrituraError("Odoo rechazó la credencial de escritura.")
    models = xmlrpc.client.ServerProxy(
        f"{cfg.url}/xmlrpc/2/object", transport=_transporte(cfg.url, timeout)
    )

    def execute(model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        return models.execute_kw(cfg.db, uid, cfg.password, model, method, args, kwargs)

    return execute


def _ilike_exacto(texto: str) -> str:
    """Escapa los comodines (porcentaje, guion bajo, barra) para usar ``=ilike`` como igualdad."""
    return texto.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _vals(**kw: Any) -> dict[str, Any]:
    """Quita los ``None`` (XML-RPC no los acepta por defecto)."""
    return {k: v for k, v in kw.items() if v is not None}


class EscritorOdoo:
    """Operaciones de escritura sobre un ``execute`` ya conectado (inyectable en los tests)."""

    def __init__(self, execute: ExecuteFn) -> None:
        self._execute = execute
        self._campos: dict[str, set[str]] = {}
        self._tipos_id: dict[str, int] | None = None

    # ── infraestructura ──────────────────────────────────────────────────────

    def _call(
        self,
        model: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        try:
            return self._execute(model, method, args or [], kwargs or {})
        except OdooEscrituraError:
            raise
        except xmlrpc.client.Fault as e:
            logger.error("Odoo fault en %s.%s: %s", model, method, e.faultString)
            raise OdooEscrituraError(f"Odoo rechazó la operación ({model}).") from e
        except (TimeoutError, OSError, xmlrpc.client.ProtocolError) as e:
            logger.warning("Odoo no respondió en %s.%s: %s", model, method, type(e).__name__)
            raise OdooEscrituraError("Odoo no respondió; se reintentará.", transitorio=True) from e

    def campos(self, modelo: str) -> set[str]:
        if modelo not in self._campos:
            info = self._call(modelo, "fields_get", [], {"attributes": ["string"]})
            self._campos[modelo] = set(info or {})
        return self._campos[modelo]

    def _buscar(self, modelo: str, dominio: list[Any], limite: int = 3) -> list[int]:
        ids = self._call(modelo, "search", [dominio], {"limit": limite})
        return [int(i) for i in ids]

    def _crear(self, modelo: str, vals: dict[str, Any]) -> int:
        nuevo = self._call(modelo, "create", [vals])
        return int(nuevo[0] if isinstance(nuevo, list) else nuevo)

    # ── resolución en Odoo ───────────────────────────────────────────────────

    def partners_por_rif(self, rif: str) -> list[int]:
        return self._buscar("res.partner", [["vat", "in", variantes_rif(rif)]])

    def proveedores_por_nombre(self, nombre: str) -> list[int]:
        return self._buscar(
            "res.partner", [["name", "=ilike", _ilike_exacto(nombre)], ["supplier_rank", ">", 0]]
        )

    def moneda_id(self, codigo: str) -> int | None:
        objetivo = clase_moneda(codigo)
        monedas = self._call(
            "res.currency",
            "search_read",
            [[["active", "=", True]]],
            {"fields": ["id", "name"], "limit": 50},
        )
        for m in monedas:
            if clase_moneda(str(m["name"])) == objetivo:
                return int(m["id"])
        return None

    def vendedor_id(self, email: str) -> int | None:
        """``res.users`` por login o email; si hay más de uno no se adivina."""
        if not email:
            return None
        e = _ilike_exacto(email)
        ids = self._buscar("res.users", ["|", ["login", "=ilike", e], ["email", "=ilike", e]], 2)
        return ids[0] if len(ids) == 1 else None

    # ── escrituras (borradores) ──────────────────────────────────────────────

    def _gps(self, modelo: str, lat: Any, lon: Any) -> dict[str, Any]:
        if lat is None or lon is None or "partner_latitude" not in self.campos(modelo):
            return {}
        return {"partner_latitude": float(lat), "partner_longitude": float(lon)}

    def _mapa_tipos_identificacion(self) -> dict[str, int]:
        """IDs de ``l10n_latam.identification.type`` (RIF vs Cédula Venezolano/Extranjero)."""
        if self._tipos_id is not None:
            return self._tipos_id
        filas = self._call(
            "l10n_latam.identification.type",
            "search_read",
            [[]],
            {"fields": ["id", "name"], "limit": 50},
        )
        mapa: dict[str, int] = {}
        for f in filas or []:
            limpio = "".join(
                c
                for c in unicodedata.normalize("NFD", str(f.get("name") or "").lower())
                if unicodedata.category(c) != "Mn"
            ).strip()
            fid = int(f["id"])
            if limpio == "rif":
                mapa["rif"] = fid
            elif "cedula" in limpio and "venezolano" in limpio:
                mapa["cedula_v"] = fid
            elif "cedula" in limpio and "extranjero" in limpio:
                mapa["cedula_e"] = fid
        self._tipos_id = mapa
        return mapa

    def _identificacion_odoo(self, rif: str | None) -> dict[str, Any]:
        """Formatea ``vat`` y ``l10n_latam_identification_type_id`` según exige Odoo 18 Venezuela.

        - RIF de 9 dígitos (``J-123456789``) → ``vat = 'J-12345678-9'`` y tipo ``RIF``.
        - Cédula de 6–8 dígitos sin dígito verificador (``V-12345678`` / ``E-82345678``) →
          ``vat = 'V-12345678'`` y tipo ``Cédula Venezolano`` / ``Cédula Extranjero``.
        """
        if not rif:
            return {}
        letra, digs = normalizar_rif(str(rif)).split("-", 1)
        es_rif_completo = len(digs) >= 9
        vat = f"{letra}-{digs[:-1]}-{digs[-1]}" if es_rif_completo else f"{letra}-{digs}"
        out: dict[str, Any] = {"vat": vat}
        if "l10n_latam_identification_type_id" in self.campos("res.partner"):
            tipos = self._mapa_tipos_identificacion()
            clave = (
                "rif"
                if es_rif_completo
                else ("cedula_v" if letra == "V" else ("cedula_e" if letra == "E" else ""))
            )
            if clave and clave in tipos:
                out["l10n_latam_identification_type_id"] = tipos[clave]
        return out

    def crear_contacto_borrador(self, datos: dict[str, Any], marca: str) -> int:
        previo = self._buscar("res.partner", [["ref", "=", marca]], 1)
        if previo:
            return previo[0]
        nota = NOTA_BORRADOR + (f"\n{datos['notas']}" if datos.get("notas") else "")
        vals = _vals(
            name=str(datos["nombre"]).strip(),
            phone=str(datos["telefono"]).strip(),
            email=datos.get("email") or None,
            comment=nota,
            customer_rank=0,
            ref=marca,
            **self._gps("res.partner", datos.get("lat"), datos.get("lon")),
        )
        return self._crear("res.partner", vals)

    def crear_cliente(self, datos: dict[str, Any], marca: str) -> int:
        """Cliente nuevo desde un pedido (con RIF); con otra dirección de entrega crea el hijo."""
        previo = self._buscar("res.partner", [["ref", "=", marca]], 1)
        if previo:
            return previo[0]
        juridica = str(datos["rif"]).upper()[:1] in ("J", "G")
        vals = _vals(
            name=str(datos["nombre"]).strip(),
            email=datos.get("email") or None,
            phone=datos.get("telefono") or None,
            street=datos.get("direccion_fiscal") or None,
            company_type="company" if juridica else "person",
            customer_rank=1,
            ref=marca,
            **self._identificacion_odoo(str(datos["rif"])),
            **(
                {}
                if datos.get("direccion_entrega")
                else self._gps("res.partner", datos.get("lat"), datos.get("lon"))
            ),
        )
        partner = self._crear("res.partner", vals)
        if datos.get("direccion_entrega"):
            self._crear(
                "res.partner",
                _vals(
                    parent_id=partner,
                    type="delivery",
                    name=str(datos["nombre"]).strip(),
                    street=datos["direccion_entrega"],
                    **self._gps("res.partner", datos.get("lat"), datos.get("lon")),
                ),
            )
        return partner

    def entrega_de(self, partner_id: int) -> int | None:
        ids = self._buscar(
            "res.partner", [["parent_id", "=", partner_id], ["type", "=", "delivery"]], 1
        )
        return ids[0] if ids else None

    def crear_pedido(
        self,
        *,
        partner_id: int,
        lineas: list[dict[str, Any]],
        marca: str,
        nota: str | None = None,
        vendedor_id: int | None = None,
        shipping_id: int | None = None,
        fecha_entrega: str | None = None,
    ) -> int:
        """``sale.order`` en borrador. Sin ``price_unit`` Odoo aplica la lista del cliente."""
        previo = self._buscar("sale.order", [["client_order_ref", "=", marca]], 1)
        if previo:
            return previo[0]
        order_line = []
        for ln in lineas:
            v: dict[str, Any] = {
                "product_id": int(ln["product_id"]),
                "product_uom_qty": float(Decimal(str(ln["cantidad"]))),
            }
            if ln.get("precio") is not None:
                v["price_unit"] = float(Decimal(str(ln["precio"])))
            order_line.append((0, 0, v))
        vals = _vals(
            partner_id=partner_id,
            client_order_ref=marca,
            note=nota or None,
            user_id=vendedor_id,
            partner_shipping_id=shipping_id,
            commitment_date=f"{fecha_entrega} 12:00:00" if fecha_entrega else None,
            order_line=order_line,
        )
        return self._crear("sale.order", vals)

    def nombre_pedido(self, order_id: int) -> str:
        r = self._call("sale.order", "read", [[order_id]], {"fields": ["name"]})
        return str(r[0]["name"]) if r else ""

    def _campo_memo(self) -> str:
        campos = self.campos("account.payment")
        for c in ("memo", "payment_reference", "ref"):
            if c in campos:
                return c
        raise OdooEscrituraError(
            "No encuentro el campo de referencia en account.payment: revisa la versión de Odoo."
        )

    def crear_pago(
        self,
        *,
        partner_id: int,
        monto: Decimal,
        journal_id: int,
        moneda_id: int,
        es_cliente: bool,
        marca: str,
        memo: str,
        fecha: str | None = None,
    ) -> int:
        """``account.payment`` en borrador (sin publicar). Nunca crea partners."""
        campo = self._campo_memo()
        previo = self._buscar("account.payment", [[campo, "ilike", marca]], 1)
        if previo:
            return previo[0]
        vals = _vals(
            partner_id=partner_id,
            amount=float(monto),
            payment_type="inbound" if es_cliente else "outbound",
            partner_type="customer" if es_cliente else "supplier",
            journal_id=journal_id,
            currency_id=moneda_id,
            date=fecha,
            **{campo: memo},
        )
        return self._crear("account.payment", vals)

    # ── proveedores y facturas de proveedor (borradores) ─────────────────────

    def partner_por_marca(self, marca: str) -> int | None:
        """Partner que ya creó esta captura (la marca va en ``ref``)."""
        ids = self._buscar("res.partner", [["ref", "=", marca]], 1)
        return ids[0] if ids else None

    def partners_por_nombre(self, nombre: str) -> list[int]:
        """Contactos con ese nombre exacto (sin mayúsculas), sean o no proveedores."""
        return self._buscar("res.partner", [["name", "=ilike", _ilike_exacto(nombre)]])

    def crear_proveedor(self, datos: dict[str, Any], marca: str) -> int:
        """Proveedor nuevo en borrador (``supplier_rank`` 1, sin rango de cliente).

        ``rif`` y ``telefono`` son opcionales (un proveedor leído de un recibo puede no traerlos);
        ``nota`` permite indicar el origen («creado desde un documento»).
        """
        previo = self._buscar("res.partner", [["ref", "=", marca]], 1)
        if previo:
            return previo[0]
        nota = [datos.get("nota") or NOTA_BORRADOR]
        if datos.get("dias_credito") not in (None, ""):
            nota.append(f"Días de crédito: {datos['dias_credito']}")
        if datos.get("notas"):
            nota.append(str(datos["notas"]))
        rif = str(datos["rif"]) if datos.get("rif") else None
        telefono = str(datos["telefono"]).strip() if datos.get("telefono") else None
        vals = _vals(
            name=str(datos["nombre"]).strip(),
            phone=telefono,
            email=datos.get("email") or None,
            street=datos.get("direccion") or None,
            company_type=(
                ("company" if rif.upper()[:1] in ("J", "G") else "person") if rif else None
            ),
            supplier_rank=1,
            customer_rank=0,
            comment=SALTO.join(nota),
            ref=marca,
            **self._identificacion_odoo(rif),
            **self._gps("res.partner", datos.get("lat"), datos.get("lon")),
        )
        return self._crear("res.partner", vals)

    def factura_por_marca(self, marca: str) -> int | None:
        """Factura de proveedor que ya creó esta captura (la marca va en ``narration``)."""
        ids = self._buscar(
            "account.move", [["move_type", "=", "in_invoice"], ["narration", "ilike", marca]], 1
        )
        return ids[0] if ids else None

    def factura_proveedor_por_ref(self, partner_id: int, numero: str) -> int | None:
        """``account.move`` de proveedor con ``ref`` = número de factura y ese partner."""
        ids = self._buscar(
            "account.move",
            [
                ["move_type", "=", "in_invoice"],
                ["partner_id", "=", partner_id],
                ["ref", "=", numero.strip()],
            ],
            1,
        )
        return ids[0] if ids else None

    def _validar_campos_factura(self) -> bool:
        """Verifica con ``fields_get`` los campos de la factura; dice si hay impuestos en línea."""
        faltan = {
            "move_type",
            "partner_id",
            "ref",
            "invoice_date",
            "currency_id",
            "invoice_line_ids",
            "narration",
        } - self.campos("account.move")
        if faltan:
            raise OdooEscrituraError(
                "Odoo no tiene los campos de factura esperados (" + ", ".join(sorted(faltan)) + ")."
            )
        return "tax_ids" in self.campos("account.move.line")

    def crear_factura_proveedor(
        self,
        *,
        partner_id: int,
        numero: str,
        fecha_emision: str,
        fecha_vencimiento: str | None,
        moneda_id: int,
        monto: Decimal,
        concepto: str,
        marca: str,
        narration: str,
    ) -> int:
        """``account.move`` ``in_invoice`` en BORRADOR (no se publica) con una línea.

        No inventa impuestos: la línea va con ``tax_ids`` vacío y el IVA y el número de control que
        dio el bot quedan en ``narration`` para que contabilidad los asigne en Odoo.
        """
        con_impuestos = self._validar_campos_factura()
        previo = self.factura_por_marca(marca)
        if previo:
            return previo
        linea: dict[str, Any] = {"name": concepto, "quantity": 1.0, "price_unit": float(monto)}
        if con_impuestos:
            linea["tax_ids"] = [(6, 0, [])]
        vals = _vals(
            move_type="in_invoice",
            partner_id=partner_id,
            ref=numero.strip(),
            invoice_date=fecha_emision,
            invoice_date_due=fecha_vencimiento
            if fecha_vencimiento and "invoice_date_due" in self.campos("account.move")
            else None,
            currency_id=moneda_id,
            narration=f"{marca} · {narration}",
            invoice_line_ids=[(0, 0, linea)],
        )
        return self._crear("account.move", vals)
