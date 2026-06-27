# Especificación Técnica — Sistema de Cobros y Conciliación de Facturación (Lubrikca)

**Versión:** 1.0 — Plano de implementación
**Contexto:** Reemplazo del flujo Google Forms + IMPORTRANGE por un sistema Odoo ↔ Google Sheets/AppSheet con motor de descuentos determinístico y capa de conciliación de facturación.

---

## 0. Principio rector del diseño

Una sola idea recorre todo el sistema y conviene tenerla presente al implementar cada pieza:

> **El sistema enmarca y marca; el humano revisa lo que se sale del marco.**

Se aplica en tres capas:
- **Descuentos** → el motor calcula el valor que corresponde; nadie lo decide a mano. Administración solo revisa excepciones.
- **Hora del pago** → el sistema contrasta la hora declarada contra el estado de cuenta bancario y marca los desvíos. No se audita el universo, solo lo marcado.
- **Facturación** → el motor calcula lo que la factura *debería* ser; se compara contra lo que Odoo *realmente* facturó. El sistema señala las desviaciones.

En ningún punto un humano carga solo con un número que mueve dinero sin que el sistema lo contraste contra algo verificable.

---

## 1. Arquitectura general

### 1.1 Flujo de extremo a extremo

```
[1] Venta concretada → Orden de Venta en Odoo (precio base más alto / lista BCV o lista especial)
        │
        ▼
[2] Cliente paga (abonos parciales) → Pago "a cuenta" registrado en Odoo por Administración
        │  (Administración coteja el recibo bancario y fija la hora oficial del pago)
        ▼
[3] Sync incremental (delta) → baja a Sheets: clientes, órdenes, líneas, pagos, estado factura
        │  (read-only — espejo de Odoo)
        ▼
[4] Vendedor en AppSheet → vincula el pago confirmado con la Orden de Venta pendiente
        │  (única acción humana de captura que queda en el flujo)
        ▼
[5] Motor de descuentos → proyecta el NETO ESPERADO según ruta de pago (no el nominal).
        │  Provisional durante el cobro; cuando los abonos equivalentes alcanzan el neto
        │  proyectado, marca la orden CANDIDATA A CIERRE y bloquea descuentos condicionales.
        │  neto = precio_lista_que_aplica − Σ(descuentos apilables) − NCs
        ▼
[6] Bandeja de aprobación (AppSheet) → Administración CONFIRMA el cierre y aprueba;
        │  revisa solo excepciones (cierre híbrido: motor proyecta/marca, humano confirma)
        │
        ▼
[7] Facturación MANUAL en Odoo (write-back purista) → Administración aplica el resultado del motor
        │
        ▼
[8] Capa de conciliación → trae de Odoo la factura real + NCs reales y las compara
           contra lo que el motor calculó → semáforo verde/amarillo/rojo
```

### 1.2 Regla de oro de la plomería

El sistema tiene **dos mundos de datos** que nunca se cruzan en escritura:

| Mundo | Tablas | Quién escribe | El sync puede tocarlas |
|---|---|---|---|
| **Espejo de Odoo** | Clientes, OrdenesVenta, LineasOrden, Pagos, EstadoFactura | Solo el sync (delta) | Sí — las refresca |
| **Trabajo humano** | Vinculaciones, BandejaFacturacion/Aprobaciones | El rep / Administración vía AppSheet | **NUNCA** |
| **Auditoría inmutable** | SerieTasas | Solo el scraper, solo append | **NUNCA** (no sobrescribe) |
| **Configuración** | DescuentosMarcaCategoria, ReglasRecurrencia, MetodosPago | Administración (mantenimiento) | No |

Si el sync llegara a escribir en el mundo de trabajo humano, borraría las vinculaciones que el rep llenó. La separación es absoluta.

### 1.3 Dirección del dato (write-back purista)

**Sheets nunca escribe a Odoo.** Odoo es la única autoridad contable. El sistema no aplica facturas ni descuentos en Odoo automáticamente; Administración los aplica a mano, guiada por la bandeja. La capa de conciliación (sección 7) detecta — no previene — los errores de transcripción, lo cual para una capa contable es preferible porque deja registro de la desviación.

---

## 2. Componentes a construir (6 piezas)

| # | Componente | Dónde corre | Función |
|---|---|---|---|
| 1 | **Scraper de tasas** | Railway (Python, horario) | Captura Binance y BCV cada hora → SerieTasas |
| 2 | **Sync incremental delta** | Apps Script o Railway (Python) | Odoo → Sheets por `write_date`, solo filas cambiadas, solo tablas-espejo |
| 3 | **App AppSheet** | AppSheet | Vinculación pago↔orden + bandeja de aprobación |
| 4 | **Motor de descuentos** | Railway (Python) | Calcula el resultado esperado de cada factura → BandejaFacturacion |
| 5 | **Capa de conciliación** | Railway (Python) | Compara motor vs factura real de Odoo → semáforo |
| 6 | **Auditoría tasa vs banco** | Railway (Python) | Importa estado de cuenta, contrasta hora declarada vs real |

Las piezas 1, 2, 4, 5, 6 son procesos backend (no son "Sheets enviando a Odoo"; son procesos aparte que leen/escriben en Sheets y solo **leen** de Odoo). La pieza 3 es la interfaz humana.

---

## 3. Modelo de datos (tablas)

> Convención: `Ref` = referencia a otra tabla. Tipos orientativos para AppSheet/Sheets.

### 3.1 Clientes *(espejo, read-only)*

| Columna | Tipo | Notas |
|---|---|---|
| cliente_id | Text (PK) | ID de Odoo (`res.partner`) |
| nombre | Text | |
| vendedor_email | Email | **Crítico** — debe calzar con el email de login en AppSheet (ver 8.1) |

### 3.2 OrdenesVenta *(espejo, read-only)*

| Columna | Tipo | Notas |
|---|---|---|
| so_id | Text (PK) | `sale.order` |
| cliente_id | Ref → Clientes | |
| fecha | Date | Fecha de la orden |
| fecha_entrega | Date | **Del despacho (`stock.picking`), no de la orden.** Ancla la ventana de contado. El motor no la puede inferir |
| monto_total | Decimal | Total de la orden al precio base con que nació |
| lista_precios | Text | Lista con que se generó la orden (BCV / USD / especial). **El motor la lee, no la asume** |
| vendedor_email | Email | Sincronizar también aquí (evita dereference en security filter) |
| es_primera_compra | Yes/No | Lookup en Odoo: ¿es la primera SO de este cliente? |
| facturada | Yes/No | Estado traído de Odoo |
| factura_id | Text | `account.move` asociado (cuando exista) |
| monto_facturado | Decimal | Monto real de la factura en Odoo (para conciliación) |

### 3.3 LineasOrden *(espejo, read-only)*

Necesaria porque los descuentos por contado son **por marca × categoría**, y eso se resuelve a nivel de línea.

| Columna | Tipo | Notas |
|---|---|---|
| linea_id | Text (PK) | |
| so_id | Ref → OrdenesVenta | |
| producto | Text | |
| marca | Text | ej. Global Oil, Sinoco |
| categoria | Text | ej. Comercial sintéticos, Industrial |
| cantidad | Decimal | |
| precio_unitario | Decimal | Según la lista de la orden |

> **Nota de implementación:** marca y categoría deben venir de Odoo de forma consistente (campos de producto o categorías de producto), porque son la clave del lookup de descuentos. Si hoy no están normalizados en Odoo, normalizarlos es prerequisito.

### 3.4 Pagos *(espejo, read-only)*

| Columna | Tipo | Notas |
|---|---|---|
| pago_id | Text (PK) | `account.payment` (a cuenta) |
| cliente_id | Ref → Clientes | |
| monto | Decimal | Monto del abono en su moneda |
| moneda | Enum | USD / VES |
| metodo_pago | Ref → MetodosPago | |
| fecha_pago | DateTime | Fecha contable del registro en Odoo |
| vendedor_email | Email | |
| saldo_sin_aplicar | Decimal (virtual) | `monto − SUM(Vinculaciones[monto_aplicado] de este pago)` |

### 3.5 MetodosPago *(catálogo, configurable)*

| Columna | Tipo | Notas |
|---|---|---|
| metodo_id | Text (PK) | |
| nombre | Text | |
| moneda | Enum | USD / VES — define la lista que dispara (ver 4.2) |
| tipo_tasa | Enum | BCV / Binance / N\_A — para métodos VES, indica a qué tasa |
| es_contado | Yes/No | Habilita el descuento por contado |

### 3.6 SerieTasas *(auditoría inmutable — APPEND ONLY)*

**La tabla más sensible del sistema.** Una vez escrita una fila, no se sobrescribe jamás. Es la evidencia contra la que se auditan los estados de cuenta.

| Columna | Tipo | Notas |
|---|---|---|
| timestamp | DateTime (PK) | Hora de captura (bucket horario) |
| tasa_bcv | Decimal | BCV del día |
| tasa_binance | Decimal | `(Σ 5 primeras compra + Σ 5 primeras venta) / 10` |
| fuente | Text | URL/endpoint de origen |
| es_heredada | Yes/No | TRUE si el scrape falló y se copió la última registrada (ver 5.3) |
| capturada_ok | Yes/No | FALSE si fue fallback |

### 3.7 DescuentosMarcaCategoria *(CONFIGURABLE — requisito explícito)*

Esta tabla es **el centro de la adaptabilidad del sistema.** Agregar una marca, una categoría o cambiar un porcentaje = agregar/editar una fila. Cero cambios de código. El motor la lee dinámicamente en cada cálculo.

| Columna | Tipo | Notas |
|---|---|---|
| regla_id | Text (PK) | |
| marca | Text | ej. Global Oil, Sinoco. `*` = aplica a todas |
| categoria | Text | ej. Comercial sintéticos, Industrial. `*` = todas las de esa marca |
| tipo_descuento | Enum | contado / (extensible a otros tipos futuros) |
| porcentaje | Decimal | ej. 0.08, 0.06, 0.03 |
| vigencia_desde | Date | **Effective dating** — ver nota |
| vigencia_hasta | Date | Vacío = vigente |
| activo | Yes/No | |

**Ejemplos cargados (los que diste):**

| marca | categoria | tipo | % |
|---|---|---|---|
| Global Oil | Comercial sintéticos | contado | 8% |
| Global Oil | Industrial (pailas/tambores) | contado | 6% |
| Sinoco | * | contado | 3% |

> **Nota crítica — effective dating (vigencia):** Los porcentajes deben tener fecha de vigencia. Si mañana cambias el descuento de Sinoco a 4%, una orden de hace dos semanas debe seguir auditándose con el 3% que regía entonces. El motor selecciona la fila vigente **a la fecha de la orden** (o del abono, según la regla), no la fila activa hoy. Sin esto, la conciliación de órdenes pasadas daría falsos rojos. La resolución de comodines (`*`) sigue prioridad: coincidencia exacta de categoría gana sobre `*`.

### 3.8 ReglasRecurrencia *(configuración)*

| Columna | Tipo | Notas |
|---|---|---|
| condicion | Enum | primera\_compra / recompra |
| tipo_beneficio | Enum | nota\_credito / porcentaje |
| valor | Decimal | Monto fijo de NC (primera compra: caja de liga) o % (recompra: 3%) |
| vigencia_desde / hasta | Date | Mismo principio de effective dating |

### 3.8b Feriados *(configuración — mantenida por Administración)*

Necesaria para calcular la ventana de contado en **días hábiles** (sección 4.6). Sin ella, una semana con feriado decretado calcularía mal el plazo y negaría/aprobaría el contado incorrectamente.

| Columna | Tipo | Notas |
|---|---|---|
| fecha | Date (PK) | Día no hábil |
| descripcion | Text | ej. "1 de mayo", feriado decretado |
| tipo | Enum | nacional / regional / bancario |

> Venezuela tiene feriados frecuentes y a veces decretados con poca antelación. El cálculo de "día hábil" debe saltar sábados, domingos **y** las fechas de esta tabla. Mantener esta tabla al día es responsabilidad operativa, no técnica.

### 3.9 Vinculaciones *(trabajo humano — escribible, el sync NUNCA la toca)*

Tabla puente. Permite que un pago cubra varias órdenes y que una orden reciba varios pagos.

| Columna | Tipo | Notas |
|---|---|---|
| vinc_id | Text (PK) | |
| pago_id | Ref → Pagos | |
| so_id | Ref → OrdenesVenta | |
| monto_aplicado | Decimal | Validado contra saldo del pago y de la orden (ver 8.2) |
| hora_pago_confirmada | DateTime | **La fija Administración** desde bucket de SerieTasas, no texto libre |
| tasa_bcv_aplicada | Decimal | Tasa BCV del bucket horario elegido (estampada, inmutable) |
| tasa_binance_aplicada | Decimal | Tasa Binance del bucket — promedio (5 compra + 5 venta)/10 (estampada, inmutable) |
| es_tasa_heredada | Yes/No | Copiado de SerieTasas para la auditoría |
| equiv_usd_bcv | Decimal (congelado) | Equivalente del `monto_aplicado` a USD vía BCV. Calculado UNA vez, nunca recalculado (ver 3.9b) |
| equiv_usd_binance | Decimal (congelado) | Equivalente del `monto_aplicado` a USD vía Binance |
| equiv_ves_bcv | Decimal (congelado) | Si el abono es en USD: equivalente a VES vía BCV |
| equiv_ves_binance | Decimal (congelado) | Si el abono es en USD: equivalente a VES vía Binance |
| confirmado_por | Email | Sello de quién |
| timestamp_registro | DateTime | Sello de cuándo (Initial value NOW()) |
| estado | Enum | pendiente / aprobado / facturado / conciliado |

### 3.9b Equivalentes congelados y regla de mezcla

Cada abono registra su equivalente en ambas tasas, **calculado una sola vez contra la tasa estampada de su bucket horario** (sección 6) y nunca recalculado. Esto convierte la valoración en dato auditable, no en una cuenta que alguien rehace después.

```
Abono en VES, monto_aplicado = M:
    equiv_usd_bcv     = M / tasa_bcv_aplicada
    equiv_usd_binance = M / tasa_binance_aplicada

Abono en USD, monto_aplicado = M:
    equiv_ves_bcv     = M × tasa_bcv_aplicada
    equiv_ves_binance = M × tasa_binance_aplicada
```

> **Nota dura — una sola definición de tasa, una sola foto:** la tasa Binance de la equivalencia es la misma definición que el resto del sistema (promedio de las 5 primeras de compra + 5 primeras de venta / 10) y se toma del **bucket horario del momento del abono**, no de la tasa de hoy ni de la del registro en Odoo. Si la equivalencia usara otra tasa, habría dos verdades de Binance conviviendo — exactamente la inconsistencia que un sistema antifraude no debe tener. Los cuatro equivalentes se congelan en el momento del abono.

**Regla de mezcla de monedas en una orden:** en teoría una orden se paga toda en una sola ruta. Si hubo mezcla (algún abono en ruta distinta a BCV cuando se proyectaba BCV-completo), el cliente **no** cumplió "completo en BCV" → la orden **migra a la regla VES@Binance** (la más conservadora: no se otorga el descuento BCV-completo). La detección es directa sumando los abonos por moneda/ruta real estampada. Ante la duda, el sistema no regala el mejor descuento.

### 3.10 BandejaFacturacion *(trabajo humano + salida del motor)*

El motor escribe el cálculo; Administración aprueba.

| Columna | Tipo | Notas |
|---|---|---|
| so_id | Ref → OrdenesVenta (PK) | |
| lista_aplicada | Text | Lista que el motor determinó según método de pago |
| precio_base_calculado | Decimal | Suma de líneas a la lista aplicada |
| descuentos_detalle | LongText/JSON | Desglose: cada descuento apilado y su origen |
| total_descuentos | Decimal | |
| ncs_calculadas | Decimal | NCs que corresponden (ej. caja primera compra) |
| total_motor | Decimal | `precio_base − descuentos − ncs` |
| requiere_revision | Yes/No | TRUE si hay algún componente fuera de lo puramente determinístico |
| aprobado_por | Email | |
| estado | Enum | calculado / aprobado / facturado |

### 3.11 Conciliacion *(computada por la pieza 5)*

| Columna | Tipo | Notas |
|---|---|---|
| so_id | Ref → OrdenesVenta (PK) | |
| total_motor | Decimal | De BandejaFacturacion |
| monto_odoo | Decimal | Factura real traída de Odoo |
| ncs_odoo | Decimal | NCs reales asociadas en Odoo |
| diferencia | Decimal | |
| resultado | Enum | verde / amarillo / rojo |
| revisado_por | Email | Para los amarillos/rojos |

---

## 4. Motor de descuentos — lógica

### 4.0 Disparador del motor: neto-objetivo, NO total nominal

**La orden nunca se paga al total nominal.** La brecha entre el nominal (precio más alto con que nació) y lo que el cliente paga *es* el descuento. Esperar a que los abonos sumen el nominal es esperar para siempre. El motor no persigue el nominal: persigue el **neto esperado** según cómo está pagando el cliente.

```
neto_objetivo = precio_lista_que_aplica − Σ descuentos_proyectados − NCs
```

El motor trabaja en dos modos:

- **Provisional (durante el cobro):** proyecta el neto para la ruta de pago en curso. Mientras los abonos equivalentes < neto proyectado, la orden sigue abierta.
- **Final (al cierre):** cuando los abonos equivalentes alcanzan el neto proyectado, la orden se marca **CANDIDATA A CIERRE**, se bloquean los descuentos condicionales y Administración **confirma** (cierre híbrido — sección 4.7).

> **Detección gratis de cartera atascada:** las órdenes que nunca alcanzan ningún neto proyectado = subcobradas = el problema de DSO/cartera vencida. La misma lógica de neto-objetivo las señala sin trabajo extra.

### 4.0b Clasificación de descuentos por dependencia (rompe la circularidad)

Algunos descuentos se conocen al llegar cada abono; otros dependen de que el pago *se complete* o de *cuándo* ocurre. El motor solo puede fijar cada uno cuando su condición es verificable:

| Descuento | Depende de | El motor lo fija |
|---|---|---|
| Reselección de lista (USD/Binance/BCV) | Método/moneda del abono | Al llegar el abono |
| Recompra 3% | Historial del cliente | Al llegar el abono |
| **Contado por marca/categoría** | Completitud **dentro de ventana de tiempo** | Solo al cierre (sección 4.6) |
| **BCV-completo** | Completitud en BCV | Solo al cierre |

**La circularidad del contado** (y de cualquier descuento condicional a completitud): "pagar completo" se mide contra un neto que *incluye* el descuento que se está evaluando. Resolución — el motor proyecta el neto **asumiendo el escenario optimista** (con el descuento condicional aplicado) y deja que la condición (tiempo, en el caso del contado) sea el árbitro:

```
1. Proyectar neto ASUMIENDO contado aplica (escenario optimista, neto más bajo)
2. ¿Abonos equivalentes alcanzan ese neto dentro de [entrega + 3 días hábiles]?
   SÍ → contado confirmado; ese neto es el final
   NO (venció la ventana) → contado negado; la orden pasó de contado a crédito;
        recalcular neto SIN contado (el objetivo sube); perseguir el resto de descuentos
```

El tiempo decide, no el monto. Por eso el contado no se puede fijar antes del cierre.

### 4.1 Fórmula maestra

```
total_factura = precio_lista_que_aplica
              − Σ descuentos_apilables
              − notas_credito
```

Los descuentos **apilan (se suman)**. No hay "el mayor gana".

### 4.2 Paso 1 — Determinar la lista que aplica (reselección, no multiplicación)

El "35%" **nunca entra al cálculo**. Es solo la diferencia promedio que observas entre lista BCV y lista USD. La lista correcta se determina por el método de pago y se lee el **precio real** de esa lista en Odoo:

```
SI metodo_pago.moneda = USD            → lista USD
SI metodo_pago.tipo_tasa = Binance     → lista USD   (VES@Binance recibe precio lista USD)
SI metodo_pago.tipo_tasa = BCV         → lista BCV
```

**Conflicto lista especial vs método de pago → gana el método.** Aunque la orden haya nacido con una lista especial (negociación del supervisor/Administración), la revaluación por método de pago siempre dispara. La lista de nacimiento es solo el techo inicial; el método de pago redefine la lista final.

> Implementación: el motor consulta vía XML-RPC el precio del producto en la `pricelist` que corresponde, NO multiplica por 0.65 ni divide por 1.35. Esto elimina la ambigüedad aritmética (×0.65 ≠ ÷1.35) y usa el precio que Odoo ya tiene cargado.

### 4.3 Paso 2 — Descuentos apilables

Se evalúan todos y se suman los que apliquen:

**a) Recurrencia** (de ReglasRecurrencia, vigente a la fecha de la orden):
- `es_primera_compra = TRUE` → NC por caja de liga de frenos (monto fijo, va al término de NCs, no a %)
- `es_primera_compra = FALSE` (recompra) → 3%

**b) Contado por marca × categoría** (de DescuentosMarcaCategoria, vigente a la fecha):
- Requiere DOS condiciones: método con `es_contado = TRUE` **Y** liquidación total dentro de la ventana de tiempo (sección 4.6)
- Se resuelve **por línea**: cada línea aporta su % según (marca, categoria); el descuento total de contado es el ponderado de las líneas. Resolución de comodines: categoría exacta > `*`.
- Ej.: Global Oil sintético = 8%; Global Oil industrial = 6%; Sinoco = 3%
- **Condicional a completitud** → el motor lo proyecta provisional pero solo lo confirma al cierre (ver 4.0b y 4.6)

**c) BCV-completo** (el único calculado, no tabulado):
- Aplica si paga **completo en VES a tasa BCV** — TODOS los abonos de la orden en ruta BCV
- `descuento = f(tasa_bcv, tasa_binance)` = función de la **diferencia diaria** entre BCV y Binance
- Se calcula **por abono** (cada abono con la tasa Binance de su hora estampada), no sobre el total a una sola tasa
- **Mezcla → migración:** si algún abono fue en ruta distinta a BCV, la orden no cumple "completo en BCV" y migra a la regla VES@Binance, perdiendo este descuento (ver 3.9b)

### 4.4 Paso 3 — Valoración por abono (clave del cross-currency)

Una factura se paga en **abonos parciales en el tiempo**, cada uno con una tasa Binance distinta (Binance varía por hora, a veces >3%). El valor pagado equivalente se obtiene **sumando los equivalentes ya congelados** de cada abono (sección 3.9b), no recalculando:

```
valor_pagado_equivalente_usd = Σ equiv_usd_<ruta>_i   (ya congelados por abono)
```

No se valora la factura entera con una sola tasa final, ni se recalcula al cierre. Cada abono ya carga su equivalente estampado contra la tasa de **su** momento (secciones 3.9b y 6). Esto es además lo contablemente correcto y deja rastro de auditoría.

### 4.5 Ejemplos de apilamiento (verificación de la regla)

| Caso | Cálculo | Total dcto |
|---|---|---|
| Sinoco, recompra, contado | 3% (recompra) + 3% (Sinoco contado) | **6%** |
| Global Oil sintético, recompra, contado | 3% + 8% | **11%** |
| Global Oil industrial, recompra, contado | 3% + 6% | **9%** |
| Cualquiera, primera compra, contado, paga BCV completo | NC caja + %contado + %BCV-completo | NC + suma |

### 4.6 Ventana de contado — días hábiles

El contado exige **liquidación total** dentro de:

```
ventana = [fecha_entrega, fecha_entrega + 3 días HÁBILES]
```

- El ancla es la **fecha de entrega** (despacho), no la fecha de la orden.
- Ejemplo (el tuyo): orden generada jueves, entrega viernes → vence **miércoles** (viernes + 3 hábiles, saltando sábado/domingo).
- "Día hábil" salta sábados, domingos **y feriados** (tabla 3.8b). Un cálculo que solo salte fines de semana falla en semanas con feriado decretado.
- **Alcance: liquidación total**, no por abono. La orden completa debe alcanzar el neto-objetivo (con contado) dentro de la ventana. Si se arrastra más allá, dejó de ser contado → pasa a crédito y pierde el descuento de contado (conserva los demás que apliquen).

### 4.7 Cierre híbrido

Cuando los abonos equivalentes alcanzan el neto proyectado:

```
1. MOTOR: marca la orden "candidata a cierre", bloquea descuentos condicionales,
          escribe el cálculo final en BandejaFacturacion
2. HUMANO (Administración): CONFIRMA el cierre y aprueba
```

El motor proyecta y marca; **el humano confirma**. Los descuentos condicionales (contado, BCV-completo) no se auto-fijan sobre una proyección que aún puede revertirse — solo se bloquean cuando la condición se cumplió y un humano lo valida. Esto evita facturar sobre un neto optimista que el cliente nunca alcanzó.

---

## 5. Scraper de tasas (pieza 1)

### 5.1 Definición de la tasa

```
tasa_binance = (suma de las 5 primeras tasas de COMPRA
              + suma de las 5 primeras tasas de VENTA) / 10
```

> **Decisión a confirmar antes de producción:** este promedio compra+venta es un punto medio. Verifica que sea el sesgo que quieres, porque define sistemáticamente si el diferencial cae a tu favor o del cliente. Es una decisión de negocio, no técnica.

### 5.2 Frecuencia

**Cada hora.** No diaria. Porque el descuento BCV-completo depende de la diferencia BCV-Binance y Binance se mueve intradía (>3% en una hora a veces). Una foto diaria no captura ese movimiento y mueve plata mal.

### 5.3 Fallback (tu regla, con el matiz necesario)

```
SI el scrape de la hora H falla:
    usar la última tasa registrada (la de H−1)
    marcar la fila: es_heredada = TRUE, capturada_ok = FALSE

SI fallan 3 capturas consecutivas:
    generar ALERTA (Telegram al equipo / email a Administración)
```

> **Matiz importante:** los abonos valorados con una tasa heredada heredan también la bandera `es_tasa_heredada = TRUE` en Vinculaciones. En la auditoría contra estado de cuenta, esos abonos se revisan **primero**, porque si Binance se movió en la hora muerta, su descuento BCV-completo salió con una tasa que no era la real de ese momento. No bloquea la operación; solo los prioriza para revisión.

### 5.4 Inmutabilidad

El scraper hace **solo append** a SerieTasas. El sync incremental NO toca esta tabla. Es registro de auditoría permanente.

---

## 6. Sello de hora del pago

### 6.1 Quién y cómo

- El **vendedor** vincula la orden con el pago (AppSheet).
- **Administración** fija la hora oficial del pago al confirmar, cotejando contra el **recibo bancario** que el vendedor presenta.
- La hora se **selecciona del bucket horario de SerieTasas** (lista desplegable de horas capturadas), **no se teclea libre**. Esto impide horas inventadas que no existen en la serie y ata el descuento a una tasa que el sistema capturó.
- La tasa de ese bucket (BCV y Binance) queda **estampada e inmutable** en `Vinculaciones.tasa_bcv_aplicada` y `Vinculaciones.tasa_binance_aplicada`, y de ahí se congelan los cuatro equivalentes (sección 3.9b).

### 6.2 Por qué Administración y no el vendedor

La hora declarada es el **único input humano que mueve plata directamente** que queda en el sistema (todo lo demás está blindado por tablas). Mantener la separación de funciones — el que cobra no fija el número que determina el descuento — preserva el control que el resto del diseño ya tiene.

### 6.3 Verificación posterior (pieza 6)

La hora declarada es una *afirmación* anclada al recibo. La verificación real ocurre contra el estado de cuenta:
- Se importa el extracto bancario.
- El sistema compara `hora_pago_confirmada` (declarada) vs hora real del banco.
- **Solo saltan** las que difieren más de un umbral (ej. 1 hora, o las que cruzan un swing de tasa > X%).
- Se auditan las excepciones, no el universo.

---

## 7. Capa de conciliación de facturación (pieza 5)

Reemplaza al write-back con algo más fuerte para una capa contable: **detección de desviaciones.**

```
Trae de Odoo:  estado (facturada?), monto_facturado, NCs asociadas
Compara contra: total_motor de BandejaFacturacion

Resultado:
  |diferencia| ≈ 0                → VERDE   (cuadra, no se hace nada)
  |diferencia| ≤ tolerancia_redondeo → AMARILLO (ojo, revisar)
  |diferencia| > tolerancia        → ROJO    (se facturó distinto a lo calculado: error o desviación)
```

Esto da control sobre la facturación **sin escribir nada a Odoo**: el motor dice lo que la factura debería ser, Odoo dice lo que fue, el sistema marca la brecha. Para los rojos queda registro de la desviación (mejor que prevenir, porque es auditable).

---

## 8. Riesgos de implementación a resolver primero

### 8.1 Mapeo de identidad (bloqueante)

`USEREMAIL()` en los security filters de AppSheet exige que **el email de login del rep = `vendedor_email` en Odoo**. Si ese mapeo no es consistente, los filtros fallan en silencio (el rep no ve nada, o lo ve todo). **Verificar antes que nada.**

### 8.2 Validaciones que sobreviven en AppSheet

Aunque el pago nace en Odoo, la **vinculación** conserva validaciones:

- Dropdown de orden — `Valid_If` que solo muestra órdenes del mismo cliente del pago, con saldo:
  ```
  FILTER("OrdenesVenta",
    AND([cliente_id] = [_THISROW].[pago_id].[cliente_id],
        [facturada] = FALSE))
  ```
- Monto aplicado — no excede saldo del pago ni de la orden:
  ```
  AND([monto_aplicado] > 0,
      [monto_aplicado] <= [pago_id].[saldo_sin_aplicar],
      [monto_aplicado] <= saldo_pendiente_orden([so_id]))
  ```
- Scoping — security filter en tablas-espejo: `[vendedor_email] = USEREMAIL()`
- Sellos — `confirmado_por` con Initial value `USEREMAIL()`, read-only; `timestamp_registro` con `NOW()`

### 8.3 Normalización de marca/categoría en Odoo (prerequisito del motor)

Los descuentos por contado dependen de leer marca y categoría consistentes desde Odoo. Si hoy no están normalizadas a nivel de producto, normalizarlas es paso previo al motor.

### 8.4 Effective dating en tablas de configuración

DescuentosMarcaCategoria y ReglasRecurrencia deben usar vigencia (fecha desde/hasta). Sin esto, cambiar un descuento rompe la conciliación de órdenes anteriores con falsos rojos.

### 8.5 Robustez del scraper

P2P de Binance es frágil (cambios de DOM/API, ratelimit, outages). Resolver el fallback (sección 5.3) **antes** de producción, no cuando falle.

---

## 9. Orden de construcción sugerido

1. **Tablas de configuración** (DescuentosMarcaCategoria, ReglasRecurrencia, MetodosPago) — son datos, se cargan ya, y extraer la tabla de descuentos completa es la mitad del blindaje antifraude.
2. **Scraper de tasas** + SerieTasas — independiente, se puede validar solo.
3. **Sync incremental delta** — modificación del sync existente a `write_date > última_corrida`, solo tablas-espejo.
4. **App AppSheet** — vinculación + security filters + validaciones (verificar 8.1 primero).
5. **Motor de descuentos** — consume todo lo anterior.
6. **Capa de conciliación** + **auditoría de tasa vs banco** — cierran el control.

---

## 10. Lo que este diseño elimina vs. el sistema actual

| Sistema actual (roto) | Sistema nuevo |
|---|---|
| Pago auto-reportado en form (sin ancla al dinero real) | Pago nace confirmado en Odoo |
| Sin validación factura↔cliente | Dropdown filtrado por cliente/orden |
| Sin validación monto ≤ saldo | `Valid_If` contra saldos reales |
| Sin scoping por vendedor | Security filter `USEREMAIL()` |
| Descuentos calculados a mano, orden por orden | Motor determinístico con neto-objetivo; Administración confirma cierre y revisa excepciones |
| Tasa Binance tecleada manual | Serie horaria scrapeada, estampada por abono |
| Reglas de descuento en la cabeza del GM | Tablas configurables con vigencia |
| Sin verificación de la facturación | Capa de conciliación motor vs Odoo (semáforo) |
| IMPORTRANGE + reescritura total frágil | Sync delta que respeta el trabajo humano |
