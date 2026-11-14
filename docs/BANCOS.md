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
| 1 | Importador por banco + modelo canónico + dedupe + saldos consolidados | **Hecho** (`cxc.bancos`). Con los estados reales (ago-nov 2026) cuadran al céntimo Bancamiga, Banesco, Provincial y Bancamiga USD; Venezuela y BDT traen huecos del propio archivo |
| 2 | Pre-conciliación por reglas contra Odoo + cola de revisión | **Hecho**: 90,8 % de aciertos medidos contra lo que concilió contabilidad (criterio: >80 %) |
| 3 | Líneas de extracto en Odoo (primero en base de pruebas) | Hoy: CSV para el asistente de importación. Push por API: **requiere decisión** (ver abajo) |
| 4 | Conectores API por banco | Pendiente |
| 5 | Agente LLM para ambiguos y anomalías | Después de tener datos etiquetados de 2-3 |

## Fase 1: qué hay

| Pieza | Dónde |
|---|---|
| Parsers (Bancamiga en Bs y en USD, Banesco, BDT, Provincial, Venezuela, Binance) | `cxc/bancos/parsers.py` |
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

Endpoints. Cada uno exige un permiso (ver "Permisos" abajo): `bancos.cargar` para importar
(y ver la lista de archivos), `bancos.ver` para el resto:

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
  del mismo día. Sin saldo (Binance) entra la hora exacta de la operación
  (`correlativo`), estable entre exports que se solapan.
- **Bancamiga web** exporta `Movimientos_*.xls` que en realidad son HTML (UTF-8 o
  Latin-1). El parser lee la tabla, el saldo inicial y la cuenta del encabezado; si la
  cuenta no termina en la esperada (7806 en Bs, 6747 en USD), rechaza el archivo.
- **Binance**: USDT se toma 1:1 con USD. Una compra de USDT con USD son dos filas, y su
  diferencia es la ganancia o pérdida de la conversión. Sin saldo: la cuenta queda fuera
  del total consolidado, con alerta.
- **Diarios** (`diarios.py`): nombres y ids leídos de Odoo el 13-nov-2026. El CSV
  resuelve el diario por nombre, así que tienen que coincidir exacto (el de Venezuela se
  llama "Bank" y el de BDT "0175*****9321"). **Bancamiga USD no tiene diario en Odoo**:
  se carga y se valida, pero no se exporta al CSV.
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

## Fase 2: pre-conciliación

Para cada movimiento, las reglas proponen qué haría contabilidad; una persona aprueba,
corrige, rechaza o reabre, y cada decisión queda en una bitácora de solo-append. Nada
se escribe en Odoo: los pagos se leen con `solo_lectura`, que rechaza cualquier método
que no sea de lectura antes de enviarlo.

| Regla | Qué propone |
|---|---|
| R1 | Comisión bancaria → `6.2.1.1.01.02 Bank Fees` |
| R2 | Pago de Odoo del **mismo diario**, mismo monto exacto, sentido correcto, fecha ±3 días. Desempatan: misma fecha, dígitos de la referencia del banco en el memo, RIF de la descripción igual al del cliente. Empate → "ambiguo" |
| R3 | Compra con tarjeta o nómina sin pago que la cierre → `Pagos pendientes` de ese banco |

**Medición** (13-nov-2026, solo lectura contra Odoo): 705 movimientos de Venezuela,
BDT, Bancamiga, Banesco y Provincial cuya línea de extracto contabilidad ya había
conciliado. Acertó 640 (90,8 %), se equivocó en 8 y no propuso nada en 57. De los 8,
7 son comisiones que contabilidad mandó a "Pagos pendientes" en vez de Bank Fees
(inconsistencia contable, no de la regla). Cuando propone, acierta el 98,8 %.
Límite: mide contra pagos que ya existían en Odoo; un cobro que llega antes de que
alguien registre el pago queda sin propuesta.

Tablas: `bancos_propuestas` (una por movimiento, con su estado: pendiente, aprobada,
corregida, rechazada; regenerar no toca las ya decididas) y `bancos_decisiones`
(quién, cuándo, qué había antes y qué quedó; un trigger rechaza `UPDATE` y
`DELETE`).

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/api/bancos/conciliacion/generar` | (`bancos.conciliar`) Lee pagos de Odoo y guarda propuestas; filtro `banco` |
| GET | `/api/bancos/conciliacion` | Propuestas con filtros `banco`, `estado`, `tipo`, `desde`, `hasta` |
| POST | `/api/bancos/conciliacion/{huella}/decidir` | (`bancos.conciliar`) `accion`: aprobar, rechazar, corregir (`pago_id` o `cuenta`), reabrir |
| GET | `/api/bancos/conciliacion/auditoria` | La bitácora; filtro `huella` |
| GET | `/api/bancos/conciliacion/aprobadas.xlsx` | Lo aprobado o corregido, para aplicarlo a mano en Odoo. Filtros `banco` y `desde`/`hasta` (fecha en que se decidió). Hojas: Para aplicar, Ya conciliadas en Odoo (el pago ya estaba conciliado al generar), Resumen |

Las reglas de clasificación se aplican de nuevo al generar, así que una regla
corregida vale también para lo que ya estaba cargado.

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

## Permisos

Los da el administrador en Configuración → Usuarios (ver `src/cxc/permisos.py`). El rol
da una base y a cada persona se le agregan o quitan permisos; cada cambio queda en
`accesos_bitacora` (solo-append). La página Bancos se abre sola si la persona tiene
cualquiera de estos:

| Permiso | Qué deja hacer | Por defecto |
|---|---|---|
| `bancos.cargar` | Subir estados de cuenta y ver la lista de archivos cargados | admin |
| `bancos.ver` | Saldos, movimientos, cola de pre-conciliación, bitácora, Excel y CSV | admin, auditor |
| `bancos.conciliar` | Generar propuestas y aprobar / corregir / rechazar / reabrir | admin |

Quien solo carga no ve saldos consolidados ni movimientos.

## Seguridad

- Los estados de cuenta no se versionan; credenciales de bancos en un vault, nunca en
  el repo.
- Acceso de solo lectura en los bancos; nunca permisos para iniciar pagos.
- Quién cargó cada archivo queda en `bancos_archivos.cargado_por`; quién aprobó,
  corrigió o rechazó cada propuesta, en `bancos_decisiones`.
