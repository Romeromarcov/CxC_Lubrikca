# Mapeo real Odoo → Sheets (entorno QA Lubrikca)

Introspección hecha contra `lixie-dev-lubrika-qa-33433878` (Odoo **18.0 Enterprise**).
Conteos: 411 partners, 535 SO, 1332 líneas, 599 pagos, 3156 movimientos, 255
productos, 1615 pickings, 2 pricelists.

## Constantes del entorno

| Concepto | Valor real |
|---|---|
| Moneda USD | `res.currency` id **1** (`USD`) |
| Moneda VES | `res.currency` id **166** (`VES`, símbolo `Bs`) |
| Pricelist "Lista USD" | `product.pricelist` id **4** (currency USD) |
| Pricelist "Precio USD Pago VES" | `product.pricelist` id **5** (currency USD) |
| Marcas (`product.brand`) | Global Oil (1), Sinoco (2), Master (3) |
| Categorías (`product.category`) | árbol con raíces **Comercial** (4) e **Industrial** (5) |

> Mapeo de listas del motor (a confirmar): `ENGINE_LISTA_USD` → **4 (Lista USD)**,
> `ENGINE_LISTA_BCV` → **5 (Precio USD Pago VES)**. Ambas en USD, distinto nivel
> de precio — coincide con el modelado del motor (precios en USD).

## Mapeo campo a campo

| Sheet (tabla.columna) | Modelo Odoo | Campo Odoo | Nota |
|---|---|---|---|
| Clientes.cliente_id | res.partner | `id` | |
| Clientes.nombre | res.partner | `name` | |
| Clientes.vendedor_email | res.partner | `user_id.login` | vendedor = login del usuario (riesgo 8.1) |
| OrdenesVenta.so_id | sale.order | `name` | p.ej. `S00553` (no el id numérico) |
| OrdenesVenta.cliente_id | sale.order | `partner_id` | |
| OrdenesVenta.fecha | sale.order | `date_order` | |
| OrdenesVenta.fecha_entrega | **stock.picking** | `date_done` | despacho saliente (`sale_id`); `commitment_date` viene vacío |
| OrdenesVenta.monto_total | sale.order | `amount_total` | en USD (pricelist USD) |
| OrdenesVenta.lista_precios | sale.order | `pricelist_id` | 4 o 5 |
| OrdenesVenta.vendedor_email | sale.order | `user_id.login` | |
| OrdenesVenta.es_primera_compra | (computado) | — | contar SO previas del `partner_id` |
| OrdenesVenta.facturada | sale.order | `invoice_status` | `invoiced` → TRUE |
| OrdenesVenta.factura_id / monto_facturado | account.move | `id` / `amount_total` | ligada por `invoice_origin`; **factura en VES** |
| LineasOrden.linea_id | sale.order.line | `id` | |
| LineasOrden.so_id | sale.order.line | `order_id` | |
| LineasOrden.producto | sale.order.line | `product_id` | |
| LineasOrden.marca | product.product | `brand_id` | **blocker #1: casi vacío** |
| LineasOrden.categoria | product.product | `categ_id` | árbol Comercial/Industrial/… |
| LineasOrden.cantidad | sale.order.line | `product_uom_qty` | |
| LineasOrden.precio_unitario | sale.order.line | `price_unit` | |
| Pagos.pago_id | account.payment | `id` | |
| Pagos.cliente_id | account.payment | `partner_id` | |
| Pagos.monto | account.payment | `amount` | |
| Pagos.moneda | account.payment | `currency_id` | USD(1)/VES(166) |
| Pagos.metodo_pago | account.payment | `journal_id` | id del diario → `MetodosPago.metodo_id` |
| Pagos.fecha_pago | account.payment | `date` | |
| Conciliacion.monto_odoo | account.move | `amount_total` (out_invoice) | ligada por `invoice_origin = SO.name` |
| Conciliacion.ncs_odoo | account.move | `amount_total` (out_refund) | notas de crédito |

## Catálogos reales

**Diarios de pago** (`account.journal`, vía `journal_id` del pago) →
poblar `MetodosPago.metodo_id`:

| id | nombre | clasificación sugerida (REVISAR) |
|---|---|---|
| 29 | Efectivo moneda extranjera (USD) | USD, N_A, contado=TRUE |
| 15 | Cash (efectivo Bs) | VES, BCV, contado=TRUE |
| 14 | Bank | VES, BCV, contado=FALSE |
| 30 | Banco Bancamiga 7806 | VES, BCV, contado=FALSE |
| 31 | Banco Nacional de Credito | VES, BCV, contado=FALSE |
| 32 | Banco Banesco | VES, BCV, contado=FALSE |
| 33 | Banco Provincial | VES, BCV, contado=FALSE |

## 🚩 Blockers a resolver en Odoo (prerequisitos del motor)

1. **`brand_id` casi vacío**: solo **8 de 255** productos tienen marca asignada.
   El descuento por contado es por **marca × categoría**; sin marca normalizada el
   motor no puede resolver Global Oil/Sinoco. → Poblar `brand_id` en
   `product.template` (sección 8.3). Alternativa: derivar la marca del nombre o de
   otro campo, pero lo correcto es normalizar `brand_id`.

2. **Mapeo de identidad (8.1)**: `user_id.login` es el correo de login del
   vendedor. En varios usuarios `login` ≠ `email` (p.ej. user 8: login
   `ruta10distribuidoraoil@gmail.com`, email `lubrikca.ruta3@gmail.com`).
   **Confirmar cuál usará el rep para entrar a AppSheet** y usar ese mismo campo.

3. **Factura en VES vs motor en USD**: la SO está en USD (`amount_total` 1650.44)
   pero la factura (`account.move`) sale en **VES** (953 202,39). La conciliación
   debe convertir a una moneda común (o comparar el equivalente USD de la factura
   con la tasa de su fecha) para no dar rojos falsos. → Definir la regla de
   conversión de la conciliación.

## Otros ajustes derivados de la introspección

- `metodo_pago` se mapea desde **`journal_id`** (no `payment_method_line_id`, que
  es genérico "Pago manual"). El diario lleva la identidad real (banco/efectivo USD).
- `fecha_entrega` requiere **segunda consulta** a `stock.picking` (`date_done`,
  filtrando `picking_type_code = outgoing` y `sale_id`).
- `es_primera_compra` se **calcula** (no existe en Odoo): primera SO del partner.
- `vendedor_email` y marca/categoría requieren **resolver relaciones** (segunda
  consulta a `res.users` y `product.product`), no salen del flat `search_read`.
