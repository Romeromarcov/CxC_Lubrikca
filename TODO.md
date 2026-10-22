# Decisiones de negocio y pendientes de confirmar

Regla seguida: ante ambigüedad se sigue la especificación; si no la cubre, se
elige la opción **más conservadora (no regalar descuento)** y se anota aquí. Cada
punto dice qué decidió el código y qué falta confirmar con negocio.

---

## Por confirmar con negocio (impactan dinero)

### 1. Diferencial Cambiario (spec 4.3c) — decidida, rediseñada en agosto 2026
El mecanismo anterior («BCV-completo», tabla `DescuentoBCVCompleto`) se retiró
por completo. Hoy son dos reglas configurables en `descuentos_diferencial_cambiario`
(`tipo_diferencial`), implementadas en `_calcular_componentes`
(`src/cxc/engine/discounts.py`). Solo aplican a órdenes nacidas en lista VES con
abonos vinculados:

- **`fijo_35_ves_usd`:** si el cliente pagó el 100 % del teórico USD
  exclusivamente en abonos 100 % USD → descuento fijo de `porcentaje_fijo`
  (35 % por defecto) sobre el precio base en VES.
- **`equiparar_binance`:** si pagó (mezcla o 100 % Binance) el 100 % del teórico
  USD, pero valorado a tasa BCV el pago queda corto frente al precio VES → nota
  de crédito que cierra esa brecha, topada al mismo `porcentaje_fijo`. Exige que
  el cliente no tenga pagos huérfanos abiertos en Odoo.
- Si ambas califican se aplica el **mayor** de los dos montos.
- Una tercera, `candidato_cierre_factura`, no es descuento automático: habilita el
  reporte de candidatos a cierre (`GET /api/diferencial/candidatos-cierre`) que
  gerencia usa para aprobar manualmente vía
  `POST /api/facturacion/aprobar-descuento-sistema`.
- Sin fila vigente y activa de `fijo_35_ves_usd` no se otorga nada (conservador).

### 2. Sesgo de la tasa Binance (spec 5.1)
`(5 compra + 5 venta)/10` es un punto medio. Implementado tal cual y
parametrizable (`BINANCE_P2P_ROWS`); queda por confirmar que ese sesgo es el
deseado.

---

## Ambigüedades resueltas de forma conservadora

### 3. El contado no depende del método de pago — decidido
Cualquier método puede ser contado o crédito y todos manejan ambas tasas
(BCV/Binance). Lo que determina el contado es **pagar el neto total dentro del
plazo** (ventana de días hábiles desde la entrega completa). La ruta BCV/Binance
se estampa **por abono** en la vinculación. `metodos_pago` es catálogo
informativo; sus columnas `es_contado`/`tipo_tasa` no las usa el motor.

### 3b. Entrega completa y devoluciones — decidido
- El plazo de contado arranca con la **entrega completa** (`delivery_status =
  full`). Si está `pending`/`partial`, `fecha_entrega` queda vacía y el contado no
  se evalúa.
- Las devoluciones se detectan (pickings con `return_id`) y marcan
  `tiene_devolucion` → la orden queda `requiere_revision`.
- Con orden entregada completa **y** devolución, el motor factura sobre
  `cantidad_entregada` (`qty_delivered`, neta de devolución): implementa
  «pedida − devuelta» y evita el doble descuento si la SO ya se ajustó en Odoo.

### 4. `fecha_entrega` ausente → sin contado
Sin ancla para la ventana (spec 4.6) el contado no se evalúa ni se otorga.

### 5. Mezcla de rutas → Binance (spec 3.9b)
Si algún abono salió de la ruta BCV, la orden no cumple «pagada 100 % en BCV» y
pierde los beneficios que lo exigen. «Ante la duda, el sistema no regala el mejor
descuento.»

---

## Decisiones de modelado (no cambian el resultado de negocio)

6. **Valoración canónica en USD.** Ambas listas entregan precio en USD; el
   neto-objetivo se compara en USD sumando los equivalentes congelados por abono
   (spec 4.4). Se evita una tasa fantasma VES→USD.
7. **Semáforo de conciliación.** `|dif| ≤ RECON_TOLERANCE_ROUNDING` → verde;
   `≤ RECON_TOLERANCE_RED` → amarillo; mayor → rojo.
8. **Fallback del scraper (spec 5.3).** Si falla Binance **o** BCV se hereda la
   fila completa del último bucket (`es_heredada`, `capturada_ok = false`) para no
   mezclar tasa fresca con heredada en una misma fila. Tres fallos consecutivos
   disparan alerta.
9. **Desempates de effective dating.** Marca/categoría: mayor especificidad
   (marca exacta pesa más que categoría exacta) → menor porcentaje → `regla_id`.
   Recurrencia: `vigencia_desde` más reciente → menor `valor`.
10. **Estado de factura** vive en `ordenes_venta` (`facturada`, `factura_id`,
    `monto_facturado`) y en la tabla `facturas`; no hay tabla separada.
11. **Emparejamiento abono ↔ banco (spec 6.3)**, en la librería `cxc.audit`
    (hoy sin cablear a la app): por monto exacto; con varios candidatos, el de
    hora más cercana; sin calce → hallazgo de prioridad alta.
12. **`requiere_revision`** se marca si hay tasa heredada, si aplicó un
    diferencial cambiario calculado, o si el contado se negó por vencimiento.
13. **Cursor del sync.** Avanza al `now` de inicio de corrida (no al máximo
    `write_date` leído) para no perder filas escritas durante la lectura; puede
    releer solapados (idempotente por upsert).

---

## Deuda técnica conocida

- **Sesión sin expiración.** El token de `cxc_session` es `email.firma` (HMAC
  truncado a 64 bits) sin fecha de vencimiento ni revocación. Ver
  `crear_session_token` en `src/cxc/auth.py`.
- **Roles nominales.** Los cinco roles tienen las nueve páginas; solo algunas
  acciones de escritura distinguen rol (ver `SETUP.md` §4).
- **`cxc.audit.HourAuditor`** (hora declarada vs banco) está implementada y
  probada pero ningún endpoint ni demonio la usa.
- **`is_historical_pricelist_enabled`** devuelve `False` siempre (decisión del
  12-sep-2026: la lista histórica es solo para auditoría); las ramas que dependen
  de ella en `web/app.py` son código inalcanzable.
- **`src/cxc/sheets/`** solo conserva `serde.py` del backend Google Sheets
  retirado; casi todas sus funciones `*_from_row`/`*_to_row` solo las ejercitan
  tests.
