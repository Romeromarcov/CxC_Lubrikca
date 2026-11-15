# Sistema de Cobros y Conciliación — Lubrikca (CxC)

Aplicación web (FastAPI + PostgreSQL) que espeja Odoo en modo **solo lectura**,
calcula con un motor determinístico lo que cada orden *debería* facturar
(descuentos, tasas BCV/Binance, notas de crédito) y lo contrasta contra lo que
Odoo realmente facturó y cobró.

Principio rector: **el sistema enmarca y marca; el humano revisa lo que se sale
del marco.** Ningún humano carga solo con un número que mueve dinero sin que el
sistema lo contraste contra algo verificable. Odoo es la única autoridad
contable: este sistema **no escribe en Odoo**, con una única excepción acotada
(nov-2026): los **borradores capturados por Telegram** (ver
[«Capturas de Telegram»](#capturas-de-telegram-única-escritura-a-odoo) más abajo).

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
append). Lo verifica `tests/test_sync.py`. Un cuarto mundo, `capturas_bot`
(lo que llega por Telegram), tampoco lo toca el sync.

### Capturas de Telegram (única escritura a Odoo)

**Cambio de principio (decisión del owner, nov-2026).** Hasta ahora CxC nunca
escribía en Odoo. Para que el equipo siga trabajando en la interfaz de CxC mientras
se prueban los bots de Telegram, los bots escriben a Omni **y** a CxC a la vez, y
CxC crea en Odoo, automáticamente, **solo borradores**:

| Captura | Qué se crea en Odoo | Qué NO se hace |
|---|---|---|
| `contacto` | `res.partner` marcado «[Borrador Telegram] pendiente de completar», sin rango de cliente | No se confirma ni se completa |
| `pedido` | `sale.order` en borrador (+ el cliente, con su RIF, si no existe) | No se confirma; no se fija precio si el bot no lo da (Odoo aplica la lista del cliente) |
| `pago`, `pago_proveedor` | `account.payment` sin publicar (inbound/outbound) | No se publica ni concilia; **nunca crea clientes ni proveedores**; si el cliente, el diario o la moneda son ambiguos o no existen, queda `INCOMPLETO` con el motivo. En un pago a proveedor con `factura_ref` se ubica la factura (`account.move` `in_invoice`, misma `ref` y partner) **solo para citarla en la nota**; si no aparece, no es error |
| `proveedor` | `res.partner` con `supplier_rank` 1 y `customer_rank` 0, «[Borrador Telegram] pendiente de completar» y los datos del bot (RIF, teléfono, email, dirección, días de crédito en la nota, GPS si el campo existe). **Si ya existe** (por RIF, con o sin guiones, o por nombre exacto) **no se duplica**: queda `ENVIADO` con el `odoo_ref` del existente y una nota en `resultado` | No modifica al proveedor existente; RIF o nombre ambiguo → `INCOMPLETO` |
| `factura_proveedor` | `account.move` `in_invoice` en **borrador** (`partner_id`, `ref` = número de factura, `invoice_date`, `invoice_date_due`, moneda, una línea por la base imponible, o por el monto total si no viene). El IVA y el número de control del bot van en `narration` | **No publica y no inventa impuestos** (la línea va sin impuestos para que contabilidad los asigne). **Nunca crea el proveedor desde una factura** ni adivina si es ambiguo: queda `INCOMPLETO` («créalo con /proveedor»). Faltantes (número, fecha, moneda, monto, proveedor) → `INCOMPLETO` hasta `completar`. La misma (proveedor, número) ya capturada → `409`; si ya está en Odoo → `ERROR` legible |

- **Credencial aparte:** `ODOO_WRITE_USERNAME` / `ODOO_WRITE_PASSWORD` (misma
  URL y base que la lectura). Debe ser un usuario de Odoo con permisos mínimos:
  crear en `res.partner`, `sale.order`, `account.payment` y `account.move` (facturas de
  proveedor en borrador), y leer lo necesario para buscar; nada más. La credencial
  de lectura (`ODOO_USERNAME`) no cambia y sigue siendo solo lectura.
- **Interruptor:** `CAPTURAS_ODOO_ESCRITURA=1`. Sin él, o sin la credencial, no se
  abre ninguna conexión de escritura: las capturas se guardan y quedan `PENDIENTE`.
- **Idempotente:** cada creación lleva la marca `BOT-<id>` y se busca antes de
  crear, así un reintento nunca duplica. Los errores técnicos de Odoo van al log;
  la interfaz solo muestra un mensaje legible.
- **Bitácora:** cada captura guarda sus eventos (recibida, intento, creada en Odoo,
  error, completada, reintento manual) en `capturas_bot_eventos`, solo-append.
- **API:** los bots usan `POST /api/bots/capturas` y `…/{id}/completar` con la
  cabecera `X-Bot-Key` (`BOT_API_KEY`); con el latido del panel de bots (más abajo) es lo
  único que esa clave abre. Las llaves
  de API normales siguen siendo solo `GET`. La interfaz (página **Capturas
  Telegram**, permisos `capturas.ver` / `capturas.gestionar`) lista, muestra,
  reintenta y, con `capturas.gestionar`, también completa pagos y facturas de
  proveedor (`…/{id}/completar` acepta la clave del bot **o** esa sesión; crear
  capturas es solo del bot). Tipos válidos (`kind`): `contacto`, `pedido`, `pago`,
  `pago_proveedor`, `proveedor`, `factura_proveedor`.
- **Estados:** `PENDIENTE` → `ENVIADO` (`odoo_ref` = `modelo:id`), o `INCOMPLETO`
  (falta algo que no se adivina) o `ERROR` (los de red se reintentan solos, máx. 5).

Una persona confirma/publica el borrador dentro de Odoo: Odoo sigue siendo la
única autoridad contable.

### Panel de bots de Telegram (plano de control de la familia de bots)

CxC no solo recibe las capturas: es también el **panel desde el que se administra** la familia de
bots de Lubrikca (página **Bots**): `pedidos` (Ventas), `cobranza` (Cobranzas), `compras`,
`eventos` y `asana` (el bot de Asana, ya en producción; su tarjeta enlaza a su propio panel).

- **Latido:** cada bot llama a `POST /api/bots/heartbeat` (misma clave `X-Bot-Key` / `BOT_API_KEY`;
  es la única otra ruta que esa clave abre) con su estado (`modo`, `gemini`, `omni`, `cxc`,
  cola de reintentos, mensajes y errores de hoy, último error) y **recibe en la respuesta su
  configuración**: `habilitado`, `parametros` (`auto_envio_min` 1–120, `gemini_model`),
  `usuarios` autorizados para ese bot, `chats` (`grupo`, `respaldo`) y un `mensaje`.
  Un bot está **en línea** si su último latido tiene menos de 3 minutos.
- **Qué se controla desde el panel:** pausar/reanudar un bot, sus parámetros y chats, la dirección
  de su propio panel (`url_panel`, útil para Asana), un mensaje para el equipo, y la lista de
  **usuarios autorizados** (id de Telegram, correo, nombre y a qué bots tienen acceso).
  Además muestra la **salud de los destinos** (Omni, CxC, Gemini y la cola de reintentos), las
  capturas incompletas o con error y la actividad reciente de cada bot.
- **Si CxC no responde**, los bots siguen con la última configuración que recibieron (o la de su
  `.env`): el panel gobierna, pero no es un punto único de falla.
- **Lo que NO guarda ni devuelve:** tokens de Telegram ni API keys (la clave de Gemini solo se
  indica si el bot la tiene). Lo que un bot reporta como texto (`ultimo_error`) se trunca y se
  redacta antes de guardarse, por si trae una URL con token.
- **Permisos:** `bots.ver` (consultar) y `bots.gestionar` (cambiar), con el mismo reparto que
  las capturas: tesorería y gerencia ven y gestionan, auditoría solo ve, ventas no ve nada.
- **Bitácora:** todo cambio hecho desde el panel queda en `bots_bitacora` (solo-append, con
  trigger): quién, qué y el antes/después de lo que realmente cambió.
- **Tablas:** `bots_registro`, `bots_usuarios`, `bots_latidos` (contadores por día, se podan a
  los 30 días) y `bots_bitacora`. Mundo aparte: el sync de Odoo no las toca.

### Mapa del código (`src/cxc/`)

| Paquete | Contenido |
|---|---|
| `web/` | `app.py` (API, auth por cookie y por llave de API, demonios) y `static/` (SPA en JS plano) |
| `engine/` | Lógica pura de dinero: `discounts.py` (motor), `runner.py` (cablea repo + precios), `balance.py`, `saldos.py`, `listas.py`, `equivalents.py`, `acotar_pago.py`, auditorías (`auditoria_*`, `nc_contra_motor.py`, `pagos_duplicados.py`, `reversadas.py`…) |
| `db/` | `schema.py` (tablas, SQLAlchemy Core), `postgres_repository.py`, `invariantes.py` (restricciones de base) |
| `odoo/` | `client.py` (lector XML-RPC), `price.py` (precio por pricelist), `escritura.py` (escritor de borradores de las capturas, credencial aparte) |
| `sync/` | `incremental.py` (sync delta) |
| `scraper/` | BCV, Binance y el orquestador `rates_scraper.py` |
| `reconciliation/` | Semáforo motor vs Odoo |
| `bancos/` | Ingesta de estados de cuenta: parsers por banco, huella de dedupe, validación de saldos, saldos consolidados, CSV de líneas de extracto para Odoo. Ver [`docs/BANCOS.md`](docs/BANCOS.md) |
| `bots/` | Panel de bots de Telegram (plano de control): modelo y validación, repositorio, servicio (latido, configuración, usuarios autorizados, salud de destinos). API en `web/bots_api.py` |
| `capturas/` | Capturas de los bots de Telegram: validación, repositorio, servicio y resolución de cliente/producto/diario. Escribe a Odoo **solo borradores** vía `odoo/escritura.py` |
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

`./scripts/escenarios.sh` corre `escenarios/` contra el Odoo de prueba de `.env.qa`
(escribe por red, tarda minutos, no es parte de la suite normal). Se niega a correr si
`ODOO_URL` no lleva `.dev.odoo.com`. Hoy no hay Odoo de prueba: el duplicado `qa1` ya no
existe, y `lixie-dev-lubrika.odoo.com` (el de `.env`) es **producción**. Ver
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
| [`docs/BANCOS.md`](docs/BANCOS.md) | Ingesta bancaria: fases, tablas, endpoints y decisiones |
| [`docs/ODOO_MAPEO.md`](docs/ODOO_MAPEO.md) | Qué modelos y campos de Odoo lee el sistema |
| [`docs/blindaje/PROGRESO.md`](docs/blindaje/PROGRESO.md) | Libro de a bordo del plan de blindaje (sep-2026): mediciones, hallazgos e incidentes |
| [`escenarios/README.md`](escenarios/README.md) | Banco de escenarios de error humano |
