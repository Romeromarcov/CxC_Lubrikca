# Sistema de Cobros y Conciliación — Lubrikca (CxC)

Backend determinístico que reemplaza el flujo Google Forms + IMPORTRANGE por un
sistema **Odoo ↔ Google Sheets/AppSheet** con motor de descuentos y capa de
conciliación de facturación.

> Fuente de verdad: [`Especificacion_Sistema_Cobros_Lubrikca.md`](Especificacion_Sistema_Cobros_Lubrikca.md).
> Este repo implementa las **piezas backend** de esa especificación. Lo que queda
> manual (AppSheet, credenciales, automated actions de Odoo) está en
> [`SETUP.md`](SETUP.md). Decisiones ante ambigüedad: [`TODO.md`](TODO.md).

Principio rector: **el sistema enmarca y marca; el humano revisa lo que se sale
del marco.** Ningún humano carga solo con un número que mueve dinero sin que el
sistema lo contraste contra algo verificable.

---

## Piezas implementadas

| # | Pieza | Módulo | Entrypoint |
|---|---|---|---|
| 1 | Scraper de tasas horario → SerieTasas | `cxc.scraper` | `python -m cxc.run_scraper` |
| 2 | Sync incremental delta Odoo→Sheets | `cxc.sync` | `python -m cxc.run_sync` |
| 4 | Motor de descuentos → BandejaFacturacion | `cxc.engine` | `python -m cxc.run_engine` |
| 5 | Conciliación motor vs Odoo → semáforo | `cxc.reconciliation` | `python -m cxc.run_reconcile` |
| 6 | Auditoría hora declarada vs banco | `cxc.audit` | (librería; ver abajo) |

La **pieza 3 (AppSheet)** es interfaz humana y **no se codifica** aquí (ver
`SETUP.md`).

Las dos capas de soporte:
- **Persistencia** (`cxc.sheets`): `SheetsRepository` sobre Google Sheets.
- **Adaptador Odoo** (`cxc.odoo`): XML-RPC **solo lectura** (Odoo es la única
  autoridad contable; *Sheets nunca escribe a Odoo* — write-back purista).

---

## Arquitectura

```
Odoo (autoridad contable, solo lectura)
  │  XML-RPC delta (write_date > cursor)
  ▼
cxc.sync ──► Google Sheets (espejo) ──┐
                                       │
cxc.scraper ──► SerieTasas (append)    ├─► cxc.engine ──► BandejaFacturacion
                                       │       (neto-objetivo, apilamiento,
AppSheet ──► Vinculaciones ────────────┘        contado, BCV-completo, cierre)
                                               │
                                               ▼
                              cxc.reconciliation ──► semáforo verde/amarillo/rojo
                              cxc.audit ──► desvíos hora declarada vs banco
```

**Regla de oro de la plomería** (sección 1.2): tres mundos de datos que no se
cruzan en escritura — *espejo de Odoo* (solo el sync), *trabajo humano*
(Vinculaciones/Bandeja, el sync **nunca** las toca) y *auditoría inmutable*
(SerieTasas, solo append). El código lo garantiza por construcción y hay un test
que lo verifica (`tests/test_sync.py::test_sync_nunca_toca_vinculaciones_ni_serietasas`).

---

## Requisitos

- Python **3.11+**
- Una cuenta de servicio de Google con acceso al Google Sheet.
- Un usuario/API key de Odoo con permisos de lectura.

Toda la configuración entra por **variables de entorno** (no hay secretos en el
repo). Ver [`.env.example`](.env.example) y `SETUP.md`.

---

## Instalación

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Linux/mac: source .venv/bin/activate
pip install -r requirements.txt        # runtime
pip install -r requirements-dev.txt    # + tests/lint/types
```

Copia la plantilla de entorno y rellénala:

```bash
cp .env.example .env   # editar con valores reales (queda fuera de git)
```

---

## Cómo correr cada pieza

Cada entrypoint lee la configuración del entorno, arma los adaptadores reales
(Google Sheets + Odoo) y ejecuta una corrida.

```bash
# Pieza 1 — capturar tasas BCV + Binance del bucket horario (correr cada hora)
python -m cxc.run_scraper

# Pieza 2 — refrescar el espejo de Odoo en Sheets (delta por write_date)
python -m cxc.run_sync

# Pieza 4 — calcular la BandejaFacturacion de las órdenes con abonos
python -m cxc.run_engine

# Pieza 5 — conciliar motor vs factura real de Odoo (semáforo)
python -m cxc.run_reconcile
```

**Pieza 6 (auditoría hora vs banco)** se usa como librería porque el formato del
estado de cuenta varía por banco; importa el extracto a `BankMovement` y corre:

```python
from cxc.audit import HourAuditor, BankMovement
from cxc.config import HourAuditConfig

auditor = HourAuditor(HourAuditConfig.from_env())
hallazgos = auditor.auditar(vinculaciones, movimientos_banco)  # solo excepciones
```

### Despliegue en Railway

Cada pieza es un proceso independiente. Configurar como **cron jobs**:

| Pieza | Frecuencia sugerida | Comando |
|---|---|---|
| Scraper | cada hora (`0 * * * *`) | `python -m cxc.run_scraper` |
| Sync | cada 15 min | `python -m cxc.run_sync` |
| Motor | cada 15–30 min | `python -m cxc.run_engine` |
| Conciliación | diaria | `python -m cxc.run_reconcile` |

Variables de entorno: cargar las de `.env.example` en el panel de Railway.
Detalles en `SETUP.md`.

---

## Tests, lint y tipos

```bash
pytest          # tests + cobertura (gate >= 90%)
ruff check .    # lint
mypy            # tipos (estricto)
```

Los tests **no usan red**: Odoo y Sheets están mockeados (in-memory) y el scraper
de Binance/BCV se prueba con fixtures capturados (`tests/fixtures/`). La función
de red de cada cliente es inyectable, así que el scraper de **producción** pega
directo al endpoint público de Binance sin credenciales.

CI (GitHub Actions, `.github/workflows/ci.yml`) corre ruff + mypy + pytest en
cada push/PR a `main`.

---

## Reglas de negocio clave (resumen)

- **Disparador neto-objetivo, no nominal** (4.0): la orden nunca se paga al total
  nominal; el motor persigue el *neto esperado* según la ruta de pago.
- **Apilamiento aditivo** (4.1): los descuentos se suman (no "gana el mayor").
  Ej.: Sinoco recompra + contado = **6%**; Global Oil sintético + recompra = **11%**.
- **Reselección de lista por método** (4.2): el método de pago define la lista y
  **gana** sobre la lista especial de nacimiento. Se lee el precio real de esa
  pricelist en Odoo (no se multiplica por un factor).
- **Contado por ventana de días hábiles** (4.6): `[entrega, entrega + 3 hábiles]`,
  saltando fines de semana **y** la tabla de Feriados. Vencido → pasa a crédito y
  pierde el contado.
- **Equivalentes congelados por abono** (3.9b): cada abono congela sus 4
  equivalentes contra la tasa estampada de su bucket horario; jamás se recalculan.
- **Mezcla → Binance** (3.9b): si algún abono salió de la ruta BCV, la orden no
  cumple "completo en BCV" y migra a VES@Binance (la más conservadora).
- **Cierre híbrido** (4.7): el motor marca *candidata a cierre*; Administración
  confirma.
- **Effective dating** (8.4): los % de descuento tienen vigencia por fecha; una
  orden vieja se audita con el % que regía entonces.

---

## Qué quedó manual (ver `SETUP.md`)

- **App AppSheet** (pieza 3): vinculación pago↔orden, bandeja de aprobación,
  security filters `USEREMAIL()`, validaciones de monto.
- **Credenciales/secretos**: service account de Google, API key de Odoo.
- **Automated actions de Odoo** y normalización de marca/categoría a nivel de
  producto (prerequisito del motor, sección 8.3).
- **Creación del Google Sheet** con las pestañas y cabeceras esperadas.
- **Mapeo de pricelists** USD/BCV → ids de Odoo (`ODOO_PRICELIST_*`).
