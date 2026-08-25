# SETUP — Configuración manual y operativa (lo que NO se codifica)

Este documento cubre todo lo que el backend **no** automatiza: credenciales/
secretos y automated actions de Odoo. El código backend está listo para
correr sin editarse una vez que esto esté en su lugar.

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

### Scraper Binance (parametrizado — único punto de ajuste si cambia el formato)
`BINANCE_P2P_URL`, `BINANCE_P2P_ASSET`, `BINANCE_P2P_FIAT`,
`BINANCE_P2P_TRADE_TYPE_BUY/SELL`, `BINANCE_P2P_ROWS`,
`BINANCE_P2P_ADV_LIST_PATH`, `BINANCE_P2P_PRICE_PATH`. Ver `.env.example`.

### BCV, alertas, motor, conciliación, auditoría
Ver `.env.example` (todos con default sensato).

---

## 2. Esquema de datos

El backend usa PostgreSQL como único almacén (ver `src/cxc/db/schema.py` para
el esquema completo y `alembic/versions/` para el historial de migraciones).
El `Procfile` corre `alembic upgrade head` en cada deploy (fase `release`); no
requiere preparación manual de tablas.

> Nota histórica: el sistema usó Google Sheets como backend antes de la
> migración a Postgres. Ese backend (`SheetsRepository`) se retiró por
> completo del código en agosto 2026 -- ver `docs/REDISENO_DESCUENTOS_
> UNIFICADOS.md` para el contexto de la migración.

---

## 3. App AppSheet (pieza 3 — interfaz humana, superada por el dashboard web)

> Nota: esta sección describe el diseño original (AppSheet sobre Google
> Sheets). El sistema hoy usa el dashboard web (`src/cxc/web/`) como interfaz
> humana -- Cobranza, Ventas, Auditoría, Configuración. Se conserva esta
> sección solo como referencia histórica del diseño de validaciones/security
> filters; no aplica a la implementación actual.

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
