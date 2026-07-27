# Plan de Trabajo — Descuentos en Línea, Facturas y Notas de Crédito en el Reporte CxC

## Resumen

Se trata de **tres extensiones interconectadas** del reporte de Cuentas por Cobrar (página `/reporte`) y del motor de descuentos:

1. **3 columnas nuevas** en la tabla del reporte: descuentos en línea de la orden, descuentos en línea de facturas, y notas de crédito (NC) asociadas.
2. **Auditoría de descuentos Odoo vs Motor**: si la orden tiene descuentos registrados en Odoo (en líneas de producto, subtotal o líneas de descuento), el motor los compara con lo que él calcula. Si coinciden → no aplica lo ya aplicado; si el motor calcula algo diferente → aplica la diferencia. Si no corresponde → va a una **bandeja de auditoría de descuentos**.
3. **Aplicación de Notas de Crédito reales de Odoo**: las NCs asociadas a facturas se aplican como reducción del saldo deudor (ya en los campos `saldo_con_descuento_*`), y también pasan por auditoría: si la NC corresponde con el motor → se aplica normalmente; si no corresponde → se aplica igual, pero se marca en la bandeja de auditoría.

---

## Confirmación de comprensión de la lógica de negocio

Entiendo correctamente lo siguiente:

### Columna 1 — Descuentos en la orden (Odoo)
- Leer el campo `discount` de cada `sale.order.line` en Odoo para esa orden.
- También considerar líneas de tipo "descuento" en la orden (secciones separadas) y descuentos en el subtotal.
- Mostrar: `"Global Oil 8% + sección descuento 5%"` o similar.

### Columna 2 — Descuentos en las facturas (Odoo)
- Por cada `account.move` (factura) asociada a la orden, leer los descuentos en líneas de factura (`discount` en `account.move.line`).
- Si la orden tiene múltiples facturas, consolidar.

### Columna 3 — Notas de Crédito (NC) asociadas a las facturas
- Las `account.move` de tipo `out_refund` ya se están trayendo en la query actual (con `move_type in ['out_invoice', 'out_refund']`).
- Actualmente las NCs se **ignoran** en el cálculo (hay un `continue` explícito en el código). La nueva lógica las usa activamente.

### Auditoría de descuentos de la orden
- **El motor calcula** lo que debería ser el descuento.
- El reporte **compara** contra lo que Odoo tiene aplicado en la orden.
- Si el motor calcula $10 de descuento y Odoo ya tiene $10 aplicados → no volver a descontar.
- Si el motor calcula $15 y Odoo tiene $10 → aplicar solo la diferencia ($5).
- Si el motor calcula $0 y Odoo tiene $10 (o viceversa con discrepancia significativa) → marcar en **bandeja de auditoría de descuentos**.

### Notas de Crédito → saldo deudor
- El monto total de NCs reales en Odoo se resta del saldo deudor en las columnas `saldo_con_descuento_bcv` y `saldo_con_descuento_lista_usd`.
- Si el monto de la NC corresponde razonablemente con lo que el motor calcula como NC → aplicar y listo.
- Si no corresponde (NC inesperada, monto diferente) → aplicar igualmente, pero enviar a la bandeja de auditoría.

---

## Open Questions / Aclaraciones necesarias

> [!IMPORTANT]
> **¿Qué significa "corresponde con los descuentos del motor"?** Propongo usar la misma tolerancia de la capa de conciliación (actualmente `tolerance_rounding` y `tolerance_red` en `ReconciliationConfig`). ¿O se necesita una tolerancia separada para descuentos?

> [!IMPORTANT]
> **¿La bandeja de auditoría de descuentos es una nueva hoja en Google Sheets, o una sección dentro del dashboard web?** Propongo ambas: una tabla en Sheets como registro persistente + una pestaña/sección nueva en la UI del reporte web.

> [!IMPORTANT]
> **¿Las NC solo se leen de Odoo (ya existentes) o el motor también puede "calcular NCs esperadas"?** Por la especificación, el motor tiene `ncs_calculadas` (ej: la caja de liga de primera compra). La auditoría compararía `ncs_odoo_reales` vs `ncs_calculadas_motor`.

> [!NOTE]
> **Scope acotado:** La lógica de "aplicar descuentos a Odoo" es write-back, lo cual va contra el principio rector del diseño (write-back purista). Interpreto que la acción es que el **motor refleja la deducción en el saldo mostrado**, y si hay discrepancia, la marca en la bandeja para que Administración actúe manualmente en Odoo. El sistema no escribe a Odoo.

---

## Proposed Changes

### 1. Consulta Odoo — Nuevos campos para líneas de orden y facturas

#### [MODIFY] [app.py](file:///c:/Users/geren/Proyectos/CxC_Lubrikca/src/cxc/web/app.py)

**Sección: Query de `sale.order.line`** (actualmente no se trae `discount` de las líneas de Odoo):
- Ampliar la query de `account.move` (ya existente, línea ~1297) para también traer `invoice_line_ids` con sus descuentos.
- Añadir una **nueva query** de `sale.order.line` para traer el campo `discount` (% de descuento por línea).
- Las `out_refund` (NCs) dejan de ser ignoradas (`continue`) y pasan a un diccionario `ncs_by_so`.

**Estructura de datos adicionales a construir (dentro del loop del reporte):**
```python
# descuentos_orden_odoo: dict[so_id, {"pct_linea": float, "monto_descuento": float, "detalle": str}]
# descuentos_factura_odoo: dict[so_id, {"monto_descuento": float, "detalle": str}]
# ncs_by_so: dict[so_id, {"monto_nc_usd": float, "nombres_nc": list[str]}]
```

**Nuevos campos en cada fila del `reporte`:**
```python
"descuentos_odoo_orden": {
    "pct_promedio": ...,
    "monto_usd": ...,
    "detalle": "Global Oil 8% (USD 45.00), Sección -5% (USD 12.00)"
},
"descuentos_odoo_factura": {
    "monto_usd": ...,
    "detalle": "FAC/2026/001: descuento $10.00"
},
"ncs_odoo": {
    "monto_usd": ...,
    "nombres": ["NC/2026/001"],
    "auditoria_estado": "ok" | "discrepancia"  # resultado de auditoría
},
"auditoria_descuentos": {
    "estado": "ok" | "discrepancia" | "sin_datos",
    "motor_calcula_usd": ...,
    "odoo_tiene_usd": ...,
    "diferencia_usd": ...,
    "aplica_diferencia": ...
}
```

**Ajuste en el cálculo de `saldo_con_descuento_bcv` / `saldo_con_descuento_lista_usd`:**
```python
# Actual:
saldo_con_descuento_bcv = max(0.0, saldo_deudor_bcv - total_descuentos_monto)

# Nuevo (incluyendo NCs reales de Odoo):
ncs_odoo_monto = ncs_by_so.get(o.so_id, {}).get("monto_nc_usd", 0.0)
saldo_con_descuento_bcv = max(0.0, saldo_deudor_bcv - total_descuentos_monto - ncs_odoo_monto)
saldo_con_descuento_lista_usd = max(0.0, saldo_deudor_lista_usd - total_descuentos_monto - ncs_odoo_monto)
```

---

### 2. Bandeja de Auditoría de Descuentos

#### [NEW] Hoja `BandejaAuditoria` en Google Sheets

Nueva hoja con las siguientes columnas:
| Campo | Descripción |
|---|---|
| `audit_id` | ID único (timestamp + so_id) |
| `so_id` | Orden de Venta |
| `tipo_auditoria` | `descuento_orden` / `nota_credito` |
| `motor_calcula_usd` | Lo que el motor calcula |
| `odoo_registrado_usd` | Lo que está en Odoo |
| `diferencia_usd` | Brecha |
| `detalle_odoo` | Descripción del descuento/NC en Odoo |
| `detalle_motor` | Desglose del motor |
| `estado` | `pendiente` / `revisado` / `aprobado` |
| `revisado_por` | Email del revisor |
| `timestamp_audit` | Fecha de detección |

#### [MODIFY] `repositories.py`

Agregar métodos:
- `append_auditoria_descuento(fila: dict)` — solo append, registro inmutable.
- `all_auditoria_descuentos()` — lectura.
- `update_estado_auditoria(audit_id, estado, revisado_por)` — para que Administración marque como revisado.

#### [NEW] API endpoints en `app.py`:
- `GET /api/auditoria-descuentos` — lista la bandeja de auditoría.
- `POST /api/auditoria-descuentos/{audit_id}/revisar` — marca como revisado.

---

### 3. Lógica de Auditoría de Descuentos (función separada)

#### [NEW] `src/cxc/engine/discount_audit.py`

Módulo con lógica pura de auditoría:

```python
def auditar_descuento_orden(
    motor_total_descuentos: Decimal,
    odoo_descuento_aplicado: Decimal,
    tolerance: Decimal = Decimal("0.50")
) -> AuditoriaResultado:
    """
    Compara el descuento calculado por el motor con el descuento aplicado en Odoo.
    
    Returns:
        - estado: 'ok' | 'coincide_parcial' | 'discrepancia'
        - descuento_a_aplicar: lo adicional que el motor aplica (diferencia)
        - enviar_a_bandeja: bool
    """

def auditar_nota_credito(
    motor_ncs_calculadas: Decimal,
    odoo_nc_monto: Decimal,
    tolerance: Decimal = Decimal("0.50")
) -> AuditoriaResultado:
    """
    Compara la NC calculada por el motor con la NC real de Odoo.
    NC siempre se aplica al saldo. Solo decide si va a bandeja.
    """
```

---

### 4. Frontend — 3 nuevas columnas en la tabla del reporte

#### [MODIFY] [app.py](file:///c:/Users/geren/Proyectos/CxC_Lubrikca/src/cxc/web/app.py) — sección HTML

La tabla principal del reporte (dentro del HTML embebido, buscando la función de render del reporte) necesita 3 columnas nuevas:

| Columna | Fuente | Display |
|---|---|---|
| **Desc. Orden (Odoo)** | `descuentos_odoo_orden` | Badge con % y monto, expandible |
| **Desc. Factura (Odoo)** | `descuentos_odoo_factura` | Monto USD, vinculado a factura |
| **Notas de Crédito** | `ncs_odoo` | Monto + nombre NC + badge estado auditoría |

Además, una **indicación visual** en la fila cuando `auditoria_descuentos.estado == 'discrepancia'` (ícono ⚠️ o borde naranja).

#### [NEW] Pestaña/Sección "Auditoría de Descuentos" en la UI

Nueva sección en el mismo dashboard con:
- Tabla de `BandejaAuditoria` filtrable por estado.
- Botón "Marcar como Revisado" por fila.
- KPI: total en revisión, total aprobados, total discrepancias activas.

---

## Verification Plan

### Backend
1. Ejecutar `GET /api/reporte-saldos?refresh=true` y verificar que las filas nuevas contengan los campos `descuentos_odoo_orden`, `descuentos_odoo_factura`, `ncs_odoo`, `auditoria_descuentos`.
2. Verificar que una orden con NC real en Odoo reduce correctamente el `saldo_con_descuento_bcv`.
3. Verificar que discrepancias van a `GET /api/auditoria-descuentos`.

### Frontend
1. Confirmar que las 3 columnas nuevas se ven en la tabla de la página `/reporte`.
2. Confirmar que la sección de auditoría se renderiza con los registros correctos.
3. Verificar que filas con discrepancias muestran el indicador visual.

### Manual
- Revisar en Odoo una orden con descuento conocido, y validar que la columna "Desc. Orden (Odoo)" muestra el mismo valor.
- Revisar una orden con NC, y confirmar que el saldo baja correctamente.

---

## Orden de ejecución sugerido

1. **Ampliar la query Odoo** en `get_reporte_saldos()`:
   - Traer `sale.order.line` con campo `discount`.
   - Traer líneas de `account.move.line` con `discount`.
   - Separar `out_refund` como NCs, no ignorarlas.
2. **Crear `discount_audit.py`** con la lógica de comparación pura.
3. **Actualizar el loop del reporte** para calcular los nuevos campos y poblar `ncs_by_so`.
4. **Ajustar el cálculo de saldos** para restar las NCs reales.
5. **Crear `BandejaAuditoria`** en Sheets + endpoints API.
6. **Actualizar `repositories.py`** con los métodos de auditoría.
7. **Actualizar la UI** con las 3 columnas nuevas y la sección de auditoría.
