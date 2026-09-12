"""Escrituras en el Odoo de prueba, con las barreras que descubrió el sondeo.

Este módulo es el único lugar del banco de escenarios que ESCRIBE en Odoo.
Concentra seis cosas que no son obvias y que cuestan caro descubrir a mano;
cada una está comentada donde se aplica:

1. **La imprenta digital de este Odoo apunta al proveedor REAL**
   (``thefactoryhka.com.ve``) y emitió documentos fiscales aprobados el
   09-sep-2026, con números de control y URL pública. El único diario de
   ventas de la base, ``INV``, tiene ese conector. Por eso todas las
   facturas del banco van por un diario aparte, ``ZZPRU``, creado sin
   conector, y por eso existe el canario de ``ControlFiscal``: si un
   escenario emite un documento fiscal, la corrida FALLA en vez de seguir.

2. **Un cliente con RIF ``J-`` queda marcado como agente de retención**, y
   eso dispara el comprobante de retención de IVA, que en este Odoo se
   rompe con un error de SQL (``COALESCE types character varying and jsonb
   cannot be matched``). Los clientes de prueba se crean con
   ``wh_iva_agent=False``.

3. **La entrega es de tres pasos** (PICK → PACK → OUT), así que
   ``delivery_status`` no llega a ``full`` hasta validar los tres. El paso
   OUT además exige un vehículo de flota.

4. **Cancelar una orden abre un asistente que puede MANDAR CORREO** al
   cliente. Se usa ``action_cancel`` del asistente, nunca
   ``action_send_mail``.

5. **Una orden confirmada queda bloqueada**; hay que escribir
   ``locked=False`` antes de cancelarla (``action_unlock`` no es invocable
   por XML-RPC en esta versión).

6. **``_create_invoices`` es privado** y no se puede llamar por XML-RPC. La
   vía pública es el asistente ``sale.advance.payment.inv``.

Todo lo que crea lleva el prefijo ``ZZ BLINDAJE`` para poder encontrarlo y
distinguirlo de los datos copiados de producción.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

PREFIJO = "ZZ BLINDAJE"

# Diario de ventas del banco de escenarios. Se crea si no existe, SIEMPRE sin
# conector de imprenta digital.
# La fecha con la que se postean las facturas del banco. Tiene que ser la MISMA
# para ``invoice_date`` y ``date``: ver el comentario en ``facturar``.
FECHA_CONTABLE = "2026-09-01"

CODIGO_DIARIO_PRUEBAS = "ZZPRU"
NOMBRE_DIARIO_PRUEBAS = "ZZ PRUEBAS BLINDAJE (sin imprenta digital)"

# Ubicacion de cliente con la imprenta digital apagada. Es la barrera
# equivalente al diario, del lado de las entregas -- ver ``ubicacion_pruebas``.
NOMBRE_UBICACION_PRUEBAS = "ZZ PRUEBAS BLINDAJE clientes"

# El modelo que lleva los números de control fiscal. Es el canario: si crece,
# se emitió un documento fiscal real y hay que abortar.
MODELO_CONTROL_FISCAL = "account.digital.ctrl.number"

# IVA 16%, y la cuenta de ingresos que usan las facturas reales de la base.
IMPUESTO_IVA_16 = 5
CUENTA_INGRESOS = 220


class EmisionFiscalDetectada(RuntimeError):
    """Se emitió un documento fiscal. Nunca debería pasar."""


class NoEsEntornoDePrueba(RuntimeError):
    """El Odoo configurado no parece un entorno de prueba."""


@dataclass(frozen=True)
class OrdenCreada:
    """Lo que un escenario necesita saber de la orden que acaba de armar."""

    id: int
    nombre: str
    cliente_id: int
    lineas: list[int]

    def __str__(self) -> str:
        return f"{self.nombre} (id {self.id})"


class OdooQA:
    """Cliente de escritura contra el Odoo de prueba."""

    def __init__(self, ejecutar: Any, url: str) -> None:
        if ".dev.odoo.com" not in url:
            raise NoEsEntornoDePrueba(
                f"ODOO_URL es {url!r}. El banco de escenarios ESCRIBE en Odoo y solo "
                "corre contra un entorno de prueba (.dev.odoo.com)."
            )
        self._ex = ejecutar
        self.url = url
        self._diario_pruebas: int | None = None
        self._ubicacion: int | None = None
        self._vehiculo: int | None = None

    # --- infraestructura -------------------------------------------------

    def ex(self, modelo: str, metodo: str, args: list[Any], kwargs: dict[str, Any] | None = None):
        return self._ex(modelo, metodo, args, kwargs or {})

    def control_fiscal_emitidos(self) -> int:
        """El canario. Se lee antes y después de cada escenario."""
        return int(self.ex(MODELO_CONTROL_FISCAL, "search_count", [[]]))

    @property
    def diario_pruebas(self) -> int:
        """El diario de ventas sin imprenta digital, creado al vuelo.

        Se verifica el conector en cada acceso y no se cachea el resultado de
        esa verificación: si alguien le conecta la imprenta al diario de
        pruebas, la próxima factura tiene que fallar acá, no en el proveedor.
        """
        if self._diario_pruebas is None:
            filas = self.ex(
                "account.journal",
                "search_read",
                [[["code", "=", CODIGO_DIARIO_PRUEBAS]]],
                {"fields": ["id"]},
            )
            if filas:
                self._diario_pruebas = int(filas[0]["id"])
            else:
                self._diario_pruebas = int(
                    self.ex(
                        "account.journal",
                        "create",
                        [
                            {
                                "name": NOMBRE_DIARIO_PRUEBAS,
                                "code": CODIGO_DIARIO_PRUEBAS,
                                "type": "sale",
                                "invoicing_digital_conn": False,
                                # Un diario de venta nuevo NO nace neutro: el
                                # default de ``billing_type`` en esta base es
                                # ``fiscal_printer``, o sea otra via fiscal
                                # distinta del conector digital. Se fuerza a
                                # ``free_form`` (impresion libre), que es la
                                # unica de las tres opciones sin dispositivo
                                # fiscal detras. Verificar solo el conector
                                # dejaba esta puerta abierta.
                                "billing_type": "free_form",
                            }
                        ],
                    )
                )
        fila = self.ex(
            "account.journal",
            "read",
            [[self._diario_pruebas]],
            {"fields": ["invoicing_digital_conn", "billing_type"]},
        )[0]
        if fila["invoicing_digital_conn"]:
            raise EmisionFiscalDetectada(
                f"El diario de pruebas {CODIGO_DIARIO_PRUEBAS} tiene conector de "
                f"imprenta digital ({fila['invoicing_digital_conn']}). Facturar por "
                "ahí emitiría documentos fiscales reales. Abortado."
            )
        if fila["billing_type"] != "free_form":
            # Se corrige en vez de abortar: el diario puede haberse creado antes
            # de que esta comprobación existiera, y dejarlo en fiscal_printer es
            # justamente lo que hay que evitar.
            self.ex(
                "account.journal",
                "write",
                [[self._diario_pruebas], {"billing_type": "free_form"}],
            )
        return self._diario_pruebas

    @property
    def ubicacion_pruebas(self) -> int:
        """Ubicación de cliente con la imprenta digital APAGADA.

        Es la barrera equivalente al diario de pruebas, del lado de las
        entregas. La nota de entrega también se emite fiscalmente
        (``account.digital.ctrl.number`` tiene filas con ``picking_id``), y lo
        que decide si se emite **no** es el campo del picking -- ése es
        calculado y de solo lectura, y sigue leyendo ``True`` -- sino
        ``is_digital_invoicing`` de la ubicación de DESTINO, que sí está
        almacenado.

        La ubicación estándar ``Socios/Clientes`` la tiene encendida. Se crea
        una hermana con la bandera apagada y todas las entregas del banco van
        ahí. Verificado en vivo: con el destino cambiado, el OUT valida,
        ``delivery_status`` llega a ``full`` y no se emite ningún documento.
        """
        if self._ubicacion is None:
            filas = self.ex(
                "stock.location",
                "search_read",
                [[["name", "=", NOMBRE_UBICACION_PRUEBAS]]],
                {"fields": ["id"]},
            )
            if filas:
                self._ubicacion = int(filas[0]["id"])
            else:
                estandar = self.ex(
                    "stock.location",
                    "search_read",
                    [[["usage", "=", "customer"], ["is_digital_invoicing", "=", True]]],
                    {"fields": ["location_id"], "limit": 1},
                )
                padre = estandar[0]["location_id"] if estandar else False
                self._ubicacion = int(
                    self.ex(
                        "stock.location",
                        "create",
                        [
                            {
                                "name": NOMBRE_UBICACION_PRUEBAS,
                                "usage": "customer",
                                "location_id": padre[0] if padre else False,
                                "is_digital_invoicing": False,
                            }
                        ],
                    )
                )
        bandera = self.ex(
            "stock.location",
            "read",
            [[self._ubicacion]],
            {"fields": ["is_digital_invoicing"]},
        )[0]["is_digital_invoicing"]
        if bandera:
            raise EmisionFiscalDetectada(
                f"La ubicación de pruebas {NOMBRE_UBICACION_PRUEBAS!r} tiene la "
                "imprenta digital encendida. Entregar ahí emitiría notas de entrega "
                "fiscales reales. Abortado."
            )
        return self._ubicacion

    @property
    def vehiculo(self) -> int:
        """Un vehículo de flota CON conductor asignado.

        El paso OUT de la entrega exige vehículo, y además exige que ese
        vehículo tenga conductor -- el mensaje de Odoo es "You must select a
        driver" aunque el campo no esté en el picking sino en el vehículo. De
        los cuatro vehículos de esta base, uno no tiene conductor; tomar "el
        primero" fallaba justo con ése.
        """
        if self._vehiculo is None:
            filas = self.ex(
                "fleet.vehicle",
                "search_read",
                [[["driver_id", "!=", False]]],
                {"fields": ["id"], "limit": 1},
            )
            if not filas:
                raise RuntimeError(
                    "No hay ningún vehículo de flota con conductor asignado; el paso "
                    "OUT de la entrega no se puede validar."
                )
            self._vehiculo = int(filas[0]["id"])
        return self._vehiculo

    def diario_banco(self, moneda: str = "VES") -> int:
        """Diario de pago en la moneda pedida."""
        codigo = {"VES": "BDV", "USD": "USD1"}.get(moneda.upper(), "BDV")
        filas = self.ex(
            "account.journal", "search_read", [[["code", "=", codigo]]], {"fields": ["id"]}
        )
        if not filas:
            raise RuntimeError(f"No existe el diario de pago {codigo}.")
        return int(filas[0]["id"])

    # --- catálogo --------------------------------------------------------

    def producto_con_precio(self, pricelist_id: int, salteando: int = 0) -> int:
        """Un producto con precio fijo en esa lista y SIN trazabilidad por lote.

        Dos condiciones, cada una por su motivo:

        * **con regla de precio fijo en la lista pedida** -- un producto sin
          regla cae al fallback de ficha, y entonces el escenario mediría el
          fallback en vez de lo que quiere medir;
        * **sin lote ni serie** -- de los 249 productos vendibles de esta base,
          176 son ``tracking='lot'`` y validar su entrega exige dar un número
          de lote. Eso no aporta nada a la cuenta por cobrar y sí impide armar
          el escenario. Quedan 8 productos que cumplen las dos condiciones en
          la lista vigente, de sobra para el banco.

        ``salteando`` sirve para pedir productos distintos en un mismo
        escenario.
        """
        items = self.ex(
            "product.pricelist.item",
            "search_read",
            [[["pricelist_id", "=", pricelist_id], ["compute_price", "=", "fixed"]]],
            {"fields": ["product_tmpl_id"]},
        )
        plantillas = [i["product_tmpl_id"][0] for i in items if i.get("product_tmpl_id")]
        if not plantillas:
            raise RuntimeError(f"La lista {pricelist_id} no tiene reglas de precio fijo.")
        candidatos = self.ex(
            "product.product",
            "search_read",
            [
                [
                    ["product_tmpl_id", "in", plantillas],
                    ["sale_ok", "=", True],
                    ["tracking", "=", "none"],
                ]
            ],
            {"fields": ["id"], "order": "id"},
        )
        if not candidatos:
            raise RuntimeError(
                f"La lista {pricelist_id} no tiene ningún producto con precio fijo y "
                "sin trazabilidad por lote."
            )
        return int(candidatos[salteando % len(candidatos)]["id"])

    # --- clientes --------------------------------------------------------

    def cliente(self, etiqueta: str) -> int:
        """Cliente de prueba, reutilizado entre corridas.

        **Sin identificación fiscal**, y eso es deliberado por dos motivos que
        se refuerzan:

        * un contacto sin RIF no puede confundirse con un contribuyente real
          ni ser sujeto de una emisión fiscal;
        * con un RIF ``J-`` Odoo lo marca automáticamente como agente de
          retención, y eso dispara el comprobante de retención de IVA, que en
          esta base se rompe con un error de SQL (``COALESCE types character
          varying and jsonb cannot be matched``) e impide postear la factura.

        Verificado en vivo: sin RIF y con ``wh_iva_agent=False`` la factura
        postea normal por el diario de pruebas. ``wh_iva_agent`` se fuerza en
        cada corrida porque Odoo lo recalcula al escribir otros campos.
        """
        nombre = f"{PREFIJO} {etiqueta}"
        filas = self.ex(
            "res.partner", "search_read", [[["name", "=", nombre]]], {"fields": ["id"]}
        )
        valores = {"company_type": "company", "wh_iva_agent": False}
        if filas:
            cid = int(filas[0]["id"])
            self.ex("res.partner", "write", [[cid], valores])
            return cid
        return int(self.ex("res.partner", "create", [{"name": nombre, **valores}]))

    # --- órdenes ---------------------------------------------------------

    def crear_orden(
        self,
        cliente_id: int,
        pricelist_id: int,
        lineas: list[tuple[int, float]],
        fecha: datetime | str = "2026-09-01 10:00:00",
    ) -> OrdenCreada:
        """Orden en borrador con las líneas pedidas: (producto_id, cantidad)."""
        if isinstance(fecha, datetime):
            fecha = fecha.strftime("%Y-%m-%d %H:%M:%S")
        so = int(
            self.ex(
                "sale.order",
                "create",
                [
                    {
                        "partner_id": cliente_id,
                        "pricelist_id": pricelist_id,
                        "date_order": fecha,
                        "order_line": [
                            (0, 0, {"product_id": p, "product_uom_qty": q}) for p, q in lineas
                        ],
                    }
                ],
            )
        )
        datos = self.ex("sale.order", "read", [[so]], {"fields": ["name", "order_line"]})[0]
        return OrdenCreada(so, datos["name"], cliente_id, list(datos["order_line"]))

    def confirmar(self, so: int) -> None:
        self.ex("sale.order", "action_confirm", [[so]])

    def entregar(self, so: int, completa: bool = True) -> list[str]:
        """Valida la cadena de pickings. Devuelve los que quedaron en ``done``.

        Tres pasos (PICK → PACK → OUT) y ``delivery_status`` no llega a
        ``full`` hasta el último. ``completa=False`` valida solo el primero,
        que es cómo se produce una entrega parcial.
        """
        validados: list[str] = []
        for _ in range(6):
            pendientes = self.ex(
                "stock.picking",
                "search_read",
                [[["sale_id", "=", so], ["state", "in", ["assigned", "confirmed"]]]],
                {"fields": ["id", "name", "picking_type_code"]},
            )
            if not pendientes:
                break
            picking = pendientes[0]
            if picking["picking_type_code"] == "outgoing":
                # Dos cosas en el paso OUT, y la segunda es la que importa.
                #
                # El paso OUT exige vehículo, y el vehículo, conductor.
                #
                # Y **la nota de entrega también se emite fiscalmente**. Lo que
                # decide si se emite es ``is_digital_invoicing`` de la
                # ubicación de DESTINO, no el campo homónimo del picking (ése
                # es calculado y sigue leyendo ``True`` incluso cuando no se
                # emite nada). Se redirige el destino a la ubicación de
                # pruebas, que tiene la bandera apagada.
                self.ex(
                    "stock.picking",
                    "write",
                    [
                        [picking["id"]],
                        {
                            "fleet_vehicle_id": self.vehiculo,
                            "location_dest_id": self.ubicacion_pruebas,
                        },
                    ],
                )
            self._forzar_cantidades(
                picking["id"],
                destino=(
                    self.ubicacion_pruebas
                    if picking["picking_type_code"] == "outgoing"
                    else None
                ),
            )
            self.ex("stock.picking", "button_validate", [[picking["id"]]])
            validados.append(picking["name"])
            if not completa:
                break
        return validados

    def _forzar_cantidades(self, picking_id: int, destino: int | None = None) -> None:
        """Registra las cantidades a mano antes de validar.

        Los productos del banco no tienen existencias en el almacén de prueba,
        así que el picking queda en ``confirmed`` (nada reservado) y Odoo se
        niega: "No puedes validar un traslado si no hay cantidades reservadas".
        Poner ``quantity`` igual a la demanda y ``picked=True`` es la vía
        equivalente a registrar la salida a mano en la pantalla, que es lo que
        hace el depósito cuando el sistema no tenía el stock cargado.
        """
        movimientos = self.ex(
            "stock.move",
            "search_read",
            [[["picking_id", "=", picking_id], ["state", "not in", ["done", "cancel"]]]],
            {"fields": ["id", "product_uom_qty", "quantity"]},
        )
        for mov in movimientos:
            valores: dict[str, Any] = {"quantity": mov["product_uom_qty"], "picked": True}
            if destino is not None:
                # El destino hay que moverlo también en el movimiento, no solo
                # en el picking: si queda apuntando a la ubicación estándar, la
                # emisión fiscal se dispara igual.
                valores["location_dest_id"] = destino
            self.ex("stock.move", "write", [[mov["id"]], valores])

    def estado_entrega(self, so: int) -> str:
        return str(
            self.ex("sale.order", "read", [[so]], {"fields": ["delivery_status"]})[0][
                "delivery_status"
            ]
            or ""
        )

    def desbloquear(self, so: int) -> None:
        """``action_unlock`` no es invocable por XML-RPC; se escribe el campo."""
        self.ex("sale.order", "write", [[so], {"locked": False}])

    def cancelar_orden(self, so: int) -> None:
        """Cancela sin mandar correo.

        ``sale.order.action_cancel`` devuelve un asistente que tiene DOS
        botones: ``action_cancel`` (solo cancela) y ``action_send_mail`` (le
        avisa al cliente). Acá se usa siempre el primero.
        """
        self.desbloquear(so)
        wizard = int(
            self.ex(
                "sale.order.cancel",
                "create",
                [{"order_id": so}],
                {"context": {"default_order_id": so}},
            )
        )
        self.ex("sale.order.cancel", "action_cancel", [[wizard]])

    def editar_linea(self, linea_id: int, valores: dict[str, Any]) -> None:
        self.ex("sale.order.line", "write", [[linea_id], valores])

    def agregar_linea(self, so: int, producto_id: int, cantidad: float) -> int:
        return int(
            self.ex(
                "sale.order.line",
                "create",
                [{"order_id": so, "product_id": producto_id, "product_uom_qty": cantidad}],
            )
        )

    def borrar_linea(self, linea_id: int) -> None:
        """Saca un producto de la orden.

        En una orden CONFIRMADA, Odoo no deja borrar la linea: contesta "una vez
        que confirmas una orden de venta, no puedes eliminar ninguna de sus
        lineas (son necesarias para determinar si algo se factura o se entrega).
        Establece la cantidad en 0."

        Eso no es un obstaculo del banco, es el hallazgo: poner la cantidad en
        cero es como se saca un producto de una orden confirmada en la vida
        real, y es exactamente lo que produce las dos lineas con
        ``cantidad_entregada`` NEGATIVA que aparecieron en los datos (S00925 con
        -10 unidades, S00952 con -4). Asi que el escenario hace lo que hace el
        humano, y cae al borrado solo si la orden todavia esta en borrador.
        """
        try:
            self.ex("sale.order.line", "write", [[linea_id], {"product_uom_qty": 0}])
        except Exception:  # noqa: BLE001 -- en borrador si se puede borrar
            self.ex("sale.order.line", "unlink", [[linea_id]])

    def editar_orden(self, so: int, valores: dict[str, Any]) -> None:
        self.ex("sale.order", "write", [[so], valores])

    # --- facturas --------------------------------------------------------

    def facturar(self, so: int) -> list[int]:
        """Factura la orden por el diario de pruebas y la postea.

        ``_create_invoices`` es privado y no se puede llamar por XML-RPC; la
        vía pública es el asistente. El asistente usa el diario por defecto
        del tipo, así que la factura se mueve a ``ZZPRU`` mientras todavía
        está en borrador -- antes de postear es cuando se puede.
        """
        contexto = {"active_model": "sale.order", "active_ids": [so], "active_id": so}
        wizard = int(
            self.ex(
                "sale.advance.payment.inv",
                "create",
                [{"advance_payment_method": "delivered", "sale_order_ids": [(6, 0, [so])]}],
                {"context": contexto},
            )
        )
        self.ex("sale.advance.payment.inv", "create_invoices", [[wizard]], {"context": contexto})

        nombre = self.ex("sale.order", "read", [[so]], {"fields": ["name"]})[0]["name"]
        borradores = self.ex(
            "account.move",
            "search_read",
            [[["invoice_origin", "=", nombre], ["state", "=", "draft"]]],
            {"fields": ["id"]},
        )
        ids = [int(f["id"]) for f in borradores]
        if ids:
            # Las DOS fechas van juntas en la misma escritura, y no es cosmetico.
            #
            # Bug real, encontrado corriendo el banco completo: al escribir solo
            # ``journal_id``, Odoo recomputa y ``account_dual_currency``
            # (``_compute_date``) pisa ``invoice_date`` con ``datetime.now()``.
            # Entonces ``l10n_ve_full.write`` rechaza el asiento con "La fecha
            # contable no puede ser menor a la fecha de la factura", porque la
            # contable habia quedado en la fecha de la orden y la de factura paso
            # a ser hoy. Fallaba ``facturar()`` y con el los 20 escenarios que
            # necesitan una factura -- todo el archivo de pagos, el de
            # devoluciones y el de facturacion.
            #
            # Fijando las dos a la misma fecha, el recomputo no tiene nada que
            # pisar y la validacion se cumple por construccion.
            self.ex(
                "account.move",
                "write",
                [
                    ids,
                    {
                        "journal_id": self.diario_pruebas,
                        "invoice_date": FECHA_CONTABLE,
                        "date": FECHA_CONTABLE,
                    },
                ],
            )
            self.ex("account.move", "action_post", [ids])
        return ids

    def factura_directa(
        self,
        cliente_id: int,
        lineas: list[tuple[int, float, float]],
        fecha: date | str = "2026-09-01",
        move_type: str = "out_invoice",
        origen: str | None = None,
    ) -> int:
        """Factura suelta: (producto, cantidad, precio). Sin orden detrás.

        Sirve para los escenarios de nota de crédito sin factura de origen y
        de factura sin orden asociada.
        """
        if isinstance(fecha, date):
            fecha = fecha.isoformat()
        valores: dict[str, Any] = {
            "move_type": move_type,
            "partner_id": cliente_id,
            "journal_id": self.diario_pruebas,
            "invoice_date": fecha,
            "invoice_line_ids": [
                (
                    0,
                    0,
                    {
                        "product_id": p,
                        "quantity": q,
                        "price_unit": precio,
                        "tax_ids": [(6, 0, [IMPUESTO_IVA_16])],
                        "account_id": CUENTA_INGRESOS,
                    },
                )
                for p, q, precio in lineas
            ],
        }
        if origen:
            valores["invoice_origin"] = origen
        fid = int(self.ex("account.move", "create", [valores]))
        self.ex("account.move", "action_post", [[fid]])
        return fid

    def nota_credito(
        self,
        factura_id: int,
        fecha: date | str = "2026-09-05",
        motivo: str = f"{PREFIJO} NC",
        factor: float = 1.0,
    ) -> list[int]:
        """Nota de crédito contra una factura, construida a mano.

        **No se usa ``account.move.reversal``** a propósito. El asistente de
        reverso exige un ``journal_id`` que la localización valida aparte -- «the
        journal must be of the credit note type» -- y el único diario que
        acepta es el real, que tiene la imprenta digital conectada. Pasar por
        ahí emitiría un documento fiscal, que es exactamente lo que el banco no
        puede hacer.

        Construir el asiento directamente con ``move_type = out_refund`` y
        ``reversed_entry_id`` apuntando a la factura da el mismo resultado para
        lo que los escenarios miden -- el espejo lee esos dos campos -- sin
        tocar el asistente ni su diario.

        ``factor`` multiplica las cantidades: con 2.0 sale una NC del doble de
        la factura, que es el escenario «nota de crédito por más que la
        factura».
        """
        if isinstance(fecha, date):
            fecha = fecha.isoformat()
        lineas = self.ex(
            "account.move.line",
            "search_read",
            [[["move_id", "=", factura_id], ["display_type", "in", ["product", False]]]],
            {"fields": ["product_id", "quantity", "price_unit", "name"]},
        )
        if not lineas:
            raise RuntimeError(f"La factura {factura_id} no tiene líneas de producto.")
        cliente = self._cliente_de(factura_id)
        nc = int(
            self.ex(
                "account.move",
                "create",
                [
                    {
                        "move_type": "out_refund",
                        "partner_id": cliente,
                        "journal_id": self.diario_pruebas,
                        "invoice_date": fecha,
                        "ref": motivo,
                        "reversed_entry_id": factura_id,
                        "invoice_line_ids": [
                            (
                                0,
                                0,
                                {
                                    "product_id": (
                                        ln["product_id"][0] if ln.get("product_id") else False
                                    ),
                                    "name": ln.get("name") or motivo,
                                    "quantity": float(ln["quantity"]) * factor,
                                    "price_unit": float(ln["price_unit"]),
                                    "tax_ids": [(6, 0, [IMPUESTO_IVA_16])],
                                    "account_id": CUENTA_INGRESOS,
                                },
                            )
                            for ln in lineas
                        ],
                    }
                ],
            )
        )
        self.ex("account.move", "action_post", [[nc]])
        return [nc]

    def anular_factura(self, factura_id: int) -> None:
        """Vuelve la factura a borrador y la cancela."""
        self.ex("account.move", "button_draft", [[factura_id]])
        self.ex("account.move", "write", [[factura_id], {"state": "cancel"}])

    def editar_factura(self, factura_id: int, valores: dict[str, Any]) -> None:
        self.ex("account.move", "write", [[factura_id], valores])

    # --- pagos -----------------------------------------------------------

    def pagar(
        self,
        factura_ids: list[int],
        monto: float | None,
        fecha: date | str = "2026-09-05",
        moneda: str = "VES",
        proporcion: float = 1.0,
    ) -> int:
        """Registra un cobro contra las facturas dadas y lo concilia.

        ``monto`` va en la moneda del diario. Con ``None`` se deja el default del
        asistente, que es el residual de la factura convertido a esa moneda con
        la tasa que Odoo tenga -- la única forma sensata de pagar en dólares una
        factura que está en bolívares sin calcular la tasa a mano. Pasar el
        total en Bs como si fueran dólares (lo que hacía la primera versión del
        escenario de la fecha) registra un pago de medio millón de dólares.
        ``proporcion`` escala ese default: ``monto=None, proporcion=0.5`` es
        «la mitad del residual, en la moneda del diario».
        """
        if isinstance(fecha, date):
            fecha = fecha.isoformat()
        contexto = {"active_model": "account.move", "active_ids": factura_ids}
        valores: dict[str, Any] = {
            "payment_date": fecha,
            "journal_id": self.diario_banco(moneda),
        }
        if monto is not None:
            valores["amount"] = monto
        wizard = int(
            self.ex(
                "account.payment.register",
                "create",
                [valores],
                {"context": contexto},
            )
        )
        if monto is None and proporcion != 1.0:
            por_defecto = float(
                self.ex("account.payment.register", "read", [[wizard]], {"fields": ["amount"]})[0][
                    "amount"
                ]
            )
            self.ex(
                "account.payment.register",
                "write",
                [[wizard], {"amount": round(por_defecto * proporcion, 2)}],
            )
        self.ex(
            "account.payment.register",
            "action_create_payments",
            [[wizard]],
            {"context": contexto},
        )
        pagos = self.ex(
            "account.payment",
            "search_read",
            [[["partner_id", "=", self._cliente_de(factura_ids[0])]]],
            {"fields": ["id"], "order": "id desc", "limit": 1},
        )
        return int(pagos[0]["id"])

    def completar_importe_local(self, pago_id: int) -> None:
        """Deja el pago como lo deja la pantalla de Odoo, no como lo deja el RPC.

        ``amount_local`` («Importe local», el equivalente en Bs) y ``amount_ref``
        son campos guardados que llena un *onchange* de la vista: por XML-RPC
        el asistente los deja en cero (medido: 816 de 827 pagos reales en USD
        lo tienen cargado; los once en cero son los del banco y cuatro
        cancelados). Sin esto, ningún detector que compare el importe local
        contra el asiento puede ver el pago.
        """
        p = self.ex(
            "account.payment", "read", [[pago_id]], {"fields": ["amount", "tax_today"]}
        )[0]
        self.ex(
            "account.payment",
            "write",
            [
                [pago_id],
                {
                    "amount_local": round(float(p["amount"]) * float(p["tax_today"]), 2),
                    "amount_ref": float(p["amount"]),
                },
            ],
        )

    def editar_fecha_como_la_ui(self, pago_id: int, fecha: str) -> None:
        """Cambia la fecha de un pago POSTEADO como lo hace una persona en la vista.

        Es el bug real del diferencial cambiario, reproducido paso a paso: por
        RPC, ``write({"date": ...})`` sobre un pago posteado y conciliado está
        permitido y no toca nada más (medido el 12-sep-2026). Lo que corrompe
        es lo que la vista hace después: el *onchange* de la fecha propone la
        tasa del día nuevo (``tax_today``) y la pantalla recalcula
        ``amount_local`` con ella. El asiento ya posteado y conciliado se queda
        con el monto VES viejo. Dos números del mismo pago, dos tasas.

        Lo que NO reproduce: si la vista además genera un asiento de ajuste
        cambiario, eso vive en el flujo de la pantalla y no se ve por RPC.
        """
        p = self.ex(
            "account.payment",
            "read",
            [[pago_id]],
            {"fields": ["amount", "tax_today", "currency_id"]},
        )[0]
        self.ex("account.payment", "write", [[pago_id], {"date": fecha}])
        propuesto = self.ex(
            "account.payment",
            "onchange",
            [
                [pago_id],
                {
                    "date": fecha,
                    "tax_today": p["tax_today"],
                    "amount": p["amount"],
                    "currency_id": p["currency_id"][0],
                },
                ["date"],
                {"date": {}, "tax_today": {}, "amount_local": {}},
            ],
        )
        tasa_nueva = float((propuesto.get("value") or {}).get("tax_today") or p["tax_today"])
        self.ex(
            "account.payment",
            "write",
            [
                [pago_id],
                {
                    "tax_today": tasa_nueva,
                    "amount_local": round(float(p["amount"]) * tasa_nueva, 2),
                },
            ],
        )

    def _cliente_de(self, factura_id: int) -> int:
        return int(
            self.ex("account.move", "read", [[factura_id]], {"fields": ["partner_id"]})[0][
                "partner_id"
            ][0]
        )

    def editar_pago(self, pago_id: int, valores: dict[str, Any]) -> None:
        """Edita un pago, sacandolo de borrador primero.

        ``action_draft`` devuelve ``None``, y el servidor XML-RPC de Odoo no
        puede serializarlo: contesta "cannot marshal None unless allow_none is
        enabled". No es que la operacion falle -- se ejecuta y despues revienta
        al armar la respuesta. Es la misma trampa que ``action_unlock`` en las
        ordenes, y se trata igual: se deja pasar ese error puntual y se
        verifica el estado despues, que es lo unico que importa.
        """
        self._sin_respuesta("account.payment", "action_draft", pago_id)
        self.ex("account.payment", "write", [[pago_id], valores])
        self._sin_respuesta("account.payment", "action_post", pago_id)

    def _sin_respuesta(self, modelo: str, metodo: str, registro_id: int) -> None:
        """Llama a un metodo que devuelve ``None`` y tolera el fallo de
        serializacion, no el de negocio.

        La distincion importa: un "cannot marshal None" quiere decir que la
        operacion SI corrio; cualquier otro error es real y se propaga.
        """
        try:
            self.ex(modelo, metodo, [[registro_id]])
        except Exception as exc:  # noqa: BLE001 -- se filtra por el mensaje
            if "cannot marshal None" not in str(exc):
                raise

    def desconciliar_pago(self, pago_id: int) -> None:
        """Rompe la conciliación entre el pago y su factura."""
        move = self.ex("account.payment", "read", [[pago_id]], {"fields": ["move_id"]})[0][
            "move_id"
        ]
        partidas = self.ex(
            "account.move.line",
            "search_read",
            [[["move_id", "=", move[0]], ["reconciled", "=", True]]],
            {"fields": ["id"]},
        )
        if partidas:
            # ``remove_move_reconcile`` tambien devuelve ``None``: mismo
            # "cannot marshal None" que ``action_draft`` y ``action_unlock``.
            try:
                self.ex(
                    "account.move.line",
                    "remove_move_reconcile",
                    [[p["id"] for p in partidas]],
                )
            except Exception as exc:  # noqa: BLE001 -- se filtra por el mensaje
                if "cannot marshal None" not in str(exc):
                    raise

    # --- devoluciones ----------------------------------------------------

    def devolver(self, so: int, cantidad: float | None = None) -> list[str]:
        """Devolución de mercancía: revierte el picking de salida.

        Es lo que hace nacer un picking ``incoming`` con ``return_id``, que es
        lo que el sync usa para marcar ``tiene_devolucion``.
        """
        salidas = self.ex(
            "stock.picking",
            "search_read",
            [
                [
                    ["sale_id", "=", so],
                    ["picking_type_code", "=", "outgoing"],
                    ["state", "=", "done"],
                ]
            ],
            {"fields": ["id", "name"]},
        )
        if not salidas:
            raise RuntimeError(f"La orden {so} no tiene entrega hecha para devolver.")
        origen = salidas[0]["id"]
        contexto = {"active_model": "stock.picking", "active_ids": [origen], "active_id": origen}
        wizard = int(
            self.ex(
                "stock.return.picking",
                "create",
                [{"picking_id": origen}],
                {"context": contexto},
            )
        )
        # El asistente crea sus lineas con ``quantity`` en CERO, asi que hay que
        # llenarlas SIEMPRE y no solo cuando se pide una devolucion parcial:
        # sin eso Odoo contesta "Especifique al menos una cantidad diferente a
        # cero". ``move_quantity`` es lo que salio en el picking original, o sea
        # el techo de lo que se puede devolver.
        lineas = self.ex(
            "stock.return.picking.line",
            "search_read",
            [[["wizard_id", "=", wizard]]],
            {"fields": ["id", "move_quantity"]},
        )
        for linea in lineas:
            devuelta = (
                cantidad if cantidad is not None else float(linea.get("move_quantity") or 0)
            )
            self.ex(
                "stock.return.picking.line",
                "write",
                [[linea["id"]], {"quantity": devuelta}],
            )
        self.ex(
            "stock.return.picking", "action_create_returns", [[wizard]], {"context": contexto}
        )

        devoluciones = self.ex(
            "stock.picking",
            "search_read",
            [[["return_id", "=", origen]]],
            {"fields": ["id", "name", "state"]},
        )
        validadas = []
        for dev in devoluciones:
            if dev["state"] in ("assigned", "confirmed"):
                self.ex("stock.picking", "button_validate", [[dev["id"]]])
            validadas.append(dev["name"])
        return validadas


def conectar_qa() -> OdooQA:
    """``OdooQA`` con la configuración del entorno ya cargada."""
    from cxc.config import AppConfig
    from cxc.odoo.client import _connect

    config = AppConfig.from_env()
    ejecutar = _connect(config.odoo)
    if not ejecutar:
        raise RuntimeError("No se pudo conectar al Odoo de prueba.")
    return OdooQA(ejecutar, os.environ.get("ODOO_URL", ""))
