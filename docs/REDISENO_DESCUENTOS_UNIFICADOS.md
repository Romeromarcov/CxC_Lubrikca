# Rediseño CxC_Lubrikca — Descuentos unificados, `requiere_pago_previo`, Ventas/Auditoría, Configuración en subpáginas

Estado: Tarea 1 implementada y probada. Tarea 2 cerrada por hallazgo de
auditoría (no había nada que unificar; el flujo reactivo Cobranza→Ventas
queda diseñado, no implementado). Tarea 3 implementada completa (3a-3g:
equivalente/teóricos por lista en `BandejaFacturacion`, descuentos
aplicados orden/factura + validación visual, pendientes de aplicar, N/C y
N/D en `/api/ventas`). Tarea 4 implementada (`venta_bruta_teorica_auditoria`
en `GET /api/auditoria`, derivada de 3a/3b + IVA, sin UI todavía). La
reorganización de Configuración en subpáginas está implementada (versión
ligera, client-side, verificada con Playwright). Queda pendiente lo
detallado en "Dependencias abiertas" (3e, recálculo reactivo, persistencia
de `bandeja_auditoria` en Postgres, aplicar migraciones en producción).

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

### 3c–3g — IMPLEMENTADAS

Todas viven en `/api/ventas` (`src/cxc/web/app.py`), sin migración nueva
(ninguna es un campo persistido — son lecturas de Odoo + comparaciones
puras contra `BandejaFacturacion`, calculadas en cada request):

| # | Columna(s) en la respuesta | Cómo se resolvió |
|---|---|---|
| c | `descuento_aplicado_orden`, `descuento_aplicado_factura`, `descuento_motor_total`, `descuento_validacion_orden`, `descuento_validacion_factura` | Nuevo helper `_leer_descuentos_lineas_odoo()` lee `sale.order.line.discount`/`account.move.line.discount` (mismo patrón de dos-formas-de-descuento que ya usaba `get_reporte_saldos`, extraído a función reutilizable en vez de reescrito). La "validación visual" reutiliza **las mismas funciones puras** que ya usa `/api/auditoria` — `discount_audit.auditar_descuento_orden`/`auditar_descuento_factura` — llamadas **en línea, sin persistir a `bandeja_auditoria`** (opción (ii) del diseño original: evita depender del hallazgo de que `PostgresRepository` no implementa esa tabla — ver "Hallazgo no contemplado" abajo, que sigue abierto para `/api/auditoria-descuentos`, pero ya no bloquea Ventas). |
| d | `descuento_pendiente_aplicar` | `audit_orden.descuento_adicional_a_aplicar` (campo que `discount_audit` ya calculaba: `max(0, motor - odoo)`) — cero nueva lógica, solo se expone. |
| e | `descuento_aplicado_sistema` | `None` explícito en cada fila, con comentario en código apuntando a esta dependencia abierta. Deliberadamente `None` y no `0`, para no afirmar "no hay ninguno" cuando en realidad "no se sabe todavía" (depende de Facturación, no construida). |
| f | `total_nc_aplicada` (ya existía, ahora expuesta como columna propia), `total_nd_aplicada` (nueva) | N/C: sin cambios (`move_type=out_refund`). N/D: nuevo helper `_leer_notas_debito_odoo()` — Odoo no distingue N/D con un `move_type` propio; son `account.move` con `move_type=out_invoice` y `debit_origin_id` apuntando a la factura original (no siempre traen `invoice_origin`), así que se buscan por ese campo contra los ids de facturas `out_invoice` ya encontradas. |
| g | `total_facturado_neto` | `facturado_con_impuestos − nc + nd` (antes solo restaba NC). |

Tests: `tests/test_ventas_columnas_3c_3g.py` — 5 casos cubriendo la matriz
pedida (validación OK vs. discrepancia con pendiente > 0, N/C reduciendo
neto, N/D atada a factura incrementando neto, y que 3e sea siempre `None`).
Regresión verificada contra el test e2e preexistente de `/api/ventas`
(`test_e2e_24_ventas_reporte_teorico_vs_real_y_alerta`), que sigue en verde.

**Regla de diseño transversal respetada:** ninguna columna nueva calcula un
descuento — todas son (1) lecturas directas de Odoo, (2) campos ya
calculados por el motor, o (3) comparaciones/derivaciones aritméticas entre
(1) y (2), reusando `discount_audit.py` en vez de reimplementar la
comparación.

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

## Diseño de IA de Configuración en subpáginas (IMPLEMENTADA — versión ligera)

### Estado antes de este cambio

`Configuración` era **una sola página larga** (`index.html`, `tab-config`)
con 16 secciones `<section class="config-card card">` en scroll continuo,
cada una con su propio mini-CRUD y su propio par de endpoints
`/api/config/...`. No había subpáginas ni navegación interna. (Corrección a
una nota anterior de este documento: sí existe un CRUD de usuarios dentro de
Configuración — `admin-user-mgmt-panel` — no había que construirlo desde
cero, sólo agruparlo.)

### Qué se implementó

Se agregó una barra de sub-navegación (mismo componente visual `.tab-btn`
que ya usa la navegación principal) dentro de `#tab-config`, con 5 grupos,
sin mover ni renombrar ningún endpoint `/api/config/...` ni ningún `id`
existente — cada `<section class="config-card">` sólo ganó un atributo
`data-subpage="..."`:

1. **💰 Descuentos** — Matriz Consolidada, Recompra/Recurrencia, Pronto
   Pago (con el nuevo toggle `requiere_pago_previo`), Volumen, Obsequios y
   Promociones, Producto/Marca/Categoría, Diferencial Cambiario, Exclusiones
   Mutuas.
2. **👤 Usuarios** — `admin-user-mgmt-panel` (ya existía, solo se agrupó).
3. **📋 Listas de Precio** — Mapeo de Lista Histórica de Auditoría, Listas
   de Precios Importadas de Odoo, Catálogo de Productos y Precios en Odoo.
4. **⚙️ Otras** — Feriados, Tasas de Cambio, Auditoría de Clientes y
   Recurrencia (Odoo).
5. **🔄 Motor** — Forzar Recálculo Completo.

**Cómo funciona** (`src/cxc/web/static/app.js`, función `applyConfigSubpage`):
al hacer clic en un botón de la subnav, se le agrega la clase
`config-subpage-hidden` (`display: none !important`, definida en
`styles.css`) a toda `[data-subpage]` que no coincida con la subpágina
elegida, y se quita de las que sí — sin tocar `style.display` inline, que es
lo que ya usan algunas secciones para su propio control de visibilidad por
rol (p. ej. "Forzar Recálculo" solo se muestra a Admin/Gerente de Ventas).
La selección se recuerda en `sessionStorage` para no resetear la subpágina
al recargar. Verificado con un navegador headless (Chromium, vía
Playwright): estado inicial correcto (Descuentos visible, el resto oculto),
clic en "Usuarios" oculta Descuentos y muestra Usuarios, y el botón activo
cambia — sin errores de JS en consola.

### Qué se dejó fuera de esta versión (deuda de diseño, no de esta tarea)

- **Sin URLs reales por subpágina** (`/configuracion/descuentos`, etc.) —
  es navegación puramente client-side dentro de una sola carga de página,
  no hay bookmark/deep-link a una subpágina específica. Decidir si vale la
  pena introducir rutas reales es una decisión de producto aparte.
- **Sin indicador visual explícito de "regla huérfana"** en las secciones
  Producto/Marca/Categoría y Diferencial Cambiario (ver Tarea 2 — el motor
  no las lee) — quedó documentado aquí, pero no se agregó un badge/aviso en
  la UI para no mezclar dos cambios de UI distintos en el mismo commit.
- **Sin tests automatizados de la UI** (no hay suite de tests de frontend en
  el repo — la verificación fue manual con Playwright, no un test que corra
  en CI).

---

## Casos no contemplados (registrados explícitamente, no resueltos implícitamente)

### #1 — Ruta duplicada `/api/config/descuentos-volumen` (GET y POST)
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

### #2 — `bandeja_auditoria` (discrepancias descuento/NC) no existe en Postgres

Al diseñar la Tarea 3c (descuentos aplicados + validación visual, que iba a
reutilizar `discount_audit.py` vía la bandeja ya persistida) se encontró que
`repo.all_auditoria()` / `repo.append_auditoria_rows()` / la actualización de
estado de `/api/auditoria-descuentos` **sólo están implementados en
`SheetsRepository`** (`src/cxc/sheets/repository.py:445-454` y alrededores).
`PostgresRepository` no define estos métodos en absoluto, aunque la tabla
`bandeja_auditoria` sí existe en `src/cxc/db/schema.py` (columnas listas,
sin código que las lea/escriba). Como el código de `web/app.py` siempre
comprueba `hasattr(repo, "all_auditoria")` antes de usarlo, esto no lanza un
error — **simplemente `/api/auditoria-descuentos` devuelve una lista vacía
en silencio cuando `REPO_BACKEND=postgres`**, que es el backend de
producción actual (ver hallazgo del reporte de investigación: "migracion a
Postgres completa"). Es decir, la bandeja de discrepancias de descuentos/NC
está efectivamente apagada en producción hoy, independientemente de esta
tarea. No se corrigió aquí (es una laguna preexistente del backend Postgres,
no algo introducido por el rediseño de descuentos) pero bloquea la Tarea 3c
tal como estaba diseñada originalmente — ver la nota en la tabla de la
Tarea 3c más arriba con las dos alternativas de diseño.

---

## Dependencias abiertas

1. ~~**Recálculo reactivo Cobranza → Ventas**~~ — **CORRECCIÓN: ya estaba
   implementado antes de este cambio**, no era una dependencia abierta. Se
   verificó que `POST /api/vincular`, `/api/vincular-masivo`,
   `/api/vinculacion/{id}/tasa-binance` y `/api/vinculacion/{id}/tasa-bcv-tipo`
   (todos en `src/cxc/web/app.py`) ya disparan
   `background_tasks.add_task(recalculate_all, so_id)`, que instancia un
   `EngineRunner` y corre `run_orden(so_id, ...)` — así que un pago nuevo sí
   dispara el recálculo y las reglas `requiere_pago_previo=True` (antes
   excluidas por falta de abono) se evalúan en cuanto se vincula el pago.
   Además, `recalculate_all_orders()` corre `runner.run_all()` tras cada
   ciclo del sync incremental. La primera versión de este documento afirmó
   incorrectamente que esto faltaba — quedó corregido tras revisar
   `web/app.py` directamente en vez de inferirlo del reporte de
   investigación inicial.
2. **3e — Descuentos aplicados desde el sistema**: depende explícitamente de
   una lógica de Facturación aún no construida (mencionado en el propio
   `/goal`). Campo dejado listo/documentado, sin cálculo.
3. **N/D (notas de débito) atadas a factura** (3f): implementada
   (`_leer_notas_debito_odoo()` en `web/app.py`, busca `account.move` con
   `move_type=out_invoice` y `debit_origin_id` apuntando a la factura
   original) pero **no verificada contra datos reales de Odoo** — Lubrikca
   podría no usar N/D en absoluto, o modelarlas distinto (`docs/ODOO_MAPEO.md`
   no las documenta). Antes de confiar en `total_nd_aplicada` en producción,
   confirmar con al menos una N/D real que el campo `debit_origin_id` se
   pobla como se asume aquí.
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
- [x] **Incidente en producción, mitigado con red de seguridad en código**:
      un despliegue de `main` a producción (Railway) mostró en logs
      `UndefinedColumn` para `descuentos_pronto_pago.requiere_pago_previo` y
      `bandeja_facturacion.equivalente_lista_usd`/`teorico_lista_ves`/etc.,
      con `/api/reporte-saldos` y `/api/ventas` devolviendo 500. Causa: el
      `Procfile` ya tiene `release: alembic upgrade head`, pero esa fase no
      corrió (o falló en silencio) en ese despliegue — quedó sin
      diagnosticar el motivo exacto (revisar logs de la fase "Release" de
      ese deploy en Railway). Mitigación agregada: `startup_event()`
      (`src/cxc/web/app.py`) ahora corre `alembic upgrade head`
      programáticamente al arrancar el proceso `web`, como respaldo si la
      fase `release` no aplicó las migraciones — best-effort, nunca tumba
      el arranque si falla (backend Sheets, DB no disponible, permisos).
      Verificado end-to-end: se hizo `alembic downgrade -2` contra un
      Postgres real (simulando el esquema desactualizado de producción) y
      se confirmó que `_aplicar_migraciones_pendientes()` deja las columnas
      al día sin intervención manual (`tests/test_startup_migraciones.py`).
      No reemplaza correr la migración una vez en producción para las
      instancias que ya están corriendo el código viejo sin este respaldo
      — solo previene la recurrencia en despliegues futuros.
- [x] Tarea 3c–3g (descuentos aplicados/pendientes por orden-factura, N/C/N/D)
      implementadas sin necesidad de migración nueva — son lecturas de Odoo
      y comparaciones puras en `/api/ventas`, ningún campo persistido nuevo.
