# SETUP — Configuración manual y operativa (lo que NO se codifica)

Este documento cubre todo lo que el backend **no** automatiza: la app AppSheet,
las credenciales/secretos, las automated actions de Odoo y la preparación del
Google Sheet. El código backend está listo para correr sin editarse una vez que
esto esté en su lugar.

---

## 1. Variables de entorno

El backend se configura 100% por env vars (sin secretos en el repo). Copiar
`.env.example` a `.env` y rellenar. En Railway, cargarlas en el panel del
servicio.

### Odoo (XML-RPC, solo lectura)
| Var | Descripción |
|---|---|
| `ODOO_URL` | URL de la instancia (ej. `https://miempresa.odoo.com`) |
| `ODOO_DB` | Nombre de la base |
| `ODOO_USERNAME` | Usuario de integración |
| `ODOO_PASSWORD` | **API key** de Odoo (no la contraseña de un humano) |
| `ODOO_PRICELIST_USD` | Id de la `product.pricelist` USD (motor) |
| `ODOO_PRICELIST_BCV` | Id de la `product.pricelist` BCV (motor) |

> Crear la API key en Odoo: *Preferencias → Seguridad de la cuenta → API Keys*.

### Google Sheets
| Var | Descripción |
|---|---|
| `GOOGLE_SHEETS_SPREADSHEET_ID` | Id del Sheet (de su URL) |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Ruta al JSON de la service account |

### Scraper Binance (parametrizado — único punto de ajuste si cambia el formato)
`BINANCE_P2P_URL`, `BINANCE_P2P_ASSET`, `BINANCE_P2P_FIAT`,
`BINANCE_P2P_TRADE_TYPE_BUY/SELL`, `BINANCE_P2P_ROWS`,
`BINANCE_P2P_ADV_LIST_PATH`, `BINANCE_P2P_PRICE_PATH`. Ver `.env.example`.

### BCV, alertas, motor, conciliación, auditoría
Ver `.env.example` (todos con default sensato).

---

## 2. Google Sheet — pestañas y cabeceras

El `SheetsRepository` espera **una pestaña por tabla**, con los encabezados en la
fila 1 que coinciden con las claves del serializador (`src/cxc/sheets/serde.py`).
Crear estas pestañas:

| Pestaña | Quién escribe | Cabeceras (fila 1) |
|---|---|---|
| `Clientes` | sync | `cliente_id, nombre, vendedor_email` |
| `OrdenesVenta` | sync | `so_id, cliente_id, fecha, fecha_entrega, monto_total, lista_precios, vendedor_email, es_primera_compra, facturada, factura_id, monto_facturado` |
| `LineasOrden` | sync | `linea_id, so_id, producto, marca, categoria, cantidad, precio_unitario` |
| `Pagos` | sync | `pago_id, cliente_id, monto, moneda, metodo_pago, fecha_pago, vendedor_email` |
| `MetodosPago` | Administración | `metodo_id, nombre, moneda, tipo_tasa, es_contado` |
| `SerieTasas` | scraper (append) | `timestamp, tasa_bcv, tasa_binance, fuente, es_heredada, capturada_ok` |
| `DescuentosMarcaCategoria` | Administración | `regla_id, marca, categoria, tipo_descuento, porcentaje, vigencia_desde, vigencia_hasta, activo` |
| `DescuentoBCVCompleto` | Gerencia (diario) | `vigencia_desde, porcentaje, vigencia_hasta, activo` |
| `PromocionPrimeraCompra` | Administración | `producto, vigencia_desde, vigencia_hasta, activo` |
| `ReglasRecurrencia` | Administración | `condicion, tipo_beneficio, valor, vigencia_desde, vigencia_hasta, activo` |
| `Feriados` | Administración | `fecha, descripcion, tipo` |
| `Vinculaciones` | AppSheet | `vinc_id, pago_id, so_id, monto_aplicado, hora_pago_confirmada, tasa_bcv_aplicada, tasa_binance_aplicada, es_tasa_heredada, equiv_usd_bcv, equiv_usd_binance, equiv_ves_bcv, equiv_ves_binance, confirmado_por, timestamp_registro, estado, moneda_abono, tipo_tasa_abono` |
| `BandejaFacturacion` | motor + AppSheet | `so_id, lista_aplicada, precio_base_calculado, descuentos_detalle, total_descuentos, ncs_calculadas, total_motor, requiere_revision, candidata_a_cierre, aprobado_por, estado` |
| `Conciliacion` | conciliación | `so_id, total_motor, monto_odoo, ncs_odoo, diferencia, resultado, revisado_por` |
| `_Meta` | sync | `key, value` (cursor `last_sync`) |

**Formatos:** booleanos `TRUE`/`FALSE`; fechas ISO `YYYY-MM-DD`; datetimes ISO
`YYYY-MM-DDTHH:MM:SS`; decimales con punto.

Compartir el Sheet con el email de la **service account** (permiso de editor).

**Datos de descuento iniciales** (sección 3.7), cargar en
`DescuentosMarcaCategoria`:

| regla_id | marca | categoria | tipo_descuento | porcentaje | vigencia_desde | activo |
|---|---|---|---|---|---|---|
| D1 | Global Oil | Comercial sintéticos | contado | 0.08 | 2026-01-01 | TRUE |
| D2 | Global Oil | Industrial (pailas/tambores) | contado | 0.06 | 2026-01-01 | TRUE |
| D3 | Sinoco | * | contado | 0.03 | 2026-01-01 | TRUE |

---

## 3. App AppSheet (pieza 3 — interfaz humana)

No se codifica aquí. Construir en AppSheet sobre el mismo Sheet:

### 3.1 Mapeo de identidad (BLOQUEANTE — sección 8.1)
`USEREMAIL()` en los security filters exige que **el email de login del rep =
`vendedor_email` en Odoo**. Verificar esta consistencia **antes que nada**; si
falla, los filtros fallan en silencio.

### 3.2 Validaciones que sobreviven en AppSheet (sección 8.2)
- Dropdown de orden (`Valid_If`): solo órdenes del mismo cliente del pago, con
  saldo y `facturada = FALSE`.
- Monto aplicado: `> 0`, `<= saldo_sin_aplicar` del pago y `<=` saldo de la orden.
- Security filter en tablas-espejo: `[vendedor_email] = USEREMAIL()`.
- Sellos: `confirmado_por` con Initial value `USEREMAIL()` (read-only);
  `timestamp_registro` con `NOW()`.

### 3.3 Sello de hora del pago (sección 6)
La hora oficial la fija **Administración** (no el vendedor) seleccionándola del
**bucket horario de SerieTasas** (dropdown de horas capturadas), nunca tecleada
libre. La tasa BCV/Binance de ese bucket queda estampada en `Vinculaciones` y de
ahí se congelan los 4 equivalentes.

### 3.4 Bandeja de aprobación (cierre híbrido, sección 4.7)
El motor marca `candidata_a_cierre`; Administración **confirma** y aprueba en
AppSheet. Revisa solo lo marcado `requiere_revision`.

---

## 4. Odoo — prerequisitos (no se codifican)

- **Normalización de marca/categoría** a nivel de producto (sección 8.3): los
  descuentos por contado dependen de leer marca y categoría consistentes. Si hoy
  no están normalizadas, hacerlo es prerequisito del motor.
- **`fecha_entrega` desde el despacho** (`stock.picking`, sección 3.2): el motor
  no la puede inferir. Exponerla en `sale.order` (campo computado/relacionado) o
  ajustar el reader para leerla del picking.
- **Automated actions**: cualquier automatización dentro de Odoo (estados,
  campos calculados) se configura en Odoo, no aquí.

> El backend **solo lee** de Odoo. La facturación es **manual** en Odoo
> (write-back purista); la pieza 5 detecta desviaciones, no las previene.

---

## 5. Decisión de negocio pendiente antes de producción

- **Sesgo de la tasa Binance** (sección 5.1): el promedio (5 compra + 5 venta)/10
  es un punto medio. Confirmar que es el sesgo deseado.
- **Fórmula del descuento BCV-completo** (sección 4.3c): el default es
  `(binance − bcv)/binance` por abono. Confirmar y, si difiere, ajustar
  `BCV_COMPLETE_FORMULA` / la función en `cxc/engine/discounts.py`. Ver `TODO.md`.
