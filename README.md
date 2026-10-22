# Sistema de Cobros y Conciliación — Lubrikca (CxC)

Aplicación web (FastAPI + PostgreSQL) que espeja Odoo en modo **solo lectura**,
calcula con un motor determinístico lo que cada orden *debería* facturar
(descuentos, tasas BCV/Binance, notas de crédito) y lo contrasta contra lo que
Odoo realmente facturó y cobró.

Principio rector: **el sistema enmarca y marca; el humano revisa lo que se sale
del marco.** Ningún humano carga solo con un número que mueve dinero sin que el
sistema lo contraste contra algo verificable. Odoo es la única autoridad
contable: este sistema **nunca escribe en Odoo**.

> Las reglas de negocio de origen están en
> [`Especificacion_Sistema_Cobros_Lubrikca.md`](Especificacion_Sistema_Cobros_Lubrikca.md)
> (secciones 4–7 vigentes; la arquitectura de esa especificación está superada,
> ver su encabezado). Decisiones de negocio tomadas ante ambigüedad:
> [`TODO.md`](TODO.md). Puesta en marcha y operación: [`SETUP.md`](SETUP.md).

---

## Cómo está armado

Un único proceso web (`Procfile`: `uvicorn cxc.web.app:app`) que además arranca
dos demonios en segundo plano (`startup_event` en `src/cxc/web/app.py`):

| Demonio | Qué hace | Frecuencia |
|---|---|---|
| `run_sync_in_background` | Sync incremental Odoo → Postgres (clientes, órdenes, líneas, pagos, facturas, entregas, catálogo), sincronización de aplicaciones conciliadas y de NC, recálculo del motor y conciliación. Catálogo y recálculo completo, una vez por día. | cada 5 min |
| `run_scraper_in_background` | Captura tasas BCV (USD y EUR, scraping directo del sitio) y Binance P2P en `serie_tasas`. | cada hora, entre las 6:00 y las 22:00 (Caracas) |

Al arrancar, `startup_event` también aplica `alembic upgrade head` como red de
seguridad si la fase `release` del `Procfile` no corrió.

```
Odoo (XML-RPC, solo lectura)
   │  delta por write_date
   ▼
cxc.sync ──► Postgres ◄── cxc.scraper (BCV + Binance → serie_tasas, solo append)
                │
                ├─► cxc.engine   motor: teórico por lista, descuentos, equivalentes
                │                congelados por abono, saldos, auditorías
                ├─► cxc.reconciliation   motor vs factura real de Odoo → semáforo
                └─► cxc.web   API REST (≈100 endpoints) + SPA estática
                                (dashboard, facturación, conciliaciones, cobranza,
                                 ventas, reporte, auditoría, inventario, configuración)
```

**Tres mundos de datos que no se cruzan en escritura:** *espejo de Odoo*
(escribe solo el sync), *trabajo humano* (vinculaciones, decisiones, reglas de
descuento — el sync nunca las toca) y *auditoría inmutable* (`serie_tasas`, solo
append). Lo verifica `tests/test_sync.py`.

### Mapa del código (`src/cxc/`)

| Paquete | Contenido |
|---|---|
| `web/` | `app.py` (API, auth por cookie y por llave de API, demonios) y `static/` (SPA en JS plano) |
| `engine/` | Lógica pura de dinero: `discounts.py` (motor), `runner.py` (cablea repo + precios), `balance.py`, `saldos.py`, `listas.py`, `equivalents.py`, `acotar_pago.py`, auditorías (`auditoria_*`, `nc_contra_motor.py`, `pagos_duplicados.py`, `reversadas.py`…) |
| `db/` | `schema.py` (tablas, SQLAlchemy Core), `postgres_repository.py`, `invariantes.py` (restricciones de base) |
| `odoo/` | `client.py` (lector XML-RPC), `price.py` (precio por pricelist) |
| `sync/` | `incremental.py` (sync delta) |
| `scraper/` | BCV, Binance y el orquestador `rates_scraper.py` |
| `reconciliation/` | Semáforo motor vs Odoo |
| `audit/` | `HourAuditor` (hora declarada vs banco). **Librería sin cablear**: ningún endpoint ni demonio la usa hoy |
| `sheets/` | Solo `serde.py`: conversión dataclass ↔ fila (resto del backend Google Sheets retirado en agosto 2026) |
| `repositories.py` | Interfaz `Repository` e `InMemoryRepository` (para tests) |

Migraciones: `alembic/versions/` (45). Scripts operativos y de auditoría:
`scripts/`. Banco de escenarios contra un Odoo de prueba: `escenarios/`
([README](escenarios/README.md)).

---

## Desarrollo

Requisitos: Python 3.11+, Docker (para el Postgres local).

```bash
python -m venv .venv
.venv\Scripts\activate                 # Linux/mac: source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env                   # completar con valores reales (no se versiona)

docker compose up -d                   # Postgres 16 igual al del CI
export DATABASE_URL=postgresql://cxc:cxc_ci_pw@localhost:5432/cxc_ci   # PowerShell: $env:DATABASE_URL = "..."
alembic upgrade head

PYTHONPATH=src uvicorn cxc.web.app:app --reload     # dashboard en http://localhost:8000
```

### Las tres barreras

```bash
./scripts/barreras.sh          # secretos + ruff + mypy + pytest con cobertura
./scripts/barreras.sh rapido   # sin cobertura
```

- `ruff check .` — lint (incluye `scripts/` y `escenarios/`).
- `mypy` — estricto; `cxc.web.app` está exento (`ignore_errors`).
- `pytest` — suite hermética (sin red: Odoo y scrapers mockeados). **Necesita
  `DATABASE_URL`**: sin Postgres fallan ~40 tests que ejercitan `get_repo()`.
  El umbral de cobertura (`--cov-fail-under` en `pyproject.toml`) es un
  trinquete sobre el valor real medido, no un objetivo; el CI usa el mismo.

CI: `.github/workflows/ci.yml` (ruff → mypy → `alembic upgrade head` → pytest)
sobre un servicio Postgres 16, en cada push/PR a `main` y `develop`.

### Banco de escenarios

`./scripts/escenarios.sh` corre `escenarios/` contra el Odoo de QA (escribe por
red, tarda minutos, no es parte de la suite normal). Ver
[`escenarios/README.md`](escenarios/README.md).

---

## Reglas de negocio clave (resumen)

- **Neto-objetivo, no nominal:** la orden nunca se paga al total nominal; el
  motor persigue el neto esperado según la ruta de pago.
- **Apilamiento aditivo:** los descuentos se suman (no "gana el mayor").
- **Lista por método de pago:** el método define la lista y gana sobre la de
  nacimiento; el precio se lee de la pricelist real de Odoo (no se multiplica
  por un factor), con fallback configurable a la ficha del producto.
- **Contado por ventana de días hábiles** desde la entrega completa, saltando
  fines de semana y la tabla `feriados`. Vencido → crédito y pierde el contado.
- **Equivalentes congelados por abono:** cada abono congela sus equivalentes
  contra la tasa estampada de su bucket horario; no se recalculan.
- **Mezcla → Binance:** si algún abono salió de la ruta BCV, la orden pierde el
  descuento de diferencial por pago "100% BCV".
- **Diferencial cambiario** (`fijo_35_ves_usd`, `equiparar_binance`): ver
  `TODO.md` §1.
- **Cierre híbrido:** el motor marca *candidata a cierre*; Administración
  confirma.
- **Effective dating:** los % de descuento tienen vigencia por fecha; una orden
  vieja se audita con el % que regía entonces.

## Documentación

| Documento | Contenido |
|---|---|
| [`SETUP.md`](SETUP.md) | Variables de entorno, despliegue en Railway, migraciones, prerrequisitos en Odoo |
| [`TODO.md`](TODO.md) | Decisiones de negocio ante ambigüedad y pendientes de confirmar |
| [`docs/ODOO_MAPEO.md`](docs/ODOO_MAPEO.md) | Qué modelos y campos de Odoo lee el sistema |
| [`docs/blindaje/PROGRESO.md`](docs/blindaje/PROGRESO.md) | Libro de a bordo del plan de blindaje (sep-2026): mediciones, hallazgos e incidentes |
| [`escenarios/README.md`](escenarios/README.md) | Banco de escenarios de error humano |
