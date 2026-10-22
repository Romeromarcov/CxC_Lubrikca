# Mapeo Odoo → Postgres

Qué lee el sistema de Odoo y a qué tabla del espejo va. Fuente de verdad: el
código (`src/cxc/odoo/client.py`, `OdooXmlRpcReader`; tablas en
`src/cxc/db/schema.py`). Odoo es **Odoo 18 Enterprise** y el acceso es **solo
lectura** por XML-RPC; el sync (`src/cxc/sync/incremental.py`) pide filas con
`write_date > cursor` (ventana solapada de 48 h) y hace upsert idempotente.

## Qué se lee de cada modelo

| Modelo Odoo | Tabla del espejo | Campos / reglas relevantes |
|---|---|---|
| `res.partner` | `clientes` | `name`, `user_id` (→ `vendedor_email` = `res.users.login`), `wh_iva_agent`, `wh_iva_rate`, `commercial_partner_id` (agrupa subcontactos bajo su empresa). Se lee con `active_test=False`: una orden histórica puede referenciar un contacto archivado |
| `sale.order` | `ordenes_venta` | `name` (el `so_id`, p. ej. `S00553`), `partner_id`, `date_order`, `amount_total`, `pricelist_id`, `user_id`, `invoice_status`, `delivery_status`, `state`, `payment_term_id` (→ `dias_credito`). Derivados: `fecha_entrega` (`stock.picking.date_done` del despacho saliente), `es_primera_compra` (primera SO del partner), `factura_id`, `tiene_devolucion` (pickings con `return_id`) |
| `sale.order.line` | `lineas_orden` | `order_id`, `product_id`, `product_uom_qty`, `price_unit`, `qty_delivered` (neta de devoluciones), `discount`, `name`, `price_subtotal`. Marca, categoría, subcategoría y presentación salen del **producto**, no de la línea. Las líneas borradas en Odoo no dejan rastro en `write_date`: se purgan con el barrido de borrados (`lineas_vigentes_por_orden`) |
| `account.payment` | `pagos` | Solo `payment_type = inbound` y estado `in_process`/`paid` (`account.payment` no tiene estado `posted`). `journal_id` identifica el método de pago (el diario lleva la identidad real: banco, efectivo USD…). Se descuentan los reembolsos |
| `account.move` | `facturas` | `move_type` ∈ `out_invoice`, `out_refund` (NC), `out_debit` (ND). `invoice_origin` puede nombrar **varias** órdenes separadas por `", "` (factura consolidada): ver `so_ids_de_invoice_origin`. `amount_residual` no se lee a propósito (varía con cada pago; se consulta en vivo). Las NC manuales toman su factura origen de la conciliación contable (respaldo: el número en `ref`) |
| `account.move.line` | `lineas_factura` | Líneas de factura/NC, incluidas las de «Descuento» |
| `stock.picking`, `stock.move.line` | `entregas`, `lineas_entrega` | Despachos salientes y su cantidad por producto |
| `product.product` | `catalogo` | Una vez al día (cambia poco) |
| `product.pricelist.item` | — (consulta en vivo) | Reglas de precio fijo por producto y vigencia; `OdooPriceResolver` (`odoo/price.py`) |

Pagos conciliados con facturas se leen vía `reconciled_invoice_ids` (el campo
`invoice_ids` lo llena solo el wizard «Registrar pago» y queda vacío cuando la
conciliación es por matching bancario o manual).

## Particularidades que conviene tener presentes

- **Moneda:** las pricelists están en USD; las facturas salen en VES. Las
  comparaciones factura ↔ motor usan los campos `*_signed_usd` de Odoo o la tasa
  de la fecha, nunca el monto VES crudo.
- **Precio por pricelist:** en Odoo 18 no hay `price_get` invocable por XML-RPC;
  el precio se obtiene de las reglas de `product.pricelist.item`. Si la lista no
  tiene regla para el producto, el resolver cae a la ficha del producto
  (`FallbackFichaConfig`). Una lista cuyas reglas están todas vencidas entrega
  precios viejos sin marcar fallback: el resolver los registra en
  `precios_de_regla_vencida`.
- **Vendedor:** se usa `res.users.login`; en algunos usuarios `login` ≠ `email`.
  El login debe coincidir con el correo con el que la persona entra al dashboard.
- **Pricelists y marca/categoría:** el mapeo de listas lógicas (USD/BCV) a ids de
  `product.pricelist` es específico del entorno; se administra desde
  Configuración y por `ODOO_PRICELIST_*` (ver `SETUP.md`).
