# Auditoría Cruzada de Datos — Cuentas por Cobrar Lubrikca C.A.

**Fecha de auditoría:** 2026-06-27
**Modo:** read-only (no se modificó ningún archivo original)
**Alcance:** todos los archivos en `Info_Odoo/` (+ subcarpeta `Listas de Precio/`) y la especificación `Especificacion_Sistema_Cobros_Lubrikca.md`
**Soporte:** scripts y CSVs con el detalle de cada caso en la carpeta `auditoria_soporte/`

> **Aviso:** Esta auditoría asiste el proceso de conciliación; no constituye asesoría contable ni financiera. Los hallazgos deben ser revisados y firmados por un profesional contable de Lubrikca antes de tomar acciones. Donde faltó información para verificar algo, se dice explícitamente en lugar de adivinar.

---

## 1. Resumen ejecutivo

Conteo de discrepancias por categoría y severidad (detalle en cada sección):

| # | Comparación | Hallazgo principal | Casos | Severidad |
|---|---|---|---|---|
| A | Pagos Odoo vs Cobranza | Pagos de Odoo sin reflejo en Cobranza | **101** (USD ≈ 88.990) | 🔴 Alta |
| A | Pagos Odoo vs Cobranza | Cobranza sin pago en Odoo | **82** (USD ≈ 46.991) | 🔴 Alta |
| A | Pagos Odoo vs Cobranza | Pagos con misma identidad pero distinta fecha (timing) | 33 | 🟡 Media |
| A | Pagos Odoo vs Cobranza | Pagos duplicados exactos en Cobranza | 12 filas / 6 grupos | 🔴 Alta |
| B | Ventas vs Inventario | Venta sin salida de inventario (cliente+producto) | 93 pares (qty 343) | 🟡 Media* |
| B | Ventas vs Inventario | Salida de inventario sin venta | 11 pares (qty 25) | 🟡 Media* |
| B | Ventas vs Inventario | Diferencia de cantidad (ambos > 0) | 56 pares | 🟡 Media |
| C | Ventas vs hoja 'Ventas' | Órdenes en Reporte/Odoo ausentes en hoja Ventas | 55 (rep) / 10 (sale.order) | 🟡 Media |
| C | Ventas vs hoja 'Ventas' | Orden con monto Odoo casi nulo vs Ventas (S00298) | 1 | 🔴 Alta |
| C | Ventas vs hoja 'Ventas' | "491 discrepancias" que en realidad son **IVA 16%** | 491 | ⚪ No es error |
| D | Precios vs listas | Líneas que NO coinciden exactamente con ninguna lista | 602/602 evaluables | 🟠 Ver limitación |
| D | Precios vs listas | Desviación **material** (>3 USD y >5% sobre ambas listas) | 382 líneas / 216 órdenes | 🔴 Alta |
| D | Precios vs listas | Productos sin lista vigente disponible | 200 líneas | 🟡 Media |
| D | Precios vs listas | Línea con lista de moneda equivocada (candidatas) | 7 | 🟡 Media |
| D | Precios vs listas | Líneas con lista indeterminada (sin método de pago) | 613 | 🟡 Media |
| E | Descuentos | Órdenes que exceden el descuento diferencial máximo | **0** | 🟢 OK |
| E | Descuentos | Órdenes marcadas "NO CUMPLE" por la validación del propio libro | 152 | 🟡 Media |
| E | Notas de crédito | NC / asientos negativos sin justificación verificable | 6 (≈ -2.146.686 VES) | 🔴 Alta |
| 2 | General | Saldos negativos (sobrepago de cliente) | 41 órdenes | 🔴 Alta |
| 2 | General | Saldo ≠ Total c/Dcto − Pagado | 4 órdenes | 🟡 Media |
| 2 | General | `Número` reutilizado en account.move (factura vs NC) | 6 valores colisionan | 🟡 Media |
| 2 | General | Clientes con variantes de escritura / posible mismo cliente | 14 + 5 pares | 🟡 Media |
| 2 | General | Filas en blanco / campos críticos vacíos | Ventas 97, account.move 113, Pagos 5 | 🟢 Baja |
| 2 | General | Fechas futuras o ilógicas | 0 | 🟢 OK |

\* La severidad de B está condicionada por limitaciones de cobertura de fechas y variantes de nombre (ver §4B).

**Lectura rápida:** Los problemas más accionables son (1) el descalce de pagos entre Odoo y Cobranza (183 registros en total entre ambos sentidos), (2) los 41 sobrepagos y la posible mala aplicación de pagos a órdenes, (3) las notas de crédito en bolívares sin justificación verificable, y (4) la imposibilidad de validar el precio unitario contra las listas porque **ninguna línea facturada coincide exactamente con ninguna lista** y el precio parece derivarse por conversión de moneda (ver §4D — esto es en sí un hallazgo de control).

---

## 2. Reglas extraídas de la especificación (interpretación verificable)

Estas son las reglas tal como las interpreté de `Especificacion_Sistema_Cobros_Lubrikca.md`. Si alguna está mal interpretada, corregir aquí cambia los hallazgos de D y E.

1. **Todo se vende en USD.** Las dos listas ("USD" y "VES/Bs") están **ambas expresadas en USD**. La lista "VES/Bs" no es en bolívares: es la lista (en USD) que aplica a quien **paga** en VES. (Confirmado por la nota de negocio y por la lista 23-02-26, que trae `Precio Divisas` y `Precio Bolívares` ambos en valores tipo USD.)

2. **La lista la define el método de pago** (§4.2 de la spec):
   - método en USD → **lista USD**
   - método VES a tasa Binance → **lista USD** (VES@Binance recibe precio lista USD)
   - método VES a tasa BCV → **lista BCV/VES**
   - Conflicto lista especial vs método de pago → **gana el método de pago**.

3. **Comparación de precios siempre USD contra USD** (§Tarea D). Nunca se convierte a bolívares ni se aplica tasa de cambio para comparar precios.

4. **Effective dating** (§3.7, §8.4): el motor toma la versión de lista/descuento **vigente a la fecha de la orden**, no la de hoy. Coincidencia exacta de categoría gana sobre comodín `*`.

5. **Descuentos apilan (se suman), no "el mayor gana"** (§4.1, §4.5):
   - Recurrencia: primera compra → **NC** por caja de liga (monto fijo); recompra → **3%**.
   - Contado por marca×categoría (de DescuentosMarcaCategoria): Global Oil sintético **8%**, Global Oil industrial **6%**, Sinoco **3%**. Requiere método `es_contado` **Y** liquidación total dentro de `[fecha_entrega, fecha_entrega + 3 días hábiles]`.
   - BCV-completo: solo si paga **todo** en VES a tasa BCV; si hubo mezcla de rutas, migra a VES@Binance y pierde este descuento.
   - Ejemplos de apilamiento de la spec: Sinoco recompra contado = 6%; Global Oil sintético recompra contado = 11%; Global Oil industrial recompra contado = 9%.

6. **Límite de descuento diferencial** (hoja `Limites de Descuento` del libro CxC): máximo **0,35 (35%)** para pago USD; **0,15 (15%)** para VES y MIXTO. Peso del pago en factura 0,85 (USD 0,65).

7. **Conciliación = semáforo por tolerancia** (§7): |dif| ≈ 0 → verde; ≤ tolerancia de redondeo → amarillo; > tolerancia → rojo. En esta auditoría se usó **> 0,01 USD = discrepancia real**, separando las diferencias pequeñas (redondeo) de los errores.

8. **Cross-currency:** montos en monedas distintas no se convierten salvo que exista tasa explícita; si no, se marcan como "no comparable directamente". (Excepción: la comparación D es USD vs USD.)

### Reglas que quedaron AMBIGUAS o NO VERIFICABLES con los datos disponibles

- **Ventana de contado (3 días hábiles):** depende de `fecha_entrega` del despacho (`stock.picking`) y de la tabla de Feriados. El export de stock (`stock.move.line`) trae fecha de movimiento pero **no enlaza a la orden de venta** ni hay tabla de feriados. → **No se pudo auditar el descuento de contado.**
- **BCV-completo y valoración por abono:** requiere la serie horaria de tasas (`SerieTasas`) y la tabla `Vinculaciones` con equivalentes congelados. Esas tablas **no están entre los archivos.** → **No verificable.**
- **NC por primera compra (caja de liga):** requiere el flag `es_primera_compra` por cliente. No está expuesto en los exports. → **No verificable.**
- **Marca/categoría por producto:** los exports no traen marca ni categoría normalizadas (la spec §8.3 lo marca como prerequisito pendiente). → El descuento de contado por marca×categoría **no es computable** desde estos archivos.

---

## 3. Fase 0 — Inventario de archivos

### 3.1 Archivos de datos (`Info_Odoo/`)

| Archivo | Hoja(s) | Filas | Rol | Llave |
|---|---|---|---|---|
| `Pagos (account.payment).xlsx` | Sheet1 | 650 | Pagos de cliente en Odoo | Cliente + Fecha + Importe (sin Nº de orden) |
| `Cobranza.xlsx` | Cobranza (631 pobladas de 1089), Montos Reales, Montos a BCV | 631 | Hoja operativa de cobranzas | Nro de Orden + Cliente + Fecha + Importe |
| `Cuentas x Cobrar Nuevo.xlsx` | 13 hojas (Ventas, Limites de Descuento, Montos Odoo, Reporte Discrepancias, …) | Ventas: 497 reales / 594 | Maestro CxC | Referencia de la orden (S00xxx) |
| `Orden de venta (sale.order).xlsx` | Sheet1 | 503 | Cabecera de órdenes | Referencia de la orden |
| `Reporte_Ventas_2026-02-01_al_2026-06-27.xlsx` | Reporte de Ventas | 1415 | Ventas a nivel **línea** | Order + product_template_id |
| `Movimientos de producto (stock.move.line).xlsx` | Sheet1 | 1270 | Salidas de inventario | Referencia ALM/OUT + Contacto + Producto |
| `Asiento contable (account.move).xlsx` | Sheet1 | 201 reales / 314 | Facturas y NC | Número (⚠ reutilizado) |

**Formatos detectados:** fechas ISO `YYYY-MM-DD HH:MM:SS`; decimal con punto, sin separador de miles; los exports de Odoo y la hoja Cobranza comparten convención (no se detectó conflicto de decimales). Monedas: USD y VES. Rango de fechas global: **2026-02-25 a 2026-06-24**.

**Mapeo de columnas clave (no se asumieron nombres):**
- *Pagos:* cliente=`Cliente/proveedor`, fecha=`Fecha`, monto nativo=`Importe firmado`, monto USD=`Importe referencia`, método=`Método de pago`, estado=`Estado`. **No hay número de orden ni de factura** → la conciliación A se hace por Cliente+Fecha+Monto (supuesto declarado).
- *Cobranza:* `Nro de Orden`, `Importe firmado` (nativo), `Moneda`, `Monto Unificado USD`, `Cliente`, `Fecha`, `Validación Ingreso`.
- *Reporte de Ventas:* `price_unit_usd` (unitario), `price_subtotal_ref` (subtotal **sin IVA**), `product_uom_qty`, `pricelist_id` = genérico "Lista de Precios" (no distingue USD/VES).
- *CxC Ventas:* `Total` (con IVA), `Total C/Dcto`, `Descuento x Diferencial %`, `Dcto Pronto Pago %`, `Moneda de pago`, `Saldo Orden`, `Monto Pagado USD`, `Status de Pago`.

### 3.2 Listas de precio (`Info_Odoo/Listas de Precio/`)

| Archivo | Vigencia (interpretada) | Tipo | Estructura | Estado |
|---|---|---|---|---|
| `Lista de Precio 23-02-26.xlsx` | **desde 2026-02-23** | USD y VES (ambas en USD) | 147 códigos con `Precio Divisas` (paga USD) y `Precio Bolívares` (paga VES, en USD) | ✅ Usable |
| `14 Precios Venta 12-3-26.xlsx` | **desde 2026-03-12** | USD (interno) | 142 códigos; `Precio Divisas` = precio USD | ✅ Usable (solo columna USD) |
| `Lista de precios (product.pricelist) USD 15-6-26.xlsx` | **desde 2026-06-15** | USD (export Odoo) | 89 ítems con `fixed_price` | ✅ Usable |
| `Lista de precios (product.pricelist) VES 15-6-26.xlsx` | 2026-06-15 | VES (export Odoo) | **Solo metadatos, sin ítems de precio** | ❌ Inutilizable |
| `Existencias y Precio en pagando en VES.xlsx` | s/f | VES | 238 filas pero `Precio de venta $` = **0 en todas** | ❌ Inutilizable |

**Consecuencia (limitación dura):** solo existe **una** versión con precios VES por ítem (la del 23-02-26). Para órdenes en VES posteriores a marzo no hay lista VES vigente con precios → la comparación D para pagos en VES queda anclada a una lista que pudo cambiar (riesgo de falsos positivos; declarado).

Orden por vigencia: **23-02-26 → 12-03-26 → 15-06-26 (USD).**

### 3.3 Hallazgo de Fase 0: el libro ya trae una conciliación previa

`Cuentas x Cobrar Nuevo.xlsx` contiene hojas `Montos Odoo`, `Reporte Discrepancias`, `Aux_Datos` y columnas de validación. Es decir, **ya existe un intento de conciliación dentro del libro**. La hoja `Reporte Discrepancias` lista 99 órdenes "con discrepancia de monto" y 21 "faltantes en Odoo". Esta auditoría reproduce y **matiza** ese trabajo (ver §4C): buena parte de esas 99 "discrepancias" son simplemente la diferencia entre `Total` y `Total C/Dcto` (el descuento) o el IVA, no errores.

---

## 4. Fase 1 — Comparaciones cruzadas

### A) Pagos Odoo (account.payment) vs Cobranza

**Llave usada:** Cliente (normalizado) + Fecha + **monto en moneda nativa** + moneda. Se evitó cruzar por el USD convertido porque para pagos VES el USD de Cobranza (`Monto Unificado USD`, calculado con tasa propia) y el de Odoo (`Importe referencia`) **se calculan con tasas distintas y no cuadran al centavo** → se marcarían como discrepancia falsa. Esto es un cruce "no comparable directamente" en USD; por eso se usó el monto nativo.

**Resultado del cruce (650 pagos Odoo vs 631 filas pobladas de Cobranza):**

| Resultado | Cantidad |
|---|---|
| Conciliados exactos (cliente+moneda+monto+fecha) | 516 |
| Conciliados con diferencia de fecha (timing) | 33 |
| **Pagos en Odoo SIN registro en Cobranza** | **101** (USD ref ≈ 88.990) |
| **Filas de Cobranza SIN pago en Odoo** | **82** (USD ≈ 46.991) |

Verificación de cuadre: 516 + 33 + 101 = 650 = total de pagos Odoo ✔

**A.1 — Pagos en Odoo sin reflejo en Cobranza (101).** Severidad 🔴 Alta. Composición: 89 en estado "En proceso", **12 en estado "Borrador"** (pagos no confirmados que igual figuran); 63 VES / 38 USD. Concentrados en las fechas recientes (jun-2026), consistente con cobranzas aún no volcadas a la hoja operativa. Ejemplos:

| Cliente | Fecha | Moneda | Monto nativo | Estado | Método |
|---|---|---|---|---|---|
| SERVICIOS Y MANTENIMIENTO SPACARS | 2026-06-23 | VES | 549.717,40 | Borrador | Bancamiga 7806 |
| UBAG Services, c.a. | 2026-06-19 | VES | — (USD 325,29) | En proceso | Banco de Venezuela |
| Chaquiro Arteaga | 2026-06-22 | USD | 70,00 | En proceso | Efectivo USD |
| MULTIPARTES Y SERVICIOS HONDANIS | 2026-06-17 | VES | — (USD 201,60) | En proceso | Bancamiga 7806 |

→ Detalle completo: `auditoria_soporte/A_pagos_sin_cobranza.csv`.

**A.2 — Cobranza sin pago en Odoo (82).** Severidad 🔴 Alta. 55 USD / 27 VES, fechas 2026-02-27 a 2026-06-23. Todas marcadas `NO ENCONTRADO EN MAESTRO` en la columna `Validación Ingreso` del propio libro. Ejemplos: ZIP MARKET 35,33 USD (orden 8); Auto Repuestos Acal 119,48 USD (orden 51); E&M CAR SHOP 399,54 VES (orden 79). → `auditoria_soporte/A_cobranza_sin_pagos.csv`.

**A.3 — Timing (33).** Mismo cliente, moneda y monto, distinta fecha (Cobranza vs Odoo). Categoría 1 (diferencias de tiempo) — deberían cerrar solos; revisar los de >15 días. Ej.: INVERSIONES ARCOSOL 21 — 129,33 USD, Odoo 2026-06-05 vs Cobranza 2026-05-18 (18 días).

**A.4 — Duplicados en Cobranza (12 filas / 6 grupos).** Severidad 🔴 Alta (riesgo de doble registro de cobro):

| Cliente | Fecha | Moneda | Monto | Nro Orden |
|---|---|---|---|---|
| CONSTRUCTORA GRANO AGREGADO | 2026-05-21 | VES | 1.000.000,00 | 214 (×2) |
| SPEED RACING, CA | 2026-05-14 | VES | 9.761,32 | 14 (×2) |
| INVERSIONES MI LINDA YEMAIRE | 2026-04-30 | VES | 56.976,38 | 53 (×2) |
| Angel ARMAS | 2026-06-05 | USD | 75,00 | **506 vs 213** (órdenes distintas) |
| Jose Lopez Espinoza | 2026-04-06 | VES | 47.405,98 | **121 vs 2** (órdenes distintas) |

Los dos últimos comparten cliente/monto/fecha pero **distinto número de orden** → o son dos pagos legítimos casualmente iguales, o un mismo pago se aplicó a dos órdenes. Revisar manualmente. → `auditoria_soporte/A_dup_cobranza.csv`.

---

### B) Reportes de venta vs historial de salidas de inventario

**Llave usada:** Contacto/Cliente (normalizado) + código de producto `[NNNN]`, agregando cantidades. **No existe llave de orden común** entre `Reporte_Ventas` y `stock.move.line` (este último referencia `ALM/OUT/xxxxx`, no S00xxx).

**Limitaciones que afectan todo B (declaradas):**
1. **Cobertura de fechas:** las ventas arrancan 2026-02-25 pero el inventario solo desde **2026-03-09** → toda venta de fin de feb/inicio marzo aparece "sin salida" por construcción.
2. **Variantes de nombre de contacto:** el inventario usa a veces el contacto de entrega/vendedor (p. ej. "SERVICIOS Y MANTENIMIENTO SPACARS **HENRY BLANCO**") mientras la venta usa la razón social → falsos "sin coincidencia".
3. Órdenes recientes "Por facturar" pueden no estar despachadas aún.

**Resultado (a nivel cliente+producto):**

| Métrica | Valor |
|---|---|
| Vendido total (qty) | 3.588 |
| Movido / despachado total (qty) | 3.103 |
| Pares (cliente+producto) vendidos sin salida de inventario | 93 (qty 343) |
| Pares con salida de inventario sin venta | 11 (qty 25) |
| Pares con diferencia de cantidad (ambos > 0) | 56 |
| Productos (sin cliente) con diferencia total ≠ 0 | 55 de 75 |

**Casos de diferencia de cantidad más grandes** (probables, más allá de la cobertura de fechas):

| Cliente | Cód | Vendido | Movido | Dif |
|---|---|---|---|---|
| CONSTRUCTORA GRANO AGREGADO | 144 | 66 | 15 | +51 |
| AGROPECUARIA EL TORO DORADO | 83 | 29 | 17 | +12 |
| MINI MARKET LAS MERCEDES | 877 | 26 | 14 | +12 |
| HIPERMERCADO PETARE | 887 | 2 | 10 | −8 |
| EN ASCENSO 2011 | 602 | 7 | 15 | −8 |

→ Detalle: `auditoria_soporte/B_inv_recon.csv`, `B_vendido_sin_salida.csv`, `B_salida_sin_venta.csv`.

**Conclusión B:** el grueso del descalce se explica por las tres limitaciones de arriba más que por inventario realmente faltante. Los casos con `movido > vendido` (salida mayor que venta, p. ej. HIPERMERCADO PETARE, EN ASCENSO 2011) son los más dignos de revisión porque sugieren despacho sin venta registrada.

---

### C) Reporte de ventas vs hoja 'Ventas' del archivo CxC

**Hallazgo metodológico clave (evita 491 falsos positivos):** `CxC.Ventas.Total` y `sale.order.Total` están **con IVA**; `Reporte_Ventas.price_subtotal_ref` está **sin IVA**. La relación es exactamente **×1,16** (ratio medido 1,1600 ± 0,00003 en 500 órdenes). Por tanto las ~491 "diferencias" entre el subtotal del reporte y los totales del libro **no son errores: son el IVA 16%.** Tras descontar el IVA, solo **3 órdenes** difieren, y por 0,02 (redondeo).

**Cobertura (unión de 549 órdenes):** Reporte 546 · sale.order 503 · hoja Ventas 493 · Montos Odoo 476.

| Hallazgo | Casos | Severidad |
|---|---|---|
| Órdenes en Reporte de Ventas ausentes en hoja Ventas | 55 | 🟡 Media |
| Órdenes en sale.order ausentes en hoja Ventas | 10 | 🟡 Media |
| Órdenes en hoja Ventas ausentes en sale.order | 0 | 🟢 |
| `CxC.Total` vs `sale.order.Total` con dif > 0,01 | 4 (S00589–S00592) | 🟡 Media |
| `CxC.Total` vs `Montos Odoo` con dif > 0,01 | 8 (7 por 0,02 + S00298) | 🟡/🔴 |

**Caso 🔴 Alta — S00298:** la hoja Ventas registra `Total` = 284,61 pero `Montos Odoo` registra 0,59 (dif 284,02). Orden facturada por casi nada en Odoo frente a lo que dice el maestro CxC. Revisar.

**Validación de la conciliación previa del libro:** la hoja `Reporte Discrepancias` lista 99 órdenes "con discrepancia de monto". Verificado: en su mayoría comparan `Monto Odoo` contra `Total C/Dcto` (después de descuento) — esa diferencia **es el descuento aplicado, no un error** (p. ej. S00004: 232,97 vs 175 = dif 57,97 = descuento). Recomiendo reetiquetar esa hoja para no confundir descuento con discrepancia. → `auditoria_soporte/C_ventas_recon.csv`.

---

### D) Precios de órdenes vs listas de precio vigentes (USD vs USD)

**Método:** para cada línea de `Reporte_Ventas` se tomó el código de producto, la fecha de la orden y la `Moneda de pago` de la hoja Ventas para decidir la lista (USD vs VES) y la versión vigente por effective dating, y se comparó `price_unit_usd` contra el precio USD de esa lista.

**Hallazgo central (🟠 limitación que es a la vez un hallazgo de control):**
- De **602** líneas con lista esperada disponible, **0 coinciden exactamente** con el precio de lista.
- **462 de 602** precios unitarios facturados **no son enteros** (p. ej. 32,76; 68,11; 90,48), mientras las listas tienen precios enteros. Esto indica que el precio **no se lee directo de la lista** sino que se **deriva por conversión de moneda/tasa** (o lleva un descuento embebido).
- Prueba decisiva: en las órdenes en VES, el ratio `precio_facturado / precio_lista_VES` es **constante dentro de cada orden** (≈ 0,75 con desviación estándar ≈ 0,000). Un multiplicador fijo por orden = regla determinística (conversión/descuento), **no** error línea por línea.

**Conclusión D:** con los archivos disponibles **no es posible afirmar de forma fiable un sobrecobro/subcobro por línea**, porque (a) ninguna línea calza con una lista, (b) el precio se deriva por un factor de conversión por orden, y (c) la única lista VES con precios es la del 23-02-26 (sin versión vigente para órdenes VES posteriores). Esto se reporta como **limitación**, no como ausencia de hallazgos.

**Lo que sí se puede señalar para revisión manual:**

| Sub-hallazgo | Líneas | Órdenes | Severidad |
|---|---|---|---|
| Desviación **material**: precio lejos de **ambas** listas (>3 USD **y** >5%) | 382 | 216 | 🔴 Alta |
| Producto no presente en ninguna lista disponible | 200 | — | 🟡 Media |
| Lista de moneda equivocada (candidatas, tol. 0,5) | 7 | — | 🟡 Media |
| Lista indeterminada (orden "Sin Pago"/sin moneda) | 613 | — | 🟡 Media |

**Ejemplos de desviación material** (productos industriales de alto valor, facturados por **encima** de toda lista conocida):

| Orden | Cód | Fecha | Paga | Facturado | Lista USD | Lista VES | Vers. |
|---|---|---|---|---|---|---|---|
| S00328 | 75 | 2026-05-13 | USD | 1.247,84 | 933 | 1.167 | mar12 |
| S00214 | 75 | 2026-05-12 | VES | 1.334,22 | 933 | 1.167 | feb23 |
| S00246 | 85 | 2026-04-24 | USD | 1.136,00 | 824 | 1.030 | mar12 |
| S00214 | 125 | 2026-05-12 | VES | 1.317,42 | 956 | 1.195 | feb23 |
| S00427 | 146 | 2026-05-26 | VES | 1.103,45 | 799 | 998 | feb23 |

→ Detalle: `auditoria_soporte/D_precios.csv`, `D_mispricing_material.csv`.

---

### E) Descuentos, notas de crédito y coherencia pago–precio–moneda

**E.1 — Descuento diferencial vs límites** (USD 0,35 / VES,MIXTO 0,15): **0 órdenes exceden** el máximo. 🟢

**E.2 — Aritmética del descuento:** `Total C/Dcto` = `Total` × (1 − dif%) × (1 − pronto_pago%) es consistente. Las 6 órdenes que a primera vista no cuadraban (S00047, S00064, S00131, S00234, S00452, S00454) se explican por el **descuento de pronto pago** apilado, no por error. 🟢

**E.3 — Validación propia del libro (`Validación por Diferencial`):**

| Resultado del libro | Órdenes |
|---|---|
| CUMPLE | 183 |
| **NO CUMPLE** | **152** |
| SIN MONEDA VÁLIDA (Sin Pago) | 160 |
| SIN TOTAL | 2 |

Las **152 marcadas "NO CUMPLE"** por la propia lógica del archivo son el conjunto que Administración debería revisar primero en materia de descuentos. 🟡 Media. → `auditoria_soporte/E_ventas_dctos.csv`.

**E.4 — Notas de crédito / asientos negativos** (account.move). Severidad 🔴 Alta:

| Número | Fecha | Cliente | Total (VES) | Estado |
|---|---|---|---|---|
| 1 | 2026-03-24 | E&M CAR SHOP | −139.113,84 | Pagado |
| 2 | 2026-03-24 | E&M CAR SHOP | −245.057,27 | Pagado |
| 3 | 2026-05-05 | MOTO REPUESTOS LA FLECHA | −47.455,08 | Pagado |
| 4 | 2026-05-07 | DISTRIBUIDORA MULTIAHORRO | −57.227,46 | Pagado |
| 5 | 2026-05-11 | CONSTRUCTORA GRANO AGREGADO | −1.605.846,75 | Pagado |
| 6 | 2026-05-19 | Auto Repuestos Acal | −51.985,86 | Pagado |

Suma ≈ **−2.146.686 VES**. Además hay **5 asientos "Revertido"**. La especificación (§3.10–3.11, §7) exige que cada NC esté justificada y respaldada; **los archivos no traen el motivo ni el respaldo** de estas NC → no es posible validar su justificación desde aquí. Requieren revisión manual con su soporte. (Nota: montos en VES, **no comparables directamente** con los USD de las órdenes sin tasa explícita.)

**E.5 — Coherencia pago–precio–moneda:** ligada a D. Las órdenes que pagan en VES quedan facturadas ≈0,75× la lista VES (por debajo incluso del nivel USD), lo que **podría** indicar lista o descuento de la moneda equivocada — pero por la limitación de D (sin lista VES vigente posterior a febrero) no puede confirmarse. 7 líneas son candidatas explícitas a "lista equivocada" (§4D).

---

## 5. Fase 2 — Auditoría general

**5.1 Duplicados**
- **Cobranza:** 12 filas / 6 grupos exactos (ver A.4). 🔴
- **CxC hoja Ventas:** 4 referencias de orden duplicadas. 🟡
- **account.move — `Número` reutilizado:** los valores 1–6 aparecen **dos veces** porque la facturación y las NC usan series de numeración que se solapan (factura `Número 1` de ALIPLUS coexiste con NC `Número 1` de E&M). El campo `Número` **no es una llave única** → riesgo de error al cruzar por él. 🟡
- sale.order: 0 duplicados. 🟢

**5.2 Campos críticos vacíos**
- Hoja `Ventas`: **97 filas completamente en blanco** (497 reales de 594).
- `account.move`: **113 filas en blanco** (201 reales de 314).
- `Pagos`: 5 filas sin `Cliente/proveedor`.
Severidad 🟢 Baja (son colas en blanco), pero conviene limpiarlas para que los conteos automáticos no engañen.

**5.3 Fechas fuera de rango / futuras:** ninguna. Todas entre 2026-02-25 y 2026-06-24 (≤ hoy). 🟢

**5.4 Saldos de CxC**
- **41 órdenes con saldo NEGATIVO (sobrepago del cliente).** 🔴 Alta. Ej.: S00008 (pagó 85,33 sobre 35,33 → −50,00); S00009 (−60,49); S00021 (−25,93); S00034 (−19,95). Sugiere pagos mal aplicados, pagos a la orden equivocada, o cobros de más. → `auditoria_soporte/G_saldos.csv`.
- **4 órdenes donde `Saldo` ≠ `Total C/Dcto − Pagado`** (la fórmula del saldo no cierra): S00017 (pagado 1.847 sobre 104,36 pero saldo = 0), S00416 (+447,02 sin reflejar), S00556 (+428,32 sin reflejar), S00570 (0,34). 🟡

**5.5 Clientes con nombres distintos que parecen el mismo**
- **14 grupos** con misma forma normalizada pero distinta escritura (dobles espacios, acentos, "C.A."): p. ej. `CONSTRUCTORA GRANO AGREGADO, C.A` vs `Constructora Grano Agregado, C.A.`; `ESTACIONAMIENTO CIACORE 2000` (3 variantes); `ELENA  MEJIAS` vs `ELENA MEJIAS`.
- **5 pares muy similares** (posible mismo cliente): `Williams Bastidas` ~ `Wuilliams bastidas`; `Neykel Quevedo` ~ `Nyekel Quevedo`; `Agropecuaria Leche Miel` ~ `AGROPECUARIA LECHE Y MIEL, C.A`; `INVERSIONES MI 'LINDA YEMAIRE` ~ `INVERSIONES MI LINDA YEMAIRE`. (Cuidado: `MOTO REPUESTOS LA FLECHA C.A.` vs `... LA FLECHA II C.A.` podrían ser sucursales distintas.) 🟡
→ `auditoria_soporte/G_clientes_variantes.csv`, `G_clientes_similares.csv`.

**5.6 Inconsistencias de moneda / tasa**
- Para pagos en VES, el USD calculado en Cobranza (`Monto Unificado USD`) y el de Odoo (`Importe referencia`) usan tasas distintas → no cuadran al centavo. Marcado como **no comparable directamente** en USD (por eso A se cruzó en moneda nativa). No es error de registro, pero impide conciliar por USD.
- Totales generales no conciliables entre USD y VES sin tasa explícita por transacción.

---

## 6. Supuestos tomados

1. El `Nro de Orden` numérico de Cobranza corresponde al sufijo de `S00xxx` (orden 52 = S00052).
2. En Pagos, el método "Efectivo moneda extranjera" = USD; "moneda nacional"/Banco = VES (para fijar la moneda nativa del cruce A).
3. `Importe referencia` (Pagos) y `Monto Unificado USD` (Cobranza) son equivalentes en USD; se prefirió cruzar por monto nativo por la diferencia de tasas.
4. La lista aplicable se infiere de `Moneda de pago` de la hoja Ventas (USD→lista USD, VES→lista VES, MIXTO→USD por la regla de migración a VES@Binance).
5. Effective dating con cortes 2026-02-23 / 2026-03-12 / 2026-06-15.
6. Tolerancia de discrepancia real: > 0,01 USD; diferencias ≤ 0,02 se tratan como redondeo.
7. `sale.order.Total` y `CxC.Ventas.Total` incluyen IVA 16%; `Reporte_Ventas.price_subtotal_ref` no.

## 7. Limitaciones encontradas

1. **Pagos sin llave de orden/factura** → la conciliación A es por Cliente+Fecha+Monto (probabilística, no por referencia).
2. **No hay llave común ventas↔inventario** (S00xxx vs ALM/OUT) → B es por cliente+producto agregado.
3. **Inventario inicia 2026-03-09**, ventas desde 2026-02-25 → descalce de cobertura inevitable en B.
4. **Lista VES con precios solo existe al 23-02-26**; la del 15-06-26 y la de "pagando en VES" están vacías → D no es fiable para órdenes VES posteriores.
5. **Ninguna línea coincide con lista** y el precio se deriva por conversión → no se puede afirmar sobre/subcobro exacto por línea (solo señalar desviaciones materiales).
6. **No verificables con estos archivos:** descuento de contado (falta fecha de entrega enlazada + feriados + marca/categoría), BCV-completo (falta SerieTasas y Vinculaciones), NC por primera compra (falta flag), justificación de las NC existentes.
7. **`Número` de account.move no es único** (series factura/NC solapadas).
8. **Montos en VES vs USD no comparables** sin tasa explícita por transacción.

## 8. Recomendaciones priorizadas (qué revisar manualmente primero)

1. 🔴 **Sobrepagos (41 órdenes con saldo negativo) y las 4 órdenes cuyo saldo no cierra.** Es dinero del cliente potencialmente mal aplicado; impacto directo en CxC. (`G_saldos.csv`)
2. 🔴 **Pagos duplicados en Cobranza (6 grupos)** y los dos pagos iguales aplicados a órdenes distintas (Angel ARMAS, Jose Lopez). Riesgo de doble conteo de cobro.
3. 🔴 **101 pagos de Odoo sin Cobranza + 82 cobranzas sin pago Odoo.** Empezar por los 12 pagos en estado "Borrador" y por las cobranzas USD (importe cierto). (`A_*.csv`)
4. 🔴 **Las 6 notas de crédito en VES (≈ −2,15 MM VES) y los 5 asientos revertidos:** exigir el soporte/justificación de cada una (especialmente CONSTRUCTORA GRANO AGREGADO −1,6 MM).
5. 🔴 **S00298** (Total 284,61 vs Odoo 0,59) y las **216 órdenes con precio materialmente fuera de toda lista**, comenzando por los ítems industriales de alto valor (códigos 75, 85, 125, 146).
6. 🟡 **Las 152 órdenes "NO CUMPLE" de la validación de descuento** del propio libro.
7. 🟡 **Unificar el maestro de clientes** (14 variantes + 5 pares) antes de cualquier conciliación automática futura; hoy infla descalces.
8. 🟢 **Prerrequisitos para poder auditar lo que hoy NO es verificable:** enlazar despacho↔orden y exportar fecha de entrega; exportar `SerieTasas` y `Vinculaciones`; normalizar marca/categoría por producto; exportar la lista de precios VES con ítems; y separar las series de numeración de facturas y NC.

---

*Generado por auditoría automatizada en modo read-only. Archivos de soporte (scripts reproducibles y CSVs con el detalle caso por caso) en `auditoria_soporte/`.*
