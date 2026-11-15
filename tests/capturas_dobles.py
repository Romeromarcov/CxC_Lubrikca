"""Dobles de prueba de las capturas: repositorio en memoria, espejo falso y un Odoo falso.

El Odoo falso implementa ``execute(model, method, args, kwargs)`` (la misma firma que usa
``EscritorOdoo``), así que los tests ejercen el escritor y el servicio de verdad, sin red.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from cxc.capturas.modelos import ERROR, PENDIENTE, Captura

UBICACION = {"latitud": 10.4806, "longitud": -66.9036, "precision_m": 12.5}


def contacto_ok(**kw: Any) -> dict[str, Any]:
    """Contacto válido según Omni: nombre, teléfono y ubicación obligatorios."""
    d: dict[str, Any] = {
        "nombre": "Pedro",
        "telefono": "0414-1234567",
        "ubicacion": dict(UBICACION),
    }
    d.update(kw)
    return d


def pedido_ok(**kw: Any) -> dict[str, Any]:
    """Pedido válido según Omni: cliente, líneas, RIF, correo, direcciones, ubicación."""
    d: dict[str, Any] = {
        "cliente": "Taller",
        "rif": "J-123456789",
        "email": "t@x.com",
        "direccion_fiscal": "Av 1",
        "entrega_igual_fiscal": True,
        "ubicacion": dict(UBICACION),
        "lineas": [{"producto": "AC-20W50", "cantidad": "2"}],
    }
    d.update(kw)
    return d


class RepoMemoria:
    """Misma interfaz que ``RepoCapturasSQL`` (ver el Protocol ``RepoCapturas``)."""

    def __init__(self) -> None:
        self.filas: dict[int, Captura] = {}
        self.bitacora: list[tuple[int, str, str, str]] = []
        self._sig = 1

    def crear(
        self,
        *,
        kind: str,
        idempotency_key: str,
        payload: dict[str, Any],
        fuente: dict[str, Any],
        auto: bool,
        estado: str,
        incompleto: list[str],
    ) -> tuple[Captura, bool]:
        for c in self.filas.values():
            if c.idempotency_key == idempotency_key:
                return c, False
        ahora = datetime.now()
        c = Captura(
            captura_id=self._sig,
            kind=kind,
            idempotency_key=idempotency_key,
            estado=estado,
            payload=payload,
            fuente=fuente,
            auto=auto,
            incompleto=incompleto,
            resultado={},
            odoo_ref="",
            error="",
            intentos=0,
            creada_en=ahora,
            actualizada_en=ahora,
        )
        self.filas[self._sig] = c
        self.bitacora.append((self._sig, "recibida", f"{kind} · {estado}", "bot"))
        self._sig += 1
        return c, True

    def obtener(self, captura_id: int) -> Captura | None:
        return self.filas.get(captura_id)

    def obtener_por_clave(self, idempotency_key: str) -> Captura | None:
        return next((c for c in self.filas.values() if c.idempotency_key == idempotency_key), None)

    def facturas_con_numero(self, numero_factura: str) -> list[Captura]:
        return [
            c
            for c in self.filas.values()
            if c.kind == "factura_proveedor"
            and str(c.payload.get("numero_factura", "")).lower() == numero_factura.lower()
        ]

    def listar(
        self, *, estado: str | None, kind: str | None, limit: int, offset: int
    ) -> tuple[list[Captura], int]:
        todas = [
            c
            for c in sorted(self.filas.values(), key=lambda x: -x.captura_id)
            if (not estado or c.estado == estado) and (not kind or c.kind == kind)
        ]
        return todas[offset : offset + limit], len(todas)

    def actualizar(
        self,
        captura_id: int,
        *,
        estado: str | None = None,
        payload: dict[str, Any] | None = None,
        incompleto: list[str] | None = None,
        resultado: dict[str, Any] | None = None,
        odoo_ref: str | None = None,
        error: str | None = None,
        sumar_intento: bool = False,
    ) -> Captura:
        c = self.filas[captura_id]
        nuevo = Captura(
            captura_id=c.captura_id,
            kind=c.kind,
            idempotency_key=c.idempotency_key,
            estado=estado if estado is not None else c.estado,
            payload=payload if payload is not None else c.payload,
            fuente=c.fuente,
            auto=c.auto,
            incompleto=incompleto if incompleto is not None else c.incompleto,
            resultado=resultado if resultado is not None else c.resultado,
            odoo_ref=odoo_ref if odoo_ref is not None else c.odoo_ref,
            error=error if error is not None else c.error,
            intentos=c.intentos + (1 if sumar_intento else 0),
            creada_en=c.creada_en,
            actualizada_en=datetime.now(),
        )
        self.filas[captura_id] = nuevo
        return nuevo

    def evento(
        self, captura_id: int, evento: str, detalle: str = "", actor: str = "sistema"
    ) -> None:
        self.bitacora.append((captura_id, evento, detalle, actor))

    def eventos(self, captura_id: int) -> list[dict[str, Any]]:
        return [
            {"evento": e, "detalle": d, "actor": a, "en": datetime.now().isoformat()}
            for i, e, d, a in self.bitacora
            if i == captura_id
        ]

    def reintentables(self, *, max_intentos: int, limite: int = 20) -> list[Captura]:
        out = [
            c
            for c in self.filas.values()
            if c.estado in (ERROR, PENDIENTE)
            and c.intentos < max_intentos
            and (c.estado == PENDIENTE or c.resultado.get("transitorio") is True)
        ]
        return out[:limite]

    def eventos_de(self, captura_id: int) -> list[str]:
        return [e for i, e, _d, _a in self.bitacora if i == captura_id]


class EspejoFalso:
    def __init__(
        self,
        clientes: dict[str, list[str]] | None = None,
        ordenes: dict[str, str] | None = None,
        productos: dict[str, list[str]] | None = None,
    ) -> None:
        self.clientes = {k.lower(): v for k, v in (clientes or {}).items()}
        self.ordenes = {k.lower(): v for k, v in (ordenes or {}).items()}
        self.prods = {k.lower(): v for k, v in (productos or {}).items()}

    def clientes_por_nombre(self, nombre: str) -> list[str]:
        return self.clientes.get(nombre.strip().lower(), [])

    def cliente_de_orden(self, so_id: str) -> str | None:
        return self.ordenes.get(so_id.strip().lower())

    def productos(self, texto: str) -> list[str]:
        return self.prods.get(texto.strip().lower(), [])


class OdooFalso:
    """``execute`` falso con respuestas por defecto razonables y registro de lo creado."""

    def __init__(self, **respuestas: Any) -> None:
        self.llamadas: list[tuple[str, str, list[Any], dict[str, Any]]] = []
        self.creados: list[tuple[str, dict[str, Any]]] = []
        self._ids: dict[str, int] = {}
        self.r: dict[str, Any] = {
            "res.partner:fields_get": {"name": {}, "partner_latitude": {}, "partner_longitude": {}},
            "account.payment:fields_get": {"memo": {}, "amount": {}},
            "account.move:fields_get": dict.fromkeys(
                [
                    "move_type",
                    "partner_id",
                    "ref",
                    "invoice_date",
                    "invoice_date_due",
                    "currency_id",
                    "invoice_line_ids",
                    "narration",
                ],
                {},
            ),
            "account.move.line:fields_get": {"tax_ids": {}, "name": {}},
            "res.currency:search_read": [{"id": 1, "name": "USD"}, {"id": 2, "name": "VES"}],
            "res.users:search": [7],
            "sale.order:read": [{"name": "S00700"}],
        }
        # ``res_partner__search=...`` → clave ``res.partner:search``
        for clave, valor in respuestas.items():
            modelo, metodo = clave.split("__", 1)
            self.r[f"{modelo.replace('_', '.')}:{metodo}"] = valor

    def __call__(self, model: str, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        self.llamadas.append((model, method, args, kwargs))
        v = self.r.get(f"{model}:{method}")
        if isinstance(v, Exception):
            raise v
        if callable(v):
            return v(args, kwargs)
        if v is not None:
            return v
        if method == "create":
            self.creados.append((model, args[0]))
            self._ids[model] = self._ids.get(model, 100) + 1
            return self._ids[model]
        if method == "search":
            return []
        raise AssertionError(f"Odoo falso: sin respuesta para {model}.{method}")

    def creados_de(self, modelo: str) -> list[dict[str, Any]]:
        return [v for m, v in self.creados if m == modelo]

    def llamadas_a(self, modelo: str, metodo: str) -> list[list[Any]]:
        return [a for m, me, a, _k in self.llamadas if m == modelo and me == metodo]
