# SETUP — Configuración y operación

Todo lo que el código no hace por sí solo: variables de entorno, base de datos,
despliegue y prerrequisitos en Odoo.

---

## 1. Variables de entorno

El backend se configura 100 % por variables de entorno (sin secretos en el repo).
Copiar `.env.example` a `.env` en local; en Railway, cargarlas en el panel del
servicio. Los defaults entre paréntesis salen de `src/cxc/config.py`.

### Obligatorias

| Var | Descripción |
|---|---|
| `DATABASE_URL` | Cadena de Postgres. Railway la inyecta al agregar el addon. Sin ella la app no puede leer ni escribir (`get_repo()` lanza `RuntimeError`) |
| `ODOO_URL`, `ODOO_DB` | Instancia y base de Odoo |
| `ODOO_USERNAME`, `ODOO_PASSWORD` | Usuario de integración y su **API key** de Odoo (*Preferencias → Seguridad de la cuenta → API Keys*), no la clave de un humano. Solo lectura |
| `SESSION_SECRET` | Clave con la que se firman las cookies de sesión. **Obligatoria en producción**: si falta, la app arranca con una clave por defecto que está en el código fuente (público) y lo advierte en el log |

### Motor y listas de precio

| Var | Default | Descripción |
|---|---|---|
| `CASH_WINDOW_BUSINESS_DAYS` | 3 | Días hábiles de la ventana de contado desde la entrega completa |
| `ENGINE_LISTA_USD` / `ENGINE_LISTA_BCV` | 4 / 5 | Listas lógicas del motor (ver `.env.example`) |
| `ODOO_PRICELIST_USD` / `ODOO_PRICELIST_BCV` | — | Id de `product.pricelist` de Odoo para cada lista lógica |
| `ENGINE_VALID_PRICELISTS_VES` / `_USD` | `5,3` / `4,8,USD` | Listas consideradas válidas por moneda |
| `ENGINE_IVA_RATE` | 0.16 | IVA aplicado a las ventas teóricas |
| `ENGINE_IGTF_RATE`, `ENGINE_IGTF_ACTIVO` | 0.03, falso | IGTF (hoy desactivado) |

El mapeo vigente de listas de precio se administra además desde la página
**Configuración** (`/api/config/pricelist-mapeo`, `/api/config/listas-precio`).

### Scrapers, alertas, conciliación

`BINANCE_P2P_*` y `BINANCE_REQUEST_TIMEOUT_SECONDS` (parametrizan URL, par,
profundidad y rutas del JSON: es el único punto de ajuste si Binance cambia el
formato), `BCV_URL`, `BCV_USD_REGEX`, `BCV_REQUEST_TIMEOUT_SECONDS`,
`SCRAPER_FAIL_ALERT_THRESHOLD` (3), `ALERT_TELEGRAM_BOT_TOKEN` /
`ALERT_TELEGRAM_CHAT_ID` / `ALERT_TELEGRAM_API_URL` (opcionales: sin ellas las
alertas solo se loguean), `RECON_TOLERANCE_ROUNDING` (0.01) y
`RECON_TOLERANCE_RED` (1.00): |dif| ≤ redondeo → verde, ≤ rojo → amarillo,
mayor → rojo. `HOUR_AUDIT_*` solo afectan a la librería `cxc.audit`, que hoy no
está cableada a la app.

---

## 2. Base de datos y migraciones

PostgreSQL es el único almacén (`src/cxc/db/schema.py` define las tablas;
`alembic/versions/` guarda el historial, 45 migraciones).

- `Procfile` ejecuta `alembic upgrade head` en la fase `release` de cada deploy.
- Como respaldo, `startup_event` repite `alembic upgrade head` al arrancar el
  proceso web (best-effort: si falla, la app sigue y registra el error). Nació de
  un deploy en el que la fase `release` no corrió y `/api/ventas` devolvió 500 por
  columnas inexistentes.
- **Checklist al agregar una migración:** crearla con `alembic revision`,
  correr `alembic upgrade head` contra el Postgres local
  (`docker compose up -d`), y confirmar que el job de CI queda verde (también la
  aplica).

### Entorno local

```bash
docker compose up -d
export DATABASE_URL=postgresql://cxc:cxc_ci_pw@localhost:5432/cxc_ci
alembic upgrade head
```

Esa base es la misma que usa el CI. Los scripts de `scripts/` leen el `.env`
local: sin indicar el `.env` de producción auditan la base local, no la de
producción.

---

## 3. Despliegue (Railway)

Un solo servicio web; no hay cron jobs separados: los demonios de sync y de
scraping viven dentro del proceso (ver README). Procesos del `Procfile`:

```
web:     PYTHONPATH=src uvicorn cxc.web.app:app --host 0.0.0.0 --port $PORT
release: alembic upgrade head
```

Operación periódica fuera del proceso web (cron externo, a gusto de
Administración): `scripts/vigilancia_diaria.py` (alertas diarias de
invariantes y pagos sobreaplicados) y `scripts/verificar_tasas_contra_bcv.py`
(contrasta el histórico de tasas con las series oficiales del BCV).

---

## 4. Acceso y roles

La autenticación es por cookie de sesión firmada (`cxc_session`) o, solo para
`GET`, por llave de API (`/api/admin/api-keys`). Toda ruta `/api/` exige una de
las dos salvo una lista blanca (login, registro de contraseña). Roles
(`usuarios_plataforma.rol`): `admin`, `gerente_ventas`, `tesoreria`, `auditor`,
`ventas`. Las nueve páginas están habilitadas para todos; las acciones
administrativas (usuarios, roles, llaves de API) exigen `admin`, el recálculo
total `admin` o `gerente_ventas`, y el cambio de estado en la bandeja de
auditoría de descuentos `admin` o `auditor`.

---

## 5. Odoo — prerrequisitos (no se codifican)

- **Marca y categoría normalizadas** a nivel de producto: los descuentos por
  marca × categoría dependen de leerlas consistentes.
- **Vendedor:** el sistema usa `user_id.login` como identidad del vendedor; debe
  coincidir con el correo con el que la persona entra al dashboard.
- **Pricelists** con reglas de precio fijo en USD; si una lista no tiene regla
  para un producto, el resolver cae a la ficha del producto con el ajuste
  configurado (`FallbackFichaConfig`). Ver [`docs/ODOO_MAPEO.md`](docs/ODOO_MAPEO.md).
- La facturación y las notas de crédito son **manuales en Odoo**; el sistema
  detecta desviaciones, no las previene.
- Las tablas de configuración de negocio (reglas de descuento, feriados, metas,
  tasas) se mantienen desde la página **Configuración** del dashboard, no por
  variables de entorno.
