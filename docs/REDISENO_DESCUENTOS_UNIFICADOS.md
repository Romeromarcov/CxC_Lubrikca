# Rediseño CxC_Lubrikca — Descuentos unificados, `requiere_pago_previo`, Ventas/Auditoría, Configuración en subpáginas

Estado: Tarea 1 implementada y probada. Tarea 2 cerrada por hallazgo de
auditoría (no había nada que unificar; el flujo reactivo Cobranza→Ventas
queda diseñado, no implementado). Tarea 3a/3b implementadas y probadas
(equivalente/teóricos por lista en `BandejaFacturacion`); 3c–3g diseñadas.
Tarea 4 implementada (`venta_bruta_teorica_auditoria` en
`GET /api/auditoria`, derivada de 3a/3b + IVA, sin UI todavía). Queda
pendiente: la reorganización de Configuración en subpáginas (diseñada, no
implementada) y lo detallado en "Dependencias abiertas".

## Tarea 1 — `requiere_pago_previo` en reglas de descuento (IMPLEMENTADA)

### Qué se hizo

- **Modelo** (`src/cxc/models.py`): campo `requiere_pago_previo: bool` agregado
  a las 8 dataclasses de reglas de descuento que sí tienen tabla en DB:
  `DescuentoProntoPago`, `DescuentoVolumen`, `DescuentoBCVCompleto`,
  `PromocionPrimeraCompra`, `DescuentoRecompra`, `DescuentoProducto`,
  `DescuentoDiferencialCambiario`, `ReglaRecurrencia`.
- **Clasificación aplicada** (una por una, según pide la tarea):

  | Regla | `requiere_pago_previo` | Motivo |
  |---|---|---|
  | `DescuentoProntoPago` (pronto pago) | `True` | Es, por definición, un descuento sobre el abono. |
  | `DescuentoBCVCompleto` (diferencial cambiario **real**, el que sí vive en el motor) | `True` | Se calcula por abono, sobre la tasa congelada de ese abono. |
  | `DescuentoDiferencialCambiario` (config "diferencial cambiario", **huérfana** — ver Tarea 2) | `True` | Aunque el motor no la lee hoy, conceptualmente es la misma familia que BCV-completo. |
  | `DescuentoVolumen` | `False` | Depende de la cantidad de la orden, no de pagos. |
  | `DescuentoRecompra` / `ReglaRecurrencia` (legado) | `False` | Depende del historial de compras del cliente. |
  | `PromocionPrimeraCompra` | `False` | Depende de si es la primera compra del cliente. |
  | `DescuentoProducto` (huérfana — ver Tarea 2) | `False` | Depende del producto/orden, no de pagos. |

- **DB/migración** (Postgres, backend de producción): `alembic/versions/e5f6a7b8c9d0_add_requiere_pago_previo.py`
  agrega la columna `requiere_pago_previo BOOLEAN NOT NULL` con
  `server_default` según la tabla (true para pronto-pago/bcv-completo/diferencial,
  false para el resto). **Falta aplicar** esta migración en cada entorno
  (`alembic upgrade head`) — no se ejecutó en este cambio porque no hay una
  base de datos Postgres disponible en este sandbox de desarrollo.
- **Repositorio Sheets** (backend legado, aún seleccionable vía
  `REPO_BACKEND=sheets`): `src/cxc/sheets/serde.py` actualizado en todos los
  `to_row`/`from_row` relevantes, con default por compatibilidad hacia atrás
  (`r.get("requiere_pago_previo", "TRUE"/"FALSE")`) para hojas existentes que
  aún no tengan la columna.
- **Motor** (`src/cxc/engine/discounts.py`): nueva función pura
  `_filtrar_por_pago_previo(reglas, tiene_pago)` que excluye toda regla con
  `requiere_pago_previo=True` cuando `EngineInputs.abonos` está vacío. Se
  aplica a las 6 listas de reglas que el motor sí consume:
  `descuentos` (pronto pago), `descuentos_volumen`, `descuentos_recompra`,
  `promociones_primera_compra`, `reglas_recurrencia`, `descuento_bcv_diario`.
  Reglas sin el atributo (compatibilidad) se tratan como `False`.
- **UI/API de Configuración** (`src/cxc/web/app.py`): el campo se expone en
  los `GET`/`POST` de pronto-pago, volumen (ambos duplicados de la ruta —
  ver "Hallazgo no contemplado" abajo), recompra, producto y diferencial
  cambiario, con default acorde a la tabla anterior. **No** se tocó el HTML
  (`index.html`) para agregar el checkbox visualmente — eso queda para
  cuando se aborde la Tarea 3e / el rediseño de Configuración, evitando
  tocar la UI dos veces.
- **Tests**: `tests/test_requiere_pago_previo.py` (6 casos): filtro puro
  con/sin pago, contado con flag `True` con y sin abonos, volumen con flag
  `True`/`False` sin abonos. Todos verdes; suite completa sin regresiones
  (2 fallos preexistentes en `test_e2e_production_readiness.py` por falta de
  `BINANCE_P2P_URL` en este sandbox — confirmado que fallan igual en `HEAD`
  sin mis cambios).

### Por qué el motor "ya hacía esto" en la práctica, pero no de forma declarativa

La investigación (ver Tarea 2) encontró que `contado_evaluable` y el bloque
BCV-completo en `discounts.py` ya sólo se evalúan `if inp.abonos`. Es decir,
la política que pide la Tarea 1 ya existía **estructuralmente hardcodeada**
para esas dos familias. Lo que faltaba es que la exclusión fuera **por
regla** (dato de configuración), no una decisión fija en el código — así una
regla de pronto-pago específica podría, en el futuro, marcarse
`requiere_pago_previo=False` (p.ej. un descuento por adelantado sin abono
previo) sin tocar el motor. La implementación de `_filtrar_por_pago_previo`
formaliza esa política sin cambiar el comportamiento observable actual
(porque los defaults coinciden con el comportamiento previo).

---

## Tarea 2 — Unificar lógica de descuentos en Ventas

### Hallazgo de auditoría (cambia el alcance de esta tarea)

Se auditó exhaustivamente `/api/ventas`, `/api/auditoria`, `/api/reporte-saldos`,
`src/cxc/audit/hour_audit.py` y `src/cxc/reconciliation/reconcile.py`
buscando cálculo de descuentos duplicado fuera de `src/cxc/engine/discounts.py`.

**No existe duplicación de la lógica de reglas de descuento fuera del
motor.** Todas las páginas (Ventas, Cobranza/reporte-saldos, Auditoría)
leen `BandejaFacturacion` (`total_motor`, `precio_base_calculado`,
`total_descuentos`, `descuentos_detalle`) — el output ya calculado por
`calcular_factura()` — y sólo le agregan encima:

- Comparación contra Odoo (facturado real, NC) — no recalcula descuentos.
- Aplicación de impuestos (IVA/IGTF) sobre el neto ya calculado por el motor.
- Formato/agrupación para KPIs.

Es decir: **Ventas (vía `BandejaFacturacion`) ya es la única fuente del
cálculo de descuentos.** No hay nada que "extraer" del reporte de CxC porque
el reporte de CxC nunca calculó descuentos por su cuenta — sólo compara.

### Lo que sí falta para que la Tarea 2 esté completamente resuelta

1. **Flujo bidireccional Ventas↔Cobranza (recálculo, no solo lectura
   inicial).** Hoy `EngineRunner.run_orden`/`run_all` (`runner.py`) se
   invocan on-demand (recálculo manual/batch), no automáticamente cuando se
   crea/actualiza una `Vinculacion`. Con `requiere_pago_previo` ya
   implementado, un pago nuevo puede activar reglas que antes estaban
   excluidas (p.ej. pronto pago, BCV-completo) — el diseño correcto es:
   - Al crear/aprobar una `Vinculacion` (Cobranza), encolar/disparar
     `EngineRunner.run_orden(so_id)` para esa orden específica, para que
     `BandejaFacturacion` se recalcule con el nuevo `abonos` no vacío.
   - **No implementado en este cambio** — es un enganche de trigger
     (Cobranza → motor) que toca el flujo de guardado de vinculaciones en
     `web/app.py`; se documenta aquí como diseño pendiente para no violar
     la regla de oro sin antes acordar dónde vive el trigger (side-effect
     síncrono en el request de Cobranza vs. job asíncrono).
   - Respeta la regla de oro: el trigger dispara un recálculo del motor
     (que sólo escribe `BandejaFacturacion`, tabla de "trabajo humano +
     output del motor"), nunca escribe a Odoo ni a `Vinculaciones` desde el
     lado del motor.

2. **`DescuentoDiferencialCambiario` y `DescuentoProducto` están
   huérfanas** (modeladas, con UI, pero el motor nunca las lee — usa
   `DescuentoBCVCompleto` para diferencial cambiario y no tiene concepto de
   "producto" en absoluto). Esto **no es duplicación** pero sí una
   desalineación entre Configuración y el motor que cualquier trabajo sobre
   "Ventas como fuente única" debe tener presente: si se decide que estas
   reglas deben pasar a ser reales, hay que cablearlas en `EngineInputs`/
   `_calcular_componentes` — trabajo no incluido en este cambio (fuera del
   alcance de la Tarea 1, que sólo pedía clasificar el flag en el modelo).

### Conclusión de la Tarea 2

Cerrada en su objetivo original ("Ventas es la única fuente, las demás
páginas consumen su resultado") — ya era así. Abierto: el recálculo
reactivo Cobranza→Ventas (arriba) y la desalineación Config↔Motor en 2
tipos de regla (documentado, no resuelto).

---

## Tarea 3 — Nuevas columnas en tabla Ventas

### 3a/3b — IMPLEMENTADAS

- `BandejaFacturacion` gana 5 campos nuevos: `equivalente_lista_usd`,
  `teorico_lista_ves`, `teorico_lista_usd`, `descuentos_teorico_ves`,
  `descuentos_teorico_usd` (`src/cxc/models.py`).
- El motor los calcula en `calcular_factura()` vía la nueva función pura
  `_teoricos_por_lista()` (`src/cxc/engine/discounts.py`), que **reutiliza
  `_calcular_componentes()`** —la misma función que produce el neto real—
  corriéndola una vez contra la lista VES vigente y otra contra la lista USD
  vigente (respetando la excepción de Lista Histórica de Auditoría, ya que
  `_precio_unitario_linea` se usa sin cambios). No hay cálculo de descuentos
  nuevo ni duplicado: es la misma lógica, con la lista forzada.
  - Si el catálogo no tiene precio para una de las dos listas (p. ej. datos
    de prueba parciales), se degrada a `0` en vez de propagar `KeyError`
    (mismo patrón que ya usaba `precio_target_usd` en el código existente).
- `equivalente_lista_usd`: "igual si nació en USD, teórico si nació en VES"
  se resuelve siempre como el teórico contra la lista USD vigente (que
  coincide con el valor real cuando la orden efectivamente nació ahí).
- Migración: `alembic/versions/f6a7b8c9d0e1_add_bandeja_teoricos_lista.py`
  agrega las 5 columnas `NUMERIC(18,4) NOT NULL DEFAULT 0` a
  `bandeja_facturacion`. Verificada de punta a punta contra un Postgres real
  (`alembic upgrade head` limpio).
- Repositorio Sheets (legado) actualizado en `serde.bandeja_to_row`/`bandeja_from_row`.
- `/api/ventas` expone `equivalente_lista_usd`, `teorico_lista_ves`,
  `teorico_lista_usd` en cada fila — lectura directa de `BandejaFacturacion`,
  sin recalcular.
- Tests: `tests/test_teoricos_por_lista.py` (orden nacida en USD, orden
  nacida en VES, catálogo parcial sin romper el cálculo).

### 3c–3g — DISEÑO (no implementado)

Estado actual de `/api/ventas` (`src/cxc/web/app.py:~6800-7005`) y su tabla:
Orden, Cliente, Vendedor, Fecha, Venta Bruta Teórica, Bruta Teórica + IVA,
Venta Neta Teórica, Neta Teórica + Imp., Venta Bruta Real, Venta Neta Real,
Facturado antes Imp., Facturado con Imp., Facturado Neto, Diferencia, Alerta.

Diseño de las columnas pedidas, en el orden de la tarea:

| # | Columna | Fuente de datos | Notas de diseño |
|---|---|---|---|
| a | Equivalente lista USD | Si `orden.lista_precios == lista_usd`: mismo valor que "Venta Bruta Teórica". Si nació en VES: `Σ price_resolver.precio(producto, lista_usd, fecha_orden) * cantidad` — **mismo cálculo que ya hace el motor internamente** (`precio_target_usd` en `discounts.py:503-509`) para el "Equiparación Binance". Reutilizar esa lógica, no reimplementarla — exponer `precio_target_usd` como un campo nuevo de `BandejaFacturacion` (p.ej. `equivalente_lista_usd`) para que Ventas lo lea, no lo recalcule. |
| b | Teóricos VES/USD vigentes | Igual patrón: el motor ya resuelve precio por lista y fecha (`_precio_linea`) respetando la excepción de Lista Histórica de Auditoría (`orden_es_historica`/`historical_price_map`, ya implementada). Exponer dos campos nuevos en `BandejaFacturacion`: `teorico_lista_ves`, `teorico_lista_usd`, calculados una vez en `calcular_factura()` reutilizando `_precio_linea` con `lista_bcv`/`lista_usd` fijas (no la lista aplicada real de la orden). |
| c | Descuentos aplicados (orden/factura) + validación visual | "Aplicados en orden" = lo que Odoo trae en `sale.order.line.discount` (ya se lee para otros propósitos). "Aplicados en factura" = `account.move.line.discount` / notas ya reflejadas en Odoo. La "validación visual" es una comparación contra `BandejaFacturacion.descuentos_detalle` (lo que el motor dictamina) — **esto ya es, conceptualmente, lo que hace `discount_audit.py`** (`auditar_descuento_orden`/`auditar_descuento_factura`). Diseño: exponer el resultado de `discount_audit` como 2 columnas nuevas en la fila de Ventas en vez de sólo en la pestaña de Auditoría — no duplicar el cálculo, sólo mostrarlo en otra vista. |
| d | Descuentos pendientes de aplicar | `BandejaFacturacion.total_descuentos` (lo que el motor dictamina) menos lo que ya está aplicado en Odoo (columna c). Cálculo derivado, no nueva lógica de descuento — se apoya en (c). |
| e | Descuentos aplicados desde el sistema (uso interno, posteriores a orden/factura en Odoo) | **Dependencia abierta explícita** (ver más abajo) — depende de una lógica de Facturación aún no construida. Se deja el campo modelado (columna nullable en la respuesta de `/api/ventas`, valor `null`/`"pendiente"` hasta que exista esa lógica) pero **no se implementa el cálculo** en este cambio. |
| f | N/C (reutilizar lógica existente) y N/D (nueva) atadas a la factura | N/C: ya se lee de Odoo `account.move` con `move_type=out_refund` y se compara en `/api/auditoria` y `reconcile.py` — reutilizar esa función tal cual. N/D: **no existe hoy ninguna lectura de notas de débito** (`move_type=out_invoice` con referencia a otra factura, o el tipo que Odoo use en esta instancia — a confirmar en `docs/ODOO_MAPEO.md`, que no documenta N/D). Diseño: nueva función `odoo_notas_debito(so_id_or_factura_id)` en el mismo módulo/patrón que ya lee N/C, de solo lectura (Odoo nunca se escribe). |
| g | Facturado en Odoo − N/C + N/D | Cálculo derivado puro de (f), sin nueva lógica de descuento. |

**Regla de diseño transversal para toda la Tarea 3:** ninguna columna nueva
debe calcular un descuento — todas son (1) lecturas directas de Odoo, (2)
campos ya calculados por el motor pero no expuestos todavía, o (3)
comparaciones/derivaciones aritméticas entre (1) y (2). Esto es lo que exige
la restricción "prohibido duplicar cálculo de descuentos fuera de Ventas".

**No implementado en este cambio** — requiere: (i) agregar los campos
nuevos a `BandejaFacturacion` + migración Alembic para `bandeja_facturacion`
(columnas `equivalente_lista_usd`, `teorico_lista_ves`, `teorico_lista_usd`),
(ii) tocar `calcular_factura()` para poblarlos, (iii) tocar `/api/ventas` y
la tabla HTML para mostrarlos, (iv) construir la lectura de N/D desde Odoo.
Volumen de trabajo estimado: comparable a la Tarea 1, pero toca la tabla más
usada del sistema (Ventas) — se recomienda hacerlo en un cambio separado con
su propia revisión, no agregado apresuradamente a este.

---

## Tarea 4 — Cálculos de auditoría de venta bruta teórica (IMPLEMENTADA)

Con (3a)/(3b) ya calculando `teorico_lista_ves`/`teorico_lista_usd`/
`descuentos_teorico_ves`/`descuentos_teorico_usd` en `BandejaFacturacion`,
los cálculos de la Tarea 4 son puramente derivados (aritmética, sin ningún
descuento nuevo) y se exponen en un campo nuevo del endpoint
`GET /api/auditoria`: `venta_bruta_teorica_auditoria` (lista, una entrada
por orden con bandeja calculada), con esta forma:

```json
{
  "so_id": "S00123",
  "lista_ves": {
    "bruta_teorica": 3600.00,           // a.1 sin impuestos (= teorico_lista_ves)
    "bruta_teorica_mas_iva": 4176.00,   // a.1 + IVA
    "neta_teorica": 3564.00,            // a.2 = teorico_lista_ves - descuentos_teorico_ves
    "neta_teorica_mas_iva": 4134.24     // a.3 = a.2 + IVA
  },
  "lista_usd": { "...": "idéntico con teorico_lista_usd/descuentos_teorico_usd (b.1-b.3)" },
  "venta_real": {
    "orden_total": 1000.00,             // c: OrdenVenta.monto_total (real, Odoo)
    "factura_neto": 970.00              // c: BandejaFacturacion.total_motor (neto real del motor)
  }
}
```

- **a.1/b.1**: `teorico_lista_ves`/`teorico_lista_usd` (ya calculados por el
  motor, Tarea 3a/3b) `* (1 + iva_rate)` — misma tasa (`config.engine.iva_rate`)
  que usa `/api/ventas` para sus columnas "+ IVA".
- **a.2/b.2**: `teorico_lista_ves − descuentos_teorico_ves` (idéntico para
  USD) — el campo `descuentos_teorico_ves`/`_usd` ya es la salida dinámica
  de `_calcular_componentes()` corrida con la lista forzada (Tarea 3a/3b);
  se recalcula solo cuando el motor recalcula la orden (p. ej. tras un pago
  nuevo), nunca es un número fijo.
- **a.3/b.3**: `(a.2)/(b.2) * (1 + iva_rate)`.
- **c**: `venta_real` — `OrdenVenta.monto_total` (real, espejo de Odoo) y
  `BandejaFacturacion.total_motor` (neto real ya calculado por el motor),
  sin recalcular nada — mismo dato que ya usa `/api/ventas`.
- **Dónde vive el cálculo**: `get_auditoria()` en `src/cxc/web/app.py`
  (bloque nuevo justo antes del `return`, no toca la lógica de discrepancias
  existente) sólo lee `BandejaFacturacion` + aplica IVA — no llama al motor
  ni duplica ninguna regla de descuento, cumpliendo la restricción
  "prohibido duplicar cálculo de descuentos fuera de Ventas".
- **No implementado**: IGTF sobre estos totales (el `/api/ventas` existente
  sólo le aplica IGTF a la "venta neta teórica + impuestos", que depende de
  si el pago fue en efectivo — un dato por-abono que no aplica de la misma
  forma a un teórico "¿y si esta orden fuera 100% VES/100% USD?"; se dejó
  fuera para no inventar una semántica de IGTF no pedida explícitamente).
  Tampoco se agregó UI (tabla/gráfico en `index.html`) para mostrar esta
  nueva sección de Auditoría — el campo está en la API, listo para
  consumirse, pero la pestaña de Auditoría no lo renderiza todavía.
- **Tests**: no se agregó un test de integración para `get_auditoria()`
  (función de ~500 líneas sin tests previos, requeriría mockear Odoo/repo
  extensivamente) — el cálculo derivado en sí reutiliza exclusivamente
  campos de `BandejaFacturacion` ya cubiertos por
  `tests/test_teoricos_por_lista.py`; el riesgo remanente es solo el
  cableado de lectura + aritmética de impuestos en `web/app.py`, módulo
  explícitamente excluido de mypy estricto y del gate de cobertura del
  repo (ver `pyproject.toml`).

---

## Diseño de IA de Configuración en subpáginas (DISEÑO, no implementado)

### Estado actual

`Configuración` es **una sola página larga** (`index.html`, `tab-configuracion`)
con ~14 secciones `<section class="card">` en scroll continuo, cada una con
su propio mini-CRUD y su propio par de endpoints `/api/config/...`. No hay
subpáginas ni navegación lateral.

### Propuesta de subpáginas

Agrupación por dominio, manteniendo cada endpoint `/api/config/...`
existente sin cambios (la reorganización es de navegación/IA, no de API):

1. **Descuentos** (la más grande — agrupa las 8 secciones de reglas):
   - Pronto Pago (Días de Gracia) — incluye ahora el toggle
     `requiere_pago_previo`.
   - Volumen.
   - Recompra/Recurrencia.
   - Obsequios y Promociones (primera compra).
   - Producto/Marca/Categoría *(marcar visualmente como "sin efecto en el
     motor todavía" — ver Tarea 2)*.
   - Diferencial Cambiario *(marcar visualmente como "sin efecto en el motor
     todavía, usa Matriz Consolidada/BCV-completo en su lugar")*.
   - Matriz Consolidada (vista de solo lectura, sección informativa).
   - Exclusiones Mutuas.
   - Sub-navegación por tabs internas (no accordion) dado el volumen.
2. **Usuarios** — gestión de `usuarios_plataforma` (alta/roles/activo). Hoy
   vive fuera de `Configuración` (revisar si ya existe en otra pestaña del
   admin — no se encontró un CRUD web de usuarios en la investigación; si no
   existe, es trabajo nuevo, no sólo reubicación).
3. **Listas de Precio** — "Listas de Precios Importadas de Odoo" (hoy
   sección de solo lectura dentro de Configuración) + la futura UI de
   "Lista Histórica de Auditoría" si se expone edición.
4. **Tasas de Cambio** — sección ya existente ("Tasas de Cambio: Odoo Sync +
   Manual").
5. **Feriados** — sección ya existente.
6. **Catálogo Odoo** — "Auditoría de Clientes y Recurrencia" + "Catálogo de
   Productos y Precios en Odoo" (ambas de solo lectura, informativas).
7. **Recálculo del Motor** — botón/acción "Forzar Recálculo Completo".

### Por qué no se implementó en este cambio

Reorganizar la IA implica: (i) agregar navegación lateral/tabs en
`index.html`+`app.js` (cambio de UI transversal, con riesgo de romper JS ya
enganchado a IDs de sección existentes), (ii) decidir si las rutas
`/configuracion/descuentos`, `/configuracion/usuarios`, etc. son URLs reales
o anclas de una SPA — decisión de producto que no estaba definida antes de
este cambio. El `/loop` pide diseñar antes de tocar UI (paso 2 del orden
secuencial) — este documento es ese diseño; la implementación queda
pendiente y se recomienda como su propio cambio, después de validar esta
propuesta con el usuario final del panel.

---

## Caso no contemplado (registrado explícitamente, no resuelto implícitamente)

**Duplicación de rutas `/api/config/descuentos-volumen` (GET y POST).**
Durante la implementación de la Tarea 1 se encontró que
`src/cxc/web/app.py` registra **dos veces** la misma ruta
`GET/POST /api/config/descuentos-volumen` (una vez usando
`VolumenRequest`/`get_config_volumen`, otra usando
`DescuentoVolumenRequest`/`get_config_descuentos_volumen`). En FastAPI
(Starlette), cuando dos rutas idénticas (mismo path + método) se registran,
la **primera registrada gana** — la segunda definición nunca se ejecuta en
producción. Esto no estaba pedido en el `/goal` y no se resolvió aquí (no es
parte del alcance de "requiere_pago_previo"), pero se actualizó el campo
nuevo en **ambas** definiciones para no dejar una rama muerta sin el flag.
Se registra aquí para que quede explícito y no se pierda: alguien debe
decidir cuál de las dos implementaciones es la "correcta" y eliminar la
otra en un cambio aparte.

---

## Dependencias abiertas

1. **Recálculo reactivo Cobranza → Ventas** (Tarea 2): un pago nuevo debe
   disparar `EngineRunner.run_orden` para que las reglas
   `requiere_pago_previo=True` (ya excluidas antes del pago) se evalúen. No
   implementado — requiere decidir síncrono vs. asíncrono y dónde engancha
   en el flujo de aprobación de `Vinculacion`.
2. **3e — Descuentos aplicados desde el sistema**: depende explícitamente de
   una lógica de Facturación aún no construida (mencionado en el propio
   `/goal`). Campo dejado listo/documentado, sin cálculo.
3. **N/D (notas de débito) atadas a factura** (3f): no existe hoy ninguna
   lectura de Odoo para N/D — a diferencia de N/C, que sí se reutiliza. Hay
   que confirmar primero en Odoo cómo se modelan las N/D en esta instancia
   (`docs/ODOO_MAPEO.md` no las documenta).
4. **`DescuentoDiferencialCambiario` y `DescuentoProducto` huérfanas**: si
   el negocio decide que deben tener efecto real, hace falta cablearlas en
   `EngineInputs`/`_calcular_componentes` — hoy sólo existen en Config/DB.
5. **Reorganización de Configuración en subpáginas**: diseñada arriba, sin
   implementar (cambio de UI transversal, requiere su propio ciclo).
6. **Ruta duplicada `/api/config/descuentos-volumen`**: registrada aquí como
   hallazgo no contemplado (ver sección anterior), no resuelta.
7. **Backend Sheets (legado)**: las hojas de Google Sheets ya desplegadas no
   tienen la columna `requiere_pago_previo`; el código tolera su ausencia
   (default `TRUE`/`FALSE` según tabla) pero alguien con acceso a las hojas
   debe agregar la columna manualmente si se sigue usando `REPO_BACKEND=sheets`
   en algún entorno.

## Checklist de migraciones Alembic

- [x] `e5f6a7b8c9d0_add_requiere_pago_previo.py` creada y **verificada**
      (agrega `requiere_pago_previo BOOLEAN NOT NULL` a
      `descuentos_pronto_pago`, `descuento_bcv_completo`,
      `descuentos_diferencial_cambiario`, `descuentos_volumen`,
      `promocion_primera_compra`, `descuentos_recompra`,
      `descuentos_producto`, `reglas_recurrencia`).
- [x] `f6a7b8c9d0e1_add_bandeja_teoricos_lista.py` creada y **verificada**
      (agrega `equivalente_lista_usd`, `teorico_lista_ves`,
      `teorico_lista_usd`, `descuentos_teorico_ves`, `descuentos_teorico_usd`
      — todas `NUMERIC(18,4) NOT NULL DEFAULT 0` — a `bandeja_facturacion`).
- [x] Ambas migraciones se corrieron con `alembic upgrade head` contra un
      Postgres 16 real (localmente, mismo flujo que usa el job de CI:
      levantar el servidor, crear el rol/DB de la cadena `DATABASE_URL`,
      aplicar la cadena completa desde `1fc70f5694c1` sin errores). CI
      (`.github/workflows/ci.yml`) también corre `alembic upgrade head`
      contra un servicio Postgres antes de la suite de tests — confirmado
      verde en el PR.
- [ ] Verificar `alembic current` en staging/producción antes de desplegar
      (no se tocó ningún entorno real fuera de CI/este sandbox).
- [ ] Tarea 3c–3g (descuentos aplicados/pendientes por orden-factura, N/D)
      requerirán migraciones nuevas cuando se implementen — no incluidas
      en este cambio.
