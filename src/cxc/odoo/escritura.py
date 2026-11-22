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
import re
import unicodedata
import xmlrpc.client
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
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


_OLC_ALFABETO = "23456789CFGHJMPQRVWX"


def encode_plus_code(lat: Any, lon: Any) -> str:
    """Calcula el Plus Code oficial de 10 dígitos (Open Location Code) para ``(lat, lon)``."""
    lat_d = min(max(Decimal(str(lat)), Decimal(-90)), Decimal(90))
    if lat_d == Decimal(90):
        lat_d -= Decimal("0.000025")
    lon_d = ((Decimal(str(lon)) + Decimal(180)) % Decimal(360)) - Decimal(180)
    lat_i = int((lat_d + Decimal(90)) * 8000)
    lon_i = int((lon_d + Decimal(180)) * 8000)
    pares: list[str] = []
    for _ in range(5):
        pares.append(_OLC_ALFABETO[lat_i % 20] + _OLC_ALFABETO[lon_i % 20])
        lat_i //= 20
        lon_i //= 20
    pares.reverse()
    codigo = "".join(pares)
    return f"{codigo[:8]}+{codigo[8:]}"


def _vals(**kw: Any) -> dict[str, Any]:
    """Quita los ``None`` (XML-RPC no los acepta por defecto)."""
    return {k: v for k, v in kw.items() if v is not None}


class EscritorOdoo:
    """Operaciones de escritura sobre un ``execute`` ya conectado (inyectable en los tests)."""

    def __init__(self, execute: ExecuteFn) -> None:
        self._execute = execute
        self._campos: dict[str, set[str]] = {}
        self._tipos_id: dict[str, int] | None = None
        self._diario_compras_ves: int | None = None
        self._diario_compras_consultado: bool = False
        self._moneda_compania: int | None = None
        self._moneda_compania_consultada: bool = False
        self._impuestos_compra: dict[str, int | None] = {}

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
        f_lat, f_lon = float(lat), float(lon)
        if f_lat == 0.0 and f_lon == 0.0:
            return {}
        return {"partner_latitude": f_lat, "partner_longitude": f_lon}

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
        try:
            letra, digs = normalizar_rif(str(rif)).split("-", 1)
        except ValueError:
            return {}
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
        lat, lon = datos.get("lat"), datos.get("lon")
        gps_vals = self._gps("res.partner", lat, lon)
        plus_raw = str(datos.get("plus_code") or "").strip()
        plus_code = (
            plus_raw
            if plus_raw and plus_raw != "6FG22222+22"
            else (encode_plus_code(lat, lon) if gps_vals else None)
        )
        calle2 = (
            {"street2": plus_code} if plus_code and "street2" in self.campos("res.partner") else {}
        )
        rif = str(datos["rif"]).strip() if datos.get("rif") else None
        telefono = str(datos.get("telefono") or "").strip() or None
        vals = _vals(
            name=str(datos["nombre"]).strip(),
            phone=telefono,
            email=datos.get("email") or None,
            street=datos.get("direccion") or None,
            comment=nota,
            customer_rank=0,
            ref=marca,
            company_type=(
                ("company" if rif.upper()[:1] in ("J", "G") else "person") if rif else None
            ),
            **calle2,
            **self._identificacion_odoo(rif),
            **gps_vals,
        )
        return self._crear("res.partner", vals)

    def leer_partner(self, partner_id: int) -> dict[str, Any] | None:
        """Lee los datos principales de un ``res.partner`` existente en Odoo."""
        deseados = [
            "id",
            "name",
            "vat",
            "email",
            "phone",
            "mobile",
            "street",
            "street2",
            "partner_latitude",
            "partner_longitude",
            "customer_rank",
            "supplier_rank",
            "comment",
        ]
        campos = [c for c in deseados if c in self.campos("res.partner")]
        if "id" not in campos:
            campos.append("id")
        filas = self._call("res.partner", "read", [[partner_id]], {"fields": campos})
        return dict(filas[0]) if filas else None

    def actualizar_contacto(
        self,
        partner_id: int,
        datos: dict[str, Any],
        *,
        promover_cliente: bool = False,
        promover_proveedor: bool = False,
        marca: str | None = None,
    ) -> int:
        """Actualiza un contacto existente en Odoo (edición o promoción a cliente/proveedor)."""
        actual = self.leer_partner(partner_id) or {}
        lat, lon = datos.get("lat"), datos.get("lon")
        gps_vals = self._gps("res.partner", lat, lon)
        plus_raw = str(datos.get("plus_code") or "").strip()
        plus_code = (
            plus_raw
            if plus_raw and plus_raw != "6FG22222+22"
            else (encode_plus_code(lat, lon) if gps_vals else None)
        )
        calle2 = (
            {"street2": plus_code} if plus_code and "street2" in self.campos("res.partner") else {}
        )
        rif = str(datos["rif"]).strip() if datos.get("rif") else None
        vals: dict[str, Any] = {}
        if datos.get("nombre"):
            vals["name"] = str(datos["nombre"]).strip()
        if datos.get("telefono"):
            vals["phone"] = str(datos["telefono"]).strip()
        if datos.get("email"):
            vals["email"] = str(datos["email"]).strip()
        if datos.get("direccion") or datos.get("direccion_fiscal"):
            vals["street"] = str(datos.get("direccion") or datos.get("direccion_fiscal")).strip()
        if rif:
            vals.update(self._identificacion_odoo(rif))
            vals["company_type"] = "company" if rif.upper()[:1] in ("J", "G") else "person"
        vals.update(calle2)
        vals.update(gps_vals)
        if promover_cliente and int(actual.get("customer_rank") or 0) == 0:
            vals["customer_rank"] = 1
        if (
            promover_proveedor
            and int(actual.get("supplier_rank") or 0) == 0
            and "supplier_rank" in self.campos("res.partner")
        ):
            vals["supplier_rank"] = 1
        if datos.get("notas"):
            previo_c = str(actual.get("comment") or "").strip()
            nueva_nota = str(datos["notas"]).strip()
            if nueva_nota and nueva_nota not in previo_c:
                vals["comment"] = f"{previo_c}\n{nueva_nota}".strip() if previo_c else nueva_nota
        if vals:
            self._call("res.partner", "write", [[partner_id], vals])
        if marca:
            etiquetas_prom = []
            if promover_cliente:
                etiquetas_prom.append("cliente")
            if promover_proveedor:
                etiquetas_prom.append("proveedor")
            sufijo = f" y promovido a {'/'.join(etiquetas_prom)}" if etiquetas_prom else ""
            self.nota_chatter(
                "res.partner",
                partner_id,
                f"{marca} · Contacto actualizado{sufijo}",
            )
        return partner_id

    def crear_cliente(self, datos: dict[str, Any], marca: str) -> int:
        """Cliente nuevo desde un pedido: dirección, GPS y Plus Code en el principal."""
        previo = self._buscar("res.partner", [["ref", "=", marca]], 1)
        if previo:
            return previo[0]
        juridica = str(datos["rif"]).upper()[:1] in ("J", "G")
        lat, lon = datos.get("lat"), datos.get("lon")
        gps_vals = self._gps("res.partner", lat, lon)
        plus_raw = str(datos.get("plus_code") or "").strip()
        plus_code = (
            plus_raw
            if plus_raw and plus_raw != "6FG22222+22"
            else (encode_plus_code(lat, lon) if gps_vals else None)
        )
        calle = datos.get("direccion_fiscal") or datos.get("direccion_entrega") or None
        calle2 = (
            {"street2": plus_code} if plus_code and "street2" in self.campos("res.partner") else {}
        )
        vals = _vals(
            name=str(datos["nombre"]).strip(),
            email=datos.get("email") or None,
            phone=datos.get("telefono") or None,
            street=calle,
            company_type="company" if juridica else "person",
            customer_rank=1,
            ref=marca,
            **calle2,
            **self._identificacion_odoo(str(datos["rif"])),
            **gps_vals,
        )
        return self._crear("res.partner", vals)

    def entrega_de(self, partner_id: int) -> int | None:
        ids = self._buscar(
            "res.partner", [["parent_id", "=", partner_id], ["type", "=", "delivery"]], 1
        )
        return ids[0] if ids else None

    def nota_chatter(self, modelo: str, res_id: int, texto: str) -> None:
        """Publica una nota interna (``mail.mt_note``) en el chatter del documento."""
        if not texto or "message_ids" not in self.campos(modelo):
            return
        self._call(
            modelo,
            "message_post",
            [[res_id]],
            {"body": texto, "message_type": "comment", "subtype_xmlid": "mail.mt_note"},
        )

    def payment_term_id_por_dias(self, dias: int) -> int | None:
        """Busca un término de pago activo en Odoo acorde a ``dias`` (best-effort)."""
        try:
            terms = self._call(
                "account.payment.term",
                "search_read",
                [[["active", "=", True]]],
                {"fields": ["id", "name"], "limit": 100},
            )
        except Exception:
            return None
        if not terms:
            return None
        target = int(dias or 0)
        if target <= 0:
            for t in terms:
                nom = str(t.get("name") or "").lower()
                if any(k in nom for k in ("inmediato", "contado", "immediate", "cash", "0 d")):
                    return int(t["id"])
            return None
        pat_exact = re.compile(rf"(?<!\d){target}(?!\d)")
        for t in terms:
            nom = str(t.get("name") or "").lower()
            if pat_exact.search(nom):
                return int(t["id"])
        try:
            lines = self._call(
                "account.payment.term.line",
                "search_read",
                [[["payment_id", "in", [int(t["id"]) for t in terms]]]],
                {"fields": ["payment_id", "nb_days"], "limit": 300},
            )
            for ln in lines or []:
                if int(ln.get("nb_days") or -1) == target:
                    pid = ln.get("payment_id")
                    if isinstance(pid, list | tuple) and pid:
                        return int(pid[0])
                    if pid:
                        return int(pid)
        except Exception:
            pass
        return None

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
        pricelist_id: int | None = None,
        payment_term_id: int | None = None,
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
            if (
                ln.get("descuento_pct") is not None
                and Decimal(str(ln["descuento_pct"])) > 0
                and "discount" in self.campos("sale.order.line")
            ):
                v["discount"] = float(Decimal(str(ln["descuento_pct"])))
            order_line.append((0, 0, v))
        pl_val = (
            pricelist_id
            if pricelist_id is not None and "pricelist_id" in self.campos("sale.order")
            else None
        )
        pt_val = (
            payment_term_id
            if payment_term_id is not None and "payment_term_id" in self.campos("sale.order")
            else None
        )
        vals = _vals(
            partner_id=partner_id,
            pricelist_id=pl_val,
            payment_term_id=pt_val,
            client_order_ref=marca,
            note=nota or None,
            user_id=vendedor_id,
            partner_shipping_id=shipping_id,
            commitment_date=f"{fecha_entrega} 12:00:00" if fecha_entrega else None,
            order_line=order_line,
        )
        order_id = self._crear("sale.order", vals)
        if nota:
            self.nota_chatter("sale.order", order_id, f"{marca} · {nota}")
        return order_id

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

    def _pago_por_marca(self, marca: str) -> int | None:
        """Busca en ``mail.message`` si esta captura ya creó un ``account.payment``."""
        if "message_ids" not in self.campos("account.payment"):
            return None
        filas = self._call(
            "mail.message",
            "search_read",
            [
                [
                    ["model", "=", "account.payment"],
                    "|",
                    ["body", "ilike", f"{marca} · "],
                    ["body", "ilike", f">{marca}<"],
                ]
            ],
            {"fields": ["res_id"], "limit": 1},
        )
        if filas and filas[0].get("res_id"):
            return int(filas[0]["res_id"])
        return None

    def crear_pago(
        self,
        *,
        partner_id: int,
        monto: Decimal,
        journal_id: int,
        moneda_id: int,
        es_cliente: bool,
        marca: str,
        memo: str | None = None,
        nota_chatter: str | None = None,
        fecha: str | None = None,
    ) -> int:
        """``account.payment`` en borrador (sin publicar).

        - ``memo``: solo la referencia de transferencia (vacío/omitido si es efectivo).
        - ``nota_chatter``: comentario completo del bot registrado como nota en el chatter.
        """
        memo_limpio = memo.strip() if memo and memo.strip() else None
        campo_memo = self._campo_memo() if memo_limpio is not None else None
        previo = self._pago_por_marca(marca)
        if previo is not None:
            return previo
        vals = _vals(
            partner_id=partner_id,
            amount=float(monto),
            payment_type="inbound" if es_cliente else "outbound",
            partner_type="customer" if es_cliente else "supplier",
            journal_id=journal_id,
            currency_id=moneda_id,
            date=fecha,
            **({campo_memo: memo_limpio} if campo_memo and memo_limpio else {}),
        )
        pago_id = self._crear("account.payment", vals)
        self.nota_chatter("account.payment", pago_id, nota_chatter or f"{marca} · Pago registrado")
        return pago_id

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
        lat, lon = datos.get("lat"), datos.get("lon")
        gps_vals = self._gps("res.partner", lat, lon)
        plus_raw = str(datos.get("plus_code") or "").strip()
        plus_code = (
            plus_raw
            if plus_raw and plus_raw != "6FG22222+22"
            else (encode_plus_code(lat, lon) if gps_vals else None)
        )
        calle2 = (
            {"street2": plus_code} if plus_code and "street2" in self.campos("res.partner") else {}
        )
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
            **calle2,
            **self._identificacion_odoo(rif),
            **gps_vals,
        )
        return self._crear("res.partner", vals)

    def factura_por_marca(self, marca: str) -> int | None:
        """Factura de proveedor ya creada por esta captura (delimitada en ``narration``)."""
        ids = self._buscar(
            "account.move",
            [["move_type", "=", "in_invoice"], ["narration", "ilike", f"{marca} · "]],
            1,
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

    def diario_compras_ves_id(self, override: Any = None) -> int | None:
        """Resuelve el diario de compras en VES por defecto (o un override futuro por id/código)."""
        if override not in (None, ""):
            txt = str(override).strip()
            if txt.isdigit():
                return int(txt)
            esc = _ilike_exacto(txt)
            ids = self._buscar(
                "account.journal",
                [
                    ["active", "=", True],
                    ["type", "in", ["purchase", "general"]],
                    "|",
                    ["code", "=ilike", esc],
                    ["name", "=ilike", esc],
                ],
                1,
            )
            return ids[0] if ids else None
        if self._diario_compras_consultado:
            return self._diario_compras_ves
        self._diario_compras_consultado = True
        if "journal_id" not in self.campos("account.move"):
            return None
        ves_id = self.moneda_id("VES")
        diarios = self._call(
            "account.journal",
            "search_read",
            [[["type", "=", "purchase"], ["active", "=", True]]],
            {"fields": ["id", "name", "code", "currency_id"], "limit": 20},
        )
        for d in diarios or []:
            cur = d.get("currency_id")
            cur_id = int(cur[0]) if isinstance(cur, list | tuple) and cur else None
            # currency_id=False usa la moneda de la compañía (VES en Venezuela)
            if cur in (False, None) or (ves_id is not None and cur_id == ves_id):
                self._diario_compras_ves = int(d["id"])
                return self._diario_compras_ves
        return None

    def buscar_impuesto_compra(self, tasa_pct: Decimal = Decimal("16")) -> int | None:
        """Busca un impuesto de compra activo en Odoo cercano a ``tasa_pct`` (cacheado)."""
        clave = f"{Decimal(str(tasa_pct)):.2f}"
        if not hasattr(self, "_impuestos_compra"):
            self._impuestos_compra = {}
        if clave in self._impuestos_compra:
            return self._impuestos_compra[clave]
        objetivo = Decimal(str(tasa_pct))
        filas = self._call(
            "account.tax",
            "search_read",
            [
                [
                    ["type_tax_use", "=", "purchase"],
                    ["active", "=", True],
                    ["amount_type", "=", "percent"],
                ]
            ],
            {"fields": ["id", "name", "amount"], "limit": 40},
        )
        elegido: int | None = None
        mejor_diff: Decimal | None = None
        for f in filas or []:
            try:
                amt = Decimal(str(f.get("amount") or 0))
            except Exception:
                continue
            diff = abs(amt - objetivo)
            if diff <= Decimal("0.1") and (mejor_diff is None or diff < mejor_diff):
                mejor_diff = diff
                elegido = int(f["id"])
        self._impuestos_compra[clave] = elegido
        return elegido

    def verificar_totales_factura(
        self,
        move_id: int,
        *,
        esperado_subtotal: Decimal | None = None,
        esperado_iva: Decimal | None = None,
        esperado_total: Decimal | None = None,
        tolerancia: Decimal = Decimal("0.05"),
    ) -> dict[str, Any]:
        """Verifica tras crear ``account.move`` que Odoo calcule subtotal/IVA/total esperados."""
        try:
            filas = self._call(
                "account.move",
                "read",
                [[int(move_id)]],
                {"fields": ["id", "amount_untaxed", "amount_tax", "amount_total"]},
            )
        except Exception as exc:
            logger.debug("No se pudo verificar totales de account.move %s: %s", move_id, exc)
            return {"ok": True, "omitido": True}
        if not filas or not isinstance(filas[0], dict):
            return {"ok": True, "omitido": True}
        row = filas[0]
        if not any(k in row for k in ("amount_untaxed", "amount_tax", "amount_total")):
            return {"ok": True, "omitido": True}

        q2 = Decimal("0.01")
        odoo_vals = {
            "subtotal": Decimal(str(row.get("amount_untaxed") or 0)).quantize(q2, ROUND_HALF_UP),
            "iva": Decimal(str(row.get("amount_tax") or 0)).quantize(q2, ROUND_HALF_UP),
            "total": Decimal(str(row.get("amount_total") or 0)).quantize(q2, ROUND_HALF_UP),
        }
        esperados: dict[str, Decimal] = {}
        diferencias: dict[str, float] = {}
        pares = (
            ("subtotal", esperado_subtotal),
            ("iva", esperado_iva),
            ("total", esperado_total),
        )
        for campo, esp_raw in pares:
            if esp_raw is None:
                continue
            esp = Decimal(str(esp_raw)).quantize(q2, ROUND_HALF_UP)
            esperados[campo] = esp
            diff = odoo_vals[campo] - esp
            if abs(diff) > tolerancia:
                diferencias[campo] = float(diff)

        ok = not diferencias
        if not ok:
            resumen_diff = ", ".join(
                f"{k}: Odoo={float(odoo_vals[k]):.2f} vs CxC={float(esperados[k]):.2f}"
                for k in diferencias
            )
            logger.warning(
                "Discrepancia post-creación en factura proveedor Odoo %s: %s",
                move_id,
                resumen_diff,
            )
            if "message_ids" in self.campos("account.move"):
                try:
                    self.nota_chatter(
                        "account.move",
                        move_id,
                        f"[ALERTA CXC] Diferencia de totales al crear factura: {resumen_diff}",
                    )
                except Exception as exc_chat:
                    logger.debug("No se pudo publicar alerta en chatter: %s", exc_chat)
        return {
            "ok": ok,
            "odoo": {k: float(v) for k, v in odoo_vals.items()},
            "esperado": {k: float(v) for k, v in esperados.items()},
            "diferencias": diferencias,
        }

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
        journal_id: int | None = None,
        account_id: int | None = None,
        tax_ids: list[int] | None = None,
        numero_control: str | None = None,
        nota_chatter: str | None = None,
    ) -> int:
        """``account.move`` ``in_invoice`` en BORRADOR con una línea.

        Por defecto va siempre al diario de compras en VES (salvo ``journal_id`` explícito).
        Cuando recibe ``tax_ids`` explícitos desde CxC los asigna a la línea para que Odoo
        calcule IVA y subtotal; si ``tax_ids`` es ``None`` mantiene lista vacía sin inventar
        impuestos. Si Odoo expone campo de número de control y llega ``numero_control``, lo escribe.
        """
        con_impuestos = self._validar_campos_factura()
        previo = self.factura_por_marca(marca)
        if previo:
            return previo
        diario = journal_id if journal_id is not None else self.diario_compras_ves_id()
        linea: dict[str, Any] = {"name": concepto, "quantity": 1.0, "price_unit": float(monto)}
        if account_id is not None and "account_id" in self.campos("account.move.line"):
            linea["account_id"] = int(account_id)
        if con_impuestos:
            ids_imp = [int(t) for t in tax_ids if t is not None] if tax_ids else []
            linea["tax_ids"] = [(6, 0, ids_imp)]
        move_campos = self.campos("account.move")
        extra_ctrl: dict[str, Any] = {}
        ctrl_txt = str(numero_control or "").strip()
        if ctrl_txt:
            for cand_ctrl in ("l10n_ve_control_number", "nro_ctrl", "control_number"):
                if cand_ctrl in move_campos:
                    extra_ctrl[cand_ctrl] = ctrl_txt
                    break
        vals = _vals(
            move_type="in_invoice",
            partner_id=partner_id,
            journal_id=diario if diario and "journal_id" in move_campos else None,
            ref=numero.strip(),
            invoice_date=fecha_emision,
            invoice_date_due=fecha_vencimiento
            if fecha_vencimiento and "invoice_date_due" in move_campos
            else None,
            currency_id=moneda_id,
            narration=f"{marca} · {narration}",
            invoice_line_ids=[(0, 0, linea)],
            **extra_ctrl,
        )
        move_id = self._crear("account.move", vals)
        if nota_chatter and "message_ids" in move_campos:
            self.nota_chatter("account.move", move_id, nota_chatter)
        return move_id

    # ── asientos contables genéricos (Zelle 3ros y gastos sin factura) ───────

    @staticmethod
    def _m2o_id_val(val: Any) -> int | None:
        if isinstance(val, list | tuple) and val:
            return int(val[0])
        if isinstance(val, int | str) and str(val).isdigit():
            return int(val)
        return None

    def moneda_compania_id(self) -> int | None:
        """Moneda base de la compañía en Odoo (VES en Lubrikca); cacheada."""
        if self._moneda_compania_consultada:
            return self._moneda_compania
        self._moneda_compania_consultada = True
        filas = self._call(
            "res.company",
            "search_read",
            [[]],
            {"fields": ["id", "currency_id"], "limit": 1},
        )
        if filas:
            self._moneda_compania = self._m2o_id_val(filas[0].get("currency_id"))
        return self._moneda_compania

    def diarios_generales(self) -> list[dict[str, Any]]:
        """Lista diarios contables activos de tipo ``general`` (operaciones varias)."""
        filas = self._call(
            "account.journal",
            "search_read",
            [[["type", "=", "general"], ["active", "=", True]]],
            {"fields": ["id", "name", "code", "currency_id"], "limit": 30},
        )
        out: list[dict[str, Any]] = []
        for f in filas or []:
            cur = f.get("currency_id")
            cur_name = str(cur[1]) if isinstance(cur, list | tuple) and len(cur) > 1 else ""
            out.append(
                {
                    "id": int(f["id"]),
                    "name": str(f.get("name") or ""),
                    "code": str(f.get("code") or ""),
                    "moneda": cur_name,
                }
            )
        return out

    def diario_general_por_defecto(self) -> int | None:
        """Elige el diario ``general`` preferido (MISC/VAR/OP) o el primero activo."""
        diarios = self.diarios_generales()
        if not diarios:
            return None
        for d in diarios:
            txt = f"{d['code']} {d['name']}".upper()
            if any(tok in txt for tok in ("MISC", "VAR", "VARIAS", "GENERAL", "OP")):
                return int(d["id"])
        return int(diarios[0]["id"])

    def leer_cuentas_partner(self, partner_id: int) -> dict[str, int | None]:
        """Lee ``property_account_receivable_id`` y ``property_account_payable_id``."""
        disponibles = self.campos("res.partner")
        pedir = [
            c
            for c in ("property_account_receivable_id", "property_account_payable_id")
            if c in disponibles
        ]
        if not pedir:
            return {"receivable_id": None, "payable_id": None}
        filas = self._call("res.partner", "read", [[partner_id]], {"fields": pedir})
        row = filas[0] if filas else {}
        return {
            "receivable_id": self._m2o_id_val(row.get("property_account_receivable_id")),
            "payable_id": self._m2o_id_val(row.get("property_account_payable_id")),
        }

    def asiento_por_marca(self, marca: str) -> int | None:
        """Asiento contable (``entry``) ya creado por esta operación idempotente."""
        ids = self._buscar(
            "account.move",
            [["move_type", "=", "entry"], ["narration", "ilike", f"{marca} · "]],
            1,
        )
        return ids[0] if ids else None

    def crear_asiento_contable(
        self,
        *,
        journal_id: int,
        fecha: str,
        ref: str,
        moneda_codigo: str,
        lineas: list[dict[str, Any]],
        marca: str,
        narration: str,
        tasa_compania: Decimal | None = None,
        nota_chatter: str | None = None,
    ) -> int:
        """Crea un ``account.move`` (``move_type='entry'``) en BORRADOR balanceado.

        Cada elemento de ``lineas`` debe traer ``account_id``, ``partner_id`` (opcional),
        ``name``, ``debito`` (Decimal/num >= 0) y ``credito`` (Decimal/num >= 0).
        Nunca publica el asiento (solo borrador para revisión contable en Odoo).
        """
        if len(lineas) < 2:
            raise OdooEscrituraError("Un asiento contable requiere al menos 2 líneas balanceadas.")

        q2 = Decimal("0.01")
        debs = [Decimal(str(ln.get("debito") or 0)).quantize(q2, ROUND_HALF_UP) for ln in lineas]
        creds = [Decimal(str(ln.get("credito") or 0)).quantize(q2, ROUND_HALF_UP) for ln in lineas]
        tot_deb = sum(debs, Decimal("0"))
        tot_cred = sum(creds, Decimal("0"))
        if tot_deb <= 0 or tot_deb != tot_cred:
            raise OdooEscrituraError(
                f"Asiento descuadrado: débitos ({tot_deb}) != créditos ({tot_cred})."
            )

        previo = self.asiento_por_marca(marca)
        if previo is not None:
            return previo

        move_campos = self.campos("account.move")
        line_campos = self.campos("account.move.line")
        cur_id = self.moneda_id(moneda_codigo) if moneda_codigo else None
        comp_cur_id = self.moneda_compania_id()
        es_extranjera = (
            cur_id is not None and comp_cur_id is not None and int(cur_id) != int(comp_cur_id)
        )
        tasa = (
            Decimal(str(tasa_compania))
            if (tasa_compania is not None and Decimal(str(tasa_compania)) > 0)
            else None
        )

        # Precalcular importes en moneda de compañía si hay moneda extranjera + tasa explícita
        debs_comp = [
            (d * tasa).quantize(q2, ROUND_HALF_UP) if (es_extranjera and tasa) else d for d in debs
        ]
        creds_comp = [
            (c * tasa).quantize(q2, ROUND_HALF_UP) if (es_extranjera and tasa) else c for c in creds
        ]
        diff_comp = sum(debs_comp, Decimal("0")) - sum(creds_comp, Decimal("0"))
        if diff_comp != Decimal("0"):
            for i in range(len(creds_comp) - 1, -1, -1):
                if creds_comp[i] > 0:
                    creds_comp[i] += diff_comp
                    break

        line_ids: list[tuple[int, int, dict[str, Any]]] = []
        for idx, ln in enumerate(lineas):
            deb, cred = debs[idx], creds[idx]
            val_ln: dict[str, Any] = {
                "account_id": int(ln["account_id"]),
                "name": str(ln.get("name") or ref)[:200],
            }
            if ln.get("partner_id") is not None:
                val_ln["partner_id"] = int(ln["partner_id"])
            if cur_id is not None and (not line_campos or "currency_id" in line_campos):
                val_ln["currency_id"] = int(cur_id)
            if (not line_campos or "amount_currency" in line_campos) and cur_id is not None:
                val_ln["amount_currency"] = float(deb - cred)
            if (not es_extranjera) or (tasa is not None):
                val_ln["debit"] = float(debs_comp[idx])
                val_ln["credit"] = float(creds_comp[idx])
            line_ids.append((0, 0, val_ln))

        vals = _vals(
            move_type="entry",
            journal_id=int(journal_id)
            if (not move_campos or "journal_id" in move_campos)
            else None,
            date=fecha if (not move_campos or "date" in move_campos) else None,
            ref=ref.strip() if (not move_campos or "ref" in move_campos) else None,
            narration=(
                f"{marca} · {narration}"
                if (not move_campos or "narration" in move_campos)
                else None
            ),
            line_ids=line_ids,
        )
        move_id = self._crear("account.move", vals)
        if nota_chatter and "message_ids" in move_campos:
            self.nota_chatter("account.move", move_id, nota_chatter)
        return move_id

    # ── reconciliación contable asistida (CxC / CxP ↔ Odoo) ─────────────────

    def buscar_factura_abierta_de_orden(
        self, so_name: str, order_id: int | None = None
    ) -> int | None:
        """Devuelve el ``account.move.id`` publicado con saldo pendiente de una SO."""
        so_limpia = str(so_name or "").strip()
        try:
            if so_limpia:
                candidatas = self._call(
                    "account.move",
                    "search_read",
                    [
                        [
                            ["move_type", "=", "out_invoice"],
                            ["state", "=", "posted"],
                            ["payment_state", "not in", ["paid", "reversed"]],
                            ["invoice_origin", "ilike", so_limpia],
                        ]
                    ],
                    {
                        "fields": ["id", "name", "invoice_origin", "payment_state"],
                        "order": "id desc",
                        "limit": 10,
                    },
                )
                for row in candidatas or []:
                    orig = str(row.get("invoice_origin") or "")
                    tokens = {
                        t.strip().upper()
                        for t in orig.replace(";", ",").split(",")
                        if t.strip()
                    }
                    if so_limpia.upper() in tokens or orig.strip().upper() == so_limpia.upper():
                        return int(row["id"])
            if order_id is not None:
                ords = self._call(
                    "sale.order",
                    "read",
                    [[int(order_id)]],
                    {"fields": ["id", "invoice_ids"]},
                )
                inv_ids = [int(i) for i in ((ords or [{}])[0].get("invoice_ids") or [])]
                if inv_ids:
                    invs = self._call(
                        "account.move",
                        "search_read",
                        [
                            [
                                ["id", "in", inv_ids],
                                ["move_type", "=", "out_invoice"],
                                ["state", "=", "posted"],
                                ["payment_state", "not in", ["paid", "reversed"]],
                            ]
                        ],
                        {"fields": ["id"], "order": "id desc", "limit": 1},
                    )
                    if invs:
                        return int(invs[0]["id"])
        except Exception as e:
            logger.info(
                "No se pudo consultar factura abierta de %s en Odoo: %s",
                so_limpia or order_id,
                e,
            )
        return None

    def reconciliar_pago_con_documento(
        self,
        *,
        pago_model: str,
        pago_res_id: int,
        factura_move_id: int,
        es_cliente: bool,
    ) -> dict[str, Any]:
        """Intenta conciliar un pago o asiento publicado contra una factura publicada en Odoo.

        Nunca publica borradores automáticamente ni lanza error si no es elegible en Odoo;
        devuelve dict estructurado ``{reconciliado, pago_move_id, factura_move_id, motivo}``.
        """
        pago_move_id: int | None = None
        pago_partner_id: int | None = None
        try:
            if pago_model == "account.payment":
                pags = self._call(
                    "account.payment",
                    "read",
                    [[int(pago_res_id)]],
                    {"fields": ["id", "name", "state", "move_id", "partner_id"]},
                )
                if not pags:
                    return {
                        "reconciliado": False,
                        "pago_move_id": None,
                        "factura_move_id": int(factura_move_id),
                        "motivo": f"Pago #{pago_res_id} no encontrado en Odoo.",
                    }
                pag = pags[0]
                pago_move_id = self._m2o_id_val(pag.get("move_id"))
                pago_partner_id = self._m2o_id_val(pag.get("partner_id"))
                est_p = str(pag.get("state") or "").lower()
                if pago_move_id is None:
                    return {
                        "reconciliado": False,
                        "pago_move_id": None,
                        "factura_move_id": int(factura_move_id),
                        "motivo": f"Pago #{pago_res_id} sin asiento contable asociado en Odoo.",
                    }
                pm_rows = self._call(
                    "account.move",
                    "read",
                    [[pago_move_id]],
                    {"fields": ["id", "state", "name", "partner_id"]},
                )
                if pm_rows and pago_partner_id is None:
                    pago_partner_id = self._m2o_id_val(pm_rows[0].get("partner_id"))
                pm_state = str((pm_rows or [{}])[0].get("state") or "").lower()
                if pm_state != "posted" and est_p not in ("posted", "paid", "in_process"):
                    est_lbl = pm_state or est_p
                    return {
                        "reconciliado": False,
                        "pago_move_id": pago_move_id,
                        "factura_move_id": int(factura_move_id),
                        "motivo": (
                            f"El pago en Odoo está en estado '{est_lbl}' (vinculado en CxC)."
                        ),
                    }
            else:
                pago_move_id = int(pago_res_id)
                pm_rows = self._call(
                    "account.move",
                    "read",
                    [[pago_move_id]],
                    {"fields": ["id", "state", "name", "partner_id"]},
                )
                if not pm_rows:
                    return {
                        "reconciliado": False,
                        "pago_move_id": pago_move_id,
                        "factura_move_id": int(factura_move_id),
                        "motivo": f"Asiento #{pago_move_id} no encontrado en Odoo.",
                    }
                pago_partner_id = self._m2o_id_val(pm_rows[0].get("partner_id"))
                pm_state = str(pm_rows[0].get("state") or "").lower()
                if pm_state != "posted":
                    return {
                        "reconciliado": False,
                        "pago_move_id": pago_move_id,
                        "factura_move_id": int(factura_move_id),
                        "motivo": (
                            f"El asiento del pago está en '{pm_state}' en Odoo "
                            "(vinculado en CxC)."
                        ),
                    }

            inv_rows = self._call(
                "account.move",
                "read",
                [[int(factura_move_id)]],
                {"fields": ["id", "name", "state", "payment_state", "move_type", "partner_id"]},
            )
            if not inv_rows:
                return {
                    "reconciliado": False,
                    "pago_move_id": pago_move_id,
                    "factura_move_id": int(factura_move_id),
                    "motivo": f"Factura #{factura_move_id} no encontrada en Odoo.",
                }
            inv = inv_rows[0]
            inv_partner_id = self._m2o_id_val(inv.get("partner_id"))
            if (
                pago_partner_id is not None
                and inv_partner_id is not None
                and pago_partner_id != inv_partner_id
            ):
                return {
                    "reconciliado": False,
                    "pago_move_id": pago_move_id,
                    "factura_move_id": int(factura_move_id),
                    "motivo": (
                        "El pago y la factura pertenecen a terceros distintos en Odoo "
                        f"(partner {pago_partner_id} vs {inv_partner_id})."
                    ),
                }
            if str(inv.get("state") or "").lower() != "posted":
                return {
                    "reconciliado": False,
                    "pago_move_id": pago_move_id,
                    "factura_move_id": int(factura_move_id),
                    "motivo": (
                        f"La factura en Odoo está en '{inv.get('state')}' (vinculado en CxC)."
                    ),
                }
            if str(inv.get("payment_state") or "").lower() == "paid":
                return {
                    "reconciliado": False,
                    "pago_move_id": pago_move_id,
                    "factura_move_id": int(factura_move_id),
                    "motivo": "La factura en Odoo ya aparece pagada (vinculado en CxC).",
                }

            acct_type = "asset_receivable" if es_cliente else "liability_payable"
            pago_lines = self._call(
                "account.move.line",
                "search_read",
                [
                    [
                        ["move_id", "=", pago_move_id],
                        ["reconciled", "=", False],
                        ["account_type", "=", acct_type],
                    ]
                ],
                {"fields": ["id", "balance", "amount_residual"], "limit": 10},
            )
            candidatas_pago = [
                ln
                for ln in (pago_lines or [])
                if (
                    float(ln.get("balance") or 0) < 0
                    if es_cliente
                    else float(ln.get("balance") or 0) > 0
                )
            ] or list(pago_lines or [])
            if not candidatas_pago:
                return {
                    "reconciliado": False,
                    "pago_move_id": pago_move_id,
                    "factura_move_id": int(factura_move_id),
                    "motivo": (
                        "El pago no tiene línea abierta por conciliar en Odoo (vinculado en CxC)."
                    ),
                }
            pago_line_id = int(candidatas_pago[0]["id"])
            inv_lbl = inv.get("name") or f"#{factura_move_id}"
            try:
                self._call(
                    "account.move",
                    "js_assign_outstanding_line",
                    [int(factura_move_id), pago_line_id],
                    {},
                )
                return {
                    "reconciliado": True,
                    "pago_move_id": pago_move_id,
                    "factura_move_id": int(factura_move_id),
                    "motivo": f"Reconciliado en Odoo con {inv_lbl}.",
                }
            except OdooEscrituraError as exc_assign:
                inv_lines = self._call(
                    "account.move.line",
                    "search_read",
                    [
                        [
                            ["move_id", "=", int(factura_move_id)],
                            ["reconciled", "=", False],
                            ["account_type", "=", acct_type],
                        ]
                    ],
                    {"fields": ["id", "balance", "amount_residual"], "limit": 10},
                )
                candidatas_inv = [
                    ln
                    for ln in (inv_lines or [])
                    if (
                        float(ln.get("balance") or 0) > 0
                        if es_cliente
                        else float(ln.get("balance") or 0) < 0
                    )
                ] or list(inv_lines or [])
                if not candidatas_inv:
                    return {
                        "reconciliado": False,
                        "pago_move_id": pago_move_id,
                        "factura_move_id": int(factura_move_id),
                        "motivo": (
                            f"Vinculado en CxC (Odoo no concilió automáticamente: {exc_assign})."
                        ),
                    }
                self._call(
                    "account.move.line",
                    "reconcile",
                    [[pago_line_id, int(candidatas_inv[0]["id"])]],
                    {},
                )
                return {
                    "reconciliado": True,
                    "pago_move_id": pago_move_id,
                    "factura_move_id": int(factura_move_id),
                    "motivo": f"Reconciliado en Odoo con {inv_lbl}.",
                }
        except Exception as exc:
            logger.warning(
                "Error no bloqueante al reconciliar en Odoo (pago=%s:%s, factura=%s): %s",
                pago_model,
                pago_res_id,
                factura_move_id,
                exc,
            )
            return {
                "reconciliado": False,
                "pago_move_id": pago_move_id,
                "factura_move_id": int(factura_move_id),
                "motivo": f"Vinculado en CxC (Odoo sin conciliar: {exc}).",
            }
