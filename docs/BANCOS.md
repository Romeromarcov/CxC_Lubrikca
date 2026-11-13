# Ingesta bancaria

Unifica los movimientos de todos los bancos para pre-conciliar, consolidar saldos y
llevarlos a Odoo como **líneas de extracto** (`account.bank.statement.line`), no como
asientos: el asiento sale de la conciliación nativa de Odoo y no hay doble
contabilización.

```
Bancos ─► [1 Ingesta] ─► [2 Crudo inmutable] ─► [3 Normalización]
                                                    │
                     [6 Cola de aprobación] ◄── [4 Dedupe + Clasificación]
                              │                     │
                              ▼                     ▼
                     [7 Hacia Odoo]        [5 Pre-conciliación]
                              │              (vs facturas / CxC)
                              ▼
                     [8 Saldos consolidados + auditoría]
```

## Estado por fase

| Fase | Entregable | Estado |
|---|---|---|
| 1 | Importador por banco + modelo canónico + dedupe + saldos consolidados | **Hecho** (`cxc.bancos`) — falta validar "cuadra al céntimo" con los estados reales |
| 2 | Pre-conciliación por reglas contra Odoo/CxC + cola de revisión | Pendiente |
| 3 | Líneas de extracto en Odoo (primero en base de pruebas) | Hoy: CSV para el asistente de importación. Push por API: **requiere decisión** (ver abajo) |
| 4 | Conectores API por banco | Pendiente |
| 5 | Agente LLM para ambiguos y anomalías | Después de tener datos etiquetados de 2-3 |

## Fase 1: qué hay

| Pieza | Dónde |
|---|---|
| Parsers (Bancamiga, Banesco, BDT, Provincial, Venezuela) | `cxc/bancos/parsers.py` |
| Modelo canónico y huella de dedupe | `cxc/bancos/modelos.py` |
| Validación de la cadena de saldos, fila a fila | `cxc/bancos/validacion.py` |
| Clasificación por reglas (comisiones, IGTF, nómina, POS…) | `cxc/bancos/clasificacion.py` |
| Banco → diario de Odoo | `cxc/bancos/diarios.py` |
| Saldos consolidados USD/VES con tasa BCV + huecos entre cortes | `cxc/bancos/saldos.py` |
| Persistencia | `cxc/bancos/repositorio.py`, tablas `bancos_archivos` y `bancos_movimientos` |

Tablas:

- `bancos_archivos`: cada archivo **tal cual llegó** (bytes + sha256) con el resultado
  de su validación. Solo-append: un trigger rechaza `UPDATE` y `DELETE`.
- `bancos_movimientos`: una fila por movimiento real, con clave `huella`. Un corte
  solapado o re-subido no duplica nada.

Endpoints (cargar: solo `admin`; leer: `admin` y `auditor`):

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/api/bancos/importar` | multipart `banco` + `archivo`. Guarda crudo, parsea, valida, inserta lo nuevo |
| GET | `/api/bancos/archivos` | Cargas hechas, con `cuadra` y observaciones |
| GET | `/api/bancos/saldos` | Saldo por cuenta al último corte, en USD y VES, con alertas |
| GET | `/api/bancos/movimientos` | Filtros `banco`, `desde`, `hasta`, `categoria` |
| GET | `/api/bancos/consolidado.xlsx` | Libro: Saldos, Movimientos, Categorías |
| GET | `/api/bancos/odoo-extracto.csv` | CSV para importar en Odoo; filtros `banco`, `desde`, `hasta` |

## Decisiones

- **Huella** = sha256 de banco, cuenta, fecha, referencia, descripción, débito,
  crédito y saldo reportado. El saldo es lo que distingue dos movimientos idénticos
  del mismo día. Sin saldo (ningún banco actual) entra la posición en el archivo.
- **Provincial**: su columna "Referencia" es el número de fila del reporte y se
  renumera en cada exportación, así que **no entra en la huella** (va a
  `correlativo`). El prototipo la usaba como referencia: los `unique_import_id` de
  Provincial generados por el prototipo no coinciden con los de este módulo.
- **Saldos**: salen del saldo que reporta el banco en el último corte de cada cuenta,
  no de sumar movimientos. Entre cortes que no se solapan se exige
  `saldo_inicial(siguiente) == saldo_final(anterior)`; si no, se alerta un hueco.
- **Conversión**: tasa BCV de la fecha de corte (`Tasas.bcv_usd`). Sin tasa no se
  convierte; la cuenta queda en `cuentas_sin_tasa` y fuera del total.
- **Un archivo que no cuadra se guarda igual**, marcado `cuadra = false`: el
  descuadre es información. Uno que no parsea no se guarda.

## Decisión pendiente: escribir en Odoo

El principio rector del sistema (README) es que **nunca escribe en Odoo**. La fase 3,
tal como está planteada (push vía JSON-RPC/XML-RPC), lo rompe. Mientras no se
decida, la vía es el CSV: una persona lo importa en el diario del banco y
`unique_import_id` garantiza que re-importarlo no duplique.

Si se decide escribir, mínimo: usuario de Odoo dedicado con permiso solo sobre
`account.bank.statement.line`, base de pruebas primero, y la cola de aprobación de la
fase 2 delante de todo envío.

**Hoy no hay base de pruebas de Odoo.** `lixie-dev-lubrika.odoo.com` (el `ODOO_URL`
de `.env`) es **producción**, aunque el nombre diga "dev": contra ella solo se lee. El
duplicado `lixie-dev-lubrika-qa1-….dev.odoo.com` (el de `.env.qa`) ya no existe. Antes
de escribir el push hace falta una rama de staging nueva en Odoo.sh (o un Odoo 18 local),
y la misma barrera del banco de escenarios: el código que escribe se niega a correr si
`ODOO_URL` no lleva `.dev.odoo.com`.

## Seguridad

- Los estados de cuenta no se versionan; credenciales de bancos en un vault, nunca en
  el repo.
- Acceso de solo lectura en los bancos; nunca permisos para iniciar pagos.
- Quién cargó cada archivo queda en `bancos_archivos.cargado_por`.
