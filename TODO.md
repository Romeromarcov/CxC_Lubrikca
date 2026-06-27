# TODO — Ambigüedades y decisiones tomadas

Regla seguida (instrucción del proyecto): ante ambigüedad se sigue el documento;
si el documento **no lo cubre**, se elige la opción **más conservadora (no regalar
descuento)** y se anota aquí. Cada punto indica qué decidió el código y qué falta
confirmar con negocio.

---

## Decisiones de negocio a confirmar (impactan dinero)

### 1. Fórmula del descuento BCV-completo (sección 4.3c) — DEFERIDA EN EL DOC
El doc define `descuento = f(tasa_bcv, tasa_binance)` por abono pero **no da la
función**. Decisión por defecto (configurable vía `BCV_COMPLETE_FORMULA`):
```
diferencial_i = (tasa_binance_i − tasa_bcv_i) / tasa_binance_i
descuento     = Σ_i  equiv_usd_bcv_i × diferencial_i
```
- Se calcula **por abono** con la tasa estampada de su bucket (cumple 4.3c/4.4).
- **Falta confirmar** la fórmula exacta con negocio. Un solo punto de cambio:
  `_diferencial_binance` / `_bcv_completo_monto` en `src/cxc/engine/discounts.py`.

### 2. Sesgo de la tasa Binance (sección 5.1)
`(5 compra + 5 venta)/10` es un punto medio. El doc lo marca como **decisión de
negocio a confirmar**. Implementado tal cual; parametrizable en nº de filas.

---

## Ambigüedades resueltas de forma conservadora (no regalar descuento)

### 3. Condición de "método contado" con múltiples abonos (4.3b)
El doc dice "método con `es_contado = TRUE`" en singular. Con varios abonos se
exige que **TODOS** sean por método `es_contado`. Si alguno no lo es, no se
proyecta contado. (Conservador: no se otorga contado salvo ruta de contado clara.)

### 4. `fecha_entrega` ausente → sin contado
Si la orden no tiene fecha de entrega (ancla de la ventana, 4.6), el contado **no
se evalúa** (no se puede verificar la ventana) → no se otorga. (Conservador.)

### 5. Mezcla de rutas → Binance (3.9b)
Si cualquier abono salió de la ruta BCV, la orden migra a `lista USD` (VES@Binance)
y **pierde** BCV-completo. "Ante la duda, el sistema no regala el mejor descuento."

---

## Decisiones de modelado (no cambian el resultado de negocio, se documentan)

### 6. Valoración canónica en USD
El "35% nunca entra al cálculo" (4.2): ambas listas (USD/BCV) entregan un precio
en **USD** (distinto nivel de precio), leído de la pricelist real de Odoo. El
neto-objetivo se compara en USD sumando los `equiv_usd_*` congelados por abono
(4.4). Se evita así una tasa fantasma VES→USD. Las pricelists de Odoo deben estar
expresadas en USD (ver `ODOO_PRICELIST_USD/BCV` en SETUP.md).

### 7. Bandas del semáforo de conciliación (sección 7)
El texto del doc mezcla "≈0" con "≤ redondeo". Implementadas tres bandas claras:
```
|dif| <= tolerancia_redondeo           -> VERDE
tolerancia_redondeo < |dif| <= roja    -> AMARILLO
|dif| > tolerancia_roja                -> ROJO
```
Tolerancias configurables (`RECON_TOLERANCE_ROUNDING`, `RECON_TOLERANCE_RED`).

### 8. Fallback del scraper ante fallo de UNA sola fuente (5.3)
Si falla Binance **o** BCV, se hereda la **fila completa** del último bucket
(`es_heredada=TRUE`, `capturada_ok=FALSE`), para no mezclar una tasa fresca con
una heredada en la misma fila (consistencia de auditoría). Alternativa (heredar
solo la fuente caída) descartada por claridad de auditoría.

### 9. Desempates de effective dating
- `DescuentosMarcaCategoria`: mayor especificidad (marca exacta pesa más que
  categoría exacta) → menor porcentaje (conservador) → `regla_id`.
- `ReglasRecurrencia`: `vigencia_desde` más reciente → menor `valor`.

### 10. `EstadoFactura` embebido en `OrdenesVenta`
El doc lista `EstadoFactura` como tabla-espejo; sus campos (`facturada`,
`factura_id`, `monto_facturado`) viven en `OrdenesVenta` (3.2). No se crea una
pestaña separada.

### 11. Emparejamiento abono↔banco en la auditoría (6.3)
Por **monto exacto**; si hay varios candidatos, el de hora más cercana a la
declarada. Sin movimiento que calce → hallazgo de prioridad ALTA.

### 12. `requiere_revision` de la Bandeja
Se marca TRUE si hay tasa heredada, si aplicó BCV-completo (calculado, no
tabulado) o si el contado se negó por vencimiento (transición contado→crédito).

### 13. Cursor del sync
Avanza al `now` de inicio de corrida (no al máximo `write_date` leído) para no
perder filas escritas durante la lectura. Trade-off: posible re-lectura de
solapados (idempotente por upsert).

---

## 🚩 Precio por pricelist en Odoo 18 (bloqueante del motor en producción)

Verificado contra el QA: el motor no puede leer el precio de un producto en una
pricelist por los métodos asumidos:
- `product.pricelist.price_get` → **removido en Odoo 18**.
- `product.pricelist._get_product_price` / `_get_products_price` → privados, **no
  invocables por XML-RPC**.
- `product.pricelist.item` de las listas 4 y 5 → **vacío** (los precios no salen
  de items simples; podrían venir de fórmula, lista base o del `list_price`).

Opciones para producción (decisión de negocio + Odoo):
1. **Usar el precio de la línea ya sincronizado** cuando la lista aplicada = lista
   de nacimiento de la orden. En este negocio TODAS las órdenes nacen en la
   pricelist 5 ("Precio USD Pago VES"); si la ruta de pago es VES/BCV, la lista
   aplicada es la 5 y el precio de la línea sirve directo (es lo que usa el demo).
2. Si se paga en USD (lista 4), exponer ese precio: crear un endpoint/acción en
   Odoo, sincronizar una tabla `Precios(producto, lista, precio)`, o confirmar la
   relación entre la lista 4 y la 5 (¿factor fijo? ¿misma base list_price?).

Hasta resolverlo, el motor opera por ruta BCV/VES con precios de línea (caso real
mayoritario). `OdooPriceResolver` queda como esqueleto a calibrar.

## Integraciones específicas del entorno (ver SETUP.md)

- **`fecha_entrega` desde `stock.picking`** (3.2): el reader lee un campo
  `fecha_entrega` en `sale.order`; exponerlo desde el despacho en Odoo.
- **`OdooPriceResolver`**: usa `product.pricelist.price_get`. Verificar contra la
  versión de Odoo del cliente; es el único punto de ajuste del precio por lista.
- **Nombres de campo de Odoo** en `cxc/odoo/client.py` (constantes `FIELDS_*`):
  ajustar si difieren del entorno.
